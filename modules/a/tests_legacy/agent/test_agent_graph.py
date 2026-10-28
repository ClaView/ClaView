"""Outer graph end to end (plan 04, 1-1 and 1-2) under the transport block: state shape after a short made-up
question, the same node functions in every variant, no variant branch in the node code, and the jev record kept
out of the router's view."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a_legacy.agent import nodes as nodes_mod
from claview_a_legacy.agent.graph import RECURSION_LIMIT, WIRING, build_outer_graph, node_names, run_outer
from claview_a_legacy.agent.nodes import NODES, check_interpretation, check_selection, part_retry, respond, router, verify
from claview_a_legacy.agent.state import CONTROL_KEYS, EVIDENCE_SUMMARY_KEYS, VARIANTS, make_initial_state
from claview_a_legacy.catalog.layer import load_layer
from claview_a_legacy.verify.jev import CRITERIA, QUESTION_NAME, JevSettings

QUESTION = "가계대출과 기업대출 잔액을 보여줘"  # names of the synthetic catalog (catalog_synth T_REG)


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("agent_graph")
    search, meta = write_raw(folder / "raw")
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


def _routes(final_state):
    return [d for d in final_state["control"]["decisions"] if d["type"] == "route"]


def test_straight_run_state_shape(layer):
    out = run_outer(QUESTION, "straight", layer=layer, as_of="2026-10-18")
    assert set(out) == set(make_initial_state(QUESTION, "straight"))
    assert out["variant"] == "straight" and out["as_of"] == "2026-10-18"
    assert tuple(out["control"]) == CONTROL_KEYS
    assert out["control"]["path"] == ["intake", "router", "interpret", "router", "select", "router", "respond"]
    assert [d["final"]["kind"] for d in _routes(out)] == ["next", "next", "finish"]
    assert out["analysis"]["text"]["raw"] == QUESTION and out["analysis"]["judgement"] is not None
    interp = out["analysis"]["interpretation"]
    assert interp is not None and interp["requirements"]  # 2.1-2.7 ran
    part = out["a_outcome"]["parts"]["interpret"]
    assert part["status"] == "done" and part["interpretation_status"] == interp["status"]
    assert out["a_outcome"]["parts"]["select"]["status"] == "done"
    assert out["a_outcome"]["status"] == "finished"
    # the selection sub-graph ran: one selection per settled requirement, in order
    assert [s["requirement_id"] for s in out["selections"]] == [r["id"] for r in interp["requirements"]]
    assert all(s["tables"] for s in out["selections"] if s["status"] != "no_table")
    assert out["selection_groups"] and all(set(g) == {"requirement_ids", "table_ids"} for g in out["selection_groups"])
    assert set(out["evidence"]) == set(EVIDENCE_SUMMARY_KEYS) and out["evidence"]["kept"] == "summary"
    assert set(out["evidence"]["pool"]) == {s["requirement_id"] for s in out["selections"]}
    assert all(t["table_id"] in out["evidence"]["pool"][s["requirement_id"]] for s in out["selections"]
               for t in s["tables"])
    # shared fields filled from the settled requirements and the final (V2) decision states
    assert [r["scope_id"] for r in out["requirements"]] == [r["id"] for r in interp["requirements"]]
    assert [r["text"] for r in out["requirements"]] == [r["quote"] for r in interp["requirements"]]
    final = out["verification"]["selection"]["states"]
    assert [r["a_status"] for r in out["requirements"]] == [
        "met" if final[r["id"]] == "auto" else final[r["id"]] for r in interp["requirements"]]
    assert all(r["reason_codes"][0] == f"interp:{q['status']}" for r, q in zip(out["requirements"],
                                                                             interp["requirements"]))
    not_met = [u["scope_id"] for u in out["unresolved"] if u["kind"] == "requirement_not_met"]
    assert not_met == [r["scope_id"] for r in out["requirements"] if r["a_status"] != "met"]
    expected_tables = list(dict.fromkeys(t["table_id"] for s in out["selections"] if final[s["requirement_id"]]
                                         != "no_table" for t in s["tables"]))
    assert [t["table_id"] for t in out["selection"]["tables"]] == expected_tables
    assert all(t["org_id"] and t["table_name"] for t in out["selection"]["tables"])
    assert "interpretation_pending" not in {u["kind"] for u in out["unresolved"]}
    assert out["verification"]["interpretation"]["check"] == "V1"
    assert out["verification"]["interpretation"]["source"] == "interpretation"
    assert out["verification"]["interpretation"]["requirement_count"] == len(interp["requirements"])
    assert out["verification"]["selection"]["check"] == "V2"
    assert next(s for s in out["verification"]["selection"]["signals"] if s["code"] == "evidence_recheck")[
        "status"] != "unavailable"
    assert out["jev_record"] is None
    assert out["response"]["status"] == "finished" and out["response"]["parts"] == {
        "intake": "ok", "interpret": "done", "select": "done"}
    assert out["response"]["requirement_count"] == len(interp["requirements"])
    assert out["control"]["budget"]["llm_calls"] == 0


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_runs_without_a_catalog(variant):
    out = run_outer("갑지표와 을지표", variant)
    assert out["response"]["status"] == "finished"
    assert out["control"]["budget"]["steps"] >= 2
    assert out["a_outcome"]["parts"]["select"]["status"] == "no_layer" and out["selections"] == []


def test_empty_question_stops_at_the_router():
    out = run_outer("  ", "router")
    assert out["response"]["status"] == "stopped" and out["response"]["reason"] == "invalid_question"
    assert out["analysis"] is None


@pytest.mark.parametrize("variant", VARIANTS)
def test_same_node_functions_in_every_variant(variant):
    graph = build_outer_graph(variant)
    specs = graph.builder.nodes
    assert set(specs) == set(node_names(variant))
    for name, spec in specs.items():
        assert spec.runnable.func is NODES[name], name
    assert graph.config["recursion_limit"] == RECURSION_LIMIT
    assert graph.config["configurable"]["variant"] == variant


def test_wiring_differs_only_in_nodes_and_edges():
    assert set(WIRING) == set(VARIANTS)
    # parts = straight's part-end checks, each followed by the part's own retry decision
    assert WIRING["parts"]["nodes"] == (*WIRING["straight"]["nodes"], "part_retry")
    assert {e for e in WIRING["parts"]["edges"]} - {e for e in WIRING["straight"]["edges"]} == {
        ("check_interpretation", "part_retry"), ("check_selection", "part_retry")}
    assert "verify" not in WIRING["straight"]["nodes"] and "verify" in WIRING["central"]["nodes"]
    assert "ask_user" in WIRING["router"]["nodes"] and "ask_user" not in WIRING["central"]["nodes"]


def test_node_code_has_no_variant_branch():
    """No comparison with a variant name in the node code and the variant read only by intake (which checks it)."""
    tree = ast.parse(Path(nodes_mod.__file__).read_text(encoding="utf-8"))
    compared = {c.value for n in ast.walk(tree) if isinstance(n, (ast.Compare, ast.Match)) for c in ast.walk(n)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    assert not compared & set(VARIANTS)
    readers = set()
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for n in ast.walk(fn):
            if (isinstance(n, ast.Name) and n.id == "variant") or (isinstance(n, ast.Constant) and n.value == "variant"):
                readers.add(fn.name)
    assert readers == {"intake"}


def test_central_verify_and_part_end_check_give_the_same_result(layer):
    state = run_outer(QUESTION, "straight", layer=layer)
    expected = check_interpretation(state)
    control = {**state["control"], "decisions": [*state["control"]["decisions"], {
        "type": "route", "final": {"kind": "verify", "args": {"stage": "interpret"}}}]}
    got = verify({**state, "jev_record": None, "control": control})

    def without_seq(update):
        out = {**update, "verification": {k: {f: v for f, v in (r or {}).items() if f != "seq"}
                                          for k, r in update["verification"].items()}}
        return out

    assert without_seq(got) == without_seq(expected)


SLASH_QUESTION = "가계대출 / 기업대출 잔액"  # one draft requirement holding two indicators: V1 mismatch


def test_router_variant_asks_with_the_default_and_goes_on(layer):
    out = run_outer(SLASH_QUESTION, "router", layer=layer)
    assert out["verification"]["interpretation"]["status"] == "mismatch"
    assert out["control"]["path"][:8] == ["intake", "router", "interpret", "router", "verify", "router", "ask_user",
                                          "router"]
    assert out["control"]["path"][-1] == "respond"
    asks = [d for d in out["control"]["decisions"] if d["type"] == "ask"]
    assert asks[0]["kind"] == "split_requirements" and asks[0]["mode"] == "asked"
    assert asks[0]["default"] == asks[0]["options"][0] and asks[0]["answer"] == "default"
    # after selection: one table question per confirm requirement, its options from the real selection
    confirm = [r for r, s in out["verification"]["selection"]["states"].items() if s == "confirm"]
    table_asks = [a for a in asks if a["kind"] == "choose_table"]
    assert [a["requirement_id"] for a in table_asks] == confirm[:len(table_asks)]
    for a in table_asks:
        chosen = next(s for s in out["selections"] if s["requirement_id"] == a["requirement_id"])
        assert a["default"] == {"table_ids": [t["table_id"] for t in chosen["tables"]]}
    assert out["control"]["changes"] == [{"by": "ask_user", "kind": a["kind"], "requirement_id": a["requirement_id"],
                                          "applied": "default"} for a in asks]
    real = [a for a in asks if a["mode"] == "asked"]
    assert out["control"]["budget"]["ask"] == len(real) <= 3 and out["control"]["resume_point"] is None
    assert out["control"]["failure_counts"].get("v1_mismatch") == 1
    assert out["response"]["asked"] == len(real) and out["response"]["ask_skipped"] == len(asks) - len(real)


@pytest.mark.parametrize("variant", ["straight", "central", "parts"])
def test_other_variants_record_the_mismatch_without_asking(variant, layer):
    out = run_outer(SLASH_QUESTION, variant, layer=layer)
    assert out["verification"]["interpretation"]["status"] == "mismatch"
    assert "ask_user" not in out["control"]["path"] and out["response"]["status"] == "finished"


def _wire(choice):
    probs = {c: (0.6 if c == choice else 0.1) for c in CRITERIA}
    return {"model": "jev-1.0.0", "answers": {QUESTION_NAME: {"type": "choice", "choice": choice,
                                                              "probabilities": probs, "confidence": 0.8}},
            "usage": {"input_tokens": 10, "output_tokens": 1}}


@pytest.mark.parametrize("variant", VARIANTS)
def test_jev_is_recorded_but_never_routes(variant, layer):
    calls = []

    def reply(body):
        calls.append(body)
        return _wire("4")

    off = run_outer(QUESTION, variant, layer=layer)
    on = run_outer(QUESTION, variant, layer=layer,
                   jev=JevSettings(mode="mock", model="jev-1.0.0", mock_reply=reply))
    assert on["jev_record"]["mode"] == "mock" and on["jev_record"]["count"] == 4
    assert on["jev_record"]["compare"] in ("same", "jev_less", "jev_more")
    assert len(calls) == 1
    assert [d["final"] for d in _routes(on)] == [d["final"] for d in _routes(off)]
    assert all("jev" not in key for d in _routes(on) for key in d["view"])


def test_replay_without_cache_in_the_graph(tmp_path, layer):
    out = run_outer(QUESTION, "central", layer=layer,
                    jev={"mode": "replay", "model": "jev-1.0.0", "cache_dir": tmp_path})
    assert out["jev_record"]["compare"] == "missing" and out["jev_record"]["mode"] == "replay"


# ---------------------------------------------------------------- part retry (4-b) and V2 into the shared fields


def _parts_state(layer, **changes):
    state = run_outer(QUESTION, "parts", layer=layer)
    control = {**state["control"], **changes.pop("control", {})}
    return {**state, **changes, "control": control}


def test_part_retry_takes_the_next_alternative_once_counted_in_the_budget(layer):
    checked = {"check": "V2", "status": "flagged", "states": {"r1": "auto"}, "lowered": [],
               "reinterpret_signal": None, "dropped_tables": ["T_X"], "signals": [], "seq": 99}
    state = _parts_state(layer, control={"stage": "select"},
                         verification={"interpretation": None, "selection": checked})
    budget = state["control"]["budget"]["next_alternative"]
    first = part_retry(state)
    assert first.goto == "select"
    control = first.update["control"]
    assert control["budget"]["next_alternative"] == budget + 1 and control["decisions"][-1]["type"] == "part"
    assert control["decisions"][-1]["final"]["kind"] == "next_alternative"
    again = part_retry({**state, "control": control})
    assert again.goto == "router" and again.update["control"]["budget"]["next_alternative"] == budget + 1


def test_part_retry_leaves_reinterpretation_to_the_router(layer):
    checked = {"check": "V2", "status": "flagged", "states": {}, "lowered": [], "reinterpret_signal": "merge",
               "dropped_tables": ["T_X"], "signals": [], "seq": 99}
    state = _parts_state(layer, control={"stage": "select"},
                         verification={"interpretation": None, "selection": checked})
    assert part_retry(state).goto == "router"


def test_part_retry_retries_the_model_only_when_it_is_on(layer):
    v1 = {"check": "V1", "status": "mismatch", "requirement_id": "r1", "signals": [], "seq": 99}
    state = _parts_state(layer, control={"stage": "interpret"}, verification={"interpretation": v1, "selection": None})
    assert part_retry(state).goto == "router"  # model off (plan 04): no model retry in 4-b either
    on = {**state["a_outcome"], "parts": {**state["a_outcome"]["parts"],
                                          "interpret": {**state["a_outcome"]["parts"]["interpret"], "model": "on"}}}
    retried = part_retry({**state, "a_outcome": on})
    assert retried.goto == "interpret" and retried.update["control"]["budget"]["retry_model"] == 1


def test_v2_lowered_state_reaches_the_shared_fields(layer):
    state = run_outer(QUESTION, "straight", layer=layer)
    ids = [r["id"] for r in state["analysis"]["interpretation"]["requirements"]]
    selections = [{"requirement_id": ids[0], "tables": [], "status": "auto"},
                  *({"requirement_id": rid, "tables": [{"table_id": "T_REG"}], "status": "auto"} for rid in ids[1:])]
    update = check_selection({**state, "selections": selections})
    statuses = [r["a_status"] for r in update["requirements"]]
    assert statuses == ["no_table", *["met"] * (len(ids) - 1)]
    assert (ids[0], "no_table") in [(u["scope_id"], u["kind"]) for u in update["unresolved"]]
    assert [t["table_id"] for t in update["selection"]["tables"]] == (["T_REG"] if len(ids) > 1 else [])

@pytest.mark.parametrize("limits, reason", [
    ({"steps": 20}, "step_limit"),
    ({"attempts": {"select": 3}}, "stage_attempt_limit"),
])
def test_a_guard_refusal_of_the_part_retry_ends_the_run_like_the_router(layer, limits, reason):
    """Same state (V2 dropped a table, no alternative used yet, a limit reached): central and parts both stop with
    the guard's reason; parts does not finish normally."""
    checked = {"check": "V2", "status": "flagged", "states": {"r1": "auto"}, "lowered": [],
               "reinterpret_signal": None, "dropped_tables": ["T_X"], "signals": [], "seq": 99}
    responses = {}
    for variant, stage, node in (("central", "verify_select", router), ("parts", "select", part_retry)):
        state = run_outer(QUESTION, variant, layer=layer)
        control = {**state["control"], "stage": stage, "resume_point": None,
                   "budget": {**state["control"]["budget"], "next_alternative": 0, **({"steps": limits["steps"]}
                                                                                     if "steps" in limits else {})},
                   "attempts": {**state["control"]["attempts"], **limits.get("attempts", {})}}
        state = {**state, "control": control, "verification": {"interpretation": None, "selection": checked}}
        command = node(state)
        assert command.goto == "respond", variant
        decision = command.update["control"]["decisions"][-1]
        assert decision["proposed"]["kind"] == "next_alternative" and decision["guard_reason"] == reason
        responses[variant] = respond({**state, **command.update})["response"]
    for key in ("status", "finished_by", "reason", "guard_reason"):
        assert responses["central"][key] == responses["parts"][key], key
    assert responses["parts"]["status"] == "stopped" and responses["parts"]["reason"] == reason

# ---------------------------------------------------------------- the four variants end to end on the real parts

E2E_QUESTIONS = (QUESTION, "제조업 생산", "서울 가계대출 2020년")  # made-up questions over the synthetic catalog


def _kinds(state):
    return [d["final"]["kind"] for d in state["control"]["decisions"] if d["type"] in ("route", "part")]


@pytest.mark.parametrize("question", E2E_QUESTIONS)
def test_four_variants_run_the_real_interpretation_and_selection_to_the_end(question, layer):
    runs = {v: run_outer(question, v, layer=layer) for v in VARIANTS}
    for variant, out in runs.items():
        assert out["response"]["status"] == "finished", variant
        assert out["a_outcome"]["parts"]["interpret"]["status"] == "done"
        assert out["a_outcome"]["parts"]["select"]["status"] == "done"
        assert out["selections"] and out["control"]["path"][-1] == "respond"
        kinds = _kinds(out)
        assert kinds[0] == "next" and kinds[-1] in ("finish", "partial")
        assert ("verify" in kinds) == (variant in ("central", "router"))
        assert ("ask" in kinds) <= (variant == "router")
    # same nodes, same inputs, defaults applied for the asks: the same selections and shared fields everywhere
    base = runs["straight"]
    for variant, out in runs.items():
        assert out["selections"] == base["selections"], variant
        for key in ("requirements", "unresolved", "selection"):
            assert out[key] == base[key], (variant, key)


def test_shared_fields_follow_the_real_selection(layer):
    out = run_outer(QUESTION, "central", layer=layer)
    states = out["verification"]["selection"]["states"]
    assert states == {s["requirement_id"]: s["status"] for s in out["selections"]}  # nothing lowered here
    for req in out["requirements"]:
        chosen = next(s for s in out["selections"] if s["requirement_id"] == req["scope_id"])
        assert req["a_status"] == ("met" if chosen["status"] == "auto" else chosen["status"])
        assert req["gaps"] == chosen["unspecified"]
        assert req["reason_codes"][1:] == [*next(r for r in out["analysis"]["interpretation"]["requirements"]
                                                 if r["id"] == req["scope_id"])["reasons"],
                                           *(f"select:{x}" for x in chosen["reasons"])]
    shared = {t["table_id"]: t for t in out["selection"]["tables"]}
    for s in out["selections"]:
        for t in s["tables"]:
            assert shared[t["table_id"]] == {"org_id": t["org_id"], "table_id": t["table_id"],
                                             "table_name": t["table_name"]}