"""Guard limits (plan 04, 1-3): retries once each, asks once per (kind, requirement) and three per question with a
default instead of a stop, allowed transitions (a backward ``next`` is a retry and counts as one), runs per step,
the step budget, and actions that are off in a variant."""

from __future__ import annotations

import pytest
from agent_support import make_view

from claview_a_legacy.agent import actions
from claview_a_legacy.agent.actions import ACTIONS, ENABLED, Action
from claview_a_legacy.agent.guard import LIMITS, guard
from claview_a_legacy.agent.state import ASK_USER, INTERPRET, SELECT, VARIANTS, VERIFY_INTERPRET, VERIFY_SELECT

# step that may start each retry
RETRY_FROM = {"retry_model": VERIFY_INTERPRET, "reinterpret": VERIFY_SELECT, "next_alternative": VERIFY_SELECT}


@pytest.mark.parametrize("kind", ["reinterpret", "retry_model", "next_alternative"])
def test_each_retry_once(kind):
    action = Action(kind)
    view = make_view("central", stage=RETRY_FROM[kind], model_on=True)
    assert guard(action, view) == (action, None)
    used = make_view("central", stage=RETRY_FROM[kind], model_on=True, budget_used=((kind, 1),))
    assert guard(action, used) == (actions.stop(f"{kind}_limit"), f"{kind}_limit")


def test_limits_hold_the_planned_numbers():
    assert {k: LIMITS[k] for k in ("reinterpret", "retry_model", "next_alternative", "ask_per_target",
                                   "ask_total")} == {"reinterpret": 1, "retry_model": 1, "next_alternative": 1,
                                                     "ask_per_target": 1, "ask_total": 3}
    assert LIMITS["stage_attempts"] == {INTERPRET: 3, VERIFY_INTERPRET: 3, SELECT: 3, VERIFY_SELECT: 3}


def test_model_retry_needs_the_model():
    assert guard(actions.retry_model(), make_view("central", stage=VERIFY_INTERPRET)) == (actions.stop("model_off"),
                                                                                           "model_off")
    assert guard(actions.retry_model(), make_view("parts", stage=INTERPRET))[1] == "model_off"


@pytest.mark.parametrize("stage, target, as_kind", [
    (INTERPRET, INTERPRET, "retry_model"), (VERIFY_INTERPRET, INTERPRET, "retry_model"),
    (SELECT, INTERPRET, "reinterpret"), (VERIFY_SELECT, INTERPRET, "reinterpret"),
    (VERIFY_SELECT, SELECT, "next_alternative"), (SELECT, SELECT, "next_alternative"),
])
def test_backward_next_is_counted_as_a_retry(stage, target, as_kind):
    fresh = make_view("central", stage=stage, model_on=True)
    assert guard(actions.next_stage(target), fresh) == (Action(as_kind), "back_transition")
    used = make_view("central", stage=stage, model_on=True, budget_used=((as_kind, 1),))
    assert guard(actions.next_stage(target), used) == (actions.stop(f"{as_kind}_limit"), f"{as_kind}_limit")


def test_backward_next_is_refused_where_retries_are_off():
    view = make_view("straight", stage=SELECT)
    assert guard(actions.next_stage(INTERPRET), view) == (actions.stop("action_not_enabled"), "action_not_enabled")
    after_ask = make_view("router", stage=ASK_USER, resume_point=VERIFY_SELECT, budget_used=(("reinterpret", 1),))
    assert guard(actions.next_stage(INTERPRET), after_ask)[1] == "reinterpret_limit"


@pytest.mark.parametrize("stage, action", [
    (None, actions.next_stage(SELECT)),
    (None, actions.verify(INTERPRET)),
    (INTERPRET, actions.verify(SELECT)),
    (VERIFY_INTERPRET, actions.verify(INTERPRET)),
    (INTERPRET, actions.next_alternative()),
    (VERIFY_INTERPRET, actions.reinterpret()),
    (VERIFY_INTERPRET, actions.partial()),
    (VERIFY_INTERPRET, actions.ask("choose_table", "r1")),
    (VERIFY_SELECT, actions.ask("split_requirements", "r1")),
    (VERIFY_SELECT, actions.retry_model()),
])
def test_transitions_not_allowed_from_the_step_that_ended(stage, action):
    view = make_view("router", stage=stage, model_on=True)
    assert guard(action, view) == (actions.stop("invalid_transition"), "invalid_transition")


def test_allowed_forward_transitions_pass():
    for stage, action in [(None, actions.next_stage(INTERPRET)), (INTERPRET, actions.verify(INTERPRET)),
                          (VERIFY_INTERPRET, actions.next_stage(SELECT)), (SELECT, actions.verify(SELECT)),
                          (VERIFY_SELECT, actions.partial()), (VERIFY_INTERPRET, actions.finish("unsupported"))]:
        assert guard(action, make_view("router", stage=stage)) == (action, None)


def test_runs_per_step_are_limited():
    view = make_view("central", stage=VERIFY_INTERPRET, attempts=((SELECT, 3),))
    assert guard(actions.next_stage(SELECT), view) == (actions.stop("stage_attempt_limit"), "stage_attempt_limit")
    rerun = make_view("central", stage=INTERPRET, attempts=((VERIFY_INTERPRET, 3),))
    assert guard(actions.verify(INTERPRET), rerun)[1] == "stage_attempt_limit"


def test_same_ask_twice_goes_on_with_the_default():
    action = actions.ask("choose_table", "r1")
    view = make_view("router", stage=VERIFY_SELECT, handled_asks=(("choose_table", "r1"),),
                     budget_used=(("ask", 1),))
    final, reason = guard(action, view)
    assert final == actions.ask("choose_table", "r1", skipped=True) and reason == "ask_skipped"
    other = guard(actions.ask("choose_table", "r2"), view)
    assert other == (actions.ask("choose_table", "r2"), None)


def test_fourth_ask_of_a_question_goes_on_with_the_default():
    view = make_view("router", stage=VERIFY_INTERPRET, budget_used=(("ask", 3),))
    assert guard(actions.ask("branch", "r9"), view) == (actions.ask("branch", "r9", skipped=True), "ask_skipped")
    below = make_view("router", stage=VERIFY_INTERPRET, budget_used=(("ask", 2),))
    assert guard(actions.ask("branch", "r9"), below) == (actions.ask("branch", "r9"), None)


def test_step_budget_stops_moves_but_not_endings():
    view = make_view("router", stage=VERIFY_INTERPRET, steps_used=LIMITS["max_steps"])
    assert guard(actions.next_stage("select"), view) == (actions.stop("step_limit"), "step_limit")
    assert guard(actions.ask("branch", "r1"), view) == (actions.stop("step_limit"), "step_limit")
    for final in (actions.finish(), actions.finish("no_table"), actions.stop("x")):
        assert guard(final, view) == (final, None)
    at_select = make_view("router", stage=VERIFY_SELECT, steps_used=LIMITS["max_steps"])
    assert guard(actions.partial(), at_select) == (actions.partial(), None)


def test_off_actions_stop():
    assert guard(actions.ask("branch", "r1"), make_view("central")) == (actions.stop("action_not_enabled"),
                                                                        "action_not_enabled")
    assert guard(actions.verify("interpret"), make_view("straight"))[1] == "action_not_enabled"
    assert guard(actions.partial(), make_view("parts"))[1] == "action_not_enabled"
    assert guard(actions.retry_model(), make_view("straight"))[1] == "action_not_enabled"


def test_enabled_table_covers_every_variant_and_only_known_actions():
    assert set(ENABLED) == set(VARIANTS)
    for variant, kinds in ENABLED.items():
        assert kinds <= set(ACTIONS) and {"next", "finish", "stop"} <= kinds
    assert ENABLED["router"] == set(ACTIONS)
    retries = {"reinterpret", "retry_model", "next_alternative"}
    assert retries <= ENABLED["central"] and retries <= ENABLED["parts"]  # 4-a and 4-b: same retries
    assert not retries & ENABLED["straight"]


@pytest.mark.parametrize("action", [Action("jump"), Action("next", {"stage": "fetch"}),
                                    Action("verify", {"stage": None}), Action("ask", {"kind": "anything"}),
                                    "next"])
def test_invalid_actions_stop(action):
    assert guard(action, make_view("router")) == (actions.stop("invalid_action"), "invalid_action")
