"""같은 이름의 값이 여러 가지(branch) 아래 있는 표에서 HCX가 엉뚱한 가지를 고르는 오선택을 코드로 보정하는 규칙을 본다."""

from __future__ import annotations

from claview_b.search import normalized
from claview_b.stages import (
    container_marked,
    drop_farther_same_name,
    drop_unnamed_expansions,
    named_below,
    named_in,
    select_named_children,
)

PERIOD = {"frequency": "M", "start": "202401", "end": "202412"}


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
        v["is_leaf"] = not any(
            len(w["path"]) > 1 and w["path"][-2]["code"] == v["path"][-1]["code"] for w in out.values()
        )
    return out


def table_of(vals):
    return {"dimensions": {"objL1": {"values": vals}}}


def row(code, expand="none"):
    return {
        "item_id": "I1",
        "dimensions": {"objL1": code},
        "period": dict(PERIOD),
        "expand": {"objL1": expand},
    }


def codes(raw):
    return [r["dimensions"]["objL1"] for r in raw["selections"]]


TOTAL = ("T", "총대출금")
FUND = ("F", "금융자금")
FISCAL = ("G", "재정자금")
LOANS = values(
    ("T", "총대출금", []),
    ("T1", "시설자금", [TOTAL]),
    ("T2", "운전자금", [TOTAL]),
    ("F", "금융자금", [TOTAL]),
    ("F1", "시설자금", [TOTAL, FUND]),
    ("F2", "운전자금", [TOTAL, FUND]),
    ("F3", "무역금융", [TOTAL, FUND]),
    ("G", "재정자금", [TOTAL]),
    ("G1", "시설자금", [TOTAL, FISCAL]),
)
ROOT = ("R", "총지수")
MATERIAL = ("M", "원재료")
FINAL = ("L", "최종재")
INDEX = values(
    ("R", "총지수", []),
    ("M", "원재료", [ROOT]),
    ("M1", "원재료국내출하", [ROOT, MATERIAL]),
    ("M2", "원재료수입", [ROOT, MATERIAL]),
    ("M3", "원재료제조용", [ROOT, MATERIAL]),
    ("M31", "원재료제조용(국내출하)", [ROOT, MATERIAL, ("M3", "원재료제조용")]),
    ("L", "최종재", [ROOT]),
    ("L1", "최종재국내출하", [ROOT, FINAL]),
    ("L2", "최종재수입", [ROOT, FINAL]),
)


# ---- 질문이 말한 값 -----------------------------------------------------------


def test_what_follows_an_ancestors_name_counts_only_when_the_question_names_the_ancestor():
    text = normalized("원재료 아래 국내출하·수입을 보여줘")
    assert named_in(text, INDEX, "M1") and named_in(text, INDEX, "M2")
    assert not named_in(text, INDEX, "L1")  # "최종재국내출하": 최종재 is not in the question
    assert not named_in(text, INDEX, "L2")


def test_a_name_followed_by_table_points_at_the_table_not_at_the_value():
    text = normalized("순대외채권 표에서는 순대외채권 아래 단기·장기만 골라줘")
    vals = values(("N", "순대외채권", []), ("S", "단기", [("N", "순대외채권")]), ("L", "장기", [("N", "순대외채권")]))
    assert container_marked(text, vals, "N")


# ---- 같은 이름은 가장 가까운 것 -------------------------------------------------


def test_the_same_name_below_a_container_means_the_nearest_one():
    text = normalized("총대출금 아래 시설자금·운전자금 잔액을 보여줘")
    assert sorted(named_below(text, LOANS, "T")) == ["T1", "T2"]


def test_a_deeper_same_name_is_dropped_when_it_sits_below_a_value_the_question_does_not_name():
    raw = {"selections": [row("T1"), row("T2"), row("F1"), row("F2"), row("G1")]}
    out, notices = drop_farther_same_name(
        raw, "총대출금 아래 시설자금·운전자금 잔액을 보여줘", table_of(LOANS), "k"
    )
    assert codes(out) == ["T1", "T2"]
    assert {n["code"] for n in notices} == {"F1", "F2", "G1"}
    assert {n["reason"] for n in notices} == {"farther_same_name_dropped"}


def test_deeper_same_names_stay_when_the_question_names_what_they_sit_below():
    raw = {"selections": [row("T1"), row("F1"), row("G1")]}
    out, notices = drop_farther_same_name(
        raw, "총대출금과 금융자금, 재정자금의 시설자금을 비교해줘", table_of(LOANS), "k"
    )
    assert codes(out) == ["T1", "F1", "G1"] and notices == []


def test_a_row_alone_is_never_dropped_and_single_table_questions_are_left_alone():
    query = "총대출금 아래 시설자금을 보여줘"
    lone = {"selections": [row("F1")]}
    assert drop_farther_same_name(lone, query, table_of(LOANS), "k")[1] == []
    both = {"selections": [row("T1"), row("F1")]}
    out, notices = drop_farther_same_name(both, query, table_of(LOANS), "k", multi_table=False)
    assert out is both and notices == []


# ---- 질문이 말하지 않는 상위 항목의 확장 ------------------------------------------


def test_an_expansion_of_a_value_the_question_never_names_is_dropped_beside_other_rows():
    raw = {"selections": [row("M", "children"), row("L", "children"), row("L2")]}
    out, notices = drop_unnamed_expansions(
        raw, "원재료 아래 국내출하·수입을 보여줘", table_of(INDEX), "k"
    )
    assert codes(out) == ["M", "L2"]
    assert [n["code"] for n in notices] == ["L"]
    assert notices[0]["reason"] == "unnamed_expansion_dropped"


def test_an_expansion_with_values_named_below_it_is_left_to_the_children_rule():
    raw = {"selections": [row("F", "children"), row("T1")]}
    out, notices = drop_unnamed_expansions(
        raw, "총대출금 아래 시설자금·운전자금을 보여줘", table_of(LOANS), "k"
    )
    assert out is raw and notices == []


def test_the_only_row_is_kept_and_single_table_questions_are_left_alone():
    only = {"selections": [row("L", "children")]}
    assert drop_unnamed_expansions(only, "원재료 아래 수입", table_of(INDEX), "k")[1] == []
    two = {"selections": [row("M", "children"), row("L", "children")]}
    out, notices = drop_unnamed_expansions(two, "원재료 아래 수입", table_of(INDEX), "k", multi_table=False)
    assert out is two and notices == []


# ---- 규칙들의 결합: 같은 이름이 여러 가지 아래 있는 표 -----------------------------


def test_the_children_rule_picks_the_nearest_named_children_of_the_container():
    raw = {"selections": [row("T", "children")]}
    out, notices = select_named_children(
        raw, "총대출금 아래 시설자금·운전자금 잔액을 보여줘", table_of(LOANS), "k"
    )
    assert codes(out) == ["T1", "T2"]
    assert notices[0]["reason"] == "query_names_children"
