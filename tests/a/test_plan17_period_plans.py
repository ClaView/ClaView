"""Actual shared package, synthetic metadata; no network or model calls."""
import copy
from types import SimpleNamespace

import pytest

from claview.a.select import periods as P


def view(t, f="M", start="202001", end="202608"):
    return SimpleNamespace(table_id=t, periods=[(f, start, end)])


def inp(conditions=(), inferred=None):
    return {"conditions": list(conditions), "condition_fields": {}, "inferred": inferred, "as_of": "2026-10-21"}


def recent():
    return {"id": "p1", "kind": "period", "attrs": {"form": "recent", "amount": 3, "unit": "month",
            "relative": True, "extract_status": "unresolved"}}


def closed(start, end):
    return {"id": "p1", "kind": "period", "attrs": {"form": "range", "extract_status": "explicit",
            "start_expr": {"year": start}, "end_expr": {"year": end}}}


@pytest.mark.parametrize("freq,start,end,wanted", [
    ("Y", "2000", "2025", "2016"), ("H", "200001", "202502", "202101"),
    ("Q", "200001", "202504", "202101"), ("M", "200001", "202512", "202301")])
def test_defaults_are_resolved_by_shared_policy(freq, start, end, wanted):
    result = P.resolve(inp(), view("T", freq, start, end))
    assert result["status"] == "ok"
    assert result["applied"]["start"] == wanted


def test_common_anchor_uses_members_not_unrelated_newer_candidates():
    views = {"a": view("a", end="202608"), "b": view("b", end="202606"), "other": view("other", end="202610")}
    source = inp([recent()])
    before = copy.deepcopy(source)
    plan = P.bundle_plan(source, views, ["a", "b"], "bundle")
    assert plan["context"]["anchor"] == "2026-06"
    assert plan["gaps"] == []
    assert [r["applied"] for r in plan["results"].values()] == [
        {"frequency": "M", "start": "202604", "end": "202606"}] * 2
    assert source == before


def test_quarter_and_half_stamps_are_not_treated_as_calendar_months():
    assert P.common_anchor([view("a", "Q", "202001", "202504"), view("b", "Q", "202001", "202503")], "Q") == "2025-09"
    assert P.table_meta(view("a", "Q", "2000 1/4", "2025 4/4"))["periods"]["Q"] == {"start": "200001", "end": "202504"}
    assert P.common_anchor([view("a", "H", "202001", "202502")], "H") == "2025-12"


def test_disjoint_members_do_not_claim_a_simultaneous_window():
    views = {"a": view("a", start="202001", end="202012"), "b": view("b", start="202301", end="202312")}
    plan = P.bundle_plan(inp(), views, list(views), "bundle")
    assert "no_common_anchor" in plan["gaps"]
    assert plan["context"]["anchor"] is None


def test_closed_edition_join_preserves_request_and_assigned_share():
    views = {"a": view("a", start="202001", end="202012"), "b": view("b", start="202101", end="202112")}
    assigned = {"a": [{"start": "2020-01", "end": "2020-12"}],
                "b": [{"start": "2021-01", "end": "2021-12"}]}
    plan = P.bundle_plan(inp([closed("2020", "2021")]), views, list(views), "bundle", assigned)
    assert plan["gaps"] == [] and plan["context"]["anchor_source"] == "table_latest"
    for t, result in plan["results"].items():
        assert result["periods"]["requested_months"] == [{"start": "2020-01", "end": "2021-12"}]
        assert result["periods"]["covered_months"] == assigned[t]


def test_outside_request_clips_and_keeps_missing_without_refresh():
    result = P.resolve(inp([closed("2019", "2020")]), view("a", start="202001", end="202012"))
    assert result["status"] == "ok" and result["periods"]["missing"]
    result = P.resolve(inp([closed("2018", "2019")]), view("a", start="202001", end="202012"))
    assert result["status"] == "no_data_in_range"


def test_hint_mismatch_is_not_explicit_frequency_conflict():
    v = view("a", "Y", "2000", "2025")
    hint = [{"kind": "frequency", "value": "M", "from_condition_id": "p1", "rule": "period_unit"}]
    result = P.resolve(inp([recent()], hint), v)
    assert result["status"] != "table_unsuitable" and result["frequency"] == "Y"
    explicit = P.resolve(inp([{"id": "f1", "kind": "frequency", "value": "M", "attrs": {}}]), v)
    assert explicit["status"] == "table_unsuitable"


def test_no_bridge_over_assignment_gap():
    assert P.continuous_assignment([(2024 * 12, 2024 * 12 + 2), (2024 * 12 + 3, 2024 * 12 + 5)]) == [
        {"start": "2024-01", "end": "2024-06"}]
    with pytest.raises(ValueError, match="continuous"):
        P.continuous_assignment([(2024 * 12, 2024 * 12 + 2), (2024 * 12 + 9, 2024 * 12 + 11)])


def test_multiple_explicit_frequencies_are_unresolved_not_silently_reduced():
    result = P.resolve(inp([{"id": "f1", "kind": "frequency", "value": "M", "attrs": {}},
                            {"id": "f2", "kind": "frequency", "value": "Q", "attrs": {}}]), view("a"))
    assert result["status"] == "unresolved" and result["issue"]["detail"] == "several_frequencies"
