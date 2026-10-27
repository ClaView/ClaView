"""Interactive entry point over the existing router graph; no provider is implicit.

Session state is server-owned. Clients submit request/version/question/option IDs,
never an edited state or an analysis. Persist the returned state on the server.
"""
from __future__ import annotations

import copy
import uuid
from datetime import date

from .assumptions import build as public_assumptions
from .assumptions import digest


def session_data(state):
    return ((state.get("a_outcome") or {}).get("parts") or {}).get("session") or {}


def with_session(state, data):
    out = copy.deepcopy(state.get("a_outcome") or {"stage": "A", "status": None, "parts": {}})
    out["parts"]["session"] = data
    return out


def state_version(state):
    return digest([state.get("request_id"), state.get("question_version"), state.get("question"),
                   state.get("as_of"), state.get("analysis"), state.get("selections")])


def _catalog_version(layer):
    from ..catalog.facets import catalog_version
    return catalog_version(layer) if layer is not None else None


def run_session(question, *, layer=None, as_of=None, request_id=None, question_version=1,
                decomposition=None, decomposition_ledger=None, b_port=None):
    """New interactive runtime; rules by default. No live mode is enabled here."""
    from ..interpret.decompose_run import parse_config
    from .graph import build_outer_graph
    cfg = parse_config(decomposition)
    if cfg is not None and cfg.execution_mode == "live":
        raise ValueError("interactive live execution needs a separately approved transport")
    # Freeze the host's local calendar date once for this interactive request.
    effective_date = as_of or (cfg.as_of if cfg is not None else None) or date.today().isoformat()  # noqa: DTZ011
    date.fromisoformat(effective_date)
    if request_id is not None and (not isinstance(request_id, str) or not request_id):
        raise ValueError("request_id must be nonempty text")
    config = {"completion2": True, "layer": layer, "decomposition": decomposition,
              "decomposition_ledger": decomposition_ledger, "candidate_policy": "union",
              "selection_mode": "refutation", "jev": None}
    state = build_outer_graph("router").invoke(
        {"question": question, "as_of": effective_date, "request_id": request_id or str(uuid.uuid4()),
         "question_version": question_version}, {"configurable": config})
    return _connect(state, b_port)


def initialize(state, config):
    return with_session(state, {"status": "active", "catalog_version": _catalog_version(config.get("layer")),
                               "choices": {}, "answered_dimensions": [], "answers": {}, "readings": [],
                               "turn": 1, "b_cache": {}})


def router_action(state):
    """Execute the route already chosen by question_route, with no semantic rules."""
    from . import actions
    stage = state["control"]["stage"]
    if not isinstance(state.get("question"), str) or not state["question"].strip():
        return actions.finish("invalid_question")
    if stage is None:
        return actions.next_stage("interpret")
    if stage == "interpret":
        return actions.verify("interpret")
    if stage == "verify_interpret":
        return actions.next_stage("select")
    if stage == "select":
        return actions.verify("select")
    if stage == "verify_select":
        route = state.get("selection_route") or {}
        if route.get("kind") == "ask":
            return actions.ask("choose_table", (route.get("requirement_ids") or [None])[0])
        if route.get("kind") == "partial":
            return actions.partial()
        return actions.finish(route.get("kind", "no_result"))
    return actions.finish("awaiting_answer" if state["control"].get("pending_question") else "no_result")


def wait_for_answer(state):
    question = copy.deepcopy(state["selection_route"]["question"])
    control = copy.deepcopy(state["control"])
    control["pending_question"] = question
    control["decisions"].append({"type": "ask", "mode": "asked", "handled": False,
                                  "kind": "choose_table", "requirement_id": question["requirement_ids"][0],
                                  "question_id": question["question_id"], "answer": None})
    return {"control": control}


def prepare_readings(state, layer):
    """Only saved, verified model alternatives. No old 2.6 or fresh model call."""
    from ..select.graph import select_tables
    from ..select.readings import reading_candidates
    from ..verify.checks import check_selection_state
    data = copy.deepcopy(session_data(state))
    data["readings"] = []
    record = (state.get("analysis") or {}).get("decomposition") or {}
    if record.get("alternative") is not None and not data.get("reading_answered"):
        for alt in reading_candidates(state["analysis"], layer):
            chosen = select_tables(alt["analysis"], layer, state.get("as_of"), candidate_policy="union",
                                   selection_mode="refutation")
            check_state = {**state, "analysis": alt["analysis"], **chosen}
            verified = check_selection_state(check_state, layer)
            if chosen["selections"] and all(verified["states"].get(s["requirement_id"]) != "no_table"
                    and s.get("bundle") for s in chosen["selections"]):
                data["readings"].append({"id": alt["option"], "analysis": alt["analysis"],
                                         "selections": chosen["selections"]})
    return with_session(state, data)


def reselect(state, layer):
    from ..select.execution import select_requirement
    from ..select.state import collect_selections
    from .common import to_common
    from .nodes import evidence_summary
    data = copy.deepcopy(session_data(state))
    affected = set(data.get("affected") or [])
    old = {s["requirement_id"]: s for s in state.get("selections") or []}
    selections, records = [], []
    for req in (state["analysis"].get("interpretation") or {}).get("requirements") or []:
        rid = req["id"]
        if rid not in affected and rid in old:
            selections.append(copy.deepcopy(old[rid]))
        else:
            pref = (data.get("choices", {}).get(rid) or {}).get("tables")
            got = select_requirement(state["analysis"], rid, layer, state.get("as_of"), preferred_tables=pref)
            selections.append(got["selection"])
            records.append(got["evidence"])
    doc = {"evidence": [e for r in records for e in r["evidence"]],
           "evidence_rows": {k: v for r in records for k, v in r["evidence_rows"].items()}}
    evidence = evidence_summary(doc, selections)
    # Keep unaffected discovery evidence, not a fabricated new search.
    for rid, ids in (state.get("evidence") or {}).get("pool", {}).items():
        if rid not in affected and rid in evidence["pool"]:
            evidence["pool"][rid] = copy.deepcopy(ids)
    data["reselected"] = sorted(affected)
    return {"selections": selections, "selection_groups": collect_selections(selections), "evidence": evidence,
            "selection_route": None, **to_common(state["analysis"], selections),
            "a_outcome": with_session(state, data)}


def verify_resumed(state, layer):
    from ..verify.checks import _merge_signal, _split_signal, check_selection_state
    affected = set(session_data(state).get("affected") or [])
    subset = {**state, "selections": [s for s in state["selections"] if s["requirement_id"] in affected]}
    fresh = check_selection_state(subset, layer)
    previous = (state.get("verification") or {}).get("selection") or {}
    current_ids = {s["requirement_id"] for s in state["selections"]}
    for key in ("states", "rechecks"):
        fresh[key] = {**{k: v for k, v in previous.get(key, {}).items() if k not in affected and k in current_ids}, **fresh[key]}
    # Cross-requirement signals are cheap and must see the entire adopted reading.
    fresh["signals"] = [_merge_signal(state["analysis"], state["selections"]), _split_signal(state["selections"], layer)]
    fresh["lowered"] += [r for r in previous.get("lowered") or [] if r["requirement_id"] not in affected
                          and r["requirement_id"] in current_ids]
    fresh["dropped_tables"] = list(dict.fromkeys(t for r in fresh["rechecks"].values() for t in r.get("dropped", [])))
    fired = [s["code"] for s in fresh["signals"] if s["status"] == "fired"]
    fresh["reinterpret_signal"] = "merge" if "merge_suspected" in fired else "split" if "split_suspected" in fired else None
    fresh["status"] = "flagged" if fired or fresh["lowered"] or fresh["dropped_tables"] else "ok"
    return fresh


def resume_session(state, answer, *, layer=None, b_port=None):
    """Validate an ID-only answer against trusted stored state, then select/V2 only."""
    from .graph import build_outer_graph
    if not session_data(state):
        raise ValueError("not an interactive session")
    required = {"request_id", "question_version", "question_id", "option_id"}
    if not isinstance(answer, dict) or set(answer) != required:
        return _invalid(state, "answer_shape")
    if any(not isinstance(answer[k], str) or not answer[k] for k in ("request_id", "question_id", "option_id")):
        return _invalid(state, "answer_shape")
    if answer["request_id"] != state.get("request_id") or type(answer["question_version"]) is not int \
            or answer["question_version"] != state.get("question_version"):
        return _invalid(state, "answer_version")
    data = copy.deepcopy(session_data(state))
    old_answer = data.get("answers", {}).get(answer["question_id"])
    if old_answer is not None:
        return copy.deepcopy(state) if old_answer == answer["option_id"] else _invalid(state, "answer_already_applied")
    pending = state["control"].get("pending_question")
    if not pending or answer["question_id"] != pending["question_id"] or pending["version"] != state_version(state):
        return _invalid(state, "answer_stale")
    if data["catalog_version"] != _catalog_version(layer):
        return _invalid(state, "catalog_changed")
    option = next((o for o in pending["options"] if o["option_id"] == answer["option_id"]), None)
    if option is None:
        return _invalid(state, "unknown_option")
    out = copy.deepcopy(state)
    effect = option["effect"]
    affected = pending["requirement_ids"]
    if effect["kind"] == "change_reading":
        item = next((r for r in data["readings"] if r["id"] == effect["reading"]), None)
        if item is None:
            return _invalid(state, "reading_missing")
        out["analysis"] = copy.deepcopy(item["analysis"])
        affected = [r["id"] for r in out["analysis"]["interpretation"]["requirements"]]
        data["choices"] = {}
        data["reading_answered"] = True
        # Old B results belong to another reading, even if requirement IDs match.
        data["b_cache"] = {}
        out["verification"]["interpretation"] = None
        out["jev_record"] = None
        data["v1_invalidated"] = "reading_changed"
    elif effect["kind"] == "keep_reading":
        data["reading_answered"] = True
    elif effect["kind"] != "pick_assignment":
        return _invalid(state, "unsupported_effect")
    if effect["kind"] == "pick_assignment":
        for rid in affected:
            data["choices"][rid] = {"question_id": pending["question_id"], "option_id": option["option_id"],
                                    "tables": copy.deepcopy(effect["tables"])}
    else:
        from .assumptions import reading_id
        data["reading_choice"] = {"question_id": pending["question_id"], "option_id": option["option_id"],
                                   "reading_id": reading_id(out["analysis"])}
    # Conditions and calculation relations can couple requirements. Include their
    # explicit dependencies without reinterpreting or broadening to all requests.
    for rel in (out["analysis"].get("interpretation") or {}).get("relations") or []:
        if set(rel.get("requirement_ids") or []) & set(affected):
            affected = list(dict.fromkeys([*affected, *rel["requirement_ids"]]))
    data["affected"] = affected
    data["answers"][pending["question_id"]] = option["option_id"]
    data["answered_dimensions"].append(pending["dimension_key"])
    data["turn"] += 1
    out["a_outcome"] = with_session(out, data)
    out["control"]["pending_question"] = None
    out["control"]["stage"] = "verify_interpret"  # next dispatch is select, never interpret
    out["control"]["changes"].append({"by": "user", "question_id": pending["question_id"],
                                      "option_id": option["option_id"], "requirement_ids": affected})
    out["control"]["decisions"].append({"type": "answer", **answer})
    try:
        result = build_outer_graph("router").invoke(out, {"configurable": {
            "completion2": True, "resume_session": True, "layer": layer, "candidate_policy": "union",
            "selection_mode": "refutation", "jev": None}})
    except ValueError:
        return _invalid(state, "answer_no_longer_applicable")
    return _connect(result, b_port)


def new_question(state, question, *, layer=None, decomposition=None, b_port=None):
    return run_session(question, layer=layer, as_of=state.get("as_of"), request_id=state["request_id"],
                       question_version=state["question_version"] + 1, decomposition=decomposition,
                       decomposition_ledger=state.get("decomposition_ledger"), b_port=b_port)


def _invalid(state, reason):
    out = copy.deepcopy(state)
    out["response"]["answer_error"] = reason
    return out


def public_response(state, response):
    response = copy.deepcopy(response)
    pending = state["control"].get("pending_question")
    response["status"] = "awaiting_answer" if pending else (state.get("selection_route") or {}).get("kind", "no_result")
    response["request_id"] = state["request_id"]
    response["question_version"] = state["question_version"]
    response["assumptions"] = public_assumptions(state)
    response["automatic_progress_unsettled"] = bool(not pending and
        (state.get("selection_route") or {}).get("eligible") and
        (state.get("analysis", {}).get("judgement") or {}).get("certain") is not True)
    response["question"] = None if not pending else {k: copy.deepcopy(v) for k, v in pending.items()
                                                      if k not in ("version", "dimension_key", "options")}
    if pending:
        response["question"]["options"] = [{"option_id": o["option_id"], "label": o["label"]} for o in pending["options"]]
    response["skipped"] = copy.deepcopy((state.get("selection_route") or {}).get("skipped") or [])
    return response


def _connect(state, port):
    from .b_connection import connect
    return connect(state, port)
