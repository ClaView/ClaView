"""Basis, calc and unit conditions (old Q4): specification moved from the old contract tests (ledger target Q4).

Old ``find_basis`` returned basis words and dictionary comparison words together; old ``find_handoff`` added
comparison phrases and "몇 %" with origin handoff_rule. In the new shape comparison and calculation requests are
kind ``calc`` (detail 2.2 "계산(전년 대비 등)"), so rows that held them are ledger "changed"; the other rows keep
their expectation. Old polarity pos/neg is "+"/"-". Finders are stretch 2: behaviour tests are xfail.
"""

from __future__ import annotations

import pytest
from spec_support import (Layers, basis, behaviour_params, cond, condition_problems, condition_shape, number_parts,
                          run_condition_case, shape_params)

S2 = "test_aflow_stage2_rules.py"
S3 = "test_aflow_stage3_rules.py"
Q4H = "test_q4_handoff_v1.py"
Q4A = "test_q4_axes_v1.py"
Q5L = "test_q5_lists_v1.py"
J4L = "test_j4_byeol_linked_v1.py"
TS = "test_aflow_title_signal.py"
BASIS_KINDS = ["basis", "calc"]  # what the old find_basis covered


def _conds(question: str, items: list[tuple]) -> list[dict]:
    """items: (kind, axis, value[, quote[, polarity[, origin]]]) in question order; IDs numbered per kind."""
    count: dict[str, int] = {}
    out = []
    for item in items:
        kind, axis, value = item[0], item[1], item[2]
        quote = item[3] if len(item) > 3 else None
        polarity = item[4] if len(item) > 4 else None
        origin = item[5] if len(item) > 5 else None
        count[kind] = count.get(kind, 0) + 1
        prefix = {"basis": "B", "calc": "K", "unit": "U"}[kind]
        attrs = {"value": value} if kind == "unit" else basis(axis, value, polarity, origin)
        out.append(cond(question, f"{prefix}{count[kind]}", kind, quote, value=value, attrs=attrs))
    return out


def _case(key: str, question: str, kinds: list[str], items: list[tuple], node: str | None = None,
          absent: list[dict] | None = None) -> dict:
    expect: dict = {"conditions": _conds(question, items)}
    if absent is not None:
        expect = {"absent": absent}
    return {"key": key, "question": question, "kinds": kinds, "layer": None, "param": question,
            "node": node or f"find_{kinds[0]}_conditions", "expect": expect}


def _rows(file: str, function: str, kinds: list[str], rows: list[tuple]) -> list[dict]:
    return [_case(f"{file}::{function}[{n}]", q, kinds, items) for n, (q, items) in enumerate(rows, 1)]


def B(axis, value, quote=None, polarity=None):  # noqa: N802  (short constructors for the tables)
    return ("basis", axis, value, quote, polarity)


def K(axis, value, quote=None, origin=None):  # noqa: N802
    return ("calc", axis, value, quote, None, origin)


def U(value, quote=None):  # noqa: N802
    return ("unit", None, value, quote)


FIND_BASIS = [
    ("통화(원계열, 말잔 기준)", [B("seasonal", "원계열"), B("stock", "말잔")]),
    ("실질 성장률(전년동기비)", [B("price", "실질"), K("comparison", "전년동기비", origin="dictionary")]),
    ("원화 기준으로 업권별로", [B("currency", "원화"), B("classification", "업권별")]),
    ("연도별 월별 잔액", []),
    ("금리 잔액 기준", [B("loan_basis", "잔액기준")]),
]

HANDOFF = "handoff_rule"
HANDOFF_RULES = [
    ("생산자물가는 1년 전보다 얼마나 올랐어", [K("comparison", "전년동기비", "1년 전보다", HANDOFF)]),
    ("한 해 전보다", []),
    ("작년 같은 달보다 얼마나", [K("comparison", "전년동기비", "작년 같은 달보다", HANDOFF)]),
    ("지난해 같은 분기 대비", [K("comparison", "전년동기비", "지난해 같은 분기 대비", HANDOFF)]),
    ("전년 동월 대비 상승률", [K("comparison", "전년동기비", "전년 동월 대비", HANDOFF)]),
    ("전년동월대비 상승률", [K("comparison", "전년동기비", "전년동월대비", HANDOFF)]),
    ("전년 대비 증가율", [K("comparison", "전년동기비", "전년 대비", HANDOFF)]),
    ("전년도에 비해 늘었나", [K("comparison", "전년동기비", "전년도에 비해", HANDOFF)]),
    ("1년 전과 비교해 늘었나", [K("comparison", "전년동기비", "1년 전과 비교해", HANDOFF)]),
    ("전월보다 얼마나 변했나", [K("comparison", "전기비", "전월보다", HANDOFF)]),
    ("전월대비 얼마나", [K("comparison", "전기비", "전월대비", HANDOFF)]),
    ("전분기 대비 늘었어", [K("comparison", "전기비", "전분기 대비", HANDOFF)]),
    ("전분기말보다 늘었는지", [K("comparison", "전기비", "전분기말보다", HANDOFF)]),
    ("직전 분기보다 얼마나", [K("comparison", "전기비", "직전 분기보다", HANDOFF)]),
    ("지난달보다 줄었나", [K("comparison", "전기비", "지난달보다", HANDOFF)]),
    ("한 달 전보다", [K("comparison", "전기비", "한 달 전보다", HANDOFF)]),
    ("몇 % 늘었어", [K("calc", "백분율", "몇 %", HANDOFF)]),
    ("몇 퍼센트 올랐어", [K("calc", "백분율", "몇 퍼센트", HANDOFF)]),
    ("전분기 대비 몇 % 늘었어", [K("comparison", "전기비", "전분기 대비", HANDOFF), K("calc", "백분율", "몇 %", HANDOFF)]),
]

NOT_HANDOFF = [
    ("GDP 대비 경상수지 비율", []), ("전국 대비 서울 비중", []), ("전월세 가격", []), ("전기전자 수출", []),
    ("전기요금 추이", []), ("1년 전 자료를 보여줘", []), ("최근 1년간 월별로", []),
    # the dictionary word is still a comparison: in the new shape it is a calc condition from the dictionary
    ("전년동기비", [K("comparison", "전년동기비", "전년동기비", "dictionary")]),
    ("금리 5% 이상인 대출 비중", []),
]

UNITS = [
    ("몇 % 늘었어", []), ("1년 전보다 몇 퍼센트 올랐어", []), ("금리 5% 이상인 대출", []), ("금리 5 % 이상", []),
    ("2~3%대, 3.5%대 같은 구간별로", []), ("% 단위로 보여줘", [U("%")]), ("퍼센트로 보여줘", [U("%")]),
    ("십억 원 단위와 %로", [U("십억원"), U("%")]),
]

CURRENCY = [
    ("1인당 국민총소득을 달러 기준으로", [B("currency", "달러")]),
    ("계약통화 기준 수입물가", [B("currency", "계약통화")]),
    ("원화 기준과 계약통화 기준으로 나눠서", [B("currency", "원화"), B("currency", "계약통화")]),
]

NEW_AXES = [
    ("기업경기조사 실적을 보여줘", [B("survey_view", "실적", "실적")]),
    ("업황 BSI 실적이 어땠는지", [B("survey_view", "실적", "실적")]),
    ("3분기 전망 기준 지수", [B("survey_view", "전망", "전망")]),
    ("기업경기조사 실적과 전망을 각각", [B("survey_view", "실적", "실적"), B("survey_view", "전망", "전망")]),
    ("채권 발행잔액을 시가 기준으로", [B("valuation", "시가", "시가")]),
    ("채권 발행잔액을 명목과 시가 기준으로", [B("price", "명목", "명목"), B("valuation", "시가", "시가")]),
    ("표본조사 성장성 지표", [B("survey_method", "표본조사", "표본조사")]),
    ("월말 잔액 기준의 통화", [B("stock", "말잔", "월말 잔액")]),
]

NOT_NEW_AXES = [
    "주택가격전망CSI를 보여줘", "가계부채전망 CSI 흐름", "향후경기전망 지수", "영업실적 추이", "도시가스 요금", "주식 시가총액",
    "시가지 개발", "시가배당률", "소비자동향조사를 보여줘", "대출수요지수(동향)를 보여줘", "가계대출 잔액을 보여줘",
    "분기말 잔액을 보여줘", "연말 잔액 증가율",
]

PERIOD_END_REPEAT = ["월말마다", "분기말마다", "반기말마다", "연말마다", "월말별로", "분기말별로", "반기말별로", "연말별로",
                     "매 분기말", "매월말", "매년 말", "각 분기말", "각 반기말"]

CASES = number_parts([
    *_rows(S3, "test_find_basis", BASIS_KINDS, FIND_BASIS),
    _case(f"{S3}::test_find_units[-]", "십억 원 단위와 %로", ["unit"], [U("십억원"), U("%")]),
    _case(f"{S3}::test_q4_negation_and_spans[-]", "가상물가를 품목별이 아니라 기본분류 기준으로", ["basis"],
          [B("classification", "품목별", "품목별", "-"), B("classification", "기본분류", "기본분류", "+")]),
    _case(f"{S3}::test_basis_does_not_cross_word_start[-]", "월말 잔액", BASIS_KINDS, [B("stock", "말잔", "월말 잔액")]),
    _case(f"{S3}::test_basis_does_not_cross_word_start[-]", "연말 잔고", BASIS_KINDS, []),
    _case(f"{S3}::test_q4_negation_hajianeun[-]", "가상통화 계절조정은 하지 않은 값", ["basis"],
          [B("seasonal", "계절조정", "계절조정", "-")]),
    _case(f"{S3}::test_negative_basis_item_is_kept_as_excluded_value[-]", "가상환율지수 계절조정하지 않은 값", ["basis"],
          [B("seasonal", "계절조정", "계절조정", "-")]),
    _case(f"{S3}::test_unit_unknown_without_meta_unit[-]", "가상신용 십억 원 단위", ["unit"], [U("십억원")]),
    *_rows(Q4H, "test_handoff_rules", ["calc"], HANDOFF_RULES),
    *_rows(Q4H, "test_not_handoff", ["calc"], NOT_HANDOFF),
    _case(f"{Q4H}::test_existing_comparison_forms_stay_in_find_basis[-]", "실질 성장률(전년동기비)", BASIS_KINDS,
          [B("price", "실질"), K("comparison", "전년동기비", origin="dictionary")]),
    *_rows(Q4H, "test_units_exclude_percent_values_and_requests", ["unit"], UNITS),
    *_rows(Q4H, "test_currency_values", BASIS_KINDS, CURRENCY),
    _case(f"{Q4H}::test_comparison_is_handoff_only[-]", "가상국민계정 주요지표 가상총소득이 1년 전보다 몇 % 늘었어",
          ["calc", "unit"], [K("comparison", "전년동기비", "1년 전보다", HANDOFF), K("calc", "백분율", "몇 %", HANDOFF)]),
    *_rows(Q4H, "test_period_end_repeat_is_frequency_with_end_anchor", BASIS_KINDS,
           [(text, []) for text in PERIOD_END_REPEAT]),
    _case(f"{Q4H}::test_end_anchor_reaches_b_handoff_scope_and_stays_frequency[-]",
          "가상국민계정 주요지표 가상총소득을 2025년 분기말마다 보여줘", ["basis"], [],
          absent=[{"kind": "basis", "attrs": {"axis": "stock"}}]),
    *_rows(Q4A, "test_new_axes", BASIS_KINDS, NEW_AXES),
    *_rows(Q4A, "test_not_new_axes", BASIS_KINDS, [(q, []) for q in NOT_NEW_AXES]),
    _case(f"{Q4A}::test_period_end_balance_rate_stays_loan_basis[-]", "연말 잔액 기준 예금은행 가계대출 금리", BASIS_KINDS,
          [B("loan_basis", "잔액기준", "잔액 기준")]),
    _case(f"{Q4A}::test_negated_market_value_keeps_polarity[-]", "채권 발행잔액은 시가가 아니라 명목 기준으로 보여줘",
          BASIS_KINDS, [B("valuation", "시가", "시가"), B("price", "명목", "명목")]),
    _case(f"{S2}::test_axis_skips_frequency_words_and_inner_byeol[-]", "업종별로 월별 특별회계", ["basis"],
          [B("classification", "업종별")]),
    _case(f"{Q5L}::test_classification_axes_do_not_split_requirements[-]", "가상차주대출 잔액을 연령별 지역별로 보여줘",
          ["basis"], [B("classification", "연령별"), B("classification", "지역별")]),
    _case(f"{J4L}::test_other_condition_words_are_conditions[-]", "가상 서비스 수출을 원계열, 계절조정 등 기업규모별로 보여줘",
          ["basis"], [B("seasonal", "원계열"), B("seasonal", "계절조정"), B("classification", "기업규모별")]),
    _case(f"{TS}::test_title_condition_counts_condition_written_in_title[-]", "가상대출을 지역별로 보여줘", BASIS_KINDS,
          [B("classification", "지역별")]),
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
