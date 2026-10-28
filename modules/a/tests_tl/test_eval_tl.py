"""Plan 37 step 4: the evaluator on fixed fake outputs (users 2026-10-28 03:55 and 04:20 UTC). Expected scores and
error causes are written by hand here; none is taken from what the path selected. No model, no network."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from conftest import fill_option_evidence  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from claview_a.cards import build_cards, table_numbers  # noqa: E402
from claview_a.contract import Settings  # noqa: E402
from claview_a.graph import JudgeError, run  # noqa: E402
from claview_a.pool import build_pool  # noqa: E402
from claview_a.search.tables import selection_catalog  # noqa: E402
from tl_eval import report as R  # noqa: E402
from tl_eval import score as S  # noqa: E402
from tl_eval import standard as ST  # noqa: E402

AS_OF = "2026-10-22"
LOAN = "2024년 1~3월 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
LOAN_OPEN = "2024년 1~3월 예금은행 가계대출 금리를 월별로 보여줘"
TWO = "2024년 1~3월 예금은행 가계대출 금리와 예금은행 수신금리를 신규취급액 기준으로 월별로 보여줘"
M2 = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
M2_LONG = "2000년 1월부터 2010년 12월까지 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
NEW, OLD = "DT_161Y006", "DT_101Y004"  # M2 평잔·원계열, new and old edition (catalog facts)
RATE_NEW, RATE_BAL, RECEIPT_NEW = "DT_121Y006", "DT_121Y015", "DT_121Y002"


@pytest.fixture(scope="module")
def views(layer):
    return selection_catalog(layer).views


@pytest.fixture(scope="module")
def titles(views):
    return {t: v.title for t, v in views.items()}


@pytest.fixture(scope="module")
def coverage(views):
    return R.coverage_of(views)


def std(cid, groups, *, ask="not_expected", axis=None, options=None, tables=None, period=None, periods=None,
        frequency=None, basis=(), confirmed_eq=None, proposed_eq=None, policy="same_as_source", part="new24",
        final_groups=None, statistics="unconfirmed"):
    status = "confirmed" if (period or periods or frequency or basis) else "unconfirmed"
    return {"case_id": cid, "part": part, "question": "", "statistics": {"count": None, "status": statistics},
            "groups": groups,
            "representative": {"confirmed_groups": groups, "confirmed_equivalents": confirmed_eq or {},
                               "proposed_equivalents": proposed_eq or {}, "status": "proposed"},
            "ask": {"expected": ask, "axis": axis, "options": options, "tables": tables},
            "new_policy": {"status": policy, "differs": {}},
            "final": {"status": status, "period": period, "periods": periods, "frequency": frequency,
                      "basis": list(basis), "groups": final_groups if final_groups is not None else groups}}


def empty():
    return {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                            "missing", "alternatives")}


def ref_of(cards, table):
    for c in cards:
        num = table_numbers(c)
        if table in num:
            return f"{c['card_id']}.{num[table]}"
    raise KeyError(table)


def go(layer, question, build):
    """Run the graph with a fake judge whose output ``build(cards)`` makes from the run's own cards."""
    cards = build_cards(question, build_pool(question, layer), layer)
    state = run(question, layer, lambda p, r: json.dumps(fill_option_evidence(build(cards), cards), ensure_ascii=False), Settings("fake"), AS_OF)
    return S.adapt(state)


def m2_out(cards, table, period_quote, evidence=None, frequency="월별"):
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"}]
    o["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                      {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
    o["periods"] = [{"quote": period_quote, "for": ["s1"]}]
    o["frequencies"] = [{"quote": frequency, "for": ["s1"]}]
    o["choices"] = [{"for": ["s1"], "table": ref_of(cards, table),
                     "evidence": evidence or [{"kind": "item", "text": "M2(평잔 원계열)"}]}]
    return o


def loan_out(cards, table, evidence, quote="예금은행 가계대출 금리"):
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": quote, "name": quote}]
    o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    o["choices"] = [{"for": ["s1"], "table": ref_of(cards, table), "evidence": evidence}]
    return o


M2_STD = dict(period={"from": "2024-01", "to": "2024-03"}, frequency="M",
              basis=[{"axis": "stock", "value": "평잔"}, {"axis": "seasonal", "value": "원계열"}])
LOAN_STD = dict(period={"from": "2024-01", "to": "2024-03"}, frequency="M",
                basis=[{"axis": "loan_basis", "value": "신규취급액"}])
LOAN_ASK = dict(ask="required", axis="loan_basis", options={"신규취급액": RATE_NEW, "잔액": RATE_BAL},
                period={"from": "2024-01", "to": "2024-03"}, frequency="M")


# ---------------------------------------------------------------- the 03:55 cases (expectations by hand)


def test_an_old_edition_representative_with_the_right_meaning_and_a_new_final_table(layer, titles, coverage):
    rec = go(layer, M2, lambda c: m2_out(c, OLD, "2024년 1~3월"))
    res = S.score_case(rec, std("old-rep", [[NEW]], confirmed_eq={NEW: [OLD]}, **M2_STD), titles, coverage)
    assert res["model_meaning"]["right"] is True and res["final_full"]["right"] is True
    assert res["final_detail"][0] == {"group": 0, "statistic": "s1", "tables": "right", "period": "right",
                                      "frequency": "right", "basis": "right", "coverage": "right"}
    assert S.attribute(res) == []
    res = S.score_case(rec, std("old-rep-p", [[NEW]], proposed_eq={NEW: [OLD]}, **M2_STD), titles, coverage)
    assert res["model_meaning"]["right"] is None  # a proposed equivalent: review, never right or wrong


def test_a_wrong_meaning_that_passes_every_fact_check(layer, titles):
    def build(cards):
        card = next(c for c in cards if c["name"] == "예금은행 대출금")
        return loan_out(cards, card["tables"][0]["table_id"], [{"kind": "title", "text": "예금은행 대출금"}])
    rec = go(layer, LOAN_OPEN, build)
    res = S.score_case(rec, std("wrong-meaning", [[RATE_NEW]], period={"from": "2024-01", "to": "2024-03"},
                                frequency="M"), titles)
    assert res["contract"]["ok"] is True and res["model_meaning"]["right"] is False
    assert res["final_full"]["right"] is False and "model_meaning_wrong" in S.attribute(res)
    assert not {"code_check_missed", "code_false_reject", "code_correction_error"} & set(S.attribute(res))


def test_a_right_representative_with_a_wrong_period_or_edition_is_a_code_error(layer, titles, coverage):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    bad = copy.deepcopy(rec)
    bad["final"]["s1"]["tables"] = [{"table_id": NEW, "period": [{"start": "2023-01", "end": "2024-03"}],
                                     "frequency": "M"}]
    res = S.score_case(bad, std("bad-period", [[NEW]], **M2_STD), titles, coverage)
    assert res["final_detail"][0]["period"] == "wrong" and res["final_full"]["right"] is False
    assert "code_correction_error" in S.attribute(res)
    bad["final"]["s1"]["tables"] = [{"table_id": OLD, "period": [{"start": "2024-01", "end": "2024-03"}],
                                     "frequency": "M"}]
    res = S.score_case(bad, std("bad-edition", [[NEW]], **M2_STD), titles, coverage)
    assert res["final_detail"][0]["tables"] == "wrong" and res["final_detail"][0]["coverage"] == "wrong"
    assert res["effects"] == {"group0:table": "worsened"} and "code_correction_error" in S.attribute(res)


def test_a_right_output_rejected_by_code_is_a_code_false_reject(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    bad = copy.deepcopy(rec)
    bad["final"]["s1"] = {**bad["final"]["s1"], "outcome": "failed", "tables": []}
    bad["checks"]["s1"]["reasons"] = [{"code": "quote_not_in_question", "detail": "M2 총액"}]  # the quote IS there
    res = S.score_case(bad, std("false-reject", [[NEW]], **M2_STD), titles)
    assert res["contract"]["checks"] == {"s1": "code_false_reject"} and res["final_full"]["right"] is False


def test_another_tables_evidence_and_a_missing_reference_are_caught(layer, titles):
    rec = go(layer, LOAN, lambda c: loan_out(c, RATE_NEW, [{"kind": "bracket", "text": "잔액 기준"}]))
    res = S.score_case(rec, std("other-evidence", [[RATE_NEW]], **LOAN_STD), titles)
    assert res["contract"]["model_fact_errors"] == {"s1": ["evidence_of_other_table"]}
    assert res["contract"]["checks"] == {"s1": "code_check_success"}

    def build2(cards):
        o = loan_out(cards, RATE_NEW, [{"kind": "bracket", "text": "신규취급액 기준"}])
        o["choices"][0]["table"] = "g99.t1"
        return o
    rec = go(layer, LOAN, build2)
    res = S.score_case(rec, std("no-table", [[RATE_NEW]], **LOAN_STD), titles)
    assert res["contract"]["model_fact_errors"] == {"s1": ["table_not_in_pool"]}
    assert {"model_output_error", "code_check_success"} <= set(S.attribute(res))


def test_a_needed_ask_the_model_missed_is_not_made_up_by_code(layer, titles):
    """Was: code corrected it into the needed ask. 06:47 UTC: no model evidence for the other table: held."""
    rec = go(layer, LOAN_OPEN, lambda c: loan_out(c, RATE_NEW, [{"kind": "item", "text": "가계대출"}]))
    res = S.score_case(rec, std("ask-needed", [], **LOAN_ASK), titles)
    assert res["model_meaning"]["ask"] == "wrong" and res["final_ask"] == "wrong"
    assert res["final_full"]["right"] is False and "code_correction_success" not in S.attribute(res)


def test_an_unneeded_ask_corrected_to_the_stated_table(layer, titles):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "금리"}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["asks"] = [{"for": "s1", "axis": "loan_basis", "options": [
            {"value": "신규취급액", "table": ref_of(cards, RATE_NEW)},
            {"value": "잔액기준", "table": ref_of(cards, RATE_BAL)}]}]
        return o
    rec = go(layer, LOAN, build)
    res = S.score_case(rec, std("ask-unneeded", [[RATE_NEW]], **LOAN_STD), titles)
    assert res["model_meaning"]["ask"] == "wrong" and res["final_full"]["right"] is True
    assert "model_meaning_wrong" in S.attribute(res) and res["final_ask"] is None


def test_a_model_basis_error_is_held_not_switched_by_code(layer, titles):
    """Was: code switched to the stated table. 06:47 UTC: no model evidence for that table: held."""
    rec = go(layer, LOAN, lambda c: loan_out(c, RATE_BAL, [{"kind": "bracket", "text": "잔액 기준"}]))
    res = S.score_case(rec, std("basis-fixed", [[RATE_NEW]], **LOAN_STD), titles)
    assert res["model_meaning"]["right"] is False and res["final_full"]["right"] is False
    assert rec["final"]["s1"]["outcome"] == "failed" and "code_correction_success" not in S.attribute(res)


def test_two_statistics_where_one_takes_the_others_table(layer, titles):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "대출"},
                           {"id": "s2", "quote": "예금은행 수신금리", "name": "수신"}]
        o["splitters"] = [{"quote": "신규취급액 기준", "axis": "loan_basis", "for": ["s1", "s2"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, RATE_NEW),
                         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]},
                        {"for": ["s2"], "table": ref_of(cards, RATE_NEW),
                         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]}]
        return o
    rec = go(layer, TWO, build)
    res = S.score_case(rec, std("two", [[RATE_NEW], [RECEIPT_NEW]], **LOAN_STD), titles)
    assert res["mapping"]["groups"] == {0: ["s1", "s2"], 1: []}  # never guessed which one is the lending rate
    # the table cannot tell the two statistics apart: held, never counted wrong (user 2026-10-28 04:36 UTC)
    assert res["model_meaning"]["groups"] == ["review", "review"] and res["model_meaning"]["right"] is None
    assert res["final_full"]["right"] is None


def test_an_edition_boundary_request_and_two_separate_points(layer, titles, coverage):
    rec = go(layer, M2_LONG, lambda c: m2_out(c, NEW, "2000년 1월부터 2010년 12월까지"))
    case = std("stitch", [[NEW]], confirmed_eq={NEW: [OLD]}, final_groups=[[NEW, OLD]],
               period={"from": "2000-01", "to": "2010-12"}, frequency="M", basis=M2_STD["basis"])
    res = S.score_case(rec, case, titles, coverage)
    assert res["final_detail"][0]["period"] == "right" and res["final_full"]["right"] is True
    q = "2020년과 2024년 M2 총액을 평잔·원계열 기준으로 연간으로 보여줘"
    rec = go(layer, q, lambda c: m2_out(c, NEW, "2020년과 2024년", frequency="연간"))
    case = std("points", [[NEW]], periods=[{"from": "2020", "to": "2020"}, {"from": "2024", "to": "2024"}],
               frequency="Y", basis=M2_STD["basis"])
    res = S.score_case(rec, case, titles, coverage)
    assert res["final_detail"][0]["period"] == "right" and res["final_full"]["right"] is True


def test_a_pool_miss_a_format_error_and_a_run_error(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    miss = copy.deepcopy(rec)
    miss["pool"] = [t for t in miss["pool"] if t != NEW]
    res = S.score_case(miss, std("pool-miss", [[NEW]], **M2_STD), titles)
    assert res["pool"]["hit"] is False and "retrieval_miss" in S.attribute(res)
    state = run(M2, layer, lambda p, r: "not json", Settings("fake"), AS_OF)
    res = S.score_case(S.adapt(state), std("format", [[NEW]], **M2_STD), titles)
    assert S.attribute(res) == ["run_error:output:not_json"] and res["final_full"]["right"] is False

    def broken(p, r):
        raise JudgeError("timeout")
    state = run(M2, layer, broken, Settings("fake"), AS_OF)
    res = S.score_case(S.adapt(state), std("judge", [[NEW]], **M2_STD), titles)
    assert S.attribute(res) == ["run_error:judge:timeout"]


def test_diagnostics_never_change_scores_or_causes(layer, titles, coverage):
    rec = go(layer, M2, lambda c: m2_out(c, OLD, "2024년 1~3월"))
    case = std("diag", [[NEW]], confirmed_eq={NEW: [OLD]}, **M2_STD)
    a = S.score_case(rec, case, titles, coverage)
    other = copy.deepcopy(rec)
    other["diagnostics"] = {sid: [{"stage": "model_choice", "support": "mismatch"}] for sid in other["diagnostics"]}
    b = S.score_case(other, case, titles, coverage)
    strip = lambda r: {k: v for k, v in r.items() if k != "diagnostics"}  # noqa: E731
    assert strip(a) == strip(b)


def test_a_proposed_new_policy_ask_is_review_not_scored(layer, titles):
    rec = go(layer, LOAN_OPEN, lambda c: loan_out(c, RATE_NEW, [{"kind": "item", "text": "가계대출"}]))
    case = std("policy", [[RATE_NEW]], policy="proposed", period={"from": "2024-01", "to": "2024-03"}, frequency="M")
    res = S.score_case(rec, case, titles)
    assert res["model_meaning"]["ask"] == "review" and res["final_full"]["right"] is None


# ---------------------------------------------------------------- the 04:20 review counterexamples


def test_a_fact_error_code_let_through_is_found_by_the_scorer(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    bad = copy.deepcopy(rec)
    bad["model"]["statistics"][0]["quote"] = "M3 총액"  # not in the question
    bad["model"]["choices"][0]["evidence"] = [{"kind": "item", "text": "없는 항목"}]  # on no card
    bad["checks"]["s1"]["reasons"] = []  # code reported nothing
    res = S.score_case(bad, std("missed", [[NEW]], **M2_STD), titles)
    assert res["contract"]["ok"] is False
    assert res["contract"]["model_fact_errors"] == {"s1": ["evidence_not_on_card", "quote_not_in_question"]}
    assert res["contract"]["checks"] == {"s1": "code_check_missed"}
    assert {"model_output_error", "code_check_missed"} <= set(S.attribute(res))


def test_a_supported_period_stopped_by_code_is_a_false_reject_not_unsupported(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    bad = copy.deepcopy(rec)
    bad["final"]["s1"] = {**bad["final"]["s1"], "outcome": "failed", "tables": []}
    bad["checks"]["s1"]["reasons"] = [{"code": "period_unresolved",
                                       "detail": {"code": "period_unresolved", "quote": "2024년 1~3월"}}]
    res = S.score_case(bad, std("p-false", [[NEW]], **M2_STD), titles)
    assert res["contract"]["checks"] == {"s1": "code_false_reject"} and "unsupported_expression" not in res["labels"]
    bad["checks"]["s1"]["reasons"] = [{"code": "period_partially_read",
                                       "detail": {"code": "period_partially_read", "quote": "2024년 1월 이후 석 달"}}]
    res = S.score_case(bad, std("p-unsup", [[NEW]], **M2_STD), titles)
    assert "unsupported_expression" in res["labels"] and "code_false_reject" not in res["labels"]
    bad["checks"]["s1"]["reasons"] = [{"code": "period_unresolved",
                                       "detail": {"code": "period_unresolved", "quote": "지지난 분기쯤"}}]
    res = S.score_case(bad, std("p-unknown", [[NEW]], **M2_STD), titles)
    assert res["review"] and not {"unsupported_expression", "code_false_reject"} & set(res["labels"])


def test_months_are_never_joined_across_statistics(layer, titles):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "대출"},
                           {"id": "s2", "quote": "예금은행 수신금리", "name": "수신"}]
        o["splitters"] = [{"quote": "신규취급액 기준", "axis": "loan_basis", "for": ["s1", "s2"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, RATE_NEW),
                         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]},
                        {"for": ["s2"], "table": ref_of(cards, RECEIPT_NEW),
                         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]}]
        return o
    rec = go(layer, TWO, build)
    case = std("two-ok", [[RATE_NEW], [RECEIPT_NEW]], **LOAN_STD)
    assert S.score_case(rec, case, titles)["final_full"]["right"] is True  # the normal pair passes
    bad = copy.deepcopy(rec)
    bad["final"]["s1"]["tables"][0]["period"] = [{"start": "2024-01", "end": "2024-01"}]
    bad["final"]["s2"]["tables"][0]["period"] = [{"start": "2024-02", "end": "2024-03"}]
    res = S.score_case(bad, case, titles)
    assert [d.get("period") for d in res["final_detail"]] == ["wrong", "wrong"] and res["final_full"]["right"] is False


def test_an_extra_edition_with_the_same_months_is_not_right(layer, titles, coverage):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    bad = copy.deepcopy(rec)
    bad["final"]["s1"]["tables"].append({"table_id": OLD, "period": [{"start": "2024-01", "end": "2024-03"}],
                                         "frequency": "M"})
    res = S.score_case(bad, std("extra", [[NEW]], **M2_STD), titles, coverage)
    assert res["final_detail"][0]["tables"] == "wrong" and res["final_detail"][0]["period"] == "wrong"
    assert res["final_full"]["right"] is False


def test_an_ask_with_a_wrong_option_is_not_right_nor_a_correction_success(layer, titles):
    rec = go(layer, LOAN_OPEN, lambda c: loan_out(c, RATE_NEW, [{"kind": "item", "text": "가계대출"}]))
    bad = copy.deepcopy(rec)
    bad["final"]["s1"]["asks"] = [{"axis": "loan_basis", "source": "code",
                                   "options": [{"value": "없는값", "table_id": NEW}]}]
    res = S.score_case(bad, std("bad-ask", [], **LOAN_ASK), titles)
    assert res["final_ask"] == "wrong" and res["final_full"]["right"] is False
    assert "code_correction_success" not in res["labels"] and res["effects"] == {"ask": "same"}


def test_a_proposed_only_pool_and_unconfirmed_conditions_are_never_counted_right(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    only_old = copy.deepcopy(rec)
    only_old["pool"] = [t for t in only_old["pool"] if t != NEW]
    res = S.score_case(only_old, std("pool-p", [[NEW]], proposed_eq={NEW: [OLD]}, **M2_STD), titles)
    assert res["pool"]["hit"] is None and "retrieval_miss" not in res["labels"]
    wild = copy.deepcopy(rec)
    wild["final"]["s1"]["tables"][0]["period"] = [{"start": "1900-01", "end": "2099-12"}]
    res = S.score_case(wild, std("golden-like", [[NEW]], part="golden16"), titles)  # conditions unconfirmed
    assert res["table_match"]["right"] is True and res["final_full"]["right"] == "na"


def test_aggregate_reports_right_wrong_review_and_na_apart(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    good = S.score_case(rec, std("a", [[NEW]], part="golden16"), titles)
    miss = copy.deepcopy(rec)
    miss["pool"] = []
    bad = S.score_case(miss, std("b", [[NEW]], **M2_STD), titles)
    review = S.score_case(rec, std("c", [[NEW]], policy="proposed", **M2_STD), titles)
    agg = S.aggregate([good, bad, review])
    assert agg["golden16"]["final_full"] == {"right": 0, "wrong": 0, "review": 0, "na": 1, "denominator": 0}
    assert agg["golden16"]["table_match"] == {"right": 1, "wrong": 0, "review": 0, "na": 0, "denominator": 1}
    assert agg["new24"]["pool_hit"] == {"right": 1, "wrong": 1, "review": 0, "na": 0, "denominator": 2}
    assert agg["new24"]["final_full"]["review"] == 1 and agg["new24"]["labels"].get("retrieval_miss") == 1


def test_the_report_keeps_run_time_information_and_rejects_records_without_it(layer, titles, coverage):
    standard = ST.build(selection_catalog(layer).views, AS_OF)
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    assert rec["run_info"]["as_of"] == AS_OF and rec["run_info"]["versions"]["check"].startswith("tl-check-")
    got = R.evaluate({"SB:DA3-01": rec}, standard, titles, coverage, "abc")
    assert [r["case_id"] for r in got["results"]] == ["SB:DA3-01"] and len(got["not_run"]) == 39
    assert got["run_versions"] == [rec["run_info"]["versions"]] and got["evaluator"]["score"] == S.SCORE_VERSION
    assert got["standard"]["sha256"] == "abc" and got["results"][0]["final_full"]["right"] is True
    no_info = {k: v for k, v in rec.items() if k != "run_info"}
    late = dict(rec, as_of="2026-10-28", run_info=dict(rec["run_info"], as_of="2026-10-28"))
    got = R.evaluate({"SB:DA3-01": no_info, "SB:DA3-02": late}, standard, titles, coverage)
    assert got["results"] == [] and {u["case_id"] for u in got["unverified"]} == {"SB:DA3-01", "SB:DA3-02"}


# ---------------------------------------------------------------- the standard file


def test_the_standard_keeps_its_sources_and_marks_what_is_not_confirmed(layer):
    data = ST.build(selection_catalog(layer).views, AS_OF)
    assert len(data["cases"]) == 40 and data["as_of"] == AS_OF
    for src in data["sources"].values():
        assert src["sha256"] == ST.sha(Path(src["path"]))  # the source files are read, never rewritten
    by = {c["case_id"]: c for c in data["cases"]}
    assert by["SB:CQ1-03"]["new_policy"]["status"] == "proposed"
    assert by["SB:CQ1-01"]["new_policy"]["status"] == "same_as_source"
    assert by["CV-AB-07"]["ask"]["axis"] == "loan_basis"
    assert all(c["statistics"]["status"] == "unconfirmed" for c in data["cases"])
    assert all(not c["representative"]["confirmed_equivalents"] for c in data["cases"])
    assert by["CV-AB-01"]["final"]["status"] == "unconfirmed"
    assert by["SB:DA3-01"]["final"]["period"] == {"from": "2024-01", "to": "2024-03"}



# ---------------------------------------------------------------- standard v2 (user 2026-10-28 04:36 UTC)


def test_standard_v2_lays_the_decisions_with_their_status_and_evidence(layer):
    base = ST.build(selection_catalog(layer).views, AS_OF)
    v2 = ST.apply_decisions(base, json.loads(ST.DECISIONS.read_text(encoding="utf-8")))
    by = {c["case_id"]: c for c in v2["cases"]}
    assert v2["decisions"]["sha256"] == ST.sha(ST.DECISIONS)
    for cid in ("CV-AB-09", "SB:DA3-01", "SB:DA3-02", "SB:CQ1-02", "SB:CQ1-03", "SB:CQ1-18"):
        assert by[cid]["representative"]["equivalents_status"] == "hold"
        assert not by[cid]["representative"]["confirmed_equivalents"]
    assert by["SB:CQ1-03"]["ask"]["expected"] == "required" and by["SB:CQ1-03"]["ask"]["axis"] == "seasonal"
    assert by["SB:CQ1-05"]["ask"]["options"] == {"계절조정": "DT_200Y108", "원계열": "DT_200Y110"}
    assert all(by[c]["ask"]["expected"] == "not_expected" and by[c]["new_policy"]["status"] == "confirmed"
               for c in ("CV-AB-17", "CV-AB-18", "CV-AB-19"))
    assert by["CV-AB-14"]["new_policy"]["status"] == "hold" and by["CV-AB-14"]["final"]["status"] == "confirmed"
    assert by["CV-AB-20"]["final"]["period"] == {"from": "2024", "to": "2024"}
    assert by["CV-AB-20"]["final"]["calc_base"]["from"] == "2023"  # never folded into the requested period
    assert all(c["final"]["status"] == "confirmed" for c in v2["cases"])
    on_disk = json.loads(ST.OUT_V2.read_text(encoding="utf-8"))
    assert on_disk["decisions"]["sha256"] == v2["decisions"]["sha256"] and on_disk["version"] == v2["version"]


def test_a_held_ask_holds_only_the_full_result_and_tables_are_still_scored(layer, titles):
    rec = go(layer, M2, lambda c: m2_out(c, NEW, "2024년 1~3월"))
    case = std("hold", [[NEW]], policy="hold", **M2_STD)
    res = S.score_case(rec, case, titles)
    assert res["table_match"]["right"] is True and res["final_full"]["right"] is None
    assert res["final_detail"][0]["period"] == "right"
