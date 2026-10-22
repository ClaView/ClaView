"""Evaluation denominators distinguish abstention, completed errors and actual adoption."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "stage6_evaluation", Path(__file__).parents[2] / "scripts/evaluate_a_stage6.py"
)
stage6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage6)


def case(case_id):
    return {
        "case_id": case_id,
        "expected": {"A": {"tables": [{"org_id": "1", "table_id": "expected"}]}},
    }


def prediction(case_id, ids=(), origin=None):
    return {
        "case_id": case_id,
        "confirmed": bool(ids),
        "selection_origin": origin,
        "selected_ids": list(ids),
        "route": "auto_adopt" if ids else "user_confirmation",
        "reason": "verified" if ids else "semantic_ambiguity",
        "original_query_preserved": True,
        "a_tables_contract_valid": True if ids else None,
    }


def test_pending_answers_are_not_exact_or_missing_table_scores():
    rows, summary = stage6.score_selection([prediction("a")], [case("a")])
    assert rows[0]["final_exact"] is None
    assert rows[0]["missing_required_ids"] is None
    assert rows[0]["unnecessary_ids"] is None
    assert summary["automatic_coverage"] == 0
    assert summary["automatic_accuracy"] is None
    assert summary["completed_exact_rate"] is None
    assert summary["a_tables_contract_valid"] is None
    assert summary["unverified"] == 1


def test_coverage_uses_all_cases_accuracy_uses_only_actual_automatic_adoptions():
    predictions = [
        prediction("a", ["1:expected"], "automatic"),
        prediction("b", ["1:wrong"], "automatic"),
        prediction("c", ["1:expected", "1:extra"], "user"),
        prediction("d"),
    ]
    rows, summary = stage6.score_selection(predictions, [case(c) for c in "abcd"])
    assert summary["automatic_coverage"] == 0.5
    assert summary["automatic_accuracy"] == 0.5
    assert summary["completed_exact_rate"] == 1 / 3
    assert summary["unnecessary_tables_in_completed"] == 2
    assert summary["missing_tables_in_completed"] == 1
    assert rows[2]["final_exact"] is False
    assert rows[3]["evaluation_state"] == "unverified"


def test_shadow_proposal_does_not_count_as_final_selection():
    p = prediction("a")
    p["decision"] = {"auto_eligible": True, "proposed_ids": ["1:expected"]}
    _, summary = stage6.score_selection([p], [case("a")])
    assert summary["automatic_count"] == 0
    assert summary["completed"] == 0
