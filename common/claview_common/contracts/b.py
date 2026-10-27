"""Module B (tables -> series/period selection -> KOSIS values). POST /b/resolve, /b/fetch, /b/run."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .a import TableRef
from .envelope import Meta, Outcome
from .versions import B_RESULT_VERSION, DATA_RESULT_VERSION


class BRequest(BaseModel):
    """B's input: the original question and the tables A chose (docs/contracts/A_to_B_표목록.md)."""

    query: str = Field(min_length=1, max_length=2000)
    tables: list[TableRef] = Field(min_length=1)
    as_of: str | None = None


class BResult(BaseModel):
    contract_version: str = B_RESULT_VERSION
    outcome: Outcome  # stage "B"; result = b_result (selections, ...) when ok
    meta: Meta | None = None


class FetchRequest(BaseModel):
    b_result: dict[str, Any]  # BResult.outcome.result


class FetchResult(BaseModel):
    contract_version: str = DATA_RESULT_VERSION
    outcome: Outcome  # stage "fetch"; result = data_result, or status "not_run"
    meta: Meta | None = None
