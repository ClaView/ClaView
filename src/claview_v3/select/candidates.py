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

import copy
import math

from ..catalog.relations import relations_of
from ..catalog.text import index_key
from ..reused.q_rules import stamp_months
from .evidence import (STOP_TERMS, TOKEN_TAIL, WORD_RE, conditions_of, items_for, requested, requirement_of, scope,
                       table_has_name, target_forms, target_names)
from .tables import grams_v1, norm_v1, selection_catalog
from . import model_refs

PATH_WEIGHT = 0.3  # a path hit is weaker evidence than a title hit (old S1)
CHANNEL_CAP = 10  # top tables kept per channel (old node_common CHANNEL_CAP)
POOL_CAP = 30  # pool after fusion (old S5 POOL_CAP)
TERM_TABLE_CAP = 30  # a term found in more tables than this is too general to add its tables
INDICATOR_TYPES = ("series_name", "indicator", "compound")
LEXICAL_NEXT = 10  # lexical candidates read for the retry words (the 2.4 path uses the first 5)
TARGET_TABLE_CAP = POOL_CAP  # plan 07 S3: tables holding every target kept in the pool of a requirement


# ---------------------------------------------------------------- 3.1


def _own_words(analysis: dict, req: dict) -> str:
    """The requirement's words with every condition blanked out (raw positions): words of two or more letters,
    particles cut, function words left out."""
    text = analysis.get("text") or {}
    raw = text.get("raw") or ""
    kept = model_refs.words(analysis, req["id"], remove_conditions=True)
    if kept is None:
        lo, hi = req.get("span") or [0, len(raw)]
        removed = [c["span"] for c in analysis.get("conditions") or [] if c.get("span")]
        kept = "".join(ch if not any(a <= k < b for a, b in removed) else " " for k, ch in enumerate(raw[:hi]))[lo:]
    words = []
    for m in WORD_RE.finditer(kept):
        word = TOKEN_TAIL.sub("", m.group(0))
        if len(word) >= 2 and word not in STOP_TERMS:
            words.append(word)
    return " ".join(words)


def build_queries(analysis: dict, requirement_id: str, extra: list[str] | None = None) -> list[dict]:
    """Search queries of one requirement: {query_id, text, source}. ``extra``: retry words (source "suggestion")."""
    req = requirement_of(analysis, requirement_id)
    ind = req.get("indicator") or {}
    rows: list[tuple[str, str]] = []
    if extra:
        rows += [(w, "suggestion") for w in extra]
    else:
        if ind.get("name"):
            rows.append((ind["name"], "term"))
        if ind.get("quote"):
            rows.append((ind["quote"], "indicator_quote"))
        if req.get("context"):
            rows.append((req["context"], "context"))
        own = _own_words(analysis, req)
        if own:
            rows.append((own, "own_words"))
        # Plan 17: an adopted basis/axis reference is useful for discovery even when
        # its words are also extracted conditions. Keep the original wording: a weak
        # catalog alias must not introduce a new measurement meaning into the query.
        qualifiers = model_refs.qualifier_words(analysis, requirement_id)
        head = ind.get("quote") or ind.get("name") or req.get("context")
        if head and qualifiers:
            rows.append((" ".join([head, *qualifiers]), "context"))
    out, seen = [], set()
    for text, source in rows:
        key = norm_v1(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"query_id": f"q{len(out) + 1}", "text": text, "source": source})
    return out


# ---------------------------------------------------------------- 3.2


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
    title: dict[str, float] = {}
    path: dict[str, float] = {}
    for g in qg:
        w = lex.idf_title.get(g, 0.0)
        for t in lex.title_inv.get(g, ()):
            title[t] = title.get(t, 0.0) + w
        for t in lex.path_inv.get(g, ()):
            path[t] = path.get(t, 0.0) + w
    out = {}
    for t in set(title) | set(path):
        a = title.get(t, 0.0) / math.sqrt(len(lex.title[t])) if lex.title[t] else 0.0
        b = path.get(t, 0.0) / math.sqrt(len(lex.path[t])) if lex.path[t] else 0.0
        out[t] = a + PATH_WEIGHT * b
    return out


def _item_scores(lex, qg: frozenset) -> dict[str, float]:
    """Best overlap of one value name per table (old S2)."""
    acc: dict[tuple[str, int], float] = {}
    for g in qg:
        w = lex.idf_item.get(g, 0.0)
        for key in lex.item_inv.get(g, ()):
            acc[key] = acc.get(key, 0.0) + w
    out: dict[str, float] = {}
    for (t, k), s in acc.items():
        grams = lex.items[t][k][1]
        score = s / math.sqrt(len(grams)) if grams else 0.0
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


# ---------------------------------------------------------------- 3.3


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


def _search_scores(channels: dict | None) -> dict[str, float]:
    best: dict[str, float] = {}
    for ch in (channels or {}).values():
        for r in ch.get("ranked_full") or []:
            best[r["table_id"]] = max(best.get(r["table_id"], 0.0), r["score"])
    return best


def term_tables(analysis: dict, requirement_id: str, channels: dict | None = None,
                cut: list[str] | None = None) -> tuple[list[str], bool]:
    """(tables, truncated): tables of the requirement's indicator and context terms (2.4), in term order. A term in
    more than TERM_TABLE_CAP tables adds its TERM_TABLE_CAP best by search score (then the term's order), and the
    cut is reported; the tables left out go to ``cut`` when given."""
    req = requirement_of(analysis, requirement_id)
    ind = req.get("indicator") or {}
    names = {n for n in (ind.get("name"), req.get("context")) if n}
    lo, hi = req.get("span") or [0, 0]
    refs = model_refs.references(analysis, requirement_id)
    own = {f["id"] for f in analysis.get("fragments") or []
           if f["id"] == ind.get("fragment_id") or (lo <= f["span"][0] and f["span"][1] <= hi)}
    scores = _search_scores(channels)
    out: list[str] = []
    truncated = False
    terms = analysis.get("terms") or []
    if refs is not None:
        terms = [{"name": r["name"], "table_ids": list(dict.fromkeys(c["table_id"] for c in r["candidates"]))}
                 for r in refs if r["place"] in ("indicator", "context") and r["name"]]
    for t in terms:
        if t.get("type") == "out_of_scope":
            continue
        named = refs is None and t.get("fragment_id") in own and t.get("type") in INDICATOR_TYPES and t.get("path") in ("exact",
                                                                                                    "dictionary")
        if t["name"] in names or named:
            ids = list(t.get("table_ids") or [])
            if len(ids) > TERM_TABLE_CAP:
                truncated = True
                ranked = sorted(ids, key=lambda x: (-scores.get(x, 0.0), ids.index(x)))
                ids = ranked[:TERM_TABLE_CAP]
                if cut is not None:
                    cut += [x for x in ranked[TERM_TABLE_CAP:] if x not in cut]
            out += [x for x in ids if x not in out]
    return out, truncated


def family_key(layer, table_id: str) -> str:
    """Family of a table: the bracket-free name shared with other tables, else the table alone."""
    return relations_of(layer).table_family.get(table_id) or f"table:{table_id}"


def catalog_alias_candidates(analysis: dict, rid: str, layer) -> list[dict]:
    """Reuse whole-name catalog abbreviation pairs already used by indicator checks.

    An abbreviation may have no title n-grams in common with its long name. Keep
    exact long-name candidates for discovery, without rewriting the interpretation
    or inferring aliases from substrings or external draft data.
    """
    ind = requirement_of(analysis, rid).get("indicator") or {}
    measure = selection_catalog(layer).measure()
    records = {}
    for word in dict.fromkeys(w for w in (ind.get("quote"), ind.get("name")) if w):
        for alias, origin in measure.forms(word):
            if origin is None:
                continue
            for row in layer.index.lookup(alias):
                if row.get("type") not in INDICATOR_TYPES or row.get("path") != "exact":
                    continue
                for tid in row["table_ids"]:
                    key = (word, alias, tid, origin["table_id"], origin["source_field"])
                    records[key] = {"table_id": tid, "quote": word, "alias": alias, "origin": dict(origin)}
    return list(records.values())


def _gives_request(analysis: dict, requirement_id: str, view, layer, as_of: str | None) -> bool:
    """A table gives a requested target (target or listed value) and touches the requested period; False when the
    requirement names neither (nothing to tell the cut tables apart)."""
    counted, _ = scope(analysis, requirement_id)
    forms = [target_forms(analysis, requirement_id, n) for n in target_names(analysis, requirement_id)]
    forms += [[(c.get("attrs") or {}).get("text") or c.get("value")] for c in conditions_of(analysis, "breakdown")
              if c["id"] in counted]
    periods, _, env = requested(analysis, requirement_id, as_of)
    if not forms and not (periods and env and env["status"] == "explicit"):
        return False
    if forms and not any(table_has_name(layer, view, f) for f in forms if f and f[0]):
        return False
    if periods and env and env["status"] == "explicit":
        lo = stamp_months(env["start"])[0]
        hi = stamp_months(env["end"])[1] if env["end"] else None
        spans = [(stamp_months(s), stamp_months(e)) for _, s, e in view.periods]
        if not any(a and b and b[1] >= lo and (hi is None or a[0] <= hi) for a, b in spans):
            return False
    return True


NO_INDICATOR = "no_indicator"  # question-level certainty failure (check 9): no requirement has an indicator term
NOT_TARGET_TYPES = ("total_header", "basis")  # r4 S3: total headers and basis terms are no targets to search by


def no_indicator(analysis: dict, requirement_id: str) -> bool:
    """Plan 07 r4 S3: the question failed check 9 (no requirement has an indicator-side term) and this requirement
    has no indicator. A requirement without an indicator next to indicator requirements is not searched by its
    targets (the question route asks the scope instead)."""
    failed = (analysis.get("judgement") or {}).get("failed_conditions") or []
    req = requirement_of(analysis, requirement_id)
    return NO_INDICATOR in failed and req.get("indicator") is None


def search_targets(analysis: dict, requirement_id: str) -> list[str]:
    """Target names to search by: the requirement's targets without total headers and basis terms."""
    types: dict[str, set] = {}
    for t in analysis.get("terms") or []:
        types.setdefault(t["name"], set()).add(t.get("type"))
    return [n for n in target_names(analysis, requirement_id)
            if not (types.get(n, set()) & set(NOT_TARGET_TYPES) and "target" not in types.get(n, set()))]


def _value_of(layer, view, forms: list[str]) -> bool:
    """One of the forms is a value of the table (any name variant of the catalog index), not a title word."""
    rel = relations_of(layer)
    for name in forms:
        if len(name) >= 2 and any(t == view.table_id and obj is not None for t, obj, _ in
                                  rel.occurrences(index_key(name))):
            return True
        if items_for(view, name):
            return True
    return False


def target_tables(analysis: dict, requirement_id: str, layer, *, union: bool = False) -> list[str]:
    """Tables holding requested values in catalog order. Legacy callers use the intersection;
    plan 17 discovery uses the union, including listed breakdown values."""
    sel = selection_catalog(layer)
    forms = [target_forms(analysis, requirement_id, n) for n in search_targets(analysis, requirement_id)]
    if union:
        counted, _ = scope(analysis, requirement_id)
        forms += [[(c.get("attrs") or {}).get("text") or c.get("value")]
                  for c in conditions_of(analysis, "breakdown") if c["id"] in counted
                  and ((c.get("attrs") or {}).get("text") or c.get("value"))]
    if not forms:
        return []
    combine = any if union else all
    return [t for t in sel.order if combine(_value_of(layer, sel.views[t], f) for f in forms)]


def _target_search(analysis: dict, requirement_id: str, layer, *, union: bool = False) -> tuple[list[dict], dict]:
    """No-indicator discovery: every table holding a requested value, without a search-score cap.
    The target-search record remains compatible with the question-route consumer."""
    found = target_tables(analysis, requirement_id, layer, union=union)
    record = {"targets": len(search_targets(analysis, requirement_id)), "tables": len(found),
              "truncated": not union and len(found) > TARGET_TABLE_CAP}
    return [{"table_id": t, "fused_score": 0.0, "channels": {}, "entered_by": "target"}
            for t in (found if union else found[:TARGET_TABLE_CAP])], record


def expand_candidates(analysis: dict, requirement_id: str, channels: dict, layer,
                      info: dict | None = None, as_of: str | None = None, *,
                      candidate_policy: str = "legacy") -> list[dict]:
    """Plan 17 discovery: union of capped lexical search, every direct indicator/context candidate,
    per-value candidates and their families. A score cap never removes a direct candidate.
    ``info.discovery`` records origins and accepted model references; evidence decides compatibility later.
    ``as_of`` remains a compatibility argument; period filtering is not a discovery decision."""
    if candidate_policy not in ("legacy", "union"):
        raise ValueError("unknown candidate policy")
    union = candidate_policy == "union"
    target_only = no_indicator(analysis, requirement_id)
    if target_only:
        pool, record = _target_search(analysis, requirement_id, layer, union=union)
        if info is not None:
            info["target_search"] = record
        if not union:
            for n, p in enumerate(pool, 1):
                p["rank"] = n
                p["family"] = family_key(layer, p["table_id"])
            return pool
    else:
        pool = fuse(channels)
    have = {p["table_id"] for p in pool}
    known = selection_catalog(layer).views
    cut: list[str] = []
    terms, truncated = term_tables(analysis, requirement_id, channels, cut)
    # Direct name candidates are finite catalog references, never a score-based search cut.
    restored = [t for t in cut if t in known and (
        union or _gives_request(analysis, requirement_id, known[t], layer, as_of))]
    terms += [t for t in restored if t not in terms]
    if info is not None:
        info["term_tables_truncated"] = False if union else truncated
        info["term_tables_restored"] = restored
    for tid in terms:
        if tid in known and tid not in have:
            pool.append({"table_id": tid, "fused_score": 0.0, "channels": {}, "entered_by": "term"})
            have.add(tid)
    targets = target_tables(analysis, requirement_id, layer, union=True) if union else []
    for ref in (model_refs.references(analysis, requirement_id) or []) if union else []:
        if ref["place"] in ("target", "breakdown_value"):
            targets += [c["table_id"] for c in ref["candidates"]
                        if c["table_id"] in known and c["table_id"] not in targets]
    targets = list(dict.fromkeys(targets))
    for tid in targets:
        if tid not in have:
            pool.append({"table_id": tid, "fused_score": 0.0, "channels": {}, "entered_by": "target"})
            have.add(tid)
    qualifiers = model_refs.search_qualifiers(analysis, requirement_id) if union else []
    qualifier_tables = list(dict.fromkeys(c["table_id"] for r in qualifiers
                                          for c in r.get("candidates") or [] if c["table_id"] in known))
    for tid in qualifier_tables:
        if tid not in have:
            pool.append({"table_id": tid, "fused_score": 0.0, "channels": {}, "entered_by": "term"})
            have.add(tid)
    aliases = catalog_alias_candidates(analysis, requirement_id, layer) if union else []
    for row in aliases:
        tid = row["table_id"]
        if tid in known and tid not in have:
            pool.append({"table_id": tid, "fused_score": 0.0, "channels": {}, "entered_by": "catalog_alias"})
            have.add(tid)
    if info is not None and union:
        info["discovery"] = {
            "direct": list(terms), "values": list(targets), "direct_cut": [],
            "qualifiers": qualifier_tables,
            "catalog_aliases": aliases,
            "references": copy.deepcopy([r for r in model_refs.references(analysis, requirement_id) or []
                                         if r["place"] in ("indicator", "context", "target", "breakdown_value")]
                                        + qualifiers),
        }
    rel = relations_of(layer)
    for p in list(pool):
        fam = rel.table_family.get(p["table_id"])
        for tid in rel.family_tables.get(fam, []) if fam else []:
            if tid in known and tid not in have:
                pool.append({"table_id": tid, "fused_score": 0.0, "channels": {}, "entered_by": "family"})
                have.add(tid)
    for n, p in enumerate(pool, 1):
        p["rank"] = n
        p["family"] = family_key(layer, p["table_id"])
    if info is not None and union:
        info["discovery"]["origins"] = [
            {"table_id": p["table_id"], "sources": list(dict.fromkeys([
                p["entered_by"], *(["term"] if p["table_id"] in terms else []),
                *(["target"] if p["table_id"] in targets else []),
                *(["qualifier"] if p["table_id"] in qualifier_tables else []),
                *(["catalog_alias"] if any(r["table_id"] == p["table_id"] for r in aliases) else [])]))}
            for p in pool]
    return pool


# ---------------------------------------------------------------- retry words


def word_suggestions(word: str, layer, top: int = LEXICAL_NEXT) -> list[str]:
    """Catalog words for one word, the way ``suggestions`` finds them: dictionary synonyms' official names, then the
    lexical candidates (plan 07 r4 9-1: a leftover word has a candidate when this is not empty)."""
    out: list[str] = []
    for entry in layer.synonyms.lookup(word):
        out += [o for o in entry.officials if o not in out]
    rel = relations_of(layer)
    for key, _score in rel.lexical(index_key(word), top=top):
        name = rel.key_names.get(key, key)
        if name not in out:
            out.append(name)
    return out


def suggestions(analysis: dict, requirement_id: str, layer, used: list[str]) -> list[str]:
    """Words for the one retry (plan 04, 4-1): dictionary synonyms of the indicator words (their official names)
    and the lexical candidates after the first ones, minus every word already searched."""
    req = requirement_of(analysis, requirement_id)
    ind = req.get("indicator") or {}
    words = [w for w in (ind.get("quote"), ind.get("name")) if w]
    used_keys = {norm_v1(u) for u in used} | {norm_v1(t["name"]) for t in analysis.get("terms") or []}
    out: list[str] = []

    def add(word: str) -> None:
        k = norm_v1(word)
        if k and k not in used_keys and word not in out:
            out.append(word)

    for w in words:
        for entry in layer.synonyms.lookup(w):
            for official in entry.officials:
                add(official)
    rel = relations_of(layer)
    for w in words:
        for key, _score in rel.lexical(index_key(w), top=LEXICAL_NEXT):
            add(rel.key_names.get(key, key))
    return out
