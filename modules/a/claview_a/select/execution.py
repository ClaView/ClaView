"""Opt-in plan 17 execution: certificates -> cell allocation -> period/bundle plan.

The legacy eligible/title/family/representative filters are not on this path.
Unrefuted is possible, never automatically proven. Missing model structure is a
recorded gap until plan 19's producer/contract is integrated, not reconstructed here.
"""
from __future__ import annotations

import copy
import json

from ..catalog.facets import catalog_version
from . import candidates, facts, periods, refute, structure
from .allocation import rank_assignments
from .cover import (LATEST, all_cells, condition_supported_cells, covers_of, has_evidence, requested_cells, signature)
from .decision import _dimensions, _scores, indicator_relation
from .evidence import check_evidence, requirement_of
from .input import selection_input
from .model_refs import adopted
from .needs import requirement_needs
from .ranking import prefer_fewer_defaults
from .tables import selection_catalog


def _period_dimensions(base: dict, results: dict) -> dict:
    """Partition original requested intervals; never count clipped intervals as the request."""
    requested = [r for result in results.values() for r in periods.ranges(result, "requested_months")]
    covered = [r for result in results.values() for r in periods.ranges(result, "covered_months")]
    edges = sorted({x for a, b in requested + covered for x in (a, b + 1)})
    spans = [(f"{periods.month_text(a)}~{periods.month_text(b - 1)}", a, b - 1)
             for a, b in zip(edges, edges[1:]) if any(lo <= a and b - 1 <= hi for lo, hi in requested)]
    freqs = sorted({r["frequency"] for r in results.values() if r.get("frequency")}) or [None]
    return {**base, "periods": spans or [(LATEST, None, None)], "frequencies": freqs,
            "flags": [] if spans else ["period_unknown"]}


def _semantic_request(analysis, rid, views, as_of, scoped=None):
    base = requested_cells(analysis, rid, views, as_of)
    # Retain only the established non-period request dimensions. The old envelope,
    # latest/freshness and frequency decisions have no effect in the new path.
    base = {**base, "periods": [(LATEST, None, None)], "frequencies": [None], "flags": []}
    return structure.project(base, scoped) if scoped is not None else base


def _semantic(cell):
    return cell[0], None, LATEST, cell[3]


def _provided(req, results, semantic_possible, *, unsettled=False):
    spans = {name: (a, b) for name, a, b in req["periods"]}
    out = {}
    for t, possible in semantic_possible.items():
        result = results[t]
        conflict = result["status"] == "no_data_in_range" or (result["status"] == "table_unsuitable" and
                   (result.get("issue") or {}).get("detail") == "frequency_not_in_table")
        if not unsettled and conflict:
            out[t] = set()
            continue
        coverage = periods.ranges(result, "covered_months")
        unknown = unsettled or result["status"] != "ok"
        out[t] = {c for c in all_cells(req) if _semantic(c) in possible and
                  (unknown or ((c[1] is None or c[1] == result.get("frequency")) and
                   (c[2] == LATEST or any(a <= spans[c[2]][0] and spans[c[2]][1] <= b for a, b in coverage))))}
    return out


def _semantic_proven(cell, nd, fact, model, structured=False):
    if (model and not structured) or nd["certainty"]["all"] or nd["certainty"]["condition_ids"] or nd["certainty"]["fragment_ids"]:
        return False
    ind = nd["evidence"]["indicator"]
    if not ind or not ind["mapped"] or not ind["settled"] or fact.get("indicator", {}).get("weak") is not False:
        return False
    # An exact occurrence of the head noun cannot prove the full indicator when
    # the requirement-level phrase check explicitly found only a partial match.
    if (fact.get("indicator", {}).get("check") or {}).get("result") == "partial":
        return False
    if cell[0] is not None and not structured:
        target = next((t for t in nd["evidence"]["targets"] if t["name"] == cell[0]), None)
        row = next((t for t in fact.get("targets", []) if t["name"] == cell[0]), {})
        if not target or not target["mapped"] or not target["settled"] or row.get("present") is not True:
            return False
    if any(not c["settled"] for c in nd["evidence"]["conditions"]):
        return False
    if any(r.get("check") != "period" for r in [*fact.get("unproven", []), *fact.get("refuted", [])]):
        return False
    if fact.get("cross") and fact["cross"].get("support") != "supported":
        return False
    return structured or (fact.get("cells") or {}).get("combinable") not in ("undetermined", "impossible")


def _supported(req, results, semantic_supported):
    supplied = _provided(req, results, semantic_supported)
    return {t: {c for c in supplied[t] if c[2] != LATEST} if results[t]["status"] == "ok" else set()
            for t in semantic_supported}


def _conflicted(checks, req, cells):
    """Conditional opposition for ordering when interpretation is not settled.

    It is not a refutation certificate: the proposed reading may itself be wrong.
    Unknown cross-axis metadata remains distinct from a known single-axis mapping.
    """
    split = {cid for ids in req["basis"] for cid in ids}
    cross = next((c for c in checks if c["name"] == "cross_axis"), {})
    if req["cross"] and (cross.get("detail") or {}).get("support") == "single_axis":
        return set(cells)
    out = set()
    for cell in cells:
        ids = {cid for kind in ("basis", "unit", "exclude") for cid in req["proof"][kind]
               if cid not in split or cid in cell[3]}
        if any(c["verdict"] == "fail" and (c.get("detail") or {}).get("condition_id") in ids
               for c in checks if c["name"] in ("basis", "unit", "exclude")):
            out.add(cell)
    return out


def _frame(nd, fact, view, result, base, family):
    ind = nd["evidence"]["indicator"]
    units = sorted(view.units())
    indicator = fact.get("indicator") or {}
    # A lexical interpretation can still name the exact same catalog value in two
    # editions. That is enough to explore a possible bundle, not to certify the
    # interpretation. Require the actual accepted reference row, not a substring
    # elsewhere on the table; partial/target occurrences cannot open this path.
    referenced_value = any(
        o.get("field") == "value" and o.get("method") == "exact"
        and o.get("value_role") == "indicator"
        and any(c.get("role") == "indicator" and c.get("obj_id") == o.get("obj_id")
                and c.get("value_id") == o.get("value_id")
                for c in indicator.get("reference_candidates") or [])
        for o in indicator.get("occurrences") or [])
    if not ind or not ind.get("name") or not (ind["mapped"] or referenced_value) \
            or (indicator.get("check") or {}).get("result") != "match" \
            or len(units) != 1 or not result.get("frequency"):
        return None
    # A search frame permits comparison, not a claim of joint executability. Weak
    # exact/qualified matches may propose a bundle within the same catalog family;
    # only _semantic_proven can supply proof. Partial/absent concepts stay singletons.
    # Explicitly split basis values are cell dimensions, not invariant frame values.
    split = {cid for ids in base["basis"] for cid in ids}
    varied = {c["axis"] for c in nd["evidence"]["conditions"] if c["condition_id"] in split}
    return json.dumps([family, ind["name"], units, {k: sorted(v) for k, v in signature(view).items() if k not in varied},
                       indicator_relation({view.table_id: fact}, [view.table_id]), result["frequency"]],
                      ensure_ascii=False, sort_keys=True)


def _semantic_support(candidate, checks, fact, req, cells):
    """Positive ranking support requires this cell's indicator and target evidence.

    Discovery by a target is not indicator evidence. Keep unsupported cells possible
    and retain their candidates; this changes ordering only, never refutation/proof.
    """
    indicator = next((c for c in checks if c["name"] == "indicator"), None)
    if indicator and (indicator.get("detail") or {}).get("available"):
        if indicator["detail"].get("result") not in ("match", "partial"):
            return set()
    elif not has_evidence(candidate, checks):
        return set()
    present = {t["name"] for t in fact.get("targets") or [] if t.get("present") is True}
    supported = condition_supported_cells(candidate["table_id"], checks, req, cells)
    return {c for c in supported if c[0] is None or c[0] in present}


def _evaluate(tables, inp, views, base, semantic_possible, semantic_proven, semantic_supported,
              semantic_conflicted, bundle_id):
    plan = periods.bundle_plan(inp, views, tables, bundle_id)
    req = _period_dimensions(base, plan["results"])
    cells = all_cells(req)
    possible = _provided(req, plan["results"], {t: semantic_possible[t] for t in tables}, unsettled=inp["unsettled"])
    proven = {t: {c for c in possible[t] if _semantic(c) in semantic_proven[t] and
                  c[2] != LATEST and plan["results"][t]["status"] == "ok"} for t in tables}
    supported = _supported(req, plan["results"], {t: semantic_supported[t] for t in tables})
    conflicts = {t: {c for c in possible[t] if _semantic(c) in semantic_conflicted[t]} for t in tables}
    if plan["gaps"]:
        proven = {t: set() for t in tables}
    # Assign each cell once, with proof preferred. Earlier-ending editions first
    # produce a continuous old/new split where the metadata supports it.
    end = {t: max((b for _, b in periods.ranges(plan["results"][t], "covered_months")), default=-1) for t in tables}
    assigned = {t: set() for t in tables}
    assignable = _provided(req, plan["results"], {t: semantic_possible[t] for t in tables}) \
        if "structure_cells" in base else possible
    for c in sorted(cells, key=refute.cell_key):
        providers = [t for t in tables if c in assignable[t]]
        if providers:
            owner = min(providers, key=lambda t: (c not in proven[t], c not in supported[t],
                                                 c in conflicts[t], end[t], tables.index(t)))
            assigned[owner].add(c)
    gaps = list(plan["gaps"])
    gaps += base.get("structure_gaps", [])
    bounds = {label: (a, b) for label, a, b in req["periods"]}
    assignments = {}
    for t in tables:
        pieces = [bounds[c[2]] for c in assigned[t] if c[2] != LATEST]
        if not pieces:
            gaps.append("assignment_unresolved")
            continue
        try:
            assignments[t] = periods.continuous_assignment(pieces)
        except ValueError:
            gaps.append("discontinuous_assignment")
    if not gaps:
        # Re-resolve exactly the context/assignment B will receive, and verify it
        # still supplies every assigned cell; no applied envelope is counted.
        plan = periods.bundle_plan(inp, views, tables, bundle_id, assignments)
        planned = _provided(req, plan["results"], {t: semantic_possible[t] for t in tables}, unsettled=inp["unsettled"])
        gaps += plan["gaps"]
        if any(not assigned[t] <= planned[t] for t in tables):
            gaps.append("assignment_not_covered")
    may = set().union(*possible.values()) if tables else set()
    proof = set().union(*proven.values()) if tables else set()
    support = set().union(*supported.values()) if tables else set()
    if "structure_cells" in base and any(_semantic(c) not in semantic_supported[t]
                                         for t in tables for c in assigned[t]):
        gaps.append("structure_not_supported")
    conflict = {c for c in may if all(c in conflicts[t] for t in tables if c in possible[t])}
    status = "proven" if cells and proof == cells and not gaps else "possible" if cells and may == cells else "partial"
    return {"tables": tables, "proof_status": status, "request": req, "cells": cells,
            "possible": may, "proven": proof, "supported": support, "conflicted": conflict,
            "missing": cells - may, "by_table": assigned,
            "period_plan": plan, "execution_gaps": list(dict.fromkeys(gaps)),
            "period_exclusions": {t: {refute.cell_key(c): {"check": "period",
                    "status": plan["results"][t]["status"], "requested_cell": refute.cell_key(c),
                    "covered_months": (plan["results"][t].get("periods") or {}).get("covered_months", [])}
                for c in cells if _semantic(c) in semantic_possible[t] and c not in possible[t]} for t in tables},
            "bundle": {"bundle_id": bundle_id, "context": plan["context"], "assignments": assignments}}


def _json(value):
    if isinstance(value, set):
        return sorted((refute.cell_key(c) for c in value))
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    return value


def compute(analysis, rid, pool, doc, layer, as_of, *, cut=None):
    views = {p["table_id"]: selection_catalog(layer).views[p["table_id"]] for p in pool}
    inp = periods.period_input(analysis, rid, as_of)
    nd = requirement_needs(analysis, rid)
    inp["unsettled"] = bool(nd["certainty"]["all"] or nd["certainty"]["condition_ids"])
    model = adopted(analysis)
    scoped = structure.read(analysis, rid)
    structured = scoped["state"] == "valid"
    if structured:
        nd["identity"]["key"] = selection_input(analysis, rid, layer, as_of, with_structure=True)["requirement_key"]
    # Until plan 19 supplies verified scopes, do not turn a model reading's old
    # question-wide negative projection into a requirement-local refutation.
    if model:
        nd["filter"]["negated_settled"] = False
    ft = facts.requirement_facts(analysis, rid, pool, doc, layer, as_of)["tables"]
    nd_refute = copy.deepcopy(nd)
    nd_refute["evidence"]["conditions"] = [c for c in nd_refute["evidence"]["conditions"] if c["kind"] != "period"]
    if structured:
        # No confirmed effects exist in this contract. Neither legacy name negation
        # nor independently projected exclude/negative-basis rows may refute.
        for c in nd_refute["evidence"]["conditions"]:
            if c["kind"] == "exclude" or c["polarity"] == "-":
                c["settled"] = False
    version = catalog_version(layer)
    table_refs = refute.refute_tables(nd_refute, ft, views, version)
    alive = [t for t in views if t not in table_refs]
    base = _semantic_request(analysis, rid, views, as_of, scoped)
    semantic_cells = all_cells(base)
    cell_facts = ft
    if structured:
        # Old target/combination facts describe a flattened OR dimension. Keep
        # positive condition refutations, but never apply those facts to new groups.
        nd_refute["evidence"]["targets"] = []
        nd_refute["evidence"]["lower_items"] = []
        cell_facts = {t: {**f, "cells": {}} for t, f in ft.items()}
    cell_refs = refute.refute_cells(nd_refute, cell_facts, views, base, semantic_cells, alive, version)
    possible = {t: {c for c in semantic_cells if refute.cell_key(c) not in cell_refs.get(t, {})} for t in alive}
    checks = {r["table_id"]: r["checks"] for r in doc.get("evidence", [])}
    refs = (analysis.get("decomposition") or {}).get("selection_refs", {}).get(rid, [])
    indicators = [r for r in refs if r["place"] == "indicator"]
    conditions = {c["id"]: c for c in analysis["conditions"]}
    condition_spans = {t: {tuple(conditions[c["detail"]["condition_id"]]["span"])
                           for c in checks.get(t, []) if c["name"] == "basis" and c["verdict"] == "pass"
                           and (c.get("detail") or {}).get("condition_id") in conditions} for t in alive}
    supported_targets = {t: structure.supported_targets(base, t, layer, indicators,
                         condition_spans=condition_spans[t]) for t in alive} if structured else {}
    strong_targets = {t: structure.supported_targets(base, t, layer, indicators, strong=True,
                      condition_spans=condition_spans[t]) for t in alive} if structured else {}
    proven = {t: {c for c in possible[t] if _semantic_proven(c, nd, ft[t], model,
                  structured=structured and c[0] in strong_targets[t] and not scoped["polarity"])} for t in alive}
    initial = {t: periods.resolve(inp, views[t]) for t in alive}
    req = _period_dimensions(base, initial)
    may = _provided(req, initial, possible, unsettled=inp["unsettled"])
    proof = {t: {c for c in may[t] if _semantic(c) in proven[t] and c[2] != LATEST} for t in alive}
    rank_checks = {t: [c for c in rows if c["name"] not in ("period", "frequency")]
                   for t, rows in checks.items()}
    scores = _scores(analysis, rid, pool, rank_checks, views, {})
    support_req = {**base, "proof": {**base["proof"], "period": [], "frequency": []}}
    by_table = {p["table_id"]: p for p in pool}
    if structured:
        semantic_supported = {t: {c for c in condition_supported_cells(t, checks.get(t, []), support_req, possible[t])
                                  if c[0] in supported_targets[t]} for t in alive}
    else:
        semantic_supported = {t: _semantic_support(by_table[t], checks.get(t, []), ft[t], support_req, possible[t])
                              for t in alive}
    semantic_conflicted = {t: _conflicted(checks.get(t, []), base, possible[t]) for t in alive}
    support = _supported(req, initial, semantic_supported)
    frames = {t: _frame(nd, ft[t], views[t], initial[t], base, candidates.family_key(layer, t)) for t in alive}
    allocation = rank_assignments(all_cells(req), may, proof, scores, frames, supported=support)
    evaluated = []
    reading = (analysis.get("decomposition") or {}).get("reading_id") or "rules"
    for n, a in enumerate(allocation["assignments"], 1):
        ev = _evaluate(a["tables"], inp, views, base, possible, proven, semantic_supported,
                       semantic_conflicted, f"{reading}:{rid}:a{n}")
        for t, exclusions in ev["period_exclusions"].items():
            for cell, opposite in exclusions.items():
                exclusions[cell] = refute.certificate("condition_conflict", nd, t, "cell",
                    {**opposite, "resolver_identity": inp["resolver_identity"],
                     "context": ev["period_plan"]["context"]}, version, cell=cell)
        ev["order"] = [bool(ev["execution_gaps"]), ("proven", "possible", "partial").index(ev["proof_status"]),
                       -len(ev["possible"]) / max(1, len(ev["cells"])),
                       -len(ev["supported"]) / max(1, len(ev["cells"])),
                       len(ev["conflicted"]) / max(1, len(ev["cells"])), len(ev["tables"]),
                       -sum(scores.get(t, 0) for t in ev["tables"]), [list(views).index(t) for t in ev["tables"]]]
        evaluated.append(ev)
    evaluated.sort(key=lambda a: a["order"])
    prefer_fewer_defaults(analysis, rid, evaluated, views, ft)
    for n, a in enumerate(evaluated, 1):
        a["id"] = f"{rid}.a{n}"
    chosen = next((a for a in evaluated if a["possible"]), None)
    gaps = ["model_structure_unrecorded"] if model and not structured else []
    if structured and scoped["polarity"]:
        gaps.append("unconfirmed_polarity")
    if not allocation["search_complete"] or (cut or {}).get("pool_cut"):
        gaps.append("search_truncated")
    if chosen:
        gaps += chosen["execution_gaps"]
        if len(chosen["tables"]) > 1 and any(ft[t].get("indicator", {}).get("weak") is not False for t in chosen["tables"]):
            gaps.append("frame_unverified")
        if chosen["proof_status"] != "proven":
            gaps.append("unproven_cover")
    return {"chosen": chosen, "alternatives": evaluated, "refutations": {"tables": table_refs, "cells": cell_refs},
            "candidate_ids": list(views), "alive_ids": alive, "facts": ft, "needs": nd,
            "allocation": allocation, "gaps": list(dict.fromkeys(gaps)), "scores": scores, "pool_cut": cut or {}}


def select_requirement(analysis, rid, layer, as_of, *, preferred_tables=None):
    req = requirement_of(analysis, rid)
    queries = candidates.build_queries(analysis, rid)
    channels = candidates.search_catalog(queries, layer)
    info = {}
    pool = candidates.expand_candidates(analysis, rid, channels, layer, info, as_of, candidate_policy="union")
    cut = facts.pool_cut(analysis, rid, channels, pool, layer, info)
    if req.get("status") == "unsupported":
        pool = []
    doc = check_evidence(analysis, [p["table_id"] for p in pool], layer, as_of, requirement_ids=[rid])
    got = compute(analysis, rid, pool, doc, layer, as_of, cut=cut)
    if preferred_tables is not None:
        choice = next((a for a in got["alternatives"] if set(a["tables"]) == set(preferred_tables)), None)
        if choice is None or choice["execution_gaps"]:
            raise ValueError("selected assignment is no longer executable")
        got["chosen"] = choice
        # Keep proof and uncertainty as computed; a user choice is not evidence.
    chosen = got["chosen"]
    views = selection_catalog(layer).views
    tables = [{"table_id": t, "org_id": views[t].org_id, "table_name": views[t].title,
               "covers": structure.covers(chosen["by_table"][t], chosen["request"], covers_of)} for t in chosen["tables"]] if chosen else []
    status = "no_table" if not tables else "confirm" if got["gaps"] else "auto"
    alternatives = [{"id": a["id"], "tables": a["tables"], "assignment": {rid: a["tables"]},
                     "complete": a["possible"] == a["cells"], "covered": len(a["possible"]), "cells": len(a["cells"]),
                     "label": "proven" if a["proof_status"] == "proven" else "possible",
                     "proof_status": a["proof_status"], "edition_links": [], "order": a["order"],
                     "gaps": a["execution_gaps"] + ([] if a["proof_status"] == "proven" else ["unproven_cover"])}
                    for a in got["alternatives"]]
    decision = {"unit": rid, "requirement_ids": [rid], "alternatives": alternatives,
                "dimensions": _dimensions(alternatives, views, {t: candidates.family_key(layer, t) for t in views}, got["needs"]),
                "cut": {"pool_cut": bool(cut.get("pool_cut")), "cover_cut": not got["allocation"]["search_complete"],
                        "decision_cut": False}, "gaps": got["gaps"],
                "today": {"tables": [t["table_id"] for t in tables], "in_alternatives": bool(chosen),
                          "first": bool(chosen) and chosen is got["alternatives"][0],
                          "alternative_id": chosen["id"] if chosen else None, "explained_by": []}}
    preview = {"needs": got["needs"], "refutations": got["refutations"], "decision": decision, "bundle": decision}
    record = _json({k: v for k, v in got.items() if k not in ("facts", "needs")})
    ready = bool(chosen and not chosen["execution_gaps"] and "model_structure_unrecorded" not in got["gaps"])
    record.update(mode="refutation", b_handoff_ready=ready, bundle=_json(chosen["bundle"]) if chosen else None,
                  execution_status="ready" if ready else "unresolved",
                  reference_candidates=[t for t in got["alive_ids"] if t not in [x["table_id"] for x in tables]])
    selection = {"requirement_id": rid, "tables": tables, "status": status, "model_step": "skipped",
                 "unspecified": [], "reasons": got["gaps"] if chosen else ["unsupported" if req.get("status") == "unsupported" else "no_cover"],
                 "alternatives": [a["tables"] for a in alternatives if not chosen or a["id"] != chosen["id"]],
                 "differences": [], "signals": {"plan17": record, "decision_preview": preview,
                     "selection_input": selection_input(analysis, rid, layer, as_of, with_structure=True), "facts": {"tables": got["facts"]},
                     "pool": len(pool), "queries": [q["text"] for q in queries], "pool_cut": cut,
                     "cells": len(chosen["cells"]) if chosen else 0, "covered": len(chosen["possible"]) if chosen else 0,
                     "period_check": bool(chosen and (chosen["execution_gaps"] or chosen["missing"]))}}
    if ready:
        selection["bundle"] = copy.deepcopy(chosen["bundle"])
    selection["reference_candidates"] = structure.reference_candidates(analysis, rid)
    return {"selection": selection, "evidence": doc, "state": {}}


def verify(analysis, selection, layer, as_of):
    """V2 recomputes certificates and shared periods; it does not trust recorded proof."""
    rid = selection["requirement_id"]
    ids = [t["table_id"] for t in selection["tables"]]
    pool = [{"table_id": t, "fused_score": 0, "channels": {}, "entered_by": "recheck",
             "family": candidates.family_key(layer, t), "rank": i} for i, t in enumerate(ids)]
    doc = check_evidence(analysis, ids, layer, as_of, requirement_ids=[rid])
    got = compute(analysis, rid, pool, doc, layer, as_of)
    same = next((a for a in got["alternatives"] if set(a["tables"]) == set(ids)), None)
    # Never substitute a different bundle during verification.
    floor = "no_table" if same is None or not same["possible"] else \
            "auto" if same["proof_status"] == "proven" and not got["gaps"] else "confirm"
    rc = {"floor": floor, "dropped": [t for t in ids if t in got["refutations"]["tables"]],
          "covered": len(same["possible"]) if same else 0, "cells": len(same["cells"]) if same else 0,
          "uncovered": len(same["missing"]) if same else 0, "flags": got["gaps"]}
    order = ("no_table", "confirm", "auto")
    state = min((selection["status"], floor), key=order.index)
    return {"state": state, "recheck": rc, "period_check": bool(same and (same["missing"] or same["execution_gaps"]))}
