"""Plan 56 order 6-3a: the period meaning and the aggregation rows of contract .4 answers, read by code.

Period meaning (user 2026-10-31 17:53 UTC): range is read by the period reader as before, and a range it cannot read
is the code's failure (never asked); user_undecided asks the statistic's period (cause unreadable_range, no
default); same_as uses the referred statistics' range when it is one, else one common question for the row's
statistics; latest_available takes the latest period of the decided frequency, a question only for the frequency.

Aggregation (policy ④ supplement, users 17:56 and 18:28 UTC): a quoted frequency no candidate gives, made from a
finer one, keeps the candidate (outcome aggregation_needed, never handed on, never complete) with three layers apart:
the method's evidence level and status (name or unit evidence is unconfirmed), the availability by the metadata per
target period by the method's needs, and the completeness after a fetch (not checked).

Parsed outputs go into ``check.check`` directly (the parser of contract .4 comes in 6-3b): the made-up answer is parsed
under the current contract, then its rows get the fields of contract .4 and the parsed answer names it. Real
catalog, no model, no network. Tables are found by card name and title basis, never by ID.
"""

from __future__ import annotations

import json

import pytest
from conftest import fill_option_evidence  # noqa: E402
from test_explicit_conditions_53 import CA
from test_explicit_conditions_53 import ref as ca_ref
from test_polarity_62 import ref as m2_ref

from claview_a import check as CH
from claview_a import periods as PR
from claview_a.cards import build_cards
from claview_a.contract import parse_output
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"
# the fixed inputs here are of contract .3 (plan 56 order 6-3b: contract .4 is the current one)
OLD = "tl-contract-2026-10-28.3"
POLAR = CH.POLARITY_CONTRACTS[0]
KEYS = ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks", "missing",
        "alternatives")


def judged(layer, q, build, periods=(), splitters=(), aggregations=()):
    """``build(cards) -> out`` (current contract); ``periods``: rows {quote, for, meaning, refers_to};
    ``splitters``: polarity rows; ``aggregations``: rows of the new contract."""
    cards = build_cards(q, build_pool(q, layer), layer)
    out = build(cards)
    out["periods"] = [{"quote": p["quote"], "for": p["for"]} for p in periods]
    out["splitters"] = [{"quote": s["quote"], "axis": s["axis"], "for": s["for"]} for s in splitters]
    parsed = parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards, OLD)
    parsed["output"]["periods"] = [dict(p) for p in periods]
    parsed["output"]["splitters"] = [dict(s) for s in splitters]
    parsed["output"]["aggregations"] = [dict(a) for a in aggregations]
    parsed["answer_contract"] = POLAR
    record = CH.check(q, cards, parsed, selection_catalog(layer).views, AS_OF)
    return record, {d["statistic"]: d for d in record["decisions"]}


def empty():
    return {k: [] for k in KEYS}


def period(quote, sids, meaning, refers_to=()):
    return {"quote": quote, "for": list(sids), "meaning": meaning, "refers_to": list(refers_to)}


def polar(quote, axis, value, sid):
    return {"quote": quote, "axis": axis, "value": value, "polarity": "required", "for": [sid]}


def m2(stats, frequency="월별"):
    """M2 tables chosen per statistic: stats = [(sid, quote, stock)]."""
    def build(cards):
        out = empty()
        out["statistics"] = [{"id": sid, "quote": quote, "name": quote} for sid, quote, _ in stats]
        if frequency:
            out["frequencies"] = [{"quote": frequency, "for": [sid for sid, _, _ in stats]}]
        out["choices"] = [{"for": [sid], "table": m2_ref(cards, layer_of[0], stock=stock, seasonal="원계열"),
                           "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]} for sid, _, stock in stats]
        return out
    return build


layer_of: list = []


@pytest.fixture(autouse=True)
def _layer(layer):
    layer_of[:] = [layer]


def stock_rows(stats):
    return [polar(stock, "stock", stock, sid) for sid, _, stock in stats] + \
        [polar("원계열", "seasonal", "원계열", sid) for sid, _, _ in stats]


# ---------------------------------------------------------------- the period meaning


def test_a_range_the_user_did_not_decide_is_asked_never_defaulted(layer):
    q = "팬데믹 이전 월별 M2 상품별 구성내역을 말잔 원계열로 보여줘"
    stats = [("s1", "M2 상품별 구성내역", "말잔")]
    _, d = judged(layer, q, m2(stats), periods=[period("팬데믹 이전", ["s1"], "user_undecided")],
                  splitters=[polar("말잔", "stock", "말잔", "s1"), polar("원계열", "seasonal", "원계열", "s1")])
    s = d["s1"]
    assert s["outcome"] == "ask_condition" and s["final_tables"] == [] and s["reasons"] == []
    assert [(a["kind"], a["cause"]) for a in s["condition_asks"]] == [("period", "unreadable_range")]
    assert s["period"]["default_applied"] is False and s["period"]["cause"] == "unreadable_range"


def test_a_range_the_reader_cannot_read_is_the_codes_failure_never_asked(layer):
    q = "팬데믹 이전 월별 M2 상품별 구성내역을 말잔 원계열로 보여줘"
    stats = [("s1", "M2 상품별 구성내역", "말잔")]
    _, d = judged(layer, q, m2(stats), periods=[period("팬데믹 이전", ["s1"], "range")],
                  splitters=[polar("말잔", "stock", "말잔", "s1"), polar("원계열", "seasonal", "원계열", "s1")])
    s = d["s1"]
    assert s["outcome"] == "failed" and [r["code"] for r in s["reasons"]] == ["period_unresolved"]
    assert not s["condition_asks"]


TWO = "2024년 월별 M2 말잔 원계열과 같은 기간 M2 평잔 원계열을 보여줘"
STATS2 = [("s1", "M2 말잔 원계열", "말잔"), ("s2", "M2 평잔 원계열", "평잔")]


def test_the_same_range_as_another_statistic_is_taken_when_it_is_one(layer):
    _, d = judged(layer, TWO, m2(STATS2), splitters=stock_rows(STATS2),
                  periods=[period("2024년", ["s1"], "range"), period("같은 기간", ["s2"], "same_as", ["s1"])])
    assert d["s1"]["outcome"] == d["s2"]["outcome"] == "selected", (d["s1"]["reasons"], d["s2"]["reasons"])
    assert d["s2"]["period_reference"] == {"refers_to": ["s1"], "quotes": ["2024년"], "status": "used"}
    assert [r["period"] for r in d["s2"]["final_tables"]] == [r["period"] for r in d["s1"]["final_tables"]]


def test_a_same_range_with_nothing_to_refer_to_is_asked_once_for_both(layer):
    q = "월별 M2 말잔 원계열과 M2 평잔 원계열을 같은 기간으로 보여줘"
    _, d = judged(layer, q, m2(STATS2), splitters=stock_rows(STATS2),
                  periods=[period("같은 기간", ["s1", "s2"], "same_as", ["s1", "s2"])])
    for s in d.values():
        assert s["outcome"] == "ask_condition" and s["period_reference"]["status"] == "none"
        (a,) = s["condition_asks"]
        assert (a["kind"], a["cause"], a["statistics"]) == ("period", "unreadable_range", ["s1", "s2"])


def test_the_latest_value_takes_the_latest_period_and_asks_nothing(layer):
    q = "최신 월별 M2 상품별 구성내역을 말잔 원계열로 보여줘"
    stats = [("s1", "M2 상품별 구성내역", "말잔")]
    _, d = judged(layer, q, m2(stats), periods=[period("최신", ["s1"], "latest_available")],
                  splitters=[polar("말잔", "stock", "말잔", "s1"), polar("원계열", "seasonal", "원계열", "s1")])
    s = d["s1"]
    assert s["outcome"] == "selected" and not s["condition_asks"], s["reasons"]
    latest = s["period"]["latest"]
    assert latest["frequency"] == "M" and [r["frequency"] for r in s["final_tables"]] == ["M"]
    (row,) = s["final_tables"]
    assert row["period"][0]["start"] == row["period"][-1]["end"]  # one month: the latest the tables hold


def test_the_latest_value_with_several_frequencies_asks_the_frequency_only(layer):
    q = "최신 M2 상품별 구성내역을 말잔 원계열로 보여줘"
    stats = [("s1", "M2 상품별 구성내역", "말잔")]
    _, d = judged(layer, q, m2(stats, frequency=None), periods=[period("최신", ["s1"], "latest_available")],
                  splitters=[polar("말잔", "stock", "말잔", "s1"), polar("원계열", "seasonal", "원계열", "s1")])
    s = d["s1"]
    assert s["outcome"] == "ask_condition" and [a["kind"] for a in s["condition_asks"]] == ["frequency"]


# ---------------------------------------------------------------- aggregation


def current_account(period_quote, frequency="연간"):
    def build(cards):
        r, _, card = ca_ref(cards, CA, layer_of[0], seasonal="계절조정")
        out = empty()
        out["statistics"] = [{"id": "s1", "quote": "경상수지", "name": "경상수지"}]
        out["frequencies"] = [{"quote": frequency, "for": ["s1"]}]
        out["choices"] = [{"for": ["s1"], "table": r, "evidence": [{"kind": "title", "text": card["name"]}]}]
        return out
    return build


def agg(method="sum", basis="flow", evidence=(("title", "경상수지(계절조정)"),), weights=()):
    return {"for": ["s1"], "requested_frequency": "Y", "source_frequency": "M", "method": method,
            "source_basis": basis, "evidence": [{"kind": k, "text": t} for k, t in evidence],
            "weights": list(weights)}


def run_ca(layer, period_quote, rows):
    q = f"{period_quote} 계절조정 경상수지를 연간으로 보여줘"
    record, d = judged(layer, q, current_account(period_quote), periods=[period(period_quote, ["s1"], "range")],
                       aggregations=rows)
    return record, d["s1"]


def test_a_yearly_request_on_monthly_tables_keeps_the_candidate_with_its_method(layer):
    # plan 56 order 6-3c (registered): the quote is the chosen table's title, on a card showing that table one series,
    # so it is linked evidence and sum of a flow is verified; before: unconfirmed, evidence_name_or_unit_only,
    # name_or_unit. Verified is still no aggregation done: the candidate stays, nothing is handed on or complete.
    record, s = run_ca(layer, "2000년대", [agg()])
    a = s["frequency"]["aggregation"]
    assert s["outcome"] == "aggregation_needed" and s["final_tables"] == [] and s["reasons"] == []
    assert record["route"]["action"] == "partial" and not s["condition_asks"]  # never handed on, never asked
    assert (a["requested"], a["source"], a["method"], a["source_basis"]) == ("Y", "M", "sum", "flow")
    assert (a["method_status"], a["why"], a["evidence_level"]) == ("verified", None, "selected_table_title")
    assert a["fetched_completeness"] == "not_checked" and a["meta_availability"]["gaps_unchecked"] is True
    assert a["source_range"] == {"start": "2000-01", "end": "2009-12"}
    years = a["meta_availability"]["periods"]
    assert len(years) == 10 and all(p["status"] == "available" for p in years)
    assert years[0]["needed"] == {"start": "2000-01", "end": "2000-12"}  # a sum needs every month


def test_an_end_value_needs_the_last_month_only_and_a_short_year_is_marked(layer):
    _, s = run_ca(layer, "2020년 이후", [agg(method="period_end", basis="end_balance")])
    years = {p["start"][:4]: p for p in s["frequency"]["aggregation"]["meta_availability"]["periods"]}
    assert years["2020"]["needed"] == {"start": "2020-12", "end": "2020-12"}
    assert years["2025"]["status"] == "available" and years["2026"]["status"] == "short"


# plan 56 order 6-3c (registered): the level is where the quote sits (was name_or_unit)
@pytest.mark.parametrize("row, why, level", [
    (agg(method="period_end", basis="average_balance"), "basis_mismatch", "selected_table_title"),
    (agg(method="unknown"), "method_unknown", "selected_table_title"),
    (agg(evidence=()), "no_evidence", "none"),
])
def test_an_unconfirmed_method_keeps_the_candidate(layer, row, why, level):
    _, s = run_ca(layer, "2000년대", [row])
    a = s["frequency"]["aggregation"]
    assert s["outcome"] == "aggregation_needed" and (a["method_status"], a["why"], a["evidence_level"]) == (
        "unconfirmed", why, level)


def test_evidence_not_on_the_card_is_an_output_error_and_the_candidate_stays(layer):
    _, s = run_ca(layer, "2000년대", [agg(evidence=(("item", "카드에 없는 정의"),))])
    assert s["outcome"] == "aggregation_needed"
    assert [e["code"] for e in s["output_errors"]] == ["aggregation_evidence_not_on_card"]
    assert s["frequency"]["aggregation"]["evidence_level"] == "none"


def test_weights_missing_from_the_table_are_a_shortfall_not_a_refusal(layer):
    _, s = run_ca(layer, "2000년대", [agg(method="weighted", basis="ratio", weights=("없는 가중치 계열",))])
    a = s["frequency"]["aggregation"]
    assert s["outcome"] == "aggregation_needed" and a["meta_availability"]["required_series_missing"] == [
        "없는 가중치 계열"]


def test_without_an_aggregation_row_the_method_is_unknown_and_the_candidate_stays(layer):
    _, s = run_ca(layer, "2000년대", [])
    a = s["frequency"]["aggregation"]
    assert s["outcome"] == "aggregation_needed" and a["why"] == "method_unknown" and a["model_row"] is False


def test_the_latest_quote_of_each_frequency():
    from types import SimpleNamespace

    v = SimpleNamespace(table_id="T", periods=[("Y", "2000", "2025"), ("Q", "2000 1/4", "2026 2/4"),
                                               ("M", "2000.01", "2026.07")])
    assert [PR.latest_quote([v], f) for f in "YQM"] == ["2025년", "2026년 2분기", "2026년 7월"]
    assert PR.latest_quote([v], "H") is None
