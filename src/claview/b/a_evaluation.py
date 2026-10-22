"""A-only development evaluation: prediction precedes scoring, with no B/API queries."""

from __future__ import annotations

import time
from pathlib import Path

from .a_graphs import build_a_graph
from .contracts import Trace, fingerprint
from .evaluation import table_keys, write_json
from .models import ScriptedModel
from .services import Services


class CaptureModel:
    def complete(self, task, system, context, schema, max_tokens, trace):
        if task != "A":
            raise ValueError(
                "Offline candidate inspection cannot simulate query planning or routing"
            )
        trace.add("model_input_capture", task=task, context=context, schema=schema, system=system)
        return {"selected_doc_ids": []}


def evaluate_a(cases, store, search, config, mode="retrieval", model=None, output=None):
    if mode not in {"retrieval", "replay", "live"}:
        raise ValueError("Unknown evaluation mode")
    if mode == "live" and model is None:
        raise ValueError("Live mode requires a model")
    if mode != "live" and (
        config.structure_query or config.search_scope != "global" or config.hierarchy
    ):
        raise ValueError("Offline evaluation requires global search without model planning")
    result = {
        "mode": mode,
        "scope": "A only; development set; no B or numeric API",
        "complete": False,
        "config": vars(config),
        "model_configuration": {
            "min_interval": getattr(model, "min_interval", None),
            "transport_retries": getattr(model, "retries", None),
        },
        "search_configuration": search.describe(),
        "metadata_snapshot": store.snapshot,
        "catalog_snapshot": store.catalog_snapshot,
        "source_sha256": {
            p.name: fingerprint(p) for p in sorted(Path(__file__).parent.glob("*.py"))
        },
        "cases": [],
    }
    start = time.monotonic()
    for case in cases:
        trace = Trace()
        provider = model if mode == "live" else CaptureModel()
        if mode == "replay":
            provider = ScriptedModel(
                {
                    "A": [
                        {
                            "selected_doc_ids": [
                                f"{t['org_id']}:{t['table_id']}"
                                for t in case["expected"]["A"]["tables"]
                            ]
                        }
                    ]
                }
            )
        trace.add("evaluation_segment", stage="A", mode=mode, input="query_only")
        began = time.monotonic()
        state = build_a_graph(Services(store, search, provider, config, trace)).invoke(
            {"query": case["query"]}, {"recursion_limit": 20 + config.max_route_rounds * 3}
        )
        trace_path = trace.save(output.parent / "traces") if output else None
        request = next((e for e in trace.events if e["event"] == "a_model_input"), {})
        row = {
            "case_id": case["case_id"],
            "query": case["query"],
            "before_ids": [r["doc_id"] for r in state.get("merged_candidates", [])],
            "candidate_ids": [r["doc_id"] for r in state.get("selection_candidates", [])],
            "condition_assessments": state.get("condition_assessments", {}),
            "outcome": state["stage_outcome"] if mode != "retrieval" else None,
            "preselection_failure": state.get("stage_outcome") if not request else None,
            "format_valid": any(e["event"] == "a_format_checked" for e in trace.events)
            if mode != "retrieval"
            else None,
            "selected_doc_ids": state.get("model_output", {}).get("selected_doc_ids"),
            "usage": [
                e["response"].get("usage", {})
                for e in trace.events
                if e["event"] == "model_response" and e.get("mode") == "live"
            ],
            "input_chars": request.get("chars"),
            "elapsed_ms": round((time.monotonic() - began) * 1000),
            "trace_ref": trace.run_id,
            "trace_file": trace_path.name if trace_path else None,
        }
        result["cases"].append(row)
        if output:
            write_json(output, result)
        # Authentication/quota failures should not trigger the remaining paid attempts.
        if mode == "live" and any(
            e.get("code") in {"hcx_http_401", "hcx_http_403", "hcx_http_429"}
            for e in state["stage_outcome"]["issues"]
        ):
            break
    # Runtime never receives expected answers. Replay is explicitly synthetic.
    expected = {c["case_id"]: c["expected"]["A"] for c in cases}
    for row in result["cases"]:
        gold = expected[row["case_id"]]
        wanted = {f"{t['org_id']}:{t['table_id']}" for t in gold["tables"]}
        row["before_inclusion"] = wanted <= set(row["before_ids"])
        row["candidate_inclusion"] = wanted <= set(row["candidate_ids"])
        selected = row["selected_doc_ids"]
        row["raw_selection_exact"] = set(selected) == wanted if row["format_valid"] else None
        row["exact"] = (
            (
                row["outcome"]["status"] == "ok"
                and table_keys(row["outcome"]["result"]) == table_keys(gold)
            )
            if mode != "retrieval"
            else None
        )
    rows = result["cases"]
    result["complete"] = len(rows) == len(cases)
    result["source_unchanged"] = result["source_sha256"] == {
        p.name: fingerprint(p) for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    result["summary"] = {
        "requested_cases": len(cases),
        "executed_cases": len(rows),
        "before_inclusion_count": sum(r["before_inclusion"] for r in rows),
        "candidate_inclusion_count": sum(r["candidate_inclusion"] for r in rows),
        "before_candidate_total": sum(len(r["before_ids"]) for r in rows),
        "candidate_total": sum(len(r["candidate_ids"]) for r in rows),
        "candidate_min": min((len(r["candidate_ids"]) for r in rows), default=0),
        "candidate_max": max((len(r["candidate_ids"]) for r in rows), default=0),
        "input_chars_total": sum(r["input_chars"] or 0 for r in rows),
        "input_chars_max": max((r["input_chars"] or 0 for r in rows), default=0),
        "format_valid_count": sum(r["format_valid"] for r in rows) if mode != "retrieval" else None,
        "raw_selection_exact_count": sum(r["raw_selection_exact"] is True for r in rows)
        if mode != "retrieval"
        else None,
        "exact_count": sum(r["exact"] for r in rows) if mode != "retrieval" else None,
        "elapsed_ms": round((time.monotonic() - start) * 1000),
        "interpretation": "candidate inspection; no model accuracy"
        if mode == "retrieval"
        else "synthetic answer replay; NOT model accuracy"
        if mode == "replay"
        else "live model development evaluation; not held-out performance",
    }
    if output:
        write_json(output, result)
    return result
