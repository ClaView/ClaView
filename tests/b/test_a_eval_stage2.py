"""Hand-calculated metrics, denominator integrity, isolation and frozen split checks."""

from __future__ import annotations

import copy
import hashlib
import math
import sys
from pathlib import Path

import pytest
from jsonschema import ValidationError

from claview.a_discovery import TableDiscovery
from claview.a_eval_contracts import (
    VERSION,
    digest,
    inputs_from_gold,
    read_json,
    validate,
    validate_gold,
    validate_splits,
)
from claview.a_eval_predict import main as predict_main
from claview.a_eval_predict import predict_case
from claview.a_eval_score import retrieval_metrics, score
from claview.candidate_store import CandidateStore
from claview.contracts import StageFailure
from claview.metadata import MetadataStore
from claview.search import SearchIndex
from claview.search_documents import build_search_documents

ROOT = Path(__file__).resolve().parents[2]
A, B, C, X = "301:A", "301:B", "301:C", "301:X"


def case(key="q1", groups=None, decision="display_candidates", status="draft"):
    groups = [[A]] if groups is None else groups
    kind = (
        "none"
        if not groups
        else "multiple_required"
        if len(groups) > 1
        else "equivalent_alternatives"
        if len(groups[0]) > 1
        else "single"
    )
    return {
        "case_id": key,
        "query": "question " + key,
        "query_revision": 1,
        "prior_turns": [],
        "tags": ["manual"],
        "families": ["family"],
        "variant_group": key,
        "semantic_group": key,
        "review_status": status,
        "reviewer": "reviewer-id" if status == "human_reviewed" else None,
        "review_note": "synthetic metric test",
        "decision": decision,
        "target_kind": kind,
        "table_groups": groups,
        "clarification_axes": ["basis"] if decision == "ask_clarification" else [],
        "evidence": [
            {
                "source": "synthetic",
                "table_id": None,
                "field": "fixture",
                "quote": "x",
                "snapshot_sha256": "0" * 64,
            }
        ],
        "rationale": "synthetic metric test",
    }


def gold(cases):
    result = {
        "schema_version": VERSION,
        "dataset_id": "synthetic",
        "split": "dev",
        "source_sha256": {},
        "cases": cases,
    }
    result["inputs_sha256"] = digest(inputs_from_gold(result))
    return result


def pred(c, order=None, decision="display_candidates"):
    order = [A] if order is None else order
    return {
        **{key: c[key] for key in ("case_id", "query", "query_revision", "prior_turns")},
        "decision": decision,
        "clarification_axes": ["basis"] if decision == "ask_clarification" else [],
        "before_ids": order.copy(),
        "after_ids": order.copy(),
        "display_order": order.copy(),
        "error_code": None,
        "elapsed_ms": 1,
        "usage": {
            "model_calls": 0,
            "external_api_calls": 0,
            "b_calls": 0,
            "blocked_network_attempts": 0,
            "billed_cost_usd": 0,
        },
        "trace": {},
    }


def batch(g, rows):
    return {
        "schema_version": VERSION,
        "dataset_id": g["dataset_id"],
        "split": g["split"],
        "inputs_sha256": g["inputs_sha256"],
        "run": {"official_ids": [A, B, C, X]},
        "cases": rows,
    }


def test_partial_multi_required_and_discounted_gain_by_hand():
    metrics = retrieval_metrics([X, A, X, B], [[A], [B]], 3)
    assert metrics["recall"] == 0.5 and not metrics["all_required"]
    assert metrics["mrr"] == 0.5 and not metrics["top1"]
    assert metrics["ndcg"] == pytest.approx((1 / math.log2(3)) / (1 + 1 / math.log2(3)))
    assert retrieval_metrics([A, B], [[A], [B]], 5)["ndcg"] == 1


def test_alternatives_give_one_gain_and_do_not_hide_other_requirements():
    one = retrieval_metrics([A, B], [[A, B]], 3)
    assert one["recall"] == one["ndcg"] == 1
    partial = retrieval_metrics([A, B], [[A, B], [C]], 3)
    assert partial["recall"] == 0.5 and not partial["all_required"]
    full = retrieval_metrics([B, A, C], [[A, B], [C]], 3)
    assert full["ndcg"] == pytest.approx(1.5 / (1 + 1 / math.log2(3)))


def test_macro_recall_differs_from_all_required_and_micro():
    c1, c2 = case(groups=[[A], [B]]), case("q2")
    g = gold([c1, c2])
    result = score(g, batch(g, [pred(c1), pred(c2)]))["by_review_status"]["draft"]
    assert result["retrieval"]["macro_recall_at"]["3"] == 0.75
    assert result["retrieval"]["all_required_at"]["3"] == 0.5
    assert result["retrieval_denominator"] == 2


def test_missing_error_and_success_all_stay_in_denominator():
    cases = [case("q1"), case("q2"), case("q3")]
    g = gold(cases)
    failed = pred(cases[1], [A], "technical_failure")
    failed["error_code"] = "test_error"
    result = score(g, batch(g, [failed, pred(cases[0])]))
    summary = result["by_review_status"]["draft"]
    assert result["received_predictions"] == 2 and len(result["cases"]) == 3
    assert summary["retrieval"]["macro_recall_at"]["5"] == pytest.approx(1 / 3)
    assert summary["decision"]["technical_failure_rate"]["value"] == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    "mutation", ["duplicate_id", "outside_catalog", "query", "revision", "shape"]
)
def test_bad_responses_are_failures_not_excluded(mutation):
    c = case()
    g, p = gold([c]), pred(c)
    if mutation == "duplicate_id":
        p["display_order"] = [A, A]
    elif mutation == "outside_catalog":
        p["before_ids"] = p["after_ids"] = p["display_order"] = ["301:FAKE"]
    elif mutation == "query":
        p["query"] = "wrong question"
    elif mutation == "revision":
        p["query_revision"] = 99
    else:
        del p["usage"]
    result = score(g, batch(g, [p]))
    assert result["cases"][0]["error_code"] == "invalid_prediction"
    assert result["by_review_status"]["draft"]["retrieval"]["mrr"] == 0


def test_wrong_filter_distinguishes_removed_alternative_from_lost_requirement():
    c = case(groups=[[A, B], [C]])
    g, p = gold([c]), pred(c, [A, B, C])
    p["after_ids"] = p["display_order"] = [B]
    r = score(g, batch(g, [p]))["cases"][0]["retrieval"]
    assert r["removed_acceptable_ids"] == [A, C]
    assert r["lost_required_groups"] == [1]


def test_decision_metrics_and_axis_adequacy_have_explicit_denominators():
    cases = [
        case("q1", [], "ask_clarification"),
        case("q2", [], "ask_clarification"),
        case("q3"),
        case("q4", [], "out_of_scope"),
        case("q5", [], "insufficient_evidence"),
    ]
    g = gold(cases)
    predictions = [
        pred(cases[0], [], "ask_clarification"),
        pred(cases[1]),
        pred(cases[2], [], "ask_clarification"),
        pred(cases[3]),
        pred(cases[4], [], "insufficient_evidence"),
    ]
    predictions[0]["clarification_axes"] = ["wrong_axis"]
    d = score(g, batch(g, predictions))["by_review_status"]["draft"]["decision"]
    assert d["clarify_precision"]["value"] == d["clarify_recall"]["value"] == 0.5
    assert d["clarify_axis_recall"]["value"] == 0
    assert d["unnecessary_clarification"]["value"] == pytest.approx(1 / 3)
    assert d["out_of_scope_false_recommendation"]["value"] == 1
    assert d["insufficient_evidence_rate"]["value"] == 0.2


def test_empty_metric_denominator_is_null_and_review_groups_are_separate():
    cases = [case("q1", status="inherited_regression"), case("q2")]
    g = gold(cases)
    result = score(g, batch(g, [pred(cases[0]), pred(cases[1], [X])]))
    assert result["by_review_status"]["inherited_regression"]["retrieval"]["mrr"] == 1
    assert result["by_review_status"]["draft"]["retrieval"]["mrr"] == 0
    assert result["by_review_status"]["draft"]["decision"]["clarify_precision"]["value"] is None
    assert result["user_selection"]["actual_human"]["accuracy"] is None


def test_duplicate_unknown_case_and_dataset_mismatch_fail_closed():
    c = case()
    g = gold([c])
    for rows in ([pred(c), pred(c)], [pred(case("unknown"))]):
        with pytest.raises(ValueError, match="duplicate_or_unknown"):
            score(g, batch(g, rows))
    wrong = batch(g, [pred(c)])
    wrong["inputs_sha256"] = "1" * 64
    with pytest.raises(ValueError, match="dataset_mismatch"):
        score(g, wrong)
    g["source_sha256"]["catalog"] = "2" * 64
    with pytest.raises(ValueError, match="source_snapshot_mismatch"):
        score(g, batch(g, [pred(c)]))


def test_input_contract_rejects_gold_and_projection_has_no_b():
    g = gold([case()])
    inputs = inputs_from_gold(g)
    validate(inputs, "inputs")
    contaminated = copy.deepcopy(inputs)
    contaminated["cases"][0]["table_groups"] = [[A]]
    with pytest.raises(ValidationError):
        validate(contaminated, "inputs")
    with pytest.raises(ValidationError):
        validate(g, "inputs")


@pytest.mark.parametrize("problem", ["overlap", "no_reviewer", "decision", "axis"])
def test_gold_semantic_structure_is_validated(problem):
    c = case()
    if problem == "overlap":
        c.update(table_groups=[[A], [A, B]], target_kind="multiple_required")
    elif problem == "no_reviewer":
        c["review_status"] = "human_reviewed"
    elif problem == "decision":
        c["decision"] = "out_of_scope"
    else:
        c["clarification_axes"] = ["irrelevant"]
    with pytest.raises(ValueError):
        validate_gold(gold([c]))


def test_split_validation_prevents_paraphrase_variant_and_family_leakage():
    dev = gold([case()])
    test = gold([case("other", [[B]])])
    test["split"] = "test_family"
    test["inputs_sha256"] = digest(inputs_from_gold(test))
    with pytest.raises(ValueError, match="family"):
        validate_splits([dev, test])
    test["cases"][0]["families"] = ["new-family"]
    test["cases"][0]["semantic_group"] = "q1"
    with pytest.raises(ValueError, match="semantic_group"):
        validate_splits([dev, test])
    test["cases"][0]["semantic_group"] = "other"
    validate_splits([dev, test])


@pytest.fixture
def discovery(tmp_path):
    store = MetadataStore(
        ROOT / "tests/b/fixtures/catalog.json", ROOT / "tests/b/fixtures/metadata.jsonl"
    )
    return TableDiscovery(
        SearchIndex(store, corpus=build_search_documents(store)),
        CandidateStore(tmp_path / "a-eval.sqlite3"),
    )


def test_runner_uses_only_question_waits_and_never_confirms(discovery, monkeypatch):
    c = {
        "case_id": "secret_label_id",
        "query": "2024년 원화 수입물가 원재료 중간재",
        "query_revision": 1,
        "prior_turns": [],
    }
    observed = []
    original = discovery.search_tables

    def search(query, context):
        observed.append((query, context))
        return original(query, context)

    def forbid(*args, **kwargs):
        pytest.fail("Must not confirm during prediction")

    monkeypatch.setattr(discovery, "search_tables", search)
    monkeypatch.setattr(discovery, "confirm_tables", forbid)
    row = predict_case(c, discovery)
    assert row["decision"] == "display_candidates"
    assert observed[0][0] == c["query"]
    assert c["case_id"] not in str(observed)
    assert row["usage"]["b_calls"] == row["usage"]["model_calls"] == 0


@pytest.mark.parametrize(
    "code,decision",
    [
        ("retrieval_empty", "insufficient_evidence"),
        ("no_compatible_candidates", "insufficient_evidence"),
        ("storage_failure", "technical_failure"),
    ],
)
def test_runner_failure_decisions_do_not_invent_out_of_scope(
    discovery, monkeypatch, code, decision
):
    def fail(*args, **kwargs):
        raise StageFailure(code)

    monkeypatch.setattr(discovery, "search_tables", fail)
    c = inputs_from_gold(gold([case()]))["cases"][0]
    row = predict_case(c, discovery)
    assert row["decision"] == decision and row["error_code"] == code


def test_runner_followup_is_explicitly_unsupported(discovery, monkeypatch):
    c = inputs_from_gold(gold([case()]))["cases"][0]
    c["prior_turns"] = [{"role": "user", "text": "previous question"}]
    monkeypatch.setattr(discovery, "search_tables", lambda *args: pytest.fail("Lost context"))
    row = predict_case(c, discovery)
    assert row["decision"] == "technical_failure"
    assert row["error_code"] == "context_not_supported_by_baseline"


def test_runner_preserves_all_filtered_evidence(discovery, monkeypatch):
    key = next(iter(discovery.search.store.catalog))

    def all_filtered(query, context):
        discovery.save_candidates(
            query,
            context,
            [
                {
                    "doc_id": key,
                    "conflicts": [{"state": "conflict"}],
                    "unknowns": [],
                    "rank_signals": {"score": 1},
                }
            ],
            {},
        )

    monkeypatch.setattr(discovery, "search_tables", all_filtered)
    c = inputs_from_gold(gold([case()]))["cases"][0]
    row = predict_case(c, discovery)
    assert row["decision"] == "insufficient_evidence"
    assert row["before_ids"] == [key] and row["after_ids"] == []
    assert row["trace"]["assessments"][key]["conflicts"]


def test_runner_sanitizes_unexpected_exceptions(discovery, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("fake_sensitive_payload")

    monkeypatch.setattr(discovery, "search_tables", fail)
    c = inputs_from_gold(gold([case()]))["cases"][0]
    row = predict_case(c, discovery)
    assert row["decision"] == "technical_failure"
    assert row["trace"]["exception_type"] == "RuntimeError"
    assert "fake_sensitive_payload" not in str(row)


def test_locked_test_cli_refuses_before_creating_results(tmp_path, monkeypatch):
    from claview.a_eval_contracts import write_new

    inputs = inputs_from_gold(gold([case()]))
    inputs["split"] = "test_family"
    path = tmp_path / "inputs.json"
    destination = tmp_path / "never-created"
    write_new(path, inputs)
    monkeypatch.setattr(
        sys, "argv", ["a_eval_predict", "--inputs", str(path), "--output-dir", str(destination)]
    )
    with pytest.raises(SystemExit) as exc:
        predict_main()
    assert exc.value.code == 2 and not destination.exists()


def test_frozen_materials_preserve_original_projection_and_split_hashes():
    folder = ROOT / "eval/cases/a_stage2"
    manifest = read_json(folder / "split_manifest.json")
    datasets = [read_json(folder / f"{split}.gold.json") for split in manifest["splits"]]
    validate_splits(datasets)
    original_path = ROOT / "eval/cases/golden_set_v1.json"
    # The A-stage materials are frozen from the 2026-10-06 golden_set_v1.json
    # (sha256 below). The only later change is B's team decision of 2026-10-06
    # (CV-AB-12 expected period start 202101 -> 202103; A-stage fields are
    # untouched), so the frozen hash must be reproduced once those later values are
    # reverted -- any other edit to the file still fails this check.
    raw = original_path.read_bytes()
    assert raw.count(b'"start": "202103"') == 1
    restored = raw.replace(b'"start": "202103"', b'"start": "202101"')
    # Second later change (B team decision of 2026-10-13): CV-AB-20 expects the default
    # annual answer (Y 2023~2024) instead of monthly (M 202301~202412); revert it likewise.
    lo, hi = restored.index(b'"case_id": "CV-AB-20"'), restored.index(b'"case_id": "CV-AB-21"')
    block = restored[lo:hi]
    assert block.count(b'"frequency": "Y"') == 2
    block = block.replace(b'"frequency": "Y"', b'"frequency": "M"')
    block = block.replace(b'"start": "2023"', b'"start": "202301"')
    block = block.replace(b'"end": "2024"', b'"end": "202412"')
    restored = restored[:lo] + block + restored[hi:]
    assert (
        hashlib.sha256(restored).hexdigest()
        == "2e27b49701636e1f51739d51bd7ccaef4c23d43c7c1ae66960e8bd671e80e5c5"
    )
    regression = next(d for d in datasets if d["split"] == "regression")
    for old, new in zip(read_json(original_path)["cases"], regression["cases"], strict=True):
        assert old["query"] == new["query"] and old["case_id"] == new["case_id"]
        assert new["table_groups"] == [
            [f"{t['org_id']}:{t['table_id']}"] for t in old["expected"]["A"]["tables"]
        ]
        assert new["review_status"] == "inherited_regression"
    for dataset in datasets:
        split = dataset["split"]
        assert digest(dataset) == manifest["splits"][split]["gold_sha256"]
        assert digest(read_json(folder / f"{split}.inputs.json")) == dataset["inputs_sha256"]
        if split != "regression":
            assert all(
                c["review_status"] == "draft" and c["reviewer"] is None for c in dataset["cases"]
            )
