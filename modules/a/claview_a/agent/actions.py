"""Router actions (plan 04, 1-3) and which actions each variant switches on.

The router only picks the action kind and its target (stage, ask kind, requirement ID). Options of an ask are made
by the ``ask_user`` node from the state, never by the router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import INTERPRET, SELECT

ASK_KINDS = ("split_requirements", "branch", "choose_table")
VERIFY_STAGES = (INTERPRET, SELECT)

# Every action name with its arguments.
ACTIONS: dict[str, tuple[str, ...]] = {
    "next": ("stage",),  # interpret | select
    "finish": ("reason",),  # None, unsupported, no_table, ...
    "stop": ("reason",),  # guard or failure reason
    "ask": ("kind", "requirement_id", "skipped"),  # skipped: set only by the guard (over the ask limit)
    "partial": (),  # go on without the requirements that have no table (ends the A run here)
    "reinterpret": (),  # settle_requirements (2.7) once more
    "retry_model": (),  # the interpretation model slot (2.6) once more
    "next_alternative": (),  # the next selection alternative once
    "verify": ("stage",),  # interpret | select
}

# Variant -> switched-on actions (plan 04, 1-2 and 3-3). Everything else is off and the guard turns it into stop.
# straight: always the next stage, V1/V2 only recorded at the part ends.
# router: central verification plus the branches (ask, partial).
# central: central verification and the retries of 3-3 chosen by the router, no branches.
# parts: V1/V2 at the part ends; the same retries as central, chosen at the part end (``part_retry`` node) instead
#        of by the router; reinterpret crosses parts and stays with the router.
_ALWAYS = frozenset({"next", "finish", "stop"})
_RETRIES = frozenset({"reinterpret", "retry_model", "next_alternative"})
ENABLED: dict[str, frozenset[str]] = {
    "straight": _ALWAYS,
    "router": _ALWAYS | _RETRIES | {"ask", "partial", "verify"},
    "central": _ALWAYS | _RETRIES | {"verify"},
    "parts": _ALWAYS | _RETRIES,
}

# Actions with a use limit counted in control.budget (guard.LIMITS holds the numbers).
LIMITED = ("reinterpret", "retry_model", "next_alternative")

# Node each non-final action runs next; finish, stop and partial go to respond.
_TARGET = {"retry_model": INTERPRET, "reinterpret": INTERPRET, "next_alternative": SELECT, "verify": "verify",
           "ask": "ask_user"}


@dataclass(frozen=True)
class Action:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "args": dict(self.args)}


def is_enabled(kind: str, variant: str) -> bool:
    return kind in ENABLED.get(variant, frozenset())


def next_stage(stage: str) -> Action:
    return Action("next", {"stage": stage})


def finish(reason: str | None = None) -> Action:
    return Action("finish", {"reason": reason})


def stop(reason: str) -> Action:
    return Action("stop", {"reason": reason})


def ask(kind: str, requirement_id: str | None, skipped: bool = False) -> Action:
    args: dict[str, Any] = {"kind": kind, "requirement_id": requirement_id}
    if skipped:
        args["skipped"] = True
    return Action("ask", args)


def partial() -> Action:
    return Action("partial")


def reinterpret() -> Action:
    return Action("reinterpret")


def retry_model() -> Action:
    return Action("retry_model")


def next_alternative() -> Action:
    return Action("next_alternative")


def verify(stage: str) -> Action:
    return Action("verify", {"stage": stage})


def is_final(action: Action) -> bool:
    return action.kind in ("finish", "stop", "partial")


def dispatched_node(action: Action) -> str | None:
    """Node this action runs next, or None when it ends the run (respond)."""
    if action.kind == "next":
        return action.args.get("stage")
    return _TARGET.get(action.kind)


def step_name(action: Action) -> str | None:
    """control.stage value once the dispatched node has run (verify is named by the stage it checks)."""
    if action.kind == "verify":
        return f"verify_{action.args.get('stage')}"
    return dispatched_node(action)
