"""Plan 56 order 6-3c (users 2026-10-31 19:46 and 20:10 UTC): the aggregation method check path.

The model proposes the method, the source basis, the average basis and their quotes; code checks where each quote
sits on the card (the name of a value chosen for the statistic, the title or a bracket of a table chosen for it, a
unit only, a fact of the card not chosen, nothing), the method and basis pair, the basis of the chosen series and the
data the method needs. Three paths, each keeping the candidate (outcome aggregation_needed, never handed on, never
complete, nothing computed by A):
- enough evidence: the method verified, the kind of computation recorded;
- too little evidence: the method unconfirmed (a unit only, no quote, an unknown method, an unstated average basis,
  a day count the card lacks, a weight series missing);
- evidence of another series: the method unconfirmed (a fact not chosen, a title that does not tell the chosen
  series' basis apart, a chosen series of another basis).

End to end: a monthly-only current-account card asked yearly (made-up answers, the table found by card name and
title basis). The cases the catalog has no table for (values of two bases in one table, a second table on the card)
use a small made-up card on the helpers. No model, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_meaning_aggregation_63 import agg, layer_of, run_ca

from claview_a import check as CH

TITLE = (("title", "경상수지(계절조정)"),)


@pytest.fixture(autouse=True)
def _layer(layer):
    layer_of[:] = [layer]


def average(basis, evidence=TITLE, source_basis="average_balance"):
    row = agg(method="period_average", basis=source_basis)
    row["average_basis"] = basis
    row["average_evidence"] = [{"kind": k, "text": t} for k, t in evidence]
    return row


def kept(s):
    a = s["frequency"]["aggregation"]
    assert s["outcome"] == "aggregation_needed" and s["final_tables"] == [] and s["reasons"] == []
    assert a["status"] == "aggregation_needed" and a["fetched_completeness"] == "not_checked"
    assert a["calculation"] is None or a["calculation"]["computed"] is False
    return a


# ---------------------------------------------------------------- 1. enough evidence: verified, still to aggregate


def test_a_sum_of_a_flow_on_the_chosen_tables_title_is_verified_and_not_computed(layer):
    record, s = run_ca(layer, "2000년대", [agg()])
    a = kept(s)
    assert (a["method_status"], a["why"], a["evidence_level"]) == ("verified", None, "selected_table_title")
    assert a["calculation"] == {"kind": "sum", "computed": False}
    assert record["route"]["action"] == "partial" and not s["condition_asks"]


def test_a_calendar_day_average_is_verified_with_the_days_of_a_leap_year(layer):
    _, s = run_ca(layer, "2024년", [average("calendar_day")])
    a = kept(s)
    assert (a["method_status"], a["why"], a["average_evidence_level"]) == ("verified", None, "selected_table_title")
    assert a["calculation"]["kind"] == "calendar_day_weighted_mean" and not a["meta_availability"][
        "required_series_missing"]  # the day counts come from the calendar, no weight series
    (year,) = a["calculation"]["periods"]
    assert year["days"] == [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] and year["total_days"] == 366
    assert a["meta_availability"]["periods"][0]["needed"] == {"start": "2024-01", "end": "2024-12"}


def test_an_average_the_definition_makes_simple_is_verified_as_a_simple_mean(layer):
    _, s = run_ca(layer, "2023년", [average("simple_of_periods")])
    a = kept(s)
    assert a["method_status"] == "verified" and a["calculation"] == {"kind": "simple_mean", "computed": False}


def test_an_end_value_needs_the_last_month_only_and_a_sum_marks_a_year_short_of_a_month(layer):
    _, s = run_ca(layer, "2020년 이후", [agg(method="period_end", basis="end_balance")])
    years = {p["start"][:4]: p for p in kept(s)["meta_availability"]["periods"]}
    assert years["2020"]["needed"] == {"start": "2020-12", "end": "2020-12"}
    _, s = run_ca(layer, "2020년 이후", [agg()])
    years = {p["start"][:4]: p for p in kept(s)["meta_availability"]["periods"]}
    assert years["2025"]["status"] == "available" and years["2026"]["status"] == "short"


# ---------------------------------------------------------------- 2. too little evidence: unconfirmed, candidate kept


@pytest.mark.parametrize("row, why", [
    (agg(evidence=(("item", "백만달러"),)), "evidence_unit_only"),  # a unit only never confirms a sum
    (agg(evidence=()), "no_evidence"),
    (agg(method="unknown"), "method_unknown"),
    (average("unknown"), "averaging_basis_unstated"),
    (average("calendar_day", evidence=()), "averaging_basis_unstated"),  # the average basis without a quote
    (average("business_day"), "day_count_unavailable"),
    (average("observation_day"), "day_count_unavailable"),
    (agg(method="weighted", basis="ratio", weights=("없는 가중치 계열",)), "required_series_missing"),
    (agg(method="period_end", basis="average_balance"), "basis_mismatch"),
])
def test_too_little_evidence_leaves_the_method_unconfirmed_and_keeps_the_candidate(layer, row, why):
    _, s = run_ca(layer, "2000년대", [row])
    a = kept(s)
    assert (a["method_status"], a["why"], a["calculation"]) == ("unconfirmed", why, None)


def test_a_unit_quote_is_on_the_card_so_it_is_no_output_error(layer):
    _, s = run_ca(layer, "2000년대", [agg(evidence=(("item", "백만달러"),))])
    assert not s.get("output_errors") and kept(s)["evidence_level"] == "unit_only"


def test_a_quote_off_the_card_is_an_output_error_with_no_evidence(layer):
    _, s = run_ca(layer, "2000년대", [average("calendar_day", evidence=(("item", "카드에 없는 정의"),))])
    assert [e["code"] for e in s["output_errors"]] == ["aggregation_evidence_not_on_card"]
    a = kept(s)
    assert a["average_evidence_level"] == "none" and a["why"] == "averaging_basis_unstated"


# ---------------------------------------------------------------- 3. evidence of another series: unconfirmed


class Card:
    """A made-up card: tables {id: {title, items, units, basis, item_basis}}; ``chosen`` values per table."""

    def __init__(self, tables):
        self.spec = tables
        card = {"tables": [{"table_id": t} for t in tables],
                "matched": [{"items": [{"name": n, "tables": [t]} for t, x in tables.items() for n in x["items"]]}]}
        self.card_of = {t: card for t in tables}
        self.row = {t: {"table_id": t, "basis": x.get("basis", {}), "item_basis": x.get("item_basis", {})}
                    for t, x in tables.items()}
        self.views = {t: SimpleNamespace(units=lambda u=tuple(x.get("units", ())): set(u),
                                         items=[SimpleNamespace(name_raw=n) for n in x["items"]])
                      for t, x in tables.items()}

    def kinds_of(self, t, text):
        x = self.spec[t]
        return ({"title"} if text == x["title"] else set()) | ({"item"} if text in x["items"] else set())

    def resolve_item(self, t, text):
        return {"name": text}


MIXED = {"A": {"title": "잔액 현황", "items": ["잔액(말잔)", "잔액(평잔)", "잔액 합계"], "units": ["십억원"],
               "item_basis": {"stock": {"values": ["말잔", "평잔"]}}},
         "B": {"title": "잔액 현황(옛)", "items": ["잔액(평잔)"], "units": ["십억원"]}}


def test_a_fact_of_a_table_not_chosen_is_not_linked():
    lk = Card(MIXED)
    chosen = {"A": set()}
    assert CH._quote_level("잔액 현황(옛)", ["A"], chosen, lk) == "not_linked"
    assert CH._quote_level("잔액(평잔)", ["A"], chosen, lk) == "not_linked"  # a value of A not chosen
    assert CH._quote_level("잔액(평잔)", ["A"], {"A": {"잔액(평잔)"}}, lk) == "selected_series_name"
    assert CH._quote_level("십억원", ["A"], chosen, lk) == "unit_only"
    assert CH._quote_level("없는 말", ["A"], chosen, lk) is None


def test_a_title_does_not_tell_apart_a_series_in_a_table_of_two_bases():
    lk = Card(MIXED)
    assert CH._series_basis("end_balance", "selected_table_title", ["A"], {"A": {"잔액 합계"}}, lk) == \
        "basis_not_specific_to_series"


def test_a_chosen_series_of_another_basis_is_a_mismatch_and_of_its_own_basis_holds():
    lk = Card(MIXED)
    assert CH._series_basis("end_balance", "selected_table_title", ["A"], {"A": {"잔액(평잔)"}}, lk) == "basis_mismatch"
    assert CH._series_basis("end_balance", "selected_table_title", ["A"], {"A": {"잔액(말잔)"}}, lk) is None


def test_a_flow_title_holds_only_for_one_shown_series_or_all_chosen():
    lk = Card({"F": {"title": "수지", "items": ["상품수지", "서비스수지"]}})
    assert CH._series_basis("flow", "selected_table_title", ["F"], {"F": {"상품수지"}}, lk) == \
        "basis_not_specific_to_series"
    assert CH._series_basis("flow", "selected_table_title", ["F"], {"F": {"상품수지", "서비스수지"}}, lk) is None
    assert CH._series_basis("flow", "selected_series_name", ["F"], {"F": {"상품수지"}}, lk) is None


def test_the_levels_and_the_reasons_are_closed_lists():
    assert CH.EVIDENCE_LEVELS == ("definition_or_formula", "selected_series_name", "selected_table_title",
                                  "unit_only", "not_linked", "none")
    assert "evidence_name_or_unit_only" not in CH.AGGREGATION_WHYS and "weighting_unstated" not in CH.AGGREGATION_WHYS


# ---------------------------------------------------------------- contract .5 (the request change of 6-3c)


def test_contract_5_carries_the_average_basis_and_4_is_read_without_it():
    from claview_a import contract as K

    row = K.output_schema()["properties"]["aggregations"]["items"]["properties"]
    assert row["average_basis"]["enum"] == list(K.AVERAGE_BASES) and "average_evidence" in row
    old = K.output_schema("tl-contract-2026-10-28.4")["properties"]["aggregations"]["items"]["properties"]
    assert "average_basis" not in old and "method" in old
    assert "tl-contract-2026-10-28.4" in CH.POLARITY_CONTRACTS and K.CONTRACT_VERSION in CH.POLARITY_CONTRACTS


def test_an_answer_of_contract_4_still_reads_its_aggregation_with_an_unknown_average_basis(layer):
    row = average("calendar_day")
    del row["average_basis"], row["average_evidence"]  # a .4 row has no average fields
    _, s = run_ca(layer, "2024년", [row])
    a = kept(s)
    assert (a["average_basis"], a["why"]) == ("unknown", "averaging_basis_unstated")
