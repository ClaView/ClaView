"""Plan 36 (user 2026-10-28 14:59 UTC): "월말", "연말"/"년말" are read as the monthly / yearly frequency with the
end point; "분기말"/"반기말" are read whole; the end point is kept in the record per quoted frequency word. Pairs of
normal and error cases, through ``periods.read_quotes`` and the straight graph (no model, no network)."""

from __future__ import annotations

import json

import pytest

from conftest import fill_option_evidence  # noqa: E402

from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.conditions.finders import find_frequency_conditions, find_period_conditions
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.basis import values as basis_values
from claview_a.search.tables import selection_catalog
from claview_a.text.normalize import normalize_text

AS_OF = "2026-10-22"


def frequencies_read(quote: str) -> tuple[list[tuple], list[str]]:
    got = PR.read_quotes([], [quote])
    return [(c["value"], c["attrs"]["anchor"]) for c in got["conditions"]], [p["code"] for p in got["problems"]]


@pytest.mark.parametrize("quote, expected", [
    ("월말", [("M", "end")]), ("월 말", [("M", "end")]), ("월말에", [("M", "end")]), ("월말 기준", [("M", "end")]),
    ("연말", [("Y", "end")]), ("년말", [("Y", "end")]), ("연말 기준", [("Y", "end")]),
    ("분기말", [("Q", "end")]), ("반기말", [("H", "end")]), ("분기말 기준", [("Q", "end")]),
    ("매 월말", [("M", "end")]), ("월말마다", [("M", "end")]), ("각 연말", [("Y", "end")]),  # unchanged
    ("월별", [("M", None)]), ("분기별", [("Q", None)]),  # no end point: unchanged
])
def test_an_end_word_is_read_as_the_frequency_and_its_end_point(quote, expected):
    assert frequencies_read(quote) == (expected, [])


@pytest.mark.parametrize("quote", [
    "월평균",  # a calculation, out of scope: not a frequency (unchanged)
    "말",  # an end word alone names no frequency
    "지난 월말", "이번 월말",  # a time or comparison word before it: one point (the existing rule)
    "전월말", "7월말", "2024년 말", "작년 말",  # joined to a number or another word: a point, not a frequency
    "월말잔액", "월말잔",  # joined to 말잔: a basis word, not a frequency
])
def test_other_end_words_are_not_a_frequency(quote):
    assert frequencies_read(quote) == ([], ["frequency_unresolved"])


def test_an_end_word_without_an_end_point_is_still_a_word_left():
    """The end word counts as read only next to a frequency read with the end point; it is never dropped as such."""
    got = PR.read_quotes([], ["말 월별"])
    assert [p["code"] for p in got["problems"]] == ["frequency_partially_read"]
    assert got["problems"][0]["uncovered"] == ["말"]


def test_year_end_points_stay_periods_and_month_end_balance_stays_a_basis_word():
    assert find_frequency_conditions(normalize_text("2024년 말")) == []
    assert [c["kind"] for c in find_period_conditions(normalize_text("2024년 말"))] == ["period"]
    assert find_frequency_conditions(normalize_text("M2 월말잔액 추이")) == []
    assert basis_values("월말잔액", "stock") == {"말잔"} and basis_values("월말", "stock") == set()


def test_frequency_points_keep_quote_for_frequency_and_end_point():
    rows = [{"quote": "월말", "for": ["s1", "s2"]}, {"quote": "분기별", "for": ["s2"]}, {"quote": "어쩌다", "for": ["s1"]}]
    got = PR.read_quotes([], [r["quote"] for r in rows])
    assert PR.frequency_points(got, rows) == [
        {"quote": "월말", "for": ["s1", "s2"], "frequency": "M", "point_in_period": "end"},
        {"quote": "분기별", "for": ["s2"], "frequency": "Q", "point_in_period": None},
        {"quote": "어쩌다", "for": ["s1"], "frequency": None, "point_in_period": None,
         "problem": "frequency_unresolved"}]


M2_MONTH_END = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월말 기준으로 보여줘"


def _m2_out(cards, frequency: str, layer) -> dict:
    card = next(c for c in cards if c["name"] == "M2 상품별 구성내역")
    views = selection_catalog(layer).views
    rows = [r for r in card["tables"] if r["basis"].get("stock") == ["평잔"] and r["basis"].get("seasonal") == ["원계열"]]
    rows.sort(key=lambda r: PR._end_key(views[r["table_id"]]), reverse=True)
    table = f"{card['card_id']}.{table_numbers(card)[rows[0]['table_id']]}"
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"}]
    out["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["frequencies"] = [{"quote": frequency, "for": ["s1"]}]
    out["choices"] = [{"for": ["s1"], "table": table, "evidence": [{"kind": "item", "text": "M2(평잔 원계열)"}]}]
    return out


def _go(question: str, out: dict, layer) -> dict:
    cards = build_cards(question, build_pool(question, layer), layer)
    state = run(question, layer, lambda payload, request: json.dumps(fill_option_evidence(out, cards),
                                                                     ensure_ascii=False), Settings("fake"), AS_OF)
    return state


def test_the_record_keeps_the_end_point_apart_from_the_resolver_anchor_and_the_basis(layer):
    cards = build_cards(M2_MONTH_END, build_pool(M2_MONTH_END, layer), layer)
    state = _go(M2_MONTH_END, _m2_out(cards, "월말", layer), layer)
    (d,) = state["record"]["decisions"]
    assert d["outcome"] == "selected" and state["action"] == "handoff"
    assert d["frequency_quotes"] == [{"quote": "월말", "for": ["s1"], "frequency": "M", "point_in_period": "end"}]
    assert {r["frequency"] for r in d["final_tables"]} == {"M"}
    # the end point chooses no basis: the stated basis stays, and the resolver's anchor is another field
    assert d["basis"]["stock"]["value"] == ["평잔"]
    assert "point_in_period" not in d["period"] and d["period"]["anchor"] != "end"


M2_MONTH_AVERAGE = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월평균으로 보여줘"


def test_an_unread_frequency_word_still_stops_the_statistic(layer):
    cards = build_cards(M2_MONTH_AVERAGE, build_pool(M2_MONTH_AVERAGE, layer), layer)
    state = _go(M2_MONTH_AVERAGE, _m2_out(cards, "월평균", layer), layer)
    (d,) = state["record"]["decisions"]
    assert d["outcome"] == "failed" and [r["code"] for r in d["reasons"]] == ["frequency_unresolved"]
    assert d["frequency_quotes"] == [{"quote": "월평균", "for": ["s1"], "frequency": None, "point_in_period": None,
                                      "problem": "frequency_unresolved"}]
