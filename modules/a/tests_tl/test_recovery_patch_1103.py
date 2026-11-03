"""Only diagnosed components are replaced; valid intent is copied before rechecking."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_reselection_guard_1103 import period_fixture

from claview_a import contract as K
from claview_a import graph as G


def run_pair(monkeypatch, question, first, second, cards, views):
    monkeypatch.setattr(G, "build_pool", lambda *a, **kw: {})
    monkeypatch.setattr(G, "build_cards", lambda *a, **kw: cards)
    monkeypatch.setattr(G, "selection_catalog", lambda _: SimpleNamespace(views=views))
    calls = []

    def judge(payload, request):
        calls.append(payload)
        return json.dumps(first if len(calls) == 1 else second, ensure_ascii=False)

    judge.answer_contract = K.CONTRACT_VERSION
    state = G.run(question, None, judge, K.Settings("fake"), "2026-10-29", research=True)
    assert len(calls) == 2
    return state


@pytest.mark.parametrize("change", ["order", "synonym", "quote_parts", "scope_loss", "statistic_space"])
def test_valid_scope_is_copied_before_checking_the_new_table(monkeypatch, change):
    q, first, cards, views = period_fixture()
    second = deepcopy(first)
    second["choices"][0]["table"] = "g2.t1"
    row = second["focuses"][0]
    if change == "order":
        row["text"] = "실업자와 취업자"
    elif change == "synonym":
        row["text"] = "취업자 및 실업자"
    elif change == "quote_parts":
        row["quotes"] = ["취업자", "실업자"]
    elif change == "scope_loss":
        row.update(text="취업자", quotes=["취업자"])
    else:
        second["statistics"][0]["quote"] = "취업자와  실업자"
    state = run_pair(monkeypatch, q, first, second, cards, views)
    record = state["record"]
    assert record["decisions"][0]["outcome"] == "selected"
    assert record["decisions"][0]["representative"] == "T2"
    assert record["model_output"]["focuses"] == first["focuses"]
    assert record["rejudge"]["second_round_raw_output"] == second
    assert state["response_text"] == json.dumps(second, ensure_ascii=False)


def test_calculation_dropped_by_recovery_is_restored_not_silently_lost(monkeypatch):
    q, first, cards, views = period_fixture()
    q += " 전년 대비도 계산해줘"
    first["calculations"] = [{"for": ["s1"], "quote": "전년 대비"}]
    second = deepcopy(first)
    second["choices"][0]["table"] = "g2.t1"
    second["calculations"] = []
    state = run_pair(monkeypatch, q, first, second, cards, views)
    assert state["record"]["decisions"][0]["outcome"] == "selected"
    assert state["record"]["model_output"]["calculations"] == first["calculations"]
    assert state["record"]["rejudge"]["preserved_fields"]


def selector_fixture():
    q, first, cards, views = period_fixture()
    views["T1"].periods = [("Y", "2000", "2025")]
    cards[0]["tables"][0]["periods"][0]["start"] = "2000"
    q = "개정 13판 " + q
    cards[0]["catalog_matches"] = [{"table_id": "T1", "axis": "분류 판", "value": "개정 13판", "item_id": "i13"}]
    return q, first, cards, views


def test_selector_repair_keeps_original_scope_and_model_authored_qualifier(monkeypatch):
    q, first, cards, views = selector_fixture()
    second = deepcopy(first)
    text = "취업자와 실업자 개정 13판"
    second["focuses"][0].update(text=text, quotes=["개정 13판", "취업자와 실업자"])
    state = run_pair(monkeypatch, q, first, second, cards, views)
    record = state["record"]
    assert record["decisions"][0]["outcome"] == "selected"
    focus = record["model_output"]["focuses"][0]
    assert first["focuses"][0]["text"] in focus["text"]
    assert text in focus["text"]
    assert record["rejudge"]["second_round_raw_output"] == second


@pytest.mark.parametrize("text", [
    "개정 13판의 취업자와 개정 13판의 실업자",
    "개정 13판의 취업자만, 실업자 제외",
])
def test_selector_repair_never_joins_unverified_new_scope_to_force_acceptance(monkeypatch, text):
    # The first wording may be equivalent, but the current contract does not isolate a selector-only patch.
    # Keep the conservative refusal instead of accepting the second wording by concatenating the old focus.
    q, first, cards, views = selector_fixture()
    second = deepcopy(first)
    second["focuses"][0].update(text=text, quotes=["개정 13판", "취업자와 실업자"])
    state = run_pair(monkeypatch, q, first, second, cards, views)
    assert state["record"]["decisions"][0]["outcome"] == "failed"
    assert not any(p["kind"] == "focuses" for p in state["record"]["rejudge"]["preserved_fields"])


@pytest.mark.parametrize("broken", ["missing_selector", "invented_quote", "over_limit"])
def test_selector_repair_still_needs_grounding_and_shared_focus_limit(monkeypatch, broken):
    q, first, cards, views = selector_fixture()
    second = deepcopy(first)
    second["focuses"][0].update(text="개정 13판의 취업자와 개정 13판의 실업자", quotes=["개정 13판", "취업자와 실업자"])
    if broken == "missing_selector":
        second["focuses"][0]["text"] = "취업자와 실업자"
    elif broken == "invented_quote":
        second["focuses"][0]["quotes"].append("원문에 없는 말")
    else:
        second["focuses"][0]["text"] += "분류" * 60
    state = run_pair(monkeypatch, q, first, second, cards, views)
    assert state["record"]["decisions"][0]["outcome"] == "failed"


def test_whitespace_match_is_unique_and_keeps_original_source():
    first = {"model_output": {"statistics": [{"id": "s1", "quote": "취업자와 실업자"}]}}
    second = {"model_output": {"statistics": [{"id": "s9", "quote": "취업자와  실업자"}]}}
    assert G._match(first, second, "s1") == "s9"
    second["model_output"]["statistics"].append({"id": "s8", "quote": "취업자와실업자"})
    assert G._match(first, second, "s1") is None


def test_repaired_scope_is_checked_on_new_table_not_just_marked_valid(monkeypatch):
    q, first, cards, views = period_fixture()
    second = deepcopy(first)
    second["choices"][0]["table"] = "g2.t1"
    second["choices"][0]["evidence"] = []
    second["focuses"][0]["text"] = "실업자와 취업자"
    state = run_pair(monkeypatch, q, first, second, cards, views)
    assert state["record"]["decisions"][0]["outcome"] == "failed"
