"""Durable user selection; offline by default, with explicit opt-in for live B."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import warnings
from pathlib import Path

from .a1_mappings import load_experiment
from .a_discovery import TableDiscovery, page_view, run_b
from .a_retrieval import POLICIES, load_metadata_corpus
from .candidate_store import CandidateStore
from .contracts import Config, StageFailure, Trace, fingerprint
from .discovery_evaluation import evaluate_search
from .evaluation import write_json
from .metadata import MetadataStore
from .models import HcxModel, ScriptedModel
from .search import SearchIndex
from .search_documents import build_search_documents
from .services import Services

COMMANDS = {
    "search-tables",
    "list-candidates",
    "confirm-tables",
    "clarify-tables",
    "reassess-tables",
    "candidate-status",
    "run-b",
    "evaluate-search",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--db", type=Path, default=Path("runs/candidates.sqlite3"))
    parser.add_argument("--query")
    parser.add_argument("--session")
    parser.add_argument("--request")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--set-id")
    parser.add_argument("--select", nargs="+")
    parser.add_argument("--answer", help="A clarification option value from the stored question")
    parser.add_argument("--a-agent-mode", choices=["off", "mock"], default="off")
    parser.add_argument("--a-responses", type=Path, help="Offline A-role response fixture")
    parser.add_argument("--a-max-calls", type=int, default=4)
    parser.add_argument("--a-max-research", type=int, choices=[0, 1], default=1)
    parser.add_argument("--a-max-metadata", type=int, choices=[0, 1], default=1)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=5)
    parser.add_argument("--ttl-seconds", type=int, default=86400)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--b-mode", choices=["scripted", "live"], default="scripted")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--prompt-key", action="store_true", help="Use hidden input for live B")
    parser.add_argument(
        "--b-responses",
        type=Path,
        help="Explicit offline ScriptedModel responses; not model accuracy",
    )
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/search_catalog.json"))
    parser.add_argument("--metadata", type=Path, default=Path("data/raw/boK_meta_raw.jsonl"))
    parser.add_argument(
        "--mappings", type=Path, default=Path("data/mappings/runtime/search_mappings.json")
    )
    parser.add_argument("--mapping-policy", choices=["approved", "off"], default="approved")
    parser.add_argument("--a1-mode", choices=["off", "rules"], default="rules")
    parser.add_argument("--a2-mode", choices=["off", "evidence"], default="evidence")
    parser.add_argument("--a-selection-mode", choices=["off", "shadow", "auto"], default="shadow")
    parser.add_argument(
        "--a1-mapping-review",
        type=Path,
        help="Explicit source-checked draft experiment review.json",
    )
    parser.add_argument("--retrieval-policy", choices=POLICIES, default="baseline")
    parser.add_argument(
        "--metadata-corpus", type=Path, help="Verified D2/D3 metadata channel corpus"
    )
    parser.add_argument(
        "--document-profile", choices=["legacy", "D0", "D1", "D2", "D3"], default="legacy"
    )
    parser.add_argument(
        "--definitions", type=Path, default=Path("data/catalog/search_definitions.json")
    )
    parser.add_argument("--cases", type=Path, default=Path("eval/cases/golden_set_v1.json"))
    parser.add_argument("--as-of", default="2026-10-02")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.a1_mapping_review and (args.a1_mode != "rules" or args.mapping_policy != "approved"):
        parser.error("A1 mapping experiment requires --a1-mode rules --mapping-policy approved")
    if args.b_mode == "live" and (args.command != "run-b" or not args.allow_external):
        parser.error("Live B requires run-b --b-mode live --allow-external")
    if args.b_mode == "live" and args.b_responses:
        parser.error("Live B cannot use scripted responses")
    if args.prompt_key and (args.command != "run-b" or args.b_mode != "live"):
        parser.error("--prompt-key requires run-b --b-mode live --allow-external")
    if args.command != "evaluate-search" and (not args.session or not args.request):
        parser.error("--session and --request are required")
    if args.command not in {"evaluate-search", "search-tables"} and not args.set_id:
        parser.error("--set-id is required")
    if args.command == "confirm-tables" and not args.select:
        parser.error("--select is required")
    if (args.a_agent_mode == "mock") != bool(args.a_responses):
        parser.error("Mock A requires --a-agent-mode mock and --a-responses together")
    if args.command == "clarify-tables" and not args.answer:
        parser.error("Clarification requires --answer")
    context = {
        "session_id": args.session,
        "request_id": args.request,
        "query_revision": args.revision,
    }
    hcx_key = _hcx_key(parser, args.prompt_key) if args.b_mode == "live" else None
    agent = None
    try:
        repository = CandidateStore(args.db)
        if args.command == "candidate-status" or (
            args.command == "list-candidates" and args.a_agent_mode == "off"
        ):
            bundle = repository.get(args.set_id, context, active=args.command == "list-candidates")
            result = (
                page_view(bundle, args.page, args.page_size)
                if args.command == "list-candidates"
                else {
                    k: bundle.get(k)
                    for k in (
                        "candidate_set_id",
                        "context",
                        "original_query",
                        "snapshots",
                        "status",
                        "expiry_reason",
                        "a_outcome",
                        "b_attempts",
                        "agent_state",
                        "search_query",
                        "a1_state",
                        "a2_state",
                        "selection_decision",
                        "selection_origin",
                        "selection_comparison",
                        "a_answers",
                    )
                }
            )
        else:
            store = MetadataStore(args.catalog, args.metadata)
            if args.document_profile == "legacy":
                corpus = build_search_documents(
                    store, args.mappings, definitions_path=args.definitions
                )
            else:
                from .document_corpus import build_document_corpus

                corpus = build_document_corpus(
                    store, args.mappings, args.definitions, args.document_profile
                )
            search = SearchIndex(store, args.mappings, corpus, mapping_policy=args.mapping_policy)
            config = Config(as_of=args.as_of)
            metadata_corpus = (
                load_metadata_corpus(store, args.metadata_corpus, args.mappings, args.definitions)
                if args.retrieval_policy
                in {"metadata", "conditions", "rrf", "raw_sum", "rrf_protected"}
                else None
            )
            discovery = TableDiscovery(
                search,
                repository,
                config,
                retrieval_policy=args.retrieval_policy,
                metadata_corpus=metadata_corpus,
                agent=(agent := _make_agent(args)),
                a1_mode=args.a1_mode,
                a2_mode=args.a2_mode,
                selection_mode=args.a_selection_mode,
                a1_dictionary=load_experiment(store, args.mappings, args.a1_mapping_review)
                if args.a1_mapping_review
                else None,
            )
            if args.command == "search-tables":
                result = discovery.search_tables(
                    args.query, context, args.page_size, args.ttl_seconds
                )
            elif args.command == "confirm-tables":
                result = discovery.confirm_tables(args.set_id, args.select, context)
            elif args.command == "clarify-tables":
                result = discovery.clarify_tables(
                    args.set_id, args.answer, context, args.page_size, args.ttl_seconds
                )
            elif args.command == "reassess-tables":
                result = discovery.reassess_tables(
                    args.set_id, context, args.page_size, args.ttl_seconds
                )
            elif args.command == "list-candidates":
                result = discovery.get_candidates(args.set_id, context, args.page, args.page_size)
            elif args.command == "run-b":
                if args.b_mode == "scripted" and not args.b_responses:
                    parser.error("--b-responses is required for offline B execution")
                if args.b_mode == "scripted":
                    responses = json.loads(args.b_responses.read_text(encoding="utf-8-sig"))
                    provider = ScriptedModel(responses)
                else:
                    provider = HcxModel(hcx_key)
                try:
                    services = Services(store, search, provider, config, Trace())
                    result = run_b(discovery, args.set_id, context, services, args.retry)
                    result["requested_b_mode"] = args.b_mode
                    result["mode"] = "stored_or_new_b_execution"
                finally:
                    if args.b_mode == "live":
                        provider.close()
            else:
                cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))["cases"]
                result = evaluate_search(cases, discovery)
                result["golden_sha256"] = fingerprint(args.cases)
        if args.output:
            write_json(args.output, result)
        print(
            json.dumps(
                result["summary"] if args.command == "evaluate-search" else result,
                ensure_ascii=False,
                indent=2,
            )
        )
    except (StageFailure, FileNotFoundError, ValueError) as exc:
        parser.exit(
            2,
            (exc.code if isinstance(exc, StageFailure) else "input_missing_or_invalid_json") + "\n",
        )
    finally:
        if agent:
            from .a_agent_scripted import close_agent

            asyncio.run(close_agent(agent))


def _hcx_key(parser, prompt_key):
    """Resolve credentials before loading data; only explicit prompting needs a TTY."""
    key = os.environ.get("CLOVASTUDIO_API_KEY")
    if prompt_key:
        if not sys.stdin.isatty():
            parser.error("Hidden input requires your local interactive terminal")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                key = getpass.getpass("CLOVA Studio API key (hidden): ")
            except (getpass.GetPassWarning, EOFError):
                parser.error("Secure hidden input unavailable; key was not requested visibly")
    if not key or not key.strip():
        parser.error("Missing environment variables: CLOVASTUDIO_API_KEY")
    return key


def _make_agent(args):
    if args.a_agent_mode == "off":
        return None
    from .a_agent import AgentConfig
    from .a_agent_scripted import scripted_agent

    return scripted_agent(
        args.a_responses,
        AgentConfig(
            enabled=True,
            max_calls=args.a_max_calls,
            max_research=args.a_max_research,
            max_metadata=args.a_max_metadata,
        ),
    )


if __name__ == "__main__":
    main()
