"""A correct per-statistic scope must fit the existing merged B focus, with one recovery and fixed ownership."""

from __future__ import annotations

import copy

import pytest
from test_focus_contract_1102 import QUESTION, ScopeJudge, checked, output
from test_focus_contract_1102 import case as case

from claview_a import check as CH
from claview_a import contract as K
from claview_a import graph as G


def paired(case, lengths=(59, 56)):
    out = output(case)
    out["statistics"].append({"id": "s2", "quote": "가계대출 금리", "name": "가계대출 금리"})
    for key in ("choices", "periods", "frequencies", "splitters"):
        out[key][0]["for"].append("s2")
    out["focuses"][0]["text"] = "가" * lengths[0]
    out["focuses"].append({"for": ["s2"], "text": "나" * lengths[1], "quotes": ["가계대출 금리"]})
    return out


def test_joined_scope_is_recoverable_before_handoff_without_truncation(case, layer):
    out = paired(case)
    record = checked(case, layer, out)
    assert [d["outcome"] for d in record["decisions"]] == ["failed", "failed"]
    assert record["model_output"] == out
    assert [len(d["focus"]["text"]) for d in record["decisions"]] == [59, 56]
    targets = G.rejudge_targets(record)["targets"]
    assert [t["statistic"] for t in targets] == ["s1", "s2"]
    assert all(t["code"] == "focus_too_long" for t in targets)
    facts = G.rejudge_input(record, targets)[0]["facts"][0]
    assert facts["statistics"] == ["s1", "s2"] and facts["chars"] == 118 and facts["limit"] == 100
    assert [len(m["text"]) for m in facts["members"]] == [59, 56]


@pytest.mark.parametrize("lengths,selected", [((49, 48), True), ((50, 48), False)])
def test_joined_limit_counts_the_separator(case, layer, lengths, selected):
    record = checked(case, layer, paired(case, lengths))
    assert all((d["outcome"] == "selected") == selected for d in record["decisions"])


def test_identical_scopes_are_deduplicated_before_counting(case, layer):
    out = paired(case, (90, 90))
    out["focuses"][1]["text"] = out["focuses"][0]["text"]
    assert all(d["outcome"] == "selected" for d in checked(case, layer, out)["decisions"])


def test_group_recovery_reaches_handoff_with_one_call_and_unchanged_conditions(case, layer):
    first, second = paired(case), paired(case, (12, 13))
    judge = ScopeJudge(first, second)
    state = G.run(QUESTION, layer, judge, K.Settings("fake"), "2026-10-29", research=True)
    assert len(judge.payloads) == 2 and state["record"]["route"]["action"] == "handoff"
    assert len(judge.payloads[1]["targets"]) == 2 and judge.payloads[1]["fixed"]["decided"] == []
    assert state["record"]["model_output"]["periods"] == first["periods"]
    assert state["record"]["model_output"]["frequencies"] == first["frequencies"]
    assert all(d["outcome"] == "selected" for d in state["record"]["decisions"])


@pytest.mark.parametrize("budget,calls,status", [(True, 2, "attempted_failed"), (False, 1, "budget_exhausted")])
def test_group_overflow_cannot_get_an_extra_recovery(case, layer, budget, calls, status):
    bad = paired(case)
    judge = ScopeJudge(bad, copy.deepcopy(bad))
    state = G.run(QUESTION, layer, judge, K.Settings("fake"), "2026-10-29", research=True,
                  research_budget=lambda: budget)
    assert len(judge.payloads) == calls
    for d in state["record"]["decisions"]:
        assert d["outcome"] == "failed"
        assert d["reasons"][0]["detail"]["recovery"]["status"] == status


def test_post_recovery_group_check_keeps_fixed_statistic_exactly(case, layer):
    first_output = paired(case, (10, 70))
    first_output["focuses"][0]["quotes"] = ["없는 인용"]
    first = checked(case, layer, first_output)
    second_output = output(case, sid="s9")
    second_output["focuses"][0]["text"] = "다" * 50
    second = checked(case, layer, second_output)
    before = copy.deepcopy(first)
    merged = G.merge_rounds(first, second, [{"statistic": "s1", "kind": "correction",
                                           "code": "focus_quote_not_in_question"}])
    assert first == before and merged["decisions"][1] == first["decisions"][1]
    target = merged["decisions"][0]
    assert target["outcome"] == "failed" and target["a_stage"] == "failed"
    assert target["reasons"][-1]["code"] == "focus_too_long"
    assert target["reasons"][-1]["detail"]["recovery"]["status"] == "attempted_failed"
    assert merged["rejudge"]["merged"][0]["outcome"] == "failed"
    assert merged["model_output"]["focuses"][0] == first_output["focuses"][1]


def decision(sid, text, *, table="T", frequency="M", start="2024-01", end="2024-12"):
    return {"statistic": sid, "quote": text, "outcome": "selected", "reasons": [],
            "focus": {"text": text}, "final_tables": [{"table_id": table, "frequency": frequency,
                "period": [{"start": start, "end": end}]}]}


@pytest.mark.parametrize("excluded", ["frequency", "period", "answer", "ask", "condition", "edition", "gap",
                                      "unverified", "aggregation", "cross_table_period"])
def test_groups_already_blocked_by_handoff_do_not_consume_recovery(excluded):
    a, b = decision("s1", "가" * 60), decision("s2", "나" * 60)
    rows = [a, b]
    if excluded == "frequency":
        b["final_tables"][0]["frequency"] = "Q"
    elif excluded == "period":
        b["final_tables"][0]["period"][0]["start"] = "2024-02"
    elif excluded == "answer":
        b["condition_answers"] = [{"option_id": "answer", "value": "2024년"}]
    elif excluded in ("ask", "condition"):
        b["asks" if excluded == "ask" else "condition_asks"] = [{"statistics": ["s2"]}]
    elif excluded == "edition":
        b["final_tables"].append(copy.deepcopy(b["final_tables"][0]))
    elif excluded == "gap":
        b["final_tables"][0]["period"].append({"start": "2025-01", "end": "2025-12"})
    elif excluded in ("unverified", "aggregation"):
        b["outcome"] = "selected_unverified" if excluded == "unverified" else "aggregation_needed"
    else:
        rows.append(decision("s3", "다", table="U", start="2023-01"))
    before = copy.deepcopy(rows)
    assert CH.check_groups(rows) == [] and rows == before


def test_a_conflicting_member_blocks_the_whole_table_group_not_a_matching_subgroup():
    rows = [decision("s1", "가" * 60), decision("s2", "나" * 60), decision("s3", "다", frequency="Q")]
    before = copy.deepcopy(rows)
    assert CH.check_groups(rows) == [] and rows == before


def test_different_calculations_do_not_exempt_a_sendable_group():
    a, b = decision("s1", "가" * 60), decision("s2", "나" * 60)
    a["calculations"], b["calculations"] = ["증감률"], ["차이"]
    assert len(CH.check_groups([a, b])) == 2
