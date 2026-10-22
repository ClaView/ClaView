"""Offline A baseline runner. Accepts inputs only; never opens gold or confirms A."""

from __future__ import annotations

import argparse
import socket
import sqlite3
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from unittest.mock import patch

from .a_eval_contracts import digest, read_json, unique_cases, validate, write_new
from .a_retrieval import POLICIES, load_metadata_corpus
from .contracts import StageFailure, fingerprint


def predict_case(case, discovery):
    validate(case, "input_case")
    started = time.perf_counter()
    prediction = {
        **case,
        "decision": "technical_failure",
        "clarification_axes": [],
        "before_ids": [],
        "after_ids": [],
        "display_order": [],
        "error_code": None,
        "elapsed_ms": 0,
        "usage": {
            "model_calls": 0,
            "external_api_calls": 0,
            "blocked_network_attempts": 0,
            "b_calls": 0,
            "billed_cost_usd": 0,
        },
        "trace": {},
    }
    context = {
        "session_id": uuid.uuid4().hex,
        "request_id": uuid.uuid4().hex,
        "query_revision": case["query_revision"],
    }
    save = discovery.save_candidates

    def capture(query, ctx, candidates, diagnostics, ttl_seconds=86400):
        prediction["before_ids"] = [row["doc_id"] for row in candidates]
        prediction["after_ids"] = [row["doc_id"] for row in candidates if not row["conflicts"]]
        prediction["trace"] = {
            "context": ctx,
            "diagnostics": diagnostics,
            "assessments": {
                row["doc_id"]: {key: row[key] for key in ("conflicts", "unknowns", "rank_signals")}
                for row in candidates
            },
        }
        return save(query, ctx, candidates, diagnostics, ttl_seconds)

    def deny_network(*args, **kwargs):
        prediction["usage"]["blocked_network_attempts"] += 1
        raise StageFailure("network_forbidden")

    try:
        if case["prior_turns"]:
            raise StageFailure("context_not_supported_by_baseline")
        with (
            patch.object(discovery, "save_candidates", capture),
            patch.object(socket.socket, "connect", deny_network),
            patch.object(socket.socket, "connect_ex", deny_network),
            patch.object(socket, "create_connection", deny_network),
        ):
            response = discovery.search_tables(case["query"], context)
        if response["status"] != "awaiting_user" or response["result"] is not None:
            raise StageFailure("invalid_waiting_response")
        set_id = response["interaction"]["candidate_set_id"]
        bundle = discovery.repository.get(set_id, context)
        if bundle.get("b_attempts") or bundle.get("a_outcome") is not None:
            raise StageFailure("unexpected_confirmation_or_b")
        prediction["display_order"] = bundle["display_order"]
        prediction["decision"] = "display_candidates"
        prediction["trace"].update(candidate_set_id=set_id, status=bundle["status"])
    except StageFailure as exc:
        prediction["error_code"] = exc.code
        if exc.code in {"retrieval_empty", "no_compatible_candidates"}:
            prediction["decision"] = "insufficient_evidence"
        elif exc.code == "source_out_of_scope":
            prediction["decision"] = "out_of_scope"
    except Exception as exc:
        # Never persist arbitrary exception messages that might contain credentials.
        prediction["error_code"] = "unexpected_exception"
        prediction["trace"]["exception_type"] = type(exc).__name__
    if prediction["usage"]["blocked_network_attempts"]:
        prediction.update(decision="technical_failure", error_code="network_forbidden")
    prediction["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    validate(prediction, "prediction_case")
    return prediction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/search_catalog.json"))
    parser.add_argument("--metadata", type=Path, default=Path("data/raw/boK_meta_raw.jsonl"))
    parser.add_argument(
        "--mappings", type=Path, default=Path("data/mappings/runtime/search_mappings.json")
    )
    parser.add_argument(
        "--mapping-policy", choices=["approved", "off", "draft_experiment"], default="approved"
    )
    parser.add_argument(
        "--definitions", type=Path, default=Path("data/catalog/search_definitions.json")
    )
    parser.add_argument("--as-of", default="2026-10-02")
    parser.add_argument(
        "--document-profile", choices=["legacy", "D0", "D1", "D2", "D3"], default="legacy"
    )
    parser.add_argument("--corpus", type=Path, help="Prebuilt official document profile")
    parser.add_argument("--retrieval-policy", choices=POLICIES, default="baseline")
    parser.add_argument(
        "--metadata-corpus", type=Path, help="Verified D2/D3 metadata channel corpus"
    )
    parser.add_argument(
        "--export-index", action="store_true", help="Persist lexical index for size measurement"
    )
    parser.add_argument(
        "--metric-policy", type=Path, default=Path("eval/a_stage2_metric_policy.json")
    )
    parser.add_argument(
        "--unlock-test",
        action="store_true",
        help="Explicit final evaluation only; permanently marks test exposure",
    )
    args = parser.parse_args()
    inputs = read_json(args.inputs)
    validate(inputs, "inputs")
    unique_cases(inputs["cases"])
    if inputs["split"].startswith("test_") and not args.unlock_test:
        parser.error("Locked test: use only after freezing policy; requires --unlock-test")
    args.output_dir.mkdir(parents=True, exist_ok=False)

    from .a_discovery import TableDiscovery
    from .candidate_store import CandidateStore
    from .contracts import Config
    from .metadata import MetadataStore
    from .search import SearchIndex
    from .search_documents import build_search_documents
    from .search_documents import digest as document_digest

    paths = {
        name: getattr(args, name) for name in ("catalog", "metadata", "mappings", "definitions")
    }
    if args.corpus:
        paths["corpus"] = args.corpus
    if args.metadata_corpus:
        paths["metadata_corpus"] = args.metadata_corpus
    source_paths = sorted(Path(__file__).parent.glob("*.py"))
    hashes = {name: fingerprint(path) for name, path in paths.items()}
    policy_hash = fingerprint(args.metric_policy)
    code_hashes = {path.name: fingerprint(path) for path in source_paths}
    started = time.perf_counter()
    store = MetadataStore(args.catalog, args.metadata)
    if args.corpus:
        corpus = read_json(args.corpus)
        if corpus["manifest"].get("document_profile") != args.document_profile or corpus[
            "corpus_id"
        ] != document_digest([corpus["manifest"], corpus["documents"]]):
            raise ValueError("prebuilt_document_profile_mismatch")
        from .document_corpus import verify_information

        information = verify_information(store, corpus, args.definitions)
        if not information["raw_preservation_verified"]:
            raise ValueError("prebuilt_document_source_mismatch")
    elif args.document_profile == "legacy":
        corpus = build_search_documents(store, args.mappings, definitions_path=args.definitions)
    else:
        from .document_corpus import build_document_corpus

        corpus = build_document_corpus(
            store, args.mappings, args.definitions, args.document_profile
        )
    config = Config(as_of=args.as_of)
    metadata_corpus = (
        load_metadata_corpus(store, args.metadata_corpus, args.mappings, args.definitions)
        if args.retrieval_policy in {"metadata", "conditions", "rrf", "raw_sum", "rrf_protected"}
        else None
    )
    database = args.output_dir / "candidates.sqlite3"
    discovery = TableDiscovery(
        SearchIndex(store, args.mappings, corpus, mapping_policy=args.mapping_policy),
        CandidateStore(database),
        config,
        retrieval_policy=args.retrieval_policy,
        metadata_corpus=metadata_corpus,
    )
    initialization_ms = round((time.perf_counter() - started) * 1000, 3)
    index_bytes = None
    if args.export_index:
        search = discovery.search
        export = {
            "corpus_id": corpus["corpus_id"],
            "scope": "serialized_lexical_index_not_process_ram_or_vector_index",
            "chunk_grams": {k: sorted(v) for k, v in search.chunk_grams.items()},
            "chunk_idf": search.chunk_idf,
            "fields": {
                k: [{"text": t, "grams": sorted(gs), "weight": w} for t, gs, w in rows]
                for k, rows in search.fields.items()
            },
            "field_idf": search.idf,
        }
        path = args.output_dir / "search-index.json"
        write_new(path, export)
        index_bytes = path.stat().st_size
    rows = [predict_case(case, discovery) for case in inputs["cases"]]
    if hashes != {name: fingerprint(path) for name, path in paths.items()} or code_hashes != {
        path.name: fingerprint(path) for path in source_paths
    }:
        raise ValueError("source_changed_during_prediction")
    with sqlite3.connect(database) as connection:
        b_count = connection.execute("SELECT COUNT(*) FROM b_attempts").fetchone()[0]
    if b_count:
        raise ValueError("unexpected_b_attempt")
    times = sorted(row["elapsed_ms"] for row in rows)
    predictions = {
        **{key: inputs[key] for key in ("schema_version", "dataset_id", "split")},
        "inputs_sha256": digest(inputs),
        "run": {
            "mode": "offline_current_a_without_confirmation",
            "adapter_version": "baseline/1",
            "retrieval_policy": discovery.retriever.describe(),
            "mapping_policy": discovery.search.mapping_dictionary.policy,
            "document_profile": args.document_profile,
            "document_corpus_id": corpus["corpus_id"],
            "corpus_manifest": corpus["manifest"],
            "serialized_index_bytes": index_bytes,
            "created_at": datetime.now(UTC).isoformat(),
            "as_of": args.as_of,
            "source_sha256": hashes,
            "code_sha256": code_hashes,
            "inputs_file_sha256": fingerprint(args.inputs),
            "metric_policy_sha256": policy_hash,
            "snapshots": discovery.snapshots,
            "config": asdict(config),
            "official_ids": sorted(store.catalog),
            "test_unlocked": args.unlock_test,
            "initialization_ms": initialization_ms,
            "mean_query_ms": mean(times),
            "p95_query_ms_nearest_rank": times[(95 * len(times) + 99) // 100 - 1],
            "usage": {key: sum(row["usage"][key] for row in rows) for key in rows[0]["usage"]},
            "database_b_attempts": b_count,
            "context_policy": "nonempty_prior_turns_are_explicit_technical_failure",
        },
        "cases": rows,
    }
    write_new(args.output_dir / "predictions.json", predictions)


if __name__ == "__main__":
    main()
