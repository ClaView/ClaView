"""B's side of the A -> B period condition (docs/contracts/A_B_기간조건_공용모델과_A송신부.md).

Pure helpers only: reading the condition, converting its month ranges to the table's periods,
range arithmetic and the `period_resolutions` rows. Which period a question asks for is still
decided in `stages` (it owns the wording rules); this module never reads the question.

Ranges here are (first, last) period indexes of one frequency (`metadata.period_number`).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from claview_common.contracts.period_condition import PeriodCondition

from .contracts import Json, StageFailure
from .metadata import period_number

FREQUENCY_ORDER = ("Y", "H", "Q", "M")
MONTHS_PER_PERIOD = {"Y": 12, "H": 6, "Q": 3, "M": 1}
Span = tuple[int, int]

# Codes the condition's own validation reports (docs: B 회신 v2, 3절).
INVALID = "period_condition_invalid"
ANSWER_MISSING = "period_condition_answer_missing"
ANSWER_CONFLICT = "period_condition_answer_conflict"
NOT_REPRESENTABLE = "period_condition_not_representable"
FREQUENCY_UNSUPPORTED = "period_condition_frequency_unsupported"
OUT_OF_COVERAGE = "period_condition_out_of_coverage"


def parse_condition(raw: Any) -> PeriodCondition:
    """The table's `period_condition` as the shared model; invalid input is refused by code.

    The HTTP boundary already validates with the same model; the in-process service path does not,
    so B checks again and reports the contract's own codes instead of a validation traceback.
    """
    if isinstance(raw, PeriodCondition):
        return raw
    try:
        return PeriodCondition.model_validate(raw)
    except ValidationError as exc:
        types = {error["type"] for error in exc.errors()}
        for code in (ANSWER_CONFLICT, ANSWER_MISSING):
            if code in types:
                raise StageFailure(code, "error", "period_condition") from None
        raise StageFailure(INVALID, "error", "period_condition") from None


# --- periods <-> months ---------------------------------------------------------------------


def slot(freq: str, year: int, month: int) -> int:
    """Index of the period of `freq` that holds this calendar month."""
    if freq == "Y":
        return year
    width = {"H": 2, "Q": 4, "M": 12}[freq]
    return year * width + (month - 1) // (12 // width)


def start_slot(freq: str, year: int, month: int) -> int | None:
    """Index of the period of `freq` that starts in this month; None when none does."""
    return None if (month - 1) % MONTHS_PER_PERIOD[freq] else slot(freq, year, month)


def end_slot(freq: str, year: int, month: int) -> int | None:
    """Index of the period of `freq` that ends in this month; None when none does."""
    return None if month % MONTHS_PER_PERIOD[freq] else slot(freq, year, month)


def span_of_months(freq: str, start: tuple[int, int], end: tuple[int, int]) -> Span | None:
    """Periods of `freq` covering exactly (year, month) .. (year, month); None when the span does not
    start and end on period boundaries (a quarter cannot show "2월", a year cannot show a half)."""
    first, last = start_slot(freq, *start), end_slot(freq, *end)
    return None if first is None or last is None else (first, last)


def ym(text: str) -> tuple[int, int]:
    return int(text[:4]), int(text[5:7])


def condition_span(freq: str, condition: PeriodCondition) -> Span | None:
    """The condition's month range as periods of `freq`; None when `freq` cannot show it exactly."""
    month_range = condition.period[0]
    return span_of_months(freq, ym(month_range.start), ym(month_range.end))


def span_months(freq: str, span: Span) -> tuple[tuple[int, int], tuple[int, int]]:
    """(first, last) (year, month) a span of periods of `freq` covers."""
    step = MONTHS_PER_PERIOD[freq]
    if freq == "Y":
        return (span[0], 1), (span[1], 12)
    width = 12 // step
    first_year, first_sub = divmod(span[0], width)
    last_year, last_sub = divmod(span[1], width)
    return (first_year, first_sub * step + 1), (last_year, (last_sub + 1) * step)


def month_text(value: tuple[int, int]) -> str:
    return f"{value[0]:04d}-{value[1]:02d}"


def period_text(index: int, freq: str) -> str:
    if freq == "Y":
        return str(index)
    width = {"H": 2, "Q": 4, "M": 12}[freq]
    year, sub = divmod(index, width)
    return f"{year:04d}{sub + 1:02d}"


def range_json(freq: str, span: Span) -> Json:
    return {"frequency": freq, "start": period_text(span[0], freq), "end": period_text(span[1], freq)}


def span_from_json(period: Json) -> Span:
    freq = period["frequency"]
    return period_number(period["start"], freq), period_number(period["end"], freq)


def coverage_span(table: Json, freq: str) -> Span | None:
    bounds = table["periods"].get(freq)
    if not bounds:
        return None
    return period_number(bounds["start"], freq), period_number(bounds["end"], freq)


# --- range arithmetic -----------------------------------------------------------------------


def intersect(a: Span, b: Span) -> Span | None:
    first, last = max(a[0], b[0]), min(a[1], b[1])
    return (first, last) if first <= last else None


def subtract(whole: Span, part: Span | None) -> list[Span]:
    """What of `whole` is not in `part` (zero, one or two ranges)."""
    if part is None:
        return [whole]
    out = []
    if whole[0] < part[0]:
        out.append((whole[0], min(whole[1], part[0] - 1)))
    if whole[1] > part[1]:
        out.append((max(whole[0], part[1] + 1), whole[1]))
    return [s for s in out if s[0] <= s[1]]


def representable_frequencies(table: Json, condition: PeriodCondition) -> list[str]:
    """Frequencies the table has that show the condition's range exactly, longest period first."""
    return [
        f for f in FREQUENCY_ORDER if f in table["periods"] and condition_span(f, condition) is not None
    ]


def reference_differs(condition: PeriodCondition | None, freq: str, requested: Span) -> Json | None:
    """A's original values and whether B's requested range / frequency differ from them."""
    if condition is None:
        return None
    first, last = span_months(freq, requested)
    month_range = condition.period[0]
    return {
        "period": [{"start": month_range.start, "end": month_range.end}],
        "frequency": condition.frequency,
        "differs": {
            "period": (month_text(first), month_text(last)) != (month_range.start, month_range.end),
            "frequency": condition.frequency != freq,
        },
    }


def resolution_row(table_id: str, condition: PeriodCondition | None, plan: Json) -> Json:
    """One `period_resolutions[]` row from a plan (see stages.plan_table_period)."""
    freq = plan["frequency"]
    row = {
        "table_id": table_id,
        "statistics": list(condition.statistics) if condition else [],
        "frequency": freq,
        "frequency_alternatives": [alt["frequency"] for alt in plan.get("alternatives", [])],
        "requested": [range_json(freq, plan["requested"])],
        "fetched": [range_json(freq, plan["fetched"])] if plan["fetched"] else [],
        "missing": [range_json(freq, s) for s in plan["missing"]],
        "calculation_extra": [
            {**range_json(freq, s), "purpose": plan["extra_purpose"]} for s in plan["extra"]
        ],
        "applied_sources": dict(plan["applied"]),
        "substituted": plan["substituted"],
        "reference": reference_differs(condition, freq, plan["requested"]),
        "notices": [dict(n) for n in plan["notices"]],
    }
    return row
