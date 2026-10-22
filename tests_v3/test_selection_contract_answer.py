"""Plan 11 R1 (contract C, 7-8, 7-11, 8-3-1, F2, F6, F7, F11, 8-6): the question contract of
``selection_route.questions[]``, the answer, the confirmed condition, the field-effect table of
data/decision_policy.json and the ``answers`` entry point of ``select_tables`` (shape only, nothing applied).
Table IDs, names and words are made up."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "spec"))
from spec_support import build_layer, itm, table  # noqa: E402

from claview_v3 import paths  # noqa: E402
from claview_v3.agent.question_route import question_route  # noqa: E402
from claview_v3.contract import shapes  # noqa: E402
from claview_v3.contract.check import (check_confirmed_condition, check_selection_answer,  # noqa: E402
                                       check_selection_answers, check_selection_route, interpretation_fields,
                                       version_of)
from claview_v3.select.cover import AXIS_KINDS  # noqa: E402

IND = "BSI코드별"  # an axis name the synthetic layer-1 sheet reads as an indicator axis
VERSION = {"analysis": "a" * 16, "selection": "b" * 16}


# ---------------------------------------------------------------- builders


def _option(oid, tables, rids=("r1",), dimension="family", value=None, assignment=None):
    return {"id": oid, "label": " / ".join(tables) or "-", "table_ids": list(tables), "complete": True,
            "value": [] if value is None else [value],
            "assignment": {r: list(tables) for r in rids} if assignment is None else assignment,
            "effect": {"dimension": dimension, "value": value, "polarity": "+"}}


def _question(qid="q1", kind="pick_assignment", options=None, rids=("r1",), dimension="family", is_open=False,
              cause="family_alternatives", version=None):
    if options is None:
        options = [_option(f"{qid}.o1", ["A"], rids, dimension), _option(f"{qid}.o2", ["B"], rids, dimension)]
    return {"id": qid, "version": dict(version or VERSION), "cause": cause, "requirement_ids": list(rids),
            "dimension": dimension, "open": is_open, "effect_kind": kind, "options": options, "values": [],
            "deferred": False}


def _route(*questions, rids=("r1",)):
    return {"kind": "ask_selection", "requirement_ids": list(rids), "options": [], "default": None, "reasons": [],
            "partial": False, "confirm": True, "questions": list(questions)}


# ---------------------------------------------------------------- question shapes, every effect kind


def test_pick_assignment_question_is_valid():
    assert check_selection_route(_route(_question()), {"r1"}) == []


def test_set_condition_question_needs_one_value_per_option():
    axis = sorted(AXIS_KINDS)[0]
    opts = [_option("q1.o1", ["A"], dimension=axis, value="가상값1"),
            _option("q1.o2", ["B"], dimension=axis, value="가상값2")]
    assert check_selection_route(_route(_question(kind="set_condition", options=opts, dimension=axis))) == []
    bad = copy.deepcopy(opts)
    bad[1]["effect"]["value"] = None
    assert any("set_condition option sets one value" in p for p in
               check_selection_route(_route(_question(kind="set_condition", options=bad, dimension=axis))))
    other = copy.deepcopy(opts)
    other[0]["effect"]["dimension"] = "family"
    assert any("differs from the question's dimension" in p for p in
               check_selection_route(_route(_question(kind="set_condition", options=other, dimension=axis))))


def test_change_reading_question_may_have_no_assignment():
    opts = [_option("q1.o1", ["A"], assignment={}), _option("q1.o2", ["B"], assignment={})]
    assert check_selection_route(_route(_question(kind="change_reading", options=opts,
                                                  cause="readings_differ"))) == []


def test_rephrase_question_is_open():
    assert check_selection_route(_route(_question(kind="rephrase", options=[], is_open=True,
                                                  cause="no_indicator_open"))) == []
    got = check_selection_route(_route(_question(kind="rephrase", options=[], is_open=False)))
    assert any("a rephrase question is open" in p for p in got)


def test_drop_requirement_question_has_an_option_that_drops():
    drop = {"id": "q1.o1", "label": "drop", "assignment": {"r1": []},
            "effect": {"dimension": "requirement", "value": None, "polarity": "-"}}
    keep = {"id": "q1.o2", "label": "rephrase", "assignment": {},
            "effect": {"dimension": "requirement", "value": None, "polarity": "+"}}
    q = _question(kind="drop_requirement", options=[drop, keep], dimension="requirement", cause="ungrounded")
    assert check_selection_route(_route(q)) == []  # no table bundle needed
    q2 = _question(kind="drop_requirement", options=[keep], dimension="requirement", cause="ungrounded")
    assert any("leaves a requirement out" in p for p in check_selection_route(_route(q2)))


def test_new_fields_come_all_or_none_and_ids_are_unique():
    q = _question()
    del q["cause"]
    assert any("missing field 'cause'" in p for p in check_selection_route(_route(q)))
    dup = _route(_question("q1"), _question("q1"))
    assert any("duplicate 'q1'" in p for p in check_selection_route(dup))
    same_opt = _question(options=[_option("q1.o1", ["A"]), _option("q1.o1", ["B"])])
    assert any("duplicate 'q1.o1'" in p for p in check_selection_route(_route(same_opt)))
    two_versions = _route(_question("q1"), _question("q2", version={"analysis": "x", "selection": "y"}))
    assert any("different versions" in p for p in check_selection_route(two_versions))


def test_option_fields_and_assignment_are_checked():
    o = _option("q1.o1", ["A"])
    del o["label"]
    assert any("missing field 'label'" in p for p in check_selection_route(_route(_question(options=[
        o, _option("q1.o2", ["B"])]))))
    stranger = _option("q1.o1", ["A"], assignment={"r9": ["A"]})
    assert any("not a requirement of the question" in p for p in check_selection_route(_route(_question(options=[
        stranger, _option("q1.o2", ["B"])]))))
    outside = _option("q1.o1", ["A"], assignment={"r1": ["Z"]})
    assert any("not among the option's table_ids" in p for p in check_selection_route(_route(_question(options=[
        outside, _option("q1.o2", ["B"])]))))
    bad_kind = _question(kind="guess")
    assert any("effect_kind" in p for p in check_selection_route(_route(bad_kind)))


def test_plan10_question_without_the_new_fields_keeps_its_checks():
    old = {"requirement_ids": ["r1"], "dimension": "family", "values": [], "deferred": False,
           "options": [{"table_ids": ["A"], "complete": True}, {"table_ids": ["B"], "complete": True}]}
    assert check_selection_route(_route(old), {"r1"}) == []
    one = {**old, "options": old["options"][:1]}
    assert any("1 options (2 to 5)" in p for p in check_selection_route(_route(one)))


# ---------------------------------------------------------------- open question


def test_open_question_branch():
    single = _question(options=[_option("q1.o1", ["A"])], is_open=True, cause="target_families")
    assert check_selection_route(_route(single)) == []
    empty = _question(options=[], is_open=True, cause="no_indicator_open")
    assert check_selection_route(_route(empty)) == []
    not_open = _question(options=[_option("q1.o1", ["A"])], is_open=False)
    got = check_selection_route(_route(not_open))
    assert any("fewer than two options is open" in p for p in got)
    assert any("1 options (2 to 5)" in p for p in got)  # the plan 10 check still applies
    six = _question(options=[_option(f"q1.o{k}", [f"T{k}"]) for k in range(6)], is_open=True)
    assert any("at most 5" in p for p in check_selection_route(_route(six)))


# ---------------------------------------------------------------- answers (shape only)


def test_answer_shape():
    assert check_selection_answer({"question_id": "q1", "option_id": "q1.o1", "version": VERSION}) == []
    assert check_selection_answer({"question_id": "q1", "text": "가상 다른 말", "version": VERSION}) == []
    both = {"question_id": "q1", "option_id": "q1.o1", "text": "가상", "version": VERSION}
    assert any("exactly one of option_id and text" in p for p in check_selection_answer(both))
    neither = {"question_id": "q1", "version": VERSION}
    assert any("exactly one of option_id and text" in p for p in check_selection_answer(neither))
    assert any("text: empty" in p for p in check_selection_answer({"question_id": "q1", "text": " ",
                                                                   "version": VERSION}))
    assert any("missing field 'version'" in p for p in check_selection_answer({"question_id": "q1",
                                                                               "option_id": "q1.o1"}))
    assert any("unknown field" in p for p in check_selection_answer({"question_id": "q1", "option_id": "q1.o1",
                                                                     "version": VERSION, "extra": 1}))


def test_answer_against_the_questions_and_version_mismatch():
    questions = [_question()]
    ok = {"question_id": "q1", "option_id": "q1.o2", "version": VERSION}
    assert check_selection_answer(ok, questions) == []
    stale = {**ok, "version": {**VERSION, "selection": "c" * 16}}
    assert any("version: stale" in p for p in check_selection_answer(stale, questions))
    assert any("unknown question" in p for p in check_selection_answer({**ok, "question_id": "q9"}, questions))
    assert any("unknown option" in p for p in check_selection_answer({**ok, "option_id": "q1.o9"}, questions))
    # a later answer to the same question replaces the earlier: both are well-formed
    assert check_selection_answers([ok, {**ok, "option_id": "q1.o1"}], questions) == []
    assert check_selection_answers("q1") == ["answers: expected a list, got str"]


# ---------------------------------------------------------------- confirmed condition


def _confirmed(**kw):
    base = {"condition_id": None, "answer": "가상값", "requirement_key": "f1#basis:axis:가상값:+",
            "requirement_id": "r1", "dimension": "axis", "value": "가상값", "polarity": "+", "source": "user",
            "from": {"question_id": "q1", "option_id": "q1.o1"}}
    return {**base, **kw}


def test_confirmed_condition_shape():
    assert check_confirmed_condition(_confirmed(), {"r1"}) == []
    assert check_confirmed_condition(_confirmed(source="interpretation", condition_id="B1", **{"from": None}),
                                     {"r1"}, {"B1"}) == []
    assert any("names the question" in p for p in check_confirmed_condition(_confirmed(**{"from": None})))
    assert any("names its condition" in p for p in check_confirmed_condition(
        _confirmed(source="interpretation", **{"from": None})))
    assert any("only a user answer" in p for p in check_confirmed_condition(
        _confirmed(source="interpretation", condition_id="B1")))
    missing = _confirmed()
    del missing["requirement_key"]
    assert any("requirement_key" in p for p in check_confirmed_condition(missing))
    assert any("polarity" in p for p in check_confirmed_condition(_confirmed(polarity="*")))
    assert any("unknown ID 'r9'" in p for p in check_confirmed_condition(_confirmed(requirement_id="r9"), {"r1"}))


def test_old_confirmed_detail_shape_stays_valid():
    from claview_v3.contract.check import check_check

    check = {"name": "confirmed", "state": "computed", "source_status": None, "verdict": "pass", "evidence_ids": [],
             "detail": {"condition_id": "B1", "answer": "가상값"}}
    assert check_check(check, condition_ids={"B1"}) == []


# ---------------------------------------------------------------- data/decision_policy.json (F2, F11)


def _policy_pairs():
    pairs: list = []
    json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"),
               object_pairs_hook=lambda kv: pairs.append(kv) or dict(kv))
    return pairs, json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))


def test_decision_policy_has_its_sections():
    _, policy = _policy_pairs()
    assert {"field_effects", "uncertainty", "confirm_allowed", "no_ask"} <= set(policy)
    assert policy["confirm_allowed"] == []  # 7-12: the confirm whitelist starts empty


def test_every_interpretation_field_maps_to_exactly_one_effect():
    pairs, policy = _policy_pairs()
    effects = policy["field_effects"]
    field_rows = next(kv for kv in pairs if dict(kv) == effects)
    keys = [k for k, _ in field_rows]
    assert len(keys) == len(set(keys)), "a row is written twice"
    assert set(effects.values()) <= set(policy["effects"])
    fields = interpretation_fields()
    seen: dict[str, list[str]] = {}
    for key in keys:
        field, _, value = key.partition("=")
        assert field in fields, f"{key}: no such interpretation field in contract/shapes.py"
        if value:
            assert fields[field] is not None and value in fields[field], f"{key}: not a value of {field}"
        seen.setdefault(field, []).append(value)
    for field, literals in fields.items():
        rows = seen.get(field)
        assert rows, f"{field}: no effect"
        if rows == [""]:
            continue  # one row for the whole field
        assert "" not in rows, f"{field}: both a field row and value rows"
        assert sorted(rows) == sorted(literals or ()), f"{field}: value rows do not cover every value once"


def test_the_fields_named_by_f2_are_covered():
    fields = interpretation_fields()
    for name in ("Requirement.context", "Judgement.structure", "TextView.raw", "CalcHint.kind"):
        assert name in fields
    for kind in ("calc", "function", "display", "relation"):
        assert kind in fields["Condition.kind"]
    _, policy = _policy_pairs()
    effects = policy["field_effects"]
    for key in ("Term.path=dictionary", "Term.path=embedding", "Term.status=draft",
                "BasisAttrs.origin=catalog_family", "Pair.verdict=undecided"):
        assert key in effects


# ---------------------------------------------------------------- question_route fills the contract


def _q(*rids):
    return {"interpretation": {"status": "ok", "requirements": [
        {"id": r, "status": "ok", "indicator": {"name": "가상"}, "reasons": [], "span": [0, 1]} for r in rids]},
        "judgement": {"certain": True, "failed_conditions": []}, "fragments": []}


def _d(ask=("family_alternatives",), bundles=None, dims=None):
    bundles = bundles or [{"table_ids": ["A"], "source": "current", "complete": True, "value": []},
                          {"table_ids": ["B"], "source": "family", "complete": True, "value": [], "family": "G"}]
    dims = dims or [{"dimension": "family", "class": "family", "kind": "family", "values": ["F", "G"], "gap": 1.0}]
    return {"bundles": bundles, "dimensions": dims, "result": "ask", "ask_reasons": list(ask), "resolved_by": [],
            "gap": 1.0, "set_would_drop": []}


def _rsel(rid, d, tables=("A",)):
    return {"requirement_id": rid, "status": "confirm", "tables": [{"table_id": t} for t in tables],
            "model_step": "pending", "unspecified": [], "reasons": ["family_alternatives"], "alternatives": [["B"]],
            "signals": {"dimensions": d}}


def test_question_route_fills_the_contract_fields():
    an, sels = _q("r1", "r2"), [_rsel("r1", _d()), _rsel("r2", _d())]
    got = question_route(an, sels)
    assert check_selection_route(got, {"r1", "r2"}) == []
    (q,) = got["questions"]
    assert q["id"] == "q1" and q["cause"] == "family_alternatives" and q["effect_kind"] == "pick_assignment"
    assert q["open"] is False and q["version"] == {"analysis": version_of(an), "selection": version_of(sels)}
    assert [o["id"] for o in q["options"]] == ["q1.o1", "q1.o2"]
    assert [o["assignment"] for o in q["options"]] == [{"r1": ["A"], "r2": ["A"]}, {"r1": ["B"], "r2": ["B"]}]
    # the plan 10 fields of the options are kept as they were
    assert [(o["table_ids"], o["default"], o["source"]) for o in q["options"]] == [(["A"], True, "current"),
                                                                                    (["B"], False, "family")]
    again = question_route(an, sels)
    assert again == got  # the same inputs give the same IDs and versions


def test_question_route_sets_a_condition_on_a_basis_axis():
    axis = sorted(AXIS_KINDS)[0]
    bundles = [{"table_ids": ["A"], "source": "current", "complete": True, "value": ["가상값1"]},
               {"table_ids": ["B"], "source": "replacement", "complete": True, "value": ["가상값2"],
                "dimension": axis}]
    dims = [{"dimension": axis, "class": "unset", "kind": AXIS_KINDS[axis], "values": ["가상값2"], "gap": None}]
    got = question_route(_q("r1"), [_rsel("r1", _d(("unspecified_dimension",), bundles, dims))])
    (q,) = got["questions"]
    assert q["effect_kind"] == "set_condition" and q["dimension"] == axis
    assert [o["effect"]["value"] for o in q["options"]] == ["가상값1", "가상값2"]
    assert check_selection_route(got) == []


def test_question_route_flat_question_from_readings():
    from claview_v3.agent.question_route import _flat_question

    opts = [{"requirement_id": None, "table_ids": ["A"], "tables": [], "default": True,
             "source": "reading:interpretation"},
            {"requirement_id": None, "table_ids": ["B"], "tables": [], "default": False, "source": "reading:P1:o1"}]
    q = _flat_question(["r1"], ["inexact_term", "readings_differ"], opts)
    assert q["effect_kind"] == "change_reading" and q["cause"] == "readings_differ"
    assert all(o["assignment"] == {} for o in q["options"])
    picks = [{"requirement_id": "r1", "table_ids": ["A"], "tables": [], "default": True, "source": "current"},
             {"requirement_id": "r2", "table_ids": ["B"], "tables": [], "default": False, "source": "candidate"}]
    q2 = _flat_question(["r1", "r2"], ["target_families"], picks)
    assert q2["effect_kind"] == "pick_assignment"
    assert [o["assignment"] for o in q2["options"]] == [{"r1": ["A"]}, {"r2": ["B"]}]


# ---------------------------------------------------------------- select_tables(..., answers)


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    tables = [table("T_SUBJ", "가상물가지수", [itm("A", "1", "가상물가지수", obj_name=IND)]),
              table("T_COMP", "가상종합표", [itm("A", "1", "가상물가지수", obj_name=IND),
                                         itm("A", "2", "가상임금", obj_name=IND)])]
    return build_layer(tmp_path_factory.mktemp("contract_answer"), tables)


def test_select_tables_with_no_answers_is_unchanged(layer):
    from claview_v3.interpret.pipeline import interpret_question
    from claview_v3.select.graph import select_tables

    analysis = interpret_question("가상물가지수 보여줘", layer)
    before = select_tables(copy.deepcopy(analysis), layer)
    assert select_tables(copy.deepcopy(analysis), layer, answers=[]) == before
    assert select_tables(copy.deepcopy(analysis), layer, answers=None) == before
    # R1: a well-formed answer is not applied yet
    answer = {"question_id": "q1", "option_id": "q1.o1", "version": VERSION}
    assert select_tables(copy.deepcopy(analysis), layer, answers=[answer]) == before
    # R3: decisions now carries observation-only units; answers are still not applied.
    assert before["decisions"] and "analysis" not in before
    assert set(before["decisions"]) == {s["signals"]["decision_preview"]["bundle"]["unit"]
                                        for s in before["selections"]}
    assert set(before["facts"]) == {s["requirement_id"] for s in before["selections"]}
    for s in before["selections"]:
        assert before["facts"][s["requirement_id"]] == s["signals"]["facts"]["tables"]
    with pytest.raises(ValueError):
        select_tables(copy.deepcopy(analysis), layer, answers=[{"question_id": "q1", "version": VERSION}])


def test_shapes_export_the_contract():
    assert set(shapes.QUESTION_CONTRACT_FIELDS) <= set(shapes.RouteQuestion.__annotations__)
    assert set(shapes.TABLE_EFFECT_KINDS) < set(shapes.EffectKind.__args__)
    assert {"rephrase", "drop_requirement"} <= set(shapes.EffectKind.__args__)
