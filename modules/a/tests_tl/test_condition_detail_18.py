"""The golden scorer's condition diagnosis by statistic, condition and quote (.18, user 2026-10-29 04:59 UTC), in
normal/error pairs on made-up records only: one statistic's quote or end point never stands in for another's, a
period quote never covers every period expression, a condition is named for a statistic only through the model's
own ``for`` (never by position), and a statistic code stopped earlier is "not run", not a preservation failure."""

from __future__ import annotations

from test_indep_38 import final, output, record, row  # noqa: F401 (shared made-up fixtures)
from test_score_split_16 import old_case, score

from tl_eval import score as S

TWO_YEARS = "2023년 가상지표와 2024년 다른지표를 보여 줘"
YEAR_END = "2020~2024년 연말 가상지표와 다른지표를 보여 줘"


def two(question, periods=(), frequencies=(), splitters=(), quotes=None, reasons=None, outcomes=None):
    m = output([("s1", "가상지표"), ("s2", "다른지표")], [("s1", "DT_X01"), ("s2", "DT_Y01")])
    m["periods"], m["frequencies"], m["splitters"] = list(periods), list(frequencies), list(splitters)
    quotes, outcomes = quotes or {}, outcomes or {}
    rec = record(m, {sid: final(outcomes.get(sid, "selected"), tables=[row(t, "2024-01", "2024-12")],
                                quotes=quotes.get(sid, ()))
                     for sid, t in (("s1", "DT_X01"), ("s2", "DT_Y01"))}, question=question)
    for sid, codes in (reasons or {}).items():
        rec["checks"][sid]["reasons"] = [{"code": c, "detail": None} for c in codes]
    return rec


def q(quote, *sids):
    return {"quote": quote, "for": list(sids)}


def kept(quote, sid, point="end", frequency="Y"):
    return {"quote": quote, "for": [sid], "frequency": frequency, "point_in_period": point}


def stated(rec, kind):
    return [(x["text"], x["state"], x["statistics"]) for x in S.condition_details(rec)["stated"] if x["kind"] == kind]


# ---------------------------------------------------------------- periods: one quote never covers every expression


def test_a_period_quoted_for_one_statistic_leaves_the_other_period_missing():
    """Counterexample 1: before .18 the one quote made the whole period "quoted"."""
    rec = two(TWO_YEARS, periods=[q("2023년", "s1")])
    assert stated(rec, "period") == [("2023", "quoted", ["s1"]), ("2024", "missing", None)]  # never placed by position
    assert S.condition_quotes(rec)["period"] == "missing"
    counts = S.condition_details(rec)
    assert S.condition_counts(counts)["unattributed"] == 1 and S.condition_counts(counts)["statistics"] == []


def test_each_period_quoted_for_its_own_statistic_is_quoted():
    rec = two(TWO_YEARS, periods=[q("2023년", "s1"), q("2024년", "s2")])
    assert stated(rec, "period") == [("2023", "quoted", ["s1"]), ("2024", "quoted", ["s2"])]
    assert S.condition_quotes(rec)["period"] == "quoted"


def test_a_repeated_quote_text_whose_place_cannot_be_told_is_review():
    question = "2024년 가상지표와 2024년 다른지표를 보여 줘"
    one = two(question, periods=[q("2024년", "s1", "s2")])
    assert [x[1] for x in stated(one, "period")] == ["review", "review"]
    both = two(question, periods=[q("2024년", "s1"), q("2024년", "s2")])
    assert [x[1] for x in stated(both, "period")] == ["quoted", "quoted"]


# ---------------------------------------------------------------- end points: per statistic, from its own record


def test_an_end_point_kept_for_one_statistic_does_not_stand_in_for_the_other():
    """Counterexample 2: before .18 one statistic's end point made the whole frequency "quoted"."""
    rec = two(YEAR_END, periods=[q("2020~2024년", "s1", "s2")], frequencies=[q("연말", "s1", "s2")],
              quotes={"s1": [kept("연말", "s1")], "s2": [kept("연말", "s2", point=None)]})
    rows = {x["statistic"]: x["state"] for x in S.condition_details(rec)["delivered"]}
    assert rows == {"s1": "quoted", "s2": "not_kept"}
    assert S.condition_quotes(rec)["frequency"] == "not_kept"
    assert S.condition_counts(S.condition_details(rec))["statistics"] == ["s2"]


def test_end_points_kept_for_both_statistics_are_quoted():
    rec = two(YEAR_END, periods=[q("2020~2024년", "s1", "s2")], frequencies=[q("연말", "s1", "s2")],
              quotes={"s1": [kept("연말", "s1")], "s2": [kept("연말", "s2")]})
    assert {x["state"] for x in S.condition_details(rec)["delivered"]} == {"quoted"}
    assert S.condition_quotes(rec) == {"frequency": "quoted", "period": "quoted"}


def test_a_statistic_code_stopped_earlier_is_not_run_not_a_preservation_failure():
    rec = two(YEAR_END, periods=[q("2020~2024년", "s1", "s2")], frequencies=[q("연말", "s1", "s2")],
              quotes={"s1": [kept("연말", "s1")]}, reasons={"s2": ["stated_basis_conflict"]},
              outcomes={"s2": "failed"})
    (s2,) = [x for x in S.condition_details(rec)["delivered"] if x["statistic"] == "s2"]
    assert s2["state"] == "not_run" and s2["stop"] == ["stated_basis_conflict"]
    assert S.condition_quotes(rec)["frequency"] == "quoted"
    assert S.condition_counts(S.condition_details(rec))["not_run"] == 1


def test_a_record_made_before_frequency_quotes_gets_no_delivery_rows():
    rec = two(YEAR_END, periods=[q("2020~2024년", "s1", "s2")], frequencies=[q("연말", "s1", "s2")])
    for f in rec["final"].values():
        del f["frequency_quotes"]
    assert S.condition_details(rec)["delivered"] == []


def test_an_end_word_only_in_another_field_names_that_quote_s_statistics():
    rec = two(YEAR_END, periods=[q("2020~2024년", "s1", "s2")], splitters=[{**q("연말", "s1"), "axis": "stock"}])
    assert stated(rec, "frequency") == [("연말", "other_field", ["s1"])]


# ---------------------------------------------------------------- cause labels only for the item's own statistics

Q2 = "2024년 가상지표와 다른지표 월별 추이"


def worse(bad_final):
    """s1 quotes 월별 only as a splitter (other field); one statistic's final table is wrong."""
    m = output([("s1", "가상지표"), ("s2", "다른지표")], [("s1", "DT_X01"), ("s2", "DT_Y01")])
    m["periods"] = [q("2024년", "s1", "s2")]
    m["splitters"] = [{**q("월별", "s1"), "axis": "other"}]
    tables = {"s1": "DT_X01", "s2": "DT_Y01", bad_final: "DT_X03"}
    rec = record(m, {sid: final(tables=[row(tables[sid], "2024-01", "2024-12")]) for sid in ("s1", "s2")},
                 question=Q2)
    return score(old_case([["DT_X01"], ["DT_Y01"]], question=Q2), rec)


def test_a_worse_result_of_a_clean_statistic_stays_on_the_code():
    res = worse("s2")
    assert "condition_in_other_field" in res["labels"] and "code_correction_error" in res["labels"]


def test_a_worse_result_of_the_statistic_with_the_condition_problem_is_review():
    res = worse("s1")
    assert "condition_in_other_field" in res["labels"]
    assert "cause_review" in res["labels"] and "code_correction_error" not in res["labels"]


def test_the_diagnosis_states_its_scope():
    assert "fixed forms" in S.condition_details(two(TWO_YEARS))["scope"]
