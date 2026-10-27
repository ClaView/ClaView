"""Evidence checks (old J2 matched values, J3 frequency/period, J4 basis/unit/exclude/cross axis, measure evidence):
specification moved from the old contract tests (ledger targets J2, J3, J4, measure).

New shape (plan 02, 4-1; detail 3.4): one ``Check`` per name (and per condition for basis/unit/exclude) for each
requirement x table, with the old status in ``source_status``, the new decision in ``verdict`` and the 4-1 ``detail``
structure. Old roles select/handoff are splits_table/in_table. Pipeline cases interpret the question and check the
listed tables (candidate selection is stretch 3); direct cases call one check with a hand-written partial analysis,
the closest form of old tests that called a J helper with a hand-made state. Ambiguous conditions are judged but
get verdict n/a. "No indicator extracted" (available false, result None) and "extracted but no match" (result
"none") are different values. The 4-3 supplement adds the cross-axis records that old T3 tests read (only the
cross_axis part; T3/T4 decisions are stretch 3) and the "prefix value is no exact binding" row of the item-prefix
graph test. The old ``_byeol_linked`` unit asserts (excluded value, complement, other condition, negated match, list
condition IDs) are direct basis checks whose prior matched_items rows carry ``condition_id``, ``match_method`` and
``polarity``. All check functions are stretch 2 (xfail).
"""

from __future__ import annotations

import pytest
from spec_catalogs import CATALOGS, CURRENCY_GRAMMAR_NAMES, UNIT_COMPARE_ROWS
from spec_support import (Layers, behaviour_params, cond, evidence_problems, evidence_shape, number_parts,
                          pattrs, ex, run_evidence_case, shape_params)

from claview_a.interpret.text import normalize_text

UN = "test_aflow_units.py"
S2 = "test_aflow_stage2_rules.py"
S3 = "test_aflow_stage3_rules.py"
Q2 = "test_q2_periods_v2.py"
Q4A = "test_q4_axes_v1.py"
Q4H = "test_q4_handoff_v1.py"
Q7 = "test_q7_measure_scopes_v1.py"
J4L = "test_j4_byeol_linked_v1.py"
J4X = "test_j4_cross_axis_v1.py"
ME = "test_measure_evidence.py"
J5 = "test_j5_measure_path_phrase_v1.py"
T3X = "test_t3_cross_axis_v1.py"
T3A = "test_t3_cross_axis_audit_v1.py"
CXP = "test_cx_item_prefix.py"


def pipe(key: str, question: str, layer: str, tables: list[str], checks: list[dict] = (), *, absent=(),
         conditions=None, node: str = "check_evidence", param: str | None = None, as_of: str | None = None) -> dict:
    expect: dict = {"checks": list(checks)}
    if absent:
        expect["absent"] = list(absent)
    if conditions is not None:
        expect["conditions"] = conditions
    return {"key": key, "question": question, "layer": layer, "tables": tables, "node": node, "as_of": as_of,
            "param": param if param is not None else question, "expect": expect}


def direct(key: str, check: str, layer: str, table: str, conditions: list[dict], checks: list[dict] = (), *,
           absent=(), question: str | None = None, req_conditions: list[str] | None = None, prior=(),
           as_of: str | None = None, param: str | None = None, input_unchanged: bool = False,
           with_text: bool = False) -> dict:
    analysis: dict = {"conditions": conditions, "interpretation": {"requirements": [
        {"id": "r1", "condition_ids": req_conditions if req_conditions is not None else [c["id"] for c in conditions]}]}}
    if with_text:  # the check reads the question words around a condition (J4 "X별" linking)
        analysis = {"text": normalize_text(question), **analysis}
    expect: dict = {"checks": list(checks)}
    if absent:
        expect["absent"] = list(absent)
    return {"key": key, "question": question, "layer": layer, "tables": [table], "check": check,
            "node": f"check_{check}", "input": analysis, "prior": list(prior), "as_of": as_of, "param": param,
            "input_unchanged": input_unchanged, "expect": expect}


def chk(name: str, table: str, *, condition: str | None = None, requirement: str = "r1", detail: dict | None = None,
        **fields) -> dict:
    spec: dict = {"name": name, "table": table, "requirement": requirement, "check": {}}
    if condition is not None:
        spec["condition"] = condition
    for k in ("source_status", "verdict", "evidence_ids", "state"):
        if k in fields:
            spec["check"][k] = fields.pop(k)
    if detail is not None:
        spec["check"]["detail"] = detail
    spec.update(fields)  # stances, row_text_contains, rows_condition, rows_contains, check_not
    return spec


def basis_cond(cid: str, axis: str, value: str, polarity: str = "+", kind: str = "basis") -> dict:
    return {"id": cid, "kind": kind, "value": value, "attrs": {"axis": axis, "value": value, "polarity": polarity}}


def period_cond(attrs: dict) -> dict:
    return {"id": "P1", "kind": "period", "value": None, "attrs": attrs}


def by_freq(*rows) -> dict:
    return {"by_frequency": [dict(zip(("frequency", "status", "reason"), r)) for r in rows]}


# ---------------------------------------------------------------- J2 matched values

J2 = [
    pipe(f"{UN}::test_j2_parent_same_axis_only[-]", "주택담보", "units", ["SYN_A"],
         [chk("matched_items", "SYN_A", detail={"rows": [{"name": "주택담보", "parent_path": [{"item_id": "10", "name": "가계"}],
                                                          "depth": 1}]})]),
    pipe(f"{UN}::test_j2_parent_cycle_stops[-]", "순환갑", "units", ["SYN_A"],
         [chk("matched_items", "SYN_A", detail={"rows": [{"name": "순환갑", "parent_path": [{"item_id": "21", "name": "순환을"}],
                                                          "depth": 1}]})]),
    pipe(f"{S3}::test_j2_joins_neighbour_words[-]", "산업용 전력 가상물가", "stage3", ["T_ITEM"],
         [chk("matched_items", "T_ITEM", rows_contains=[{"name": "산업용전력", "term": "산업용 전력", "match_method": "nospace",
                                                          "parent_path": [{"item_id": "1", "name": "공산품"}], "depth": 1}])]),
    pipe(f"{S3}::test_exclude_finds_complement_first[-]", "신선식품을 뺀 가상물가지수", "stage3", ["T_SPECIAL", "T_FRESH"],
         [chk("matched_items", "T_SPECIAL", rows_condition="X1",
              detail={"rows": [{"name": "신선식품이외", "match_method": "complement", "polarity": "+"}]}),
          chk("exclude", "T_SPECIAL", condition="X1", source_status="match", verdict="pass",
              detail={"complement_found": True, "items": ["신선식품이외"]}),
          chk("exclude", "T_FRESH", condition="X1", source_status="conflict", verdict="fail")],
         conditions=[{"id": "X1", "kind": "exclude", "value": "신선식품"}]),
    pipe(f"{S3}::test_negative_basis_item_is_kept_as_excluded_value[-]", "가상환율지수 계절조정하지 않은 값", "stage3", ["T_FX"],
         [chk("matched_items", "T_FX", rows_condition="B1", detail={"rows": [{"name": "계절조정", "polarity": "-"}]}),
          chk("basis", "T_FX", condition="B1", source_status="match", detail={"status": "match", "role": "in_table"},
              row_text_contains="빼고")],
         conditions=[basis_cond("B1", "seasonal", "계절조정", "-")]),
]

# ---------------------------------------------------------------- J3 frequency and period

SNAPSHOT_END = [("2026년 9월 가계대출 잔액", "unknown", "snapshot_lag"),
                ("2026년 가계대출 잔액", "partial", "ended_before_request"),
                ("2030년 가계대출 잔액", "outside", "ended_before_request")]
COMPARE_PERIOD = [
    ("T_PY", pattrs("range", ex("2001년", "2001"), ex("2003년", "2003")), "'2001', '2003'", ("Y", "covered", None)),
    ("T_PY", pattrs("range", ex("2001년", "2001"), ex("2011년", "2011")), "'2001', '2011'", ("Y", "unknown", "snapshot_lag")),
    ("T_PY", pattrs("range", ex("2001년", "2001"), ex("2015년", "2015")), "'2001', '2015'",
     ("Y", "partial", "ended_before_request")),
    ("T_PY", pattrs("range", ex("1980년", "1980"), ex("1985년", "1985")), "'1980', '1985'",
     ("Y", "outside", "starts_after_request")),
    ("T_PY", pattrs("since", ex("2020년", "2020")), "'2020', None", ("Y", "outside", "ended_before_request")),
    ("T_PM", pattrs("since", ex("2005년", "2005")), "'2005', None", ("M", "partial", "starts_after_request")),
]
YEARLY_Q = "가상부문 가상갑 금리와 가상계정상 가상병 금리를 비교하되 금리만 월별로 보여줘"
YEARLY_DESIGNATED_Q = "가상부문 가상갑 금리와 가상계정상 가상병 금리를 비교하되 가상병 금리만 월별로 보여줘"
SPLIT_Q = "2014년 가계 기준과 2016년 기업 기준 대출 잔액을 각각"

J3 = [
    pipe(f"{S2}::test_j3_frequency_match_and_covered[-]", "2010년부터 2012년까지 월별 가계대출 잔액", "units", ["SYN_A"],
         [chk("frequency", "SYN_A", source_status="match", verdict="pass",
              detail={"requested": ["M"], "available": ["M"], "status": "match"}, stances=["support"]),
          chk("period", "SYN_A", detail={"request": {"start": "2010", "end": "2012", "form": "range"},
                                         **by_freq(("M", "covered", None))}, stances=["support"])]),
    pipe(f"{S2}::test_j3_frequency_conflict_keeps_period_rows[-]", "연간 가계대출 잔액", "units", ["SYN_A"],
         [chk("frequency", "SYN_A", source_status="conflict", verdict="fail", detail={"status": "conflict"},
              stances=["against"]),
          chk("period", "SYN_A", detail={"by_frequency": [{"frequency": "M", "reason": "not_requested"}]})]),
    *[pipe(f"{S2}::test_j3_period_against_snapshot_end[{n}]", q, "units", ["SYN_A"],
           [chk("period", "SYN_A", detail={**by_freq(("M", status, reason)),
                                           "coverage_status": "ended_before_request" if status == "outside" else "n/a"})])
      for n, (q, status, reason) in enumerate(SNAPSHOT_END, 1)],
    pipe(f"{S2}::test_q5_splits_when_period_binds_part_of_sentence[-]", SPLIT_Q, "units", ["SYN_A"],
         [chk("period", "SYN_A", requirement="r1", detail={"request": {"start": "2014"}}),
          chk("period", "SYN_A", requirement="r2", detail={"request": {"start": "2016"}})]),
    *[direct(f"{S2}::test_compare_period[{n}]", "period", "periods", tid, [period_cond(attrs)],
             [chk("period", tid, detail=by_freq(row))], param=param)
      for n, (tid, attrs, param, row) in enumerate(COMPARE_PERIOD, 1)],
    direct(f"{S2}::test_unresolved_period_is_unknown[-]", "period", "periods", "T_PY",
           [period_cond(pattrs("recent", relative=True, amount=6, unit="month", status="unresolved"))],
           [chk("period", "T_PY", detail=by_freq(("Y", "unknown", "period_unresolved")))]),
    direct(f"{S2}::test_period_envelope_uses_months_and_keeps_open_end[-]", "period", "periods", "T_PSINCE",
           [period_cond(pattrs("since", ex("2015년", "2015")))],
           [chk("period", "T_PSINCE", detail=by_freq(("M", "covered", "open_end")))]),
    direct(f"{Q2}::test_half_year_point_is_read_by_j3_and_envelope[-]", "period", "periods", "T_PHALF",
           [period_cond(pattrs("point", ex("2026년 상반기", "2026", unit="H", value="상")))],
           [chk("period", "T_PHALF", detail=by_freq(("M", "covered", None), ("H", "covered", None),
                                                    ("Y", "unknown", "snapshot_lag")))]),
    direct(f"{Q2}::test_this_year_since_is_open_end_not_freshness[-]", "period", "periods", "T_PTHIS",
           [period_cond(pattrs("since", ex("올해", alias="올해"), relative=True))],
           [chk("period", "T_PTHIS", detail=by_freq(("M", "covered", "open_end"), ("Y", "unknown", "snapshot_lag")))],
           as_of="2026-10-12"),
    direct(f"{Q2}::test_this_year_since_is_open_end_not_freshness[-]", "period", "periods", "T_POLD",
           [period_cond(pattrs("since", ex("올해", alias="올해"), relative=True))],
           [chk("period", "T_POLD", detail=by_freq(("Y", "outside", "ended_before_request")))], as_of="2026-10-12"),
    pipe(f"{Q7}::test_ambiguous_frequency_is_not_unmet[-]", YEARLY_Q, "q7_yearly", ["T_Y"],
         [chk("frequency", "T_Y", requirement=r, detail={"requested": []}, check_not={"verdict": "fail"})
          for r in ("r1", "r2")]),
    pipe(f"{Q7}::test_designated_frequency_conflict_stays_unmet[-]", YEARLY_DESIGNATED_Q, "q7_yearly", ["T_Y"],
         [chk("frequency", "T_Y", requirement="r2", verdict="fail", detail={"requested": ["M"], "status": "conflict"}),
          chk("frequency", "T_Y", requirement="r1", check_not={"detail": {"status": "conflict"}})]),
]

# ---------------------------------------------------------------- J4 basis, calc, unit

NEW_AXES = [
    ("가상기업경기조사 전망 지수", "T_OUT", "match"), ("가상기업경기조사 전망 지수", "T_ACT", "conflict"),
    ("가상기업경기조사 실적 지수", "T_ACT", "match"), ("가상발행잔액을 시가 기준으로", "T_MKT", "match"),
    ("가상발행잔액은 시가가 아니라 액면으로", "T_MKT", "conflict"), ("표본조사 가상성장성 지표", "T_SAMPLE", "match"),
    ("가상기업경기조사 전망 지수", "T_CSI", "unknown"), ("가상기업경기조사 실적 지수", "T_CSI", "unknown"),
    ("가상발행잔액을 시가 기준으로", "T_GAS", "unknown"),
]
CURRENCY_VERDICTS = [("달러", "T_PAY", "match", "in_table"), ("달러", "T_DEP", "unknown", "in_table"),
                     ("계약통화", "T_DEP", "conflict", "splits_table"), ("원화", "T_DEP", "match", "in_table"),
                     ("원화", "T_FEE", "unknown", "splits_table"), ("달러", "T_FEE", "unknown", "splits_table")]
CURRENCY_FROM_UNIT = [("달러", "T_BOP", "match", "splits_table", "unit"), ("원화", "T_BOP", "unknown", "splits_table", "unit"),
                      ("원화", "T_WON", "unknown", "splits_table", "none"), ("달러", "T_WON", "unknown", "splits_table", "unit"),
                      ("원화", "T_FX", "match", "in_table", "item"), ("달러", "T_FX", "unknown", "in_table", "item")]
UNIT_CURRENCY_ROWS = [("T_C1", {"detail": {"status": "match", "source": "unit"}}),
                      ("T_C2", {"detail": {"status": "match", "source": "unit"}}),
                      ("T_C3", None), ("T_C4", None), ("T_C5", None)]
PAIR = "가상부문 가상예탁 금리와 가상계정상 가상예금 금리를 신규취급액 기준으로 보여줘"
HANDOFF_Q = "가상국민계정 주요지표 가상총소득이 1년 전보다 몇 % 늘었어"


def currency(value: str) -> list[dict]:
    return [basis_cond("B1", "currency", value)]


J4_BASIS = [
    pipe(f"{S3}::test_q4_negation_and_spans[-]", "가상물가를 품목별이 아니라 기본분류 기준으로", "stage3", ["T_ITEM", "T_BASIC"],
         [chk("basis", "T_ITEM", condition="B1", source_status="conflict", detail={"status": "conflict"}),
          chk("basis", "T_BASIC", condition="B2", source_status="match", detail={"status": "match"})],
         conditions=[basis_cond("B1", "classification", "품목별", "-"), basis_cond("B2", "classification", "기본분류", "+")]),
    pipe(f"{S3}::test_j4_title_basis_match_and_opposite_conflict[-]", "가상통화 원계열 말잔", "stage3", ["T_RAW", "T_SA"],
         [chk("basis", "T_RAW", condition="B1", source_status="match",
              detail={"condition_id": "B1", "status": "match", "role": "splits_table", "source": "title"},
              stances=["support"]),
          chk("basis", "T_SA", condition="B1", source_status="conflict", detail={"status": "conflict"}, stances=["against"])],
         conditions=[basis_cond("B1", "seasonal", "원계열")]),
    pipe(f"{S3}::test_j4_group_basis_uses_title_bracket[-]", "가상신용을 업권별로", "stage3", ["T_SECTOR", "T_USE"],
         [chk("basis", "T_SECTOR", condition="B1", detail={"status": "match"}),
          chk("basis", "T_USE", condition="B1", detail={"status": "unknown"})],
         conditions=[basis_cond("B1", "classification", "업권별")]),
    pipe(f"{S3}::test_q5_splits_requirements_by_same_axis_values[-]", "가상통화 원계열과 계절조정계열을 각각", "stage3",
         ["T_RAW", "T_SA"],
         [chk("basis", "T_RAW", condition="B1", requirement="r1", detail={"status": "match"}),
          chk("basis", "T_SA", condition="B2", requirement="r2", detail={"status": "match"})]),
    pipe(f"{S3}::test_unit_unknown_without_meta_unit[-]", "가상신용 십억 원 단위", "stage3", ["T_SECTOR"],
         [chk("unit", "T_SECTOR", condition="U1", source_status="conflict", detail={"status": "differ"})],
         conditions=[{"id": "U1", "kind": "unit", "value": "십억원"}]),
    pipe(f"{S3}::test_q4_negation_hajianeun[-]", "가상통화 계절조정은 하지 않은 값", "stage3", ["T_SA", "T_RAW"],
         [chk("basis", "T_SA", condition="B1", source_status="conflict", detail={"status": "conflict"}),
          chk("basis", "T_RAW", condition="B1", source_status="match", detail={"status": "match"})]),
    *[direct(f"{S3}::test_unit_compare_uses_scale_and_base[{n}]", "unit", "unit_compare", f"T_U{n}",
             [{"id": "U1", "kind": "unit", "value": wanted, "attrs": {"value": wanted}}],
             [chk("unit", f"T_U{n}", condition="U1", detail={"status": verdict})], param=f"'{wanted}', {have!r}")
      for n, (wanted, have, verdict) in enumerate(UNIT_COMPARE_ROWS, 1)],
    *[pipe(f"{Q4A}::test_j4_uses_new_axes[{n}]", q, "q4_axes", [tid],
           [chk("basis", tid, condition="B1", detail={"status": status})], param=f"{q}', '{tid}")
      for n, (q, tid, status) in enumerate(NEW_AXES, 1)],
    direct(f"{Q4A}::test_market_value_does_not_make_city_gas_an_opposite[-]", "basis", "q4_axes", "T_GAS",
           [basis_cond("B1", "price", "명목")],
           [chk("basis", "T_GAS", condition="B1", check_not={"detail": {"status": "conflict"}})]),
    pipe(f"{Q4H}::test_comparison_is_handoff_only[-]", HANDOFF_Q, "q4_handoff", ["T_GROWTH", "T_INDEX", "T_CUR", "T_IMP"],
         [chk("basis", "T_GROWTH", condition="K1", source_status="match",
              detail={"status": "match", "role": "in_table", "source": "item"})],
         absent=[{"name": "basis", "table": t, "condition": "K1"} for t in ("T_INDEX", "T_CUR", "T_IMP")]
         + [{"name": "basis", "table": "*", "condition": "K2"}],
         conditions=[{"id": "K1", "kind": "calc", "attrs": {"axis": "comparison", "value": "전년동기비"}},
                     {"id": "K2", "kind": "calc", "attrs": {"axis": "calc", "value": "백분율"}}]),
    pipe(f"{Q4H}::test_dollar_display_items_are_handoff_not_conflict[-]", "가상국민계정 주요지표 1인당 가상총소득을 달러 기준으로",
         "q4_handoff", ["T_CUR"], [chk("basis", "T_CUR", condition="B1", detail={"status": "match", "role": "in_table"})],
         conditions=currency("달러")),
    pipe(f"{Q4H}::test_contract_currency_matches_items[-]", "가상수입물가지수를 계약통화 기준으로", "q4_handoff", ["T_IMP"],
         [chk("basis", "T_IMP", condition="B1", detail={"status": "match", "role": "in_table"})],
         conditions=currency("계약통화")),
    *[direct(f"{Q4H}::test_currency_item_grammar[{n}]", "basis", "currency_grammar", f"T_G{n:02d}", currency(value),
             [chk("basis", f"T_G{n:02d}", condition="B1", detail={"status": "match"}) if hit else
              chk("basis", f"T_G{n:02d}", condition="B1", check_not={"detail": {"status": "match"}})], param=name)
      for n, (value, name, hit) in enumerate(CURRENCY_GRAMMAR_NAMES, 1)],
    *[direct(f"{Q4H}::test_currency_item_verdicts[{n}]", "basis", "q4_currency", tid, currency(value),
             [chk("basis", tid, condition="B1", detail={"status": status, "role": role})], param=f"'{value}', '{tid}'")
      for n, (value, tid, status, role) in enumerate(CURRENCY_VERDICTS, 1)],
    pipe(f"{Q4H}::test_comparison_item_needs_word_end[-]", "가상소비자물가지수가 전월보다 얼마나 올랐어", "q4_bidet", ["T_BIDET"],
         absent=[{"name": "basis", "table": "T_BIDET", "condition": "K1"}],
         conditions=[{"id": "K1", "kind": "calc", "attrs": {"axis": "comparison", "value": "전기비"}}]),
    *[direct(f"{Q4H}::test_unit_currency_needs_clear_money_unit[-]", "basis", "unit_currency", tid, currency("달러"),
             [chk("basis", tid, condition="B1", **({"detail": want["detail"]} if want else
                                                  {"check_not": {"detail": {"status": "match"}}}))])
      for tid, want in UNIT_CURRENCY_ROWS],
    *[direct(f"{Q4H}::test_currency_from_unit[{n}]", "basis", "q4_unit", tid, currency(value),
             [chk("basis", tid, condition="B1", detail={"status": status, "role": role, "source": source})],
             param=f"'{value}', '{tid}'")
      for n, (value, tid, status, role, source) in enumerate(CURRENCY_FROM_UNIT, 1)],
    pipe(f"{Q7}::test_ambiguous_condition_gives_no_points_and_no_met[-]", PAIR, "q7_scopes", ["T_NEW", "T_BAL"],
         [chk("basis", t, condition="B1", requirement=r, verdict="n/a", detail={"status": s})
          for r in ("r1", "r2") for t, s in (("T_NEW", "match"), ("T_BAL", "conflict"))]),
]

# ---------------------------------------------------------------- J4 "X별" linked values

LINKED = ["가상 서비스 수출을 대기업, 중견기업 등 기업규모별로 보여줘", "가상 서비스 수출을 대기업과 중견기업 같은 기업규모별로 보여줘"]
UNPROVEN = [("가상 지역별 예금은행 가계대출을 보여줘", "SYN_B", "B1"), ("가상 서비스 수출 대기업 중견기업 기업규모별로 보여줘", "SYN_K", "B1"),
            ("가상 서비스 수출을 원계열, 계절조정 등 기업규모별로 보여줘", "SYN_K", "B3"),
            ("가상 서비스 수출을 대기업, 대기업 등 기업규모별로 보여줘", "SYN_K", "B1")]
SEPARATORS = ["수출을 기업규모별(대기업·중견기업)로", "수출을 기업규모별(대기업/중견기업)로", "수출을 기업규모별(대기업과 중견기업)로",
              "수출을 기업규모별(대기업 및 중견기업)로", "수출을 기업규모별(대기업 또는 중견기업)로"]
MATCH = {"status": "match", "role": "in_table", "source": "item"}
UNKNOWN = {"status": "unknown", "role": "splits_table", "source": "none"}


def byeol(key: str, q: str, tid: str, detail: dict, cid: str = "B1", param: str | None = None, **extra) -> dict:
    return pipe(key, q, "j4_linked", [tid], [chk("basis", tid, condition=cid, detail=detail, **extra)], param=param)


# ---- the old _byeol_linked unit asserts, as direct basis checks: the hand-made J2 matches are the prior
# matched_items check (condition_id, match_method, polarity per row), the excluded names an exclude condition, the
# "Q5 list" condition IDs breakdown conditions of subkind list.
LQ = "수출을 대기업, 중견기업 등 기업규모별로"
LINK_B1 = cond(LQ, "B1", "basis", "기업규모별", value="기업규모별",
               attrs={"axis": "classification", "value": "기업규모별", "polarity": "+"})
SYN_K_ITEMS = {"대기업": "1", "중견기업": "2"}  # axis A of SYN_K (spec_catalogs.J4_LINKED)


def mrow(name: str, *, condition: str | None = None, method: str = "exact", polarity: str = "+") -> dict:
    return {"condition_id": condition, "obj_id": "A", "item_id": SYN_K_ITEMS[name], "name": name, "term": name,
            "parent_path": [], "depth": 0, "match_method": method, "polarity": polarity}


def other_basis(cid: str, value: str) -> dict:
    return {"id": cid, "kind": "basis", "value": value, "attrs": {"axis": "classification", "value": value,
                                                                   "polarity": "+"}}


def list_item(cid: str, value: str) -> dict:
    return {"id": cid, "kind": "breakdown", "value": value,
            "attrs": {"subkind": "list", "text": value, "source": "list", "measure_pair": False}}


def linked(function: str, rows: list[dict], detail: dict, extra: list[dict] = ()) -> dict:
    return direct(f"{J4L}::{function}[-]", "basis", "j4_linked", "SYN_K", [LINK_B1, *extra],
                  [chk("basis", "SYN_K", condition="B1", detail=detail)], question=LQ,
                  prior=[{"name": "matched_items", "detail": {"rows": rows}}], with_text=True)


EXCLUDED_ASSERTS = "test_linked_excluded_value_and_complement_not_counted"
LIST_IDS = "test_linked_list_condition_ids_allowed_only_when_passed"
J4_LINKED_DIRECT = [
    linked(EXCLUDED_ASSERTS, [mrow("대기업"), mrow("중견기업")], MATCH),  # both listed values count
    linked(EXCLUDED_ASSERTS, [mrow("대기업"), mrow("중견기업")], UNKNOWN,  # same value as the exclusion target
           [{"id": "X1", "kind": "exclude", "value": "중견기업", "attrs": {"text": "중견기업", "complement_hints": []}}]),
    linked(EXCLUDED_ASSERTS, [mrow("대기업"), mrow("중견기업", method="complement")], UNKNOWN),  # complement value
    linked(EXCLUDED_ASSERTS, [mrow("대기업"), mrow("중견기업", condition="B2")], UNKNOWN,  # word of another condition
           [other_basis("B2", "중견기업")]),
    linked(EXCLUDED_ASSERTS, [mrow("대기업"), mrow("중견기업", polarity="-")], UNKNOWN),  # negated match
    # matches carried by the list conditions themselves count; matches carried by other (non-list) conditions do not
    linked(LIST_IDS, [mrow("대기업", condition="R1"), mrow("중견기업", condition="R2")], MATCH,
           [list_item("R1", "대기업"), list_item("R2", "중견기업")]),
    linked(LIST_IDS, [mrow("대기업", condition="B2"), mrow("중견기업", condition="B3")], UNKNOWN,
           [other_basis("B2", "대기업"), other_basis("B3", "중견기업")]),
]

J4_LINKED = [
    *[byeol(f"{J4L}::test_linked_list_is_match[{n}]", q, "SYN_K", MATCH, stances=["support"], row_text_contains="대기업")
      for n, q in enumerate(LINKED, 1)],
    *[byeol(f"{J4L}::test_unproven_link_is_unknown_with_both[{n}]", q, tid, UNKNOWN, cid, evidence_ids=[])
      for n, (q, tid, cid) in enumerate(UNPROVEN, 1)],
    *J4_LINKED_DIRECT,
    byeol(f"{J4L}::test_linked_parenthesis_form[-]", "수출을 기업규모별(대기업, 중견기업)로", "SYN_K", MATCH),
    *[byeol(f"{J4L}::test_linked_parenthesis_real_separators[{n}]", q, "SYN_K", MATCH) for n, q in enumerate(SEPARATORS, 1)],
    byeol(f"{J4L}::test_linked_parenthesis_space_only_is_one_name_not_list[-]", "지역별(예금은행 가계대출)", "SYN_B",
          {"status": "unknown"}),
    byeol(f"{J4L}::test_linked_parenthesis_space_only_is_one_name_not_list[-]", "기업규모별(대기업 중견기업)", "SYN_K",
          {"status": "unknown"}),
    byeol(f"{J4L}::test_linked_parenthesis_multiword_item_counts_once[-]", "지역별(예금은행 가계대출, 비은행 가계대출)", "SYN_B",
          {"status": "unknown"}),
    byeol(f"{J4L}::test_linked_parenthesis_multiword_item_counts_once[-]", "지역별(예금은행 가계대출, 비은행 가계대출)", "SYN_B2",
          MATCH),
    byeol(f"{J4L}::test_linked_one_word_two_names_counts_once[-]", "수출을 대기업, 중견기업 등 기업규모별로", "SYN_K2",
          {"status": "unknown"}),
]

# ---------------------------------------------------------------- J4 cross axis (direct, with the basis checks as prior)

XQ = "가계대출 잔액을 연령별·지역별로 보여줘"


def _xa_conditions(values=("연령별", "지역별")) -> list[dict]:
    quotes = ("연령별", "지역별")
    return [cond(XQ, f"B{k}", "basis", quotes[k - 1], value=v, attrs={"axis": "classification", "value": v})
            for k, v in enumerate(values, 1)]


def _xa_prior(statuses=("match", "match")) -> list[dict]:
    return [{"name": "basis", "detail": {"condition_id": f"B{k}", "status": s, "role": "in_table", "source": "item"}}
            for k, s in enumerate(statuses, 1)]


def xa(function: str, layer: str, detail: dict | None, *, conditions=None, statuses=("match", "match"),
       input_unchanged: bool = False, file: str = J4X) -> dict:
    conds = conditions if conditions is not None else _xa_conditions()
    checks = [chk("cross_axis", "T", detail=detail)] if detail is not None else []
    absent = [{"name": "cross_axis", "table": "T"}] if detail is None and not input_unchanged else []
    return direct(f"{file}::{function}[-]", "cross_axis", layer, "T", conds, checks, absent=absent, question=XQ,
                  prior=_xa_prior(statuses[:len(conds)]), input_unchanged=input_unchanged)


J4_CROSS = [
    xa("test_distinct_axes_supported", "xa_distinct", {"support": "supported", "axes": {"B1": ["AGE"], "B2": ["REG"]}}),
    xa("test_one_axis_listing_both_is_single_axis", "xa_one_axis",
       {"support": "single_axis", "reason": "shared_axis", "axes": {"B1": ["ACC"], "B2": ["ACC"]}}),
    xa("test_title_only_match_is_unknown", "xa_title_only", {"support": "unknown", "reason": "no_axis_evidence"}),
    xa("test_unmatched_condition_is_unknown", "xa_no_values", {"support": "unknown"}, statuses=("match", "unknown")),
    xa("test_one_condition_returns_none", "xa_no_values", None, conditions=_xa_conditions()[:1]),
    xa("test_axis_shared_by_both_but_other_axis_for_one_is_supported", "xa_shared_plus",
       {"support": "supported", "axes": {"B1": ["ACC"], "B2": ["ACC", "REG"]}}),
    xa("test_does_not_read_ranking_or_change_inputs", "xa_no_values", None, input_unchanged=True),
    xa("test_same_value_twice_is_one_classification", "xa_same_value", None,
       conditions=_xa_conditions(("연령별", "연령별"))),
    xa("test_combined_values_on_shared_axis_are_unknown", "xa_combined", {"support": "unknown", "reason": "combined_values"}),
    xa("test_middle_dot_inside_one_value_is_not_combined", "xa_middle_dot", {"support": "single_axis"}),
    xa("test_combined_value_on_other_axis_is_ignored", "xa_other_axis", {"support": "single_axis"}),
]

# ---- old T3 cross-axis records: only the cross_axis evidence the T3 record carried (T3/T4 decisions are stretch 3)
SINGLE = {"support": "single_axis"}
T3_CROSS = [
    xa("test_unconfirmed_intent_only_records", "xa_one_axis", SINGLE, file=T3X),
    xa("test_supported_and_unknown_never_raise_or_lower", "xa_distinct", {"support": "supported"}, file=T3X),
    xa("test_supported_and_unknown_never_raise_or_lower", "xa_title_only", {"support": "unknown"}, file=T3X),
    xa("test_single_condition_or_no_table_is_untouched", "xa_no_values", None, conditions=_xa_conditions()[:1], file=T3X),
    xa("test_boundary_listing_unconfirmed_is_met_but_not_high", "xa_one_axis", SINGLE, file=T3X),
    xa("test_boundary_combined_values_on_one_axis_are_not_listing", "xa_combined_two",
       {"support": "unknown", "reason": "combined_values"}, file=T3X),
    xa("test_boundary_middle_dot_inside_one_value_is_still_listing", "xa_middle_dot_paren", {"support": "single_axis"},
       file=T3X),
    xa("test_boundary_supported_is_axis_structure_only", "xa_distinct", {"support": "supported"}, file=T3X),
    # the rule-path graph run: "연령별"/"지역별" both meet values of the one axis "구분"
    pipe(f"{T3A}::test_graph_reads_stay_declared[1]", "가상대출 잔액을 연령별·지역별로 보여줘", "t3_audit", ["T"],
         [chk("cross_axis", "T", detail={"support": "single_axis"})], param="False"),
]

# ---- old item-prefix graph test (rule path): a value "북부-교육비" is not an exact binding of the listed "북부"
CX_Q = "가상지출을 북부와 남부로 비교해줘"
CX_PREFIX = [
    pipe(f"{CXP}::test_graph_evidence_changes_score_without_exact_binding_or_met_claim[1]", CX_Q, "cx_prefix", ["T_PREFIX"],
         [chk("matched_items", "T_PREFIX", rows_condition=cid, detail={"rows": []}) for cid in ("R1", "R2")],
         conditions=[{"id": "R1", "kind": "breakdown", "value": "북부"}, {"id": "R2", "kind": "breakdown", "value": "남부"}],
         param="False"),
]

# ---------------------------------------------------------------- indicator evidence (measure)

INDICATOR = [
    pipe(f"{ME}::test_match_partial_none_and_catalog_abbreviation_provenance[-]", "실질 XYZ 증가율", "measure",
         ["GOOD", "BAD", "NONE"],
         [chk("indicator", "GOOD", detail={"result": "match", "available": True}, stances=["support"]),
          chk("indicator", "BAD", detail={"result": "partial", "available": True}, stances=["unknown"]),
          chk("indicator", "NONE", detail={"result": "none", "available": True, "by_field": {"title": "none"}},
              stances=["against"])]),
    pipe(f"{ME}::test_parent_survey_substring_is_context_but_exact_path_segment_is_evidence[-]", "가상생산", "measure",
         ["NONE", "PATH"],
         [chk("indicator", "NONE", detail={"result": "none"}),
          chk("indicator", "PATH", detail={"by_field": {"catalog_path": "match"}})]),
    pipe(f"{ME}::test_no_measure_extracted_is_unknown_evidence_and_neutral_score[-]", "2024년 연간 실질 지역별로 보여줘", "measure",
         ["NONE"], [chk("indicator", "NONE", verdict="n/a", detail={"available": False, "result": None}, stances=["unknown"])]),
    pipe(f"{J5}::test_on_whole_phrase_equals_segment[-]", "차주당 가계대출 잔액을 보여줘", "j5_path", ["SYN_X"],
         [chk("indicator", "SYN_X", detail={"result": "match"})]),
    *[pipe(f"{J5}::test_on_not_whole_leaf_phrase_not_counted[{n}]", "차주당 가계대출 잔액을 보여줘", "j5_path", [tid],
           [chk("indicator", tid, check_not={"detail": {"result": "match"}})], param=tid)
      for n, tid in enumerate(["SYN_E", "SYN_S", "SYN_U", "SYN_N", "SYN_P"], 1)],
    pipe(f"{J5}::test_measure_does_not_change_breakdown_support[-]", "차주당 가계대출 잔액을 연령별 지역별로 보여줘", "j5_path",
         ["SYN_1"], [chk("indicator", "SYN_1", detail={"result": "match"})]),
]

CASES = number_parts([*J2, *J3, *J4_BASIS, *J4_LINKED, *J4_CROSS, *INDICATOR, *T3_CROSS, *CX_PREFIX])


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return Layers(tmp_path_factory, CATALOGS)


@pytest.mark.parametrize("case", shape_params(CASES))
def test_case_shape(case):
    assert evidence_shape(case, CATALOGS) == []


@pytest.mark.parametrize("case", behaviour_params(CASES))
def test_case_behaviour(case, layers):
    doc, conditions = run_evidence_case(case, layers)
    assert evidence_problems(case, doc, conditions) == []
