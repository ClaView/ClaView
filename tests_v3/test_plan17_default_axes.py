"""Requested evidence outranks default-axis convenience; scope stays local."""
import copy

import pytest
from test_plan17_candidates import build_layer, itm, table

from claview_v3.interpret.pipeline import interpret_question
from claview_v3.select import execution
from claview_v3.select.graph import select_tables
from claview_v3.select.ranking import additional_target_axes, prefer_fewer_defaults
from claview_v3.select.tables import selection_catalog


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    return build_layer(tmp_path_factory.mktemp("default_axes"), [
        table("extra", "가상지표 지역별", [itm("I", "i", "가상지표", obj_name="BSI코드별"),
            itm("R", "all", "총계", obj_name="지역코드별"), itm("R", "s", "가상서울", obj_name="지역코드별")],
            units=["원"], periods=[("년", "2020", "2025")]),
        table("plain", "가상통계", [itm("I", "i", "가상지표", obj_name="BSI코드별")],
            units=["원"], periods=[("년", "2020", "2025")]),
    ])


def test_real_selection_prefers_fewer_defaults_without_changing_evidence(catalog, monkeypatch):
    a = interpret_question("가상지표 2023년 보여줘", catalog)
    before = copy.deepcopy(a)
    monkeypatch.setattr(execution, "_scores", lambda *args: {"extra": 99, "plain": 1})
    with monkeypatch.context() as m:
        m.setattr(execution, "prefer_fewer_defaults", lambda *args: None)
        old = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")["selections"][0]
    new = select_tables(a, catalog, "2026-10-21", selection_mode="refutation")["selections"][0]
    assert [t["table_id"] for t in old["tables"]] == ["extra"]
    assert [t["table_id"] for t in new["tables"]] == ["plain"]
    x, y = old["signals"]["plan17"], new["signals"]["plan17"]
    for key in ("candidate_ids", "alive_ids", "refutations", "scores"):
        assert x[key] == y[key]
    assert {tuple(r["tables"]): r["proof_status"] for r in x["alternatives"]} == {
        tuple(r["tables"]): r["proof_status"] for r in y["alternatives"]}
    assert a == before
    assert [r["order"] for r in y["alternatives"]] == sorted(r["order"] for r in y["alternatives"])


def analysis(text="가상지표"):
    return {"text": {"raw": text}, "conditions": [], "interpretation": {"requirements": [
        {"id": "r1", "span": [0, len(text)], "targets": [], "condition_ids": []}]}}


@pytest.mark.parametrize("word", ["지역코드별", "가상서울"])
def test_explicit_axis_or_value_needs_no_default(catalog, word):
    v = selection_catalog(catalog).views["extra"]
    assert additional_target_axes(analysis(f"가상지표 {word}"), "r1", v) == []


def test_other_requirement_words_do_not_set_this_axis(catalog):
    a = analysis("가상지표 그리고 가상서울 다른지표")
    a["interpretation"]["requirements"][0]["span"] = [0, 4]
    assert additional_target_axes(a, "r1", selection_catalog(catalog).views["extra"]) == [
        {"obj_id": "R", "name": "지역코드별"}]


def test_short_byeol_condition_uses_existing_axis_matching(catalog):
    a = interpret_question("가상지표 지역별 2023년 보여줘", catalog)
    rid = a["interpretation"]["requirements"][0]["id"]
    assert additional_target_axes(a, rid, selection_catalog(catalog).views["extra"]) == []


def test_model_alias_uses_own_candidate_axis_not_broad_span(catalog):
    a = analysis("가상지표 가상서울 다른요구")
    a["decomposition"] = {"applied_source": "model", "selection_refs": {"r1": [
        {"quote": "가상지표", "candidates": []}]}}
    v = selection_catalog(catalog).views["extra"]
    assert len(additional_target_axes(a, "r1", v)) == 1
    a["decomposition"]["selection_refs"]["r1"].append(
        {"quote": "이곳", "candidates": [{"table_id": "extra", "obj_id": "R"}]})
    assert additional_target_axes(a, "r1", v) == []


def rows():
    cells = {(None, "Y", "2023", ())}
    return [{"tables": [table_id], "proof_status": "possible", "cells": cells,
             "possible": cells, "supported": cells, "proven": set(), "conflicted": set(),
             "execution_gaps": [], "period_plan": {"context": {"anchor": None}},
             "order": [False, 1, -1, -1, 0, 1, -score, [n]]}
            for n, (table_id, score) in enumerate((("extra", 99), ("plain", 1)))]


def facts():
    return {t: {"indicator": {"check": {"available": True, "result": "match"}, "occurrences": [
        {"field": "value", "value_role": "indicator", "method": "exact"}]}} for t in ("extra", "plain")}


@pytest.mark.parametrize("difference", ["unit", "basis", "period", "support", "unexecutable", "unknown", "metadata"])
def test_convenience_never_overrides_different_evidence(catalog, difference):
    views = copy.deepcopy(selection_catalog(catalog).views)
    order = rows()
    if difference == "unit":
        views["plain"].table_units = ["달러"]
    elif difference == "basis":
        views["plain"].title = "가상통계 실질"
    elif difference == "period":
        order[1]["period_plan"]["context"]["anchor"] = "2022"
    elif difference == "support":
        order[1]["supported"] = set()
    elif difference == "unexecutable":
        order[1]["execution_gaps"] = ["table_unsuitable"]
    elif difference == "unknown":
        views["plain"].axis_roles["I"] = "unknown"
    else:
        views["plain"].has_meta = False
    prefer_fewer_defaults(analysis(), "r1", order, views, facts())
    assert [r["tables"] for r in order] == [["extra"], ["plain"]]


def test_a_single_provider_and_indicator_axes_are_not_penalized(catalog):
    views = selection_catalog(catalog).views
    order = rows()[:1]
    prefer_fewer_defaults(analysis(), "r1", order, views, facts())
    assert order[0]["tables"] == ["extra"] and not order[0]["default_axis_preference"]["comparable"]
    assert additional_target_axes(analysis(), "r1", views["plain"]) == []


def test_missing_model_structure_keeps_prior_order(catalog):
    a = analysis()
    a["decomposition"] = {"applied_source": "model", "selection_refs": {"r1": []}}
    order = rows()
    prefer_fewer_defaults(a, "r1", order, selection_catalog(catalog).views, facts())
    assert [r["tables"] for r in order] == [["extra"], ["plain"]]


@pytest.mark.parametrize("missing", ["partial_check", "partial_value", "target_role", "no_occurrence"])
def test_partial_indicator_cannot_win_by_having_fewer_axes(catalog, missing):
    ft = facts()
    ind = ft["plain"]["indicator"]
    if missing == "partial_check":
        ind["check"]["result"] = "partial"
    elif missing == "partial_value":
        ind["occurrences"][0]["method"] = "partial"
    elif missing == "target_role":
        ind["occurrences"][0]["value_role"] = "target"
    else:
        ind["occurrences"] = []
    order = rows()
    prefer_fewer_defaults(analysis(), "r1", order, selection_catalog(catalog).views, ft)
    assert [r["tables"] for r in order] == [["extra"], ["plain"]]
