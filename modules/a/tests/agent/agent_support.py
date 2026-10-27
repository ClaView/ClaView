"""Hand-made router views and a pure walk over route + guard (plan 04, 8: router path tests).

``walk`` plays the part results from a small ``world`` description and updates a ``RouterView`` the way the nodes
and the router node do (stage, verification results, handled asks, attempts, budget). In the variant parts, after
each part (whose check ran at its end) it first asks the part-end retry rule (``router.part_retry``) through the
guard, as the ``part_retry`` node does; those moves are labelled ``part:<action>``. It never builds a graph, so a
path test fails only on the rule tables or the guard.
"""

from __future__ import annotations

from dataclasses import replace

from claview_a.agent.actions import is_final, step_name
from claview_a.agent.guard import LIMITS, guard
from claview_a.agent.router import RouterView, part_retry, route
from claview_a.agent.state import ASK_USER, INTERPRET, SELECT, VERIFY_INTERPRET, VERIFY_SELECT

PART_END_VARIANTS = ("straight", "parts")  # V1/V2 run at the part ends in these wirings


def make_view(variant: str, **fields) -> RouterView:
    base = dict(variant=variant, stage=None, stage_status=None, input_ok=True, resume_point=None,
                interp_status=None, split_requirement_id=None, branch_requirement_id=None, certain=None,
                failed_conditions=(), v1=None, v1_requirement_id=None, v2=None, v2_reinterpret=None,
                v2_dropped=False, decision_states=(), handled_asks=(), attempts=(), budget_used=(), steps_used=0,
                model_on=False)
    base.update(fields)
    return RouterView(**base)


def label(action) -> str:
    args = action.args
    if action.kind in ("next", "verify"):
        return f"{action.kind}:{args['stage']}"
    if action.kind == "ask":
        return f"ask:{args['kind']}:{args.get('requirement_id')}" + (":skipped" if args.get("skipped") else "")
    if action.kind in ("finish", "stop"):
        return f"{action.kind}:{args.get('reason')}" if args.get("reason") else action.kind
    return action.kind


def _pick(value, n: int):
    """A world entry is one value for every run, or a list with one value per run (last one repeats)."""
    if isinstance(value, list):
        return value[min(n, len(value) - 1)]
    return value


def _bump(pairs: tuple, key: str) -> tuple:
    d = dict(pairs)
    d[key] = d.get(key, 0) + 1
    return tuple(sorted(d.items()))


def _apply(view: RouterView, final, world: dict, runs: dict) -> RouterView:
    variant = view.variant
    fields = {"steps_used": view.steps_used + 1, "attempts": _bump(view.attempts, step_name(final))}
    budget = view.budget_used
    if final.kind in ("reinterpret", "retry_model", "next_alternative"):
        budget = _bump(budget, final.kind)
    if final.kind == "ask" and not final.args.get("skipped"):
        budget = _bump(budget, "ask")
    fields["budget_used"] = budget
    if final.kind == "ask":
        fields["stage"] = ASK_USER
        fields["resume_point"] = view.resume_point if view.stage == ASK_USER else view.stage
        fields["handled_asks"] = view.handled_asks + ((final.args["kind"], final.args.get("requirement_id")),)
    else:
        fields["resume_point"] = None
    target = final.args.get("stage") if final.kind in ("next", "verify") else None
    if final.kind in ("retry_model", "reinterpret") or (final.kind == "next" and target == INTERPRET):
        n = runs["interpret"]
        runs["interpret"] += 1
        fields.update(stage=INTERPRET, interp_status=_pick(world.get("interp_status"), n),
                      split_requirement_id=_pick(world.get("split_requirement_id"), n),
                      branch_requirement_id=_pick(world.get("branch_requirement_id"), n), v1=None, v2=None,
                      v2_reinterpret=None, v2_dropped=False, decision_states=())
        if variant in PART_END_VARIANTS:
            fields.update(v1=_pick(world.get("v1", "ok"), n),
                          v1_requirement_id=_pick(world.get("v1_requirement_id"), n))
    elif final.kind == "next_alternative" or (final.kind == "next" and target == SELECT):
        n = runs["select"]
        runs["select"] += 1
        fields.update(stage=SELECT, v2=None, v2_reinterpret=None, v2_dropped=False,
                      decision_states=_pick(world.get("states", ()), n))
        if variant in PART_END_VARIANTS:
            fields.update(v2="ok", v2_reinterpret=_pick(world.get("v2_reinterpret"), n),
                          v2_dropped=_pick(world.get("v2_dropped", False), n))
    elif final.kind == "verify" and target == INTERPRET:
        n = runs["interpret"] - 1
        fields.update(stage=VERIFY_INTERPRET, v1=_pick(world.get("v1", "ok"), n),
                      v1_requirement_id=_pick(world.get("v1_requirement_id"), n))
    elif final.kind == "verify" and target == SELECT:
        n = runs["select"] - 1
        fields.update(stage=VERIFY_SELECT, v2="ok", v2_reinterpret=_pick(world.get("v2_reinterpret"), n),
                      v2_dropped=_pick(world.get("v2_dropped", False), n))
    return replace(view, **fields)


def walk(variant: str, world: dict | None = None, *, limits=LIMITS, model_on: bool = False, max_moves: int = 40):
    """Labels of the final actions until a final one. world keys: interp_status, split_requirement_id,
    branch_requirement_id, v1, v1_requirement_id, v2_reinterpret, v2_dropped, states (tuple of (requirement ID,
    state)); each a value or a list per run."""
    world = world or {}
    view = make_view(variant, model_on=model_on)
    runs = {"interpret": 0, "select": 0}
    out = []
    part_checked = True
    for _ in range(max_moves):
        if variant == "parts" and not part_checked:
            part_checked = True
            proposed = part_retry(view, view.stage)
            if proposed is not None:
                final, _reason = guard(proposed, view, limits)
                if final.kind == proposed.kind:
                    out.append(f"part:{label(final)}")
                    view = _apply(view, final, world, runs)
                    part_checked = False
                    continue
        final, _reason = guard(route(view), view, limits)
        out.append(label(final))
        if is_final(final):
            return out
        view = _apply(view, final, world, runs)
        part_checked = view.stage not in (INTERPRET, SELECT)
    raise AssertionError(f"no final action after {max_moves} moves: {out}")
