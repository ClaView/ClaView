"""Prepared B conditions survive A's HTTP answers and record reload, without sending them.

The real A service and public response model run with scripted judgments and temporary records.
The suite's network fixture blocks external connections; no B service or live judge is supplied.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_a_http_1102 import AS_OF, PERIOD_QUERY, VERSION, no_judgments, script

from claview_a import contract as K
from claview_a_api import http as H
from claview_a_api import screen as S
from claview_a_api.api import app
from claview_a_api.run_record import RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_common.contracts.b import BRequest


def _judges(question, has_period, has_frequency):
    output = script()
    if has_period:
        output["periods"][0].update(
            quote="2024년", meaning="range", form="absolute", start="2024", end="same"
        )
    if not has_frequency:
        output["frequencies"][0].update(quote="M2 통화량", value="not_stated")
    judge = ScriptJudge(by_question({question: output}), answer_contract=K.CONTRACT_VERSION)
    return S.Judges(judge, judge, "script"), judge


def _reply(view, step):
    kind, mode, value = step
    question, = [question for question in view["questions"] if question["kind"] == kind]
    reply = {"question_id": question["question_id"], "question_version": question["question_version"]}
    selected_range = None
    if mode == "text":
        reply["text"] = value
    else:
        option = next(option for option in question["options"] if option["value"] == value)
        reply["option_id"] = option["option_id"]
        selected_range = option.get("range")
    return reply, selected_range


def _body(view, reply):
    return {"contract_version": VERSION, "session_id": view["session"]["session_id"], "replies": [reply]}


def _assert_no_calls(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["llm_calls"] == body["meta"]["external_calls"] == 0
    assert body["execution_mode"] == "synthetic"
    return body["view"]


@pytest.mark.parametrize(
    ("question", "has_period", "has_frequency", "steps", "sources"),
    [
        (PERIOD_QUERY, False, True, [("period", "text", "2024년")], ("user_answer", "question")),
        (PERIOD_QUERY, False, True, [("period", "option", "최근 3년")], ("user_answer", "question")),
        ("M2 통화량을 2024년 보여줘", True, False,
         [("frequency", "option", "M")], ("quoted", "user_answer")),
        ("M2 통화량을 보여줘", False, False,
         [("period", "text", "2024년"), ("frequency", "option", "M")], ("user_answer", "user_answer")),
    ],
    ids=["typed-period", "selected-period", "frequency-only", "both-answered"],
)
def test_prepared_condition_survives_http_answers_and_record_reload(
    tmp_path, monkeypatch, question, has_period, has_frequency, steps, sources
):
    store = RunStore(tmp_path / "http_records")
    supplied, judge = _judges(question, has_period, has_frequency)
    direct_store = RunStore(tmp_path / "direct_records")
    direct_judges, direct_judge = _judges(question, has_period, has_frequency)
    monkeypatch.setattr(S, "live_judges", no_judgments)
    app.dependency_overrides[H.get_store] = lambda: store
    app.dependency_overrides[H.get_judges] = lambda: supplied
    try:
        with TestClient(app) as client:
            first = client.post("/a/select", json={"contract_version": VERSION, "query": question, "as_of": AS_OF})
            view = _assert_no_calls(first)
            assert view["status"] == "needs_answer" and not view["handoff"]["ready"]
            session_id = view["session"]["session_id"]
            direct = S.select(question, AS_OF, direct_judges, store=direct_store)
            app.dependency_overrides[H.get_judges] = no_judgments
            # A new store instance reconstructs the view from the server-held checkpoint.
            app.dependency_overrides[H.get_store] = lambda: RunStore(store.root)
            before = _assert_no_calls(client.get(f"/a/records/{session_id}"))
            assert before == view
            selected_period = None
            for index, step in enumerate(steps):
                reply, shown_range = _reply(view, step)
                direct_reply, _ = _reply(direct["view"], step)
                if step[0] == "period" and shown_range:
                    selected_period = shown_range
                view = _assert_no_calls(client.post("/a/answer", json=_body(view, reply)))
                direct = S.answer(
                    direct["checkpoint"], direct["view"]["session"]["session_id"], [direct_reply], direct_store
                )
                if index < len(steps) - 1:
                    assert view["status"] == "needs_answer" and not view["handoff"]["ready"]
                assert _assert_no_calls(client.get(f"/a/records/{session_id}")) == view

            assert view["status"] == "ready" and view["session"]["as_of"] == AS_OF
            request, = view["handoff"]["ready"]
            assert request["b_request_issue"] is None
            prepared = request["b_request"]
            assert BRequest.model_validate(prepared).model_dump() == prepared
            assert prepared["query"] == question and prepared["as_of"] == AS_OF
            assert prepared == direct["view"]["handoff"]["ready"][0]["b_request"]
            table, = prepared["tables"]
            condition = table["period_condition"]
            visible_table, = view["statistics"][0]["tables"]
            assert (condition["period_source"], condition["frequency_source"]) == sources
            assert condition["period"] == visible_table["period"]
            assert condition["frequency"] == visible_table["frequency"] == "M"
            if selected_period:
                assert condition["period"] == [selected_period]
            else:
                assert condition["period"] == [{"start": "2024-01", "end": "2024-12"}]
            answers = {answer["kind"]: answer for answer in condition["answers"]}
            assert set(answers) == {step[0] for step in steps}
            for answer in answers.values():
                assert answer["source"] == "user_answer" and answer["statistics"] == ["s1"]
            assert request["tables"] == [{key: value for key, value in table.items() if key != "period_condition"}]
            assert all("period_condition" not in legacy for legacy in request["tables"])

            record = json.loads((store.root / "a_sessions" / session_id / "record.json").read_text(encoding="utf-8"))
            assert record["external_counts"] == {"B_model": 0, "KOSIS": 0}
            # The record holds the checkpoint, not a cached prepared request: reload must reassemble it.
            assert "b_request" not in record["handoff"]
            reloaded = S.load(RunStore(store.root), session_id)["view"]
            assert reloaded == view
        assert judge.calls == direct_judge.calls == 1
    finally:
        app.dependency_overrides.clear()
