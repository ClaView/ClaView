"""Completion 2 review: execution eligibility and honest public provenance."""
# ruff: noqa: F401, F811 -- imported pytest fixture
import copy

import pytest
from test_completion2 import Port, answer, catalog

from claview.a.agent.assumptions import VERSION, build
from claview.a.agent.b_connection import AdapterPort, connect
from claview.a.agent.question_route import completion2_route
from claview.a.agent.session import public_response, resume_session, run_session, session_data


@pytest.mark.parametrize("claim", ["first_assignment", "user_selection"])
def test_rejected_progress_is_removed_without_reconnecting_or_losing_uncertainty(catalog, claim):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    _unresolved(state)
    state["response"]["assumptions"] = build(state)
    progress = next(r for r in state["response"]["assumptions"]["items"] if r["reason_code"] == "first_assignment")
    progress["reason_code"] = claim
    class Reject(Port):
        def __call__(self, payload):
            self.calls.append(copy.deepcopy(payload))
            raise ValueError("do not expose exception text")
    port = Reject()
    before = copy.deepcopy(state)
    got = connect(state, port)
    assert state == before and got["analysis"] == state["analysis"]
    assert got["response"]["execution_status"] == "not_executed"
    assert got["response"]["b"]["skipped"] == [{"requirement_id": "r1", "reason": "connection_error"}]
    assert got["response"]["assumptions"]["items"] == [r for r in before["response"]["assumptions"]["items"]
                                                         if r["reason_code"] != claim]
    assert any(r["reason_code"] == "interpretation_unsettled" for r in got["response"]["assumptions"]["items"])
    assert connect(got, port) == got and len(port.calls) == 1
    changed = copy.deepcopy(got)
    changed["question_version"] += 1
    connect(changed, port)
    assert len(port.calls) == 2


def test_mixed_b_return_and_contract_rejection_keep_only_executed_progress(catalog):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    _unresolved(state)
    req = copy.deepcopy(state["analysis"]["interpretation"]["requirements"][0])
    req["id"] = "r2"
    state["analysis"]["interpretation"]["requirements"].append(req)
    sel = copy.deepcopy(state["selections"][0])
    sel["requirement_id"] = "r2"
    state["selections"].append(sel)
    state["selection_route"]["eligible"] = ["r1", "r2"]
    state["response"]["assumptions"] = build(state)
    class Mixed(Port):
        def __call__(self, payload):
            got = super().__call__(payload)
            if got["scopes"][0]["scope_id"] == "r2":
                raise ValueError("rejected")
            got["fetch"] = {"status": "ok"}
            return got
    port = Mixed()
    got = connect(state, port)
    assert got["response"]["execution_status"] == "partial"
    assert [r["status"] for r in got["response"]["b"]["requirements"]] == ["returned", "connection_error"]
    assert got["response"]["b"]["skipped"] == [{"requirement_id": "r2", "reason": "connection_error"}]
    claims = [r for r in got["response"]["assumptions"]["items"] if r["reason_code"] == "first_assignment"]
    assert [r["requirement_ids"] for r in claims] == [["r1"]]
    assert connect(got, port) == got and len(port.calls) == 2


def _unresolved(state):
    req = state["analysis"]["interpretation"]["requirements"][0]
    req["status"] = "unresolved"
    req["indicator"]["name"] = None
    state["analysis"]["judgement"]["certain"] = False
    return req


def _fact(state, value):
    selection = state["selections"][0]
    for table in selection["tables"]:
        selection["signals"]["facts"]["tables"][table["table_id"]]["indicator"] = copy.deepcopy(value)


@pytest.mark.parametrize("stage", ["waiting", "skipped", "no_result"])
def test_unexecuted_tables_never_claim_adoption(catalog, stage):
    state = run_session("가상생산 2023년 보여줘" if stage == "waiting" else "가상고용 2023년 보여줘",
                        layer=catalog, as_of="2026-10-21")
    state["analysis"]["judgement"]["certain"] = False
    if stage != "waiting":
        state["selection_route"].update(kind="partial" if stage == "skipped" else "no_result", eligible=[])
    rows = build(state)["items"]
    assert not {r["reason_code"] for r in rows} & {"first_assignment", "user_selection"}
    assert any(r["reason_code"] == "interpretation_unsettled" for r in rows)


@pytest.mark.parametrize("support", ["match", "partial", "occurrence", "unknown", "unchecked"])
def test_f2_keeps_unresolved_with_support_or_unknown_facts(catalog, support):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    _unresolved(state)
    fact = {"check": {"available": False, "result": None}, "occurrences": [], "absent": None}
    if support in ("match", "partial"):
        fact.update(check={"available": True, "result": support, "origin": "context"}, absent=True)
    elif support == "occurrence":
        fact.update(occurrences=[{"field": "title", "method": "partial"}], absent=False)
    elif support == "unknown":
        fact = {}
    _fact(state, fact)
    route = completion2_route(state, catalog)
    assert route["eligible"] == ["r1"] and route["kind"] == "assume" and route["question"] is None


@pytest.mark.parametrize("missing", [False, True])
def test_f2_explicit_absence_stops_only_that_requirement_before_b(catalog, missing):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    req = _unresolved(state)
    if missing:
        req["indicator"] = None
    _fact(state, {"absent": True, "check": {"available": False, "result": None}, "occurrences": []})
    original = copy.deepcopy(state["selections"])
    state["selection_route"] = completion2_route(state, catalog)
    state["response"] = public_response(state, state["response"])
    port = Port()
    got = connect(state, port)
    assert got["selection_route"]["kind"] == "no_result"
    assert got["response"]["skipped"] == [{"requirement_id": "r1", "reason": "indicator_meaning_unsupported"}]
    assert not port.calls and got["response"]["execution_status"] == "not_executed"
    assert got["selections"] == original  # eligibility only, not candidate deletion
    assert not any(r["reason_code"] == "first_assignment" for r in got["response"]["assumptions"]["items"])
    other = copy.deepcopy(req)
    other.update(id="r2", status="ok")
    state["analysis"]["interpretation"]["requirements"].append(other)
    selection = copy.deepcopy(state["selections"][0])
    selection["requirement_id"] = "r2"
    for fact in selection["signals"]["facts"]["tables"].values():
        fact["indicator"] = {"check": {"available": True, "result": "match"}, "absent": False}
    state["selections"].append(selection)
    state["selection_route"] = completion2_route(state, catalog)
    state["response"] = public_response(state, state["response"])
    result = connect(state, port)
    assert result["selection_route"]["kind"] == "partial"
    assert result["selection_route"]["eligible"] == ["r2"]
    assert [s["requirement_id"] for s in port.calls[0]["selections"]] == ["r2"]


def test_f2_null_name_and_uncertainty_alone_are_not_a_block(catalog):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    state["analysis"]["judgement"]["certain"] = False
    state["analysis"]["interpretation"]["requirements"][0]["indicator"]["name"] = None
    _fact(state, {"absent": True})
    assert completion2_route(state, catalog)["eligible"] == ["r1"]


def test_f2_mixed_bundle_does_not_claim_all_indicator_evidence_absent(catalog):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    _unresolved(state)
    _fact(state, {"absent": True})
    selection = state["selections"][0]
    selection["tables"].append({"table_id": "support"})
    selection["signals"]["facts"]["tables"]["support"] = {"indicator": {"check": {"result": "match"}}}
    assert completion2_route(state, catalog)["eligible"] == ["r1"]


@pytest.mark.parametrize("failure", ["adapter_echo", "result_request"])
def test_port_checks_real_adapter_echo_and_full_result_before_fetch(failure):
    calls = []
    envelope = {"version": VERSION, "items": []}
    payload = {"requirement_keys": [{"reading_id": "rules", "requirement_id": "r1"}], "a_assumptions": envelope}
    def adapter(payload, *, a_assumptions):
        calls.append("adapter")
        assert a_assumptions == envelope and a_assumptions is not envelope
        return {"scopes": [{"scope_id": "r1"}], "a_assumptions": {} if failure == "adapter_echo" else a_assumptions}
    def run(request, services):
        calls.append("model")
        return {"scopes": [{"scope_id": "r1", "status": "ok"}], "selections": [{}], "a_assumptions": envelope}
    def result_check(result, request):
        calls.append("result_check")
        return ["invalid requirement reference"]
    port = AdapterPort(adapter=adapter, check_request=lambda _: [], check_scope_result=lambda _: [],
                       check_result=result_check, run_b=run, fetch=lambda *_: calls.append("fetch"),
                       services=None, contract_version="test")
    with pytest.raises(ValueError):
        port(payload)
    assert calls == (["adapter"] if failure == "adapter_echo" else ["adapter", "model", "result_check"])


def test_port_requires_full_result_checker():
    with pytest.raises(TypeError, match="checker required"):
        AdapterPort(adapter=None, check_request=None, check_scope_result=None, check_result=None,
                    run_b=None, fetch=None, services=None, contract_version="test")


def test_price_selection_still_has_user_table_provenance(catalog):
    state = run_session("가상생산 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    got = resume_session(state, answer(state), layer=catalog)
    rows = got["response"]["assumptions"]["items"]
    selected = [r for r in rows if r["reason_code"] == "user_selection"]
    assert len(selected) == 1 and selected[0]["source"] == "user" and selected[0]["confirmed"]
    assert not any(r["reason_code"] == "user_interpretation" for r in rows)


def test_answer_already_applied_and_catalog_change_are_atomic(catalog, monkeypatch):
    from claview.a.agent import session
    state = run_session("가상생산 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    got = resume_session(state, answer(state), layer=catalog)
    changed = resume_session(got, answer(state, 1), layer=catalog)
    assert changed["response"]["answer_error"] == "answer_already_applied"
    assert changed["analysis"] == got["analysis"] and session_data(changed) == session_data(got)
    monkeypatch.setattr(session, "_catalog_version", lambda _: "changed")
    stale = resume_session(state, answer(state), layer=catalog)
    assert stale["response"]["answer_error"] == "catalog_changed"
    assert stale["analysis"] == state["analysis"] and session_data(stale) == session_data(state)


def test_relation_operand_skip_removes_table_progress_but_keeps_uncertainty(catalog):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    state["analysis"]["judgement"]["certain"] = False
    req = copy.deepcopy(state["analysis"]["interpretation"]["requirements"][0])
    req["id"] = "r2"
    state["analysis"]["interpretation"]["requirements"].append(req)
    state["analysis"]["interpretation"]["relations"] = [{"kind": "ratio", "requirement_ids": ["r1", "r2"]}]
    state["selection_route"].update(kind="partial", eligible=["r1"],
                                   skipped=[{"requirement_id": "r2", "reason": "no_table"}])
    state["response"] = public_response(state, state["response"])
    assert any(r["reason_code"] == "first_assignment" for r in state["response"]["assumptions"]["items"])
    port = Port()
    result = connect(state, port)
    assert port.calls == [] and result["response"]["execution_status"] == "not_executed"
    assert {s["requirement_id"] for s in result["response"]["b"]["skipped"]} == {"r1", "r2"}
    rows = result["response"]["assumptions"]["items"]
    assert not any(r["reason_code"] in ("first_assignment", "user_selection") for r in rows)
    assert any(r["reason_code"] == "interpretation_unsettled" for r in rows)
    assert result["response"]["b"]["a_assumptions"] == result["response"]["assumptions"]
    assert connect(result, port) == result and not port.calls
