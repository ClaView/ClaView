"""Deterministic official-source checks; draft experiment, never human approval."""

from __future__ import annotations

import copy
import json

from .contracts import StageFailure, fingerprint
from .mappings import MappingDictionary, official_fields, text_key

VERSION = "claview-a1-mapping-review/1"
ACTOR = "codex_deterministic_source_check"


def source_review(store, source_path):
    dictionary = MappingDictionary(store, source_path)
    if dictionary.legacy_rows or not dictionary.rows:
        raise StageFailure("a1_review_requires_v2_dictionary")
    original = json.loads(source_path.read_text(encoding="utf-8-sig"))
    fields = official_fields(store)
    reviews, selected = [], []
    for row in dictionary.rows:
        concept = dictionary.concepts[row["concept_id"]]
        witnesses = []
        check = None
        if (
            not dictionary.invalid[row["mapping_id"]]
            and not row["alternative_concept_ids"]
            and row["condition_status"] == "structured"
        ):
            if row["relation"] == "spelling_variant" and text_key(row["expression"]) == text_key(
                concept["canonical_name"]
            ):
                check = "spacing_only"
                witnesses = [
                    e
                    for e in concept["evidence"]
                    if text_key(concept["canonical_name"])
                    in text_key(fields[e["doc_id"]][e["field"]])
                ]
            elif (
                row["relation"] == "abbreviation"
                and row["expression"].isascii()
                and row["expression"].isalpha()
            ):
                check = "acronym_and_canonical_joint_quote"
                witnesses = [
                    e
                    for e in concept["evidence"]
                    if text_key(row["expression"]) in text_key(e["quote"])
                    and text_key(concept["canonical_name"]) in text_key(e["quote"])
                ]
        eligible = bool(witnesses) and row["review_status"] == "draft"
        if eligible:
            selected.append(row["mapping_id"])
        reviews.append(
            {
                "mapping_id": row["mapping_id"],
                "expression": row["expression"],
                "actor": ACTOR,
                "human_review": False,
                "review_status": row["review_status"],
                "operational_approval": False,
                "experiment_eligible": eligible,
                "check": check if witnesses else None,
                "reason": "official_string_relation_checked; human_semantic_review_pending"
                if eligible
                else dictionary.invalid[row["mapping_id"]]
                or "relation_not_proven_by_current_official_quote; human_review_pending",
                "witnesses": witnesses,
                "scope": row["scope"],
                "conditions": row["conditions"],
                "exclusions": row["exclusions"],
            }
        )
    subset = copy.deepcopy(original)
    subset["mappings"] = [r for r in subset["mappings"] if r["mapping_id"] in selected]
    concept_ids = {r["concept_id"] for r in subset["mappings"]}
    subset["concepts"] = [c for c in subset["concepts"] if c["concept_id"] in concept_ids]
    review = {
        "schema_version": VERSION,
        "actor": ACTOR,
        "human_review": False,
        "operational_approval": False,
        "source_sha256": fingerprint(source_path),
        "source_version": original["source_version"],
        "selected_mapping_ids": selected,
        "reviews": reviews,
    }
    return subset, review


def load_experiment(store, source_path, review_path):
    """Recheck the source, audit and dictionary together; no broad draft-policy switch."""
    review = json.loads(review_path.read_text(encoding="utf-8-sig"))
    expected_subset, expected_review = source_review(store, source_path)
    expected = {**expected_review, "dictionary_file": "dictionary.json"}
    dictionary_path = review_path.parent / "dictionary.json"
    if (
        review != expected
        or json.loads(dictionary_path.read_text(encoding="utf-8-sig")) != expected_subset
    ):
        raise StageFailure("a1_mapping_review_mismatch")
    dictionary = MappingDictionary(store, dictionary_path, "draft_experiment")
    if any(dictionary.invalid.values()):
        raise StageFailure("a1_mapping_review_stale")
    return dictionary


def write_review(store, source_path, output):
    subset, review = source_review(store, source_path)
    output.mkdir(parents=True, exist_ok=False)
    for name, payload in (
        ("dictionary.json", subset),
        ("review.json", {**review, "dictionary_file": "dictionary.json"}),
    ):
        with (output / name).open("x", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=False, indent=2)
            target.write("\n")
    return review
