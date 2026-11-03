"""Recovery changes only diagnosed components; made-up responses, no live model or ledger."""
from __future__ import annotations

import copy

import pytest

from claview_a import graph as G


def record(*, reasons=()):
    out = {"statistics": [{"id": "s1", "quote": "국내 판매와 수출 물가", "name": "물가"}],
           "periods": [{"for": ["s1"], "quote": "2024년"}],
           "frequencies": [{"for": ["s1"], "quote": "월별"}],
           "focuses": [{"for": ["s1"], "text": "국내 판매와 수출 물가", "quotes": ["국내 판매", "수출"]}],
           "calculations": [{"for": ["s1"], "quote": "가장 크게 변한 달"}],
           "aggregations": [],
           "splitters": [{"for": ["s1"], "axis": "seasonal", "value": "원계열", "polarity": "required",
                          "quote": "원계열"}]}
    return {"question": "2024년 월별 국내 판매와 수출 물가 원계열과 가장 크게 변한 달",
            "model_output": out,
            "decisions": [{"statistic": "s1", "outcome": "failed", "reasons": list(reasons)}],
            "rejudge": {"targets": [{"statistic": "s1", "kind": "correction"}]}}


def adopted(first, second):
    second["decisions"][0].update(outcome="selected", reasons=[])
    return G.merge_rounds(first, second, first["rejudge"]["targets"])


@pytest.mark.parametrize("change", ["focus_text", "focus_quote", "calculation_deleted", "calculation_added"])
def test_table_recovery_cannot_change_normal_scope_or_calculation(change):
    first = record()
    second = copy.deepcopy(first)
    if change == "focus_text":
        second["model_output"]["focuses"][0]["text"] = "국내 판매 물가"
    elif change == "focus_quote":
        second["model_output"]["focuses"][0]["quotes"] = ["국내 판매"]
    elif change == "calculation_deleted":
        second["model_output"]["calculations"] = []
    else:
        second["model_output"]["calculations"].append({"for": ["s1"], "quote": "수출"})
    result = adopted(first, second)
    assert result["rejudge"]["rejected"][0]["why"] == "condition_lost"
    assert result["model_output"] == first["model_output"]


def basis_change(second):
    second["model_output"]["splitters"][0]["polarity"] = "allowed"
    second["model_output"]["changes"] = [{"for": ["s1"], "axis": "seasonal", "value": "원계열",
        "from": "required", "to": "allowed", "quote": "원계열", "reason": "Review the first interpretation."}]


@pytest.mark.parametrize("reason", [
    {"code": "focus_quote_not_in_question"},
    {"code": "period_not_covered"},
    {"code": "rejudge_pending", "detail": {"axis": "seasonal"}},
    {"code": "basis_polarity_invalid", "detail": {"cause": "unreadable_row",
        "detail": {"axis": "stock", "quote": "말잔"}}},
    {"code": "basis_polarity_invalid", "detail": {"cause": "polarity_conflict",
        "detail": {"axis": "seasonal", "value": "계절조정"}}},
])
def test_unrelated_diagnosis_never_allows_weakening_a_valid_basis(reason):
    first = record(reasons=[reason])
    second = copy.deepcopy(first)
    basis_change(second)
    assert adopted(first, second)["rejudge"]["rejected"][0]["why"] == "condition_lost"


def test_actual_diagnosed_basis_can_change_with_provenance_and_reason():
    first = record(reasons=[{"code": "basis_polarity_invalid", "detail": {"cause": "polarity_conflict",
        "detail": {"axis": "seasonal", "value": "원계열"}}}])
    second = copy.deepcopy(first)
    basis_change(second)
    assert adopted(first, second)["rejudge"]["merged"]
    second["model_output"]["changes"][0]["reason"] = ""
    assert adopted(first, second)["rejudge"]["rejected"]


@pytest.mark.parametrize("code", ["focus_quote_not_in_question", "focus_too_long", "focus_catalog_condition_missing"])
def test_diagnosed_focus_can_change_but_calculations_stay(code):
    first = record(reasons=[{"code": code, "detail": {"selectors": []}}])
    second = copy.deepcopy(first)
    text = "국내 판매와 수출 물가 2024년 기준" if code == "focus_catalog_condition_missing" else "판매·수출 물가"
    second["model_output"]["focuses"][0].update(text=text, quotes=["국내 판매와 수출 물가"])
    assert adopted(first, second)["rejudge"]["merged"]
    second["model_output"]["calculations"] = []
    assert adopted(first, second)["rejudge"]["rejected"]


@pytest.mark.parametrize("change", ["quote", "text"])
def test_missing_selector_repair_preserves_the_existing_scope(change):
    first = record(reasons=[{"code": "focus_catalog_condition_missing",
        "detail": {"selectors": [{"axis": "기준연도", "value": "2024년 기준"}]}}])
    first["question"] += " 2024년 기준"
    second = copy.deepcopy(first)
    focus = second["model_output"]["focuses"][0]
    focus["text"] += " 2024년 기준"
    focus["quotes"].append("2024년 기준")
    second["decisions"][0]["focus"] = copy.deepcopy(focus)
    assert adopted(first, second)["rejudge"]["merged"]
    if change == "quote":
        focus["quotes"].remove("수출")
    else:
        focus["text"] = "국내 판매 물가 2024년 기준"
    second["decisions"][0]["focus"] = copy.deepcopy(focus)
    assert adopted(first, second)["rejudge"]["rejected"]


@pytest.mark.parametrize("code", ["focus_quote_not_in_question", "focus_too_long"])
def test_focus_rewrite_repairs_bad_quotes_without_losing_valid_sources(code):
    first = record(reasons=[{"code": code}])
    first["model_output"]["focuses"][0]["quotes"].append("원문에 없는 인용")
    second = copy.deepcopy(first)
    focus = second["model_output"]["focuses"][0]
    focus.update(text="판매·수출 물가", quotes=["국내 판매와 수출 물가"])
    assert adopted(first, second)["rejudge"]["merged"]
    focus["quotes"] = ["국내 판매"]
    assert adopted(first, second)["rejudge"]["rejected"]


@pytest.mark.parametrize("change", ["delete", "method", "frequency"])
def test_aggregation_intent_is_preserved_during_other_recovery(change):
    first = record()
    first["model_output"]["aggregations"] = [{"for": ["s1"], "requested_frequency": "Y",
        "source_frequency": "M", "method": "sum", "source_basis": "flow", "weights": [],
        "average_basis": "unknown", "evidence": [{"kind": "title", "text": "월중 합계"}],
        "average_evidence": []}]
    second = copy.deepcopy(first)
    if change == "delete":
        second["model_output"]["aggregations"] = []
    else:
        field, value = ("method", "period_average") if change == "method" else ("requested_frequency", "Q")
        second["model_output"]["aggregations"][0][field] = value
    assert adopted(first, second)["rejudge"]["rejected"]


def test_reselecting_a_table_can_update_evidence_without_changing_aggregation_intent():
    first = record()
    first["model_output"]["aggregations"] = [{"for": ["s1"], "requested_frequency": "Y",
        "source_frequency": "M", "method": "sum", "source_basis": "flow", "weights": [],
        "average_basis": "unknown", "evidence": [{"kind": "title", "text": "월중 합계"}],
        "average_evidence": []}]
    second = copy.deepcopy(first)
    second["model_output"]["aggregations"][0]["evidence"] = [{"kind": "item", "text": "월중액"}]
    assert adopted(first, second)["rejudge"]["merged"]


def test_unchanged_rows_allow_whitespace_and_renumbering_without_mutating_first():
    first = record()
    original = copy.deepcopy(first)
    second = copy.deepcopy(first)
    second["model_output"]["statistics"][0]["id"] = "s9"
    second["decisions"][0]["statistic"] = "s9"
    for key, rows in second["model_output"].items():
        if key != "statistics":
            for row in rows:
                row["for"] = ["s9"]
    second["model_output"]["focuses"][0].update(text="국내판매와 수출물가", quotes=["수 출", "국내판매"])
    second["model_output"]["calculations"][0]["quote"] = "가장 크게 변한달"
    assert adopted(first, second)["rejudge"]["merged"]
    assert first == original
