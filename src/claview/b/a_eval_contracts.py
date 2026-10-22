"""Versioned, A-only evaluation contracts; no production selection policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

VERSION = "claview-a-eval/1"
DECISIONS = [
    "display_candidates",
    "ask_clarification",
    "out_of_scope",
    "insufficient_evidence",
    "technical_failure",
]
REVIEW_STATES = ["inherited_regression", "draft", "human_reviewed"]
SPLITS = ["regression", "dev", "test_expression", "test_family"]


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def array(items, minimum=0, unique=False):
    return {"type": "array", "items": items, "minItems": minimum, "uniqueItems": unique}


TEXT = {"type": "string", "minLength": 1}
ID = {"type": "string", "pattern": r"^[0-9]+:[A-Za-z0-9_]+$"}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NUMBER = {"type": "number", "minimum": 0}
INPUT_FIELDS = {
    "case_id": TEXT,
    "query": TEXT,
    "query_revision": {"type": "integer", "minimum": 1},
    "prior_turns": array(obj({"role": {"enum": ["user", "assistant"]}, "text": TEXT})),
}
EVIDENCE = obj(
    {
        "source": TEXT,
        "table_id": {"anyOf": [ID, {"type": "null"}]},
        "field": TEXT,
        "quote": TEXT,
        "snapshot_sha256": HASH,
    }
)
GOLD_FIELDS = {
    **INPUT_FIELDS,
    "tags": array(TEXT, 1, True),
    "families": array(TEXT, 1, True),
    "variant_group": TEXT,
    "semantic_group": TEXT,
    "review_status": {"enum": REVIEW_STATES},
    "reviewer": {"anyOf": [TEXT, {"type": "null"}]},
    "review_note": TEXT,
    "decision": {"enum": DECISIONS[:-1]},
    "target_kind": {"enum": ["none", "single", "multiple_required", "equivalent_alternatives"]},
    "table_groups": array(array(ID, 1, True)),
    "clarification_axes": array(TEXT, unique=True),
    "evidence": array(EVIDENCE, 1),
    "rationale": TEXT,
}
USAGE = obj(
    {
        "model_calls": {"type": "integer", "minimum": 0},
        "external_api_calls": {"type": "integer", "minimum": 0},
        "blocked_network_attempts": {"type": "integer", "minimum": 0},
        "b_calls": {"type": "integer", "minimum": 0},
        "billed_cost_usd": {"anyOf": [NUMBER, {"type": "null"}]},
    }
)
PREDICTION = obj(
    {
        **INPUT_FIELDS,
        "decision": {"enum": DECISIONS},
        "clarification_axes": array(TEXT, unique=True),
        "before_ids": array(ID, unique=True),
        "after_ids": array(ID, unique=True),
        "display_order": array(ID, unique=True),
        "error_code": {"anyOf": [TEXT, {"type": "null"}]},
        "elapsed_ms": NUMBER,
        "usage": USAGE,
        "trace": {"type": "object"},
    }
)
ENVELOPE = {
    "schema_version": {"const": VERSION},
    "dataset_id": TEXT,
    "split": {"enum": SPLITS},
}
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "input_case": obj(INPUT_FIELDS),
        "gold_case": obj(GOLD_FIELDS),
        "prediction_case": PREDICTION,
        "inputs": obj({**ENVELOPE, "cases": array(obj(INPUT_FIELDS), 1)}),
        "gold": obj(
            {
                **ENVELOPE,
                "inputs_sha256": HASH,
                "source_sha256": {"type": "object", "additionalProperties": HASH},
                "cases": array(obj(GOLD_FIELDS), 1),
            }
        ),
    },
    "$ref": "#/$defs/gold",
}


def validate(value, kind):
    schema = {**SCHEMA, "$ref": f"#/$defs/{kind}"}
    Draft202012Validator(schema).validate(value)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def inputs_from_gold(gold):
    return {
        **{key: gold[key] for key in ENVELOPE},
        "cases": [{key: case[key] for key in INPUT_FIELDS} for case in gold["cases"]],
    }


def unique_cases(cases):
    keys = [case["case_id"] for case in cases]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_case_id")


def validate_gold(gold, official_ids=None):
    validate(gold, "gold")
    unique_cases(gold["cases"])
    if gold["inputs_sha256"] != digest(inputs_from_gold(gold)):
        raise ValueError("gold_inputs_hash_mismatch")
    for case in gold["cases"]:
        groups = case["table_groups"]
        ids = [key for group in groups for key in group]
        if len(ids) != len(set(ids)):
            raise ValueError("overlapping_alternative_groups")
        if official_ids is not None and not set(ids) <= set(official_ids):
            raise ValueError("gold_id_outside_catalog")
        if (case["decision"] == "display_candidates") != bool(groups):
            raise ValueError("decision_targets_mismatch")
        expected_kind = (
            "none"
            if not groups
            else "multiple_required"
            if len(groups) > 1
            else "equivalent_alternatives"
            if len(groups[0]) > 1
            else "single"
        )
        if case["target_kind"] != expected_kind:
            raise ValueError("target_kind_mismatch")
        if (case["decision"] == "ask_clarification") != bool(case["clarification_axes"]):
            raise ValueError("clarification_axis_mismatch")
        if case["review_status"] == "human_reviewed" and not case["reviewer"]:
            raise ValueError("human_review_requires_reviewer")


def validate_splits(datasets):
    """Keep complete variant/paraphrase bundles together; family holdout is stricter."""
    owners = {"case_id": {}, "variant_group": {}, "semantic_group": {}}
    family_sets = {}
    table_owners = {}
    for data in datasets:
        validate_gold(data)
        split = data["split"]
        family_sets.setdefault(split, set())
        for case in data["cases"]:
            family_sets[split].update(case["families"])
            # Regression and dev are both exposed development material.
            partition = "development" if split in {"regression", "dev"} else split
            for field, mapping in owners.items():
                value = case[field]
                if value in mapping and (field == "case_id" or mapping[value] != partition):
                    raise ValueError(f"split_leakage:{field}")
                mapping[value] = partition
            referenced = {t for group in case["table_groups"] for t in group}
            referenced.update(e["table_id"] for e in case["evidence"] if e["table_id"])
            for table in referenced:
                if table in table_owners and table_owners[table] != partition:
                    raise ValueError("split_leakage:table_variant")
                table_owners[table] = partition
    known = family_sets.get("regression", set()) | family_sets.get("dev", set())
    if family_sets.get("test_family", set()) & (known | family_sets.get("test_expression", set())):
        raise ValueError("split_leakage:family")
    if not family_sets.get("test_expression", set()) <= known:
        raise ValueError("expression_test_requires_known_families")
