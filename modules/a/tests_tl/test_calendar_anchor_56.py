"""Plan 56 order 4 (bundle 3, policy ①): a quoted relative or open period counts from the period as_of falls in.

The ① example table (``data/period_calendar_examples_56.json``) through the resolver on the as_of calendar; then the
final selection among candidates: a table that lags the request's end keeps it (``latest_unpublished``, the period
handed on still ends at as_of's period), a table whose end another candidate covers does not (an old edition that
stopped), a request that starts before every candidate fails (``period_not_covered``, detail ``past_shortfall``); a request with no period keeps the
old anchor; the policy's ``anchor_rule: data_latest`` gives the rule before. Synthetic tables; no model, no network.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from claview_a import periods as PR
from claview_a.periods import resolver as R

DATA = json.loads((Path(__file__).parent / "data" / "period_calendar_examples_56.json").read_text(encoding="utf-8"))
AS_OF = DATA["as_of"]
CALENDAR = {"bundle_id": "t", "anchor_source": "as_of_calendar", "anchor": AS_OF[:7]}


def conditions(quote):
    return PR.read_quotes([quote], [])["conditions"]


@pytest.mark.parametrize("row", DATA["rows"], ids=[f"{r['quote']}|{r['table']}" for r in DATA["rows"]])
def test_the_example_table_reads_as_agreed(row, monkeypatch):
    # Preserve the pre-11-02 calendar policy examples as a versioned comparison.
    monkeypatch.setattr(R, "_POLICY", dataclasses.replace(R.default_policy(), recent_anchor_rule="as_of_calendar"))
    got = R.resolve_period_for_table(conditions(row["quote"]), DATA["tables"][row["table"]], AS_OF, context=CALENDAR)
    assert got["status"] == "ok"
    assert [got["applied"]["start"], got["applied"]["end"]] == row["applied"]
    assert [[k["start"], k["end"], k["kind"]] for k in got["periods"]["missing_kinds"]] == row["missing"]


@pytest.mark.parametrize("row", DATA["not_fit"], ids=[r["quote"] for r in DATA["not_fit"]])
def test_a_span_a_coarser_table_cannot_start_on_is_never_rounded(row, monkeypatch):
    monkeypatch.setattr(R, "_POLICY", dataclasses.replace(R.default_policy(), recent_anchor_rule="as_of_calendar"))
    got = R.resolve_period_for_table(conditions(row["quote"]), DATA["tables"][row["table"]], AS_OF, context=CALENDAR)
    assert (got["status"], got["issue"]["detail"]) == (row["status"], row["detail"])


def test_the_table_latest_anchor_still_reads_as_before():
    got = R.resolve_period_for_table(conditions("최근 3년"), DATA["tables"]["M"], AS_OF)
    assert (got["applied"]["start"], got["applied"]["end"]) == ("202309", "202608")
    assert got["periods"]["missing_kinds"] == []


# ---------------------------------------------------------------- the final selection


def view(table_id, *periods):
    # plan 56 order 7 (input only): a table unit, the preservation evidence a replacement or a stitch needs
    return SimpleNamespace(table_id=table_id, periods=list(periods), table_units=["십억원"])


def select(quote, views, usable=None, as_of=AS_OF):
    ids = list(views)
    usable = {t: "ok" for t in ids} if usable is None else usable
    return PR.final_tables(ids[0], ids, usable, views, conditions(quote), as_of)


def months(got):
    return [(p["start"], p["end"]) for t in got["tables"] for p in t["period"]]


def test_a_lagging_table_is_kept_and_its_unpublished_end_marked():
    got = select("2020년부터", {"T": view("T", ("M", "2000.01", "2026.08"))})
    assert got["status"] == "selected" and months(got) == [("2020-01", "2026-10")]
    assert got["tables"][0]["latest_unpublished"] == [{"start": "2026-09", "end": "2026-10"}]
    assert got["anchor"] == "2026-10"


def test_a_table_to_2025_asked_2026_is_kept():
    got = select("2026년", {"T": view("T", ("Y", "2000", "2025"))})
    assert got["status"] == "selected" and got["tables"][0]["latest_unpublished"] == [
        {"start": "2026-01", "end": "2026-12"}]


def test_an_old_edition_another_candidate_continues_does_not_pass_alone():
    views = {"OLD": view("OLD", ("M", "2000.01", "2015.12")), "NEW": view("NEW", ("M", "2016.01", "2026.08"))}
    got = select("2010년부터", views)
    assert got["status"] == "selected" and got["detail"] == "stitched"
    assert [t["table_id"] for t in got["tables"]] == ["NEW", "OLD"]
    assert got["latest_unpublished"] == [{"start": "2026-09", "end": "2026-10"}]
    got = select("2020년부터", views)  # the new edition alone covers it; the old one is never chosen
    assert [t["table_id"] for t in got["tables"]] == ["NEW"]


def test_an_old_edition_whose_end_a_unverified_candidate_covers_is_not_unpublished():
    views = {"OLD": view("OLD", ("M", "2000.01", "2015.12")), "NEW": view("NEW", ("M", "2000.01", "2026.08"))}
    got = select("2010년부터", views, usable={"OLD": "ok"})
    assert got["status"] == "failed" and got["reason"] == "content_unverified"


def test_a_request_starting_before_every_candidate_is_a_past_shortfall():
    got = select("2000년부터", {"T": view("T", ("Y", "2010", "2025"))})
    assert got["status"] == "failed" and (got["reason"], got["detail"]) == ("period_not_covered", "past_shortfall")


def test_a_request_with_no_period_keeps_the_old_anchor():
    got = PR.final_tables("T", ["T"], {"T": "ok"}, {"T": view("T", ("M", "2000.01", "2026.08"))}, [], AS_OF)
    assert got["status"] == "selected" and months(got) == [("2023-09", "2026-08")]
    assert "latest_unpublished" not in got["tables"][0]


def test_the_data_latest_rule_gives_the_rule_before(monkeypatch):
    monkeypatch.setattr(R, "_POLICY", dataclasses.replace(R.default_policy(), anchor_rule="data_latest"))
    got = select("2020년부터", {"T": view("T", ("M", "2000.01", "2026.08"))})
    assert months(got) == [("2020-01", "2026-08")] and "latest_unpublished" not in got["tables"][0]


def test_the_packaged_policy_names_the_calendar_rule():
    policy = R.default_policy()
    assert policy.anchor_rule == "as_of_calendar" and policy.relative_rules_version
    # .4 changes recent windows only; calendar offsets, explicit periods and no-default questions retain their rules.
    assert policy.recent_anchor_rule == "data_latest"
    assert policy.version == "period-policy/2026-11-03.5"


def test_a_period_after_as_of_is_never_unpublished():
    got = R.resolve_period_for_table(conditions("2027년"), DATA["tables"]["Y"], AS_OF, context=CALENDAR)
    assert [k["kind"] for k in got["periods"]["missing_kinds"]] == ["after_as_of"]
    assert select("2027년", {"T": view("T", ("Y", "2000", "2025"))})["status"] == "failed"
