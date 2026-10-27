"""Question-interpretation nodes after find_conditions (detail 2.3-2.7, plan 04 2-1).

- split_fragments (2.3): phrase structure of the question and its fragments (``phrases``).
- attach_terms (2.4): out-of-scope list -> approved dictionary -> exact names -> 2-gram lexical search (``terms``).
- judge_fragments (2.5): the ten-row judgement table, conservative attribution, the seven certainty checks and the
  verified-structure check 8 (``reading``, ``certainty``, ``structure``; policy in data/certainty_policy.json).
- model_slot (2.6): the place of the model step. The model is off in plan 04 (no call): the judgement passes
  unchanged, with ``model_step`` "pending" when it is not certain.
- settle_requirements (2.7): requirements from the drafts and attribution of the judgement it is given (plan 04
  2-4: kept as they are), relations and status (``settle``).

``judge_fragments`` reads the catalog relations of the terms, so it takes the catalog layer as an optional last
argument; ``attach_terms`` takes the question text and conditions optionally so that it reads the same tokens as the
other nodes (the pipeline passes them).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..contract.shapes import Condition, Fragment, Interpretation, Judgement, Term, TextView
from .certainty import build_judgement
from .phrases import split_fragments
from .settle import settle
from .terms import attach_terms

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

__all__ = ["attach_terms", "judge_fragments", "model_slot", "settle_requirements", "split_fragments"]


def judge_fragments(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
                    layer: CatalogLayer | None = None, overrides=None) -> Judgement:
    """``overrides``: the model step's decisions fed into the judgement table (``overrides``); None: the rules."""
    return build_judgement(text, conditions, fragments, terms, layer, overrides)


def model_slot(judgement: Judgement) -> Judgement:
    """2.6 with the model off: certain judgements pass, undecided ones are marked for the model step."""
    out = dict(judgement)
    if not judgement["certain"]:
        out["model_step"] = "pending"
    return out


def settle_requirements(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
                        judgement: Judgement, layer: CatalogLayer | None = None, overrides=None) -> Interpretation:
    return settle(text, conditions, fragments, terms, judgement, layer, overrides)
