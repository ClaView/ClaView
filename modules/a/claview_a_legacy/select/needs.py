"""S1 requirement needs (plan 11 r4, 2절 S1 and 3절; R1b observation path): interpretation facts and confirmed
conditions -> per-requirement effects.

    needs[rid] = {requirement_id, identity, filter, evidence, set, causes, certainty}

Effect levels (``data/decision_policy.json`` ``effect_rules``, C15): ``filter`` (1: used only to refute: negated
fragments, negative basis words, exclusions), ``evidence`` (2: needs table-side proof, its opposite refutes:
indicator, targets, lower items, period, unit, breakdown, exclusion, basis, frequency), ``set`` (3: on top of 2, a
dimension the question set: frequency and positive basis words), ``causes`` (4: ask causes, never used by 1 to 3).

Certainty (3절) is decided per (requirement identity key, condition or fragment ID) from
``judgement.failed_sources`` only (``interp.status`` and ``judgement.certain`` are not read):
- a source with requirement IDs and condition / fragment IDs makes those conditions / fragments of those
  requirements unsettled; with requirement IDs only, the whole requirement;
- a source without requirement IDs but with fragment (condition) IDs concerns the requirements holding those
  fragments (conditions); held by none, every requirement; with no ID at all, every requirement (question level);
- a condition inside an unsettled fragment is unsettled; ambiguous conditions are never settled. Plan 11 R1c: a
  source about a name mapping only (``data/decision_policy.json`` ``certainty.name_only_sources``: the source
  condition and the name parts its detail ``<why>:<part>`` may name) unsettles that fragment's names (indicator,
  target, lower item) but not the conditions read inside the fragment, which keep their own sources;
- a requirement whose status is unresolved or unsupported is unsettled as a whole;
- confirmed conditions (``source: user``) are settled (R2 applies them; R1b takes none).
Name mapping (C2): an indicator or target name is mapped for sure only with a catalog name read exactly or from the
dictionary and no draft term (a lexical or embedding path, or a draft term, is an ask cause).

Nothing here reads a table or removes anything.
"""

from __future__ import annotations

import json

from .. import paths
from ..catalog.text import index_key
from .evidence import (HANDOFF_ONLY_AXES, axis_of, is_byeol, named_lower_items, polarity_of, requirement_of, scope,
                       value_of)
from .facts import requirement_identity

EXACT_PATHS = ("exact", "dictionary")
UNSETTLED_STATUS = ("unresolved", "unsupported")
NAME_ONLY_SOURCES: dict[str, list[str]] = {
    k: list(v) for k, v in (json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8")).get("certainty") or {})
    .get("name_only_sources", {}).items() if not k.startswith("_")}


def name_only(row: dict) -> bool:
    """A failed source about a name mapping only (module docstring, R1c): its condition is listed and its detail
    names one of the listed name parts after the colon; no condition ID of its own."""
    parts = NAME_ONLY_SOURCES.get(row.get("condition") or "")
    if not parts or row.get("condition_ids"):
        return False
    _why, sep, part = str(row.get("detail") or "").rpartition(":")
    return bool(sep) and part in parts


def _fragments_of_requirement(analysis: dict, rid: str) -> set[str]:
    return set(requirement_identity(analysis, rid)["fragment_ids"])


def _conditions_of_requirement(analysis: dict, rid: str) -> set[str]:
    req = requirement_of(analysis, rid)
    return set(req.get("condition_ids") or []) | set(req.get("shared_condition_ids") or [])


def unsettled(analysis: dict, rid: str, confirmed: list | None = None) -> dict:
    """{all, condition_ids, fragment_ids, sources}: what of the requirement is not settled (module docstring)."""
    j = analysis.get("judgement") or {}
    reqs = [r["id"] for r in (analysis.get("interpretation") or {}).get("requirements") or []]
    frags = {r: _fragments_of_requirement(analysis, r) for r in reqs}
    conds = {r: _conditions_of_requirement(analysis, r) for r in reqs}
    out = {"all": False, "condition_ids": set(), "fragment_ids": set(), "sources": []}
    cond_frags: set[str] = set()  # unsettled fragments whose conditions are unsettled too (not name-only sources)
    for row in j.get("failed_sources") or []:
        rids = list(row.get("requirement_ids") or [])
        cids = list(row.get("condition_ids") or [])
        fids = list(row.get("fragment_ids") or [])
        if rids:
            hit = rid in rids
        elif fids:
            holders = [r for r in reqs if frags[r] & set(fids)]
            hit = rid in holders or not holders
        elif cids:
            holders = [r for r in reqs if conds[r] & set(cids)]
            hit = rid in holders or not holders
        else:
            hit = True
        if not hit:
            continue
        out["sources"].append(dict(row))
        if cids or fids:
            out["condition_ids"] |= set(cids)
            out["fragment_ids"] |= set(fids)
            if not name_only(row):
                cond_frags |= set(fids)
        else:
            out["all"] = True
    req = requirement_of(analysis, rid)
    if req.get("status") in UNSETTLED_STATUS:
        out["all"] = True
    out["condition_ids"] |= set(req.get("ambiguous_condition_ids") or [])
    spans = {f["id"]: f["span"] for f in analysis.get("fragments") or []}
    for c in analysis.get("conditions") or []:  # a condition inside an unsettled fragment
        if c.get("span") and any(fid in spans and spans[fid][0] < c["span"][1] and c["span"][0] < spans[fid][1]
                                 for fid in cond_frags):
            out["condition_ids"].add(c["id"])
    settled_by_user = {d.get("condition_id") for d in confirmed or [] if d.get("source") == "user"}
    out["condition_ids"] -= settled_by_user
    return {"all": out["all"], "condition_ids": sorted(out["condition_ids"]),
            "fragment_ids": sorted(out["fragment_ids"]), "sources": out["sources"]}


def settled(needs_r: dict, condition_id: str | None = None, fragment_id: str | None = None) -> bool:
    """Is the value (a condition, a fragment, or the requirement itself when both are None) settled?"""
    c = needs_r["certainty"]
    if c["all"]:
        return False
    if condition_id is not None and condition_id in c["condition_ids"]:
        return False
    if fragment_id is not None and fragment_id in c["fragment_ids"]:
        return False
    return True


def _term_for(analysis: dict, fragment_id, name: str | None) -> list[dict]:
    return [t for t in analysis.get("terms") or [] if t.get("name") == name
            and (fragment_id is None or t.get("fragment_id") == fragment_id)]


def _mapped(analysis: dict, fragment_id, name: str | None, path: str | None) -> tuple[bool, list[str]]:
    """(mapped for sure, ask causes) of a name (C2)."""
    if not name:
        return False, ["no_catalog_name"]
    terms = _term_for(analysis, fragment_id, name)
    causes = []
    paths = {path} if path else {t.get("path") for t in terms}
    if not paths or not paths <= set(EXACT_PATHS):
        causes += [f"term_path:{p}" for p in sorted(p for p in paths if p) if p not in EXACT_PATHS] or ["no_term"]
    if any(t.get("status") == "draft" for t in terms):
        causes.append("term_draft")
    return not causes, causes


def _fragment_at(analysis: dict, span) -> str | None:
    if not span:
        return None
    return next((f["id"] for f in analysis.get("fragments") or []
                 if f["span"][0] <= span[0] < f["span"][1]), None)


def requirement_needs(analysis: dict, rid: str, confirmed: list | None = None) -> dict:
    """The needs of one requirement (module docstring)."""
    req = requirement_of(analysis, rid)
    identity = requirement_identity(analysis, rid)
    certainty = unsettled(analysis, rid, confirmed)
    nd = {"requirement_id": rid, "identity": identity, "certainty": certainty}
    counted, amb = scope(analysis, rid)
    by_id = {c["id"]: c for c in analysis.get("conditions") or []}
    causes = [{"kind": "failed_source", **s} for s in certainty["sources"]]

    # 2: indicator, targets, lower items
    ind = req.get("indicator") or None
    indicator = None
    if ind:
        fid = ind.get("fragment_id") or _fragment_at(analysis, ind.get("span"))
        mapped, why = _mapped(analysis, fid, ind.get("name"), ind.get("path"))
        indicator = {"name": ind.get("name"), "quote": ind.get("quote"), "path": ind.get("path"), "fragment_id": fid,
                     "settled": settled(nd, fragment_id=fid), "mapped": mapped}
        causes += [{"kind": "indicator_name", "detail": w} for w in why]
    targets = []
    for t in req.get("targets") or []:
        fid = t.get("fragment_id") or _fragment_at(analysis, t.get("span"))
        mapped, why = _mapped(analysis, fid, t.get("name"), None)
        targets.append({"name": t.get("name") or t.get("quote"), "quote": t.get("quote"), "named": bool(t.get("name")),
                        "fragment_id": fid, "settled": settled(nd, fragment_id=fid), "mapped": mapped})
        causes += [{"kind": "target_name", "detail": w} for w in why if t.get("name")]
    breakdowns = [c for c in analysis.get("conditions") or [] if c.get("kind") == "breakdown" and c["id"] in counted]
    lower = []
    for x in named_lower_items(analysis, rid):
        word = index_key(x["word"])
        cond = next((c for c in breakdowns if index_key((c.get("attrs") or {}).get("text") or c.get("value") or "")
                     in (word, index_key(x["text"]))), None)
        fid = _fragment_at(analysis, x.get("span"))
        cid = cond["id"] if cond else None
        lower.append({"text": x["text"], "word": x["word"], "name": x["name"], "condition_id": cid,
                      "fragment_id": fid, "named": bool(x["name"]),
                      "settled": settled(nd, condition_id=cid, fragment_id=fid)})

    # 1 / 2 / 3: conditions of the requirement (own and shared; ambiguous ones only as causes)
    conditions, filters, question_set = [], [], []
    for cid in [*(req.get("condition_ids") or []), *(req.get("shared_condition_ids") or [])]:
        c = by_id.get(cid)
        if c is None or cid not in counted or any(x["condition_id"] == cid for x in conditions):
            continue
        kind = c.get("kind")
        if kind not in ("period", "frequency", "basis", "unit", "breakdown", "exclude"):
            continue  # calc, relation, function, display: 0 (not used by the selection)
        if kind == "basis" and axis_of(c) in HANDOFF_ONLY_AXES:
            continue  # handed to B, never judged on the table
        row = {"condition_id": cid, "kind": kind, "axis": axis_of(c) or None, "value": value_of(c) or None,
               "polarity": polarity_of(c), "byeol": kind == "basis" and is_byeol(c),
               "settled": settled(nd, condition_id=cid)}
        conditions.append(row)
        if (kind == "basis" and row["polarity"] == "-") or kind == "exclude":
            filters.append({"condition_id": cid, "kind": kind, "settled": row["settled"]})
        if row["settled"] and (kind == "frequency" or (kind == "basis" and row["polarity"] == "+")):
            question_set.append({"condition_id": cid, "kind": kind, "axis": row["axis"], "value": row["value"]})
    for cid in sorted(amb):
        causes.append({"kind": "ambiguous_condition", "condition_id": cid})
    for c in conditions:
        if c["kind"] == "period" and ((by_id[c["condition_id"]].get("attrs") or {}).get("invalid")):
            causes.append({"kind": "period_invalid", "condition_id": c["condition_id"]})
    if req.get("status") in UNSETTLED_STATUS:
        causes.append({"kind": "requirement_status", "detail": req.get("status")})

    negated_frags = [f["id"] for f in analysis.get("fragments") or [] if f.get("phrase") == "negated"]
    negated = [w for w in (analysis.get("judgement") or {}).get("negated") or [] if w]
    nd.update(
        filter={"negated": negated, "negated_fragment_ids": negated_frags,
                "negated_settled": not certainty["all"] and not (set(negated_frags) & set(certainty["fragment_ids"])),
                "forbidden": filters},
        evidence={"indicator": indicator, "targets": targets, "lower_items": lower, "conditions": conditions},
        set=question_set, causes=causes)
    return nd


def all_needs(analysis: dict, confirmed: list | None = None) -> dict[str, dict]:
    """needs[rid] for every requirement of the interpretation."""
    return {r["id"]: requirement_needs(analysis, r["id"], confirmed)
            for r in (analysis.get("interpretation") or {}).get("requirements") or []}
