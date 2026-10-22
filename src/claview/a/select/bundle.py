"""Bundle-level common table (plan 07 r4 S2, decision 5 and 9-5). Model off: no call.

A bundle is a group of requirements the interpretation reads from one table: ``interpretation.table_group_hint``
only (joined transitively; relations do not bundle until the interpretation marks confirmed operands; unsupported
requirements stay out).

After every requirement of the bundle has its cover (3.5) and choice (3.6):
1. the common family (plan 07 r5 Q0, bundle first: the bundle's (requirement, cell) pairs are covered at once): a
   family (not "mixed") with an alternative for every requirement of the bundle that covers the most pairs (complete
   alternatives first); then the family most requirements already chose, the higher rank-score sum, the family key;
2. the bundle cells are the (requirement, cell) pairs of every requirement; a family table provides the pairs it
   provides to each requirement (each requirement's own evidence checks: the family tables are in every pool, the
   family being complete for each one);
3. the representative step (survey, source, frequency, rank; ``cover.representatives``) runs once over the bundle
   cells, with the bundle rank (the sum of the requirements' rank scores) and the basis-word standing of every
   requirement; under the keep_all policy the union of the family alternatives is taken as it is;
4. each requirement takes from the bundle tables a least cover of its own cells. It is replaced only when its own
   conditions hold on the new tables as well as on its current ones (recheck floor not lower, no more unproven
   conditions) and its indicator evidence is not weaker; the state is never raised: a replaced requirement is
   confirm with reason ``group_family`` (9-5), after the recheck (4-5) and decide_state run again.
Nothing here names a table, case or question.
"""

from __future__ import annotations

from .cover import INDICATOR_VALUE, best_cover, family_members, indicator_result, representatives


def bundles(analysis: dict) -> list[list[str]]:
    """Requirement ID groups of two or more from ``table_group_hint``, in requirement order."""
    interp = analysis.get("interpretation") or {}
    ids = [r["id"] for r in interp.get("requirements") or [] if r.get("status") != "unsupported"]
    parent = {r: r for r in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for hint in interp.get("table_group_hint") or []:
        group = [g for g in hint if g in parent]
        for a, b in zip(group, group[1:]):
            parent[find(b)] = find(a)
    out: dict[str, list[str]] = {}
    for r in ids:
        out.setdefault(find(r), []).append(r)
    return [g for g in out.values() if len(g) >= 2]


def _alt_of(cover: dict, family: str) -> dict | None:
    return next((a for a in cover.get("alternatives") or [] if a["family"] == family and a["complete"]), None)


def _best_alt(cover: dict, family: str) -> dict | None:
    alts = [a for a in cover.get("alternatives") or [] if a["family"] == family]
    return max(alts, key=lambda a: (a["complete"], len(a["covered"])), default=None)


def common_family(members: list[str], covers: dict[str, dict], choices: dict[str, dict],
                  log: list | None = None) -> str | None:
    """Plan 07 r5 Q0 (bundle first): the family that covers the most (requirement, cell) pairs of the bundle over
    every member at once (complete alternatives first, a family not "mixed" with an alternative for every member);
    then the family most members chose, the higher rank-score sum, the family key. None without such a family.
    ``log`` (plan 11 R0, record only): receives the families left out and the key part that decided."""
    families = None
    for r in members:
        fams = {a["family"] for a in covers[r].get("alternatives") or [] if a["family"] != "mixed"}
        families = fams if families is None else families & fams
    if not families:
        return None

    def key(fam: str):
        alts = [_best_alt(covers[r], fam) for r in members]
        complete = sum(1 for a in alts if a and a["complete"])
        covered = sum(len(a["covered"]) for a in alts if a)
        chose = sum(1 for r in members if (((choices.get(r) or {}).get("chosen")) or {}).get("family") == fam)
        score = sum(a.get("score_sum", 0.0) for a in alts if a)
        return (-complete, -covered, -chose, -score, fam)

    best = min(families, key=key)
    if log is not None:
        reasons = ("cells", "cells", "chosen", "score", "family_key")
        for fam in sorted(families - {best}):
            a, b = key(fam), key(best)
            part = next((k for k in range(len(a)) if a[k] != b[k]), len(a) - 1)
            log.append({"step": "common_family", "family": fam, "kept": best, "reason": reasons[part]})
    return best


def bundle_tables(family: str, members: list[str], covers: dict[str, dict], layer, views: dict,
                  policy: str, log: list | None = None) -> tuple[list[str], list[dict]]:
    """(tables, marks) of the bundle: the representative step once over the bundle cells (steps 2-3)."""
    chosen: list[str] = []
    for r in members:
        chosen += [t for t in (_alt_of(covers[r], family) or _best_alt(covers[r], family) or {}).get("tables", [])
                   if t not in chosen]
    if policy != "representative":
        return chosen, []
    fam_tables = [t for t in family_members(layer, family) if t in views]
    for t in chosen:
        if t not in fam_tables:
            fam_tables.append(t)
    prov = {t: {(r, c) for r in members for c in covers[r]["provided"].get(t, set()) & covers[r]["cells"]}
            for t in fam_tables}
    cells = {(r, c) for r in members for c in covers[r]["cells"]}
    usable = {t for r in members for t in covers[r]["usable"] if t in prov}
    siblings = {t for r in members for t in covers[r].get("siblings") or [] if t in prov}
    named = {t for r in members for t in covers[r].get("named") or []}
    words = {t: {(r, cid): s for r in members for cid, s in (covers[r].get("words") or {}).get(t, {}).items()}
             for t in fam_tables}
    score = {t: sum((covers[r].get("scores") or {}).get(t, 0.0) for r in members) for t in fam_tables}
    order = {t: min((covers[r].get("order") or {}).get(t, 10 ** 6) for r in members) for t in fam_tables}
    tables, marks, _ = representatives(chosen, {c: fam_tables for c in chosen}, prov, usable, views, words, score,
                                       order, cells, siblings, named, log)
    return tables, marks


def own_tables(tables: list[str], cover: dict) -> list[str]:
    """The requirement's least cover of its own cells from the bundle tables (step 4)."""
    usable = [t for t in tables if t in cover["provided"]]
    got = best_cover(usable, cover["cells"], cover["provided"], cover.get("scores") or {},
                     {t: (cover.get("order") or {}).get(t, 0) for t in usable})
    return list(got["tables"])


def indicator_tier(evidence: dict | None, rid: str, tables: list[str]) -> float:
    """Weakest indicator evidence over ``tables`` (match 1, partial 0.5, no indicator words 0.5, none 0)."""
    checks = {e["table_id"]: e["checks"] for e in (evidence or {}).get("evidence") or []
              if e["requirement_id"] == rid}
    values = []
    for t in tables:
        res = indicator_result(checks.get(t, []))
        values.append(INDICATOR_VALUE.get(res, 0.5 if res is None else 0.0))
    return min(values) if values else 0.0


FLOOR_ORDER = ("no_table", "confirm", "auto")  # worse to better


def no_worse(new: dict | None, old: dict | None) -> bool:
    """The recheck of the new tables holds the requirement's own conditions as well as the old one."""
    if not new:
        return False
    if not old:
        return True
    return (FLOOR_ORDER.index(new["floor"]) >= FLOOR_ORDER.index(old["floor"])
            and len(new.get("unproven") or []) <= len(old.get("unproven") or []))
