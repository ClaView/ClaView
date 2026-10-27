"""Module C (data_result -> chart/table view model). POST /c/present."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .envelope import Meta, Outcome
from .versions import VIEW_VERSION


class CRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    data_result: dict[str, Any]


class ViewModel(BaseModel):
    contract_version: str = VIEW_VERSION
    outcome: Outcome  # stage "C"; result = chart (views, series, notices, calculation)
    meta: Meta | None = None
