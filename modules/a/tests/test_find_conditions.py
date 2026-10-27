"""find_conditions (detail 2.2): overlaps between kinds, names, relation/function/display words (plan 04, 2-5).

The per-kind behaviour moved from the old tests is pinned in modules/a/tests/spec; this file covers what only the new
``find_conditions`` does. Synthetic catalog only (made-up "가상" names); no network, no model, no question file.
"""

from __future__ import annotations

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a.catalog.layer import load_layer
from claview_a.contract import check
from claview_a.interpret import conditions as finders
from claview_a.interpret.text import normalize_text


def _axis(obj_id: str, names: list[str]) -> tuple:
    return (obj_id, "계정항목별", "ACCOUNT", [(f"{obj_id}{k}", n, None, None, None) for k, n in enumerate(names, 1)])


TABLES = [
    ("T_MAIN", "가상주요지표(연간지표)", "Synthetic Main", [_axis("B", ["가상총소득"])]),
    ("T_PRICE", "가상물가표", "Synthetic Price", [_axis("C", ["가상식료품", "가상에너지", "가상원재료"])]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("find_conditions")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


def run(question: str, layer=None) -> list[dict]:
    found = finders.find_conditions(normalize_text(question), layer)
    assert check.check_conditions(found, question) == []  # full shape
    return found


def brief(found: list[dict]) -> list[tuple]:
    return [(c["id"], c["quote"]) for c in found]


def test_question_order_and_ids_per_kind():
    q = "2024년부터 가상수출을 월별로 원계열 기준 1년 전보다 몇 % 늘었는지 십억 원 단위로 보여줘"
    assert brief(run(q)) == [("P1", "2024년부터"), ("F1", "월별"), ("B1", "원계열"), ("K1", "1년 전보다"), ("K2", "몇 %"),
                             ("U1", "십억 원 단위")]


def test_runs_without_a_catalog_layer():
    assert brief(run("가상갑과 가상을의 차이 추이")) == [("R1", "가상갑"), ("R2", "가상을"), ("L1", "차이"), ("D1", "추이")]


def test_marker_inside_a_value_condition_is_dropped():
    q = "가상수출 전년 대비 증가율"
    text = normalize_text(q)
    assert [c["quote"] for c in finders.find_relation_conditions(text)] == ["대비"]  # the finder alone
    assert brief(run(q)) == [("K1", "전년 대비")]
    q2 = "가상에너지를 뺀 가상물가"
    assert [c["quote"] for c in finders.find_function_conditions(normalize_text(q2))] == ["를 뺀"]
    assert brief(run(q2)) == [("X1", "가상에너지를 뺀")]


def test_marker_that_holds_a_value_condition_stays():
    found = run("가상갑은 가상을의 몇 %인가")
    assert [(c["id"], c["quote"], c["attrs"]) for c in found] == [
        ("L1", "의 몇 %", {"relation": "percent_of"}),
        ("K1", "몇 %", {"axis": "calc", "value": "백분율", "polarity": "+", "origin": "handoff_rule"}),
    ]


def test_frequency_inside_a_catalog_name_is_part_of_the_name(layer):
    q = "가상주요지표(연간지표)를 2024년부터 월별로 보여줘"
    text = normalize_text(q)
    assert [c["quote"] for c in finders.find_frequency_conditions(text)] == ["연간", "월별"]  # the finder alone
    assert brief(run(q, layer)) == [("P1", "2024년부터"), ("F1", "월별")]  # renumbered after the drop
    assert brief(run(q)) == [("F1", "연간"), ("P1", "2024년부터"), ("F2", "월별")]  # no catalog: no name protection


def test_frequency_word_outside_names_stays(layer):
    assert brief(run("가상총소득을 연간으로 보여줘", layer)) == [("F1", "연간")]


@pytest.mark.parametrize("question, kind, expected", [
    ("가상갑 지수 추이를 나란히 비교해줘", "display",
     [("추이", {"display": "trend"}), ("나란히", {"display": "side_by_side"}), ("비교", {"display": "compare"})]),
    ("가상갑의 구성을 보여줘", "display", [("구성", {"display": "composition"})]),
    ("가상갑 구성비", "display", []),
    ("가상갑을 가상을로 나눈 비율", "relation", [("로 나눈", {"relation": "ratio"}), ("비율", {"relation": "ratio"})]),
    ("가상부채비율 추이", "relation", []),
    ("가상갑 대비 가상을 비중", "relation", [("대비", {"relation": "ratio"}), ("비중", {"relation": "share_of"})]),
    ("가상갑과 가상을의 격차", "relation", [("격차", {"relation": "diff"})]),
    ("가상갑을 비금융법인·가계로 나눠서", "function", [("로 나눠서", {"function": "split_by"})]),
    ("가상물가지수 중 가상식료품", "function", [("중", {"function": "among"})]),
    ("가상중소기업 비중", "function", []),
    ("가상갑과 가상을을 각각 보여줘", "function", [("각각", {"function": "each"})]),
    ("가상갑 흐름", "display", []),
])
def test_relation_function_display_words(question, kind, expected):
    found = [c for c in run(question) if c["kind"] == kind]
    assert [(c["quote"], c["attrs"]) for c in found] == expected


def test_byeol_word_stops_before_its_particle():
    (b1,) = [c for c in run("가상신용을 업권별로 보여줘") if c["kind"] == "basis"]
    assert (b1["quote"], b1["value"], b1["attrs"]["axis"]) == ("업권별", "업권별", "classification")


def test_exclude_list_uses_the_catalog_layer(layer):
    q = "가상식료품과 가상에너지를 뺀 가상물가"
    assert brief(run(q, layer)) == [("X1", "가상식료품"), ("X2", "가상에너지를 뺀")]
    assert brief(run(q)) == [("X1", "가상에너지를 뺀")]  # without the catalog the first name is not known as a value


def test_quote_has_no_whitespace_a_rule_took_at_its_ends():
    assert [c["quote"] for c in run("2020~2023년 가상물가") if c["kind"] == "period"] == ["2020~2023년"]
    q = "가상채권에서 1 뺀 값"
    (minus,) = finders.find_function_conditions(normalize_text(q))
    assert (minus["quote"], q[minus["span"][0]:minus["span"][1]]) == ("뺀", "뺀")


def test_period_end_expression_keeps_the_written_words():
    (p1,) = run("  2025년   1분기말부터 2026년 1분기말까지  ")
    assert p1["quote"] == "2025년   1분기말부터 2026년 1분기말까지"
    assert p1["attrs"]["start_expr"]["text"] == "2025년   1분기말" and p1["attrs"]["start_expr"]["end_mark"] is True
    assert p1["attrs"]["end_expr"]["text"] == "2026년 1분기말"
