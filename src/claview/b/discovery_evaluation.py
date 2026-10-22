"""Offline development retrieval metrics; answers are read only after prediction."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from statistics import mean

from .contracts import StageFailure, fingerprint


def evaluate_search(cases, discovery):
    rows = []
    sources = {p.name: fingerprint(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    started = time.perf_counter()
    for case in cases:
        context = {
            "session_id": uuid.uuid4().hex,
            "request_id": uuid.uuid4().hex,
            "query_revision": 1,
        }
        began = time.perf_counter()
        try:
            response = discovery.search_tables(case["query"], context)
            set_id = response.get("candidate_set_id") or response["interaction"]["candidate_set_id"]
            bundle = discovery.repository.get(set_id, context)
            pool = bundle["candidate_pool"]
            before = [c["doc_id"] for c in pool]
            after = [c["doc_id"] for c in pool if not c["conflicts"]]
            order = bundle["display_order"]
            diagnostics = bundle["diagnostics"]
            failure = None
        except StageFailure as exc:
            pool, before, after, order, diagnostics, failure = [], [], [], [], {}, exc.code
            set_id = None
        rows.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "candidate_set_id": set_id,
                "context": context,
                "before_ids": before,
                "after_ids": after,
                "display_order": order,
                "assessments": {
                    c["doc_id"]: {
                        "conflicts": c["conflicts"],
                        "unknowns": c["unknowns"],
                        "rank_signals": c["rank_signals"],
                    }
                    for c in pool
                },
                "diagnostics": diagnostics,
                "failure": failure,
                "elapsed_ms": round((time.perf_counter() - began) * 1000, 3),
            }
        )
    # The production functions above never receive the expected payload or case ID.
    for row, case in zip(rows, cases):
        gold = {f"{t['org_id']}:{t['table_id']}" for t in case["expected"]["A"]["tables"]}

        def first_rank(order):
            return next((i for i, key in enumerate(order, 1) if key in gold), None)

        row["expected_ids"] = sorted(gold)
        row["before_inclusion"] = gold <= set(row["before_ids"])
        row["after_inclusion"] = gold <= set(row["after_ids"])
        row["wrongly_filtered"] = sorted((gold & set(row["before_ids"])) - set(row["after_ids"]))
        row["first_correct_rank"] = first_rank(row["display_order"])
        row["baseline_rank"] = first_rank(row["before_ids"])
        row["filtered_pool_rank"] = first_rank(row["after_ids"])
        row["recall"] = {
            str(k): len(gold & set(row["display_order"][:k])) / len(gold) for k in (3, 5, 10)
        }
        row["channels"] = {}
        for run in row["diagnostics"].get("runs", []):
            for name, candidates in run["channels"].items():
                ids = [c["doc_id"] for c in candidates]
                row["channels"][name] = {
                    "ids": ids,
                    "first_correct_rank": first_rank(ids),
                    "inclusion": gold <= set(ids),
                }

    def mrr(field):
        return mean(1 / r[field] if r[field] else 0 for r in rows) if rows else 0

    summary = {
        "cases": len(rows),
        "before_inclusion_count": sum(r["before_inclusion"] for r in rows),
        "after_inclusion_count": sum(r["after_inclusion"] for r in rows),
        "before_total": sum(len(r["before_ids"]) for r in rows),
        "after_total": sum(len(r["after_ids"]) for r in rows),
        "recall_at": {
            str(k): mean(r["recall"][str(k)] for r in rows) if rows else 0 for k in (3, 5, 10)
        },
        "mrr": mrr("first_correct_rank"),
        "baseline_mrr": mrr("baseline_rank"),
        "filtered_pool_mrr": mrr("filtered_pool_rank"),
        "baseline_recall_at": {
            str(k): mean(set(r["expected_ids"]) <= set(r["before_ids"][:k]) for r in rows)
            if rows
            else 0
            for k in (3, 5, 10)
        },
        "filtered_pool_recall_at": {
            str(k): mean(set(r["expected_ids"]) <= set(r["after_ids"][:k]) for r in rows)
            if rows
            else 0
            for k in (3, 5, 10)
        },
        "wrongly_filtered_cases": [r["case_id"] for r in rows if r["wrongly_filtered"]],
        "candidate_min": min((len(r["after_ids"]) for r in rows), default=0),
        "candidate_max": max((len(r["after_ids"]) for r in rows), default=0),
        "mean_query_ms": mean(r["elapsed_ms"] for r in rows) if rows else 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    return {
        "mode": "offline_user_selection_retrieval",
        "complete": len(rows) == len(cases),
        "limitations": (
            "Development set; all 22 answers have one table. "
            "No user-selection accuracy or B model/API performance measured."
        ),
        "snapshots": discovery.snapshots,
        "source_sha256": sources,
        "source_unchanged": sources
        == {p.name: fingerprint(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "summary": summary,
        "cases": rows,
    }
