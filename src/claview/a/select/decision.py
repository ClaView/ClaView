"""S6 decision (plan 11 r4, 2절 S6; R3 observation path, bundle decisions).

For one requirement, after the selection made today (which this module never changes):

1. S1 needs (``needs.py``);
2. the candidates: today's pool and the cut candidates restored on the table surface (C4, ``facts.restoration``;
   observation path only, K13), the restored ones checked here;
3. S4 refutation (``refute.py``, the only removals, each with a certificate);
4. S5: the requested cells over the tables left, the cells each gives (the refuted cells taken out), edition
   linking (tables of one family whose differences are only source and period are one alternative, members latest
   first: ``edition_links``), then every minimal cover with the partial or unproven assignments beside it (C7, K2),
   capped after the linking (K14: ``cover_cut``; a unit only a cut cover uses has the gap ``cover_cut``, not
   ``partial_cover``);
5. S6: the alternatives (every assignment not refuted), each with its edition links, its gaps (5절 codes computed
   again from the current assignment and obligations, C13: no history reason is a gap), ``proven`` (complete and
   no gap) or ``possible``, and an order key; the dimensions the alternatives differ by; whether today's selection
   is among them (and, when it is not, the refutation codes that explain it). R1c: each alternative records its
   indicator relation (``indicator_relation``: subject / component / undetermined / mixed), a ``relation``
   dimension when the alternatives differ on it (never a refutation), and a condition check judged against on a
   table left in the assignment is the gap ``unproven_condition`` (such an assignment is never proven).

Order key (used for the order only, P4), after ``cover.order_alternatives`` and the representative step: complete
first, fewer stale units for "the latest" (today's latest-end rule, now an order element), more covered cells,
fewer units, higher rank-score sum, higher evidence tier, family, candidate order.
R3 then combines the member decisions of each table_group_hint bundle (a member with no alternative takes the
empty assignment, ``member_no_table`` with its cause). The existing bundle choice and
common_family affect order only. Requirement contexts stay local; only the resulting bundle record is exposed.
Nothing here names a table, case or question.
"""

from __future__ import annotations

import itertools
import json
import math

from .. import paths
from ..catalog.facets import catalog_version
from . import refute
from .bundle import bundles, common_family
from .candidates import family_key
from .cover import (AXIS_KINDS, INDICATOR_VALUE, LATEST, _end, _rows, _start, all_cells, dimension_kinds,
                    enumerate_assignments, fresh, indicator_result, latest_ends, latest_group, question_words,
                    rank_score, requested_cells, score_items)
from .evidence import check_evidence, conditions_of, scope
from .facts import requirement_facts
from .needs import requirement_needs
from .tables import nospace, selection_catalog

DECISION_CAP = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))["observation_caps"]["DECISION_CAP"]

EDITION_KINDS = frozenset({"source", "period"})
GAP_CODES = ("indicator_weak", "unproven_condition", "partial_cover", "period_unknown", "cover_uncertain")
CONDITION_CHECKS = ("basis", "unit", "exclude", "frequency", "period")
RELATIONS = {"subject_candidate": "subject", "component": "component"}


def indicator_relation(facts_tables: dict[str, dict], tables: list[str]) -> str:
    """Plan 11 R1c (R4c, record only): the indicator relation of an assignment, read from the strong occurrences
    (``facts.STRONG_METHODS``) of its tables: subject (some occurrence is a subject candidate), component (else some
    is a component), undetermined (neither); tables of an assignment that differ give ``mixed``."""
    from .facts import STRONG_METHODS

    got = set()
    for t in tables:
        rels = {o["relation"] for o in ((facts_tables.get(t) or {}).get("indicator") or {}).get("occurrences") or []
                if o["method"] in STRONG_METHODS}
        got.add(next((RELATIONS[r] for r in RELATIONS if r in rels), "undetermined"))
    return got.pop() if len(got) == 1 else "mixed" if got else "undetermined"


# ---------------------------------------------------------------- edition linking


def editions(tables: list[str], views: dict, families: dict[str, str], order: dict[str, int]) -> list[list[str]]:
    """Groups of tables of one family whose pairwise differences are only source and period (``dimension_kinds``),
    joined transitively; members ordered latest end first (then the wider range, then the candidate order)."""
    parent = {t: t for t in tables}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_family: dict[str, list[str]] = {}
    for t in tables:
        by_family.setdefault(families[t], []).append(t)
    for members in by_family.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if find(a) != find(b) and set(dimension_kinds(views[a], views[b])) <= EDITION_KINDS:
                    parent[find(b)] = find(a)
    groups: dict[str, list[str]] = {}
    for t in tables:
        groups.setdefault(find(t), []).append(t)
    out = [sorted(g, key=lambda t: (-_end(views[t]), -(_end(views[t]) - _start(views[t])), order.get(t, 0)))
           for g in groups.values()]
    return sorted(out, key=lambda g: min(order.get(t, 0) for t in g))


# ---------------------------------------------------------------- gaps (5절, C13)


def assignment_gaps(needs_r: dict, req: dict, tables: list[str], facts_tables: dict[str, dict],
                    complete: bool) -> list[str]:
    """The gap codes of one assignment, from the current facts of its tables and the requirement's obligations."""
    gaps = []
    ev = needs_r["evidence"]
    fs = [facts_tables.get(t) or {} for t in tables]
    ind = ev["indicator"]
    if ind is not None and (not ind["mapped"] or any(
            (f.get("indicator") or {}).get("weak") is not False
            or ((f.get("indicator") or {}).get("check") or {}).get("origin") == "catalog_name" for f in fs)):
        gaps.append("indicator_weak")
    unnamed = any(not t["named"] for t in ev["targets"])
    # A split axis alone does not prove a list relation or its table-to-cell assignment.
    # The production cell prover checks those separately; this legacy preview stays conservative.
    against = any(r.get("check") in CONDITION_CHECKS
                  for f in fs for r in f.get("refuted") or [])
    if unnamed or against or any(u.get("check") != "period" for f in fs for u in f.get("unproven") or []) \
            or any((f.get("cross") or {}).get("independent_axes") == "unproven" for f in fs) \
            or any(r.get("found") is False or r.get("under") is False for f in fs for r in f.get("lower_items") or []):
        gaps.append("unproven_condition")
    if not complete:
        gaps.append("partial_cover")
    periods = [c for c in ev["conditions"] if c["kind"] == "period"]
    if "period_unknown" in req.get("flags", []) or "period_lag" in req.get("flags", []) or (periods and any(
            ((f.get("period") or {}).get("table") or {}).get("status") in (None, "unknown") for f in fs)):
        gaps.append("period_unknown")
    if any((f.get("cells") or {}).get("combinable") == "undetermined" for f in fs):
        gaps.append("cover_uncertain")
    return gaps


# ---------------------------------------------------------------- dimensions


def _dimensions(alts: list[dict], views: dict, families: dict[str, str], needs_r: dict) -> list[dict]:
    if len(alts) < 2:
        return []
    dims: dict[str, dict] = {}
    first = alts[0]
    for a in alts[1:]:
        if len(first["tables"]) == 1 and len(a["tables"]) == 1:
            x, y = first["tables"][0], a["tables"][0]
            kinds = dimension_kinds(views[x], views[y])
            if families.get(x) != families.get(y):
                kinds = {**kinds, "family": []}
        else:
            kinds = {"assignment": []}
        rx, ry = first.get("indicator_relation"), a.get("indicator_relation")
        if rx is not None and ry is not None and rx != ry:  # R1c (R4c): subject / component, shown by the ask
            kinds = {**kinds, "relation": [rx, ry]}
        for k, words in kinds.items():
            d = dims.setdefault(k, {"name": k, "kind": k, "values": [], "alternative_ids": []})
            d["values"] += [w for w in words if w not in d["values"]]
            d["alternative_ids"].append(a["id"])
    set_kinds = set()
    for s in needs_r["set"]:
        if s["kind"] == "frequency":
            set_kinds.add("frequency")
        elif s["kind"] == "basis":
            set_kinds.add(AXIS_KINDS.get(s["axis"] or "", "other"))
    for d in dims.values():
        d["set_by_question"] = d["kind"] in set_kinds
    return list(dims.values())


# ---------------------------------------------------------------- one requirement


def _scores(analysis: dict, rid: str, cands: list[dict], checks_by: dict, views: dict, given: dict) -> dict:
    """Rank scores (``cover.rank_score`` plus the score items) of the candidates; today's scores where given."""
    from .cover import title_bonus_words
    qns = title_bonus_words(analysis, rid)
    qwords = question_words(analysis, rid)
    out = {}
    for p in cands:
        t = p["table_id"]
        if t in given:
            out[t] = given[t]
            continue
        s = rank_score(p, checks_by.get(t, []), qns, views.get(t))
        items = score_items(views.get(t), qwords, [views[o["table_id"]] for o in cands
                                                   if o["table_id"] != t and o["table_id"] in views])
        out[t] = round(s + sum(x["value"] for x in items), 6)
    return out


def _stale_units(units: list[str], views: dict, families: dict[str, str], req: dict) -> set[str]:
    """For "the latest" (no period): the units whose head does not reach the latest end of its group or is past the
    snapshot lag of every candidate (``cover.provided_cells`` and ``cover.fresh``). An order element only (C11: no
    refutation), so that the latest table comes first as today."""
    if all(p[0] != LATEST for p in req["periods"]):
        return set()
    lgroups = {u: latest_group(views[u], families[u]) for u in units}
    latest = latest_ends({u: views[u] for u in units}, req, lgroups)
    out = set()
    for u in units:
        tops = latest["family"].get(lgroups[u], {})
        reach = any((e := max((h for _, _, h in _rows(views[u], [f])), default=None)) is not None
                    and tops.get(f) is not None and e >= tops[f] for f in req["frequencies"])
        if not reach or not fresh(views[u], req, latest):
            out.add(u)
    return out


def _today(selection: dict | None) -> list[str]:
    if not selection or selection.get("status") == "no_table":
        return []
    return [t["table_id"] for t in selection.get("tables") or []]


def requirement_preview(analysis: dict, rid: str, state: dict, selection: dict | None, layer,
                        as_of: str | None = None, *, context: dict | None = None) -> dict:
    """``signals.decision_preview`` of one requirement (module docstring): {needs, candidates, refutations, cells,
    decision}. Reads the requirement's selection state (pool, evidence, facts, pool_cut, cover) and changes
    nothing in it."""
    sel = selection_catalog(layer)
    views_all = sel.views
    version = catalog_version(layer)
    nd = requirement_needs(analysis, rid)
    pool = [p for p in state.get("pool") or [] if p["table_id"] in views_all]
    pool_ids = [p["table_id"] for p in pool]
    pc = state.get("pool_cut") or {}
    restored = [t for t in pc.get("restored") or [] if t in views_all and t not in pool_ids]
    checks_by = {e["table_id"]: e["checks"] for e in (state.get("evidence") or {}).get("evidence") or []
                 if e["requirement_id"] == rid}
    facts_tables = dict(((state.get("facts") or {}).get("tables")) or {})
    cands = list(pool)
    if restored:
        doc = check_evidence(analysis, restored, layer, as_of, requirement_ids=[rid])
        checks_by.update({e["table_id"]: e["checks"] for e in doc["evidence"]})
        extra = [{"table_id": t, "fused_score": 0.0, "channels": {}, "entered_by": "restored",
                  "family": family_key(layer, t), "rank": len(pool) + k} for k, t in enumerate(restored, 1)]
        facts_tables.update(requirement_facts(analysis, rid, extra, doc, layer, as_of)["tables"])
        cands += extra
    ids = [p["table_id"] for p in cands]
    order = {t: k for k, t in enumerate(ids)}
    views = {t: views_all[t] for t in ids}
    families = {t: family_key(layer, t) for t in ids}

    table_refs = refute.refute_tables(nd, {t: facts_tables.get(t) or {} for t in ids}, views, version)
    alive = [t for t in ids if t not in table_refs]
    req = requested_cells(analysis, rid, {t: views[t] for t in alive}, as_of)
    cells = all_cells(req)
    counted, _ = scope(analysis, rid)
    breakdown_keys = {c["id"]: (c.get("attrs") or {}).get("text") or c.get("value")
                      for c in conditions_of(analysis, "breakdown") if c["id"] in counted}
    cell_refs = refute.refute_cells(nd, facts_tables, views, req, cells, alive, version, breakdown_keys)
    prov = {t: {c for c in cells if refute.cell_key(c) not in cell_refs.get(t, {})} for t in alive}
    live = [t for t in alive if prov[t]]

    given = dict((state.get("cover") or {}).get("scores") or {})
    score = _scores(analysis, rid, [p for p in cands if p["table_id"] in live], checks_by, views, given)
    groups = editions(live, views, families, order)
    unit_of = {t: g[0] for g in groups for t in g}
    members = {g[0]: g for g in groups}
    uprov = {g[0]: set().union(*(prov[t] for t in g)) for g in groups}
    uscore = {g[0]: max(score.get(t, 0.0) for t in g) for g in groups}
    got = enumerate_assignments(list(members), cells, uprov, uscore)
    stale = _stale_units(list(members), views, families, req)

    alts = []
    in_cut = [{"units": [u], "complete": False, "in_cut_cover": True} for u in got.get("cut_units") or []]
    for a in [*got["covers"], *got["partial"], *in_cut]:
        units = a["units"]
        tables = [t for u in units for t in members[u]]
        covered = cells & set().union(*(uprov[u] for u in units))
        if a.get("in_cut_cover"):  # part of a cover past the cap: cover_cut, not partial_cover
            gaps = [*assignment_gaps(nd, req, tables, facts_tables, True), "cover_cut"]
        else:
            gaps = assignment_gaps(nd, req, tables, facts_tables, a["complete"])
        tier = max(INDICATOR_VALUE.get(r, 0.5 if r is None else 0.0)
                   for r in (indicator_result(checks_by.get(u, [])) for u in units))
        fams = sorted({families[u] for u in units})
        key = [not a["complete"], sum(1 for u in units if u in stale), -len(covered), len(units),
               -round(sum(uscore[u] for u in units), 6), -tier, fams[0] if len(fams) == 1 else "mixed",
               sorted(order[u] for u in units)]
        alts.append({"assignment": {rid: list(units)}, "tables": list(units), "complete": a["complete"],
                     "covered": len(covered), "cells": len(cells),
                     "edition_links": [{"head": u, "members": list(members[u])} for u in units if len(members[u]) > 1],
                     "gaps": gaps, "label": "proven" if a["complete"] and not gaps else "possible", "order": key,
                     "indicator_relation": indicator_relation(facts_tables, tables)})
    alts.sort(key=lambda a: a["order"])
    for k, a in enumerate(alts, 1):
        a["id"] = f"{rid}.a{k}"

    today = _today(selection)

    def same(a: dict) -> bool:
        us = [unit_of.get(t) for t in today]  # two editions of one unit chosen together are that unit
        return bool(today) and None not in us and set(us) == set(a["tables"])

    hit = next((a for a in alts if same(a)), None)
    explained: list[str] = []
    if today and hit is None:
        for t in today:
            if t in table_refs:
                explained += [c["code"] for c in table_refs[t]]
            elif t not in order:
                explained.append("not_candidate")
            elif not prov.get(t):
                explained += [c["code"] for cs in cell_refs.get(t, {}).values() for c in cs]
        if not explained:
            us = {unit_of[t] for t in today if t in unit_of}
            if got["cut"]:
                explained.append("cover_cut")
            elif any(set(a["tables"]) < us for a in alts if a["complete"]):
                explained.append("not_minimal")
            else:
                explained.append("unexplained")
        explained = list(dict.fromkeys(explained))
    pool_cut = bool(pc.get("pool_cut"))
    decision = {"unit": rid, "requirement_ids": [rid], "alternatives": alts,
                "dimensions": _dimensions(alts, views, families, nd),
                "cut": {"pool_cut": pool_cut, "cover_cut": bool(got["cut"]), "decision_cut": False},
                "gaps": [g for g, on in (("pool_cut", pool_cut), ("cover_cut", bool(got["cut"]))) if on],
                "today": {"tables": today, "in_alternatives": hit is not None,
                          "first": bool(alts) and hit is not None and hit is alts[0],
                          "alternative_id": hit["id"] if hit else None, "explained_by": explained}}
    if context is not None:
        context.update(needs=nd, request=req, facts=facts_tables)
    return {"needs": nd, "candidates": {"pool": len(pool_ids), "restored": restored, "alive": len(alive),
                                        "live": len(live), "editions": len(groups), "tried": got["tried"]},
            "refutations": {"tables": table_refs, "cells": cell_refs}, "cells": len(cells), "decision": decision}



# ---------------------------------------------------------------- bundle decision (R3, observation only)


def _expanded_tables(alt: dict, rid: str) -> list[str]:
    links = {e["head"]: e["members"] for e in alt["edition_links"]}
    return list(dict.fromkeys(t for head in alt["assignment"][rid] for t in links.get(head, [head])))


def _no_assignment_cause(prev: dict) -> str:
    """Why a requirement preview has no alternative, read from its counts (the first stage that left nothing)."""
    c = prev.get("candidates") or {}
    if not c.get("pool", 0) and not c.get("restored"):
        return "no_candidate"
    if not prev.get("cells", 0):
        return "no_requested_cells"
    if not c.get("live", 0):
        return "all_refuted"
    if ((prev.get("decision") or {}).get("cut") or {}).get("cover_cut"):
        return "cover_cut"
    return "no_cover"


def _empty_option(rid: str, prev: dict) -> dict:
    """The empty member assignment of a requirement with no alternative (kept so the other members stay)."""
    return {"id": f"{rid}.none", "assignment": {rid: []}, "tables": [], "complete": False, "covered": 0,
            "cells": prev.get("cells", 0), "edition_links": [], "gaps": ["member_no_table"], "label": "possible",
            "order": [], "empty": True, "cause": _no_assignment_cause(prev)}


def bundle_preview(members: list[str], selections: dict[str, dict], states: dict[str, dict],
                   contexts: dict[str, dict], layer, *, cap: int | None = None) -> dict:
    """Cartesian product of non-refuted member assignments, after each member's edition linking.

    No requirement is omitted. A member with no alternative (every candidate refuted, no candidate, no requested
    cell) takes the empty assignment ``[]`` with its cause (``member_no_table``), so the other members keep their
    alternatives; a member whose current selection has no table matches that empty assignment.
    Today's assignment, if present, comes first; common_family is the next order preference only.
    C13: recompute gaps for each current member assignment and its current obligations, never from
    selection status, reasons or the history of the bundle replacement. A bounded product records the
    exact number omitted as decision_cut; it never pads a partial tuple into a full assignment.
    """
    cap = DECISION_CAP if cap is None else cap
    if cap < 1:
        raise ValueError("decision cap must be positive")
    previews = {r: selections[r]["signals"]["decision_preview"] for r in members}
    source = {r: previews[r]["decision"] for r in members}
    unit = "bundle:" + json.dumps(members, ensure_ascii=False, separators=(",", ":"))
    covers = {r: states.get(r, {}).get("cover") or {} for r in members}
    choices = {r: states.get(r, {}).get("choice") or {} for r in members}
    preferred = common_family(members, covers, choices)
    options = {r: sorted(source[r]["alternatives"], key=lambda a: a["order"]) or [_empty_option(r, previews[r])]
               for r in members}
    empty = {r: options[r][0]["cause"] for r in members if options[r][0].get("empty")}
    total = math.prod(len(options[r]) for r in members)
    current = {r: (options[r][0] if not _today(selections[r]) else None) if r in empty else
               next((a for a in options[r] if a["id"] == source[r]["today"]["alternative_id"]), None)
               for r in members}
    today_possible = all(current.values())
    families = {t: family_key(layer, t) for r in members for a in options[r] for t in a["tables"]}

    def common(alt: dict) -> bool:
        if alt.get("empty"):
            return True  # neutral: the preference is decided by the members that have tables
        return preferred is not None and bool(alt["tables"]) and all(
            families[t] == preferred for t in alt["tables"])

    common_options = {r: [a for a in options[r] if common(a)] for r in members}
    # Enumerate preferences first, without materialising the possibly enormous Cartesian product.
    # Each phase preserves all remaining combinations until the recorded cap, including mixed families.
    def combinations():
        if today_possible:
            yield tuple(current[r] for r in members)
        yield from itertools.product(*(common_options[r] for r in members))
        yield from itertools.product(*(options[r] for r in members))

    gaps_by_alt = {}
    for r in members:
        ctx = contexts[r]
        for a in options[r]:
            if a.get("empty"):
                gaps_by_alt[r, a["id"]] = list(a["gaps"])
                continue
            if "cover_cut" in a["gaps"]:  # from cut_units: keep cover_cut, never partial_cover (as in S5)
                gaps_by_alt[r, a["id"]] = [*assignment_gaps(
                    ctx["needs"], ctx["request"], _expanded_tables(a, r), ctx["facts"], True), "cover_cut"]
                continue
            gaps_by_alt[r, a["id"]] = assignment_gaps(
                ctx["needs"], ctx["request"], _expanded_tables(a, r), ctx["facts"], a["complete"])

    seen, alts = set(), []
    for combo in combinations():
        signature = tuple(a["id"] for a in combo)
        if signature in seen:
            continue
        if len(alts) == cap:
            break
        seen.add(signature)
        is_today = today_possible and all(a is current[r] for r, a in zip(members, combo))
        gaps_by_r = {r: list(gaps_by_alt[r, a["id"]]) for r, a in zip(members, combo)}
        gaps = list(dict.fromkeys(g for r in members for g in gaps_by_r[r]))
        complete = all(a["complete"] for a in combo)
        alts.append({
            "assignment": {r: list(a["assignment"][r]) for r, a in zip(members, combo)},
            "member_alternative_ids": dict(zip(members, signature)),
            "edition_links": [{"requirement_id": r, "head": e["head"], "members": list(e["members"])}
                              for r, a in zip(members, combo) for e in a["edition_links"]],
            "complete": complete, "covered": sum(a["covered"] for a in combo),
            "cells": sum(a["cells"] for a in combo), "gaps": gaps, "gaps_by_requirement": gaps_by_r,
            "member_no_table": {r: empty[r] for r in members if r in empty},
            "label": "proven" if complete and not gaps else "possible",
            "order": [not is_today, not all(common(a) for a in combo), [a["order"] for a in combo]],
        })
    alts.sort(key=lambda a: a["order"])
    for k, a in enumerate(alts, 1):
        a["id"] = f"{unit}.a{k}"
    hit = next((a for a in alts if today_possible and all(
        a["member_alternative_ids"][r] == current[r]["id"] for r in members)), None)

    # Compare member projections, not table unions: swapped members are different assignments.
    views = selection_catalog(layer).views
    dimensions = []
    for r in members:
        used = {a["member_alternative_ids"][r] for a in alts}
        projected = [a for a in options[r] if a["id"] in used]
        for d in _dimensions(projected, views, families, contexts[r]["needs"]):
            differ = set(d["alternative_ids"])  # member alternatives that differ from the first on this dimension
            dimensions.append({**d, "name": f"{r}:{d['name']}", "requirement_ids": [r],
                               "alternative_ids": [a["id"] for a in alts
                                                   if a["member_alternative_ids"][r] in differ]})
    cuts = {k: any(source[r]["cut"].get(k, False) for r in members) for k in ("pool_cut", "cover_cut")}
    cuts["decision_cut"] = total > len(alts)
    explained = list(dict.fromkeys(code for r in members if current[r] is None
                                  for code in (source[r]["today"]["explained_by"] or ["no_assignment"])))
    return {"unit": unit, "requirement_ids": list(members), "alternatives": alts,
            "dimensions": dimensions, "cut": cuts, "gaps": [k for k, cut in cuts.items() if cut],
            "combination_count": total, "enumerated": len(alts), "omitted": total - len(alts), "cap": cap,
            "member_no_table": dict(empty),
            "order_preference": {"common_family": preferred, "current_choice_first": True},
            "today": {"assignment": {r: _today(selections[r]) for r in members},
                      "in_alternatives": hit is not None, "first": hit is not None and hit is alts[0],
                      "alternative_id": hit["id"] if hit else None, "explained_by": explained}}


def attach_bundles(analysis: dict, results: list[dict], contexts: dict[str, dict], layer) -> None:
    """Write only decision_preview.bundle. Singleton units reuse the R1b requirement decision unchanged."""
    by_rid = {r["selection"]["requirement_id"]: r["selection"] for r in results}
    states = {r["selection"]["requirement_id"]: r.get("state") or {} for r in results}
    assigned = set()
    for members in bundles(analysis):
        # collect_selections supplies every requirement, including those whose selection is no_table.
        if not all(r in by_rid for r in members):
            raise ValueError("bundle member missing from selections")
        d = bundle_preview(members, by_rid, states, contexts, layer)
        for r in members:
            by_rid[r]["signals"]["decision_preview"]["bundle"] = d
        assigned.update(members)
    for r, selection in by_rid.items():
        if r not in assigned:
            prev = selection["signals"]["decision_preview"]
            prev["bundle"] = prev["decision"]


def decisions_from(selections: list[dict]) -> dict[str, dict]:
    """One D record per hinted bundle, otherwise per requirement; accept older requirement previews too."""
    out = {}
    for s in selections:
        prev = ((s.get("signals") or {}).get("decision_preview")) or {}
        d = prev["bundle"] if prev.get("bundle") is not None else prev.get("decision")
        if d is not None:
            out[d["unit"]] = d
    return out
