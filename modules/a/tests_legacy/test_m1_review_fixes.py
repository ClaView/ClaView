"""Fixes of the M1-1 review (plan 04 M1): name protection before the list/exclude rules, the word-boundary choice of
name spans, and frequency/period words glued to a general noun. Synthetic catalog only ("가상" names)."""

from __future__ import annotations

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a_legacy.catalog.index import TermIndex
from claview_a_legacy.catalog.layer import load_layer
from claview_a_legacy.catalog.spans import NameSpanIndex, read_particles
from claview_a_legacy.interpret import conditions as finders
from claview_a_legacy.interpret.text import normalize_text


def _axis(obj_id: str, names: list[str]) -> tuple:
    return (obj_id, "계정항목별", "ACCOUNT", [(f"{obj_id}{k}", n, None, None, None) for k, n in enumerate(names, 1)])


TABLES = [
    ("T_QRATE", "가상분기금리", "Synthetic Quarter Rate", [_axis("A", ["가상총계", "가상소계"])]),
    ("T_MAIN", "가상주요표", "Synthetic Main", [_axis("B", ["가상총소득"])]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("review_fixes")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


def _kinds(found: list[dict], kind: str) -> list[str]:
    return [c["value"] for c in found if c["kind"] == kind]


# ---------------------------------------------------------------- 1) name protection before the Q5 rules


def test_frequency_inside_a_name_does_not_block_an_exclusion(layer):
    q = "가상분기금리를 뺀 가상총계"
    text = normalize_text(q)
    assert _kinds(finders.find_frequency_conditions(text), "frequency") == ["Q"]  # the reused Q3 sees "분기"
    found = finders.find_conditions(text, layer)
    assert _kinds(found, "exclude") == ["가상분기금리"]
    assert _kinds(found, "frequency") == []
    assert _kinds(finders.find_exclude_conditions(text, layer), "exclude") == ["가상분기금리"]
    # without the catalog the name is unknown: the frequency still blocks the exclusion (old Q5 behaviour)
    assert _kinds(finders.find_conditions(text, None), "exclude") == []


def test_frequency_inside_a_name_does_not_block_a_list(layer):
    q = "가상분기금리와 가상총계를 보여줘"
    text = normalize_text(q)
    found = finders.find_conditions(text, layer)
    assert _kinds(found, "breakdown") == ["가상분기금리", "가상총계"]
    assert _kinds(found, "frequency") == []


# ---------------------------------------------------------------- 2) boundary-valid choice of name spans


def _finder(keys: list[str]) -> NameSpanIndex:
    entries = {k: [{"key": k, "name": k, "type": "indicator", "table_ids": ["T"], "obj_id": "A", "obj_name": "A",
                    "item_id": k, "path": "exact", "variant": "name", "kind": "value", "status": "draft"}]
               for k in keys}
    return NameSpanIndex(index=TermIndex(entries=entries, span_keys=frozenset(keys)), particles=read_particles())


def test_a_valid_split_survives_when_the_longest_names_do_not_fit():
    finder = _finder(["abcde", "ghijk", "fg", "hijk", "abcd", "ef"])
    got = [s["quote"] for s in finder.find("abcdefghijk")]
    assert got == ["abcde", "fg", "hijk"]  # valid splits: abcde|fg|hijk or abcd|ef|ghijk (equal; longer name first)
    assert "".join(got) == "abcdefghijk"


def test_the_choice_covers_most_characters_and_keeps_boundaries():
    finder = _finder(["abcdef", "ab", "cdefgh", "gh"])
    # abcdef|gh and ab|cdefgh are both valid and cover the word; the longer name further left wins
    assert [s["quote"] for s in finder.find("abcdefgh")] == ["abcdef", "gh"]
    # no valid split: "cd" has no right boundary (nothing chosen starts after it), so "ab" has none either
    assert _finder(["ab", "cd"]).find("abcdx") == []
    # a name with a particle after it is valid alone
    assert [s["quote"] for s in _finder(["abcd", "bcdef"]).find("abcd를")] == ["abcd"]


# ---------------------------------------------------------------- 3) frequency/period word glued to a general noun


@pytest.mark.parametrize("question, expected", [
    ("연간지표로 가상총소득을 보여줘", []),
    ("분기자료를 보여줘", []),
    ("연간 가상총소득을 보여줘", ["Y"]),
    ("분기별자료를 보여줘", ["Q"]),
    ("월별로 가상총소득을 보여줘", ["M"]),
])
def test_time_word_glued_to_a_general_noun_is_not_a_frequency(question, expected):
    """find_conditions drops it (the reused Q3 finder alone is unchanged: modules/a/tests_legacy/test_find_conditions.py)."""
    text = normalize_text(question)
    assert _kinds(finders.find_conditions(text, None), "frequency") == expected


def test_compound_time_word_does_not_block_an_exclusion():
    found = finders.find_conditions(normalize_text("분기자료를 뺀 가상총계"), None)
    assert _kinds(found, "exclude") == ["분기자료"] and _kinds(found, "frequency") == []


def test_compound_words_come_from_the_data_file():
    assert finders.compound_time_word("연간", "지표로")
    assert not finders.compound_time_word("분기별", "자료")
    assert not finders.compound_time_word("연간", " 지표")
    assert not finders.compound_time_word("연간", "지표)를", "가상주요지표(")  # bracket qualifier keeps it


# ---------------------------------------------------------------- calc words (user decision 10/18)


@pytest.mark.parametrize("question, value", [
    ("가상총소득이 가장 높은 달을 보여줘", "최댓값"), ("가상총소득이 가장 낮은 달", "최솟값"),
    ("가상총소득 상위 5개", "상위"), ("가상총소득 하위 3개", "하위"), ("가상총소득의 연평균", "연평균"),
    ("가상총소득 누적 값", "누적"),
])
def test_ranking_and_accumulation_words_are_calc_conditions(question, value):
    found = finders.find_conditions(normalize_text(question), None)
    assert _kinds(found, "calc") == [value]


def test_accumulated_chart_is_not_a_calc_condition():
    assert _kinds(finders.find_conditions(normalize_text("가상총소득을 누적 막대그래프로"), None), "calc") == []


@pytest.mark.parametrize("question, quotes", [
    ("가상총소득이 전년보다 얼마나 늘었는지 보여줘", ["늘었"]),
    ("가상총소득 증가율을 보여줘", ["증가율"]),
    ("가상총소득이 어떻게 변했나", ["변했"]),
])
def test_change_words_are_calc_hints_not_conditions(question, quotes):
    text = normalize_text(question)
    assert [h["quote"] for h in finders.find_calc_hints(text)] == quotes
    assert all(c["quote"] not in quotes for c in finders.find_conditions(text, None))
