"""Core fragments (old Q7 core text and the measure phrase): specification moved from the old contract tests
(ledger targets 기타 (Q7), measure).

Old ``core_text`` built a search string without period, frequency and request words. In the new shape this is the
set of core fragments (detail 2.3); the cases compare the core fragment quotes joined with one space. The old measure
phrase also removed basis words and "X별"; the new fragments keep condition words inside a phrase (2.4), so only the
removal of period/frequency words and the original-text quote are kept (ledger "changed"). Stretch 2 (xfail).
"""

from __future__ import annotations

import pytest
from spec_catalogs import CATALOGS
from spec_support import (Layers, analysis_problems, analysis_shape, behaviour_params, catalog_problems, number_parts,
                          run_analysis, shape_params)

Q2 = "test_q2_periods_v2.py"
Q3 = "test_q3_frequency_v2.py"
S1 = "test_aflow_stage1_rules.py"
ME = "test_measure_evidence.py"
NODE = "split_fragments"


def _case(key: str, question: str, expect: dict, layer: str | None = None) -> dict:
    return {"key": key, "question": question, "layer": layer, "node": NODE, "param": question, "expect": expect}


CASES = number_parts([
    _case(f"{Q3}::test_core_text_drops_whole_repetition_word[-]", "가계가 국내주식을 분기마다 얼마나 순매입했는지",
          {"core_excludes": ["마다", "분기"]}),
    _case(f"{S1}::test_core_text_drops_conditions_and_requests[-]", "1999년부터 2003년까지 연간 수출 단가를 막대그래프로 보여줘.",
          {"core_equals": "수출 단가를"}),
    _case(f"{S1}::test_conditions_carry_spans_into_question[-]", "  2011년 이후   월별 가계대출 잔액을 보여줘",
          {"core_equals": "가계대출 잔액을"}, layer="units"),
    _case(f"{Q2}::test_core_text_drops_new_spans[-]", "2026년 1월부터 6월까지 반도체 생산자물가를 월별로 알려줘",
          {"core_startswith": "반도체 생산자물가를"}),
    _case(f"{Q2}::test_core_text_drops_new_spans[-]", "2024년 말잔 원계열 M2", {"core_equals": "말잔 원계열 M2"}),
    _case(f"{ME}::test_rule_spans_remove_conditions_and_preserve_original_locations[-]",
          "2024년  연간 실질 ＸＹＺ 증가율을 지역별로 보여줘",
          {"core_contains": ["ＸＹＺ 증가율"], "core_excludes": ["2024년", "연간"]}, layer="measure"),
])


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return Layers(tmp_path_factory, CATALOGS)


@pytest.mark.parametrize("case", shape_params(CASES))
def test_case_shape(case):
    assert catalog_problems(case, CATALOGS) + analysis_shape(case) == []


@pytest.mark.parametrize("case", behaviour_params(CASES))
def test_case_behaviour(case, layers):
    analysis = run_analysis(case, layers)
    assert analysis_problems(case, analysis) == []
