"""B command line with explicit external-call opt-in (B and its search tools only).

The A -> B flow and its golden end-to-end evaluation are `claview-pipeline` (claview.pipeline.cli).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import warnings
from pathlib import Path

from .contracts import Config, StageFailure, Trace, today_iso
from .embeddings import NcpEmbedder, VectorIndex, build_vector_index
from .evaluation import evaluate, write_json
from .fetch import KosisClient, build_api_request, fetch_data, validate_b_result
from .graphs import build_b_graph
from .metadata import MetadataStore
from .models import HcxModel, ScriptedModel
from .search import SearchIndex
from .search_documents import build_search_documents
from .services import Services


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "inspect",
            "evaluate",
            "fetch-gold",
            "diagrams",
            "build-search-docs",
            "build-vector-index",
            "search",
        ],
    )
    p.add_argument("--catalog", type=Path, default=Path("data/catalog/search_catalog.json"))
    p.add_argument("--metadata", type=Path, default=Path("data/raw/boK_meta_raw.jsonl"))
    p.add_argument("--cases", type=Path, default=Path("eval/cases/golden/golden_set_v1.json"))
    p.add_argument("--mode", choices=["retrieval", "replay", "live"], default="retrieval")
    p.add_argument("--case", default="CV-AB-19")
    p.add_argument("--query")
    p.add_argument("--search-mode", choices=["legacy", "documents", "hybrid"], default="documents")
    p.add_argument(
        "--mappings", type=Path, default=Path("data/mappings/review/검색용_의미매핑_초안.json")
    )
    p.add_argument("--vector-index", type=Path, default=Path("data/indexes/ncp-v2.json"))
    p.add_argument("--definitions", type=Path, default=Path("data/catalog/search_definitions.json"))
    p.add_argument("--hierarchy", action="store_true")
    p.add_argument("--search-scope", choices=["global", "hierarchy", "combined"], default="global")
    p.add_argument("--candidate-policy", choices=["legacy", "union"], default="union")
    p.add_argument("--channel-k", type=int, default=10)
    p.add_argument("--candidate-pool-limit", type=int, default=30)
    p.add_argument("--expansion-limit", type=int, default=10)
    p.add_argument("--model-candidate-limit", type=int, default=40)
    p.add_argument("--no-frequency-filter", action="store_true")
    p.add_argument("--prompt-key", action="store_true", help="Read HCX key with hidden local input")
    p.add_argument(
        "--hcx-min-interval",
        type=float,
        default=20,
        help="Minimum seconds between HCX requests (0-60, default 20)",
    )
    p.add_argument("--allow-external", action="store_true")
    p.add_argument(
        "--as-of", default=None, help="Reference date YYYY-MM-DD (default: today, the query date)"
    )
    p.add_argument("--output", type=Path, default=Path("eval/results/latest.json"))
    args = p.parse_args()
    if not 0 <= args.hcx_min_interval <= 60:
        p.error("--hcx-min-interval must be between 0 and 60")
    live = args.command in {"evaluate", "fetch-gold"} and args.mode == "live"
    embedding_live = args.command == "build-vector-index" or (
        args.search_mode == "hybrid" and args.command in {"search", "evaluate"}
    )
    if (live or embedding_live) and not args.allow_external:
        p.error("Live requests require --allow-external")
    if args.hierarchy and args.search_scope == "hierarchy":
        p.error("Use --search-scope hierarchy without --hierarchy")
    effective_scope = "combined" if args.hierarchy else args.search_scope
    needed = []
    if live and args.command != "fetch-gold":
        needed.append("CLOVASTUDIO_API_KEY")
    if live and args.command == "fetch-gold":
        needed.append("KOSIS_API_KEY")
    if embedding_live:
        needed.append("CLOVASTUDIO_API_KEY")
    hcx_key = os.environ.get("CLOVASTUDIO_API_KEY") if needed else None
    if args.prompt_key:
        if "CLOVASTUDIO_API_KEY" not in needed:
            p.error("--prompt-key requires an external HCX/embedding operation")
        if not sys.stdin.isatty():
            p.error("Hidden input requires your local interactive terminal")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                hcx_key = getpass.getpass("CLOVA Studio API key (hidden): ")
            except (getpass.GetPassWarning, EOFError):
                p.error("Secure hidden input unavailable; key was not requested visibly")
    missing = [
        name
        for name in needed
        if not (hcx_key if name == "CLOVASTUDIO_API_KEY" else os.environ.get(name))
    ]
    if missing:
        p.error("Missing environment variables: " + ", ".join(missing))
    if effective_scope != "global" and args.command == "evaluate" and args.mode == "replay":
        p.error(
            "Replay fixtures do not provide category decisions; use live or tests for hierarchy"
        )
    if (
        effective_scope != "global"
        and args.command in {"search", "evaluate"}
        and args.mode == "retrieval"
    ):
        p.error("Retrieval-only commands do not run model routing; use evaluate --mode live")
    clients = []
    try:
        store = MetadataStore(args.catalog, args.metadata)
        corpus = build_search_documents(store, args.mappings, definitions_path=args.definitions)
        embedder = NcpEmbedder(hcx_key) if embedding_live else None
        if embedder:
            clients.append(embedder)
        if args.command in {"build-search-docs", "build-vector-index"}:
            result = (
                corpus
                if args.command == "build-search-docs"
                else build_vector_index(corpus, embedder, args.vector_index)
            )
            if embedder:
                result["embedding_usage"] = embedder.usage
            write_json(args.output, result)
            print(
                json.dumps(
                    {"output": str(args.output), "result": result.get("manifest", result)},
                    ensure_ascii=False,
                )
            )
            return
        vectors = None
        if args.search_mode == "hybrid" and embedding_live:
            vectors = VectorIndex(
                corpus, json.loads(args.vector_index.read_text(encoding="utf-8")), embedder
            )
        search = SearchIndex(
            store, args.mappings, corpus if args.search_mode != "legacy" else None, vectors
        )
        config = Config(
            hierarchy=args.hierarchy,
            search_scope=effective_scope,
            candidate_policy=args.candidate_policy,
            channel_k=args.channel_k,
            candidate_pool_limit=args.candidate_pool_limit,
            expansion_limit=args.expansion_limit,
            model_candidate_limit=args.model_candidate_limit,
            filter_frequency=not args.no_frequency_filter,
            as_of=args.as_of or today_iso(),
        )
        trace = Trace()
        cases = None
        if args.command in {"evaluate", "fetch-gold"}:
            cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))["cases"]
        if args.command == "search":
            if not args.query:
                p.error("--query is required")
            candidates = (
                search.retrieve(args.query, config.top_k)
                if config.candidate_policy == "legacy"
                else search.retrieve_pool(
                    args.query,
                    [],
                    config.channel_k,
                    config.candidate_pool_limit,
                    config.expansion_limit,
                )
            )
            result = {
                "query": args.query,
                "search": search.last_retrieval,
                "candidates": search.candidate_context(args.query, candidates),
            }
        elif args.command == "inspect":
            duplicate = {}
            for key, doc in store.catalog.items():
                duplicate.setdefault(doc["title"], []).append(
                    {"doc_id": key, "depth": doc["depth"], "path": doc["path"]}
                )
            result = {
                "tables": len(store.catalog),
                "metadata": len(store.records),
                "duplicate_titles": {k: v for k, v in duplicate.items() if len(v) > 1},
                "snapshot": store.snapshot,
            }
        elif args.command == "evaluate":
            model = HcxModel(hcx_key, min_interval=args.hcx_min_interval) if live else None
            if model:
                clients.append(model)
            result = evaluate(
                cases, store, search, config, model, args.mode, args.output.parent / "traces"
            )
        elif args.command == "fetch-gold":
            selected = [c for c in cases if args.case == "all" or c["case_id"] == args.case]
            if not selected:
                p.error("Unknown case")
            client = KosisClient(os.environ["KOSIS_API_KEY"]) if live else None
            if client:
                clients.append(client)
            result = {
                "mode": "live_numeric_query" if live else "validated_request_plan",
                "cases": [],
            }
            for case in selected:
                b = case["expected"]["B"]
                validate_b_result(b, store)
                result["cases"].append(
                    {
                        "case_id": case["case_id"],
                        "result": fetch_data(b, store, client, trace)
                        if client
                        else {"requests": [build_api_request(s) for s in b["selections"]]},
                    }
                )
            result["trace_ref"] = trace.run_id
            trace.save(args.output.parent / "traces")
        else:  # diagrams
            services = Services(store, search, ScriptedModel({}), config, trace)
            result = {"B": build_b_graph(services).get_graph().draw_mermaid()}
        result["search_configuration"] = search.describe()
        if embedder:
            result["embedding_usage"] = embedder.usage
        write_json(args.output, result)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "summary": result.get("summary", result.get("response")),
                    "mode": result.get("mode"),
                    "cases": len(cases) if cases else None,
                },
                ensure_ascii=False,
            )
        )
    except (StageFailure, FileNotFoundError) as exc:
        p.exit(
            2,
            (
                exc.code
                if isinstance(exc, StageFailure)
                else "Input file missing; see data setup guide"
            )
            + "\n",
        )
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    main()
