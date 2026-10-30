"""질문이 다른 통계표를 가리키는 말("M2 상품별 구성표에서는")을 그 표의 값에 대한 요청으로 세지 않는 규칙을 본다."""

from __future__ import annotations

from claview_b.search import normalized
from claview_b.stages import drop_unrequested_rows, mask_table_references

PERIOD = {"frequency": "M", "start": "202401", "end": "202412"}
QUERY = (
    "2024년 월말 원계열 기준으로 M2 상품별 구성표에서는 M2 아래 만기 2년 미만 정기예적금을, "
    "Lf 상품별 구성표에서는 예금취급기관 발행 금융상품 아래 2년 이상 장기금융상품을 골라 비교해줘."
)
M2_TITLE = "M2 상품별 구성내역(말잔 원계열)"
LF_TITLE = "Lf 상품별 구성내역(말잔 원계열)"

ISSUED = ("LE01000", "예금취급기관 발행 금융상품")
LF_VALUES = {
    "LE01000": {"name": "예금취급기관 발행 금융상품 1)", "path": [{"code": "LE00000", "name": "Lf"}, {"code": "LE01000", "name": "예금취급기관 발행 금융상품 1)"}], "is_leaf": False},
    "LE01100": {"name": "M2", "path": [{"code": "LE00000", "name": "Lf"}, {"code": "LE01000", "name": "예금취급기관 발행 금융상품 1)"}, {"code": "LE01100", "name": "M2"}], "is_leaf": True},
    "LE01200": {"name": "2년이상 장기금융상품 2)", "path": [{"code": "LE00000", "name": "Lf"}, {"code": "LE01000", "name": "예금취급기관 발행 금융상품 1)"}, {"code": "LE01200", "name": "2년이상 장기금융상품 2)"}], "is_leaf": True},
}
for _code, _v in LF_VALUES.items():
    _v["parent_code"] = _v["path"][-2]["code"] if len(_v["path"]) > 1 else None


def table():
    return {"dimensions": {"objL1": {"values": LF_VALUES}}}


def row(code):
    return {"item_id": "I1", "dimensions": {"objL1": code}, "period": dict(PERIOD), "expand": {"objL1": "none"}}


def codes(raw):
    return [r["dimensions"]["objL1"] for r in raw["selections"]]


def test_the_words_that_start_another_tables_title_are_masked_when_followed_by_table():
    masked = mask_table_references(normalized(QUERY), (M2_TITLE,))
    assert "m2상품별구성" not in masked
    assert "m2아래" in masked  # the value mention ("M2 아래 만기 2년 미만 ...") stays


def test_a_word_that_merely_starts_a_title_is_not_masked_without_the_word_table():
    query = normalized("정기예금 금리를 비교해줘")
    assert mask_table_references(query, ("정기예금 금리(신규취급액 기준)",)) == query


def test_a_short_overlap_is_not_a_table_reference():
    query = normalized("M2 표에서 보여줘")
    assert mask_table_references(query, ("M2 상품별 구성내역(말잔 원계열)",)) == query


def test_a_value_named_only_as_the_other_tables_title_and_as_a_container_is_dropped():
    raw = {"selections": [row("LE01100"), row("LE01200")]}
    kept, notices = drop_unrequested_rows(raw, QUERY, table(), "k", True, (M2_TITLE,))
    assert codes(kept) == ["LE01200"]
    assert notices[0]["detail"] == "container_only"


def test_without_the_other_tables_title_the_same_value_stays_as_before():
    raw = {"selections": [row("LE01100"), row("LE01200")]}
    kept, notices = drop_unrequested_rows(raw, QUERY, table(), "k", True)
    assert codes(kept) == ["LE01100", "LE01200"] and notices == []


def test_a_value_the_question_asks_for_itself_stays_even_beside_a_table_reference():
    raw = {"selections": [row("LE01100"), row("LE01200")]}
    query = QUERY + " Lf 표의 M2도 같이 보여줘."
    kept, _ = drop_unrequested_rows(raw, query, table(), "k", True, (M2_TITLE,))
    assert codes(kept) == ["LE01100", "LE01200"]
