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

from typing import Literal, NotRequired, TypedDict

# ---------------------------------------------------------------- enumerations

ConditionKind = Literal["period", "frequency", "basis", "unit", "breakdown", "exclude", "calc", "relation",
                        "function", "display"]
FrequencyCode = Literal["Y", "H", "Q", "M"]
Polarity = Literal["+", "-"]
TermType = Literal["series_name", "indicator", "target", "basis", "total_header", "compound", "out_of_scope",
                   "unknown"]
TermPath = Literal["exact", "dictionary", "lexical", "embedding"]
ApprovalStatus = Literal["approved", "draft"]
PairVerdict = Literal["split", "merge", "attach", "undecided"]
# "Certain" conditions of 2.5 that can fail (plan 04, 2-3): an appositive gloss or a requirement without a term
# (old 7-row table rows 2 and 7; judgement-table row 10 and unresolved requirements), a term that is not an exact
# match (or of mixed type, or a weak word), content words left over after terms and conditions, an ambiguous condition
# word or a needed axis-value gloss; new in plan 04: a catalog name used by no requirement, a modifier whose scope
# over a list the catalog does not settle, a list read differently in different tables (row 3), an ambiguous
# condition attribution.
CertaintyFailure = Literal["row_2_or_7", "inexact_term", "leftover_word", "ambiguous_word", "unused_name",
                           "scope_ambiguous", "mixed_reading", "ambiguous_attribution",
                           # decision 10/18 (check 8): the question's structure is not among the verified ones
                           "unverified_structure",
                           # user decision 10/18 (split grounding): a requirement of a split without a grounding
                           # table, or a reading whose ungrounded pieces were reattached to another requirement
                           "ungrounded_split", "reattached",
                           # user decision 10/19 (check 9): no requirement has an indicator-side term (the question
                           # names only target, total-header or basis terms)
                           "no_indicator"]
# Phrase kind of a fragment (plan 04, 2.3 phrase structure).
PhraseKind = Literal["item", "modifier", "complement", "negated", "appositive", "topic", "relation"]
InterpretationStatus = Literal["ok", "ask", "unsupported", "partial"]
RequirementStatus = Literal["ok", "unresolved", "unsupported"]
RelationKind = Literal["compare", "ratio", "diff", "share_of"]

CheckName = Literal["confirmed", "matched_items", "period", "frequency", "basis", "unit", "exclude", "cross_axis",
                    "region", "measure_kind", "indicator",
                    # plan 07 S1: the requirement's breakdown (lower items, "X별" axes) in the table; record mode
                    "breakdown"]
CheckState = Literal["computed", "unsupported", "skipped", "missing_meta"]
Verdict = Literal["pass", "fail", "n/a"]
Stance = Literal["support", "against", "unknown"]

# ---------------------------------------------------------------- 2.1 text


class TextView(TypedDict):
    raw: str  # the question as given
    norm: str  # NFKC, runs of whitespace -> one space, trimmed (reused Q1)
    nospace: str  # norm without whitespace
    offsets: list[int]  # norm index -> raw index


# ---------------------------------------------------------------- 2.2 condition attributes (one TypedDict per kind)


class PeriodExpr(TypedDict):
    """One end of a period as written, not resolved to a date."""

    text: str  # the words of this end ("2026년 1월", "6월", "작년 말")
    year: str | None  # four-digit year when written
    alias: str | None  # year alias (올해, 작년, 지난해, 재작년, 금년, 올) or month/quarter relative word (지난, 이번, 저번)
    sub_unit: Literal["Q", "M", "H"] | None  # quarter, month or half below the year
    sub_value: str | None  # "1".."4", "1".."12" (kept as written even when out of range), "상"/"하"
    end_mark: bool  # "말" after the unit (분기말, 7월말, 작년 말)


class PeriodAttrs(TypedDict):
    form: Literal["range", "since", "recent", "point", "alias"]  # old kind; "alias" is reserved, not used yet
    start_expr: PeriodExpr | None  # range/since/point: the (first) end; recent: None
    end_expr: PeriodExpr | None  # range only (year None when omitted: "2026년 1월부터 6월까지")
    open_end: bool  # since (and 올해 = since this year)
    amount: int | None  # recent N
    unit: Literal["year", "month", "quarter", "half"] | None  # recent unit
    relative: bool  # depends on the reference date (alias, recent, 지난달 ...)
    invalid: bool  # a unit value out of range (13월, 5분기)
    # Old extraction status as if a reference date were known: unresolved when the old rules could not resolve the
    # period even with one (month/quarter relatives, recent months/quarters/halves, invalid units, year wrap).
    extract_status: Literal["explicit", "unresolved"]


class FrequencyAttrs(TypedDict):
    value: FrequencyCode
    anchor: Literal["end"] | None  # period-end repetition (분기말마다)


class BasisAttrs(TypedDict):
    axis: str  # old Q4 axis: seasonal, stock, price, loan_basis, currency, size_law, survey_view, survey_method,
    # valuation, classification
    value: str
    polarity: Polarity
    # catalog_family (plan 04, A-7): a bracket qualifier of a table family ("가상대출금(가상용도별)"), axis "family"
    origin: Literal["dictionary", "handoff_rule", "catalog_family"]


class UnitAttrs(TypedDict):
    value: str  # "십억원", "%"
    currency: str | None  # currency named by a money unit (원 -> 원화, 달러 -> 달러), else None


class BreakdownAttrs(TypedDict):
    subkind: str  # old kind ("list")
    text: str  # catalog-corrected name (quote stays as written)
    source: str  # old source: list, catalog_tail, catalog_merge, ...
    measure_pair: bool


class ExcludeAttrs(TypedDict):
    text: str
    complement_hints: list[str]


class CalcAttrs(TypedDict):
    """Comparison bases and calculation requests (old Q4 axes comparison and calc) are kind calc (2.2)."""

    axis: Literal["comparison", "calc"]
    value: str  # 전년동기비, 전기비, 백분율
    polarity: Polarity
    origin: Literal["dictionary", "handoff_rule"]


class RelationAttrs(TypedDict):
    relation: Literal["ratio", "diff", "compare", "share_of", "percent_of", "net"]


class FunctionAttrs(TypedDict):
    function: Literal["split_by", "minus", "among", "each"]  # 로 나눠서, 를 뺀, 중/가운데, 각각


class DisplayAttrs(TypedDict):
    display: Literal["trend", "compare", "composition", "side_by_side"]  # 추이, 비교, 구성, 나란히


ATTRS_BY_KIND: dict = {
    "period": PeriodAttrs, "frequency": FrequencyAttrs, "basis": BasisAttrs, "unit": UnitAttrs,
    "breakdown": BreakdownAttrs, "exclude": ExcludeAttrs, "calc": CalcAttrs, "relation": RelationAttrs,
    "function": FunctionAttrs, "display": DisplayAttrs,
}
ID_PREFIX: dict = {"period": "P", "frequency": "F", "basis": "B", "unit": "U", "breakdown": "R", "exclude": "X",
                   "calc": "K", "relation": "L", "function": "N", "display": "D"}


ConditionSource = Literal["question", "user"]  # plan 15 r3 7.4 (appendix A 2): where a condition's value came from
PeriodDiagnosis = Literal["ok", "unsupported", "malformed"]  # plan 15 r3 appendix A 1


class Condition(TypedDict):
    id: str
    kind: ConditionKind
    quote: str  # == text.raw[span[0]:span[1]]
    span: list[int]  # [start, end) in the raw question
    value: str | None  # frequency code, basis/calc value, unit, breakdown/exclude text; None for period
    attrs: dict  # shape by kind: ATTRS_BY_KIND
    status: Literal["candidate"]


class ConditionFields(TypedDict):
    """Plan 15 r3 step 4 (7.4, appendix A 1-2): the fields of one condition after the requirements are settled.
    Kept in ``Analysis.decomposition.condition_fields`` (condition ID -> fields), not on the conditions: the rule-path
    conditions stay as they were (existing expectations compare them whole). ``source`` question or user (not
    ``attrs.source``, the extraction method); ``confirmed`` the per-kind check (no table lookup, no query result);
    ``applied`` None in A (B fills it); ``period_diagnosis`` on period conditions only."""

    source: ConditionSource
    confirmed: bool
    applied: bool | None
    period_diagnosis: NotRequired[PeriodDiagnosis]


# ---------------------------------------------------------------- 2.3 fragments, 2.4 terms


class Fragment(TypedDict):
    id: str
    quote: str
    span: list[int]
    role: Literal["core", "object"]  # object = object phrase of a function word ("비금융법인·가계로 나눠서")
    protected_by: str | None  # catalog-name key that kept the phrase uncut, if any
    phrase: NotRequired[PhraseKind]  # plan 04 (2.3): phrase kind the fragment came from


class Term(TypedDict):
    fragment_id: str
    name: str
    type: TermType
    table_ids: list[str]
    obj_id: str | None
    obj_name: str | None
    path: TermPath
    status: ApprovalStatus
    score: float | None


# ---------------------------------------------------------------- 2.5 judgement


class Pair(TypedDict):
    left: str  # fragment ID
    right: str  # fragment ID
    row: int  # first matching row of the judgement table (plan 04, 2-2), 1..10
    verdict: PairVerdict
    relation: str | None


class DraftRequirement(TypedDict):
    id: str
    fragment_ids: list[str]
    indicator_fragment_id: str | None
    # plan 04 (2.5) reading of the draft, read by the model step and settle_requirements; all optional
    indicator: NotRequired[str | None]  # catalog name of the indicator term
    context: NotRequired[str | None]  # table name read as context of a value
    targets: NotRequired[list[str]]  # catalog names of the target values (shared targets included)
    breakdown: NotRequired[list[str]]  # lower items or X-byeol axes the requirement is broken down by
    condition_ids: NotRequired[list[str]]  # own conditions (a view of a copied requirement included)
    status: NotRequired[RequirementStatus]  # unresolved: no term, mixed basis axes, appositive gloss
    broad: NotRequired[bool]  # no indicator: a broad question over its targets
    row: NotRequired[int]  # judgement-table row that made this requirement (1..10)


class CalcHint(TypedDict):
    """A change expression ("늘었", "증가율") kept as a mark, not a condition (user decision 10/18)."""

    quote: str
    span: list[int]
    kind: str  # change, rate
    requirement_id: str | None  # the one draft requirement it belongs to; None when it has several or none
    requirement_ids: NotRequired[list[str]]  # every draft requirement it belongs to (copies of one item: all)


class ModelOption(TypedDict):
    """One choice of a decision point (plan 06, 2): made by the code, never by the model."""

    id: str  # o1, o2, ... unique within one request
    text: str  # what the choice means, as sent to the model
    effect: str  # how the code applies it (keep, unknown, name, one, split, copy, all, first, separate, ...)
    type: NotRequired[str]  # name choices: term type label
    tables: NotRequired[int]  # name choices: number of tables holding the name
    paths: NotRequired[list[str]]  # name choices: at most two representative paths ("상위 > 값")


ModelPointKind = Literal["name", "appositive", "reading", "scope", "attribution", "word"]


class ModelPoint(TypedDict):
    id: str  # P1, P2, ...
    kind: ModelPointKind
    ask: str  # what the point decides
    options: list[ModelOption]
    requirement_ids: list[str]  # requirements of the draft interpretation the point concerns


class ModelRecord(TypedDict):
    """The model step of 2.6 (plan 06, 4): points sent, choices and reasons received, and what became of them."""

    points: list[ModelPoint]
    choices: dict[str, str]  # point ID -> option ID
    reasons: dict[str, str]  # point ID -> the model's reason
    prompt_sha256: str | None
    outcome: Literal["skipped", "done", "rejected", "failed"]
    notes: NotRequired[list[str]]  # code notes: why skipped, rejected or failed (new check failures, error class)


class FailedSource(TypedDict):
    """Plan 11 R0 (7-6), record only: where one certainty failure came from."""

    condition: str  # the failed_conditions code
    requirement_ids: list[str]  # requirements (2.7 numbering) it concerns; empty for a question-level failure
    detail: str  # the cause inside the check (e.g. "no_catalog_name:target")
    # plan 11 R1b (3절), record only: the conditions / fragments the failure is about (empty: the whole requirement,
    # or the whole question when requirement_ids is empty too)
    condition_ids: NotRequired[list[str]]
    fragment_ids: NotRequired[list[str]]


class Judgement(TypedDict):
    pairs: list[Pair]
    requirement_draft: list[DraftRequirement]
    certain: bool
    failed_conditions: list[CertaintyFailure]
    # plan 04 (2.5), all optional
    attribution: NotRequired[dict[str, str]]  # condition ID -> draft requirement ID, "common" or "ambiguous"
    table_group_hint: NotRequired[list[list[str]]]  # draft requirement IDs read from one table (rows 6 and 8)
    calc_hints: NotRequired[list[CalcHint]]
    negated: NotRequired[list[str]]  # words a negation took out ("A가 아니라 B": A)
    leftover: NotRequired[list[str]]  # check 5: words left after names, conditions and known words
    unused_names: NotRequired[list[str]]  # check 6: catalog names used by no requirement field
    # 2.6: pending while the model is off; with the model on (plan 06, 4): skipped (no call: no point or too many
    # points), done (choices accepted), rejected (the recheck found a new failure), failed (call or reply failure)
    model_step: NotRequired[Literal["pending", "skipped", "done", "rejected", "failed"]]
    model: NotRequired[ModelRecord]  # plan 06: the model step's points, choices and reasons
    structure: NotRequired[str]  # check 8: structure signature of the question (canonical JSON, no words)
    failed_sources: NotRequired[list[FailedSource]]  # plan 11 R0: provenance of failed_conditions (record only)


# ---------------------------------------------------------------- 2.7 interpretation


class IndicatorRef(TypedDict):
    quote: str
    span: list[int]
    fragment_id: str | None
    name: str | None  # catalog term
    path: TermPath | None


class TargetRef(TypedDict):
    quote: str
    span: list[int]
    fragment_id: str | None
    name: str | None


class RequirementRefs(TypedDict):
    """Plan 15 r3 step 4 (appendix A 3): the segments and mentions a requirement of a model structure stands on."""

    segment_ids: list[str]
    mention_ids: list[str]


class InferredHint(TypedDict):
    """Plan 15 r3 7.5: a frequency inferred from the period units of a requirement (a hint only: never a condition,
    frequency evidence or table-bundle split)."""

    kind: Literal["frequency"]
    value: FrequencyCode
    from_condition_id: str  # the period condition the unit was read from
    rule: Literal["period_unit"]


class Requirement(TypedDict):
    id: str  # r1, r2, ...
    quote: str  # contiguous words of the question this requirement stands on
    span: list[int]
    indicator: IndicatorRef | None
    measure_kind: str | None
    targets: list[TargetRef]
    condition_ids: list[str]  # conditions of this requirement only
    shared_condition_ids: list[str]  # = interpretation.common_condition_ids (repeated per requirement for B)
    ambiguous_condition_ids: list[str]  # attribution undecided: carried, never used to pass or fail a table
    status: RequirementStatus
    reasons: list[str]
    context: NotRequired[str | None]  # plan 04 (5-2): table name read as context
    breakdown: NotRequired[list[str]]  # plan 04 (5-2): breakdown items
    # plan 15 r3 step 4, optional: ``refs`` and ``reading_id`` only on requirements of a model structure (appendix
    # A 3: (reading_id, id)); the inferred frequency hint of 7.5 is kept in ``Analysis.decomposition.inferred``
    refs: NotRequired[RequirementRefs]
    reading_id: NotRequired[str]


class Relation(TypedDict):
    kind: RelationKind
    requirement_ids: list[str]
    condition_id: str | None


class Interpretation(TypedDict):
    status: InterpretationStatus
    common_condition_ids: list[str]
    relations: list[Relation]
    display_condition_ids: list[str]
    requirements: list[Requirement]
    reasons: list[str]
    table_group_hint: NotRequired[list[list[str]]]  # plan 04 (5-2): requirement IDs of same-table siblings
    # plan 11 R0 (8-2 item 10), record only: for each unsupported requirement, the indicator candidate types and
    # whether every one of them is out_of_scope (unsupported is set when any one is)
    unsupported_sources: NotRequired[dict[str, dict]]


# ---------------------------------------------------------------- plan 15 r3 (3.1-3.3): segments and mentions
# Record only in step 2 (no stage reads them yet). Optional on ``Analysis`` so that an analysis of the old shape
# stays valid; ``check.check_analysis`` checks their content once they are present.

SegmentKind = Literal["name", "condition", "marker", "request", "word", "grammar", "space", "punct"]
MentionKind = Literal["name", "condition", "marker", "request"]
MarkerKind = Literal["list", "divide", "negation", "exclusion", "relation"]
CandidateRole = Literal["indicator", "target", "basis", "breakdown", "table_name", "unclassified"]
CandidatePath = Literal["exact", "dictionary", "lexical"]
# the source type of a candidate: the term-index type, an ITEM row of a table or a classification-axis name
CandidateType = Literal["series_name", "indicator", "target", "basis", "total_header", "compound", "unknown",
                        "item_row", "axis_name"]


class Segment(TypedDict):
    """One physical chunk of the raw question (3.1): the segments, joined in order, are ``text.raw`` exactly."""

    segment_id: str  # s1, s2, ... in text order
    span: list[int]  # [start, end) in the raw question
    quote: str  # == text.raw[span[0]:span[1]]
    kind: SegmentKind


class MentionCandidate(TypedDict):
    """One catalog object a mention may stand for (3.3), never collapsed over tables or values."""

    table_id: str
    obj_id: str | None  # axis ID; "ITEM" for an ITEM row; None for a table name
    value_id: str | None  # the raw item_id of a value or ITEM row; None for a table name or an axis name
    value_name: str | None  # the catalog name (value, ITEM row, table or axis name)
    role: CandidateRole  # unclassified: the role cannot be mapped (never read as an indicator)
    path: CandidatePath
    type: CandidateType


class Mention(TypedDict):
    """One semantic occurrence over contiguous segments (3.2); mentions may overlap."""

    mention_id: str  # m1, m2, ... in text order
    kind: MentionKind
    segment_ids: list[str]  # contiguous, in text order
    # plan 19 (contract owner; decision of 2026-10-21): the raw [start, end) of the mention, read by the final
    # reference check (``check._ref_problems``) by the generator's range rule; absent in analyses of older runs (then
    # the check reads by segments). Record only: the model payload never sends it.
    span: NotRequired[list[int]]
    condition_ids: list[str]  # existing condition IDs (2.2) of a condition mention or of a marker condition
    marker: MarkerKind | None  # the marker class a mention shows (only that a marker is written: no scope)
    candidates: list[MentionCandidate]
    overlaps: list[str]  # other mentions sharing a segment


class RefMap(TypedDict):
    """Plan 15 r3 appendix A 3: segment <-> mention <-> 2.3 fragment (fragment IDs are a compatibility view)."""

    segment_mentions: dict[str, list[str]]  # segment ID -> mention IDs (every segment)
    segment_fragments: dict[str, list[str]]  # segment ID -> fragment IDs whose span overlaps it (every segment)
    fragment_segments: dict[str, list[str]]  # fragment ID -> segment IDs its span overlaps (every fragment)
    mention_fragments: dict[str, list[str]]  # mention ID -> fragment IDs over its segments (every mention)


# ``Analysis`` itself is defined after the decomposition shapes below (its optional ``decomposition`` record holds
# them; the annotations here are real objects, so they must exist first).


# ---------------------------------------------------------------- plan 15 r3 (5.1-5.2, 6): decomposition proposal
# Step 3: the model's structure proposal and the verification result. Not part of ``Analysis`` (no stage reads them
# yet). ``interpret/decompose_schema.py`` builds the per-question JSON Schema from the field names of these shapes
# (one definition): every field is required there (null or [] for none), no other field is allowed.

BreakdownKind = Literal["values", "axis", "descendants"]
BindingScope = Literal["question", "requirements", "unresolved", "name_content"]
PolarityKind = Literal["exclude", "replace"]
UnusedReason = Literal["unmapped", "unattached", "unresolved"]
AmbiguityKind = Literal["role", "scope", "meaning", "condition", "polarity"]
# fixed reason codes of an ambiguity (5.2): several senses, several scopes, condition attribution open, role open,
# polarity scope open, merge or split of requirements open, concept without a catalog name
AmbiguityReason = Literal["several_senses", "several_scopes", "condition_unattached", "role_unclear",
                          "polarity_scope", "merge_or_split", "unmapped_concept"]


class ProposalRef(TypedDict):
    """3.4 Ref: contiguous segments in text order; a subquote only over one segment (found there exactly once); the
    sense a name stands for (a code-made sense ID of the payload) or None."""

    segment_ids: list[str]
    subquote: str | None
    sense_id: str | None


class ProposalBreakdown(TypedDict):
    kind: BreakdownKind
    axis: ProposalRef | None
    values: list[ProposalRef]
    marker_refs: list[ProposalRef]


class RequirementProposal(TypedDict):
    id: str  # r1 .. r<limit>
    indicator: ProposalRef | None  # None: unresolved
    targets: list[ProposalRef]
    basis_refs: list[ProposalRef]
    context: ProposalRef | None  # a table name or other context, or None
    breakdowns: list[ProposalBreakdown]


class ConditionBinding(TypedDict):
    """The one place a condition is attributed (5.2): question = every requirement, requirements = the named ones,
    unresolved = attribution open (candidate requirements named), name_content = words inside a name."""

    condition_id: str
    scope: BindingScope
    requirement_ids: list[str]
    name_refs: list[ProposalRef]


class RelationProposal(TypedDict):
    condition_id: str  # a relation condition; its kind comes from the condition
    requirement_ids: list[str]  # operand order kept


class PolarityLink(TypedDict):
    kind: PolarityKind
    marker_refs: list[ProposalRef]
    negative_refs: list[ProposalRef]
    positive_refs: list[ProposalRef]
    requirement_ids: list[str]  # empty: every requirement


class UnusedRef(TypedDict):
    ref: ProposalRef
    reason: UnusedReason


class Ambiguity(TypedDict):
    refs: list[ProposalRef]
    kind: AmbiguityKind
    condition_ids: list[str]
    reason_code: AmbiguityReason


class Reading(TypedDict):
    requirements: list[RequirementProposal]
    condition_bindings: list[ConditionBinding]
    relations: list[RelationProposal]
    polarity_links: list[PolarityLink]
    request_refs: list[ProposalRef]
    unused_refs: list[UnusedRef]


class Proposal(TypedDict):
    primary: Reading
    alternative: Reading | None
    ambiguities: list[Ambiguity]


VerifyOutcome = Literal["accept", "fallback"]
AlternativeState = Literal["absent", "kept", "dropped"]
IssueReading = Literal["primary", "alternative", "proposal"]
# 6.1 / 6.2 reasons that send the whole question to the rules (primary or proposal) or drop the alternative
FallbackCode = Literal["format", "unknown_id", "limit_exceeded", "ref_not_contiguous", "wrong_sense",
                       "subquote_not_found", "subquote_repeated", "subquote_multi_segment", "segment_unused",
                       "condition_missing", "condition_conflict", "condition_binding_invalid", "target_as_indicator",
                       "request_swallow", "polarity_no_marker", "polarity_negated_exclusion",
                       "polarity_excluded_required", "polarity_inverted", "polarity_marker_request_only",
                       # step 3 fix 3: a clear (or certain) polarity object not on the negative side, another name
                       # negated by a clear marker, or a clear marker's effect set aside (unused, ambiguity only)
                       "polarity_target_mismatch", "polarity_effect_lost",
                       # plan 19 stage 3: a basis merge when data/decompose_policy.json basis_merge.effect is fatal
                       "basis_merged_fatal"]
# 6.1 / 6.2 findings that keep the structure and are assumption material (never a pass for certainty)
AcceptCode = Literal["unclear_scope", "unmapped_concept", "sense_unresolved", "indicator_unresolved",
                     "breakdown_value_unconfirmed", "breakdown_incomplete", "condition_unresolved", "role_ambiguous",
                     "indicator_role_unclassified", "polarity_unattached", "polarity_incomplete", "relation_incomplete",
                     "unused_unresolved", "ambiguity",
                     # step 3 fix 2: a sense (or candidates) of a role the place does not take (not the indicator)
                     "role_mismatch",
                     # plan 19 stage 3: two exclusive positive basis conditions applied to one requirement (policy
                     # non_fatal, user decision 10/21), a ratio relation not over exactly two distinct requirements,
                     # ratio relations of one pair in different orders (direction unknown), a lexical sense whose
                     # name adds a measurement meaning the question does not have
                     "basis_merged", "ratio_operands", "ratio_order_conflict", "added_measure"]


class VerifyIssue(TypedDict):
    """One finding with the scope it touches (requirement IDs of its reading, segments, conditions; all empty: the
    whole question)."""

    code: FallbackCode | AcceptCode
    reading: IssueReading
    fatal: bool  # a fallback reason (primary, proposal) or the reason an alternative was dropped
    requirement_ids: list[str]
    segment_ids: list[str]
    condition_ids: list[str]
    detail: str  # code-made detail (IDs and names of the check), never the model's own words


class VerifyScopes(TypedDict):
    """Issue codes of the kept structure by scope: per requirement, segment and condition, and question-level ones."""

    requirements: dict[str, list[str]]
    segments: dict[str, list[str]]
    conditions: dict[str, list[str]]
    question: list[str]


class VerifyResult(TypedDict):
    outcome: VerifyOutcome  # fallback: the whole question goes to the rules path (no mixed fallback)
    fallback_reasons: list[VerifyIssue]  # empty when accepted
    issues: list[VerifyIssue]  # accepted: findings of the primary reading and the proposal (assumption material)
    alternative: AlternativeState
    alternative_issues: list[VerifyIssue]  # dropped: why; kept: its own findings
    assumption_seed: bool  # accepted with any finding left or an alternative kept
    scopes: VerifyScopes  # accepted: ``issues`` by scope (empty when the question falls back)


# ---------------------------------------------------------------- plan 15 r3 step 4 (7, 8, appendix A): the record
# ``Analysis.decomposition`` (optional): which structure was adopted and why, the verification, the condition bindings
# (the source of truth of the attribution), internal assumption seeds, the certainty basis of 7.2, the settled
# alternative reading and the rule diagnostics kept for comparison. Present when ``interpret_question`` is given a
# decomposition setting; the default call (no setting) adds no record.

DecompositionMode = Literal["rules", "gpt"]
ExecutionMode = Literal["fake", "replay", "live"]
AppliedSource = Literal["rules", "model", "rules_fallback"]
# why the whole question went back to the rules: the payload was over a limit, the proposal could not be had
# (transport: an error or no answer), it broke the format (JSON, schema, unknown IDs), it was over the requirement
# limit, or its meaning failed the code check (6.1 / 6.2)
DecompositionFallback = Literal["input_limit", "transport", "format", "limit", "semantic",
                                "ledger_conflict", "ledger_in_progress", "replay_miss"]
AssumptionSource = Literal["question", "user", "catalog", "default"]  # appendix A 2 (not the condition source)


class SeedRefs(TypedDict):
    segment_ids: list[str]
    mention_ids: list[str]
    condition_ids: list[str]


class RequirementKey(TypedDict):
    """Appendix A 3: a requirement of one reading."""

    reading_id: str
    requirement_id: str


class AssumptionSeed(TypedDict):
    """An internal assumption seed (completion 1): what of a kept structure is open, by scope. Not the shared
    ``assumptions`` contract (completion 2)."""

    seed_id: str  # stable for the same content
    stage: Literal["A"]
    reading_id: str
    requirement_keys: list[RequirementKey]
    refs: SeedRefs
    reason_code: str  # a verification AcceptCode, ``alternative_reading`` or ``unverified_structure``
    source: AssumptionSource


CertaintyReason = Literal["rules_uncertain_only"]
RuleCheckOutcome = Literal["resolved_by_code", "still_valid", "not_rechecked"]


class CertaintyBasis(TypedDict):
    """7.2: certain only if every one holds (``differences``: what differs between the rule and model structures).
    User decision 10/20 22:48 (B): when the only reason the reading is not certain is that the rules were not certain
    (the structure verified and the same, every rule failure ground re-checked and resolved, no failure, seed,
    unresolved requirement or kept alternative of its own), no failed source is written and ``certain`` (false) and
    ``reason`` record it here; absent in every other case."""

    rules_certain: bool
    same_structure: bool
    verified: bool
    seeds_left: bool
    failures_left: bool
    differences: list[str]
    certain: NotRequired[bool]
    reason: NotRequired[CertaintyReason]


class RuleCheck(TypedDict):
    """Fix round 2 (C): one rule failure source re-checked against an adopted reading (``data/decompose_policy.json``
    ``rule_failures.checks``): resolved_by_code (left out), still_valid (kept) or not_rechecked (no re-check exists:
    kept, never counted as confirmed)."""

    condition: str  # the rule source's failure code
    detail: str  # the rule source's detail
    outcome: RuleCheckOutcome
    check: str  # the re-check that decided ("" when none exists)


class RuleDiagnostics(TypedDict):
    """The rule reading kept for comparison. Never copied into a model structure as such: each rule failure source is
    re-checked against the adopted reading and only a ground not resolved by the code is carried (user decision 10/20
    22:10, fix round 2: ``decompose_settle.carry_rule_failures``, recorded in ``rule_checks``)."""

    certain: bool
    failed_conditions: list[CertaintyFailure]
    failed_sources: list[FailedSource]
    status: InterpretationStatus | None


class SelectionRef(TypedDict):
    """Validated model reference projected for selection; candidates retain raw item codes."""

    place: str
    segment_ids: list[str]
    span: list[int]
    quote: str
    name: str | None
    candidates: list[MentionCandidate]


# ---------------------------------------------------------------- plan 19 contract joint (10/21): the kept structure
# ``decomposition.structure`` (and ``alternative.structure``, in that reading's own requirement IDs): what the settling
# used to flatten (breakdown groups as quote strings, polarity links as negated spans), kept for an adopted model
# reading only. Every ref is a ``SelectionRef`` made by the same reader as ``selection_refs`` (decompose_refs); a
# positive ref of a requirement equals its ``selection_refs`` item. Rules adopted or a fallback: ``structure`` None
# with ``structure_reason``. A record of an older run has neither field (absent: no structure, not a violation).
# The selection reads none of it yet (field_effects 0_unused); B reads it through its adapter.

STRUCTURE_VERSIONS = ("structure-2026-10-21.1",)  # supported versions (contract/check.py); the first is written
STRUCTURE_VERSION = STRUCTURE_VERSIONS[0]
StructureReason = Literal["rules_structure_unrecorded"]  # the rule structure is not recorded in this form
DescendantsStatus = Literal["unexpanded"]  # no hierarchy expander in A: the parents are kept, never expanded
PolarityScopeStatus = Literal["clear", "uncertain"]  # read from the verification findings only


class BreakdownDescendants(TypedDict):
    """A ``descendants`` breakdown: the refs whose lower values are asked for (axis, then values; their candidate rows
    are the parent rows), not expanded."""

    status: DescendantsStatus
    source_refs: list[SelectionRef]


class StructureBreakdown(TypedDict):
    """One breakdown group of a requirement (one proposal breakdown): its kind as proposed, the axis, the values
    (empty: values not specified), the breakdown markers; ``descendants`` exactly for the kind ``descendants``."""

    group_id: str  # b1, b2, ... in proposal order, unique within the requirement
    kind: BreakdownKind
    axis: SelectionRef | None
    values: list[SelectionRef]
    markers: list[SelectionRef]
    descendants: BreakdownDescendants | None


class RequirementStructure(TypedDict):
    """The kept structure of one settled requirement: filter values (``targets``), breakdown groups, basis refs and
    the context ref (the indicator is ``Requirement.indicator`` and the first ``selection_refs`` item)."""

    targets: list[SelectionRef]
    breakdowns: list[StructureBreakdown]
    basis: list[SelectionRef]
    context: SelectionRef | None


class PolarityStructure(TypedDict):
    """One proposed polarity link as proposed: markers, negative and positive sides, and the requirements it applies
    to in settled IDs (proposal scope translated, never replaced by ref membership; empty: every requirement).
    ``scope_status``: uncertain when a verification finding about the polarity scope lies on the link."""

    link_id: str  # p1, p2, ... in proposal order
    kind: PolarityKind
    markers: list[SelectionRef]
    negative: list[SelectionRef]
    positive: list[SelectionRef]
    requirement_ids: list[str]
    scope_status: PolarityScopeStatus


class ConfirmedEffect(TypedDict):
    """A negative ref of a link whose target and attribution the existing verification grounds confirm on their own
    (the model naming it confirms nothing): the requirements it applies to and the refs of that evidence. Kept in the
    contract, but held: no effect is made in this version (``DecompositionStructure.confirmed_effects`` is [])."""

    link_id: str
    negative_ref_index: int  # index into that link's ``negative``
    requirement_ids: list[str]  # settled IDs, within the link's scope
    evidence_refs: list[SelectionRef]  # the marker ref(s) and the negative ref


class DecompositionStructure(TypedDict):
    version: str  # one of STRUCTURE_VERSIONS (another version: unsupported, checked apart from violations)
    requirements: dict[str, RequirementStructure]  # settled requirement ID -> its structure (every requirement)
    polarity: list[PolarityStructure]
    # held empty in this version (user decision of 2026-10-21): always [], a non-empty list is a contract violation;
    # the polarity links above are kept as recorded but are no confirmed refutation
    confirmed_effects: list[ConfirmedEffect]


class SettledReading(TypedDict):
    """A reading settled into the existing contract (the alternative reading, kept for the selection to come)."""

    reading_id: str
    requirement_ids: dict[str, str]  # settled requirement ID -> proposal requirement ID
    bindings: list[ConditionBinding]
    fragments: list[Fragment]
    terms: list[Term]
    judgement: Judgement
    interpretation: Interpretation
    assumption_seeds: list[AssumptionSeed]
    condition_fields: dict[str, ConditionFields]  # under this reading's attribution
    inferred: dict[str, list[InferredHint]]  # requirement ID -> frequency hint ([]: none)
    rule_checks: list[RuleCheck]  # the rule failure sources re-checked against this reading
    selection_refs: NotRequired[dict[str, list[SelectionRef]]]
    ref_map: NotRequired[RefMap]
    certainty: NotRequired[CertaintyBasis]
    structure: NotRequired[DecompositionStructure | None]  # plan 19: this reading's kept structure (its own IDs)


class DecompositionRecord(TypedDict):
    requested_mode: DecompositionMode
    execution_mode: ExecutionMode | None  # None on the rules mode (no model client is made)
    applied_source: AppliedSource
    fallback_reason: DecompositionFallback | None
    error_class: str | None  # transport: the exception class name only (never its message)
    as_of: str  # the reference date the condition fields were checked with
    reading_id: str | None  # the adopted model reading (None: the rules)
    verify: VerifyResult | None  # None: no proposal was verified (rules mode, input limit, transport)
    bindings: list[ConditionBinding]  # the adopted reading's condition bindings (the attribution's source of truth)
    requirement_ids: dict[str, str]  # settled requirement ID -> proposal requirement ID (adopted reading)
    assumption_seeds: list[AssumptionSeed]
    certainty: CertaintyBasis | None  # None when the rules are adopted
    alternative: SettledReading | None
    rule_diagnostics: RuleDiagnostics
    rule_checks: list[RuleCheck]  # the rule failure sources re-checked against the adopted reading ([]: rules)
    condition_fields: dict[str, ConditionFields]  # condition ID -> fields under the adopted structure (7.4)
    inferred: dict[str, list[InferredHint]]  # adopted requirement ID -> frequency hint (7.5; []: none)
    selection_refs: NotRequired[dict[str, list[SelectionRef]]]
    # plan 19 contract joint: the adopted reading's kept structure; None (rules adopted, fallback) with the reason
    structure: NotRequired[DecompositionStructure | None]
    structure_reason: NotRequired[StructureReason | None]


class Analysis(TypedDict):
    text: TextView
    conditions: list[Condition]
    fragments: list[Fragment]
    terms: list[Term]
    judgement: Judgement | None
    interpretation: Interpretation | None
    # plan 15 r3 (3.1-3.3, appendix A 3): optional, record only in step 2
    segments: NotRequired[list[Segment]]
    mentions: NotRequired[list[Mention]]
    ref_map: NotRequired[RefMap]
    # plan 15 r3 step 4: optional decomposition record
    decomposition: NotRequired[DecompositionRecord]


# ---------------------------------------------------------------- 3.4 evidence check details (one per check name)


class ConfirmedFrom(TypedDict):
    """Plan 11 R1 (C, 7-8): the question and option a user-confirmed condition came from."""

    question_id: str
    option_id: str | None  # None for a text answer to an open question


# Plan 11 R1 (C, 7-8, 8-3-1, F6): a confirmed condition widens the existing ``confirmed`` detail (no second spec).
# ``condition_id`` is None for a condition that comes from a user answer and is no condition of the analysis. The new
# fields are optional so that a detail of the old shape stays valid; ``check.check_confirmed_condition`` requires
# them all. ``requirement_key``: the requirement identity key of plan 11 R0 (``select/facts.requirement_identity``:
# fragment IDs + condition-combination key). Functional form because ``from`` is a Python keyword.
ConfirmedDetail = TypedDict("ConfirmedDetail", {
    "condition_id": str | None,
    "answer": str,
    "requirement_key": NotRequired[str],
    "requirement_id": NotRequired[str],
    "dimension": NotRequired[str],
    "value": NotRequired[str],
    "polarity": NotRequired[Polarity],
    "source": NotRequired[Literal["interpretation", "user"]],
    "from": NotRequired[ConfirmedFrom | None],  # None when the source is the interpretation
})


class ParentStep(TypedDict):
    item_id: str
    name: str


class MatchedItemRow(TypedDict):
    condition_id: str | None  # None for a term match that belongs to no condition
    obj_id: str
    item_id: str
    name: str
    term: str | None  # question words that matched (kept from old J2)
    parent_path: list[ParentStep]  # ancestors on the same axis, nearest first
    depth: int  # number of ancestors (old J2 meaning) == len(parent_path)
    match_method: str  # exact, nospace, complement, ...
    polarity: Polarity


class MatchedItemsDetail(TypedDict):
    rows: list[MatchedItemRow]


class PeriodRequested(TypedDict):
    """The period request as the check resolved it (resolution happens here, not in the interpretation)."""

    condition_ids: list[str]
    form: str | None
    start: str | None
    end: str | None


class PeriodRow(TypedDict):
    frequency: FrequencyCode
    requested: bool  # this frequency is judged for the request (asked for, or no frequency asked)
    status: Literal["covered", "partial", "outside", "unknown"]
    coverage: list[str | None]  # [start, end] of the table for this frequency
    reason: str | None


class PeriodDetail(TypedDict):
    request: PeriodRequested | None
    by_frequency: list[PeriodRow]  # table period order
    coverage_status: str  # ended_before_request or n/a


class FrequencyDetail(TypedDict):
    requested: list[FrequencyCode]
    available: list[FrequencyCode]
    status: Literal["match", "conflict", "unknown"]


class BasisDetail(TypedDict):
    condition_id: str
    status: Literal["match", "conflict", "unknown"]
    role: Literal["splits_table", "in_table"]
    source: Literal["title", "path", "item", "unit", "none"]


class UnitDetail(TypedDict):
    condition_id: str
    status: Literal["same", "rescale", "differ", "unknown"]  # scale and base compared, not substrings


class ExcludeDetail(TypedDict):
    condition_id: str
    complement_found: bool
    items: list[str]


class CrossAxisDetail(TypedDict):
    support: Literal["supported", "single_axis", "unknown"]
    reason: str | None
    axes: dict[str, list[str]]  # condition ID -> axis IDs that can carry it


class RegionDetail(TypedDict):
    status: str


class MeasureKindDetail(TypedDict):
    status: str


class IndicatorDetail(TypedDict):
    """``available`` false = no indicator phrase could be taken from the question (result None, stance unknown);
    ``result`` "none" = a phrase was taken and the table does not match it. The two are different values."""

    result: Literal["match", "partial", "none"] | None
    available: bool
    by_field: dict[str, str]  # title, item, catalog_path, ... -> match/partial/none
    phrase: str | None
    coverage: float | None
    origin: str | None  # where the phrase came from (rule, model)


class BreakdownRow(TypedDict):
    text: str  # the breakdown entry as the interpretation gives it
    word: str  # its catalog name (the exact term of the one fragment holding it, plan 07 r4 9-4)
    kind: Literal["lower"]  # a named lower item ("X별" axes are left to the basis and cross_axis checks)
    found: bool  # a value of the table
    under: bool | None  # found: under the indicator in that table (None: no indicator name or no reading)


class BreakdownDetail(TypedDict):
    """Plan 07 S1 (record mode): match = every named lower item found (none shown elsewhere than under the
    indicator), partial = some, none = none."""

    status: Literal["match", "partial", "none"]
    rows: list[BreakdownRow]


DETAIL_BY_NAME: dict = {
    "confirmed": ConfirmedDetail, "matched_items": MatchedItemsDetail, "period": PeriodDetail,
    "frequency": FrequencyDetail, "basis": BasisDetail, "unit": UnitDetail, "exclude": ExcludeDetail,
    "cross_axis": CrossAxisDetail, "region": RegionDetail, "measure_kind": MeasureKindDetail,
    "indicator": IndicatorDetail, "breakdown": BreakdownDetail,
}


class Check(TypedDict):
    name: CheckName
    state: CheckState
    source_status: str | None  # old judgement status as it was (match/conflict/unknown/covered/...)
    verdict: Verdict
    evidence_ids: list[str]
    detail: dict  # shape by name: DETAIL_BY_NAME


class EvidenceEntry(TypedDict):
    requirement_id: str
    table_id: str
    checks: list[Check]


class EvidenceRow(TypedDict):
    kind: str
    stance: Stance
    table_id: str | None
    text: str | None


class EvidenceDoc(TypedDict):
    evidence: list[EvidenceEntry]
    evidence_rows: dict[str, EvidenceRow]


# ---------------------------------------------------------------- table selection (plan 04, 5-2)

DecisionState = Literal["auto", "confirm", "no_table"]


class Covers(TypedDict):
    """Requested cells one table covers (plan 04, 4-5): target names, period segments ("YYYY-MM~YYYY-MM" or
    "latest"), frequency codes, basis condition IDs of split axes."""

    targets: list[str]
    periods: list[str]
    frequencies: list[str]
    basis: list[str]


class SelectionTable(TypedDict):
    table_id: str
    covers: Covers
    org_id: NotRequired[str | None]  # carried for the shared selection field (agent/common.py)
    table_name: NotRequired[str | None]


class Unspecified(TypedDict):
    """A dimension the question did not set, with the tables put in for it (decision 5, at most 3)."""

    dimension: str  # basis axis names of the differing bracket words ("price", "seasonal+stock") or "qualifier"
    table_ids: list[str]
    more: NotRequired[bool]  # more tables than the cap
    # representative policy (decision 5 as changed): the kind of dimension and what was left out
    kind: NotRequired[str]  # mixed_basis, other ("+"-joined)
    other_table_ids: NotRequired[list[str]]
    other_values: NotRequired[list[str]]


class SelectionBundle(TypedDict):
    """Plan 19 contract joint item 6 (agreed with B, 10/21): the period bundle of one selection, handed to B as is.
    ``context`` and every ``assignments`` entry follow the shared period package (``period_core.check_context`` /
    ``check_assignment``: no rule of it is copied here); ``bundle_id`` equals ``context.bundle_id``; ``assignments``:
    table ID (one of the selection's tables) -> its one continuous segment [{start, end}] (a table not named: the whole
    request)."""

    bundle_id: str
    context: dict
    assignments: dict[str, list[dict]]


class Selection(TypedDict):
    requirement_id: str
    tables: list[SelectionTable]
    unspecified: list[Unspecified]
    status: DecisionState
    model_step: Literal["pending", "skipped"]  # 3.6: pending while the model is off and families compete
    reasons: list[str]
    alternatives: NotRequired[list[list[str]]]  # other contending table groups (ask_user choose_table options)
    differences: NotRequired[list[dict]]  # 3.6 difference rows {family, table_ids, only_axes, only_values}
    signals: NotRequired[dict]  # counts recorded for measurement (pool, candidates with evidence, cells ...)
    # plan 19 contract joint item 6: optional public fields for B (absent: none; filled by plan 17, not here)
    bundle: NotRequired[SelectionBundle]
    # the model-reference candidates kept for B, never fetched: the element shape of
    # ``signals.facts.tables[t].indicator.reference_candidates`` (select/facts.py: raw mention candidates)
    reference_candidates: NotRequired[list[MentionCandidate]]


class SelectionGroup(TypedDict):
    requirement_ids: list[str]
    table_ids: list[str]


RouteKind = Literal["proceed", "ask_interpretation", "ask_selection", "no_table"]


# Plan 11 R1 (C, 7-8, F7, 8-6 item 1): what answering a question does.
# set_condition: the answer becomes a confirmed condition of the requirements (dimension, value, polarity);
# change_reading: the answer fixes one reading of the interpretation (the analysis is read again);
# pick_assignment: the answer fixes the table assignment of the requirements (the evidence is checked again);
# rephrase: the answer is a new question text (interpretation from the start);
# drop_requirement: the answer leaves the requirements out (a partial answer).
EffectKind = Literal["set_condition", "change_reading", "pick_assignment", "rephrase", "drop_requirement"]
TABLE_EFFECT_KINDS = ("set_condition", "change_reading", "pick_assignment")  # options are table bundles


class QuestionVersion(TypedDict):
    """Plan 11 R1 (7-8): content versions of the analysis and the selections a question was asked over; an answer
    carrying another version is not applied (asked again)."""

    analysis: str
    selection: str


class OptionEffect(TypedDict):
    """Plan 11 R1 (C): the condition an option stands for (``value`` None when the option is no single value)."""

    dimension: str
    value: str | None
    polarity: Polarity


class RouteOption(TypedDict):
    """Plan 11 R1 (C, 7-8): one option of a question of the new contract. ``assignment``: requirement ID -> table IDs
    the option gives that requirement (requirements not named keep theirs; an empty list leaves the requirement out,
    ``drop_requirement``; empty for ``change_reading``, whose readings number their requirements anew). The fields of
    plan 10 (``value``, ``table_ids``, ``tables``, ``complete``, ``default``, ``source``) stay as they were."""

    id: str
    label: str
    assignment: dict[str, list[str]]
    effect: OptionEffect
    value: NotRequired[list[str]]
    table_ids: NotRequired[list[str]]
    tables: NotRequired[list[dict]]
    complete: NotRequired[bool]
    default: NotRequired[bool]
    source: NotRequired[str]


class RouteQuestion(TypedDict):
    """Plan 10 section 4: one question of a table ask: the requirements it is for, the dimension asked (``family``,
    a mark dimension, or the ask reason for a flat table list), its values, the options (value -> cover bundle
    {value, table_ids, tables, complete, default, source}, at most 5) and ``deferred`` (past the three questions of
    one turn: asked later, those requirements go on with confirm).

    Plan 11 R1 (C, 7-8): ``id``, ``version``, ``cause`` (the ask cause code), ``open`` (fewer than two options: asked
    openly, never a way to go on), ``effect_kind`` and options of shape ``RouteOption``. Optional so that a question
    of the plan 10 shape stays valid; ``check.check_selection_route`` requires them all once one is present."""

    requirement_ids: list[str]
    dimension: str
    values: list[str]
    options: list[dict]
    deferred: bool
    id: NotRequired[str]
    version: NotRequired[QuestionVersion]
    cause: NotRequired[str]
    open: NotRequired[bool]
    effect_kind: NotRequired[EffectKind]


QUESTION_CONTRACT_FIELDS = ("id", "version", "cause", "open", "effect_kind")  # plan 11 R1: present all or none


class SelectionAnswer(TypedDict):
    """Plan 11 R1 (C, 7-8): one answer: the question, the chosen option or a text (exactly one of the two), and the
    version of the question it answers. A later answer to the same question replaces an earlier one."""

    question_id: str
    option_id: NotRequired[str]
    text: NotRequired[str]
    version: QuestionVersion


class SelectionRoute(TypedDict):
    """Plan 07 r4 S5: the question-level way after the selection recheck (agent/question_route.py). ``options``:
    at most 5; a table option {requirement_id, table_ids, tables[{table_id, table_name, values}], default, source},
    an interpretation option {requirement_id, name | text, default, source}. ``default``: what an automatic run goes
    on with (the current reading and choice)."""

    kind: RouteKind
    requirement_ids: list[str]
    options: list[dict]
    default: dict | None
    reasons: list[str]
    partial: bool  # some requirements are no_table or out of scope; the way is the others'
    confirm: bool  # going on needs a confirmation (any confirm reason or an ask)
    questions: NotRequired[list[RouteQuestion]]  # plan 10: the questions of one turn (``options`` kept as before)
