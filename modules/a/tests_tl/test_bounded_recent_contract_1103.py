"""A named end and a count are separate model facts; date arithmetic stays in the resolver."""

from __future__ import annotations

import copy
import json

import pytest
from test_semantic_recovery_fix_1102 import f, record

from claview_a import contract as K
from claview_a import graph as G
from claview_a.periods import semantic as S

V8 = "tl-contract-2026-10-28.8"
V9 = "tl-contract-2026-10-28.9"
V10 = "tl-contract-2026-10-28.10"
QUESTION = "2025년까지 최근 3년 월별 대출금리"


def bounded(**changes):
    return {"for": ["s1"], "quote": "2025년까지 최근 3년", "meaning": "range", "form": "relative",
            "start": "none", "end": "2025", "count": "3", "unit": "Y", "read": "ok", "refers_to": [],
            **changes}


@pytest.mark.parametrize("end,sub_unit,sub_value", [
    ("2025", None, None), ("2025-08", "M", "8"), ("2025-Q3", "Q", "3"), ("2025-H1", "H", "1"),
])
def test_named_end_is_structured_without_computing_start(end, sub_unit, sub_value):
    got = S.read_rows([bounded(end=end)], [f()], as_of="2026-10-29", answer_contract=V10)
    assert got["problems"] == []
    condition = next(c for c in got["conditions"] if c["kind"] == "period")
    attrs = condition["attrs"]
    assert attrs["form"] == "recent" and attrs["start_expr"] is None
    assert attrs["amount"] == 3 and attrs["unit"] == "year"
    assert attrs["end_expr"] == {"text": end, "year": "2025", "alias": None,
                                 "sub_unit": sub_unit, "sub_value": sub_value, "end_mark": False}
    assert condition["quote"] == "2025년까지 최근 3년"


@pytest.mark.parametrize("version", [V8, V9])
def test_old_contracts_still_reject_calendar_values_in_relative_rows(version):
    got = S.read_rows([bounded()], [f()], as_of="2026-10-29", answer_contract=version)
    assert [(p["code"], p["why"]) for p in got["problems"]] == [
        ("period_structure_invalid", "relative_with_calendar_values")]
    assert not [c for c in got["conditions"] if c["kind"] == "period"]


@pytest.mark.parametrize("change,why", [
    ({"start": "2023"}, "relative_with_calendar_values"),
    ({"end": "open"}, "relative_end_invalid"),
    ({"end": "same"}, "relative_end_invalid"),
    ({"end": "2025-13"}, "relative_end_invalid"),
    ({"end": "2025-Q5"}, "relative_end_invalid"),
    ({"end": "2025-H3"}, "relative_end_invalid"),
    ({"end": "2025-08-31"}, "relative_end_invalid"),
    ({"count": "0"}, "count_invalid"),
    ({"count": "-3"}, "count_invalid"),
    ({"count": "1.5"}, "count_invalid"),
    ({"unit": "none"}, "unit_invalid"),
])
def test_invalid_values_do_not_fall_back_to_catalog_end(change, why):
    got = S.read_rows([bounded(**change)], [f()], answer_contract=V10)
    assert [(p["code"], p["why"]) for p in got["problems"]] == [("period_structure_invalid", why)]
    assert not [c for c in got["conditions"] if c["kind"] == "period"]


def test_unbounded_recent_rows_remain_identical_between_contracts():
    row = bounded(end="none")
    before = S.read_rows([row], [f()], as_of="2026-10-29", answer_contract=V9)
    assert before == S.read_rows([row], [f()], as_of="2026-10-29", answer_contract=V10)


def test_calendar_offsets_still_cannot_carry_calendar_dates():
    got = S.read_rows([bounded(form="calendar_offset", count="-1")], [f()], answer_contract=V10)
    assert got["problems"][0]["why"] == "relative_with_calendar_values"


def test_new_contract_keeps_schema_shape_and_historical_versions():
    assert K.CONTRACT_VERSION == V10 and V9 in K.EARLIER_CONTRACTS
    assert K.output_schema(V10) == K.output_schema(V9)
    old = K.output_schema(V8)["properties"]["periods"]["items"]["properties"]
    assert "calendar_offset" not in old["form"]["enum"]
    assert "unsupported" not in old["meaning"]["enum"]
    assert K.BOUNDED_RECENT_CONTRACTS == (V10,)


def records(*, replacement=None):
    first = record([bounded()], [f(value="unreadable")], version=V10, question=QUESTION)
    second = record([bounded() if replacement is None else replacement], [f()], version=V10, question=QUESTION)
    return first, second


@pytest.mark.parametrize("change", [
    {"end": "none"}, {"end": "2024"}, {"count": "2"}, {"unit": "M"},
    {"form": "absolute", "start": "2023", "end": "2025", "count": "none", "unit": "none"},
])
def test_frequency_repair_may_not_change_valid_bounded_recent_meaning(change):
    first, second = records(replacement=bounded(**change))
    original = copy.deepcopy(first)
    assert "periods:2025년까지 최근 3년" in G.condition_preservation(first, second, "s1", "s1")
    assert first == original


def test_frequency_only_repair_keeps_the_period_and_records_only_the_failed_row():
    first, second = records()
    assert not G.condition_preservation(first, second, "s1", "s1")
    changes = G.condition_corrections(first, second, "s1", "s1")
    assert len(changes) == 1 and changes[0]["kind"] == "frequencies"
    assert changes[0]["why"] == ["frequency_unreadable"]


def test_recovery_validation_uses_the_records_contract():
    first, second = records()
    assert G._row_problems(first["model_output"], "s1", first["as_of"], V10) == [
        {"code": "frequency_unreadable", "quote": "월별", "structured": {"value": "unreadable", "anchor": "none"}}]
    old = G._row_problems(first["model_output"], "s1", first["as_of"], V9)
    assert old[0]["code"] == "period_structure_invalid"
    assert old[0]["why"] == "relative_with_calendar_values"


def test_declared_absence_can_still_be_repaired_in_current_contract():
    absent = bounded(quote="대출금리", meaning="not_stated", form="none", end="none", count="none", unit="none")
    first, second = records()
    first["model_output"]["periods"] = [absent]
    assert not G.condition_preservation(first, second, "s1", "s1")


def test_applied_user_answer_cannot_be_replaced_during_recovery():
    first, second = records()
    first["decisions"][0]["condition_answers"] = [{"kind": "period", "text": "2025년까지 최근 3년"}]
    assert "user_answers" in G.condition_preservation(first, second, "s1", "s1")


@pytest.mark.parametrize("mode,expected", [("repair_frequency", "selected"),
                                            ("change_end", "failed"), ("still_unreadable", "failed")])
def test_graph_uses_one_recovery_and_preserves_named_end(layer, mode, expected):
    from test_period_semantics_1102 import TAIL, F, output

    question = f"2025년까지 최근 3년 월별 {TAIL}"
    first = output(question, layer, [bounded()], [F("월별", "unreadable")], contract=V10)
    after_period = bounded(end="2024") if mode == "change_end" else bounded()
    after_frequency = "unreadable" if mode == "still_unreadable" else "M"
    second = output(question, layer, [after_period], [F("월별", after_frequency)], contract=V10)
    calls, budgets = [], []

    def judge(payload, request):
        calls.append(copy.deepcopy(payload))
        assert len(calls) <= 2
        return json.dumps(first if len(calls) == 1 else second, ensure_ascii=False)

    def budget():
        budgets.append(True)
        return len(budgets) == 1

    judge.answer_contract = V10
    state = G.run(question, layer, judge, K.Settings("fake"), "2026-10-29",
                  research=True, research_budget=budget)
    assert len(calls) == 2 and len(budgets) == 1
    assert calls[1]["targets"][0]["conditions"]["periods"] == [bounded()]
    assert all(p["code"] == "frequency_unreadable" for p in calls[1]["targets"][0]["condition_problems"])
    decision, = state["record"]["decisions"]
    assert decision["outcome"] == expected, decision["reasons"]
    if expected == "selected":
        assert decision["final_tables"][0]["period"] == [{"start": "2023-01", "end": "2025-12"}]
        fixes = state["record"]["rejudge"]["condition_corrections"]
        assert len(fixes) == 1 and fixes[0]["kind"] == "frequencies"
    elif mode == "change_end":
        assert state["record"]["rejudge"]["rejected"][0]["why"] == "condition_lost"
