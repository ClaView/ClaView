"""Module A (question -> tables). POST /a/select, POST /a/answer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .envelope import Meta
from .versions import A_RESULT_VERSION

# A's public response status (claview_a.agent.session.public_response).
Decision = Literal["proceed", "assume", "partial", "awaiting_answer", "no_result"]


class TableRef(BaseModel):
    """What B reads from A: the three table fields (B_TABLE_FIELDS)."""

    org_id: str
    table_id: str
    table_name: str | None = None


class Option(BaseModel):
    option_id: str
    label: str


class PendingQuestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    question_id: str
    options: list[Option]


class BRequestGroup(BaseModel):
    """Requirements A sends to B together (related requirements share one B call)."""

    requirement_ids: list[str]
    tables: list[TableRef]


class ARequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    as_of: str | None = None


class AAnswerRequest(BaseModel):
    """Resume a waiting A run. `state` is the checkpoint A returned; only option_id comes from the user."""

    state: dict[str, Any]
    option_id: str


class AResult(BaseModel):
    contract_version: str = A_RESULT_VERSION
    decision: Decision
    tables: list[TableRef] = Field(default_factory=list)  # all tables A hands to B, in order, once each
    b_requests: list[BRequestGroup] = Field(default_factory=list)  # one B call per requirement group
    pending_question: PendingQuestion | None = None
    response: dict[str, Any]  # A's full public response (selections, analysis, assumptions)
    state: dict[str, Any]  # checkpoint: send back unchanged to /a/answer
    meta: Meta | None = None
