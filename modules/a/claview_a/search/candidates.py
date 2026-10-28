"""Candidate stages of table selection (plan 04, 4-1: 3.1 build_queries, 3.2 search_catalog, 3.3 expand_candidates).

- 3.1 ``build_queries``: the indicator term (catalog words) is the main query; the requirement's own words (its quote
  minus every condition) are the helper. An adopted model reading also supplies one soft query with its positive,
  locally bound basis/axis words. Evidence checks still decide whether the table satisfies those conditions.
- 3.2 ``search_catalog``: the title/path channel (old S1) and the value channel (old S2), character 2-gram idf
  overlap (src/claview/aflow/s_nodes.py s1/s2 :18-57 and node_common.py ``_channel``/``_rank_rows`` :29-47, commit
  55fd41b), over inverted indexes. The meaning channel (S3) is outside plan 04.
- 3.3 ``expand_candidates``: channel tops summed after max normalisation (old S5 :78-115, weights 1.0, top 10 per
  channel, 30 in the pool), then the tables of the requirement's terms ("용어의 표") and whole families. Families:
  tables whose names are the same once every bracket part is removed (``catalog.relations.family_core``: bracket
  qualifiers and year ranges only); tables that hold the same indicator value under other names are other
  families. Adding never passes a table: every candidate goes through the evidence checks.
- ``suggestions``: words for the one retry after no candidate has evidence (plan 04, 4-1): dictionary synonyms of
  the indicator words and the next lexical candidates not used yet.

Nothing here names a table, case or question.
"""

from __future__ import annotations
import math
from ..catalog.relations import relations_of
from .tables import grams_v1, norm_v1, selection_catalog


PATH_WEIGHT = 0.3  # a path hit is weaker evidence than a title hit (old S1)
CHANNEL_CAP = 10  # top tables kept per channel (old node_common CHANNEL_CAP)
POOL_CAP = 30  # pool after fusion (old S5 POOL_CAP)


def _rank_rows(scores: dict[str, float]) -> list[dict]:
    order = sorted((t for t, s in scores.items() if s > 0), key=lambda t: (-scores[t], t))
    return [{"table_id": t, "raw_score": scores[t], "rank": i + 1} for i, t in enumerate(order)]


def _channel(per_query: dict) -> dict:
    """Queries of one channel merged: best score per table, ranks again (old ``_channel``)."""
    best: dict[str, float] = {}
    qids: dict[str, list] = {}
    for qid, rows in per_query.items():
        for row in rows:
            tid = row["table_id"]
            best[tid] = max(best.get(tid, 0.0), row["raw_score"])
            qids.setdefault(tid, []).append(qid)
    order = sorted(best, key=lambda t: (-best[t], t))
    ranked = [{"table_id": t, "score": best[t], "rank": i + 1, "query_ids": qids[t]} for i, t in enumerate(order)]
    return {"ranked_full": ranked, "merged": ranked[:CHANNEL_CAP]}


def _title_scores(lex, qg: frozenset) -> dict[str, float]:
    """overlap(q, title) + 0.3 overlap(q, path); overlap = idf sum of shared grams / sqrt(doc length) (old S1)."""
    # A query's grams are a set. Ordinary += can turn equal overlaps into
    # different channel ranks through floating-point accumulation order.
    title: dict[str, list[float]] = {}
    path: dict[str, list[float]] = {}
    for g in sorted(qg):
        w = lex.idf_title.get(g, 0.0)
        for t in lex.title_inv.get(g, ()):
            title.setdefault(t, []).append(w)
        for t in lex.path_inv.get(g, ()):
            path.setdefault(t, []).append(w)
    out = {}
    for t in sorted(set(title) | set(path)):
        a = math.fsum(title.get(t, ())) / math.sqrt(len(lex.title[t])) if lex.title[t] else 0.0
        b = math.fsum(path.get(t, ())) / math.sqrt(len(lex.path[t])) if lex.path[t] else 0.0
        out[t] = a + PATH_WEIGHT * b
    return out


def _item_scores(lex, qg: frozenset) -> dict[str, float]:
    """Best overlap of one value name per table (old S2)."""
    acc: dict[tuple[str, int], list[float]] = {}
    for g in sorted(qg):
        w = lex.idf_item.get(g, 0.0)
        for key in lex.item_inv.get(g, ()):
            acc.setdefault(key, []).append(w)
    out: dict[str, float] = {}
    for (t, k), weights in sorted(acc.items()):
        grams = lex.items[t][k][1]
        score = math.fsum(weights) / math.sqrt(len(grams)) if grams else 0.0
        if score > out.get(t, 0.0):
            out[t] = score
    return out


def search_catalog(queries: list[dict], layer) -> dict:
    """{title: channel, item: channel} for the queries."""
    lex = selection_catalog(layer).lexicon()
    title, item = {}, {}
    for q in queries:
        qg = grams_v1(norm_v1(q["text"]))
        title[q["query_id"]] = _rank_rows(_title_scores(lex, qg))
        item[q["query_id"]] = _rank_rows(_item_scores(lex, qg))
    return {"title": _channel(title), "item": _channel(item)}


def fuse(channels: dict) -> list[dict]:
    """Weighted sum of max-normalised channel scores (old S5, weights 1.0), top POOL_CAP."""
    fused: dict[str, float] = {}
    channel_rank: dict[str, dict] = {}
    for name, ch in channels.items():
        rows = ch["merged"]
        if not rows:
            continue
        top = max(r["score"] for r in rows) or 1.0
        for r in rows:
            tid = r["table_id"]
            fused[tid] = fused.get(tid, 0.0) + r["score"] / top
            channel_rank.setdefault(tid, {})[name] = r["rank"]
    order = sorted(fused, key=lambda t: (-round(fused[t], 12), min(channel_rank[t].values()), t))
    return [{"table_id": t, "fused_score": round(fused[t], 6), "channels": channel_rank[t], "entered_by": "search"}
            for t in order[:POOL_CAP]]


def family_key(layer, table_id: str) -> str:
    """Family of a table: the bracket-free name shared with other tables, else the table alone."""
    return relations_of(layer).table_family.get(table_id) or f"table:{table_id}"
