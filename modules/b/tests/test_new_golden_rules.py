"""신규 골든셋(v5) 실제 HCX 실행에서 드러난 B 보정 규칙을 값 단위로 본다.

규칙: "구분해"는 분해 요청이 아님, 레이아웃 표현("그래프를 따로 배치")은 분해 요청이 아님,
`named_shorter_value`(질문이 말한 짧은 값으로 교체), `replace_unnamed_container`(말하지 않은 상위 값 행을
말한 하위 값 행으로 교체), `drop_container_beside_named_children`("P 중 C·D"의 P 행 제거),
단일표 질문의 컨테이너 전용 행 제거.
"""

from __future__ import annotations

import pytest

from claview_b.stages import (
    asks_for_breakdown,
    asks_to_split_by,
    drop_container_beside_named_children,
    drop_unrequested_rows,
    named_shorter_value,
    replace_unnamed_container,
    split_words,
)
from test_multi_table_guards import TABLE_VALUES, codes, row, table_of, values

# ---- 말뜻이 다른 "구분해", 레이아웃 표현 ----------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "대기업과 중소기업을 구분해서 보여줘",  # 구분해 != 분해
        "그래프를 따로 배치해줘",
        "지표별로 그래프를 나눠줘",
        "기업규모별로 그래프를 나눠줘",
        "별도로 표시해줘",
    ],
)
def test_wording_about_layout_or_distinguishing_is_not_a_breakdown_request(query):
    assert not asks_for_breakdown(query)
    assert not asks_to_split_by(query)
    assert split_words(query) == []


@pytest.mark.parametrize(
    "query", ["대·중소기업 부채비율을 분해해서 보여줘", "업종별로 나눠서 보여줘", "항목별로 분해해줘"]
)
def test_real_breakdown_wording_is_still_a_breakdown_request(query):
    assert asks_for_breakdown(query)


# ---- named_shorter_value --------------------------------------------------------

SHORT = values(
    ("A", "제조업", []),
    ("A1", "제조업 대기업", [("A", "제조업")]),
    ("A2", "제조업 중소기업", [("A", "제조업")]),
    ("B", "서비스업", []),
)


def test_chosen_value_that_adds_words_the_question_never_says_is_replaced_by_the_named_one():
    assert named_shorter_value("2024년 제조업 대출 추이", SHORT, "A1") == "A"


@pytest.mark.parametrize(
    ("query", "chosen"),
    [
        ("2024년 제조업 대기업 대출 추이", "A1"),  # the longer name is said in full
        ("2024년 제조업 대출 추이", "A"),  # already the named value
        ("2024년 제조업의 대기업 대출 추이", "A1"),  # the added word is in the question
        ("2024년 서비스업 대출 추이", "B"),
    ],
)
def test_named_shorter_value_leaves_other_choices_alone(query, chosen):
    assert named_shorter_value(query, SHORT, chosen) is None


def test_added_part_that_the_question_mentions_elsewhere_keeps_the_longer_value():
    # 신용카드(물품및용역구매) must not become 신용카드 when the question says 물품
    vals = values(
        ("C", "신용카드", []),
        ("C1", "신용카드(물품및용역구매)", [("C", "신용카드")]),
    )
    assert named_shorter_value("신용카드로 물품 구매한 금액", vals, "C1") is None


def test_exclusion_values_and_longer_named_values_are_not_replaced():
    vals = values(
        ("F", "신선식품", []),
        ("F2", "신선식품이외", []),
        ("R", "원재료", []),
        ("R1", "원재료국내출하", [("R", "원재료")]),
        ("R2", "원재료수입", [("R", "원재료")]),
    )
    assert named_shorter_value("신선식품을 뺀 물가", vals, "F2") is None  # rule B decides "X이외"
    assert named_shorter_value("원재료수입 물가 추이", vals, "R1") is None  # a longer value is named


# ---- replace_unnamed_container ----------------------------------------------------

INVEST = ("L", "총투자")
LOANS = values(
    ("L", "총투자", []),
    ("L1", "총자본형성", [INVEST]),
    ("L2", "재고증감", [INVEST]),
)


def test_plain_row_on_a_value_the_question_does_not_name_becomes_the_named_value_below_it():
    raw = {"selections": [row("L")]}
    fixed, notices = replace_unnamed_container(raw, "2024년 총자본형성 추이", table_of(LOANS), "T")
    assert codes(fixed) == ["L1"]
    assert [n["reason"] for n in notices] == ["unnamed_container_replaced_by_named_below"]


def test_named_container_and_rows_without_a_named_child_are_kept():
    raw = {"selections": [row("L")]}
    for query in ("2024년 총투자 추이", "2024년 투자 추이"):
        fixed, notices = replace_unnamed_container(raw, query, table_of(LOANS), "T")
        assert codes(fixed) == ["L"] and notices == []


def test_expanding_rows_are_not_replaced():
    raw = {"selections": [row("L", expand="children")]}
    fixed, notices = replace_unnamed_container(raw, "2024년 총자본형성 추이", table_of(LOANS), "T")
    assert codes(fixed) == ["L"] and notices == []


# ---- "P 중 C·D" --------------------------------------------------------------------


def test_container_row_beside_its_named_children_after_jung_is_dropped():
    raw = {"selections": [row("M"), row("F"), row("T")]}
    query = "제조업 중 식료품 및 음료와 섬유제품을 월별로 보여줘"
    fixed, notices = drop_container_beside_named_children(raw, query, table_of(TABLE_VALUES), "T")
    assert codes(fixed) == ["F", "T"]
    assert [n["detail"] for n in notices] == ["container_beside_named_children"]


@pytest.mark.parametrize(
    ("query", "selected"),
    [
        ("제조업 식료품 및 음료와 섬유제품을 보여줘", ["M", "F", "T"]),  # no "중"
        ("제조업 중소기업 대출을 보여줘", ["M", "F", "T"]),  # "중소기업" is not "중"
        ("제조업 중 식료품 및 음료를 보여줘", ["M", "S"]),  # no named child is selected
    ],
)
def test_container_row_is_kept_without_the_jung_wording_or_named_children(query, selected):
    raw = {"selections": [row(c) for c in selected]}
    fixed, notices = drop_container_beside_named_children(raw, query, table_of(TABLE_VALUES), "T")
    assert codes(fixed) == selected and notices == []


# ---- single-table container_only ------------------------------------------------------


def test_single_table_question_drops_a_container_used_only_as_container():
    raw = {"selections": [row("M"), row("F")]}
    fixed, _ = drop_unrequested_rows(
        raw, "제조업 아래 식료품 및 음료 추이", table_of(TABLE_VALUES), "T", multi_table=False
    )
    assert codes(fixed) == ["F"]


def test_single_table_question_keeps_values_it_does_not_name():
    raw = {"selections": [row("F"), row("S")]}
    fixed, _ = drop_unrequested_rows(
        raw, "식료품 및 음료 추이", table_of(TABLE_VALUES), "T", multi_table=False
    )
    assert codes(fixed) == ["F", "S"]  # synonyms are allowed in a one-table question

