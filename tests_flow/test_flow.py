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

from claview.b.adapter_v3 import to_b_request
from claview.b.contract import B_CONTRACT_VERSION, STRUCTURE_VERSIONS, check_request
from claview_flow import NOT_RUN_FETCH_REASON, FlowSession, live_b_clients, make_answer
from claview_flow.live import LiveNotAllowed
from claview_v3 import _guard
from claview_v3.agent.b_connection import connect

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
    assert session.port().contract_version == B_CONTRACT_VERSION


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


def _fake_proposal(payload: dict, schema: dict) -> dict:
    """A fake model proposal built from the payload's own IDs (indicator + every condition)."""
    indicator = next(r for r in payload["candidate_summary"] if r["role"] == "indicator")
    requirement = {
        "id": "r1",
        "indicator": {
            "segment_ids": indicator["segment_ids"],
            "subquote": None,
            "sense_id": indicator["sense_id"],
        },
        "targets": [],
        "basis_refs": [],
        "context": None,
        "breakdowns": [],
    }
    bindings = [
        {"condition_id": c["id"], "scope": "question", "requirement_ids": [], "name_refs": []}
        for c in payload["conditions"]
    ]
    requests = [
        {"segment_ids": [s["id"]], "subquote": None, "sense_id": None}
        for s in payload["segments"]
        if s["kind"] == "request"
    ]
    reading = {
        "requirements": [requirement],
        "condition_bindings": bindings,
        "relations": [],
        "polarity_links": [],
        "request_refs": requests,
        "unused_refs": [],
    }
    return {"primary": reading, "alternative": None, "ambiguities": []}


def test_fake_model_structure_is_accepted_by_b(base):
    session, model, client = flow(base)
    proposals = []

    def source(payload, schema):
        proposals.append(payload)
        return _fake_proposal(payload, schema)

    decomposition = {"mode": "gpt", "execution_mode": "fake", "proposal_source": source}
    state = session.run_question(PROCEED_Q, as_of=AS_OF, decomposition=decomposition)
    record = state["analysis"]["decomposition"]
    assert len(proposals) == 1
    assert record["applied_source"] == "model"
    assert record["structure"]["version"] in STRUCTURE_VERSIONS
    # The real B adapter carries the structure and finds no contract error.
    request = to_b_request(state, a_assumptions=state["response"]["assumptions"])
    assert check_request(request) == [] and not request.get("input_errors")
    hints = [scope["hints"].get("structure") for scope in request["scopes"]]
    assert hints and all(h and h["version"] in STRUCTURE_VERSIONS for h in hints)
    response = state["response"]
    assert all(r["status"] == "returned" for r in response["b"]["requirements"])
    assert not [s for s in response["b"]["skipped"] if s["reason"] == "connection_error"]
    assert set(scope_statuses(state)) == {"ok"}
    assert response["execution_status"] == "completed"
    assert response["decomposition"]["real_calls"] == 0


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

    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-hcx-key-not-real")
    monkeypatch.setenv("KOSIS_API_KEY", "fake-kosis-key-not-real")
    logger = logging.getLogger("httpx")
    monkeypatch.setattr(logger, "level", logging.NOTSET)
    model, client = live_b_clients(allow_external=True)  # builds clients only; sends nothing
    client.close()
    assert not logger.isEnabledFor(logging.INFO)


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
