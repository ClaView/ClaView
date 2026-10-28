"""Action guard (plan 04, 1-3; same place and order as plan 02, 4-4). Runs after every proposal, the router's and
the part-end retry's (``part_retry`` node), so a proposal (now code, later perhaps a model) never bypasses a limit.

Order of the checks:
1. unknown action or bad arguments -> stop(invalid_action)
2. a ``next`` that goes back (to the step that ended or an earlier one) is a retry and is counted as one: to
   interpret from interpret = retry_model, to interpret from select = reinterpret, to select from select =
   next_alternative (guard reason ``back_transition`` when it passes)
3. action off in this variant -> stop(action_not_enabled)
4. transition not allowed from the step that ended (``TRANSITIONS``) -> stop(invalid_transition)
5. retry_model while the model is off -> stop(model_off) (neither 4-a nor 4-b retries the model then)
6. limited action used up (reinterpret, retry_model, next_alternative; ``control.budget``) -> stop(<kind>_limit)
7. step run more often than its retries allow (``stage_attempts``) -> stop(stage_attempt_limit)
8. ask over its limit ((kind, requirement) once, whole question ``ask_total``) -> not a stop: the same ask marked
   ``skipped``, so ``ask_user`` applies the default and records ``ask_skipped``
9. a non-final action with the step budget used up -> stop(step_limit)
"""

from __future__ import annotations

from typing import Any

from . import actions
from .actions import Action
from .router import INTERPRET_STEPS, SELECT_STEPS, RouterView
from .state import INTERPRET, SELECT, VERIFY_INTERPRET, VERIFY_SELECT

_RETRY = {"reinterpret": 1, "retry_model": 1, "next_alternative": 1}
LIMITS: dict[str, Any] = {
    **_RETRY,
    "ask_per_target": 1,  # per (ask kind, requirement ID)
    "ask_total": 3,  # real asks in one question
    # Runs of a step: the first one plus the retries that lead back to it.
    "stage_attempts": {
        INTERPRET: 1 + _RETRY["retry_model"] + _RETRY["reinterpret"],
        VERIFY_INTERPRET: 1 + _RETRY["retry_model"] + _RETRY["reinterpret"],
        SELECT: 1 + _RETRY["next_alternative"] + _RETRY["reinterpret"],
        VERIFY_SELECT: 1 + _RETRY["next_alternative"] + _RETRY["reinterpret"],
    },
    # Longest router path: interpret, verify, retry, verify, 3 asks, select, verify, reinterpret, interpret's verify,
    # select, verify, next_alternative, verify = 16 dispatches; the rest is margin.
    "max_steps": 20,
}

INVALID_ACTION = "invalid_action"
INVALID_TRANSITION = "invalid_transition"
ACTION_NOT_ENABLED = "action_not_enabled"
BACK_TRANSITION = "back_transition"
MODEL_OFF = "model_off"
STAGE_ATTEMPT_LIMIT = "stage_attempt_limit"
ASK_SKIPPED = "ask_skipped"
STEP_LIMIT = "step_limit"

_ORDER = {INTERPRET: 0, SELECT: 1}
_INTERPRET_ASKS = ("split_requirements", "branch")

# Part of the step that ended -> non-final action kinds allowed from it (finish and stop are always allowed).
TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"next"}),
    INTERPRET: frozenset({"next", "verify", "retry_model", "ask"}),
    SELECT: frozenset({"verify", "reinterpret", "next_alternative", "ask", "partial"}),
}


def _part(step: str | None) -> str | None:
    if step in INTERPRET_STEPS:
        return INTERPRET
    if step in SELECT_STEPS:
        return SELECT
    return None


def _valid(action: Action) -> bool:
    if not isinstance(action, Action) or action.kind not in actions.ACTIONS:
        return False
    if action.kind in ("next", "verify") and action.args.get("stage") not in actions.VERIFY_STAGES:
        return False
    if action.kind == "ask" and action.args.get("kind") not in actions.ASK_KINDS:
        return False
    return True


def _back_as_retry(action: Action, part: str | None) -> Action | None:
    """The retry a ``next`` stands for when it goes back, else None."""
    if action.kind != "next" or part is None:
        return None
    target = action.args["stage"]
    if _ORDER[target] > _ORDER[part]:
        return None
    if target == INTERPRET:
        return actions.retry_model() if part == INTERPRET else actions.reinterpret()
    return actions.next_alternative()


def _transition_ok(action: Action, view: RouterView) -> bool:
    if action.kind in ("finish", "stop"):
        return True
    step = view.effective_stage
    part = _part(step)
    if action.kind not in TRANSITIONS.get(part, frozenset()):
        return False
    if action.kind == "next":
        target = action.args["stage"]
        return _ORDER[target] == (_ORDER[part] + 1 if part else 0)
    if action.kind == "verify":
        return action.args["stage"] == part and step == part
    if action.kind == "ask":
        return (action.args["kind"] in _INTERPRET_ASKS) == (part == INTERPRET)
    return True


def guard(action: Action, view: RouterView, limits: dict[str, Any] = LIMITS) -> tuple[Action, str | None]:
    """Return (final action, guard reason). The reason is None when the proposal passes unchanged."""
    if not _valid(action):
        return actions.stop(INVALID_ACTION), INVALID_ACTION
    reason = None
    retry = _back_as_retry(action, _part(view.effective_stage))
    if retry is not None:
        action, reason = retry, BACK_TRANSITION
    if not actions.is_enabled(action.kind, view.variant):
        return actions.stop(ACTION_NOT_ENABLED), ACTION_NOT_ENABLED
    if not _transition_ok(action, view):
        return actions.stop(INVALID_TRANSITION), INVALID_TRANSITION
    if action.kind == "retry_model" and not view.model_on:
        return actions.stop(MODEL_OFF), MODEL_OFF
    if action.kind in actions.LIMITED and view.used(action.kind) >= limits[action.kind]:
        limit = f"{action.kind}_limit"
        return actions.stop(limit), limit
    step = actions.step_name(action)
    allowed = limits["stage_attempts"].get(step) if step else None
    if allowed is not None and view.attempts_of(step) >= allowed:
        return actions.stop(STAGE_ATTEMPT_LIMIT), STAGE_ATTEMPT_LIMIT
    if action.kind == "ask" and not action.args.get("skipped"):
        kind, target = action.args["kind"], action.args.get("requirement_id")
        if view.asks_of(kind, target) >= limits["ask_per_target"] or view.used("ask") >= limits["ask_total"]:
            action, reason = actions.ask(kind, target, skipped=True), ASK_SKIPPED
    if not actions.is_final(action) and view.steps_used >= limits["max_steps"]:
        return actions.stop(STEP_LIMIT), STEP_LIMIT
    return action, reason
