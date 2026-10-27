"""Execution must retain candidates without invoking the four legacy deletion paths."""
import copy

import pytest

from test_plan17_candidates import build_layer, itm, table
from claview_a.contract.check import check_selections
from claview_a.interpret.pipeline import interpret_question
from claview_a.select.graph import select_tables
from claview_a.select.cover import verify_selection


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    return build_layer(tmp_path_factory.mktemp("plan17_execution"), [
        table("old", "가상지표(첫판)", [itm("A", "1", "가상지표", obj_name="BSI코드별")],
              units=["원"], periods=[("월", "2020.01", "2020.12")]),
        table("new", "가상지표(다음판)", [itm("A", "1", "가상지표", obj_name="BSI코드별")],
              units=["원"], periods=[("월", "2021.01", "2021.12")]),
        table("weak", "가상지표 관련 자료", [itm("A", "2", "부가지표", obj_name="BSI코드별")],
              units=["원"], periods=[("월", "2020.01", "2021.12")]),
    ])


def test_execution_bypasses_legacy_deletions_and_preserves_analysis(catalog, monkeypatch):
    from claview_a.select import graph
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy deletion path used")
    monkeypatch.setattr(graph, "cover_cells_fn", forbidden)
    monkeypatch.setattr(graph, "choose_table_fn", forbidden)
    monkeypatch.setattr(graph, "apply_bundles", forbidden)
    a = interpret_question("가상지표 2020년부터 2021년까지 보여줘", catalog)
    before = copy.deepcopy(a)
    got = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")
    assert a == before
    assert check_selections(got["selections"]) == []
    for s in got["selections"]:
        record = s["signals"]["plan17"]
        assert set(record["candidate_ids"]) == set(record["alive_ids"]) | set(record["refutations"]["tables"])
        assert set(record["alive_ids"]) == {t for alt in record["alternatives"] for t in alt["tables"]}
        current = s["signals"]["decision_preview"]["decision"]["today"]
        assert current["tables"] == [t["table_id"] for t in s["tables"]]
        if s["tables"]:
            assert verify_selection(a, s, catalog, "2026-10-21")["recheck"] is not None


def test_closed_request_can_keep_two_editions_without_collapsing_to_head(catalog):
    from claview_a.select.refute import is_certificate
    a = interpret_question("가상지표 2020년부터 2021년까지 보여줘", catalog)
    got = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")
    s = got["selections"][0]
    record = s["signals"]["plan17"]
    joined = next(alt for alt in record["alternatives"] if set(alt["tables"]) == {"old", "new"})
    assert joined["missing"] == [] and joined["execution_gaps"] == []
    assert set(joined["bundle"]["assignments"]) == {"old", "new"}
    assert all(len(v) == 1 for v in joined["bundle"]["assignments"].values())
    certificates = [c for rows in joined["period_exclusions"].values() for c in rows.values()]
    assert certificates and all(is_certificate(c) for c in certificates)
    assert all(c["opposite_fact"]["resolver_identity"]["contract"] == "period-contract/2026-10-21.2"
               for c in certificates)


def test_default_and_explicit_legacy_are_identical(catalog):
    a = interpret_question("가상지표 보여줘", catalog)
    assert select_tables(a, catalog, "2026-10-21") == select_tables(a, catalog, "2026-10-21", selection_mode="legacy")


def test_bad_mode_rejected(catalog):
    with pytest.raises(ValueError, match="selection mode"):
        select_tables({}, catalog, selection_mode="unknown")


def test_unresolved_model_structure_never_becomes_proven_or_reads_global_negation(catalog, monkeypatch):
    from claview_a.select import execution
    a = interpret_question("가상지표 보여줘", catalog)
    monkeypatch.setattr(execution, "adopted", lambda _: True)
    a["judgement"]["negated"] = ["가상지표"]
    s = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")["selections"][0]
    record = s["signals"]["plan17"]
    assert "model_structure_unrecorded" in record["gaps"]
    assert not s["signals"]["decision_preview"]["needs"]["filter"]["negated_settled"]
    assert all(alt["proof_status"] != "proven" for alt in record["alternatives"])
    assert record["b_handoff_ready"] is False


def test_new_structure_is_not_silently_treated_as_legacy(catalog, monkeypatch):
    from claview_a.select import execution
    a = interpret_question("가상지표 보여줘", catalog)
    a["decomposition"] = {"structure": {"version": "unagreed"}}
    monkeypatch.setattr(execution, "adopted", lambda _: True)
    with pytest.raises(ValueError, match="agreed contract"):
        select_tables(a, catalog, "2026-10-21", selection_mode="refutation")


def test_reverification_never_raises_current_status(catalog):
    a = interpret_question("가상지표 보여줘", catalog)
    s = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")["selections"][0]
    s["status"] = "confirm"
    assert verify_selection(a, s, catalog, "2026-10-21")["state"] != "auto"


def test_unsupported_frequency_is_not_a_latest_cell(catalog):
    a = interpret_question("가상지표 연간으로 보여줘", catalog)
    got = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")
    assert all(s["status"] == "no_table" for s in got["selections"])


def test_missing_metadata_stays_possible_with_execution_gap(catalog, monkeypatch):
    from claview_a.select.tables import selection_catalog
    v = selection_catalog(catalog).views["old"]
    monkeypatch.setattr(v, "periods", [])
    a = interpret_question("가상지표 보여줘", catalog)
    s = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")["selections"][0]
    row = next(x for x in s["signals"]["plan17"]["alternatives"] if x["tables"] == ["old"])
    assert row["possible"] and row["execution_gaps"] and row["proof_status"] != "proven"


def test_outer_graph_uses_the_same_new_selection_and_v2(catalog):
    from claview_a.agent.graph import run_outer
    result = run_outer("가상지표 보여줘", "router", layer=catalog, as_of="2026-10-21", selection_mode="refutation")
    assert result["selections"]
    assert all(s["signals"]["plan17"]["mode"] == "refutation" for s in result["selections"])


def test_support_cover_is_enumerated_without_becoming_proof():
    from claview_a.select.allocation import rank_assignments
    possible = {"weak": {1, 2}, "A": {1, 2}, "B": {1, 2}}
    empty = {t: set() for t in possible}
    got = rank_assignments({1, 2}, possible, empty, {}, dict.fromkeys(possible, "comparison"),
                           supported={"weak": set(), "A": {1}, "B": {2}})
    combined = next(a for a in got["assignments"] if set(a["tables"]) == {"A", "B"})
    assert combined["proof_status"] == "possible" and combined["proven"] == set()
    assert {t for a in got["assignments"] for t in a["tables"]} == set(possible)


@pytest.mark.parametrize("axes_known", [True, False])
def test_cross_axis_support_orders_without_removing_single_axis(tmp_path, monkeypatch, axes_known):
    from claview_a.select import execution
    layer = build_layer(tmp_path, [
        table("single", "가상측정", [itm("I", "i", "가상측정", obj_name="BSI코드별"),
              itm("G", "age", "연령", obj_name="분류별"), itm("G", "region", "지역", obj_name="분류별")],
              units=["원"], periods=[("년", "2000", "2025")]),
        table("cross", "가상측정(연령별 지역별)", [itm("I", "i", "가상측정", obj_name="BSI코드별"),
              *([itm("G", "a", "가상연령", obj_name="연령별"), itm("H", "b", "가상지역", obj_name="지역별")]
                if axes_known else [itm("G", "a", "가상값", obj_name="분류별")])],
              units=["원"], periods=[("년", "2000", "2025")]),
    ])
    a = interpret_question("가상측정 연령별 지역별 보여줘", layer)
    monkeypatch.setattr(execution, "adopted", lambda _: True)
    monkeypatch.setattr(execution, "_scores", lambda *args: {"single": 999, "cross": 1})
    s = select_tables(a, layer, "2026-10-21", selection_mode="refutation")["selections"][0]
    record = s["signals"]["plan17"]
    assert [t["table_id"] for t in s["tables"]] == ["cross"]
    assert {"single", "cross"} <= set(record["alive_ids"])
    assert all(row["proof_status"] != "proven" for row in record["alternatives"])
    assert next(row for row in record["alternatives"] if row["tables"] == ["single"])["supported"] == []
    winner = record["chosen"]
    assert bool(winner["supported"]) == axes_known
    assert winner["conflicted"] == []
    assert next(row for row in record["alternatives"] if row["tables"] == ["single"])["conflicted"]


def test_unknown_period_never_counts_as_positive_support():
    from claview_a.select.execution import _supported
    from claview_a.select.cover import LATEST
    cell = (None, None, LATEST, ())
    req = {"targets": [None], "frequencies": [None], "periods": [(LATEST, None, None)], "basis": [()]}
    result = {"T": {"status": "unresolved", "frequency": None}}
    assert _supported(req, result, {"T": {cell}}) == {"T": set()}


def test_qualified_matches_can_propose_a_frame_without_becoming_proof(catalog):
    from claview_a.select.execution import _frame, _semantic_proven
    from claview_a.select.tables import selection_catalog
    view = selection_catalog(catalog).views["old"]
    nd = {"evidence": {"indicator": {"name": "가상지표", "mapped": True, "settled": True},
                       "conditions": [], "targets": []},
          "certainty": {"all": False, "condition_ids": [], "fragment_ids": []}}
    fact = {"indicator": {"weak": True, "check": {"result": "match"}, "occurrences": []}}
    assert _frame(nd, fact, view, {"frequency": "M"}, {"basis": [()]}, "family") is not None
    assert not _semantic_proven((None, None, "latest", ()), nd, fact, False)
    fact["indicator"]["check"]["result"] = "partial"
    assert _frame(nd, fact, view, {"frequency": "M"}, {"basis": [()]}, "family") is None


@pytest.mark.parametrize("cross,proven", [("single_axis", False), ("unknown", False), ("supported", True)])
def test_rule_proof_requires_independent_cross_axes(cross, proven):
    from claview_a.select.execution import _semantic_proven
    nd = {"evidence": {"indicator": {"mapped": True, "settled": True}, "targets": [],
                       "conditions": [{"settled": True}, {"settled": True}]},
          "certainty": {"all": False, "condition_ids": [], "fragment_ids": []}}
    fact = {"indicator": {"weak": False}, "cross": {"support": cross}, "cells": {"combinable": "supported"}}
    assert _semantic_proven((None, None, "latest", ()), nd, fact, False) is proven


def test_new_period_path_does_not_rank_by_old_period_checks(catalog, monkeypatch):
    from claview_a.select import execution
    original = execution._scores
    seen = []
    def capture(analysis, rid, pool, checks, *args):
        seen.extend(c["name"] for rows in checks.values() for c in rows)
        return original(analysis, rid, pool, checks, *args)
    monkeypatch.setattr(execution, "_scores", capture)
    a = interpret_question("가상지표 2020년부터 2021년까지 월별로 보여줘", catalog)
    select_tables(a, catalog, "2026-10-21", selection_mode="refutation")
    assert seen and not {"period", "frequency"} & set(seen)
