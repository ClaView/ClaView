"""Record mode of the evidence checks (plan 04, 4-2 and decision 3): what each check would drop if it filtered.

Counted per requirement: an answer group is in the pool when one of its tables is a candidate *with indicator
evidence* (cover.has_evidence) of some requirement. A check drops the group when, for some requirement that has
such candidates of the group, every one of them fails the check: filtering would take the group out of that
requirement's selection. A candidate of another requirement, or one without indicator evidence, is no survival
(it could not be selected for that requirement). ``touched``: at least one evidence candidate of the group fails.
``no_evidence``: the group has candidates but none with indicator evidence.

Nothing here names a table, case or question; scripts/record_evidence.py runs it over the 417 questions.
"""

from __future__ import annotations

from .cover import has_evidence


def drop_record(got: dict, groups: list[list[str]], checks: tuple[str, ...]) -> dict:
    """{groups, in_pool, dropped{check}, touched{check}, no_evidence} of one case. ``got``: select_tables output
    (selections with ``signals.term_tables``, evidence)."""
    # tables that count as evidence without indicator words: the term tables and (plan 07 S3) the target tables
    term = {s["requirement_id"]: set((s.get("signals") or {}).get("term_tables") or [])
            | set((s.get("signals") or {}).get("target_tables") or []) for s in got["selections"]}
    by_req: dict[str, dict[str, list]] = {}
    seen: set[str] = set()
    for e in got["evidence"]["evidence"]:
        rid, tid = e["requirement_id"], e["table_id"]
        seen.add(tid)
        entered = "term" if tid in term.get(rid, set()) else "search"
        if has_evidence({"entered_by": entered}, e["checks"]):
            by_req.setdefault(rid, {})[tid] = e["checks"]
    out = {"groups": len(groups), "in_pool": 0, "dropped": {c: 0 for c in checks}, "touched": {c: 0 for c in checks},
           "no_evidence": 0}
    for g in groups:
        per_req = [[cands[t] for t in g if t in cands] for cands in by_req.values()]
        per_req = [p for p in per_req if p]
        if not per_req:
            out["no_evidence"] += any(t in seen for t in g)
            continue
        out["in_pool"] += 1
        for name in checks:
            fails = [[any(c["name"] == name and c["verdict"] == "fail" for c in chk) for chk in p] for p in per_req]
            out["dropped"][name] += any(all(f) for f in fails)
            out["touched"][name] += any(any(f) for f in fails)
    return out
