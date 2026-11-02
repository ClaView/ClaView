"""Plan 55 (user 2026-10-31 12:36 UTC): whose negation a word is, before a basis value is read as excluded.

"N뿐/N만 아니라" is not a negation of N; "N을 상품별로 표가 아니라" negates 표, not N; "N 표가 아니라" negates the
noun phrase N is in. A negated exclusion (D1, user 2026-10-31 12:47 UTC): the prohibitive "N은 제외하지 말고" requires
N (a 원계열-only table conflicts, a table without a seasonal value is unverified); "N은 빼지 말고" is the same
construction (an exclusion stem of the lexicon, -지, the prohibitive 말-), so it is read the same; the permissive "N은
제외하지 않아도 돼" states nothing; any other ("N을 제외하지 않고") is not read. A form whose scope is not read stays
unresolved: never turned from excluded to required or back by a guess. The reading
function, then the straight graph on the real catalog with a fake model (no network): the question and a splitter
quote, the model's ask on the axis (model_ask) and the options of an ask on another axis (ask_option).
"""

from __future__ import annotations

import pytest
import test_stated_basis_18 as T18
from test_explicit_conditions_53 import FREQUENCY, M2, PERIOD, STOCK, codes, dated, go, m2, output, ref

from claview_a.search.basis import polarity


def read(text, axis="seasonal"):
    got = polarity(text, axis)
    return sorted(got["required"]), sorted(got["excluded"]), [(u["value"], u["why"]) for u in got["unresolved"]]


# ---------------------------------------------------------------- reading


@pytest.mark.parametrize("text, required, excluded", [
    # a negation of another word: 표 is negated, the value is asked for
    ("원계열을 상품별로 표가 아니라 선그래프로 보여줘", ["원계열"], []),
    ("계절조정을 표가 아니라 그래프로", ["계절조정"], []),
    ("표 말고 그래프로, 원계열로 보여줘", ["원계열"], []),
    # not only the value
    ("원계열뿐 아니라 계절조정도 보여줘", ["계절조정", "원계열"], []),
    ("계절조정뿐 아니라 원계열도", ["계절조정", "원계열"], []),
    ("원계열만이 아니라 계절조정도", ["계절조정", "원계열"], []),
    ("원계열뿐만 아니라 계절조정도", ["계절조정", "원계열"], []),
    # the value inside the negated noun phrase
    ("계절조정 표가 아니라 원계열 표로 보여줘", ["원계열"], ["계절조정"]),
    ("계절조정의 표가 아니라 원계열로", ["원계열"], ["계절조정"]),
    ("계절조정한 표가 아니라 원계열 표", ["원계열"], ["계절조정"]),
    # '말고' is a lexicon negation word: the value right before it is excluded (as before)
    ("원계열 말고 계절조정으로", ["계절조정"], ["원계열"]),
    # '안 된' is a basis negation word (it was read as a requirement before)
    ("계절조정 안 된 자료", [], ["계절조정"]),
    ("계절조정 안된 값", [], ["계절조정"]),
])
def test_whose_negation_it_is_is_read_before_the_value(text, required, excluded):
    assert read(text) == (required, excluded, [])


@pytest.mark.parametrize("text", ["계절조정은 제외하지 말고 보여줘", "계절조정은 빼지 말고"])
def test_a_prohibited_exclusion_is_a_requirement(text):
    """D1: required, and nothing about 원계열 (no opposite value is inferred)."""
    assert read(text) == (["계절조정"], [], [])


@pytest.mark.parametrize("text", ["계절조정을 제외하지 않아도 돼", "계절조정은 빼지 않아도 된다", "계절조정은 제외하지 않아도 괜찮아"])
def test_a_permitted_value_is_neither_required_nor_excluded(text):
    assert read(text) == ([], [], [])


@pytest.mark.parametrize("text", ["계절조정을 제외하지 않고", "계절조정을 빼지 않은 값"])
def test_any_other_negated_exclusion_is_not_read(text):
    """Before plan 55 '계절조정을 제외하지 않고' was read as an exclusion."""
    assert read(text) == ([], [], [("계절조정", "double_negation_unread")])


@pytest.mark.parametrize("text, why", [
    # a verb negation: its scope is not read (as before)
    ("계절조정 자료는 보고 싶지 않아", "negation_scope_unread"),
    ("계절조정 값은 보고 싶지 않은", "negation_scope_unread"),
    ("계절조정은 보고 싶지 않아", "negation_scope_unread"),
    # the value has its own particle and a word that is not '-로' stands before the negated noun: not read
    ("원계열을 상품별 표가 아니라 그래프로", "negation_scope_unread"),
    ("계절조정을 적용한 값이 아니라 원계열", "negation_scope_unread"),
    ("원계열을 그래프로 그리는 게 아니라", "negation_scope_unread"),
    # a negation after the negated exclusion
    ("계절조정은 제외하지 말고 않은", "double_negation_unread"),
])
def test_a_scope_that_is_not_read_stays_unresolved(text, why):
    required, excluded, unresolved = read(text)
    assert [w for v, w in unresolved if v in {"원계열", "계절조정"} and v not in required] == [why]
    assert not (set(excluded) & {v for v, _ in unresolved})


@pytest.mark.parametrize("text, required, excluded", [
    ("계절조정은 하지 않은 값", [], ["계절조정"]), ("계절조정이 아니라 원계열", ["원계열"], ["계절조정"]),
    ("명목이 아니라 실질", ["실질"], ["명목"]), ("원계열", ["원계열"], [])])
def test_the_plan_53_readings_are_kept(text, required, excluded):
    axis = "price" if "명목" in text else "seasonal"
    assert read(text, axis) == (required, excluded, [])


# ---------------------------------------------------------------- through the graph


B = "월말 잔액 기준 광의통화"
OTHER_WORD = (f"{B} 원계열을 상품별로 표가 아니라 선그래프로 보여줘", "원계열을 상품별로 표가 아니라 선그래프로", "원계열")
DOUBLE = (f"{B}를 상품별로 보여줘. 계절조정은 제외하지 말고.", "계절조정은 제외하지 말고", "계절조정")
IN_NOUN = (f"{B}를 상품별로 계절조정 표가 아니라 원계열 표로 보여줘", "계절조정 표가 아니라 원계열 표로", "원계열")
OTHER = {"원계열": "계절조정", "계절조정": "원계열"}


@pytest.mark.parametrize("question, quote, stated", [OTHER_WORD, DOUBLE, IN_NOUN])
@pytest.mark.parametrize("with_splitter", [False, True])
def test_the_stated_value_passes_and_the_other_conflicts(layer, question, quote, stated, with_splitter):
    splitters = [STOCK] + ([{"quote": quote, "axis": "seasonal", "for": ["s1"]}] if with_splitter else [])
    _, d = m2(layer, stated, splitters, question=question)
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []
    assert d["s1"]["basis"]["seasonal"]["source"] == ("splitter" if with_splitter else "question_one_statistic")
    _, d = m2(layer, OTHER[stated], splitters, question=question)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]


CA_Q = "2024년 월별 국제수지 경상수지를 보여줘. {}."


@pytest.mark.parametrize("quote", ["계절조정은 제외하지 말고", "계절조정은 빼지 말고"])
def test_a_prohibited_exclusion_is_checked_as_a_requirement(layer, quote):
    """Met by a 계절조정 table; a table with no seasonal value on its title is unverified (a warning, as for any
    stated value); a 원계열 table conflicts (``test_the_stated_value_passes_and_the_other_conflicts``)."""
    def single(name, seasonal):
        def build(cards):
            r, _, card = T18.ref(cards, name, layer, seasonal=seasonal)
            return T18.output([("s1", "경상수지")], [{"quote": quote, "axis": "seasonal", "for": ["s1"]}],
                              [("s1", r, card["name"])])
        return T18.go(CA_Q.format(quote), layer, build)[1]["s1"]
    d = single("경상수지", "계절조정")
    assert d["outcome"] == "selected" and codes(d) == [] and d["stated_basis_checks"][0]["result"] == "match"
    d = single("국제수지", None)
    assert d["outcome"] == "selected" and codes(d) == []
    assert d["stated_basis_checks"][0]["result"] == "table_value_unknown"
    assert [(w["code"], w["stated"]) for w in d["warnings"]] == [("stated_basis_unverified", ["계절조정"])]


@pytest.mark.parametrize("name, seasonal", [("경상수지", "계절조정"), ("국제수지", None)])
def test_a_permitted_value_states_nothing_through_the_graph(layer, name, seasonal):
    quote = "계절조정은 제외하지 않아도 돼"

    def build(cards):
        r, _, card = T18.ref(cards, name, layer, seasonal=seasonal)
        return T18.output([("s1", "경상수지")], [{"quote": quote, "axis": "seasonal", "for": ["s1"]}],
                          [("s1", r, card["name"])])
    d = T18.go(CA_Q.format(quote), layer, build)[1]["s1"]
    assert d["outcome"] == "selected" and codes(d) == [] and d["warnings"] == []
    assert not d.get("stated_basis_checks")


def test_any_other_negated_exclusion_stops_the_statistic(layer):
    quote = "계절조정을 제외하지 않고"

    def build(cards):
        r, _, card = T18.ref(cards, "경상수지", layer, seasonal="계절조정")
        return T18.output([("s1", "경상수지")], [{"quote": quote, "axis": "seasonal", "for": ["s1"]}],
                          [("s1", r, card["name"])])
    d = T18.go(CA_Q.format(quote), layer, build)[1]["s1"]
    assert d["outcome"] == "failed" and codes(d) == ["basis_polarity_unresolved"]


def test_a_value_negated_by_an_doen_is_excluded(layer):
    """'계절조정 안 된 자료': the 계절조정 table conflicts (it was read as the stated value before)."""
    q = f"{B}를 상품별로 계절조정 안 된 자료로 보여줘"
    _, d = m2(layer, "계절조정", [STOCK], question=q)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
    _, d = m2(layer, "원계열", [STOCK], question=q)
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []


def test_two_stated_values_of_one_statistic_stay_an_attribution_question(layer):
    """'원계열뿐 아니라 계절조정도': both are stated; one statistic with one table stops as before (not a polarity)."""
    _, d = m2(layer, "원계열", [STOCK], question=f"{B}를 상품별로 원계열뿐 아니라 계절조정도 보여줘")
    assert codes(d["s1"]) == ["basis_attribution_unresolved"]


@pytest.mark.parametrize("question, quote, stated", [OTHER_WORD, DOUBLE, IN_NOUN])
def test_a_model_ask_on_the_axis_becomes_the_stated_option(layer, question, quote, stated):
    def build(cards):
        r1, t1, _ = ref(cards, M2, layer, seasonal="원계열", stock="말잔")
        r2, t2, _ = ref(cards, M2, layer, seasonal="계절조정", stock="말잔")
        tables.update({"원계열": t1, "계절조정": t2})
        ask = {"for": "s1", "axis": "seasonal", "options": [{"value": "원계열", "table": r1},
                                                               {"value": "계절조정", "table": r2}]}
        return output([("s1", "광의통화")], [STOCK, {"quote": quote, "axis": "seasonal", "for": ["s1"]}], [],
                      asks=[ask], periods=[PERIOD], frequencies=[FREQUENCY])
    tables = {}
    _, d = go(dated(question), layer, build)  # a period and frequency quoted (plan 56 order 5, see ``dated``)
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []
    assert [c["code"] for c in d["s1"]["corrections"]][:1] == ["unneeded_ask_to_choice"]
    assert [t["table_id"] for t in d["s1"]["final_tables"]] == [tables[stated]]


@pytest.mark.parametrize("question, quote, stated", [OTHER_WORD, DOUBLE, IN_NOUN])
@pytest.mark.parametrize("seasonal", ["원계열", "계절조정"])
def test_the_options_of_an_ask_on_another_axis_keep_the_stated_value(layer, question, quote, stated, seasonal):
    q = question.replace(B, "광의통화")

    def build(cards):
        r1, _, _ = ref(cards, M2, layer, seasonal=seasonal, stock="말잔")
        r2, _, _ = ref(cards, M2, layer, seasonal=seasonal, stock="평잔")
        ask = {"for": "s1", "axis": "stock", "options": [{"value": "말잔", "table": r1}, {"value": "평잔", "table": r2}]}
        return output([("s1", "광의통화")], [{"quote": quote, "axis": "seasonal", "for": ["s1"]}], [], asks=[ask],
                      periods=[PERIOD], frequencies=[FREQUENCY])
    # plan 56 order 8 (input only, the 17:53 rule): a period and frequency quoted, else the period is asked first
    _, d = go(dated(q), layer, build)
    if seasonal == stated:
        assert d["s1"]["outcome"] == "ask" and codes(d["s1"]) == []
    else:
        assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
        assert d["s1"]["reasons"][0]["detail"]["stage"] == "ask_option"
