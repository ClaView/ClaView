"""One flow: real A session -> real B adapter/contract/run -> fetch, on the real catalog.

The B selection model and the KOSIS client are the SYNTHETIC ones of synthetic_b.py,
so these tests check wiring and contracts, never model or value accuracy. Questions
are short probe sentences chosen for the route they take with A's rules (proceed,
wait for a price answer, no table); they are not evaluation cases.
"""

from __future__ import annotations

import copy
import socket

import pytest
from synthetic_b import SyntheticFetchClient, SyntheticSelectionModel

from claview_a_legacy import _guard
from claview_a_legacy.agent.b_connection import connect
from claview_b.contracts import StageFailure
from claview_pipeline import (
    NOT_RUN_FETCH_REASON,
    TABLE_LIST_CONTRACT,
    FlowSession,
    live_b_clients,
    make_answer,
    to_table_request,
)
from claview_pipeline.b_port import B_TABLE_FIELDS
from claview_pipeline.live import LiveNotAllowed

AS_OF = "2026-10-22"
PROCEED_Q = "GDP 2023년 보여줘"
WAIT_Q = "총고정자본형성 2023년"
NO_TABLE_Q = "수출 2023년 보여줘"


def flow(base: FlowSession, *, fetch: bool = True):
    model = SyntheticSelectionModel(base.store)
    client = SyntheticFetchClient() if fetch else None
    session = FlowSession.load(
        b_model=model, fetch_client=client, layer=base.layer, store=base.store
    )
    return session, model, client


def scope_statuses(state: dict) -> list[str]:
    return [
        scope["status"]
        for req in state["response"]["b"]["requirements"]
        for scope in req.get("result", {}).get("scopes", [])
    ]


def test_rules_question_runs_real_b_and_synthetic_fetch(base):
    session, model, client = flow(base)
    state = session.run_question(PROCEED_Q, as_of=AS_OF)
    response = state["response"]
    assert response["status"] in ("proceed", "assume")
    assert response["b"]["status"] == "returned"
    assert [r["status"] for r in response["b"]["requirements"]] == ["returned"] * len(
        response["b"]["requirements"]
    )
    assert response["b"]["requirements"] and set(scope_statuses(state)) == {"ok"}
    fetches = [r["result"]["fetch"] for r in response["b"]["requirements"]]
    assert all(f["status"] == "ok" and f["result"]["series"] for f in fetches)
    assert response["execution_status"] == "completed"
    assert model.calls and client.calls
    assert session.port().contract_version == TABLE_LIST_CONTRACT


def test_price_question_waits_then_answer_runs_b_once(base):
    session, model, client = flow(base)
    waiting = session.run_question(WAIT_Q, as_of=AS_OF)
    assert waiting["response"]["status"] == "awaiting_answer"
    assert waiting["response"]["execution_status"] == "awaiting_answer"
    assert waiting["response"]["b"]["reason"] == "awaiting_answer"
    assert model.calls == [] and client.calls == []
    before = copy.deepcopy(waiting)
    reply = make_answer(waiting)
    resumed = session.answer(waiting, reply)
    assert waiting == before
    assert resumed["response"]["status"] != "awaiting_answer"
    assert resumed["analysis"] == waiting["analysis"]
    assert resumed["response"]["execution_status"] == "completed"
    assert set(scope_statuses(resumed)) == {"ok"}
    calls = (len(model.calls), len(client.calls))
    assert calls[0] >= 1 and calls[1] >= 1
    # The same answer again returns the stored state; B is not called again.
    assert session.answer(resumed, reply) == resumed
    assert (len(model.calls), len(client.calls)) == calls


def test_no_result_never_calls_b(base):
    session, model, client = flow(base)
    state = session.run_question(NO_TABLE_Q, as_of=AS_OF)
    assert state["response"]["status"] == "no_result"
    assert state["response"]["b"]["status"] == "not_run"
    assert state["response"]["b"]["reason"] == "no_executable_requirements"
    assert state["response"]["execution_status"] == "not_executed"
    assert model.calls == [] and client.calls == []


def test_reconnecting_the_same_state_does_not_call_b_again(base):
    session, model, client = flow(base)
    state = session.run_question(PROCEED_Q, as_of=AS_OF)
    calls = (len(model.calls), len(client.calls))
    assert calls[0] >= 1
    again = connect(state, session.port())
    assert (len(model.calls), len(client.calls)) == calls
    assert again["response"]["b"]["requirements"] == state["response"]["b"]["requirements"]
    assert again["response"]["execution_status"] == state["response"]["execution_status"]


def test_without_fetch_client_fetch_is_not_run_and_not_completed(base):
    session, model, _ = flow(base, fetch=False)
    state = session.run_question(PROCEED_Q, as_of=AS_OF)
    response = state["response"]
    assert model.calls
    assert set(scope_statuses(state)) == {"ok"}
    fetches = [r["result"]["fetch"] for r in response["b"]["requirements"]]
    assert fetches and all(f["status"] == "not_run" for f in fetches)
    assert all(f["reason"] == NOT_RUN_FETCH_REASON and f["requests"] for f in fetches)
    assert all("apiKey" not in request for f in fetches for request in f["requests"])
    assert response["execution_status"] != "completed"


def test_without_b_model_no_port_is_built(base):
    state = base.run_question(PROCEED_Q, as_of=AS_OF)
    assert base.port() is None
    assert state["response"]["b"]["reason"] == "b_port_not_configured"
    assert state["response"]["execution_status"] == "not_executed"


def test_live_clients_keep_httpx_request_urls_out_of_info_logs(monkeypatch):
    import logging

    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key-not-real")
    monkeypatch.setenv("KOSIS_API_KEY", "fake-kosis-key-not-real")
    logger = logging.getLogger("httpx")
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    model, client = live_b_clients(allow_external=True)  # builds clients only; sends nothing
    client.close()
    assert not logger.isEnabledFor(logging.INFO)


def test_live_b_model_is_the_fixed_openai_model_and_ignores_the_hcx_key(monkeypatch):
    from claview_b.models import OpenAIModel
    from claview_common.settings import B_MODEL_ID

    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key-not-real")
    monkeypatch.setenv("KOSIS_API_KEY", "fake-kosis-key-not-real")
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-hcx-key-not-real")
    model, client = live_b_clients(allow_external=True)
    client.close()
    assert isinstance(model, OpenAIModel) and model.model == B_MODEL_ID == "gpt-6.1-terra"
    model.close()


def test_live_b_clients_need_the_openai_key_even_when_only_an_hcx_key_is_set(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("KOSIS_API_KEY", "fake-kosis-key-not-real")
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-hcx-key-not-real")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        live_b_clients(allow_external=True)


@pytest.mark.parametrize("flag", [False, None, "yes", 1])
def test_live_clients_refuse_without_explicit_opt_in(flag):
    with pytest.raises(LiveNotAllowed):
        live_b_clients(flag)
    with pytest.raises(LiveNotAllowed):
        live_b_clients()


def test_network_is_blocked_in_this_suite():
    before = len(_guard.NETWORK_ATTEMPTS)
    with pytest.raises(_guard.NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9))
    assert len(_guard.NETWORK_ATTEMPTS) > before
    del _guard.NETWORK_ATTEMPTS[before:]


def test_b_receives_the_question_and_a_tables_only(base):
    """The port hands B the original question and A's chosen tables as `{"tables": [...]}`."""
    session, model, _ = flow(base)
    port = session.port()
    requests = []

    def recording(payload, *, a_assumptions):
        request = to_table_request(payload, a_assumptions=a_assumptions)
        requests.append((payload, request))
        return request

    port.adapter = recording
    state = session.run_question(PROCEED_Q, as_of=AS_OF)
    assert requests and state["response"]["execution_status"] == "completed"
    for payload, request in requests:
        chosen = [(t["org_id"], t["table_id"]) for s in payload["selections"] for t in s["tables"]]
        tables = request["a_result"]["tables"]
        assert request["query"] == PROCEED_Q == state["question"]
        assert list(request["a_result"]) == ["tables"] and tables
        assert all(list(t) == list(B_TABLE_FIELDS) for t in tables)
        assert [(t["org_id"], t["table_id"]) for t in tables] == list(dict.fromkeys(chosen))
    assert set(model.calls) == {
        f"B:{t['org_id']}:{t['table_id']}" for _, r in requests for t in r["a_result"]["tables"]
    }


def test_no_tables_or_a_waiting_never_calls_b(base):
    session, model, client = flow(base)
    state = session.run_question(PROCEED_Q, as_of=AS_OF)
    calls = (len(model.calls), len(client.calls))
    # A payload whose selections carry no table is refused before B runs.
    payload = {
        "question": PROCEED_Q,
        "selections": [{"requirement_id": "r1", "tables": []}],
        "a_assumptions": state["response"]["assumptions"],
        "requirement_keys": [{"reading_id": "x", "requirement_id": "r1"}],
    }
    with pytest.raises(ValueError):
        session.port()(payload)
    # A question that waits for the user never reaches B.
    waiting = session.run_question(WAIT_Q, as_of=AS_OF)
    assert waiting["response"]["b"]["reason"] == "awaiting_answer"
    assert (len(model.calls), len(client.calls)) == calls


class _FailingModel:
    """B's selection model that always fails (stands for a failed B selection)."""

    def __init__(self):
        self.calls = []

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.calls.append(task)
        raise StageFailure("synthetic_model_failure")


def test_b_graph_failure_stays_a_stage_failure(base):
    model, client = _FailingModel(), SyntheticFetchClient()
    session = FlowSession.load(
        b_model=model, fetch_client=client, layer=base.layer, store=base.store
    )
    state = session.run_question(PROCEED_Q, as_of=AS_OF)
    response = state["response"]
    assert model.calls and client.calls == []
    requirements = response["b"]["requirements"]
    assert requirements and all(r["status"] == "returned" for r in requirements)
    for r in requirements:
        b_outcome = r["result"]["b_outcome"]
        assert b_outcome["stage"] == "B" and b_outcome["status"] != "ok" and b_outcome["issues"]
        assert {s["status"] for s in r["result"]["scopes"]} == {b_outcome["status"]}
        assert r["result"]["selections"] == [] and r["result"]["fetch"] is None
    assert response["execution_status"] != "completed"
