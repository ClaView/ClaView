"""Exact user windows and bounded relative periods, with no provider calls."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_conditions_56 import go, question, reply
from test_period_semantics_1102 import F, P

from claview_a import answers as AN
from claview_a import check as CH
from claview_a import periods as PR
from claview_a.periods import semantic as S

AS_OF = "2026-10-29"


def card(freq="M", end="2026.08"):
    start = {"M": "2000.01", "Q": "2000 1/4", "Y": "2000", "H": "2000 1/2"}[freq]
    return SimpleNamespace(table_id="T", periods=[(freq, start, end)], table_units=["십억원"])


@pytest.mark.parametrize("start,end,freq,expected", [
    ("2024-02", "2024-05", "Q", "not_fit"),
    ("2024-01", "2024-03", "Q", "confirmed_quote"),
    ("2024-02", "2024-05", "M", "confirmed_quote"),
    ("2024-02", "2024-12", "Y", "not_fit"),
])
def test_explicit_frequency_cannot_bypass_exact_boundary_check(start, end, freq, expected):
    reading = S.read_rows([P("기간", start=start, end=end)], [F("주기", freq)], AS_OF)
    c = card(freq, {"M": "2026.08", "Q": "2026 2/4", "Y": "2025"}[freq])
    got = CH._frequency_decision(reading, ["T"], SimpleNamespace(views={"T": c}), AS_OF)
    assert got["reason"] == expected
    assert got["value"] == freq


def test_typed_period_rejection_preserves_session_and_allows_corrected_answer(layer):
    state, _ = go(question(word="분기별로"), layer, frequencies=["분기별"])
    ask = next(q for q in AN.questions(state) if q["kind"] == "period")
    before = copy.deepcopy(state)
    with pytest.raises(AN.AnswerError) as exc:
        AN.apply(state, [reply(state, ask, text="2024년 2월부터 2024년 5월까지")], layer)
    assert exc.value.cause == "period_condition_not_representable"
    assert state == before
    result = AN.apply(state, [reply(state, ask, text="2024년 1월부터 2024년 6월까지")], layer)
    d = result["record"]["decisions"][0]
    assert d["outcome"] == "selected"
    assert d["final_tables"][0]["period"] == [{"start": "2024-01", "end": "2024-06"}]
    assert d["final_tables"][0]["period_phrase"]["source"] == "user_answer"


@pytest.mark.parametrize("end,freq,coverage,count,unit,expected", [
    ("2025", "M", "2026.08", "3", "Y", ("2023-01", "2025-12")),
    ("2024-Q3", "Q", "2026 2/4", "6", "M", ("2024-04", "2024-09")),
    ("2026", "Y", "2025", "3", "Y", ("2024-01", "2026-12")),
    ("2024-06", "M", "2026.08", "6", "M", ("2024-01", "2024-06")),
])
def test_bounded_recent_computes_from_stated_end_not_latest(end, freq, coverage, count, unit, expected):
    reading = S.read_rows([P("종료일과 기간 길이", form="relative", end=end, count=count, unit=unit)],
                          [F("주기", freq)], AS_OF)
    assert reading["problems"] == []
    got = PR.final_tables("T", ["T"], {"T": "ok"}, {"T": card(freq, coverage)}, reading["conditions"], AS_OF)
    assert got["status"] == "selected", got
    assert got["tables"][0]["period"] == [{"start": expected[0], "end": expected[1]}]
    assert PR.frequency_fit(reading["conditions"], freq, AS_OF)


def test_missing_future_bounded_request_does_not_succeed_on_a_shortened_share():
    reading = S.read_rows([P("기간", form="relative", end="2027", count="3", unit="Y")],
                          [F("연간", "Y")], AS_OF)
    got = PR.final_tables("T", ["T"], {"T": "ok"}, {"T": card("Y", "2025")},
                          reading["conditions"], AS_OF)
    assert got["status"] == "failed" and got["tables"] == []
    assert got["detail"] == "after_as_of"
    assert PR.requested_span(reading["conditions"][0], AS_OF) == {
        "start": "2025-01", "end": "2027-12", "open_end": False}


@pytest.mark.parametrize("rep", ["NEW", "OLD"])
def test_fully_covered_candidate_survives_another_candidates_missing_future(rep):
    reading = S.read_rows([P("기간", form="relative", end="2027", count="3", unit="Y")],
                          [F("연간", "Y")], AS_OF)
    views = {"NEW": card("Y", "2027"), "OLD": card("Y", "2025")}
    got = PR.final_tables(rep, list(views), dict.fromkeys(views, "ok"), views,
                          reading["conditions"], AS_OF)
    assert got["status"] == "selected", got
    assert got["tables"][0]["table_id"] == "NEW"
    assert got["tables"][0]["period"] == [{"start": "2025-01", "end": "2027-12"}]


@pytest.mark.parametrize("quote,frequency,expected", [
    ("이번 분기", "Q", True), ("지난 분기", "Q", True),
    ("지난달", "M", True), ("지난달", "Q", False),
])
def test_calendar_relative_unit_projects_without_implying_monthly_frequency(quote, frequency, expected):
    reading = PR.read_quotes([quote], [])
    assert reading["problems"] == []
    assert PR.frequency_fit(reading["conditions"], frequency, AS_OF) is expected


def test_bounded_recent_end_not_on_frequency_boundary_is_not_expanded():
    reading = S.read_rows([P("기간", form="relative", end="2024-05", count="6", unit="M")],
                          [F("분기별", "Q")], AS_OF)
    assert not PR.frequency_fit(reading["conditions"], "Q", AS_OF)
    got = PR.final_tables("T", ["T"], {"T": "ok"}, {"T": card("Q", "2026 2/4")},
                          reading["conditions"], AS_OF)
    assert got["status"] == "failed" and not got["tables"]


def test_typed_count_word_keeps_original_answer_and_matches_recent_years(layer):
    state, _ = go(question(word="월별로"), layer, frequencies=["월별"])
    ask = next(q for q in AN.questions(state) if q["kind"] == "period")
    results = [AN.apply(state, [reply(state, ask, text=text)], layer) for text in ("최근 3개년", "최근 3년")]
    first, second = [r["record"]["decisions"][0] for r in results]
    assert first["outcome"] == second["outcome"] == "selected"
    assert first["final_tables"][0]["period"] == second["final_tables"][0]["period"]
    assert first["condition_answers"][0]["text"] == "최근 3개년"
    assert first["final_tables"][0]["period_phrase"]["source"] == "user_answer"


def test_typed_bounded_recent_keeps_original_text_and_exact_range(layer):
    state, _ = go(question(word="월별로"), layer, frequencies=["월별"])
    ask = next(q for q in AN.questions(state) if q["kind"] == "period")
    text = "2025년까지 최근 3년"
    result = AN.apply(state, [reply(state, ask, text=text)], layer)
    d = result["record"]["decisions"][0]
    assert d["outcome"] == "selected"
    assert d["final_tables"][0]["period"] == [{"start": "2023-01", "end": "2025-12"}]
    assert d["condition_answers"][0]["text"] == text
    assert d["final_tables"][0]["period_phrase"]["source"] == "user_answer"
