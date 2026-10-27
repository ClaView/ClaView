"""Breakdown (listed names) and exclude conditions (old Q5): specification moved from the old contract tests
(ledger target Q5).

Old breakdown rows (kind "list") are kind ``breakdown`` with ``attrs.subkind`` "list"; the catalog-corrected name is
``value``/``attrs.text`` while ``quote`` stays as written. Old tests that looked names up through a hand-made name
object now get the same names from a synthetic catalog (ledger "changed"). Finders are stretch 2 (xfail).
"""

from __future__ import annotations

import pytest
from spec_catalogs import CATALOGS
from spec_support import (Layers, behaviour_params, cond, condition_problems, condition_shape, number_parts,
                          run_condition_case, shape_params)

S2 = "test_aflow_stage2_rules.py"
Q5L = "test_q5_lists_v1.py"
CXP = "test_cx_item_prefix.py"
QG = "test_q6g_graph.py"


def R(question, n, value, quote=None, source=None, measure_pair=None, nth=0):  # noqa: N802
    attrs = {}
    if source is not None:
        attrs["source"] = source
    if measure_pair is not None:
        attrs["measure_pair"] = measure_pair
    if attrs:
        attrs["text"] = value
    return cond(question, f"R{n}", "breakdown", quote, nth=nth, value=value, attrs=attrs or None)


def X(question, n, value, quote=None, hints=None):  # noqa: N802
    attrs = {"text": value, "complement_hints": hints} if hints is not None else None
    return cond(question, f"X{n}", "exclude", quote, value=value, attrs=attrs)


def _case(key: str, question: str, kinds: list[str], conditions: list[dict], layer: str | None = "q5_lists",
          param: str | None = None) -> dict:
    return {"key": key, "question": question, "kinds": kinds, "layer": layer, "param": param or question,
            "node": f"find_{kinds[0]}_conditions", "expect": {"conditions": conditions}}


def names(question: str, values: list[str]) -> list[dict]:
    return [R(question, n, v) for n, v in enumerate(values, 1)]


MEASURE_PAIR = [
    ("가상예탁 가상정기 금리와 가상은행 가상정기예금 금리를 비교해줘", True),
    ("가상부문 가상조달 규모와 가상계정상 가상정부 차액을 비교해줘", True),
    ("가상부문 가상조달 규모와 가상계정의 가상차입금을 비교해줘", True),
    ("가상치즈와 가상버터의 가상물가를 비교해줘", False),
    ("가상생산자물가지수 중 가상식료품과 가상신선식품을 비교해줘", False),
    ("가상수입 가상원재료와 가상중간재의 물가 흐름", False),
]
REJOINED = [
    ("가상인력난·가상인건비를 꼽은 비중", [("가상인력난 가상인건비", "가상인력난·가상인건비")]),
    ("가상특허 및 가상실용권과 가상상표권으로 비교", [("가상특허 및 가상실용권", "가상특허 및 가상실용권"), ("가상상표권", "가상상표권")]),
    ("가상전자·가상광학 지수", [("가상전자및가상광학", "가상전자·가상광학")]),
]
SHARED_HEAD = [
    ("가상카드표의 가상개인 이용건수와 가상법인 이용건수를 비교해줘", ["가상개인 이용건수", "가상법인 이용건수"]),
    ("가상수지표에서 가상기타서비스와 가상가공서비스 수지는 각각", ["가상기타서비스수지", "가상가공서비스수지"]),
    ("가상카드표 이용규모를 가상신용·가상체크·가상선불카드별로", ["가상신용카드", "가상체크카드"]),
]
EXCLUDE_RULES = [
    ("가상순채권(가상채권에서 가상채무를 뺀 것)은 얼마", []),
    ("가상주식(가상직접투자 제외 가상증권분) 잔액", ["가상직접투자"]),
    ("가상신선식품은 빼고 보여줘", ["가상신선식품"]),
    ("가상생산자물가 뺀 지수", ["가상생산자물가"]),
]
TAIL_Q = "가상경상수지와 가상상품·가상서비스·가상이전수지 추이"
BOUNDARY_Q = "가상은행 신규취급액 기준 가상고정대출과 가상변동대출 비중을 비교해줘"
EXCLUDE_ALL_Q = "가상식료품과 가상에너지를 뺀 가상생산자물가지수"
EXCLUDE_HINT_Q = "에너지를 뺀 소비자물가"
CX_Q = "가상지출을 북부와 남부로 비교해줘"
QG_Q = "서울과 부산의 가상지표갑을 월별로 비교해줘"

CASES = number_parts([
    _case(f"{S2}::test_list_chain_splits_on_middle_dot_and_wa_gwa[-]", "수도권·비수도권과 해외 매출을 보여줘", ["breakdown"],
          names("수도권·비수도권과 해외 매출을 보여줘", ["수도권", "비수도권", "해외"]), layer=None),
    _case(f"{S2}::test_list_chain_ignores_word_ending_in_gwa[-]", "물가 결과 보여줘", ["breakdown"], [], layer=None),
    _case(f"{S2}::test_exclude_makes_complement_hints[-]", EXCLUDE_HINT_Q, ["exclude"],
          [X(EXCLUDE_HINT_Q, 1, "에너지", "에너지를 뺀", ["에너지이외", "에너지제외", "에너지 제외"])], layer=None),
    *[_case(f"{Q5L}::test_measure_pair_is_marked_not_removed[{n}]", q, ["breakdown"],
            [{"id": "R1", "kind": "breakdown", "attrs": {"measure_pair": pair}},
             {"id": "R2", "kind": "breakdown", "attrs": {"measure_pair": pair}}])
      for n, (q, pair) in enumerate(MEASURE_PAIR, 1)],
    _case(f"{Q5L}::test_basis_boundary_word_is_not_a_modifier[-]", BOUNDARY_Q, ["breakdown"],
          [R(BOUNDARY_Q, 1, "가상고정대출", measure_pair=False), R(BOUNDARY_Q, 2, "가상변동대출", measure_pair=False)]),
    _case(f"{Q5L}::test_shared_tail_goes_to_earlier_names_in_same_table[-]", TAIL_Q, ["breakdown"],
          [R(TAIL_Q, 1, "가상경상수지", "가상경상수지", "list"), R(TAIL_Q, 2, "가상상품수지", "가상상품", "catalog_tail"),
           R(TAIL_Q, 3, "가상서비스수지", "가상서비스", "catalog_tail"), R(TAIL_Q, 4, "가상이전수지", "가상이전수지", "list")]),
    _case(f"{Q5L}::test_shared_tail_needs_same_table[-]", "가상상품·가상이전수지", ["breakdown"],
          names("가상상품·가상이전수지", ["가상상품", "가상이전수지"]), layer="q5_tail_split"),
    *[_case(f"{Q5L}::test_split_catalog_name_is_rejoined_with_original_quote[{n}]", q, ["breakdown"],
            [R(q, k, text, quote) for k, (text, quote) in enumerate(rows, 1)])
      for n, (q, rows) in enumerate(REJOINED, 1)],
    *[_case(f"{Q5L}::test_shared_head_word_expands_only_with_catalog_items[{n}]", q, ["breakdown"], names(q, values))
      for n, (q, values) in enumerate(SHARED_HEAD, 1)],
    _case(f"{Q5L}::test_cheoreom_tail_is_removed[-]", "가상원재료와 가상중간재처럼 나눠", ["breakdown"],
          names("가상원재료와 가상중간재처럼 나눠", ["가상원재료", "가상중간재"]), layer=None),
    _case(f"{Q5L}::test_i_ga_ending_kept_when_word_is_in_catalog[-]", "가상원재료와 가상생산자물가", ["breakdown"],
          names("가상원재료와 가상생산자물가", ["가상원재료", "가상생산자물가"]), layer="known_ppi"),
    _case(f"{Q5L}::test_i_ga_ending_kept_when_word_is_in_catalog[-]", "가상원재료와 가상생산자물가", ["breakdown"],
          names("가상원재료와 가상생산자물가", ["가상원재료", "가상생산자물"]), layer=None),
    _case(f"{Q5L}::test_time_unit_category_values_are_kept[-]", "가상기간금리를 1년과 3년으로 나눠 보여줘", ["breakdown"],
          names("가상기간금리를 1년과 3년으로 나눠 보여줘", ["1년", "3년"])),
    *[_case(f"{Q5L}::test_other_middle_dots_are_list_separators[-]", f"가상갑{dot}가상병 비교", ["breakdown"],
            names(f"가상갑{dot}가상병 비교", ["가상갑", "가상병"]), layer=None) for dot in ("ᆞ", "・")],
    _case(f"{Q5L}::test_exclude_list_takes_all_members[-]", EXCLUDE_ALL_Q, ["exclude", "breakdown"],
          [X(EXCLUDE_ALL_Q, 1, "가상식료품"), X(EXCLUDE_ALL_Q, 2, "가상에너지")]),
    _case(f"{Q5L}::test_exclude_does_not_take_a_modified_list_member[-]", "가상신선식품 가상생산자물가지수와 가상신선식품을 뺀 물가",
          ["exclude"], [X("가상신선식품 가상생산자물가지수와 가상신선식품을 뺀 물가", 1, "가상신선식품")]),
    *[_case(f"{Q5L}::test_exclude_rules[{n}]", q, ["exclude"], [X(q, k, v) for k, v in enumerate(values, 1)], layer=None)
      for n, (q, values) in enumerate(EXCLUDE_RULES, 1)],
    # 4-3 supplement: the Q5 list conditions that two graph tests read (rule path)
    _case(f"{CXP}::test_graph_evidence_changes_score_without_exact_binding_or_met_claim[1]", CX_Q, ["breakdown"],
          names(CX_Q, ["북부", "남부"]), layer="cx_prefix", param="False"),
    _case(f"{QG}::test_single_requirement_retains_breakdowns_and_common_pool[-]", QG_Q, ["breakdown"],
          names(QG_Q, ["서울", "부산"]), layer=None),
])


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return Layers(tmp_path_factory, CATALOGS)


@pytest.mark.parametrize("case", shape_params(CASES))
def test_case_shape(case):
    assert condition_shape(case, CATALOGS) == []


@pytest.mark.parametrize("case", behaviour_params(CASES))
def test_case_behaviour(case, layers):
    found = run_condition_case(case, layers)
    assert condition_problems(case, found) == []
