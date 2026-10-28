"""Rules of find_conditions (detail 2.2) other than periods and frequencies (those are the reused Q2/Q3 copies).

Ported from the old A pipeline (commit 55fd41b). The rule structure is kept; the condition vocabulary moved to
``data/condition_words.json`` and the grammar words (X-byeol mark, negation, units, list, exclusion and marker words)
to ``data/lexicon.json`` (``lexicon``, plan 08), in the same order (the order decides overlaps):
- basis words and "X별" classification words: src/claview/aflow/rules.py find_basis (:641-667) with BASIS_AXES,
  _NOT_BEFORE, _AXIS, _FREQ_AXIS; polarity from src/claview/aflow/q_nodes.py q4 (:83-96)
- comparison bases and calculation requests (the old hand-off conditions, kind calc in the new shape):
  rules.py find_handoff (:682-707) with _THAN and HANDOFF_PATTERNS
- units: rules.py find_units (:750-755)
- lists, list refinement and exclusions: rules.py find_lists, _clean_chain, _token_before, _phrase_after,
  list_modified, exclude_list_members, refine_lists, find_excludes (:319-530) and the condition part of q_nodes.py
  q5 (:316-348)
New here: relation, function and display words (detail 2.2), as plain word patterns.

Positions are indices of the normalised question (``text["norm"]``); the finders turn them into raw spans.
One change to the old output: an "X별" classification word stops after "별" (a following "로" is a particle, not part
of the word), as the evidence specification writes these conditions ("기업규모별", "지역별").
"""

from __future__ import annotations
import json
from .. import paths


WORDS = json.loads(paths.CONDITION_WORDS.read_text(encoding="utf-8"))  # condition vocabulary (basis axes, calc, period)
_WORDS = WORDS


BASIS_AXES: dict[str, dict[str, list[str]]] = _WORDS["basis_axes"]
