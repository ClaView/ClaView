"""Comparing undecided readings by their tables (plan 07 S4). Model off: no call.

When the interpretation did not settle the question and has reading candidates, table selection runs once per
reading (at most ``MAX_READINGS``, the interpretation's own reading first) and the chosen tables are compared:
all the same -> ``same`` (the question can go on, confirm); different -> ``differ`` (the table is asked, question
route in ``agent/question_route.py``); no candidate -> ``none``.

Where the readings come from (adapter, ``reading_candidates``). The interpretation exposes no reading candidates as
analyses of their own yet. Until it does, this adapter rebuilds them from what the interpretation already makes
without a model: the decision points of 2.6 (``interpret.model_step.prepare``, the dry run of the model step) and,
for one option of one point at a time, the same apply-and-recheck path the model's choices take
(``overrides_of`` + ``recheck``). Only points that choose between readings of the question are used (``KINDS``:
catalog name, list reading, apposition, modifier scope, condition attribution); word points (leftover words) and
options that ask (``ASK_EFFECTS``) or keep the draft are not readings. A reading whose recheck adds a failure the
interpretation's own judgement did not have (a contract shape problem, a new certainty failure, a V-check) is left
out. What the interpretation should give later instead: the reading candidates themselves (e.g. a list of
``{point, option, interpretation}`` next to ``judgement.model``), so that selection no longer calls 2.6 internals.

Nothing here names a table, case or question.

Plan 15 step 5: an adopted model structure supplies its verified alternative directly. Only the legacy rule
path uses the dry-run adapter described above; the model path never prepares old 2.6 options.
"""

from __future__ import annotations
import copy

from . import model_refs

MAX_READINGS = 4  # readings compared: the interpretation's own and at most 3 option combinations (plan 07 r5 Q4)
KINDS = ("name", "reading", "appositive", "scope", "attribution")


def _undecided(analysis: dict) -> bool:
    judgement = analysis.get("judgement") or {}
    interp = analysis.get("interpretation") or {}
    return bool(interp.get("requirements")) and (judgement.get("certain") is False
                                                 or interp.get("status") in ("ask", "partial"))


def reading_candidates(analysis: dict, layer, limit: int = MAX_READINGS - 1) -> list[dict]:
    """Other readings of an undecided question: [{point, kind, option, effect, text, analysis}] (adapter; module
    docstring). Empty when the question is settled, has no judgement or no layer."""
    judgement = analysis.get("judgement")
    if layer is None or not judgement or not _undecided(analysis) or limit <= 0:
        return []
    if model_refs.adopted(analysis):
        record = analysis["decomposition"]
        alt = record.get("alternative")
        if alt is None:
            return []
        variant = copy.deepcopy(analysis)
        for key in ("fragments", "terms", "judgement", "interpretation", "ref_map"):
            variant[key] = copy.deepcopy(alt[key])
        target = variant["decomposition"]
        for key in ("reading_id", "requirement_ids", "bindings", "assumption_seeds", "condition_fields",
                    "inferred", "rule_checks", "selection_refs", "certainty"):
            target[key] = copy.deepcopy(alt[key])
        # Optional reading-local fields must not survive from the primary. Old
        # saved alternatives have no structure; absence is not an inherited one.
        for key in ("structure", "structure_reason"):
            target.pop(key, None)
            if key in alt:
                target[key] = copy.deepcopy(alt[key])
        target["alternative"] = None
        target["verify"]["issues"] = copy.deepcopy(record["verify"]["alternative_issues"])
        target["verify"]["alternative"] = "absent"
        target["verify"]["alternative_issues"] = []
        from ..contract.check import scope_index
        target["verify"]["scopes"] = scope_index(target["verify"]["issues"])
        target["verify"]["assumption_seed"] = bool(target["verify"]["issues"])
        return [{"point": "decomposition", "kind": "reading", "option": alt["reading_id"], "effect": "reading",
                 "text": " ".join(r["quote"] for r in alt["interpretation"]["requirements"]), "analysis": variant}]
    from ..interpret import model_step as ms  # read-only use of the 2.6 dry run (no model, no call)

    prep = ms.prepare(analysis["text"], analysis["conditions"], analysis["fragments"], analysis["terms"],
                      {**judgement, "certain": False}, layer)
    if prep.ctx is None:
        return []
    known = set(judgement.get("failed_conditions") or [])
    points = [p for p in prep.points if p.kind in KINDS]
    singles = [(p, o) for p in points for o in p.options if o.effect not in ms.ASK_EFFECTS and o.effect != "keep"]
    # plan 07 r5 Q4: combinations of the code-made options, one option per point; one point first, then two points
    combos: list[list[tuple]] = [[x] for x in singles]
    combos += [[a, b] for i, a in enumerate(singles) for b in singles[i + 1:] if a[0].id != b[0].id]
    out: list[dict] = []
    seen: set[str] = set()
    for combo in combos:
        ov, asks = ms.overrides_of(prep.ctx, [p for p, _o in combo], {p.id: o.id for p, o in combo})
        if asks:
            continue
        chk = ms.recheck(prep.ctx, ov)
        if set(chk.failures) - known:
            continue
        interp = chk.interpretation
        key = repr([((r.get("indicator") or {}).get("name"), (r.get("indicator") or {}).get("quote"),
                     sorted(r["condition_ids"]), [t.get("name") for t in r["targets"]])
                    for r in interp["requirements"]])
        if key in seen:
            continue
        seen.add(key)
        variant = {**analysis, "judgement": {**chk.judgement, "certain": judgement.get("certain", False)},
                   "interpretation": interp}
        out.append({"point": "+".join(p.id for p, _o in combo), "kind": "+".join(p.kind for p, _o in combo),
                    "option": "+".join(o.id for _p, o in combo), "effect": "+".join(o.effect for _p, o in combo),
                    "text": " / ".join(o.text for _p, o in combo), "analysis": variant})
        if len(out) >= limit:
            return out
    return out


def option_yields(analysis: dict, prep, point, option, layer, as_of: str | None = None,
                  policy: str | None = None, *, candidate_policy: str = "legacy",
                  selection_mode: str = "legacy") -> bool:
    """Plan 07 r5 Q7: the selection gives a table when the interpretation takes ``option`` of ``point`` (the same
    apply-and-recheck path as ``reading_candidates``; model off). An option that keeps the draft or asks gives no
    new reading: False."""
    from ..interpret import model_step as ms
    from .graph import select_tables

    if prep is None or prep.ctx is None or option.effect in ms.ASK_EFFECTS or option.effect == "keep":
        return False
    ov, asks = ms.overrides_of(prep.ctx, [point], {point.id: option.id})
    if asks:
        return False
    chk = ms.recheck(prep.ctx, ov)
    variant = {**analysis, "judgement": {**chk.judgement, "certain": False}, "interpretation": chk.interpretation}
    if not (chk.interpretation or {}).get("requirements"):
        return False
    extra = {"selection_mode": "refutation"} if selection_mode == "refutation" else {}
    return bool(output_tables(select_tables(variant, layer, as_of, policy,
                                            candidate_policy=candidate_policy, **extra)["selections"]))


def output_tables(selections: list[dict]) -> list[str]:
    return list(dict.fromkeys(t["table_id"] for s in selections if s["status"] != "no_table" for t in s["tables"]))


def compare_readings(analysis: dict, selections: list[dict], layer, as_of: str | None = None,
                     policy: str | None = None, candidates: list[dict] | None = None, *,
                     candidate_policy: str = "legacy", selection_mode: str = "legacy") -> dict:
    """{status: none|same|differ, readings: [{source, tables}]} of one question; ``selections``: the selections of
    the interpretation's own reading (first row). ``candidates`` (tests): the other readings, else
    ``reading_candidates``."""
    from .graph import select_tables

    others = reading_candidates(analysis, layer) if candidates is None else candidates
    rows = [{"source": "interpretation", "tables": output_tables(selections)}]
    extra = {"selection_mode": "refutation"} if selection_mode == "refutation" else {}
    for c in others[:MAX_READINGS - 1]:
        got = select_tables(c["analysis"], layer, as_of, policy, candidate_policy=candidate_policy, **extra)
        rows.append({"source": f"{c['point']}:{c['option']}", "kind": c.get("kind"), "effect": c.get("effect"),
                     "text": c.get("text"), "tables": output_tables(got["selections"])})
    if len(rows) == 1:
        status = "none"
    else:
        status = "same" if all(set(r["tables"]) == set(rows[0]["tables"]) for r in rows) else "differ"
    return {"status": status, "readings": rows}
