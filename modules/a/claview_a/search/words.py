"""check_evidence (detail 3.4, plan 04 4-1): evidence of each requirement x candidate table, in the 1-stretch shape.

Order of the checks (3.4): confirmed answer -> basis axis -> frequency and period -> region -> indicator evidence;
``matched_items`` (classification values matched by the question words) comes first because the basis and exclude
checks read it. Each check returns ``Check`` rows (contract.shapes): the old judgement status stays in
``source_status``, the new decision is ``verdict``. ``confirmed`` (no confirmed answers yet) and ``measure_kind``
(the interpretation leaves ``measure_kind`` None) are not emitted.

Rules are ported from the old A pipeline (commit 55fd41b), structure kept, word lists in ``data/evidence_words.json``:
- matched_items: src/claview/aflow/j_nodes.py ``_match_method``, ``_j2_terms``, ``j2`` (:46-157)
- period, frequency: j_nodes.py ``_envelope``, ``j3`` (:160-243) with rules.py ``compare_period`` (:586-611)
- basis, unit, exclude: j_nodes.py ``_byeol_linked``, ``_fit_basis``, ``_fit_currency_item``, ``_fit_handoff_only``,
  ``_fit_unit``, ``j4`` (:250-526) with rules.py ``basis_forms``, ``unit_currency``, ``compare_unit`` (:641-779)
- cross_axis: j_nodes.py ``cross_axis_support`` (:338-384)
- indicator: src/claview/aflow/measure.py ``rule_phrases``, ``assess`` (:78-185)
New here: ``region`` (the requirement's target values among the table's values or name), the verdicts, the period
resolution from the written period attributes, and the per-requirement scope below.

Scope. Period, frequency, cross_axis, region and indicator are judged per requirement with its own and shared
conditions. The per-condition checks (basis, unit, exclude) are made for every condition of the question so the
record shows how each table stands on each condition; only the requirement's own and shared conditions decide
(``verdict`` pass/fail), the others and every ambiguous one get verdict n/a. ``matched_items`` holds the value
matches of every condition and of the requirement's own words.

Verdicts: positive evidence -> pass, evidence against -> fail, no evidence either way (unknown) -> n/a. The verdicts
are recorded; which of them filter is the record-mode decision (plan 04, 4-2; ``data/evidence_filters.json``).
``prior`` is the list of checks already made for the same requirement x table (basis and exclude read the
matched_items check, cross_axis the basis checks). Each check function returns ``(checks, evidence_rows)``;
``check_evidence`` merges them into one ``EvidenceDoc``.
"""

from __future__ import annotations
import re
from typing import TYPE_CHECKING
from .tables import WORDS


if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer


TITLE_ONLY_AXES = frozenset(WORDS["title_only_axes"])
HANDOFF_ONLY_AXES = frozenset(WORDS["handoff_only_axes"])


STOP_TERMS = frozenset(WORDS["stop_terms"])


_TAIL = re.compile(WORDS["token_tail"])


_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
TOKEN_TAIL, WORD_RE = _TAIL, _WORD  # shared with the query builder (candidates.py)
