"""Expectation items marked unconfirmed in the standard (``expectation_items``, score .22, plan 52, user 2026-10-29
16:08 UTC), in normal/error pairs on made-up tables and run records only: no model, no network.

An unconfirmed representative or ask is held, never right or wrong; a confirmed period, a fact, contract or evidence
error still fails. One statistic's editions are one answer group with the editions as its allowed tables, and the
final rows split it by period: extra tables, gaps and overlaps fail."""

from __future__ import annotations

import copy

from test_indep_38 import TITLES, final, option, output, record, row  # noqa: F401 (shared made-up fixtures)
from tl_eval import score as S

COVERAGE = {t: {"M": (2000 * 12, 2026 * 12 + 6)} for t in TITLES}
Q = "2024년 가상지표 월별 추이를 보여 줘"
LONG = "2005년부터 2020년까지 가상지표 월별 추이를 보여 줘"


def case(groups, item_status=None, ask=None, final_groups=None, period=("2024-01", "2024-12")):
    c = {"case_id": "T-52", "part": "dev50", "question": Q,
         "statistics": {"count": None, "status": "unconfirmed"}, "groups": groups,
         "representative": {"confirmed_groups": groups, "confirmed_equivalents": {}, "proposed_equivalents": {},
                            "status": "proposed"},
         "ask": ask or {"expected": "not_expected"}, "new_policy": {"status": "same_as_source"},
         "final": {"status": "confirmed", "period": {"from": period[0], "to": period[1]}, "frequency": "M",
                   "basis": [], "groups": final_groups or groups}}
    if item_status:
        c["expectation_items"] = {k: {"state": v, "why": "test"} for k, v in item_status.items()}
    return c


CONFIRMED = {"representative": "confirmed", "ask": "confirmed", "period_frequency": "confirmed"}


def status(**kw):
    return {**CONFIRMED, **kw}


def chosen(table, rows, question=Q, evidence=None):
    m = output([("s1", "가상지표")], [("s1", table)])
    m["periods"] = [{"quote": "2024년" if question == Q else "2005년부터 2020년까지", "for": ["s1"]}]
    m["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    if evidence:
        m["choices"][0]["evidence"] = evidence
    return record(m, {"s1": final(tables=rows)}, question=question)


def score(c, rec):
    return S.score_case(rec, c, TITLES, COVERAGE)


YEAR = [row("DT_X01", "2024-01", "2024-12")]


# ---------------------------------------------------------------- an unconfirmed representative is held


def test_an_unconfirmed_representative_is_held_whether_the_table_is_in_the_group_or_not():
    c = case([["DT_X01", "DT_Y01"]], status(representative="insufficient"))
    inside = score(c, chosen("DT_X01", YEAR))
    outside = score(c, chosen("DT_Z01", [row("DT_Z01", "2024-01", "2024-12")]))
    for res in (inside, outside):
        assert res["items"]["representative"]["verdict"] == "held"
        assert res["model_meaning"]["right"] is None and res["model_table_set"]["right"] is None
        assert res["completed"]["right"] is None
    assert inside["items"]["period_frequency"]["verdict"] == "right"


def test_the_same_choice_is_scored_when_the_representative_is_confirmed():
    c = case([["DT_X01", "DT_Y01"]], status())
    assert score(c, chosen("DT_X01", YEAR))["items"]["representative"]["verdict"] == "right"
    wrong = score(c, chosen("DT_Z01", [row("DT_Z01", "2024-01", "2024-12")]))
    assert wrong["items"]["representative"]["verdict"] == "wrong" and wrong["model_meaning"]["right"] is False


def test_an_evidence_error_still_fails_under_an_unconfirmed_representative():
    c = case([["DT_X01", "DT_Y01"]], status(representative="insufficient"))
    res = score(c, chosen("DT_X01", YEAR, evidence=[{"kind": "item", "text": "카드에없는값"}]))
    assert res["completed"]["right"] is False
    assert res["completed"]["blocked_by"]


def test_a_confirmed_period_still_fails_under_an_unconfirmed_representative():
    c = case([["DT_X01", "DT_Y01"]], status(representative="insufficient"))
    res = score(c, chosen("DT_X01", [row("DT_X01", "2024-01", "2024-06")]))
    assert res["items"]["period_frequency"]["verdict"] == "wrong"
    assert res["completed"]["right"] is False and res["final_full"]["right"] is False


# ---------------------------------------------------------------- an unconfirmed ask is held, the tables are scored


ASK = {"expected": "required", "axis": "seasonal", "tables": ["DT_X01", "DT_X02"]}


def asked():
    options = [option("원계열", "DT_X01"), option("계절조정", "DT_X02")]
    m = output([("s1", "가상지표")], [], asks=[{"for": "s1", "axis": "seasonal", "options": options}])
    asks = [{"axis": "seasonal", "options": [{"value": "원계열", "table_id": "DT_X01"},
                                             {"value": "계절조정", "table_id": "DT_X02"}]}]
    return record(m, {"s1": final(outcome="ask", asks=asks)}, route="ask")


def test_no_ask_where_the_ask_item_is_unconfirmed_is_held_not_wrong():
    c = case([["DT_X01", "DT_X02"]], status(ask="insufficient"), ask=ASK)
    res = score(c, chosen("DT_X01", YEAR))
    assert res["items"]["ask"]["verdict"] == "held"
    assert res["items"]["representative"]["verdict"] == "right"
    assert res["model_meaning"]["right"] is not False and res["completed"]["right"] is not False


def test_an_ask_where_the_ask_item_is_unconfirmed_is_held_too():
    c = case([["DT_X01", "DT_X02"]], status(ask="insufficient"), ask=ASK)
    res = score(c, asked())
    assert res["items"]["ask"]["verdict"] == "held"
    assert res["items"]["representative"]["verdict"] in ("review", "na")  # no table chosen: nothing to score
    assert res["completed"]["right"] is not False


def test_a_table_outside_the_group_is_still_wrong_under_an_unconfirmed_ask():
    c = case([["DT_X01", "DT_X02"]], status(ask="insufficient"), ask=ASK)
    res = score(c, chosen("DT_Z01", [row("DT_Z01", "2024-01", "2024-12")]))
    assert res["items"]["representative"]["verdict"] == "wrong" and res["model_meaning"]["right"] is False


def test_with_a_confirmed_ask_the_missing_ask_stays_wrong():
    c = case([["DT_X01", "DT_X02"]], status(), ask=ASK)
    res = score(c, chosen("DT_X01", YEAR))
    assert res["items"]["ask"]["verdict"] == "wrong" and res["model_meaning"]["right"] is False


# ---------------------------------------------------------------- one statistic's editions


EDITIONS = [["DT_X01", "DT_X03"]]  # one statistic: either edition may be its representative


def stitched(rows):
    return chosen("DT_X01", rows, question=LONG)


def edition_case(groups=EDITIONS):
    c = case(groups, status(), period=("2005-01", "2020-12"))
    c["question"] = LONG
    return c


def test_one_representative_and_two_editions_by_period_is_right():
    res = score(edition_case(), stitched([row("DT_X03", "2005-01", "2010-12"), row("DT_X01", "2011-01", "2020-12")]))
    assert res["items"]["representative"]["verdict"] == "right"
    assert res["final_table_set"]["right"] is True
    assert res["items"]["period_frequency"]["verdict"] == "right"


def test_an_extra_table_beside_the_editions_fails():
    res = score(edition_case(), stitched([row("DT_X03", "2005-01", "2010-12"), row("DT_X01", "2011-01", "2020-12"),
                                          row("DT_Y01", "2011-01", "2020-12")]))
    assert res["final_table_set"]["right"] is False and res["completed"]["right"] is False


def test_a_gap_between_editions_fails():
    res = score(edition_case(), stitched([row("DT_X03", "2005-01", "2009-12"), row("DT_X01", "2011-01", "2020-12")]))
    assert res["items"]["period_frequency"]["verdict"] == "wrong" and res["completed"]["right"] is False


def test_an_overlap_between_editions_fails():
    res = score(edition_case(), stitched([row("DT_X03", "2005-01", "2011-06"), row("DT_X01", "2011-01", "2020-12")]))
    assert res["items"]["period_frequency"]["verdict"] == "wrong" and res["completed"]["right"] is False


def test_editions_written_as_separate_statistics_would_call_one_representative_missing():
    """The shape plan 52 v1 used, kept as the error it was: each edition its own required group."""
    res = score(edition_case([["DT_X03"], ["DT_X01"]]),
                stitched([row("DT_X03", "2005-01", "2010-12"), row("DT_X01", "2011-01", "2020-12")]))
    assert res["model_table_set"]["right"] is False and res["model_table_set"]["missing_groups"] == [["DT_X03"]]


# ---------------------------------------------------------------- without expectation_items nothing changes


def test_a_standard_without_expectation_items_has_no_items_and_scores_as_before():
    c = case([["DT_X01"]])
    res = score(c, chosen("DT_X01", YEAR))
    assert "items" not in res and res["completed"]["right"] is True
    held = copy.deepcopy(c)
    held["expectation_items"] = {k: {"state": "confirmed"} for k in CONFIRMED}
    assert score(held, chosen("DT_X01", YEAR))["completed"]["right"] is True
