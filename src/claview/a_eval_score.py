"""Gold-only scorer for saved A predictions, independent of search execution."""

from __future__ import annotations

import argparse
import math
from collections import Counter
from statistics import mean

from jsonschema import ValidationError

from .a_eval_contracts import VERSION, digest, read_json, validate, validate_gold, write_new

KS = (3, 5, 10)


def fraction(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def retrieval_metrics(order, groups, k=None):
    """One binary gain per requirement, regardless of how many alternatives appear."""
    order = order if k is None else order[:k]
    positions = [
        next((i for i, key in enumerate(order, 1) if key in group), None) for group in groups
    ]
    ranks = [rank for rank in positions if rank is not None]
    count = len(groups)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(count, k or count) + 1))
    return {
        "recall": len(ranks) / count,
        "all_required": len(ranks) == count,
        "any_relevant": bool(ranks),
        "top1": 1 in ranks,
        "mrr": 1 / min(ranks) if ranks else 0,
        "ndcg": sum(1 / math.log2(rank + 1) for rank in ranks) / ideal,
    }


def score_case(case, prediction, official_ids):
    error = None
    if prediction is None:
        error = "missing_prediction"
    else:
        try:
            validate(prediction, "prediction_case")
            for field in ("query", "query_revision", "prior_turns"):
                if prediction[field] != case[field]:
                    raise ValueError("prediction_query_mismatch")
            before, after, order = (
                set(prediction[key]) for key in ("before_ids", "after_ids", "display_order")
            )
            if not order <= after <= before <= official_ids:
                raise ValueError("invalid_candidate_membership")
            if prediction["decision"] == "display_candidates" and not order:
                raise ValueError("empty_display_response")
            if (prediction["decision"] == "ask_clarification") != bool(
                prediction["clarification_axes"]
            ):
                raise ValueError("invalid_clarification_response")
            if prediction["error_code"] and prediction["decision"] not in {
                "technical_failure",
                "insufficient_evidence",
            }:
                raise ValueError("error_decision_mismatch")
            if prediction["decision"] == "technical_failure":
                error = prediction["error_code"] or "technical_failure"
        except (ValidationError, ValueError):
            error = "invalid_prediction"
    decision = "technical_failure" if error else prediction["decision"]
    lists = {
        key: [] if error else prediction[key]
        for key in ("before_ids", "after_ids", "display_order")
    }
    groups = case["table_groups"]
    row = {
        "case_id": case["case_id"],
        "review_status": case["review_status"],
        "tags": case["tags"],
        "gold_decision": case["decision"],
        "predicted_decision": decision,
        "error_code": error,
        "decision_correct": decision == case["decision"],
        "clarification_axis_hit": bool(
            set(case["clarification_axes"])
            & set(prediction["clarification_axes"] if not error else [])
        ),
        "retrieval": None,
    }
    if groups:
        before = retrieval_metrics(lists["before_ids"], groups)
        after = retrieval_metrics(lists["after_ids"], groups)
        lost = [
            i
            for i, group in enumerate(groups)
            if set(group) & set(lists["before_ids"]) and not set(group) & set(lists["after_ids"])
        ]
        removed = sorted(
            {key for group in groups for key in group}
            & (set(lists["before_ids"]) - set(lists["after_ids"]))
        )
        row["retrieval"] = {
            "before": before,
            "after": after,
            "display": retrieval_metrics(lists["display_order"], groups),
            "at": {str(k): retrieval_metrics(lists["display_order"], groups, k) for k in KS},
            "lost_required_groups": lost,
            "removed_acceptable_ids": removed,
        }
    return row


def summarize(rows):
    retrieval = [row["retrieval"] for row in rows if row["retrieval"] is not None]
    n = len(rows)
    predicted_clarify = [r for r in rows if r["predicted_decision"] == "ask_clarification"]
    gold_clarify = [r for r in rows if r["gold_decision"] == "ask_clarification"]
    tp = sum(r["gold_decision"] == "ask_clarification" for r in predicted_clarify)
    axis_tp = sum(
        r["gold_decision"] == "ask_clarification" and r["clarification_axis_hit"]
        for r in predicted_clarify
    )
    outside = [r for r in rows if r["gold_decision"] == "out_of_scope"]
    supported = [
        r for r in rows if r["gold_decision"] in {"display_candidates", "ask_clarification"}
    ]
    non_clarify = [r for r in rows if r["gold_decision"] != "ask_clarification"]
    decisions = {
        "accuracy": fraction(sum(r["decision_correct"] for r in rows), n),
        "clarify_precision": fraction(tp, len(predicted_clarify)),
        "clarify_recall": fraction(tp, len(gold_clarify)),
        "clarify_axis_precision": fraction(axis_tp, len(predicted_clarify)),
        "clarify_axis_recall": fraction(axis_tp, len(gold_clarify)),
        "unnecessary_clarification": fraction(
            sum(r["predicted_decision"] == "ask_clarification" for r in non_clarify),
            len(non_clarify),
        ),
        "out_of_scope_false_recommendation": fraction(
            sum(r["predicted_decision"] == "display_candidates" for r in outside), len(outside)
        ),
        "supported_false_reject": fraction(
            sum(r["predicted_decision"] == "out_of_scope" for r in supported), len(supported)
        ),
        "insufficient_evidence_rate": fraction(
            sum(r["predicted_decision"] == "insufficient_evidence" for r in rows), n
        ),
        "technical_failure_rate": fraction(
            sum(r["predicted_decision"] == "technical_failure" for r in rows), n
        ),
        "confusion_counts": dict(
            sorted(
                Counter(f"{r['gold_decision']} -> {r['predicted_decision']}" for r in rows).items()
            )
        ),
    }

    def avg(values):
        return mean(values) if values else None

    return {
        "cases": n,
        "retrieval_denominator": len(retrieval),
        "retrieval": {
            "candidate_any_before": avg([r["before"]["any_relevant"] for r in retrieval]),
            "candidate_all_before": avg([r["before"]["all_required"] for r in retrieval]),
            "candidate_all_after": avg([r["after"]["all_required"] for r in retrieval]),
            "display_all": avg([r["display"]["all_required"] for r in retrieval]),
            "top1": avg([r["display"]["top1"] for r in retrieval]),
            "mrr": avg([r["display"]["mrr"] for r in retrieval]),
            "macro_recall_at": {
                str(k): avg([r["at"][str(k)]["recall"] for r in retrieval]) for k in KS
            },
            "all_required_at": {
                str(k): avg([r["at"][str(k)]["all_required"] for r in retrieval]) for k in KS
            },
            "ndcg_at": {str(k): avg([r["at"][str(k)]["ndcg"] for r in retrieval]) for k in KS},
            "incorrect_hard_filter_cases": sum(bool(r["lost_required_groups"]) for r in retrieval),
            "removed_acceptable_id_cases": sum(
                bool(r["removed_acceptable_ids"]) for r in retrieval
            ),
        },
        "decision": decisions,
    }


def score(gold, predictions):
    validate_gold(gold)
    if (
        predictions.get("schema_version") != VERSION
        or predictions.get("inputs_sha256") != gold["inputs_sha256"]
        or predictions.get("dataset_id") != gold["dataset_id"]
        or predictions.get("split") != gold["split"]
    ):
        raise ValueError("prediction_dataset_mismatch")
    ids = {case["case_id"] for case in gold["cases"]}
    by_id = {}
    for prediction in predictions["cases"]:
        key = prediction.get("case_id")
        if key not in ids or key in by_id:
            raise ValueError("duplicate_or_unknown_prediction_case")
        by_id[key] = prediction
    official_ids = set(predictions["run"]["official_ids"])
    for name in ("catalog", "metadata"):
        expected = gold["source_sha256"].get(name)
        if expected and predictions["run"].get("source_sha256", {}).get(name) != expected:
            raise ValueError("prediction_source_snapshot_mismatch")
    validate_gold(gold, official_ids)
    rows = [score_case(case, by_id.get(case["case_id"]), official_ids) for case in gold["cases"]]
    statuses = sorted({case["review_status"] for case in gold["cases"]})
    tags = sorted({tag for row in rows for tag in row["tags"]})
    return {
        "schema_version": VERSION,
        "dataset_id": gold["dataset_id"],
        "split": gold["split"],
        "gold_sha256": digest(gold),
        "predictions_sha256": digest(predictions),
        "run": predictions["run"],
        "all_requested_rows_accounted": len(rows) == len(gold["cases"]),
        "received_predictions": len(by_id),
        "by_review_status": {
            s: summarize([r for r in rows if r["review_status"] == s]) for s in statuses
        },
        "by_tag_and_review_status": {
            s: {
                tag: summarize([r for r in rows if tag in r["tags"] and r["review_status"] == s])
                for tag in tags
                if any(tag in r["tags"] and r["review_status"] == s for r in rows)
            }
            for s in statuses
        },
        "user_selection": {
            "actual_human": {"n": 0, "accuracy": None},
            "mock_selection": {"n": 0, "accuracy": None},
        },
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_new(args.output, score(read_json(args.gold), read_json(args.predictions)))


if __name__ == "__main__":
    main()
