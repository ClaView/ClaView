"""Possible edition linking and positive support must not invent certainty."""
import copy

import pytest

from test_plan17_execution import catalog
from claview.a.select.allocation import rank_assignments
from claview.a.select.execution import _frame, _semantic_proven, _semantic_support
from claview.a.select.tables import selection_catalog
from test_plan17_candidates import analysis, build_layer, itm, table


def lexical_evidence():
    nd = {"evidence": {"indicator": {"name": "가상지표", "mapped": False, "settled": False},
                       "conditions": [], "targets": []},
          "certainty": {"all": True, "condition_ids": [], "fragment_ids": []}}
    fact = {"indicator": {"weak": True, "check": {"result": "match"},
             "occurrences": [{"field": "value", "method": "exact", "value_role": "indicator",
                              "obj_id": "A", "value_id": "1", "relation": "undetermined"}],
             "reference_candidates": [{"role": "indicator", "obj_id": "A", "value_id": "1"}]}}
    return nd, fact


def test_lexical_reference_to_exact_value_can_join_editions_without_proving_them(catalog):
    nd, fact = lexical_evidence()
    views = selection_catalog(catalog).views
    frames = {t: _frame(nd, fact, views[t], {"frequency": "M"}, {"basis": [()]}, "family")
              for t in ("old", "new")}
    assert frames["old"] is not None and frames["old"] == frames["new"]
    possible = {"old": {1, 2}, "new": {1, 2}}
    support = {"old": {1}, "new": {2}}
    empty = {t: set() for t in possible}
    result = rank_assignments({1, 2}, possible, empty, {}, frames, supported=support)
    joined = next(a for a in result["assignments"] if set(a["tables"]) == {"old", "new"})
    assert joined["proof_status"] == "possible" and joined["proven"] == set()
    assert not _semantic_proven((None, None, "latest", ()), nd, fact, True)


@pytest.mark.parametrize("mutation", ["partial", "wrong_value", "wrong_axis", "target", "target_occurrence",
                                      "unreferenced", "no_name", "no_frequency", "indicator_mismatch"])
def test_weak_hypothesis_needs_exact_referenced_indicator_row(catalog, mutation):
    nd, fact = lexical_evidence()
    record = fact["indicator"]
    result = {"frequency": "M"}
    if mutation == "partial":
        record["occurrences"][0]["method"] = "partial"
    elif mutation == "wrong_value":
        record["reference_candidates"][0]["value_id"] = "other"
    elif mutation == "wrong_axis":
        record["reference_candidates"][0]["obj_id"] = "other"
    elif mutation == "target":
        record["reference_candidates"][0]["role"] = "target"
    elif mutation == "target_occurrence":
        record["occurrences"][0]["value_role"] = "target"
    elif mutation == "unreferenced":
        record["reference_candidates"] = []
    elif mutation == "no_name":
        nd["evidence"]["indicator"]["name"] = None
    elif mutation == "no_frequency":
        result["frequency"] = None
    else:
        record["check"]["result"] = "none"
    assert _frame(nd, fact, selection_catalog(catalog).views["old"], result,
                  {"basis": [()]}, "family") is None


def support_request():
    return {"basis": [()], "cross": [], "proof": {k: [] for k in
            ("period", "frequency", "basis", "unit", "exclude")}}


def indicator_check(result):
    return {"name": "indicator", "detail": {"available": True, "result": result}}


def test_target_discovery_does_not_override_missing_indicator_evidence():
    candidate = {"table_id": "t", "entered_by": "target"}
    cell = ("target", None, "latest", ())
    fact = {"targets": [{"name": "target", "present": True}]}
    assert _semantic_support(candidate, [indicator_check("none")], fact, support_request(), {cell}) == set()
    assert _semantic_support(candidate, [indicator_check("match")], fact, support_request(), {cell}) == {cell}


def test_support_requires_the_target_of_each_cell_without_removing_possible_cells():
    candidate = {"table_id": "t", "entered_by": "term"}
    cells = {(name, None, "latest", ()) for name in ("a", "b")}
    fact = {"targets": [{"name": "a", "present": True}, {"name": "b", "present": False}]}
    before = copy.deepcopy(cells)
    assert _semantic_support(candidate, [indicator_check("match")], fact, support_request(), cells) == {
        ("a", None, "latest", ())}
    assert cells == before


def test_target_only_request_keeps_its_positive_target_support():
    candidate = {"table_id": "t", "entered_by": "target"}
    cell = ("a", None, "latest", ())
    fact = {"targets": [{"name": "a", "present": True}]}
    checks = [{"name": "indicator", "detail": {"available": False, "result": None}}]
    assert _semantic_support(candidate, checks, fact, support_request(), {cell}) == {cell}


def test_catalog_abbreviation_adds_exact_candidates_without_changing_the_reading(tmp_path):
    from claview.a.select.candidates import catalog_alias_candidates, expand_candidates
    layer = build_layer(tmp_path, [
        table("source", "가상총생산(ABC)", [itm("I", "i", "가상총생산(ABC)", obj_name="BSI코드별")]),
        table("long", "가상보고서", [itm("I", "i", "가상총생산", obj_name="BSI코드별")]),
        table("partial", "가상비율", [itm("I", "i", "가상총생산증가율", obj_name="BSI코드별")])])
    a = analysis()
    a["interpretation"]["requirements"][0]["indicator"].update(name="ABC", quote="ABC")
    a["terms"] = []
    before = copy.deepcopy(a)
    aliases = catalog_alias_candidates(a, "r1", layer)
    assert "long" in {r["table_id"] for r in aliases}
    assert "partial" not in {r["table_id"] for r in aliases}
    info = {}
    got = expand_candidates(a, "r1", {}, layer, info, candidate_policy="union")
    assert "long" in {p["table_id"] for p in got}
    assert info["discovery"]["catalog_aliases"] == aliases
    assert a == before
    legacy = expand_candidates(a, "r1", {}, layer, {}, candidate_policy="legacy")
    assert "long" not in {p["table_id"] for p in legacy}
    a["interpretation"]["requirements"][0]["indicator"].update(name="ABCD", quote="ABCD")
    assert catalog_alias_candidates(a, "r1", layer) == []
