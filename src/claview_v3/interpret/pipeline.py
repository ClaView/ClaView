"""interpret_question: the rule path of question interpretation wired from the nodes (detail 2.1-2.7, plan 04 2-1).

2.1 normalize_text -> 2.2 find_conditions -> 2.3 split_fragments -> 2.4 attach_terms -> 2.5 judge_fragments ->
2.6 model_slot (model off: the judgement passes; undecided ones are marked ``model_step: pending``) ->
2.7 settle_requirements.

With a model client (``model``, plan 06) 2.6 is the model step of ``model_step``: undecided judgements get decision
points the model chooses among, the choices are applied and checked again, and 2.7 runs on the result. Without one
(the default) the output is the rule path above, unchanged.

Plan 15 r3 (3.1-3.3, record only): after 2.2 the raw question is cut into ``segments`` and its semantic occurrences
(``mentions``, with uncollapsed catalog candidates) are read; ``ref_map`` ties them to the 2.3 fragments. They are
attached to the analysis of both the rule path and the model step and change nothing else in it.

Plan 15 r3 step 4 (2.2, 7.4, 7.5, 8): ``decomposition`` (``decompose_run.DecompositionConfig`` or a mapping; only
fake or injected answers in this step). The rule analysis is always made and kept; the rules mode adopts it, the gpt
mode asks for one proposal, verifies it and settles it (or goes back to the rules for the whole question). Either
way the analysis gets a ``decomposition`` record (with the condition fields and the inferred frequency hint of the
adopted structure). Without the argument (the default) the output is the rule path exactly as before (no record).
The old 2.6 model step (``model``) is left as it was and cannot be combined with the gpt mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..contract.shapes import Analysis
from .conditions import find_conditions
from .decompose_run import decompose, parse_config
from .mentions import build_mentions, build_ref_map
from .model_step import run as run_model_step
from .nodes import attach_terms, judge_fragments, model_slot, settle_requirements, split_fragments
from .segments import build_segments
from .text import normalize_text

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer


def interpret_question(question: str, layer: CatalogLayer | None = None, model=None, *,
                       decomposition=None) -> Analysis:
    cfg = parse_config(decomposition)
    if model is not None and cfg is not None and cfg.mode == "gpt":
        raise ValueError("the gpt decomposition and the old 2.6 model step cannot run together")
    text = normalize_text(question)
    conditions = find_conditions(text, layer)
    segments = build_segments(text, conditions, layer)
    mentions = build_mentions(text, conditions, segments, layer)
    fragments = split_fragments(text, conditions, layer)
    occurrences = {"segments": segments, "mentions": mentions,
                   "ref_map": build_ref_map(segments, mentions, fragments)}
    terms = attach_terms(fragments, layer, text, conditions)
    judged = judge_fragments(text, conditions, fragments, terms, layer)
    if model is not None:
        return {**run_model_step(text, conditions, fragments, terms, judged, layer, model), **occurrences}
    judgement = model_slot(judged)
    interpretation = settle_requirements(text, conditions, fragments, terms, judgement, layer)
    rules = {"text": text, "conditions": conditions, "fragments": fragments, "terms": terms, "judgement": judgement,
             "interpretation": interpretation, **occurrences}
    if cfg is None:
        return rules
    return decompose(cfg, rules, layer)
