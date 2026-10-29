"""지수의 기준연도 표기와 '상위 항목 아래 하위 항목' 질문에서 B의 코드 규칙이 질문을 바로 읽는지 본다."""

from __future__ import annotations

import pytest

from claview_b.contracts import StageFailure
from claview_b.stages import named_parent_value, resolve_explicit_period, stated_years

TABLE = {"periods": {"M": {"start": "200001", "end": "202608"}, "Y": {"start": "2000", "end": "2025"}}}


@pytest.mark.parametrize(
    "query, years",
    [
        ("2024년 월별 수출물가 경유를 보여줘", ["2024"]),
        ("2024년 월별 수출물가 경유, 2020=100으로 해줘", ["2024"]),
        ("2024년 월별 수출물가 경유를 2020＝100 기준으로 비교해", ["2024"]),
        ("2024년 월별 수출물가 경유를 2020년=100 으로", ["2024"]),
        ("2024년 월별 수출물가 경유, 기준연도 2020년으로", ["2024"]),
        ("2020년부터 2024년까지 수출물가 (2015=100)", ["2020", "2024"]),
        ("2020=100 기준 수출물가 경유를 보여줘", []),
    ],
)
def test_base_year_notation_is_not_a_requested_year(query, years):
    assert stated_years(query) == years


@pytest.mark.parametrize(
    "query",
    [
        "2024년 월별 수출물가 경유를 보여줘. 모두 원화 기준, 2020=100으로 해줘.",
        "2024년 월별 수출물량지수 나프타를 2020=100 기준으로 비교해",
        "2024년 월별 수출물가 경유 기준연도 2020년으로 보여줘",
    ],
)
def test_explicit_period_ignores_the_base_year(query):
    # before the fix the base year was read as the end year: 202401~202012 -> invalid_period
    assert resolve_explicit_period(query, TABLE, "M") == {
        "frequency": "M",
        "start": "202401",
        "end": "202412",
    }


def test_a_question_with_only_a_base_year_states_no_period():
    assert resolve_explicit_period("수출물가 경유를 2020=100으로 보여줘", TABLE, "M") is None


def test_a_real_period_error_still_fails():
    with pytest.raises(StageFailure) as stopped:
        resolve_explicit_period("2024년부터 2020년까지 월별 수출물가", TABLE, "M")
    assert stopped.value.code == "invalid_period"


def values(*rows):
    """code -> {"name", "path"} like the metadata's classification values; path lists the ancestors."""
    out = {}
    for code, name, parents in rows:
        path = [{"code": c, "name": n} for c, n in parents] + [{"code": code, "name": name}]
        out[code] = {"name": name, "path": path}
    return out


DEPOSITS = values(
    ("A21", "순수저축성예금 1)", [("A2", "저축성수신")]),
    ("A211", "정기예금", [("A2", "저축성수신"), ("A21", "순수저축성예금 1)")]),
    (
        "A2113",
        "정기예금(1~2년 미만) 2)",
        [("A2", "저축성수신"), ("A21", "순수저축성예금 1)"), ("A211", "정기예금")],
    ),
    (
        "A2118",
        "정기예금(1년)",
        [("A2", "저축성수신"), ("A21", "순수저축성예금 1)"), ("A211", "정기예금"), ("A2113", "정기예금(1~2년 미만) 2)")],
    ),
    (
        "A2111",
        "정기예금(6개월 미만) 2)",
        [("A2", "저축성수신"), ("A21", "순수저축성예금 1)"), ("A211", "정기예금")],
    ),
)

@pytest.mark.parametrize(
    "query, code",
    [
        ("2024년 월별 신규취급액 수신금리 표에서 정기예금 아래 1년 만기를 골라줘", "A2118"),
        ("정기예금 아래 6개월 미만 금리를 보여줘", "A2111"),
        ("정기예금 중 1~2년 미만 금리", "A2113"),
    ],
)
def test_a_sub_value_the_question_spells_out_is_not_replaced_by_its_parent(query, code):
    assert named_parent_value(query, DEPOSITS, code) is None


def test_a_sub_type_of_a_named_value_still_goes_to_the_named_value():
    # the original purpose of the rule: the question names only the ancestor
    assert named_parent_value("정기예금 금리를 보여줘", DEPOSITS, "A2118") == "A211"
