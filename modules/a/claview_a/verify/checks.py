"""Checks whose place depends on the variant (plan 04, 3): V1 requirement count, V2 selection recheck.

Both read the outer state only (``analysis``, ``selections``) and never change it; the node that calls them writes
the result to ``verification``. The certainty checks of 2.5 are read from the judgement, never computed again.

V1 code signals (plan 04, 3-1). A signal that cannot be read from what the analysis holds is recorded with status
``unavailable`` instead of being guessed:
- ``slash_in_indicator``: "A / B" inside the indicator words of one requirement whose catalog name has no "/".
- ``unused_indicator_name``: an indicator-type catalog name of the question used by no requirement (judgement
  ``unused_names``, certainty check 6).
- ``same_table_siblings_merged``: one draft requirement holding two indicator names of the same table that are not
  its breakdown (judgement-table row 2 is the legitimate one-requirement case and is skipped).
- ``relation_single_requirement``: a relation or comparison expression with a single requirement.

V2 (plan 04, 3-2): ``skipped`` without selections; otherwise the evidence recheck through
``select.cover.verify_choice`` (its floor only lowers a decision state; dropped tables are reported), the merge
signal (same indicator, same condition meaning, same tables) and the split signal (one requirement covered by tables
of different families). Without the catalog layer the recheck and the split signal are ``unavailable``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..select.candidates import family_key
from ..select.cover import verify_choice, verify_selection
from ..select import model_refs
from ..contract.check import check_analysis

Json = dict[str, Any]

INDICATOR_TYPES = ("indicator", "series_name")
RELATION_DISPLAYS = ("compare", "side_by_side")
_SLASH = re.compile(r"\S\s*/\s*\S")


def _signal(code: str, status: str, requirement_ids: list[str] | None = None, detail: Any = None) -> Json:
    return {"code": code, "status": status, "requirement_ids": list(requirement_ids or []), "detail": detail}


def _units(analysis: Json) -> tuple[str | None, list[Json]]:
    """Requirements V1 counts: settled ones when 2.7 ran, the 2.5 drafts otherwise."""
    fragments = {f["id"]: f for f in analysis.get("fragments") or []}
    interp = analysis.get("interpretation")
    if interp is not None:
        units = []
        for r in interp.get("requirements") or []:
            ind = r.get("indicator") or {}
            own = model_refs.fragment_ids(analysis, r["id"])
            units.append({"id": r["id"], "name": ind.get("name"), "quote": ind.get("quote") or r.get("quote"),
                          "fragment_ids": own if own is not None else
                          ([ind["fragment_id"]] if ind.get("fragment_id") else [])})
        return "interpretation", units
    judgement = analysis.get("judgement")
    if judgement is None:
        return None, []
    units = []
    for d in judgement.get("requirement_draft") or []:
        frag = fragments.get(d.get("indicator_fragment_id") or "")
        quote = frag["quote"] if frag else " ".join(fragments[f]["quote"] for f in d.get("fragment_ids") or []
                                                    if f in fragments)
        units.append({"id": d["id"], "name": d.get("indicator"), "quote": quote or None,
                      "fragment_ids": list(d.get("fragment_ids") or [])})
    return "judgement", units


def indicator_count(units: list[Json]) -> int:
    """Distinct indicators over the requirements; a requirement without an indicator counts as its own."""
    keys = set()
    for u in units:
        if u.get("name"):
            keys.add(("name", u["name"]))
        elif u.get("quote"):
            keys.add(("quote", u["quote"]))
        else:
            keys.add(("id", u["id"]))
    return len(keys)


def _slash_signal(units: list[Json]) -> Json:
    readable = [u for u in units if u.get("quote")]
    if not readable:
        return _signal("slash_in_indicator", "unavailable", detail="no indicator words")
    hits = [u["id"] for u in readable if _SLASH.search(u["quote"]) and "/" not in (u.get("name") or "")]
    return _signal("slash_in_indicator", "fired" if hits else "clear", hits)


def _unused_signal(analysis: Json) -> Json:
    judgement = analysis.get("judgement") or {}
    if "unused_names" not in judgement:
        return _signal("unused_indicator_name", "unavailable", detail="judgement has no unused_names")
    types: dict[str, set] = {}
    for t in analysis.get("terms") or []:
        types.setdefault(t["name"], set()).add(t["type"])
    names = list(judgement["unused_names"])
    hits = [n for n in names if types.get(n, set()) & set(INDICATOR_TYPES)]
    others = [n for n in names if n not in hits]
    return _signal("unused_indicator_name", "fired" if hits else "clear", detail={"names": hits, "other": others})


def _siblings_signal(analysis: Json) -> Json:
    judgement = analysis.get("judgement")
    if judgement is None:
        return _signal("same_table_siblings_merged", "unavailable", detail="no judgement")
    if model_refs.adopted(analysis):
        # The model has no rule-table row number. Only indicator references count as indicator siblings;
        # context and breakdown values legitimately share the same table, even when their quotes are aliases.
        fragments = {f["id"]: f for f in analysis.get("fragments") or []}
        hits = []
        for req in (analysis.get("interpretation") or {}).get("requirements") or []:
            tables: dict[str, set] = {}
            for ref in model_refs.references(analysis, req["id"]) or []:
                if ref["place"] != "indicator":
                    continue
                for c in ref["candidates"]:
                    if c["role"] in ("indicator", "table_name"):
                        tables.setdefault(c["table_id"], set()).add(c["value_name"])
                if ref["candidates"]:
                    continue
                for t in analysis.get("terms") or []:
                    f = fragments.get(t["fragment_id"])
                    if f and t["type"] in INDICATOR_TYPES and ref["span"][0] <= f["span"][0] \
                            and f["span"][1] <= ref["span"][1]:
                        for tid in t.get("table_ids") or []:
                            tables.setdefault(tid, set()).add(t["name"])
            if any(len(names) > 1 for names in tables.values()):
                hits.append(req["id"])
        return _signal("same_table_siblings_merged", "fired" if hits else "clear", hits,
                       detail="model indicator references")
    by_fragment: dict[str, list[Json]] = {}
    for t in analysis.get("terms") or []:
        if t["type"] in INDICATOR_TYPES:
            by_fragment.setdefault(t["fragment_id"], []).append(t)
    hits = []
    for d in judgement.get("requirement_draft") or []:
        if d.get("row") == 2:
            continue
        skip = set(d.get("breakdown") or [])
        tables: dict[str, set] = {}
        for fid in d.get("fragment_ids") or []:
            for t in by_fragment.get(fid, []):
                if t["name"] in skip:
                    continue
                for tid in t.get("table_ids") or []:
                    tables.setdefault(tid, set()).add(t["name"])
        if any(len(names) > 1 for names in tables.values()):
            hits.append(d["id"])
    return _signal("same_table_siblings_merged", "fired" if hits else "clear", hits, detail="draft requirement IDs")


def _relation_signal(analysis: Json, count: int) -> Json:
    found = [c["id"] for c in analysis.get("conditions") or [] if c["kind"] == "relation"
             or (c["kind"] == "display" and (c.get("attrs") or {}).get("display") in RELATION_DISPLAYS)]
    fired = bool(found) and count == 1
    return _signal("relation_single_requirement", "fired" if fired else "clear", detail={"condition_ids": found})


def check_interpretation_state(state: Json) -> Json:
    """V1: requirement-count code signals over ``state["analysis"]`` (plan 04, 3-1). jev is recorded elsewhere."""
    analysis = state.get("analysis")
    if analysis is None:
        return {"check": "V1", "status": "skipped", "source": None, "requirement_count": 0, "indicator_count": 0,
                "requirement_id": None, "signals": [], "certain": None, "failed_conditions": []}
    source, units = _units(analysis)
    judgement = analysis.get("judgement") or {}
    signals = [_slash_signal(units), _unused_signal(analysis), _siblings_signal(analysis),
               _relation_signal(analysis, len(units))]
    if analysis.get("decomposition") is not None:
        problems = check_analysis(analysis)
        signals.append(_signal("decomposition_contract", "fired" if problems else "clear", detail=problems))
    fired = [s for s in signals if s["status"] == "fired"]
    if fired:
        status = "mismatch"
    elif source is None or all(s["status"] == "unavailable" for s in signals):
        status = "unavailable"
    else:
        status = "ok"
    target = next((rid for s in fired for rid in s["requirement_ids"]), None)
    return {"check": "V1", "status": status, "source": source, "requirement_count": len(units),
            "indicator_count": indicator_count(units), "requirement_id": target, "signals": signals,
            "certain": judgement.get("certain"), "failed_conditions": list(judgement.get("failed_conditions") or [])}


# ---------------------------------------------------------------- V2


def _condition_bag(requirement: Json, conditions: dict[str, Json]) -> tuple:
    """What a requirement's own conditions mean: kind, value and all attrs (polarity, axis, period fields ...).
    The quote is left out, so the same meaning written in other words compares equal."""
    bag = []
    for cid in requirement.get("condition_ids") or []:
        c = conditions.get(cid)
        if c is None:
            bag.append(("?", cid, None))
        else:
            attrs = json.dumps(c.get("attrs") or {}, ensure_ascii=False, sort_keys=True)
            bag.append((c["kind"], c.get("value"), attrs))
    targets = [t.get("name") or t.get("quote") for t in requirement.get("targets") or []]
    return tuple(sorted(bag, key=repr)), tuple(sorted(str(t) for t in targets))


def _merge_signal(analysis: Json | None, selections: list[Json]) -> Json:
    interp = (analysis or {}).get("interpretation")
    if interp is None:
        return _signal("merge_suspected", "unavailable", detail="no interpretation")
    conditions = {c["id"]: c for c in (analysis or {}).get("conditions") or []}
    tables = {s["requirement_id"]: tuple(sorted(t["table_id"] for t in s.get("tables") or []))
              for s in selections}
    reqs = interp.get("requirements") or []
    hits = []
    for i, a in enumerate(reqs):
        for b in reqs[i + 1:]:
            name_a, name_b = (a.get("indicator") or {}).get("name"), (b.get("indicator") or {}).get("name")
            if (name_a and name_a == name_b and _condition_bag(a, conditions) == _condition_bag(b, conditions)
                    and tables.get(a["id"]) and tables.get(a["id"]) == tables.get(b["id"])):
                hits += [a["id"], b["id"]]
    return _signal("merge_suspected", "fired" if hits else "clear", hits)


STATE_ORDER = ("auto", "confirm", "no_table")  # better to worse; V2 only moves a state to the right


def _lower(current: str | None, floor: str | None) -> str | None:
    if current not in STATE_ORDER or floor not in STATE_ORDER:
        return current
    return max(current, floor, key=STATE_ORDER.index)


def _split_signal(selections: list[Json], layer: Any) -> Json:
    """Split suspicion (plan 04, 3-2): one requirement's cover needs tables of different families."""
    if layer is None:
        return _signal("split_suspected", "unavailable", detail="no catalog layer")
    hits, detail = [], {}
    for s in selections:
        tables = [t["table_id"] for t in s.get("tables") or []]
        if s.get("status") == "no_table" or len(tables) < 2:
            continue
        families = sorted({family_key(layer, t) for t in tables})
        if len(families) > 1:
            hits.append(s["requirement_id"])
            detail[s["requirement_id"]] = families
    return _signal("split_suspected", "fired" if hits else "clear", hits, detail=detail)


def check_selection_state(state: Json, layer: Any = None) -> Json:
    """V2: recheck of the chosen table groups (plan 04, 3-2). States are only lowered, never raised.

    With the catalog layer, every chosen group goes through ``select.cover.verify_choice`` again (evidence checks and
    cell cover); its ``floor`` lowers the state and its dropped tables are reported (``dropped_tables``, read by the
    router's or the part's next_alternative). Without the layer, only a requirement without tables is lowered and the
    evidence recheck and the split signal are ``unavailable``.
    """
    selections = state.get("selections") or []
    if not selections:
        return {"check": "V2", "status": "skipped", "reason": "no_selections", "states": {}, "lowered": [],
                "reinterpret_signal": None, "dropped_tables": [], "rechecks": {}, "signals": []}
    analysis = state.get("analysis")
    states, lowered, dropped, rechecks = {}, [], [], {}
    for s in selections:
        rid, current = s["requirement_id"], s.get("status")
        tables = [t["table_id"] for t in s.get("tables") or []]
        new = current
        if not tables:
            new = _lower(current, "no_table") if current in STATE_ORDER else "no_table"
        elif layer is not None and analysis is not None and current != "no_table":
            # plan 07 r5 Q6: the one recheck function (``select.cover.verify_selection``), also called before B
            ver = verify_selection(analysis, s, layer, state.get("as_of"))
            got = ver["recheck"]
            rechecks[rid] = {k: got[k] for k in ("floor", "dropped", "covered", "cells", "uncovered")}
            rechecks[rid]["period_check"] = ver["period_check"]  # Q8: handed on to B
            dropped += [t for t in got["dropped"] if t not in dropped]
            new = _lower(current, got["floor"])
        if new != current:
            lowered.append({"requirement_id": rid, "from": current, "to": new})
        states[rid] = new
    merge = _merge_signal(analysis, selections)
    split = _split_signal(selections, layer)
    if layer is None or analysis is None:
        recheck = _signal("evidence_recheck", "unavailable", detail="no catalog layer")
    else:
        recheck = _signal("evidence_recheck", "fired" if dropped or lowered else "clear",
                          [x["requirement_id"] for x in lowered], detail={"dropped": dropped})
    reinterpret = "merge" if merge["status"] == "fired" else "split" if split["status"] == "fired" else None
    flagged = bool(lowered or reinterpret or dropped)
    return {"check": "V2", "status": "flagged" if flagged else "ok", "reason": None, "states": states,
            "lowered": lowered, "reinterpret_signal": reinterpret, "dropped_tables": dropped, "rechecks": rechecks,
            "signals": [merge, split, recheck]}
