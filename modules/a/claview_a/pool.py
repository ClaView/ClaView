"""Candidate pool of one question (plan 37, step 1).

The raw question is one query of the selector's character 2-gram search (title/path channel and value channel, top
10 per channel, 30 after fusion: ``claview_a.search.candidates.search_catalog`` and ``fuse``). Every hit brings its
whole sibling family (tables whose names are the same once bracket parts are removed: ``catalog.relations``), so that
a card always shows all siblings.

``extra`` words (the model's search words for a re-search) never change the question's pool: they are searched
on their own with the same settings and only add hits of tables the question's cards do not already show, at most
``EXTRA_CAP`` hit tables, after it. The cap counts hits before family expansion: the families of those hits can
bring more tables to the cards. What the cap leaves out is recorded (``extra``), never dropped silently.

Nothing here names a table, case or question.
"""

from __future__ import annotations

from .search.candidates import family_key, fuse, search_catalog
from .search.families import family_members

# .3: extra hits skip tables already on the question's cards; found/added/cut extra hits are recorded
POOL_VERSION = "tl-pool-2026-10-29.3"
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
    {family, members (catalog order), hit_tables, best_rank, score, source}. With extra words, ``extra``:
    {cap, found, added, cut} (hit tables new to the cards, before the cap; those kept; those the cap left out)."""
    queries = queries_of(question, extra)
    own = [q for q in queries if q["source"] == "question"]
    more = [q for q in queries if q["source"] == "extra"]
    hits = [{**r, "source": "question"} for r in (fuse(search_catalog(own, layer)) if own else [])]
    extra_row = None
    if more:
        have = {t for r in hits for t in family_members(layer, family_key(layer, r["table_id"]))}
        found = [r for r in fuse(search_catalog(more, layer)) if r["table_id"] not in have]
        hits += [{**r, "source": "extra"} for r in found[:EXTRA_CAP]]
        extra_row = {"cap": EXTRA_CAP, "found": [r["table_id"] for r in found],
                     "added": [r["table_id"] for r in found[:EXTRA_CAP]],
                     "cut": [r["table_id"] for r in found[EXTRA_CAP:]]}
    families: dict[str, dict] = {}
    for rank, row in enumerate(hits, start=1):
        fam = family_key(layer, row["table_id"])
        entry = families.get(fam)
        if entry is None:
            entry = families[fam] = {"family": fam, "members": family_members(layer, fam), "hit_tables": [],
                                     "best_rank": rank, "score": row["fused_score"], "source": row["source"]}
        entry["hit_tables"].append(row["table_id"])
    out = {"version": POOL_VERSION, "queries": queries, "hits": hits, "families": list(families.values())}
    if extra_row is not None:
        out["extra"] = extra_row
    return out


def pool_tables(pool: dict) -> list[str]:
    """Every table of the pool's families, in card order."""
    out: list[str] = []
    for entry in pool["families"]:
        out += [t for t in entry["members"] if t not in out]
    return out
