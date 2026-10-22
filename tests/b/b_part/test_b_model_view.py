"""What the model is sent for a table: the slimmed copy (`model_table`) and the output cap.

HCX counts input tokens plus the requested output cap against a per-minute token limit
(60,000 on the current key). The table's `path` field was ~70% of a large table's size and
repeats what `parent_code` already says, so the model gets a copy without it. These tests pin
what the copy keeps and drops, that pruning and the answer check are unaffected, and that
B asks for a smaller output cap.
"""

from __future__ import annotations

import copy
import json

import pytest

from claview.contracts import Config, Trace
from claview.models import ScriptedModel
from claview.stages import (
    B_PROMPT,
    EXPAND_PROMPT,
    BSteps,
    Services,
    build_b_context,
    display_names,
    model_table,
)


def _value(name, parent=None, path=None, leaf=True, unit=None):
    return {"name": name, "unit": unit, "parent_code": parent, "path": path, "is_leaf": leaf}


def _tree_table() -> dict:
    """T > A > (A1, A2, A3 "기타") ; T > B > (B1 "기타"): two values share the name "기타"."""
    t = {"code": "T", "name": "총계"}
    a = {"code": "A", "name": "광공업"}
    b = {"code": "B", "name": "서비스업"}
    values = {
        "T": _value("총계", None, [t], leaf=False, unit="%"),
        "A": _value("광공업", "T", [t, a], leaf=False),
        "A1": _value("제조업", "A", [t, a, {"code": "A1", "name": "제조업"}]),
        "A2": _value("광업", "A", [t, a, {"code": "A2", "name": "광업"}], unit="%"),
        "A3": _value("기타", "A", [t, a, {"code": "A3", "name": "기타"}]),
        "B": _value("서비스업", "T", [t, b], leaf=False),
        "B1": _value("기타", "B", [t, b, {"code": "B1", "name": "기타"}]),
    }
    return {
        "org_id": "301",
        "table_id": "DT_TREE",
        "table_name": "트리 표",
        "items": {"I1": {"name": "지표", "unit": None, "parent_code": None}},
        "dimensions": {"objL1": {"name": "산업", "values": values}},
        "periods": {"Y": {"start": "2010", "end": "2025"}},
        "notes": [],
        "units": ["%"],
        "snapshot_id": "test",
        "warnings": [],
    }


def test_model_table_drops_what_the_model_can_derive():
    view = model_table(_tree_table())
    values = view["dimensions"]["objL1"]["values"]
    assert all("path" not in v for v in values.values())
    assert set(values) == {"T", "A", "A1", "A2", "A3", "B", "B1"}  # every code stays
    assert values["T"] == {"name": "총계", "is_leaf": False, "unit": "%"}  # no parent_code
    assert values["A"] == {"name": "광공업", "parent_code": "T", "is_leaf": False}  # no empty unit
    assert values["A1"] == {"name": "제조업", "parent_code": "A", "is_leaf": True}
    assert values["A2"] == {"name": "광업", "parent_code": "A", "unit": "%", "is_leaf": True}
    # everything else about the table is passed through
    assert view["dimensions"]["objL1"]["name"] == "산업"
    assert {k: v for k, v in view.items() if k != "dimensions"} == {
        k: v for k, v in _tree_table().items() if k != "dimensions"
    }


def test_model_table_writes_is_leaf_on_every_value_and_matches_the_stored_flag():
    """Leaving the flag off for leaves made HCX pick a child in CV-AB-03 (2026-10-16)."""
    table = _tree_table()
    values = model_table(table)["dimensions"]["objL1"]["values"]
    for code, stored in table["dimensions"]["objL1"]["values"].items():
        assert values[code]["is_leaf"] is stored["is_leaf"], code


def test_same_name_values_keep_the_official_name_and_say_what_tells_them_apart():
    table = _tree_table()
    values = model_table(table)["dimensions"]["objL1"]["values"]
    labels = display_names(table["dimensions"]["objL1"]["values"])
    # `name` is never rewritten: a question that quotes it still matches it exactly
    assert values["A3"]["name"] == values["B1"]["name"] == "기타"
    assert values["A3"]["under"] == "광공업" and values["B1"]["under"] == "서비스업"
    for code in ("A3", "B1"):  # under + name is the label B shows to the user
        assert values[code]["under"] + " > " + values[code]["name"] == labels[code]
    assert all("under" not in values[c] for c in ("T", "A", "A1", "A2", "B"))  # unique names


def test_pruned_axis_keeps_the_ancestors_of_the_whole_axis():
    full = _tree_table()
    pruned = copy.deepcopy(full)
    pruned["dimensions"]["objL1"]["values"] = {
        c: v for c, v in full["dimensions"]["objL1"]["values"].items() if c in {"T", "A", "A3"}
    }
    # "기타" is unique in the pruned axis, but the model is told what the user would be told
    values = model_table(pruned, full)["dimensions"]["objL1"]["values"]
    assert values["A3"] == {"name": "기타", "under": "광공업", "parent_code": "A", "is_leaf": True}
    assert "under" not in model_table(pruned)["dimensions"]["objL1"]["values"]["A3"]


def test_model_table_does_not_change_the_stored_table():
    table = _tree_table()
    before = copy.deepcopy(table)
    model_table(table)
    assert table == before


# ---- real tables ------------------------------------------------------------------------


def _steps(store, config, model=None):
    return BSteps(
        Services(store, search=None, model=model or ScriptedModel({}), config=config, trace=Trace())
    )


def _context_for(case, store, config, model=None):
    steps = _steps(store, config, model)
    state = {"query": case["query"], "a_result": case["expected"]["A"]}
    state.update(steps.load(state))
    state.update(steps.context(state))
    assert "stage_outcome" not in state, state.get("stage_outcome")
    return state


def test_model_context_has_no_path_and_keeps_every_golden_code(golden_cases, store, config):
    for case in golden_cases:
        state = _context_for(case, store, config)
        shown = state["selection_context"]["table_metadata"]["dimensions"]
        stored = state["table_metadata"][state["active_table"]]["dimensions"]
        for axis, dimension in shown.items():
            for code, value in dimension["values"].items():
                assert "path" not in value, (case["case_id"], code)
                assert value["is_leaf"] is stored[axis]["values"][code]["is_leaf"], code
        for sel in case["expected"]["B"]["selections"]:
            for axis, v in sel["dimensions"].items():
                assert v["code"] in shown[axis]["values"], (case["case_id"], v["code"])
        # the stored table (used by expansion and labels) still has its paths
        assert all("path" in v for d in stored.values() for v in d["values"].values()), case[
            "case_id"
        ]


def test_model_context_is_much_smaller_for_the_table_that_got_http_429(golden_cases, store, config):
    """CV-AB-04's table (DT_301Y013) was ~131k characters; a compact copy is far smaller."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-04")
    compact = _context_for(case, store, config)
    full = _context_for(case, store, Config(compact_model_context=False))
    sent = len(json.dumps(compact["selection_context"], ensure_ascii=False))
    before = len(json.dumps(full["selection_context"], ensure_ascii=False))
    assert sent < 0.5 * before, (sent, before)
    assert sent < 60000
    # same candidates either way: the slimming does not change which values are shown
    key = "table_metadata"
    for axis, dimension in full["selection_context"][key]["dimensions"].items():
        assert set(dimension["values"]) == set(
            compact["selection_context"][key]["dimensions"][axis]["values"]
        )


def test_exact_name_of_the_asked_value_survives_when_the_name_is_shared(
    golden_cases, store, config
):
    """CV-AB-03 asks for "재화와 서비스의 수출". That name belongs to two values of DT_200Y101;
    both must keep it as `name` (HCX picked a child when it was folded into a long label)."""
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-03")
    shown = _context_for(case, store, config)["selection_context"]["table_metadata"]
    values = shown["dimensions"]["objL1"]["values"]
    key = "13102136288ACC_ITEM."
    assert values[key + "30105"]["name"] == "재화와 서비스의 수출"
    assert values[key + "30105"]["under"] == "지출항목별 증감률(실질)"
    assert values[key + "60103"]["name"] == "재화와 서비스의 수출"
    assert values[key + "60103"]["under"].startswith("지출구조")
    assert values[key + "3010501"]["name"] == "(재화)"
    assert values[key + "3010501"]["under"] == "지출항목별 증감률(실질) > 재화와 서비스의 수출"


def test_pruning_is_decided_on_the_stored_table_as_before(golden_cases, store, config):
    """The size check that triggers pruning is unchanged; only the model's copy is slimmed."""
    for case_id, pruned in (("CV-AB-04", True), ("CV-AB-05", True), ("CV-AB-20", False)):
        case = next(c for c in golden_cases if c["case_id"] == case_id)
        t = case["expected"]["A"]["tables"][0]
        stored = store.get(t["org_id"], t["table_id"])
        assert bool(build_b_context(case["query"], stored, config)["pruned_axes"]) is pruned
        state = _context_for(case, store, config)
        shown = state["selection_context"]["table_metadata"]
        assert bool(shown["pruned_axes"]) is pruned, case_id


def test_flag_off_sends_the_table_as_stored(golden_cases, store):
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-13")
    state = _context_for(case, store, Config(compact_model_context=False))
    values = state["selection_context"]["table_metadata"]["dimensions"]
    assert any("path" in v for d in values.values() for v in d["values"].values())


# ---- output cap and prompt --------------------------------------------------------------


class _Spy(ScriptedModel):
    def __init__(self):
        super().__init__({})
        self.max_tokens = []

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.max_tokens.append(max_tokens)
        return {"selections": []}


def _asked_max_tokens(golden_cases, store, config):
    case = next(c for c in golden_cases if c["case_id"] == "CV-AB-13")
    spy = _Spy()
    steps = _steps(store, config, spy)
    state = {"query": case["query"], "a_result": case["expected"]["A"]}
    state.update(steps.load(state))
    state.update(steps.context(state))
    steps.select(state)
    return spy.max_tokens


def test_b_asks_for_the_configured_output_cap(golden_cases, store):
    assert Config().max_b_output_tokens == 6144
    assert _asked_max_tokens(golden_cases, store, Config()) == [6144]
    assert _asked_max_tokens(golden_cases, store, Config(max_b_output_tokens=2048)) == [2048]


@pytest.mark.parametrize("bad", [0, -1, 32769])
def test_output_cap_must_be_within_hcx_limits(bad):
    with pytest.raises(ValueError):
        Config(max_b_output_tokens=bad)


def test_the_largest_golden_answer_fits_well_under_the_cap(golden_cases):
    """Even listed one by one (no `expand`), the longest golden answer is far below the cap."""
    longest = 0
    for case in golden_cases:
        rows = [
            {
                "item_id": s["item_id"],
                "dimensions": {a: v["code"] for a, v in s["dimensions"].items()},
                "period": s["period"],
                "expand": {a: "none" for a in s["dimensions"]},
            }
            for s in case["expected"]["B"]["selections"]
        ]
        longest = max(longest, len(json.dumps({"selections": rows}, ensure_ascii=False)))
    # at the pessimistic 1 token per character the answer still fits in half the cap
    assert longest * 2 <= Config().max_b_output_tokens, longest


def test_prompt_matches_the_slimmed_table():
    # every value carries is_leaf, so the prompt does not talk about a missing mark
    assert "is_leaf가 true인 값에는 쓰지 않는다" in EXPAND_PROMPT
    assert "표시가 없는" not in EXPAND_PROMPT
    assert "parent_code" in B_PROMPT
    assert "under에" in B_PROMPT
