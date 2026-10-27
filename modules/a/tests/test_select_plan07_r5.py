"""Plan 07 r5 Q3 score items and Q6/Q8 marks on made-up table views (no catalog file, no network, no model)."""

from __future__ import annotations

from types import SimpleNamespace

from claview_a.select.cover import (AMBIGUOUS_WORD, TITLE_WORD, UNASKED_AXIS, period_check, score_items,
                                     title_words)


def _view(title: str, dims=(), roles=None, items=()):
    return SimpleNamespace(title=title, title_ns="".join(title.split()), dims=list(dims), axis_roles=roles or {},
                           items=[SimpleNamespace(obj_id=o, name_nospace=n) for o, n in items])


def _words(text: str, own=(), ambiguous=()):
    return {"text": "".join(text.split()), "own": set(own), "ambiguous": set(ambiguous)}


def test_title_words_skip_years_and_short_pieces():
    assert title_words("가상M2 상품별 구성(말잔, 2009~)") == ["가상M2", "상품별", "구성", "말잔"]


def test_a_table_name_word_the_question_writes_is_a_plus_and_an_own_condition_word_is_not():
    v = _view("가상M2 상품별 구성(말잔, 원계열)")
    items = score_items(v, _words("가상M2 상품별 말잔을 보여줘", own={"말잔"}), [])
    assert {(i["item"], i["value"], i["reason"]) for i in items} == {("title_word", TITLE_WORD, "가상M2"),
                                                                      ("title_word", TITLE_WORD, "상품별")}


def test_a_table_name_word_inside_another_written_word_is_no_plus():
    v = _view("가상자산 지표(가상분류)")
    words = {**_words("가상수정가상자산이익률 가상분류로"), "starts": ("가상수정가상자산이익률", "가상분류로")}
    assert [i["reason"] for i in score_items(v, words, [])] == ["가상분류"]


def test_an_ambiguous_condition_word_is_a_weak_plus():
    v = _view("가상생산(계절조정, 실질, 분기)")
    items = score_items(v, _words("가상생산을 계절조정한 실질로", ambiguous={"계절조정", "실질"}), [])
    assert sorted((i["reason"], i["value"]) for i in items) == [
        ("가상생산", TITLE_WORD), ("계절조정 (ambiguous condition)", AMBIGUOUS_WORD),
        ("실질 (ambiguous condition)", AMBIGUOUS_WORD)]


def test_a_basis_axis_the_question_names_nowhere_is_a_minus_only_against_candidates_without_it():
    quartile = _view("가상분위표", dims=[("Q", "가상분위 구분코드별")], roles={"Q": "basis"}, items=[("Q", "1가상분위")])
    plain = _view("가상손익표", dims=[("A", "계정항목별")], roles={"A": "indicator"})
    got = score_items(quartile, _words("가상이익률을 보여줘"), [plain])
    assert [(i["item"], i["value"]) for i in got] == [("unasked_axis", -UNASKED_AXIS)]
    assert score_items(quartile, _words("가상이익률을 보여줘"), [quartile]) == []  # every candidate has it
    assert score_items(quartile, _words("가상이익률을 1가상분위로 보여줘"), [plain]) == []  # a value is named


def test_period_check_marks_an_unknown_or_uncovered_period():
    assert period_check({"flags": ["period_unknown"], "unproven": []})
    assert period_check({"flags": [], "unproven": [{"check": "period"}]})
    assert not period_check({"flags": [], "unproven": [{"check": "basis"}]})
    assert not period_check(None)
