"""Module B (tables -> series/period selection -> KOSIS values). POST /b/resolve, /b/fetch, /b/run."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator
from pydantic_core import PydanticCustomError

from .envelope import Meta, Outcome
from .period_condition import BResultData, PeriodCondition
from .table import TableRef
from .versions import B_RESULT_VERSION, DATA_RESULT_VERSION


class BTableRef(TableRef):
    """B selection inputs extend the shared table identity."""

    model_config = ConfigDict(extra="forbid")

    focus: str | None = Field(default=None, max_length=100)
    period_condition: PeriodCondition | None = None

    @field_validator("focus")
    @classmethod
    def nonempty_focus(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise PydanticCustomError("period_condition_invalid", "Focus must not be empty")
        return value

    @model_serializer(mode="wrap")
    def omit_unused_additions(self, handler) -> dict[str, Any]:
        result = handler(self)
        for name in ("focus", "period_condition"):
            if getattr(self, name) is None:
                result.pop(name, None)
        return result


class BRequest(BaseModel):
    """B's input: the original question and the tables A chose (docs/contracts/A_to_B_표목록.md)."""

    query: str = Field(min_length=1, max_length=2000)
    tables: list[BTableRef] = Field(min_length=1)
    as_of: str | None = None

    @model_validator(mode="after")
    def duplicate_conditions(self) -> BRequest:
        # Legacy requests keep their table list. New senders combine focus before this boundary.
        if not any(table.period_condition is not None for table in self.tables):
            return self
        seen: dict[tuple[str, str], BTableRef] = {}
        for table in self.tables:
            key = (table.org_id, table.table_id)
            previous = seen.get(key)
            if previous is None:
                seen[key] = table
            elif previous.period_condition != table.period_condition:
                raise PydanticCustomError(
                    "period_condition_conflicting_tables", "Conflicting conditions for the same table"
                )
            elif previous.model_dump() != table.model_dump():
                raise PydanticCustomError("period_condition_invalid", "Duplicate table must be combined by sender")
        self.tables = list(seen.values())
        return self


class BResult(BaseModel):
    contract_version: str = B_RESULT_VERSION
    outcome: Outcome  # stage "B"; result = b_result (selections, ...) when ok
    meta: Meta | None = None

    @model_validator(mode="after")
    def period_results(self) -> BResult:
        result = self.outcome.result
        if result is not None and "period_resolutions" in result:
            BResultData.model_validate(result)
        return self


class FetchRequest(BaseModel):
    b_result: dict[str, Any]  # BResult.outcome.result


class FetchResult(BaseModel):
    contract_version: str = DATA_RESULT_VERSION
    outcome: Outcome  # stage "fetch"; result = data_result, or status "not_run"
    meta: Meta | None = None
