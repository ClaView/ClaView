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

A_VIEW_VERSION = "a-view/2026-10-30.2"  # .2: A's assignments per request, notes, warning details kept

# A's outcome per statistic, copied as A wrote it (claview_a.check.OUTCOMES).
Outcome = Literal["selected", "selected_unverified", "ask", "ask_unverified", "not_in_pool", "failed"]
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


class Basis(_Model):
    axis: str
    label: str
    value: list[str]
    source: str  # splitter, question_one_statistic, model_choice, user_answer, ...


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
    kind: Literal["basis_axis", "period"]
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
    conditions: Conditions = Field(default_factory=Conditions)
    reasons: list[Reason] = Field(default_factory=list)
    warnings: list[Reason] = Field(default_factory=list)
    search_words: list[str] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)


class QuestionOption(_Model):
    option_id: str
    label: str
    value: str
    table_id: str | None = None
    table_name: str | None = None


class QuestionView(_Model):
    question_id: str
    question_version: int
    kind: Literal["basis_axis", "period"]
    statistics: list[str]
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
