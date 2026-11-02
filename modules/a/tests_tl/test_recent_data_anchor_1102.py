"""Recent windows use available data; calendar requests and user answers keep their meaning.

All judges below are fixed inputs. These checks exercise A only and do not establish model quality or B support.
"""

from __future__ import annotations

import copy
import json

import pytest
from test_calendar_anchor_56 import months, view
from test_conditions_56 import go, question, reply
from test_semantic_calendar_fix_1102 import frequency, period

from claview_a import answers as AN
from claview_a import contract as K
from claview_a import graph as G
from claview_a import periods as PR
from claview_a.periods import semantic as S
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-29"


def select(quote, frequency_, views, *, semantic=False):
    if semantic:
        row = period(quote=quote, form="relative", start="none", end="none", count="3", unit="Y")
        conditions = S.read_rows([row], [frequency(frequency_)], AS_OF)["conditions"]
    else:
        words = {"M": "월별", "Q": "분기별", "Y": "연간", "H": "반기별"}
        conditions = PR.read_quotes([quote], [words[frequency_]])["conditions"]
    ids = list(views)
    return PR.final_tables(ids[0], ids, {t: "ok" for t in ids}, views, conditions, AS_OF)


@pytest.mark.parametrize("quote,frequency_,coverage,expected", [
    ("최근 3년", "M", ("M", "2000.01", "2026.08"), ("2023-09", "2026-08")),
    ("최근 6개월", "M", ("M", "2000.01", "2026.08"), ("2026-03", "2026-08")),
    ("최근 3년", "Q", ("Q", "2000 1/4", "2026 2/4"), ("2023-07", "2026-06")),
    ("최근 4분기", "Q", ("Q", "2000 1/4", "2026 2/4"), ("2025-07", "2026-06")),
    ("최근 3년", "Y", ("Y", "2000", "2025"), ("2023-01", "2025-12")),
])
def test_recent_range_counts_full_units_back_from_that_frequency_latest(quote, frequency_, coverage, expected):
    got = select(quote, frequency_, {"T": view("T", coverage)})
    assert got["status"] == "selected", got
    assert months(got) == [expected]
    assert not got["tables"][0].get("latest_unpublished")


def test_current_structured_meaning_and_stored_quote_use_the_same_latest_reference():
    views = {"T": view("T", ("M", "2000.01", "2026.08"))}
    quoted = select("최근 3년", "M", views)
    structured = select("최근 3년", "M", views, semantic=True)
    assert quoted["status"] == structured["status"] == "selected"
    assert months(quoted) == months(structured) == [("2023-09", "2026-08")]


@pytest.mark.parametrize("quote,expected", [
    ("2024년", ("2024-01", "2024-12")),
    ("2024년 3월부터 2025년 2월까지", ("2024-03", "2025-02")),
    ("올해", ("2026-01", "2026-10")),
    ("작년", ("2025-01", "2025-12")),
    ("지난달", ("2026-09", "2026-09")),
    ("2020년부터", ("2020-01", "2026-10")),
])
def test_calendar_and_explicit_requests_are_not_moved_to_the_latest_data(quote, expected):
    got = select(quote, "M", {"T": view("T", ("M", "2000.01", "2026.08"))})
    assert got["status"] == "selected", got
    assert months(got) == [expected]


def test_an_incomplete_history_does_not_succeed_with_a_shorter_recent_window():
    got = select("최근 3년", "M", {"T": view("T", ("M", "2025.01", "2026.08"))})
    assert got["status"] == "failed", got
    assert got["tables"] == []
    assert got["reason"] == "period_not_covered"


def test_a_discontinued_edition_does_not_make_its_own_old_window_look_current():
    views = {"OLD": view("OLD", ("M", "2000.01", "2015.12")),
             "NEW": view("NEW", ("M", "2016.01", "2026.08"))}
    got = select("최근 3년", "M", views)
    assert got["status"] == "selected", got
    assert [t["table_id"] for t in got["tables"]] == ["NEW"]
    assert months(got) == [("2023-09", "2026-08")]


def _assert_latest_three_years(decision, layer):
    (table,) = decision["final_tables"]
    assert table["frequency"] == "M"
    catalog = selection_catalog(layer).views[table["table_id"]]
    ends = [PR.P.metadata_months(f, end)[1] for f, _, end in catalog.periods if f == "M"]
    latest = max(ends)
    assert table["period"] == [{"start": PR.P.month_text(latest - 35), "end": PR.P.month_text(latest)}]


def test_missing_period_still_asks_and_preset_preview_matches_applied_answer(layer):
    state, decisions = go(question(word="월별로"), layer, frequencies=["월별"])
    original = copy.deepcopy(state)
    assert decisions["s1"]["outcome"] == "ask_condition"
    assert decisions["s1"]["period"]["default_applied"] is False
    (ask,) = AN.questions(state)
    option = next(o for o in ask["options"] if o["value"] == "최근 3년")
    raw_option = next(o for o in decisions["s1"]["condition_asks"][0]["options"] if o["value"] == "최근 3년")
    assert option["range"] == raw_option["ranges"]["M"]
    after = AN.apply(state, [{"question_id": ask["question_id"], "question_version": AN.question_version(state),
                              "option_id": option["option_id"]}], layer)
    decision = after["record"]["decisions"][0]
    _assert_latest_three_years(decision, layer)
    assert decision["final_tables"][0]["period"] == [option["range"]]
    assert decision["condition_answers"][0]["quote"] == "최근 3년"
    assert decision["condition_answers"][0]["source"] == "user_answer"
    assert decision["final_tables"][0]["period_phrase"]["source"] == "user_answer"
    assert after["record"]["model_output"] == state["record"]["model_output"]
    assert state == original


def test_relative_typed_answer_matches_direct_question_without_changing_the_users_words(layer):
    state, _ = go(question(word="월별로"), layer, frequencies=["월별"])
    (ask,) = AN.questions(state)
    answer = "최근 3년"
    after = AN.apply(state, [reply(state, ask, text=answer)], layer)
    decision = after["record"]["decisions"][0]
    _assert_latest_three_years(decision, layer)
    _, direct = go(question(answer, "월별로"), layer, periods=[answer], frequencies=["월별"])
    assert decision["final_tables"][0]["period"] == direct["s1"]["final_tables"][0]["period"]
    assert decision["condition_answers"][0]["text"] == answer
    assert decision["condition_answers"][0]["source"] == "user_answer"
    assert decision["final_tables"][0]["period_phrase"]["source"] == "user_answer"


def test_explicit_typed_answer_stays_fixed_and_keeps_user_source(layer):
    state, _ = go(question(word="월별로"), layer, frequencies=["월별"])
    (ask,) = AN.questions(state)
    answer = "2024년 3월부터 2024년 8월까지"
    after = AN.apply(state, [reply(state, ask, text=answer)], layer)
    decision = after["record"]["decisions"][0]
    assert decision["outcome"] == "selected"
    assert decision["final_tables"][0]["period"] == [{"start": "2024-03", "end": "2024-08"}]
    assert decision["condition_answers"][0]["text"] == answer
    assert decision["condition_answers"][0]["source"] == "user_answer"
    assert decision["final_tables"][0]["period_phrase"]["source"] == "user_answer"


def test_frequency_specific_option_dates_survive_period_then_frequency_answers(layer):
    state, decisions = go(question(), layer)
    assert decisions["s1"]["period"]["default_applied"] is False
    (ask,) = [q for q in AN.questions(state) if q["kind"] == "period"]
    option = next(o for o in ask["options"] if o["value"] == "최근 3년")
    assert len(option["ranges"]) > 1
    assert len({tuple(sorted(span.items())) for span in option["ranges"].values()}) > 1
    assert option["range"] is None  # a single fabricated calendar span cannot represent different frequencies
    after_period = AN.apply(state, [{"question_id": ask["question_id"], "question_version": AN.question_version(state),
                                    "option_id": option["option_id"]}], layer)
    decision = after_period["record"]["decisions"][0]
    period_answer = copy.deepcopy(decision["condition_answers"][0])
    assert period_answer["quote"] == "최근 3년" and period_answer["range"] is None
    assert period_answer["ranges"] == option["ranges"]
    assert period_answer["source"] == "user_answer"
    (frequency_ask,) = AN.questions(after_period)
    assert frequency_ask["kind"] == "frequency"
    monthly = next(o for o in frequency_ask["options"] if o["value"] == "M")
    after_frequency = AN.apply(after_period, [{"question_id": frequency_ask["question_id"],
                                              "question_version": AN.question_version(after_period),
                                              "option_id": monthly["option_id"]}], layer)
    final_decision = after_frequency["record"]["decisions"][0]
    assert final_decision["outcome"] == "selected"
    assert final_decision["final_tables"][0]["period"] == [option["ranges"]["M"]]
    assert final_decision["frequency"]["value"] == "M"
    assert final_decision["frequency"]["source"] == "user_answer"
    assert next(a for a in final_decision["condition_answers"] if a["kind"] == "period") == period_answer
    assert after_frequency["record"]["model_output"] == state["record"]["model_output"]


@pytest.mark.parametrize("end_offset,accepted", [(0, True), (-1, False)])
def test_relative_answer_checks_a_fixed_boundary_against_the_real_reference(layer, end_offset, accepted):
    from test_period_semantics_1102 import AS_OF as SEMANTIC_AS_OF
    from test_period_semantics_1102 import TABLE, TAIL, F, P, output

    catalog = selection_catalog(layer).views[TABLE]
    latest = max(PR.P.metadata_months(f, end)[1] for f, _, end in catalog.periods if f == "M")
    bound = PR.P.month_text(latest + end_offset)
    quote = f"{bound[:4]}년 {int(bound[5:])}월까지"
    question_ = f"{quote} 월별 {TAIL}"
    out = output(question_, layer, [P(quote, meaning="user_undecided", end=bound)], [F("월별", "M")],
                 contract=K.CONTRACT_VERSION)

    def judge(payload, request):
        return json.dumps(out, ensure_ascii=False)

    judge.answer_contract = K.CONTRACT_VERSION
    state = G.run(question_, layer, judge, K.Settings("fake"), SEMANTIC_AS_OF)
    original = copy.deepcopy(state)
    (ask,) = AN.questions(state)
    assert ask["partial_periods"] == [{"quote": quote, "start": None, "end": bound}]
    assert any(o["value"] == "최근 3년" for o in ask["options"]) is accepted
    response = reply(state, ask, text="최근 3년")
    if accepted:
        after = AN.apply(state, [response], layer)
        decision = after["record"]["decisions"][0]
        assert decision["outcome"] == "selected", decision["reasons"]
        assert decision["final_tables"][0]["period"][0]["end"] == bound
        assert decision["condition_answers"][0]["source"] == "user_answer"
    else:
        with pytest.raises(AN.AnswerError) as exc:
            AN.apply(state, [response], layer)
        assert exc.value.code == "answer_unreadable"
    assert state == original
