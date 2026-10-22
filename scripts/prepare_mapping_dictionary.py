"""Migrate the retained review snapshot into one source-bound runtime dictionary."""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator

from claview.a_eval_contracts import read_json, write_new
from claview.contracts import fingerprint
from claview.mappings import SCHEMA, VERSION, official_fields, text_key
from claview.metadata import MetadataStore

ROOT = Path(__file__).resolve().parents[1]


def build(store, legacy_path):
    old = read_json(legacy_path)
    source = {
        "catalog": store.catalog_snapshot,
        "metadata": store.snapshot,
        "legacy": fingerprint(legacy_path),
    }
    fields = official_fields(store)
    concepts, rows = {}, []

    def concept(name):
        key = "concept-" + hashlib.sha256(text_key(name).encode()).hexdigest()[:12]
        if key not in concepts:
            evidence = []
            for doc_id, values in fields.items():
                for field, quote in values.items():
                    if text_key(name) and text_key(name) in text_key(quote):
                        evidence.append({"doc_id": doc_id, "field": field, "quote": quote})
                        break
            concepts[key] = {
                "concept_id": key,
                "canonical_name": name,
                "search_terms": [name] if evidence else [],
                "evidence": evidence,
            }
        return key

    # Explicitly authored condition translations remain drafts, not human approvals.
    structured = {
        "M002": [["경제", "경기", "지수", "한국은행"]],
        "M007": [["성장률", "실질", "명목", "국내", "경제", "연간", "분기"]],
        "M008": [["소득", "국민", "경제", "연간", "분기"]],
        "M018": [["지역별"]],
        "M027": [["매출액", "매출액가중"], ["실적"]],
        "M044": [["소비자", "심리", "지수"]],
        "M054": [["명목"], ["잔액"]],
        "M058": [["지급수단별", "지급수단"]],
        "M059": [["지급수단별", "지급수단"]],
        "M063": [["M2", "통화"]],
        "M064": [["월별", "월간"], ["M2", "통화"]],
        "M069": [["금리", "이자율"]],
        "M070": [["금리", "이자율"]],
        "M074": [["잔액", "잔액기준"], ["금리", "이자율"]],
    }
    exclusions = {
        "M069": [["이자액", "납부액"]],
        "M070": [["이자액", "납부액"]],
        "M064": [["평균", "평잔"]],
        "M074": [["신규", "신규취급액"]],
    }
    for original in old["mappings"]:
        identity = original["mapping_id"]
        relation = (
            "spelling_variant"
            if original.get("normalization_only")
            else "related_concept"
            if original["relation"] == "related"
            else "definition"
            if original["relation"] == "criterion"
            else "abbreviation"
            if identity in {"M002", "M007", "M008", "M018", "M027", "M044", "M070"}
            else "alias"
        )
        resolved = identity in structured or bool(original.get("normalization_only"))
        rows.append(
            {
                "mapping_id": identity,
                "expression": original["input_expression"],
                "concept_id": concept(
                    "국내총생산" if identity == "M007" else original["canonical_expression"]
                ),
                "alternative_concept_ids": [],
                "relation": relation,
                "scope": {"org_ids": [original["scope"]["org_id"]], "table_ids": []},
                "conditions": [{"any_positive": terms} for terms in structured.get(identity, [])],
                "exclusions": [{"any_positive": terms} for terms in exclusions.get(identity, [])],
                "condition_status": "structured" if resolved else "unresolved",
                "legacy_condition": original.get("condition", ""),
                "source_version": source,
                "review_status": "draft",
                "reviewer": None,
                "provenance": {"kind": "retained_review_migration", "source_mapping_id": identity},
            }
        )
    for identity, expression, candidates in [
        ("M077", "가계빚", ["가계신용", "가계대출"]),
        ("M078", "물가", ["생산자물가지수", "수입물가지수", "국내공급물가지수"]),
        ("M079", "금리", ["예금은행 대출금리", "예금은행 수신금리"]),
    ]:
        ids = [concept(name) for name in candidates]
        rows.append(
            {
                "mapping_id": identity,
                "expression": expression,
                "concept_id": ids[0],
                "alternative_concept_ids": ids[1:],
                "relation": "ambiguous",
                "scope": {"org_ids": ["301"], "table_ids": []},
                "conditions": [],
                "exclusions": [],
                "condition_status": "unresolved",
                "legacy_condition": "Meaning clarification required",
                "source_version": source,
                "review_status": "draft",
                "reviewer": None,
                "provenance": {"kind": "scope_requirement_draft", "source_mapping_id": "none"},
            }
        )
    payload = {
        "schema_version": VERSION,
        "source_version": source,
        "concepts": list(concepts.values()),
        "mappings": rows,
    }
    Draft202012Validator(SCHEMA).validate(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/mappings/runtime")
    args = parser.parse_args()
    legacy = ROOT / "data/mappings/review/검색용_의미매핑_초안.json"
    store = MetadataStore(
        ROOT / "data/catalog/search_catalog.json", ROOT / "data/raw/boK_meta_raw.jsonl"
    )
    payload = build(store, legacy)
    args.output.mkdir(parents=True, exist_ok=False)
    write_new(args.output / "search_mappings.json", payload)
    write_new(args.output / "schema.json", SCHEMA)
    source_csvs = {}
    for name in ("통계어사전_후보.csv", "통계어사전_우선순위.csv"):
        path = legacy.parent / name
        with path.open(encoding="utf-8-sig", newline="") as handle:
            source_csvs[name] = {
                "rows": sum(1 for _ in csv.DictReader(handle)),
                "sha256": fingerprint(path),
                "disposition": "retained_review_source_not_auto_approved",
            }
    coverage = Counter(e["doc_id"] for c in payload["concepts"] for e in c["evidence"])
    write_new(
        args.output / "review_queue.json",
        {
            "source_csvs": source_csvs,
            "source_version": payload["source_version"],
            "legacy_rows": 76,
            "current_rows": len(payload["mappings"]),
            "human_approved": 0,
            "legacy_table_scope_policy": (
                "old example IDs retained in immutable source; runtime scope follows "
                "official concept evidence, never gold IDs"
            ),
            "concept_coverage_by_document": dict(sorted(coverage.items())),
            "checks": [
                "relation and equivalence",
                "positive and negative counterexamples",
                "structured conditions and exclusions",
                "scope and other senses",
                "official source version",
                "real reviewer identity before approval",
            ],
            "rows": [
                {
                    "mapping_id": r["mapping_id"],
                    "review_status": r["review_status"],
                    "condition_status": r["condition_status"],
                    "reviewer": None,
                    "concept_has_evidence": bool(
                        next(c for c in payload["concepts"] if c["concept_id"] == r["concept_id"])[
                            "evidence"
                        ]
                    ),
                }
                for r in payload["mappings"]
            ],
        },
    )
    print(
        {
            "mappings": len(payload["mappings"]),
            "concepts": len(payload["concepts"]),
            "review_status": dict(Counter(r["review_status"] for r in payload["mappings"])),
            "documents_with_concept_evidence": len(coverage),
        }
    )


if __name__ == "__main__":
    main()
