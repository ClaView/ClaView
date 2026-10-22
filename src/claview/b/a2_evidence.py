"""Source-bound A2 checks; atomic availability is not whole-question fulfillment."""

from __future__ import annotations

import copy
import re
from collections import defaultdict

from .a1_query import SERIES
from .a_conditions import assess_candidate
from .a_semantics import AXES, BasisEvidence, choices_in
from .contracts import StageFailure
from .document_corpus import (
    build_document_corpus,
    dereference,
    select_model_evidence,
    source_entry,
)
from .mappings import text_key
from .metadata import FREQUENCIES, normalize_period, period_number
from .search import grams
from .search_documents import digest

VERSION = "claview-a2-evidence/1"
PERIOD_KINDS = {"period_point", "period_range", "relative_period", "open_period"}
UNIT = r"(?:십억|백만|천만|백억|천억|억|조|만|천)?(?:원|달러)|%|퍼센트|명|건"
UNIT_PATTERN = re.compile(
    rf"(?P<before>{UNIT})\s*단위|단위\s*(?:는|:|=)?\s*(?P<after>{UNIT})(?![가-힣])"
)


def validate_question(question):
    sources = {s["source_id"]: s["text"] for s in question["sources"]}
    if sources.get("original") != question["original_query"]:
        raise StageFailure("a2_original_source_mismatch")
    scopes = {s["scope_id"] for s in question["scopes"]}
    ids = [r["requirement_id"] for r in question["requirements"]]
    if len(set(ids)) != len(ids):
        raise StageFailure("a2_duplicate_requirement")
    for requirement in question["requirements"]:
        if not set(requirement["scope_ids"]) <= scopes or not requirement["source_refs"]:
            raise StageFailure("a2_requirement_source_invalid")
        for ref in requirement["source_refs"]:
            text = sources.get(ref["source_id"], "")
            if (
                not (0 <= ref["start"] < ref["end"] <= len(text))
                or text[ref["start"] : ref["end"]] != ref["quote"]
            ):
                raise StageFailure("a2_requirement_source_invalid")


def requirements_for(question):
    """Keep A1 identity and add only explicit display-unit requests with source offsets."""
    validate_question(question)
    requirements = copy.deepcopy(question["requirements"])
    for scope in question["scopes"]:
        for match in UNIT_PATTERN.finditer(scope["text"]):
            requirements.append(
                {
                    "requirement_id": f"a2-unit-{len(requirements)}",
                    "kind": "unit",
                    "value": match["before"] or match["after"],
                    "scope_ids": [scope["scope_id"]],
                    "status": "explicit",
                    "polarity": "positive",
                    "target": {"kind": "request_scope", "binding": "unresolved"},
                    "source_refs": [
                        {
                            "source_id": scope["source_id"],
                            "start": scope["start"] + match.start(),
                            "end": scope["start"] + match.end(),
                            "quote": match.group(),
                        }
                    ],
                }
            )
    return requirements


class Citations:
    def __init__(self, store, key):
        self.store, self.key, self.entries = store, key, {}
        self.records = {"catalog": store.catalog[key], "metadata": store.records.get(key, {})}
        self.hashes = {source: digest(record) for source, record in self.records.items()}

    def add(self, source, pointer):
        record = self.records[source]
        try:
            value = dereference(record, pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        entry = source_entry(source, pointer, value, pointer, self.hashes[source])
        citation_id = "e-" + digest([source, pointer, entry["ref"]["record_hash"]])[:16]
        self.entries[citation_id] = {
            "evidence_id": citation_id,
            "doc_id": self.key,
            "source": source,
            "pointer": pointer,
            "quote": entry["quote"],
            "record_hash": entry["ref"]["record_hash"],
            "value_hash": entry["ref"]["value_hash"],
            "source_snapshot": self.store.catalog_snapshot
            if source == "catalog"
            else self.store.snapshot,
        }
        return citation_id

    def context(self, context):
        refs = []
        pointer = context["source_field"]
        if pointer == "catalog.title":
            refs.append(self.add("catalog", "/title"))
        else:
            refs.extend(self.add("metadata", r["source_field"]) for r in context["branch_evidence"])
            refs.append(self.add("metadata", pointer))
            if context.get("unit"):
                refs.append(self.add("metadata", context["unit_evidence"]["source_field"]))
        return list(dict.fromkeys(r for r in refs if r))


def context_matches(context, requirement, title):
    text = title + " " + context.get("branch_text", context["source_quote"])
    kind, value = requirement["kind"], requirement["value"]
    if kind in AXES:
        return choices_in(text, kind, context.get("unit")) == {value}
    if kind == "series_basis":
        return any(
            text_key(term) in text_key(text) for term, choice in SERIES.items() if choice == value
        )
    if kind == "unit":
        return text_key(context.get("unit") or "") == text_key(value)
    return False


class A2Evidence:
    def __init__(self, search, *, basis=None, corpus=None):
        self.search, self.store = search, search.store
        self.basis = basis or BasisEvidence(self.store)
        self._corpus = corpus
        self._documents = None

    def describe(self):
        return {
            "version": VERSION,
            "mode": "evidence_only",
            "ranking_changed": False,
            "auto_accept": False,
        }

    def _select_documents(self, query, key):
        if self._documents is None:
            if self._corpus is None:
                self._corpus = build_document_corpus(self.store, profile="D2")
            self._documents = defaultdict(list)
            for doc in self._corpus["documents"]:
                self._documents[doc["doc_id"]].append(doc)
        # Reuse the existing whole-chunk budget selector, with a pre-indexed table view.
        local = {"corpus_id": self._corpus["corpus_id"], "documents": self._documents[key]}
        selected = select_model_evidence(
            local, query, [key], max_candidates=1, max_chunks=3, per_table=3, max_chars=14000
        )
        return {
            "corpus_id": selected["corpus_id"],
            "chunk_ids": [r["chunk_id"] for r in selected["selected"]],
            "serialized_chars": selected["serialized_chars"],
            "omissions": selected["omissions"],
            "selected_locations": sorted(
                {
                    r["pointer"]
                    for doc in selected["selected"]
                    for r in doc["source_refs"]
                    if r["source"] == "metadata"
                }
            ),
            "purpose": "relevance_locator_only; decisive_checks_use_full_raw_fields",
        }

    def _period_check(self, requirement, scope_requirements, key, citations):
        raw = self.store.records.get(key, {}).get("meta", {}).get("PRD", [])
        refs = [citations.add("metadata", f"/meta/PRD/{i}") for i in range(len(raw))]
        refs = [ref for ref in refs if ref]
        kind = requirement["kind"]
        if requirement["status"] != "explicit" or kind in {"relative_period", "open_period"}:
            return "unknown", "b_period_policy_or_query_clarification_required", refs, False
        if not raw:
            return "unknown", "metadata_refresh_required", refs, True
        frequencies = {
            r["value"]
            for r in scope_requirements
            if r["kind"] == "frequency"
            and r["status"] == "explicit"
            and r["polarity"] == "positive"
        }
        if kind == "frequency":
            if requirement["polarity"] != "positive" or len(frequencies) != 1:
                return "unknown", "frequency_scope_unresolved", refs, False
            available = {FREQUENCIES.get(r.get("PRD_SE")) for r in raw if isinstance(r, dict)}
            if None in available or not available:
                return "unknown", "metadata_refresh_required", refs, True
            state = "match" if requirement["value"] in available else "conflict"
            return (
                state,
                "cached_native_frequency_available"
                if state == "match"
                else "cached_native_frequency_mismatch",
                refs,
                False,
            )
        value = requirement["value"]
        begin, end = (value["start"], value["end"]) if kind == "period_range" else (value, value)
        if len(frequencies) != 1:
            return "unknown", "frequency_binding_required_for_period", refs, False
        frequency = next(iter(frequencies))

        def endpoint(point, last):
            precision = "Q" if point["quarter"] else "M" if point["month"] else "Y"
            if precision != "Y" and precision != frequency:
                raise StageFailure("period_precision_requires_b_conversion")
            if frequency == "Y":
                return str(point["year"])
            part = point["quarter"] if frequency == "Q" else point["month"]
            part = part or ((4 if frequency == "Q" else 12) if last else 1)
            return f"{point['year']:04d}{part:02d}"

        try:
            lo, hi = (
                period_number(endpoint(begin, False), frequency),
                period_number(endpoint(end, True), frequency),
            )
            bounds = {
                (
                    period_number(normalize_period(r["STRT_PRD_DE"], frequency), frequency),
                    period_number(normalize_period(r["END_PRD_DE"], frequency), frequency),
                )
                for r in raw
                if FREQUENCIES.get(r.get("PRD_SE")) == frequency
            }
        except StageFailure as exc:
            if exc.code == "period_precision_requires_b_conversion":
                return "unknown", exc.code, refs, False
            return "unknown", "period_metadata_incomplete", refs, True
        except (KeyError, TypeError, AttributeError):
            return "unknown", "period_precision_or_metadata_incomplete", refs, True
        if len(bounds) > 1:
            return "unknown", "coverage_varies_by_record", refs, True
        if bounds and all(start <= lo <= hi <= finish for start, finish in bounds):
            return "match", "cached_bounds_cover_request_not_observations", refs, False
        return "unknown", "metadata_refresh_required", refs, True

    def _lexical_contexts(self, query, key, citations, selected_locations):
        contexts = self.basis.contexts[key]
        q = grams(query)
        ranked = sorted(
            contexts,
            key=lambda c: (
                -len(q & grams(c.get("branch_text", c["source_quote"]))),
                c["source_field"] not in selected_locations,
                c["source_field"],
            ),
        )
        chosen = [c for c in ranked if q & grams(c.get("branch_text", c["source_quote"]))][:3]
        result = [
            {
                "kind": "lexical_presence_only",
                "context": c["context"],
                "evidence_ids": citations.context(c),
                "provides_requested_statistic": "unknown",
            }
            for c in chosen
        ]
        cat_path = citations.add("catalog", "/path")
        if cat_path:
            result.append(
                {
                    "kind": "catalog_classification_only",
                    "evidence_ids": [cat_path],
                    "provides_requested_statistic": "unknown",
                }
            )
        notes = self.store.records.get(key, {}).get("meta", {}).get("CMMT", [])
        ranked_notes = sorted(
            enumerate(notes), key=lambda p: -len(q & grams(str(p[1].get("CMMT_DC", ""))))
        )
        for index, row in ranked_notes[:2]:
            if q & grams(str(row.get("CMMT_DC", ""))):
                ref = citations.add("metadata", f"/meta/CMMT/{index}/CMMT_DC")
                result.append(
                    {
                        "kind": "note_requires_semantic_reading",
                        "evidence_ids": [ref],
                        "provides_requested_statistic": "unknown",
                    }
                )
        return result

    def assess_table(self, question, requirements, key):
        if key not in self.store.catalog:
            raise StageFailure("a2_non_catalog_candidate")
        citations = Citations(self.store, key)
        title = self.store.catalog[key]["title"]
        scopes = {s["scope_id"]: s for s in question["scopes"]}
        grouped = {
            scope_id: [r for r in requirements if scope_id in r["scope_ids"]] for scope_id in scopes
        }
        basis = {
            scope_id: self.basis.assess(scope["text"], key) for scope_id, scope in scopes.items()
        }
        legacy = {
            scope_id: assess_candidate(scope["text"], key, self.search)
            for scope_id, scope in scopes.items()
        }
        checks = []
        for requirement in requirements:
            kind, scope_id = requirement["kind"], requirement["scope_ids"][0]
            state, reason, refs, refresh = "unknown", "semantic_requirement_unresolved", [], False
            responsibility = "A_table_fit"
            if kind in PERIOD_KINDS or kind in {"frequency", "unit"}:
                responsibility = "A_compatibility_then_B_conditions"
            if kind in PERIOD_KINDS or kind == "frequency":
                state, reason, refs, refresh = self._period_check(
                    requirement, grouped[scope_id], key, citations
                )
            elif kind in AXES and requirement["status"] == "explicit":
                same_axis = {
                    r["value"]
                    for r in grouped[scope_id]
                    if r["kind"] == kind and r["polarity"] == "positive"
                }
                check = next(
                    (
                        c
                        for c in basis[scope_id]["checks"]
                        if c["axis"] == kind and c["choice"] == requirement["value"]
                    ),
                    None,
                )
                if check and len(same_axis) <= 1:
                    state, reason = check["state"], check["reason"]
                    for context in check["official_evidence"]:
                        refs.extend(citations.context(context))
                    if state == "conflict":
                        refs.append(citations.add("catalog", "/title"))
                    if state == "match":
                        reason = "atomic_basis_mentioned_target_binding_unverified"
                else:
                    reason = "multiple_basis_targets_unresolved"
            elif (
                kind in {"series_basis", "unit"}
                and requirement["status"] == "explicit"
                and requirement["polarity"] == "positive"
            ):
                witnesses = [
                    c for c in self.basis.contexts[key] if context_matches(c, requirement, title)
                ]
                for witness in witnesses[:3]:
                    refs.extend(citations.context(witness))
                if refs:
                    state, reason = "match", "atomic_metadata_available_target_binding_unverified"
                else:
                    reason = (
                        "unit_binding_or_conversion_requires_B"
                        if kind == "unit"
                        else "basis_not_explicitly_supported"
                    )
            elif kind in {"composition", "surface_list", "exclusion"}:
                reason = "target_binding_or_composition_requires_A_review"
            elif kind == "request":
                reason = "literal_relatedness_does_not_prove_required_statistic"
            refs = list(dict.fromkeys(r for r in refs if r))
            if state != "unknown" and not refs:
                state, reason = "unknown", "official_evidence_missing"
            checks.append(
                {
                    "requirement_id": requirement["requirement_id"],
                    "kind": kind,
                    "state": state,
                    "reason": reason,
                    "scope_ids": requirement["scope_ids"],
                    "source_refs": requirement["source_refs"],
                    "responsibility": responsibility,
                    "evidence_ids": refs,
                    "metadata_refresh_needed": refresh,
                    "semantic_fit_verified": False,
                    "verification_scope": "cached_table_compatibility"
                    if kind in PERIOD_KINDS | {"frequency"}
                    else "atomic_metadata_only",
                }
            )
        joint = []
        for scope_id, scope in scopes.items():
            positive = [
                r
                for r in grouped[scope_id]
                if r["kind"] in set(AXES) | {"series_basis", "unit"}
                and r["status"] == "explicit"
                and r["polarity"] == "positive"
            ]
            witnesses = []
            for context in self.basis.contexts[key]:
                branch = context["context"]
                # A title by itself and an incomplete branch cannot witness a series.
                if not isinstance(branch, dict) or branch["path_status"] != "complete":
                    continue
                negatives = [
                    r
                    for r in grouped[scope_id]
                    if r["kind"] in AXES and r["polarity"] == "negative"
                ]
                excluded = any(
                    r["value"]
                    in choices_in(
                        title + " " + context.get("branch_text", context["source_quote"]),
                        r["kind"],
                        context.get("unit"),
                    )
                    for r in negatives
                )
                if (
                    positive
                    and not excluded
                    and all(context_matches(context, r, title) for r in positive)
                ):
                    witnesses.append(context)
            joint.append(
                {
                    "scope_id": scope_id,
                    "state": "match" if witnesses else "unknown",
                    "reason": "same_branch_atomic_conditions_only"
                    if witnesses
                    else "no_single_complete_branch_witness",
                    "requirement_ids": [r["requirement_id"] for r in positive],
                    "witnesses": [
                        {"context": c["context"], "evidence_ids": citations.context(c)}
                        for c in witnesses[:2]
                    ],
                    "omitted_witnesses": max(0, len(witnesses) - 2),
                    "target_measure_unit_jointly_verified": False,
                    "series_combination_verified": False,
                }
            )
        document_selection = self._select_documents(question["effective_query"], key)
        related = self._lexical_contexts(
            question["effective_query"], key, citations, document_selection["selected_locations"]
        )
        metadata_issues = sorted(
            {c["reason"] for c in checks if c["metadata_refresh_needed"]}
            | {
                c["reason"]
                for value in legacy.values()
                for c in value["checks"]
                if c["field"] == "metadata" and c["state"] == "unknown"
            }
            | {
                "metadata_path_" + c["context"]["path_status"]
                for c in self.basis.contexts[key]
                if isinstance(c["context"], dict) and c["context"]["path_status"] != "complete"
            }
        )
        a_unresolved = [
            c["requirement_id"]
            for c in checks
            if c["responsibility"] == "A_table_fit" and c["state"] != "match"
        ]
        b_pending = [c["requirement_id"] for c in checks if c["responsibility"] != "A_table_fit"]
        conflicts = [c["requirement_id"] for c in checks if c["state"] == "conflict"]
        # A2 does not decide a table set; even all atomic matches leave target semantics open.
        return {
            "doc_id": key,
            "table_name": title,
            "checks": checks,
            "joint_atomic_checks": joint,
            "related_evidence": related,
            "evidence": list(citations.entries.values()),
            "document_selection": document_selection,
            "metadata_issues": metadata_issues,
            "metadata_checks": [
                {"scope_id": scope_id, **check}
                for scope_id, assessment in legacy.items()
                for check in assessment["checks"]
                if check["field"] == "metadata"
            ],
            "metadata_refresh_needed": bool(metadata_issues),
            "a_unresolved_requirement_ids": a_unresolved,
            "b_condition_requirement_ids": b_pending,
            "conflict_requirement_ids": conflicts,
            "whole_request_state": "unknown",
            "whole_request_reason": "target_binding_and_series_combinations_not_verified",
            "api_support": "unverified",
            "observations_verified": False,
            "series_selection": None,
            "meaning_verified": False,
        }

    def compare(self, question, candidate_ids):
        requirements = requirements_for(question)
        by_id = {
            key: self.assess_table(question, requirements, key)
            for key in dict.fromkeys(candidate_ids)
        }
        return {
            "schema_version": VERSION,
            "original_query": question["original_query"],
            "sources": question["sources"],
            "requirements": requirements,
            "by_doc_id": by_id,
            "source_versions": {
                "catalog": self.store.catalog_snapshot,
                "metadata": self.store.snapshot,
            },
            "decision": "evidence_only_awaiting_selection_policy",
            "auto_accept": False,
            "model_calls": 0,
            "statistical_api_calls": 0,
        }


def diagnose_selection(assessment, selected_ids):
    """Inspect a saved choice with no gold answer or expected table count."""
    rows = assessment["by_doc_id"]
    selected = [key for key in dict.fromkeys(selected_ids) if key in rows]
    available = {
        c["requirement_id"]
        for row in rows.values()
        for c in row["checks"]
        if c["state"] == "match" and c["responsibility"] == "A_table_fit"
    }
    represented = {
        c["requirement_id"]
        for key in selected
        for c in rows[key]["checks"]
        if c["state"] == "match" and c["responsibility"] == "A_table_fit"
    }
    return {
        "selected_ids": selected,
        "outside_candidate_ids": sorted(set(selected_ids) - set(rows)),
        "selected_with_conflicts": [
            key for key in selected if rows[key]["conflict_requirement_ids"]
        ],
        "selected_requiring_metadata_refresh": [
            key for key in selected if rows[key]["metadata_refresh_needed"]
        ],
        "atomic_evidence_not_represented": sorted(available - represented),
        "missing_table_determined": False,
        "unnecessary_tables_determined": False,
        "additional_table_necessity": {key: "unknown" for key in selected},
        "reason": "atomic_presence_does_not_establish_required_minimum_table_set",
    }
