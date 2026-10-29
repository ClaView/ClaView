"""'통계표를 사용해'는 수치표 표시 요청이 아니다. 수치표로 보여 달라는 요청은 그대로 인식한다."""

import pytest

from claview_c.chart_policy import requests


def kinds(query):
    return [r["kind"] for r in requests(query)]


@pytest.mark.parametrize(
    "query",
    [
        "월별 예금은행 대출금리의 신규취급액 표와 잔액 기준 표를 각각 사용해, 대기업대출만 골라 선그래프로 비교해줘",
        "산업별대출금 표를 각각 사용해 제조업 아래 섬유제품만 골라 비교해줘",
        "수출물가와 생산자물가의 기본분류 표를 각각 선택해 비교해줘",
        "두 표를 함께 조회해 선그래프로 보여줘",
    ],
)
def test_using_a_statistics_table_is_not_a_numeric_table_request(query):
    assert "table" not in kinds(query)


def test_an_explicit_line_request_wins_when_the_question_also_names_tables():
    assert kinds("신규취급액 표와 잔액 기준 표를 각각 사용해 선그래프로 비교해줘") == ["line"]


@pytest.mark.parametrize(
    "query",
    [
        "수치표로 보여줘",
        "표로 정리해줘",
        "금리를 표로 보여줘",
        "데이터표를 보여줘",
        "표 형식으로 보여줘",
        "이 값을 표를 만들어 보여줘",
    ],
)
def test_a_numeric_table_request_is_still_recognised(query):
    assert "table" in kinds(query)
