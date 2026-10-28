"""Router paths per variant (plan 04, 1-2, 1-3 and 3-3) on hand-made views: action order, return after an ask,
no repeated ask, all requirements without a table, retry before ask."""

from __future__ import annotations

import pytest
from agent_support import make_view, walk

from claview_a_legacy.agent import actions
from claview_a_legacy.agent.router import route
from claview_a_legacy.agent.state import ASK_USER, VARIANTS, VERIFY_INTERPRET

PLAIN = {
    "straight": ["next:interpret", "next:select", "finish"],
    "parts": ["next:interpret", "next:select", "finish"],
    "central": ["next:interpret", "verify:interpret", "next:select", "verify:select", "finish"],
    "router": ["next:interpret", "verify:interpret", "next:select", "verify:select", "finish"],
}


@pytest.mark.parametrize("variant", VARIANTS)
def test_plain_path(variant):
    assert walk(variant) == PLAIN[variant]


def test_router_never_sees_question_text_or_table_ids():
    names = set(make_view("router").to_dict())
    assert not names & {"question", "text", "table_id", "table_ids", "options", "tables", "jev_record", "jev"}


def test_first_route_and_invalid_input():
    assert route(make_view("router")) == actions.next_stage("interpret")
    assert route(make_view("router", input_ok=False)) == actions.stop("invalid_question")


def test_straight_does_not_branch_on_v1():
    world = {"v1": "mismatch", "v1_requirement_id": "d1", "interp_status": "ask", "split_requirement_id": "r1",
             "branch_requirement_id": "r1"}
    assert walk("straight", world, model_on=True) == PLAIN["straight"]


def test_parts_retries_the_model_inside_the_part():
    world = {"v1": "mismatch", "v1_requirement_id": "d1", "interp_status": "ask", "split_requirement_id": "r1"}
    assert walk("parts", world, model_on=True) == ["next:interpret", "part:retry_model", "next:select", "finish"]
    assert walk("parts", world, model_on=False) == PLAIN["parts"]  # model off: no model retry in either 4-a or 4-b


def test_parts_takes_the_next_alternative_inside_the_part():
    assert walk("parts", {"v2_dropped": True}) == ["next:interpret", "next:select", "part:next_alternative",
                                                   "finish"]


def _retries(path):
    kinds = ("retry_model", "next_alternative", "reinterpret")
    return [step.removeprefix("part:") for step in path if step.removeprefix("part:") in kinds]


@pytest.mark.parametrize("world, model_on", [
    ({"v1": "mismatch"}, True),
    ({"v1": "mismatch"}, False),
    ({"v2_dropped": True}, False),
    ({"v2_reinterpret": "merge", "v2_dropped": True}, False),
    ({"v1": "mismatch", "v2_reinterpret": "merge", "v2_dropped": True}, True),
])
def test_central_and_parts_make_the_same_retries(world, model_on):
    """4-a and 4-b: same retries in the same order and the same limits; only who chooses differs."""
    central, parts = walk("central", world, model_on=model_on), walk("parts", world, model_on=model_on)
    assert _retries(central) == _retries(parts)
    assert any(step.startswith("part:") for step in parts) == any(k != "reinterpret" for k in _retries(parts))
    assert not any(step.startswith("part:") for step in central)


def test_router_asks_split_once_then_returns_to_the_interpret_rules():
    world = {"v1": "mismatch", "v1_requirement_id": "d1"}
    assert walk("router", world) == ["next:interpret", "verify:interpret", "ask:split_requirements:d1",
                                     "next:select", "verify:select", "finish"]


def test_central_has_no_ask_on_v1_mismatch():
    assert walk("central", {"v1": "mismatch", "v1_requirement_id": "d1"}) == PLAIN["central"]


def test_router_retries_the_model_before_asking():
    world = {"v1": "mismatch", "v1_requirement_id": "d1"}
    assert walk("router", world, model_on=True) == [
        "next:interpret", "verify:interpret", "retry_model", "verify:interpret", "ask:split_requirements:d1",
        "next:select", "verify:select", "finish"]


def test_router_retry_that_fixes_v1_needs_no_ask():
    world = {"v1": ["mismatch", "ok"], "v1_requirement_id": ["d1", None]}
    assert walk("router", world, model_on=True) == [
        "next:interpret", "verify:interpret", "retry_model", "verify:interpret", "next:select", "verify:select",
        "finish"]


def test_central_retries_the_model_once_and_goes_on():
    world = {"v1": "mismatch", "v1_requirement_id": "d1"}
    assert walk("central", world, model_on=True) == [
        "next:interpret", "verify:interpret", "retry_model", "verify:interpret", "next:select", "verify:select",
        "finish"]


def test_router_interpretation_ask_kinds():
    """settle_requirements: status ask = judgement not certain (split); a branch is a requirement reason."""
    split = {"interp_status": "ask", "split_requirement_id": "r2"}
    assert walk("router", split)[2] == "ask:split_requirements:r2"
    branch = {"interp_status": "ok", "branch_requirement_id": "r1"}
    assert walk("router", branch) == ["next:interpret", "verify:interpret", "ask:branch:r1", "next:select",
                                      "verify:select", "finish"]


def test_split_and_branch_are_two_asks_each_once():
    world = {"interp_status": "ask", "split_requirement_id": "r1", "branch_requirement_id": "r1", "v1": "mismatch",
             "v1_requirement_id": "d1"}
    path = walk("router", world)
    assert path[:5] == ["next:interpret", "verify:interpret", "ask:split_requirements:r1", "ask:branch:r1",
                        "next:select"]


@pytest.mark.parametrize("variant", ["central", "router"])
def test_unsupported_finishes_after_verify(variant):
    assert walk(variant, {"interp_status": "unsupported"}) == ["next:interpret", "verify:interpret",
                                                                "finish:unsupported"]


def test_parts_unsupported_finishes_without_verify():
    assert walk("parts", {"interp_status": "unsupported"}) == ["next:interpret", "finish:unsupported"]


ALL_NO_TABLE = {"states": (("r1", "no_table"), ("r2", "no_table"))}
SOME_NO_TABLE = {"states": (("r1", "auto"), ("r2", "no_table"))}


@pytest.mark.parametrize("variant, last", [("straight", "finish"), ("parts", "finish:no_table"),
                                           ("central", "finish:no_table"), ("router", "finish:no_table")])
def test_all_requirements_without_a_table(variant, last):
    assert walk(variant, ALL_NO_TABLE)[-1] == last


@pytest.mark.parametrize("variant, last", [("central", "finish"), ("router", "partial"), ("parts", "finish")])
def test_some_requirements_without_a_table(variant, last):
    assert walk(variant, SOME_NO_TABLE)[-1] == last


def test_router_asks_each_confirm_requirement_once_in_order():
    world = {"states": (("r1", "confirm"), ("r2", "auto"), ("r3", "confirm"))}
    assert walk("router", world) == ["next:interpret", "verify:interpret", "next:select", "verify:select",
                                     "ask:choose_table:r1", "ask:choose_table:r3", "finish"]


def test_central_does_not_ask_on_confirm():
    assert walk("central", {"states": (("r1", "confirm"),)}) == PLAIN["central"]


def test_more_than_three_asks_go_on_with_defaults():
    world = {"states": tuple((f"r{i}", "confirm") for i in range(1, 6))}
    path = walk("router", world)
    assert path[4:] == ["ask:choose_table:r1", "ask:choose_table:r2", "ask:choose_table:r3",
                        "ask:choose_table:r4:skipped", "ask:choose_table:r5:skipped", "finish"]


@pytest.mark.parametrize("variant", ["central", "router"])
def test_reinterpret_once_on_the_v2_signal(variant):
    path = walk(variant, {"v2_reinterpret": "merge", "states": (("r1", "auto"), ("r2", "auto"))})
    assert path == ["next:interpret", "verify:interpret", "next:select", "verify:select", "reinterpret",
                    "verify:interpret", "next:select", "verify:select", "finish"]


def test_parts_reinterpret_once_on_the_v2_signal():
    assert walk("parts", {"v2_reinterpret": "merge"}) == ["next:interpret", "next:select", "reinterpret",
                                                          "next:select", "finish"]


def test_straight_never_reinterprets():
    assert walk("straight", {"v2_reinterpret": "merge", "v2_dropped": True}) == PLAIN["straight"]


@pytest.mark.parametrize("variant", ["central", "router"])
def test_next_alternative_once_before_the_confirm_ask(variant):
    world = {"v2_dropped": True, "states": (("r1", "confirm"),)}
    path = walk(variant, world)
    expected = ["next:interpret", "verify:interpret", "next:select", "verify:select", "next_alternative",
                "verify:select"]
    assert path[:6] == expected
    assert path[6:] == (["ask:choose_table:r1", "finish"] if variant == "router" else ["finish"])


def test_after_ask_the_rules_of_the_step_before_apply():
    view = make_view("router", stage=ASK_USER, resume_point=VERIFY_INTERPRET, v1="mismatch",
                     v1_requirement_id="d1", handled_asks=(("split_requirements", "d1"),))
    assert route(view) == actions.next_stage("select")


def test_unknown_variant_stops():
    assert route(make_view("other", stage="interpret")) == actions.stop("unknown_variant")
