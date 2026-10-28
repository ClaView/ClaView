"""The table-selection sub-graph (``select_tables``, plan 04 4-1; node map v3 section 3). Model off: no call.

    split_by_requirement --(one Send per requirement)--> select_requirement --> collect_selections

    select_requirement runs the per-requirement graph:
    START -> build_queries -> search_catalog -> expand_candidates -> check_evidence
      check_evidence: a candidate with evidence -> cover_cells -> choose_table -> verify_choice -> decide_state
                      none, retry not used and retry words found -> retry_words -> build_queries (once)
                      none otherwise -> decide_state (no_table)
    START -> decide_state when the requirement is out of scope (unsupported)

collect_selections first covers every ``table_group_hint`` bundle once more (plan 07 r4 S2, ``apply_bundles``,
``bundle.py``): a requirement that takes the bundle's tables runs the recheck and decide_state again.

Nodes share only the state below; the catalog layer comes from ``config["configurable"]["layer"]``.
``select_tables(analysis, layer, as_of, answers=None)`` is the entry point: {selections, selection_groups, evidence,
facts, decisions} (plan 11 R1: ``answers`` is shape-checked only, not applied yet).
"""

# No `from __future__ import annotations` here: LangGraph reads the `config` annotation as an object.
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from . import candidates as cand
from . import decision as decision_mod
from . import facts as facts_mod
from .bundle import bundle_tables, bundles, common_family, indicator_tier, no_worse, own_tables
from .cover import DEFAULT_POLICY
from .cover import cover_cells as cover_cells_fn
from .cover import has_evidence, period_check, question_words, verify_choice as verify_choice_fn
from .differences import dimension_record
from .evidence import check_evidence as check_evidence_fn
from .evidence import requirement_of
from .input import selection_input
from .state import choose_table as choose_table_fn
from .state import collect_selections as collect_fn
from .state import decide_state as decide_state_fn
from .tables import selection_catalog


class RequirementState(TypedDict, total=False):
    analysis: dict
    requirement_id: str
    as_of: Any
    suggested: list
    used_words: list
    queries: list
    channels: dict
    pool: list
    pool_info: dict
    evidence: dict
    with_evidence: list
    retried: bool
    cover: dict
    choice: dict
    recheck: dict
    selection: dict
    bundle: dict  # plan 07 S2: the bundle record put in the selection signals
    pool_cut: dict  # plan 11 R0: which caps cut search candidates (record only)
    facts: dict  # plan 11 R0: evidence facts per candidate (record only)
    pruned_bundle: list  # plan 11 R0: what the bundle step left out (record only)


class SelectState(TypedDict, total=False):
    analysis: dict
    as_of: Any
    results: Annotated[list, operator.add]
    selections: list
    selection_groups: list
    evidence: dict


def _layer(config: Any):
    layer = ((config or {}).get("configurable") or {}).get("layer")
    if layer is None:
        raise ValueError("select_tables needs a catalog layer")
    return layer


# ---------------------------------------------------------------- per-requirement nodes


def build_queries(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    queries = cand.build_queries(state["analysis"], state["requirement_id"], state.get("suggested"))
    used = list(state.get("used_words") or []) + [q["text"] for q in queries]
    return {"queries": queries, "used_words": used}


def search_catalog(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    return {"channels": cand.search_catalog(state["queries"], _layer(config))}


def expand_candidates(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    info: dict = {}
    pool = cand.expand_candidates(state["analysis"], state["requirement_id"], state["channels"], _layer(config), info,
                                  state.get("as_of"),
                                  candidate_policy=((config or {}).get("configurable") or {}).get("candidate_policy", "legacy"))
    cut = facts_mod.pool_cut(state["analysis"], state["requirement_id"], state["channels"], pool, _layer(config),
                             info)
    return {"pool": pool, "pool_info": info, "pool_cut": cut}


def check_evidence(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    rid = state["requirement_id"]
    doc = check_evidence_fn(state["analysis"], [p["table_id"] for p in state["pool"]], _layer(config),
                            state.get("as_of"), requirement_ids=[rid])
    by_table = {e["table_id"]: e["checks"] for e in doc["evidence"]}
    found = [p["table_id"] for p in state["pool"] if has_evidence(p, by_table.get(p["table_id"], []))]
    return {"evidence": doc, "with_evidence": found}


def retry_words(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    words = cand.suggestions(state["analysis"], state["requirement_id"], _layer(config),
                             state.get("used_words") or [])
    return {"suggested": words, "retried": True}


def cover_cells(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    return {"cover": cover_cells_fn(state["analysis"], state["requirement_id"], state["pool"], state["evidence"],
                                    _layer(config), state.get("as_of"))}


def choose_table(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    policy = ((config or {}).get("configurable") or {}).get("policy")
    return {"choice": choose_table_fn(state["cover"], _layer(config), policy)}


def verify_choice(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    choice = state["choice"]
    if not choice.get("chosen"):
        return {"recheck": None}
    tables = list(dict.fromkeys(choice.get("tables") or choice["chosen"]["tables"]))
    return {"recheck": verify_choice_fn(state["analysis"], state["requirement_id"], tables, _layer(config),
                                        state.get("as_of"), candidates=state["cover"]["usable"])}


def decide_state(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    layer = _layer(config)
    analysis, rid = state["analysis"], state["requirement_id"]
    req = requirement_of(analysis, rid)
    unsupported = req.get("status") == "unsupported"
    choice, recheck, cover = state.get("choice"), state.get("recheck"), state.get("cover") or {}
    no_evidence = not unsupported and not state.get("with_evidence")
    if UNGROUNDED in (req.get("reasons") or []) and not (recheck and recheck["floor"] == "auto"
                                                       and not recheck.get("unproven")):
        # user decision 10/19 21:00: a split piece no catalog table holds: no table ("not in the catalog"), never
        # another table in its place (the question gets a partial answer)
        status, reasons = "no_table", [NOT_IN_CATALOG]
    else:
        status, reasons = decide_state_fn(analysis, rid, unsupported=unsupported, no_evidence=no_evidence,
                                          choice=choice, recheck=recheck, cover_flags=cover.get("flags") or [],
                                          signals=state.get("pool_info") or {})
        if status != "no_table" and _indicator_unchecked(req, state.get("evidence"), rid,
                                                         [t["table_id"] for t in (recheck or {}).get("tables") or []]):
            # plan 10 (user 10/20 02:28): an indicator no candidate could be checked against is never decided here
            status, reasons = "confirm", [*reasons, INDICATOR_UNCHECKED]
    views = selection_catalog(layer).views
    tables = []
    if status != "no_table":
        for t in recheck["tables"]:
            v = views[t["table_id"]]
            tables.append({"table_id": t["table_id"], "org_id": v.org_id, "table_name": v.title,
                           "covers": t["covers"]})
    kept = {t["table_id"] for t in tables}
    marks = [{**m, "table_ids": [t for t in m["table_ids"] if t in kept]}
             for m in (choice or {}).get("unspecified", [])]
    marks = [m for m in marks if m["table_ids"]]
    chosen = (choice or {}).get("chosen") or {}
    others = [list(a["tables"]) for a in (choice or {}).get("contenders", []) if a["family"] != chosen.get("family")]
    selection = {
        "requirement_id": rid, "tables": tables, "unspecified": marks if status != "no_table" else [],
        "status": status, "model_step": (choice or {}).get("model_step") or "skipped", "reasons": reasons,
        "alternatives": others, "differences": (choice or {}).get("differences") or [],
        "signals": {
            "pool": len(state.get("pool") or []), "with_evidence": len(state.get("with_evidence") or []),
            "retried": bool(state.get("retried")), "cells": len(cover.get("cells") or []),
            "covered": (recheck or {}).get("covered", 0), "alternatives": len(cover.get("alternatives") or []),
            "queries": [q["text"] for q in state.get("queries") or []],
            "term_tables": [p["table_id"] for p in state.get("pool") or [] if p["entered_by"] == "term"],
            "term_tables_truncated": bool((state.get("pool_info") or {}).get("term_tables_truncated")),
            "conventions": [c["table_id"] for c in (choice or {}).get("conventions") or []],
            # plan 07 r5: the score gap of the first two alternatives (recorded only, Q5) and the period mark for B
            # (Q8: the table stays)
            "score_gap": _score_gap(cover),
            "period_check": period_check(recheck) if status != "no_table" else False,
        },
    }
    items = (cover.get("score_items") or {})
    if items and tables:  # plan 07 r5 Q3: the score items of the chosen tables, with their reasons
        selection["signals"]["score_items"] = {t["table_id"]: items.get(t["table_id"], []) for t in tables}
    if (state.get("pool_info") or {}).get("target_search") is not None:  # plan 07 r4 S3
        record = dict(state["pool_info"]["target_search"])
        # families counted after the evidence checks: over the tables that give a requested cell
        firsts: dict[str, str] = {}
        for t in cover.get("usable") or []:
            firsts.setdefault(cand.family_key(layer, t), t)
        record["families"] = len(firsts)
        record["family_tables"] = list(firsts.values())  # one table per family (ask options)
        selection["signals"]["target_search"] = record
        selection["signals"]["target_tables"] = [p["table_id"] for p in state.get("pool") or []
                                                 if p["entered_by"] == "target"]
    if state.get("bundle") is not None:  # plan 07 S2
        selection["signals"]["bundle"] = dict(state["bundle"])
    # plan 11 R0 (7-2): the new records, in new fields only; nothing above reads them
    facts = state.get("facts")
    if facts is None:
        facts = facts_mod.requirement_facts(analysis, rid, state.get("pool") or [], state.get("evidence"), layer,
                                            state.get("as_of"))
    selection["signals"]["facts"] = facts
    selection["signals"]["selection_input"] = selection_input(analysis, rid, layer, state.get("as_of"))
    selection["signals"]["covers_all"] = cover.get("covers_all") or {}
    selection["signals"]["pool_cut"] = state.get("pool_cut") or {"caps": {}, "cut": 0, "matched_count": 0,
                                                                  "matched": [], "pool_cut": False}
    if (state.get("pool_info") or {}).get("discovery") is not None:
        selection["signals"]["candidate_discovery"] = state["pool_info"]["discovery"]
    selection["signals"]["pruned"] = facts_mod.pruned_record(cover, choice, state.get("pruned_bundle"), selection)
    return {"selection": selection, "facts": facts}


INDICATOR_UNCHECKED = "indicator_unchecked"


def _indicator_unchecked(req: dict, evidence: dict | None, rid: str, chosen: list[str]) -> bool:
    """The requirement has an indicator, its indicator check is n/a (no indicator phrase) on every candidate, and no
    chosen table holds a value the question writes exactly (its evidence is then only the search or a term table)."""
    if not req.get("indicator"):
        return False
    entries = [e for e in (evidence or {}).get("evidence") or [] if e["requirement_id"] == rid]
    checks = [c for e in entries for c in e["checks"] if c["name"] == "indicator"]
    if not checks or any((c.get("detail") or {}).get("available") for c in checks):
        return False
    exact = any(row.get("condition_id") is None and row.get("polarity") == "+" and row.get("match_method") == "exact"
                for e in entries if e["table_id"] in chosen for c in e["checks"] if c["name"] == "matched_items"
                for row in (c.get("detail") or {}).get("rows") or [])
    return not exact


def _score_gap(cover: dict) -> float | None:
    """Rank-score gap between the first two family alternatives in their order (plan 07 r5 Q5: recorded, its
    threshold is set after measurement)."""
    from .cover import order_alternatives

    alts = order_alternatives(cover.get("alternatives") or [])
    if len(alts) < 2:
        return None
    return round(alts[0].get("score_sum", 0.0) - alts[1].get("score_sum", 0.0), 6)


def _after_start(state: RequirementState) -> str:
    req = requirement_of(state["analysis"], state["requirement_id"])
    return "decide_state" if req.get("status") == "unsupported" else "build_queries"


def _after_evidence(state: RequirementState) -> str:
    if state.get("with_evidence"):
        return "cover_cells"
    return "decide_state" if state.get("retried") else "retry_words"


def _after_retry(state: RequirementState) -> str:
    return "build_queries" if state.get("suggested") else "decide_state"


def build_requirement_graph():
    g = StateGraph(RequirementState)
    for name, fn in (("build_queries", build_queries), ("search_catalog", search_catalog),
                     ("expand_candidates", expand_candidates), ("check_evidence", check_evidence),
                     ("retry_words", retry_words), ("cover_cells", cover_cells), ("choose_table", choose_table),
                     ("verify_choice", verify_choice), ("decide_state", decide_state)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, _after_start, ["build_queries", "decide_state"])
    g.add_edge("build_queries", "search_catalog")
    g.add_edge("search_catalog", "expand_candidates")
    g.add_edge("expand_candidates", "check_evidence")
    g.add_conditional_edges("check_evidence", _after_evidence, ["cover_cells", "retry_words", "decide_state"])
    g.add_conditional_edges("retry_words", _after_retry, ["build_queries", "decide_state"])
    g.add_edge("cover_cells", "choose_table")
    g.add_edge("choose_table", "verify_choice")
    g.add_edge("verify_choice", "decide_state")
    g.add_edge("decide_state", END)
    return g.compile()


REQUIREMENT_GRAPH = build_requirement_graph()


# ---------------------------------------------------------------- outer nodes


def split_by_requirement(state: SelectState, config: RunnableConfig | None = None) -> dict:
    return {"results": []}


def _fan_out(state: SelectState):
    reqs = (state["analysis"].get("interpretation") or {}).get("requirements") or []
    if not reqs:
        return "collect_selections"
    return [Send("select_requirement", {"analysis": state["analysis"], "requirement_id": r["id"],
                                        "as_of": state.get("as_of")}) for r in reqs]


def select_requirement(state: RequirementState, config: RunnableConfig | None = None) -> dict:
    if ((config or {}).get("configurable") or {}).get("selection_mode") == "refutation":
        from .execution import select_requirement as execute

        return {"results": [execute(state["analysis"], state["requirement_id"], _layer(config), state.get("as_of"))]}
    out = REQUIREMENT_GRAPH.invoke(state, config)
    # the requirement's state goes on to the bundle step (S2) without the table views (r4 9-5: no view objects in
    # the state; they are read again from the catalog layer)
    kept = {k: v for k, v in out.items() if k not in ("analysis", "channels")}
    if kept.get("cover"):
        kept["cover"] = {k: v for k, v in kept["cover"].items() if k != "views"}
    return {"results": [{"selection": out["selection"], "evidence": out.get("evidence"), "state": kept}]}


UNGROUNDED = "ungrounded"  # requirement reason of the interpretation (split grounding, user decision 10/19)
NOT_IN_CATALOG = "not_in_catalog"
GROUP_FAMILY = "group_family"  # reason of a requirement whose tables the bundle step replaced (S2, 9-5)
_STATES = ("auto", "confirm", "no_table")


def apply_bundles(analysis: dict, results: list[dict], config: RunnableConfig | None = None,
                  as_of: str | None = None) -> list[dict]:
    """Plan 07 r4 S2 (``bundle.py``): every ``table_group_hint`` bundle with a common family gets its tables from
    the representative step over the bundle cells; a requirement whose own least cover of them differs from its
    tables, and whose own conditions and indicator evidence hold on them as well, takes them: recheck (4-5) and
    decide_state again, then never above its old state and confirm (``group_family``). Every requirement of such a
    bundle carries the bundle record in its signals."""
    layer = _layer(config)
    policy = ((config or {}).get("configurable") or {}).get("policy") or DEFAULT_POLICY
    views = selection_catalog(layer).views
    by_rid = {r["selection"]["requirement_id"]: r for r in results}
    for group in bundles(analysis):
        states = {rid: by_rid[rid]["state"] for rid in group if rid in by_rid and (by_rid[rid]["state"] or {})
                  .get("cover") and by_rid[rid]["selection"]["status"] != "no_table"}
        members = [rid for rid in group if rid in states]
        if len(members) < 2:
            continue
        covers = {rid: states[rid]["cover"] for rid in members}
        bundle_log: list[dict] = []  # plan 11 R0: what the bundle step left out (record only)
        family = common_family(members, covers, {rid: states[rid].get("choice") for rid in members}, bundle_log)
        if family is None:
            continue
        tables, marks = bundle_tables(family, members, covers, layer, views, policy, bundle_log)
        for rid in members:
            st = {**states[rid], "analysis": analysis, "pruned_bundle": list(bundle_log)}
            current = [t["table_id"] for t in by_rid[rid]["selection"]["tables"]]
            mine = own_tables(tables, covers[rid])
            changed = bool(mine) and set(mine) != set(current)
            if changed:
                recheck = verify_choice_fn(analysis, rid, mine, layer, as_of, candidates=covers[rid]["usable"])
                evidence = by_rid[rid].get("evidence")
                changed = (no_worse(recheck, st.get("recheck"))
                           and indicator_tier(evidence, rid, mine) >= indicator_tier(evidence, rid, current))
            st["bundle"] = {"requirement_ids": list(members), "family": family, "tables": list(tables),
                            "changed": changed}
            if changed:
                old = by_rid[rid]["selection"]
                own_marks = [{**m, "table_ids": [t for t in m["table_ids"] if t in mine]} for m in marks]
                st["choice"] = {**st["choice"], "tables": mine,
                                "unspecified": [m for m in own_marks if m["table_ids"]]}
                st["recheck"] = recheck
                st.update(decide_state(st, config))
                sel = st["selection"]
                worst = max(old["status"], sel["status"], "confirm", key=_STATES.index)
                sel["status"] = worst
                sel["reasons"] = list(dict.fromkeys([*sel["reasons"], GROUP_FAMILY]))
            else:
                st.update(decide_state(st, config))
            st.pop("analysis", None)
            by_rid[rid].update(selection=st["selection"], state=st)
    return results


def collect_selections(state: SelectState, config: RunnableConfig | None = None) -> dict:
    order = {r["id"]: k for k, r in enumerate((state["analysis"].get("interpretation") or {}).get("requirements")
                                              or [])}
    results = sorted(state.get("results") or [], key=lambda r: order.get(r["selection"]["requirement_id"], 0))
    if ((config or {}).get("configurable") or {}).get("selection_mode") == "refutation":
        selections = [r["selection"] for r in results]
        evidence = {"evidence": [], "evidence_rows": {}}
        for r in results:
            evidence["evidence"] += r["evidence"]["evidence"]
            evidence["evidence_rows"].update(r["evidence"]["evidence_rows"])
        return {"selections": selections, "selection_groups": collect_fn(selections), "evidence": evidence}
    results = apply_bundles(state["analysis"], results, config, state.get("as_of"))
    # plan 10 D step: after the bundle step, so that a bundle's tables are judged once and not replaced afterwards
    views = selection_catalog(_layer(config)).views
    for r in results:
        sel = r["selection"]
        if sel["status"] != "no_table" and (r.get("state") or {}).get("cover"):
            qtext = question_words(state["analysis"], sel["requirement_id"])["text"]
            sel.setdefault("signals", {})["dimensions"] = dimension_record(sel, r["state"], views, qtext=qtext)
    # plan 11 R3 (K13): observation only, after apply_bundles; never feeds back into the selection.
    # A failure here never stops the selection: every preview becomes {"error": <type name>} (no message, which
    # could carry paths).
    contexts = {}
    try:
        for r in results:
            sel = r["selection"]
            contexts[sel["requirement_id"]] = {}
            sel.setdefault("signals", {})["decision_preview"] = decision_mod.requirement_preview(
                state["analysis"], sel["requirement_id"], r.get("state") or {}, sel, _layer(config),
                state.get("as_of"), context=contexts[sel["requirement_id"]])
        decision_mod.attach_bundles(state["analysis"], results, contexts, _layer(config))
    except Exception as exc:  # noqa: BLE001 - observation only
        for r in results:
            r["selection"].setdefault("signals", {})["decision_preview"] = {"error": type(exc).__name__}
    selections = [r["selection"] for r in results]
    # plan 11 R0 (8-2 item 9): do the operands of a calculation relation agree on frequency, unit and period
    for rel in facts_mod.relation_record(state["analysis"], selections, _layer(config)):
        for sel in selections:
            if sel["requirement_id"] in rel["requirement_ids"]:
                sel.setdefault("signals", {}).setdefault("relations", []).append(rel)
    evidence = {"evidence": [], "evidence_rows": {}}
    for r in results:
        if r.get("evidence"):
            evidence["evidence"] += r["evidence"]["evidence"]
            evidence["evidence_rows"].update(r["evidence"]["evidence_rows"])
    return {"selections": selections, "selection_groups": collect_fn(selections), "evidence": evidence}


def build_select_graph():
    g = StateGraph(SelectState)
    g.add_node("split_by_requirement", split_by_requirement)
    g.add_node("select_requirement", select_requirement)
    g.add_node("collect_selections", collect_selections)
    g.add_edge(START, "split_by_requirement")
    g.add_conditional_edges("split_by_requirement", _fan_out, ["select_requirement", "collect_selections"])
    g.add_edge("select_requirement", "collect_selections")
    g.add_edge("collect_selections", END)
    return g.compile()


SELECT_GRAPH = build_select_graph()


def select_tables(analysis: dict, layer, as_of: str | None = None, policy: str | None = None,
                  answers: list | None = None, *, candidate_policy: str = "legacy",
                  selection_mode: str = "legacy") -> dict:
    """{selections, selection_groups, evidence, facts, decisions} of one analysis (2.7 output) on one catalog layer.

    Plan 11 R1 (C, 7-4, 7-8, 7-11): ``answers`` (``SelectionAnswer`` list, the entry point of selecting again) is
    checked for its shape only and not applied yet (R2): an answer of a wrong shape raises ValueError; None or empty
    gives the selection as before. ``facts``: requirement ID -> table ID -> evidence facts (the R0 records of
    ``signals.facts``); ``decisions``: the observation-only bundle decisions. ``analysis`` is returned only when a
    ``change_reading`` answer was applied (never in R1)."""
    if candidate_policy not in ("legacy", "union"):
        raise ValueError("unknown candidate policy")
    if selection_mode not in ("legacy", "refutation"):
        raise ValueError("unknown selection mode")
    if answers:
        from ..contract.check import check_selection_answers

        problems = check_selection_answers(answers)
        if problems:
            raise ValueError("select_tables: answers of a wrong shape: " + "; ".join(problems))
    out = SELECT_GRAPH.invoke({"analysis": analysis, "as_of": as_of, "results": []},
                              {"configurable": {"layer": layer, "policy": policy, "candidate_policy": candidate_policy,
                                                "selection_mode": selection_mode}})
    selections = out.get("selections") or []
    facts = {s["requirement_id"]: dict(((s.get("signals") or {}).get("facts") or {}).get("tables") or {})
             for s in selections}
    # R3 returns the same records carried by decision_preview.bundle (no new OuterState channel).
    return {"selections": selections, "selection_groups": out.get("selection_groups") or [],
            "evidence": out.get("evidence") or {"evidence": [], "evidence_rows": {}},
            "facts": facts, "decisions": decision_mod.decisions_from(selections)}
