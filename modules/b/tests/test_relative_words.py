"""지표 이름 속의 낱말("현재생활형편CSI"의 "현재")을 기간 표현으로 잘못 읽지 않는 규칙을 본다."""

from __future__ import annotations

import pytest

from claview_b.stages import has_relative_words, resolve_explicit_period

VALUES = {
    "CSI.A": {"name": "현재생활형편CSI", "path": [{"code": "CSI.A", "name": "현재생활형편CSI"}]},
    "CSI.B": {"name": "생활형편전망CSI", "path": [{"code": "CSI.B", "name": "생활형편전망CSI"}]},
}
TABLE = {
    "dimensions": {"objL1": {"values": VALUES}},
    "periods": {"M": {"start": "200809", "end": "202412"}},
}


@pytest.mark.parametrize(
    "query",
    [
        "2024년 현재생활형편CSI와 생활형편전망CSI를 월별로 비교해줘.",
        "2024년 현재생활형편CSI를 월별로 보여줘.",
    ],
)
def test_a_name_that_contains_the_word_is_not_relative_wording(query):
    assert not has_relative_words(query)  # 표 없이도 뒤에 한글이 이어지면 기간 표현이 아님


@pytest.mark.parametrize(
    "query",
    [
        "2024년 현재 생활형편CSI를 월별로 보여줘.",
        "2024년 현재 생활형편 CSI를 월별로 보여줘.",
        "2024년 현재 생활 형편CSI를 월별로 보여줘.",
    ],
)
def test_the_name_with_other_spacing_is_found_in_the_table_and_removed(query):
    assert has_relative_words(query)  # 표를 모르면 띄어 쓴 "현재"는 기간 표현으로 본다
    assert not has_relative_words(query, TABLE)


@pytest.mark.parametrize(
    "query",
    [
        "현재 금리를 보여줘.",
        "현재까지의 추이를 보여줘.",
        "현재는 어때?",
        "현재의 소비심리를 보여줘.",
        "최근 5년 생활형편전망CSI를 보여줘.",
        "올해 현재생활형편CSI를 보여줘.",
        "작년 현재생활형편CSI를 보여줘.",
    ],
)
def test_real_relative_wording_is_still_found(query):
    assert has_relative_words(query, TABLE)


def test_a_stated_year_with_such_a_name_resolves_the_period():
    query = "2024년 전국 전체 가구의 현재생활형편CSI와 생활형편전망CSI를 월별 선그래프로 비교해줘."
    period = resolve_explicit_period(query, TABLE, "M")
    assert period == {"frequency": "M", "start": "202401", "end": "202412"}
