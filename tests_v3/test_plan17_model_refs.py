"""Accepted semantic references survive discovery without becoming confirmed facts."""

import copy

import pytest

from claview_v3.select import candidates as C
from claview_v3.select.model_refs import qualifier_words, search_qualifiers
from test_plan17_candidates import analysis, layer


def model_analysis():
    a = analysis()
    raw = "가상지표 가상축별 가상기준"
    a["text"]["raw"] = raw
    a["fragments"] = [{"id": "g1", "span": [0, 4]}]
    r = a["interpretation"]["requirements"][0]
    r.update(quote=raw, span=[0, len(raw)], targets=[], indicator={
        "name": "가상지표", "quote": "가상지표", "span": [0, 4], "fragment_id": "g1", "path": "exact"},
        condition_ids=["B1", "B2"], shared_condition_ids=[], ambiguous_condition_ids=[])
    refs = [
        {"place": "indicator", "quote": "가상지표", "span": [0, 4], "name": "가상지표",
         "segment_ids": ["s1"], "candidates": [{"table_id": "T_A"}]},
        {"place": "breakdown_axis", "quote": "가상축별", "span": [5, 9], "name": None,
         "segment_ids": ["s2"], "candidates": []},
        {"place": "basis", "quote": "가상기준", "span": [10, 14], "name": None,
         "segment_ids": ["s3"], "candidates": []}]
    a["decomposition"] = {"applied_source": "model", "selection_refs": {"r1": refs}}
    a["conditions"] = [{"id": "B1", "kind": "basis", "span": [5, 9], "quote": "가상축별",
                        "attrs": {"axis": "classification", "value": "가상축별", "polarity": "+"}},
                       {"id": "B2", "kind": "basis", "span": [10, 14], "quote": "가상기준",
                        "attrs": {"axis": "classification", "value": "가상기준", "polarity": "+"}}]
    return a


def test_model_axis_and_basis_survive_condition_removal_as_one_soft_query():
    a = model_analysis()
    before = copy.deepcopy(a)
    assert [q["text"] for q in C.build_queries(a, "r1")] == ["가상지표", "가상지표 가상축별 가상기준"]
    assert a == before
    # Existing retry suggestions are not padded with another interpretation of the question.
    assert [q["text"] for q in C.build_queries(a, "r1", ["새검색어"])] == ["새검색어"]


@pytest.mark.parametrize("mode", ["negative", "excluded", "ambiguous", "other_requirement", "name_content",
                                 "negated_fragment"])
def test_nonpositive_or_unbound_conditions_do_not_become_search_preferences(mode):
    a = model_analysis()
    r, c = a["interpretation"]["requirements"][0], a["conditions"][0]
    if mode == "negative":
        c["attrs"]["polarity"] = "-"
    elif mode == "excluded":
        c["kind"] = "exclude"
    elif mode == "ambiguous":
        r["ambiguous_condition_ids"] = ["B1"]
    elif mode in ("other_requirement", "name_content"):
        r["condition_ids"].remove("B1")
    else:
        a["fragments"].append({"id": "g2", "span": [5, 9], "phrase": "negated"})
    assert qualifier_words(a, "r1") == ["가상기준"]


def test_negative_overlap_wins_over_a_simultaneous_positive_condition():
    a = model_analysis()
    negative = copy.deepcopy(a["conditions"][0])
    negative.update(id="B3")
    negative["attrs"]["polarity"] = "-"
    a["conditions"].append(negative)
    a["interpretation"]["requirements"][0]["condition_ids"].append("B3")
    assert qualifier_words(a, "r1") == ["가상기준"]


def test_shared_conditions_and_unmapped_raw_references_are_retained():
    a = model_analysis()
    r = a["interpretation"]["requirements"][0]
    r["condition_ids"].remove("B1")
    r["shared_condition_ids"] = ["B1"]
    assert qualifier_words(a, "r1") == ["가상축별", "가상기준"]
    a["conditions"] = []
    r["condition_ids"] = r["shared_condition_ids"] = []
    assert qualifier_words(a, "r1") == ["가상축별", "가상기준"]


def test_duplicate_roles_and_reference_order_do_not_repeat_or_reorder_words():
    a = model_analysis()
    refs = a["decomposition"]["selection_refs"]["r1"]
    duplicate = {**copy.deepcopy(refs[1]), "place": "basis"}
    refs[:] = [refs[2], duplicate, refs[0], refs[1]]
    assert qualifier_words(a, "r1") == ["가상축별", "가상기준"]
    assert len(search_qualifiers(a, "r1")) == 2


def test_lexical_alias_cannot_add_unrequested_meaning_to_search_text():
    a = model_analysis()
    ref = a["decomposition"]["selection_refs"]["r1"][2]
    ref.update(name="가상기준 전년대비", candidates=[{"table_id": "T_C", "obj_id": "B",
               "value_id": "v", "role": "basis", "path": "lexical"}])
    assert "전년대비" not in " ".join(q["text"] for q in C.build_queries(a, "r1"))


def test_rules_and_rejected_readings_do_not_use_model_qualifiers():
    a = model_analysis()
    a["decomposition"]["applied_source"] = "rules_fallback"
    queries = C.build_queries(a, "r1")
    del a["decomposition"]
    assert C.build_queries(a, "r1") == queries
    assert qualifier_words(a, "r1") == []


@pytest.mark.parametrize("place", ["basis", "breakdown_axis"])
def test_union_keeps_local_qualifier_candidates_beyond_search_caps(layer, monkeypatch, place):
    a = model_analysis()
    ref = a["decomposition"]["selection_refs"]["r1"][1]
    ref.update(place=place, name="가상축별", candidates=[{
        "table_id": "T_C", "obj_id": "G", "value_id": None if place == "breakdown_axis" else "c",
        "role": "breakdown" if place == "breakdown_axis" else "basis", "path": "exact"}])
    monkeypatch.setattr(C, "POOL_CAP", 1)
    info, before = {}, copy.deepcopy(a)
    pool = C.expand_candidates(a, "r1", {}, layer, info, candidate_policy="union")
    assert "T_C" in {p["table_id"] for p in pool}
    assert info["discovery"]["qualifiers"] == ["T_C"]
    assert "qualifier" in next(x["sources"] for x in info["discovery"]["origins"] if x["table_id"] == "T_C")
    assert ref in info["discovery"]["references"]
    assert a == before
    a["conditions"][0]["attrs"]["polarity"] = "-"
    negative = C.expand_candidates(a, "r1", {}, layer, {}, candidate_policy="union")
    assert "T_C" not in {p["table_id"] for p in negative}


def test_foreign_requirement_qualifier_candidates_do_not_leak(layer):
    a = model_analysis()
    a["decomposition"]["selection_refs"]["r2"] = [{"place": "basis", "name": "가상다른기준",
        "quote": "가상다른기준", "span": [0, 4], "candidates": [{"table_id": "T_C"}]}]
    assert "T_C" not in {p["table_id"] for p in C.expand_candidates(a, "r1", {}, layer, {}, candidate_policy="union")}


def split_request():
    cells = {(None, None, "latest", (cid,)) for cid in ("B1", "B2")}
    request = {"basis": [("B1",), ("B2",)], "cross": [],
               "proof": {"basis": ["B1", "B2"], "period": [], "frequency": [], "unit": [], "exclude": []}}
    return request, cells


def basis_check(cid, status):
    return {"name": "basis", "source_status": status,
            "verdict": {"match": "pass", "conflict": "fail", "unknown": "n/a"}[status],
            "detail": {"condition_id": cid}}


def test_basis_support_is_per_cell_and_unknown_does_not_count_as_supported():
    from claview_v3.select.cover import condition_supported_cells

    request, cells = split_request()
    before = copy.deepcopy(request)
    a = condition_supported_cells("A", [basis_check("B1", "match"), basis_check("B2", "conflict")], request, cells)
    b = condition_supported_cells("B", [basis_check("B1", "conflict"), basis_check("B2", "match")], request, cells)
    assert a == {(None, None, "latest", ("B1",))}
    assert b == {(None, None, "latest", ("B2",))}
    assert a | b == cells
    assert condition_supported_cells("weak", [basis_check("B1", "unknown"), basis_check("B2", "unknown")],
                                     request, cells) == set()
    assert request == before


def test_condition_support_order_retains_possible_singleton_and_full_bundle():
    from claview_v3.select.cover import order_alternatives

    _, cells = split_request()
    base = {"complete": True, "covered": cells, "best_score": 10, "score_sum": 10, "tier": 1}
    weak = {**base, "family": "first", "tables": ["weak"], "condition_supported": set(), "score_sum": 999}
    supported = {**base, "family": "second", "tables": ["A", "B"], "condition_supported": cells}
    before = copy.deepcopy([weak, supported])
    assert order_alternatives([weak, supported]) == [supported, weak]
    assert [weak, supported] == before
    # Old/rules alternatives without the new internal support record retain their order.
    legacy = [{k: v for k, v in a.items() if k != "condition_supported"} for a in (weak, supported)]
    assert order_alternatives(legacy) == legacy


def test_shared_constraint_failure_cannot_be_hidden_by_a_supported_split_value():
    from claview_v3.select.cover import condition_supported_cells

    request, cells = split_request()
    request["proof"]["basis"].append("B3")
    checks = [basis_check("B1", "match"), basis_check("B2", "match"), basis_check("B3", "unknown")]
    assert condition_supported_cells("T", checks, request, cells) == set()
