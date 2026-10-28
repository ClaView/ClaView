"""Period conditions (old Q2): specification moved from the old contract tests (ledger target Q2).

New design (detail 2.2): a period is found with its structure and is not resolved to dates. The old start/end dates
become ``start_expr``/``end_expr`` as written; ``relative`` marks a dependence on the reference date; the old status
is kept as ``extract_status`` with the meaning "resolvable once a reference date is known" (so it no longer depends
on whether an as_of was given). Rows whose old expectation held resolved dates are ledger "changed".
The finder is stretch 2: behaviour tests are xfail(NotImplementedError).
"""

from __future__ import annotations

import pytest
from spec_support import (Layers, behaviour_params, cond, condition_problems, condition_shape, ex, number_parts,
                          pattrs, run_condition_case, shape_params)

Q2 = "test_q2_periods_v2.py"
Q3 = "test_q3_frequency_v2.py"
S1 = "test_aflow_stage1_rules.py"
NODE = "find_period_conditions"


def _case(key: str, question: str, periods: list[tuple], param: str | None = None) -> dict:
    """periods: (quote, attrs or None for a quote-only row[, nth])."""
    conds = []
    for k, item in enumerate(periods, 1):
        quote, attrs = item[0], item[1]
        nth = item[2] if len(item) > 2 else 0
        conds.append(cond(question, f"P{k}", "period", quote, nth=nth, value=None, attrs=attrs))
    return {"key": key, "question": question, "kinds": ["period"], "node": NODE, "layer": None,
            "param": param if param is not None else question, "expect": {"conditions": conds}}


def _rows(function: str, rows: list[tuple], file: str = Q2) -> list[dict]:
    return [_case(f"{file}::{function}[{n}]", q, periods) for n, (q, periods) in enumerate(rows, 1)]


# ---- test_periods_v2: (question, [(quote, attrs)])
PERIODS_V2 = [
    ("2026년 1월부터 6월까지", [("2026년 1월부터 6월까지",
                          pattrs("range", ex("2026년 1월", "2026", unit="M", value="1"), ex("6월", unit="M", value="6")))]),
    ("2024년 1분기부터 4분기까지", [("2024년 1분기부터 4분기까지",
                            pattrs("range", ex("2024년 1분기", "2024", unit="Q", value="1"), ex("4분기", unit="Q", value="4")))]),
    ("2026년 상반기부터 하반기까지", [("2026년 상반기부터 하반기까지",
                             pattrs("range", ex("2026년 상반기", "2026", unit="H", value="상"), ex("하반기", unit="H", value="하")))]),
    ("2024년 상반기부터 2026년 상반기까지", [("2024년 상반기부터 2026년 상반기까지",
                                  pattrs("range", ex("2024년 상반기", "2024", unit="H", value="상"),
                                         ex("2026년 상반기", "2026", unit="H", value="상")))]),
    ("2025년 1분기말부터 2026년 1분기말까지", [("2025년 1분기말부터 2026년 1분기말까지",
                                    pattrs("range", ex("2025년 1분기말", "2025", unit="Q", value="1", end=True),
                                           ex("2026년 1분기말", "2026", unit="Q", value="1", end=True)))]),
    ("2024년부터 2026년 2분기까지", [("2024년부터 2026년 2분기까지",
                             pattrs("range", ex("2024년", "2024"), ex("2026년 2분기", "2026", unit="Q", value="2")))]),
    ("2026년 상반기 이후", [("2026년 상반기 이후", pattrs("since", ex("2026년 상반기", "2026", unit="H", value="상")))]),
    ("2026년 2분기말부터", [("2026년 2분기말부터", pattrs("since", ex("2026년 2분기말", "2026", unit="Q", value="2", end=True)))]),
    ("2026년 상반기 신용카드", [("2026년 상반기", pattrs("point", ex("2026년 상반기", "2026", unit="H", value="상")))]),
    ("2026년 하반기", [("2026년 하반기", pattrs("point", ex("2026년 하반기", "2026", unit="H", value="하")))]),
    ("2026년 2분기말 잔액", [("2026년 2분기말", pattrs("point", ex("2026년 2분기말", "2026", unit="Q", value="2", end=True)))]),
    ("2026년 2분기 말 가계", [("2026년 2분기 말", pattrs("point", ex("2026년 2분기 말", "2026", unit="Q", value="2", end=True)))]),
    ("2026년 7월말 잔액", [("2026년 7월말", pattrs("point", ex("2026년 7월말", "2026", unit="M", value="7", end=True)))]),
    ("2026년 6월 한 달간 M2", [("2026년 6월 한 달간", pattrs("point", ex("2026년 6월", "2026", unit="M", value="6")))]),
    ("작년부터 올해까지", [("작년부터 올해까지", pattrs("range", ex("작년", alias="작년"), ex("올해", alias="올해"), relative=True))]),
    ("올해 1월부터 6월까지", [("올해 1월부터 6월까지", pattrs("range", ex("올해 1월", alias="올해", unit="M", value="1"),
                                                ex("6월", unit="M", value="6"), relative=True))]),
    ("올해 원화 기준", [("올해", pattrs("since", ex("올해", alias="올해"), relative=True))]),
    ("금년 실적", [("금년", pattrs("since", ex("금년", alias="금년"), relative=True))]),
    ("올해 들어 주택", [("올해 들어", pattrs("since", ex("올해", alias="올해"), relative=True))]),
    ("올 들어 주택", [("올 들어", pattrs("since", ex("올", alias="올"), relative=True))]),
    ("작년 비금융법인", [("작년", pattrs("point", ex("작년", alias="작년"), relative=True))]),
    ("지난해 실적", [("지난해", pattrs("point", ex("지난해", alias="지난해"), relative=True))]),
    ("작년 한 해 은행", [("작년 한 해", pattrs("point", ex("작년", alias="작년"), relative=True))]),
    ("재작년 실적", [("재작년", pattrs("point", ex("재작년", alias="재작년"), relative=True))]),
    ("올해 2분기 실질 GDP", [("올해 2분기", pattrs("point", ex("올해 2분기", alias="올해", unit="Q", value="2"), relative=True))]),
    ("작년 3월 수출", [("작년 3월", pattrs("point", ex("작년 3월", alias="작년", unit="M", value="3"), relative=True))]),
    ("올해 상반기", [("올해 상반기", pattrs("point", ex("올해 상반기", alias="올해", unit="H", value="상"), relative=True))]),
    ("작년 하반기", [("작년 하반기", pattrs("point", ex("작년 하반기", alias="작년", unit="H", value="하"), relative=True))]),
    ("작년 말 기준 잔액", [("작년 말", pattrs("point", ex("작년 말", alias="작년", end=True), relative=True))]),
    ("최근 2년 동안 M1", [("최근 2년 동안", pattrs("recent", relative=True, amount=2, unit="year"))]),
    ("최근 6달", [("최근 6달", pattrs("recent", relative=True, amount=6, unit="month", status="unresolved"))]),
    ("최근 2분기", [("최근 2분기", pattrs("recent", relative=True, amount=2, unit="quarter", status="unresolved"))]),
    ("최근 1반기", [("최근 1반기", pattrs("recent", relative=True, amount=1, unit="half", status="unresolved"))]),
    ("지난달 실적", [("지난달", pattrs("point", ex("지난달", alias="지난", unit="M"), relative=True, status="unresolved"))]),
    ("지난 분기 명목 GDP", [("지난 분기", pattrs("point", ex("지난 분기", alias="지난", unit="Q"), relative=True,
                                           status="unresolved"))]),
    ("이번 달 금리", [("이번 달", pattrs("point", ex("이번 달", alias="이번", unit="M"), relative=True, status="unresolved"))]),
]

RANGE_JAN_JUN = ("2026년 1월부터 6월까지",
                 pattrs("range", ex("2026년 1월", "2026", unit="M", value="1"), ex("6월", unit="M", value="6")))
COMPARISON_BASIS = [
    ("2026년 1월부터 6월까지 철강 가격이 작년 같은 달보다 몇 % 올랐는지", [RANGE_JAN_JUN]),
    ("원유 수입 가격이 작년 같은 달보다 얼마나 오르내렸는지", []),
    ("2026년 1월부터 6월까지 반도체 생산자물가가 1년 전보다 몇 %", [RANGE_JAN_JUN]),
    ("올해 1분기에 가계대출 잔액이 직전 분기보다 얼마나 늘었어",
     [("올해 1분기", pattrs("point", ex("올해 1분기", alias="올해", unit="Q", value="1"), relative=True))]),
    ("2025년 분기마다 판매신용이 전분기말보다 얼마나", [("2025년", pattrs("point", ex("2025년", "2025")))]),
    ("2026년 7월 생산자물가는 전월보다 얼마나", [("2026년 7월", pattrs("point", ex("2026년 7월", "2026", unit="M", value="7")))]),
    ("수출물가 지수의 전년동월대비 상승률", []),
    ("작년 3월보다 늘었나", []),
    ("지난달에 비해 줄었나", []),
    ("지난해와 비교해 어때", []),
    ("올해보다 높았나", []),
    ("작년 같은 분기 대비", []),
    ("지난 분기 말보다", []),
]

STOCK_WORDS = [
    ("2024년 말잔 원계열 M2", [("2024년", pattrs("point", ex("2024년", "2024")))]),
    ("작년 말잔 기준", [("작년", pattrs("point", ex("작년", alias="작년"), relative=True))]),
    *[(q, []) for q in (
        "연말 잔액 기준 예금은행", "잔액이 분기말마다 어떻게", "월말 기준으로 매달", "현재가계부채 CSI와 가계부채전망 CSI 흐름",
        "현재경기판단 지수와 6개월 뒤를 내다본 향후경기전망 지수", "6개월 전과 비교해 지금 저축이 늘었다고",
        "향후 1년 기대인플레이션율", "2년 미만 정기예적금과 1년 만기 정기예탁금", "지난해외 직접투자",
        "은행에서 이번에 새로 받은 대출", "최근 본원통화 구성내역", "그동안 어떻게 늘어 왔는지")],
]

INVALID_UNITS = [
    ("2019년 13월", [("2019년 13월", pattrs("point", ex("2019년 13월", "2019", unit="M", value="13"), invalid=True,
                                         status="unresolved"))]),
    ("2026년 5분기", [("2026년 5분기", pattrs("point", ex("2026년 5분기", "2026", unit="Q", value="5"), invalid=True,
                                          status="unresolved"))]),
    ("2025년 12월부터 2월까지", [("2025년 12월부터 2월까지",
                           pattrs("range", ex("2025년 12월", "2025", unit="M", value="12"), ex("2월", unit="M", value="2"),
                                  status="unresolved"))]),
    ("2026년 1월부터 2분기까지", [("2026년 1월부터", pattrs("since", ex("2026년 1월", "2026", unit="M", value="1")))]),
]

STAGE1_PERIODS = [
    ("1999년부터 2003년까지", [("1999년부터 2003년까지", pattrs("range", ex("1999년", "1999"), ex("2003년", "2003")))]),
    ("1999~2003년", [("1999~2003년", pattrs("range", ex("1999", "1999"), ex("2003년", "2003")))]),
    ("2010년 3분기부터 2012년 1분기까지", [("2010년 3분기부터 2012년 1분기까지",
                                   pattrs("range", ex("2010년 3분기", "2010", unit="Q", value="3"),
                                          ex("2012년 1분기", "2012", unit="Q", value="1")))]),
    ("2011년 이후", [("2011년 이후", pattrs("since", ex("2011년", "2011")))]),
    ("최근 4년간", [("최근 4년간", pattrs("recent", relative=True, amount=4, unit="year"))]),
    ("최근 4년간", [("최근 4년간", pattrs("recent", relative=True, amount=4, unit="year"))]),
    ("최근 6개월", [("최근 6개월", pattrs("recent", relative=True, amount=6, unit="month", status="unresolved"))]),
    ("2019년 5월", [("2019년 5월", pattrs("point", ex("2019년 5월", "2019", unit="M", value="5")))]),
    ("2019년과 2021년", [("2019년", pattrs("point", ex("2019년", "2019"))),
                       ("2021년", pattrs("point", ex("2021년", "2021")))]),
]

TOGETHER = [
    ("최근 3년간 월별로 보여줘", ["최근 3년간"]), ("2020년부터 매년 수출액", ["2020년부터"]), ("2024년 연간 수출액", ["2024년"]),
    ("2025년 분기마다 판매신용", ["2025년"]), ("최근 1년간 분기마다 순자금운용", ["최근 1년간"]),
    ("최근 6개월 대출이 매달 얼마씩 늘었는지", ["최근 6개월"]),
    ("2024년 1분기부터 2025년 4분기까지 분기별 잔액", ["2024년 1분기부터 2025년 4분기까지"]),
    ("2026년 1월부터 6월까지 금리를 매달 알려줘", ["2026년 1월부터 6월까지"]),
]

SPACES = "  최근 2년  동안   작년 말  기준 가계대출 잔액을 보여줘"

CASES = number_parts([
    *_rows("test_periods_v2", PERIODS_V2),
    # as_of is no longer an input of the finder: the same expressions with and without a reference date
    *[_case(f"{Q2}::test_relative_without_as_of_keeps_span_and_stays_unresolved[-]", q, p) for q, p in (
        ("작년 비금융법인", [("작년", pattrs("point", ex("작년", alias="작년"), relative=True))]),
        ("올해 들어 주택", [("올해 들어", pattrs("since", ex("올해", alias="올해"), relative=True))]),
        ("작년부터 올해까지", [("작년부터 올해까지",
                         pattrs("range", ex("작년", alias="작년"), ex("올해", alias="올해"), relative=True))]),
        ("2026년 상반기", [("2026년 상반기", pattrs("point", ex("2026년 상반기", "2026", unit="H", value="상")))]),
    )],
    _case(f"{Q2}::test_year_alias_crosses_new_year[-]", "작년 말 잔액",
          [("작년 말", pattrs("point", ex("작년 말", alias="작년", end=True), relative=True))]),
    _case(f"{Q2}::test_existing_month_recent_contract_is_kept[-]", "최근 6개월",
          [("최근 6개월", pattrs("recent", relative=True, amount=6, unit="month", status="unresolved"))]),
    *_rows("test_comparison_basis_is_not_a_period", COMPARISON_BASIS),
    *_rows("test_stock_words_and_indicator_names_are_not_periods", STOCK_WORDS),
    *[_case(f"{Q2}::test_longest_candidate_wins_and_no_reentry[-]", q, p) for q, p in (
        ("재작년 실적", [("재작년", pattrs("point", ex("재작년", alias="재작년"), relative=True))]),
        ("2024년 상반기부터 2026년 상반기까지 이용건수", [("2024년 상반기부터 2026년 상반기까지",
                                              pattrs("range", ex("2024년 상반기", "2024", unit="H", value="상"),
                                                     ex("2026년 상반기", "2026", unit="H", value="상")))]),
        ("작년 같은 달보다", []),
    )],
    *_rows("test_invalid_units_are_not_silently_shrunk", INVALID_UNITS),
    _case(f"{Q2}::test_spaces_and_spans_map_back_to_question[-]", SPACES,
          [("최근 2년  동안", pattrs("recent", relative=True, amount=2, unit="year")),
           ("작년 말", pattrs("point", ex("작년 말", alias="작년", end=True), relative=True))]),
    *[_case(f"{Q3}::test_period_and_frequency_together[{n}]", q, [(p, None) for p in ps])
      for n, (q, ps) in enumerate(TOGETHER, 1)],
    _case(f"{Q3}::test_period_word_does_not_become_frequency_in_graph[-]", "최근 6개월간 업황 실적이 어땠는지 알려줘",
          [("최근 6개월간", None)]),
    *_rows("test_periods", STAGE1_PERIODS, file=S1),
    _case(f"{S1}::test_conditions_carry_spans_into_question[-]", "  2011년 이후   월별 가계대출 잔액을 보여줘",
          [("2011년 이후", pattrs("since", ex("2011년", "2011")))]),
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
