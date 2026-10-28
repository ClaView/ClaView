"""Output shapes of question interpretation and evidence checks (plan 02, 4-1). Draft for stretch 1.

The TypedDicts below are the single definition of the shapes: ``check.py`` reads them to validate data, so a field
added here is checked everywhere. Field names follow plan 02, 4-1 and the detail document (2.1-2.7, 3.4); where the
plan left a choice open, the choice made here is noted next to the field.

This module deliberately has no ``from __future__ import annotations``: the annotations must be real objects so the
checker can read them without evaluating strings.

Question interpretation (one ``Analysis`` per question)
- ``text``: normalisation of the question (2.1).
- ``conditions[]``: condition candidates (2.2). ``status`` is always ``candidate`` at this stage; the old extraction
  status survives as ``attrs.extract_status`` (period). IDs carry a kind prefix: P period, F frequency, B basis,
  U unit, R breakdown, X exclude, K calc, L relation, N function, D display, numbered per kind in question order.
- ``fragments[]`` (2.3), ``terms[]`` (2.4), ``judgement`` (2.5), ``interpretation`` (2.7).
- Plan 15 r3 (optional): ``segments[]`` (physical chunks of the raw question), ``mentions[]`` (semantic occurrences
  with their uncollapsed catalog candidates) and ``ref_map`` (segment <-> mention <-> fragment).
- Plan 15 r3 step 3 (not in ``Analysis``): the decomposition ``Proposal`` of the model and its ``VerifyResult``.
- Plan 15 r3 step 4 (optional): ``decomposition`` (``DecompositionRecord``: requested and adopted path, verification,
  condition bindings, assumption seeds, certainty basis, the settled alternative, rule diagnostics, the condition
  fields ``ConditionFields`` and the inferred frequency hints); requirements of a model structure carry ``refs`` and
  ``reading_id``.
- Plan 19 contract joint (optional): ``decomposition.structure`` (``DecompositionStructure``, version
  ``STRUCTURE_VERSION``): the adopted reading's breakdown groups, polarity links and confirmed effects, refs of the
  ``SelectionRef`` shape; None with ``structure_reason`` for the rules. ``Selection.bundle`` / ``reference_candidates``:
  optional public fields for B.

Plan 11 R1 (contract C): the question contract of ``selection_route.questions[]`` (``RouteQuestion``,
``RouteOption``), the answer (``SelectionAnswer``) and the confirmed condition (``ConfirmedDetail`` widened).

Evidence checks (3.4)
- ``EvidenceDoc``: ``evidence[]`` (one entry per requirement x candidate table, each with ``checks[]``) and
  ``evidence_rows`` (``{id: row}``; a check points at rows by ID, the stance lives in the row).
- A check keeps the old judgement as ``source_status`` and the new decision as ``verdict``.
- Old J4 roles are renamed: ``select`` -> ``splits_table``, ``handoff`` -> ``in_table``.
"""

from typing import Literal, TypedDict


ConditionKind = Literal["period", "frequency", "basis", "unit", "breakdown", "exclude", "calc", "relation",
                        "function", "display"]


class TextView(TypedDict):
    raw: str  # the question as given
    norm: str  # NFKC, runs of whitespace -> one space, trimmed (reused Q1)
    nospace: str  # norm without whitespace
    offsets: list[int]  # norm index -> raw index


class PeriodExpr(TypedDict):
    """One end of a period as written, not resolved to a date."""

    text: str  # the words of this end ("2026년 1월", "6월", "작년 말")
    year: str | None  # four-digit year when written
    alias: str | None  # year alias (올해, 작년, 지난해, 재작년, 금년, 올) or month/quarter relative word (지난, 이번, 저번)
    sub_unit: Literal["Q", "M", "H"] | None  # quarter, month or half below the year
    sub_value: str | None  # "1".."4", "1".."12" (kept as written even when out of range), "상"/"하"
    end_mark: bool  # "말" after the unit (분기말, 7월말, 작년 말)


ID_PREFIX: dict = {"period": "P", "frequency": "F", "basis": "B", "unit": "U", "breakdown": "R", "exclude": "X",
                   "calc": "K", "relation": "L", "function": "N", "display": "D"}


class Condition(TypedDict):
    id: str
    kind: ConditionKind
    quote: str  # == text.raw[span[0]:span[1]]
    span: list[int]  # [start, end) in the raw question
    value: str | None  # frequency code, basis/calc value, unit, breakdown/exclude text; None for period
    attrs: dict  # shape by kind: ATTRS_BY_KIND
    status: Literal["candidate"]
