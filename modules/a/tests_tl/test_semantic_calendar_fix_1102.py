"""Calendar offsets and incomplete meanings are structured, never inferred from quote wording."""

from __future__ import annotations

import pytest

from claview_a import contract as K
from claview_a import periods as PR
from claview_a.periods import semantic as S


def period(**values):
    return {"quote": "source expression", "for": ["s1"], "meaning": "range", "refers_to": [],
            "form": "absolute", "start": "2024", "end": "same", "count": "none", "unit": "none",
            "read": "ok", **values}


def frequency(value="M", **values):
    return {"quote": "source frequency", "for": ["s1"], "value": value, "anchor": "none", **values}


def empty_period(meaning, **values):
    return period(**({"meaning": meaning, "form": "none", "start": "none", "end": "none"} | values))


@pytest.mark.parametrize("as_of,unit,offset,start,end", [
    ("2026-10-29", "Y", "-1", "2025-01", "2025-12"),
    ("2026-10-29", "Y", "0", "2026-01", "2026-10"),
    ("2026-10-29", "M", "-1", "2026-09", "2026-09"),
    ("2026-01-01", "M", "-1", "2025-12", "2025-12"),
    ("2026-10-29", "Q", "-1", "2026-07", "2026-09"),
    ("2026-11-02", "Q", "0", "2026-10", "2026-11"),
    ("2026-01-01", "Q", "-1", "2025-10", "2025-12"),
    ("2026-10-29", "H", "-1", "2026-01", "2026-06"),
    ("2026-10-29", "H", "0", "2026-07", "2026-10"),
    ("2024-02-29", "M", "0", "2024-02", "2024-02"),
])
def test_calendar_point_uses_as_of_not_catalog_or_model_year(as_of, unit, offset, start, end):
    row = period(form="calendar_offset", start="none", end="none", count=offset, unit=unit)
    reading = S.read_rows([row], [frequency()], as_of=as_of)
    assert reading["problems"] == []
    span = PR.requested_span(reading["conditions"][0], as_of)
    assert (span["start"], span["end"]) == (start, end)
    assert reading["conditions"][0]["attrs"]["relative"] is True


def test_calendar_shape_can_be_validated_without_executing_date_calculation():
    row = period(form="calendar_offset", start="none", end="none", count="-1", unit="Y")
    assert S.read_rows([row], [frequency()])["problems"] == []


def test_recent_one_year_and_previous_year_are_different_requests():
    as_of = "2026-10-29"
    recent = period(form="relative", start="none", end="none", count="1", unit="Y")
    previous = {**recent, "form": "calendar_offset", "count": "-1"}
    spans = [PR.requested_span(S.read_rows([row], [frequency()], as_of=as_of)["conditions"][0], as_of)
             for row in (recent, previous)]
    assert [(s["start"], s["end"]) for s in spans] == [("2026-01", "2026-10"), ("2025-01", "2025-12")]


@pytest.mark.parametrize("change", [
    {"count": "1.5"}, {"count": "--1"}, {"count": "none"}, {"unit": "none"}, {"start": "2025"},
])
def test_bad_calendar_offsets_are_interpretation_errors(change):
    row = period(form="calendar_offset", start="none", end="none", count="-1", unit="Y") | change
    got = S.read_rows([row], [frequency()], as_of="2026-10-29")
    assert got["problems"][0]["code"] == "period_structure_invalid"


@pytest.mark.parametrize("row", [
    empty_period("not_stated", start="2024"),
    empty_period("latest_available", count="1"),
    empty_period("range", read="failed", start="2024"),
    empty_period("not_stated", refers_to=["s2"]),
    empty_period("same_as"),
])
def test_inactive_values_and_invalid_references_are_not_silently_ignored(row):
    got = S.read_rows([row], [frequency()])
    assert any(p["code"] == "period_structure_invalid" for p in got["problems"])


@pytest.mark.parametrize("value,anchor", [("not_stated", "end"), ("M", "bad")])
def test_invalid_frequency_anchors_are_not_silently_ignored(value, anchor):
    got = S.read_rows([period()], [frequency(value, anchor=anchor)])
    assert any(p["code"] == "frequency_unreadable" for p in got["problems"])


def test_incomplete_user_range_keeps_its_explicit_endpoint_without_defaulting():
    row = period(meaning="user_undecided", start="none", end="2023")
    got = S.read_rows([row], [frequency()])
    assert got["problems"] == []
    assert not [c for c in got["conditions"] if c["kind"] == "period"]
    assert got["partial_periods"] == [{"quote": row["quote"], "start": None, "end": "2023-12"}]


def test_ambiguous_range_is_not_invented_and_unsupported_is_not_internal_failure():
    ambiguous = empty_period("user_undecided")
    got = S.read_rows([ambiguous], [frequency()])
    assert got["problems"] == [] and not [c for c in got["conditions"] if c["kind"] == "period"]
    got = S.read_rows([empty_period("unsupported")], [frequency()])
    assert [p["code"] for p in got["problems"]] == ["period_expression_unsupported"]


def test_two_requested_frequencies_are_not_an_internal_model_error():
    got = S.read_rows([period()], [frequency("M"), frequency("Q")])
    assert [p["code"] for p in got["problems"]] == ["frequency_multiple_requested"]


def test_exclusion_keeps_policy_and_reports_unchecked_representation_limits():
    got = S.read_rows([empty_period("excluded")], [frequency()])
    p = got["problems"][0]
    assert p["code"] == "period_exclusion_unsupported"
    assert p["a_support"] == "unsupported"
    assert p["b_contract_support"] == "not_checked"


def test_contract_9_adds_calendar_meaning_without_rewriting_contract_8():
    current = K.output_schema()["properties"]["periods"]["items"]["properties"]
    older = K.output_schema("tl-contract-2026-10-28.8")["properties"]["periods"]["items"]["properties"]
    assert "calendar_offset" in current["form"]["enum"]
    assert "unsupported" in current["meaning"]["enum"]
    assert "calendar_offset" not in older["form"]["enum"]
    assert "unsupported" not in older["meaning"]["enum"]


@pytest.mark.parametrize("quote,unit,offset,start,end", [
    ("작년", "Y", "-1", "2025-01", "2025-12"),
    ("올해", "Y", "0", "2026-01", "2026-10"),
    ("지난달", "M", "-1", "2026-09", "2026-09"),
    ("이번 분기", "Q", "0", "2026-10", "2026-10"),
])
def test_calendar_rows_reach_real_table_selection_with_calendar_bounds(layer, quote, unit, offset, start, end):
    from test_period_semantics_1102 import TAIL, F, P, checked, output

    question = f"{quote} 월별 {TAIL}"
    row = P(quote, form="calendar_offset", count=offset, unit=unit)
    record = checked(question, layer, output(question, layer, [row], [F("월별", "M")]), K.CONTRACT_VERSION)
    decision = record["decisions"][0]
    assert decision["outcome"] == "selected", decision.get("reasons")
    assert decision["final_tables"][0]["period"] == [{"start": start, "end": end}]


def test_unsupported_meaning_does_not_spend_the_one_recovery(layer):
    from test_period_semantics_1102 import TAIL, F, P, checked, output

    from claview_a.graph import rejudge_targets

    question = f"정확한 범위를 정할 수 없는 시기의 월별 {TAIL}"
    row = P("정확한 범위를 정할 수 없는 시기", meaning="unsupported", form="none")
    record = checked(question, layer, output(question, layer, [row], [F("월별", "M")]), K.CONTRACT_VERSION)
    assert record["decisions"][0]["failure_kinds"] == ["unsupported_expression"]
    assert rejudge_targets(record)["targets"] == []


@pytest.mark.parametrize("quote,unit,frequency_quote", [("올해", "Y", "연간"), ("이번 분기", "Q", "분기별")])
def test_current_calendar_unit_keeps_as_of_open_end_policy(layer, quote, unit, frequency_quote):
    from test_period_semantics_1102 import TAIL, F, P, checked, final, output

    question = f"{quote} {frequency_quote} {TAIL}"
    structured = P(quote, form="calendar_offset", count="0", unit=unit)
    out = output(question, layer, [structured], [F(frequency_quote, unit)])
    current = checked(question, layer, out, K.CONTRACT_VERSION)["decisions"][0]
    assert current["outcome"] == "selected", current.get("reasons")
    legacy = output(question, layer, [{"quote": quote, "for": ["s1"], "meaning": "range", "refers_to": []}],
                    [{"quote": frequency_quote, "for": ["s1"]}], contract="tl-contract-2026-10-28.7")
    older = checked(question, layer, legacy, "tl-contract-2026-10-28.7")["decisions"][0]
    assert older["outcome"] == "selected" and final(current) == final(older)
