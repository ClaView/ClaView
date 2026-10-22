"""Explicit HCX-007 QueryInterpreter-only experiment, seven authorized questions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path

from claview.a1_interpreter import VERSION, QueryInterpretation, interpretation_gate
from claview.a1_query import prepare_question
from claview.a_discovery import TableDiscovery
from claview.a_model_contracts import (
    BASE_PROMPT,
    PROMPTS,
    canonical,
    fingerprint,
    make_packet,
    role_schema,
)
from claview.a_model_experiment import ExperimentLedger, read, save_new, seal
from claview.a_model_providers import AProvider
from claview.a_model_runtime import AdviceCache, Limits, Price, RoleRuntime
from claview.candidate_store import CandidateStore
from claview.contracts import Config
from claview.contracts import fingerprint as file_hash
from claview.metadata import MetadataStore
from claview.search import SearchIndex
from claview.search_documents import build_search_documents

CASES = ("CV-AB-01", "CV-AB-02", "CV-AB-04", "CV-AB-06", "CV-AB-09", "CV-AB-19", "CV-AB-22")
PATHS = {
    "catalog": "data/catalog/search_catalog.json",
    "metadata": "data/raw/boK_meta_raw.jsonl",
    "mappings": "data/mappings/runtime/search_mappings.json",
    "definitions": "data/catalog/search_definitions.json",
    "cases": "eval/cases/golden_set_v1.json",
    "prices": "eval/configs/a_stage7_prices.json",
}
MAX_OUTPUT = 2048


def search_index():
    store = MetadataStore(Path(PATHS["catalog"]), Path(PATHS["metadata"]))
    mapping = Path(PATHS["mappings"])
    return SearchIndex(
        store,
        mapping,
        build_search_documents(store, mapping, definitions_path=Path(PATHS["definitions"])),
        mapping_policy="approved",
    )


def predict(discovery, query, request):
    context = {"session_id": "stage5", "request_id": request, "query_revision": 1}
    started = time.monotonic()
    response = discovery.search_tables(query, context)
    set_id = response.get("candidate_set_id") or response["interaction"]["candidate_set_id"]
    bundle = discovery.repository.get(set_id, context)
    return {
        "query": query,
        "candidate_set_id": set_id,
        "context": context,
        "candidate_ids": [c["doc_id"] for c in bundle["candidate_pool"]],
        "display_order": bundle["display_order"],
        "status": response["status"],
        "selected_ids": bundle["selection"],
        "selection_decision": bundle.get("selection_decision"),
        "interpretation": bundle.get("a1_state", {}).get("interpretation"),
        "original_query_preserved": bundle["original_query"] == query,
        "elapsed_processing_seconds": time.monotonic() - started,
        "snapshots": bundle["snapshots"],
    }


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    sources = {
        p.as_posix(): file_hash(p)
        for p in [*sorted(Path("src/claview").glob("*.py")), Path(__file__).resolve()]
    }
    inputs = {p: file_hash(Path(p)) for p in PATHS.values()}
    selected = {
        c["case_id"]: c["query"] for c in read(PATHS["cases"])["cases"] if c["case_id"] in CASES
    }
    if set(selected) != set(CASES):
        raise ValueError("Authorized question set is incomplete")
    search = search_index()
    versions = {
        "dictionary": inputs[PATHS["mappings"]],
        "catalog": search.store.catalog_snapshot,
        "metadata": search.store.snapshot,
        "policy": VERSION,
    }
    provider = object.__new__(AProvider)
    provider.model = "HCX-007"
    jobs, baseline = [], []
    discovery = TableDiscovery(
        search,
        CandidateStore(output / "baseline.sqlite3"),
        Config(as_of="2026-10-02"),
        selection_mode="auto",
    )
    for case_id in CASES:
        query = selected[case_id]
        baseline.append({"case_id": case_id, **predict(discovery, query, case_id)})
        packet = make_packet(query, versions)
        schema = role_schema("QueryInterpreter", packet)
        _, body = provider.build("QueryInterpreter", packet, schema, MAX_OUTPUT)
        reservation = provider.reservation(body, MAX_OUTPUT)
        gate = interpretation_gate(prepare_question(query))
        jobs.append(
            {
                "case_id": case_id,
                "query": query,
                "packet": packet,
                "schema": schema,
                "packet_hash": fingerprint(packet),
                "body_bytes": len(canonical(body).encode("utf-8")),
                "reservation": list(reservation),
                "gate": gate,
            }
        )
    price = read(PATHS["prices"])["prices"]["HCX-007"]
    total_cost = sum((Price(**price).cost(j["reservation"]) for j in jobs), Decimal(0))
    plan = seal(
        {
            "schema_version": "claview-stage5/1",
            "role": "QueryInterpreter",
            "model": "HCX-007",
            "jobs": jobs,
            "versions": versions,
            "source_sha256": sources,
            "input_sha256": inputs,
            "price": price,
            "max_output_tokens": MAX_OUTPUT,
            "max_retries": 0,
            "request_interval_seconds": 20,
            "max_request_seconds": 60,
            "system_prompt": BASE_PROMPT + PROMPTS["QueryInterpreter"],
            "authorization": {
                "source": "user_stage5_request",
                "total_request_ceiling_including_retries": 14,
                "planned_query_interpreter_ceiling": 7,
                "evidence_judge": "not_enabled_in_this_independent_experiment",
                "statistical_api_B_embeddings_JEV": "forbidden",
            },
            "limits": {
                "max_calls": 7,
                "max_input_tokens": sum(j["reservation"][0] for j in jobs),
                "max_output_tokens": MAX_OUTPUT * 7,
                "max_seconds": 900,
                "max_cost_by_currency": {"KRW": str(total_cost * Decimal("1.2"))},
            },
            "cost_note": (
                "derived_conservative_reservation_from_authorized_call_scope; "
                "not_a_user_supplied_money_budget_or_invoice"
            ),
        }
    )
    save_new(output / "baseline.json", baseline)
    save_new(output / "plan.json", plan)
    print(
        json.dumps(
            {
                "status": "prepared_no_api_calls",
                "jobs": len(jobs),
                "eligible": sum(j["gate"]["needed"] for j in jobs),
                "max_body_bytes": max(j["body_bytes"] for j in jobs),
                "max_output_tokens": MAX_OUTPUT,
                "limits": plan["limits"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def verify(plan):
    if fingerprint({k: v for k, v in plan.items() if k != "plan_id"}) != plan["plan_id"]:
        raise ValueError("Plan hash mismatch")
    if (
        plan["model"] != "HCX-007"
        or plan["role"] != "QueryInterpreter"
        or [j["case_id"] for j in plan["jobs"]] != list(CASES)
        or plan["limits"]["max_calls"] != 7
        or plan["max_retries"] != 0
        or plan["max_output_tokens"] != MAX_OUTPUT
    ):
        raise ValueError("Authorized stage-5 scope mismatch")
    for field in ("source_sha256", "input_sha256"):
        if any(file_hash(Path(p)) != h for p, h in plan[field].items()):
            raise ValueError("Frozen inputs or code changed")
    allowed_queries = {
        c["case_id"]: c["query"] for c in read(PATHS["cases"])["cases"] if c["case_id"] in CASES
    }
    for job in plan["jobs"]:
        if (
            job["query"] != allowed_queries[job["case_id"]]
            or job["schema"] != role_schema("QueryInterpreter", job["packet"])
            or job["gate"] != interpretation_gate(prepare_question(job["query"]))
            or job["packet"] != make_packet(job["query"], plan["versions"])
            or fingerprint(job["packet"]) != job["packet_hash"]
        ):
            raise ValueError("Frozen packet mismatch")


def execute(plan_path):
    plan = read(plan_path)
    verify(plan)
    directory = plan_path.parent
    report_path = directory / "live.json"
    if report_path.exists():
        raise ValueError("Live report already exists; no repeat dispatch")
    ledger = ExperimentLedger(
        directory / "live-ledger.sqlite3",
        plan_id=plan["plan_id"],
        mode="live",
        limits=plan["limits"],
        stop_on_failure=False,
    )
    if ledger.results():
        raise ValueError(
            "This scope was already started; inspect the ledger without dispatching again"
        )
    price = Price(**plan["price"])
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    key_present = bool(key and key.strip())
    search = search_index()
    cache = AdviceCache(directory / "advice-cache.sqlite3")
    rows, last_dispatch, stop_reason = [], None, None
    with asyncio.Runner() as runner:
        provider = AProvider("HCX-007", api_key=key)
        try:
            for job in plan["jobs"]:
                if stop_reason or not key_present or not job["gate"]["needed"]:
                    rows.append(
                        {
                            "case_id": job["case_id"],
                            "call_status": "not_called",
                            "reason": stop_reason
                            or ("key_missing" if not key_present else job["gate"]["reason"]),
                            "query": job["query"],
                        }
                    )
                    continue
                wait = (
                    max(0, plan["request_interval_seconds"] - (time.monotonic() - last_dispatch))
                    if last_dispatch is not None
                    else 0
                )
                began_wait = time.monotonic()
                if wait:
                    time.sleep(wait)
                waited = time.monotonic() - began_wait
                ledger.claim(job["case_id"] + ":QueryInterpreter")
                runtime = RoleRuntime(
                    Limits(
                        max_calls=1,
                        max_tokens=128000,
                        max_seconds=60,
                        max_retries=0,
                        max_output_tokens=MAX_OUTPUT,
                        max_cost=str(price.cost(tuple(job["reservation"]))),
                    ),
                    enabled=True,
                    allow_live=True,
                    cache=cache,
                    attempt_ledger=ledger,
                )
                interpreter = QueryInterpretation(
                    provider,
                    runtime,
                    versions=plan["versions"],
                    price=price,
                    packet=job["packet"],
                    run_async=runner.run,
                )
                discovery = TableDiscovery(
                    search,
                    CandidateStore(directory / "live-candidates.sqlite3"),
                    Config(as_of="2026-10-02"),
                    selection_mode="auto",
                    interpreter=interpreter,
                )
                last_dispatch = time.monotonic()
                prediction = predict(discovery, job["query"], job["case_id"])
                interpretation = prediction["interpretation"]
                runtime_report = runtime.summary()
                app_advice = interpretation.get("advice", {"status": "fallback"})
                if interpretation["status"] == "fallback":
                    app_advice = {
                        **app_advice,
                        "status": "fallback",
                        "reason": interpretation.get("reason"),
                    }
                ledger.complete({"advice": app_advice, "runtime": runtime_report})
                attempts = [t for t in runtime_report["trace"] if t["event"] == "attempt"]
                accepted = interpretation["status"] == "advice"
                row = {
                    "case_id": job["case_id"],
                    **prediction,
                    "call_status": "success"
                    if accepted and attempts
                    else "failed"
                    if attempts
                    else "not_called",
                    "request_wait_seconds": waited,
                    "runtime": runtime_report,
                }
                rows.append(row)
                save_new(directory / (job["case_id"] + "-live.json"), row)
                print(
                    json.dumps(
                        {
                            "case_id": job["case_id"],
                            "call_status": row["call_status"],
                            "calls": runtime.external_calls,
                            "application_status": interpretation["status"],
                            "reason": interpretation.get("reason"),
                            "auxiliary_count": len(interpretation["auxiliaries"]),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if any(
                    a["status"]
                    in {
                        "model_unavailable",
                        "quota",
                        "transport_error",
                        "unsafe_provider_response",
                        "provider_http_error",
                        "provider_status_error",
                        "overloaded",
                    }
                    for a in attempts
                ):
                    stop_reason = "stopped_after_auth_quota_or_transport_failure"
        finally:
            runner.run(provider.close())
    result = {
        "mode": "live_QueryInterpreter_only",
        "plan_id": plan["plan_id"],
        "key_present": key_present,
        "authentication_confirmed": any("parsed_response" in a for a in ledger.audit()["attempts"]),
        "cases": rows,
        "ledger": ledger.audit(),
        "EvidenceJudge": {
            "status": "not_called",
            "reason": "separate_need_review_after_interpreter_experiment",
        },
    }
    save_new(report_path, result)
    compare(directory, plan, result)


def compare(directory, plan, live):
    baseline = {r["case_id"]: r for r in read(directory / "baseline.json")}
    gold = {c["case_id"]: c for c in read(PATHS["cases"])["cases"]}
    rows = []
    for row in live["cases"]:
        original = baseline[row["case_id"]]
        expected = {
            f"{t['org_id']}:{t['table_id']}"
            for t in gold[row["case_id"]]["expected"]["A"]["tables"]
        }
        modes = {}
        for name, value in (
            ("off", original),
            ("interpreter", row if "candidate_ids" in row else original),
        ):
            selected = set(value["selected_ids"])
            modes[name] = {
                "candidate_inclusion": expected <= set(value["candidate_ids"]),
                "first_correct_rank": next(
                    (i for i, k in enumerate(value["display_order"], 1) if k in expected), None
                ),
                "selected_ids": sorted(selected),
                "final_exact": selected == expected if selected else None,
                "unnecessary_tables": sorted(selected - expected) if selected else None,
                "missing_required_tables": sorted(expected - selected) if selected else None,
                "decision": value["selection_decision"]["route"],
                "candidate_count": len(value["candidate_ids"]),
            }
        rows.append(
            {
                "case_id": row["case_id"],
                "call_status": row["call_status"],
                "original_query_preserved": row.get("original_query_preserved", True),
                **modes,
            }
        )
    attempts = live["ledger"]["attempts"]
    summary = {
        "cases": rows,
        "actual_requests": len(attempts),
        "retries": sum(a["repair"] for a in attempts),
        "known_input_tokens": sum(a["usage"]["input_tokens"] for a in attempts if a["usage"]),
        "known_output_tokens": sum(a["usage"]["output_tokens"] for a in attempts if a["usage"]),
        "unknown_usage_requests": sum(a["usage"] is None for a in attempts),
        "request_wait_seconds": sum(r.get("request_wait_seconds", 0) for r in live["cases"]),
        "provider_processing_seconds": sum(a["elapsed_seconds"] for a in attempts),
        "local_and_provider_processing_seconds": sum(
            r.get("elapsed_processing_seconds", 0) for r in live["cases"]
        ),
        "source_sha256": plan["source_sha256"],
        "input_sha256": plan["input_sha256"],
        "new_gold_or_function_tests_in_metrics": False,
        "selection_scope": "no_selection_is_abstention_not_a_success_or_a_missing_table_score",
        "default_model_mode": "off",
        "actual_invoice_cost": None,
        "EvidenceJudge": live["EvidenceJudge"],
    }
    save_new(directory / "comparison.json", summary)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"cases", "source_sha256", "input_sha256"}
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("execute")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--live", action="store_true")
    run.add_argument("--allow-external", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output)
    elif not args.live or not args.allow_external:
        parser.error("Both --live and --allow-external are required")
    else:
        execute(args.plan)


if __name__ == "__main__":
    main()
