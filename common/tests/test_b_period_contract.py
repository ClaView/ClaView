"""Shared A-to-B wire shapes, independent answer authority, and lossless HTTP serialization."""

from __future__ import annotations

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from claview_common.contracts import (
    BRequest,
    BResult,
    BResultData,
    BTableRef,
    MonthRange,
    PeriodAnswer,
    PeriodCondition,
    PeriodRange,
    PeriodResolution,
    TableRef,
)


def answer(kind="period", statistics=None, **extra):
    return {
        "kind": kind, "question_id": f"q:{kind}", "question_version": 0,
        "statistics": statistics if statistics is not None else ["s1"],
        "option_id": None, "value": "2024년" if kind == "period" else "M",
        "text": "2024년" if kind == "period" else None, "source": "user_answer", **extra,
    }


def condition(**extra):
    return {
        "statistics": ["s1"], "frequency": "M", "frequency_source": "catalog",
        "period": [{"start": "2024-01", "end": "2024-12"}], "period_source": "user_answer",
        "period_phrase": "2024년", "answers": [answer()], **extra,
    }


def table(**extra):
    return {"org_id": "301", "table_id": "T_SHARED", "table_name": "Example", **extra}


def resolution(**extra):
    span = {"frequency": "M", "start": "202401", "end": "202412"}
    return {
        "table_id": "T_SHARED", "statistics": ["s1"], "frequency": "M",
        "requested": [span], "fetched": [copy.deepcopy(span)], "missing": [], "calculation_extra": [],
        "applied_sources": {"period": "a_user_answer", "frequency": "b_computed"},
        "substituted": False,
        "reference": {"period": [{"start": "2024-01", "end": "2024-12"}], "frequency": "M",
                      "differs": {"period": False, "frequency": False}}, "notices": [], **extra,
    }


@pytest.mark.parametrize(("period_source", "frequency_source", "kinds"), [
    ("user_answer", "catalog", ["period"]),
    ("quoted", "user_answer", ["frequency"]),
    ("user_answer", "user_answer", ["period", "frequency"]),
    ("quoted", "question", []),
])
def test_authority_is_independent_for_period_and_frequency(period_source, frequency_source, kinds):
    body = condition(period_source=period_source, frequency_source=frequency_source,
                     answers=[answer(kind) for kind in kinds])
    parsed = PeriodCondition.model_validate(body)
    assert parsed.period_source == period_source
    assert parsed.frequency_source == frequency_source
    assert [row.kind for row in parsed.answers] == kinds


def test_multiple_answers_jointly_cover_multiple_statistics():
    body = condition(statistics=["s1", "s2"], answers=[
        answer(statistics=["s1"], question_id="q:s1:period"),
        answer(statistics=["s2"], question_id="q:s2:period"),
    ])
    parsed = PeriodCondition.model_validate(body)
    assert len(parsed.answers) == 2
    assert parsed.statistics == ["s1", "s2"]


def test_duplicate_common_answers_merge_scopes_without_changing_input():
    body = condition(statistics=["s1", "s2"], answers=[answer(statistics=["s1"]), answer(statistics=["s2"])])
    original = copy.deepcopy(body)
    parsed = PeriodCondition.model_validate(body)
    assert len(parsed.answers) == 1
    assert parsed.answers[0].statistics == ["s1", "s2"]
    assert body == original


@pytest.mark.parametrize("change", [
    {"value": "2025년"}, {"text": "2025년"}, {"option_id": "different"},
])
def test_conflicting_duplicate_answers_fail(change):
    with pytest.raises(ValidationError, match="period_condition_answer_conflict"):
        PeriodCondition.model_validate(condition(answers=[answer(), answer(**change)]))


@pytest.mark.parametrize("changes", [
    {"statistics": []}, {"statistics": ["s1", "s2"]},
    {"answers": []}, {"answers": [answer(statistics=["s9"])]},
    {"answers": [answer("frequency")]},
    {"frequency_source": "user_answer"},
])
def test_answer_scope_and_kind_cannot_be_missing(changes):
    with pytest.raises(ValidationError, match="period_condition_answer_missing"):
        PeriodCondition.model_validate(condition(**changes))


def test_unrelated_frequency_answer_cannot_authorize_period():
    with pytest.raises(ValidationError, match="Missing answer target coverage for period"):
        PeriodCondition.model_validate(condition(
            statistics=["s1", "s2"], frequency_source="user_answer",
            answers=[answer(statistics=["s1"]), answer("frequency", statistics=["s1", "s2"])],
        ))


@pytest.mark.parametrize("changes", [
    {"source": "quoted"}, {"kind": "basis_axis"}, {"question_version": True},
    {"question_version": -1}, {"statistics": []}, {"statistics": [" "]},
    {"value": None, "text": None}, {"table_id": "T_SHARED"},
])
def test_answer_tracking_subset_rejects_invalid_shapes(changes):
    with pytest.raises(ValidationError):
        PeriodAnswer.model_validate(answer(**changes))


@pytest.mark.parametrize("changes", [
    {"period_source": "default"}, {"frequency_source": "default"}, {"frequency_source": None},
    {"frequency": "D"}, {"period": []},
    {"period": [{"start": "2024-01", "end": "2024-12"}] * 2},
    {"period_phrase": {"text": "2024年", "source": "quoted"}},
    {"unrecognized": True},
])
def test_condition_shape_rejects_defaults_and_unsupported_fields(changes):
    with pytest.raises(ValidationError):
        PeriodCondition.model_validate(condition(**changes))


@pytest.mark.parametrize("span", [
    {"start": "2024-13", "end": "2025-01"}, {"start": "2024-00", "end": "2024-12"},
    {"start": "0000-01", "end": "2024-12"}, {"start": "2024-1", "end": "2024-12"},
    {"start": "2024-12", "end": "2024-01"}, {"start": 202401, "end": "2024-12"},
])
def test_month_ranges_are_real_ordered_months(span):
    with pytest.raises(ValidationError):
        MonthRange.model_validate(span)


def test_reference_frequency_does_not_force_period_boundary_conversion():
    # PC-34: only B can choose a supported frequency; the input contract keeps the exact user months.
    parsed = PeriodCondition.model_validate(condition(
        frequency="Q", period=[{"start": "2024-02", "end": "2024-05"}],
    ))
    assert parsed.frequency == "Q" and parsed.period[0].start == "2024-02"


def test_optional_phrase_can_be_absent_and_quoted_reference_needs_no_answers():
    body = condition(period_source="quoted", answers=[])
    del body["period_phrase"]
    del body["statistics"]
    assert PeriodCondition.model_validate(body).period_phrase is None


def test_existing_a_table_shape_stays_unchanged():
    assert TableRef.model_validate(table(focus="scope", period_condition=condition())).model_dump() == table()
    assert BTableRef.model_validate(table()).model_dump() == table()
    assert BTableRef.model_validate(table(focus=None, period_condition=None)).model_dump() == table()


@pytest.mark.parametrize("focus", ["", "  ", "\t\n"])
def test_empty_focus_is_rejected_with_a_stable_error_code(focus):
    with pytest.raises(ValidationError) as exc:
        BTableRef.model_validate(table(focus=focus))
    assert exc.value.errors()[0]["type"] == "period_condition_invalid"


def test_answer_errors_expose_contract_codes_without_parsing_messages():
    with pytest.raises(ValidationError) as missing:
        PeriodCondition.model_validate(condition(answers=[]))
    assert missing.value.errors()[0]["type"] == "period_condition_answer_missing"
    with pytest.raises(ValidationError) as conflict:
        PeriodCondition.model_validate(condition(answers=[answer(), answer(value="2025년")]))
    assert conflict.value.errors()[0]["type"] == "period_condition_answer_conflict"


def test_b_http_roundtrip_preserves_new_fields_without_adding_missing_keys():
    app = FastAPI()
    seen = []

    @app.post("/resolve")
    def receive(body: BRequest):
        rows = [row.model_dump() for row in body.tables]  # The real B HTTP endpoint uses this serialization.
        seen.append(rows)
        return rows

    with TestClient(app) as client:
        result = client.post("/resolve", json={"query": "Example", "tables": [table()]}).json()
        assert result == [table()]
        response = client.post("/resolve", json={
            "query": "Example", "tables": [table(focus="scope", period_condition=condition())],
        })
        assert response.status_code == 200
        assert response.json()[0]["focus"] == "scope"
        assert response.json()[0]["period_condition"]["period_source"] == "user_answer"
        invalid = client.post("/resolve", json={
            "query": "Example", "tables": [table(period_condition=condition(answers=[]))],
        })
        assert invalid.status_code == 422
        assert len(seen) == 2


def test_duplicate_tables_have_one_identical_condition():
    row = table(focus="scope", period_condition=condition())
    body = {"query": "Example", "tables": [row, copy.deepcopy(row)]}
    assert len(BRequest.model_validate(body).tables) == 1
    body["tables"][1]["period_condition"]["frequency"] = "Q"
    with pytest.raises(ValidationError, match="period_condition_conflicting_tables"):
        BRequest.model_validate(body)


def test_duplicate_table_cannot_silently_drop_different_focus():
    with pytest.raises(ValidationError, match="Duplicate table must be combined"):
        BRequest.model_validate({"query": "Example", "tables": [
            table(focus="first", period_condition=condition()),
            table(focus="second", period_condition=condition()),
        ]})


def test_legacy_request_table_list_is_not_deduplicated():
    assert len(BRequest.model_validate({"query": "Example", "tables": [table(), table()]}).tables) == 2


@pytest.mark.parametrize(("frequency", "start", "end"), [
    ("Y", "2024", "2025"), ("H", "202401", "202402"),
    ("Q", "202401", "202404"), ("M", "202401", "202412"),
])
def test_b_period_notation(frequency, start, end):
    assert PeriodRange(frequency=frequency, start=start, end=end).end == end


@pytest.mark.parametrize(("frequency", "start", "end"), [
    ("Y", "202401", "202412"), ("H", "202401", "202403"),
    ("Q", "202400", "202404"), ("Q", "202401", "202405"),
    ("M", "202401", "202413"), ("Y", "0000", "2024"),
    ("M", "202412", "202401"), ("M", "2024-01", "2024-12"),
])
def test_b_period_notation_rejects_wrong_frequency_and_order(frequency, start, end):
    with pytest.raises(ValidationError):
        PeriodRange(frequency=frequency, start=start, end=end)


def test_result_extra_fields_and_original_reference_survive():
    row = resolution(fetched=[{"frequency": "M", "start": "202401", "end": "202410"}],
                     missing=[{"frequency": "M", "start": "202411", "end": "202412"}])
    payload = {"selections": [{"items": ["keep"]}], "custom": {"keep": True}, "period_resolutions": [row]}
    original = copy.deepcopy(payload)
    parsed = BResult(outcome={"stage": "B", "status": "ok", "result": payload, "new_envelope_field": 42})
    dumped = parsed.model_dump()
    assert dumped["outcome"]["result"] == original
    assert dumped["outcome"]["new_envelope_field"] == 42
    assert dumped["contract_version"] == "b-result/2"
    assert BResultData.model_validate(payload).model_dump()["custom"] == {"keep": True}
    assert parsed.outcome.result["period_resolutions"][0]["reference"]["differs"]["period"] is False


def test_legacy_result_does_not_acquire_an_empty_new_section():
    payload = {"selections": ["kept"], "unknown_old_field": 42}
    assert BResultData.model_validate(payload).model_dump() == payload
    assert BResult(outcome={"stage": "B", "status": "ok", "result": payload}).outcome.result == payload


@pytest.mark.parametrize("change", [
    {"fetched": [{"frequency": "Q", "start": "202401", "end": "202404"}]},
    {"applied_sources": {"period": "a_quoted_fallback", "frequency": "a_quoted_fallback"}},
    {"substituted": "false"},
    {"frequency_alternatives": ["M"]},
])
def test_malformed_new_results_fail_without_touching_legacy_fields(change):
    with pytest.raises(ValidationError):
        BResult(outcome={"stage": "B", "status": "ok", "result": {
            "selections": [], "period_resolutions": [resolution(**change)],
        }})


def test_duplicate_resolution_tables_and_null_section_fail():
    for rows in ([resolution(), resolution()], None):
        with pytest.raises(ValidationError):
            BResultData.model_validate({"period_resolutions": rows})


def test_fallback_period_and_user_frequency_are_independent():
    parsed = PeriodResolution.model_validate(resolution(applied_sources={
        "period": "a_quoted_fallback", "frequency": "a_user_answer",
    }))
    assert parsed.applied_sources.frequency == "a_user_answer"


def test_new_models_export_json_schema():
    for model in (PeriodCondition, PeriodAnswer, BTableRef, PeriodResolution, BResultData):
        assert model.model_json_schema()["title"] == model.__name__
