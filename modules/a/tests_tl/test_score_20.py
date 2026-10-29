"""Evaluator .20 (user 2026-10-29 06:20 UTC), pairs on made-up records: a quote at several places is placed when
the qualifiers the model wrote leave one place (else held), and a quote at one place needs no qualifier; where the
ask policy is held, a statistic that asked is never turned into a wrong "missing group", while its options'
frequency and period are recorded as facts; a policy that is not held still scores the ask."""

from __future__ import annotations

from test_indep_38 import COVERAGE, TITLES, final, output, record, row  # noqa: F401 (shared fixtures)
from test_score_split_16 import old_case
from test_targets_19 import marks, run, t

from tl_eval import score as S

TWICE = "2024년 신규 표의 가상지표 아래 고정형과, 잔액 표의 다른지표 아래 고정형을 월별 비교"
PLACED = [t("r1", "고정형", ["DT_X01"], qualifiers=["신규"]), t("r2", "고정형", ["DT_Y01"], occurrence=2,
                                                            qualifiers=["잔액"])]


def twice(splitters, quotes=("고정형", "고정형")):
    stats = [("s1", quotes[0], "DT_X01"), ("s2", quotes[1], "DT_Y01")]
    return run(TWICE, stats, {"s1": "DT_X01", "s2": "DT_Y01"}, splitters=splitters, targets=PLACED)


def test_a_quote_at_two_places_is_placed_by_the_model_s_qualifiers():
    res = twice([("s1", "신규"), ("s2", "잔액")])
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")}
    assert res["requirements"]["placed_by_qualifier"] == ["s1", "s2"] and res["completed"]["right"] is True


def test_a_quote_at_two_places_without_qualifiers_stays_held():
    res = twice([])
    assert res["requirements"]["split_review"] == ["s1", "s2"] and res["final_full"]["right"] is None


def test_qualifiers_that_place_the_tables_the_other_way_are_wrong():
    res = twice([("s1", "잔액"), ("s2", "신규")])
    assert marks(res) == {"r1": ("wrong", "wrong"), "r2": ("wrong", "wrong")}


def test_a_quote_at_one_place_needs_no_qualifier():
    res = twice([], quotes=("가상지표 아래 고정형", "다른지표 아래 고정형"))
    assert marks(res) == {"r1": ("right", "right"), "r2": ("right", "right")}
    assert res["requirements"]["qualifier_review"] == [] and res["completed"]["right"] is True


# ---------------------------------------------------------------- a held ask policy

Q = "2024년 가상지표 월별 추이를 보여 줘"


def asked(policy):
    """A CV-AB-14 shape: the answer group is another table; the statistic asked between two tables."""
    opts = [("원계열", "DT_X01"), ("계절조정", "DT_X02")]
    m = output([("s1", "가상지표")], asks=[{"for": "s1", "axis": "seasonal", "options": [
        {"value": v, "table": tab, "evidence": [{"kind": "title", "text": TITLES[tab]}]} for v, tab in opts]}])
    m["periods"] = [{"quote": "2024년", "for": ["s1"]}]
    m["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    quotes = [{"quote": "월별", "for": ["s1"], "frequency": "M", "point_in_period": None}]
    rec = record(m, {"s1": final("ask", asks=[{"for": "s1", "axis": "seasonal", "options": [
        {"value": v, "table_id": tab} for v, tab in opts]}], quotes=quotes)}, question=Q, route="ask")
    case = old_case([["DT_Y01"]], question=Q, ask={"expected": policy})
    coverage = {**COVERAGE, "DT_X02": {"M": (2000 * 12, 2010 * 12)}}
    return S.score_case(rec, case, TITLES, coverage)


def test_a_held_ask_policy_keeps_an_ask_outside_the_groups_held_not_missing():
    res = asked("policy_undecided")
    assert res["model_meaning"]["right"] is None and res["final_full"]["right"] is None
    assert res["completed"]["right"] is None
    assert any(d.get("status") == "held with the ask policy" for d in res["final_detail"])


def test_the_option_coverage_is_recorded_as_a_fact_apart_from_the_verdict():
    facts = {f["table"]: (f["offers_frequency"], f["covers_period"]) for f in asked("policy_undecided")
             ["ask_option_facts"]}
    assert facts == {"DT_X01": (True, True), "DT_X02": (True, False)}


def test_a_policy_that_is_not_held_still_scores_the_unneeded_ask_wrong():
    res = asked("not_expected")
    assert res["model_meaning"]["right"] is False and res["completed"]["right"] is False
