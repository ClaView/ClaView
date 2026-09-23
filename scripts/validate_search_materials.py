"""Inspect frozen search drafts offline; this does not approve their semantics."""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "catalog": "data/samples/search-review/검색문서_기본349건.json",
    "details": "data/samples/search-review/검색문서_상세16건.json",
    "mappings": "data/mappings/review/검색용_의미매핑_초안.json",
    "cases": "eval/goldenset/review/검색평가_초안_48건.json",
}


def inspect(root=ROOT):
    files = {key: root / path for key, path in FILES.items()}
    loaded = {key: json.loads(path.read_text(encoding="utf-8-sig")) for key, path in files.items()}
    catalog = loaded["catalog"]["documents"]
    details = loaded["details"]["documents"]
    mappings = loaded["mappings"]["mappings"]
    cases = loaded["cases"]["cases"]
    issues = []

    def require(condition, message):
        if not condition:
            issues.append(message)

    for rows, field in (
        (catalog, "table_id"),
        (details, "table_id"),
        (mappings, "mapping_id"),
        (cases, "id"),
    ):
        ids = [row.get(field) for row in rows]
        require(all(ids) and len(set(ids)) == len(ids), f"Invalid/duplicate {field}")

    by_id = {row["table_id"]: row for row in catalog}
    detail_ids = {row["table_id"] for row in details}
    with (root / "data/catalog/boK_tables.csv").open(encoding="utf-8-sig", newline="") as f:
        base = {row["TBL_ID"]: row for row in csv.DictReader(f)}
    require(set(base) == set(by_id), "Catalog table ID set differs from shared CSV")
    title_differences = [
        key for key in set(base) & set(by_id) if base[key]["TBL_NM"] != by_id[key]["title"]
    ]
    require(not title_differences, f"Catalog title mismatch: {title_differences}")
    for row in catalog:
        require(row.get("org_id") == "301", f"Wrong organization: {row['table_id']}")
        require(row.get("api_verified") is False, "Snapshot API readiness changed")
    for doc in details:
        key = doc["table_id"]
        require(key in by_id, f"Unknown detail: {key}")
        if key in by_id:
            require(doc["title"] == by_id[key]["title"], f"Detail title: {key}")
            require(doc["catalog_path"] == by_id[key]["path"], f"Detail path: {key}")
        require(set(doc.get("related_table_ids", [])) <= set(by_id), f"Related IDs: {key}")
        binding = doc["api_binding"]
        require(
            binding["item_code"] is None and binding["dimension_codes"] is None,
            f"Frozen draft contains executable codes: {key}",
        )
        require(
            binding["verified_api_request"] is False and doc["readiness"]["api_execution"] is False,
            f"API readiness: {key}",
        )
        observation = doc["official_observations"]
        require(
            all(
                observation.get(field)
                for field in ("period_display", "dimension_labels", "displayed_member_examples")
            ),
            f"Missing observations: {key}",
        )
    for rule in mappings:
        key = rule["mapping_id"]
        require(set(rule["scope"]["table_ids"]) <= detail_ids, f"Mapping scope: {key}")
        require(set(rule.get("source_table_ids", [])) <= set(by_id), f"Mapping source: {key}")
        require(rule["relation"] in {"alias", "related", "criterion"}, f"Relation: {key}")
        require(
            all(
                rule.get(field)
                for field in ("input_expression", "canonical_expression", "condition")
            ),
            f"Incomplete mapping: {key}",
        )
        require(
            rule["auto_select_table"] is False and rule["status"] == "draft_review_required",
            f"Approval changed: {key}",
        )
    for case in cases:
        key = case["id"]
        require(case["evidence"] in detail_ids, f"Case evidence: {key}")
        candidates = case.get("expected_candidate_table_ids", []) + case.get(
            "acceptable_candidate_table_ids", []
        )
        require(set(candidates) <= set(by_id), f"Case candidates: {key}")
        require(case["execution_status"] == "not_run", f"Execution status changed: {key}")
        require(
            bool(case.get("query")) and bool(case.get("expected_action")), f"Incomplete case: {key}"
        )
    golden_text = (root / "eval/goldenset/골든셋_초안_5건.md").read_text(encoding="utf-8")
    golden_questions = [
        line[2:].strip() for line in golden_text.splitlines() if line.startswith("> ")
    ]
    exact_matches = [
        {"case_id": row["id"], "query": row["query"]}
        for row in cases
        if row["query"] in golden_questions
    ]
    return {
        "validation_kind": "offline_structure_and_reference_only",
        "counts": {
            "catalog": len(catalog),
            "details": len(details),
            "categories": len({d["category"] for d in details}),
            "mappings": len(mappings),
            "normalization_only": sum(bool(m.get("normalization_only")) for m in mappings),
            "relations": dict(Counter(m["relation"] for m in mappings)),
            "cases": len(cases),
            "case_types": dict(Counter(c["type"] for c in cases)),
        },
        "catalog_title_differences": title_differences,
        "exact_question_matches_with_five_case_draft": exact_matches,
        "files": [
            {"path": FILES[key], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for key, path in files.items()
        ],
        "issues": issues,
        "passed": not issues,
        "api_requests_tested": 0,
        "retrieval_cases_executed": 0,
        "semantic_review": "pending",
    }


if __name__ == "__main__":
    result = inspect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
