"""Model proposals are fake, but the interpretation and outer nodes are real."""
# ruff: noqa: F401, F811 -- imported pytest fixture
import copy

import pytest

from test_plan15_settle import P, R, bind, ind, layer, proposal, reading, req
from test_plan17_alternative_structure import QUESTION, _proposals

from claview.a.agent.session import (
    resume_session,
    run_session,
    session_data,
    state_version,
    with_session,
)
from claview.a.contract.check import check_analysis
from claview.a.select.graph import select_tables
from claview.a.select.readings import reading_candidates


def test_fake_model_called_once_and_unresolved_meaning_is_public(layer):
    calls = []
    def source(payload, schema):
        calls.append(payload)
        p = P(payload)
        return proposal(reading([req("r1", ind(p, "가상대출금"))],
            [bind(c["id"]) for c in payload["conditions"]], request=[R(p.s("보여줘"))]))
    state = run_session("가상대출금 최근 3개월 보여줘", layer=layer, as_of="2026-10-21",
                        decomposition={"mode": "gpt", "proposal_source": source})
    assert len(calls) == 1
    assert state["analysis"]["decomposition"]["applied_source"] == "model"
    assert state["jev_record"]["mode"] == "blocked_by_decomposition"
    assert state["response"]["decomposition"]["real_calls"] == 0
    assert state["control"]["budget"]["retry_model"] == 0
    assert check_analysis(state["analysis"]) == []


def test_failed_fake_model_does_not_retry_or_ask_just_because_uncertain(layer):
    calls = []
    def source(payload, schema):
        calls.append(1)
        return {"invalid": True}
    state = run_session("가상대출금 보여줘", layer=layer, as_of="2026-10-21",
                        decomposition={"mode": "gpt", "proposal_source": source})
    assert len(calls) == 1
    assert state["analysis"]["decomposition"]["applied_source"] == "rules_fallback"
    assert state["control"]["budget"]["retry_model"] == 0
    assert state["response"]["question"] is None


def test_resume_adopts_whole_verified_alternative_including_bindings(layer, monkeypatch):
    calls = []
    def source(payload, schema):
        calls.append(1)
        return _proposals(P(payload))
    state = run_session(QUESTION, layer=layer, as_of="2026-10-21",
                        decomposition={"mode": "gpt", "proposal_source": source})
    assert len(calls) == 1
    expected = reading_candidates(state["analysis"], layer)[0]["analysis"]
    chosen = select_tables(expected, layer, "2026-10-21", selection_mode="refutation")
    # Force only the pending UI decision, not the adoption/selection/verification.
    # This fixture's semantic difference is attribution, which must not itself ask.
    assert state["response"]["question"] is None
    data = copy.deepcopy(session_data(state))
    data["readings"] = [{"id": "alternative", "analysis": expected, "selections": chosen["selections"]}]
    state["a_outcome"] = with_session(state, data)
    state["control"]["pending_question"] = {
        "question_id": "fixture", "version": state_version(state), "dimension_key": "fixture",
        "requirement_ids": ["r1", "r2"], "options": [{"option_id": "alternative",
            "effect": {"kind": "change_reading", "reading": "alternative"}}]}
    from claview.a.agent import nodes
    def forbidden(*a, **kw):
        raise AssertionError("model rerun")
    monkeypatch.setattr(nodes, "execute_decomposition", forbidden)
    got = resume_session(state, {"request_id": state["request_id"], "question_version": 1,
                               "question_id": "fixture", "option_id": "alternative"}, layer=layer)
    assert "answer_error" not in got["response"]
    assert got["analysis"] == expected
    assert check_analysis(got["analysis"]) == []
    for field in ("structure", "selection_refs", "bindings", "assumption_seeds", "condition_fields"):
        assert got["analysis"]["decomposition"][field] == expected["decomposition"][field]
    assert got["analysis"]["judgement"]["failed_sources"] == expected["judgement"]["failed_sources"]
    assert len(calls) == 1 and got["control"]["attempts"]["interpret"] == 1


@pytest.mark.parametrize("pick_alternative", [False, True])
def test_real_concept_question_resume_confirms_reading_not_tables(layer, monkeypatch, pick_alternative):
    from claview.a.agent.assumptions import build, reading_id
    from claview.a.agent import nodes

    calls = []
    def source(payload, schema):
        calls.append(1)
        p = P(payload)
        def one(chosen, unused):
            return reading([req("r1", ind(p, chosen))], [bind(c["id"]) for c in payload["conditions"]],
                           request=[R(p.s("보여줘"))], unused=[{"ref": ind(p, unused), "reason": "unresolved"}])
        return proposal(one("가상대출금", "가상예금액"), alternative=one("가상예금액", "가상대출금"))

    state = run_session("가상대출금 가상예금액 2023년 보여줘", layer=layer, as_of="2026-10-21",
                        decomposition={"mode": "gpt", "proposal_source": source})
    assert state["analysis"]["decomposition"]["applied_source"] == "model"
    assert state["response"]["status"] == "awaiting_answer"
    assert state["response"]["question"]["dimension"] == "measurement_concept"
    assert not any(r["reason_code"] in ("first_assignment", "user_selection")
                   for r in state["response"]["assumptions"]["items"])
    alternative = session_data(state)["readings"][0]["analysis"]
    expected = alternative if pick_alternative else copy.deepcopy(state["analysis"])
    # Cache entries from the previous reading must never survive adoption.
    session_data(state)["b_cache"]["old-reading"] = {"status": "returned"}
    def forbidden(*args, **kwargs):
        raise AssertionError("extra decomposition call")
    monkeypatch.setattr(nodes, "execute_decomposition", forbidden)
    q = state["response"]["question"]
    answer = {"request_id": state["request_id"], "question_version": 1,
              "question_id": q["question_id"], "option_id": q["options"][int(pick_alternative)]["option_id"]}
    got = resume_session(state, answer, layer=layer)
    assert "answer_error" not in got["response"]
    assert got["analysis"] == expected and check_analysis(got["analysis"]) == []
    assert got["response"]["status"] != "awaiting_answer"
    assert len(calls) == 1 and got["control"]["attempts"]["interpret"] == 1
    assert session_data(got)["choices"] == {}
    assert session_data(got)["reading_choice"]["reading_id"] == reading_id(expected)
    rows = got["response"]["assumptions"]["items"]
    confirmed = [r for r in rows if r["reason_code"] == "user_interpretation"]
    assert len(confirmed) == 1 and confirmed[0]["confirmed"] and confirmed[0]["source"] == "user"
    assert confirmed[0]["adopted"]["value"] == reading_id(expected)
    assert not any(r["reason_code"] == "user_selection" for r in rows)
    defaults = [r for r in rows if r["reason_code"] == "first_assignment"]
    assert defaults and all(r["source"] == "default" and not r["confirmed"] for r in defaults)
    if pick_alternative:
        assert not session_data(got)["b_cache"]
        assert session_data(got)["v1_invalidated"] == "reading_changed"
        assert got["verification"]["interpretation"] is None and got["jev_record"] is None
    assert resume_session(got, answer, layer=layer) == got
    assert build(got) == got["response"]["assumptions"]
