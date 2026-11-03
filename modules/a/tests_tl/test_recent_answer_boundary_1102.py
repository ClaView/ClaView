"""A period answer keeps its explicit boundary and the window offered for its chosen frequency.

Scripted model rows on the real catalog, no model or data requests. These cases complement the recent-window
tests with an incomplete period and an undecided frequency, including answers submitted in either order.
"""

from __future__ import annotations

import copy
import json

import pytest

from test_period_semantics_1102 import AS_OF, F, P, QUOTE, TAIL, output

from claview_a import answers as AN
from claview_a import contract as K
from claview_a import graph as G


def state_for(layer, end="2025-12"):
    quote = f"{end[:4]}년 {int(end[5:])}월까지"
    question = f"{quote} {TAIL}"
    out = output(question, layer, [P(quote, meaning="user_undecided", end=end)],
                 [F(QUOTE, "not_stated")], contract=K.CONTRACT_VERSION)

    def judge(payload, request):
        return json.dumps(out, ensure_ascii=False)

    judge.answer_contract = K.CONTRACT_VERSION
    return G.run(question, layer, judge, K.Settings("fake"), AS_OF)


def response(state, kind, value, *, typed=False):
    ask = next(q for q in AN.questions(state) if q["kind"] == kind)
    row = {"question_id": ask["question_id"], "question_version": AN.question_version(state)}
    if typed:
        return {**row, "text": value}
    return {**row, "option_id": next(o["option_id"] for o in ask["options"] if o["value"] == value)}


@pytest.mark.parametrize("end,frequency,start", [("2025-12", "Y", "2023-01"), ("2026-06", "Q", "2023-07")])
@pytest.mark.parametrize("typed", [False, True])
def test_partial_end_recent_answer_uses_a_supporting_frequency(frozen_metadata_layer, end, frequency, start, typed):
    # Fix the catalog endpoints that define this explicit-boundary policy case.
    layer = frozen_metadata_layer
    state = state_for(layer, end)
    before = copy.deepcopy(state)
    ask = next(q for q in AN.questions(state) if q["kind"] == "period")
    option = next(o for o in ask["options"] if o["value"] == "최근 3년")
    expected = {"start": start, "end": end}
    assert option["ranges"] == {frequency: expected}
    after = AN.apply(state, [response(state, "period", "최근 3년", typed=typed)], layer)
    (decision,) = after["record"]["decisions"]
    assert decision["outcome"] == "selected"
    assert decision["frequency"]["value"] == frequency
    assert decision["frequency"]["source"] == "catalog"
    assert decision["final_tables"][0]["period"] == [expected]
    assert decision["final_tables"][0]["period_phrase"]["source"] == "user_answer"
    assert decision["condition_answers"][0]["quote"] == "최근 3년"
    assert decision["condition_answers"][0]["source"] == "user_answer"
    if typed:
        assert decision["condition_answers"][0]["text"] == "최근 3년"
    else:
        assert decision["condition_answers"][0]["ranges"] == option["ranges"]
    assert state == before


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_same_submission_uses_the_answered_frequency_before_boundary_validation(layer, reverse, typed):
    state = state_for(layer)
    before = copy.deepcopy(state)
    replies = [response(state, "period", "최근 3년", typed=typed), response(state, "frequency", "Y")]
    after = AN.apply(state, replies[::-1] if reverse else replies, layer)
    (decision,) = after["record"]["decisions"]
    assert decision["outcome"] == "selected"
    assert decision["final_tables"][0]["period"] == [{"start": "2023-01", "end": "2025-12"}]
    assert decision["frequency"]["source"] == "user_answer"
    assert {a["kind"] for a in decision["condition_answers"]} == {"period", "frequency"}
    assert all(a["source"] == "user_answer" for a in decision["condition_answers"])
    assert state == before


@pytest.mark.parametrize("frequency", ["Q", "M"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_same_submission_cannot_replace_the_explicit_end(layer, frequency, reverse, typed):
    state = state_for(layer)
    before = copy.deepcopy(state)
    replies = [response(state, "period", "최근 3년", typed=typed), response(state, "frequency", frequency)]
    with pytest.raises(AN.AnswerError) as exc:
        AN.apply(state, replies[::-1] if reverse else replies, layer)
    assert exc.value.code == "answer_unreadable"
    assert state == before


def test_frequency_first_then_period_keeps_the_previous_user_answer(layer):
    initial = state_for(layer)
    after_frequency = AN.apply(initial, [response(initial, "frequency", "Y")], layer)
    (decision,) = after_frequency["record"]["decisions"]
    previous = copy.deepcopy(decision["condition_answers"][0])
    assert previous["kind"] == "frequency" and previous["source"] == "user_answer"
    after = AN.apply(after_frequency, [response(after_frequency, "period", "최근 3년")], layer)
    (final,) = after["record"]["decisions"]
    assert final["outcome"] == "selected"
    assert next(a for a in final["condition_answers"] if a["kind"] == "frequency") == previous
    assert final["final_tables"][0]["period"] == [{"start": "2023-01", "end": "2025-12"}]


def test_a_typed_absolute_period_cannot_change_the_existing_end(layer):
    state = state_for(layer)
    before = copy.deepcopy(state)
    with pytest.raises(AN.AnswerError) as exc:
        AN.apply(state, [response(state, "period", "2024년", typed=True)], layer)
    assert exc.value.code == "answer_unreadable"
    assert state == before
