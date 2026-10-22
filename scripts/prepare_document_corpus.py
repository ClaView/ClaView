"""Generate document profiles from retained official sources, without evaluation inputs."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from claview.b.a_eval_contracts import write_new
from claview.b.document_corpus import (
    PROFILES,
    audit_catalog,
    build_document_corpus,
    verify_information,
)
from claview.b.metadata import MetadataStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/search_catalog.json"))
    parser.add_argument("--metadata", type=Path, default=Path("data/raw/boK_meta_raw.jsonl"))
    parser.add_argument(
        "--mappings", type=Path, default=Path("data/mappings/runtime/search_mappings.json")
    )
    parser.add_argument(
        "--definitions", type=Path, default=Path("data/catalog/search_definitions.json")
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    audit = audit_catalog(json.loads(args.catalog.read_text(encoding="utf-8-sig")))
    write_new(args.output / "catalog-audit.json", audit)
    if audit["errors"]:
        raise ValueError("invalid_official_catalog")
    store = MetadataStore(args.catalog, args.metadata)
    report = {}
    for profile in PROFILES:
        start = time.perf_counter()
        corpus = build_document_corpus(store, args.mappings, args.definitions, profile)
        verification = verify_information(store, corpus, args.definitions)
        if not verification["raw_preservation_verified"]:
            raise ValueError("document_information_loss")
        regenerated = build_document_corpus(store, args.mappings, args.definitions, profile)
        if regenerated != corpus:
            raise ValueError("nondeterministic_document_generation")
        path = args.output / f"{profile}.json"
        write_new(path, corpus)
        write_new(args.output / f"{profile}.manifest.json", corpus["manifest"])
        report[profile] = {
            "corpus_id": corpus["corpus_id"],
            "chunk_count": len(corpus["documents"]),
            "bytes": path.stat().st_size,
            "generation_and_check_ms": round((time.perf_counter() - start) * 1000, 3),
            "deterministic": True,
            "information": verification,
        }
        print(f"{profile}: {len(corpus['documents'])} chunks, verified", flush=True)
    write_new(args.output / "generation-report.json", report)


if __name__ == "__main__":
    main()
