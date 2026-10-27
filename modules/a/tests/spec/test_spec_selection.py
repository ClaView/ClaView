"""Table selection (plan 04, 8 "표 선택"; 4-3 to 4-6): pipeline cases on synthetic catalogs.

Each case interprets a made-up question on a made-up catalog and runs ``select_tables`` (3.1-3.8, model off). The
expectations are the plan's rules, not answers of the evaluation sets: year ranges joined (two and three tables),
frequency not set, frequency and period not multiplied apart, basis not set (all tables put in, over the cap),
least target cover, the missing cell combination, competing families (pending), no evidence after the retry, the
cover recheck, one query group for requirements that share a table, and the latest data when no period is given.
Table IDs and names are made up here.
"""

from __future__ import annotations

import pytest
from spec_catalogs import Q7_SCOPES
from spec_support import Layers, itm, table

from claview_a.contract.check import check_evidence, check_selections
from claview_a.interpret.pipeline import interpret_question
from claview_a.select.graph import select_tables


def _y(a: str, b: str) -> tuple:
    return ("년", a, b)


def _m(a: str, b: str) -> tuple:
    return ("월", a, b)


def _t(tid: str, title: str, values: list[str], periods: list[tuple]) -> dict:
    return table(tid, title, [itm("A", str(k), v, unit="%") for k, v in enumerate(values, 1)], periods=periods)


M_ALL = [_m("2000.01", "2026.08")]
CATALOGS = {
    "years": [_t("T_A", "가상재무표(1998~2007)", ["가상부채비율", "전산업"], [_y("1998", "2007")]),
              _t("T_B", "가상재무표(2007~2010)", ["가상부채비율", "전산업"], [_y("2007", "2010")]),
              _t("T_C", "가상재무표(2009~)", ["가상부채비율", "전산업"], [_y("2009", "2025")])],
    "freq": [_t("T_MIX", "가상수출표", ["가상수출액"], [_y("2000", "2025"), _m("2010.01", "2026.08")]),
             _t("T_MON", "가상월간수출표", ["가상수출액"], [_m("2000.01", "2026.08")])],
    "freq_one": [_t("T_MIX", "가상수출표", ["가상수출액"], [_y("2000", "2025"), _m("2010.01", "2026.08")])],
    "basis": [_t("T_RAW", "가상통화량(원계열)", ["가상통화량"], M_ALL),
              _t("T_SA", "가상통화량(계절조정계열)", ["가상통화량"], M_ALL)],
    "basis4": [_t("T_MR", "가상총통화(말잔, 원계열)", ["가상총통화"], [_m("2005.01", "2026.08")]),
               _t("T_MS", "가상총통화(말잔, 계절조정계열)", ["가상총통화"], [_m("2004.01", "2026.08")]),
               _t("T_PR", "가상총통화(평잔, 원계열)", ["가상총통화"], [_m("2003.01", "2026.08")]),
               _t("T_PS", "가상총통화(평잔, 계절조정계열)", ["가상총통화"], [_m("2002.01", "2026.08")])],
    "targets": [_t("T_AB", "가상업종표", ["가상부채비율", "갑업", "을업"], [_y("2000", "2025")]),
                _t("T_GA", "가상갑업표", ["가상부채비율", "갑업"], [_y("2000", "2025")]),
                _t("T_EUL", "가상을업표", ["가상부채비율", "을업"], [_y("2000", "2025")])],
    "combo": [_t("T_G20", "가상갑업표", ["가상부채비율", "갑업"], [_y("2020", "2020")]),
              _t("T_E21", "가상을업표", ["가상부채비율", "을업"], [_y("2021", "2021")])],
    "fams": [_t("T_ONE", "가상국제수지", ["가상경상수지", "가상상품수지"], M_ALL),
             _t("T_TWO", "가상지역별 수지", ["가상경상수지", "가상아시아"], M_ALL)],
    "group": [_t("T_GN", "가상국민계정", ["가상국내총생산", "가상국민총소득"], [_y("2000", "2025")])],
    "latest": [_t("T_OLD", "가상물량표(2000~2010)", ["가상물량"], [_y("2000", "2010")]),
               _t("T_NEW", "가상물량표(2011~)", ["가상물량"], [_y("2011", "2025")])],
    # Codex r1 #5: two family tables a year apart at the end; no period -> only the one with the latest data
    "latest_close": [_t("T_24", "가상산출표(2000~2024)", ["가상산출"], [_y("2000", "2024")]),
                     _t("T_25", "가상산출표(2000~2025)", ["가상산출"], [_y("2000", "2025")])],
    # Codex r1 #1: "연령" and "지역" listed on one axis (no cross classification) / on two axes
    "cross_single": [table("T_LIST", "가상차주표", [itm("A", "1", "가상잔액"), itm("A", "2", "연령"),
                                                 itm("A", "3", "지역")], periods=M_ALL)],
    "cross_two": [table("T_CROSS", "가상차주 교차표", [itm("A", "1", "가상잔액"),
                                                  itm("AGE", "2", "20대", obj_name="연령별", order=2),
                                                  itm("REG", "3", "서울", obj_name="지역별", order=3)],
                        periods=M_ALL)],
    "q7_scopes": Q7_SCOPES,
    # Codex r3 and the representative policy (made-up tables)
    "r3_latest_basis": [_t("T_N", "가상산출(명목)", ["가상산출"], [_y("2000", "2025")]),
                        _t("T_R", "가상산출(실질)", ["가상산출"], [_y("2000", "2024")])],
    "r3_agency": [table("T_G", "가상산출표", [itm("A", "1", "가상산출"), itm("SRC", "2", "집계기관갑", obj_name="출처",
                                                                     order=2)], periods=[_y("2000", "2025")]),
                  table("T_E", "가상산출표", [itm("A", "1", "가상산출"), itm("SRC", "2", "집계기관을", obj_name="출처",
                                                                     order=2)], periods=[_y("2000", "2025")])],
    "rep_source": [_t("T_S1", "가상출처표", ["가상수량"], [_y("2000", "2024")]),
                   _t("T_S2", "가상출처표", ["가상수량"], [_y("2000", "2025")])],
    "rep_source_tie": [_t("T_N2", "가상출처표", ["가상수량"], [_y("2005", "2025")]),
                       _t("T_W", "가상출처표", ["가상수량"], [_y("2000", "2025")])],
    "rep_survey": [_t("T_ACT", "가상경기조사(실적)", ["가상업황"], M_ALL),
                   _t("T_OUT", "가상경기조사(전망)", ["가상업황"], M_ALL)],
    "rep_freq": [_t("T_Q", "가상수지표(분기)", ["가상경상수지"], [("분기", "2000 1/4", "2026 2/4")]),
                 _t("T_QY", "가상수지표(분기 및 연간)", ["가상경상수지"], [("분기", "2000 1/4", "2026 2/4"),
                                                                    _y("2000", "2025")])],
    "rep_other": [_t("T_BANK", "가상대출금(예금은행)", ["가상대출금"], M_ALL),
                  _t("T_NBANK", "가상대출금(비은행)", ["가상대출금"], M_ALL)],
    # Codex r4 (representative policy, made-up tables)
    "r4_stale_sibling": [_t("T_N", "가상산출(명목)", ["가상산출"], [_y("2000", "2025")]),
                         _t("T_R", "가상산출(실질)", ["가상산출"], [_y("2000", "2022")])],
    "r4_agency_names": [table("T_A", "가상산출표", [itm("A", "1", "가상산출"),
                                                itm("S", "2", "집계기관갑", obj_name="출처", order=2)],
                              periods=[_y("2000", "2025")]),
                        table("T_B", "가상산출표", [itm("A", "1", "가상산출"),
                                                itm("S", "2", "집계기관을", obj_name="작성기관", order=2)],
                              periods=[_y("2000", "2025")])],
    "r4_unknown_axis": [table("T_10", "가상산업표", [itm("A", "1", "가상산출"),
                                                 itm("K", "2", "KSIC10", obj_name="분류체계", order=2)],
                              periods=[_y("2000", "2025")]),
                        table("T_11", "가상산업표", [itm("A", "1", "가상산출"),
                                                 itm("K", "2", "KSIC11", obj_name="분류체계", order=2)],
                              periods=[_y("2000", "2025")])],
    "r4_bracket_words": [_t("T_BK", "가상대출금(예금은행 원계열)", ["가상대출금"], M_ALL),
                         _t("T_NB", "가상대출금(비은행 원계열)", ["가상대출금"], M_ALL)],
    "r4_base_year": [_t("T_10", "가상지수(2010=100)", ["가상지수"], M_ALL),
                     _t("T_15", "가상지수(2015=100)", ["가상지수"], M_ALL)],
    "r4_named": [_t("T_OLD", "가상출처표(구계열)", ["가상수량"], [_y("2000", "2024")]),
                 _t("T_NEW", "가상출처표(신계열)", ["가상수량"], [_y("2000", "2025")])],
    # Codex r2 (made-up tables)
    "r2_narrow_mate": [_t("T_N", "가상지표(명목)", ["가상지표", "갑업", "을업"], [_y("2000", "2025")]),
                       _t("T_R", "가상지표(실질)", ["가상지표", "갑업"], [_y("2000", "2025")])],
    "r2_axis_values": [_t("T_RAW", "가상통화량", ["가상통화량", "원계열"], [_y("2000", "2025")]),
                       _t("T_SA", "가상통화량", ["가상통화량", "계절조정계열"], [_y("2000", "2025")])],
    "r2_unit": [table("T_U", "가상수치표", [itm("A", "1", "가상수치", unit="%")], periods=[_y("2000", "2025")],
                      units=("%",))],
    "r2_lag_join": [_t("T_A", "가상산출표(2000~2025)", ["가상산출"], [_y("2000", "2025")]),
                    _t("T_B", "가상산출표(2026~)", ["가상산출"], [_y("2026", "2026")])],
    "r2_latest_fit": [_t("T_N", "가상산출(명목)", ["가상산출"], [_y("2000", "2025")]),
                      _t("T_R", "가상산출(실질)", ["가상산출"], [_y("2000", "2024")])],
    "r2_relative": [_t("T_Y", "가상연간표", ["가상연수치"], [_y("2000", "2025")])],
    # user decision 10/19 21:45 (fix B): names derived from table names (no exact table: an ungrounded split)
    "ungrounded": [_t("T_L1", "가상은행 가상대출금리(신규취급액 기준)", ["가상가계대출"], [_y("2000", "2025")]),
                   _t("T_L2", "가상은행 가상대출금리(잔액 기준)", ["가상가계대출"], [_y("2000", "2025")]),
                   _t("T_L3", "가상은행 가상수신금리(신규취급액 기준)", ["가상정기예금"], [_y("2000", "2025")])],
    "r2_cap": [_t("T_NG", "가상지표(명목)", ["가상지표", "갑업"], [_y("2000", "2025")]),
               _t("T_NE", "가상지표(명목)", ["가상지표", "을업"], [_y("2000", "2025")]),
               _t("T_RG", "가상지표(실질)", ["가상지표", "갑업"], [_y("2000", "2025")]),
               _t("T_RE", "가상지표(실질)", ["가상지표", "을업"], [_y("2000", "2025")])],
}


def case(key: str, layer: str, question: str, as_of: str | None = None, rep: dict | None = None,
         only: str | None = None, **expect) -> dict:
    """``expect``: the expectation under the keep_all policy (and under representative unless ``rep`` replaces
    it); ``only``: run under that policy only."""
    return {"key": key, "layer": layer, "question": question, "as_of": as_of, "expect": expect,
            "rep": rep if rep is not None else expect, "only": only}


def _mark(kind: str, others: int) -> dict:
    return {"rep_mark": (kind, others)}


CASES = [
    # 4-3 period segments: year-range family joined (two and three tables)
    case("years_three", "years", "2005년부터 2015년까지 가상부채비율을 보여줘", tables={"T_A", "T_B", "T_C"},
         periods={"T_A": ["2005-01~2006-12", "2007-01~2007-12"], "T_C": ["2009-01~2010-12", "2011-01~2015-12"]},
         not_reasons=["partial_cover"]),
    case("years_two", "years", "2005년부터 2008년까지 가상부채비율을 보여줘", tables={"T_A", "T_B"},
         not_reasons=["partial_cover"]),
    # decision 2: no period -> the latest
    case("no_period_latest", "years", "가상부채비율을 보여줘", tables={"T_C"}, periods={"T_C": ["latest"]}),
    case("no_period_latest_split_family", "latest", "가상물량을 보여줘", tables={"T_NEW"}),
    # 4-3 frequency x period pairs are not multiplied apart (yearly 2000~, monthly 2010~)
    case("monthly_before_monthly_data", "freq", "2005년 월별 가상수출액을 보여줘", tables={"T_MON"},
         frequencies={"T_MON": ["M"]}),
    case("yearly_inside_yearly_data", "freq", "2005년 연간 가상수출액을 보여줘", tables={"T_MIX"}),
    case("monthly_before_monthly_data_only_table", "freq_one", "2005년 월별 가상수출액을 보여줘", tables=set(),
         status="no_table", reasons=["no_cover"]),
    # 4-3 frequency not set: not split, not filtered
    case("frequency_not_set", "freq_one", "2015년 가상수출액을 보여줘", tables={"T_MIX"},
         frequencies={"T_MIX": []}),
    # 4-4 / decision 5: basis not set -> every table, marked; over the cap -> 3 + more
    # representative (decision 5 as changed): mixed basis not set -> rank 1 alone, marked, confirm
    case("basis_not_set_all", "basis", "가상통화량 추이를 보여줘", tables={"T_RAW", "T_SA"}, status="confirm",
         reasons=["unspecified"], unspecified=[("seasonal", {"T_RAW", "T_SA"}, False)],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("mixed_basis", 1)}),
    case("basis_set", "basis", "가상통화량 원계열을 보여줘", tables={"T_RAW"}, not_reasons=["unspecified"]),
    case("basis_not_set_over_cap", "basis4", "가상총통화 추이를 보여줘", count=3, status="confirm",
         reasons=["unspecified", "unspecified_more"],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], "not_reasons": ["unspecified_more"],
              **_mark("mixed_basis", 3)}),
    # 4-3 least target cover: one table with both targets, not two
    case("targets_least_cover", "targets", "갑업과 을업의 가상부채비율을 보여줘", tables={"T_AB"},
         targets={"T_AB": ["갑업", "을업"]}),
    # 4-3 counterexample: 갑 2020 table + 을 2021 table leave 갑 2021 and 을 2020 open -> partial
    case("cell_combination_missing", "combo", "2020년부터 2021년까지 갑업과 을업의 가상부채비율을 보여줘",
         tables={"T_G20", "T_E21"}, status="confirm", reasons=["partial_cover"]),
    # 3.6 two families: first by rank, model step pending, confirm, difference rows by axis values
    case("families_pending", "fams", "가상경상수지 추이를 보여줘", count=1, status="confirm",
         reasons=["family_alternatives"], model_step="pending", alternatives=1, differences=True),
    # Codex r1 #5 / decision 2: no period -> only the table with the latest data, not one a year short
    case("no_period_latest_only", "latest_close", "가상산출을 보여줘", tables={"T_25"}, not_reasons=["unspecified"]),
    # Codex r1 #1: auto only when proven. A basis word no table settles, or two "X별" breakdowns that one axis
    # lists side by side (no cross classification), leave the cover unproven -> confirm
    case("basis_word_unproven", "basis", "실질 가상통화량을 보여줘", status="confirm",
         reasons=["unproven_condition"]),
    case("cross_breakdown_unproven", "cross_single", "가상잔액을 연령별·지역별로 보여줘", tables={"T_LIST"},
         status="confirm", reasons=["unproven_condition"]),
    case("cross_breakdown_proven", "cross_two", "가상잔액을 연령별·지역별로 보여줘", tables={"T_CROSS"},
         not_reasons=["unproven_condition"]),
    # Codex r2 #2: a family mate narrower than the chosen table (gives 갑 only) still shows the unset basis
    case("r2_narrow_mate_marked", "r2_narrow_mate", "2020년 갑업과 을업의 가상지표를 보여줘", tables={"T_N", "T_R"},
         status="confirm", reasons=["unspecified"], unspecified=[("price", {"T_N", "T_R"}, False)],
         rep={"tables": {"T_N"}, "status": "confirm", "reasons": ["unspecified"], **_mark("mixed_basis", 1)}),
    # Codex r2 #3: same title, basis read from the axis values (원계열 / 계절조정계열)
    case("r2_axis_values_marked", "r2_axis_values", "가상통화량 추이를 보여줘", tables={"T_RAW", "T_SA"},
         status="confirm", reasons=["unspecified"], unspecified=[("seasonal", {"T_RAW", "T_SA"}, False)],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("mixed_basis", 1)}),
    # Codex r2 #4: the unit fails -> the condition is not proven -> not auto
    case("r2_unit_fails", "r2_unit", "2020년 가상수치를 십억원 단위로 보여줘", tables={"T_U"}, status="confirm",
         reasons=["unproven_condition"]),
    # Codex r2 #5: a chosen table's period check is unknown (snapshot lag) -> not auto
    case("r2_period_unknown_table", "r2_lag_join", "2025년부터 2026년까지 가상산출을 보여줘", tables={"T_A", "T_B"},
         status="confirm", reasons=["period_unknown"]),
    # Codex r2 #6: the latest is computed among tables that meet the basis (실질 ends a year earlier)
    case("r2_latest_among_fit", "r2_latest_fit", "실질 가상산출을 보여줘", tables={"T_R"}),
    # Codex r2 #7: a relative period keeps its resolved cells; the open year stays uncovered
    case("r2_relative_keeps_cells", "r2_relative", "최근 2년 가상연수치를 보여줘", as_of="2026-10-12",
         tables={"T_Y"}, status="confirm", reasons=["partial_cover", "period_unknown"],
         periods={"T_Y": ["2025-01~2025-12"]}),
    # Codex r2 #10: the cap of 3 holds per dimension over the requirement, the cover kept
    case("r2_cap_per_dimension", "r2_cap", "2020년 갑업과 을업의 가상지표를 보여줘", count=3, status="confirm",
         reasons=["unspecified", "unspecified_more"], not_reasons=["partial_cover"],
         rep={"count": 2, "status": "confirm", "reasons": ["unspecified"], "not_reasons": ["partial_cover"],
              **_mark("mixed_basis", 1)}),
    # Codex r3 #2: the latest is computed within one basis value, so an unset-basis sibling ending a year earlier
    # (실질 to 2024, 명목 to 2025) stays to be marked
    case("r3_latest_keeps_unset_sibling", "r3_latest_basis", "가상산출 추이를 보여줘", tables={"T_N", "T_R"},
         status="confirm", reasons=["unspecified"], unspecified=[("price", {"T_N", "T_R"}, False)],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("mixed_basis", 1)}),
    # Codex r3 #3: two tables of one name that differ by the values of an agency/source axis
    case("r3_agency_values", "r3_agency", "2020년 가상산출을 보여줘", tables={"T_G", "T_E"}, status="confirm",
         reasons=["unspecified"], unspecified=[("qualifier", {"T_G", "T_E"}, False)],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("other", 1)}),
    # representative policy by dimension kind (research note, section 2): set by the question -> filtered;
    # not set -> one representative. period: years_* and no_period_latest* above (both policies)
    case("rep_source_latest_end", "rep_source", "2020년 가상수량을 보여줘", only="representative",
         tables={"T_S2"}, not_reasons=["unspecified"]),
    case("rep_source_wider_range", "rep_source_tie", "2020년 가상수량을 보여줘", only="representative",
         tables={"T_W"}, not_reasons=["unspecified"]),
    # user decision 2026-10-20 01:57 (plan 10: conventions emptied): survey view not set -> one table, marked
    case("rep_survey_convention", "rep_survey", "가상업황을 보여줘", only="representative", count=1,
         status="confirm", reasons=["unspecified"], conventions=[], **_mark("survey", 1)),
    case("rep_survey_set", "rep_survey", "가상업황 전망을 보여줘", only="representative", tables={"T_OUT"},
         not_reasons=["unspecified"]),
    case("rep_frequency_not_set", "rep_freq", "2020년 가상경상수지를 보여줘", only="representative", count=1,
         not_reasons=["unspecified"]),
    case("rep_frequency_set", "rep_freq", "2020년 연간 가상경상수지를 보여줘", only="representative",
         tables={"T_QY"}, not_reasons=["unspecified"]),
    case("rep_other_not_set", "rep_other", "가상대출금 추이를 보여줘", only="representative", count=1,
         status="confirm", reasons=["unspecified"], **_mark("other", 1)),
    case("rep_other_set", "rep_other", "예금은행 가상대출금 추이를 보여줘", only="representative", tables={"T_BANK"},
         not_reasons=["unspecified"]),
    # Codex r4: what is not compared or not classified is never a safe kind -> other (confirm)
    # #1 a sibling of a stale group (실질 ends three years earlier) is still compared and marked
    case("r4_stale_sibling_marked", "r4_stale_sibling", "가상산출 추이를 보여줘", only="representative",
         tables={"T_N"}, status="confirm", reasons=["unspecified"], **_mark("mixed_basis", 1)),
    # #2 agency/source axes compared whatever their names (출처 / 작성기관)
    case("r4_agency_axes_by_value", "r4_agency_names", "2020년 가상산출을 보여줘", tables={"T_A", "T_B"},
         status="confirm", reasons=["unspecified"],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("other", 1)}),
    # #3 an axis no word list knows (분류체계 KSIC10 / KSIC11) is still compared by its values
    case("r4_unknown_axis_values", "r4_unknown_axis", "2020년 가상산출을 보여줘", only="representative", count=1,
         status="confirm", reasons=["unspecified"], **_mark("other", 1)),
    # #4 bracket words compared word by word (예금은행 / 비은행 beside 원계열; base years 2010=100 / 2015=100)
    case("r4_bracket_words_kept", "r4_bracket_words", "2020년 가상대출금을 보여줘", tables={"T_BK", "T_NB"},
         status="confirm", reasons=["unspecified"],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("other", 1)}),
    case("r4_base_year_kept", "r4_base_year", "2020년 가상지수를 보여줘", tables={"T_10", "T_15"},
         status="confirm", reasons=["unspecified"],
         rep={"count": 1, "status": "confirm", "reasons": ["unspecified"], **_mark("other", 1)}),
    # #5 a table the question named by its full name is not replaced by a sibling
    case("r4_named_table_kept", "r4_named", "2020년 가상출처표(구계열)를 보여줘", only="representative",
         tables={"T_OLD"}),
    # 3.4 no evidence -> one retry -> no_table
    case("no_evidence_retry_no_table", "group", "가상없는말 추이를 보여줘", tables=set(), status="no_table",
         reasons=["no_evidence"], retried=True),
]
GROUP_CASES = [
    case("same_table_one_group", "group", "가상국내총생산과 가상국민총소득을 보여줘",
         groups=[(["r1", "r2"], ["T_GN"])]),
    case("one_requirement_one_group", "years", "2005년부터 2008년까지 가상부채비율을 보여줘",
         groups=[(["r1"], None)]),
]


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return Layers(tmp_path_factory, CATALOGS)


POLICIES = ("keep_all", "representative")


def _run(c: dict, layers, policy: str | None = None) -> dict:
    layer = layers.get(c["layer"])
    analysis = interpret_question(c["question"], layer)
    out = select_tables(analysis, layer, c.get("as_of"), policy)
    assert check_selections(out["selections"], out["selection_groups"]) == []
    assert check_evidence(out["evidence"]) == []
    return out


def _problems(c: dict, out: dict, policy: str = "keep_all") -> list[str]:
    exp = c["rep"] if policy == "representative" else c["expect"]
    sels = out["selections"]
    if len(sels) != 1:
        return [f"{len(sels)} selections"]
    s = sels[0]
    got = {t["table_id"] for t in s["tables"]}
    covers = {t["table_id"]: t["covers"] for t in s["tables"]}
    out_ = []
    if "tables" in exp and got != exp["tables"]:
        out_.append(f"tables {sorted(got)} != {sorted(exp['tables'])}")
    if "count" in exp and len(got) != exp["count"]:
        out_.append(f"{len(got)} tables != {exp['count']}")
    if "status" in exp and s["status"] != exp["status"]:
        out_.append(f"status {s['status']} != {exp['status']}")
    out_ += [f"reason {r} missing in {s['reasons']}" for r in exp.get("reasons", []) if r not in s["reasons"]]
    out_ += [f"reason {r} present" for r in exp.get("not_reasons", []) if r in s["reasons"]]
    for key in ("periods", "frequencies", "targets"):
        for tid, want in (exp.get(key) or {}).items():
            if covers.get(tid, {}).get(key) != want:
                out_.append(f"{tid} covers.{key} {covers.get(tid, {}).get(key)} != {want}")
    for dim, ids, more in exp.get("unspecified", []):
        if not any(u["dimension"] == dim and set(u["table_ids"]) == ids and u.get("more") == more
                   for u in s["unspecified"]):
            out_.append(f"unspecified {dim} {sorted(ids)} not in {s['unspecified']}")
    if "model_step" in exp and s["model_step"] != exp["model_step"]:
        out_.append(f"model_step {s['model_step']}")
    if "alternatives" in exp and len(s.get("alternatives") or []) != exp["alternatives"]:
        out_.append(f"alternatives {s.get('alternatives')}")
    if exp.get("differences") and not all(row["only_values"] or row["only_axes"] for row in s.get("differences", [])):
        out_.append(f"differences {s.get('differences')}")
    if "retried" in exp and s["signals"]["retried"] != exp["retried"]:
        out_.append("retry not used")
    if "rep_mark" in exp:
        kind, others = exp["rep_mark"]
        if not any(kind in (u.get("kind") or "") and len(u.get("table_ids") or []) == 1
                   and len(u.get("other_table_ids") or []) == others and u.get("other_values")
                   for u in s["unspecified"]):
            out_.append(f"no {kind} mark with one table and {others} others in {s['unspecified']}")
    if "conventions" in exp and s["signals"].get("conventions") != exp["conventions"]:
        out_.append(f"conventions {s['signals'].get('conventions')}")
    return out_


_RUNS = [(c, p) for c in CASES for p in POLICIES if c["only"] in (None, p)]


@pytest.mark.parametrize("c, policy", _RUNS, ids=[f"{c['key']}-{p}" for c, p in _RUNS])
def test_selection_case(c, policy, layers):
    assert _problems(c, _run(c, layers, policy), policy) == []


def test_default_policy_is_representative(layers):
    from claview_a.select.cover import DEFAULT_POLICY

    assert DEFAULT_POLICY == "representative"
    c = CASES[8]
    assert _problems(c, _run(c, layers), "representative") == []


@pytest.mark.parametrize("c", GROUP_CASES, ids=[c["key"] for c in GROUP_CASES])
def test_query_groups(c, layers):
    out = _run(c, layers)
    got = [(g["requirement_ids"], g["table_ids"]) for g in out["selection_groups"]]
    want = c["expect"]["groups"]
    assert [r for r, _ in got] == [r for r, _ in want]
    for (_, tables), (_, wanted) in zip(got, want):
        if wanted is not None:
            assert tables == wanted


@pytest.mark.parametrize("policy", POLICIES)
def test_ambiguous_basis_does_not_split_the_family(policy, layers):
    """Codex r1 #6: a basis word whose attribution is ambiguous (n/a) neither scores nor excludes: the
    신규취급액/잔액 family stays undecided wherever it is selected (r2 carries a listed word no table holds, which
    leaves its cell open whatever the basis). keep_all: both tables; representative: one, the other marked."""
    c = case("ambiguous", "q7_scopes", "가상부문 가상예탁 금리와 가상계정상 가상예금 금리를 신규취급액 기준으로 보여줘")
    out = _run(c, layers, policy)
    selected = [s for s in out["selections"] if s["tables"]]
    assert selected
    for s in selected:
        got = {t["table_id"] for t in s["tables"]}
        if policy == "keep_all":
            assert {"T_NEW", "T_BAL"} <= got, s
        else:
            marked = {x for u in s["unspecified"] for x in [*u["table_ids"], *u.get("other_table_ids", [])]}
            assert len(got) == 1 and {"T_NEW", "T_BAL"} <= marked and s["status"] == "confirm", s


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("question, covered", [("가상대출금리와 가상수신금리를 보여줘", True),
                                               ("2030년 가상대출금리와 가상수신금리를 보여줘", False)])
def test_ungrounded_requirement_is_selected_as_usual(policy, question, covered, layers):
    """User decision 10/19 21:45 (fix B): a requirement of an ungrounded split is selected as any other; it is
    not_in_catalog (no table) only when the chosen tables do not cover its own conditions (2030 is past the data)."""
    c = case("ungrounded", "ungrounded", question)
    layer = layers.get(c["layer"])
    analysis = interpret_question(question, layer)
    assert all("ungrounded" in r["reasons"] for r in analysis["interpretation"]["requirements"])
    out = _run(c, layers, policy)
    sels = {s["requirement_id"]: s for s in out["selections"]}
    assert set(sels) == {"r1", "r2"}
    for s in sels.values():
        if covered:
            assert s["tables"] and s["status"] != "no_table" and "not_in_catalog" not in s["reasons"], s
        else:
            assert not s["tables"] and s["status"] == "no_table" and s["reasons"] == ["not_in_catalog"], s
    if covered:
        assert {t["table_id"] for t in sels["r2"]["tables"]} == {"T_L3"} and sels["r2"]["status"] == "auto"


@pytest.mark.parametrize("policy", POLICIES)
def test_cover_recheck_keeps_covers_and_unspecified_marks(policy, layers):
    """4-5: every selected table carries what it covers; the unspecified marks name selected tables only."""
    out = _run(CASES[8], layers, policy)
    s = out["selections"][0]
    assert all(t["covers"]["periods"] for t in s["tables"])
    kept = {t["table_id"] for t in s["tables"]}
    assert all(set(u["table_ids"]) <= kept for u in s["unspecified"])
