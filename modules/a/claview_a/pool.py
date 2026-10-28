"""Candidate pool of one question (plan 37, step 1).

The raw question is one query of the selector's character 2-gram search (title/path channel and value channel, top
10 per channel, 30 after fusion: ``claview_a.search.candidates.search_catalog`` and ``fuse``). Every hit brings its
whole sibling family (tables whose names are the same once bracket parts are removed: ``catalog.relations``), so that
a card always shows all siblings.

``extra`` words (later: dictionary words or the model's search words) never change the question's pool: they are
searched on their own and only add hits the question's pool lacks, at most ``EXTRA_CAP`` hit tables, after it.
The cap counts hits before family expansion: the families of those hits can bring more tables to the cards.

Nothing here names a table, case or question.
"""

from __future__ import annotations

from .search.candidates import family_key, fuse, search_catalog
from .search.families import family_members

POOL_VERSION = "tl-pool-2026-10-28.2"
EXTRA_CAP = 10  # hit tables extra words may add after the question's pool (before family expansion)


def queries_of(question: str, extra: list[str] | tuple = ()) -> list[dict]:
    """The question as one query, then each extra word (duplicates and blanks left out)."""
    out, seen = [], set()
    for text, source in [(question, "question"), *((w, "extra") for w in extra)]:
        key = (text or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append({"query_id": f"q{len(out) + 1}", "text": key, "source": source})
    return out


def build_pool(question: str, layer, extra: list[str] | tuple = ()) -> dict:
    """{version, queries, hits, families}. ``hits``: the question's fused rows in rank order, then the rows extra
    words add (``source`` "extra"). ``families``: one row per sibling family in the order of its best hit:
    {family, members (catalog order), hit_tables, best_rank, score, source}."""
    queries = queries_of(question, extra)
    own = [q for q in queries if q["source"] == "question"]
    more = [q for q in queries if q["source"] == "extra"]
    hits = [{**r, "source": "question"} for r in (fuse(search_catalog(own, layer)) if own else [])]
    if more:
        have = {r["table_id"] for r in hits}
        added = [r for r in fuse(search_catalog(more, layer)) if r["table_id"] not in have][:EXTRA_CAP]
        hits += [{**r, "source": "extra"} for r in added]
    families: dict[str, dict] = {}
    for rank, row in enumerate(hits, start=1):
        fam = family_key(layer, row["table_id"])
        entry = families.get(fam)
        if entry is None:
            entry = families[fam] = {"family": fam, "members": family_members(layer, fam), "hit_tables": [],
                                     "best_rank": rank, "score": row["fused_score"], "source": row["source"]}
        entry["hit_tables"].append(row["table_id"])
    return {"version": POOL_VERSION, "queries": queries, "hits": hits, "families": list(families.values())}


def pool_tables(pool: dict) -> list[str]:
    """Every table of the pool's families, in card order."""
    out: list[str] = []
    for entry in pool["families"]:
        out += [t for t in entry["members"] if t not in out]
    return out
