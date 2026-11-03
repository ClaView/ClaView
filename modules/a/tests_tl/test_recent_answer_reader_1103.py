"""Direct period answers retain counted units, explicit ends and unread constraints."""

from __future__ import annotations

import pytest

from claview_a import periods as P
from claview_a.conditions import q_rules


def period_rows(reading):
    return [condition for condition in reading["conditions"] if condition["kind"] == "period"]


@pytest.mark.parametrize("quote", ["최근 3개년", "최근 3개 년", "지난 7개년도", "최근 12 개 년도"])
def test_counted_year_spellings_keep_the_original_quote(quote):
    got = P.read_quotes([quote], [])
    assert not got["problems"]
    row, = period_rows(got)
    assert row["quote"] == quote
    assert row["attrs"]["form"] == "recent" and row["attrs"]["unit"] == "year"
    assert row["attrs"]["amount"] == int("".join(c for c in quote if c.isdigit()))
    assert row["attrs"]["start_expr"] is row["attrs"]["end_expr"] is None
    assert q_rules.find_frequencies(quote) == []


@pytest.mark.parametrize("quote,year,sub_unit,sub_value,amount,unit", [
    ("2025년까지 최근 3년", "2025", None, None, 3, "year"),
    ("최근 3년 2025년까지", "2025", None, None, 3, "year"),
    ("2025년까지의 최근 3개년도", "2025", None, None, 3, "year"),
    ("최근 8개 분기 2025년 3분기까지", "2025", "Q", "3", 8, "quarter"),
    ("2025년 8월까지 최근 6개월", "2025", "M", "8", 6, "month"),
    ("2025년 하반기까지 최근 2개 반기", "2025", "H", "하", 2, "half"),
])
def test_explicit_end_and_count_are_one_uncomputed_condition(quote, year, sub_unit, sub_value, amount, unit):
    got = P.read_quotes([quote], [])
    assert not got["problems"]
    row, = period_rows(got)
    assert row["quote"] == quote
    attrs = row["attrs"]
    assert attrs["form"] == "recent" and attrs["amount"] == amount and attrs["unit"] == unit
    assert attrs["start_expr"] is None
    assert (attrs["end_expr"]["year"], attrs["end_expr"]["sub_unit"], attrs["end_expr"]["sub_value"]) == (
        year, sub_unit, sub_value)
    raw, = q_rules.find_periods(quote, "2000-01-01")
    assert raw["start"] is raw["end"] is None
    assert raw["status"] == "unresolved"
    assert q_rules.find_frequencies(quote) == []


@pytest.mark.parametrize("quote", [
    "2025년까지 최근 3년 제외", "최근 3개년 빼고", "2025년까지 최근 3년 이전",
    "2025년까지 최근 3년 2개월", "2025년까지 최근 3년 초반",
    "2025년까지 제외하고 최근 3년",
])
def test_unread_scope_words_are_not_consumed_by_the_new_pattern(quote):
    got = P.read_quotes([quote], [])
    assert any(problem["code"] == "period_partially_read" for problem in got["problems"])


def test_a_frequency_outside_the_bounded_recent_span_stays_a_frequency():
    quote = "2025년까지 최근 3개년 월별"
    got = P.read_quotes([quote], ["월별"])
    assert not got["problems"]
    row, = period_rows(got)
    assert row["quote"] == "2025년까지 최근 3개년"
    assert [value for value, _, _ in q_rules.find_frequencies(quote)] == ["M"]
