"""Optional single-role A1 interpretation; source rules remain authoritative."""

from __future__ import annotations

import asyncio
import copy

from .a1_query import prepare_question
from .a2_evidence import PERIOD_KINDS, requirements_for
from .a2_selection import explicit_requests
from .a_model_contracts import make_packet, validate_grounding, validate_meaning
from .a_semantics import AXES
from .contracts import StageFailure
from .mappings import mentions

VERSION = "claview-a1-interpreter/1"
CONDITION_KINDS = set(AXES) | PERIOD_KINDS | {"frequency", "series_basis", "unit", "exclusion"}


def interpretation_gate(question):
    requests, residuals = explicit_requests(question, requirements_for(question))
    if (
        requests
        and not residuals
        and len({r["kind"] for r in requests}) == 1
        and requests[0]["kind"] != "unbound"
    ):
        return {"needed": False, "reason": "explicit_official_request_resolved_by_rules"}
    return {"needed": True, "reason": "whole_request_interpretation_unverified"}


def condition_signature(question):
    return {
        (r["kind"], str(r["value"]), r["polarity"])
        for r in question["requirements"]
        if r["kind"] in CONDITION_KINDS
    }


def validate_interpretation(value, packet, question):
    """Reject shifted conditions; hypotheses may aid retrieval but never become hard rules."""
    validate_grounding("QueryInterpreter", value, packet)
    validate_meaning("QueryInterpreter", value, packet)
    source_questions = {key: prepare_question(text) for key, text in packet["sources"].items()}
    for row in value["mentions"]:
        if row["kind"] != "condition":
            continue
        span = row["span"]
        required = [
            r
            for r in source_questions[span["source_id"]]["requirements"]
            if r["kind"] in CONDITION_KINDS
        ]
        expected_kinds = PERIOD_KINDS if row["axis"] == "period" else {row["axis"]}
        supported = [
            r
            for r in required
            if r["kind"] in expected_kinds
            and any(
                span["start"] <= ref["start"] < ref["end"] <= span["end"]
                for ref in r["source_refs"]
            )
        ]
        polarity = {"positive": "affirmed", "negative": "negated", "unknown": "unknown"}
        if not supported or any(polarity[r["polarity"]] != row["polarity"] for r in supported):
            raise StageFailure("interpreter_condition_change")
    original_conditions = condition_signature(question)
    auxiliaries = []
    for row in value["expansions"]:
        term = row["term"]
        introduced = condition_signature(prepare_question(term)) - original_conditions
        if introduced:
            raise StageFailure("interpreter_condition_change")
        span = row["span"]
        source = packet["sources"][span["source_id"]]
        # A negated/unresolved phrase must not become a positive supplementary query.
        matching = [m for m in mentions(source, span["quote"]) if m["start"] == span["start"]]
        if any(m["negated"] for m in matching):
            raise StageFailure("interpreter_negation_change")
        auxiliaries.append(
            {
                "expression": term,
                "kind": "model_hypothesis",
                "source_ids": [span["source_id"]],
                "source_span": span,
                "status": "inferred_retrieval_only",
                "meaning_verified": False,
                "can_filter_candidates": False,
                "can_confirm_tables": False,
            }
        )
    return auxiliaries


class QueryInterpretation:
    """Injection requires an explicit runtime; no key loading or live adoption by default."""

    def __init__(
        self, provider, runtime, *, versions, price=None, packet=None, run_async=asyncio.run
    ):
        if provider.model != "HCX-007":
            raise StageFailure("stage5_model_not_allowed")
        if runtime.limits.max_calls > 1 or runtime.limits.max_retries != 0:
            raise StageFailure("stage5_single_attempt_required")
        if not provider.is_mock and (not runtime.allow_live or runtime.attempt_ledger is None):
            raise StageFailure("stage5_live_scope_required")
        self.provider, self.runtime, self.versions = provider, runtime, versions
        self.price, self.packet = price, packet
        self.run_async = run_async

    def describe(self):
        return {
            "version": VERSION,
            "role": "QueryInterpreter",
            "provider": self.provider.identity,
            "max_calls": 1,
            "max_retries": 0,
        }

    def __call__(self, question):
        gate = interpretation_gate(question)
        if not self.runtime.enabled or not gate["needed"]:
            return {
                "status": "not_called",
                "reason": "disabled" if not self.runtime.enabled else gate["reason"],
                "auxiliaries": [],
                "gate": gate,
                "runtime": self.runtime.summary(),
            }
        packet = make_packet(
            question["original_query"],
            self.versions,
            prior_turns=[s["text"] for s in question["sources"][1:]],
        )
        if self.packet is not None and packet != self.packet:
            raise StageFailure("stage5_frozen_packet_mismatch")
        advice = self.run_async(
            self.runtime.invoke("QueryInterpreter", packet, self.provider, price=self.price)
        )
        output = {
            "status": advice["status"],
            "advice": advice,
            "gate": gate,
            "auxiliaries": [],
            "input_packet": packet,
            "runtime": self.runtime.summary(),
        }
        if advice["status"] == "advice":
            try:
                output["auxiliaries"] = validate_interpretation(advice["value"], packet, question)
                output["mentions"] = copy.deepcopy(advice["value"]["mentions"])
                output["condition_policy"] = "original_rules_unchanged; model_mentions_are_advisory"
            except StageFailure as exc:
                output.update(status="fallback", reason=exc.code, auxiliaries=[], applied=False)
        else:
            output["reason"] = advice["reason"]
        return output
