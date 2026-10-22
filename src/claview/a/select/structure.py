"""Read the shared interpretation contract and preserve group boundaries in cells.

This module never rewrites a model role or reconstructs missing structure. Catalog
positions provide positive support; absence of a reference row is not refutation.
"""
from __future__ import annotations

import copy
import itertools
import json
import math

from .. import paths
from ..catalog.facets import table_facets
from ..catalog.loader import ITEM_OBJ_ID
from ..contract.check import structure_status
from ..interpret.decompose_payload import spelling_key
from .facts import _pair


def read(analysis, rid):
    status = structure_status(analysis)
    if status["problems"]:
        raise ValueError("plan 19 agreed contract: " + "; ".join(status["problems"]))
    if status["state"] != "valid":
        return {"state": status["state"], "requirement": None, "polarity": []}
    value = analysis["decomposition"]["structure"]
    return copy.deepcopy({"state": "valid", "version": value["version"],
        "requirement": value["requirements"][rid],
        "polarity": [p for p in value["polarity"] if not p["requirement_ids"] or rid in p["requirement_ids"]],
        "confirmed_effects": value["confirmed_effects"]})


def _axes(ref):
    out = {}
    for c in ref["candidates"]:
        if c["obj_id"] is not None and c["value_id"] is not None:
            out.setdefault(c["table_id"], set()).add(c["obj_id"])
    return out


def _same_axis(a, b):
    shared = a.keys() & b.keys()
    return bool(shared) and all(len(a[t]) == len(b[t]) == 1 and a[t] == b[t] for t in shared)


def project(base, scoped):
    """A cell selects one value per group; different groups combine. Filter values
    on a confirmed common physical axis are a set, not contradictory AND values.
    Unknown filter axes remain explicit gaps, never silently certified.
    """
    if scoped["state"] != "valid":
        return base
    req = scoped["requirement"]
    components, dimensions, axes, gaps = {}, [], [], []
    filters = []
    for n, ref in enumerate(req["targets"], 1):
        key = f"filter:{n}"
        components[key] = ref
        pos = _axes(ref)
        matched = [g for g in filters if all(_same_axis(pos, _axes(components[k])) for k in g)]
        if len(matched) == 1:
            matched[0].append(key)
        else:
            filters.append([key])
        if not pos and len(req["targets"]) > 1:
            gaps.append("filter_axis_unresolved")
    dimensions += filters
    for group in req["breakdowns"]:
        gid = group["group_id"]
        if group["kind"] == "descendants":
            gaps.append("descendants_unexpanded")
        if group["axis"] is not None:
            axes.append({"group_id": gid, "ref": group["axis"]})
        values = []
        for n, ref in enumerate(group["values"], 1):
            key = f"{gid}:{n}"
            components[key] = ref
            values.append(key)
        if values:
            dimensions.append(values)
        elif group["axis"] is None:
            gaps.append("breakdown_unresolved")
    policy = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))
    cap = policy["structure_cells"]["max_combinations"]
    count = math.prod(map(len, dimensions))
    if count > cap:
        gaps.append("structure_cell_limit")
        combinations = [()]
    else:
        combinations = list(itertools.product(*dimensions)) if dimensions else [()]
    cells = {json.dumps(c, separators=(",", ":")): list(c) for c in combinations}
    return {**base, "targets": list(cells), "target_forms": {},
            "structure_cells": cells, "structure_components": components,
            "structure_axes": axes, "structure_gaps": list(dict.fromkeys(gaps)),
            "structure": scoped}


def _positions(ref, table, facet, axis=False, strong=False):
    rows = []
    for c in ref["candidates"]:
        if c["table_id"] != table:
            continue
        if c["obj_id"] is None:
            if not axis and c["role"] == "table_name" and c["value_id"] is None \
                    and spelling_key(c["value_name"]) == spelling_key(facet.title) \
                    and (not strong or c["path"] in ("exact", "dictionary")):
                rows.append((None, None, c["path"]))
            continue
        ar = facet.axis(c["obj_id"])
        if strong and (c["path"] not in ("exact", "dictionary") or
                       (c["obj_id"] != ITEM_OBJ_ID and (not ar or ar["draft"]))):
            continue
        if axis:
            if c["value_id"] is None and c["role"] == "breakdown" and facet.axis(c["obj_id"]) is not None:
                rows.append((c["obj_id"], None, c["path"]))
        elif c["value_id"] is not None and facet.value(c["obj_id"], c["value_id"]) is not None:
            rows.append((c["obj_id"], c["value_id"], c["path"]))
    return list(dict.fromkeys(rows))


def supported_targets(base, table, layer, indicator_refs, *, strong=False, condition_spans=()):
    """Exact raw positions must coexist in one table, including all requested axes.
    Two tables with one axis each cannot supply a two-axis observation together.
    Returned support is independent of interpretation certainty (never proof).
    """
    if base.get("structure_gaps"):
        return set()
    facet = table_facets(layer)[table]
    indicator = [r for ref in indicator_refs for r in _positions(ref, table, facet, strong=strong)]
    if not indicator:
        return set()
    requirement = base["structure"]["requirement"]
    constants = []
    for ref in requirement["basis"] + ([requirement["context"]] if requirement["context"] else []):
        rows = _positions(ref, table, facet, strong=strong)
        if not rows and ref["place"] == "basis" and tuple(ref["span"]) in condition_spans:
            rows = [(None, None, "exact")]
        if not rows:
            return set()
        constants.append(rows)
    group_axes = {}
    for group in requirement["breakdowns"]:
        found = [{r[0] for r in _positions(ref, table, facet, strong=strong) if r[0] is not None}
                 for ref in group["values"]]
        found = [x for x in found if x]
        if group["axis"] is not None:
            found.append({r[0] for r in _positions(group["axis"], table, facet, axis=True, strong=strong)})
        if found:
            common = set.intersection(*found)
            if not common:
                return set()
            group_axes[group["group_id"]] = common
    axes = [_positions(a["ref"], table, facet, axis=True, strong=strong) for a in base["structure_axes"]]
    if any(not a for a in axes):
        return set()
    out = set()
    for token, keys in base["structure_cells"].items():
        values = [_positions(base["structure_components"][k], table, facet, strong=strong) for k in keys]
        choices = [indicator, *values, *constants, *axes]
        if any(not rows for rows in choices) or math.prod(map(len, choices)) > 10000:
            continue
        for assignment in itertools.product(*choices):
            value_rows = assignment[:1 + len(values) + len(constants)]
            axis_rows = assignment[1 + len(values) + len(constants):]
            if len({r[0] for r in axis_rows}) != len(axis_rows):
                continue
            if any(_pair(facet, a[:2], b[:2]) != "possible"
                   for a, b in itertools.combinations(value_rows, 2)):
                continue
            if any(k.split(":")[0] in group_axes and row[0] not in group_axes[k.split(":")[0]]
                   for k, row in zip(keys, value_rows[1:])):
                continue
            selected_axes = {a["group_id"]: row[0] for a, row in zip(base["structure_axes"], axis_rows)}
            if any(axis not in group_axes.get(gid, {axis}) for gid, axis in selected_axes.items()):
                continue
            if any(k.split(":")[0] in selected_axes and row[0] != selected_axes[k.split(":")[0]]
                   for k, row in zip(keys, value_rows[1:])):
                continue
            for k, row in zip(keys, value_rows[1:]):
                if not k.startswith("filter:"):
                    selected_axes.setdefault(k.split(":")[0], row[0])
            if len(set(selected_axes.values())) != len(selected_axes):
                continue
            out.add(token)
            break
    return out


def covers(cells, req, legacy):
    result = legacy(cells, req)
    if "structure_cells" in req:
        result["targets"] = sorted({req["structure_components"][k]["quote"] for c in cells
                                    for k in req["structure_cells"][c[0]]})
    return result


def reference_candidates(analysis, rid):
    """Raw rows, not the legacy list of unselected table IDs."""
    out, seen = [], set()
    refs = (analysis.get("decomposition") or {}).get("selection_refs", {}).get(rid, [])
    for ref in refs:
        for row in ref["candidates"]:
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                out.append(copy.deepcopy(row))
    return out
