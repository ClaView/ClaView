"""B request and result contract (plan 03 r2, section 1; user decisions of 2026-10-20).

The request carries, per requirement (scope), the tables the table selection settled and optional
hints read from the question interpretation. Both producers end in this one shape: the requirement-
level table selection (claview_v3, `adapter_v3`) and a question-level one (`adapter_question`, one
scope for the whole question). B works per scope only.

The result is per scope: a status the router acts on, the series, where the period and frequency
came from, the defaults B assumed (it does not ask for a missing period or frequency), and issues
with a failure kind and a resume point. `fetch_selections` flattens the series of ok scopes into the
`selections` list the current fetch reads.

Only shapes and checks live here; adapters fill the request and the B graph fills the result.
"""

from __future__ import annotations

import re
from typing import Any, Literal, NotRequired, TypedDict

Json = dict[str, Any]

# Version of this contract, written by the adapters into `BRequest.contract_version`.
# 2026-10-21.1: requirement structure slot (`hints.structure`), adapter notices, open value-row
# negation under a structure, bundle checks against the scope's tables.
# 2026-10-21.2: the structure slot follows A's agreed `DecompositionStructure`
# (structure-2026-10-21.1): settled requirement IDs, a version gate, `structure_reason`.
# 2026-10-21.3: A's public assumptions (`BRequest.a_assumptions`, returned unchanged at the
# result's top level) and the `supersedes` rule of stage-B shared assumptions.
B_CONTRACT_VERSION = "b-contract/2026-10-21.3"

# A's public assumption envelopes B accepts (user's B brief "완료 2" of 2026-10-21)
A_ASSUMPTION_VERSIONS = ("a-assumptions/2026-10-21.1",)
ASSUMPTION_SOURCES = ("question", "user", "catalog", "default")

# Structure versions of the interpretation B reads (A plan 19 contract document of 2026-10-21;
# A's `shapes.STRUCTURE_VERSIONS`). Another version is an input contract error (user decision of
# 10/21 05:13): B never falls back to the old question-level words for it.
STRUCTURE_VERSIONS = ("structure-2026-10-21.1",)
# why a rules record has no structure (A `StructureReason`), and the adopted sources that are the
# rules (A `DecompositionRecord.applied_source`: "rules", "model", "rules_fallback")
STRUCTURE_REASONS = ("rules_structure_unrecorded",)
RULES_SOURCES = ("rules", "rules_fallback")
ADOPTED_MODEL_SOURCE = "model"  # the only source that keeps a structure
STRUCTURE_BREAKDOWN_KINDS = ("values", "axis", "descendants")
STRUCTURE_POLARITY_KINDS = ("exclude", "replace")
STRUCTURE_SCOPE_STATUSES = ("clear", "uncertain")
# A's MentionCandidate and ConditionBinding value sets (contract document 2, A shapes)
CANDIDATE_ROLES = ("indicator", "target", "basis", "breakdown", "table_name", "unclassified")
CANDIDATE_PATHS = ("exact", "dictionary", "lexical")
CANDIDATE_TYPES = (
    "series_name",
    "indicator",
    "target",
    "basis",
    "total_header",
    "compound",
    "unknown",
    "item_row",
    "axis_name",
)
BINDING_SCOPES = ("question", "requirements", "unresolved", "name_content")

SCOPE_STATUSES = ("ok", "ask", "unresolved", "table_unsuitable")
ISSUE_KINDS = ("input", "condition", "model", "validation", "data")
# "none": the run ends with the error (an input contract error, a broken catalog): no step of A
# or B can repair it by running again.
RESUME_POINTS = ("A_reselect", "ask_user", "B_context", "B_select", "fetch", "none")
# Where a period or frequency came from: the agreed condition-slot sources (plan 03 r2, 10).
# `default` = assumed by B (a missing period is not asked: the recent window of the frequency; a
# missing frequency: the longest one) and then also listed in `assumptions`. The detailed rule
# ("question_word", "period_unit", "table_single", "longest_frequency", "recent_years:M=3", ...)
# is kept in the record's `rule`.
PERIOD_SOURCES = ("question", "catalog", "default", "user")
FREQUENCY_SOURCES = PERIOD_SOURCES
DECIDED_BY = ("code", "model", "expand")
DECISIONS = ("auto", "confirm")
# What B may ask. Frequency and period are never asked (they are assumed and recorded).
ASK_KINDS = ("series_count", "unit", "condition")
ASSUMPTION_DIMENSIONS = ("period", "frequency")
PERIOD_FREQUENCIES = ("Y", "H", "Q", "M")


class BTable(TypedDict):
    org_id: str
    table_id: str
    table_name: str | None


class ValueMention(TypedDict):
    """A table value the table selection matched in the question (code-first mapping, plan M4)."""

    table_id: str
    obj_id: str | None  # "ITEM" for the item axis
    value_id: str  # the table's own code (equal to B metadata codes)
    name: str | None
    method: str  # exact, exact_with_qualifier, ...
    role: str | None  # target, series_name, indicator, ...
    relation: str | None  # mention, subject_candidate, ...
    parents: list[str]  # ancestor names the table selection recorded for the value (may be empty)


class ValueRow(TypedDict):
    """A catalog value a mention of the question may stand for, on one of the scope's tables
    (from `analysis.mentions[].candidates`): target, basis, indicator, breakdown or ITEM rows."""

    mention_id: str
    table_id: str
    obj_id: str | None  # "ITEM" for an ITEM row
    value_id: str | None
    value_name: str | None
    role: str  # indicator, target, basis, breakdown
    path: str  # exact, dictionary, lexical
    # the mention's words were taken out by a negation (question-level words, rules and older
    # outputs); None under a requirement structure (`hints.structure`): the adapter does not
    # decide it, and an open polarity is never a settled exclusion
    negated: bool | None
    bound: bool  # the mention belongs to this requirement (False: bound to no requirement)


class ScopeHints(TypedDict, total=False):
    """Every field is optional: a missing field means not given; B then reads the question.

    Hints are used for code-side fixing and cross-checks only; they are not put in the model's
    context (user decision 5)."""

    text: str  # the requirement's words in the question
    indicator: str | None  # catalog name of the indicator
    targets: list[str]
    breakdown: list[str]
    conditions: list[Json]  # own + shared conditions {id, kind, quote, value, attrs}
    ambiguous_condition_ids: list[str]  # carried, never applied
    negated: list[str]  # words a negation took out of the question ("A가 아니라 B": A)
    calc_hints: list[Json]  # change/rate marks of the question ({quote, kind}), question level
    # inference hints: a frequency read from a period's unit, not stated as a frequency
    # ({kind: frequency, value, from_condition_id, rule: period_unit}); absent = B reads the units
    inferred: list[Json]
    value_mentions: list[ValueMention]
    # A's condition slots of the scope's conditions {source, confirmed, period_diagnosis?}; A's
    # `applied` is never carried (B fills it in the result)
    condition_fields: dict[str, Json]
    value_rows: list[ValueRow]
    # model-reference candidates of the scope's tables (never fetched, never an alternative)
    reference_candidates: list[Json]
    covers: dict[str, Json]  # table_id -> covers of the table selection
    a_period_request: Json | None  # the table selection's reading of the period (comparison only)
    # the interpretation's own structure for this requirement (A plan 19 `DecompositionStructure`
    # of a supported version, `STRUCTURE_VERSIONS`): {version, source, requirement (A's
    # RequirementStructure of this settled ID: targets, breakdowns, basis, context), polarity
    # (links naming this requirement or none = every requirement), confirmed_effects (naming
    # it), condition_bindings (from `decomposition.bindings`, the attribution's source of
    # truth)}, carried as given. Only confirmed effects are settled; an `uncertain` link or a
    # negation without a confirmed effect is never an exclusion or a refutation. B does not
    # read it for fixing or excluding yet. When present, `breakdown` and `negated` stay empty
    # and value rows leave `negated` None.
    structure: Json
    # why the interpretation recorded no structure (`structure: None`, e.g.
    # "rules_structure_unrecorded"); the old fields are read
    structure_reason: str


class BScope(TypedDict):
    scope_id: str
    tables: list[BTable]  # every table of the selected bundle, in order
    decision: Literal["auto", "confirm"]
    hints: ScopeHints
    requirement_key: NotRequired[Json]  # {reading_id: str | None, requirement_id}
    # {bundle_id, context: {bundle_id, anchor_source, anchor, simultaneous?}, assignments:
    # {table_id: [{start, end} as YYYY-MM]}} (the table selection's bundle; absent = none)
    bundle: NotRequired[Json]
    # the first alternative bundle of the table selection, kept apart and never fetched
    alternatives: NotRequired[list[Json]]
    # what the adapter noticed while reading the selection ({code, detail}); carried into the
    # scope result's notices as kind "adapter"
    adapter_notices: NotRequired[list[Json]]


class SkippedScope(TypedDict):
    scope_id: str
    reason: str  # no_table, not_selected


class BRequest(TypedDict):
    question: str
    as_of: str | None
    scopes: list[BScope]
    skipped: list[SkippedScope]
    route: Json | None  # {kind, confirm, partial, reasons, requirement_ids} of the table selection
    relations: list[Json]
    table_group_hint: list[list[str]]
    source: str  # producer of the request ("claview_v3", "question_level")
    # input contract errors found while building the request ({code, detail, scope_id?}); a scope
    # named here (or every scope, for a request-level error) ends with the error
    input_errors: NotRequired[list[Json]]
    contract_version: NotRequired[str]  # B_CONTRACT_VERSION of the adapter that built it
    # A's public assumptions {version, items} (`check_a_assumptions`); absent = an older input.
    # B never reads them for its own decisions and returns them unchanged in the result.
    a_assumptions: NotRequired[Json]


class Issue(TypedDict):
    code: str
    kind: str  # ISSUE_KINDS
    retryable: bool
    resume_at: str  # RESUME_POINTS
    table_id: NotRequired[str | None]
    series_id: NotRequired[str | None]


class PeriodRecord(TypedDict):
    source: str  # PERIOD_SOURCES
    requested: Json | None  # what the question asked, as read; None when it named no period
    applied: Json | None  # {frequency, start, end} sent to fetch
    rule: NotRequired[str | None]
    notice: NotRequired[Json | None]
    # month coverage of the scope over its tables: {requested, covered, unmet} lists of
    # {start, end} as YYYY-MM (the union of the tables' covered parts and what no table covers)
    coverage_months: NotRequired[Json]


class FrequencyRecord(TypedDict):
    source: str  # FREQUENCY_SOURCES
    chosen: str | None
    alternatives: list[str]
    rule: NotRequired[str | None]


class Assumption(TypedDict):
    dimension: str  # ASSUMPTION_DIMENSIONS
    value: Any  # what B assumed ("Y", {"frequency", "start", "end"}, ...)
    rule: str  # the rule that gave it ("longest_frequency", "recent_years:Y=10", ...)


class SharedAssumption(TypedDict):
    """The shared assumption contract (stage B entries): what B adopted without the question
    stating it. The final merge with A's seeds is the A->B link's job."""

    assumption_id: str
    requirement_ids: list[str]
    refs: Json  # {segment_ids, mention_ids, condition_ids}
    reason_code: str
    adopted: Json  # {value, interpretation}
    source: str  # question, user, catalog, default
    confirmed: bool
    stage: str  # "B"
    supersedes: NotRequired[str | None]
    fragment_ids: NotRequired[list[str]]


class Ask(TypedDict):
    kind: str  # ASK_KINDS
    message: str
    options: list[Json]


class BScopeResult(TypedDict):
    scope_id: str
    status: str  # SCOPE_STATUSES
    series: list[Json]  # selections in the shape fetch reads, each with scope_id
    period: PeriodRecord | None
    frequency: FrequencyRecord | None
    assumptions: list[Assumption]
    decided: dict[str, str]  # axis -> DECIDED_BY
    notices: list[Json]
    issues: list[Issue]
    ask: NotRequired[Ask | None]
    # condition slots after B: {condition_id: {source, confirmed, applied, period_diagnosis?}}
    condition_fields: NotRequired[dict[str, Json]]
    shared_assumptions: NotRequired[list[SharedAssumption]]


# ------------------------------------------------------- failure codes -> kind and resume point

# Every StageFailure code raised by B and fetch (a test reads the source files and checks that
# this table covers them).
# (kind, resume_at, retryable)
ISSUE_OF_CODE: dict[str, tuple[str, str, bool]] = {
    # input: the table or the A result cannot be used as given
    "a_result_missing": ("input", "A_reselect", False),
    "invalid_a_result": ("input", "A_reselect", False),
    "metadata_missing": ("input", "A_reselect", False),
    "metadata_incomplete": ("input", "A_reselect", False),
    "metadata_code_conflict": ("input", "A_reselect", False),
    "metadata_parent_cycle": ("input", "A_reselect", False),
    "invalid_metadata_axis": ("input", "A_reselect", False),
    "invalid_metadata_period": ("input", "A_reselect", False),
    # the whole catalog is broken: reselecting tables would hit it again (user decision 10/20)
    "invalid_catalog_path": ("input", "none", False),
    "duplicate_catalog_id": ("input", "none", False),
    "duplicate_metadata_id": ("input", "none", False),
    # the request breaks the input contract (malformed condition, bad bundle context)
    "malformed_condition": ("input", "none", False),
    "anchor_after_table_latest": ("input", "none", False),
    # a table's assignment of more than one segment (claview_period.check_assignment)
    "invalid_assignment": ("input", "none", False),
    "input_contract_error": ("input", "none", False),
    "table_unsuitable": ("input", "A_reselect", False),
    # condition: the question leaves something open or asks for what the table cannot give
    "conditions_incomplete": ("condition", "ask_user", False),
    "metadata_refresh_required": ("condition", "ask_user", False),
    "broad_scope_context_incomplete": ("condition", "ask_user", False),
    "context_too_large": ("condition", "ask_user", False),
    "calculation_definition_required": ("condition", "ask_user", False),
    "selection_count_requires_confirmation": ("condition", "ask_user", False),
    "unit_conflict": ("condition", "ask_user", False),
    # the interpretation could not resolve the period: never replaced by the default window
    "period_unsupported": ("condition", "ask_user", False),
    # the request lies outside the table's coverage: another table or edition may hold it
    "no_data_in_range": ("condition", "A_reselect", False),
    # model: the selection call failed or answered unusably
    "hcx_key_missing": ("model", "B_select", False),
    "hcx_transport_error": ("model", "B_select", True),
    "hcx_invalid_response": ("model", "B_select", True),
    "hcx_service_error": ("model", "B_select", True),
    "hcx_sensitive_response": ("model", "B_select", False),
    "output_truncated": ("model", "B_select", False),
    "unexpected_finish_reason": ("model", "B_select", True),
    "invalid_model_output": ("model", "B_select", True),
    "fixture_response_missing": ("model", "B_select", False),
    # B re-asks the model itself on these two (Config.max_expand_retries); when the code reaches the
    # result, that retry budget is spent, so the router should not re-run the same selection.
    "expand_target_has_no_children": ("model", "B_select", False),
    "expand_multiple_axes": ("model", "B_select", False),
    # validation: the answer does not hold against the metadata or the question
    "invalid_period": ("validation", "B_context", False),
    "explicit_period_mismatch": ("validation", "B_select", False),
    "explicit_frequency_mismatch": ("validation", "B_select", False),
    "duplicate_selection": ("validation", "B_select", False),
    "invalid_b_result": ("validation", "B_select", False),
    "invalid_series_id": ("validation", "B_select", False),
    "duplicate_series_id": ("validation", "B_select", False),
    "metadata_label_or_unit_mismatch": ("validation", "B_select", False),
    # data: the statistics request or its response
    "kosis_key_missing": ("data", "fetch", False),
    "kosis_transport_error": ("data", "fetch", True),
    "kosis_invalid_response": ("data", "fetch", True),
    "kosis_service_error": ("data", "fetch", False),
    "kosis_credential_reflected": ("data", "fetch", False),
    "unexpected_response_series": ("data", "fetch", False),
    "unexpected_response_period": ("data", "fetch", False),
    "duplicate_response_period": ("data", "fetch", False),
    "response_unit_conflict": ("data", "fetch", False),
    "data_client_missing": ("data", "fetch", False),
    # fetch issues that do not stop the fetch (normalize_rows): the series is kept and reported
    "missing_value": ("data", "fetch", False),
    "value_parse_error": ("data", "fetch", False),
    "missing_periods": ("data", "fetch", False),
}

# Codes built at run time ("hcx_http_429"): prefix -> (kind, resume_at); retryable for 429 and 5xx.
ISSUE_OF_PREFIX: dict[str, tuple[str, str]] = {
    "hcx_http_": ("model", "B_select"),
    "kosis_http_": ("data", "fetch"),
}


def issue_of(code: str, **where: Any) -> Issue:
    """The issue entry of a failure code. Unknown codes become `validation` at `B_select`."""
    if code in ISSUE_OF_CODE:
        kind, resume_at, retryable = ISSUE_OF_CODE[code]
    else:
        for prefix, (kind, resume_at) in ISSUE_OF_PREFIX.items():
            if code.startswith(prefix):
                status = code.removeprefix(prefix)
                retryable = status == "429" or status.startswith("5")
                break
        else:
            kind, resume_at, retryable = "validation", "B_select", False
    issue: Issue = {"code": code, "kind": kind, "retryable": retryable, "resume_at": resume_at}
    for key in ("table_id", "series_id"):
        if key in where:
            issue[key] = where[key]
    return issue


# ---------------------------------------------------------------- flattening for fetch

# The exact keys fetch.validate_b_result accepts for one selection.
FETCH_SELECTION_KEYS = (
    "series_id",
    "label",
    "org_id",
    "table_id",
    "item_id",
    "dimensions",
    "period",
    "unit",
)
SERIES_KEYS = {*FETCH_SELECTION_KEYS, "scope_id"}


def _coordinate(row: Json) -> tuple:
    """What fetch treats as one series (stages.selection_key): table, item, codes and period."""
    return (
        row["org_id"],
        row["table_id"],
        row["item_id"],
        tuple(sorted((axis, value["code"]) for axis, value in row["dimensions"].items())),
        row["period"]["frequency"],
        row["period"]["start"],
        row["period"]["end"],
    )


def series_fetched_as(results: list[BScopeResult]) -> dict[str, str]:
    """series_id -> the series_id fetched for its coordinate (the first ok series holding it).

    Two scopes may ask for the very same series; fetch accepts each coordinate once."""
    first: dict[tuple, str] = {}
    out = {}
    for result in results:
        if result["status"] != "ok":
            continue
        for row in result["series"]:
            out[row["series_id"]] = first.setdefault(_coordinate(row), row["series_id"])
    return out


def fetch_selections(results: list[BScopeResult]) -> list[Json]:
    """The series of the ok scopes in the shape fetch reads: scope_id dropped, each coordinate
    once (the first series holding it), order kept. See `series_fetched_as`, `series_scopes`."""
    fetched = set(series_fetched_as(results).values())
    return [
        {k: row[k] for k in FETCH_SELECTION_KEYS}
        for result in results
        if result["status"] == "ok"
        for row in result["series"]
        if row["series_id"] in fetched
    ]


def series_scopes(results: list[BScopeResult]) -> dict[str, list[str]]:
    """fetched series_id -> the scopes whose series it serves, in scope order."""
    owner = {
        row["series_id"]: result["scope_id"]
        for result in results
        if result["status"] == "ok"
        for row in result["series"]
    }
    out: dict[str, list[str]] = {}
    for series_id, fetched in series_fetched_as(results).items():
        scopes = out.setdefault(fetched, [])
        if owner[series_id] not in scopes:
            scopes.append(owner[series_id])
    return out


# ---------------------------------------------------------------- checks

# NotRequired is not seen through postponed annotations: the optional keys are named here.
_REQUEST_ALL = set(BRequest.__annotations__)
_REQUEST_KEYS = _REQUEST_ALL - {"input_errors", "contract_version", "a_assumptions"}
_SCOPE_ALL = set(BScope.__annotations__)
_SCOPE_KEYS = _SCOPE_ALL - {"requirement_key", "bundle", "alternatives", "adapter_notices"}
_HINT_KEYS = set(ScopeHints.__annotations__)
_TABLE_KEYS = set(BTable.__annotations__)
_MENTION_KEYS = set(ValueMention.__annotations__)
_CONDITION_KEYS = {"id", "kind", "quote", "value", "attrs"}
_RESULT_ALL = set(BScopeResult.__annotations__)
_RESULT_KEYS = _RESULT_ALL - {"ask", "condition_fields", "shared_assumptions"}
_VALUE_ROW_KEYS = set(ValueRow.__annotations__)
_SHARED_ALL = set(SharedAssumption.__annotations__)
_SHARED_REQUIRED = _SHARED_ALL - {"supersedes", "fragment_ids"}
_A_FIELD_KEYS = {"source", "confirmed", "period_diagnosis"}
_HINT_TEXT_LISTS = ("targets", "breakdown", "ambiguous_condition_ids", "negated")
_PERIOD_KEYS = ("frequency", "start", "end")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _texts(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _optional_text(value: Any) -> bool:
    return value is None or isinstance(value, str)


_PERIOD_TEXT = {
    "Y": re.compile(r"\d{4}"),
    "H": re.compile(r"\d{4}0[12]"),
    "Q": re.compile(r"\d{4}0[1-4]"),
    "M": re.compile(r"\d{4}(0[1-9]|1[0-2])"),
}


def _period(value: Any) -> bool:
    """A {frequency, start, end} period in the table format of its frequency, start <= end."""
    if not isinstance(value, dict) or not set(_PERIOD_KEYS) <= set(value):
        return False
    freq, start, end = value["frequency"], value["start"], value["end"]
    if not isinstance(freq, str) or freq not in _PERIOD_TEXT:
        return False
    pattern = _PERIOD_TEXT[freq]
    return (
        isinstance(start, str)
        and isinstance(end, str)
        and bool(pattern.fullmatch(start))
        and bool(pattern.fullmatch(end))
        and start <= end
    )


def _dimensions(value: Any) -> bool:
    """{axis: {code, name}} with text codes, as the B result and fetch carry them."""
    return isinstance(value, dict) and all(
        isinstance(axis, str)
        and isinstance(v, dict)
        and _text(v.get("code"))
        and _optional_text(v.get("name"))
        for axis, v in value.items()
    )


def _check_mention(mention: Any, own_tables: set, path: str) -> list[str]:
    if not isinstance(mention, dict) or set(mention) != _MENTION_KEYS:
        return [f"{path}: keys differ from the contract"]
    if not _text(mention["value_id"]) or not _text(mention["method"]):
        return [f"{path}: value_id or method empty"]
    if not all(_optional_text(mention[k]) for k in ("obj_id", "name", "role", "relation")):
        return [f"{path}: a text field is not text"]
    if not _texts(mention["parents"]):
        return [f"{path}.parents: not a list of names"]
    if not _text(mention["table_id"]) or mention["table_id"] not in own_tables:
        return [f"{path}: table not in the scope"]
    return []


def _check_condition(condition: Any, path: str) -> list[str]:
    if not isinstance(condition, dict) or set(condition) != _CONDITION_KEYS:
        return [f"{path}: keys differ from the contract"]
    if not _text(condition["id"]) or not _text(condition["kind"]):
        return [f"{path}: id or kind empty"]
    if not isinstance(condition["attrs"], dict):
        return [f"{path}.attrs: not an object"]
    if not _optional_text(condition["quote"]) or not _optional_text(condition["value"]):
        return [f"{path}: quote or value is not text"]
    return []


def _check_hints(hints: Any, own_tables: set, path: str) -> list[str]:
    if not isinstance(hints, dict) or not set(hints) <= _HINT_KEYS:
        return [f"{path}.hints: unknown keys or not an object"]
    out = []
    for key in _HINT_TEXT_LISTS:
        if key in hints and not _texts(hints[key]):
            out.append(f"{path}.hints.{key}: not a list of text")
    for key in ("conditions", "value_mentions"):
        if key in hints and not isinstance(hints[key], list):
            out.append(f"{path}.hints.{key}: not a list")
    if "inferred" in hints and not (
        isinstance(hints["inferred"], list)
        and all(isinstance(h, dict) and _text(h.get("kind")) for h in hints["inferred"])
    ):
        out.append(f"{path}.hints.inferred: not a list of {{kind, value, ...}}")
    if "calc_hints" in hints and not (
        isinstance(hints["calc_hints"], list)
        and all(isinstance(h, dict) and _text(h.get("kind")) for h in hints["calc_hints"])
    ):
        out.append(f"{path}.hints.calc_hints: not a list of {{quote, kind}}")
    if "covers" in hints and not (
        isinstance(hints["covers"], dict)
        and all(isinstance(v, dict) for v in hints["covers"].values())
    ):
        out.append(f"{path}.hints.covers: not an object of objects")
    for key in ("text", "indicator"):
        if key in hints and not _optional_text(hints[key]):
            out.append(f"{path}.hints.{key}: not text")
    for key in ("value_rows", "reference_candidates"):
        if key in hints and not (
            isinstance(hints[key], list) and all(isinstance(r, dict) for r in hints[key])
        ):
            out.append(f"{path}.hints.{key}: not a list of objects")
    if "condition_fields" in hints and not (
        isinstance(hints["condition_fields"], dict)
        and all(isinstance(v, dict) for v in hints["condition_fields"].values())
    ):
        out.append(f"{path}.hints.condition_fields: not an object of objects")
    if "a_period_request" in hints and not (
        hints["a_period_request"] is None or isinstance(hints["a_period_request"], dict)
    ):
        out.append(f"{path}.hints.a_period_request: not an object")
    if "structure" in hints:
        problems = check_structure(hints["structure"])
        if problems:
            out += [f"{path}.hints.{p}" for p in problems]
        elif hints.get("breakdown") or hints.get("negated"):
            out.append(
                f"{path}.hints: breakdown/negated words are not re-applied under a structure"
            )
    if "structure_reason" in hints and (
        not _text(hints["structure_reason"]) or "structure" in hints
    ):
        out.append(f"{path}.hints.structure_reason: text, and only without a structure")
    if out:
        return out
    for k, mention in enumerate(hints.get("value_mentions", [])):
        out += _check_mention(mention, own_tables, f"{path}.hints.value_mentions[{k}]")
    ids = []
    for k, condition in enumerate(hints.get("conditions", [])):
        problems = _check_condition(condition, f"{path}.hints.conditions[{k}]")
        out += problems
        if not problems:
            ids.append(condition["id"])
    if len(ids) != len(set(ids)):
        out.append(f"{path}.hints.conditions: duplicate id")
    if set(ids) & set(hints.get("ambiguous_condition_ids", [])):
        out.append(f"{path}.hints: an ambiguous condition is also applied")
    for cid, fields in hints.get("condition_fields", {}).items():
        if cid not in ids:
            out.append(f"{path}.hints.condition_fields[{cid}]: not a condition of the scope")
        elif not set(fields) <= _A_FIELD_KEYS:
            out.append(f"{path}.hints.condition_fields[{cid}]: unknown keys (applied is B's)")
    for k, row in enumerate(hints.get("value_rows", [])):
        if set(row) != _VALUE_ROW_KEYS:
            out.append(f"{path}.hints.value_rows[{k}]: keys differ from the contract")
        elif not isinstance(row["table_id"], str) or row["table_id"] not in own_tables:
            out.append(f"{path}.hints.value_rows[{k}]: table not in the scope")
        elif not isinstance(row["bound"], bool) or not (
            isinstance(row["negated"], bool) or (row["negated"] is None and "structure" in hints)
        ):
            out.append(
                f"{path}.hints.value_rows[{k}]: bound a boolean, negated a boolean "
                "(None only under a requirement structure)"
            )
    for k, row in enumerate(hints.get("reference_candidates", [])):
        if not isinstance(row.get("table_id"), str) or row["table_id"] not in own_tables:
            out.append(f"{path}.hints.reference_candidates[{k}]: table not in the scope")
    return out


def check_bundle_shape(bundle: Any, table_ids: Any) -> list[str]:
    """Problems of a bundle's shape against its scope's tables, short of the assigned segments
    themselves: A's `SelectionBundle` exactly (bundle_id, context object, assignments object, no
    other field), a valid context with the same bundle_id, assignments of the scope's own
    tables. The adapter, the runner and `check_request` all use it. Never raises.
    """
    from claview_period import check_context

    if not isinstance(bundle, dict) or not _text(bundle.get("bundle_id")):
        return ["bundle: needs a bundle_id"]
    out = [f"bundle: unknown field '{k}'" for k in sorted(set(bundle) - _BUNDLE_KEYS, key=str)]
    out += [f"bundle: missing field '{k}'" for k in sorted(_BUNDLE_KEYS - set(bundle))]
    context = bundle.get("context")
    if not isinstance(context, dict):
        out.append("bundle.context: not an object")
    else:
        out += [f"bundle.{p}" for p in check_context(context)]
        if context.get("bundle_id") != bundle["bundle_id"]:
            out.append("bundle.context.bundle_id: differs from the bundle")
    assignments = bundle.get("assignments")
    if not isinstance(assignments, dict):
        return out + ["bundle.assignments: not an object"]
    own = (
        {t for t in table_ids if isinstance(t, str)} if isinstance(table_ids, list | set) else set()
    )
    for table_id in assignments:
        if table_id not in own:
            out.append(f"bundle.assignments[{table_id}]: table not in the scope")
    return out


def bundle_segment_problems(bundle: Any) -> list[str]:
    """Problems of each table's assigned segments (the shared `check_assignment`: one continuous
    segment per table). Never raises."""
    from claview_period import check_assignment

    assignments = bundle.get("assignments", {}) if isinstance(bundle, dict) else {}
    if not isinstance(assignments, dict):
        return []
    return [
        f"bundle.assignments[{table_id}].{p}"
        for table_id, segments in assignments.items()
        for p in check_assignment(segments)
    ]


# ---- the interpretation's structure (A plan 19, `DecompositionStructure`, structure-2026-10-21.1)
# Shapes only (unknown, missing and wrongly typed fields); whether a reference matches its
# question and selection_refs is A's own check. The field sets mirror the agreed contract
# document; B does not import A's package.

_REF_KEYS = {"place", "segment_ids", "span", "quote", "name", "candidates"}
_BUNDLE_KEYS = {"bundle_id", "context", "assignments"}
_CANDIDATE_KEYS = {"table_id", "obj_id", "value_id", "value_name", "role", "path", "type"}
_BINDING_KEYS = {"condition_id", "scope", "requirement_ids", "name_refs"}
_PROPOSAL_REF_KEYS = {"segment_ids", "subquote", "sense_id"}
_REQUIREMENT_STRUCTURE_KEYS = {"targets", "breakdowns", "basis", "context"}
_BREAKDOWN_KEYS = {"group_id", "kind", "axis", "values", "markers", "descendants"}
_DESCENDANTS_KEYS = {"status", "source_refs"}
_POLARITY_KEYS = {
    "link_id",
    "kind",
    "markers",
    "negative",
    "positive",
    "requirement_ids",
    "scope_status",
}
_EFFECT_KEYS = {"link_id", "negative_ref_index", "requirement_ids", "evidence_refs"}
_DECOMPOSITION_STRUCTURE_KEYS = {"version", "requirements", "polarity", "confirmed_effects"}
_SLICE_KEYS = {
    "version",
    "source",
    "requirement",
    "polarity",
    "confirmed_effects",
    "condition_bindings",
}


def _keys(value: Any, keys: set, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path}: not an object"]
    out = [f"{path}: unknown field '{k}'" for k in sorted(set(value) - keys, key=str)]
    out += [f"{path}: missing field '{k}'" for k in sorted(keys - set(value))]
    return out


def _check_ref(ref: Any, path: str) -> list[str]:
    out = _keys(ref, _REF_KEYS, path)
    if out:
        return out
    if not (_text(ref["place"]) and _texts(ref["segment_ids"]) and isinstance(ref["quote"], str)):
        out.append(f"{path}: place, segment_ids or quote of the wrong type")
    span = ref["span"]
    if not (
        isinstance(span, list)
        and len(span) == 2
        and all(isinstance(i, int) and not isinstance(i, bool) for i in span)
    ):
        out.append(f"{path}.span: two integers expected")
    if not _optional_text(ref["name"]):
        out.append(f"{path}.name: not text")
    if not isinstance(ref["candidates"], list):
        return out + [f"{path}.candidates: not a list"]
    for i, candidate in enumerate(ref["candidates"]):
        out += check_mention_candidate(candidate, f"{path}.candidates[{i}]")
    return out


def check_mention_candidate(candidate: Any, path: str) -> list[str]:
    """A's MentionCandidate: exactly its fields, of their types (not whether it is a raw
    candidate row of the reference: that is A's own check)."""
    out = _keys(candidate, _CANDIDATE_KEYS, path)
    if out:
        return out
    if not _text(candidate["table_id"]):
        out.append(f"{path}.table_id: not text")
    for key in ("obj_id", "value_id", "value_name"):
        if not _optional_text(candidate[key]):
            out.append(f"{path}.{key}: not text")
    for key, allowed in (
        ("role", CANDIDATE_ROLES),
        ("path", CANDIDATE_PATHS),
        ("type", CANDIDATE_TYPES),
    ):
        if candidate[key] not in allowed:
            out.append(f"{path}.{key}: unknown")
    return out


def check_condition_bindings(bindings: Any, path: str = "decomposition.bindings") -> list[str]:
    """A's ConditionBinding list (the attribution's source of truth B carries with a structure):
    exactly its fields, of their types. Never raises."""
    if not isinstance(bindings, list):
        return [f"{path}: not a list"]
    out = []
    for i, binding in enumerate(bindings):
        bpath = f"{path}[{i}]"
        problems = _keys(binding, _BINDING_KEYS, bpath)
        if problems:
            out += problems
            continue
        if not _text(binding["condition_id"]):
            out.append(f"{bpath}.condition_id: not text")
        if binding["scope"] not in BINDING_SCOPES:
            out.append(f"{bpath}.scope: unknown")
        if not _texts(binding["requirement_ids"]):
            out.append(f"{bpath}.requirement_ids: not a list of text")
        if not isinstance(binding["name_refs"], list):
            out.append(f"{bpath}.name_refs: not a list")
            continue
        for k, ref in enumerate(binding["name_refs"]):
            rpath = f"{bpath}.name_refs[{k}]"
            problems = _keys(ref, _PROPOSAL_REF_KEYS, rpath)
            if problems:
                out += problems
            elif not (
                _texts(ref["segment_ids"])
                and _optional_text(ref["subquote"])
                and _optional_text(ref["sense_id"])
            ):
                out.append(f"{rpath}: segment_ids, subquote or sense_id of the wrong type")
    return out


def _check_refs(refs: Any, path: str) -> list[str]:
    if not isinstance(refs, list):
        return [f"{path}: not a list"]
    return [p for i, ref in enumerate(refs) for p in _check_ref(ref, f"{path}[{i}]")]


def _check_requirement_structure(value: Any, path: str) -> list[str]:
    out = _keys(value, _REQUIREMENT_STRUCTURE_KEYS, path)
    if out:
        return out
    out += _check_refs(value["targets"], f"{path}.targets")
    out += _check_refs(value["basis"], f"{path}.basis")
    if value["context"] is not None:
        out += _check_ref(value["context"], f"{path}.context")
    if not isinstance(value["breakdowns"], list):
        return out + [f"{path}.breakdowns: not a list"]
    groups = []
    for i, group in enumerate(value["breakdowns"]):
        gpath = f"{path}.breakdowns[{i}]"
        problems = _keys(group, _BREAKDOWN_KEYS, gpath)
        if problems:
            out += problems
            continue
        if not _text(group["group_id"]):
            out.append(f"{gpath}.group_id: not text")
        else:
            groups.append(group["group_id"])
        if group["kind"] not in STRUCTURE_BREAKDOWN_KINDS:
            out.append(f"{gpath}.kind: unknown")
        if group["axis"] is not None:
            out += _check_ref(group["axis"], f"{gpath}.axis")
        out += _check_refs(group["values"], f"{gpath}.values")
        out += _check_refs(group["markers"], f"{gpath}.markers")
        descendants = group["descendants"]
        if (descendants is None) != (group["kind"] != "descendants"):
            out.append(f"{gpath}.descendants: only and always for kind descendants")
        elif descendants is not None:
            problems = _keys(descendants, _DESCENDANTS_KEYS, f"{gpath}.descendants")
            if problems:
                out += problems
            else:
                if descendants["status"] != "unexpanded":
                    out.append(f"{gpath}.descendants.status: unknown")
                out += _check_refs(descendants["source_refs"], f"{gpath}.descendants.source_refs")
    if len(groups) != len(set(groups)):
        out.append(f"{path}.breakdowns: duplicate group_id")
    return out


def _check_polarity(links: Any, path: str) -> list[str]:
    if not isinstance(links, list):
        return [f"{path}: not a list"]
    out = []
    ids = []
    for i, link in enumerate(links):
        lpath = f"{path}[{i}]"
        problems = _keys(link, _POLARITY_KEYS, lpath)
        if problems:
            out += problems
            continue
        if not _text(link["link_id"]):
            out.append(f"{lpath}.link_id: not text")
        else:
            ids.append(link["link_id"])
        if link["kind"] not in STRUCTURE_POLARITY_KINDS:
            out.append(f"{lpath}.kind: unknown")
        if link["scope_status"] not in STRUCTURE_SCOPE_STATUSES:
            out.append(f"{lpath}.scope_status: unknown")
        if not _texts(link["requirement_ids"]):
            out.append(f"{lpath}.requirement_ids: not a list of text")
        for key in ("markers", "negative", "positive"):
            out += _check_refs(link[key], f"{lpath}.{key}")
    if len(ids) != len(set(ids)):
        out.append(f"{path}: duplicate link_id")
    return out


def _check_effects(effects: Any, path: str) -> list[str]:
    if not isinstance(effects, list):
        return [f"{path}: not a list"]
    out = []
    for i, effect in enumerate(effects):
        epath = f"{path}[{i}]"
        problems = _keys(effect, _EFFECT_KEYS, epath)
        if problems:
            out += problems
            continue
        index = effect["negative_ref_index"]
        if not _text(effect["link_id"]) or not (
            isinstance(index, int) and not isinstance(index, bool) and index >= 0
        ):
            out.append(f"{epath}: link_id text and negative_ref_index a non-negative integer")
        if not _texts(effect["requirement_ids"]) or not effect["requirement_ids"]:
            out.append(f"{epath}.requirement_ids: a non-empty list of text")
        out += _check_refs(effect["evidence_refs"], f"{epath}.evidence_refs")
    return out


def _check_effect_links(polarity: Any, effects: Any, known: list | None, path: str) -> list[str]:
    """Each confirmed effect names a link, a position of its negative refs, and requirements
    inside the link's scope (an empty link scope = every requirement of the reading, `known`).
    Read whenever the polarity links and the effects have their own shape, whatever else is
    wrong; `known` None (a requirement's slice) checks the links' named requirements only."""
    if _check_polarity(polarity, path) or _check_effects(effects, path):
        return []  # their own shape problems are reported by those checks
    links = {link["link_id"]: link for link in polarity}
    out = []
    for i, effect in enumerate(effects):
        link = links.get(effect["link_id"])
        if link is None:
            out.append(f"{path}.confirmed_effects[{i}].link_id: unknown link")
            continue
        if effect["negative_ref_index"] >= len(link["negative"]):
            out.append(f"{path}.confirmed_effects[{i}].negative_ref_index: outside the link")
        scope = link["requirement_ids"] or known
        if scope is not None and not set(effect["requirement_ids"]) <= set(scope):
            out.append(f"{path}.confirmed_effects[{i}].requirement_ids: outside the link's scope")
    for i, link in enumerate(polarity):
        if known is not None and not set(link["requirement_ids"]) <= set(known):
            out.append(f"{path}.polarity[{i}].requirement_ids: not requirements of the reading")
    return out


def _held_empty(structure: Json, path: str) -> list[str]:
    """In structure-2026-10-21.1 no confirmed effect is made (user decision of 2026-10-21, A
    contract document 4.6): a non-empty list breaks the contract (the rules of each effect are
    still read and reported)."""
    effects = structure.get("confirmed_effects")
    if isinstance(effects, list) and effects:
        return [
            f"{path}.confirmed_effects: held empty in {structure.get('version')!r} "
            f"(no confirmed effect is made this round), got {len(effects)}"
        ]
    return []


def _check_present_effects(structure: Json, known: list | None, path: str) -> list[str]:
    """The confirmed effects' own checks on whatever of polarity and effects is present (the
    rules of a listed effect are reported whatever else is wrong, top-level fields included)."""
    out = []
    if "polarity" in structure:
        out += _check_polarity(structure["polarity"], f"{path}.polarity")
    if "confirmed_effects" in structure:
        out += _held_empty(structure, path)
        out += _check_effects(structure["confirmed_effects"], f"{path}.confirmed_effects")
        if "polarity" in structure:
            out += _check_effect_links(
                structure["polarity"], structure["confirmed_effects"], known, path
            )
    return out


def check_decomposition_structure(structure: Any, requirement_ids: list[str]) -> list[str]:
    """Problems of the interpretation's own `DecompositionStructure` of a supported version
    (`analysis.decomposition.structure`): the agreed shape, `requirements` keyed by exactly the
    reading's (settled) requirement IDs, and confirmed effects inside their links. An
    unsupported version is reported by the adapter as structure_unsupported_version (a contract
    error; the old words are never read for it).
    Never raises."""
    path = "decomposition.structure"
    out = _keys(structure, _DECOMPOSITION_STRUCTURE_KEYS, path)
    if not isinstance(structure, dict):
        return out
    if "version" in structure and structure["version"] not in STRUCTURE_VERSIONS:
        return [f"{path}.version: unsupported version {structure['version']!r}"]
    if "requirements" in structure:
        requirements = structure["requirements"]
        if not isinstance(requirements, dict):
            out.append(f"{path}.requirements: not an object")
        else:
            if set(requirements) != set(requirement_ids):
                out.append(f"{path}.requirements: keys differ from the reading's requirement IDs")
            for rid, value in requirements.items():
                out += _check_requirement_structure(value, f"{path}.requirements.{rid}")
    out += _check_present_effects(structure, requirement_ids, path)
    return out


def check_structure(structure: Any) -> list[str]:
    """Problems of a requirement's structure slice (`hints.structure`):
    {version, source, requirement: RequirementStructure, polarity, confirmed_effects,
    condition_bindings}. Never raises."""
    out = _keys(structure, _SLICE_KEYS, "structure")
    if not isinstance(structure, dict):
        return out
    if "version" in structure and structure["version"] not in STRUCTURE_VERSIONS:
        out.append("structure.version: unsupported version")
    if "source" in structure and not _text(structure["source"]):
        out.append("structure.source: not text")
    if "requirement" in structure:
        out += _check_requirement_structure(structure["requirement"], "structure.requirement")
    out += _check_present_effects(structure, None, "structure")
    if "condition_bindings" in structure:
        out += check_condition_bindings(
            structure["condition_bindings"], "structure.condition_bindings"
        )
    return out


def _check_bundle(bundle: Any, own_tables: set, path: str) -> list[str]:
    shape = check_bundle_shape(bundle, own_tables)
    if shape and shape[0] == "bundle: needs a bundle_id":
        return [f"{path}.{shape[0]}"]
    return [f"{path}.{p}" for p in shape + bundle_segment_problems(bundle)]


def _check_scope(scope: Any, path: str) -> list[str]:
    if not isinstance(scope, dict) or not _SCOPE_KEYS <= set(scope) <= _SCOPE_ALL:
        return [f"{path}: keys differ from the contract"]
    out = []
    if not _text(scope["scope_id"]):
        out.append(f"{path}.scope_id: empty or not text")
    if not isinstance(scope["decision"], str) or scope["decision"] not in DECISIONS:
        out.append(f"{path}.decision: unknown")
    tables = scope["tables"]
    if not isinstance(tables, list) or not tables:
        return out + [f"{path}.tables: empty or not a list"]
    keys = []
    for j, table in enumerate(tables):
        if not isinstance(table, dict) or set(table) != _TABLE_KEYS:
            out.append(f"{path}.tables[{j}]: keys differ from the contract")
        elif not _text(table["org_id"]) or not _text(table["table_id"]):
            out.append(f"{path}.tables[{j}]: org_id or table_id empty")
        elif not _optional_text(table["table_name"]):
            out.append(f"{path}.tables[{j}]: table_name is not text")
        else:
            keys.append((table["org_id"], table["table_id"]))
    if len(keys) != len(set(keys)):
        out.append(f"{path}.tables: duplicate table")
    own = {k[1] for k in keys}
    key = scope.get("requirement_key")
    if key is not None and not (
        isinstance(key, dict)
        and set(key) == {"reading_id", "requirement_id"}
        and _optional_text(key["reading_id"])
        and key["requirement_id"] == scope["scope_id"]
    ):
        out.append(f"{path}.requirement_key: needs reading_id and the scope's requirement_id")
    if "bundle" in scope:
        out += _check_bundle(scope["bundle"], own, path)
    alternatives = scope.get("alternatives", [])
    if not isinstance(alternatives, list) or not all(
        isinstance(a, dict) and _texts(a.get("table_ids")) and a.get("fetch") is False
        for a in alternatives
    ):
        out.append(f"{path}.alternatives: [{{table_ids, fetch: false}}] expected")
    notices = scope.get("adapter_notices", [])
    if not isinstance(notices, list) or not all(
        isinstance(n, dict) and set(n) == {"code", "detail"} and _text(n["code"]) for n in notices
    ):
        out.append(f"{path}.adapter_notices: [{{code, detail}}] expected")
    return out + _check_hints(scope["hints"], own, path)


def check_request(request: Any) -> list[str]:
    """Problems of a B request (empty when it holds the contract). Never raises."""
    if not isinstance(request, dict) or not _REQUEST_KEYS <= set(request) <= _REQUEST_ALL:
        return ["request: keys differ from the contract"]
    out: list[str] = []
    if not _text(request["question"]):
        out.append("request.question: empty")
    if not _optional_text(request["as_of"]):
        out.append("request.as_of: not text")
    if not _text(request["source"]):
        out.append("request.source: empty")
    if request["route"] is not None and not isinstance(request["route"], dict):
        out.append("request.route: not an object")
    for key in ("scopes", "skipped", "relations"):
        if not isinstance(request[key], list):
            out.append(f"request.{key}: not a list")
    if not (
        isinstance(request["table_group_hint"], list)
        and all(_texts(group) for group in request["table_group_hint"])
    ):
        out.append("request.table_group_hint: not a list of id lists")
    if out:
        return out
    sent: list[str] = []
    for i, scope in enumerate(request["scopes"]):
        out += _check_scope(scope, f"scopes[{i}]")
        if isinstance(scope, dict) and _text(scope.get("scope_id")):
            sent.append(scope["scope_id"])
    if len(sent) != len(set(sent)):
        out.append("scopes: duplicate scope_id")
    skipped: list[str] = []
    for i, entry in enumerate(request["skipped"]):
        if not isinstance(entry, dict) or set(entry) != {"scope_id", "reason"}:
            out.append(f"skipped[{i}]: keys differ from the contract")
        elif not _text(entry["scope_id"]) or not _text(entry["reason"]):
            out.append(f"skipped[{i}]: scope_id or reason empty")
        else:
            skipped.append(entry["scope_id"])
    if len(skipped) != len(set(skipped)):
        out.append("skipped: duplicate scope_id")
    if set(skipped) & set(sent):
        out.append("skipped: scope also sent to B")
    errors = request.get("input_errors", [])
    if not isinstance(errors, list) or not all(
        isinstance(e, dict) and _text(e.get("code")) and _optional_text(e.get("scope_id"))
        for e in errors
    ):
        out.append("request.input_errors: [{code, detail, scope_id?}] expected")
    if "contract_version" in request and not _text(request["contract_version"]):
        out.append("request.contract_version: not text")
    if "a_assumptions" in request:
        out += [f"request.{p}" for p in check_a_assumptions(request["a_assumptions"])]
    return out


def _check_series(rows: Any, scope_id: Any) -> list[str]:
    if not isinstance(rows, list):
        return ["result.series: not a list"]
    out = []
    ids = []
    for k, row in enumerate(rows):
        path = f"result.series[{k}]"
        if not isinstance(row, dict) or set(row) != SERIES_KEYS:
            out.append(f"{path}: keys differ from the contract")
        elif row["scope_id"] != scope_id:
            out.append(f"{path}: scope_id differs from the scope")
        elif not all(
            _text(row[k]) for k in ("series_id", "label", "org_id", "table_id", "item_id")
        ):
            out.append(f"{path}: an id or the label is empty")
        elif not _dimensions(row["dimensions"]) or not _period(row["period"]):
            out.append(f"{path}: dimensions or period malformed")
        elif not _optional_text(row["unit"]):
            out.append(f"{path}.unit: not text")
        else:
            ids.append(row["series_id"])
    if len(ids) != len(set(ids)):
        out.append("result.series: duplicate series_id")
    return out


def _check_period_record(record: Any) -> list[str]:
    if record is None:
        return []
    if not isinstance(record, dict) or not {"source", "requested", "applied"} <= set(record):
        return ["result.period: missing fields"]
    if not isinstance(record["source"], str) or record["source"] not in PERIOD_SOURCES:
        return ["result.period.source: unknown"]
    if record["applied"] is not None and not _period(record["applied"]):
        return ["result.period.applied: not a period"]
    if record["requested"] is not None and not isinstance(record["requested"], dict):
        return ["result.period.requested: not an object"]
    return []


def _check_frequency_record(record: Any) -> list[str]:
    if record is None:
        return []
    if not isinstance(record, dict) or not {"source", "chosen", "alternatives"} <= set(record):
        return ["result.frequency: missing fields"]
    if not isinstance(record["source"], str) or record["source"] not in FREQUENCY_SOURCES:
        return ["result.frequency.source: unknown"]
    if record["chosen"] is not None and record["chosen"] not in PERIOD_FREQUENCIES:
        return ["result.frequency.chosen: unknown"]
    if not _texts(record["alternatives"]) or not set(record["alternatives"]) <= set(
        PERIOD_FREQUENCIES
    ):
        return ["result.frequency.alternatives: not a list of frequencies"]
    return []


def _check_assumptions(assumptions: Any) -> list[str]:
    if not isinstance(assumptions, list):
        return ["result.assumptions: not a list"]
    out = []
    for k, entry in enumerate(assumptions):
        if not isinstance(entry, dict) or set(entry) != {"dimension", "value", "rule"}:
            out.append(f"result.assumptions[{k}]: keys differ from the contract")
        elif (
            not isinstance(entry["dimension"], str)
            or entry["dimension"] not in ASSUMPTION_DIMENSIONS
        ):
            out.append(f"result.assumptions[{k}].dimension: unknown")
        elif not _text(entry["rule"]):
            out.append(f"result.assumptions[{k}].rule: empty")
        elif entry["value"] is None or entry["value"] == "" or entry["value"] == {}:
            out.append(f"result.assumptions[{k}].value: empty")
        elif entry["dimension"] == "period" and not _period(entry["value"]):
            out.append(f"result.assumptions[{k}].value: not a period")
        elif entry["dimension"] == "frequency" and entry["value"] not in PERIOD_FREQUENCIES:
            out.append(f"result.assumptions[{k}].value: not a frequency")
    return out


def _check_issues(issues: list) -> list[str]:
    out = []
    for k, issue in enumerate(issues):
        if not isinstance(issue, dict) or not _text(issue.get("code")):
            out.append(f"result.issues[{k}]: code missing")
        elif not isinstance(issue.get("retryable"), bool):
            out.append(f"result.issues[{k}]: retryable missing")
        elif issue.get("kind") not in ISSUE_KINDS or issue.get("resume_at") not in RESUME_POINTS:
            out.append(f"result.issues[{k}]: bad kind or resume point for {issue['code']}")
    return out


def _check_ask(ask: Any) -> list[str]:
    if not isinstance(ask, dict) or not isinstance(ask.get("kind"), str):
        return ["result.ask.kind: unknown"]
    if ask["kind"] not in ASK_KINDS:
        return ["result.ask.kind: unknown"]
    if not _text(ask.get("message")) or not isinstance(ask.get("options"), list):
        return ["result.ask: message or options missing"]
    if not ask["options"] or not all(
        isinstance(o, dict) and _text(o.get("value")) for o in ask["options"]
    ):
        return ["result.ask: no option to choose"]
    return []


def _check_assumed(result: Json, record: Any, source: str, dimension: str) -> list[str]:
    """A value B assumed (source `source`) must also be listed in `assumptions`."""
    if not isinstance(record, dict) or record.get("source") != source:
        return []
    recorded = result["assumptions"] if isinstance(result["assumptions"], list) else []
    if any(isinstance(a, dict) and a.get("dimension") == dimension for a in recorded):
        return []
    return [f"result: assumed {dimension} not recorded in assumptions"]


def check_scope_result(result: Any) -> list[str]:
    """Problems of one B scope result (empty when it holds the contract). Never raises."""
    if not isinstance(result, dict) or not _RESULT_KEYS <= set(result) <= _RESULT_ALL:
        return ["result: keys differ from the contract"]
    out: list[str] = []
    status = result["status"] if isinstance(result["status"], str) else None
    if not _text(result["scope_id"]):
        out.append("result.scope_id: empty")
    if status not in SCOPE_STATUSES:
        out.append("result.status: unknown")
    out += _check_series(result["series"], result["scope_id"])
    if not isinstance(result["issues"], list) or not isinstance(result["decided"], dict):
        return out + ["result.issues or result.decided: wrong type"]
    if not isinstance(result["notices"], list):
        out.append("result.notices: not a list")
    if isinstance(result["series"], list):
        if status == "ok" and not result["series"]:
            out.append("result: ok without series")
        if status != "ok" and result["series"]:
            out.append("result: series on a scope that is not ok")
    if status in {"unresolved", "table_unsuitable"} and not result["issues"]:
        out.append("result: failure without an issue")
    out += _check_issues(result["issues"])
    for axis, how in result["decided"].items():
        if not isinstance(how, str) or how not in DECIDED_BY:
            out.append(f"result.decided[{axis}]: unknown")
    period = result["period"]
    out += _check_period_record(period)
    out += _check_frequency_record(result["frequency"])
    out += _check_assumptions(result["assumptions"])
    if status == "ok" and not (isinstance(period, dict) and _period(period.get("applied"))):
        out.append("result: ok without an applied period")
    out += _check_assumed(result, period, "default", "period")
    out += _check_assumed(result, result["frequency"], "default", "frequency")
    ask = result.get("ask")
    if status == "ask" and not ask:
        out.append("result: ask without a question")
    if ask is not None:
        out += _check_ask(ask)
    out += _check_result_fields(result.get("condition_fields", {}))
    out += _check_shared(result.get("shared_assumptions", []), result["scope_id"])
    return out


def _check_result_fields(fields: Any) -> list[str]:
    if not isinstance(fields, dict):
        return ["result.condition_fields: not an object"]
    out = []
    for cid, entry in fields.items():
        if not isinstance(entry, dict) or not {"source", "confirmed", "applied"} <= set(entry):
            out.append(f"result.condition_fields[{cid}]: needs source, confirmed, applied")
        elif entry["applied"] not in (True, False, None):
            out.append(f"result.condition_fields[{cid}].applied: true, false or None")
    return out


def _check_shared(entries: Any, scope_id: Any) -> list[str]:
    if not isinstance(entries, list):
        return ["result.shared_assumptions: not a list"]
    out = []
    ids = []
    for k, entry in enumerate(entries):
        path = f"result.shared_assumptions[{k}]"
        if not isinstance(entry, dict) or not _SHARED_REQUIRED <= set(entry) <= _SHARED_ALL:
            out.append(f"{path}: keys differ from the contract")
        elif not _text(entry["assumption_id"]):
            out.append(f"{path}.assumption_id: not text")
        elif entry["assumption_id"] in ids:
            out.append(f"{path}.assumption_id: used twice")
        elif not _texts(entry["requirement_ids"]):
            out.append(f"{path}.requirement_ids: not a list of text")
        elif entry["stage"] != "B" or entry["source"] not in PERIOD_SOURCES:
            out.append(f"{path}: stage must be B and source one of {PERIOD_SOURCES}")
        elif not isinstance(entry["refs"], dict) or set(entry["refs"]) != {
            "segment_ids",
            "mention_ids",
            "condition_ids",
        }:
            out.append(f"{path}.refs: {{segment_ids, mention_ids, condition_ids}} expected")
        elif not all(_texts(v) for v in entry["refs"].values()):
            out.append(f"{path}.refs: lists of text expected")
        elif not isinstance(entry["adopted"], dict) or set(entry["adopted"]) != {
            "value",
            "interpretation",
        }:
            out.append(f"{path}.adopted: {{value, interpretation}} expected")
        elif scope_id not in entry["requirement_ids"] or not isinstance(entry["confirmed"], bool):
            out.append(f"{path}: requirement_ids must hold the scope, confirmed a boolean")
        elif "supersedes" in entry and not (
            entry["supersedes"] is None or _text(entry["supersedes"])
        ):
            out.append(f"{path}.supersedes: one A assumption_id or None")
        if isinstance(entry, dict) and _text(entry.get("assumption_id")):
            ids.append(entry["assumption_id"])
    return out


# ---------------------------------------------------------------- A's public assumptions

_A_ENVELOPE_KEYS = {"version", "items"}
_A_ITEM_KEYS = {
    "assumption_id",
    "stage",
    "requirement_ids",
    "requirement_keys",
    "refs",
    "reason_code",
    "adopted",
    "source",
    "confirmed",
}
_A_KEY_KEYS = {"reading_id", "requirement_id"}
_A_REFS_KEYS = {"segment_ids", "mention_ids", "condition_ids"}
_A_ADOPTED_KEYS = {"value", "interpretation"}


def _json_value(value: Any, _open: frozenset = frozenset()) -> bool:
    """A JSON value: null, a boolean, a finite number, text, a list or an object of text keys
    (a list or object containing itself is none)."""
    if value is None or isinstance(value, bool | str):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list | dict):
        if id(value) in _open:
            return False  # a cycle
        inner = _open | {id(value)}
        if isinstance(value, list):
            return all(_json_value(v, inner) for v in value)
        return all(isinstance(k, str) and _json_value(v, inner) for k, v in value.items())
    return False


def _same_json(a: Any, b: Any, _open: frozenset = frozenset()) -> bool:
    """Equal as JSON: `false` is not `0`, `1` is not `1.0` (Python's == says they are). A pair
    of containers met again while being compared (a cycle on both sides) is not compared twice."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list | dict):
        pair = (id(a), id(b))
        if pair in _open:
            return True
        inner = _open | {pair}
        if isinstance(a, list):
            return len(a) == len(b) and all(
                _same_json(x, y, inner) for x, y in zip(a, b, strict=True)
            )
        return a.keys() == b.keys() and all(_same_json(a[k], b[k], inner) for k in a)
    return a == b


def _unique_texts(value: Any) -> bool:
    return (
        isinstance(value, list) and all(_text(v) for v in value) and len(value) == len(set(value))
    )


def check_a_assumptions(envelope: Any, requirement_ids: list[str] | None = None) -> list[str]:
    """Problems of A's public assumption envelope (`BRequest.a_assumptions`).

    Exactly {version, items}; a supported version (`A_ASSUMPTION_VERSIONS`); each item exactly
    its fields (requirement_keys, refs and adopted too); IDs, reason_code and interpretation
    non-empty text; reference lists text without duplicates; `value` a JSON value; source one
    of `ASSUMPTION_SOURCES`; stage "A"; confirmed a boolean; requirement_ids = the set of the
    keys' requirement_id; assumption_id unique in the envelope. With `requirement_ids` (A's
    requirement list the adapter holds) every item must name requirements of it. Never raises.
    """
    path = "a_assumptions"
    out = _keys(envelope, _A_ENVELOPE_KEYS, path)
    if not isinstance(envelope, dict):
        return out
    if "version" in envelope and envelope["version"] not in A_ASSUMPTION_VERSIONS:
        return [f"{path}.version: unsupported version {envelope['version']!r}"]
    items = envelope.get("items")
    if "items" in envelope and not isinstance(items, list):
        return out + [f"{path}.items: not a list"]
    known = set(requirement_ids) if requirement_ids is not None else None
    seen: list[str] = []
    for i, item in enumerate(items or []):
        ipath = f"{path}.items[{i}]"
        problems = _keys(item, _A_ITEM_KEYS, ipath)
        if problems:
            out += problems
            continue
        if not _text(item["assumption_id"]):
            out.append(f"{ipath}.assumption_id: not text")
        elif item["assumption_id"] in seen:
            out.append(f"{ipath}.assumption_id: used twice in the envelope")
        else:
            seen.append(item["assumption_id"])
        if item["stage"] != "A":
            out.append(f"{ipath}.stage: must be 'A'")
        if item["source"] not in ASSUMPTION_SOURCES:
            out.append(f"{ipath}.source: unknown")
        if not isinstance(item["confirmed"], bool):
            out.append(f"{ipath}.confirmed: not a boolean")
        if not _text(item["reason_code"]):
            out.append(f"{ipath}.reason_code: not text")
        if not _unique_texts(item["requirement_ids"]):
            out.append(f"{ipath}.requirement_ids: not a list of distinct text")
        keys = item["requirement_keys"]
        key_ids = []
        if not isinstance(keys, list):
            out.append(f"{ipath}.requirement_keys: not a list")
        else:
            pairs = []
            for k, key in enumerate(keys):
                kpath = f"{ipath}.requirement_keys[{k}]"
                problems = _keys(key, _A_KEY_KEYS, kpath)
                if problems:
                    out += problems
                elif not (_text(key["reading_id"]) and _text(key["requirement_id"])):
                    out.append(f"{kpath}: reading_id and requirement_id must be text")
                else:
                    key_ids.append(key["requirement_id"])
                    pairs.append((key["reading_id"], key["requirement_id"]))
            if len(pairs) != len(set(pairs)):
                out.append(f"{ipath}.requirement_keys: duplicate key")
        if _unique_texts(item["requirement_ids"]) and set(item["requirement_ids"]) != set(key_ids):
            out.append(f"{ipath}.requirement_ids: differ from the keys' requirement_id")
        if known is not None and _unique_texts(item["requirement_ids"]):
            for rid in item["requirement_ids"]:
                if rid not in known:
                    out.append(f"{ipath}.requirement_ids: {rid!r} is no requirement of A")
        refs = item["refs"]
        problems = _keys(refs, _A_REFS_KEYS, f"{ipath}.refs")
        if problems:
            out += problems
        else:
            for key in sorted(_A_REFS_KEYS):
                if not _unique_texts(refs[key]):
                    out.append(f"{ipath}.refs.{key}: not a list of distinct text")
        adopted = item["adopted"]
        problems = _keys(adopted, _A_ADOPTED_KEYS, f"{ipath}.adopted")
        if problems:
            out += problems
        else:
            if not _text(adopted["interpretation"]):
                out.append(f"{ipath}.adopted.interpretation: not text")
            if not _json_value(adopted["value"]):
                out.append(f"{ipath}.adopted.value: not a JSON value")
    return out


def check_b_result(result: Any, request: Any) -> list[str]:
    """Problems of `run_b_request`'s result against its request for A's assumptions: the
    envelope comes back unchanged at the top level as JSON (absent when the request had none),
    and a stage-B `supersedes` is None or one A assumption_id of the request's envelope that
    replaces the same requirement and the same conditions: its requirements hold the scope's
    requirement (`requirement_key.requirement_id`; a scope without one supersedes nothing) and
    its `refs.condition_ids` are the B entry's, not empty. Never raises."""
    if not isinstance(result, dict) or not isinstance(request, dict):
        return ["result or request: not an object"]
    out = []
    sent = request.get("a_assumptions")
    if "a_assumptions" in request:
        if "a_assumptions" not in result or not _same_json(result["a_assumptions"], sent):
            out.append("result.a_assumptions: not the request's envelope unchanged")
    elif "a_assumptions" in result:
        out.append("result.a_assumptions: given without one in the request")
    items = {}
    if isinstance(sent, dict) and isinstance(sent.get("items"), list):
        items = {
            i["assumption_id"]: i
            for i in sent["items"]
            if isinstance(i, dict) and isinstance(i.get("assumption_id"), str)
        }
    scopes = request.get("scopes")
    requirement_of = {}
    for s in scopes if isinstance(scopes, list) else []:
        key = s.get("requirement_key") if isinstance(s, dict) else None
        if isinstance(key, dict) and isinstance(s.get("scope_id"), str):
            requirement_of[s["scope_id"]] = key.get("requirement_id")
    results = result.get("scopes")
    for k, scope in enumerate(results if isinstance(results, list) else []):
        if not isinstance(scope, dict):
            continue
        entries = scope.get("shared_assumptions")
        for j, entry in enumerate(entries if isinstance(entries, list) else []):
            if not isinstance(entry, dict) or entry.get("supersedes") is None:
                continue
            path = f"result.scopes[{k}].shared_assumptions[{j}].supersedes"
            target = entry["supersedes"]
            if not isinstance(target, str):
                out.append(f"{path}: one A assumption_id or None")
                continue
            if target not in items:
                out.append(f"{path}: {target!r} is not in the request's A assumptions")
                continue
            scope_id = scope.get("scope_id")
            requirement = requirement_of.get(scope_id) if isinstance(scope_id, str) else None
            a_ids = items[target].get("requirement_ids")
            if not isinstance(requirement, str) or requirement not in (
                a_ids if isinstance(a_ids, list) else []
            ):
                out.append(f"{path}: {target!r} is another requirement's assumption")
                continue
            mine = (entry.get("refs") or {}) if isinstance(entry.get("refs"), dict) else {}
            theirs = items[target].get("refs")
            theirs = theirs if isinstance(theirs, dict) else {}
            b_conditions = mine.get("condition_ids")
            a_conditions = theirs.get("condition_ids")
            if not (
                _texts(b_conditions)
                and _texts(a_conditions)
                and b_conditions
                and set(b_conditions) == set(a_conditions)
            ):
                out.append(f"{path}: {target!r} does not stand for the same conditions")
    return out
