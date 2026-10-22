"""Conservative table-set decisions from exact official designations and A2 evidence.

This bounded grammar is deliberately not a general natural-language semantic resolver.
Only complete, explicitly quoted official requests can qualify for automatic adoption.
"""

from __future__ import annotations

import itertools
import re

from .a2_evidence import PERIOD_KINDS, Citations, context_matches, validate_question
from .a_semantics import AXES, choices_in
from .contracts import StageFailure
from .mappings import text_key

VERSION = "claview-a2-selection/1"
QUOTED = re.compile(r'"([^"\n]+)"|「([^」\n]+)」|“([^”\n]+)”')
# Full consumption is required. Unrecognized words are never silently discarded.
WRAPPERS = re.compile(
    r"공식\s*항목|공식\s*표|통계표|비교해줘|보여줘|조회해줘|알려줘|"
    r"비교|조회|추이|각각|함께|그리고|또한|항목|표|의|을|를|과|와|및|"
    r"부터|까지|년|월|분기|단위로|단위|로|[\s,;:.!?·()]+"
)


def explicit_requests(question, requirements):
    """Resolve a finite grammar, preserving every residual fragment as a blocker."""
    validate_question(question)
    requests, residuals = [], []
    for source in question["sources"]:
        text = source["text"]
        masked = list(text)
        for match in QUOTED.finditer(text):
            label = next(g for g in match.groups() if g is not None)
            designators = list(re.finditer(r"공식\s*항목|통계표|공식\s*표", text[: match.start()]))
            kind = (
                ("item" if "항목" in designators[-1].group() else "table")
                if designators
                else "unbound"
            )
            requests.append(
                {
                    "requirement_id": f"official-{len(requests) + 1}",
                    "label": label,
                    "kind": kind,
                    "source_ref": {
                        "source_id": source["source_id"],
                        "start": match.start(),
                        "end": match.end(),
                        "quote": match.group(),
                    },
                }
            )
            masked[match.start() : match.end()] = " " * (match.end() - match.start())
        for req in requirements:
            if req["kind"] not in set(AXES) | PERIOD_KINDS | {"frequency", "series_basis", "unit"}:
                continue
            if req["status"] != "explicit" or req["polarity"] != "positive":
                continue
            for ref in req["source_refs"]:
                if ref["source_id"] == source["source_id"]:
                    masked[ref["start"] : ref["end"]] = " " * (ref["end"] - ref["start"])
        residue = WRAPPERS.sub("", "".join(masked))
        if residue:
            residuals.append({"source_id": source["source_id"], "text": residue})
    return requests, residuals


def failure_decision(code="a2_decision_failed"):
    return {
        "version": VERSION,
        "route": "unresolved",
        "reason": "technical_failure",
        "failure_code": code,
        "auto_eligible": False,
        "proposed_ids": [],
        "clarification": None,
        "recovery": "reassess_or_direct_selection",
    }


def verify_set(ids, coverage, required):
    """Every chosen table must have a private requirement; a partial cover is never success."""
    ids = list(dict.fromkeys(ids))
    required = set(required)
    represented = set().union(*(set(coverage.get(key, [])) for key in ids))
    unique = {
        key: sorted(
            set(coverage.get(key, []))
            - set().union(*(set(coverage.get(other, [])) for other in ids if other != key))
        )
        for key in ids
    }
    return {
        "all_required_covered": bool(required) and required <= represented,
        "missing_requirements": sorted(required - represented),
        "additional_table_contribution": unique,
        "redundant_ids": [key for key, contribution in unique.items() if not contribution],
        "valid": bool(required) and required <= represented and all(unique.values()),
    }


class TableSetPolicy:
    def __init__(self, evidence):
        self.evidence = evidence
        self.store = evidence.store
        # This is exact official identity lookup, not another ranked search engine.
        self.identities = {}
        for key, cat in self.store.catalog.items():
            self.identities.setdefault(text_key(cat["title"]), []).append((key, None))
            for context in evidence.basis.contexts[key]:
                branch = context["context"]
                if isinstance(branch, dict) and branch["axis"] == "ITEM":
                    self.identities.setdefault(text_key(context["source_quote"]), []).append(
                        (key, context)
                    )

    def _witness(self, key, context, requirements, row):
        if row["metadata_refresh_needed"]:
            return None, "metadata_insufficient"
        conditions = [r for r in requirements if r["kind"] in set(AXES) | {"series_basis", "unit"}]
        refs = Citations(self.store, key)
        title = self.store.catalog[key]["title"]
        title_id = refs.add("catalog", "/title")
        if context is None:
            # A table designation proves identity only; external series conditions need binding.
            for req in conditions:
                if req["kind"] not in AXES or choices_in(title, req["kind"]) != {req["value"]}:
                    return None, "semantic_ambiguity"
        else:
            if context["context"]["path_status"] != "complete":
                return None, "metadata_insufficient"
            if not all(context_matches(context, r, title) for r in conditions):
                # An exclusive opposite official title can eliminate an alternative.
                if any(
                    r["kind"] in AXES
                    and choices_in(title, r["kind"])
                    and r["value"] not in choices_in(title, r["kind"])
                    for r in conditions
                ):
                    return None, "explicit_conflict"
                return None, "semantic_ambiguity"
            refs.context(context)
        if row["conflict_requirement_ids"]:
            return None, "explicit_conflict"
        for check in row["checks"]:
            if check["kind"] in PERIOD_KINDS | {"frequency"} and check["state"] != "match":
                return None, "metadata_insufficient" if check[
                    "metadata_refresh_needed"
                ] else "semantic_ambiguity"
        return {
            "kind": "exact_official_item_in_complete_branch" if context else "exact_official_table",
            "evidence": list(refs.entries.values()),
            "title_evidence_id": title_id,
            "basis_options": {
                axis: sorted(
                    choices_in(
                        title
                        + (
                            " " + context.get("branch_text", context["source_quote"])
                            if context
                            else ""
                        ),
                        axis,
                        context.get("unit") if context else None,
                    )
                )
                for axis in AXES
            },
            "scope": "table_fit_only; no_B_code_selection_or_observation_validation",
        }, None

    def decide(self, question, assessment):
        reqs = assessment["requirements"]
        requests, residuals = explicit_requests(question, reqs)
        result = {
            "version": VERSION,
            "route": "user_confirmation",
            "reason": "semantic_ambiguity",
            "auto_eligible": False,
            "proposed_ids": [],
            "clarification": None,
            "requirements": requests,
            "unparsed_fragments": residuals,
            "coverage": {},
            "witnesses": {},
            "blocked_candidates": [],
            "outside_candidate_ids": [],
            "solutions": [],
            "b_conditions": [r for r in reqs if r["kind"] in PERIOD_KINDS | {"frequency", "unit"}],
            "verification_scope": "bounded_exact_official_designations",
            "model_calls": 0,
            "statistical_api_calls": 0,
        }
        if not assessment["by_doc_id"]:
            result.update(route="unresolved", reason="search_insufficient")
            return result
        if not requests or residuals:
            result["reason_detail"] = "whole_question_meaning_not_verified"
            return result
        if len({r["kind"] for r in requests}) != 1 or requests[0]["kind"] == "unbound":
            result["reason_detail"] = "official_designation_type_or_target_binding_unresolved"
            return result
        # Quoted names can contain basis terms belonging to different independent requests.
        # Only unquoted conditions are global. Binding across clauses is not guessed.
        external = []
        for req in reqs:
            spans = req["source_refs"]
            inside = all(
                any(
                    r["source_ref"]["source_id"] == ref["source_id"]
                    and r["source_ref"]["start"]
                    <= ref["start"]
                    < ref["end"]
                    <= r["source_ref"]["end"]
                    for r in requests
                )
                for ref in spans
            )
            if not inside and req["kind"] not in {"request", "surface_list", "composition"}:
                external.append(req)
        if any(r["status"] != "explicit" or r["polarity"] != "positive" for r in external):
            result["reason_detail"] = "unresolved_or_negative_condition"
            return result
        if any(len({r["value"] for r in external if r["kind"] == axis}) > 1 for axis in AXES):
            result["reason_detail"] = "condition_target_binding_unresolved"
            return result
        # Different temporal clauses cannot be assigned to quoted requests by proximity alone.
        temporal_scopes = {
            s for r in external if r["kind"] in PERIOD_KINDS | {"frequency"} for s in r["scope_ids"]
        }
        if len(temporal_scopes) > 1:
            result["reason_detail"] = "condition_target_binding_unresolved"
            return result
        relevant_checks = {r["requirement_id"] for r in external}
        for request in requests:
            rid = request["requirement_id"]
            for key, context in self.identities.get(text_key(request["label"]), []):
                if (context is None) != (request["kind"] == "table"):
                    continue
                if key not in assessment["by_doc_id"]:
                    result["outside_candidate_ids"].append(key)
                    continue
                row = assessment["by_doc_id"][key]
                scoped_row = {
                    **row,
                    "checks": [c for c in row["checks"] if c["requirement_id"] in relevant_checks],
                    "conflict_requirement_ids": [
                        r for r in row["conflict_requirement_ids"] if r in relevant_checks
                    ],
                }
                witness, failure = self._witness(key, context, external, scoped_row)
                if failure:
                    result["blocked_candidates"].append(
                        {"doc_id": key, "requirement_id": rid, "reason": failure}
                    )
                else:
                    result["coverage"].setdefault(key, []).append(rid)
                    result["witnesses"].setdefault(key, []).append(
                        {"requirement_id": rid, **witness}
                    )
        result["coverage"] = {k: sorted(set(v)) for k, v in result["coverage"].items()}
        result["outside_candidate_ids"] = sorted(set(result["outside_candidate_ids"]))
        required = [r["requirement_id"] for r in requests]
        covered = set().union(*(set(v) for v in result["coverage"].values()))
        result["missing_requirements"] = sorted(set(required) - covered)
        # Unknown official alternatives block a uniqueness claim, even if another table fits.
        unresolved = [
            b
            for b in result["blocked_candidates"]
            if b["reason"] != "explicit_conflict"
            and b["requirement_id"] not in result["coverage"].get(b["doc_id"], [])
        ]
        if result["outside_candidate_ids"] or result["missing_requirements"]:
            reason = (
                "metadata_insufficient"
                if unresolved and all(b["reason"] == "metadata_insufficient" for b in unresolved)
                else "search_insufficient"
            )
            if unresolved and any(b["reason"] == "semantic_ambiguity" for b in unresolved):
                reason = "semantic_ambiguity"
            result.update(
                route="user_confirmation" if reason == "semantic_ambiguity" else "unresolved",
                reason=reason,
            )
            return result
        if unresolved:
            result.update(
                route="unresolved",
                reason="metadata_insufficient"
                if any(b["reason"] == "metadata_insufficient" for b in unresolved)
                else "semantic_ambiguity",
            )
            return result
        ids = sorted(result["coverage"])
        if len(ids) > 16:
            result.update(
                route="unresolved",
                reason="search_insufficient",
                reason_detail="set_search_budget_exceeded",
            )
            return result
        all_covers = []
        for size in range(1, min(len(ids), len(required)) + 1):
            solutions = [
                list(s)
                for s in itertools.combinations(ids, size)
                if verify_set(s, result["coverage"], required)["valid"]
            ]
            all_covers.extend(solutions)
            if solutions and not result["solutions"]:
                result["solutions"] = solutions
        # Fewer tables is not a license to pick an unspecified statistical basis.
        for axis in ("price_basis", "loan_basis", "currency"):
            if axis in {r["kind"] for r in external}:
                continue
            signatures = {
                self._basis_signature(cover, axis, result["witnesses"]) for cover in all_covers
            }
            if len(signatures) > 1 and any(signatures):
                result.update(
                    reason_detail="basis_changes_cover_before_minimization",
                    clarification=self._clarification(all_covers, external, result["witnesses"]),
                )
                return result
        if len(result["solutions"]) == 1:
            selected = result["solutions"][0]
            result.update(
                route="auto_accept",
                reason="unique_minimum_verified_cover",
                auto_eligible=True,
                proposed_ids=selected,
                validation=verify_set(selected, result["coverage"], required),
            )
        elif result["solutions"]:
            result["reason_detail"] = "alternative_verified_table_sets"
            result["clarification"] = self._clarification(
                result["solutions"], external, result["witnesses"]
            )
        return result

    @staticmethod
    def _basis_signature(cover, axis, witnesses):
        return tuple(
            sorted({c for key in cover for w in witnesses[key] for c in w["basis_options"][axis]})
        )

    def _clarification(self, solutions, requirements, witnesses):
        specified = {r["kind"] for r in requirements}
        for axis in ("price_basis", "loan_basis", "currency"):
            if axis in specified:
                continue
            groups = {}
            for solution in solutions:
                choices = [set(self._basis_signature([k], axis, witnesses)) for k in solution]
                if any(len(c) != 1 for c in choices) or len(set.union(*choices)) != 1:
                    break
                choice = next(iter(choices[0]))
                groups.setdefault(choice, []).append(solution)
            else:
                if len(groups) > 1:
                    return {
                        "axis": axis,
                        "question": f"표 선택에 필요한 {AXES[axis]['label']} 기준을 선택해 주세요.",
                        "options": [
                            {
                                "value": c,
                                "label": AXES[axis]["choices"][c][0],
                                "candidate_sets": groups[c],
                            }
                            for c in sorted(groups)
                        ],
                        "a1_required": False,
                        "reason": "official_branch_basis_changes_table_set",
                    }
        return None

    def validate_proposal(self, question, assessment, ids):
        fresh = self.decide(question, assessment)
        if not fresh["auto_eligible"] or set(ids) != set(fresh["proposed_ids"]):
            raise StageFailure("a2_proposal_not_verified", "unresolved")
        return fresh
