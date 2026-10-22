"""Requirement-local projection and rejected-model isolation."""

import copy

from claview.a.select.input import selection_input

from test_plan17_candidates import analysis, layer  # shared synthetic catalog fixture


def test_rule_fallback_never_reads_rejected_model_candidates(layer):
    a = analysis()
    expected = selection_input(a, "r1", layer)
    a["decomposition"] = {"applied_source": "rules", "reading_id": "rejected",
                          "selection_refs": {"r1": [{"candidates": [{"table_id": "wrong"}]}]},
                          "assumption_seeds": [{"requirement_keys": []}]}
    assert selection_input(a, "r1", layer) == expected


def test_input_is_detached_and_key_includes_condition_meaning(layer):
    a = analysis()
    a["conditions"] = [{"id": "p1", "kind": "period", "value": "2020", "attrs": {
        "form": "point", "start_expr": {"year": "2020"}}}]
    a["interpretation"]["requirements"][0]["condition_ids"] = ["p1"]
    before = copy.deepcopy(a)
    first = selection_input(a, "r1", layer)
    first["conditions"][0]["value"] = "changed"
    assert a == before
    a["conditions"][0]["attrs"]["start_expr"]["year"] = "2021"
    assert selection_input(a, "r1", layer)["requirement_key"] != first["requirement_key"]


def test_requirement_order_is_not_semantic_identity(layer):
    a = analysis()
    first = selection_input(a, "r1", layer)
    other = copy.deepcopy(a["interpretation"]["requirements"][0])
    other["id"] = "r2"
    other["targets"] = []
    a["interpretation"]["requirements"].insert(0, other)
    assert selection_input(a, "r1", layer)["requirement_key"] == first["requirement_key"]


def test_shared_by_a_subset_does_not_become_a_question_wide_binding(layer):
    a = analysis()
    a["conditions"] = [{"id": "f1", "kind": "frequency", "value": "M", "attrs": {}}]
    first = a["interpretation"]["requirements"][0]
    first["shared_condition_ids"] = ["f1"]
    other = copy.deepcopy(first)
    other["id"] = "r2"
    third = copy.deepcopy(first)
    third.update(id="r3", shared_condition_ids=[])
    a["interpretation"]["requirements"] += [other, third]
    [binding] = selection_input(a, "r1", layer)["condition_bindings"]
    assert binding["scope"] == "requirements" and binding["requirement_ids"] == ["r1", "r2"]
