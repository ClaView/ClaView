"""Plan 17 cover kernel over independently checked table/cell facts.

Callers establish a comparison frame (meaning, unit, basis and period context) before
combining tables. A family key by itself is not such a frame. A search frame does not
prove compatibility: callers must validate every cell before marking it proven.
"""

from __future__ import annotations

from .cover import enumerate_assignments


def rank_assignments(cells: set, possible: dict[str, set], proven: dict[str, set],
                     scores: dict[str, float], frames: dict[str, str | None], *,
                     supported: dict[str, set] | None = None,
                     cap: int | None = None, combos: int | None = None) -> dict:
    """Enumerate both proof and possibility covers, retaining unused candidates as singletons.

    A possible singleton must not suppress a supported or proven multi-table cover.
    Tables without a comparison frame stay singletons. Proof/possibility sets have the same domain,
    with proven <= possible <= requested cells. No table removal is performed here.
    """
    if set(proven) != set(possible) or set(frames) != set(possible):
        raise ValueError("allocation table domains differ")
    if any(not proven[t] <= possible[t] <= cells for t in possible):
        raise ValueError("invalid cell proof inclusion")
    if supported is not None and (set(supported) != set(possible) or
                                  any(not supported[t] <= possible[t] for t in possible)):
        raise ValueError("invalid support domain")
    if cap is not None and cap < 1 or combos is not None and combos < 1:
        raise ValueError("allocation limits must be positive")
    table_order = {t: i for i, t in enumerate(possible)}
    groups: dict[tuple, list[str]] = {}
    for t, frame in frames.items():
        # Unknown frames never connect by name, family or an identical None value.
        key = ("comparison", frame) if frame is not None else ("unknown", t)
        groups.setdefault(key, []).append(t)
    combinations: set[tuple[str, ...]] = {(t,) for t in possible}
    cut_reasons, searches = [], []
    for frame, tables in groups.items():
        searches_for = [("proven", proven), ("possible", possible)]
        if supported is not None:
            searches_for.append(("supported", supported))
        for kind, supplied in searches_for:
            found = enumerate_assignments(tables, cells, supplied, scores, cap=cap, combos=combos)
            searches.append({"frame": list(frame), "kind": kind, "tried": found["tried"], "cut": found["cut"]})
            for row in [*found["covers"], *found["partial"]]:
                combinations.add(tuple(row["units"]))
            if found["cut"]:
                cut_reasons.append({"kind": "cover_cut", "frame": list(frame), "proof": kind,
                                    "unexplored_tables": found["cut_units"]})
    assignments = []
    for tables in combinations:
        may = set().union(*(possible[t] for t in tables))
        proof = set().union(*(proven[t] for t in tables))
        status = "proven" if cells and proof == cells else "possible" if cells and may == cells else "partial"
        assignments.append({"tables": list(tables), "proof_status": status,
                            "proven": proof, "possible": may, "missing": cells - may,
                            "by_table": {t: {"proven": set(proven[t]), "possible": set(possible[t])}
                                         for t in tables},
                            "order": [("proven", "possible", "partial").index(status),
                                      -len(may), len(tables),
                                      -round(sum(scores.get(t, 0.0) for t in tables), 6),
                                      [table_order[t] for t in tables]]})
    assignments.sort(key=lambda row: row["order"])
    for i, row in enumerate(assignments, 1):
        row["id"] = f"a{i}"
    return {"assignments": assignments, "selected_assignment_id": next(
                (a["id"] for a in assignments if a["possible"]), None),
            "search_complete": not cut_reasons, "cut_reasons": cut_reasons, "searches": searches}
