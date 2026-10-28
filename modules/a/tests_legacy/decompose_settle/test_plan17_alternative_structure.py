"""F5: switching readings preserves the alternative's own structure and scopes.

The fake proposals pass the integrated interpreter/validator/structure producer.
No other checkout is imported by these tests.
"""

import copy

import pytest

from test_plan15_settle import R, bd, bind, ind, layer, proposal, reading, req, run
from claview_a_legacy.contract.check import check_analysis, scope_index
from claview_a_legacy.select import readings
from claview_a_legacy.select.evidence import scope
from claview_a_legacy.select.input import selection_input


QUESTION = "가상서울 말고 가상부산 가상대출금 가상예금액 최근 3개월 분기별로 보여줘"
READING_FIELDS = ("reading_id", "requirement_ids", "bindings", "assumption_seeds", "condition_fields",
                  "inferred", "rule_checks", "selection_refs", "certainty")
ANALYSIS_FIELDS = ("fragments", "terms", "judgement", "interpretation", "ref_map")


def _proposals(p):
    seoul = R(p.s("가상서울"), z=p.z("가상서울", "target"))
    busan = R(p.s("가상부산"), z=p.z("가상부산", "target"))

    def one(alternative):
        owner = "r2" if alternative else "r1"
        reqs = [req("r1", ind(p, "가상대출금"), targets=[] if alternative else [busan]),
                req("r2", ind(p, "가상예금액"), breakdowns=[bd([busan])] if alternative else [])]
        bindings = [bind(c["id"], "requirements", ["r1" if alternative else "r2"])
                    if c["kind"] in ("frequency", "period") else bind(c["id"])
                    for c in p.payload["conditions"]]
        link = {"kind": "replace", "marker_refs": [R(p.s("말고"))], "negative_refs": [seoul],
                "positive_refs": [busan], "requirement_ids": [owner]}
        return reading(reqs, bindings, links=[link], request=[R(p.s("보여줘"))])

    return proposal(one(False), alternative=one(True))


def _raw_ref(analysis, quote, place):
    segment = next(s for s in analysis["segments"] if s["quote"] == quote)
    candidates = [copy.deepcopy(c) for m in analysis["mentions"]
                  if m["kind"] == "name" and m["segment_ids"] == [segment["segment_id"]]
                  for c in m["candidates"] if c["value_name"] == quote and c["role"] == "target"]
    return {"place": place, "segment_ids": [segment["segment_id"]], "span": list(segment["span"]),
            "quote": quote, "name": quote if candidates else None, "candidates": candidates}


def _structure(analysis, record, alternative):
    # Two readings share settled IDs, but one filters r1 and the other splits r2.
    refs = record["selection_refs"]
    owner = "r2" if alternative else "r1"
    value = copy.deepcopy(next(r for r in refs[owner] if r["place"] != "indicator"))
    requirements = {rid: {"targets": [], "breakdowns": [], "basis": [], "context": None} for rid in refs}
    if alternative:
        requirements[owner]["breakdowns"] = [{"group_id": "b1", "kind": "values", "axis": None,
                                               "values": [value], "markers": [], "descendants": None}]
    else:
        requirements[owner]["targets"] = [value]
    positive = {**copy.deepcopy(value), "place": "polarity_positive"}
    return {"version": "structure-2026-10-21.1", "requirements": requirements,
            "polarity": [{"link_id": "p1", "kind": "replace",
                          "markers": [_raw_ref(analysis, "말고", "polarity_marker")],
                          "negative": [_raw_ref(analysis, "가상서울", "polarity_negative")],
                          "positive": [positive], "requirement_ids": [owner], "scope_status": "clear"}],
            "confirmed_effects": []}


@pytest.fixture
def analysis(layer):
    a = run(QUESTION, layer, _proposals)
    d = a["decomposition"]
    assert d["applied_source"] == "model" and d["alternative"] is not None
    assert check_analysis(a) == []
    return a


def _check_legacy_contract(analysis):
    assert check_analysis(analysis) == []


def _other(a, layer):
    (candidate,) = readings.reading_candidates(a, layer)
    assert candidate["option"] == "alternative"
    return candidate["analysis"]


def test_same_requirement_ids_switch_structure_conditions_refs_and_failures_together(analysis, layer):
    before = copy.deepcopy(analysis)
    primary = analysis["decomposition"]
    expected = primary["alternative"]
    other = _other(analysis, layer)
    switched = other["decomposition"]
    assert set(primary["structure"]["requirements"]) == set(expected["structure"]["requirements"]) == {"r1", "r2"}
    assert primary["structure"] != expected["structure"]
    assert switched["structure"] == expected["structure"]
    assert "structure_reason" not in switched  # the primary's field is not inherited
    assert switched["structure"]["requirements"]["r1"]["targets"] == []
    assert switched["structure"]["polarity"][0]["requirement_ids"] == ["r2"]
    assert switched["structure"]["confirmed_effects"] == []
    for key in READING_FIELDS:
        assert switched[key] == expected[key], key
    for key in ANALYSIS_FIELDS:
        assert other[key] == expected[key], key
    assert other["judgement"]["failed_sources"]
    assert switched["assumption_seeds"]
    assert primary["bindings"] != switched["bindings"]
    assert primary["selection_refs"] != switched["selection_refs"]
    for c in analysis["conditions"]:
        if c["kind"] in ("frequency", "period"):
            assert c["id"] in scope(analysis, "r2")[0] and c["id"] not in scope(analysis, "r1")[0]
            assert c["id"] in scope(other, "r1")[0] and c["id"] not in scope(other, "r2")[0]
    for rid in ("r1", "r2"):
        inp = selection_input(other, rid, layer)
        assert inp["reading_id"] == "alternative"
        assert inp["positive_refs"] == expected["selection_refs"][rid]
        assert inp["binding_requirement_ids"] == expected["requirement_ids"]
    assert switched["verify"]["issues"] == primary["verify"]["alternative_issues"]
    assert switched["verify"]["scopes"] == scope_index(primary["verify"]["alternative_issues"])
    assert switched["verify"]["alternative"] == "absent"
    assert switched["verify"]["alternative_issues"] == []
    assert switched["alternative"] is None
    assert readings.reading_candidates(other, layer) == []
    _check_legacy_contract(other)
    assert analysis == before


def test_reading_comparison_passes_the_alternatives_own_data_to_selection(analysis, layer, monkeypatch):
    from claview_a_legacy.select import graph

    expected = analysis["decomposition"]["alternative"]
    real_select = graph.select_tables
    seen = []

    def select(a, *args, **kwargs):
        seen.append(a)
        assert a["decomposition"]["structure"] == expected["structure"]
        assert a["decomposition"]["bindings"] == expected["bindings"]
        assert a["decomposition"]["selection_refs"] == expected["selection_refs"]
        return real_select(a, *args, **kwargs)

    monkeypatch.setattr(graph, "select_tables", select)
    result = readings.compare_readings(analysis, [], layer, "2026-10-20")
    assert len(seen) == 1 and len(result["readings"]) == 2
    assert result["readings"][1]["source"] == "decomposition:alternative"


def test_switched_nested_records_do_not_mutate_primary_or_saved_alternative(analysis, layer):
    before = copy.deepcopy(analysis)
    other = _other(analysis, layer)
    d = other["decomposition"]
    d["structure"]["requirements"]["r2"]["breakdowns"][0]["values"][0]["candidates"].clear()
    d["structure"]["polarity"][0]["negative"][0]["segment_ids"].clear()
    d["structure"]["polarity"][0]["requirement_ids"].clear()
    d["bindings"].clear()
    d["selection_refs"]["r1"][0]["candidates"].clear()
    d["condition_fields"].clear()
    d["inferred"].clear()
    d["assumption_seeds"].clear()
    other["judgement"]["failed_sources"].clear()
    other["ref_map"].clear()
    assert analysis == before
    assert _other(analysis, layer)["decomposition"]["structure"] == before["decomposition"]["alternative"]["structure"]


@pytest.mark.parametrize("primary_fields", ["absent", "object", "null_reason"])
def test_old_alternative_without_structure_never_inherits_primary_fields(analysis, layer, primary_fields):
    d = analysis["decomposition"]
    d["alternative"].pop("structure")
    if primary_fields == "absent":
        d.pop("structure")
        d.pop("structure_reason")
    elif primary_fields == "null_reason":
        # A malformed parent must not supply a fallback reason to an old alternative.
        d.update(structure=None, structure_reason="rules_structure_unrecorded")
    other = _other(analysis, layer)
    assert "structure" not in other["decomposition"]
    assert "structure_reason" not in other["decomposition"]


@pytest.mark.parametrize("value", [None, {"version": "unsupported"}])
def test_invalid_alternative_structure_is_not_repaired_with_the_primary(analysis, layer, value):
    analysis["decomposition"]["alternative"]["structure"] = value
    other = _other(analysis, layer)
    assert other["decomposition"]["structure"] == value
    assert "structure_reason" not in other["decomposition"]
    # Validation remains the contract boundary's job; switching cannot hide its input.


def test_new_alternative_does_not_require_a_primary_structure_field(analysis, layer):
    d = analysis["decomposition"]
    d.pop("structure")
    d.pop("structure_reason")
    assert _other(analysis, layer)["decomposition"]["structure"] == d["alternative"]["structure"]
