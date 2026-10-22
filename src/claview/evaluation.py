"""Evaluation owns expected answers; runtime services never load the gold file."""

from __future__ import annotations

import json
from collections import Counter

from .contracts import Config, Json, Trace
from .graphs import build_b_graph, invoke_pipeline
from .models import ScriptedModel
from .services import Services
from .stages import selection_key


def table_keys(result: Json) -> set:
    return {(t["org_id"], t["table_id"]) for t in result["tables"]}


def condition_counts(result: Json) -> Counter:
    return Counter(selection_key(s) for s in result["selections"])


def _expected_periods(expected_b: Json) -> set[tuple]:
    return {tuple(sorted(s["period"].items())) for s in expected_b["selections"]}


def b_success(b: Json, expected_b: Json) -> bool:
    """Whether B did the right thing for this query, by the team's 2026-10
    policy (see "ClaView B파트 실패 10건 분석" / README_B_검증.md): it
    reproduced the exact golden selections (status=ok; since the 2026-10-06
    flow change a relative-period query that lags the table's data also lands
    here, with a period notice attached), or -- kept for older stored results --
    it resolved a relative-period query to the golden answer's own period and
    stopped at status=needs_confirmation. Both count as success.
    `b_conditions_exact` below is left unchanged
    (it still means "status=ok and selections match exactly") so existing
    callers keep their meaning; this is a separate, additive metric."""
    if b["status"] == "ok":
        return condition_counts(b["result"]) == condition_counts(expected_b)
    if b["status"] == "needs_confirmation":
        resolved = (b["result"] or {}).get("resolved_period")
        if resolved is None:
            return False
        expected = _expected_periods(expected_b)
        return len(expected) == 1 and tuple(sorted(resolved.items())) in expected
    return False


def scripted_answers(case: Json) -> dict:
    expected = case["expected"]
    responses = {
        "A": [
            {
                "selected_doc_ids": [
                    f"{t['org_id']}:{t['table_id']}" for t in expected["A"]["tables"]
                ]
            }
        ]
    }
    for table in expected["A"]["tables"]:
        rows = [
            {
                "item_id": s["item_id"],
                "dimensions": {a: v["code"] for a, v in s["dimensions"].items()},
                "period": s["period"],
                "expand": {a: "none" for a in s["dimensions"]},
            }
            for s in expected["B"]["selections"]
            if (s["org_id"], s["table_id"]) == (table["org_id"], table["table_id"])
        ]
        responses[f"B:{table['org_id']}:{table['table_id']}"] = [{"selections": rows}]
    return responses


def evaluate(
    cases: list[Json],
    store,
    search,
    config: Config,
    model=None,
    mode: str = "retrieval",
    trace_dir=None,
) -> Json:
    if mode not in {"retrieval", "replay", "live"}:
        raise ValueError(mode)
    if mode == "live" and model is None:
        raise ValueError("Live mode needs a model")
    results = []
    for case in cases:
        expected = case["expected"]
        query = case["query"]
        baseline = search.retrieve(query, config.top_k)
        baseline_ids = [r["doc_id"] for r in baseline]
        candidates = (
            baseline
            if config.candidate_policy == "legacy"
            else search.retrieve_pool(
                query, [], config.channel_k, config.candidate_pool_limit, config.expansion_limit
            )
        )
        wanted = {f"{t['org_id']}:{t['table_id']}" for t in expected["A"]["tables"]}
        ids = [r["doc_id"] for r in candidates]
        row = {
            "case_id": case["case_id"],
            "preflight_scope": "global",
            "candidate_ids": ids,
            "baseline_top_10_ids": baseline_ids,
            "recall_at_5": len(wanted & set(baseline_ids[:5])) / len(wanted),
            "recall_at_10": len(wanted & set(baseline_ids[:10])) / len(wanted),
            "candidate_inclusion": len(wanted & set(ids)) / len(wanted),
            "candidate_count": len(ids),
            "retrieval": search.last_retrieval,
        }
        if mode != "retrieval":
            trace = Trace()
            provider = ScriptedModel(scripted_answers(case)) if mode == "replay" else model
            services = Services(store, search, provider, config, trace)
            trace.add("evaluation_segment", mode=mode, stage="B", input_a="golden")
            # B independent evaluation deliberately receives expected A, never expected B.
            b = build_b_graph(services).invoke(
                {"query": query, "a_result": expected["A"]},
                {"recursion_limit": 10 + 4 * len(expected["A"]["tables"])},
            )["stage_outcome"]
            if mode == "replay":
                services.model = ScriptedModel(scripted_answers(case))
            trace.add("evaluation_segment", mode=mode, stage="integrated", input_a="predicted")
            combined = invoke_pipeline(query, services, through="B", selection_mode="legacy")
            a = combined["stage_outcomes"]["A"]
            actual_b = combined["stage_outcomes"].get("B")
            searched = next((e for e in trace.events if e["event"] == "a_search"), None)
            merged = next((e for e in trace.events if e["event"] == "a_candidates"), None)
            reviewed = next((e for e in trace.events if e["event"] == "a_candidate_review"), None)
            model_ids = {
                c["doc_id"]
                for c in (
                    merged["merged"] if merged else searched["candidates"] if searched else []
                )
            }
            if reviewed:
                model_ids = set(reviewed["after_ids"])
            row.update(
                {
                    "a_status": a["status"],
                    "a_format_valid": any(e["event"] == "a_format_checked" for e in trace.events),
                    "a_candidate_inclusion": len(wanted & model_ids) / len(wanted),
                    "a_exact": a["status"] == "ok"
                    and table_keys(a["result"]) == table_keys(expected["A"]),
                    "b_independent_status": b["status"],
                    "b_success": b_success(b, expected["B"]),
                    "integrated_b_success": bool(actual_b) and b_success(actual_b, expected["B"]),
                    "b_issues": b["issues"],
                    "b_conditions_exact": b["status"] == "ok"
                    and condition_counts(b["result"]) == condition_counts(expected["B"]),
                    "b_unit_exact": b["status"] == "ok"
                    and Counter((selection_key(s), s["unit"]) for s in b["result"]["selections"])
                    == Counter((selection_key(s), s["unit"]) for s in expected["B"]["selections"]),
                    "integrated_b_status": actual_b["status"] if actual_b else "not_run",
                    "integrated_b_exact": bool(
                        actual_b
                        and actual_b["status"] == "ok"
                        and condition_counts(actual_b["result"]) == condition_counts(expected["B"])
                    ),
                    "trace_ref": trace.run_id,
                }
            )
            if trace_dir:
                trace.save(trace_dir)
        results.append(row)
    summary = {
        "cases": len(cases),
        "retrieval_recall_at_5": sum(x["recall_at_5"] for x in results) / len(cases),
        "retrieval_recall_at_10": sum(x["recall_at_10"] for x in results) / len(cases),
        "candidate_inclusion": sum(x["candidate_inclusion"] for x in results) / len(cases),
        "candidate_count_min": min(x["candidate_count"] for x in results),
        "candidate_count_max": max(x["candidate_count"] for x in results),
    }
    if mode != "retrieval":
        summary["a_candidate_inclusion"] = sum(x["a_candidate_inclusion"] for x in results) / len(
            cases
        )
        for key in (
            "a_format_valid",
            "a_exact",
            "b_conditions_exact",
            "b_unit_exact",
            "b_success",
            "integrated_b_success",
            "integrated_b_exact",
        ):
            summary[key + "_count"] = sum(x[key] for x in results)
    return {
        "mode": mode,
        "scope": "synthetic answer replay; NOT model accuracy"
        if mode == "replay"
        else "retrieval only; no table-selection model"
        if mode == "retrieval"
        else "live model development evaluation",
        "metadata_snapshot": store.snapshot,
        "catalog_snapshot": store.catalog_snapshot,
        "config": vars(config),
        "search_configuration": search.describe(),
        "summary": summary,
        "cases": results,
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
