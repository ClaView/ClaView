"""Real structure producer -> consumer, public bundle, V2 and alternative isolation."""
import copy

import pytest
from test_plan15_settle import R, bind, ind, layer, proposal, reading, req, run

from claview_v3.contract.check import check_analysis, check_selections
from claview_v3.select.graph import select_tables
from claview_v3.select.input import selection_input
from claview_v3.select import execution


def simple(p):
    return proposal(reading([req("r1", ind(p, "가상대출금"))],
                            [bind(c["id"]) for c in p.payload["conditions"]],
                            request=[R(p.s("보여줘"))]))


@pytest.mark.parametrize("period", ["1900년부터 2020년까지", "2020년부터 2090년까지", "1900년부터 2090년까지"])
def test_clipping_keeps_a_valid_public_bundle(layer, period):
    a = run(f"가상대출금 {period} 보여줘", layer, simple)
    assert a["decomposition"]["applied_source"] == "model"
    before = copy.deepcopy(a)
    selections = select_tables(a, layer, "2026-10-21", selection_mode="refutation")["selections"]
    assert check_selections(selections) == []
    s = selections[0]
    record = s["signals"]["plan17"]
    assert "bundle" in s and record["b_handoff_ready"]
    assert record["chosen"]["missing"] or record["chosen"]["proof_status"] != "proven"
    for t, segments in s["bundle"]["assignments"].items():
        assert len(segments) == 1
        periods = record["chosen"]["period_plan"]["results"][t]["periods"]["covered_months"]
        assert any(p["start"] <= segments[0]["start"] <= segments[0]["end"] <= p["end"] for p in periods)
    assert a == before


def test_public_reference_candidates_are_rows_not_table_ids(layer):
    a = run("가상대출금 보여줘", layer, simple)
    s = select_tables(a, layer, "2026-10-21", selection_mode="refutation")["selections"][0]
    assert s["reference_candidates"] and all(isinstance(c, dict) and c["table_id"] for c in s["reference_candidates"])
    assert check_selections([s]) == []


def test_empty_confirmed_effects_disable_both_negative_refutation_paths(layer, monkeypatch):
    a = run("가상대출금 보여줘", layer, simple)
    original_needs, original_refute = execution.requirement_needs, execution.refute.refute_tables
    checked = []

    def needs(*args, **kw):
        nd = original_needs(*args, **kw)
        nd["filter"].update(negated=["가상대출금"], negated_settled=True)
        nd["evidence"]["conditions"] += [{"condition_id": "neg", "kind": "exclude", "polarity": "-",
                                           "axis": None, "byeol": False, "settled": True}]
        return nd

    def refute(nd, *args):
        assert nd["filter"]["negated_settled"] is False
        assert not next(c for c in nd["evidence"]["conditions"] if c["condition_id"] == "neg")["settled"]
        checked.append(True)
        return original_refute(nd, *args)

    monkeypatch.setattr(execution, "requirement_needs", needs)
    monkeypatch.setattr(execution.refute, "refute_tables", refute)
    select_tables(a, layer, "2026-10-21", selection_mode="refutation")
    assert checked


def test_bad_model_null_stops_selection(layer):
    a = run("가상대출금 보여줘", layer, simple)
    a["decomposition"]["structure"] = None
    assert check_analysis(a)
    with pytest.raises(ValueError, match="agreed contract"):
        select_tables(a, layer, selection_mode="refutation")


def test_scoped_structure_is_copied_into_input(layer):
    a = run("가상대출금 보여줘", layer, simple)
    inp = selection_input(a, "r1", layer, with_structure=True)
    assert inp["structure"]["requirement"] == a["decomposition"]["structure"]["requirements"]["r1"]
    inp["structure"]["requirement"]["targets"].append({})
    assert a["decomposition"]["structure"]["requirements"]["r1"]["targets"] == []
