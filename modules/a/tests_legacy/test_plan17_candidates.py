"""Direct and per-value candidates survive score caps; search cuts stay visible."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table

from claview_a_legacy.select import candidates as C
from claview_a_legacy.select.facts import pool_cut


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    return build_layer(tmp_path_factory.mktemp("plan17_candidates"), [
        table("T_A", "가상지표(첫판)", [itm("A", "1", "가상지표", obj_name="BSI코드별"),
                                      itm("G", "a", "가상서울", obj_name="지역코드별")]),
        table("T_B", "가상지표(다음판)", [itm("A", "1", "가상지표", obj_name="BSI코드별"),
                                        itm("G", "b", "가상부산", obj_name="지역코드별")]),
        table("T_C", "가상별도표", [itm("A", "1", "가상지표", obj_name="BSI코드별")]),
    ])


def analysis():
    return {"text": {"raw": "가상서울 가상부산 가상지표"}, "conditions": [],
            "fragments": [{"id": "g1", "span": [10, 14]}],
            "terms": [{"fragment_id": "g1", "name": "가상지표", "type": "indicator", "path": "exact",
                       "table_ids": ["T_A", "T_B", "T_C"]}],
            "judgement": {"failed_conditions": []},
            "interpretation": {"requirements": [{"id": "r1", "quote": "가상서울 가상부산 가상지표",
                "span": [0, 14], "indicator": {"name": "가상지표", "fragment_id": "g1"},
                "targets": [{"name": "가상서울"}, {"name": "가상부산"}],
                "condition_ids": [], "shared_condition_ids": [], "ambiguous_condition_ids": []}]}}


def test_direct_candidates_are_not_lost_to_the_term_score_cap(layer, monkeypatch):
    monkeypatch.setattr(C, "TERM_TABLE_CAP", 1)
    a, info = analysis(), {}
    before = copy.deepcopy(a)
    pool = C.expand_candidates(a, "r1", {}, layer, info, candidate_policy="union")
    assert {p["table_id"] for p in pool} == {"T_A", "T_B", "T_C"}
    assert info["term_tables_restored"] == ["T_B", "T_C"]
    assert not info["term_tables_truncated"]
    assert info["discovery"]["direct_cut"] == []
    assert pool_cut(a, "r1", {}, pool, layer, info)["cut"] == 0
    assert a == before


def test_values_are_a_union_and_do_not_require_one_table_to_have_every_value(layer):
    a = analysis()
    a["terms"] = []
    assert C.target_tables(a, "r1", layer) == []  # legacy intersection helper
    assert set(C.target_tables(a, "r1", layer, union=True)) == {"T_A", "T_B"}
    info = {}
    pool = C.expand_candidates(a, "r1", {}, layer, info, candidate_policy="union")
    assert {"T_A", "T_B"} <= {p["table_id"] for p in pool}
    assert all("target" in r["sources"] for r in info["discovery"]["origins"]
               if r["table_id"] in ("T_A", "T_B"))


def test_target_only_questions_keep_the_union_beyond_the_search_cap(layer, monkeypatch):
    a = analysis()
    a["judgement"]["failed_conditions"] = ["no_indicator"]
    a["interpretation"]["requirements"][0]["indicator"] = None
    a["terms"] = []
    monkeypatch.setattr(C, "TARGET_TABLE_CAP", 1)
    info = {}
    pool = C.expand_candidates(a, "r1", {}, layer, info, candidate_policy="union")
    assert {"T_A", "T_B"} <= {p["table_id"] for p in pool}
    assert info["target_search"]["tables"] == 2 and not info["target_search"]["truncated"]


def test_model_path_keeps_only_accepted_local_candidate_references(layer):
    a = analysis()
    a["decomposition"] = {"applied_source": "model", "selection_refs": {"r1": [
        {"place": "indicator", "name": "가상지표", "candidates": [{"table_id": "T_A"}]},
        {"place": "target", "name": "가상미대응", "candidates": [
            {"table_id": "T_C", "obj_id": "G", "item_id": "c", "role": "target", "path": "lexical"}]},
    ]}}
    info = {}
    pool = C.expand_candidates(a, "r1", {}, layer, info, candidate_policy="union")
    assert "T_C" in {p["table_id"] for p in pool}
    assert info["discovery"]["references"] == a["decomposition"]["selection_refs"]["r1"]
    assert "T_C" in info["discovery"]["values"]
    info["discovery"]["references"][0]["candidates"].clear()
    assert a["decomposition"]["selection_refs"]["r1"][0]["candidates"] == [{"table_id": "T_A"}]


def test_default_keeps_the_legacy_candidate_policy(layer):
    a = analysis()
    a["terms"] = []
    info = {}
    assert C.expand_candidates(a, "r1", {}, layer, info) == []
    assert "discovery" not in info
    assert C.expand_candidates(a, "r1", {}, layer, {}, candidate_policy="union")


def test_union_setting_reaches_outer_graph_and_reading_comparisons(layer, monkeypatch):
    from claview_a_legacy.agent.graph import run_outer
    from claview_a_legacy.select.readings import compare_readings

    original, calls = C.expand_candidates, []

    def capture(*args, **kwargs):
        calls.append(kwargs.get("candidate_policy", "legacy"))
        return original(*args, **kwargs)

    monkeypatch.setattr(C, "expand_candidates", capture)
    state = run_outer("가상지표 보여줘", "straight", layer=layer, candidate_policy="union")
    assert calls and set(calls) == {"union"}
    assert all("candidate_discovery" in s["signals"] for s in state["selections"])
    candidate = {"point": "synthetic", "option": "other", "kind": "reading", "effect": "reading",
                 "text": "same meaning", "analysis": state["analysis"]}
    calls.clear()
    compare_readings(state["analysis"], state["selections"], layer, candidates=[candidate], candidate_policy="union")
    assert calls and set(calls) == {"union"}
