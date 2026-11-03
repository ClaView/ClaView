"""Illustrative period fixtures preserve authority and complete query-range accounting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claview_common.contracts import BRequest, MonthRange, PeriodRange, PeriodResolution

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASES = ["period_answer_only", "frequency_answer_only", "both_answers", "quoted_reference",
         "same_table_two_statistics"]


def pair(name):
    request = BRequest.model_validate_json((FIXTURES / "b_period_request" / f"{name}.json").read_text("utf-8"))
    resolution = PeriodResolution.model_validate_json(
        (FIXTURES / "b_period_resolution" / f"{name}.json").read_text("utf-8"))
    return request, resolution


def months(spans: list[MonthRange] | list[PeriodRange]) -> set[int]:
    """Expand already validated example ranges for assertions only; no production period decisions."""
    result = set()
    for span in spans:
        if isinstance(span, MonthRange):
            start = int(span.start[:4]) * 12 + int(span.start[5:]) - 1
            end = int(span.end[:4]) * 12 + int(span.end[5:]) - 1
        else:
            width = {"Y": 12, "H": 6, "Q": 3, "M": 1}[span.frequency]
            first = 1 if span.frequency == "Y" else int(span.start[4:])
            last = 1 if span.frequency == "Y" else int(span.end[4:])
            start = int(span.start[:4]) * 12 + (first - 1) * width
            end = int(span.end[:4]) * 12 + last * width - 1
        result.update(range(start, end + 1))
    return result


@pytest.mark.parametrize("name", CASES)
def test_fixture_models_and_targets_match(name):
    request, resolution = pair(name)
    (table,) = request.tables
    condition = table.period_condition
    assert condition is not None
    assert table.table_id == resolution.table_id
    assert condition.statistics == resolution.statistics
    assert resolution.reference is not None
    assert resolution.reference.period == condition.period
    assert resolution.reference.frequency == condition.frequency
    # The result fixture remains a single table's record, not an undocumented envelope.
    assert json.loads(resolution.model_dump_json())["table_id"] == table.table_id


@pytest.mark.parametrize("name", CASES)
def test_fixture_fetched_and_missing_account_for_requested_and_calculation_ranges(name):
    _, resolution = pair(name)
    requested, fetched = months(resolution.requested), months(resolution.fetched)
    missing, extra = months(resolution.missing), months(resolution.calculation_extra)
    assert requested - fetched == missing
    assert extra <= fetched - requested
    assert fetched - requested == extra  # no unexplained data outside the display request


@pytest.mark.parametrize("name", CASES)
def test_fixture_applied_sources_match_each_fields_answer_authority(name):
    request, resolution = pair(name)
    condition = request.tables[0].period_condition
    period_answered = condition.period_source == "user_answer"
    frequency_answered = condition.frequency_source == "user_answer"
    assert (resolution.applied_sources.period == "a_user_answer") is period_answered
    assert (resolution.applied_sources.frequency == "a_user_answer") is frequency_answered
    if period_answered:
        assert months(resolution.requested) == months(condition.period)
    if frequency_answered:
        assert resolution.frequency == condition.frequency
    assert resolution.reference.differs.period == (months(resolution.requested) != months(condition.period))
    assert resolution.reference.differs.frequency == (resolution.frequency != condition.frequency)


@pytest.mark.parametrize("name", CASES)
def test_fixture_notices_use_the_agreed_codes_and_calculation_purpose(name):
    request, resolution = pair(name)
    codes = {notice.code for notice in resolution.notices}
    expected = set()
    if resolution.missing:
        answered = request.tables[0].period_condition.period_source == "user_answer"
        expected.add("confirmed_period_partly_unavailable" if answered else "requested_period_partly_unavailable")
    if resolution.reference.differs.period or resolution.reference.differs.frequency:
        expected.add("reference_period_differs")
    if resolution.calculation_extra:
        expected.add("calculation_range_extended")
        assert all(span.purpose == "yoy_baseline" for span in resolution.calculation_extra)
    assert codes == expected
    for notice in resolution.notices:
        assert notice.visible == (notice.code not in {"reference_period_differs", "calculation_range_extended"})
