"""Shared fields (plan 04, 5-3): key names equal to the working folder's state file (read as text, never imported)
and the ``to_common`` mapping."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from claview.a.agent import state as outer
from claview.a.agent.common import to_common

# The working folder next to this checkout (read as text only; skipped where it is absent).
WORKING_AGENT = Path(__file__).resolve().parents[3].parent / "claview" / "src" / "claview" / "agent"
SHARED_NAMES = ("CONTROL_KEYS", "SELECTION_KEYS", "REQUIREMENT_KEYS", "UNRESOLVED_KEYS")


def _text(name: str) -> str:
    path = WORKING_AGENT / name
    if not path.is_file():
        pytest.skip(f"working folder file absent: {name}")
    return path.read_text(encoding="utf-8")


def _assigned_tuples(text: str) -> dict:
    found = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in SHARED_NAMES:
                found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def test_shared_key_names_equal_the_working_folder():
    theirs = _assigned_tuples(_text("state.py"))
    assert set(theirs) == set(SHARED_NAMES)
    assert {name: getattr(outer, name) for name in SHARED_NAMES} == theirs


def test_shared_state_field_names_are_a_subset_of_the_working_folder():
    text = _text("state.py")
    tree = ast.parse(text)
    agent = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AgentState")
    theirs = {n.target.id for n in agent.body if isinstance(n, ast.AnnAssign)}
    shared = {"question", "as_of", "request_id", "requirements", "unresolved", "selection", "a_outcome", "control",
              "response"}
    assert shared <= theirs
    new = {"variant", "analysis", "selections", "selection_groups", "evidence", "verification", "jev_record",
           "selection_route", "question_version", "decomposition_ledger", "decomposition_runs"}
    # Plan 15 step 5: question version, call ledger snapshot, and interpretation visits.
    assert set(outer.OuterState.__annotations__) == shared | new
    assert set(outer.make_initial_state("q", "straight")) == shared | new
    assert tuple(outer.initial_control()) == outer.CONTROL_KEYS


def test_met_means_the_same_as_in_the_working_folder():
    """There ``met`` is the only status that is not lifted to unresolved; here ``met`` comes only from auto."""
    assert re.search(r'a_status"\]\s*!=\s*"met"', _text("nodes.py"))
    analysis = _analysis([_req("r1"), _req("r2"), _req("r3")])
    selections = [_sel("r1", "auto"), _sel("r2", "confirm", reasons=["partial_cover"])]
    statuses = {r["scope_id"]: r["a_status"] for r in to_common(analysis, selections)["requirements"]}
    assert statuses == {"r1": "met", "r2": "confirm", "r3": "not_selected"}


def _req(rid, status="ok", ambiguous=(), quote=None):
    return {"id": rid, "quote": quote or f"{rid} words", "span": [0, 1], "indicator": None, "measure_kind": None,
            "targets": [], "condition_ids": [], "shared_condition_ids": [], "ambiguous_condition_ids": list(ambiguous),
            "status": status, "reasons": []}


def _sel(rid, status, tables=("T1",), reasons=(), unspecified=()):
    return {"requirement_id": rid, "tables": [{"table_id": t, "covers": {}} for t in tables],
            "unspecified": list(unspecified), "status": status, "model_step": None, "reasons": list(reasons)}


def _analysis(requirements, interp_status="ok"):
    interp = {"status": interp_status, "common_condition_ids": [], "relations": [], "display_condition_ids": [],
              "requirements": requirements, "reasons": []}
    return {"text": {}, "conditions": [], "fragments": [], "terms": [], "judgement": None, "interpretation": interp}


def test_to_common_fields():
    analysis = _analysis([_req("r1", quote="경상수지"), _req("r2", status="unresolved", ambiguous=["B1"]),
                          _req("r3")])
    selections = [_sel("r1", "auto", tables=("T1", "T2"), unspecified=[{"dimension": "basis", "table_ids": ["T2"]}]),
                  _sel("r3", "no_table", tables=(), reasons=["no_evidence"]),
                  _sel("r2", "confirm", tables=("T2",))]
    out = to_common(analysis, selections)
    first = out["requirements"][0]
    assert tuple(first) == outer.REQUIREMENT_KEYS
    assert first == {"scope_id": "r1", "text": "경상수지", "a_status": "met",
                     "gaps": [{"dimension": "basis", "table_ids": ["T2"]}], "reason_codes": ["interp:ok"],
                     "source": "A"}
    assert out["requirements"][1]["reason_codes"] == ["interp:unresolved"]
    assert out["requirements"][2]["reason_codes"] == ["interp:ok", "select:no_evidence"]
    assert [(u["scope_id"], u["kind"], u["detail"], u["from_node"]) for u in out["unresolved"]] == [
        ("r2", "ambiguous_condition", "B1", "interpret"), ("r2", "requirement_unresolved", "unresolved", "interpret"),
        ("r2", "requirement_not_met", "confirm", "A"),
        ("r3", "no_table", ["no_evidence"], "select"), ("r3", "requirement_not_met", "no_table", "A")]
    assert all(tuple(u) == outer.UNRESOLVED_KEYS for u in out["unresolved"])
    assert tuple(out["selection"]) == outer.SELECTION_KEYS
    assert [t["table_id"] for t in out["selection"]["tables"]] == ["T1", "T2"]
    assert out["selection"]["backups"] == []


def test_every_requirement_that_is_not_met_is_unresolved():
    """As the working folder's agent/nodes.py: a_status != met -> requirement_not_met."""
    assert re.search(r'requirement_not_met', _text("nodes.py"))
    analysis = _analysis([_req("r1"), _req("r2"), _req("r3")])
    out = to_common(analysis, [_sel("r1", "auto"), _sel("r2", "confirm")])
    not_met = [(u["scope_id"], u["detail"]) for u in out["unresolved"] if u["kind"] == "requirement_not_met"]
    assert not_met == [("r2", "confirm"), ("r3", "not_selected")]
    met = {r["scope_id"] for r in out["requirements"] if r["a_status"] == "met"}
    assert met == {"r1"} and all(u["scope_id"] not in met for u in out["unresolved"])


def test_v2_lowered_states_reach_a_status_and_tables():
    analysis = _analysis([_req("r1"), _req("r2")])
    selections = [_sel("r1", "auto", tables=("T1",)), _sel("r2", "auto", tables=("T2",))]
    checked = {"states": {"r1": "confirm", "r2": "no_table"}}
    out = to_common(analysis, selections, checked)
    assert [r["a_status"] for r in out["requirements"]] == ["confirm", "no_table"]
    assert [t["table_id"] for t in out["selection"]["tables"]] == ["T1"]
    assert ("r2", "no_table") in [(u["scope_id"], u["kind"]) for u in out["unresolved"]]
    assert to_common(analysis, selections, {"status": "skipped", "states": {}})["requirements"][0]["a_status"] == "met"


def test_to_common_without_selection_or_settled_requirements():
    out = to_common(_analysis([_req("r1")]), None)
    assert out["requirements"][0]["a_status"] == "not_selected"
    front_only = {**_analysis([]), "interpretation": None}
    assert to_common(front_only, []) == {
        "requirements": [],
        "unresolved": [{"scope_id": None, "kind": "interpretation_pending", "detail": "front_only",
                        "from_node": "interpret"}],
        "selection": {"tables": [], "backups": []}}
    assert to_common(None, None) == {"requirements": [], "unresolved": [], "selection": {"tables": [],
                                                                                          "backups": []}}
