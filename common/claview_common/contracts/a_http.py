"""Versioned HTTP boundary for the new A; checkpoints remain server-side."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .a_view import AView
from .envelope import Meta
from .versions import A_HTTP_VERSION


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Required on requests: legacy callers cannot silently receive a different response.
    contract_version: Literal["a-http/2026-11-02.1"]


class AHTTPSelectRequest(_Request):
    query: str = Field(min_length=1, max_length=2000)
    as_of: str | None = None

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query_empty")
        return value

    @field_validator("as_of")
    @classmethod
    def iso_day(cls, value: str | None) -> str | None:
        if value is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("as_of_format")
            date.fromisoformat(value)
        return value


class AHTTPReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question_id: str = Field(min_length=1, max_length=256)
    question_version: int = Field(ge=0)
    option_id: str | None = Field(default=None, min_length=1, max_length=256)
    text: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def one_answer(self) -> AHTTPReply:
        if (self.option_id is None) == (self.text is None):
            raise ValueError("option_or_text_required")
        return self


class AHTTPAnswerRequest(_Request):
    session_id: str = Field(pattern=r"^[0-9a-f]{8,64}$")
    replies: list[AHTTPReply] = Field(min_length=1, max_length=100)


class AHTTPResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["a-http/2026-11-02.1"] = A_HTTP_VERSION
    view: AView
    execution_mode: Literal["synthetic", "live"]
    meta: Meta
