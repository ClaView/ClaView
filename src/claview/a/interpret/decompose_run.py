"""The decomposition setting and the adopted path of one question (plan 15 r3 step 4: 2.2, 6.1, 7.2, 8).

``DecompositionConfig`` (or a mapping with its field names): ``mode`` rules | gpt (default rules), ``execution_mode``
fake | replay | live (live/replay require a validated source), ``proposal_source`` (fake: a
callable ``(payload, schema) -> proposal dict or JSON text``; without one the gpt mode is a setting error) and
``as_of`` (the request's reference date for the condition fields; None: the probe date). The rules mode never reads
the execution mode and calls nothing.

``decompose``: the rule analysis is always made first and kept. In the gpt mode the payload and schema are built
(an input limit: back to the rules), the proposal is asked for once (an error or no answer: ``transport``, only the
exception class is kept), verified (a fallback: back to the rules, the reason ``format`` / ``limit`` /
``semantic``), and an accepted proposal is settled (``decompose_settle``); its alternative, when kept, is settled
the same way and recorded. Going back is always the whole question (no requirement is mixed in) and leaves the rule
results as they are. The ``decomposition`` record says what was asked for and what was adopted.

Plan 19 contract joint: an adopted reading (and a kept alternative, in its own IDs) also records its ``structure``
(``decompose_structure``; refs made by the same projection as ``selection_refs``); the rules adopted or a fallback
record ``structure`` None with ``structure_reason`` ``rules_structure_unrecorded``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import get_args

from ..contract.shapes import DecompositionMode, ExecutionMode
from .condition_fields import DEFAULT_AS_OF, fields_of
from .decompose_payload import build_payload, is_input_limit
from .decompose_schema import build_schema
from .decompose_settle import (ALTERNATIVE, PRIMARY, Question, carry_calc_hints, carry_rule_failures,
                               compare_structures, finish_certainty, mark_difference, settle_reading)
from .decompose_structure import STRUCTURE_UNRECORDED, build_structure, selection_ref
from .decompose_verify import polarity_grounds, verify
from .decompose_settle import requirement_refs

MODES = get_args(DecompositionMode)
EXECUTION_MODES = get_args(ExecutionMode)
AVAILABLE_EXECUTION = ("fake", "live", "replay")
FORMAT_CODES = frozenset({"format", "unknown_id"})


@dataclass(frozen=True)
class DecompositionConfig:
    mode: str = "rules"
    execution_mode: str = "fake"
    proposal_source: Callable | None = None
    as_of: str | None = None


def parse_config(value) -> DecompositionConfig | None:
    """None stays None (the default call); a mapping or a ``DecompositionConfig`` is checked (setting errors raise
    ``ValueError``)."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        known = {f.name for f in fields(DecompositionConfig)}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError(f"unknown decomposition settings: {unknown}")
        value = DecompositionConfig(**dict(value))
    if not isinstance(value, DecompositionConfig):
        raise TypeError("decomposition: a DecompositionConfig or a mapping")
    if value.mode not in MODES:
        raise ValueError(f"decomposition mode {value.mode!r} not in {list(MODES)}")
    if value.mode == "gpt":
        if value.execution_mode not in EXECUTION_MODES:
            raise ValueError(f"execution mode {value.execution_mode!r} not in {list(EXECUTION_MODES)}")
        if value.execution_mode not in AVAILABLE_EXECUTION:
            raise ValueError(f"execution mode {value.execution_mode!r} is not available yet")
        if not callable(value.proposal_source):
            raise ValueError("execution needs a proposal source")
        source_mode = getattr(value.proposal_source, "execution_mode", "fake")
        if source_mode != value.execution_mode:
            raise ValueError("proposal source differs from execution mode")
        if value.execution_mode != "fake":
            validate = getattr(value.proposal_source, "validate", None)
            if not callable(validate):
                raise ValueError("live/replay requires a validated source")
            validate()
    return value


def _rule_diagnostics(rules: dict) -> dict:
    j = rules.get("judgement") or {}
    return {"certain": bool(j.get("certain")), "failed_conditions": list(j.get("failed_conditions") or []),
            "failed_sources": [dict(x) for x in j.get("failed_sources") or []],
            "status": (rules.get("interpretation") or {}).get("status")}


def _record(cfg: DecompositionConfig, rules: dict, adopted: dict, applied: str, as_of: str, **kw) -> dict:
    out = {"requested_mode": cfg.mode, "execution_mode": cfg.execution_mode if cfg.mode == "gpt" else None,
           "applied_source": applied, "fallback_reason": None, "error_class": None, "as_of": as_of,
           "reading_id": None, "verify": None, "bindings": [], "requirement_ids": {}, "assumption_seeds": [],
           "certainty": None, "alternative": None, "rule_diagnostics": _rule_diagnostics(rules), "rule_checks": [],
           # plan 19 contract joint: no structure is recorded for the rules (adopted or after a fallback)
           "structure": None, "structure_reason": STRUCTURE_UNRECORDED,
           **fields_of(adopted, as_of)}
    out.update(kw)
    return out


def _fallback_reason(reasons: list[dict]) -> str:
    codes = {r["code"] for r in reasons}
    if "limit_exceeded" in codes:
        return "limit"  # the IDs past the limit are no format error of their own
    if codes & FORMAT_CODES:
        return "format"
    return "semantic"


def _rules_result(cfg, rules: dict, as_of: str, applied: str, **kw) -> dict:
    return {**rules, "decomposition": _record(cfg, rules, rules, applied, as_of, **kw)}


def _settled(q: Question, reading: dict, issues: list[dict], reading_id: str, rules: dict, grounds: dict):
    s = settle_reading(q, reading, issues, reading_id)
    carry_calc_hints(s, (rules.get("judgement") or {}).get("calc_hints") or [])
    carry_rule_failures(q, s, rules, reading, issues)  # only the rule grounds still valid in this reading
    by_id = {r["id"]: r for r in reading["requirements"]}
    # plan 19 stage 3: the rows of the quoted range only (decompose_refs); one projection for refs and structure
    s["selection_refs"] = {rid: [selection_ref(q, place, ref) for place, ref in requirement_refs(by_id[pid])]
                           for rid, pid in s["requirement_ids"].items()}
    # plan 19 contract joint: the proposal's structure kept in this reading's own IDs (decompose_structure)
    s["structure"] = build_structure(q, reading, issues, s["requirement_ids"], grounds)
    return s


def decompose(cfg: DecompositionConfig, rules: dict, layer=None) -> dict:
    """The analysis of the adopted path with its ``decomposition`` record (module docstring). ``rules``: the rule
    analysis of the question (text, conditions, fragments, terms, judgement, interpretation, segments, mentions,
    ref_map)."""
    as_of = cfg.as_of or DEFAULT_AS_OF
    if cfg.mode == "rules":
        return _rules_result(cfg, rules, as_of, "rules")
    text, conditions, segments, mentions = rules["text"], rules["conditions"], rules["segments"], rules["mentions"]
    payload, local = build_payload(text, conditions, segments, mentions, layer)
    if is_input_limit(payload):
        return _rules_result(cfg, rules, as_of, "rules_fallback", fallback_reason="input_limit")
    schema = build_schema(payload)
    try:
        proposal = cfg.proposal_source(payload, schema)
    except Exception as exc:  # noqa: BLE001 - any failure to get the answer is a transport failure (class only)
        from .decompose_archive import ReplayMiss

        if isinstance(exc, ReplayMiss):
            return _rules_result(cfg, rules, as_of, "rules_fallback", fallback_reason="replay_miss")
        return _rules_result(cfg, rules, as_of, "rules_fallback", fallback_reason="transport",
                             error_class=type(exc).__name__)
    if proposal is None:
        return _rules_result(cfg, rules, as_of, "rules_fallback", fallback_reason="transport")
    result = verify(proposal, payload, local, text, conditions, segments, mentions)
    if result["outcome"] == "fallback":
        return _rules_result(cfg, rules, as_of, "rules_fallback", verify=result,
                             fallback_reason=_fallback_reason(result["fallback_reasons"]))
    if isinstance(proposal, (str, bytes)):
        proposal = json.loads(proposal)
    q = Question(text, conditions, segments, mentions, local, rules.get("terms"))
    rules_j, rules_i = rules.get("judgement") or {}, rules.get("interpretation") or {}
    rules_certain = bool(rules_j.get("certain"))
    grounds = polarity_grounds(payload, local, text, conditions, segments, mentions)
    primary = _settled(q, proposal["primary"], result["issues"], PRIMARY, rules, grounds)
    alternative = None
    if result["alternative"] == "kept":
        alternative = _settled(q, proposal["alternative"], result["alternative_issues"], ALTERNATIVE, rules, grounds)
        # scoped to the primary reading's requirements the alternative reads otherwise (none: the whole question)
        back = compare_structures(alternative["interpretation"], alternative["judgement"],
                                  primary["interpretation"], primary["judgement"])
        if back["same"]:
            back = {"same": True, "differences": ["alternative"], "requirement_ids": []}
        mark_difference(q, primary, back, "alternative_reading", "mixed_reading")
    cmp = compare_structures(rules_i, rules_j, primary["interpretation"], primary["judgement"])
    if not cmp["same"]:
        mark_difference(q, primary, cmp, "unverified_structure", "unverified_structure")
    rules_failed = list(rules_j.get("failed_conditions") or [])
    basis = finish_certainty(primary, rules_certain, cmp, True, rules_failed, alternative is not None)
    if alternative is not None:
        alt_cmp = compare_structures(rules_i, rules_j, alternative["interpretation"], alternative["judgement"])
        if not alt_cmp["same"]:
            mark_difference(q, alternative, alt_cmp, "unverified_structure", "unverified_structure")
        alternative["certainty"] = finish_certainty(alternative, rules_certain, alt_cmp, True, rules_failed, True)
    model = {"text": text, "conditions": conditions, "fragments": primary["fragments"], "terms": primary["terms"],
             "judgement": primary["judgement"], "interpretation": primary["interpretation"], "segments": segments,
             "mentions": mentions, "ref_map": primary["ref_map"]}
    alt_record = None
    if alternative is not None:
        alt_record = {k: alternative[k] for k in ("reading_id", "requirement_ids", "bindings", "fragments", "terms",
                                                  "judgement", "interpretation", "assumption_seeds", "rule_checks",
                                                  "selection_refs", "ref_map", "certainty", "structure")}
        alt_record.update(fields_of({**model, "judgement": alternative["judgement"],
                                     "interpretation": alternative["interpretation"]}, as_of))
    record = _record(cfg, rules, model, "model", as_of, reading_id=PRIMARY, verify=result,
                     bindings=primary["bindings"], requirement_ids=primary["requirement_ids"],
                     assumption_seeds=primary["assumption_seeds"], certainty=basis, alternative=alt_record,
                     rule_checks=primary["rule_checks"], selection_refs=primary["selection_refs"],
                     structure=primary["structure"], structure_reason=None)
    return {**model, "decomposition": record}
