"""Word-boundary name spans and year-range-free names (plan 04, 2-5 (a)(b); catalog/spans.py).

Synthetic catalog only (made-up "가상" names); no network, no model, no question file. The stretch-1 ``find_spans``
is shown next to the new finder where they differ, and its behaviour is not changed.
"""

from __future__ import annotations

import pytest
from catalog_synth import write_inputs, write_raw

from claview.a.catalog.layer import load_layer
from claview.a.catalog.spans import NameSpanIndex, read_particles
from claview.a.catalog.text import strip_year_range


def _axis(obj_id: str, names: list[str]) -> tuple:
    return (obj_id, "계정항목별", "ACCOUNT", [(f"{obj_id}{k}", n, None, None, None) for k, n in enumerate(names, 1)])


TABLES = [
    ("T_FLOW", "가상금융거래표(2009~)", "Synthetic Flow",
     [_axis("A", ["가상예금", "가상대출", "가상기타자산(2007~)(누계)", "가상현금흐름(2007~)"])]),
    ("T_MAIN", "가상주요지표(연간지표)", "Synthetic Main", [_axis("B", ["가상총소득"])]),
    ("T_BAL", "가상잔액표", "Synthetic Balance", [_axis("C", ["말잔", "가상예금 금리"])]),
    ("T_PAIR", "가상짝표", "Synthetic Pair", [_axis("D", ["가상갑", "가상을"])]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("name_spans")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


def quotes(spans: list[dict]) -> list[str]:
    return [s["quote"] for s in spans]


# ---------------------------------------------------------------- (b) year-range-free names


@pytest.mark.parametrize("name, stripped", [
    ("금융거래표(2009~)", "금융거래표"), ("현금흐름비율(~2006)", "현금흐름비율"), ("현금흐름표(1995~2007)", "현금흐름표"),
    ("생산자물가지수-총지수(1910~1964)", "생산자물가지수-총지수"), ("단기투자증권처분손실(2003)", "단기투자증권처분손실"),
    ("(장기투자증권)(2003~)", "(장기투자증권)"), ("금융거래표 (1975-2005)", "금융거래표"),
    ("성장성 지표(2007~2010, 표본조사)", None), ("재무상태표(1999, 표본조사)", None), ("가계대출", None), ("M2(말잔)", None),
])
def test_strip_year_range_drops_only_year_range_brackets(name, stripped):
    assert strip_year_range(name) == stripped


def _year_free(cands: list[dict]) -> list[tuple]:
    return [(c["path"], c["kind"], c["table_ids"], c["item_id"]) for c in cands if c["variant"] == "no_year_range"]


def test_year_range_free_names_are_exact_candidates_beside_the_index(layer):
    spans = layer.name_spans()
    # a value: the term index has the year-range-free form only as the lexical bracket-free form
    assert {c["path"] for c in layer.index.lookup("가상현금흐름")} == {"lexical"}
    assert _year_free(spans.lookup("가상현금흐름")) == [("exact", "value", ["T_FLOW"], "A4")]
    # a table: the name before its first bracket is an exact candidate of the index already, nothing is added
    assert {c["path"] for c in layer.index.lookup("가상금융거래표")} == {"exact"}
    assert _year_free(spans.lookup("가상금융거래표")) == []
    # a name that keeps another bracket gets its own key; its bracket-free form is not made exact
    assert layer.index.lookup("가상기타자산(누계)") == []
    assert _year_free(spans.lookup("가상기타자산(누계)")) == [("exact", "value", ["T_FLOW"], "A3")]
    assert _year_free(spans.lookup("가상기타자산")) == []
    assert quotes(spans.find("가상기타자산(누계)를 보여줘")) == ["가상기타자산(누계)"]


# ---------------------------------------------------------------- (a) word boundaries


def test_a_match_across_a_word_start_is_not_a_name(layer):
    text = "월말 잔액"
    assert quotes(layer.index.find_spans(text)) == ["말 잔"]  # stretch 1 finder, unchanged
    assert layer.name_spans().find(text) == []


@pytest.mark.parametrize("text, expected", [
    ("가상예금을 보여줘", ["가상예금"]),  # particle after the name
    ("가상예금, 가상대출", ["가상예금", "가상대출"]),  # punctuation
    ("(가상예금)", ["가상예금"]),  # inside brackets
    ("가상예금 금리를 보여줘", ["가상예금 금리"]),  # across a space: whole words, then a particle
    ("가상예금 금리차 보여줘", ["가상예금"]),  # across a space the last word must end with the name
    ("새가상예금 금리", []),  # across a space the first word must start with the name
    ("비가상예금", []),  # inside one word without a boundary
    ("가상예금가상대출", ["가상예금", "가상대출"]),  # two names written together: the next name's start is a boundary
    ("가상주요지표(연간지표)를", ["가상주요지표(연간지표)"]),
])
def test_name_ends_on_word_boundaries(layer, text, expected):
    spans = layer.name_spans().find(text)
    assert quotes(spans) == expected
    for s in spans:
        assert text[s["start"]:s["end"]] == s["quote"] and s["candidates"] >= 1


def test_a_dropped_candidate_is_no_boundary_evidence(layer):
    finder = layer.name_spans()
    assert quotes(finder.find("가상갑가상을")) == ["가상갑", "가상을"]
    # "가상을" is followed by "것" (no particle, no name): it drops, and then "가상갑" has no right boundary either
    assert finder.find("가상갑가상을것") == []
    assert quotes(layer.index.find_spans("가상갑가상을것")) == ["가상갑", "가상을"]  # stretch 1 finder, unchanged


def test_positions_are_in_the_original_text(layer):
    text = "  가상예금　금리를  보여줘"
    (span,) = layer.name_spans().find(text)
    assert span["quote"] == "가상예금　금리" and text[span["start"]:span["end"]] == span["quote"]


def test_particles_come_from_the_data_file():
    particles = read_particles()
    assert {"을", "를", "으로", "에서는"} <= set(particles)
    assert list(particles) == sorted(particles, key=len, reverse=True)


def test_name_span_index_without_extra_keys_uses_the_term_index_keys(layer):
    plain = NameSpanIndex(index=layer.index, particles=read_particles())
    assert plain.keys == layer.index.span_keys
    assert quotes(plain.find("가상예금을")) == ["가상예금"]


def test_layer_builds_the_name_span_index_once(layer):
    assert layer.name_spans() is layer.name_spans()
    assert layer.list_names() is layer.list_names()
