"""Source-bound terminology lookup; expansions never replace the original question."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from .contracts import StageFailure, fingerprint

VERSION = "claview-mappings/2"
POLICIES = ("approved", "off", "draft_experiment")
RELATIONS = (
    "alias",
    "abbreviation",
    "spelling_variant",
    "definition",
    "related_concept",
    "ambiguous",
)
EXPANDABLE = {"alias", "abbreviation", "spelling_variant"}
NEGATION = re.compile(
    r"^(?:\s|은|는|이|가|을|를|기준|으로|로)*"
    r"(?:말고|아닌|아니라|아니고|빼고|제외|필요\s*없)"
)
UNCERTAIN = re.compile(r"제외하지|제외할\s*필요|아니지|않|말아|또는|아니면|인지|둘\s*중")


def text_key(value):
    """Only for source matching/deduplication; never mutate the user's question."""
    return re.sub(r"[^\w가-힣]", "", value.casefold())


def mentions(query, expression):
    letters = list(re.sub(r"\s", "", expression))
    if not letters:
        return []
    pattern = r"\s*".join(re.escape(char) for char in letters)
    if letters[0].isascii() and letters[0].isalnum():
        pattern = r"(?<![A-Za-z0-9_])" + pattern
    else:
        pattern = r"(?<![가-힣A-Za-z0-9_])" + pattern
    if letters[-1].isascii() and letters[-1].isalnum():
        pattern += r"(?![A-Za-z0-9_])"
    else:
        pattern += r"(?=$|[\s\W]|은|는|이|가|을|를|의|과|와|로|도|만|부터)"
    return [
        {
            "start": m.start(),
            "end": m.end(),
            "quote": m.group(),
            "negated": bool(NEGATION.match(query[m.end() :])),
        }
        for m in re.finditer(pattern, query, re.IGNORECASE)
    ]


def predicate(query, terms):
    found = [span for term in terms for span in mentions(query, term)]
    if not found or UNCERTAIN.search(query):
        return {"state": "unknown", "spans": found}
    states = {span["negated"] for span in found}
    return {
        "state": "unknown" if len(states) > 1 else "conflict" if True in states else "match",
        "spans": found,
    }


def compatible_scope(query, scope, store):
    """Exclude only explicit, exclusive title-basis conflicts from added retrieval."""
    axes = {
        "price_basis": {"nominal": ["명목"], "real": ["실질"]},
        "loan_basis": {"stock": ["잔액", "잔액기준"], "new": ["신규", "신규취급액"]},
        "currency": {"krw": ["원화"], "usd": ["달러"]},
    }
    kept, rejected = set(scope), []
    for axis, choices in axes.items():
        positive, negative = set(), set()
        for choice, terms in choices.items():
            for term in terms:
                for span in mentions(query, term):
                    (negative if span["negated"] else positive).add(choice)
        if positive & negative:
            continue
        for key in sorted(kept):
            title = text_key(store.catalog[key]["title"])
            present = {
                choice
                for choice, terms in choices.items()
                if any(text_key(term) in title for term in terms)
            }
            conflict = bool(present and present <= negative)
            conflict |= bool(len(positive) == 1 and present and not positive & present)
            if conflict:
                kept.remove(key)
                rejected.append(
                    {
                        "doc_id": key,
                        "axis": axis,
                        "state": "conflict",
                        "source_field": "catalog.title",
                        "source_quote": store.catalog[key]["title"],
                        "positive": sorted(positive),
                        "negative": sorted(negative),
                    }
                )
    return kept, rejected


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": TEXT, "uniqueItems": True}
PREDICATE = object_schema({"any_positive": {**STRINGS, "minItems": 1}})
EVIDENCE = object_schema({"doc_id": TEXT, "field": TEXT, "quote": TEXT})
CONCEPT = object_schema(
    {
        "concept_id": TEXT,
        "canonical_name": TEXT,
        "search_terms": STRINGS,
        "evidence": {"type": "array", "items": EVIDENCE},
    }
)
ROW = object_schema(
    {
        "mapping_id": TEXT,
        "expression": TEXT,
        "concept_id": TEXT,
        "alternative_concept_ids": STRINGS,
        "relation": {"enum": list(RELATIONS)},
        "scope": object_schema({"org_ids": {**STRINGS, "minItems": 1}, "table_ids": STRINGS}),
        "conditions": {"type": "array", "items": PREDICATE},
        "exclusions": {"type": "array", "items": PREDICATE},
        "condition_status": {"enum": ["structured", "unresolved"]},
        "legacy_condition": {"type": "string"},
        "source_version": object_schema({"catalog": TEXT, "metadata": TEXT, "legacy": TEXT}),
        "review_status": {"enum": ["draft", "approved", "rejected"]},
        "reviewer": {"type": ["string", "null"]},
        "provenance": object_schema({"kind": TEXT, "source_mapping_id": TEXT}),
    }
)
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    **object_schema(
        {
            "schema_version": {"const": VERSION},
            "source_version": object_schema({"catalog": TEXT, "metadata": TEXT, "legacy": TEXT}),
            "concepts": {"type": "array", "items": CONCEPT},
            "mappings": {"type": "array", "items": ROW},
        }
    ),
}


def official_fields(store):
    result = {}
    for key, doc in store.catalog.items():
        fields = {"catalog.title": doc["title"], "catalog.path": doc["path"]}
        for i, row in enumerate(store.records.get(key, {}).get("meta", {}).get("ITM", [])):
            if row.get("ITM_NM"):
                fields[f"ITM[{i}].ITM_NM"] = row["ITM_NM"]
        for i, row in enumerate(store.records.get(key, {}).get("meta", {}).get("CMMT", [])):
            if row.get("CMMT_DC"):
                fields[f"CMMT[{i}].CMMT_DC"] = row["CMMT_DC"]
        result[key] = fields
    return result


class MappingDictionary:
    def __init__(self, store, path: Path | None, policy="approved", max_expansions=3):
        if policy not in POLICIES or not 0 <= max_expansions <= 10:
            raise StageFailure("invalid_mapping_policy")
        self.policy, self.max_expansions = policy, max_expansions
        self.snapshot = fingerprint(path) if path else None
        self.rows, self.concepts, self.invalid = [], {}, {}
        self.store = store
        payload = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
        self.version = payload.get("schema_version")
        if self.version != VERSION:
            self.legacy_rows = payload.get("mappings", [])
            return
        self.legacy_rows = []
        Draft202012Validator(SCHEMA).validate(payload)
        self.rows = payload["mappings"]
        self.concepts = {c["concept_id"]: c for c in payload["concepts"]}
        if len(self.concepts) != len(payload["concepts"]) or len(
            {r["mapping_id"] for r in self.rows}
        ) != len(self.rows):
            raise StageFailure("duplicate_mapping_identity")
        fields = official_fields(store)
        for concept in self.concepts.values():
            valid = all(
                fields.get(e["doc_id"], {}).get(e["field"]) == e["quote"]
                for e in concept["evidence"]
            )
            valid &= all(
                any(text_key(term) in text_key(e["quote"]) for e in concept["evidence"])
                for term in concept["search_terms"]
            )
            concept["source_valid"] = bool(
                concept["evidence"] and concept["search_terms"] and valid
            )
        for row in self.rows:
            ids = [row["concept_id"], *row["alternative_concept_ids"]]
            if any(cid not in self.concepts for cid in ids):
                raise StageFailure("unknown_mapping_concept")
            if row["review_status"] == "approved" and not row["reviewer"]:
                raise StageFailure("mapping_approval_requires_reviewer")
            reason = None
            if row["source_version"] != payload["source_version"] or any(
                row["source_version"][name] != value
                for name, value in (
                    ("catalog", store.catalog_snapshot),
                    ("metadata", store.snapshot),
                )
            ):
                reason = "stale_source_version"
            if not set(row["scope"]["table_ids"]) <= set(store.catalog):
                reason = "non_official_scope"
            if not self.concepts[row["concept_id"]]["source_valid"]:
                reason = reason or "official_evidence_missing_or_changed"
            self.invalid[row["mapping_id"]] = reason

    def describe(self):
        rows = self.rows or self.legacy_rows
        counts = Counter(row.get("review_status", row.get("status", "unknown")) for row in rows)
        return {
            "version": self.version,
            "snapshot": self.snapshot,
            "policy": self.policy,
            "max_expansions": self.max_expansions,
            "total": len(rows),
            "review_counts": dict(counts),
            "runtime_conditions": self.version == VERSION,
        }

    def resolve(self, query, allowed=None):
        trace, expansions = [], []
        result = {
            "original_query": query,
            "policy": self.policy,
            "trace": trace,
            "expansions": expansions,
            "dictionary": self.describe(),
        }
        if self.legacy_rows:
            for row in self.legacy_rows:
                spans = mentions(query, row.get("input_expression", ""))
                if spans:
                    trace.append(
                        {
                            "mapping_id": row["mapping_id"],
                            "action": "deferred",
                            "reason": "legacy_requires_structured_review",
                            "source_spans": spans,
                        }
                    )
            return result
        proposals = []
        for row in self.rows:
            spans = mentions(query, row["expression"])
            if not spans:
                continue
            entry = {
                "mapping_id": row["mapping_id"],
                "concept_id": row["concept_id"],
                "alternative_concept_ids": row["alternative_concept_ids"],
                "relation": row["relation"],
                "review_status": row["review_status"],
                "source_spans": spans,
                "action": "deferred",
                "reason": None,
                "condition_checks": [],
                "exclusion_checks": [],
            }
            trace.append(entry)
            reason = self.invalid[row["mapping_id"]]
            if self.policy == "off":
                reason = "dictionary_disabled"
            elif row["review_status"] == "rejected":
                reason = "review_rejected"
                entry["action"] = "rejected"
            elif row["review_status"] != "approved" and self.policy != "draft_experiment":
                reason = reason or "pending_review"
            elif row["relation"] not in EXPANDABLE or row["alternative_concept_ids"]:
                reason = reason or "not_an_equivalent_expression"
            elif row["condition_status"] == "unresolved":
                reason = reason or "condition_not_structured"
            elif UNCERTAIN.search(query):
                reason = reason or "negation_or_alternative_scope_unknown"
            elif any(s["negated"] for s in spans):
                reason = reason or "expression_negated_or_mixed"
                entry["action"] = "rejected"
            if not reason:
                checks = [predicate(query, p["any_positive"]) for p in row["conditions"]]
                exclusions = [predicate(query, p["any_positive"]) for p in row["exclusions"]]
                entry.update(condition_checks=checks, exclusion_checks=exclusions)
                if any(c["state"] == "conflict" for c in checks):
                    reason, entry["action"] = "condition_conflict", "rejected"
                elif any(c["state"] == "unknown" for c in checks):
                    reason = "condition_unknown"
                elif any(c["state"] == "match" for c in exclusions):
                    reason, entry["action"] = "exclusion_matched", "rejected"
                elif any(c["state"] == "unknown" and c["spans"] for c in exclusions):
                    reason = "exclusion_unknown"
            concept = self.concepts[row["concept_id"]]
            scope = {e["doc_id"] for e in concept["evidence"]}
            scope &= {
                key for key in self.store.catalog if key.split(":")[0] in row["scope"]["org_ids"]
            }
            if row["scope"]["table_ids"]:
                scope &= set(row["scope"]["table_ids"])
            if allowed is not None:
                scope &= set(allowed)
            scope, scope_conflicts = compatible_scope(query, scope, self.store)
            entry["scope_conflicts"] = scope_conflicts
            if not scope:
                reason = reason or "scope_empty"
            if reason:
                entry["reason"] = reason
                continue
            terms = [t for t in concept["search_terms"] if text_key(t) not in text_key(query)]
            proposals.append((row, entry, terms, sorted(scope)))
        # Same surface expression with unresolved different concepts must not expand arbitrarily.
        meanings = {}
        for row, _, _, _ in proposals:
            meanings.setdefault(text_key(row["expression"]), set()).add(row["concept_id"])
        seen = set()
        for row, entry, terms, scope in proposals:
            if len(meanings[text_key(row["expression"])]) > 1:
                entry["reason"] = "multiple_applicable_concepts"
                continue
            if not terms:
                entry.update(action="applied", reason="equivalent_text_already_present")
                continue
            expression = query + "\n" + " ".join(terms)
            signature = (expression, tuple(scope))
            if signature in seen:
                entry["reason"] = "duplicate_expansion"
            elif len(expansions) >= self.max_expansions:
                entry["reason"] = "expansion_budget"
            else:
                seen.add(signature)
                entry.update(action="applied", reason="scoped_retrieval_expansion")
                expansions.append(
                    {
                        "expression": expression,
                        "allowed_ids": scope,
                        "mapping_id": row["mapping_id"],
                        "concept_id": row["concept_id"],
                        "source_spans": entry["source_spans"],
                        "experimental": row["review_status"] != "approved",
                    }
                )
        return result


def ingest_proposals(payload, proposals):
    """Accept a mock/model proposal into review only, without trusting claimed approvals."""
    if len(proposals) > 20:
        raise StageFailure("mapping_proposal_budget")
    result = deepcopy(payload)
    concepts = {c["concept_id"] for c in result["concepts"]}
    existing = {(text_key(r["expression"]), r["concept_id"]) for r in result["mappings"]}
    for proposal in proposals:
        if proposal["concept_id"] not in concepts or not 0 < len(proposal["expression"]) <= 100:
            raise StageFailure("invalid_mapping_proposal")
        identity = (text_key(proposal["expression"]), proposal["concept_id"])
        if identity in existing:
            continue
        existing.add(identity)
        row = {
            "mapping_id": f"proposal-{len(result['mappings']) + 1}",
            "expression": proposal["expression"],
            "concept_id": proposal["concept_id"],
            "alternative_concept_ids": [],
            "relation": proposal["relation"],
            "scope": {"org_ids": ["301"], "table_ids": []},
            "conditions": [],
            "exclusions": [],
            "condition_status": "unresolved",
            "legacy_condition": "Model proposal requires independent condition review",
            "source_version": result["source_version"],
            "review_status": "draft",
            "reviewer": None,
            "provenance": {"kind": "model_proposal", "source_mapping_id": "none"},
        }
        result["mappings"].append(row)
    Draft202012Validator(SCHEMA).validate(result)
    return result
