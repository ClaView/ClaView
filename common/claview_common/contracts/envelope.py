"""The stage envelope every step returns (was claview.b.contracts.outcome, now shared).

{"stage", "status", "result", "issues", "trace_ref"}  (+ "interaction" when waiting on the user)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Issue(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    message: str | None = None
    field: str | None = None
    series_id: str | None = None
    retryable: bool | None = None


class Outcome(BaseModel):
    """One stage result. `status` is "ok" or a failure status (error, unresolved, unsupported,
    needs_confirmation, awaiting_user, not_run, ...)."""

    model_config = ConfigDict(extra="allow")

    stage: str
    status: str
    result: dict[str, Any] | None = None
    issues: list[Issue] = Field(default_factory=list)
    trace_ref: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Meta(BaseModel):
    """Measurements a module adds to every response (summed by the evaluation runner)."""

    latency_ms: float
    llm_calls: int = 0
    external_calls: int = 0
    mode: str
