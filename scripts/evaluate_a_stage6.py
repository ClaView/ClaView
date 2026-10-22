"""Fixed-version original22 integration evaluation; stage5 selected model mode is off."""

from __future__ import annotations

import argparse
import json
import socket
import time
from collections import Counter
from pathlib import Path

from claview.a1_mappings import load_experiment
from claview.a_discovery import TableDiscovery
from claview.candidate_store import CandidateStore
from claview.contracts import Config, fingerprint
from claview.discovery_evaluation import evaluate_search
from claview.metadata import MetadataStore
from claview.search import SearchIndex
from claview.search_documents import build_search_documents

VARIANTS = {
    "baseline": ("off", False, "off", "off"),
    "rules": ("rules", False, "off", "off"),
    "dictionary_experiment": ("rules", True, "off", "off"),
    "evidence": ("rules", False, "evidence", "off"),
    "shadow": ("rules", False, "evidence", "shadow"),
    "auto": ("rules", False, "evidence", "auto"),
}
PAIRS = {
    "rules": ("baseline", "rules"),
    "dictionary": ("rules", "dictionary_experiment"),
    "evidence": ("rules", "evidence"),
    "set_judgment": ("evidence", "shadow"),
    "auto_option": ("shadow", "auto"),
}
DATA = [
    "data/catalog/search_catalog.json",
    "data/raw/boK_meta_raw.jsonl",
    "data/mappings/runtime/search_mappings.json",
    "data/catalog/search_definitions.json",
    "eval/cases/golden_set_v1.json",
    "data/mappings/experiments/a1_source_checked_v1/review.json",
    "data/mappings/experiments/a1_source_checked_v1/dictionary.json",
]
REFERENCES = [
    "runs/a-golden22-20261006-102630/evaluation.json",
    "runs/a1-a2-stage4-20261006/verified-evaluation/evaluation.json",
    "runs/a1-a2-stage4-20261006/verified-tests.xml",
    "runs/a1-a2-stage5-20261006/interpreter-only/comparison.json",
    "runs/a1-a2-stage5-20261006/interpreter-only/postmortem.json",
    "runs/a1-a2-stage5-20261006/offline-tests.xml",
    "runs/a-hcx-check-20261006-102841/evaluation.json",
    "runs/a-hcx-six-20261006-104431/evaluation.json",
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_new(path, value):
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")


def block_network(*args, **kwargs):
    raise AssertionError("External requests are forbidden for stage6 selected model=off")


def score_selection(predictions, cases):
    """Score only completed decisions; never turn absent user answers into wrong selections."""
    gold = {c["case_id"]: c for c in cases}
    rows = []
    for prediction in predictions:
        case = gold[prediction["case_id"]]
        expected = {f"{t['org_id']}:{t['table_id']}" for t in case["expected"]["A"]["tables"]}
        actual = set(prediction["selected_ids"])
        confirmed = prediction["confirmed"]
        rows.append(
            {
                **prediction,
                "final_exact": actual == expected if confirmed else None,
                "unnecessary_ids": sorted(actual - expected) if confirmed else None,
                "missing_required_ids": sorted(expected - actual) if confirmed else None,
                "evaluation_state": "completed" if confirmed else "unverified",
            }
        )
    completed = [r for r in rows if r["confirmed"]]
    automatic = [r for r in completed if r["selection_origin"] == "automatic"]
    summary = {
        "cases": len(rows),
        "completed": len(completed),
        "unverified": len(rows) - len(completed),
        "completed_exact_count": sum(r["final_exact"] for r in completed),
        "completed_exact_rate": sum(r["final_exact"] for r in completed) / len(completed)
        if completed
        else None,
        "confirmed_exact_fraction_of_all": sum(r["final_exact"] for r in completed) / len(rows),
        "unnecessary_tables_in_completed": sum(len(r["unnecessary_ids"]) for r in completed)
        if completed
        else None,
        "missing_tables_in_completed": sum(len(r["missing_required_ids"]) for r in completed)
        if completed
        else None,
        "automatic_count": len(automatic),
        "automatic_coverage": len(automatic) / len(rows),
        "automatic_accuracy": sum(r["final_exact"] for r in automatic) / len(automatic)
        if automatic
        else None,
        "routes": dict(Counter(r["route"] for r in rows)),
        "reasons": dict(Counter(r["reason"] for r in rows)),
        "original_query_preserved": all(r["original_query_preserved"] for r in rows),
        "a_tables_observed_count": len(completed),
        "a_tables_contract_valid": all(r["a_tables_contract_valid"] for r in completed)
        if completed
        else None,
        "b_execution_count": 0,
        "user_answers_received": 0,
        "user_confirmation_success": None,
    }
    return rows, summary


def capture(discovery, report):
    predictions = []
    for case in report["cases"]:
        bundle = (
            discovery.repository.get(case["candidate_set_id"], case["context"])
            if case["candidate_set_id"]
            else {}
        )
        decision = bundle.get("selection_decision") or {}
        a = bundle.get("a_outcome") or {}
        tables = a.get("result", {}).get("tables", [])
        confirmed = a.get("status") == "ok" and bool(tables)
        a1 = bundle.get("a1_state", {})
        a2 = bundle.get("a2_state", {})
        predictions.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "candidate_set_id": case["candidate_set_id"],
                "context": case["context"],
                "confirmed": confirmed,
                "selection_origin": bundle.get("selection_origin"),
                "selected_ids": bundle.get("selection", []),
                "a_tables": tables if confirmed else None,
                "a_tables_contract_valid": all(
                    set(t) == {"org_id", "table_id", "table_name"} for t in tables
                )
                and {f"{t['org_id']}:{t['table_id']}" for t in tables}
                == set(bundle.get("selection", []))
                if confirmed
                else None,
                "original_query_preserved": bundle.get("original_query") == case["query"],
                "route": decision.get("route", "user_confirmation")
                if not case["failure"]
                else "technical_failure",
                "reason": decision.get("reason", "manual_selection")
                if not case["failure"]
                else case["failure"],
                "failure": case["failure"],
                "decision": decision,
                "a1_requirements_count": len(a1.get("requirements", [])),
                "a1_contribution": a1.get("contribution", {}),
                "mapping_expansions": bundle.get("diagnostics", {})
                .get("mapping_resolution", {})
                .get("expansions", []),
                "a2_check_states": dict(
                    Counter(
                        check["state"]
                        for candidate in a2.get("by_doc_id", {}).values()
                        for check in candidate.get("checks", [])
                    )
                ),
                "model_status": "not_called",
                "model_reason": "stage5_not_adopted; selected_configuration_off",
                "model_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "request_wait_ms": 0,
                "local_processing_ms": case["elapsed_ms"],
            }
        )
    return predictions


def evaluate(output):
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    socket.create_connection = block_network
    output.mkdir(parents=True, exist_ok=False)
    sources = {p.as_posix(): fingerprint(p) for p in sorted(Path("src/claview").glob("*.py"))}
    sources["scripts/evaluate_a_stage6.py"] = fingerprint(Path(__file__))
    inputs = {name: fingerprint(Path(name)) for name in DATA}
    references = {name: fingerprint(Path(name)) for name in REFERENCES}
    model_evidence = read(REFERENCES[4])
    if model_evidence["default_model_mode"] != "off":
        raise ValueError("Stage5 model decision changed; a new evaluation plan is required")
    cases = read(DATA[4])["cases"]
    if len(cases) != 22 or len({c["case_id"] for c in cases}) != 22:
        raise ValueError("This evaluation requires original22")
    plan = {
        "schema_version": "claview-stage6-integration/1",
        "settings": {
            "retrieval": "baseline",
            "documents": "legacy",
            "mapping": "approved",
            "agent": "off",
            "interpreter": "off",
            "as_of": "2026-10-02",
        },
        "variants": VARIANTS,
        "variant_fields": ["a1", "experimental_dictionary", "a2", "selection"],
        "model_preflight": {
            "selected_configuration": "off",
            "reason": "stage5_7_responses_0_accepted; no_live_configuration_adopted",
            "authorized_request_ceiling": 44,
            "authorized_per_question_ceiling": 2,
            "planned_requests": 0,
            "max_output_tokens": 0,
            "reserved_input_tokens": 0,
            "reserved_output_tokens": 0,
            "retries": 0,
            "network_blocked": True,
        },
        "queries": [{"case_id": c["case_id"], "query": c["query"]} for c in cases],
        "source_sha256": sources,
        "input_sha256": inputs,
        "reused_reference_sha256": references,
        "limits": ["all22_single_table", "extra48_excluded", "function_tests_separate"],
    }
    write_new(output / "plan.json", plan)
    started = time.perf_counter()
    store = MetadataStore(Path(DATA[0]), Path(DATA[1]))
    dictionary = load_experiment(store, Path(DATA[2]), Path(DATA[5]))
    reports, summaries = {}, {}
    for name, (a1, experimental, a2, selection) in VARIANTS.items():
        began = time.perf_counter()
        directory = output / name
        directory.mkdir()
        # Each arm gets a fresh index so warm caches from another arm do not bias timing.
        search = SearchIndex(
            store,
            Path(DATA[2]),
            build_search_documents(store, Path(DATA[2]), definitions_path=Path(DATA[3])),
            mapping_policy="approved",
        )
        discovery = TableDiscovery(
            search,
            CandidateStore(directory / "candidates.sqlite3"),
            Config(as_of="2026-10-02"),
            a1_mode=a1,
            a1_dictionary=dictionary if experimental else None,
            a2_mode=a2,
            selection_mode=selection,
        )
        report = evaluate_search(cases, discovery)
        predictions = capture(discovery, report)
        write_new(directory / "predictions.json", predictions)
        rows, selected = score_selection(predictions, cases)
        write_new(directory / "retrieval.json", report)
        write_new(directory / "selection.json", {"cases": rows, "summary": selected})
        summary = {
            "retrieval": {
                **report["summary"],
                "top1_count": sum(r["first_correct_rank"] == 1 for r in report["cases"]),
            },
            "selection": selected,
            "dictionary": discovery.a1_dictionary.describe(),
            "mapping_expansion_cases": sum(bool(r["mapping_expansions"]) for r in rows),
            "auxiliary_added_count": sum(
                len(r["a1_contribution"].get("auxiliary_added_ids", [])) for r in rows
            ),
            "a2_check_states": dict(sum((Counter(r["a2_check_states"]) for r in rows), Counter())),
            "model_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "request_wait_ms": 0,
            "total_local_processing_ms": round((time.perf_counter() - began) * 1000, 3),
        }
        reports[name], summaries[name] = report, summary
        print(
            json.dumps(
                {
                    "variant": name,
                    "retrieval": summary["retrieval"],
                    "completed": selected["completed"],
                }
            ),
            flush=True,
        )
    differences = {}
    for label, (left, right) in PAIRS.items():
        differences[label] = {
            "left": left,
            "right": right,
            "changed_candidates_or_order": [
                a["case_id"]
                for a, b in zip(reports[left]["cases"], reports[right]["cases"], strict=True)
                if any(a[k] != b[k] for k in ("before_ids", "after_ids", "display_order"))
            ],
        }
    protected = {**inputs, **references, **sources}
    changed = [p for p, sha in protected.items() if fingerprint(Path(p)) != sha]
    report = {
        "schema_version": plan["schema_version"],
        "settings": plan["settings"],
        "variants": summaries,
        "paired_differences": differences,
        "source_sha256": sources,
        "input_sha256": inputs,
        "reused_reference_sha256": references,
        "protected_files_changed": changed,
        "elapsed_local_ms": round((time.perf_counter() - started) * 1000, 3),
        "actual_model_requests": 0,
        "retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "request_wait_ms": 0,
        "model_processing_ms": None,
        "model_quality": "not_measured_on22; stage5_failures_reused_separately",
        "b_handoff": "no_golden_case_confirmed; contract_tests_reported_separately",
        "default_decision": "rules/evidence/shadow; approved_mapping; model_off",
        "experimental_options": ["source_checked_dictionary", "auto"],
        "functional_tests_in_metrics": False,
        "extra48_in_metrics": False,
    }
    write_new(output / "evaluation.json", report)
    if changed:
        raise RuntimeError("Frozen evaluation source or data changed")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    evaluate(args.output)


if __name__ == "__main__":
    main()
