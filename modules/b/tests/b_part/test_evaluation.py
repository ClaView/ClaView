"""Tests for claview_b.evaluation's `b_success` metric (added 2026-10-02).

Before this, `evaluate()`'s only pass/fail signal for B
(`b_conditions_exact`) only ever counted `status == "ok"`, so the 8 cases
that correctly stop at `status == "needs_confirmation"` (the team's 2026-10
confirmation-flow policy -- see README_B_검증.md) were counted as
"failures" even though B did exactly what it was supposed to do. These
tests pin the new, additive `b_success` field (and its `integrated_b_*`
counterpart) against the real golden set and a few synthetic outcome
shapes, so a future change that quietly reverts to the old all-or-nothing
accounting gets caught here.
"""

from __future__ import annotations

from claview_b.evaluation import b_success, evaluate


def _selection(item_id: str, period: dict) -> dict:
    return {
        "org_id": "301",
        "table_id": "DT_TEST001",
        "item_id": item_id,
        "dimensions": {},
        "unit": "%",
        "period": period,
    }


def _expected_b(*periods: dict) -> dict:
    return {"selections": [_selection("ITEM1", p) for p in periods]}


def test_b_success_true_for_exact_ok_match():
    period = {"frequency": "Y", "start": "2020", "end": "2024"}
    expected = _expected_b(period)
    b = {"status": "ok", "result": {"selections": [_selection("ITEM1", period)]}}
    assert b_success(b, expected) is True


def test_b_success_false_for_ok_with_wrong_selections():
    expected = _expected_b({"frequency": "Y", "start": "2020", "end": "2024"})
    b = {
        "status": "ok",
        "result": {
            "selections": [_selection("OTHER", {"frequency": "Y", "start": "2020", "end": "2024"})]
        },
    }
    assert b_success(b, expected) is False


def test_b_success_true_for_needs_confirmation_with_matching_resolved_period():
    period = {"frequency": "M", "start": "202108", "end": "202607"}
    expected = _expected_b(period)
    b = {
        "status": "needs_confirmation",
        "result": {"resolved_period": period, "message": "..."},
    }
    assert b_success(b, expected) is True


def test_b_success_false_for_needs_confirmation_with_wrong_resolved_period():
    expected = _expected_b({"frequency": "M", "start": "202108", "end": "202607"})
    b = {
        "status": "needs_confirmation",
        "result": {"resolved_period": {"frequency": "M", "start": "202101", "end": "202607"}},
    }
    assert b_success(b, expected) is False


def test_b_success_false_for_unresolved_or_error():
    expected = _expected_b({"frequency": "Y", "start": "2020", "end": "2024"})
    assert b_success({"status": "unresolved", "result": None}, expected) is False
    assert b_success({"status": "error", "result": None}, expected) is False


def test_b_success_false_when_expected_has_mixed_periods():
    """A case whose golden selections span more than one distinct period
    has no single "the" expected period to compare resolved_period
    against -- b_success must not guess, it must say False rather than
    silently picking one."""
    expected = _expected_b(
        {"frequency": "Y", "start": "2020", "end": "2024"},
        {"frequency": "Y", "start": "2019", "end": "2023"},
    )
    b = {
        "status": "needs_confirmation",
        "result": {"resolved_period": {"frequency": "Y", "start": "2020", "end": "2024"}},
    }
    assert b_success(b, expected) is False


def test_golden_set_b_success_count_is_22_of_22(golden_cases, store, search, config):
    """All 22 golden cases now count as B successes under the new metric:
    14 reproduce the golden selections exactly (status=ok), and the other 8
    correctly resolve their relative-period wording and stop to confirm
    (status=needs_confirmation) -- both are success, not just the first
    kind. This is the concrete number the 2026-10-01 README flagged as
    "undercounted" (14/22) before this metric existed."""
    result = evaluate(golden_cases, store, search, config, mode="replay")
    assert result["summary"]["b_success_count"] == 22
    for row in result["cases"]:
        assert row["b_success"], (row["case_id"], row["b_independent_status"])
