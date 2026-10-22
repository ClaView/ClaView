"""New specification cases of plan 04 (U0, M1): judgement table rows 1-10, certainty checks 5-7, name boundaries,
false-alarm guards (a right reading must not drop to undecided) and miss guards (a wrong reading must not be
certain). Cases follow the user-confirmed case table of 10/18 (J, C, N, F, M) plus the J3 extension (rows read
differently in different tables, with "및" and "그리고"); X, A and B cases were added by the implementer (miss guards,
the M1-3 review findings, the dev_mini count-miss patterns) and wait for the user's confirmation.

Every case runs on a synthetic catalog (made-up "가상" names; no real table, case or question). Expectations are
written on the 2.5 judgement (the 2.7 interpretation follows its drafts): the requirement draft count, the rows
used (pair rows and draft rows), certain or not, failed checks that must or must not be there, and a few reading
fields (breakdown, targets, broad, table groups, negation, terms, conditions). Shape tests check the case data; the
behaviour tests run interpret_question.
"""

from __future__ import annotations

import pytest
import json

from catalog_synth import write_inputs
from spec_catalogs import P09_JUDGEMENT
from spec_support import IMPLEMENTED, Layers, itm, run_analysis, table, write_catalog

from claview.a.catalog.layer import load_layer

from claview.a.contract import shapes

NODE = "judge_fragments"
FAILURES = set(shapes.CertaintyFailure.__args__)


def ax(obj: str, axis_name: str, values: list, order: int = 1) -> list[dict]:
    """Values of one axis: names, or (name, parent name) pairs."""
    ids: dict[str, str] = {}
    rows = []
    for k, v in enumerate(values, 1):
        name, parent = (v if isinstance(v, tuple) else (v, None))
        ids[name] = f"{obj}{k}"
        rows.append(itm(obj, ids[name], name, parent=ids.get(parent) if parent else None, obj_name=axis_name,
                        order=order))
    return rows


ACC = "계정항목별"  # generic axis: values outside the target dictionary make it an indicator axis
ORG = "기관코드별"  # target axis (layer 1)
IND = "업종코드별"  # target axis (layer 1)
BASE = "기준년도구분별"  # basis axis (layer 1)

CATALOGS: dict[str, list[dict]] = {
    # J1, J2, J2b, J7, J10, C5, C6, M7, N5: one table, 가상경상수지 above 가상상품수지 and 가상서비스수지
    "jt_bop": [table("T1", "가상국제수지표", ax("A", ACC, ["가상경상수지", ("가상상품수지", "가상경상수지"),
                                                       ("가상서비스수지", "가상경상수지")]))],
    # J3: upper/lower in T2, siblings in T3 (and the 및 / 그리고 forms)
    "jt_mixed": [
        table("T2", "가상지수표갑", ax("A", ACC, ["가상갑지수", ("가상을지수", "가상갑지수"), "가상A지출",
                                            ("가상B투자", "가상A지출"), ("가상C지출", "가상A지출")])),
        table("T3", "가상지수표을", ax("A", ACC, ["가상갑지수", "가상을지수", "가상A지출", "가상B투자", "가상C지출"])),
    ],
    # J4, J4b, F1, F6, F8: target axes
    "jt_target": [
        table("T4", "가상기업재무표", ax("I", IND, ["가상제조업", "가상건설업", "가상비제조업"])
              + ax("A", ACC, ["가상부채비율"], order=2)),
        table("T_EXP", "가상수출전망표", ax("O", ORG, ["가상갑기업", "가상을기업"]) + ax("A", ACC, ["가상수출전망"], order=2)),
        table("T_INST", "가상기관표", ax("O", ORG, ["가상금융법인", "가상한국은행"]) + ax("A", ACC, ["가상잔액값"], order=2)),
    ],
    # J5: a table name with two classification axes that do not split the family
    "jt_axes": [table("T5", "가상차주대출 잔액", ax("G", "연령별", ["가상20대", "가상30대"])
                      + ax("R", "지역별", ["가상서울", "가상부산"], order=2))],
    # J6, J6c, M6: basis values of one table
    "jt_basis": [table("T6", "가상GDP표", ax("A", ACC, ["가상GDP"]) + ax("P", BASE, ["명목", "실질"], order=2)
                       + ax("S", BASE, ["원계열", "계절조정"], order=3))],
    # J6b, F2, F3, F4: table families with bracket qualifiers
    "jt_family": [
        table("T_RAW", "가상통화량(원계열)", ax("A", ACC, ["가상M2"])),
        table("T_SA", "가상통화량(계절조정계열)", ax("A", ACC, ["가상M2"])),
        table("T_FORM", "가상대출금(가상기업형태별)", ax("A", ACC, ["가상시설자금"])),
        table("T_USE", "가상대출금(가상용도별)", ax("A", ACC, ["가상시설자금"])),
        table("T_TYPE", "가상무역통계(가상유형별)", ax("A", ACC, ["가상수출액"])),
        table("T_INDU", "가상무역통계(가상산업별)", ax("A", ACC, ["가상수출액"])),
        table("T_YEAR", "가상주요지표(연간지표)", ax("A", ACC, ["가상총소득"])),
        table("T_QTR", "가상주요지표(분기지표)", ax("A", ACC, ["가상총소득"])),
    ],
    # J8, J9: siblings of one table; two tables with nothing in common
    "jt_sib": [
        table("T7", "가상국외표", ax("A", ACC, ["가상국외자산순증", "가상국외부채순증"])),
        table("T8", "가상대외채무표", ax("A", ACC, ["가상대외채무"])),
        table("T9", "가상대외채권표", ax("A", ACC, ["가상대외채권"])),
    ],
    # M1, M2, M4, M5: indicators side by side (siblings)
    "jt_two": [
        table("T_A", "가상수지표둘", ax("A", ACC, ["가상경상수지", "가상상품수지", "가상소득수지"])),
        table("T_B", "가상지수모음", ax("A", ACC, ["가상갑지수", "가상을지수"])),
    ],
    # A-3: jt_bop with a target axis
    "jt_bop_org": [table("T1", "가상국제수지표", ax("A", ACC, ["가상경상수지", ("가상상품수지", "가상경상수지"),
                                                           ("가상서비스수지", "가상경상수지")])
                         + ax("O", ORG, ["가상은행", "가상조합"], order=2))],
    # B. patterns behind the dev_mini count misses (10/18), as synthetic catalogs
    "jt_b_bop": [table("T_SB", "가상국제수지B", ax("A", ACC, ["가상서비스수지", ("가상운송수지", "가상서비스수지"),
                                                        ("가상여행수지", "가상서비스수지"), ("가상건설수지", "가상서비스수지"),
                                                        ("가상가공서비스수지", "가상서비스수지")]))],
    "jt_b_loan": [table("T_BL", "가상예금은행 대출금(말잔)", ax("A", ACC, ["가상시설자금", "가상운전자금", "가상특별자금"]))],
    "jt_b_price": [
        table("T_BP1", "가상수입물가지수(가상용도별)", ax("A", ACC, ["가상원재료", "가상중간재"])),
        table("T_BP2", "가상국내공급물가지수", ax("A", ACC, ["가상원재료", "가상중간재", "가상최종재"])),
    ],
    "jt_b_axes": [
        table("T_BA1", "가상차주대출 잔액", ax("G", "연령별", ["가상20대"]) + ax("R", "지역별", ["가상서울"], order=2)),
        table("T_BA2", "가상연령별 가상지역별", ax("A", ACC, ["가상차주수"])),
    ],
    # R. second review (10/18): survey view, branches, out of scope
    "jt_view": [table("T_VA", "가상경기지수(실적)", ax("A", ACC, ["가상업황"])),
                table("T_VF", "가상경기지수(전망)", ax("A", ACC, ["가상업황"]))],
    "jt_bop_series": [
        table("T1", "가상국제수지표", ax("A", ACC, ["가상경상수지", ("가상상품수지", "가상경상수지"),
                                             ("가상서비스수지", "가상경상수지")])),
        table("T_S", "가상경상수지(가상계절조정)", ax("A", ACC, ["가상상품수지", "가상서비스수지"])),
    ],
    "jt_branch": [table("T_BR", "가상생산표", ax("A", ACC, ["가상생산지수1)", "가상생산지수2)"]))],
    "jt_oos": [table("T_OO", "가상고용표", ax("A", ACC, ["가상취업자수"]))],
    # C7, M3: the target 가상은행 only in the loan-rate table
    "jt_scope_one": [
        table("T_LR", "가상대출금리", ax("O", ORG, ["가상은행", "가상조합"])),
        table("T_DR", "가상예금금리", ax("O", ORG, ["가상조합"])),
    ],
    # C7n: 가상은행 in both tables
    "jt_scope_both": [
        table("T_LR", "가상대출금리", ax("O", ORG, ["가상은행", "가상조합"])),
        table("T_DR", "가상예금금리", ax("O", ORG, ["가상은행", "가상조합"])),
    ],
    # F5: "비중" is also a value name
    "jt_share": [
        table("T_PAY", "가상급료표", ax("A", ACC, ["가상급료수지", ("가상급료지급", "가상급료수지"),
                                            ("가상급료수입", "가상급료수지")])),
        table("T_SH", "가상구성표", ax("A", ACC, ["비중"])),
    ],
    # F7: two lower values of one upper value
    "jt_travel": [table("T_TR", "가상여행표", ax("A", ACC, ["가상여행지급", ("가상일반여행지급", "가상여행지급"),
                                                     ("가상유학연수지급", "가상여행지급")]))],
    # V. third review (10/18): four exact rates with a basis axis; basis-axis values with brackets; a spaced
    # out-of-scope entry; the basis and family catalogs together
    "jt_rates": [table("T_RT", "가상금리표", ax("A", ACC, ["가상A금리", "가상B금리", "가상C금리", "가상D금리"])
                       + ax("B", BASE, ["신규취급액", "잔액"], order=2))],
    "jt_price_basis": [table("T_PB", "가상GDP표", ax("A", ACC, ["가상GDP"])
                             + ax("P", BASE, ["가상가격기준(갑)", "가상가격기준(을)"], order=2))],
    "jt_oos_space": [table("T_OO", "가상고용표", ax("A", ACC, ["가상취업자수"]))],
    # N1-N4, N6: name boundaries
    "jt_names": [
        table("T_N1", "가상국", ax("A", ACC, ["가상값하나"])),
        table("T_N2", "가상시장표", ax("A", ACC, ["가상금리"])),
        table("T_N4", "가상재무비율(2007~2010)", ax("A", ACC, ["가상값둘"]), periods=(("년", "2007", "2010"),)),
        table("T_N6", "가상연간지표", ax("A", ACC, ["가상값셋"])),
    ],
    # P. the five decomposition patterns of the end-to-end check (10/18): an and-name, dividing verbs, institution
    # and axis words of one table family, basis copies with a shared qualifier, family-level indicator names
    "jt_p_and": [
        table("T_AN1", "가상기관 가상고정 및 가상변동대출 비중(신규취급액 기준)", ax("A", ACC, ["가상기업대출"])),
        table("T_AN2", "가상기관 가상고정 및 가상변동대출 비중(잔액 기준)", ax("A", ACC, ["가상기업대출"])),
        table("T_AN3", "가상총괄 가상경상수지 및 가상상품수지", ax("A", ACC, ["가상경상수지", "가상상품수지"])),
    ],
    "jt_p_divide": [
        table("T_DV1", "가상활동별 가상재형태별 가상투자(명목, 연간)", ax("M", "가상금액구분별", ["가상투자", "가상설비"])
              + ax("A", ACC, ["가상농업", "가상광업"], order=2)),
        table("T_DV2", "가상자산/가상자본 지표(가상분류)", ax("I", "가상업코드별", ["가상식료품", "가상화학"])
              + ax("S", "가상규모별", ["가상대기업", "가상중소기업"], order=2)
              + ax("A", ACC, ["가상자기자본비율"], order=3)),
        table("T_DV3", "가상재무표", ax("A", ACC, ["가상자산", "가상자본"])),
        table("T_DV4", "가상M2 가상주체별 보유현황(평잔, 원계열)", ax("A", ACC, ["가상M2(평잔 원계열)", "가상가계"])),
        table("T_DV5", "가상M2 가상주체별 보유현황(말잔, 원계열)", ax("A", ACC, ["가상M2(말잔 원계열)", "가상가계"])),
    ],
    "jt_p_family": [
        table("T_FM1", "가상대출금(가상기관, 가상규모별)", ax("A", ACC, ["가상대출금", "가상제조업"])),
        table("T_FM2", "가상대출금(가상기관, 가상형태별)", ax("A", ACC, ["가상대출금", "가상제조업"])),
        table("T_FM3", "가상대출금(가상비은행, 가상규모별)", ax("A", ACC, ["가상대출금", "가상제조업"])),
    ],
    "jt_p_copy": [
        table("T_CP1", "가상활동별 가상생산(계절조정, 명목, 분기)", ax("A", ACC, ["가상생산", "가상농업"])),
        table("T_CP2", "가상활동별 가상생산(계절조정, 실질, 분기)", ax("A", ACC, ["가상생산", "가상농업"])),
        table("T_CP3", "가상활동별 가상생산(원계열, 명목, 분기)", ax("A", ACC, ["가상생산", "가상농업"])),
        table("T_CP4", "가상차주 가상대출 신규취급액", ax("A", ACC, ["가상대출", "가상남성"])),
        table("T_CP5", "가상차주 가상대출 잔액", ax("A", ACC, ["가상대출", "가상남성"])),
        table("T_CP6", "가상수입물가표", ax("A", ACC, ["가상수입물가"])
              + ax("B", BASE, ["원화기준", "계약통화기준"], order=2)),
    ],
    "jt_p_name": [
        table("T_NM1", "가상통화표(말잔)", ax("A", ACC, ["가상M3(말잔 원계열)", "가상현금"])),
        table("T_NM2", "가상통화표(평잔)", ax("A", ACC, ["가상M3(평잔 원계열)", "가상현금"])),
        table("T_NM3", "가상자본수지표", ax("A", ACC, ["가상국외순증(F)", "가상국내순증"])),
    ],
    # G. split grounding (user decision 10/18 14:39): a piece without grounding reads in another requirement's table
    "jt_g": [
        table("T_G1", "가상기관 가상대출금리", ax("A", ACC, ["가상대출금리", "가상가계"])
              + ax("S", "가상규모별", ["가상대기업", "가상중소기업"], order=2)),
        table("T_G2", "가상심리지수", ax("A", ACC, ["가상심리지수"])
              + ax("B", BASE, ["가상계절조정", "가상원계열"], order=2)),
    ],
}
CATALOGS["jt_basis_family"] = CATALOGS["jt_basis"] + CATALOGS["jt_family"]
# plan 05 review round 2: an indicator with two classification axes next to other indicators
CATALOGS["jt_rw"] = CATALOGS["jt_axes"] + CATALOGS["jt_two"]
# plan 05 H: table names "<target value> <X-byeol> <head noun>" whose head noun alone names no value
CATALOGS["jt_h"] = [
    table("T_H1", "가상기관 지역별 가상수신(말잔)", ax("O", ORG, ["가상기관", "가상조합"])
          + ax("R", "지역별", ["가상서울", "가상부산"], order=2)),
    table("T_H2", "가상보험사 지역별 가상수신(말잔)", ax("O", ORG, ["가상보험사"])
          + ax("R", "지역별", ["가상서울", "가상부산"], order=2)),
]
# user decision 10/19, role rule: 가상비거주발행 / 가상거주발행 are values of an indicator axis (계정항목별) typed target
# by their siblings (가상조합, 가상협회: target values of a target axis); the table also holds the institutions
CATALOGS["jt_role"] = [
    table("T_RO1", "가상기관목록", ax("O", ORG, ["가상조합", "가상협회"])),
    table("T_RO2", "가상발행표", ax("O", ORG, ["가상은행", "가상갑법인", "가상을법인"])
          + ax("A", ACC, ["가상발행", ("가상비거주발행", "가상발행"), ("가상거주발행", "가상발행"), ("가상조합", "가상발행"),
                          ("가상협회", "가상발행"), "가상A값", "가상B값", "가상C값"], order=2)),
]
# user decision 10/19, coordinator: a table name that is also a value with the listed value as its sibling
CATALOGS["jt_coord"] = [
    table("T_CO1", "가상총지출", ax("A", ACC, ["가상총지출", "가상소비지출", "가상투자지출"])),
    table("T_CO2", "가상지출표", ax("A", ACC, ["가상소비지출", "가상투자지출"])),
]
# 10/19 detail fixes: possessive pairs, a condition inside an indicator name, a target written as a qualifier
CATALOGS["jt_pair"] = [table("T_PA", "가상기업표", ax("I", IND, ["가상대기업", "가상중소기업"])
                             + ax("A", ACC, ["가상연구비", "가상이자수익"], order=2))]
CATALOGS["jt_inner"] = [table("T_IN", "가상예금표", ax("A", ACC, ["가상예금 원화예금"])
                              + ax("S", BASE, ["평잔", "말잔"], order=2))]
CATALOGS["jt_gen"] = [
    table("T_GE1", "가상경영분석(가상전산업)", ax("I", IND, ["가상전산업", "가상서비스"]) + ax("A", ACC, ["가상인건비"], order=2)),
    table("T_GE2", "가상경영분석(가상제조)", ax("I", IND, ["가상제조"]) + ax("A", ACC, ["가상인건비"], order=2)),
]
# 10/19 second detail fixes: "X의 A, B와 C" with X a qualifier word; target-type values under a head
CATALOGS["jt_gen3"] = [
    table("T_G31", "가상분석표(가상전산업)", ax("I", IND, ["가상전산업", "가상서비스"])
          + ax("A", ACC, ["가상A율", "가상B율", "가상C율"], order=2)),
    table("T_G32", "가상분석표(가상제조)", ax("I", IND, ["가상제조"]) + ax("A", ACC, ["가상A율", "가상D율"], order=2)),
]
# 10/19: "N을 X별 A와 B로" with A and B values of the X-byeol axis of the table N
CATALOGS["jt_axisval"] = [table("T_AV", "가상수입물가지수", ax("U", "용도별", ["가상원재료", "가상중간재"]))]
# review r2-5: a lower value whose catalog name holds a bracket basis word
CATALOGS["jt_inner5"] = [table("T_I5", "가상수지표잔", ax("A", ACC, ["가상경상수지", ("가상상품수지(평잔)", "가상경상수지"),
                                                      ("가상서비스수지", "가상경상수지")]))]
CATALOGS["jt_lowtgt"] = [
    table("T_LT1", "가상기관목록", ax("O", ORG, ["가상조합", "가상협회"])),
    table("T_LT2", "가상신용표", ax("A", ACC, ["가상신용부문", ("가상보조부문", "가상신용부문"), ("가상연금부문", "가상신용부문"),
                                          ("가상조합", "가상신용부문"), ("가상협회", "가상신용부문"), "가상X값", "가상Y값",
                                          "가상Z값"])),
]
# plan 05 B: the family qualifiers 가상기관 / 가상비은행 are also target values of a table
CATALOGS["jt_b_targets"] = CATALOGS["jt_p_family"] + [
    table("T_FM4", "가상기관표", ax("O", ORG, ["가상기관", "가상비은행"]) + ax("A", ACC, ["가상잔액"], order=2))]
CATALOGS.update(P09_JUDGEMENT)  # plan 09 (spec_catalogs.py)
# plan 09 (10/19): a total header that is also a family qualifier, listed with values of its target axis
CATALOGS["jt_s_peer"] = [
    table("T_PE1", "가상경영표(가상전산업)", ax("I", IND, ["가상전산업", ("가상제조업", "가상전산업"),
                                                    ("가상비제조업", "가상전산업")]) + ax("A", ACC, ["가상판매비"], order=2)),
    table("T_PE2", "가상경영표(가상제조)", ax("I", IND, ["가상제조"]) + ax("A", ACC, ["가상판매비"], order=2)),
]
# user decision 10/19 21:45: values with bracket qualifiers ("(순)", "(-)") whose bracket-free form is shared
CATALOGS["jt_np"] = [table("T_NP1", "가상신용표", ax("A", ACC, ["가상국외신용(순)", "가상국외신용(-)", "가상국내신용"]))]

def _case(cid: str, question: str, layer: str, **expect) -> dict:
    return {"key": f"test_spec_judgement::{cid}", "id": cid, "question": question, "layer": layer, "node": NODE,
            "expect": expect}


CASES = [
    # ---- 1. judgement table rows (2-2)
    _case("J1", "2020년 가상경상수지와 2020년 가상경상수지를 보여줘", "jt_bop", count=1, rows=[1], certain=True),
    _case("J2", "가상경상수지와 가상상품수지·가상서비스수지를 보여줘", "jt_bop", count=1, rows=[2], certain=True,
          breakdown={0: ["가상상품수지", "가상서비스수지"]}),
    _case("J2b", "가상경상수지와 2021년 가상상품수지를 보여줘", "jt_bop", count=2, rows=[7], not_rows=[2]),
    _case("J3", "가상갑지수와 가상을지수를 보여줘", "jt_mixed", rows=[3], certain=False, failed=["mixed_reading"]),
    _case("J3-및", "가상A지출 및 가상B투자를 보여줘", "jt_mixed", rows=[3], certain=False, failed=["mixed_reading"]),
    _case("J3-그리고", "가상A지출 그리고 가상C지출을 보여줘", "jt_mixed", rows=[3], certain=False,
          failed=["mixed_reading"]),
    _case("J4", "가상제조업과 가상건설업의 가상부채비율을 보여줘", "jt_target", count=1, rows=[4], certain=True,
          targets={0: ["가상제조업", "가상건설업"]}),
    _case("J4b", "가상제조업과 가상건설업을 보여줘", "jt_target", count=1, rows=[4], broad={0: True},
          targets={0: ["가상제조업", "가상건설업"]}),
    _case("J5", "가상차주대출 잔액을 연령별 지역별로 보여줘", "jt_axes", count=1, rows=[5], certain=True,
          breakdown={0: ["연령별", "지역별"]}),
    _case("J6", "가상GDP를 명목과 실질로 보여줘", "jt_basis", count=2, rows=[6], certain=True, together=[[0, 1]]),
    _case("J6b", "가상통화량 원계열과 계절조정계열을 각각 보여줘", "jt_family", count=2, rows=[6], apart=[[0, 1]]),
    _case("J6c", "가상GDP를 명목과 원계열로 보여줘", "jt_basis", certain=False, failed=["row_2_or_7"],
          status={0: "unresolved"}),
    _case("J7", "2020년 가상경상수지와 2021년 가상경상수지를 보여줘", "jt_bop", count=2, rows=[7]),
    _case("J8", "가상국외자산순증과 가상국외부채순증을 보여줘", "jt_sib", count=2, rows=[8], together=[[0, 1]]),
    _case("J9", "가상대외채무와 가상대외채권을 보여줘", "jt_sib", count=2, rows=[9], apart=[[0, 1]]),
    _case("J10", "가상경상수지와 모의블루칩지수를 보여줘", "jt_bop", rows=[10], certain=False, failed=["row_2_or_7"]),
    # ---- 2. certainty checks 5-7 (2-3)
    _case("C5", "가상경상수지 속사정을 보여줘", "jt_bop", certain=False, failed=["leftover_word"], leftover=["속사정을"]),
    _case("C5n", "가상경상수지를 보여줘", "jt_bop", count=1, certain=True),
    _case("C5p", "가상경상수지 추세분석해줘", "jt_bop", certain=False, failed=["leftover_word"]),
    _case("C6", "가상경상수지를 보여주고 가상상품수지는 빼지 말고", "jt_bop", certain=False,
          failed_any=["unused_name", "leftover_word"]),
    _case("C7", "가상은행의 가상대출금리와 가상예금금리를 보여줘", "jt_scope_one", certain=False,
          failed=["scope_ambiguous"]),
    _case("C7n", "가상은행의 가상대출금리와 가상예금금리를 보여줘", "jt_scope_both", count=2, not_failed=["scope_ambiguous"],
          targets={0: ["가상은행"], 1: ["가상은행"]}),
    # ---- 3. name boundaries (2-5, 2.2)
    _case("N1", "가상국민소득을 보여줘", "jt_names", terms_absent=["가상국"], certain=False),
    _case("N2", "가상 금리차를 보여줘", "jt_names", terms_absent=["가상금리"], certain=False),
    _case("N3", "가상 금리를 보여줘", "jt_names", terms_present=["가상금리"], count=1),
    _case("N4", "2008년 가상재무비율을 보여줘", "jt_names", terms_present=["가상재무비율"],
          conditions=[("period", "2008년", None)], count=1, certain=True),
    _case("N5", "연간 가상경상수지를 보여줘", "jt_bop", conditions=[("frequency", "연간", "Y")],
          conditions_absent=["basis"], count=1, certain=True),
    _case("N6", "가상연간지표를 보여줘", "jt_names", conditions_absent=["frequency"], terms_present=["가상연간지표"],
          count=1, certain=True),
    # ---- 4-1. a right reading must not drop to undecided (false-alarm guards)
    _case("F1", "가상제조업과 가상비제조업 각각의 가상부채비율을 보여줘", "jt_target", count=1, certain=True,
          targets={0: ["가상제조업", "가상비제조업"]}),
    _case("F2", "가상대출금을 가상기업형태별과 가상용도별로 각각 보여줘", "jt_family", count=2, rows=[6], certain=True),
    _case("F3", "가상무역통계를 가상유형별이 아니라 가상산업별로 보여줘", "jt_family", count=1, certain=True,
          negated=["가상유형별"]),
    _case("F4", "가상주요지표를 연간지표가 아니라 분기지표로 보여줘", "jt_family", count=1, certain=True,
          negated=["연간지표"]),
    _case("F5", "가상급료수지 중 가상급료지급이 차지하는 비중을 보여줘", "jt_share", not_failed=["unused_name"]),
    _case("F6", "가상금융법인과 가상한국은행을 알려줘", "jt_target", count=1, broad={0: True}, not_failed=["unused_name"]),
    _case("F7", "가상여행지급의 가상일반여행지급과 가상유학연수지급을 보여줘", "jt_travel", count=1,
          not_failed=["scope_ambiguous"], breakdown={0: ["가상일반여행지급", "가상유학연수지급"]}),
    _case("F8", "가상갑기업과 가상을기업 각각의 가상수출전망을 보여줘", "jt_target", count=1,
          not_failed=["scope_ambiguous"], targets={0: ["가상갑기업", "가상을기업"]}),
    # ---- 4-2. a wrong reading must not be certain (miss guards)
    _case("M1", "가상경상수지가 전년보다 얼마나 늘었는지 가상상품수지와 같이 보여줘", "jt_two", count=2,
          not_failed=["leftover_word"], calc_on=0, hints_on={"늘었": 0}),
    _case("M2", "가상경상수지의 전년 대비 증가율과 가상상품수지를 보여줘", "jt_two", count=2, calc_on=0),
    _case("M3", "가상은행의 가상대출금리와 가상예금금리", "jt_scope_one", certain=False, failed=["scope_ambiguous"]),
    _case("M4", "가상경상수지 흐름을 가상소득수지와 엮어서 보여줘", "jt_two", certain=False, failed=["leftover_word"]),
    _case("M5", "가상갑지수와 가상을지수를 합친 값", "jt_two", count=2, certain=True, relation="sum"),
    _case("M6", "가상GDP 명목과 실질, 원계열과 계절조정", "jt_basis", certain=False, failed=["row_2_or_7"],
          status={0: "unresolved"}),
    _case("M7", "2020년 가상경상수지와 가상상품수지", "jt_bop", count=1, rows=[2], common=["2020년"],
          breakdown={0: ["가상상품수지"]}),
    # ---- X. added by the implementer (not in the confirmed case table; to be confirmed): miss guards of rules
    # written while measuring dev_mini
    _case("X1", "가상경상수지와 가상상품수지를 보여줘. 가상상품수지는 2020년부터", "jt_two", count=2,
          owner={"2020년부터": 1}),  # a later sentence naming an item designates it (2-4 rules 1 and 8), no new item
    _case("X2", "2015년 기준 가상경상수지를 보여줘", "jt_bop", certain=False, failed=["leftover_word"]),  # base year
    _case("X3", "가상은행의 가상예금금리를 보여줘", "jt_scope_one", certain=False, failed=["scope_ambiguous"]),
    _case("X4", "가상경상수지를 가상갑지수와 가상을지수로 나눠 보여줘", "jt_two", certain=False, failed=["mixed_reading"]),
    _case("X5", "가상제조업과 가상부채비율과 가상수출전망을 보여줘", "jt_target", certain=False,
          failed=["scope_ambiguous"]),  # a target no table of one indicator holds
    # ---- B. patterns of the dev_mini count misses (one requirement each; read from words, so not certain)
    _case("B1", "가상서비스수지와 가상가공·가상운송·가상여행·가상건설수지를 보여줘", "jt_b_bop", count=1, rows=[2],
          certain=False),  # "X와 a·b·c수지": the list is X's breakdown; an unnamed member keeps it undecided
    _case("B2", "가상예금은행 대출금을 가상시설자금·가상운전자금·가상특별자금 가상용도별로 나눠 월별로 보여줘", "jt_b_loan",
          count=1, rows=[2], breakdown={0: ["가상시설자금", "가상운전자금", "가상특별자금", "가상용도별"]}),
    _case("B3", "가상수입 가상원재료와 가상중간재의 물가 흐름을 보여줘", "jt_b_price", count=1, rows=[2], certain=False),
    _case("B4", "가상차주대출 잔액을 가상연령별 가상지역별로 보여줘", "jt_b_axes", count=1, rows=[5],
          breakdown={0: ["가상연령별 가상지역별"]}, not_failed=["unused_name"]),  # a name of X-byeol words: axes
    _case("B5", "가상차주대출 잔액이 얼마인지 나이와 사는 곳을 함께 나눠 보여줘", "jt_b_axes", count=1, rows=[5],
          certain=False),  # "A와 B를 나눠": breakdown axes read from words
    # ---- A. review findings of M1-3 (Codex, 10/18), each locked as a case
    _case("A1", "가상경상수지 대비 가상상품수지 비율을 보여줘", "jt_bop", count=2, relation="ratio",
          not_rows=[2]),  # a relation word comes before row 2
    _case("A2", "가상여행지급의 2020년 가상일반여행지급과 2021년 가상유학연수지급을 보여줘", "jt_travel", count=2,
          not_rows=[2]),  # lower items with their own periods are no breakdown of one requirement
    _case("A3", "가상은행 가상경상수지와 가상조합 가상상품수지를 보여줘", "jt_bop_org", count=2, not_rows=[2],
          targets={0: ["가상은행"], 1: ["가상조합"]}, not_failed=["unused_name"]),  # targets stay with their items
    _case("A4", "가상GDP 명목 상위 5개와 명목 하위 5개를 보여줘", "jt_basis", count=2),  # copies compare calc too
    _case("A5", "가상GDP를 연간과 분기로 각각 보여줘", "jt_basis", count=2, rows=[6]),  # copies by frequency
    _case("A6", "가상GDP의 증가율을 명목과 실질로 보여줘", "jt_basis", count=2, hints_all={"증가율": [0, 1]}),
    _case("A7", "가상대출금을 가상기업형태별과 가상용도별로 각각 보여줘", "jt_family", count=2,
          own_quotes={0: ["가상기업형태별"], 1: ["가상용도별"]}),  # family qualifiers are condition IDs
    # ---- R. second review findings (Codex xhigh, 10/18): wrong-certain paths read apart or left undecided
    _case("R1", "가상경상수지를 가상상품수지로 나눈 비율을 보여줘", "jt_bop", count=2, relation="ratio",
          not_rows=[2]),  # "A를 B로 나눈": a relation inside one item is no breakdown
    _case("R1b", "가상경상수지를 가상상품수지로 나눈 값을 보여줘", "jt_bop", count=2, not_rows=[2]),
    _case("R2", "2020년 가상경상수지를 보여주고 2021년 가상경상수지를 보여줘", "jt_bop", count=2),
    _case("R2b", "가상경상수지와 가상상품수지를 보여주고 가상상품수지는 2021년부터 보여줘", "jt_two", count=2,
          owner={"2021년부터": 1}),  # a designation clause names an item of the first clause: no new item
    _case("R3", "가상GDP 명목 상위 5개와 명목 상위 10개를 보여줘", "jt_basis", count=2),  # calc values compared
    _case("R4", "가상차주대출 잔액과 가상연령별 가상지역별을 보여줘", "jt_b_axes", count=2,
          not_rows=[5]),  # a listed table name of X-byeol words stays a name
    _case("R4b", "가상차주대출 잔액을 가상연령별 가상지역별로 보여줘", "jt_b_axes", count=1, rows=[5],
          certain=False),  # read as axes from a name: not certain
    _case("R5", "가상A금리(잔액 기준)와 가상B금리를 비교하되 차이는 신규취급액 기준으로", "jt_two", count=2,
          ambiguous=["신규취급액"]),  # the conflict check comes before a relation designation
    _case("R6", "가상GDP 전망을 보여줘", "jt_basis", certain=False, failed=["ambiguous_word"]),
    _case("R6n", "가상경기지수 전망을 보여줘", "jt_view", count=1, not_failed=["ambiguous_word"]),
    _case("R7", "가상생산지수를 보여줘", "jt_branch", count=1, interp_status="ask"),  # two official names: a branch
    _case("R8", "가상금융법인과 가상한국은행을 알려줘", "jt_target", count=1,
          interp={0: {"indicator": None, "targets": 2}}),  # a broad question keeps its targets as targets
    _case("R9", "가상국제수지표와 가상경상수지를 보여줘", "jt_bop", count=1,
          interp={0: {"indicator": "가상경상수지", "context": "가상국제수지표"}}),  # table name + its value
    _case("R9b", "가상경상수지와 가상상품수지·가상서비스수지를 보여줘", "jt_bop_series", count=1, rows=[2],
          breakdown={0: ["가상상품수지", "가상서비스수지"]}),  # an upper value that also names a table stays the head
    _case("R10", "가상실업률을 보여줘", "jt_oos", count=1, interp_status="unsupported",
          interp={0: {"status": "unsupported"}}),  # an out-of-scope term reaches 2.5 and 2.7
    _case("R11", "가상경상수지와 가상상품수지를 보여주고 가상갑지수와 가상을지수를 비교해줘", "jt_two", count=4,
          interp_relations=[("compare", [2, 3])]),  # a comparison word of a later clause stays in its clause
    # ---- V. third review findings (Codex xhigh, 10/18): each reads apart correctly or stays undecided
    _case("V1", "가상A금리와 가상B금리를 보여주고 가상C금리와 가상D금리를 신규취급액 기준으로 각각 보여줘", "jt_rates",
          count=4, owner={"신규취급액": 2}, own_quotes={2: ["신규취급액"], 3: ["신규취급액"]}),  # its item clause only
    _case("V1b", "가상A금리와 가상B금리를 신규취급액 기준으로 보여주고 가상C금리와 가상D금리를 각각 보여줘", "jt_rates",
          count=4, ambiguous=["신규취급액"], certain=False),  # not the last item of the first clause alone
    _case("V2", "가상GDP를 보여주고 가상GDP는 명목과 실질로 각각 보여줘", "jt_basis", certain=False,
          failed=["ambiguous_attribution"]),  # a designation clause that would split the requirement
    _case("V2b", "2020년 가상경상수지를 보여주고 2021년 가상경상수지는 보여줘", "jt_bop", certain=False,
          failed=["ambiguous_attribution"]),  # a designation clause with a period of its own
    _case("V3", "가상GDP를 연간과 분기와 월간으로 각각 보여줘", "jt_basis", count=3, rows=[6],
          own_quotes={0: ["연간"], 1: ["분기"], 2: ["월간"]}),  # three listed frequencies: three copies
    _case("V3b", "가상GDP를 연간, 분기, 월간으로 각각 보여줘", "jt_basis", count=3, rows=[6],
          own_quotes={0: ["연간"], 1: ["분기"], 2: ["월간"]}),
    _case("V4", "가상국제수지표와 가상경상수지·가상상품수지를 보여줘", "jt_bop", count=1, rows=[2],
          breakdown={0: ["가상상품수지"]}, interp={0: {"indicator": "가상경상수지", "context": "가상국제수지표"}}),
    _case("V5", "가상국제수지표의 가상경상수지 대비 가상상품수지 비율을 보여줘", "jt_bop", count=2,
          interp={0: {"indicator": "가상경상수지", "context": "가상국제수지표"}, 1: {"indicator": "가상상품수지"}},
          interp_relations=[("ratio", [0, 1])]),  # the table before a relation is its context, no operand
    _case("V5b", "가상국제수지표에서 가상경상수지와 가상상품수지의 차이를 보여줘", "jt_bop", count=2,
          interp={0: {"indicator": "가상경상수지", "context": "가상국제수지표"}}, interp_relations=[("diff", [0, 1])]),
    _case("V6", "가상GDP를 가상가격기준(갑)과 가상가격기준(을)로 보여줘", "jt_price_basis", certain=False,
          failed=["inexact_term"]),  # a basis read from catalog value names carries no condition ID
    _case("V6b", "가상GDP를 가상가격기준으로 보여줘", "jt_price_basis", certain=False, failed=["inexact_term"]),
    _case("V7", "가상GDP를 가상유형별과 가상산업별로 보여줘", "jt_basis_family", count=1, rows=[5],
          breakdown={0: ["가상유형별", "가상산업별"]}, not_rows=[6]),  # another family's X-byeol: breakdown axes
    _case("V7b", "가상GDP를 가상유형별, 가상산업별로 보여줘", "jt_basis_family", count=1, rows=[5],
          breakdown={0: ["가상유형별", "가상산업별"]}, not_rows=[6]),
    _case("V8", "가상 실업률을 보여줘", "jt_oos_space", count=1, interp_status="unsupported",
          interp={0: {"status": "unsupported"}}),  # an out-of-scope entry of two words
    # ---- P. decomposition patterns of the end-to-end check (implementer, 10/18; to be confirmed). Each pattern
    # with a variant and a guard that must keep its old reading
    # P1: a table name with "및" whose leading words are written elsewhere stays one name (no list cut inside)
    # user decision 10/19 21:45 (decision 2): a similar name with table candidates passes check 1
    _case("P1", "가상기관 신규취급액의 가상고정 및 가상변동대출 비중을 보여줘", "jt_p_and", count=1, certain=False,
          failed=["leftover_word"], interp={0: {"indicator": "가상고정 및 가상변동대출 비중"}}),
    _case("P1b", "가상기관의 가상고정 및 가상변동대출 비중을 비교해줘", "jt_p_and", count=1, certain=False,
          interp={0: {"indicator": "가상고정 및 가상변동대출 비중"}}),
    _case("P1n", "가상경상수지 및 가상상품수지를 보여줘", "jt_p_and", count=2,
          rows=[8]),  # guard: "A 및 B" of two catalog names stays a list
    # P2: dividing verbs take axes of the phrase they divide (catalog axis names, or X-byeol words of its name)
    _case("P2", "가상활동과 가상재 형태를 동시에 나눈 연간 명목 가상투자를 찾아줘", "jt_p_divide", count=1, rows=[5],
          certain=False, interp={0: {"indicator": "가상투자"}}),
    _case("P2n", "가상갑과 가상을을 동시에 나눈 가상투자를 찾아줘", "jt_p_divide", not_rows=[5],
          certain=False),  # guard: words that name no axis of the phrase's tables are no axes
    _case("P2b", "가상자산 가상자본 지표에서 가상업과 가상규모를 나누어 비교해줘", "jt_p_divide", count=1, rows=[5],
          certain=False),  # "N에서 A와 B를 나누어": A and B are (parts of) axis names of a table of N
    _case("P2bn", "가상자산 가상자본 지표에서 가상갑과 가상을을 나누어 비교해줘", "jt_p_divide", not_rows=[5],
          certain=False),
    _case("P2c", "가상M2를 보유한 가상주체별로 나눠 평잔 원계열을 비교해줘", "jt_p_divide", count=1,
          breakdown={0: ["가상주체별"]}, certain=False, failed=["leftover_word"],
          interp={0: {"indicator": "가상M2"}}),  # an adnominal verb before the axis names no indicator
    # P3: an institution word and an X-byeol of one table name qualify one requirement (no copy without two values)
    _case("P3", "가상기관 가상대출금을 가상규모별로 나눠 비교해줘", "jt_p_family", count=1, rows=[6],
          breakdown={0: ["가상규모별"]}),
    _case("P3b", "가상기관 가상대출금을 가상규모별로 보여줘", "jt_p_family", count=1, breakdown={0: ["가상규모별"]}),
    _case("P3n", "가상대출금을 가상규모별과 가상형태별로 각각 보여줘", "jt_p_family", count=2, rows=[6],
          own_quotes={0: ["가상규모별"], 1: ["가상형태별"]}),  # guard: two qualifiers no table name holds together
    # P4: copies by two values of one basis slot; a value of another slot before them and a tail frequency are
    # common, a general noun after a basis value ("금액") is read as part of it
    _case("P4", "계절조정 가상생산의 명목 금액과 실질 금액을 분기별로 보여줘", "jt_p_copy", count=2, rows=[6],
          own_quotes={0: ["계절조정", "명목"], 1: ["계절조정", "실질"]}),
    _case("P4b", "계절조정 가상생산을 명목과 실질로 보여줘", "jt_p_copy", count=2, rows=[6],
          own_quotes={0: ["계절조정", "명목"], 1: ["계절조정", "실질"]}),
    _case("P4c", "가상차주 가상대출의 신규취급액과 잔액을 비교해줘", "jt_p_copy", count=2, rows=[6], certain=False,
          failed=["inexact_term"]),  # words that stand in for each other in table names; 잔액 is no condition
    _case("P4n", "가상생산을 명목과 원계열로 보여줘", "jt_p_copy", certain=False, failed=["row_2_or_7"],
          status={0: "unresolved"}),  # guard: values of two slots stay unresolved
    # P5: a word that is the common front of several catalog names keeps its family-level name
    _case("P5", "가상M3를 보여줘", "jt_p_name", count=1, certain=False, interp={0: {"indicator_name": "가상M3"}}),
    _case("P5b", "가상국외순증을 보여줘", "jt_p_name", count=1,
          interp={0: {"indicator_name": "가상국외순증(F)"}}),  # one trimmed name only: that name
    # ---- G. split grounding (user decision 10/18 14:39; implementer cases, to be confirmed): every requirement of
    # a split needs a table of its indicator holding its own targets and basis values
    _case("G1", "가상기관과 가상대출금리를 보여줘", "jt_g", count=1, certain=False, failed=["reattached"],
          interp={0: {"indicator": "가상대출금리", "context": "가상기관"}}),  # the start of the table name: context
    _case("G2", "가상규모와 가상대출금리를 보여줘", "jt_g", count=1, certain=False, failed=["reattached"],
          breakdown={0: ["가상규모와"]}),  # (part of) an axis name of the indicator's table: a breakdown axis
    _case("G3", "가상계절과 가상심리지수를 보여줘", "jt_g", count=1, certain=False,
          failed=["reattached"]),  # the start of a basis value of the indicator's table
    # user decision 10/19 21:45 (decision 3): an ungrounded split no longer blocks certainty
    _case("G4", "가상모의와 가상대출금리를 보여줘", "jt_g", count=2, certain=False, failed=["row_2_or_7"],
          not_failed=["ungrounded_split", "reattached"]),  # nowhere to attach: the split stays as read
    _case("G5", "가상대출금리와 가상심리지수를 보여줘", "jt_g", count=2,
          not_failed=["ungrounded_split", "reattached"]),  # both grounded: the check does not block the split
    _case("G6", "가상국외자산순증과 가상국외부채순증을 보여줘", "jt_sib", count=2, together=[[0, 1]],
          not_failed=["ungrounded_split", "reattached"]),  # siblings of one table: both grounded
    _case("P4d", "가상수입물가의 원화기준 금액과 계약통화기준 금액을 보여줘", "jt_p_copy", count=2, rows=[6],
          certain=False),  # basis value names over condition words of one slot (원화, 계약통화): two copies
    # ---- plan 05 (10/19, approved r1): structure rules of the decomposition, two directions per type
    # E. negation covers its own phrase only; an excluding request verb makes its clause's names exclusions
    _case("E1", "가상GDP를 연간이 아니라 분기로 보여줘", "jt_basis", count=1, negated=["연간"],
          interp={0: {"indicator": "가상GDP"}}),  # the frequency before the marker, not the object before it
    _case("E1n", "가상경상수지가 아니라 가상상품수지를 보여줘", "jt_two", count=1, negated=["가상경상수지"],
          interp={0: {"indicator": "가상상품수지"}}),  # must stay: a name right before the marker is negated
    _case("E2", "가상경상수지는 보여주고 가상상품수지는 빼줘", "jt_two", count=1,
          interp={0: {"indicator": "가상경상수지"}}),
    _case("E2b", "가상경상수지를 가상상품수지 없이 보여줘", "jt_two", count=1, interp={0: {"indicator": "가상경상수지"}}),
    _case("E2n", "가상경상수지는 보여주고 가상상품수지도 보여줘", "jt_two", count=2),  # must stay: no exclusion
    # D. topic segments are requirement boundaries whatever the head's type; a segment's conditions are its own
    _case("D1", "가상경상수지는 원계열로, 가상상품수지는 계절조정으로 보여줘", "jt_two", count=2,
          own_quotes={0: ["원계열"], 1: ["계절조정"]}, not_failed=["ambiguous_attribution"]),
    _case("D2", "가상제조업은 월별로, 가상부채비율은 분기별로 보여줘", "jt_target", count=2,
          own_quotes={0: ["월별"], 1: ["분기별"]}),  # a target head keeps its own segment too
    _case("D2n", "가상GDP는 명목과 실질로 보여줘", "jt_basis", count=2, rows=[6]),  # must stay: one topic is no cut
    # C. items joined by list words are siblings: a listed target value is no target of the listed indicators
    _case("C1", "가상경상수지 및 가상은행을 보여줘", "jt_bop_org", count=2, broad={1: True}, certain=False,
          failed=["scope_ambiguous"]),
    _case("C1b", "가상조합, 가상경상수지를 보여줘", "jt_bop_org", count=2, broad={0: True}, certain=False),
    _case("C1n", "가상은행 가상경상수지를 보여줘", "jt_bop_org", count=1,
          targets={0: ["가상은행"]}),  # must stay: a bare modifier right before is a target
    # A. a scope phrase feeds row 2: "X 중 B가 차지하는 비중" with B under X in every common table is one requirement
    _case("SC1", "가상급료수지 중 가상급료지급이 차지하는 비중을 보여줘", "jt_share", count=1, rows=[2],
          breakdown={0: ["가상급료지급"]}, interp_relations=[("share_of", [0])]),
    _case("SC1n", "가상경상수지 중 가상상품수지가 차지하는 비중을 보여줘", "jt_two", count=2, not_rows=[2],
          interp_relations=[("share_of", [0, 1])]),  # must stay: siblings, not under X: two requirements
    # B. copies of one indicator: distributive marker, then phrase shape (repeated head), then slot evidence
    _case("BC1", "가상차주대출 잔액을 연령별, 지역별로 각각 보여줘", "jt_axes", count=2,
          breakdown={0: ["연령별"], 1: ["지역별"]}),  # "각각" over two different X-byeol axes: one each
    _case("BC1n", "가상차주대출 잔액을 연령별, 지역별로 보여줘", "jt_axes", count=1,
          breakdown={0: ["연령별", "지역별"]}),  # must stay: no marker, one requirement broken down by both
    _case("BC2", "가상대출금을 가상규모별, 가상형태별, 가상규모별로 보여줘", "jt_p_family", count=2,
          own_quotes={0: ["가상규모별"], 1: ["가상형태별"]}),  # one item, two values that swap: copies, repeat merges
    _case("BC3", "가상대출금(가상규모별)과 가상대출금(가상형태별)을 보여줘", "jt_p_family", count=2,
          own_quotes={0: ["가상규모별"], 1: ["가상형태별"]}),  # a repeated head "X(A)과 X(B)": parallel copies
    _case("BC4", "가상대출금을 가상기관과 가상비은행으로 각각 보여줘", "jt_b_targets", count=1,
          targets={0: ["가상기관", "가상비은행"]}),  # values of one target axis: one requirement, "각각" or not
    _case("BC4n", "가상대출금(가상기관)과 가상대출금(가상비은행)을 보여줘", "jt_b_targets",
          count=2),  # must stay: the repeated head still makes copies
    # F. "A, 즉 B" is one requirement with two name candidates; names of different tables stay unresolved
    _case("AP1", "가상대외채무, 즉 가상대외채권을 보여줘", "jt_sib", count=1, certain=False, status={0: "unresolved"},
          interp={0: {"indicator": "가상대외채무"}}),
    _case("AP2", "가상국외자산순증, 즉 가상국외부채순증을 보여줘", "jt_sib", count=1, certain=False,
          status={0: "ok"}),  # one table holds both names: one requirement, still not certain (appositive)
    _case("AP1n", "가상대외채무, 가상대외채권을 보여줘", "jt_sib", count=2),  # must stay: a plain list
    # G. a question written without spaces is read for names again; such names are never exact
    _case("NS1", "가상대외채무와가상대외채권을보여줘", "jt_sib", count=2, certain=False, failed=["inexact_term"]),
    _case("NS2", "2020년가상경상수지를월별로보여줘", "jt_bop", count=1, certain=False,
          interp={0: {"indicator": "가상경상수지"}}),
    _case("NS1n", "가상대외채무와 가상대외채권을 보여줘", "jt_sib", count=2,
          not_failed=["inexact_term"]),  # must stay: spaced names stay exact
    # H. the head noun of table names (X-byeol words and the table's own target values taken off) is a family alias
    # user decision 10/19 21:45 (decision 2): a head alias with table candidates passes check 1
    _case("HA1", "가상조합 가상수신을 지역별로 보여줘", "jt_h", count=1, certain=True,
          targets={0: ["가상조합"]}, interp={0: {"indicator": "가상수신", "indicator_name": "가상수신"}}),
    _case("HA1n", "가상조합 가상모의를 지역별로 보여줘", "jt_h", certain=False,
          leftover=["가상모의를"]),  # must stay: a word that is no head alias stays a leftover word
    # ---- plan 05 review round 1 (Codex xhigh, 10/19): each finding locked as a case
    _case("RV1", "가상차주대출 잔액을 연령별, 지역별로 따로 보여줘", "jt_axes", count=2,
          breakdown={0: ["연령별"], 1: ["지역별"]}),  # "따로" is a distributive marker like "각각"
    _case("RV2", "가상차주대출 잔액을 연령별, 지역별로 보여줘", "jt_axes", count=1, certain=False,
          failed=["scope_ambiguous"]),  # listed axes with no marker: one requirement, undecided (decision 2)
    _case("RV2n", "가상차주대출 잔액을 연령별, 지역별로 동시에 보여줘", "jt_axes", count=1,
          not_failed=["scope_ambiguous"]),  # must stay: a simultaneous marker settles one requirement
    _case("RV2d", "가상차주대출 잔액을 연령별·지역별로 보여줘", "jt_axes", count=1, certain=False,
          failed=["scope_ambiguous"]),  # round 2: a middle dot lists the axes too (decision 2 has no exception)
    _case("RV3", "가상대출금을 2020년 가상기관과 2021년 가상비은행으로 각각 보여줘", "jt_b_targets", count=2,
          own_quotes={0: ["2020년"], 1: ["2021년"]}),  # own periods differ: no merge into target values
    _case("RV4", "가상대외채무(즉 가상대외채권)와 가상국외자산순증을 보여줘", "jt_sib", count=2, certain=False,
          interp={0: {"indicator": "가상대외채무"}, 1: {"indicator": "가상국외자산순증"}}),  # the bracket ends it
    _case("RV5", "가상제조업은 월별로, 가상부채비율은 분기별로 보여줘. 가상수출전망은 연간으로 보여줘", "jt_target",
          count=3, own_quotes={0: ["월별"], 1: ["분기별"], 2: ["연간"]}),  # topic segments in one clause of several
    _case("RV6", "가상경상수지를 보여주고 가상상품수지와 가상소득수지는 빼줘", "jt_two", count=1,
          interp={0: {"indicator": "가상경상수지"}}),  # the whole listed exclusion clause
    _case("RV6b", "가상경상수지는 제외하지 말고 보여줘", "jt_two", count=1, conditions_absent=["exclude"],
          interp={0: {"indicator": "가상경상수지"}}),  # a negated exclusion verb excludes nothing
    _case("RV7", "가상제조업, 가상건설업, 가상부채비율을 보여줘", "jt_target", count=3, certain=False),
    _case("RV7b", "가상제조업과 가상부채비율과 가상건설업을 보여줘", "jt_target", count=3,
          certain=False),  # the same reading in any order
    _case("RV8", "비가상농업취업자수를 보여줘", "jt_p_divide", count=1, status={0: "unresolved"},
          interp={0: {"targets": 0}}),  # no name taken from inside an unknown compound
    # ---- plan 05 review round 2 (Codex xhigh, 10/19): the variants of round 1, each locked as a case
    _case("RW1", "가상차주대출 잔액을 연령별/지역별로 보여줘", "jt_axes", count=1, certain=False,
          failed=["scope_ambiguous"]),  # a slash lists the axes too
    _case("RW2", "가상차주대출 잔액은 연령별, 지역별로 동시에, 가상경상수지는 따로 보여줘", "jt_rw", count=2,
          breakdown={0: ["연령별", "지역별"]}),  # markers count in their own topic segment
    _case("RW3", "가상경상수지(즉 가상상품수지)를 가상은행으로 보여줘", "jt_bop_org", count=1, certain=False,
          targets={0: ["가상은행"]}),  # the appositive candidates end at the closing bracket
    _case("RW4", "가상경상수지를 비교하고 가상상품수지 및 가상소득수지는 빼줘", "jt_two", count=1,
          interp={0: {"indicator": "가상경상수지"}}),  # any coordinator, any clause end the parser cuts at
    _case("RW4b", "가상경상수지를 보여주고 가상상품수지, 가상소득수지는 빼줘", "jt_two", count=1,
          interp={0: {"indicator": "가상경상수지"}}),
    _case("RW5", "가상제조업가상부채비율증가속도를 보여줘", "jt_target", count=1, status={0: "unresolved"},
          interp={0: {"targets": 0}}),  # a dropped candidate is no boundary for the name before it
    # ---- user decision 10/19 (implementer cases, to be confirmed): check 9, the role rule, the coordinator
    # check 9: a question whose terms are targets only is never certain; an indicator or no term at all is not check 9
    _case("NI1", "가상제조업과 가상건설업을 보여줘", "jt_target", count=1, certain=False, failed=["no_indicator"]),
    _case("NI1n", "가상제조업과 가상건설업의 가상부채비율을 보여줘", "jt_target", count=1,
          not_failed=["no_indicator"]),  # must stay: an indicator is named
    _case("NI2n", "가상경상수지와 모의블루칩지수를 보여줘", "jt_bop", certain=False, failed=["row_2_or_7"],
          not_failed=["no_indicator"]),  # must stay: a word with no term keeps row 10
    _case("NI3n", "가상실업률을 보여줘", "jt_oos", count=1, interp_status="unsupported",
          not_failed=["no_indicator"]),  # must stay: an out-of-scope term keeps its own handling
    # role rule: the one target name on an indicator axis of a table holding the other targets is the indicator
    _case("RR1", "가상은행의 가상비거주발행을 알려줘", "jt_role", count=1, targets={0: ["가상은행"]},
          not_failed=["no_indicator"], interp={0: {"indicator": "가상비거주발행", "targets": 1}}),
    _case("RR2", "가상갑법인이랑 가상을법인의 가상거주발행을 비교해줘", "jt_role", count=1,
          targets={0: ["가상갑법인", "가상을법인"]}, not_failed=["no_indicator"],
          interp={0: {"indicator": "가상거주발행", "targets": 2}}),
    _case("RR1n", "가상비거주발행과 가상거주발행을 보여줘", "jt_role", count=1, certain=False,
          failed=["no_indicator"], interp={0: {"indicator": None, "targets": 2}}),  # two names qualify: no rule
    _case("RR2n", "가상갑법인과 가상을법인을 보여줘", "jt_role", count=1, certain=False, failed=["no_indicator"],
          interp={0: {"indicator": None, "targets": 2}}),  # must stay: no name on an indicator axis
    # coordinator: "A하고 B" with A a table name and B's sibling value in it is a list of two (read two ways)
    _case("HG1", "가상총지출하고 가상소비지출을 보여줘", "jt_coord", count=2, certain=False, failed=["mixed_reading"],
          interp={0: {"indicator": "가상총지출", "context": None}, 1: {"indicator": "가상소비지출", "context": None}}),
    _case("HG1b", "가상총지출과 가상소비지출을 보여줘", "jt_coord", count=2, certain=False, failed=["mixed_reading"],
          interp={0: {"indicator": "가상총지출"}, 1: {"indicator": "가상소비지출"}}),
    _case("HG1n", "가상지출표하고 가상소비지출을 보여줘", "jt_coord", count=1,
          interp={0: {"indicator": "가상소비지출", "context": "가상지출표"}}),  # must stay: a table name only (R9)
    # ---- 10/19 detail fixes (implementer cases, to be confirmed)
    # "A의 X와 B의 Y": each "N의" belongs to the indicator right after it
    _case("PP1", "가상대기업의 가상연구비와 가상중소기업의 가상이자수익을 보여줘", "jt_pair", count=2,
          targets={0: ["가상대기업"], 1: ["가상중소기업"]}),
    _case("PP1n", "가상대기업의 가상연구비와 가상이자수익을 보여줘", "jt_pair", count=2,
          targets={0: ["가상대기업"], 1: ["가상대기업"]}),  # must stay: one modifier over the list
    # a condition word inside the indicator name is no condition of its own (test_condition_inside_a_name below)
    _case("IN1", "가상예금 원화예금을 평잔으로 보여줘", "jt_inner", count=1, common=["평잔"]),
    _case("IN1n", "가상예금 원화예금(말잔)을 보여줘", "jt_inner", count=1,
          common=["말잔"]),  # must stay: a bracket after the name is a condition
    # a qualifier word that is a target name, written "N의" before the indicator, is its target
    _case("GT1", "가상서비스가 아니라 가상전산업의 가상인건비를 보여줘", "jt_gen", count=1,
          targets={0: ["가상전산업"]}, interp={0: {"indicator": "가상인건비", "targets": 1}}),
    _case("GT1n", "가상전산업 가상인건비를 보여줘", "jt_gen", count=1, targets={0: []},
          common=["가상전산업"]),  # must stay: without "의" the qualifier stays a condition
    # ---- 10/19 second detail fixes (implementer cases, to be confirmed)
    # "X의 A, B와 C" with X a qualifier word read as a target: X on every item, or check 7 when a table lacks it
    _case("GL1", "가상전산업의 가상A율, 가상B율과 가상C율을 보여줘", "jt_gen3", count=3,
          targets={0: ["가상전산업"], 1: ["가상전산업"], 2: ["가상전산업"]}),
    _case("GL1n", "가상전산업의 가상A율, 가상B율과 가상D율을 보여줘", "jt_gen3", count=3, certain=False,
          failed=["scope_ambiguous"]),  # 가상D율 has no table with 가상전산업: never certain on the first only
    # "A의 X와 B의 Y" with X of target type only: X is A's indicator when an indicator axis of A's table holds it
    _case("PH1", "가상갑법인의 가상비거주발행과 가상을법인의 가상A값을 보여줘", "jt_role", count=2,
          targets={0: ["가상갑법인"], 1: ["가상을법인"]}, interp={0: {"indicator": "가상비거주발행"}}),
    _case("PH1n", "가상갑법인의 가상은행과 가상을법인의 가상A값을 보여줘", "jt_role", certain=False,
          failed=["scope_ambiguous"]),  # 가상은행 is on no indicator axis: where 가상갑법인 belongs is open
    _case("PH2", "가상갑법인의 가상A값과 가상을법인의 가상비거주발행을 보여줘", "jt_role", count=2,
          targets={0: ["가상갑법인"], 1: ["가상을법인"]}, interp={1: {"indicator": "가상비거주발행"}}),  # a later pair too
    _case("PH2n", "가상갑법인의 가상A값과 가상을법인의 가상은행을 보여줘", "jt_role", certain=False,
          failed=["scope_ambiguous"]),  # a later "B의 X" with no indicator never joins the first requirement
    # head + lower items of target type under it in every common table: row 2 (breakdown)
    _case("LT1", "가상신용부문과 가상보조부문·가상연금부문을 찾아줘", "jt_lowtgt", count=1, rows=[2],
          breakdown={0: ["가상보조부문", "가상연금부문"]}, targets={0: []}),
    _case("LT1n", "가상X값과 가상보조부문을 찾아줘", "jt_lowtgt", not_rows=[2]),  # must stay: not under the head
    # "N을 X별 A와 B로": the X-byeol word naming the axis of A and B in N's table is no own condition (row 2)
    _case("AV1", "가상수입물가지수를 용도별 가상원재료와 가상중간재로 비교해줘", "jt_axisval", count=1, rows=[2],
          breakdown={0: ["가상원재료", "가상중간재"]}),
    _case("AV1n", "가상수입물가지수를 지역별 가상원재료와 가상중간재로 비교해줘", "jt_axisval",
          not_rows=[2]),  # must stay: an X-byeol word naming no axis of the table keeps its own condition
    # ---- review round r1b (Codex, 10/19): each finding locked as a case (implementer cases, to be confirmed)
    # 1: an excluded indicator name is no indicator of the question (check 9)
    _case("FX1", "가상제조업을 보여주고 가상부채비율은 빼줘", "jt_target", certain=False, failed=["no_indicator"]),
    _case("FX1n", "가상제조업의 가상부채비율을 보여주고 가상수출전망은 빼줘", "jt_target", count=1,
          not_failed=["no_indicator"]),  # must stay: the indicator asked for is no exclusion
    # 2: a topic segment starts at the conditions right before its head when a separator ends the segment before
    _case("FT1", "가상A금리는 월별로, 분기별로 가상B금리는 보여줘", "jt_rates", count=2,
          owner={"월별": 0, "분기별": 1}),
    _case("FT1n", "가상A금리는 월별로 분기별로 가상B금리는 보여줘", "jt_rates", certain=False,
          failed=["row_2_or_7"]),  # no separator: whose segment the middle condition is stays undecided
    # 3: a separator after a complete request ends the clause; a requested name is not excluded
    _case("FE1", "가상경상수지를 보여줘, 가상상품수지는 빼줘. 가상서비스수지를 보여줘", "jt_bop", count=2,
          interp={0: {"indicator": "가상경상수지"}, 1: {"indicator": "가상서비스수지"}}),
    _case("FE1n", "가상경상수지와 가상상품수지는 빼줘. 가상서비스수지를 보여줘", "jt_bop", count=1,
          interp={0: {"indicator": "가상서비스수지"}}),  # must stay: a whole exclusion clause excludes its names
    # 8: a scope phrase with one lower item compares that item's own conditions before row 2
    _case("FS1", "가상경상수지 중 가상상품수지(2020년)를 보여줘", "jt_bop", count=1, not_rows=[2],
          interp={0: {"indicator": "가상상품수지"}}),
    _case("FS1n", "가상경상수지 중 가상상품수지를 보여줘", "jt_bop", count=1, rows=[2],
          breakdown={0: ["가상상품수지"]}),  # must stay: no own condition: row 2
    # 9: values listed under a table name keep the same-table group hint
    _case("FG1", "가상국제수지표와 가상상품수지와 가상서비스수지를 보여줘", "jt_bop", count=2, together=[[0, 1]]),
    _case("FG1n", "가상대외채무와 가상대외채권을 보여줘", "jt_sib", count=2,
          apart=[[0, 1]]),  # must stay: names of different tables get no hint
    # 10: relation words (비율) and distributive markers (따로) of the data lists make a tail condition common
    _case("FA1", "가상A금리와 가상B금리의 비율을 신규취급액 기준으로 보여줘", "jt_rates", count=2, common=["신규취급액"]),
    _case("FA1b", "가상A금리와 가상B금리를 따로 신규취급액 기준으로 보여줘", "jt_rates", count=2, common=["신규취급액"]),
    _case("FA1n", "가상A금리와 가상B금리를 신규취급액 기준으로 보여줘", "jt_rates", count=2,
          ambiguous=["신규취급액"]),  # must stay: no marker, no relation word: ambiguous
    # ---- review round r2 (Codex, 10/19): each finding locked as a case (implementer cases, to be confirmed)
    # r2-1: an excluded indicator name in the same fragment as the asked target is no indicator (check 9, by name)
    _case("GX1", "가상제조업을 보여줘 가상부채비율은 빼줘", "jt_target", certain=False, failed=["no_indicator"]),
    _case("GX1n", "가상부채비율을 보여줘 가상제조업은 빼줘", "jt_target", count=1,
          not_failed=["no_indicator"]),  # must stay: the asked name is the indicator
    # r2-2: conditions with a tail word ("분기 기준으로") still lead the next topic head
    _case("GT2", "가상A금리는 월별로, 분기 기준으로 가상B금리는 보여줘", "jt_rates", count=2,
          owner={"월별": 0, "분기": 1}),
    _case("GT2n", "가상A금리는 월별로 분기 기준으로 가상B금리는 보여줘", "jt_rates", certain=False,
          failed=["row_2_or_7"]),  # no separator: undecided
    # r2-3: every final request form of the request list ends a clause before a separator
    _case("GE2", "가상경상수지를 보여줄래, 가상상품수지는 빼줘. 가상서비스수지를 보여줘", "jt_bop", count=2,
          interp={0: {"indicator": "가상경상수지"}, 1: {"indicator": "가상서비스수지"}}),
    _case("GE2n", "가상경상수지와 가상상품수지는 빼줘. 가상서비스수지를 보여줄래", "jt_bop", count=1,
          interp={0: {"indicator": "가상서비스수지"}}),  # must stay: a whole exclusion clause excludes its names
    # r2-5: the bracket basis word of a protected lower name is that item's own condition (no row 2)
    _case("GS2", "가상경상수지 중 가상상품수지(평잔)를 보여줘", "jt_inner5", count=1, not_rows=[2]),
    _case("GS2n", "가상경상수지 중 가상서비스수지를 보여줘", "jt_inner5", count=1, rows=[2],
          breakdown={0: ["가상서비스수지"]}),  # must stay: no own condition
    # r2-6: a relation word inside a catalog name ("가상부채비율") is no comparison word
    _case("GA2", "가상잔액값과 가상부채비율을 신규취급액 기준으로 보여줘", "jt_target", count=2,
          ambiguous=["신규취급액"]),
    _case("GA2n", "가상잔액값과 가상부채비율의 비율을 신규취급액 기준으로 보여줘", "jt_target", count=2,
          common=["신규취급액"]),  # must stay: a relation word of its own makes it common
    # ---- plan 09 P1 (implementer cases, to be confirmed): derived names of the name index are never exact
    # table_core: the table name without a first word that starts two name families
    # user decision 10/19 21:45 (decision 2): a derived name with table candidates passes check 1
    _case("DN1", "가상은행 신규취급액 기준 가상대출금리를 월별로 보여줘", "jt_d_core", count=1, certain=False,
          failed=["leftover_word"], terms_present=["가상대출금리"], interp={0: {"indicator": "가상대출금리"}}),
    _case("DN1n", "가상여신잔고를 보여줘", "jt_d_core", certain=False, terms_absent=["가상여신잔고"],
          interp={0: {"indicator_name": None}}),  # must stay: a first word of one family keeps no shorter name
    # no_tail: the table name without its tail word
    # user decision 10/19 21:45 (decision 2): a derived name with table candidates passes check 1
    _case("DN2", "가상수입물가를 가상용도별로 보여줘", "jt_d_tail", count=1, certain=True,
          interp={0: {"indicator": "가상수입물가"}}),
    _case("DN2n", "가상환율을 보여줘", "jt_d_tail", count=1, certain=True,
          interp={0: {"indicator": "가상환율"}}),  # must stay: a value name keeps its own exact reading
    # mark-free key: the name written with a space for its slash keeps the exact reading of the name
    _case("DN3", "가상자산 가상자본 지표를 보여줘", "jt_d_mark", count=1,
          interp={0: {"indicator": "가상자산 가상자본 지표"}}),
    _case("DN3n", "가상자산과 가상자본을 보여줘", "jt_d_mark", count=2,
          interp={0: {"indicator": "가상자산"}, 1: {"indicator": "가상자본"}}),  # must stay: two values
    # path_node: a list-path node standing in the table names
    # user decision 10/19 21:45 (decision 2): a derived name with table candidates passes check 1
    _case("DN4", "가상연체율을 보여줘", "jt_d_path", count=1, certain=True,
          interp={0: {"indicator": "가상연체율"}}),
    # abbr: a bracket abbreviation of a value name; never the other side of a table's "A 및 B"
    # user decision 10/19 21:45 (decision 2): a derived name with value candidates passes check 1
    _case("DN5", "VGN을 보여줘", "jt_d_abbr", count=1, certain=True,
          interp={0: {"indicator": "VGN"}}),
    # user decision 10/19: the abbreviation of a value that is also the other side of a table's "A 및 B" is a name
    # too, and "A와 B" reads two requirements (the convention of row 8)
    # user decision 10/19 21:45 (decision 2): a derived name with value candidates passes check 1
    _case("DN5b", "가상GDP와 VGNI를 보여줘", "jt_d_abbr", count=2, certain=True,
          interp={0: {"indicator": "가상GDP"}, 1: {"indicator": "VGNI"}}),
    # ---- plan 09 P2b (implementer cases, to be confirmed): a list an adnominal verb takes before a head name is one
    # phrase of the head (no requirement per listed word; the words stay leftover words: never certain)
    _case("MH1", "가상계절과 가상불규칙 변동을 제거한 가상심리지수를 보여줘", "jt_s_mod", count=1, certain=False,
          failed=["leftover_word"], conditions_absent=["breakdown"], interp={0: {"indicator": "가상심리지수"}}),
    _case("MH1n", "가상원재료와 가상중간재를 포함한 가상물가지수를 보여줘", "jt_s_mod", count=2, certain=False,
          interp={0: {"indicator": "가상원재료"}, 1: {"indicator": "가상중간재"}}),  # must stay: listed names
    _case("MH2n", "가상계절과 가상불규칙 변동을 뺀 가상심리지수를 보여줘", "jt_s_mod", count=2, certain=False,
          conditions=[("exclude", "변동을 뺀", None)]),  # must stay: an exclusion verb keeps its own reading
    _case("MH3n", "가상계절과 가상불규칙 변동을 보여줘", "jt_s_mod", count=2, certain=False,
          conditions=[("breakdown", "가상계절", None)]),  # must stay: no verb and head name: a list
    # ---- plan 09 P2c (implementer cases, to be confirmed): a table name written apart is read as that name, never
    # exact
    # a genitive inside the words of a table name
    # user decision 10/19 21:45 (decision 2): a similar name with table candidates passes check 1
    _case("SN1", "가상수출 물량이 아니라 가상수출 금액의 지수를 보여줘", "jt_s_split", count=1, certain=True,
          interp={0: {"indicator": "가상수출 금액의 지수", "indicator_name": "가상수출금액지수"}}),
    _case("SN1n", "가상조합의 가상대출금리를 보여줘", "jt_s_split", count=1, targets={0: ["가상조합"]},
          interp={0: {"indicator": "가상대출금리"}}),  # must stay: a target value before the genitive is a target
    _case("SN1b", "가상수출 금액의 지수와 가상수단을 보여줘", "jt_s_split", certain=False,
          terms_absent=["가상수출금액지수"]),  # must stay: the words after the genitive start a list
    # a negation between the two parts of a table name ("H는 w1이 아니라 w2"): w2 + H is the name asked
    # user decision 10/19 21:45 (decision 2): a similar name with table candidates passes check 1
    _case("SN2", "가상수단은 가상직불이 아니라 가상선불로 보여줘", "jt_s_split", count=1, certain=True,
          negated=["가상직불"], interp={0: {"indicator_name": "가상선불가상수단"}}),
    _case("SN2n", "가상수단은 가상갑이 아니라 가상을로 보여줘", "jt_s_split", count=1, certain=False,
          failed=["leftover_word"], interp={0: {"indicator": "가상수단"}}),  # must stay: no table name joined
    # review of plan 09: the genitive form reads the name of the whole family (as the spaced form does); a name asked
    # after the negation is no topic the words qualify
    # user decision 10/19 21:45 (decision 2): a similar name with table candidates passes check 1
    _case("SN1c", "가상수입 금액의 지수를 보여줘", "jt_s_split", count=1, certain=True,
          interp={0: {"indicator_name": "가상수입금액지수"}}),
    _case("SN2b", "가상선불 말고 가상수단을 보여줘", "jt_s_split", count=1, certain=False,
          interp={0: {"indicator": "가상수단"}}),  # must stay: the asked name is not taken into another
    # ---- user decision 10/19 (plan 09, implementer cases, to be confirmed): conditions after a list, written with an
    # adnominal ending and a general noun after them, are the list's tail: common or ambiguous, never the last item's
    _case("AT1", "가상GDP와 가상GNI를 계절조정한 실질 분기 금액으로 보여줘", "jt_s_tail", count=2, certain=False,
          ambiguous=["계절조정", "실질"], common=["분기"]),
    _case("AT1b", "가상GDP와 가상GNI를 계절조정 실질 분기로 보여줘", "jt_s_tail", count=2, certain=False,
          ambiguous=["계절조정", "실질"], common=["분기"]),  # the same without the adnominal ending
    _case("AT1n", "가상GDP를 계절조정한 실질 분기 금액으로 보여줘", "jt_s_tail", count=1,
          common=["계절조정", "실질", "분기"]),  # must stay: the one requirement keeps every condition
    # ---- plan 09 (10/19, implementer cases, to be confirmed): values listed together of one axis keep one role; the
    # listed axes of a dividing verb keep their words without particles
    _case("LP1", "가상판매비를 가상전산업과 가상비제조업으로 각각 보여줘", "jt_s_peer", count=1,
          targets={0: ["가상전산업", "가상비제조업"]}),
    _case("LP1b", "가상전산업과 가상비제조업 각각의 가상판매비를 보여줘", "jt_s_peer", count=1,
          targets={0: ["가상전산업", "가상비제조업"]}),
    _case("LP1n", "가상전산업 가상판매비를 보여줘", "jt_s_peer", count=1, targets={0: []},
          common=["가상전산업"]),  # must stay: a total header alone keeps its basis reading
    _case("DV1", "가상업과 가상규모를 나눠 가상자기자본비율을 보여줘", "jt_p_divide", count=1,
          breakdown={0: ["가상업", "가상규모"]}),
    _case("DV1n", "가상갑과 가상을을 나눠 가상자기자본비율을 보여줘", "jt_p_divide", certain=False,
          not_rows=[5]),  # must stay: words that name no axis of the phrase's table are no axes
    # ---- user decision 10/19 21:45 (implementer cases, two directions per rule)
    # decision 1: request and ending fragments of the lexicon ("알고 싶습니다", "원합니다") are no leftover words
    _case("LX1", "가상경상수지를 알고 싶습니다", "jt_bop", count=1, certain=True, leftover=[]),
    _case("LX2", "가상경상수지를 원합니다", "jt_bop", count=1, certain=True, leftover=[]),
    _case("LX1n", "가상경상수지를 지난번보다 보여줘", "jt_bop", count=1, certain=False, failed=["leftover_word"],
          leftover=["지난번보다"]),  # must stay: a word outside the lexicon stays a leftover word
    # decision 2 exceptions: a bracket-free form of names with bracket qualifiers, an out-of-scope entry
    _case("NP1", "가상국외신용을 보여줘", "jt_np", count=1, certain=False, failed=["inexact_term"]),
    _case("NP1n", "가상국외신용(순)을 보여줘", "jt_np", count=1, certain=True,
          interp={0: {"indicator": "가상국외신용(순)"}}),  # the name with its bracket stays exact
    _case("OS1", "가상실업률을 보여줘", "jt_oos", count=1, certain=False, failed=["inexact_term"],
          interp_status="unsupported"),  # an out-of-scope entry is no similar name
    # "A 및 B" of two names (a value's abbreviation included) reads as "A와 B" does: no derived name of it
    _case("DN5c", "가상GDP 및 VGNI를 보여줘", "jt_d_abbr", count=2, certain=True,
          interp={0: {"indicator": "가상GDP"}, 1: {"indicator": "VGNI"}}),
    # decision 3: requirements of an ungrounded split keep the split and carry the reason "ungrounded" (no
    # "certain" key: these cases add no structure to the verified list of check 8)
    _case("UG1", "가상대출금리와 가상수신금리를 보여줘", "jt_d_core", count=2, not_failed=["ungrounded_split"],
          interp={0: {"reasons": ["ungrounded"]}, 1: {"reasons": ["row:9", "ungrounded"]}}),
    _case("UG1n", "가상대출금리와 가상여신을 보여줘", "jt_d_core", count=2, not_failed=["ungrounded_split"],
          interp={1: {"reasons": ["row:9"]}}),  # a grounded member carries no such reason
    # B2 fix: a list member running into a catalog name written with a space holds the whole name; the X-byeol word
    # inside it is no basis
    _case("B2s", "가상예금은행 대출금을 가상시설자금·가상운전자금·가상특별 자금 가상용도별로 나눠 월별로 보여줘",
          "jt_b_loan", count=1, rows=[2], breakdown={0: ["가상시설자금", "가상운전자금", "가상특별 자금", "가상용도별"]},
          conditions=[("basis", "가상용도별", None)]),  # the X-byeol word outside the name stays a basis
    # P2c fix: a request word glued to the last word of the genitive and negation forms is taken off first
    _case("SN1g", "가상수입 금액의 지수를보여줘", "jt_s_split", count=1, certain=True,
          interp={0: {"indicator_name": "가상수입금액지수"}}),
    _case("SN2g", "가상수단은 가상직불이 아니라 가상선불로보여줘", "jt_s_split", count=1, certain=True,
          negated=["가상직불"], interp={0: {"indicator_name": "가상선불가상수단"}}),
    _case("SN2gn", "가상수단은 가상갑이 아니라 가상을로보여줘", "jt_s_split", count=1, certain=False,
          failed=["leftover_word"], interp={0: {"indicator": "가상수단"}}),  # must stay: no table name joined
]

EXPECT_KEYS = {"count", "rows", "not_rows", "certain", "failed", "failed_any", "not_failed", "breakdown", "targets",
               "broad", "together", "apart", "status", "leftover", "terms_absent", "terms_present", "conditions",
               "conditions_absent", "negated", "calc_on", "hints_on", "relation", "common", "owner", "hints_all",
               "own_quotes", "ambiguous", "interp_status", "interp", "interp_relations"}

OUT_OF_SCOPE = {"jt_oos": ["가상실업률"], "jt_oos_space": ["가상 실업률"]}  # out-of-scope lists (2.4 first path)


class SpecLayers(Layers):
    """Synthetic layers; a layer named in OUT_OF_SCOPE gets an approved out-of-scope list."""

    def get(self, name):
        if name is None or name in self.cache or name not in OUT_OF_SCOPE:
            return super().get(name)
        folder = self.factory.mktemp(f"spec_{name}")
        search, meta = write_catalog(folder / "raw", self.catalogs[name])
        inputs = write_inputs(folder / "inputs", overrides=[])
        empty = folder / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        oos = folder / "oos.jsonl"
        oos.write_text("".join(json.dumps({"term": t, "reason": "spec", "source": "spec", "status": "approved"},
                                          ensure_ascii=False) + "\n" for t in OUT_OF_SCOPE[name]), encoding="utf-8")
        self.cache[name] = load_layer(search_catalog=search, meta_raw=meta, overrides=False,
                                      include_external_synonyms=False, synonyms_approved_path=empty,
                                      out_of_scope_path=oos, **inputs)
        return self.cache[name]


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return SpecLayers(tmp_path_factory, CATALOGS)


def _param(c: dict, behaviour: bool):
    marks = []
    if behaviour and c["node"] not in IMPLEMENTED:
        marks.append(pytest.mark.xfail(strict=True, raises=NotImplementedError, reason=f"2구간: {c['node']}"))
    return pytest.param(c, id=c["id"], marks=marks)


def shape_problems(case: dict) -> list[str]:
    q, exp = case["question"], case["expect"]
    out = []
    if case["layer"] not in CATALOGS:
        out.append(f"layer {case['layer']!r} is not a synthetic catalog")
    out += [f"unknown expectation {k!r}" for k in sorted(set(exp) - EXPECT_KEYS)]
    for key in ("failed", "failed_any", "not_failed"):
        out += [f"{key}: {f!r} is not a CertaintyFailure" for f in exp.get(key, []) if f not in FAILURES]
    out += [f"rows: {r} not in 1..10" for r in exp.get("rows", []) + exp.get("not_rows", []) if not 1 <= r <= 10]
    for key in ("breakdown", "targets"):
        for k, words in exp.get(key, {}).items():
            out += [f"{key}[{k}]: {w!r} not in the question" for w in words if w not in q]
            if "count" in exp and not 0 <= k < exp["count"]:
                out.append(f"{key}[{k}]: no requirement {k}")
    for key in ("leftover", "negated", "common"):
        out += [f"{key}: {w!r} not in the question" for w in exp.get(key, []) if w not in q]
    for kind, quote, _value in exp.get("conditions", []):
        if kind not in shapes.ConditionKind.__args__ or quote not in q:
            out.append(f"conditions: ({kind}, {quote})")
    for w in exp.get("hints_on", {}):
        if w not in q:
            out.append(f"hints_on: {w!r} not in the question")
    if not exp:
        out.append("no expectation")
    if "together" in exp or "apart" in exp:
        n = exp.get("count")
        for grp in exp.get("together", []) + exp.get("apart", []):
            if n is not None and any(not 0 <= k < n for k in grp):
                out.append(f"group {grp}: no such requirement")
    return out


def _rows(j: dict) -> set[int]:
    return {p["row"] for p in j["pairs"]} | {d["row"] for d in j["requirement_draft"] if "row" in d}


def behaviour_problems(case: dict, analysis: dict) -> list[str]:
    exp, j = case["expect"], analysis["judgement"]
    drafts = j["requirement_draft"]
    out = []
    if "count" in exp and len(drafts) != exp["count"]:
        out.append(f"count: {len(drafts)} != {exp['count']}")
    rows = _rows(j)
    out += [f"rows: {r} not used ({sorted(rows)})" for r in exp.get("rows", []) if r not in rows]
    out += [f"rows: {r} used ({sorted(rows)})" for r in exp.get("not_rows", []) if r in rows]
    if "certain" in exp and j["certain"] != exp["certain"]:
        out.append(f"certain: {j['certain']} (failed {j['failed_conditions']})")
    out += [f"failed: {f} missing ({j['failed_conditions']})" for f in exp.get("failed", [])
            if f not in j["failed_conditions"]]
    if exp.get("failed_any") and not set(exp["failed_any"]) & set(j["failed_conditions"]):
        out.append(f"failed_any: none of {exp['failed_any']} ({j['failed_conditions']})")
    out += [f"not_failed: {f} present" for f in exp.get("not_failed", []) if f in j["failed_conditions"]]
    for key in ("breakdown", "targets"):
        for k, words in exp.get(key, {}).items():
            got = drafts[k].get(key, []) if k < len(drafts) else None
            if got != words:
                out.append(f"{key}[{k}]: {got} != {words}")
    for k, flag in exp.get("broad", {}).items():
        if k >= len(drafts) or drafts[k].get("broad") is not flag:
            out.append(f"broad[{k}]: not {flag}")
    for k, status in exp.get("status", {}).items():
        if k >= len(drafts) or drafts[k].get("status") != status:
            out.append(f"status[{k}]: not {status}")
    ids = [d["id"] for d in drafts]
    hint_sets = [set(g) for g in j.get("table_group_hint", [])]
    for grp in exp.get("together", []):
        want = {ids[k] for k in grp if k < len(ids)}
        if not any(want <= h for h in hint_sets):
            out.append(f"together {grp}: groups {j.get('table_group_hint')}")
    for grp in exp.get("apart", []):
        want = {ids[k] for k in grp if k < len(ids)}
        if any(want <= h for h in hint_sets):
            out.append(f"apart {grp}: groups {j.get('table_group_hint')}")
    if "leftover" in exp and j.get("leftover") != exp["leftover"]:
        out.append(f"leftover: {j.get('leftover')} != {exp['leftover']}")
    names = {t["name"] for t in analysis["terms"]}
    exact = {t["name"] for t in analysis["terms"] if t["path"] == "exact"}  # name spans (lexical guesses aside)
    out += [f"exact term {n!r} present" for n in exp.get("terms_absent", []) if n in exact]
    out += [f"term {n!r} missing ({sorted(names)})" for n in exp.get("terms_present", []) if n not in names]
    for kind, quote, value in exp.get("conditions", []):
        if not any(c["kind"] == kind and c["quote"] == quote and (value is None or c["value"] == value)
                   for c in analysis["conditions"]):
            out.append(f"condition ({kind}, {quote}, {value}) missing")
    out += [f"condition kind {k} present" for k in exp.get("conditions_absent", [])
            if any(c["kind"] == k for c in analysis["conditions"])]
    if "negated" in exp and j.get("negated") != exp["negated"]:
        out.append(f"negated: {j.get('negated')} != {exp['negated']}")
    if "calc_on" in exp:
        k = exp["calc_on"]
        calcs = [c["id"] for c in analysis["conditions"] if c["kind"] == "calc"]
        own = drafts[k].get("condition_ids", []) if k < len(drafts) else []
        if not calcs or not set(calcs) <= set(own):
            out.append(f"calc_on {k}: calc {calcs} not own of requirement {k} ({own})")
    for word, k in exp.get("hints_on", {}).items():
        hit = [h for h in j.get("calc_hints", []) if h["quote"] == word]
        if not hit or k >= len(ids) or hit[0]["requirement_id"] != ids[k]:
            out.append(f"hint {word!r}: {hit}")
    if "relation" in exp and not any(p.get("relation") == exp["relation"] for p in j["pairs"]):
        out.append(f"relation {exp['relation']} missing ({j['pairs']})")
    for word, ks in exp.get("hints_all", {}).items():
        hit = [h for h in j.get("calc_hints", []) if h["quote"] == word]
        want = [ids[k] for k in ks if k < len(ids)]
        if not hit or hit[0].get("requirement_ids") != want:
            out.append(f"hint {word!r} on {want}: {hit}")
    by_quote = {c["id"]: c["quote"] for c in analysis["conditions"]}
    for k, quotes in exp.get("own_quotes", {}).items():
        got = [by_quote.get(c) for c in drafts[k].get("condition_ids", [])] if k < len(drafts) else []
        out += [f"own_quotes[{k}]: {q!r} not own ({got})" for q in quotes if q not in got]
    att = j.get("attribution", {})
    for quote in exp.get("ambiguous", []):
        hit = [c for c in analysis["conditions"] if c["quote"] == quote]
        if not hit or att.get(hit[0]["id"]) != "ambiguous":
            out.append(f"ambiguous {quote!r}: {att}")
    interp = analysis["interpretation"] or {}
    reqs = interp.get("requirements", [])
    if "interp_status" in exp and interp.get("status") != exp["interp_status"]:
        out.append(f"interpretation status {interp.get('status')!r} != {exp['interp_status']!r}")
    for k, want in exp.get("interp", {}).items():
        if k >= len(reqs):
            out.append(f"interp[{k}]: no requirement")
            continue
        r = reqs[k]
        for field_, value in want.items():
            if field_ == "indicator":
                got = (r.get("indicator") or {}).get("quote") if r.get("indicator") else None
            elif field_ == "indicator_name":
                got = (r.get("indicator") or {}).get("name")
            elif field_ == "targets":
                got = len(r.get("targets", []))
            else:
                got = r.get(field_)
            if got != value:
                out.append(f"interp[{k}].{field_}: {got!r} != {value!r}")
    rel_ids = [r["id"] for r in reqs]
    for kind, ks in exp.get("interp_relations", []):
        want = [rel_ids[k] for k in ks if k < len(rel_ids)]
        if not any(x["kind"] == kind and x["requirement_ids"] == want for x in interp.get("relations", [])) \
                or any(x["kind"] == kind and x["requirement_ids"] != want for x in interp.get("relations", [])):
            out.append(f"interp relations {kind} {want}: {interp.get('relations')}")
    for quote, k in exp.get("owner", {}).items():
        hit = [c for c in analysis["conditions"] if c["quote"] == quote]
        if not hit or k >= len(ids) or j.get("attribution", {}).get(hit[0]["id"]) != ids[k]:
            out.append(f"owner {quote!r}: {j.get('attribution')}")
    for quote in exp.get("common", []):
        hit = [c for c in analysis["conditions"] if c["quote"] == quote]
        if not hit or j.get("attribution", {}).get(hit[0]["id"]) != "common":
            out.append(f"common {quote!r}: {j.get('attribution')}")
    return out


@pytest.mark.parametrize("case", [_param(c, False) for c in CASES])
def test_case_shape(case):
    assert shape_problems(case) == []


@pytest.mark.parametrize("case", [_param(c, True) for c in CASES])
def test_case_behaviour(case, layers):
    analysis = run_analysis(case, layers)
    assert behaviour_problems(case, analysis) == []


def test_settle_keeps_the_judgement_attribution(layers):
    """V9 (third review, 10/18): 2.7 takes the drafts and the attribution of the judgement it is given ("공통·모호는
    2.5 결과 그대로"); it does not judge the question again. The judgement of "가상GDP를 명목과 실질로 보여줘" is
    given with both basis conditions ambiguous and no own condition (a node-contract reproduction)."""
    from claview.a.interpret import conditions as finders
    from claview.a.interpret import nodes
    from claview.a.interpret.text import normalize_text

    layer = layers.get("jt_basis")
    text = normalize_text("가상GDP를 명목과 실질로 보여줘")
    conds = finders.find_conditions(text, layer)
    frags = nodes.split_fragments(text, conds, layer)
    terms = nodes.attach_terms(frags, layer, text, conds)
    judgement = nodes.judge_fragments(text, conds, frags, terms, layer)
    basis = [c["id"] for c in conds if c["kind"] == "basis"]
    assert len(judgement["requirement_draft"]) == 2 and len(basis) == 2
    given = {**judgement, "certain": False, "failed_conditions": ["ambiguous_attribution"],
             "attribution": {**judgement["attribution"], **{cid: "ambiguous" for cid in basis}},
             "requirement_draft": [{**d, "condition_ids": [c for c in d.get("condition_ids", []) if c not in basis]}
                                   for d in judgement["requirement_draft"]]}
    interp = nodes.settle_requirements(text, conds, frags, terms, nodes.model_slot(given), layer)
    assert interp["status"] == "ask"
    for req in interp["requirements"]:
        assert not set(basis) & set(req["condition_ids"]), req
        assert set(basis) <= set(req["ambiguous_condition_ids"]), req


def test_case_ids_are_unique_and_cover_the_case_table():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    table_ids = {"J1", "J2", "J2b", "J3", "J4", "J4b", "J5", "J6", "J6b", "J6c", "J7", "J8", "J9", "J10", "C5", "C5n",
                 "C5p", "C6", "C7", "C7n", "N1", "N2", "N3", "N4", "N5", "N6", "F1", "F2", "F3", "F4", "F5", "F6",
                 "F7", "F8", "M1", "M2", "M3", "M4", "M5", "M6", "M7"}
    assert table_ids <= set(ids)


def test_condition_inside_a_name(layers):
    """A condition word inside the indicator name ("원화" of "가상예금 원화예금") is no condition of the requirement;
    the condition after the name is (10/19 detail fix)."""
    from claview.a.interpret.pipeline import interpret_question

    a = interpret_question("가상예금 원화예금을 평잔으로 보여줘", layers.get("jt_inner"))
    inner = [c["id"] for c in a["conditions"] if c["quote"] == "원화"]
    assert inner, a["conditions"]
    (req,) = a["interpretation"]["requirements"]
    assert not set(inner) & set(req["condition_ids"] + req["shared_condition_ids"] + req["ambiguous_condition_ids"])
    assert not set(inner) & set(a["judgement"]["attribution"])


@pytest.mark.parametrize("question, word, polarity", [
    ("가상A금리를 신규취급액 기준이 아니라 잔액 기준으로 보여줘", "신규취급액", "-"),
    ("가상통화량을 계절조정계열이 아니라 원계열로 보여줘", "계절조정", "-"),
    ("가상A금리를 신규취급액 기준으로 보여줘", "신규취급액", "+"),  # must stay: no negation after the tail word
    ("가상통화량을 계절조정계열로 보여줘", "계절조정", "+"),
])
def test_negation_reaches_a_basis_word_across_a_tail_word(question, word, polarity):
    """10/19: a negation after a tail word of a basis word ("기준", "계열"; data/condition_words.json) negates it."""
    from claview.a.interpret.conditions import find_basis_conditions
    from claview.a.interpret.text import normalize_text

    got = [c["attrs"]["polarity"] for c in find_basis_conditions(normalize_text(question)) if c["quote"] == word]
    assert got == [polarity]


def test_each_relation_has_its_own_operands(layers):
    """Review r1b finding 7: two relations of one clause each name their own operand requirements."""
    from claview.a.interpret.pipeline import interpret_question

    a = interpret_question("가상A금리 대비 가상B금리 비율, 가상C금리 대비 가상D금리 비율을 보여줘", layers.get("jt_rates"))
    rels = {(x["condition_id"], tuple(x["requirement_ids"])) for x in a["interpretation"]["relations"]}
    by_quote = {}
    for c in a["conditions"]:
        if c["kind"] == "relation":
            by_quote.setdefault(c["quote"], []).append(c["id"])
    first_ratio, second_ratio = by_quote["비율"]
    assert (first_ratio, ("r1", "r2")) in rels and (second_ratio, ("r3", "r4")) in rels
    assert all(len(ids) == 2 for _c, ids in rels)
    b = interpret_question("가상A금리와 가상B금리의 차이를 보여줘", layers.get("jt_rates"))  # must stay: one pair
    assert [x["requirement_ids"] for x in b["interpretation"]["relations"]] == [["r1", "r2"]]


def test_relation_operands_end_at_a_coordinator(layers):
    """Review r2-4: "A 대비 B 비율 및 C 대비 D 비율": a coordinator ends the first relation's operands."""
    from claview.a.interpret.pipeline import interpret_question

    a = interpret_question("가상A금리 대비 가상B금리 비율 및 가상C금리 대비 가상D금리 비율을 보여줘", layers.get("jt_rates"))
    ratios = [c["id"] for c in a["conditions"] if c["quote"] == "비율"]
    rels = {x["condition_id"]: x["requirement_ids"] for x in a["interpretation"]["relations"]}
    assert rels[ratios[0]] == ["r1", "r2"] and rels[ratios[1]] == ["r3", "r4"]


def test_model_verify_checks_each_relation_mapping(layers):
    """Review r2-4: the V-check compares each relation's operands with its word's place, not only their number."""
    import copy

    from claview.a.interpret.model_verify import _relation_mapping
    from claview.a.interpret.pipeline import interpret_question

    a = interpret_question("가상A금리 대비 가상B금리 비율, 가상C금리 대비 가상D금리 비율을 보여줘", layers.get("jt_rates"))
    conds = {c["id"]: c for c in a["conditions"]}
    raw = a["text"]["raw"]
    assert _relation_mapping(a["interpretation"], conds, raw) == []  # must stay: the settled mapping passes
    bad = copy.deepcopy(a["interpretation"])
    bad["relations"][1]["requirement_ids"] = ["r2", "r3"]  # the first 비율 over B and C: a wrong mapping
    assert _relation_mapping(bad, conds, raw) == [f"V-req:relation_operands:{bad['relations'][1]['condition_id']}"]


def test_an_excluded_name_the_question_requests_surfaces():
    """Review r2-3: an excluded name followed by a complete request word before its exclusion ends is a requested
    name: check 6 lists it as unused instead of dropping it silently; a name inside its own exclusion is not."""
    from types import SimpleNamespace

    from claview.a.interpret.certainty import _requested
    from claview.a.interpret.phrases import Tok

    a = Tok("name", 0, 6, "가상경상수지", excluded=True)
    req = Tok("word", 8, 13, "보여줄래", label="request")
    b = Tok("name", 14, 20, "가상상품수지", excluded=True)
    verb = Tok("word", 22, 24, "빼줘", label="exclude_verb")
    reading = SimpleNamespace(toks=[a, req, b, verb])
    conds = [{"id": "X1", "kind": "exclude", "span": [0, 24]}]
    assert _requested(a, reading, conds)
    assert not _requested(b, reading, conds)  # must stay: the excluded name itself
