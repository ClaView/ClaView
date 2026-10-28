"""Soft preference within equally supported assignments, never a refutation.

Unknown units/axis roles and missing model structure do not establish a comparison.
Only the opt-in execution path uses this module; legacy scores remain unchanged.
"""
from __future__ import annotations

import json

from . import model_refs
from .cover import _asked_axis, signature
from .evidence import breakdown_axes, is_byeol, polarity_of, requirement_of
from .refute import cell_key
from .tables import nospace


def additional_target_axes(analysis, rid, view):
    if not view.has_meta or any(view.axis_roles.get(obj) not in ("indicator", "target", "basis")
                                for obj, _ in view.dims):
        return None
    req = requirement_of(analysis, rid)
    refs = model_refs.references(analysis, rid)
    local = set(req.get("condition_ids") or []) | set(req.get("shared_condition_ids") or [])
    conditions = [c for c in analysis.get("conditions") or [] if c["id"] in local]
    if req.get("ambiguous_condition_ids"):
        return None
    if refs is None:
        raw = (analysis.get("text") or {}).get("raw") or ""
        lo, hi = req.get("span") or (0, 0)
        words = [raw[lo:hi], req.get("context") or ""]
        words += [t.get("name") or t.get("quote") or "" for t in req.get("targets") or []]
        mentioned = set()
    else:
        words = [r.get("quote") or "" for r in refs]
        mentioned = {c["obj_id"] for r in refs for c in r.get("candidates") or []
                     if c["table_id"] == view.table_id}
    words += [c.get("quote") or "" for c in conditions]
    words += [str(c.get("value") or "") for c in conditions]
    for c in conditions:
        if c["kind"] == "basis" and is_byeol(c) and polarity_of(c) == "+":
            mentioned |= breakdown_axes(c, view)
    qtext = nospace(" ".join(words))
    return [{"obj_id": obj, "name": name} for obj, name in view.dims
            if view.axis_roles[obj] == "target" and obj not in mentioned
            and len({it.item_id for it in view.items if it.obj_id == obj}) > 1
            and not _asked_axis(view, obj, name, qtext)]


def prefer_fewer_defaults(analysis, rid, rows, views, table_facts):
    """Reorder only the occupied slots of a comparable group.

    Full supported cell sets, prior evidence priorities, units, fixed basis and
    period context must agree. No candidate, proof or original score is changed.
    Final order remains sortable by downstream decision/group consumers.
    """
    groups = {}
    model_missing = model_refs.adopted(analysis) and not (
        (analysis.get("decomposition") or {}).get("structure"))
    axes = {t: additional_target_axes(analysis, rid, view) for t, view in views.items()}
    for index, row in enumerate(rows):
        row["prior_order"] = list(row["order"])
        extra = {t: axes[t] for t in row["tables"]}
        row["default_axis_preference"] = {"axes": extra, "comparable": False,
                                           "count": sum(len(v) for v in extra.values())
                                           if all(v is not None for v in extra.values()) else None}
        if model_missing or row["execution_gaps"] or not row["cells"] or row["supported"] != row["cells"]:
            continue
        if any(v is None for v in extra.values()):
            continue
        # A partial head-word hit is not the same measured concept, even when
        # legacy support counts every requested cell. Convenience must not beat
        # a more specific indicator. Exact value/role grounding is a comparison
        # prerequisite only, never proof of the interpretation or assignment.
        grounded = True
        for table in row["tables"]:
            indicator = table_facts.get(table, {}).get("indicator", {})
            check = indicator.get("check") or {}
            if not (check.get("available") and check.get("result") == "match" and any(
                r.get("field") == "value" and r.get("value_role") == "indicator" and r.get("method") == "exact"
                for r in indicator.get("occurrences") or [])):
                grounded = False
        if not grounded:
            continue
        units = {tuple(sorted(views[t].units())) for t in row["tables"]}
        bases = {json.dumps({k: sorted(v) for k, v in signature(views[t]).items()}, sort_keys=True)
                 for t in row["tables"]}
        if len(units) != 1 or len(next(iter(units))) != 1 or len(bases) != 1:
            continue
        context = {k: v for k, v in row["period_plan"]["context"].items() if k != "bundle_id"}
        key = json.dumps([row["order"][:6],
            [sorted(cell_key(c) for c in row[k]) for k in ("cells", "possible", "supported", "proven", "conflicted")],
            sorted(units), sorted(bases), context], sort_keys=True)
        groups.setdefault(key, []).append(index)
    for positions in groups.values():
        comparable = [rows[i] for i in positions]
        for row in comparable:
            row["default_axis_preference"]["comparable"] = len(positions) > 1
        comparable.sort(key=lambda r: (r["default_axis_preference"]["count"], r["prior_order"]))
        for index, row in zip(positions, comparable):
            rows[index] = row
    for index, row in enumerate(rows):
        row["order"] = [*row["prior_order"][:6], index, *row["prior_order"][6:]]

