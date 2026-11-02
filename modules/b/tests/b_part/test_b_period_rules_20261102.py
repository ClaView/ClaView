"""2026-11-02 B period rules (docs/modules/b/design/B_기간_수정_목록_20261102.md, group 가).

What is pinned here, per document item:
  가-2  count wording: "최근 8개 분기", "최근 3개년"
  가-3  months -> quarters / half-years / years only when they divide exactly
  가-4  a stated end with a relative count: "2025년까지 최근 3년"
  가-5  an open start: "2020년부터", "2020년 3월부터"
  가-6  one closed calendar period: 지난달, 지난 분기, 지난 반기, 지난해, 이번 달/분기/반기, 금년
  가-7  frequency cue words (월급 / 상·하반기 / 매년 ...)
  가-8  parts of a year: "1~3월", 상반기, 하반기, "2023년 2월부터 2024년까지"
작년(가-1) itself is covered in test_b_unit.py and the augmented golden cases.
"""

from __future__ import annotations

import pytest
from tests.b_part.test_b_unit import make_table

from claview_b.contracts import Config, StageFailure
from claview_b.stages import (
    decide_frequency,
    has_relative_words,
    infer_frequency,
    resolve_explicit_period,
    resolve_relative_period,
)

AS_OF = "2026-10-30"
Y = {"Y": {"start": "2010", "end": "2025"}}
H = {"H": {"start": "202001", "end": "202601"}}
Q = {"Q": {"start": "202001", "end": "202602"}}
Q_LONG = {"Q": {"start": "200001", "end": "202602"}}
M = {"M": {"start": "200001", "end": "202608"}}
ALL = {**Y, **H, **Q, **M}


def relative(query, periods, as_of=AS_OF, freq=None):
    table = make_table(periods=periods)
    return resolve_relative_period(query, table, Config(as_of=as_of), freq)


def period_of(result):
    return result.get("period") or result["result"]["resolved_period"]


def span(result):
    p = period_of(result)
    return p["frequency"], p["start"], p["end"]


def stops(query, periods, code="conditions_incomplete", as_of=AS_OF):
    with pytest.raises(StageFailure) as exc:
        relative(query, periods, as_of)
    assert exc.value.code == code


# --- 가-2, 가-3: counts and units -------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "periods", "expected"),
    [
        ("최근 8개 분기", Q, ("Q", "202403", "202602")),
        ("최근 8분기", Q, ("Q", "202403", "202602")),
        ("최근 3개년", Y, ("Y", "2023", "2025")),
        ("최근 3년간 연도별", Y, ("Y", "2023", "2025")),
        ("최근 4개 반기", H, ("H", "202402", "202601")),
        ("최근 6개월 분기별", Q, ("Q", "202601", "202602")),
        ("최근 12개월 연도별", Y, ("Y", "2025", "2025")),
        ("최근 2년 반기별", H, ("H", "202402", "202601")),
        ("최근 6개월", M, ("M", "202603", "202608")),
        ("지난 3개월", M, ("M", "202606", "202608")),
        ("최근 3년 월별", M, ("M", "202309", "202608")),
    ],
)
def test_counts_and_units_resolve(query, periods, expected):
    assert span(relative(query, periods)) == expected


@pytest.mark.parametrize(
    ("query", "periods"),
    [
        ("최근 5개월 분기별", Q),  # 5 months are not whole quarters
        ("최근 7개월 반기별", H),
        ("최근 18개월 연도별", Y),
        ("최근 0년 연도별", Y),
    ],
)
def test_counts_that_do_not_divide_into_whole_periods_stop(query, periods):
    stops(query, periods)


# --- 가-4: stated end with a count ------------------------------------------------------------


def test_stated_end_with_count_ends_at_the_stated_end():
    result = relative("2025년까지 최근 3년 월별", M)
    assert result["status"] == "resolved"  # the user's own end: no lag notice
    assert span(result) == ("M", "202301", "202512")


def test_stated_end_with_a_month():
    assert span(relative("2025년 3월까지 최근 6개월", M)) == ("M", "202410", "202503")


def test_stated_end_with_a_quarter():
    assert span(relative("2025년 4분기까지 최근 8분기", Q)) == ("Q", "202401", "202504")


def test_stated_end_beyond_the_table_is_cut_with_a_notice():
    result = relative("2027년까지 최근 3년 연도별", Y)
    assert result["status"] == "needs_confirmation"
    assert span(result) == ("Y", "2023", "2025")
    assert "2027" in result["result"]["message"]
    assert "?" not in result["result"]["message"]


def test_stated_end_before_the_table_starts_stops():
    stops("2005년까지 최근 3년 연도별", Y, code="metadata_refresh_required")


# --- 가-5: open start ---------------------------------------------------------------------------


def test_year_to_now_is_open_ended():
    assert span(relative("2020년부터 연도별", Y, as_of="2025-12-15")) == ("Y", "2020", "2025")
    assert span(relative("2020년부터 월별", M)) == ("M", "202001", "202608")
    assert span(relative("2020년 이후 월별", M)) == ("M", "202001", "202608")


def test_open_start_with_a_month_or_quarter():
    assert span(relative("2020년 3월부터 월별", M)) == ("M", "202003", "202608")
    assert span(relative("2022년 2분기부터 분기별", Q)) == ("Q", "202202", "202602")


def test_open_start_reaching_today_by_word():
    assert span(relative("2020년부터 현재까지 연도별", Y, as_of="2025-12-15")) == ("Y", "2020", "2025")
    assert span(relative("2020년부터 최근까지 연도별", Y, as_of="2025-12-15")) == ("Y", "2020", "2025")


def test_open_start_with_a_comparison_starts_earlier():
    assert span(relative("2020년부터 월별로 전년 대비 증가율", M)) == ("M", "201901", "202608")


def test_open_start_that_cannot_start_on_a_period_boundary_stops():
    stops("2020년 2월부터 분기별", Q)


def test_a_stated_end_is_not_an_open_start():
    assert not has_relative_words("2020년부터 2024년까지 연도별")
    assert not has_relative_words("2020년 1월부터 6월까지 월별")
    assert has_relative_words("2020년부터 연도별")
    assert has_relative_words("2020년 3월부터 월별")


# --- 가-6: one closed calendar period -----------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "periods", "as_of", "expected"),
    [
        ("지난달 월별", M, "2026-09-10", ("M", "202608", "202608")),
        ("이번 달 월별", M, "2026-08-15", ("M", "202608", "202608")),
        ("지난 분기 분기별", Q, "2026-05-10", ("Q", "202601", "202601")),
        ("이번 분기 분기별", Q, "2026-05-10", ("Q", "202602", "202602")),
        ("지난 반기 반기별", H, "2026-10-01", ("H", "202601", "202601")),
        ("지난해 연도별", Y, "2026-10-01", ("Y", "2025", "2025")),
        ("지난해 월별", M, "2026-10-01", ("M", "202501", "202512")),
        ("작년 3분기 분기별", Q, "2026-10-01", ("Q", "202503", "202503")),
        ("작년 1~3월 월별", M, "2026-10-01", ("M", "202501", "202503")),
        ("작년 하반기 월별", M, "2026-10-01", ("M", "202507", "202512")),
    ],
)
def test_closed_calendar_period_inside_the_table(query, periods, as_of, expected):
    result = relative(query, periods, as_of=as_of)
    assert result["status"] == "resolved"
    assert span(result) == expected


def test_this_year_words_keep_the_tables_latest_year():
    for word in ("올해", "금년", "이번 해"):
        assert span(relative(f"{word} 연도별", Y, as_of="2026-10-01")) == ("Y", "2025", "2025")


def test_closed_period_after_the_table_is_replaced_by_the_one_holding_the_latest_period():
    result = relative("지난달 월별", M)  # 지난달 = 2026-09, table ends 202608
    assert result["status"] == "needs_confirmation"
    assert span(result) == ("M", "202608", "202608")
    assert result["result"]["requested_as_of_period"] == "202609"
    assert "지난달(2026년 9월)" in result["result"]["message"]
    assert "?" not in result["result"]["message"]

    result = relative("지난 분기 분기별", Q)  # 지난 분기 = 2026 Q3, table ends 2026 Q2
    assert result["status"] == "needs_confirmation"
    assert span(result) == ("Q", "202602", "202602")

    result = relative("지난 반기 반기별", H, as_of="2027-02-01")  # 2026 하반기 is not there yet
    assert result["status"] == "needs_confirmation"
    assert span(result) == ("H", "202601", "202601")


def test_closed_period_partly_in_the_table_is_cut_with_a_notice():
    result = relative("이번 분기 월별", M, as_of="2026-08-15")  # 2026 Q3 = 7~9월, table ends 202608
    assert result["status"] == "needs_confirmation"
    assert span(result) == ("M", "202607", "202608")
    assert "일부" in result["result"]["message"]


def test_closed_period_before_the_table_starts_stops():
    stops("지난해 연도별", {"Y": {"start": "2026", "end": "2026"}}, code="metadata_refresh_required")
    stops("작년 3분기 분기별", {"Q": {"start": "202601", "end": "202602"}}, code="metadata_refresh_required")


def test_closed_period_the_frequency_cannot_show_exactly_stops():
    stops("지난달 연도별", Y)
    stops("작년 3분기 연도별", Y)


def test_a_comparison_baseline_is_not_the_requested_period():
    # "이번 달 ... 지난달보다": the question is about 이번 달
    assert span(relative("이번 달 매출이 지난달보다 얼마나 늘었는지 월별", M, as_of="2026-08-15")) == (
        "M",
        "202608",
        "202608",
    )
    # only the baseline named: not the period being asked for
    stops("지난달 대비 증감 월별", M)


def test_wording_without_a_period_still_stops():
    for query in ("지금 상황을 연도별로 보여줘", "현재 연도별", "최신 연도별", "최근 연도별", "지난 연도별 추이"):
        stops(query, Y)


# --- 가-7: frequency cue words ----------------------------------------------------------------


def freq(query, periods=ALL):
    return decide_frequency(query, make_table(periods=periods))


def test_a_stated_frequency_wins():
    assert freq("분기별로 보여줘") == ("Q", [])
    assert freq("반기별로 보여줘") == ("H", [])
    assert freq("월별로 보여줘") == ("M", [])
    assert freq("2024년 3월 매출") == ("M", [])
    assert freq("지난달 매출") == ("M", [])
    assert freq("이번 달 매출") == ("M", [])
    assert freq("어느 달이 가장 높아?") == ("M", [])


def test_year_cues():
    for text in ("연간 추이", "연도별 추이", "년도별 추이", "매년 추이", "연별 추이"):
        assert freq(text) == ("Y", []), text


def test_words_that_merely_contain_month_are_not_a_monthly_cue():
    assert freq("월급 연도별 추이") == ("Y", [])
    # no cue at all: the longest frequency is the default, the rest are alternatives
    assert freq("월평균 임금 추이") == ("Y", ["H", "Q", "M"])
    assert freq("월세 지수 추이", {"Y": Y["Y"], "M": M["M"]}) == ("Y", ["M"])
    # a digit before it is a month again
    assert freq("1월 월평균 기온") == ("M", [])


def test_half_of_year_words_are_not_the_half_year_frequency():
    assert freq("2024년 상반기 월별") == ("M", [])
    assert freq("2024년 상반기 분기별") == ("Q", [])
    assert freq("2024년 하반기 월별") == ("M", [])
    # no other cue: half-year periods when the table has them, else what can show exactly that part
    assert freq("2024년 상반기 소득") == ("H", [])
    assert freq("2024년 상반기 소득", {**Y, **Q, **M}) == ("Q", ["M"])
    assert freq("2024년 상반기 소득", {**Y, **M}) == ("M", [])
    assert freq("반기별 상반기와 하반기") == ("H", [])


def test_a_count_is_a_range_not_a_frequency():
    assert freq("최근 6개월 분기별", {**Q, **M}) == ("Q", [])
    assert freq("최근 8분기", {**Q, **M}) == ("Q", [])
    assert freq("최근 6개월", {**Q, **M}) == ("M", [])
    # years only name the range: the default (longest) and the alternatives stay as before
    assert freq("최근 3년") == ("Y", ["H", "Q", "M"])
    assert freq("최근 3개년") == ("Y", ["H", "Q", "M"])
    # the table has no quarters: the count's own unit is converted exactly instead of stopping
    assert freq("최근 8분기", M) == ("M", [])
    assert span(relative("최근 8분기", M)) == ("M", "202409", "202608")


def test_infer_frequency_reads_the_same_cues():
    table = make_table(periods=ALL)
    assert infer_frequency("2024년 상반기 월별", table) == "M"
    assert infer_frequency("월급 연도별", table) == "Y"
    assert infer_frequency("월평균 임금", table) is None
    assert infer_frequency("월평균 임금", make_table(periods=Y)) == "Y"


# --- 가-8: parts of a year ----------------------------------------------------------------------


def explicit(query, periods):
    table = make_table(periods=periods)
    f, _alternatives = decide_frequency(query, table)
    result = resolve_explicit_period(query, table, f)
    return None if result is None else (result["frequency"], result["start"], result["end"])


@pytest.mark.parametrize(
    ("query", "periods", "expected"),
    [
        ("2024년 1~3월 월별", M, ("M", "202401", "202403")),
        ("2024년 1월부터 3월까지 월별", M, ("M", "202401", "202403")),
        ("2024년 3월 월별", M, ("M", "202403", "202403")),
        ("2024년 상반기 월별", M, ("M", "202401", "202406")),
        ("2024년 하반기 월별", M, ("M", "202407", "202412")),
        ("2024년 하반기 반기별", H, ("H", "202402", "202402")),
        ("2024년 상반기 반기별", H, ("H", "202401", "202401")),
        ("2024년 상반기 분기별", Q, ("Q", "202401", "202402")),
        ("2024년 1분기 월별", M, ("M", "202401", "202403")),
        ("2024년 2~3분기 분기별", Q, ("Q", "202402", "202403")),
        ("2023년 2월부터 2024년까지 월별", M, ("M", "202302", "202412")),
        ("2020년부터 2024년 3분기까지 분기별", Q, ("Q", "202001", "202403")),
        ("2008년 1분기부터 2024년 4분기까지 분기별", Q_LONG, ("Q", "200801", "202404")),
        ("2020년부터 2024년까지 연도별", Y, ("Y", "2020", "2024")),
        ("2024년 상반기 소득", {**Y, **M}, ("M", "202401", "202406")),
    ],
)
def test_parts_of_a_year(query, periods, expected):
    assert explicit(query, periods) == expected


def test_a_part_the_frequency_cannot_show_exactly_stops():
    for query, periods in (
        ("2024년 2월 분기별", Q),
        ("2024년 상반기 연도별", Y),
        ("2024년 1분기 반기별", H),
    ):
        table = make_table(periods=periods)
        f, _alternatives = decide_frequency(query, table)
        with pytest.raises(StageFailure) as exc:
            resolve_explicit_period(query, table, f)
        assert exc.value.code == "conditions_incomplete", query


def test_a_comparison_wording_still_starts_earlier():
    assert explicit("2024년 월별로 전년보다 얼마나 올랐는지", M) == ("M", "202301", "202412")
