"""Checked evidence and diagnosed quote repairs share the existing one recovery."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_reselection_guard_1103 import checked, period_fixture

from claview_a import graph as G
from claview_a import contract as K
from claview_a import reselection as R
from claview_a.cards import shown_value
from claview_a.search.tables import ItemView


def replacement(out, cards):
    new = deepcopy(out)
    new["choices"][0]["table"] = "g2.t1"
    new["choices"][0]["evidence"] = [
        {"kind": "item", "text": shown_value(row["items"][0])} for row in cards[1]["matched"]]
    return new


def test_decorated_item_evidence_retains_the_verified_target_items():
    q, out, cards, views = period_fixture()
    first = checked(q, out, cards, views)
    second = checked(q, replacement(out, cards), cards, views)
    assert second["decisions"][0]["citation"]["status"] == "verified"
    assert R.period_losses(first, second, "s1", "s1") == []
    merged = G.merge_rounds(first, second, first["rejudge"]["targets"])
    assert merged["decisions"][0]["outcome"] == "selected"
    assert merged["decisions"][0]["period"]["tables"][0]["period"] == [
        {"start": "2010-01", "end": "2025-12"}]


@pytest.mark.parametrize("change", ["wrong_id", "other_table", "missing_item", "ambiguous_id"])
def test_checked_item_identity_is_required_not_just_a_matching_name(change):
    q, out, cards, views = period_fixture()
    first = checked(q, out, cards, views)
    second = checked(q, replacement(out, cards), cards, views)
    evidence = second["decisions"][0]["model_evidence"]
    if change == "wrong_id":
        evidence[0]["item"]["item_ids"] = ["T2.not_the_requested_item"]
    elif change == "other_table":
        evidence[0]["table"] = "T1"
    elif change == "missing_item":
        evidence.pop()
    else:
        evidence[0]["item"]["item_ids"].append("T2.other")
    assert R.period_losses(first, second, "s1", "s1") == ["period_reselection:item_evidence_missing"]


@pytest.mark.parametrize("same_id", [True, False])
def test_reselection_cannot_freeze_an_item_shared_by_different_objects(same_id):
    q, out, cards, views = period_fixture()
    original = views["T2"].items[0]
    views["T2"].items.append(ItemView(9, "another_axis", original.item_id if same_id else "T2.other", original.name_raw,
                                     original.name_raw, original.name_raw, None, "명"))
    first = checked(q, out, cards, views)
    assert "period_reselection" not in first["decisions"][0]


def calculation_fixture():
    q, out, cards, views = period_fixture()
    q += " 증감률과 가장 크게 변한 해도 보여줘"
    out["calculations"] = [
        {"for": ["s1"], "quote": "증감률"},
        {"for": ["s1"], "quote": "가장 크게 변한 연도를 계산해 주세요"}]
    first = checked(q, out, cards, views)
    new = replacement(out, cards)
    new["calculations"][1]["quote"] = "가장 크게 변한 해"
    return q, out, cards, views, first, new


def test_only_the_diagnosed_invalid_calculation_quote_can_be_repaired():
    q, out, cards, views, first, new = calculation_fixture()
    first_snapshot = deepcopy(first)
    assert any(w["code"] == "calculation_quote_not_in_question"
               for w in first["decisions"][0]["warnings"])
    second = checked(q, new, cards, views)
    assert G.condition_preservation(first, second, "s1", "s1") == []
    merged = G.merge_rounds(first, second, first["rejudge"]["targets"])
    assert merged["decisions"][0]["outcome"] == "selected"
    assert [r["quote"] for r in merged["model_output"]["calculations"]] == ["증감률", "가장 크게 변한 해"]
    assert first == first_snapshot


@pytest.mark.parametrize("change", ["no_diagnosis", "other_diagnosis", "delete_good", "change_good",
                                    "delete_bad", "add_quote", "bad_replacement"])
def test_invalid_calculation_does_not_license_other_changes(change):
    q, out, cards, views, first, new = calculation_fixture()
    if change == "no_diagnosis":
        first["decisions"][0]["warnings"] = []
    elif change == "other_diagnosis":
        first["decisions"][0]["warnings"] = [
            {"code": "calculation_quote_not_in_question", "detail": "다른 행"}]
    elif change == "delete_good":
        new["calculations"].pop(0)
    elif change == "change_good":
        new["calculations"][0]["quote"] = "취업자"
    elif change == "delete_bad":
        new["calculations"].pop()
    elif change == "add_quote":
        new["calculations"].append({"for": ["s1"], "quote": "연간"})
    else:
        new["calculations"][1]["quote"] = "다시 만든 원문 밖 계산 인용"
    second = checked(q, new, cards, views)
    assert "calculations:changed" in G.condition_preservation(first, second, "s1", "s1")
    assert G.merge_rounds(first, second, first["rejudge"]["targets"])["rejudge"]["rejected"]


def test_repaired_calculation_keeps_other_statistics_rows_untouched():
    q, out, cards, views, first, new = calculation_fixture()
    # A warning attached to a different statistic cannot authorize this row's repair.
    warning = first["decisions"][0]["warnings"].pop()
    first["decisions"].append({"statistic": "s2", "warnings": [warning]})
    second = checked(q, new, cards, views)
    assert "calculations:changed" in G.condition_preservation(first, second, "s1", "s1")


def test_valid_calculation_cannot_become_editable_from_a_spurious_warning():
    q, out, cards, views, first, new = calculation_fixture()
    first["decisions"][0]["warnings"].append({"code": "calculation_quote_not_in_question", "detail": "증감률"})
    new["calculations"][0]["quote"] = "취업자"
    second = checked(q, new, cards, views)
    assert "calculations:changed" in G.condition_preservation(first, second, "s1", "s1")


def test_invalid_calculation_cannot_disappear_into_a_duplicate_of_a_valid_row():
    q, out, cards, views, first, new = calculation_fixture()
    new["calculations"][1]["quote"] = "증감률"
    second = checked(q, new, cards, views)
    assert "calculations:changed" in G.condition_preservation(first, second, "s1", "s1")
    assert G.merge_rounds(first, second, first["rejudge"]["targets"])["rejudge"]["rejected"]


def test_original_duplicate_calculations_remain_preserved_during_a_quote_repair():
    q, out, cards, views, _, new = calculation_fixture()
    out["calculations"].append(deepcopy(out["calculations"][0]))
    new["calculations"].append(deepcopy(new["calculations"][0]))
    first = checked(q, out, cards, views)
    second = checked(q, new, cards, views)
    assert G.condition_preservation(first, second, "s1", "s1") == []
    assert G.merge_rounds(first, second, first["rejudge"]["targets"])["rejudge"]["merged"]


def test_full_graph_accepts_checked_repairs_with_the_same_one_recovery(monkeypatch):
    q, out, cards, views, _, new = calculation_fixture()
    monkeypatch.setattr(G, "build_pool", lambda *a, **kw: {})
    monkeypatch.setattr(G, "build_cards", lambda *a, **kw: cards)
    monkeypatch.setattr(G, "selection_catalog", lambda _: SimpleNamespace(views=views))
    calls = []

    def judge(payload, request):
        calls.append(payload)
        return json.dumps(out if len(calls) == 1 else new, ensure_ascii=False)

    judge.answer_contract = K.CONTRACT_VERSION
    state = G.run(q, None, judge, K.Settings("fake"), "2026-10-29", research=True)
    assert len(calls) == 2
    assert state["record"]["decisions"][0]["outcome"] == "selected"
    assert state["record"]["model_output"]["calculations"] == new["calculations"]
    # Catalog item links are internal guards, not additional model request fields.
    assert "period_reselection_items" not in json.dumps(calls[1], ensure_ascii=False)
    assert G.after_check({"round": 2}) == "research_end"
