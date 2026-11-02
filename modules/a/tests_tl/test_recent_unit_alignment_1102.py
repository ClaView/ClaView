"""Exact recent-unit conversion keeps stated frequencies; nonintegral spans stay rejected."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from claview_a import periods as P
from claview_a.periods import resolver as R

AS_OF = "2026-09-29"
WORDS = {"M": "월별", "Q": "분기별", "H": "반기별", "Y": "연간"}
COVERAGE = {"M": ("2000.01", "2026.08"), "Q": ("2000 1/4", "2026 2/4"),
            "H": ("2000 1/2", "2026 1/2"), "Y": ("2000", "2025")}


def select(quote, frequency):
    conditions = P.read_quotes([quote], [WORDS[frequency]])["conditions"]
    view = SimpleNamespace(table_id="T", periods=[(frequency, *COVERAGE[frequency])], table_units=["십억원"])
    return conditions, P.final_tables("T", ["T"], {"T": "ok"}, {"T": view}, conditions, AS_OF)


@pytest.mark.parametrize("quote,frequency,start,end", [
    ("최근 6개월", "Q", "2026-01", "2026-06"),
    ("최근 12개월", "Y", "2025-01", "2025-12"),
    ("최근 2분기", "H", "2026-01", "2026-06"),
    ("최근 12개월", "H", "2025-07", "2026-06"),
    ("최근 4분기", "Y", "2025-01", "2025-12"),
    ("최근 3년", "M", "2023-09", "2026-08"),
])
def test_exact_recent_length_is_accepted_in_the_stated_frequency(quote, frequency, start, end):
    conditions, got = select(quote, frequency)
    assert P.frequency_fit(conditions, frequency, AS_OF)
    assert got["status"] == "selected", got
    assert got["tables"][0]["frequency"] == frequency
    assert got["tables"][0]["period"] == [{"start": start, "end": end}]
    assert not got["results"]["T"]["assumptions"]


@pytest.mark.parametrize("quote,frequency", [
    ("최근 5개월", "Q"), ("최근 11개월", "Y"), ("최근 3분기", "H"),
])
def test_incomplete_stated_period_is_not_rounded_or_changed(quote, frequency):
    conditions, got = select(quote, frequency)
    assert not P.frequency_fit(conditions, frequency, AS_OF)
    assert got["status"] == "failed"
    assert got["tables"] == []


def test_historical_data_latest_policy_keeps_its_original_strict_rule(monkeypatch):
    monkeypatch.setattr(R, "_POLICY", dataclasses.replace(
        R.default_policy(), anchor_rule="data_latest", recent_anchor_rule=None))
    _, got = select("최근 6개월", "Q")
    assert got["status"] == "failed"
    assert got["reason"] == "period_unresolved"
    assert got["detail"] == "conditions_incomplete"
