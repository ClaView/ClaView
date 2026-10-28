"""Shared fields from the interpretation and selection outputs (plan 04, 5-3).

The only place that fills ``requirements``, ``unresolved`` and ``selection`` of the outer state. Their names and
meaning come from the working folder's plan 02 (``REQUIREMENT_KEYS``, ``UNRESOLVED_KEYS``, ``SELECTION_KEYS``);
this module does not define them anew.

| this copy | shared field |
|---|---|
| ``interpretation.requirements[].id`` | ``requirements[].scope_id`` |
| requirement ``quote`` | ``requirements[].text`` |
| final decision state (V2's when it ran): ``auto`` only -> ``met``; confirm / no_table keep the name | ``a_status`` |
| no selection for the requirement | ``a_status`` ``not_selected`` |
| interpretation ``Requirement.status`` and ``reasons`` | ``reason_codes`` ``interp:<status>`` + reasons |
| selection ``reasons`` | ``reason_codes`` ``select:<reason>`` |
| selection ``unspecified`` dimensions | ``gaps`` |
| ambiguous conditions, unresolved requirements, no_table | ``unresolved[{scope_id, kind, detail, from_node}]`` |
| every ``a_status`` but ``met`` (as the working folder's ``agent/nodes.py``) | ``unresolved`` ``requirement_not_met`` |
| tables of the per-requirement groups, but not of no_table ones | ``selection.tables`` (org_id, table_id, table_name) |

Without an interpretation (``interpretation`` None) there is no settled requirement: ``requirements`` is empty and
one ``unresolved`` entry of kind ``interpretation_pending`` says so.
"""

from __future__ import annotations

import copy
from typing import Any

from .state import REQUIREMENT_KEYS, SELECTION_KEYS, UNRESOLVED_KEYS

Json = dict[str, Any]
MET = "met"
NOT_SELECTED = "not_selected"
NO_TABLE = "no_table"


def _unresolved(scope_id: Any, kind: str, detail: Any, from_node: str) -> Json:
    entry = {"scope_id": scope_id, "kind": kind, "detail": detail, "from_node": from_node}
    assert tuple(entry) == UNRESOLVED_KEYS
    return entry


def _final_states(selections: list[Json], checked: Json | None) -> dict[str, str | None]:
    """Decision state per requirement: V2's lowered state when it ran on these selections, else the selection's."""
    states = {s["requirement_id"]: s.get("status") for s in selections}
    for rid, state in ((checked or {}).get("states") or {}).items():
        if rid in states:
            states[rid] = state
    return states


def _a_status(state: str | None) -> str:
    if not state:
        return NOT_SELECTED
    return MET if state == "auto" else state


def to_common(analysis: Json | None, selections: list[Json] | None, checked: Json | None = None) -> Json:
    """``{requirements, unresolved, selection}`` of the outer state from ``analysis``, ``selections`` and the V2
    result ``checked`` (``verification.selection``; optional)."""
    selections = selections or []
    by_requirement = {s["requirement_id"]: s for s in selections}
    states = _final_states(selections, checked)
    interp = (analysis or {}).get("interpretation")
    requirements: list[Json] = []
    unresolved: list[Json] = []
    if analysis is not None and interp is None:
        unresolved.append(_unresolved(None, "interpretation_pending", "front_only", "interpret"))
    for r in (interp or {}).get("requirements") or []:
        chosen = by_requirement.get(r["id"])
        reasons = [f"interp:{r['status']}", *r.get("reasons", [])]
        if chosen:
            reasons += [f"select:{x}" for x in chosen.get("reasons", [])]
        a_status = _a_status(states.get(r["id"]))
        entry = {
            "scope_id": r["id"],
            "text": r["quote"],
            "a_status": a_status,
            "gaps": copy.deepcopy((chosen or {}).get("unspecified", [])),
            "reason_codes": reasons,
            "source": "A",
        }
        assert tuple(entry) == REQUIREMENT_KEYS
        requirements.append(entry)
        for cid in r.get("ambiguous_condition_ids", []):
            unresolved.append(_unresolved(r["id"], "ambiguous_condition", cid, "interpret"))
        if r["status"] == "unresolved":
            unresolved.append(_unresolved(r["id"], "requirement_unresolved", r["status"], "interpret"))
        if a_status == NO_TABLE:
            unresolved.append(_unresolved(r["id"], NO_TABLE, (chosen or {}).get("reasons", []), "select"))
        if a_status != MET:
            unresolved.append(_unresolved(r["id"], "requirement_not_met", a_status, "A"))
    tables: list[Json] = []
    seen = set()
    for s in selections:
        if states.get(s["requirement_id"]) == NO_TABLE:
            continue
        for t in s.get("tables", []):
            if t["table_id"] in seen:
                continue
            seen.add(t["table_id"])
            tables.append({"org_id": t.get("org_id"), "table_id": t["table_id"], "table_name": t.get("table_name")})
    selection = {"tables": tables, "backups": []}
    assert tuple(selection) == SELECTION_KEYS
    return {"requirements": requirements, "unresolved": unresolved, "selection": selection}
