"""Plan 55 follow-up (user 2026-10-31 12:36 and 12:47 UTC): the scope of a negation near a basis value, narrowed.

(a) a value in the negated noun phrase ("N 표가 아니라", "N표가 아니라") is excluded only when the word right after
the negation is another value of the axis ("... 아니라 M 표로"); with a display form or another word there ("N 표가
아니라 그래프로") the scope is not read; a word closed by a particle before the negated noun closes N's phrase.
(b) N's phrase may hold up to two words after N ("N 계열을", "실질 GDP를", "신규취급액 기준 대출금리를") before the
negated other word. (c) "N 말고도 M도" is additive. (d) "N은 제외하지는 말고" / "빼지도 말고" is the prohibitive: N
required; no other double negation is read. (e) an exclusion negated again in its clause ("제외하고 싶지 않아") is
not read. (f) the lexicon's exclusions ("N은 필요 없어", "N 없이", "N은 빼줘") exclude N; "N을 제외할 필요는 없어" is
not read (neither an exclusion nor a permission). (g) a value joined only by a coordinator to a next value that is
not required ("원화나 달러화 말고") is not read. A form whose scope is not read stays unresolved: never turned from
excluded to required or back. The reading function, then the straight graph with a fake model for (a) and (f).

Review of 55b (h-o): (h) the other word W of (b) is a word of its own: not a phrase closed by a particle that is also a
modifier ending ("N 보는 게 아니라"), a modifier before 대신 ("N을 보는 대신"), a clause nominalized on its verb ("N
계열을 고른게 아니라"), a verb in -지 right after N ("N을 적용하지 말고"), nor right before another value ("N 계열을
이번에는 말고 M으로"). (i) the (f) exclusions only right after N, its particle or 도 ("N밖에 필요 없어", "N외에는
빼줘", "N빠짐없이" are not). (j) an exclusion conceded or negated again ("N 없이는 안 돼", "N 없이도 돼", "N은 빼줘도
돼", "N은 제외해도 돼", "N 말고는", "N은 제외하면 안 돼", "N을 제외할 필요까지는 없어", "N은 제외 안 해도 돼", "N은
제외하기 싫어") is not read, while a negation of another word after a phrase of its own ("N은 하지 않은 값을 표 말고")
or an unrelated exclusion ("설명 없이") keeps the exclusion. (k) "N은 제외하지 마세요" is the prohibitive (말-
written 마-). (l) "N 계열뿐 아니라 M도" is not only. (m) "N 말고 M도" and "N 표 말고 M 표도" are not read. (n) (g)
also before a free value, after the particles 이든/도 ("원화도 달러화도 아닌"). (o) "N하지 말고" stays an exclusion.
(p) a negated anaphor after a clause mark (\"N, 그건 빼고\") is not read.
"""

from __future__ import annotations

import pytest
from test_explicit_conditions_53 import STOCK, codes, m2

from claview_a.search.basis import polarity


def read(text, axis="seasonal"):
    got = polarity(text, axis)
    return sorted(got["required"]), sorted(got["excluded"]), [(u["value"], u["why"]) for u in got["unresolved"]]


SCOPE = "negation_scope_unread"
DOUBLE = "double_negation_unread"


# ---------------------------------------------------------------- (a) the value inside the negated noun phrase


@pytest.mark.parametrize("text, axis, required, excluded", [
    ("계절조정 표가 아니라 원계열 표로 보여줘", "seasonal", ["원계열"], ["계절조정"]),
    ("광의통화를 계절조정 데이터가 아니라 원계열 데이터로", "seasonal", ["원계열"], ["계절조정"]),
    ("계절조정 표 대신 원계열 표로", "seasonal", ["원계열"], ["계절조정"]),
    ("계절조정표가 아니라 원계열표로 보여줘", "seasonal", ["원계열"], ["계절조정"]),
    ("계절조정된 계열이 아니라 원계열로", "seasonal", ["원계열"], ["계절조정"]),
    ("광의통화를 월말 잔액 자료가 아닌 평균 잔액 자료로", "stock", ["평잔"], ["말잔"]),
    ("전년동기비 그래프가 아니라 전기비 그래프로", "comparison", ["전기비"], ["전년동기비"]),
    # a word closed by its particle closes the value's phrase: the negation is of another word
    ("원화기준 수출액을 품목별이 아니라 용도별로 보여줘", "currency", ["원화"], []),
])
def test_a_value_in_the_negated_noun_phrase_is_excluded_against_another_value(text, axis, required, excluded):
    assert read(text, axis) == (required, excluded, [])


@pytest.mark.parametrize("text, value", [
    ("원계열 표가 아니라 그래프로 보여줘", "원계열"),
    ("실업률을 계절조정 표가 아니라 그래프로 보여줘", "계절조정"),
    ("계절조정 표 대신 그래프로", "계절조정"),
    ("계절조정 표가 아니라", "계절조정"),
    ("계절조정된 계열이 아니라 그래프로", "계절조정"),
    # the same partner rule for a noun written against the value
    ("원계열표가 아니라 그래프로 보여줘", "원계열"),
    ("실업률을 계절조정표가 아니라 그래프로", "계절조정"),
])
def test_a_negated_noun_phrase_without_another_value_is_not_read(text, value):
    """Before: read as an exclusion of the value (a guess: the contrast may be of the display form only)."""
    assert read(text) == ([], [], [(value, SCOPE)])


def test_a_contrast_with_the_same_value_keeps_only_the_second_one():
    assert read("실업률을 계절조정 표가 아니라 계절조정 그래프로 보여줘") == (["계절조정"], [], [("계절조정", SCOPE)])


# ---------------------------------------------------------------- (b) the value's own phrase before another word


@pytest.mark.parametrize("text, axis, value", [
    ("계절조정 계열을 표 대신 차트로 보여줘", "seasonal", "계절조정"),
    ("계절조정 계열을 지역별로 막대그래프 말고 선그래프로 보여줘", "seasonal", "계절조정"),
    ("표 대신 원계열 그래프를 상품별 말고 지역별로 보여줘", "seasonal", "원계열"),
    ("실질 GDP를 연간이 아니라 분기별로 보여줘", "price", "실질"),
    ("신규취급액 기준 대출금리를 표 대신 선그래프로 보여줘", "loan_basis", "신규취급액"),
])
def test_a_value_whose_phrase_is_closed_before_the_negated_word_is_required(text, axis, value):
    assert read(text, axis) == ([value], [], [])


def test_the_phrase_rule_keeps_the_negated_noun_phrase_apart():
    text = "광의통화를 계절조정 데이터가 아니라 원계열 데이터를 막대 말고 선그래프로 보여줘"
    assert read(text) == (["원계열"], ["계절조정"], [])


@pytest.mark.parametrize("text, value", [
    # another negation after the negated word, in the value's clause (the after() rule)
    ("계절조정 계열을 표 대신 차트로 보여주지 말고", "계절조정"),
    ("계절조정 계열을 표로는 보고 싶지 않고 그래프로 보고 싶어", "계절조정"),
    # a verb in -지 is no other word: "보여주지 말고" may negate the value's phrase (left unread, as before)
    ("원계열 수치를 표로 보여주지 말고 선그래프로 그려줘", "원계열"),
    ("원계열 수치를 보여주지 말고 계절조정으로", "원계열"),
    # the value word closed by its particle, then a word that is not "-로" before the negated noun (plan 55)
    ("원계열을 상품별 표가 아니라 그래프로", "원계열"),
])
def test_a_value_phrase_with_another_negation_or_a_verb_is_not_read(text, value):
    required, excluded, unresolved = read(text)
    assert (value, SCOPE) in unresolved and value not in required and value not in excluded


# ---------------------------------------------------------------- (c) "N 말고도 M도"


@pytest.mark.parametrize("text", ["원계열 말고도 계절조정도 보여줘", "광의통화를 원계열 말고도 계절조정도 보여줘"])
def test_besides_the_value_is_additive(text):
    assert read(text) == (["계절조정", "원계열"], [], [])


@pytest.mark.parametrize("text", ["원계열 말고 계절조정으로", "원계열 말고 계절조정 도표로"])
def test_a_plain_malgo_still_negates_the_value(text):
    assert read(text) == (["계절조정"], ["원계열"], [])


@pytest.mark.parametrize("text", ["원계열 말고 계절조정도 보여줘", "원계열 말고 계절조정 계열도 보여줘"])
def test_malgo_before_a_value_with_also_is_not_read(text):
    """'N 말고 M도' may be 'besides N, M too' (like 'N 말고도'): N is not read as excluded (55b first had it
    excluded)."""
    assert read(text) == (["계절조정"], [], [("원계열", SCOPE)])


# ---------------------------------------------------------------- (d) the prohibitive with a particle after -지


@pytest.mark.parametrize("text, axis, required", [
    ("평잔은 제외하지는 말고 말잔과 함께 보여줘", "stock", ["말잔", "평잔"]),
    ("계절조정은 빼지도 말고 보여줘", "seasonal", ["계절조정"]),
    ("계절조정은 제외하지도 말고", "seasonal", ["계절조정"]),
])
def test_a_prohibited_exclusion_with_a_particle_is_a_requirement(text, axis, required):
    """D1: the same construction (exclusion stem, -지, 말-); nothing about the other value."""
    assert read(text, axis) == (required, [], [])


@pytest.mark.parametrize("text", ["계절조정은 제외하지는 않고", "계절조정은 빼지도 않은 값",
                                  "계절조정은 빼지는 않아도 돼"])
def test_any_other_double_negation_with_a_particle_is_not_read(text):
    """Not generalized (12:47): neither a requirement nor a permission."""
    assert read(text) == ([], [], [("계절조정", DOUBLE)])


# ---------------------------------------------------------------- (e) an exclusion negated again


@pytest.mark.parametrize("text", ["계절조정을 제외하고 싶지 않아", "계절조정은 빼고 싶지 않은데",
                                  "계절조정을 뺀 게 아니라"])
def test_an_exclusion_negated_in_its_clause_is_not_read(text):
    """Before: read as an exclusion."""
    assert read(text) == ([], [], [("계절조정", DOUBLE)])


@pytest.mark.parametrize("text, required, excluded", [
    ("계절조정은 하지 않은 값", [], ["계절조정"]), ("계절조정이 아니라 원계열", ["원계열"], ["계절조정"]),
    ("원계열 말고 계절조정으로", ["계절조정"], ["원계열"]), ("계절조정 안 된 자료", [], ["계절조정"]),
    ("계절조정을 제외하고 원계열만", ["원계열"], ["계절조정"]),
])
def test_an_exclusion_not_negated_again_is_kept(text, required, excluded):
    assert read(text) == (required, excluded, [])


# ---------------------------------------------------------------- (f) the lexicon's explicit exclusions


@pytest.mark.parametrize("text, required", [
    ("계절조정은 필요 없어, 원계열로 보여줘", ["원계열"]), ("계절조정은 필요가 없어", []),
    ("계절조정 없이 원계열로만 보여줘", ["원계열"]),
])
def test_an_existence_negation_or_without_excludes_the_value(text, required):
    """Before: read as a requirement of 계절조정 (a flip)."""
    assert read(text) == (required, ["계절조정"], [])


@pytest.mark.parametrize("text", ["원계열은 빼줘", "원계열은 빼 줘", "원계열은 빼주세요"])
def test_an_exclusion_request_excludes_the_value(text):
    assert read(text) == ([], ["원계열"], [])


@pytest.mark.parametrize("text, why", [
    # no need to exclude: not an exclusion, and not read as a permission (that would generalize 12:47)
    ("계절조정을 제외할 필요는 없어", DOUBLE),
    # an exclusion request later in the value's clause: never a requirement
    ("원계열 자료는 빼줘", SCOPE),
    ("계절조정 값은 필요 없어", SCOPE),
])
def test_a_negated_or_distant_exclusion_is_not_read(text, why):
    value = "원계열" if "원계열" in text else "계절조정"
    assert read(text) == ([], [], [(value, why)])


def test_without_inside_another_word_is_no_exclusion():
    assert read("원계열과 계절조정을 빠짐없이 보여줘") == (["계절조정", "원계열"], [], [])


# ---------------------------------------------------------------- (g) coordination


@pytest.mark.parametrize("text, required, excluded, unresolved", [
    ("수출액을 원화나 달러화 말고 계약통화 기준으로 보여줘", ["계약통화"], ["달러"], [("원화", SCOPE)]),
    ("원화 또는 달러화 말고 계약통화로", ["계약통화"], ["달러"], [("원화", SCOPE)]),
    ("원화와 달러화 말고 계약통화로", ["계약통화"], ["달러"], [("원화", SCOPE)]),
])
def test_a_value_coordinated_with_a_negated_value_is_not_read(text, required, excluded, unresolved):
    """Before: 원화 read as required (the negation may cover both)."""
    assert read(text, "currency") == (required, excluded, unresolved)


def test_a_value_coordinated_with_an_unread_value_is_not_read():
    assert read("원계열과 계절조정을 제외하지 않고") == ([], [], [("원계열", SCOPE), ("계절조정", DOUBLE)])


@pytest.mark.parametrize("text, required, excluded", [
    ("원계열과 계절조정은 제외하지 말고", ["계절조정", "원계열"], []),
    ("원계열과 계절조정 모두 보여줘", ["계절조정", "원계열"], []),
    ("원계열로 보여줘 계절조정 말고", ["원계열"], ["계절조정"]),
    ("달러기준 말고 원화기준으로, 표 대신 그래프로 보여줘", ["원화"], ["달러"]),
])
def test_a_value_coordinated_with_a_required_value_or_not_coordinated_is_kept(text, required, excluded):
    axis = "currency" if "원화" in text else "seasonal"
    assert read(text, axis) == (required, excluded, [])


# ---------------------------------------------------------------- through the graph


B = "월말 잔액 기준 광의통화"


@pytest.mark.parametrize("seasonal", ["원계열", "계절조정"])
@pytest.mark.parametrize("with_splitter", [False, True])
def test_a_negated_noun_phrase_without_another_value_stops_the_statistic(layer, seasonal, with_splitter):
    """(a) '원계열 표가 아니라 그래프로': before, 원계열 was excluded and a 계절조정 table passed."""
    quote = "원계열 표가 아니라 그래프로"
    splitters = [STOCK] + ([{"quote": quote, "axis": "seasonal", "for": ["s1"]}] if with_splitter else [])
    _, d = m2(layer, seasonal, splitters, question=f"{B}를 상품별로 {quote} 보여줘")
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["basis_polarity_unresolved"]


@pytest.mark.parametrize("seasonal, ok", [("원계열", True), ("계절조정", False)])
def test_a_negated_noun_phrase_against_another_value_excludes_it(layer, seasonal, ok):
    quote = "계절조정 데이터가 아니라 원계열 데이터로"
    _, d = m2(layer, seasonal, [STOCK, {"quote": quote, "axis": "seasonal", "for": ["s1"]}],
              question=f"{B}를 상품별로 {quote} 보여줘")
    assert (d["s1"]["outcome"] == "selected") is ok
    assert codes(d["s1"]) == ([] if ok else ["stated_basis_conflict"])


@pytest.mark.parametrize("with_splitter", [False, True])
def test_an_existence_negation_excludes_the_value_through_the_graph(layer, with_splitter):
    """(f) '계절조정은 필요 없어': before, read as a requirement (the 원계열 table conflicted)."""
    quote = "계절조정은 필요 없어"
    splitters = [STOCK] + ([{"quote": quote, "axis": "seasonal", "for": ["s1"]}] if with_splitter else [])
    q = f"{B}를 상품별로 보여줘. {quote}."
    _, d = m2(layer, "원계열", splitters, question=q)
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []
    assert d["s1"]["basis"]["seasonal"]["excluded"] == ["계절조정"]
    _, d = m2(layer, "계절조정", splitters, question=q)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
    assert d["s1"]["reasons"][0]["detail"]["excluded"] == ["계절조정"]


@pytest.mark.parametrize("seasonal", ["원계열", "계절조정"])
def test_no_need_to_exclude_stops_the_statistic(layer, seasonal):
    """(f) '계절조정을 제외할 필요는 없어': not an exclusion, not a permission: not read."""
    quote = "계절조정을 제외할 필요는 없어"
    _, d = m2(layer, seasonal, [STOCK, {"quote": quote, "axis": "seasonal", "for": ["s1"]}],
              question=f"{B}를 상품별로 보여줘. {quote}.")
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["basis_polarity_unresolved"]


# ---------------------------------------------------------------- review of 55b: (h) W is a word of its own


@pytest.mark.parametrize("text, axis, value", [
    # a phrase closed by 은/는, which is also a modifier ending
    ("원계열 보는 게 아니라 계절조정으로 보여줘", "seasonal", "원계열"),
    ("원계열 보는 거 말고 계절조정으로 보여줘", "seasonal", "원계열"),
    ("원계열 쓰는 것 말고 계절조정 써줘", "seasonal", "원계열"),
    ("원계열 보는 표 말고 계절조정 표로 보여줘", "seasonal", "원계열"),
    ("원계열 담은 표가 아니라 계절조정 표로", "seasonal", "원계열"),
    ("원계열 넣은 표 말고 계절조정 표로 보여줘", "seasonal", "원계열"),
    ("계절조정 하는 게 아니라 원계열로 보여줘", "seasonal", "계절조정"),
    ("수출액을 원화 기준 보는 게 아니라 달러기준으로 보여줘", "currency", "원화"),
    ("광의통화를 평잔 기준 보는 게 아니라 말잔으로 보여줘", "stock", "평잔"),
    ("원계열 데이터는 표가 아니라 그래프로 보여줘", "seasonal", "원계열"),
    # a modifier before 대신 ("instead of doing")
    ("원계열을 보는 대신 계절조정으로 보여줘", "seasonal", "원계열"),
    ("원계열을 쓰는 대신 계절조정을 써줘", "seasonal", "원계열"),
    ("원계열로 보는 대신 계절조정으로 보여줘", "seasonal", "원계열"),
    ("원계열 수치를 보여주는 대신 계절조정 수치로 보여줘", "seasonal", "원계열"),
    ("원계열을 본 대신 계절조정으로", "seasonal", "원계열"),
    ("광의통화를 말잔으로 보는 대신 평잔으로 보여줘", "stock", "말잔"),
    ("GDP를 명목으로 보는 대신 실질로 보여줘", "price", "명목"),
    # a clause nominalized on its verb
    ("계절조정 계열을 고른게 아니라 원계열을 골랐어", "seasonal", "계절조정"),
    ("계절조정 자료를 달라는게 아니라 원계열 자료를 달라는 거야", "seasonal", "계절조정"),
    # a verb in -지 right after the value: the value is its object
    ("원계열을 보여주지 말고 계절조정으로", "seasonal", "원계열"),
    ("원계열을 보여주지는 말고 계절조정으로", "seasonal", "원계열"),
    ("계절조정을 적용하지 말고 원계열로 보여줘", "seasonal", "계절조정"),
    ("계절조정을 반영하지 말고 그대로 보여줘", "seasonal", "계절조정"),
    ("계절조정을 쓰지 말고 원계열로 보여줘", "seasonal", "계절조정"),
    # W right before another value: the negation may contrast the two values
    ("계절조정 계열을 이번에는 말고 원계열로 보여줘", "seasonal", "계절조정"),
    ("계절조정 자료를 이번엔 말고 원계열 자료로 보여줘", "seasonal", "계절조정"),
    ("계절조정 계열은 지금은 말고 원계열로 먼저 보여줘", "seasonal", "계절조정"),
    ("계절조정을 이번에는 말고 원계열로 보여줘", "seasonal", "계절조정"),
    ("원계열 자료를 그대로가 아니라 계절조정해서 보여줘", "seasonal", "원계열"),
])
def test_a_negated_word_that_is_no_word_of_its_own_is_not_read(text, axis, value):
    """55b first read these as a requirement of the value (a flip: the sentence negates it, or may)."""
    required, excluded, unresolved = read(text, axis)
    assert (value, SCOPE) in unresolved and value not in required and value not in excluded


@pytest.mark.parametrize("text, axis, required", [
    ("원계열을 표로 보여주지 말고 그래프로", "seasonal", ["원계열"]),
    ("원계열을 상품별로 표가 아니라 선그래프로 보여줘", "seasonal", ["원계열"]),
    ("계절조정 계열을 표 대신 차트로 보여줘", "seasonal", ["계절조정"]),
])
def test_a_negated_word_of_its_own_still_leaves_the_value_required(text, axis, required):
    assert read(text, axis) == (required, [], [])


def test_a_value_inside_the_modified_noun_before_daesin_is_excluded_against_another_value():
    assert read("원계열 보는 대신 계절조정으로") == (["계절조정"], ["원계열"], [])


# ---------------------------------------------------------------- (i) the (f) exclusions right after the value only


@pytest.mark.parametrize("text, axis, value", [
    ("계절조정밖에 필요 없어", "seasonal", "계절조정"),
    ("실업률은 계절조정밖에 필요 없어", "seasonal", "계절조정"),
    ("계절조정밖엔 필요 없어요", "seasonal", "계절조정"),
    ("계절조정외에는 필요 없어", "seasonal", "계절조정"),
    ("원계열외에는 빼줘", "seasonal", "원계열"),
    ("계절조정빼곤 필요 없어", "seasonal", "계절조정"),
    ("광의통화는 평잔밖에 필요 없어", "stock", "평잔"),
    ("수출액은 원화기준밖에 필요 없어", "currency", "원화"),
])
def test_only_the_value_is_not_an_exclusion(text, axis, value):
    """'N밖에 필요 없어' means only N: 55b first read N as excluded (a flip); not read."""
    assert read(text, axis) == ([], [], [(value, SCOPE)])


@pytest.mark.parametrize("text, required, excluded", [
    ("원계열빠짐없이 보여줘", ["원계열"], []),
    ("계절조정없이 원계열로만", ["원계열"], ["계절조정"]),
    ("계절조정도 필요 없어", [], ["계절조정"]),
    ("계절조정이 필요 없는 원계열로", ["원계열"], ["계절조정"]),
])
def test_an_exclusion_predicate_right_after_the_value(text, required, excluded):
    assert read(text) == (required, excluded, [])


# ---------------------------------------------------------------- (j) an exclusion conceded or negated again


@pytest.mark.parametrize("text", [
    # 'cannot do without': N is needed
    "계절조정 없이는 안 돼", "계절조정이 없이는 안 돼", "계절조정 없이 보면 안 돼", "계절조정 없이 보여주면 안 돼",
    "계절조정 없이는 의미가 없어", "계절조정 없이는 볼 수 없어", "계절조정 없이는 곤란해",
    "전월비는 계절조정 없이는 의미가 없어", "계절조정은 빼줘선 안 돼",
    # a concession: N is free
    "계절조정 없이도 돼", "계절조정은 빼줘도 돼", "계절조정은 제외해도 돼", "계절조정은 필요 없어도 돼",
    "계절조정은 제외 안 해도 돼",
    # the exclusion negated or not wanted
    "계절조정은 제외하면 안 돼", "계절조정은 제외 안 돼", "계절조정을 제외할 필요까지는 없어",
    "계절조정을 제외할 필요까진 없어", "계절조정을 제외할 필욘 없어", "계절조정은 제외하기 싫어",
    "계절조정 제외는 하지 마",
    # except N: the rest of the sentence decides
    "계절조정 말고는 다 괜찮아", "계절조정 빼고는 다 보여줘",
])
def test_an_exclusion_conceded_or_negated_again_is_not_read(text):
    """Neither an exclusion nor a requirement nor a permission (12:47: not generalized)."""
    assert read(text) == ([], [], [("계절조정", DOUBLE)])


@pytest.mark.parametrize("text", ["원계열 말고는 보여주지 마", "원계열이 아니면 안 돼", "원계열 없이는 비교가 안 돼"])
def test_only_the_value_written_with_a_negation_is_not_an_exclusion(text):
    """'N 말고는 보여주지 마', 'N이 아니면 안 돼': only N; before, read as an exclusion (a flip)."""
    assert read(text) == ([], [], [("원계열", DOUBLE)])


@pytest.mark.parametrize("text, required, excluded", [
    # the later negation is of another word after a phrase of its own, or an exclusion of another word
    ("계절조정은 하지 않은 값을 표 말고 그래프로", [], ["계절조정"]),
    ("원계열을 제외하고 설명 없이 보여줘", [], ["원계열"]),
    ("계절조정 없이 원계열로만 보여줘", ["원계열"], ["계절조정"]),
    ("계절조정은 필요 없어, 원계열로 보여줘", ["원계열"], ["계절조정"]),
    ("계절조정만 아니면 되니까", [], ["계절조정"]),
])
def test_an_exclusion_with_an_unrelated_negation_is_kept(text, required, excluded):
    assert read(text) == (required, excluded, [])


@pytest.mark.parametrize("text", ["계절조정은 하지 않은 값을 원하는 게 아니라",
                                  "계절조정은 하지 않은 값을 원하는게 아니라"])
def test_an_exclusion_inside_a_negated_clause_is_not_read(text):
    assert read(text) == ([], [], [("계절조정", DOUBLE)])


@pytest.mark.parametrize("text", ["원계열로 보면 안 돼", "계절조정은 싫고 원계열로 보여줘"])
def test_another_negation_later_in_the_clause_is_not_read(text):
    value = "원계열" if text.startswith("원계열") else "계절조정"
    required, excluded, unresolved = read(text)
    assert (value, SCOPE) in unresolved and value not in required and value not in excluded


# ---------------------------------------------------------------- (k) the prohibitive written 마-


@pytest.mark.parametrize("text", ["계절조정은 제외하지 마", "계절조정은 제외하지 마세요",
                                  "계절조정은 제외하지 마시고 보여주세요", "계절조정은 빼지 마"])
def test_a_prohibited_exclusion_written_ma_is_a_requirement(text):
    """D1: the same construction (exclusion stem, -지, 말-); before, '제외하지 마세요' was read as an exclusion."""
    assert read(text) == (["계절조정"], [], [])


# ---------------------------------------------------------------- (l) not only, a noun between


@pytest.mark.parametrize("text, axis, required", [
    ("계절조정 계열뿐 아니라 원계열도 보여줘", "seasonal", ["계절조정", "원계열"]),
    ("원계열 자료뿐 아니라 계절조정 자료도 같이 보여줘", "seasonal", ["계절조정", "원계열"]),
    ("계절조정 그래프만이 아니라 원계열 그래프도 보여줘", "seasonal", ["계절조정", "원계열"]),
    ("광의통화 평잔 자료만 아니라 말잔 자료도 보여줘", "stock", ["말잔", "평잔"]),
])
def test_not_only_the_noun_phrase_of_the_value_is_no_negation(text, axis, required):
    """12:47: '계절조정뿐 아니라 원계열도' requires both; before, the first value was excluded (a flip)."""
    assert read(text, axis) == (required, [], [])


# ---------------------------------------------------------------- (m) "N 말고 M도"


@pytest.mark.parametrize("text, axis, value", [
    ("계절조정만 말고 원계열도 같이 보여줘", "seasonal", "계절조정"),
    ("명목 말고 실질도 보여줘", "price", "명목"),
    ("원계열 그래프 말고 계절조정 그래프도 같이 그려줘", "seasonal", "원계열"),
    ("원계열 말고 다른 계열도 있으면 보여줘", "seasonal", "원계열"),
])
def test_malgo_before_an_added_word_is_not_read(text, axis, value):
    required, excluded, unresolved = read(text, axis)
    assert (value, SCOPE) in unresolved and value not in required and value not in excluded


def test_neither_noun_phrase_excludes_a_value():
    text = "원계열 표도 아니고 계절조정 표도 아니고 그래프로 보여줘"
    assert read(text) == ([], [], [("원계열", SCOPE), ("계절조정", SCOPE)])


# ---------------------------------------------------------------- (n) coordination before a free value, 이든, 도


@pytest.mark.parametrize("text, axis, first", [
    ("원계열과 계절조정은 제외하지 않아도 돼", "seasonal", "원계열"),
    ("원계열과 계절조정은 빼지 않아도 된다", "seasonal", "원계열"),
    ("원계열, 계절조정은 제외하지 않아도 돼", "seasonal", "원계열"),
    ("원계열이든 계절조정이든 제외하지 않아도 돼", "seasonal", "원계열"),
    ("원화나 달러화는 제외하지 않아도 돼", "currency", "원화"),
])
def test_a_value_coordinated_with_a_free_value_is_not_read(text, axis, first):
    """Before: the first value was required (a condition added to a permission); never free either (12:47)."""
    assert read(text, axis) == ([], [], [(first, SCOPE)])


@pytest.mark.parametrize("text, axis, required, excluded, unresolved", [
    ("원화도 달러화도 아닌 계약통화 기준으로 보여줘", "currency", ["계약통화"], ["달러"], [("원화", SCOPE)]),
    ("원계열이든 계절조정이든 상관없어", "seasonal", [], [], [("원계열", SCOPE), ("계절조정", SCOPE)]),
    ("계절조정 여부와 상관없이 보여줘", "seasonal", [], [], [("계절조정", SCOPE)]),
    ("원계열도 계절조정도 보여줘", "seasonal", ["계절조정", "원계열"], [], []),
    ("원계열도 계절조정도 제외하지 말고 보여줘", "seasonal", ["계절조정", "원계열"], [], []),
])
def test_values_joined_by_ideun_or_do(text, axis, required, excluded, unresolved):
    assert read(text, axis) == (required, excluded, unresolved)


# ---------------------------------------------------------------- (o) a verb in -지 written onto the value


@pytest.mark.parametrize("text, required", [
    ("계절조정하지 말고 그냥 보여줘", []), ("계절조정하지도 말고 그냥 보여줘", []),
    ("계절조정하지 말고 원계열로 보여줘", ["원계열"]),
])
def test_a_verb_in_ji_written_onto_the_value_is_no_noun(text, required):
    """'계절조정하지 말고' (do not adjust): excluded, as at plan 55; 55b first left it unresolved."""
    assert read(text) == (required, ["계절조정"], [])


@pytest.mark.parametrize("text", ["실업률을 계절조정, 그건 빼고 원계열로 보여줘", "계절조정, 그것은 빼고 원계열로",
                                  "계절조정, 그거 말고 원계열로", "계절조정. 그건 빼지 말고 보여줘"])
def test_a_negation_of_an_anaphor_after_a_clause_mark_is_not_read(text):
    """(p) '계절조정, 그건 빼고': the anaphor takes up the value after the comma, so its clause goes on and the value
    is not read (scope-25 of the corpus; it read as required before)."""
    assert read(text)[0] in ([], ["원계열"]) and read(text)[2] == [("계절조정", SCOPE)]


@pytest.mark.parametrize("text, required", [
    ("계절조정, 그리고 원계열도 보여줘", ["계절조정", "원계열"]), ("원계열, 그것도 상품별로 보여줘", ["원계열"]),
    ("계절조정, 그걸로 보여줘", ["계절조정"]),
])
def test_a_clause_mark_without_a_negated_anaphor_keeps_the_value(text, required):
    assert read(text) == (required, [], [])


# ---------------------------------------------------------------- review of 55b through the graph


Q = "월말 잔액 기준의 광의통화를 상품별로 보고 싶어. {}"


@pytest.mark.parametrize("quote, seasonal, outcome, code", [
    # the prohibitive written 마-: required
    ("계절조정은 제외하지 마세요.", "계절조정", "selected", []),
    ("계절조정은 제외하지 마세요.", "원계열", "failed", ["stated_basis_conflict"]),
    # an exclusion with a display negation of another word: still an exclusion
    ("계절조정은 하지 않은 값을 표 말고 그래프로 보여줘", "원계열", "selected", []),
    ("계절조정은 하지 않은 값을 표 말고 그래프로 보여줘", "계절조정", "failed", ["stated_basis_conflict"]),
    # 'cannot do without': not read
    ("계절조정 없이는 안 돼.", "계절조정", "failed", ["basis_polarity_unresolved"]),
    ("계절조정 없이는 안 돼.", "원계열", "failed", ["basis_polarity_unresolved"]),
])
def test_the_review_readings_through_the_graph(layer, quote, seasonal, outcome, code):
    _, d = m2(layer, seasonal, [STOCK], question=Q.format(quote))
    assert d["s1"]["outcome"] == outcome and codes(d["s1"]) == code
