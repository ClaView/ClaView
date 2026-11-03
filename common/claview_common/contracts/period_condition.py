"""A-to-B period condition shapes; no period interpretation or coverage decisions live here."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator
from pydantic_core import PydanticCustomError

from .versions import PERIOD_CONDITION_VERSION

Frequency = Literal["Y", "H", "Q", "M"]
PeriodSource = Literal["quoted", "user_answer"]
FrequencySource = Literal["question", "catalog", "user_answer"]
Identifier = Annotated[str, Field(min_length=1)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _unique(values: list[str]) -> list[str]:
    if any(not value.strip() for value in values):
        raise PydanticCustomError("period_condition_invalid", "Empty identifier")
    return list(dict.fromkeys(values))


class MonthRange(_Contract):
    """Inclusive months; this validates syntax, not the chosen frequency's boundaries."""

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def month(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) or value.startswith("0000-"):
            raise PydanticCustomError("period_condition_invalid", "Month must be YYYY-MM")
        return value

    @model_validator(mode="after")
    def ordered(self) -> MonthRange:
        if self.start > self.end:
            raise PydanticCustomError("period_condition_invalid", "End precedes start")
        return self


class PeriodAnswer(_Contract):
    """B's tracking subset of an A answer; the target table is its enclosing table item."""

    kind: Literal["period", "frequency"]
    question_id: Identifier
    question_version: int = Field(ge=0)
    statistics: list[Identifier] = Field(min_length=1)
    option_id: Identifier | None = None
    value: str | None = None
    text: str | None = None
    source: Literal["user_answer"] = "user_answer"

    @field_validator("statistics")
    @classmethod
    def targets(cls, values: list[str]) -> list[str]:
        return _unique(values)

    @model_validator(mode="after")
    def raw_answer(self) -> PeriodAnswer:
        if not any(isinstance(value, str) and value.strip() for value in (self.value, self.text)):
            raise PydanticCustomError("period_condition_invalid", "Answer value or text required")
        return self


class PeriodCondition(_Contract):
    """Field sources decide authority. A period must not be reinterpreted from an answer's text."""

    frequency: Frequency
    frequency_source: FrequencySource
    period: list[MonthRange] = Field(min_length=1, max_length=1)
    period_source: PeriodSource
    statistics: list[Identifier] = Field(default_factory=list)
    period_phrase: str | None = None
    latest_unpublished: list[MonthRange] | None = None
    answers: list[PeriodAnswer] = Field(default_factory=list)

    @field_validator("statistics")
    @classmethod
    def targets(cls, values: list[str]) -> list[str]:
        return _unique(values)

    @model_validator(mode="after")
    def answer_links(self) -> PeriodCondition:
        grouped: dict[tuple[str, int, str], PeriodAnswer] = {}
        for answer in self.answers:
            key = (answer.question_id, answer.question_version, answer.kind)
            previous = grouped.get(key)
            if previous is None:
                grouped[key] = answer.model_copy(deep=True)
                continue
            if any(getattr(previous, name) != getattr(answer, name)
                   for name in ("option_id", "value", "text", "source")):
                raise PydanticCustomError("period_condition_answer_conflict", "Conflicting answer records")
            previous.statistics = _unique(previous.statistics + answer.statistics)
        self.answers = list(grouped.values())
        for kind, source in (("period", self.period_source), ("frequency", self.frequency_source)):
            if source != "user_answer":
                continue
            if not self.statistics:
                raise PydanticCustomError("period_condition_answer_missing", "Statistics required for user_answer")
            covered = {sid for answer in self.answers if answer.kind == kind for sid in answer.statistics}
            if not set(self.statistics) <= covered:
                raise PydanticCustomError(
                    "period_condition_answer_missing", "Missing answer target coverage for " + kind
                )
        return self


class PeriodRange(_Contract):
    """B's inclusive period notation (YYYY for Y; YYYYpp for H, Q and M)."""

    frequency: Frequency
    start: str
    end: str

    @model_validator(mode="after")
    def valid_periods(self) -> PeriodRange:
        for value in (self.start, self.end):
            width = 4 if self.frequency == "Y" else 6
            if not re.fullmatch(r"[0-9]{" + str(width) + r"}", value) or value.startswith("0000"):
                raise PydanticCustomError("period_resolution_invalid", "Invalid period format")
            if width == 6 and not 1 <= int(value[4:]) <= {"H": 2, "Q": 4, "M": 12}[self.frequency]:
                raise PydanticCustomError("period_resolution_invalid", "Invalid period index")
        if self.start > self.end:
            raise PydanticCustomError("period_resolution_invalid", "End precedes start")
        return self


class CalculationExtra(PeriodRange):
    purpose: Identifier


class AppliedPeriodSources(_Contract):
    period: Literal["a_user_answer", "b_computed", "a_quoted_fallback"]
    frequency: Literal["a_user_answer", "b_computed"]


class PeriodDifferences(_Contract):
    period: bool
    frequency: bool


class PeriodReference(_Contract):
    """Original A values. Differences compare requested ranges, not clipped fetched ranges."""

    period: list[MonthRange] = Field(min_length=1, max_length=1)
    frequency: Frequency
    differs: PeriodDifferences


class PeriodNotice(_Contract):
    code: Identifier
    visible: bool
    message: str | None = None


class PeriodResolution(_Contract):
    table_id: Identifier
    statistics: list[Identifier] = Field(default_factory=list)
    frequency: Frequency
    frequency_alternatives: list[Frequency] = Field(default_factory=list)
    requested: list[PeriodRange] = Field(min_length=1)
    fetched: list[PeriodRange]
    missing: list[PeriodRange]
    calculation_extra: list[CalculationExtra]
    applied_sources: AppliedPeriodSources
    substituted: bool
    reference: PeriodReference | None
    notices: list[PeriodNotice]

    @model_validator(mode="after")
    def one_frequency(self) -> PeriodResolution:
        for span in self.requested + self.fetched + self.missing + self.calculation_extra:
            if span.frequency != self.frequency:
                raise PydanticCustomError("period_resolution_invalid", "Frequency mismatch")
        if self.frequency in self.frequency_alternatives:
            raise PydanticCustomError("period_resolution_invalid", "Alternative equals selected frequency")
        self.frequency_alternatives = list(dict.fromkeys(self.frequency_alternatives))
        return self


class BResultData(BaseModel):
    """Only the new result section is typed; existing B result fields remain intact."""

    model_config = ConfigDict(extra="allow")

    period_resolutions: list[PeriodResolution] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_tables(self) -> BResultData:
        ids = [row.table_id for row in self.period_resolutions]
        if len(ids) != len(set(ids)):
            raise PydanticCustomError("period_resolution_invalid", "Duplicate table")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler) -> dict[str, Any]:
        result = handler(self)
        if "period_resolutions" not in self.model_fields_set:
            result.pop("period_resolutions", None)
        return result


__all__ = [
    "PERIOD_CONDITION_VERSION", "AppliedPeriodSources", "BResultData", "CalculationExtra", "Frequency",
    "FrequencySource", "MonthRange", "PeriodAnswer", "PeriodCondition", "PeriodDifferences", "PeriodNotice",
    "PeriodRange", "PeriodReference", "PeriodResolution", "PeriodSource",
]
