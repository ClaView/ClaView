"""AView: the public result of the new A that the pipeline and the screen read (plan 54).

They read this and never the new A's internal state (record, decisions, model output). The adapter that fills it
(``claview_a_api.view``) copies A's outcome per statistic and adds nothing of its own: it never chooses a table,
drops a question or turns a failure into a success. What may go to B (``handoff.ready``) and what may not
(``handoff.blocked``) are told apart here, and the pipeline sends only ``ready`` requests.

Owner: the new-A screen connection (plan 54). The other contract files are unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

A_VIEW_VERSION = "a-view/2026-11-02.11"  # .2: A's assignments per request, notes, warning details kept
# .11: the provenance of A's table-local focus, distinct from its unchanged statistic quote. Quote grounding
# confirms source presence, not the semantic adequacy of the model's scope. B's table fields do not change.
# .3: each final table's catalog range and latest unpublished months, the run's period policy (plan 56 order 4)
# .4: A's own period and frequency questions (outcome ask_condition, kind frequency, option ranges; plan 56 order 5)
# .5: the values a basis excludes are kept with it (plan 56 order 6-2b, bundle 5)
# .6: a candidate kept to be aggregated (outcome aggregation_needed, its aggregation record; plan 56 order 6-3)
# .7: the aggregation record's evidence levels by where the quote sits, a verified method, the average basis and the
# kind of computation (still not computed; plan 56 order 6-3c)
# .8: an ask option's status and whether it may be chosen, the options A chose by ⑥-나 (plan 56 order 8)
# .10 (added only): each basis axis's polarity mode and the quotes of each stated value (Codex review 11-01: the screen
# tells a value either is enough, both values asked together and each value asked in its own words apart)
# .9 (added only): A's stage, the warning rows, the end point status, each final table's period hint, phrase and
# sources, the basis note (plan 56 order 11, bundle 11); never sent to B

# A's outcome per statistic, copied as A wrote it (claview_a.check.OUTCOMES).
Outcome = Literal[
    "selected",
    "selected_unverified",
    "ask",
    "ask_unverified",
    "ask_condition",
    "aggregation_needed",
    "not_in_pool",
    "failed",
]
# One display class per outcome: the screen tells these apart.
StatisticState = Literal["selected", "unverified", "asking", "missing", "failed"]
ViewStatus = Literal["ready", "needs_answer", "partial", "failed"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInfo(_Model):
    """The run the session was made with (kept unchanged across answers)."""

    versions: dict[str, Any]
    model: dict[str, Any]
    research_model: dict[str, Any] | None = None
    profile: str
    period_policy: dict[str, Any] | None = None  # {version, sha256} of the period policy the run used


class Session(_Model):
    session_id: str
    question_version: int
    question: str  # the original question, never edited
    as_of: str
    run: RunInfo


class FinalTable(_Model):
    org_id: str
    table_id: str
    table_name: str | None = None
    period: list[dict[str, Any]] | None = None  # A's period rows for this table (requested months)
    frequency: str | None = None
    # the table's catalog range in that frequency ({frequency, start, end}, source: the A catalog)
    catalog_range: dict[str, Any] | None = None
    # the requested months after the table's latest period, not published yet (policy ①: kept, handed on)
    latest_unpublished: list[dict[str, Any]] | None = None
    # A's reference for this table ({frequency, start, end, share}) and the words it came from ({text, source})
    period_hint: dict[str, Any] | None = None
    period_phrase: dict[str, Any] | None = None
    period_source: Literal["quoted", "default", "user_answer"] | None = None
    frequency_source: str | None = None  # question, catalog, user_answer
    default_reason: str | None = None
    invalidated_by: str | None = None  # an answer that made an earlier assignment void


class Basis(_Model):
    axis: str
    label: str
    value: list[str]
    source: str  # splitter, question_one_statistic, model_choice, user_answer, ...
    excluded: list[str] = Field(default_factory=list)  # values the question excludes on the axis, as A kept them
    note: str | None = None
    # the model's polarity rows of the axis (contract .4 on): all (every value required) or any (one is enough), and
    # the quotes each stated value came with
    mode: Literal["all", "any"] | None = None
    value_quotes: dict[str, list[str]] = Field(default_factory=dict)


class Conditions(_Model):
    """The quotes the model attached to the statistic (as written; nothing resolved here)."""

    periods: list[str] = Field(default_factory=list)
    frequencies: list[str] = Field(default_factory=list)
    calculations: list[str] = Field(default_factory=list)
    splitters: list[dict[str, str]] = Field(default_factory=list)


class Reason(_Model):
    code: str
    detail: Any = None


class Answer(_Model):
    """A user's answer, kept as given: the question, the statistic it is for, the raw value and its source."""

    question_id: str
    question_version: int
    statistics: list[str]
    kind: Literal["basis_axis", "period", "frequency"]
    option_id: str | None = None
    value: str | None = None  # the chosen option's value, or the text the user typed
    text: str | None = None  # typed text only (period input); never parsed here
    table_id: str | None = None
    source: Literal["user_answer"] = "user_answer"


class StatisticView(_Model):
    request_id: str  # session and statistic
    statistic: str
    quote: str  # the part of the question this statistic is
    name: str
    outcome: Outcome
    state: StatisticState
    verified: bool
    representative: str | None = None
    tables: list[FinalTable] = Field(default_factory=list)
    basis: list[Basis] = Field(default_factory=list)
    focus: str | None = None
    focus_provenance: dict[str, Any] | None = None
    conditions: Conditions = Field(default_factory=Conditions)
    reasons: list[Reason] = Field(default_factory=list)
    warnings: list[Reason] = Field(default_factory=list)
    search_words: list[str] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)
    # aggregation_needed: requested and source frequency, method, evidence level, method status, the source range
    # and its availability; shown as a candidate, never handed on or complete before the aggregation is done
    aggregation: dict[str, Any] | None = None
    # A chose a basis without asking (⑥-나: one option supported, the others confirmed unsupported): {axis, value,
    # message}; never the user's choice
    auto_decided: list[dict[str, Any]] = Field(default_factory=list)
    # A's stage (selected, waiting_answer, rejudge_pending, blocked_contract, failed) and its warnings as rows
    a_stage: Literal["selected", "waiting_answer", "rejudge_pending", "blocked_contract", "failed"] | None = None
    warning_rows: list[dict[str, Any]] = Field(default_factory=list)
    end_point_status: list[dict[str, Any]] = Field(default_factory=list)


class QuestionOption(_Model):
    option_id: str
    label: str
    value: str
    table_id: str | None = None
    table_name: str | None = None
    # a basis option as A checked it: supported, or unconfirmed (shown, never selectable); unsupported ones are not
    # offered (plan 56 order 8, ⑥-가)
    status: Literal["supported", "unconfirmed"] | None = None
    selectable: bool = True
    range: dict[str, str] | None = None  # a period option's months, {start, end} as YYYY-MM (A's as_of calendar)


class QuestionView(_Model):
    question_id: str
    question_version: int
    kind: Literal["basis_axis", "period", "frequency"]
    statistics: list[str]
    cause: str | None = None  # why A asks a period or frequency (user_omitted, several_valid_frequencies)
    axis: str | None = None
    axis_label: str | None = None
    text: str
    input: Literal["choice", "choice_or_text", "text"]
    options: list[QuestionOption] = Field(default_factory=list)
    synthetic: bool = False  # a question made by a test supplier, not by A


class HandoffTable(_Model):
    org_id: str
    table_id: str
    table_name: str | None = None
    focus: str | None = None


class Assignment(_Model):
    """What A decided for one table of a request: its frequency and requested months (A's spans, unchanged)."""

    table_id: str
    frequency: str | None = None
    period: list[dict[str, Any]] | None = None
    statistics: list[str]
    catalog_range: dict[str, Any] | None = None  # the table's A catalog range in that frequency (A's "available")
    latest_unpublished: list[dict[str, Any]] | None = None  # requested months after the table's latest period


class HandoffRequest(_Model):
    request_id: str
    statistics: list[str]
    tables: list[HandoffTable]
    period_answers: list[Answer] = Field(default_factory=list)
    # A's own decisions for the request, kept so the pipeline can check B's answer against them (B's table list
    # has no field for them)
    assignments: list[Assignment] = Field(default_factory=list)
    calculations: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[Reason] = Field(default_factory=list)  # what the B contract cannot carry, without blocking


class Blocked(_Model):
    statistics: list[str]
    code: str
    detail: Any = None


class Handoff(_Model):
    ready: list[HandoffRequest] = Field(default_factory=list)
    blocked: list[Blocked] = Field(default_factory=list)


class Failure(_Model):
    stage: str
    code: str


class AView(_Model):
    contract_version: str = A_VIEW_VERSION
    session: Session
    status: ViewStatus
    a_route: str  # A's own route (handoff / ask / partial / fail), copied
    statistics: list[StatisticView] = Field(default_factory=list)
    questions: list[QuestionView] = Field(default_factory=list)
    handoff: Handoff = Field(default_factory=Handoff)
    failure: Failure | None = None
    research: dict[str, Any] | None = None
    warnings: list[Reason] = Field(default_factory=list)
