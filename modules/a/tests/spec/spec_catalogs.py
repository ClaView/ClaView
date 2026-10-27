"""Synthetic catalogs of the specification cases, rewritten from the old contract tests' synthetic tables.

Table IDs and names are the made-up ones of the old tests (and a few more made up here, marked); nothing comes
from questions, answers or the real catalog. The old tests wrote the snapshot without a TBL block and sometimes put
values on the axis "ITEM"; the new loader keeps "ITEM" rows apart from the classification axes (they carry the
table name), which only matters to cases that are xfail now.
"""

from __future__ import annotations

from spec_support import itm, table

M_2000 = (("월", "2000.01", "2026.08"),)
Y_2000 = (("년", "2000", "2025"),)


def one_axis(tid: str, title: str, names: list, *, unit: str | None = "지수", path: str = "가상 > 통계",
             periods=M_2000, units=(), obj_name: str | None = None, ids_by_name: bool = True) -> dict:
    """One axis "A" (old ``_itm(name)``); a (name, obj) pair puts the value on axis obj instead."""
    items = []
    for k, n in enumerate(names, 1):
        name, obj = (n if isinstance(n, tuple) else (n, "A"))
        items.append(itm(obj, name if ids_by_name else str(k), name, unit=unit, obj_name=obj_name))
    return table(tid, title, items, path=path, periods=periods, units=units)


# ---- test_aflow_units.py (SYN_A month 2008.01-2026.08) and the stage-1/2 tests that reuse it
_UNITS_M = (("월", "200801", "202608"),)
UNITS = [
    table("SYN_A", "가계대출 잔액", [
        itm("ITEM", "I1", "잔액", unit="십억원"), itm("A", "10", "가계", unit="십억원"),
        itm("A", "11", "주택담보", parent="10", unit="십억원"),
        itm("B", "10", "다른축", unit="십억원", order=2),  # same item ID on another axis: not a parent
        itm("C", "20", "순환갑", parent="21", unit="십억원", order=3),
        itm("C", "21", "순환을", parent="20", unit="십억원", order=3),
    ], path="금융 > 대출", periods=_UNITS_M, units=("십억원",)),
    table("SYN_B", "기업대출 잔액", [itm("ITEM", "I1", "잔액", unit="십억원"), itm("A", "30", "중소기업", unit="십억원")],
          path="금융 > 대출", periods=_UNITS_M, units=("십억원",)),
    table("SYN_C", "가계 소비지출", [itm("ITEM", "I1", "지출", unit="십억원"), itm("A", "40", "가계", unit="십억원")],
          path="가계 > 소비", periods=_UNITS_M, units=("십억원",)),
]

# ---- test_aflow_stage3_rules.py
_P = "가상 > 물가"
STAGE3 = [
    table("T_BASIC", "가상물가지수(기본분류)", [itm("A", "1", "총지수", unit="지수"), itm("A", "2", "공산품", "1", "지수"),
                                         itm("A", "3", "서비스", "1", "지수")], path=_P),
    table("T_SPECIAL", "가상물가지수(특수분류)", [itm("A", "1", "신선식품", unit="지수"), itm("A", "2", "신선식품이외", unit="지수")],
          path=_P),
    table("T_ITEM", "가상물가지수(품목별)", [itm("A", "1", "공산품", unit="지수"), itm("A", "2", "산업용전력", "1", "지수")],
          path=_P),
    table("T_RAW", "가상통화(말잔, 원계열)", [itm("A", "1", "가상통화", unit="지수")], path="가상 > 통화"),
    table("T_SA", "가상통화(말잔, 계절조정계열)", [itm("A", "1", "가상통화", unit="지수")], path="가상 > 통화"),
    table("T_SECTOR", "가상신용(업권별, 분기)", [itm("A", "1", "가상신용", unit="지수")], path="가상 > 신용"),
    table("T_USE", "가상신용(용도별, 분기)", [itm("A", "1", "가상신용", unit="지수")], path="가상 > 신용"),
    table("T_FRESH", "신선식품 가상물가", [itm("A", "1", "합계", unit="지수")], path="기타 > 물가"),
    table("T_FX", "가상환율지수", [itm("S", "1", "원계열", unit="지수"), itm("S", "2", "계절조정", unit="지수")],
          path="가상 > 환율"),
]

# ---- test_q4_axes_v1.py
_AXES_ITEM = {"T_CSI": "가계부채전망CSI", "T_GAS": "도시가스"}
Q4_AXES = [
    table(tid, title, [itm("A", "1", _AXES_ITEM.get(tid, "합계"), unit="지수")], path="가상 > 조사")
    for tid, title in (("T_CSI", "가상소비자동향조사(전국, 월)"), ("T_GAS", "가상생산자물가지수(기본분류)"),
                       ("T_ACT", "가상기업경기조사(실적)"), ("T_OUT", "가상기업경기조사(전망)"), ("T_MKT", "가상발행잔액(시가)"),
                       ("T_FACE", "가상발행잔액"), ("T_SAMPLE", "가상성장성 지표(1990~1997, 표본조사)"))
]

# ---- test_q4_handoff_v1.py (cat, cur_cat, unit_cat and the one-table catalog of test_comparison_item_needs_word_end)
Q4_HANDOFF = [
    one_axis("T_GROWTH", "가상국민계정 주요지표(분기)", ["가상총소득(전기비)", "가상총소득(전년동기비)"]),
    one_axis("T_INDEX", "가상수출물가지수(기본분류)", ["가상총지수"]),
    one_axis("T_CUR", "가상국민계정 주요지표(연간)", ["1인당 가상총소득(명목 원화표시)", "1인당 가상총소득(명목 달러표시)"]),
    one_axis("T_IMP", "가상수입물가지수(기본분류)", ["원화기준", "계약통화기준", "달러기준"]),
]
Q4_CURRENCY = [
    one_axis("T_PAY", "가상결제통화(수출)", ["달러 (미국)", "원화 (미국)", "싱가폴달러 (기타)"]),
    one_axis("T_DEP", "가상총수신(말잔)", ["원화예금", "외화예금"]),
    one_axis("T_FEE", "가상생산자물가지수(품목별)", ["원화수입수수료", "가상품목"]),
]
Q4_UNIT = [
    table("T_BOP", "가상국제수지", [itm("A", "가상경상수지", "가상경상수지", unit="백만달러")], units=("백만달러",)),
    table("T_WON", "가상예금", [itm("A", "가상예금", "가상예금", unit="십억원")], units=("십억원",)),
    table("T_FX", "가상총수신", [itm("A", "원화예금", "원화예금", unit="십억원"), itm("A", "외화예금", "외화예금", unit="십억원")],
          units=("십억원",)),
]
Q4_BIDET = [one_axis("T_BIDET", "가상소비자물가지수(품목별)", ["전기비데", "가상품목"])]
# test_currency_item_grammar: one value name per table (made-up table IDs T_G01..T_G12, neutral title and unit)
CURRENCY_GRAMMAR_NAMES = [
    ("달러", "달러 (미국)", True), ("달러", "미달러화", True), ("달러", "달러기준", True),
    ("달러", "1인당 가상총소득(명목 달러표시)", True), ("달러", "싱가폴달러 (기타)", False), ("달러", "홍콩달러화", False),
    ("달러", "외화자금이체(백만달러)", False), ("원화", "원화기준", True), ("원화", "만기2년이상원화채권", True),
    ("원화", "원화예금", True), ("원화", "원화수입수수료", False), ("계약통화", "계약통화기준", True),
]
CURRENCY_GRAMMAR = [one_axis(f"T_G{n:02d}", "가상통계표", [name]) for n, (_, name, _) in enumerate(CURRENCY_GRAMMAR_NAMES, 1)]
# test_unit_currency_needs_clear_money_unit: table unit / value units (made-up table IDs T_C1..T_C5)
UNIT_CURRENCY = [
    table("T_C1", "가상통계표", [itm("A", "1", "가상값")], units=("백만달러",)),
    table("T_C2", "가상통계표", [itm("A", "1", "가상값", unit="억달러"), itm("A", "2", "가상비율", unit="%")]),
    table("T_C3", "가상통계표", [itm("A", "1", "가상값", unit="달러"), itm("A", "2", "가상금액", unit="만원")]),
    table("T_C4", "가상통계표", [itm("A", "1", "가상값")], units=("원/달러",)),
    table("T_C5", "가상통계표", [itm("A", "1", "가상값")], units=("%",)),
]
# test_unit_compare_uses_scale_and_base: table units (made-up table IDs T_U1..T_U6)
UNIT_COMPARE_ROWS = [("원", ["십억원"], "rescale"), ("십억원", ["십억원", "%"], "same"), ("%", ["십억원"], "differ"),
                     ("달러", ["백만달러"], "rescale"), ("원", ["지수"], "differ"), ("원", [], "unknown")]
UNIT_COMPARE = [table(f"T_U{n}", "가상통계표", [itm("A", "1", "가상값")], units=tuple(have))
                for n, (_, have, _) in enumerate(UNIT_COMPARE_ROWS, 1)]

# ---- test_q5_lists_v1.py
Q5_LISTS = [
    one_axis("T_BOP", "가상수지표", ["가상경상수지", "가상상품수지", "가상서비스수지", "가상이전수지", "가상기타서비스수지",
                                "가상가공서비스수지"]),
    one_axis("T_OTHER", "가상다른표", ["가상상품"]),
    one_axis("T_MERGE", "가상애로표", ["가상인력난 가상인건비", "가상특허 및 가상실용권", "가상상표권", "가상전자및가상광학"]),
    one_axis("T_CARD", "가상카드표", ["가상개인 이용건수", "가상법인 이용건수", "가상신용카드", "가상체크카드", "가상선불카드"]),
    one_axis("T_PRICE", "가상생산자물가지수", ["가상식료품", "가상에너지", "가상신선식품", "가상원재료", "가상중간재"]),
    one_axis("T_TERM", "가상기간금리", ["1년", "3년"]),
    one_axis("T_LOAN", "가상차주대출", [("연령", "AGE"), ("지역", "AREA")]),
]

# ---- test_q5_phrase_v1.py (year 2000-2025, unit %)
Q5_PHRASE = [
    one_axis(tid, title, names, unit="%", periods=Y_2000) for tid, title, names in (
        ("T_GROW", "가상기업 성장성 지표(가상법 기준)", ["가상매출액증가율"]),
        ("T_DEP", "가상은행 수신금리(잔액 기준)", ["총수신", "정기예금"]),
        ("T_LOAN", "가상은행 대출금리(잔액 기준)", ["총대출", "가계대출"]),
        ("T_CD", "가상시장 금리", ["가상CD"]),
        ("T_IDX", "가상소비자 지수", ["가상현재판단", "가상향후전망"]))
]
# test_phrase_end_rule_unit: the old known() set {"가상전망", "가상현재", "지수"} as catalog names (made-up table)
Q5_PHRASE_KNOWN = [one_axis("T_KNOWN", "가상지수표", ["가상전망", "가상현재", "지수"])]
# test_i_ga_ending_kept_when_word_is_in_catalog / test_subject_particle_...: "가상생산자물가" is a catalog name
KNOWN_PPI = [one_axis("T_KNOWN", "가상생산자물가", ["가상원재료"])]
# test_shared_tail_needs_same_table: the two names live in different tables
Q5_TAIL_SPLIT = [one_axis("T1", "가상표하나", ["가상이전수지"]), one_axis("T2", "가상표둘", ["가상상품수지"])]

# ---- test_q7_measure_scopes_v1.py
Q7_SCOPES = [
    one_axis("T_NEW", "가상예금 금리(신규취급액 기준)", ["가상예금", "가상예탁"], unit="%"),
    one_axis("T_BAL", "가상예금 금리(잔액 기준)", ["가상예금", "가상예탁"], unit="%"),
    one_axis("T_ONE", "가상단일 지수(신규취급액 기준)", ["가상단일"], unit="%"),
]
Q7_YEARLY = [one_axis("T_Y", "가상금리 연간 통계", ["가상갑", "가상병"], unit="%", periods=Y_2000)]

# ---- test_j4_byeol_linked_v1.py (year 2010-2025, unit 백만달러); SYN_B2 and SYN_K2 are made up here
_J4 = (("년", "2010", "2025"),)


def _j4(tid, title, values):
    items = [itm("ITEM", "I1", values[0], unit="백만달러", obj_name="구분")]
    items += [itm("A", str(k), v, unit="백만달러", obj_name="구분") for k, v in enumerate(values[1:], 1)]
    return table(tid, title, items, periods=_J4, units=("백만달러",))


J4_LINKED = [
    _j4("SYN_K", "가상 서비스 수출(기관형태별)", ["수출", "대기업", "중견기업", "중기업", "원계열", "계절조정"]),
    _j4("SYN_B", "가상 가계신용(업권별)", ["잔액", "예금은행", "가계대출"]),
    _j4("SYN_P", "가상 서비스 수입", ["수입", "합계"]),
    _j4("SYN_B2", "가상 가계신용(업권별)", ["잔액", "예금은행", "가계대출", "비은행"]),  # third value on the same axis
    _j4("SYN_K2", "가상 서비스 수출(기관형태별)", ["수출", "대기업", "대기업(제조)"]),  # one word, two value names
]

# ---- test_j5_measure_path_phrase_v1.py (year 2013-2025, unit 십만원)
_J5 = (("년", "2013", "2025"),)


def _j5(tid, title, path, values):
    items = [itm(obj, item, name, unit="십만원", obj_name="구분") for obj, item, name in values]
    return table(tid, title, items, path=path, periods=_J5, units=("십만원",))


J5_PATH = [
    _j5("SYN_X", "가상 교차표", "가상통계 > 차주별 부채 > 차주당 가계대출 잔액 (교차항목)", [("A", "1", "20대"), ("B", "1", "수도권")]),
    _j5("SYN_E", "가상 교차표2", "가상통계 > 차주별 부채 > 차주당 가계대출 잔액 및 연체", [("A", "1", "20대")]),
    _j5("SYN_S", "가상 교차표3", "가상통계 > 차주당 부채 > 가계대출 잔액", [("A", "1", "20대")]),
    _j5("SYN_U", "가상 세부표", "가상통계 > 차주당 가계대출 잔액 > 세부 구성", [("A", "1", "20대")]),
    _j5("SYN_N", "가상 신규표", "가상통계 > 차주별 부채 > 차주당 가계대출 신규취급액", [("A", "1", "20대")]),
    _j5("SYN_P", "가상 일부표", "가상통계 > 차주별 부채 > 가계대출 잔액", [("A", "1", "20대")]),
    _j5("SYN_1", "가상 한축표", "가상통계 > 차주별 부채 > 차주당 가계대출 잔액", [("A", "1", "20대"), ("A", "2", "수도권")]),
]

# ---- test_measure_evidence.py (year 2000-2025)
MEASURE = [
    table(tid, title, [itm("ITEM", "main", title)] + [itm("A", str(k), label, obj_name="분류")
                                                      for k, label in enumerate(labels)],
          path=path, periods=Y_2000)
    for tid, (title, path, labels) in {
        "BAD": ("산업정보(실질)", "가상 > 산업", ["XYZ"]),
        "GOOD": ("관측정보(실질)", "가상 > 전국", ["가상생산(실질증가율)", "가상생산(XYZ)"]),
        "NONE": ("다른 주제(실질)", "가상 > 가상생산조사", ["지역별", "서울"]),
        "PATH": ("보충정보", "가상 > 가상생산", ["금액"]),
    }.items()
]

# ---- test_j4_cross_axis_v1.py: one table "T" per case (axes as (obj_id, axis name), values as (obj_id, name))


def cross_axis_table(dims: list, values: list, title: str = "가상 표") -> list[dict]:
    names = dict(dims)
    items = [itm(obj, f"{obj}.{k}", name, obj_name=names[obj], order=list(names).index(obj) + 1)
             for k, (obj, name) in enumerate(values)]
    return [table("T", title, items)]


# ---- test_t3_cross_axis_audit_v1.py: the graph catalog (values "연령"/"지역" on one axis "구분")
_M_2026_09 = (("월", "2000.01", "2026.09"),)
T3_AUDIT = [
    table("T", "가상대출 잔액", [itm("A", "1", "가상대출 잔액", obj_name="계정항목"),
                             itm("B", "b1", "연령", obj_name="구분", order=2), itm("B", "b2", "지역", obj_name="구분", order=2)],
          path="가상 > 가상대출 잔액", periods=_M_2026_09),
    table("U", "다른수치", [itm("A", "1", "다른수치", obj_name="분류")], path="가상 > 다른수치", periods=_M_2026_09),
]

# ---- test_cx_item_prefix.py: values whose first component is the listed name (T_PREFIX) or the exact name (T_EXACT)
CX_PREFIX = [
    table(tid, title, [itm("AREA", str(k), name, obj_name="구역별") for k, name in enumerate(names)],
          path="가상 > 가상지출", periods=Y_2000)
    for tid, title, names in (("T_PREFIX", "가상지출", ["북부-교육비", "북부-보건비", "남부-교육비", "남부-보건비"]),
                              ("T_EXACT", "가상지출 참고", ["북부", "남부", "북부-교육비", "남부-교육비"]))
]

# ---- period-check cases (J3 compare_period and friends): made-up table IDs T_P*
PERIOD_TABLES = [
    table("T_PY", "가상연간표", [itm("A", "1", "가상값")], periods=(("년", "1990", "2010"),)),
    table("T_PM", "가상월간표", [itm("A", "1", "가상값")], periods=(("월", "2008.01", "2026.08"),)),
    table("T_PHALF", "가상혼합표", [itm("A", "1", "가상값")],
          periods=(("월", "2000.01", "2026.08"), ("반기", "2000 1/2", "2026 1/2"), ("년", "2000", "2025"))),
    table("T_PTHIS", "가상올해표", [itm("A", "1", "가상값")], periods=(("월", "2000.01", "2026.01"), ("년", "2000", "2025"))),
    table("T_POLD", "가상옛표", [itm("A", "1", "가상값")], periods=(("년", "2000", "2023"),)),
    table("T_PSINCE", "가상긴월표", [itm("A", "1", "가상값")], periods=(("월", "2000.01", "2026.08"),)),
]

# ---- plan 09 (implementer cases, to be confirmed): derived names of the name index and the structure fixes; one
# value axis "계정항목별" per table (made-up table IDs T_D*, T_S*)
def _acc(tid: str, title: str, names: list, path: str = "가상 > 통계") -> dict:
    return table(tid, title, [itm("A", f"A{k}", n, obj_name="계정항목별") for k, n in enumerate(names, 1)], path=path)


P09_JUDGEMENT: dict[str, list[dict]] = {
    # P1 table_core: the first word 가상은행 starts two name families; 가상단독 one only (and no path node)
    "jt_d_core": [
        _acc("T_DR1", "가상은행 가상대출금리(신규취급액 기준)", ["가상가계대출", "가상기업대출"]),
        _acc("T_DR2", "가상은행 가상대출금리(잔액 기준)", ["가상가계대출", "가상기업대출"]),
        _acc("T_DR3", "가상은행 가상수신금리(신규취급액 기준)", ["가상정기예금"]),
        _acc("T_DR4", "가상단독 가상여신잔고(말잔)", ["가상여신"]),
    ],
    # P1 no_tail: the name without its tail word; not when that form is a value name
    "jt_d_tail": [
        _acc("T_DP1", "가상수입물가지수(가상기본분류)", ["가상원재료", "가상중간재"]),
        _acc("T_DP2", "가상수입물가지수(가상용도별)", ["가상원재료"]),
        _acc("T_DP3", "가상환율지수", ["가상환율"]),
    ],
    # P1 mark-free key of a table name ("가상자산/가상자본 지표" written with a space)
    "jt_d_mark": [
        _acc("T_DM1", "가상자산/가상자본 지표(가상분류)", ["가상부채비율"]),
        _acc("T_DM2", "가상재무표", ["가상자산", "가상자본"]),
    ],
    # P1 path_node (and table_core by its path node): no first word shared, the list node stands in both names
    "jt_d_path": [
        _acc("T_DQ1", "가상기관 가상연체율(잔액)", ["가상가계"], path="가상 > 통계 > 가상연체율"),
        _acc("T_DQ2", "가상조합 가상연체율(잔액)", ["가상가계"], path="가상 > 통계 > 가상연체율"),
    ],
    # P1 abbr: a Latin abbreviation of a value name; not the other side of a table's "A 및 B"
    "jt_d_abbr": [
        _acc("T_DA1", "가상소득표", ["가상총소득(VGN)", "가상순소득"]),
        _acc("T_DA2", "가상활동별 가상GDP 및 VGNI(명목)", ["가상GDP", "가상총소득(VGNI)"]),
        _acc("T_DA3", "가상활동별 가상투자(명목)", ["가상투자"]),
    ],
    # P2b: a list that an adnominal verb takes before a head name
    "jt_s_mod": [
        _acc("T_SM1", "가상심리지수", ["가상심리지수", "가상경기"]),
        _acc("T_SM2", "가상물가지수", ["가상원재료", "가상중간재"]),
    ],
    # P2c: table names written apart (a genitive inside; a negation between the two parts)
    "jt_s_split": [
        _acc("T_SS1", "가상수출금액지수", ["가상총지수"]),
        _acc("T_SS2", "가상수출물량지수", ["가상총지수"]),
        _acc("T_SS3", "가상선불가상수단", ["가상발행액"]),
        _acc("T_SS4", "가상직불가상수단", ["가상발행액"]),
        _acc("T_SS5", "가상수단", ["가상발행액"]),
        table("T_SS6", "가상조합 가상대출금리", [itm("O", "O1", "가상조합", obj_name="기관코드별"),
                                           itm("O", "O2", "가상금고", obj_name="기관코드별")]),
        table("T_SS7", "가상대출금리", [itm("O", "O1", "가상조합", obj_name="기관코드별")]),
        # review of plan 09: a family of two tables sharing the name before the bracket
        _acc("T_SS8", "가상수입금액지수(원계열)", ["가상총지수"]),
        _acc("T_SS9", "가상수입금액지수(계절조정계열)", ["가상총지수"]),
    ],
    # user decision 10/19 (plan 09): conditions after a list of two indicators, a general noun after them that is
    # also a value name ("금액")
    "jt_s_tail": [
        table("T_ST1", "가상활동별 가상GDP 및 가상GNI(가상분류)",
              [itm("A", "A1", "가상GDP", obj_name="계정항목별"), itm("A", "A2", "가상GNI", obj_name="계정항목별"),
               itm("P", "P1", "명목", obj_name="기준년도구분별", order=2),
               itm("P", "P2", "실질", obj_name="기준년도구분별", order=2),
               itm("S", "S1", "원계열", obj_name="기준년도구분별", order=3),
               itm("S", "S2", "계절조정", obj_name="기준년도구분별", order=3),
               itm("M", "M1", "금액", obj_name="가상금액구분별", order=4),
               itm("M", "M2", "비중", obj_name="가상금액구분별", order=4)]),
    ],
}

CATALOGS: dict[str, list[dict]] = {
    "units": UNITS, "stage3": STAGE3, "q4_axes": Q4_AXES, "q4_handoff": Q4_HANDOFF, "q4_currency": Q4_CURRENCY,
    "q4_unit": Q4_UNIT, "q4_bidet": Q4_BIDET, "currency_grammar": CURRENCY_GRAMMAR, "unit_currency": UNIT_CURRENCY,
    "unit_compare": UNIT_COMPARE, "q5_lists": Q5_LISTS, "q5_phrase": Q5_PHRASE, "q5_phrase_known": Q5_PHRASE_KNOWN,
    "known_ppi": KNOWN_PPI, "q5_tail_split": Q5_TAIL_SPLIT, "q7_scopes": Q7_SCOPES, "q7_yearly": Q7_YEARLY,
    "j4_linked": J4_LINKED, "j5_path": J5_PATH, "measure": MEASURE, "periods": PERIOD_TABLES,
    "xa_distinct": cross_axis_table([("AGE", "연령별"), ("REG", "지역별")], [("AGE", "20대"), ("REG", "서울")]),
    "xa_one_axis": cross_axis_table([("ACC", "계정항목별")],
                                    [("ACC", "연령별"), ("ACC", "20대"), ("ACC", "지역별"), ("ACC", "서울")]),
    "xa_title_only": cross_axis_table([("G", "구분코드별"), ("ACC", "계정항목별")], [("G", "20대"), ("ACC", "서울")],
                                      title="연령별 지역별"),
    # the old meta had the two axes without values; the loader builds axes from value rows, so one value each
    "xa_no_values": cross_axis_table([("AGE", "연령별"), ("REG", "지역별")], [("AGE", "20대"), ("REG", "서울")]),
    "xa_shared_plus": cross_axis_table([("ACC", "계정항목별"), ("REG", "지역별")],
                                       [("ACC", "연령별"), ("ACC", "지역별"), ("REG", "서울")]),
    "xa_same_value": cross_axis_table([("RG", "지역코드별")], [("RG", "지역별")]),
    "xa_combined": cross_axis_table([("ACC", "계정항목별")], [("ACC", "연령별"), ("ACC", "지역별"), ("ACC", "20대-수도권")]),
    "xa_middle_dot": cross_axis_table([("ACC", "계정항목별")], [("ACC", "연령별"), ("ACC", "지역별"), ("ACC", "강원·제주권")]),
    "xa_other_axis": cross_axis_table([("ACC", "계정항목별"), ("UNIT", "단위별")],
                                      [("ACC", "연령별"), ("ACC", "지역별"), ("UNIT", "원/달러")]),
    # test_t3_cross_axis_v1.py boundary tables (two combined values; a bracketed middle-dot value)
    "xa_combined_two": cross_axis_table([("ACC", "계정항목별")],
                                        [("ACC", "연령별"), ("ACC", "지역별"), ("ACC", "20대-수도권"), ("ACC", "30대-수도권")]),
    "xa_middle_dot_paren": cross_axis_table([("ACC", "계정항목별")],
                                            [("ACC", "연령별"), ("ACC", "지역별"), ("ACC", "(인천·경기)"), ("ACC", "강원·제주권")]),
    "t3_audit": T3_AUDIT, "cx_prefix": CX_PREFIX,
}
