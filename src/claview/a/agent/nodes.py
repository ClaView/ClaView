"""Nodes of the outer graph (plan 04, 1-1). One function per node, the same objects in every variant.

No node branches on the variant: the variant changes only the wiring (``graph.py``) and the router's rule table
(``router.py``). Settings come from ``config["configurable"]``: ``variant`` (set by ``build_outer_graph``), ``layer``
(catalog layer, optional), ``jev`` (``verify.jev.JevSettings`` or dict, default off) and ``ask_mode`` (only
``auto``: an ask applies its default and goes on; a real interrupt is a placeholder, plan 04 decision 7).

Who writes what:
- ``intake``: every field's initial value.
- ``interpret``: ``analysis`` (and resets selections and checks, which belonged to the previous analysis).
- ``select``: ``selections``, ``selection_groups`` and the ``evidence`` summary (``select.graph.select_tables``);
  its part record also holds ``question``: what the question-level way needs to build its options (plan 07 r4 9-2,
  ``question_route.selection_notes``: readings, decision points, table labels, official names, leftover words).
- ``check_interpretation`` / ``check_selection`` (part ends, variants straight and parts) and ``verify`` (central,
  variants central and router): ``verification``, ``jev_record`` and the shared fields rebuilt from the checked
  result (V2 may lower decision states). ``check_selection`` (also inside ``verify`` of the select stage) writes
  ``selection_route`` after V2 (plan 07 r4 S5, ``question_route.route_of_state``); ``respond`` writes it when the run
  ended before selection. No router rule reads it yet (user decision 10/19).
- ``part_retry`` (part ends, variant parts): the 3-3 retry chosen by the part (``router.part_retry`` through the
  ``guard``, counted in ``control.budget``); a guard stop ends the run as it would from the router; else back to
  the router.
- ``router``: ``control``, through the pure ``route`` and the ``guard``.
- ``ask_user``: appends its ask record and the applied default to ``control``.
- ``respond``: ``response`` and the final ``a_outcome`` status.
"""

# No `from __future__ import annotations` here: LangGraph reads the `config` annotation as an object.
import copy
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from ..interpret.decompose_run import parse_config
from ..select.graph import select_tables
from ..verify import jev as jev_mod
from ..verify.checks import check_interpretation_state, check_selection_state
from . import actions
from .actions import Action
from .common import to_common
from .decomposition import execute as execute_decomposition
from .guard import LIMITS, guard
from .question_route import route_of_state, selection_notes
from .router import RouterView, make_view, route
from .router import part_retry as part_retry_rule
from .state import (ASK_USER, INTERPRET, SELECT, VARIANTS, VERIFY_INTERPRET, VERIFY_SELECT, Json,
                    make_initial_state)

RESPOND = "respond"
ROUTER = "router"
DISPATCH_TYPES = ("route", "part")  # decision records that send the run to a node

ASK_QUESTIONS = {
    "split_requirements": "질문에서 따로 볼 지표가 몇 개인가요?",
    "branch": "이 말은 다음 중 어느 통계를 뜻하나요?",
    "choose_table": "다음 표 묶음 중 어느 것으로 볼까요?",
}


def _conf(config: Any) -> dict:
    return dict((config or {}).get("configurable") or {})


def _last(state: Json, types: tuple[str, ...]) -> Json | None:
    found = [d for d in state["control"]["decisions"] if d.get("type") in types]
    return found[-1] if found else None


def _last_route(state: Json) -> Json | None:
    return _last(state, ("route",))


def _with_part(state: Json, part: str, value: Json) -> Json:
    outcome = copy.deepcopy(state.get("a_outcome") or {"stage": "A", "status": None, "parts": {}})
    outcome["parts"][part] = value
    return outcome


def _common(state: Json, verification: Json) -> Json:
    return to_common(state.get("analysis"), state.get("selections"), verification.get("selection"))


# ---------------------------------------------------------------- intake


def intake(state: Json, config: RunnableConfig | None = None) -> Json:
    if _conf(config).get("completion2") and _conf(config).get("resume_session"):
        return state
    variant = _conf(config).get("variant")
    if variant not in VARIANTS:
        raise ValueError("unknown outer-graph variant")
    question = state.get("question")
    out = make_initial_state(question, variant, as_of=state.get("as_of"), request_id=state.get("request_id"))
    cfg = parse_config(_conf(config).get("decomposition"))
    version = state.get("question_version", 1)
    if type(version) is not int or version < 1:
        raise ValueError("question_version must be a positive integer")
    out["question_version"] = version
    if cfg is not None and cfg.mode == "gpt":
        out["as_of"] = state.get("as_of") or cfg.as_of
        out["request_id"] = state.get("request_id") or str(uuid.uuid4())
    ok = isinstance(question, str) and bool(question.strip())
    out["a_outcome"]["parts"]["intake"] = {"status": "ok" if ok else "invalid_question", "reasons": []}
    if _conf(config).get("completion2"):
        from .session import initialize
        out["a_outcome"] = initialize(out, _conf(config))
    return out


# ---------------------------------------------------------------- parts


def interpret(state: Json, config: RunnableConfig | None = None) -> Json:
    """The interpretation sub-graph (``interpret_question``). Run again by ``reinterpret`` / ``retry_model``."""
    last = _last(state, DISPATCH_TYPES)
    why = last["final"]["kind"] if last else "next"
    analysis, execution, ledger = execute_decomposition(state, _conf(config))
    judgement = analysis.get("judgement") or {}
    interp = analysis.get("interpretation")
    part = {
        # done: 2.1-2.7 ran; front_only would mean 2.7 gave no interpretation
        "status": "front_only" if interp is None else "done",
        "interpretation_status": interp.get("status") if interp else None,
        "reasons": [] if why == "next" else [f"rerun:{why}"],
        "model": "off",  # 2.6 model slot: off in plan 04 (no call)
        "model_step": judgement.get("model_step"),
    }
    return {
        "analysis": analysis,
        "decomposition_ledger": ledger,
        "decomposition_runs": [*(state.get("decomposition_runs") or []), *([execution] if execution else [])],
        "selections": [],
        "selection_groups": [],
        "evidence": None,
        "verification": {"interpretation": None, "selection": None},
        "selection_route": None,
        "a_outcome": _with_part(state, INTERPRET, part),
        **to_common(analysis, None),
    }


def evidence_summary(evidence: Json | None, selections: list[Json]) -> Json:
    """What the state keeps of the evidence document (see ``state.py``): counts and the candidate pool per
    requirement, in the order of the document."""
    doc = evidence or {"evidence": [], "evidence_rows": {}}
    pool: dict[str, list[str]] = {s["requirement_id"]: [] for s in selections}
    for entry in doc["evidence"]:
        tables = pool.setdefault(entry["requirement_id"], [])
        if entry["table_id"] not in tables:
            tables.append(entry["table_id"])
    return {"kept": "summary", "pairs": len(doc["evidence"]), "checks": sum(len(e["checks"]) for e in doc["evidence"]),
            "rows": len(doc["evidence_rows"]), "pool": pool}


def select(state: Json, config: RunnableConfig | None = None) -> Json:
    """The selection sub-graph (``select_tables``, 3.1-3.8). Run again by ``next_alternative``.

    It needs the catalog layer and the settled requirements; without them nothing is selected and the part status
    says why (``no_layer``, ``no_interpretation``)."""
    if _conf(config).get("completion2") and _conf(config).get("resume_session"):
        from .session import reselect
        return reselect(state, _conf(config).get("layer"))
    last = _last(state, DISPATCH_TYPES)
    why = last["final"]["kind"] if last else "next"
    layer = _conf(config).get("layer")
    analysis = state.get("analysis")
    selections: list[Json] = []
    groups: list[Json] = []
    evidence = None
    if layer is None:
        status = "no_layer"
    elif not analysis or analysis.get("interpretation") is None:
        status = "no_interpretation"
    else:
        extra = {"selection_mode": "refutation"} if _conf(config).get("selection_mode") == "refutation" else {}
        out = select_tables(analysis, layer, state.get("as_of"),
                            candidate_policy=_conf(config).get("candidate_policy", "legacy"), **extra)
        selections, groups = out["selections"], out["selection_groups"]
        evidence = evidence_summary(out["evidence"], selections)
        status = "done"
    part = {
        "status": status,
        "reasons": [] if why == "next" else [f"rerun:{why}"],
        "model_pending": sum(1 for s in selections if s.get("model_step") == "pending"),
        "states": [s["status"] for s in selections],
    }
    if status == "done" and not _conf(config).get("completion2"):
        # plan 07 r4 9-2: what the question-level way reads to build its options (the way itself after V2)
        part["question"] = selection_notes(analysis, selections, layer, state.get("as_of"),
                                           candidate_policy=_conf(config).get("candidate_policy", "legacy"), **extra)
    return {
        "selections": selections,
        "selection_groups": groups,
        "evidence": evidence,
        "verification": {**(state.get("verification") or {}), "selection": None},
        "selection_route": None,
        "a_outcome": _with_part(state, SELECT, part),
        **to_common(analysis, selections),
    }


# ---------------------------------------------------------------- checks


def _seq(state: Json) -> int:
    """Number of the check run: decisions only grow and at least one lies between two runs of a check."""
    return len(state["control"]["decisions"])


def check_interpretation(state: Json, config: RunnableConfig | None = None) -> Json:
    """V1 and the jev record. jev reads the question only, so an existing answer is compared again, not re-asked."""
    result = {**check_interpretation_state(state), "seq": _seq(state)}
    ours = result["indicator_count"] if result["status"] != "skipped" else None
    previous = state.get("jev_record")
    if ((state.get("analysis") or {}).get("decomposition") or {}).get("requested_mode") == "gpt":
        record = {"mode": "blocked_by_decomposition", "count": None, "compare": "missing", "model": None,
                  "usage": None, "choice": None, "ours": ours}
    elif previous is not None and previous.get("count") is not None:
        record = jev_mod.recompare(previous, ours)
    else:
        record = jev_mod.record_jev(state["question"], ours, _conf(config).get("jev"))
    verification = {**(state.get("verification") or {}), "interpretation": result}
    return {"verification": verification, "jev_record": record, **_common(state, verification)}


def check_selection(state: Json, config: RunnableConfig | None = None) -> Json:
    """V2; the shared fields are rebuilt so that a lowered decision state reaches ``a_status``; then the
    question-level way (plan 07 r4 S5) over the checked state."""
    layer = _conf(config).get("layer")
    if _conf(config).get("completion2"):
        from .question_route import completion2_route
        from .session import prepare_readings, verify_resumed
        result = verify_resumed(state, layer) if _conf(config).get("resume_session") else check_selection_state(state, layer)
        verification = {**(state.get("verification") or {}), "selection": {**result, "seq": _seq(state)}}
        outcome = prepare_readings(state, layer)
        route = completion2_route({**state, "verification": verification, "a_outcome": outcome}, layer)
        return {"verification": verification, "selection_route": route, "a_outcome": outcome,
                **_common(state, verification)}
    verification = {**(state.get("verification") or {}),
                    "selection": {**check_selection_state(state, layer), "seq": _seq(state)}}
    route = route_of_state({**state, "verification": verification}, layer)
    return {"verification": verification, "selection_route": route, **_common(state, verification)}


def verify(state: Json, config: RunnableConfig | None = None) -> Json:
    """Central verification: the stage comes from the router's verify action."""
    last = _last_route(state)
    stage = last["final"]["args"].get("stage") if last else None
    if stage == INTERPRET:
        return check_interpretation(state, config)
    if stage == SELECT:
        return check_selection(state, config)
    raise ValueError("verify needs a stage")


# ---------------------------------------------------------------- ask_user


def _ask_options(kind: str, requirement_id: str | None, state: Json) -> tuple[list[Json], Json | None]:
    analysis = state.get("analysis") or {}
    if kind == "split_requirements":
        v1 = (state.get("verification") or {}).get("interpretation") or {}
        rules = v1.get("requirement_count")
        if rules is None:
            interp = analysis.get("interpretation")
            rules = len((interp or {}).get("requirements") or ((analysis.get("judgement") or {})
                                                               .get("requirement_draft") or []))
        fired = sum(1 for s in v1.get("signals") or [] if s["status"] == "fired")
        options = [{"source": "rules", "requirement_count": rules}]
        if fired:
            options.append({"source": "code_signals", "requirement_count": rules + fired})
        return options, options[0]
    if kind == "branch":
        names = []
        for t in analysis.get("terms") or []:
            if t["type"] in ("indicator", "series_name") and t["name"] not in names:
                names.append(t["name"])
        options = [{"term": n} for n in names]
        return options, (options[0] if options else None)
    chosen = next((s for s in state.get("selections") or [] if s["requirement_id"] == requirement_id), None)
    groups = [[t["table_id"] for t in (chosen or {}).get("tables", [])]]
    groups += [list(alt) for alt in (chosen or {}).get("alternatives", [])]
    options = [{"table_ids": g} for g in groups if g]
    return options, (options[0] if options else None)


def ask_user(state: Json, config: RunnableConfig | None = None) -> Json:
    if _conf(config).get("completion2"):
        from .session import wait_for_answer
        return wait_for_answer(state)
    if _conf(config).get("ask_mode", "auto") != "auto":
        raise NotImplementedError("interactive ask (interrupt and resume) is a placeholder: plan 04 decision 7")
    args = _last_route(state)["final"]["args"]
    kind, target, skipped = args["kind"], args.get("requirement_id"), bool(args.get("skipped"))
    options, default = _ask_options(kind, target, state)
    control = copy.deepcopy(state["control"])
    control["decisions"].append({
        "type": "ask",
        "step": len(control["decisions"]) + 1,
        "kind": kind,
        "requirement_id": target,
        "question": ASK_QUESTIONS[kind],
        "options": options,
        "default": default,
        "mode": "ask_skipped" if skipped else "asked",
        "answer": "default",  # automatic run: no user answer, the default is applied
        "handled": True,
    })
    control["changes"].append({"by": ASK_USER, "kind": kind, "requirement_id": target, "applied": "default"})
    control["pending_question"] = None
    return {"control": control}


# ---------------------------------------------------------------- router and part retry


def _note_failure(control: Json, state: Json) -> None:
    """Count a V1/V2 failure once per check run (the part retry and the router may both see the same run)."""
    stage = control["stage"]
    verification = state.get("verification") or {}
    if stage in (INTERPRET, VERIFY_INTERPRET):
        found = verification.get("interpretation") or {}
        kind = "v1_mismatch" if found.get("status") == "mismatch" else None
        code = next((s["code"] for s in found.get("signals") or [] if s["status"] == "fired"), None)
    elif stage in (SELECT, VERIFY_SELECT):
        found = verification.get("selection") or {}
        kind = "v2_flagged" if found.get("status") == "flagged" else None
        code = found.get("reinterpret_signal") or ("lowered" if found.get("lowered") else None)
    else:
        return
    last = control["last_failure"] or {}
    if kind and (last.get("type"), last.get("seq")) != (kind, found.get("seq")):
        control["last_failure"] = {"type": kind, "code": code, "stage": stage, "status": found.get("status"),
                                   "seq": found.get("seq")}
        control["failure_counts"][kind] = control["failure_counts"].get(kind, 0) + 1


def _prepare(state: Json, node: str) -> tuple[Json, RouterView]:
    control = copy.deepcopy(state["control"])
    control["path"].append(node)
    _note_failure(control, state)
    record = state.get("jev_record")
    control["budget"]["llm_calls"] = (1 if record and record.get("mode") == "live" else 0) + sum(
        r["model_calls"] for r in state.get("decomposition_runs") or [])
    return control, make_view({**state, "control": control})


def _record(control: Json, kind: str, view: RouterView, proposed: Action, final: Action, reason: str | None):
    control["decisions"].append({
        "type": kind,
        "step": len(control["decisions"]) + 1,
        "view": view.to_dict(),
        "proposed": proposed.to_dict(),
        "final": final.to_dict(),
        "guard_reason": reason,
    })


def _dispatch(control: Json, final: Action, view: RouterView) -> str:
    """Bookkeeping of a sent action (attempts, budget, stage, resume point); returns the next node."""
    target = actions.dispatched_node(final) or RESPOND
    if final.kind != "ask":
        control["resume_point"] = None  # the rules of the step before an ask applied once; also cleared at the end
    if target != RESPOND:
        step = actions.step_name(final)
        control["attempts"][step] = control["attempts"].get(step, 0) + 1
        control["budget"]["steps"] += 1
        if final.kind in actions.LIMITED:
            control["budget"][final.kind] += 1
        if final.kind == "ask":
            if not final.args.get("skipped"):
                control["budget"]["ask"] += 1
            if view.stage != ASK_USER:
                control["resume_point"] = view.stage
        control["stage"] = step
    control["path"].append(target)
    return target


def router(state: Json, config: RunnableConfig | None = None) -> Command:
    control, view = _prepare(state, ROUTER)
    if _conf(config).get("completion2"):
        from .session import router_action
        final = router_action(state)
        _record(control, "route", view, final, final, None)
        return Command(update={"control": control}, goto=_dispatch(control, final, view))
    proposed = route(view)
    final, reason = guard(proposed, view, LIMITS)
    _record(control, "route", view, proposed, final, reason)
    return Command(update={"control": control}, goto=_dispatch(control, final, view))


def part_retry(state: Json, config: RunnableConfig | None = None) -> Command:
    """Part end (variant parts): the same 3-3 retry as the router would choose, chosen here; else the router."""
    control, view = _prepare(state, "part_retry")
    proposed = part_retry_rule(view, view.stage)
    if proposed is None:
        return Command(update={"control": control}, goto=ROUTER)
    final, reason = guard(proposed, view, LIMITS)
    _record(control, "part", view, proposed, final, reason)
    # A guard stop is carried out here with its own reason (respond reads it), as the router would in 4-a; the
    # guard only ever returns the proposal itself or a stop for these actions.
    return Command(update={"control": control}, goto=_dispatch(control, final, view))


# ---------------------------------------------------------------- respond


def respond(state: Json, config: RunnableConfig | None = None) -> Json:
    control = state["control"]
    last = _last(state, DISPATCH_TYPES)  # the decision that sent the run here: the router's or a part-end stop
    final = last["final"] if last else {"kind": "stop", "args": {"reason": "no_decision"}}
    kind = final["kind"]
    status = {"stop": "stopped", "partial": "partial"}.get(kind, "finished")
    asks = [d for d in control["decisions"] if d.get("type") == "ask"]
    outcome = copy.deepcopy(state.get("a_outcome") or {"stage": "A", "status": None, "parts": {}})
    outcome["status"] = status
    response = {
        "status": status,
        "finished_by": kind,
        "reason": final["args"].get("reason"),
        "guard_reason": last["guard_reason"] if last else None,
        "asked": sum(1 for d in asks if d["mode"] == "asked"),
        "ask_skipped": sum(1 for d in asks if d["mode"] == "ask_skipped"),
        "parts": {name: part.get("status") for name, part in outcome["parts"].items()},
        "requirement_count": len(state.get("requirements") or []),
        "unresolved": copy.deepcopy(state.get("unresolved") or []),
        "jev_compare": (state.get("jev_record") or {}).get("compare"),
    }
    if state.get("decomposition_runs"):
        runs = state["decomposition_runs"]
        measured = [r["latency_ms"] for r in runs if r["latency_ms"] is not None]
        response["decomposition"] = {
            "visits": len(runs), "provider_calls": sum(r["provider_calls"] for r in runs),
            "fake_calls": sum(r["fake_calls"] for r in runs), "model_calls": sum(r["model_calls"] for r in runs),
            "real_calls": sum(r.get("real_calls", 0) for r in runs),
            "replay_calls": sum(r.get("replay_calls", 0) for r in runs),
            "applied_source": runs[-1]["applied_source"], "fallback_reason": runs[-1]["fallback_reason"],
            "latency_ms": sum(measured) if measured else None,
            "usage": next((r["usage"] for r in runs if r.get("usage") is not None), None)}
    if _conf(config).get("completion2"):
        from .session import public_response
        response = public_response(state, response)
        outcome["status"] = response["status"]
        return {"response": response, "a_outcome": outcome}
    out = {"response": response, "a_outcome": outcome}
    if state.get("selection_route") is None and state.get("analysis") is not None:
        # the run ended before the selection recheck (out of scope after interpretation, a stop): the way from
        # what the state holds (plan 07 r4 9-2: row 1 comes only from here in variants 3 and 4)
        out["selection_route"] = route_of_state(state, _conf(config).get("layer"))
    return out


NODES = {
    "intake": intake,
    "router": router,
    "interpret": interpret,
    "select": select,
    "verify": verify,
    "check_interpretation": check_interpretation,
    "check_selection": check_selection,
    "part_retry": part_retry,
    "ask_user": ask_user,
    "respond": respond,
}
