"""Interactive graph tests: actual A rules/selection, synthetic metadata and B."""
import copy

import pytest
from test_plan17_candidates import build_layer, itm, table

from claview_v3.agent.assumptions import VERSION, build, check
from claview_v3.agent.b_connection import AdapterPort, connect
from claview_v3.agent.question_route import completion2_route
from claview_v3.agent.session import (
    new_question,
    resume_session,
    run_session,
    session_data,
)


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    return build_layer(tmp_path_factory.mktemp("completion2"), [
        table("nominal", "가상생산(명목)", [itm("A", "1", "가상생산", obj_name="BSI코드별")],
              units=["원"], periods=[("년", "2020", "2025")]),
        table("real", "가상생산(실질)", [itm("A", "1", "가상생산", obj_name="BSI코드별")],
              units=["원"], periods=[("년", "2020", "2025")]),
        table("jobs", "가상고용", [itm("A", "2", "가상고용", obj_name="BSI코드별")],
              units=["명"], periods=[("년", "2020", "2025")]),
    ])


class Port:
    a_assumptions_version = VERSION
    contract_version = "test"

    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(copy.deepcopy(payload))
        rid = payload["requirement_keys"][0]["requirement_id"]
        return {"a_assumptions": payload["a_assumptions"], "scopes": [{"scope_id": rid, "status": "ok",
            "condition_fields": {"c1": {"applied": {"value": "B actual"}}},
            "period": {"coverage_months": {"unmet": []}}, "shared_assumptions": [], "notices": []}]}


def answer(state, index=0):
    q = state["response"]["question"]
    return {"request_id": state["request_id"], "question_version": state["question_version"],
            "question_id": q["question_id"], "option_id": q["options"][index]["option_id"]}


def test_price_waits_and_resumes_real_selection_without_reinterpret(catalog, monkeypatch):
    port = Port()
    waiting = run_session("가상생산 2023년 보여줘", layer=catalog, as_of="2026-10-21", b_port=port)
    assert waiting["response"]["status"] == "awaiting_answer"
    assert waiting["response"]["question"]["dimension"] == "price"
    assert len(waiting["response"]["question"]["options"]) >= 2
    assert port.calls == []
    assert "effect" not in waiting["response"]["question"]["options"][0]
    before = copy.deepcopy(waiting)
    from claview_v3.agent import nodes
    def forbidden(*a, **kw):
        raise AssertionError("interpretation rerun")
    monkeypatch.setattr(nodes, "execute_decomposition", forbidden)
    replied = resume_session(waiting, answer(waiting, 1), layer=catalog, b_port=port)
    assert waiting == before
    assert replied["response"]["status"] != "awaiting_answer"
    assert replied["analysis"] == waiting["analysis"]
    assert len(port.calls) == 1
    selected = [t["table_id"] for t in replied["selections"][0]["tables"]]
    assert selected == waiting["control"]["pending_question"]["options"][1]["effect"]["tables"]
    assert replied["control"]["attempts"]["interpret"] == 1
    assert replied["control"]["budget"]["retry_model"] == 0
    assert replied["response"]["b"]["requirements"][0]["result"]["scopes"][0]["condition_fields"]
    assert replied["analysis"]["conditions"] == waiting["analysis"]["conditions"]
    assert resume_session(replied, answer(waiting, 1), layer=catalog, b_port=port) == replied
    assert len(port.calls) == 1


@pytest.mark.parametrize("field,value,reason", [("option_id", "other", "unknown_option"),
    ("question_id", "other", "answer_stale"), ("question_version", 9, "answer_version"),
    ("request_id", "other", "answer_version")])
def test_invalid_answer_is_atomic(catalog, field, value, reason):
    s = run_session("가상생산 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    a = answer(s)
    a[field] = value
    got = resume_session(s, a, layer=catalog)
    assert got["response"]["answer_error"] == reason
    for key in ("analysis", "selections", "control", "a_outcome"):
        assert got[key] == s[key]


def test_new_question_starts_new_version_not_resume(catalog):
    s = run_session("가상생산 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    n = new_question(s, "가상고용 2023년 보여줘", layer=catalog)
    assert n["request_id"] == s["request_id"] and n["question_version"] == 2
    assert not n["control"]["pending_question"]
    assert session_data(n)["choices"] == {}


def test_uncertainty_table_count_and_classification_are_not_questions(catalog):
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    s["analysis"]["judgement"]["certain"] = False
    for selection in s["selections"]:
        selection["status"] = "confirm"
        selection["signals"]["decision_preview"]["decision"]["dimensions"] = [
            {"kind": k, "values": ["target"], "set_by_question": False} for k in ("family", "assignment", "other")]
    route = completion2_route(s, catalog)
    assert route["kind"] == "assume" and route["question"] is None


def test_unrelated_pool_price_variants_cannot_create_a_question(catalog):
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    decision = s["selections"][0]["signals"]["decision_preview"]["decision"]
    decision["dimensions"] = [{"kind": "mixed_basis", "values": ["price"], "set_by_question": False}]
    decision["alternatives"] = [{"tables": [t], "gaps": []} for t in ("jobs", "nominal", "real")]
    assert completion2_route(s, catalog)["question"] is None


def test_partial_full_indicator_cannot_be_proven_by_exact_head_occurrence():
    from claview_v3.select.execution import _semantic_proven
    nd = {"certainty": {"all": False, "condition_ids": [], "fragment_ids": []},
          "evidence": {"indicator": {"mapped": True, "settled": True}, "conditions": []}}
    fact = {"indicator": {"weak": False, "check": {"result": "match"}},
            "cells": {"combinable": "possible"}}
    assert _semantic_proven((None, None, "latest", ()), nd, fact, False)
    fact["indicator"]["check"]["result"] = "partial"
    assert not _semantic_proven((None, None, "latest", ()), nd, fact, False)


def test_assumption_ids_stable_and_changed_adoption_gets_new_id(catalog):
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    s["analysis"]["judgement"]["certain"] = False
    first = build(s)
    assert check(first) == []
    session_data(s)["turn"] += 1
    assert build(s) == first
    s["selections"][0]["tables"][0]["table_id"] = "changed"
    after = build(s)
    a = next(r for r in first["items"] if r["reason_code"] == "first_assignment")
    b = next(r for r in after["items"] if r["reason_code"] == "first_assignment")
    assert a["assumption_id"] != b["assumption_id"]


def test_b_unready_not_run_and_missing_capability_is_explicit(catalog):
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    port = Port()
    s["selections"][0].pop("bundle")
    got = connect(s, port)
    assert port.calls == [] and got["response"]["b"]["skipped"]
    got = connect(s, lambda _: None)
    assert got["response"]["b"]["status"] == "contract_pending"


def test_b_does_not_upgrade_a_or_overwrite_conditions(catalog):
    port = Port()
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21", b_port=port)
    assert len(port.calls) == 1
    before = copy.deepcopy(s["analysis"])
    got = connect(s, port)
    assert len(port.calls) == 1 and got["analysis"] == before


def test_bad_b_echo_rejected_without_retry(catalog):
    class Bad(Port):
        def __call__(self, payload):
            result = super().__call__(payload)
            result["a_assumptions"]["version"] = "wrong"
            return result
    port = Bad()
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21", b_port=port)
    assert s["response"]["b"]["requirements"][0]["status"] == "connection_error"
    connect(s, port)
    assert len(port.calls) == 1


def test_resume_rechecks_only_affected_requirement(catalog, monkeypatch):
    s = run_session("가상생산과 가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    assert s["response"]["status"] == "awaiting_answer" and len(s["selections"]) == 2
    affected = set(s["control"]["pending_question"]["requirement_ids"])
    from claview_v3.select import execution
    original_select, original_verify = execution.select_requirement, execution.verify
    selections, checks = [], []
    def select(a, rid, *args, **kw):
        selections.append(rid)
        return original_select(a, rid, *args, **kw)
    def verify(a, chosen, *args, **kw):
        checks.append(chosen["requirement_id"])
        return original_verify(a, chosen, *args, **kw)
    monkeypatch.setattr(execution, "select_requirement", select)
    monkeypatch.setattr(execution, "verify", verify)
    got = resume_session(s, answer(s, 1), layer=catalog)
    assert "answer_error" not in got["response"]
    assert set(selections) == affected and set(checks) == affected
    before = {x["requirement_id"]: x for x in s["selections"]}
    for selection in got["selections"]:
        if selection["requirement_id"] not in affected:
            assert selection == before[selection["requirement_id"]]


def test_stale_state_rejected_before_applying(catalog):
    s = run_session("가상생산 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    a = answer(s)
    s["analysis"]["judgement"]["certain"] = not s["analysis"]["judgement"]["certain"]
    got = resume_session(s, a, layer=catalog)
    assert got["response"]["answer_error"] == "answer_stale"
    assert got["control"] == s["control"]


@pytest.mark.parametrize("change", ["version", "unknown", "id", "stage", "refs", "keys", "value"])
def test_public_assumption_contract_rejects_bad_rows(catalog, change):
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    s["analysis"]["judgement"]["certain"] = False
    envelope = build(s)
    row = envelope["items"][0]
    if change == "version":
        envelope["version"] = "unsupported"
    elif change == "unknown":
        row["unknown"] = 1
    elif change == "id":
        envelope["items"].append(copy.deepcopy(row))
    elif change == "stage":
        row["stage"] = "B"
    elif change == "refs":
        row["refs"]["segment_ids"] = [1]
    elif change == "value":
        row["adopted"]["value"] = float("nan")
    else:
        row["requirement_ids"] = ["wrong"]
    assert check(envelope)


@pytest.mark.parametrize("target", ["known", "missing", "other_requirement"])
def test_supersedes_reference_is_checked(catalog, target):
    class Replacing(Port):
        def __call__(self, payload):
            got = super().__call__(payload)
            source = payload["a_assumptions"]["items"][0]
            rid = got["scopes"][0]["scope_id"]
            got["scopes"][0]["shared_assumptions"] = [{"assumption_id": "b:1", "stage": "B",
                "requirement_ids": [rid if target != "other_requirement" else "other"], "refs": source["refs"],
                "supersedes": source["assumption_id"] if target != "missing" else "missing"}]
            return got
    s = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    s["analysis"]["judgement"]["certain"] = False
    s["response"]["assumptions"] = build(s)
    s["response"]["assumptions"]["items"][0]["refs"]["condition_ids"] = [s["analysis"]["conditions"][0]["id"]]
    got = connect(s, Replacing())
    entry = got["response"]["b"]["requirements"][0]
    assert entry["status"] == ("returned" if target == "known" else "connection_error")
    assert bool(got["response"]["b"]["superseded"]) == (target == "known")
    expected = copy.deepcopy(s["response"]["assumptions"])
    if target != "known":
        expected["items"] = [r for r in expected["items"] if r["reason_code"] not in
                             ("first_assignment", "user_selection")]
    assert got["response"]["assumptions"] == expected


@pytest.mark.parametrize("failure", [None, "request", "input", "scope_ids", "result", "echo", "fetch"])
def test_adapter_port_validates_before_model_and_fetch(catalog, failure):
    calls = []
    def adapter(payload, *, a_assumptions):
        calls.append("adapter")
        return {"scopes": [{"scope_id": payload["requirement_keys"][0]["requirement_id"]}],
                "input_errors": ["bad"] if failure == "input" else [], "a_assumptions": a_assumptions}
    def request_check(request):
        if failure == "scope_ids":
            request["scopes"][0]["scope_id"] = "other"
        return ["bad"] if failure == "request" else []
    def run_b(request, svc):
        calls.append("model")
        return {"scopes": [{"scope_id": request["scopes"][0]["scope_id"], "status": "ok"}],
                "a_assumptions": {} if failure == "echo" else request["a_assumptions"],
                "selections": [{"test": True}]}
    def fetch(result, svc):
        calls.append("fetch")
        if failure == "fetch":
            raise TimeoutError("secret URL must not be logged")
        return {"status": "ok", "rows": []}
    port = AdapterPort(adapter=adapter, check_request=request_check,
        check_scope_result=lambda _: ["bad"] if failure == "result" else [], run_b=run_b, fetch=fetch,
        services=None, contract_version="test", a_assumptions_version=VERSION, check_result=lambda result, request: [])
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21", b_port=port)
    if failure in ("request", "input", "scope_ids"):
        assert calls == ["adapter"]
    elif failure and failure != "fetch":
        assert calls == ["adapter", "model"]
    elif failure == "fetch":
        assert calls == ["adapter", "model", "fetch"]
        got = state["response"]["b"]["requirements"][0]["result"]
        assert got["scopes"][0]["status"] == "ok" and got["fetch"] == {"status": "error", "error_type": "TimeoutError"}
        assert state["response"]["execution_status"] == "partial"
    else:
        assert calls == ["adapter", "model", "fetch"]
        assert state["response"]["b"]["requirements"][0]["result"]["fetch"]["status"] == "ok"
        assert state["response"]["execution_status"] == "completed"
    if failure in ("request", "input", "scope_ids", "result", "echo"):
        assert state["response"]["execution_status"] == "not_executed"
        assert state["response"]["b"]["skipped"] == [{"requirement_id": "r1", "reason": "connection_error"}]
        assert not any(r["reason_code"] in ("first_assignment", "user_selection")
                       for r in state["response"]["assumptions"]["items"])


@pytest.mark.parametrize("second_ready", [True, False])
def test_related_requirements_are_one_b_request_or_skipped(catalog, second_ready):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    first = state["selections"][0]
    second = copy.deepcopy(first)
    second["requirement_id"] = "r2"
    state["selections"].append(second)
    state["analysis"]["interpretation"]["relations"] = [{"kind": "ratio", "requirement_ids": [first["requirement_id"], "r2"]}]
    state["selection_route"]["eligible"] = [first["requirement_id"]] + (["r2"] if second_ready else [])
    class Related(Port):
        def __call__(self, payload):
            self.calls.append(payload)
            return {"a_assumptions": payload["a_assumptions"], "scopes": [
                {"scope_id": k["requirement_id"], "status": "ok"} for k in payload["requirement_keys"]]}
    port = Related()
    got = connect(state, port)
    assert len(port.calls) == (1 if second_ready else 0)
    if second_ready:
        assert {s["requirement_id"] for s in port.calls[0]["selections"]} == {"r1", "r2"}
    else:
        assert {s["reason"] for s in got["response"]["b"]["skipped"]} == {"relation_operand_unavailable"}


def test_reading_difference_only_asks_for_different_indicator_and_tables(catalog):
    state = run_session("가상고용 2023년 보여줘", layer=catalog, as_of="2026-10-21")
    alt = copy.deepcopy(state["analysis"])
    alt["interpretation"]["requirements"][0]["indicator"]["name"] = "다른개념"
    selections = copy.deepcopy(state["selections"])
    selections[0]["tables"][0]["table_id"] = "other"
    session_data(state)["readings"] = [{"id": "alternative", "analysis": alt, "selections": selections}]
    route = completion2_route(state, catalog)
    assert route["kind"] == "ask" and route["question"]["dimension"] == "measurement_concept"
    session_data(state)["readings"][0]["selections"] = state["selections"]
    assert completion2_route(state, catalog)["kind"] != "ask"


def test_missing_date_is_frozen_for_resume(catalog, monkeypatch):
    from datetime import date

    from claview_v3.agent import session
    class FirstDay(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 21)
    monkeypatch.setattr(session, "date", FirstDay)
    state = run_session("가상생산 2023년 보여줘", layer=catalog)
    assert state["as_of"] == "2026-10-21"
    class LaterDay(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 22)
    monkeypatch.setattr(session, "date", LaterDay)
    result = resume_session(state, answer(state), layer=catalog)
    assert result["as_of"] == "2026-10-21"
