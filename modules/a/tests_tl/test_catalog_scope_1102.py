"""Keep explicit catalog classification labels distinct from the query date; no model/network."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import pytest

from test_focus_contract_1102 import FOCUS7, QUESTION, ScopeJudge, output
from test_focus_contract_1102 import case as case

from claview_a import check as CH, contract as K, graph as G
from claview_a.cards import build_cards, card_facts, render_card
from claview_a.catalog_scope import contains, matches, missing_scope
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog


def view(labels):
    return NS(table_id="T", dims=[("code", "분류 판")], items=[
        NS(name_raw=value, obj_id="code", item_id=f"i{n}") for n, value in enumerate(labels)])


def test_catalog_family_requires_real_labels_and_preserves_space_without_number_alias():
    v = view([f"개정 {n}판" for n in range(1, 15)])
    got = matches("개정 13판을 적용해줘", v)
    assert got == [{"table_id": "T", "axis": "분류 판", "value": "개정 13판", "item_id": "i12"}]
    assert not matches("개정 99판을 적용해줘", v)
    assert not matches("2024년", view(["2024년", "2023년"]))
    assert not matches("개정 13판", view(["개정 13판"]))
    assert contains("2024년 기준", "2024년기준")
    assert not contains("12024년 기준", "2024년기준")


def test_presence_does_not_invent_polarity_or_assign_another_statistic():
    rows = matches("개정 13판 제외", view(["개정 12판", "개정 13판"]))
    d = {"focus": {"text": "개정 13판 제외", "source": "model_scope"}, "final_tables": [{"table_id": "T"}]}
    assert not missing_scope(d, rows, "개정 13판 제외")
    d["focus"]["text"] = "지표"
    assert missing_scope(d, rows, "개정 13판 제외") == rows
    assert not missing_scope(d, rows, "다른 통계의 지표")
    d["focus"]["source"] = "legacy_quote"
    assert not missing_scope(d, rows, "개정 13판")


def make_record(case, layer, keep=False):
    cards = copy.deepcopy(case[0])
    q = "개정 13판 " + QUESTION
    cards[0]["catalog_matches"] = [{"table_id": case[1], "axis": "분류 판", "value": "개정 13판", "item_id": "i13"}]
    out = output(case)
    if keep:
        out["focuses"][0]["text"] += " 개정 13판"
        out["focuses"][0]["quotes"].append("개정 13판")
    parsed = K.parse_output(json.dumps(out, ensure_ascii=False), cards, FOCUS7)
    return CH.check(q, cards, parsed, selection_catalog(layer).views, "2026-10-29")


def test_omitted_catalog_selector_uses_existing_recovery_and_keeps_period(case, layer):
    first, fixed = make_record(case, layer), make_record(case, layer, keep=True)
    d = first["decisions"][0]
    assert d["outcome"] == "failed" and d["focus"]["status"] == "invalid"
    targets = G.rejudge_targets(first)["targets"]
    assert len(targets) == 1 and targets[0]["code"] == "focus_catalog_condition_missing"
    facts = G.rejudge_input(first, targets)[0]["facts"]
    assert facts[0]["selectors"][0]["value"] == "개정 13판"
    merged = G.merge_rounds(first, fixed, targets)
    assert merged["decisions"][0]["outcome"] == "selected"
    assert merged["model_output"]["periods"] == first["model_output"]["periods"]
    assert merged["model_output"]["frequencies"] == first["model_output"]["frequencies"]
    assert G.condition_preservation(first, first, "s1", "s1") == ["focus_selector:분류 판:개정 13판"]


def test_wrong_table_cannot_bypass_missing_selector_during_recovery(case, layer):
    first, fixed = make_record(case, layer), make_record(case, layer, keep=True)
    fixed["decisions"][0]["focus"]["text"] = "다른 표"
    assert "focus_selector:분류 판:개정 13판" in G.condition_preservation(first, fixed, "s1", "s1")


@pytest.mark.parametrize("fixed,budget,calls,selected", [(True, True, 2, True), (False, True, 2, False),
                                                      (True, False, 1, False)])
def test_selector_recovery_uses_one_existing_slot(case, layer, monkeypatch, fixed, budget, calls, selected):
    cards = copy.deepcopy(case[0])
    cards[0]["catalog_matches"] = [{"table_id": case[1], "axis": "분류 판", "value": "개정 13판", "item_id": "i13"}]
    monkeypatch.setattr(G, "build_cards", lambda *args: cards)
    first, second = output(case), output(case)
    if fixed:
        second["focuses"][0]["text"] += " 개정 13판"
        second["focuses"][0]["quotes"].append("개정 13판")
    judge = ScopeJudge(first, second)
    state = G.run("개정 13판 " + QUESTION, layer, judge, K.Settings("fake"), "2026-10-29",
                  research=True, research_budget=lambda: budget)
    assert len(judge.payloads) == calls
    assert (state["record"]["decisions"][0]["outcome"] == "selected") == selected
    assert state["record"]["model_output"]["periods"] == first["periods"]
    assert state["record"]["model_output"]["frequencies"] == first["frequencies"]


def test_partial_recovery_does_not_claim_another_statistics_global_label(case, layer):
    cards = copy.deepcopy(case[0])
    cards[0]["catalog_matches"] = [{"table_id": case[1], "axis": "분류 판", "value": "개정 13판", "item_id": "i13"}]
    parsed = K.parse_output(json.dumps(output(case), ensure_ascii=False), cards, FOCUS7)
    parsed["scope_statistic_count"] = 2  # only one target was re-judged; the other statistic is fixed
    record = CH.check("개정 13판 " + QUESTION, cards, parsed, selection_catalog(layer).views, "2026-10-29")
    assert record["decisions"][0]["outcome"] == "selected"


def test_large_catalog_axis_exposes_the_matching_value_without_listing_all(layer):
    # A real catalog regression: the thirteenth label was hidden by the card's 12-item listing cap.
    q = "2024년 기준 현금흐름표의 제조업 현금흐름을 2024년 한 해 보여줘"
    cards = build_cards(q, build_pool(q, layer), layer)
    card = next(c for c in cards if any(r["table_id"] == "DT_501Y013" for r in c["tables"]))
    got = [r for r in card["catalog_matches"] if r["table_id"] == "DT_501Y013"]
    assert any(r["value"] == "2024년기준" for r in got)
    assert "2024년기준" in card_facts(card)["DT_501Y013"]
    lookup = CH.Cards(cards, selection_catalog(layer).views)
    assert lookup.kinds_of("DT_501Y013", "2024년기준") == {"item"}
    assert lookup.resolve_item("DT_501Y013", "2024년기준")["item_ids"] == [got[0]["item_id"]]
    assert not lookup.kinds_of("DT_501Y079", "2024년기준")
    assert "질문과 일치하는 분류값" in render_card(card)
    assert any(not a["listed"] for a in card["axes"]["DT_501Y013"] if a["count"] == 13)
