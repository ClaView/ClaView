"""통계표가 여럿인 질문에서 HCX 응답을 코드로 보정하는 규칙(기간 정리, "A 아래 B", 질문에 없는 값 제거)을 본다."""

from __future__ import annotations

from claview_b.search import normalized
from claview_b.stages import (
    apply_resolved_period,
    container_marked,
    drop_repeated_rows,
    drop_unrequested_rows,
    expand_selections,
    named_below,
    named_in,
    replace_container_with_named,
    select_named_children,
)

PERIOD = {"frequency": "M", "start": "202401", "end": "202412"}
RESOLVED = {"frequency": "M", "start": "202401", "end": "202412"}


def values(*rows):
    """code -> value like the metadata's classification values; `parents` lists the ancestors."""
    out = {}
    for code, name, parents in rows:
        path = [{"code": c, "name": n} for c, n in parents] + [{"code": code, "name": name}]
        out[code] = {
            "name": name,
            "parent_code": parents[-1][0] if parents else None,
            "path": path,
            "is_leaf": True,
        }
    for v in out.values():
        v["is_leaf"] = not any(w["parent_code"] and w["path"][-2]["code"] == v["path"][-1]["code"] for w in out.values() if len(w["path"]) > 1)
    return out


ROOT = ("ROOT", "산업별대출금")
FOOD = ("F", "식료품 및 음료")
MANU = ("M", "제조업")
TABLE_VALUES = values(
    ("ROOT", "산업별대출금", []),
    ("M", "제조업", [ROOT]),
    ("F", "식료품 및 음료", [ROOT, MANU]),
    ("T", "섬유제품", [ROOT, MANU]),
    ("E", "전기장비", [ROOT, MANU]),
    ("S", "서비스업", [ROOT]),
)
LOAN = ("L", "기업대출")
LOAN_VALUES = values(
    ("L", "기업대출", []),
    ("L1", "대기업대출", [LOAN]),
    ("L2", "중소기업대출", [LOAN]),
    ("H", "가계대출", []),
    ("H1", "소액대출 (500만원 이하)", [("H", "가계대출")]),
)


def table_of(vals):
    return {"dimensions": {"objL1": {"values": vals}}}


def row(code, expand="none", period=PERIOD):
    return {
        "item_id": "I1",
        "dimensions": {"objL1": code},
        "period": dict(period),
        "expand": {"objL1": expand},
    }


def codes(raw):
    return [r["dimensions"]["objL1"] for r in raw["selections"]]


# ---- 기간 -------------------------------------------------------------------


def test_rows_that_differ_only_in_the_models_period_collapse_once_the_period_is_resolved():
    raw = {
        "selections": [
            row("L1", period={**PERIOD, "start": "202401", "end": "202412"}),
            row("L1", period={**PERIOD, "start": "202501", "end": "202512"}),
            row("L1", period={**PERIOD, "start": "202601", "end": "202612"}),
        ]
    }
    kept, notices = drop_repeated_rows(apply_resolved_period(raw, RESOLVED), "k")
    assert codes(kept) == ["L1"]
    assert [n["reason"] for n in notices] == ["repeated_row_dropped"] * 2


def test_without_a_resolved_period_the_models_periods_are_left_alone():
    raw = {"selections": [row("L1"), row("L1", period={**PERIOD, "end": "202312"})]}
    assert apply_resolved_period(raw, None) is raw


# ---- 질문이 말하는 이름 -----------------------------------------------------


def test_a_value_is_named_by_its_name_or_by_what_follows_its_parents_name():
    text = normalized("기업대출 아래 대기업·중소기업 금리를 비교해줘")
    assert named_in(text, LOAN_VALUES, "L1")  # "대기업" is the stem of "대기업대출"
    assert named_in(text, LOAN_VALUES, "L2")
    assert not named_in(text, LOAN_VALUES, "H1")


def test_a_value_is_a_container_only_when_every_mention_is_followed_by_below():
    both = normalized("정기예금 아래 1년 만기와 순수저축성예금 아래 정기예금 전체를 골라줘")
    vals = values(("P", "순수저축성예금", []), ("D", "정기예금", [("P", "순수저축성예금")]))
    assert container_marked(both, vals, "P")
    assert not container_marked(both, vals, "D")  # also asked for itself ("... 아래 정기예금 전체")


def test_a_name_inside_a_longer_value_name_is_not_a_separate_mention():
    # "기업대출 아래 중소기업대출": the "기업대출" in "중소기업대출" is that value, not a second mention
    text = normalized("대출금리 표의 기업대출 아래 중소기업대출을 골라줘")
    assert container_marked(text, LOAN_VALUES, "L")
    assert not container_marked(text, LOAN_VALUES, "L2")


def test_the_container_is_still_asked_for_when_it_is_mentioned_on_its_own_too():
    text = normalized("기업대출과 기업대출 아래 중소기업대출을 골라줘")
    assert not container_marked(text, LOAN_VALUES, "L")


def test_a_container_beside_a_value_whose_name_contains_it_is_dropped():
    raw = {"selections": [row("L2"), row("L")]}
    kept, notices = drop_unrequested_rows(
        raw, "대출금리 표의 기업대출 아래 중소기업대출을 골라줘", table_of(LOAN_VALUES), "k"
    )
    assert codes(kept) == ["L2"]
    assert notices[0]["detail"] == "container_only"


# ---- 상위 항목만 고른 답을 질문이 말한 하위 항목으로 바꾸기 ---------------------

REFINED = ("P", "원유정제처리제품")
OIL_VALUES = values(
    ("P", "원유정제처리제품", []),
    ("N", "나프타", [REFINED]),
    ("G", "휘발유", [REFINED]),
    ("D", "경유", [REFINED]),
    ("K", "등유", [REFINED]),
)


def test_a_container_row_becomes_the_value_the_question_names_below_it():
    raw = {"selections": [row("P")]}
    out, notices = replace_container_with_named(
        raw, "원유정제처리제품 아래 나프타만 골라 비교해줘", table_of(OIL_VALUES), "k"
    )
    assert codes(out) == ["N"]
    assert notices == [
        {
            "table": "k",
            "axis": "objL1",
            "code": "P",
            "replaced_by": "N",
            "reason": "container_replaced_by_named_below",
        }
    ]


def test_every_value_named_below_the_container_gets_a_row():
    raw = {"selections": [row("P")]}
    out, _ = replace_container_with_named(
        raw, "원유정제처리제품 아래 나프타·휘발유·경유 세 품목만 비교해줘", table_of(OIL_VALUES), "k"
    )
    assert sorted(codes(out)) == ["D", "G", "N"]


def test_other_axes_and_the_period_of_the_row_are_kept():
    vals = {"dimensions": {"objL1": {"values": OIL_VALUES}, "objL2": {"values": values(("W", "원화", []))}}}
    one = {
        "item_id": "I1",
        "dimensions": {"objL1": "P", "objL2": "W"},
        "period": dict(PERIOD),
        "expand": {"objL1": "none", "objL2": "none"},
    }
    out, _ = replace_container_with_named(
        {"selections": [one]}, "원유정제처리제품 아래 나프타를 원화로 보여줘", vals, "k"
    )
    assert out["selections"][0]["dimensions"] == {"objL1": "N", "objL2": "W"}
    assert out["selections"][0]["period"] == PERIOD


def test_a_row_that_already_selects_the_named_value_is_not_repeated():
    raw = {"selections": [row("P"), row("N")]}
    out, _ = replace_container_with_named(
        raw, "원유정제처리제품 아래 나프타만 골라 비교해줘", table_of(OIL_VALUES), "k"
    )
    assert codes(out) == ["N"]


def test_a_container_is_kept_when_the_question_names_nothing_below_it():
    raw = {"selections": [row("P")]}
    out, notices = replace_container_with_named(
        raw, "원유정제처리제품 아래 품목을 보여줘", table_of(OIL_VALUES), "k"
    )
    assert out is raw and notices == []


def test_a_container_the_question_asks_for_itself_is_kept():
    raw = {"selections": [row("P")]}
    out, notices = replace_container_with_named(
        raw, "원유정제처리제품과 원유정제처리제품 아래 나프타를 비교해줘", table_of(OIL_VALUES), "k"
    )
    assert out is raw and notices == []


def test_rows_that_expand_and_single_table_questions_are_left_alone():
    query = "원유정제처리제품 아래 나프타만 골라줘"
    expanding = {"selections": [row("P", "children")]}
    assert replace_container_with_named(expanding, query, table_of(OIL_VALUES), "k")[1] == []
    plain = {"selections": [row("P")]}
    out, notices = replace_container_with_named(plain, query, table_of(OIL_VALUES), "k", multi_table=False)
    assert out is plain and notices == []


# ---- 질문에 없는 값 제거 ----------------------------------------------------


def test_values_the_question_does_not_say_are_dropped_beside_the_ones_it_does():
    raw = {"selections": [row("L1"), row("L2"), row("H1")]}
    kept, notices = drop_unrequested_rows(raw, "기업대출 아래 대기업·중소기업 금리", table_of(LOAN_VALUES), "k")
    assert codes(kept) == ["L1", "L2"]
    assert [n["code"] for n in notices] == ["H1"]


def test_a_container_is_dropped_when_the_question_only_names_what_is_below_it():
    vals = values(("P", "순수저축성예금", []), ("D", "정기예금", [("P", "순수저축성예금")]))
    raw = {"selections": [row("P"), row("D")]}
    kept, notices = drop_unrequested_rows(raw, "순수저축성예금 아래 정기예금을 골라줘", table_of(vals), "k")
    assert codes(kept) == ["D"]
    assert notices[0]["detail"] == "container_only"


def test_nothing_is_dropped_when_no_value_is_named():
    raw = {"selections": [row("L1"), row("H1")]}
    kept, notices = drop_unrequested_rows(raw, "대출금리를 비교해줘", table_of(LOAN_VALUES), "k")
    assert kept is raw and notices == []


def test_single_table_questions_are_left_alone():
    raw = {"selections": [row("L1"), row("H1")]}
    kept, notices = drop_unrequested_rows(
        raw, "기업대출 아래 대기업대출 금리", table_of(LOAN_VALUES), "k", multi_table=False
    )
    assert kept is raw and notices == []


def test_rows_that_expand_a_value_are_not_touched():
    raw = {"selections": [row("L", "children"), row("L1"), row("H1")]}
    kept, _ = drop_unrequested_rows(raw, "기업대출 아래 대기업대출", table_of(LOAN_VALUES), "k")
    assert "L" in codes(kept)


# ---- "A 아래 B" -------------------------------------------------------------


def test_named_values_below_an_expanded_container_replace_the_expansion():
    text = normalized("각 표의 제조업 아래 섬유제품만 골라 비교해줘")
    assert named_below(text, TABLE_VALUES, "M") == ["T"]


def test_several_named_values_below_a_higher_level_are_found_through_their_container():
    text = normalized("제조업 아래 식료품 및 음료·섬유제품·전기장비 세 업종만 비교해줘")
    assert named_below(text, TABLE_VALUES, "ROOT") == ["F", "T", "E"]


def test_one_named_value_needs_a_container_in_the_question():
    text = normalized("산업별대출금 중 섬유제품을 보여줘")
    assert named_below(text, TABLE_VALUES, "ROOT") == []


def test_select_named_children_uses_the_value_named_below_the_container():
    raw = {"selections": [row("M", "children")]}
    out, notices = select_named_children(
        raw, "제조업 아래 섬유제품만 골라줘", table_of(TABLE_VALUES), "k"
    )
    assert codes(out) == ["T"] and out["selections"][0]["expand"] == {"objL1": "none"}
    assert notices[0]["reason"] == "query_names_values_below"


def test_the_old_two_named_children_rule_is_unchanged_without_below():
    raw = {"selections": [row("M", "children")]}
    out, notices = select_named_children(raw, "제조업 섬유제품 보여줘", table_of(TABLE_VALUES), "k")
    assert out["selections"] == raw["selections"] and notices == []


# ---- 확장과 명시 선택의 중복 ------------------------------------------------


def test_a_series_that_is_both_expanded_and_selected_is_one_series():
    table = {"dimensions": {"objL1": {"values": TABLE_VALUES}}}
    raw = {"selections": [row("M", "children"), row("T")]}
    expanded = expand_selections(raw, table)
    assert sorted(codes(expanded)) == ["E", "F", "T"]
