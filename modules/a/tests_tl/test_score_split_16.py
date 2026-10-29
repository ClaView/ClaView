"""The golden scorer's split statistics, per-target asks and completion (.16, user 2026-10-28 19:56 UTC), in
normal/error pairs on made-up tables, questions and run records only: no model, no network.

Without a confirmed target list, a split answer keeps its per-statistic judgment held (only the table set is scored);
with targets, a split and a merged answer get the same verdict. A condition that surely failed is never hidden by a
held judgment."""

from __future__ import annotations

from test_indep_38 import TITLES, final, option, output, record, row  # noqa: F401 (shared made-up fixtures)
from tl_eval import score as S

COVERAGE = {t: {"M": (2000 * 12, 2026 * 12 + 6)} for t in TITLES}
Q2 = "2024년 가상지표와 다른지표 월별 추이"


def old_case(groups, question="2024년 가상지표 월별 추이를 보여 줘", ask=None, requirements=None):
    case = {"case_id": "T-1", "part": "golden16", "question": question,
            "statistics": {"count": None, "status": "unconfirmed"}, "groups": groups,
            "representative": {"confirmed_groups": groups, "confirmed_equivalents": {}, "proposed_equivalents": {}},
            "ask": ask or {"expected": "not_expected"}, "new_policy": {"status": "same_as_source"},
            "final": {"status": "confirmed", "period": {"from": "2024-01", "to": "2024-12"}, "frequency": "M",
                      "basis": [], "groups": groups}}
    if requirements:
        case["requirements"] = {"status": "confirmed", "targets": requirements}
    return case


def score(case, rec):
    return S.score_case(rec, case, TITLES, COVERAGE)


YEAR = row("DT_X01", "2024-01", "2024-12")


def test_merged_and_split_answers_get_the_same_verdict_with_targets():
    reqs = [{"id": "r1", "text": "가상지표", "tables": ["DT_X01"]}]
    case = old_case([["DT_X01"]], requirements=reqs)
    merged = record(output([("s1", "가상지표")], [("s1", "DT_X01")]), {"s1": final(tables=[YEAR])})
    split = record(output([("s1", "가상지표"), ("s2", "가상지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                   {"s1": final(tables=[YEAR]), "s2": final(tables=[YEAR])})
    a, b = score(case, merged), score(case, split)
    assert a["final_full"]["right"] is True and b["final_full"]["right"] is True
    assert a["completed"]["right"] is True and b["completed"]["right"] is True


def test_without_targets_a_split_answer_keeps_only_the_table_set():
    case = old_case([["DT_X01"]])
    split = record(output([("s1", "가상지표"), ("s2", "가상지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                   {"s1": final(tables=[YEAR]), "s2": final(tables=[YEAR])})
    res = score(case, split)
    assert res["final_table_set"]["right"] is True
    assert res["model_meaning"]["right"] is None and res["final_full"]["right"] is None
    assert res["completed"]["right"] is None  # nothing failed, the per-target judgment is held


def test_a_group_left_out_fails_completion_even_when_the_mapping_is_held():
    case = old_case([["DT_X01"], ["DT_Y01"]], question=Q2)
    rec = record(output([("s1", "가상지표"), ("s2", "다른지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                 {"s1": final(tables=[YEAR]), "s2": final(tables=[YEAR])}, question=Q2)
    res = score(case, rec)
    assert res["final_table_set"]["right"] is False
    assert res["completed"]["right"] is False and "final_table_set_wrong" in res["completed"]["blocked_by"]


def test_one_split_statistic_with_a_wrong_period_fails_completion():
    case = old_case([["DT_X01"]])
    rec = record(output([("s1", "가상지표"), ("s2", "가상지표")], [("s1", "DT_X01"), ("s2", "DT_X01")]),
                 {"s1": final(tables=[YEAR]), "s2": final(tables=[row("DT_X01", "2024-01", "2024-06")])})
    res = score(case, rec)
    assert res["final_table_set"]["right"] is False and res["completed"]["right"] is False


def test_swapped_targets_are_wrong_although_the_table_set_is_the_same():
    reqs = [{"id": "r1", "text": "가상지표", "tables": ["DT_X01"]}, {"id": "r2", "text": "다른지표", "tables": ["DT_Y01"]}]
    case = old_case([["DT_X01"], ["DT_Y01"]], question=Q2, requirements=reqs)
    swapped = record(output([("s1", "가상지표"), ("s2", "다른지표")], [("s1", "DT_Y01"), ("s2", "DT_X01")]),
                     {"s1": final(tables=[row("DT_Y01", "2024-01", "2024-12")]), "s2": final(tables=[YEAR])},
                     question=Q2)
    res = score(case, swapped)
    assert res["final_table_set"]["right"] is True  # the set alone cannot see the swap
    assert res["model_meaning"]["right"] is False and res["final_full"]["right"] is False


def ask_case():
    return old_case([["DT_X01"]], ask={"expected": "required", "axis": "seasonal",
                                       "options": {"원계열": "DT_X01", "계절조정": "DT_X02"}})


def asked(*per_stat):
    m = output([(sid, "가상지표") for sid, _ in per_stat],
               asks=[{"for": sid, "axis": "seasonal", "options": opts} for sid, opts in per_stat])
    finals = {sid: final("ask", asks=[{"for": sid, "axis": "seasonal",
                                       "options": [{"value": o["value"], "table_id": o["table"]} for o in opts]}])
              for sid, opts in per_stat}
    return record(m, finals, route="ask")


BOTH = [option("원계열", "DT_X01"), option("계절조정", "DT_X02")]


def test_split_statistics_each_asking_the_axis_are_right():
    assert score(ask_case(), asked(("s1", BOTH)))["final_full"]["right"] is True
    assert score(ask_case(), asked(("s1", BOTH), ("s2", BOTH)))["final_full"]["right"] is True


def test_a_wrong_option_in_the_second_ask_is_caught():
    res = score(ask_case(), asked(("s1", BOTH), ("s2", [option("원계열", "DT_X01"), option("계절조정", "DT_X03")])))
    assert res["final_full"]["right"] is False


def test_a_statistic_selecting_where_the_ask_is_due_is_wrong():
    m = output([("s1", "가상지표"), ("s2", "가상지표")], [("s2", "DT_X01")],
               asks=[{"for": "s1", "axis": "seasonal", "options": BOTH}])
    rec = record(m, {"s1": final("ask", asks=[{"for": "s1", "axis": "seasonal", "options": [
        {"value": "원계열", "table_id": "DT_X01"}, {"value": "계절조정", "table_id": "DT_X02"}]}]),
        "s2": final(tables=[YEAR])}, route="ask")
    res = score(ask_case(), rec)
    assert res["final_full"]["right"] is False
    assert any(d.get("selected_where_ask_due") == ["s2"] for d in res["final_detail"])


def test_a_held_mapping_does_not_hide_a_sure_evidence_error():
    case = old_case([["DT_X01"], ["DT_X03"]], question=Q2)
    m = output([("s1", "가상지표")], [("s1", "DT_X01")])
    m["choices"][0]["evidence"] = [{"kind": "title", "text": "카드에 없는 제목"}]
    rec = record(m, {"s1": final(tables=[row("DT_X03", "2024-01", "2024-12")])}, question=Q2)
    res = score(case, rec)  # s1's representative is in one group and its final table in the other: held mapping
    assert res["final_full"]["right"] is None
    assert res["evidence_contract"]["ok"] is False and res["completed"]["right"] is False


def test_right_tables_and_months_with_wrong_evidence():
    case = old_case([["DT_X01"]])
    m = output([("s1", "가상지표")], [("s1", "DT_X01")])
    m["choices"][0]["evidence"] = [{"kind": "title", "text": "카드에 없는 제목"}]
    res = score(case, record(m, {"s1": final(tables=[YEAR])}))
    assert res["table_match"]["right"] is True and res["final_full"]["right"] is True
    assert res["evidence_contract"]["ok"] is False and res["completed"]["right"] is False
