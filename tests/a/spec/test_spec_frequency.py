"""Frequency conditions (old Q3): specification moved from the old contract tests (ledger target Q3).

These run now: the reused Q3 rules with the adapter ``find_frequency_conditions`` (new ``conditions`` shape).
Sentences are the synthetic examples of the old tests. No model, network or question file.
"""

from __future__ import annotations

import pytest
from spec_support import (Layers, behaviour_params, cond, condition_problems, condition_shape, number_parts,
                          run_condition_case, shape_params)

Q3 = "test_q3_frequency_v2.py"
S1 = "test_aflow_stage1_rules.py"
Q4H = "test_q4_handoff_v1.py"
NODE = "find_frequency_conditions"


def _case(key: str, question: str, conditions: list[dict], param: str | None = None) -> dict:
    return {"key": key, "question": question, "kinds": ["frequency"], "node": NODE, "layer": None,
            "param": param if param is not None else question, "expect": {"conditions": conditions}}


def _rows(file: str, function: str, rows: list[tuple]) -> list[dict]:
    """rows: (question, [(value, quote[, anchor])]) -> one case per parametrize row (1-based)."""
    out = []
    for n, (question, expected) in enumerate(rows, 1):
        conds = []
        for k, item in enumerate(expected, 1):
            value, quote = item[0], item[1]
            c = cond(question, f"F{k}", "frequency", quote, value=value)
            if len(item) > 2:
                c["attrs"] = {"value": value, "anchor": item[2]}
            conds.append(c)
        out.append(_case(f"{file}::{function}[{n}]", question, conds))
    return out


REPETITION = [
    ("수출액이 매달 얼마였는지", [("M", "매달")]),
    ("수출액이 달마다 얼마였는지", [("M", "달마다")]),
    ("수출액이 월마다 얼마였는지", [("M", "월마다")]),
    ("수출액을 다달이 보여줘", [("M", "다달이")]),
    ("잔액이 월말마다 어땠는지", [("M", "월말마다")]),
    ("분배율이 해마다 어떻게 변했는지", [("Y", "해마다")]),
    ("분배율이 매해 어떻게 변했는지", [("Y", "매해")]),
    ("분배율이 연도마다 어땠는지", [("Y", "연도마다")]),
    ("잔액이 연말마다 어땠는지", [("Y", "연말마다")]),
    ("성장률이 분기마다 어땠는지", [("Q", "분기마다")]),
    ("잔액이 분기말마다 어떻게 변했는지", [("Q", "분기말마다")]),
    ("성장률을 매분기 보여줘", [("Q", "매분기")]),
    ("수입이 반기마다 얼마였는지", [("H", "반기마다")]),
    ("수입을 매반기 보여줘", [("H", "매반기")]),
]

NOT_FREQUENCY = [
    "최근 6개월간 업황 실적이 어땠는지", "6개월간 업황 실적", "지난 분기 성장률은 얼마였어",
    "1분기 잔액이 직전 분기보다 얼마나 늘었어", "작년 같은 분기보다 얼마나 늘었어", "동분기 대비 증가율", "이번 분기 전망",
    "다음 분기 전망", "전 분기보다 얼마나 늘었어", "2026년 1분기 실질 GNI", "올해 2분기 성장률", "2026년 3월 소비자물가",
    "2025년 하반기 수출", "월말 잔액과 월평균 잔액", "2026년 7월말 잔액",
]

TOGETHER = [
    ("최근 3년간 월별로 보여줘", [("M", "월별")]),
    ("2020년부터 매년 수출액", [("Y", "매년")]),
    ("2024년 연간 수출액", [("Y", "연간")]),
    ("2025년 분기마다 판매신용", [("Q", "분기마다")]),
    ("최근 1년간 분기마다 순자금운용", [("Q", "분기마다")]),
    ("최근 6개월 대출이 매달 얼마씩 늘었는지", [("M", "매달")]),
    ("2024년 1분기부터 2025년 4분기까지 분기별 잔액", [("Q", "분기별")]),
    ("2026년 1월부터 6월까지 금리를 매달 알려줘", [("M", "매달")]),
]

VARIANTS = [
    ("연도별로", "Y"), ("년도별", "Y"), ("연별", "Y"), ("년별", "Y"), ("매 년", "Y"), ("매 해", "Y"),
    ("년마다", "Y"), ("1년마다", "Y"), ("한 해마다", "Y"), ("연 단위로", "Y"), ("년 단위로", "Y"),
    ("반기별로", "H"), ("매 반기", "H"), ("반년마다", "H"), ("6개월마다", "H"), ("반기 단위로", "H"),
    ("분기별로", "Q"), ("분기 별로", "Q"), ("매 분기", "Q"), ("3개월마다", "Q"), ("석 달마다", "Q"),
    ("세 달마다", "Q"), ("분기 단위로", "Q"),
    ("월별로", "M"), ("월 별로", "M"), ("월간으로", "M"), ("매 월", "M"), ("매 달", "M"), ("한 달마다", "M"),
    ("1개월마다", "M"), ("달별", "M"), ("월 단위로", "M"),
    ("해별", "Y"), ("년도마다", "Y"), ("매 연도", "Y"), ("연단위", "Y"), ("연도 단위로", "Y"), ("각 연도", "Y"),
    ("연도 별로", "Y"), ("연 주기로", "Y"), ("1년 주기로", "Y"), ("12개월마다", "Y"), ("일 년마다", "Y"),
    ("6개월 마다", "H"), ("여섯 달마다", "H"), ("6개월 단위로", "H"), ("6개월 주기로", "H"),
    ("3개월 마다", "Q"), ("삼개월마다", "Q"), ("3개월 단위로", "Q"), ("3개월 주기로", "Q"), ("쿼터별", "Q"),
    ("달 마다", "M"), ("월 마다", "M"), ("월단위", "M"), ("1개월 마다", "M"), ("한 달 단위로", "M"),
    ("월 주기로", "M"), ("각 월", "M"), ("각 달", "M"), ("달달이", "M"), ("1달 마다", "M"),
]

VARIANTS_NOT = [
    "산업마다 다르게 보여줘", "기관마다 금리", "매월요일 발표", "관련별 자료", "매출액과 매입액", "두 달마다 보여줘",
    "2년마다 보여줘", "2개월마다 보여줘", "5월 별로 보여줘", "연 단위 금리", "수출액을 일별로 보여줘", "수출액을 매주 보여줘",
    "2개월 단위로 보여줘", "두 해마다 보여줘", "각 달러 환율", "수출액을 매 달러로", "분기평균 금리", "분기점이 된 해",
    "당분기 실적", "수년간 이어진 흐름",
]

STAGE1_DICTIONARY = [
    ("수출액을 연간으로", ["Y"]), ("연도별 추이", ["Y"]), ("최근 3년간 월별로", ["M"]), ("반기별 실적", ["H"]),
    ("상반기 실적", []), ("분기별 성장률", ["Q"]), ("2023년 2분기 성장률", []), ("월말 잔액과 월평균 잔액", []),
    ("매월 그리고 매년", ["M", "Y"]),
]

PERIOD_END_REPEAT = [
    ("월말마다", ("M", "월말마다")), ("분기말마다", ("Q", "분기말마다")),
    ("반기말마다", ("H", "반기말마다")), ("연말마다", ("Y", "연말마다")),
    ("월말별로", ("M", "월말별로")), ("분기말별로", ("Q", "분기말별로")),
    ("반기말별로", ("H", "반기말별로")), ("연말별로", ("Y", "연말별로")),
    ("매 분기말", ("Q", "매 분기말")), ("매월말", ("M", "매월말")), ("매년 말", ("Y", "매년 말")),
    ("각 분기말", ("Q", "각 분기말")), ("각 반기말", ("H", "각 반기말")),
]

BARE_PERIOD_END = [
    ("분기말 대외채무", [("Q", "분기", "end")]), ("반기말 잔액", [("H", "반기", "end")]), ("월말 잔액", []),
    ("연말 잔액", []), ("1분기말 잔액", []), ("분기별로", [("Q", "분기별", None)]),
]

POINT_OR_COMPARISON_END = [
    "2025년 1분기말 잔액", "지난 분기말보다 늘었나", "전분기말보다", "직전 분기말 대비", "이번 분기말 잔액",
    "같은 분기말", "하반기말 잔액", "작년 연말보다",
]

LONGER = "수출액을 6개월마다 보여주고 3개월마다도 보여줘"

CASES = number_parts([
    *_rows(Q3, "test_repetition_words", REPETITION),
    *_rows(Q3, "test_not_frequency", [(q, []) for q in NOT_FREQUENCY]),
    *_rows(Q3, "test_period_and_frequency_together", TOGETHER),
    _case(f"{Q3}::test_no_frequency_word_stays_empty[-]", "2026년 1월부터 9월까지 소비자심리지수가 얼마였는지 알려줘", []),
    _case(f"{Q3}::test_repetition_word_reaches_condition_and_requirement[-]",
          "2026년 1월부터 6월까지 수출액이 매달 얼마였는지 알려줘",
          [cond("2026년 1월부터 6월까지 수출액이 매달 얼마였는지 알려줘", "F1", "frequency", "매달", value="M")]),
    _case(f"{Q3}::test_period_word_does_not_become_frequency_in_graph[-]", "최근 6개월간 업황 실적이 어땠는지 알려줘", []),
    *[_case(f"{Q3}::test_frequency_word_variants[{n}]", f"수출액을 {word} 보여줘",
            [{"id": "F1", "kind": "frequency", "value": value}], param=word)
      for n, (word, value) in enumerate(VARIANTS, 1)],
    *_rows(Q3, "test_variants_not_frequency", [(q, []) for q in VARIANTS_NOT]),
    _case(f"{Q3}::test_longer_word_wins_over_inner_unit[-]", LONGER,
          [cond(LONGER, "F1", "frequency", "6개월마다", value="H"), cond(LONGER, "F2", "frequency", "3개월마다", value="Q")]),
    *[_case(f"{S1}::test_frequency_dictionary[{n}]", q,
            [{"id": f"F{k}", "kind": "frequency", "value": v} for k, v in enumerate(values, 1)])
      for n, (q, values) in enumerate(STAGE1_DICTIONARY, 1)],
    _case(f"{S1}::test_conditions_carry_spans_into_question[-]", "  2011년 이후   월별 가계대출 잔액을 보여줘",
          [cond("  2011년 이후   월별 가계대출 잔액을 보여줘", "F1", "frequency", "월별", value="M")]),
    *[_case(f"{Q4H}::test_period_end_repeat_is_frequency_with_end_anchor[{n}]", text,
            [cond(text, "F1", "frequency", quote, value=value, attrs={"value": value, "anchor": "end"})])
      for n, (text, (value, quote)) in enumerate(PERIOD_END_REPEAT, 1)],
    *_rows(Q4H, "test_bare_period_end_keeps_previous_verdict", BARE_PERIOD_END),
    *_rows(Q4H, "test_point_or_comparison_period_end_is_not_a_frequency", [(q, []) for q in POINT_OR_COMPARISON_END]),
    _case(f"{Q4H}::test_end_anchor_reaches_b_handoff_scope_and_stays_frequency[-]",
          "가상국민계정 주요지표 가상총소득을 2025년 분기말마다 보여줘",
          [{"id": "F1", "kind": "frequency", "value": "Q", "attrs": {"value": "Q", "anchor": "end"}}]),
])


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return Layers(tmp_path_factory, {})


@pytest.mark.parametrize("case", shape_params(CASES))
def test_case_shape(case):
    assert condition_shape(case) == []


@pytest.mark.parametrize("case", behaviour_params(CASES))
def test_case_behaviour(case, layers):
    found = run_condition_case(case, layers)
    assert condition_problems(case, found) == []
