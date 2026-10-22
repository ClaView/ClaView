"""Group-aware selection support; no model or network. Raw row IDs are synthetic."""
import copy

import pytest

from test_plan17_candidates import build_layer, itm, table
from claview.a.select import structure
from claview.a.select.cover import all_cells, covers_of


def ref(name, obj, value=None, tables=("both",), place="breakdown_value"):
    return {"place": place, "segment_ids": [name], "span": [0, len(name)], "quote": name, "name": name,
            "candidates": [{"table_id": t, "obj_id": obj, "value_id": value, "value_name": name,
                            "path": "exact", "role": "breakdown" if value is None else "target",
                            "type": "axis_name" if value is None else "item_row"} for t in tables]}


def group(gid, values=(), axis=None, kind="values"):
    return {"group_id": gid, "kind": kind, "axis": axis, "values": list(values), "markers": [],
            "descendants": {"status": "unexpanded", "source_refs": list(values)} if kind == "descendants" else None}


def scoped(groups=(), targets=()):
    return {"state": "valid", "version": "structure-2026-10-21.1", "polarity": [], "confirmed_effects": [],
            "requirement": {"targets": list(targets), "breakdowns": list(groups), "basis": [], "context": None}}


def project(value):
    return structure.project({"targets": [None], "frequencies": [None], "periods": [("latest", None, None)],
                              "basis": [()], "constraints": [], "cross": [], "proof": {}, "flags": []}, value)


@pytest.fixture
def catalog(tmp_path):
    rows = [itm("I", "i", "가상지표", obj_name="BSI코드별"),
            itm("R", "a", "가상지역가", obj_name="지역별"), itm("R", "b", "가상지역나", obj_name="지역별"),
            itm("S", "c", "가상업종가", obj_name="산업별"), itm("S", "d", "가상업종나", obj_name="산업별"),
            itm("F", "f", "가상필터", obj_name="구분별")]
    return build_layer(tmp_path, [table("both", "가상지표", rows),
        table("region", "가상지표 지역별", rows[:3]), table("sector", "가상지표 산업별", [rows[0], *rows[3:5]])])


def test_filters_and_groups_form_joint_cells_without_flattening(catalog):
    value = scoped([group("b1", [ref("가상지역가", "R", "a"), ref("가상지역나", "R", "b")]),
                    group("b2", [ref("가상업종가", "S", "c"), ref("가상업종나", "S", "d")])],
                   [ref("가상필터", "F", "f", place="target")])
    before = copy.deepcopy(value)
    got = project(value)
    assert len(all_cells(got)) == 4
    assert all(len(keys) == 3 and "filter:1" in keys for keys in got["structure_cells"].values())
    assert structure.supported_targets(got, "both", catalog, [ref("가상지표", "I", "i")]) == set(got["targets"])
    assert value == before
    assert structure.covers(all_cells(got), got, covers_of)["targets"] == sorted(
        ["가상필터", "가상지역가", "가상지역나", "가상업종가", "가상업종나"])


def test_same_axis_filters_are_a_set_different_axes_are_joint():
    got = project(scoped(targets=[ref("a", "R", "a"), ref("b", "R", "b"), ref("c", "S", "c")]))
    assert len(got["targets"]) == 2
    assert all(len(keys) == 2 for keys in got["structure_cells"].values())


def test_two_tables_with_separate_axes_do_not_support_crossed_observations(catalog):
    value = scoped([group("b1", axis=ref("지역별", "R", tables=("both", "region")), kind="axis"),
                    group("b2", axis=ref("산업별", "S", tables=("both", "sector")), kind="axis")])
    got = project(value)
    indicator = ref("가상지표", "I", "i", tables=("both", "region", "sector"))
    assert structure.supported_targets(got, "both", catalog, [indicator]) == set(got["targets"])
    assert structure.supported_targets(got, "region", catalog, [indicator]) == set()
    assert structure.supported_targets(got, "sector", catalog, [indicator]) == set()


def test_same_axis_sibling_filters_cannot_be_joined_to_wrong_indicator(catalog):
    got = project(scoped([group("b1", [ref("가상지역나", "R", "b")])]))
    assert structure.supported_targets(got, "both", catalog, [ref("가상지역가", "R", "a")]) == set()


def test_values_must_belong_to_their_named_axis(catalog):
    got = project(scoped([group("b1", [ref("가상업종가", "S", "c")], ref("지역별", "R"))]))
    assert structure.supported_targets(got, "both", catalog, [ref("가상지표", "I", "i")]) == set()


def test_descendants_remain_unexpanded_and_cannot_claim_support(catalog):
    got = project(scoped([group("b1", [ref("가상지역가", "R", "a")], kind="descendants")]))
    assert got["structure_gaps"] == ["descendants_unexpanded"]
    assert structure.supported_targets(got, "both", catalog, [ref("가상지표", "I", "i")]) == set()


def test_combination_limit_is_an_explicit_gap_not_a_truncated_success():
    got = project(scoped([group(f"b{n}", [ref(str(v), str(n), str(v)) for v in range(5)]) for n in range(4)]))
    assert "structure_cell_limit" in got["structure_gaps"]
    assert len(got["structure"]["requirement"]["breakdowns"]) == 4


def test_unknown_filter_axis_stays_a_gap():
    a = ref("unknown", None, "a")
    a["candidates"] = []
    got = project(scoped(targets=[a, ref("b", "R", "b")]))
    assert "filter_axis_unresolved" in got["structure_gaps"]


def test_one_values_group_cannot_mix_two_axes(catalog):
    got = project(scoped([group("b1", [ref("가상지역가", "R", "a"), ref("가상업종가", "S", "c")])]))
    assert structure.supported_targets(got, "both", catalog, [ref("가상지표", "I", "i")]) == set()


def title_ref(name, tables=("both",), path="exact", place="indicator"):
    value = ref(name, None, tables=tables, place=place)
    for row in value["candidates"]:
        row.update(role="table_name", type="table_name", path=path)
    return value


def test_table_name_indicator_is_supported_but_weak_mapping_is_not_proof(catalog):
    got = project(scoped())
    exact = title_ref("가상지표")
    assert structure.supported_targets(got, "both", catalog, [exact], strong=True) == set(got["targets"])
    weak = title_ref("가상지표", path="lexical")
    assert structure.supported_targets(got, "both", catalog, [weak]) == set(got["targets"])
    assert structure.supported_targets(got, "both", catalog, [weak], strong=True) == set()
    assert structure.supported_targets(got, "region", catalog, [exact]) == set()


def test_basis_and_context_without_conditions_are_required(catalog):
    value = scoped()
    value["requirement"]["basis"] = [ref("가상필터", "F", "f", place="basis")]
    value["requirement"]["context"] = title_ref("가상지표", place="context")
    got = project(value)
    indicator = ref("가상지표", "I", "i", tables=("both", "region"))
    assert structure.supported_targets(got, "both", catalog, [indicator]) == set(got["targets"])
    assert structure.supported_targets(got, "region", catalog, [indicator]) == set()
    value["requirement"]["context"]["candidates"] = []
    assert structure.supported_targets(project(value), "both", catalog, [indicator]) == set()


def test_ambiguous_axis_cannot_borrow_another_axis_to_make_cross_support(catalog):
    axis = ref("분류별", "R")
    axis["candidates"] += ref("분류별", "S")["candidates"]
    got = project(scoped([group("b1", [ref("가상업종가", "S", "c")], axis),
                          group("b2", axis=ref("산업별", "S"), kind="axis")]))
    assert structure.supported_targets(got, "both", catalog, [ref("가상지표", "I", "i")]) == set()
