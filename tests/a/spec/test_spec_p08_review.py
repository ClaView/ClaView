"""Review of plan 08 (Codex, 2026-10-19 r1): the required findings as two-direction specification cases, each with a
case that must change and a case that must stay. Synthetic catalogs of ``test_spec_judgement``; no real table, case
or question."""

from __future__ import annotations

import copy

import pytest
import test_spec_judgement as J
from spec_support import table

from claview.a.interpret.model_verify import _relation_mapping
from claview.a.interpret.pipeline import interpret_question


# a value name that ends in the exclusion suffix of the lexicon (a held-out generated question of 10/19 had one)
CATALOGS = dict(J.CATALOGS, x_suffix=[table("T_EN", "가상에너지표", J.ax("A", J.ACC, [
    "가상에너지구분", ("가상에너지이외", "가상에너지구분"), ("가상에너지", "가상에너지구분")]))])


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return J.SpecLayers(tmp_path_factory, CATALOGS)


def _excluded(a: dict) -> set[str]:
    return {c["value"] for c in a["conditions"] if c["kind"] == "exclude"}


def _indicators(a: dict) -> list:
    return [(r.get("indicator") or {}).get("name") for r in a["interpretation"]["requirements"]]


def _relations(a: dict) -> dict:
    quote = {c["id"]: c["quote"] for c in a["conditions"]}
    out: dict = {}
    for x in a["interpretation"]["relations"]:
        out.setdefault(quote.get(x["condition_id"]), []).append(x["requirement_ids"])
    return out


# ---- 1: a request form made of a display or function word and its ending is a complete request


@pytest.mark.parametrize("request_word", ["비교해", "비교해줘", "보여줘"])
def test_a_display_request_protects_the_names_it_asks_for(layers, request_word):
    a = interpret_question(f"가상A금리와 가상B금리를 {request_word}, 가상C금리는 빼줘. 가상D금리를 보여줘",
                           layers.get("jt_rates"))
    assert {"가상A금리", "가상B금리", "가상D금리"} <= set(_indicators(a))
    assert "가상C금리" not in _indicators(a)


def test_names_without_a_request_before_the_exclusion_stay_excluded(layers):  # must stay
    a = interpret_question("가상A금리와 가상B금리는 빼줘. 가상D금리를 보여줘", layers.get("jt_rates"))
    assert _indicators(a) == ["가상D금리"]


# ---- 2: the existence negation is one predicate, with or without a particle in it


@pytest.mark.parametrize("predicate", ["필요 없어", "필요가 없어", "필요는 없어", "필요 없어요", "필요없어"])
def test_an_existence_negation_excludes_its_clause(layers, predicate):
    a = interpret_question(f"가상상품수지는 {predicate}. 가상서비스수지를 보여줘", layers.get("jt_bop"))
    assert "가상상품수지" not in _indicators(a)
    assert "가상서비스수지" in _indicators(a)


def test_a_positive_need_is_a_request(layers):  # must stay
    a = interpret_question("가상상품수지가 필요해. 가상서비스수지를 보여줘", layers.get("jt_bop"))
    assert not _excluded(a)
    assert _indicators(a) == ["가상상품수지", "가상서비스수지"]


# ---- 3: a middle dot ends a relation phrase as a comma does


@pytest.mark.parametrize("mark", ["·", ", "])
def test_a_list_mark_between_relation_phrases_ends_the_operands(layers, mark):
    a = interpret_question(f"가상A금리 대비 가상B금리 비율{mark}2020년 가상C금리 대비 가상D금리 비율을 보여줘",
                           layers.get("jt_rates"))
    assert _relations(a)["비율"] == [["r1", "r2"], ["r3", "r4"]]
    assert a["judgement"]["certain"] is True


def test_a_middle_dot_list_before_a_relation_word_stays_one_operand_list(layers):  # must stay
    a = interpret_question("가상A금리와 가상B금리의 차이를 보여줘", layers.get("jt_rates"))
    assert [x["requirement_ids"] for x in a["interpretation"]["relations"]] == [["r1", "r2"]]


# ---- 4: the model check reads the operands in their order


def test_model_verify_refuses_reversed_operands(layers):
    a = interpret_question("가상A금리 대비 가상B금리 비율 및 가상C금리 대비 가상D금리 비율을 보여줘", layers.get("jt_rates"))
    conds = {c["id"]: c for c in a["conditions"]}
    raw = a["text"]["raw"]
    assert _relation_mapping(a["interpretation"], conds, raw) == []  # must stay: the settled mapping passes
    bad = copy.deepcopy(a["interpretation"])
    second = [x for x in bad["relations"] if conds[x["condition_id"]]["quote"] == "비율"][1]
    second["requirement_ids"] = list(reversed(second["requirement_ids"]))
    assert _relation_mapping(bad, conds, raw) == [f"V-req:relation_operands:{second['condition_id']}"]


# ---- 5: a sentence mark after a glued request word is a boundary of the glued name


@pytest.mark.parametrize("question", ["가상경상수지를보여줘.", "가상경상수지를보여줘", "가상경상수지를보여줘?"])
def test_a_glued_request_keeps_its_name_before_a_sentence_mark(layers, question):
    a = interpret_question(question, layers.get("jt_bop"))
    assert _indicators(a) == ["가상경상수지"]


def test_a_grammar_word_followed_by_an_unknown_tail_is_still_no_boundary(layers):  # must stay (finding 9)
    a = interpret_question("가상농업규모지수를 보여줘.", layers.get("jt_p_divide"))
    assert _indicators(a) == [None]


# ---- glued request words (user 10/19): one catalog name before a glued request word reads like the spaced form


def _paths(a: dict) -> list:
    return [(r.get("indicator") or {}).get("path") for r in a["interpretation"]["requirements"]]


@pytest.mark.parametrize("layer, glued, spaced", [
    ("jt_bop", "가상경상수지를보여줘", "가상경상수지를 보여줘"),
    ("jt_bop", "가상경상수지보여줘", "가상경상수지 보여줘"),
    ("jt_bop", "가상경상수지를보여줄래요.", "가상경상수지를 보여줄래요."),
    ("jt_bop", "가상경상수지와 가상상품수지를보여줘", "가상경상수지와 가상상품수지를 보여줘"),
    ("jt_inner5", "가상경상수지 중 가상상품수지(평잔)를보여줘", "가상경상수지 중 가상상품수지(평잔)를 보여줘"),
    ("jt_two", "가상경상수지와 가상상품수지를보여줘. 가상상품수지는 2020년부터",
     "가상경상수지와 가상상품수지를 보여줘. 가상상품수지는 2020년부터"),
])
def test_one_name_before_a_glued_request_reads_like_the_spaced_form(layers, layer, glued, spaced):
    a, b = interpret_question(glued, layers.get(layer)), interpret_question(spaced, layers.get(layer))
    assert _indicators(a) == _indicators(b) and _paths(a) == _paths(b)
    assert a["judgement"]["certain"] == b["judgement"]["certain"]
    assert a["judgement"]["attribution"].keys() == b["judgement"]["attribution"].keys()
    assert sorted(a["judgement"]["attribution"].values(), key=str) == sorted(b["judgement"]["attribution"].values(),
                                                                            key=str)


@pytest.mark.parametrize("layer, question", [
    ("jt_bop", "가상경상수지와가상상품수지를보여줘"),  # two names glued together: the glued reading (never exact)
    ("jt_p_divide", "가상농업규모지수를보여줘"),  # the front is no catalog name
])
def test_glued_words_that_are_no_single_name_stay_inexact(layers, layer, question):  # must stay
    a = interpret_question(question, layers.get(layer))
    assert a["judgement"]["certain"] is False
    assert "exact" not in _paths(a)

# ---- glued request words after condition and marker words (user 10/19, part A): read like the spaced form


@pytest.mark.parametrize("layer, spaced", [
    ("jt_axes", "가상차주대출 잔액을 연령별 지역별로 보여줘"),  # X-byeol axis
    ("jt_rates", "가상A금리와 가상B금리의 비율을 신규취급액 기준으로 보여줘"),  # basis + tail noun
    ("jt_bop", "가상경상수지 대비 가상상품수지 비율을 보여줘"),  # relation word
    ("jt_two", "가상경상수지를 가상상품수지 없이 보여줘"),  # exclusion
    ("jt_axes", "가상차주대출 잔액을 연령별, 지역별로 동시에 보여줘"),  # simultaneous marker
    ("jt_bop", "가상경상수지를 가상상품수지로 나눈 값을 보여줘"),  # filler noun
    ("jt_names", "가상 금리를 보여줘"),  # a name written over a space before the glued request
    ("jt_b_axes", "가상차주대출 잔액을 가상연령별 가상지역별로 보여줘"),  # the same, an X-byeol table name
])
def test_a_known_word_before_a_glued_request_reads_like_the_spaced_form(layers, layer, spaced):
    glued = spaced.replace(" 보여줘", "보여줘")
    for q in (glued, glued + "."):
        a, b = interpret_question(q, layers.get(layer)), interpret_question(spaced, layers.get(layer))
        assert _indicators(a) == _indicators(b) and _paths(a) == _paths(b)
        assert [r["breakdown"] for r in a["interpretation"]["requirements"]] == \
            [r["breakdown"] for r in b["interpretation"]["requirements"]]
        assert a["judgement"]["certain"] == b["judgement"]["certain"]


def test_an_unknown_word_before_a_glued_request_stays_unknown(layers):  # must stay
    a = interpret_question("가상경상수지를 속사정보여줘", layers.get("jt_bop"))
    assert a["judgement"]["certain"] is False and "leftover_word" in a["judgement"]["failed_conditions"]

# ---- held-out 9393/9494 and the last variant failures (review of plan 08, part B)


def test_an_exclusion_suffix_inside_a_catalog_name_is_part_of_the_name(layers):
    a = interpret_question("가상에너지구분의 가상에너지이외와 가상에너지를 보여줘", layers.get("x_suffix"))
    assert not _excluded(a)
    assert _indicators(a) == ["가상에너지구분"]
    assert a["interpretation"]["requirements"][0]["breakdown"] == ["가상에너지이외", "가상에너지"]


def test_an_exclusion_suffix_on_a_word_that_is_no_name_still_excludes(layers):  # must stay
    a = interpret_question("가상경상수지 가상상품수지이외를 보여줘", layers.get("jt_bop"))
    assert "가상상품수지" in _excluded(a)


def test_a_family_qualifier_in_the_brackets_of_an_exact_name_is_its_condition(layers):
    a = interpret_question("가상대출금(가상용도별)을 보여줘", layers.get("jt_family"))
    (r,) = a["interpretation"]["requirements"]
    quotes = {c["id"]: c["quote"] for c in a["conditions"]}
    assert "가상용도별" in [quotes[c] for c in r["condition_ids"] + r["shared_condition_ids"]]


def test_a_word_in_the_front_of_a_name_stays_part_of_it(layers):  # must stay
    a = interpret_question("가상예금 원화예금을 평잔으로 보여줘", layers.get("jt_inner"))
    (r,) = a["interpretation"]["requirements"]
    quotes = {c["id"]: c["quote"] for c in a["conditions"]}
    assert "원화" not in [quotes[c] for c in r["condition_ids"] + r["shared_condition_ids"]]


@pytest.mark.parametrize("question", ["가상차주대출 잔액을 연령별, 지역별로 동시에, 가상경상수지는 따로 보여줘",
                                      "가상차주대출 잔액은 연령별, 지역별로 동시에, 가상경상수지를 따로 보여줘",
                                      "가상차주대출 잔액은 연령별, 지역별로 동시에, 가상경상수지는 따로 보여줘"])
def test_object_and_topic_arguments_cut_one_clause_alike(layers, question):
    a = interpret_question(question, layers.get("jt_rw"))
    assert a["judgement"]["certain"] is True
    assert [r["breakdown"] for r in a["interpretation"]["requirements"]] == [["연령별", "지역별"], []]


def test_the_object_of_a_verb_inside_the_clause_is_no_argument_head(layers):  # must stay
    a = interpret_question("가상상품수지를 뺀 가상경상수지는 보여줘", layers.get("jt_bop"))
    assert _indicators(a) == ["가상경상수지"]


def test_a_request_word_written_apart_is_a_request_not_a_particle(layers):
    a = interpret_question("가상경상수지와 가상상품수지를 같이 해", layers.get("jt_bop"))
    b = interpret_question("가상경상수지와 가상상품수지를 같이 보여줘", layers.get("jt_bop"))
    assert _indicators(a) == _indicators(b) and a["judgement"]["certain"] == b["judgement"]["certain"]


def test_a_particle_after_a_bracket_stays_a_particle(layers):  # must stay
    a = interpret_question("가상A금리(잔액 기준)와 가상B금리를 보여줘", layers.get("jt_rates"))
    assert _indicators(a) == ["가상A금리", "가상B금리"]


@pytest.mark.parametrize("question", ["가상제조업의 가상부채비율을 보여주고 가상수출 전망은 빼줘",
                                      "가상제조업의 가상부채비율을 보여주고 가상수출전망은 빼줘"])
def test_a_condition_word_inside_an_excluded_name_is_part_of_it(layers, question):
    a = interpret_question(question, layers.get("jt_target"))
    assert a["judgement"]["certain"] is True and _indicators(a) == ["가상부채비율"]
    (r,) = a["interpretation"]["requirements"]
    kinds = {c["id"]: c["kind"] for c in a["conditions"]}
    assert [kinds[c] for c in r["condition_ids"] + r["shared_condition_ids"] if kinds[c] == "basis"] == []


def test_a_name_with_a_condition_word_inside_is_not_excluded_when_asked(layers):  # must stay
    a = interpret_question("가상제조업의 가상수출 전망을 보여줘", layers.get("jt_target"))
    assert not _excluded(a) and "가상수출전망" in _indicators(a)