"""Requirements and condition attribution (old Q5 requirements, Q5 phrase, Q7 measure scopes): specification
moved from the old contract tests (ledger targets Q5, Q2, Q3, Q4).

New shape (detail 2.5-2.7): ``interpretation.requirements[]`` with own ``condition_ids``, ``shared_condition_ids``
(= ``interpretation.common_condition_ids``) and ``ambiguous_condition_ids``. An old "shared + requirement_unresolved
issue" condition is an ambiguous one. Old requirement texts that were glued from two places of the question
(shared tail, shared head) become an ``indicator``/``targets`` reference, because a requirement quote is one
contiguous part of the question (ledger "changed"). Attribution is looked up through the condition that covers the
marked words. The old attribution unit tests fed hand-placed conditions and cut points to ``measure_scopes``; the
new cases give the question only, so they are "changed". Nodes are stretch 2 (xfail).
"""

from __future__ import annotations

import pytest
from spec_catalogs import CATALOGS
from spec_support import (Layers, analysis_problems, analysis_shape, behaviour_params, catalog_problems, cond,
                          number_parts, old_measure_count, run_analysis, shape_params, span_of)

Q2 = "test_q2_periods_v2.py"
Q3 = "test_q3_frequency_v2.py"
Q4H = "test_q4_handoff_v1.py"
S2 = "test_aflow_stage2_rules.py"
S3 = "test_aflow_stage3_rules.py"
Q5L = "test_q5_lists_v1.py"
Q5P = "test_q5_phrase_v1.py"
Q7 = "test_q7_measure_scopes_v1.py"
SETTLE = "settle_requirements"
JUDGE = "judge_fragments"


def _case(key: str, question: str, expect: dict, layer: str | None = None, node: str = SETTLE,
          param: str | None = None) -> dict:
    return {"key": key, "question": question, "layer": layer, "node": node, "param": param or question,
            "expect": expect}


def req(question: str, quote: str | None = None, *, indicator: str | None = None, targets: list[str] = (),
        **fields) -> dict:
    out = dict(fields)
    if quote is not None:
        out["quote"], out["span"] = quote, span_of(question, quote)
    if indicator is not None:
        out["indicator"] = {"quote": indicator, "span": span_of(question, indicator)}
    if targets:
        out["targets"] = [{"quote": t, "span": span_of(question, t)} for t in targets]
    return out


def texts(question: str, quotes: list[str | None]) -> list[dict]:
    return [req(question, q) for q in quotes]


# ---------------------------------------------------------------- old graph cases

PAIR = "가상부문 가상예탁 금리와 가상계정상 가상예금 금리를 신규취급액 기준으로 보여줘"
PAIR_COMPARE = "가상부문 가상예탁 금리와 가상계정상 가상예금 금리 차이가 신규취급액 기준으로 어떻게 변했나"
DESIGNATED = [
    ("가상부문 가상예탁 금리와 가상계정상 가상예금 금리를 비교하되 가상예탁 금리만 신규취급액 기준으로", 0),
    ("가상부문 가상예탁 금리와 가상계정상 가상예금 금리를 비교하되 가상예금 금리에만 신규취급액 기준을 적용해줘", 1),
    ("가상부문 가상예탁 금리와 가상계정상 가상예금 금리를 각각 보여줘. 가상예금 금리는 신규취급액 기준으로", 1),
]
Q_TAIL = "2014년 기준과 2015년 기준 성장성 지표를 각각 보여줘"
Q_OWN = "2014년 가상매출액과 2015년 가상영업이익을 각각 보여줘"
Q_LIST = "2014년 기준과 2015년 기준 가상매출액 증가율과 가상영업이익률을 보여줘"
Q_HEAD = "가상은행 총대출금리와 총수신금리 차이는 얼마였어?"
Q_NET = "해외 가상근로자 임금과 국내 가상근로자 임금을 상계한 순수취액을 알려줘"
Q_PHRASE_1 = "가상현재 지수와 6개월 뒤를 내다본 가상전망 지수 흐름이 궁금해"
Q_PHRASE_2 = "가상현재 지수와 6개월 뒤를 내다본 다음 흐름이 궁금해"

GRAPH = [
    _case(f"{Q2}::test_relative_years_attach_to_their_own_requirement[-]", "올해 가계 기준과 작년 기업 기준 대출 잔액을 각각",
          {"conditions": [{"id": "P1", "kind": "period", "attrs": {"form": "since", "relative": True}},
                          {"id": "P2", "kind": "period", "attrs": {"form": "point", "relative": True}}],
           "requirement_count": 2, "attribution": [("올해", 0), ("작년", 1)]}, layer="units"),
    _case(f"{Q2}::test_relative_flag_reaches_b_handoff[-]", "작년 가계대출 잔액을 보여줘",
          {"conditions": [cond("작년 가계대출 잔액을 보여줘", "P1", "period", "작년", attrs={"relative": True})],
           "attribution": [("작년", "attached")]}, layer="units"),
    _case(f"{Q3}::test_repetition_word_reaches_condition_and_requirement[-]",
          "2026년 1월부터 6월까지 수출액이 매달 얼마였는지 알려줘",
          {"conditions": [cond("2026년 1월부터 6월까지 수출액이 매달 얼마였는지 알려줘", "F1", "frequency", "매달", value="M")],
           "attribution": [("매달", "attached")], "core_excludes": ["매달"]}, layer="units"),
    _case(f"{Q4H}::test_end_anchor_reaches_b_handoff_scope_and_stays_frequency[-]",
          "가상국민계정 주요지표 가상총소득을 2025년 분기말마다 보여줘",
          {"conditions": [{"id": "F1", "kind": "frequency", "attrs": {"value": "Q", "anchor": "end"}}],
           "attribution": [("분기말마다", "attached")]}, layer="q4_handoff"),
    _case(f"{S2}::test_q5_one_requirement_by_default[-]", "2019년과 2021년 가계대출·기업대출 잔액",
          {"conditions": [{"id": "R1", "kind": "breakdown", "value": "가계대출"},
                          {"id": "R2", "kind": "breakdown", "value": "기업대출"}],
           "requirement_count": 1, "requirements": [{"status": "ok"}],
           "attribution": [("2019년", "attached"), ("2021년", "attached"), ("가계대출", "attached"),
                           ("기업대출", "attached")]}, layer="units"),
    _case(f"{S2}::test_q5_splits_when_period_binds_part_of_sentence[-]", "2014년 가계 기준과 2016년 기업 기준 대출 잔액을 각각",
          {"requirement_count": 2, "attribution": [("2014년", 0), ("2016년", 1)], "requirement_starts": {1: "2016년"}},
          layer="units"),
    _case(f"{S2}::test_q5_ambiguous_attribution_is_unresolved[-]", "2010년 가계대출 대비 2020년 잔액",
          {"requirement_count": 1, "requirements": [{"status": "unresolved"}]}, layer="units"),
    _case(f"{S2}::test_q5_exclude_condition_has_span_into_question[-]", "가계대출을 뺀  대출 잔액",
          {"conditions": [cond("가계대출을 뺀  대출 잔액", "X1", "exclude", "가계대출을 뺀")],
           "attribution": [("가계대출을 뺀", "attached")]}, layer="units"),
    _case(f"{S3}::test_q5_splits_requirements_by_same_axis_values[-]", "가상통화 원계열과 계절조정계열을 각각",
          {"requirement_count": 2, "attribution": [("원계열", 0), ("계절조정", 1)]}, layer="stage3"),
    _case(f"{S3}::test_middle_clause_conditions_stay_in_their_clause[-]", "가상통화 2010년 말잔과 2015년 원계열 평잔과 2020년 월별",
          {"attribution": [("원계열", 1), ("평잔", 1), ("월별", "common")]}, layer="stage3"),
    _case(f"{S3}::test_repeated_basis_value_is_one_requirement[-]", "가상통화 명목, 실질, 명목 값",
          {"requirement_count": 2, "together": [[("명목", 0), ("명목", 1)]], "apart": [[("명목", 0), "실질"]]},
          layer="stage3"),
    _case(f"{S3}::test_two_split_axes_are_unresolved[-]", "가상통화 명목과 실질, 원계열과 계절조정",
          {"all_requirements": {"status": "unresolved"}}, layer="stage3"),
    _case(f"{Q5L}::test_classification_axes_do_not_split_requirements[-]", "가상차주대출 잔액을 연령별 지역별로 보여줘",
          {"requirement_count": 1, "attribution": [("연령별", "attached"), ("지역별", "attached")]}, layer="q5_lists"),
    _case(f"{Q5L}::test_value_axes_still_split[-]", "가상차주대출 원계열과 계절조정계열을 각각", {"requirement_count": 2},
          layer="q5_lists"),
    _case(f"{Q5P}::test_condition_only_requirement_shares_tail_measure[-]", Q_TAIL,
          {"requirement_count": 2, "attribution": [("2014년", 0), ("2015년", 1)], "common_condition_ids": [],
           "requirements": [req(Q_TAIL, indicator="성장성 지표"),
                            req(Q_TAIL, "2015년 기준 성장성 지표를 각각 보여줘", indicator="성장성 지표")]},
          layer="q5_phrase"),
    _case(f"{Q5P}::test_requirement_with_own_measure_keeps_its_phrase[-]", Q_OWN,
          {"requirements": texts(Q_OWN, ["2014년 가상매출액과", "2015년 가상영업이익을 각각 보여줘"]),
           "attribution": [("2014년", 0), ("2015년", 1)], "common_condition_ids": []}, layer="q5_phrase"),
    _case(f"{Q5P}::test_shared_tail_keeps_listing_conditions[-]", Q_LIST,
          {"requirement_count": 2, "attribution": [("2014년", 0), ("2015년", 1), ("가상매출액", "common"),
                                                   ("가상영업이익률", "common")]}, layer="q5_phrase"),
    _case(f"{Q5P}::test_bare_second_name_shares_head_with_catalog_evidence[-]", Q_HEAD,
          {"requirements": [req(Q_HEAD, "가상은행 총대출금리"), req(Q_HEAD, indicator="총수신금리", targets=["가상은행"])],
           "attribution": [("총대출금리", 0), ("총수신금리", 1)]}, layer="q5_phrase"),
    _case(f"{Q5P}::test_second_name_with_own_modifier_keeps_its_phrase[-]", "가상은행 총대출금리와 가상조합 총수신금리 차이는 얼마였어?",
          {"requirements": texts("가상은행 총대출금리와 가상조합 총수신금리 차이는 얼마였어?", [None, "가상조합 총수신금리 차이는"])},
          layer="q5_phrase"),
    _case(f"{Q5P}::test_no_catalog_evidence_no_shared_head[-]", "가상은행 총대출금리와 가상CD금리 차이는 얼마였어?",
          {"requirements": texts("가상은행 총대출금리와 가상CD금리 차이는 얼마였어?", [None, "가상CD금리 차이는"])},
          layer="q5_phrase"),
    _case(f"{Q5P}::test_adnominal_clause_continues_measure_phrase[-]", "가상소비자 현재판단 지수와 6개월 뒤를 내다본 향후전망 지수 흐름이 궁금해",
          {"requirements": texts("가상소비자 현재판단 지수와 6개월 뒤를 내다본 향후전망 지수 흐름이 궁금해",
                                 [None, "6개월 뒤를 내다본 향후전망 지수 흐름이"])}, layer="q5_phrase"),
    _case(f"{Q5P}::test_adnominal_without_name_does_not_extend[-]", "가상소비자 현재판단 지수와 향후전망 지수를 비교한 표를 보여줘",
          {"requirements": texts("가상소비자 현재판단 지수와 향후전망 지수를 비교한 표를 보여줘",
                                 ["가상소비자 현재판단 지수", "향후전망 지수를"])}, layer="q5_phrase"),
    _case(f"{Q5P}::test_phrase_end_rule_unit[-]", Q_PHRASE_1,
          {"requirements": texts(Q_PHRASE_1, [None, "6개월 뒤를 내다본 가상전망 지수 흐름이"])},
          layer="q5_phrase_known", node=JUDGE),
    _case(f"{Q5P}::test_phrase_end_rule_unit[-]", Q_PHRASE_2, {"requirements": texts(Q_PHRASE_2, [None, "6개월 뒤를"])},
          layer="q5_phrase_known", node=JUDGE),
    _case(f"{Q5P}::test_netting_two_phrases_is_one_requirement[-]", Q_NET,
          {"conditions": [{"id": "R1", "kind": "breakdown"}, {"id": "R2", "kind": "breakdown"}],
           "requirement_count": 1, "own_includes": {0: ["R1", "R2"]}}, layer="q5_phrase"),
    _case(f"{Q5P}::test_difference_of_two_phrases_still_splits[-]", "해외 가상근로자 임금과 국내 가상근로자 임금의 차이를 알려줘",
          {"requirements": texts("해외 가상근로자 임금과 국내 가상근로자 임금의 차이를 알려줘",
                                 ["해외 가상근로자 임금", "국내 가상근로자 임금의 차이를"])}, layer="q5_phrase"),
    *[_case(f"{Q5P}::test_combine_verbs_unit[{n}]", f"해외 가상수취 임금과 국내 가상지급 임금을 {verb} 순액을 알려줘",
            {"requirement_count": 1}, node=JUDGE, param=verb)
      for n, verb in enumerate(["상계한", "차감한", "합산한", "뺀"], 1)],
    _case(f"{Q7}::test_measure_pair_question_gets_one_requirement_per_measure[-]", PAIR,
          {"requirements": texts(PAIR, ["가상부문 가상예탁 금리", "가상계정상 가상예금 금리를"]),
           "attribution": [("신규취급액", "ambiguous")]}, layer="q7_scopes"),
    _case(f"{Q7}::test_handoff_scopes_carry_ambiguous_condition_with_mark[-]", PAIR,
          {"requirement_count": 2, "attribution": [("신규취급액", "ambiguous")]}, layer="q7_scopes"),
    _case(f"{Q7}::test_single_measure_question_keeps_condition_points[-]", "가상단일 지수를 신규취급액 기준으로 보여줘",
          {"requirement_count": 1, "attribution": [("신규취급액", "attached")]}, layer="q7_scopes"),
    _case(f"{Q7}::test_comparison_basis_is_common_and_keeps_points[-]", PAIR_COMPARE,
          {"requirement_count": 2, "attribution": [("신규취급액", "common")]}, layer="q7_scopes"),
    *[_case(f"{Q7}::test_designated_basis_goes_to_one_requirement[{n}]", q,
            {"requirement_count": 2, "attribution": [("신규취급액", owner)]}, layer="q7_scopes")
      for n, (q, owner) in enumerate(DESIGNATED, 1)],
    _case(f"{Q7}::test_ambiguous_frequency_is_not_unmet[-]", "가상부문 가상갑 금리와 가상계정상 가상병 금리를 비교하되 금리만 월별로 보여줘",
          {"requirement_count": 2, "attribution": [("월별", "ambiguous")]}, layer="q7_yearly"),
    _case(f"{Q7}::test_designated_frequency_conflict_stays_unmet[-]",
          "가상부문 가상갑 금리와 가상계정상 가상병 금리를 비교하되 가상병 금리만 월별로 보여줘",
          {"requirement_count": 2, "attribution": [("월별", 1)]}, layer="q7_yearly"),
]

# ---------------------------------------------------------------- old measure_scopes unit tests (marks -> question)

ATTRIBUTION = [
    ("2023년 가상수출액과 2024년 가상수입액을 비교해줘", {"P1": "2023년", "P2": "2024년"}, {"P1": 0, "P2": 1}),
    ("2024년부터 가상물가 전망과 가상생산 지수를 월별로 비교해줘", {"P1": "2024년부터", "F1": "월별"},
     {"P1": "common", "F1": "common"}),
    ("가상수출액과 2024년 가상수입액을 비교해줘", {"P1": "2024년"}, {"P1": 1}),
    ("월별 가상물가와 분기 가상생산을 비교해줘", {"F1": "월별", "F2": "분기"}, {"F1": 0, "F2": 1}),
    ("가상통화 평잔과 가상금리(신규취급액 기준)를 나란히", {"B1": "평잔", "B2": "신규취급액"}, {"B1": 0, "B2": 1}),
    ("가상예탁 금리와 가상예금 금리를 신규취급액 기준으로 보여줘", {"B1": "신규취급액"}, {"B1": "ambiguous"}),
    ("가상예탁 금리와 가상예금 금리를 신규취급액 기준으로 각각 보여줘", {"B1": "신규취급액"}, {"B1": "common"}),
    ("원화 기준 가상수입 물가와 가상생산 물가를 비교해줘", {"B1": "원화"}, {"B1": "ambiguous"}),
    ("원화 기준 가상수입 물가와 달러 기준 가상수출 물가를 비교해줘", {"B1": "원화", "B2": "달러"}, {"B1": 0, "B2": 1}),
    ("가상갑 지수와 2023년 가상병 지수와 2024년 가상정 지수를 비교해줘", {"P1": "2023년", "P2": "2024년"}, {"P1": 1, "P2": 2}),
    ("2024년 가상갑 지수와 가상병 지수와 가상정 지수를 비교해줘", {"P1": "2024년"}, {"P1": "common"}),
]
TAIL_BASIS = [
    ("가상A금리와 가상B금리의 차이를 신규취급액 기준으로 비교해줘", "common"),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 비교해줘", "common"),
    ("가상A금리와 가상B금리 차이가 신규취급액 기준으로 어떻게 변했나", "common"),
    ("가상A금리와 가상B금리가 얼마나 차이 나는지 신규취급액 기준으로 견줘줘", "common"),
    ("가상A금리와 가상B금리의 격차를 신규취급액 기준으로 보여줘", "common"),
    ("가상A금리와 가상B금리의 차이를 보여줘. 가상B금리는 신규취급액 기준으로", 1),
    ("가상A금리와 가상B금리를 비교하되 가상A금리는 신규취급액 기준으로", 0),
    ("가상A금리와 가상B금리의 차이를 보여줘. 신규취급액 기준으로", "ambiguous"),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 보여줘", "ambiguous"),
]
DESIGNATION = [
    ("가상A금리와 가상B금리를 비교하되 가상A금리만 신규취급액 기준으로", 0),
    ("가상A금리와 가상B금리를 비교하되 가상B금리에만 신규취급액 기준을 적용해줘", 1),
    ("가상A금리와 가상B금리를 각각 보여줘. 가상B금리는 신규취급액 기준으로", 1),
    ("가상A금리와 가상B금리를 각각 보여주되 가상B금리는 신규취급액 기준으로", 1),
    ("가상A금리와 가상B금리의 차이를 보여주되 신규취급액 기준은 가상A금리에만", 0),
    ("가상갑 예금 금리와 가상병 대출 금리를 비교하되 대출 금리만 신규취급액 기준으로", 1),
    ("가상갑 금리와 가상병 금리를 비교하되 금리만 신규취급액 기준으로", "ambiguous"),
    ("가상A금리와 가상B금리를 각각 보여줘. 신규취급액 기준으로", "ambiguous"),
    ("가상A금리와 가상B금리를 보여줘. 둘 다 신규취급액 기준으로 각각", "ambiguous"),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 각각 보여줘", "common"),
]
COUNTER = [
    ("가상A금리와 가상B금리를 비교하되 가상A금리는 잔액 기준으로 하고 신규취급액 기준은 가상B금리에만 적용해줘",
     {"B1": "잔액", "B2": "신규취급액"}, 2, {"B1": 0, "B2": 1}),
    ("가상A금리와 가상B금리를 비교하되 신규취급액 기준은 가상B금리에만 적용하고 잔액 기준은 가상A금리에만 적용해줘",
     {"B1": "신규취급액", "B2": "잔액"}, 2, {"B1": 1, "B2": 0}),
    ("가상A금리와 가상B금리와 가상C금리를 비교하되 가상A금리는 잔액 기준으로 하고 신규취급액 기준은 가상C금리에만 적용해줘",
     {"B1": "잔액", "B2": "신규취급액"}, 3, {"B1": 0, "B2": 2}),
    ("가상A금리와 가상B금리를 비교하되 가상A금리는 신규취급액 기준으로 하고 두 금리 모두 2024년부터 비교해줘",
     {"B1": "신규취급액", "P1": "2024년부터"}, 2, {"B1": 0, "P1": "common"}),
    ("가상A금리와 가상B금리를 비교하되 가상A금리는 잔액 기준으로 해줘. 신규취급액 기준은 가상B금리에만 적용해줘",
     {"B1": "잔액", "B2": "신규취급액"}, 2, {"B1": 0, "B2": 1}),
    ("가상A금리와 가상B금리를 비교하되 가상A금리는 잔액 기준, 가상B금리는 신규취급액 기준으로",
     {"B1": "잔액", "B2": "신규취급액"}, 2, {"B1": 0, "B2": 1}),
    ("가상A금리와 가상B금리의 차이를 보여주되 차이는 신규취급액 기준으로", {"B2": "신규취급액"}, 2, {"B2": "common"}),
    ("가상A금리(잔액 기준)와 가상B금리를 비교하되 기준은 신규취급액으로",
     {"B1": "잔액", "B2": "신규취급액"}, 2, {"B1": 0, "B2": "ambiguous"}),
    ("가상A금리와 가상B금리를 비교하되 신규취급액 기준은 가상A금리에만 적용하지 말고 가상B금리에만 적용해줘",
     {"B1": "신규취급액"}, 2, {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 비교하되 가상A금리만 빼고 신규취급액 기준으로 보여줘", {"B1": "신규취급액"}, 2,
     {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리와 가상C금리를 비교하되 가상A금리와 가상B금리에만 신규취급액 기준을 적용해줘",
     {"B1": "신규취급액"}, 3, {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리와 가상C금리를 보여주되 가상A금리와 가상B금리는 신규취급액 기준으로", {"B1": "신규취급액"}, 3,
     {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 비교하되 전자에만 신규취급액 기준을 적용해줘", {"B1": "신규취급액"}, 2, {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 비교하되 가상C금리만 신규취급액 기준으로", {"B1": "신규취급액"}, 2, {"B1": "ambiguous"}),
    ("가상갑 예금 금리와 가상병 대출 금리를 비교하되 대출금리만 신규취급액 기준으로", {"B1": "신규취급액"}, 2, {"B1": 1}),
    ("가상갑 예금 금리와 가상병 대출  금리를 비교하되 대출 금리만 신규취급액 기준으로", {"B1": "신규취급액"}, 2, {"B1": 1}),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 보여주고 설명은 모두 생략해줘", {"B1": "신규취급액"}, 2, {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 보여주고 제목과 출처는 각각 한 줄로 써줘", {"B1": "신규취급액"}, 2,
     {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 보여주고 그래프 색상은 작년 보고서와 비교해줘", {"B1": "신규취급액"}, 2,
     {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 보여주되 비교 설명은 생략해줘", {"B1": "신규취급액"}, 2, {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 신규취급액 기준으로 보여줘", {"B1": "신규취급액"}, 2, {"B1": "ambiguous"}),
    ("가상A금리와 가상B금리를 비교하되 신규취급액 기준으로", {"B1": "신규취급액"}, 2, {"B1": "common"}),
    ("가상A금리와 가상B금리의 차이가 0.5%p인지 신규취급액 기준으로 비교해줘", {"B1": "신규취급액"}, 2, {"B1": "common"}),
]
DIFFERENT_BASIS = [
    ("가상A금리 잔액 기준과 가상B금리를 신규취급액 기준으로 차이를 보여줘", {"B1": 0, "B2": 1}),
    ("가상A금리 잔액 기준과 가상B금리를 비교해서 신규취급액 기준으로 보여줘", {"B1": 0, "B2": "ambiguous"}),
]


def _marks_case(key: str, question: str, marks: dict, places: dict, measures: int | None = None,
                layer: str | None = None) -> dict:
    return _case(key, question, {"requirement_count": old_measure_count(question, measures),
                                 "attribution": [(marks[cid], places[cid]) for cid in places]}, layer=layer)


UNIT_CASES = [
    *[_marks_case(f"{Q7}::test_attribution_rules[{n}]", q, marks, places)
      for n, (q, marks, places) in enumerate(ATTRIBUTION, 1)],
    *[_marks_case(f"{Q7}::test_tail_basis_is_common_only_when_it_modifies_the_comparison[{n}]", q, {"B1": "신규취급액"},
                  {"B1": where}) for n, (q, where) in enumerate(TAIL_BASIS, 1)],
    *[_marks_case(f"{Q7}::test_explicit_designation_comes_first[{n}]", q, {"B1": "신규취급액"}, {"B1": where})
      for n, (q, where) in enumerate(DESIGNATION, 1)],
    *[_marks_case(f"{Q7}::test_independent_review_counter_examples[{n}]", q, marks, places, measures)
      for n, (q, marks, measures, places) in enumerate(COUNTER, 1)],
    _marks_case(f"{Q7}::test_unsure_designation_keeps_period_ambiguous[-]", "가상갑 금리와 가상병 금리를 비교하되 금리는 2024년부터",
                {"P1": "2024년부터"}, {"P1": "ambiguous"}),
    _marks_case(f"{Q7}::test_unsure_designation_keeps_period_ambiguous[-]", "가상갑 금리와 가상병 금리를 비교하되 가상병 금리는 2024년부터",
                {"P1": "2024년부터"}, {"P1": 1}),
    *[_marks_case(f"{Q7}::test_different_basis_per_measure_is_kept[{n}]", q, {"B1": "잔액", "B2": "신규취급액"}, places)
      for n, (q, places) in enumerate(DIFFERENT_BASIS, 1)],
    _marks_case(f"{Q7}::test_subject_particle_ends_phrase_only_for_unknown_words[-]",
                "가상갑 지수와 가상생산자물가 전년동월대비 상승률을 월별로 비교해줘", {"B1": "전년동월대비", "F1": "월별"},
                {"B1": 1, "F1": "common"}, layer="known_ppi"),
    # new 2.5 row 7: neither side has a catalog term, so the rule draft is undecided instead of "not split"
    _case(f"{Q7}::test_no_phrase_end_means_no_split[-]", "가상갑 지수와 가상병 지수", {"judgement": {"certain": False}},
          node=JUDGE),
]

CASES = number_parts([*GRAPH, *UNIT_CASES])


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


def _bare_analysis(statuses: list[str]) -> dict:
    reqs = [{"id": f"r{k}", "quote": "", "span": [0, 1], "condition_ids": [], "shared_condition_ids": [],
             "ambiguous_condition_ids": [], "status": s} for k, s in enumerate(statuses, 1)]
    return {"conditions": [], "fragments": [],
            "interpretation": {"status": "partial", "common_condition_ids": [], "requirements": reqs}}


@pytest.mark.parametrize("statuses, ok", [([], False), (["unresolved"], True), (["unresolved", "unresolved"], True),
                                          (["unresolved", "ok"], False)])
def test_all_requirements_expectation_needs_at_least_one_requirement(statuses, ok):
    """The comparison helper itself: "all requirements unresolved" is not met by an empty requirement list."""
    case = {"question": "가상통화 명목과 실질", "expect": {"all_requirements": {"status": "unresolved"}}}
    problems = analysis_problems(case, _bare_analysis(statuses))
    assert (problems == []) is ok, problems
    if not statuses:
        assert problems == ["all_requirements: no requirements"]
