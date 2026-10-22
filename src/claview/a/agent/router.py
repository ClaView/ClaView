"""Router input view and the rule tables of the four variants (plan 04, 1-2, 1-3 and 3-3).

``route`` is a pure function of a read-only ``RouterView``. The view carries step names, statuses, check results,
requirement IDs with their decision states, the asks already handled, attempt and budget counts and the variant;
never the question text, table IDs or ask options. Limits are applied afterwards by ``guard.guard``.

The retries of plan 04 3-3 that stay inside a part (model retry after V1, next alternative after V2) are one
function, ``part_retry``: the router calls it in central and router (4-a: the router chooses), the ``part_retry``
node calls it at the part ends in parts (4-b: the part chooses). Both go through the same guard and count in the same
``control.budget``. Reinterpretation crosses parts and is always the router's.

Rule tables (first matching line wins; ``stage`` is the step that ended, or the step before the ask after
``ask_user``):
- straight: interpret -> next(select); select -> finish. V1/V2 run at the part ends and are only recorded.
- parts (part retries already done by the part): interpret, unsupported -> finish(unsupported); else next(select).
  select, reinterpretation signal and none yet -> reinterpret; all no_table -> finish(no_table); else finish.
- central and router (plan 04, 1-3 table; the ask and partial lines only in router):
  interpret, V1 not run -> verify(interpret); unsupported -> finish(unsupported); part retry (model retry) ->
  retry_model; V1 mismatch or interpretation status ask, not yet asked -> ask(split_requirements); a requirement with
  a ``branch`` reason, not yet asked -> ask(branch); else next(select).
  select, V2 not run -> verify(select); reinterpretation signal, none yet -> reinterpret; part retry -> next_alternative;
  a confirm requirement not yet asked -> ask(choose_table, first such); all no_table -> finish(no_table); some
  no_table -> partial; else finish.

What the interpretation says (``interpret/settle.py``): status ``ask`` means the judgement is not certain (the router
may ask to split requirements); several official candidates for a term are a ``branch`` reason of that requirement,
whatever the interpretation status.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from . import actions
from .actions import Action
from .state import ASK_USER, INTERPRET, SELECT, VERIFY_INTERPRET, VERIFY_SELECT

BRANCH_REASON = "branch"  # requirement reason set by settle_requirements
NO_TABLE = "no_table"
CONFIRM = "confirm"
INTERPRET_STEPS = (INTERPRET, VERIFY_INTERPRET)
SELECT_STEPS = (SELECT, VERIFY_SELECT)


@dataclass(frozen=True)
class RouterView:
    variant: str
    stage: str | None  # step that just ended; None right after intake
    stage_status: str | None
    input_ok: bool
    resume_point: str | None  # step whose rules apply again after ask_user
    interp_status: str | None  # analysis.interpretation.status
    split_requirement_id: str | None  # ask target when the interpretation status is ask
    branch_requirement_id: str | None  # first requirement with a branch reason
    certain: bool | None
    failed_conditions: tuple[str, ...]
    v1: str | None  # V1 status; None = not run on the current analysis
    v1_requirement_id: str | None  # first requirement a V1 signal points at
    v2: str | None  # V2 status; None = not run on the current selections
    v2_reinterpret: str | None  # merge | split | None
    v2_dropped: bool  # a table failed the evidence recheck
    decision_states: tuple[tuple[str, str], ...]  # (requirement ID, auto | confirm | no_table)
    handled_asks: tuple[tuple[str, str | None], ...]  # asks already handled (asked or skipped)
    attempts: tuple[tuple[str, int], ...]
    budget_used: tuple[tuple[str, int], ...]
    steps_used: int
    model_on: bool

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("attempts", "budget_used"):
            out[key] = dict(getattr(self, key))
        out["decision_states"] = dict(self.decision_states)
        out["handled_asks"] = [list(pair) for pair in self.handled_asks]
        out["failed_conditions"] = list(self.failed_conditions)
        return out

    def used(self, counter: str) -> int:
        return dict(self.budget_used).get(counter, 0)

    def attempts_of(self, step: str) -> int:
        return dict(self.attempts).get(step, 0)

    def handled(self, kind: str, requirement_id: str | None) -> bool:
        return (kind, requirement_id) in self.handled_asks

    def asks_of(self, kind: str, requirement_id: str | None) -> int:
        return sum(1 for pair in self.handled_asks if pair == (kind, requirement_id))

    @property
    def effective_stage(self) -> str | None:
        """The step whose rules apply: the step before the ask while an ask has just been handled."""
        return self.resume_point if self.stage == ASK_USER else self.stage


# ---------------------------------------------------------------- view


def _ask_targets(interp: dict | None) -> tuple[bool, str | None, str | None]:
    if not interp:
        return False, None, None
    requirements = interp.get("requirements") or []
    split = interp.get("status") == "ask"
    split_target = next((r["id"] for r in requirements if r.get("status") != "ok"), None)
    branch_target = next((r["id"] for r in requirements if BRANCH_REASON in (r.get("reasons") or [])), None)
    return split, split_target, branch_target


def _decision_states(state: dict) -> tuple[tuple[str, str], ...]:
    checked = (state.get("verification") or {}).get("selection") or {}
    if checked.get("states"):
        return tuple((rid, s) for rid, s in checked["states"].items())
    return tuple((s["requirement_id"], s["status"]) for s in state.get("selections") or [] if s.get("status"))


def make_view(state: dict) -> RouterView:
    control = state["control"]
    analysis = state.get("analysis") or {}
    interp = analysis.get("interpretation")
    judgement = analysis.get("judgement") or {}
    verification = state.get("verification") or {}
    v1 = verification.get("interpretation")
    v2 = verification.get("selection")
    parts = (state.get("a_outcome") or {}).get("parts") or {}
    stage = control["stage"]
    if stage in (INTERPRET, SELECT):
        stage_status = (parts.get(stage) or {}).get("status")
    elif stage == VERIFY_INTERPRET:
        stage_status = v1.get("status") if v1 else None
    elif stage == VERIFY_SELECT:
        stage_status = v2.get("status") if v2 else None
    elif stage == ASK_USER:
        asks = [d for d in control["decisions"] if d.get("type") == "ask"]
        stage_status = asks[-1]["mode"] if asks else None
    else:
        stage_status = None
    handled = tuple((d["kind"], d.get("requirement_id")) for d in control["decisions"] if d.get("type") == "ask")
    split, split_target, branch_target = _ask_targets(interp)
    return RouterView(
        variant=state["variant"],
        stage=stage,
        stage_status=stage_status,
        input_ok=(parts.get("intake") or {}).get("status") == "ok",
        resume_point=control["resume_point"],
        interp_status=interp.get("status") if interp else None,
        split_requirement_id=split_target if split else None,
        branch_requirement_id=branch_target,
        certain=judgement.get("certain") if judgement else None,
        failed_conditions=tuple(judgement.get("failed_conditions") or ()),
        v1=v1.get("status") if v1 else None,
        v1_requirement_id=v1.get("requirement_id") if v1 else None,
        v2=v2.get("status") if v2 else None,
        v2_reinterpret=v2.get("reinterpret_signal") if v2 else None,
        v2_dropped=bool(v2.get("dropped_tables")) if v2 else False,
        decision_states=_decision_states(state),
        handled_asks=handled,
        attempts=tuple(sorted(control["attempts"].items())),
        budget_used=tuple(sorted(control["budget"].items())),
        steps_used=control["budget"]["steps"],
        model_on=(parts.get(INTERPRET) or {}).get("model") == "on",
    )


# ---------------------------------------------------------------- rules


def _all_no_table(view: RouterView) -> bool:
    states = [s for _, s in view.decision_states]
    return bool(states) and all(s == NO_TABLE for s in states)


def part_retry(view: RouterView, stage: str | None = None) -> Action | None:
    """The 3-3 retry a part may do on its own after its check, or None. Same rule for 4-a (router) and 4-b (part)."""
    stage = view.effective_stage if stage is None else stage
    if stage in INTERPRET_STEPS:
        if view.interp_status == "unsupported":
            return None
        if view.v1 == "mismatch" and view.model_on and view.used("retry_model") == 0:
            return actions.retry_model()
    elif stage in SELECT_STEPS:
        if view.v2_reinterpret and view.used("reinterpret") == 0:
            return None  # reinterpretation first, and it is the router's
        if view.v2_dropped and view.used("next_alternative") == 0:
            return actions.next_alternative()
    return None


def _split_wanted(view: RouterView) -> tuple[bool, str | None]:
    if view.interp_status == "ask":
        return True, view.split_requirement_id
    if view.v1 == "mismatch":
        return True, view.v1_requirement_id
    return False, None


def _straight(view: RouterView, stage: str | None) -> Action:
    if stage == INTERPRET:
        return actions.next_stage(SELECT)
    if stage == SELECT:
        return actions.finish()
    return actions.stop("unexpected_stage")


def _parts(view: RouterView, stage: str | None) -> Action:
    if stage == INTERPRET:
        if view.interp_status == "unsupported":
            return actions.finish("unsupported")
        return actions.next_stage(SELECT)
    if stage == SELECT:
        if view.v2_reinterpret and view.used("reinterpret") == 0:
            return actions.reinterpret()
        if _all_no_table(view):
            return actions.finish(NO_TABLE)
        return actions.finish()
    return actions.stop("unexpected_stage")


def _after_interpret(view: RouterView, stage: str, branches: bool) -> Action:
    if view.interp_status == "unsupported":
        return actions.finish("unsupported")
    retry = part_retry(view, stage)
    if retry is not None:
        return retry
    if branches:
        wanted, target = _split_wanted(view)
        if wanted and not view.handled("split_requirements", target):
            return actions.ask("split_requirements", target)
        branch = view.branch_requirement_id
        if branch is not None and not view.handled("branch", branch):
            return actions.ask("branch", branch)
    return actions.next_stage(SELECT)


def _after_select(view: RouterView, stage: str, branches: bool) -> Action:
    if view.v2_reinterpret and view.used("reinterpret") == 0:
        return actions.reinterpret()
    retry = part_retry(view, stage)
    if retry is not None:
        return retry
    if branches:
        for rid, decision in view.decision_states:
            if decision == CONFIRM and not view.handled("choose_table", rid):
                return actions.ask("choose_table", rid)
    if _all_no_table(view):
        return actions.finish(NO_TABLE)
    if branches and any(s == NO_TABLE for _, s in view.decision_states):
        return actions.partial()
    return actions.finish()


def _central(view: RouterView, stage: str | None, branches: bool = False) -> Action:
    if stage == INTERPRET and view.v1 is None:
        return actions.verify(INTERPRET)
    if stage in INTERPRET_STEPS:
        return _after_interpret(view, stage, branches)
    if stage == SELECT and view.v2 is None:
        return actions.verify(SELECT)
    if stage in SELECT_STEPS:
        return _after_select(view, stage, branches)
    return actions.stop("unexpected_stage")


def _router(view: RouterView, stage: str | None) -> Action:
    return _central(view, stage, branches=True)


RULES = {"straight": _straight, "router": _router, "central": _central, "parts": _parts}


def route(view: RouterView) -> Action:
    if not view.input_ok:
        return actions.stop("invalid_question")
    if view.stage is None:
        return actions.next_stage(INTERPRET)
    rules = RULES.get(view.variant)
    if rules is None:
        return actions.stop("unknown_variant")
    return rules(view, view.effective_stage)
