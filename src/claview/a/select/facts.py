"""Evidence facts (plan 11, E and 7-2 to 7-12; R0 record mode): one record per (requirement, candidate table).

The existing checks (``evidence.py``) stay the producers and their verdicts are read as they are; this module only
gathers what they found into one record and adds what they did not keep apart. Nothing here filters, orders or
changes a state: the records go into the new selection fields ``signals.facts``, ``signals.pool_cut`` (and the
cover module's ``signals.covers_all`` and ``signals.pruned``).

Per (requirement, table):
- ``indicator``: every occurrence of the requirement's indicator name in the table (field title / path / value, value
  ID, value role, parent chain, match method exact / exact_with_qualifier / derived / partial / partial_words) and
  its relation (subject_candidate / component / mention / undetermined), decided from the value role, the parents,
  the siblings and the table's subject names; never from "the top value of the indicator axis". ``weak`` (7-5):
  no occurrence that is both a subject candidate or a component and exact, exact with a qualifier or derived.
- ``conditions``: the verdicts of the basis / unit / exclude checks as they are, with, for a negation, whether the
  forbidden value is present among others or the table holds only it, and for an X-byeol word, axis mapping and
  title proof apart; ``cross``: two X-byeol words proven by the title only are never two independent axes.
- ``period``: the facts that tell tables apart (covered / partial / outside / unknown) apart from the facts handed to
  B (snapshot lag, unresolved period); ``period_anchor`` (7-7) per frequency over the candidates whose indicator
  evidence is certain, and ``by_anchor`` (covered / partial against that anchor).
- ``cells``: whether the requested values can be read together in this table, from the parent chains (possible /
  impossible / undetermined / n/a).
- ``refuted`` (a check judged against) and ``unproven`` (no verdict either way, or no check) kept apart.
Per requirement: ``identity`` = fragment IDs + condition-combination key (7-11).

``pool_cut`` (7-12): which caps cut search candidates; a cut is marked only when some cut table holds the
indicator name exactly, with a qualifier, or as a derived name (checked on the table facets).

Nothing here names a table, case or question; the words compared come from the catalog and the question.
"""

from __future__ import annotations

import itertools
import json

from .. import paths

from ..catalog.facets import DERIVED, PATH_LEAF, TITLE_CORE, catalog_version, facet_of
from ..catalog.text import index_key
from ..reused import q_rules
from .evidence import (HANDOFF_ONLY_AXES, axis_of, basis_forms, conditions_of, is_byeol, items_for, linked_members,
                       polarity_of, requested, requirement_of, scope, target_forms, target_names, value_of)
from .tables import nospace, selection_catalog
from . import model_refs

STRONG_METHODS = ("exact", "exact_with_qualifier", "derived")
CERTAIN_RELATIONS = ("subject_candidate", "component")
PARTIAL_CAP = 5  # partial occurrences written out per table (the count is kept)
CUT_MATCHES_CAP = 10  # cut tables with a strong match written out (the count is kept)
_PERIOD_SPLIT = ("covered", "partial", "outside")


# ---------------------------------------------------------------- requirement identity (7-11)


def _period_expr(e: dict | None) -> str:
    e = e or {}
    return "/".join(str(e.get(k) or "") for k in ("year", "alias", "sub_unit", "sub_value", "end_mark"))


def _condition_signature(c: dict) -> str:
    a = c.get("attrs") or {}
    sig = ":".join(str(x) for x in (c.get("kind"), a.get("axis") or "", a.get("value") or c.get("value") or "",
                                    a.get("polarity") or "+"))
    if c.get("kind") == "period":  # plan 11 R1b (C8): the period meaning: form, range and its frequency unit
        sig += ":" + ":".join(str(x) for x in (a.get("form") or "", _period_expr(a.get("start_expr")),
                                                _period_expr(a.get("end_expr")), a.get("amount") or "",
                                                a.get("unit") or "", "relative" if a.get("relative") else ""))
    return sig


def requirement_identity(analysis: dict, rid: str) -> dict:
    """{fragment_ids, conditions, key}: the fragment IDs of the requirement's draft (the judgement drafts are in
    requirement order, 2.7) and the signatures of its own and shared conditions (kind, axis, value, polarity), so
    that copies of one indicator per basis combination keep distinct keys."""
    reqs = (analysis.get("interpretation") or {}).get("requirements") or []
    k = next((n for n, r in enumerate(reqs) if r.get("id") == rid), None)
    req = requirement_of(analysis, rid)
    drafts = (analysis.get("judgement") or {}).get("requirement_draft") or []
    own_fragments = model_refs.fragment_ids(analysis, rid)
    if own_fragments is not None:
        frags = own_fragments
    elif k is not None and k < len(drafts) and len(drafts) == len(reqs):
        frags = list(drafts[k].get("fragment_ids") or [])
    else:
        lo, hi = req.get("span") or [0, 0]
        frags = [f["id"] for f in analysis.get("fragments") or [] if f["span"][0] < hi and lo < f["span"][1]]
    by_id = {c["id"]: c for c in analysis.get("conditions") or []}
    own = list(req.get("condition_ids") or []) + list(req.get("shared_condition_ids") or [])
    conds = sorted(_condition_signature(by_id[c]) for c in dict.fromkeys(own) if c in by_id)
    frags = sorted(dict.fromkeys(frags))
    key = ",".join(frags) + "#" + "|".join(conds)
    if own_fragments is not None:
        key = str(req.get("reading_id")) + ":" + rid + ":" + ",".join(req["refs"]["segment_ids"]) + "#" + key
    return {"fragment_ids": frags, "conditions": conds, "key": key}


# ---------------------------------------------------------------- indicator occurrences


def indicator_name(req: dict) -> str | None:
    ind = req.get("indicator") or {}
    return ind.get("name") or ind.get("quote") or None


def _value_relation(facet, v) -> str:
    """Relation of an indicator name found at value ``v`` of the table (module docstring)."""
    if v.role == "series_name":
        return "subject_candidate"  # the ITEM row carries the table's own name
    axis = facet.axis(v.obj_id)
    if axis is None or axis["role"] in ("unknown", "human") or v.role in ("unknown", "compound"):
        return "undetermined"
    if v.role in ("target", "basis"):
        return "mention"
    subjects = {s["key"] for s in facet.subjects}
    if v.key in subjects or index_key(_bare(v.name)) in subjects:
        return "subject_candidate"
    if v.parents:
        parent = facet.value(v.obj_id, v.parents[0])
        if parent is not None and parent.role in ("indicator", "total_header", "series_name"):
            return "component"
        return "undetermined"
    if v.role == "indicator" and v.root_siblings > 0:
        return "component"  # one of several listed values under the table's subject
    return "undetermined"


def _bare(name: str) -> str:
    from ..catalog.text import strip_parens

    return strip_parens(name or "")


def _occ(field: str, method: str, relation: str, v=None, source: str | None = None) -> dict:
    out = {"field": field, "method": method, "relation": relation, "value_id": None, "obj_id": None,
           "value_role": None, "parents": [], "anomaly": None, "source": source}
    if v is not None:
        out.update(value_id=v.item_id, obj_id=v.obj_id, value_role=v.role, parents=list(v.parent_names),
                   anomaly=v.anomaly, name=v.name)
    return out


def strong_occurrences(facet, key: str) -> list[dict]:
    """Occurrences matched exactly, exactly with a qualifier, or by a derived name (subjects and values)."""
    out: list[dict] = []
    if not key or facet is None:
        return out
    full = index_key(facet.title)
    core_hit = False
    for s in facet.subjects:
        if s["key"] != key:
            continue
        if s["source"] == TITLE_CORE:
            core_hit = True
            out.append(_occ("title", "exact" if full == key else "exact_with_qualifier", "subject_candidate",
                            source=s["source"]))
        elif s["source"] == PATH_LEAF:
            out.append(_occ("path", "exact", "subject_candidate", source=s["source"]))
        elif s["source"].startswith(DERIVED):
            out.append(_occ("title", "derived", "subject_candidate", source=s["source"]))
    if full == key and not core_hit:
        out.append(_occ("title", "exact", "subject_candidate", source="title"))
    seen: set[int] = set()
    for method, positions in (("exact", facet.by_key.get(key) or []),
                              ("exact_with_qualifier", facet.by_bare.get(key) or []),
                              ("derived", facet.derived_values.get(key) or [])):
        for pos in positions:
            if pos in seen:
                continue
            seen.add(pos)
            v = facet.values[pos]
            out.append(_occ("value", method, _value_relation(facet, v), v))
    return out


def indicator_facts(analysis: dict, rid: str, facet, checks: list[dict]) -> dict:
    """The ``indicator`` record of one (requirement, table) (module docstring)."""
    req = requirement_of(analysis, rid)
    name = indicator_name(req)
    check = next((c for c in checks if c["name"] == "indicator"), None)
    detail = (check or {}).get("detail") or {}
    rec = {"name": name, "check": {"available": detail.get("available"), "result": detail.get("result"),
                                   "origin": detail.get("origin")},
           "occurrences": [], "partial_count": 0, "weak": None, "weak_reasons": [], "absent": None,
           "mention_only": None}
    refs = model_refs.references(analysis, rid)
    if refs is not None:
        rec["reference_candidates"] = [dict(c) for r in refs if r["place"] == "indicator"
                                       for c in r["candidates"] if facet and c["table_id"] == facet.table_id]
    if not name or facet is None:
        return rec
    key = index_key(name)
    occ = strong_occurrences(facet, key)
    strong_values = {(o["obj_id"], o["value_id"]) for o in occ if o["field"] == "value"}
    partial = []
    inside = 0  # R1c: partial occurrences where the whole name sits inside a longer name (title or value)
    full = index_key(facet.title)
    if len(key) >= 2 and not any(o["field"] == "title" for o in occ) and key in full:
        partial.append(_occ("title", "partial", "undetermined", source="title"))
        inside += 1
    for v in facet.values:
        if (v.obj_id, v.item_id) in strong_values or len(v.key) < 2 or len(key) < 2:
            continue
        if key in v.key or v.key in key:
            partial.append(_occ("value", "partial", _value_relation(facet, v), v))
            inside += key in v.key
    if detail.get("result") == "partial":
        for f, r in (detail.get("by_field") or {}).items():
            if r == "partial":
                partial.append(_occ(f if f != "catalog_path" else "path", "partial_words", "undetermined",
                                    source="indicator_check"))
    rec["partial_count"] = len(partial)
    rec["occurrences"] = occ + partial[:PARTIAL_CAP]
    # plan 11 R1b (K1): absent only when the name occurs in no way at all: no exact, qualified or derived occurrence,
    # no partial one on the facets, and the word check found neither all nor some of the words
    rec["absent"] = not occ and not partial and detail.get("result") not in ("match", "partial")
    # plan 11 R1c (R4b): the name stands on the table only inside other, longer names: no exact, qualified or
    # derived occurrence, every partial occurrence holds the whole name, and no word of it is missing
    rec["mention_only"] = not occ and bool(partial) and inside == len(partial)
    certain = [o for o in occ if o["relation"] in CERTAIN_RELATIONS and o["method"] in STRONG_METHODS]
    rec["weak"] = not certain
    if not certain:
        reasons = []
        if not occ and not partial:
            reasons.append("no_occurrence")
        if not occ and partial:
            reasons.append("partial_only")
        rels = {o["relation"] for o in occ}
        if "undetermined" in rels:
            reasons.append("relation_undetermined")
        if rels and rels <= {"mention"}:
            reasons.append("mention_only")
        rec["weak_reasons"] = reasons
    return rec


# ---------------------------------------------------------------- conditions


def _has_any(text: str, forms) -> bool:
    return any(nospace(f) and nospace(f) in text for f in forms)


def _negation(view, forms: list[str], title_hit: bool, *, axis: str | None = None,
              value: str | None = None) -> str:
    """forbidden_only (the table is the forbidden value only: no other value on the axes holding it),
    forbidden_present (among other values: B can leave it out; or the table name says it and no value axis shows
    it, which proves nothing either way), absent. Plan 11 R1b (C6): the table name alone never gives
    forbidden_only; a value axis has to show it."""
    from .basis import values

    def matches(text):
        return value in values(text, axis, value) if axis else _has_any(text, forms)

    hits = [it for it in view.items if matches(it.name_nospace)]
    if not hits:
        return "forbidden_present" if title_hit else "absent"
    objs = {it.obj_id for it in hits}
    others = [it for it in view.items if it.obj_id in objs and not matches(it.name_nospace)]
    if axis and any(values(it.name_nospace, axis, value) - {value} for it in hits):
        return "forbidden_present"
    return "forbidden_present" if others else "forbidden_only"


def basis_value_facts(view, axis: str, value: str) -> dict:
    """Plan 11 R1c: where the requested value of a positive basis word and the other values of its basis axis
    (``evidence.basis_forms``) occur on the table: title (with its bracket qualifiers), catalog path, value names,
    axis names. ``title_conflict``: the title carries another value of the axis and the requested value occurs
    nowhere on the table (a catalog fact: the table is of the opposite basis). Both values present, or no basis
    word in the title, is no conflict. ``title_words``: whether the title carries any value of the axis."""
    from .basis import occurrences

    places = {"title": [view.title], "path": [view.path],
              "value": [it.name_raw for it in view.items], "axis": [name for _, name in view.dims]}
    hits = [{"field": place, "index": i, **hit}
            for place, texts in places.items() for i, text in enumerate(texts)
            for hit in occurrences(text, axis, value)]
    requested_in = [p for p in places if any(h["field"] == p and h["value"] == value for h in hits)]
    opposite = sorted({h["form"] for h in hits if h["field"] == "title" and h["value"] != value})
    return {"requested_in": requested_in, "opposite_in_title": opposite,
            "title_words": bool(opposite) or "title" in requested_in,
            "title_conflict": bool(opposite) and not requested_in, "occurrences": hits}


def _byeol(analysis: dict, c: dict, view, matches: list[dict]) -> dict:
    value = value_of(c)
    stem = value[:-1]
    title = value in view.title_ns
    mapped = any(stem and stem in d for d in view.dim_names_ns) or any(
        stem and (n == stem or n.startswith(stem)) for n in view.item_names_ns)
    members = linked_members(analysis, c, matches) if not mapped else []
    return {"axis_mapping": bool(mapped or members), "title_proof": title, "members": members[:4]}


def condition_facts(analysis: dict, rid: str, view, checks: list[dict]) -> tuple[list[dict], dict | None]:
    """(conditions, cross): the basis / unit / exclude verdicts as the checks gave them, with the negation and
    X-byeol facts apart (module docstring)."""
    counted, amb = scope(analysis, rid)
    by_id = {c["id"]: c for c in analysis.get("conditions") or []}
    matches = next(((c.get("detail") or {}).get("rows") or [] for c in checks if c["name"] == "matched_items"), [])
    out = []
    for ch in checks:
        if ch["name"] not in ("basis", "unit", "exclude"):
            continue
        cid = (ch.get("detail") or {}).get("condition_id")
        c = by_id.get(cid) or {}
        row = {"condition_id": cid, "kind": c.get("kind") or ch["name"], "check": ch["name"],
               "verdict": ch["verdict"], "status": ch.get("source_status"),
               "source": (ch.get("detail") or {}).get("source"), "role": (ch.get("detail") or {}).get("role"),
               "counted": cid in counted, "ambiguous": cid in amb, "negation": None, "byeol": None}
        row["basis"] = None
        if view is not None and ch["name"] == "basis":
            if is_byeol(c):
                row["byeol"] = _byeol(analysis, c, view, matches)
            elif polarity_of(c) == "+" and c.get("kind") == "basis":
                row["basis"] = basis_value_facts(view, axis_of(c), value_of(c))
            elif polarity_of(c) == "-":
                from .basis import values

                forms = list(basis_forms(axis_of(c), value_of(c)))
                row["negation"] = _negation(view, forms,
                                            value_of(c) in values(view.title_ns, axis_of(c), value_of(c)),
                                            axis=axis_of(c), value=value_of(c))
        elif view is not None and ch["name"] == "exclude":
            name = nospace((c.get("attrs") or {}).get("text") or c.get("value") or "")
            if name:
                row["negation"] = _negation(view, [name], name in view.title_ns)
                if (ch.get("detail") or {}).get("complement_found") and row["negation"] == "absent":
                    row["negation"] = "complement"
        out.append(row)
    byeol = [r for r in out if r["byeol"] is not None and r["counted"]]
    cross = None
    if len(byeol) >= 2:
        check = next((c for c in checks if c["name"] == "cross_axis"), None)
        support = ((check or {}).get("detail") or {}).get("support")
        title_only = sum(1 for r in byeol if r["byeol"]["title_proof"] and not r["byeol"]["axis_mapping"])
        cross = {"support": support, "title_only_words": title_only,
                 "independent_axes": "supported" if support == "supported" else "unproven"}
    return out, cross


def proof_ids(analysis: dict, rid: str) -> dict[str, list[str]]:
    """The requirement's own and shared conditions a verdict is needed for (as ``cover.requested_cells`` lists
    them): period, frequency, basis (hand-off axes aside), unit, exclude."""
    counted, _ = scope(analysis, rid)
    return {"period": [c["id"] for c in conditions_of(analysis, "period") if c["id"] in counted],
            "frequency": [c["id"] for c in conditions_of(analysis, "frequency") if c["id"] in counted],
            "basis": [c["id"] for c in conditions_of(analysis, "basis") if c["id"] in counted
                      and axis_of(c) not in HANDOFF_ONLY_AXES],
            "unit": [c["id"] for c in conditions_of(analysis, "unit") if c["id"] in counted],
            "exclude": [c["id"] for c in conditions_of(analysis, "exclude") if c["id"] in counted]}


def refuted_unproven(analysis: dict, rid: str, checks: list[dict]) -> tuple[list[dict], list[dict]]:
    """(refuted, unproven): a check judged against (verdict fail) and a needed fact with no verdict either way or no
    check at all (7-5: a missing fact is unproven), kept apart."""
    proof = proof_ids(analysis, rid)
    refuted, unproven = [], []
    by_cond = {(c["name"], (c.get("detail") or {}).get("condition_id")): c for c in checks}
    for name in ("basis", "unit", "exclude"):
        for cid in proof[name]:
            c = by_cond.get((name, cid))
            if c is None:
                unproven.append({"check": name, "condition_id": cid, "why": "no_check"})
            elif c["verdict"] == "fail":
                refuted.append({"check": name, "condition_id": cid})
            elif c["verdict"] != "pass":
                unproven.append({"check": name, "condition_id": cid, "why": c.get("source_status") or "n/a"})
    for name in ("frequency", "period"):
        if not proof[name]:
            continue
        c = next((x for x in checks if x["name"] == name), None)
        if c is None:
            unproven.append({"check": name, "condition_id": None, "why": "no_check"})
        elif c["verdict"] == "fail":
            refuted.append({"check": name, "condition_id": None})
        elif c["verdict"] != "pass":
            unproven.append({"check": name, "condition_id": None, "why": c.get("source_status") or "n/a"})
    for name in ("indicator", "region"):
        c = next((x for x in checks if x["name"] == name), None)
        if c is not None and c["verdict"] == "fail":
            refuted.append({"check": name, "condition_id": None})
    return refuted, unproven


# ---------------------------------------------------------------- period


def _label(m: int) -> str:
    return f"{m // 12:04d}-{m % 12 + 1:02d}"


def _months(stamp, end: bool = False) -> int | None:
    got = q_rules.stamp_months(stamp)
    return None if got is None else got[1 if end else 0]


def period_request(analysis: dict, rid: str, as_of: str | None) -> dict:
    periods, wanted, env = requested(analysis, rid, as_of)
    relative = any((c.get("attrs") or {}).get("relative") or (c.get("attrs") or {}).get("form") == "recent"
                   for c in periods)
    return {"requested": bool(periods), "relative": relative, "frequencies": list(wanted), "envelope": env}


def period_facts(check: dict | None) -> dict:
    """Table-separating facts and B-notice facts of the period check, kept apart."""
    detail = (check or {}).get("detail") or {}
    rows = [r for r in detail.get("by_frequency") or [] if r.get("requested")]
    table_rows, lag, unresolved = [], False, False
    for r in rows:
        status = r.get("status")
        if r.get("reason") == "snapshot_lag":
            lag = True
        if r.get("reason") == "period_unresolved":
            unresolved = True
        table_rows.append({"frequency": r.get("frequency"), "status": status if status in _PERIOD_SPLIT else
                           "unknown", "coverage": list(r.get("coverage") or [])})
    split = [r["status"] for r in table_rows]
    status = None
    if detail.get("request") is not None and split:
        status = next((s for s in ("covered", "partial", "outside") if s in split), None) or "unknown"
        if "covered" not in split and "unknown" in split:
            status = "unknown"
    return {"table": {"status": status, "by_frequency": table_rows},
            "notice": {"snapshot_lag": lag, "period_unresolved": unresolved}}


def period_anchors(request: dict, ends: dict[str, dict[str, int]], certain: set[str], version: str) -> list[dict]:
    """7-7: per frequency, the anchor of a relative period is the latest end among the candidates whose indicator
    evidence is certain; a calendar period keeps the reference date (anchor None). ``ends``: table -> frequency ->
    last month."""
    if not request["requested"]:
        return []
    env = request["envelope"] or {}
    lo = _months(env.get("start")) if env.get("status") == "explicit" else None
    hi = _months(env.get("end"), True) if env.get("end") else None
    freqs = request["frequencies"] or sorted({f for e in ends.values() for f in e})
    out = []
    for f in freqs:
        tops = [e[f] for t, e in ends.items() if t in certain and f in e]
        anchor = max(tops) if tops and request["relative"] else None
        rng = None
        if lo is not None:
            if anchor is not None:
                length = (hi if hi is not None else anchor) - lo
                rng = [_label(anchor - length), _label(anchor)]
            else:
                rng = [_label(lo), _label(hi) if hi is not None else None]
        out.append({"frequency": f, "anchor": _label(anchor) if anchor is not None else None,
                    "resolved_range": rng, "catalog_version": version, "relative": request["relative"],
                    "candidates": len(tops)})
    return out


def _by_anchor(anchors: list[dict], view) -> str | None:
    """covered / partial against the anchor records of a relative period (None when none applies)."""
    got = None
    for a in anchors:
        if a["anchor"] is None or not a["resolved_range"]:
            continue
        cov = next(((s, e) for f, s, e in view.periods if f == a["frequency"]), None)
        if cov is None:
            continue
        start, end = _months(cov[0]), _months(cov[1], True)
        want_lo = _months(a["resolved_range"][0])
        want_hi = _months(a["resolved_range"][1], True)
        ok = None not in (start, end, want_lo, want_hi) and start <= want_lo and end >= want_hi
        got = "covered" if ok and got in (None, "covered") else "partial"
    return got


# ---------------------------------------------------------------- cells: can the requested values be read together


def _elements(analysis: dict, rid: str, view, facet, indicator: dict, checks: list[dict], layer) -> dict:
    """Requested parts -> value positions (obj_id, item_id) of the table: the indicator (strong value occurrences),
    each target, each positive basis word matched at a value."""
    out: dict[str, list[tuple[str, str]]] = {}
    ind = [(o["obj_id"], o["value_id"]) for o in indicator.get("occurrences") or []
           if o["field"] == "value" and o["method"] in STRONG_METHODS]
    if ind:
        out["indicator"] = ind
    for name in target_names(analysis, rid):
        vals = [(it.obj_id, it.item_id) for f in target_forms(analysis, rid, name) for it in items_for(view, f)]
        if vals:
            out[f"target:{name}"] = list(dict.fromkeys(vals))
    counted, _ = scope(analysis, rid)
    by_id = {c["id"]: c for c in analysis.get("conditions") or []}
    rows = next(((c.get("detail") or {}).get("rows") or [] for c in checks if c["name"] == "matched_items"), [])
    for r in rows:
        cid = r.get("condition_id")
        if cid in counted and r.get("polarity") == "+" and (by_id.get(cid) or {}).get("kind") == "basis" \
                and not is_byeol(by_id[cid]):
            out.setdefault(f"basis:{cid}", [])
            if (r["obj_id"], r["item_id"]) not in out[f"basis:{cid}"]:
                out[f"basis:{cid}"].append((r["obj_id"], r["item_id"]))
    return out


def _pair(facet, a: tuple[str, str], b: tuple[str, str]) -> str:
    if a[0] != b[0] or a == b:
        return "possible"
    va, vb = facet.value(*a), facet.value(*b)
    if va is None or vb is None or va.anomaly or vb.anomaly:
        return "undetermined"
    if a[1] in vb.parents or b[1] in va.parents:
        return "possible"
    return "impossible"


def basis_exclusive(analysis: dict, rid: str) -> set[frozenset]:
    """Pairs of ``basis:<id>`` parts never asked in one cell: two positive values of one basis axis are split into
    cells of their own (``cover.requested_cells``)."""
    counted, _ = scope(analysis, rid)
    by_axis: dict[str, list[str]] = {}
    for c in conditions_of(analysis, "basis"):
        if c["id"] in counted and polarity_of(c) == "+" and not is_byeol(c):
            by_axis.setdefault(axis_of(c), []).append(c["id"])
    return {frozenset((f"basis:{a}", f"basis:{b}")) for ids in by_axis.values() if len(ids) >= 2
            for a, b in itertools.combinations(ids, 2)}


def cell_facts(elements: dict, facet, exclusive: set[frozenset] | None = None) -> dict:
    """possible / impossible / undetermined / n/a: two requested parts of one cell on the same axis are read
    together only when one value lies under the other (one parent chain); parts on different axes always combine.
    Targets are cells of their own and are not paired with each other. Plan 11 R1b (K4): two basis parts are
    compared too (different basis conditions on two branches of one physical axis are impossible), except the
    ``exclusive`` pairs, which are never asked in one cell."""
    kinds = list(elements)
    if facet is None or len(kinds) < 2:
        return {"combinable": "n/a", "pairs": []}
    pairs = []
    for x, y in itertools.combinations(kinds, 2):
        if x.startswith("target:") and y.startswith("target:"):
            continue
        if exclusive and frozenset((x, y)) in exclusive:
            continue
        results = {_pair(facet, a, b) for a in elements[x] for b in elements[y]}
        verdict = "possible" if "possible" in results else "undetermined" if "undetermined" in results \
            else "impossible"
        pairs.append({"parts": [x, y], "verdict": verdict})
    verdicts = {p["verdict"] for p in pairs}
    combinable = "impossible" if "impossible" in verdicts else "undetermined" if "undetermined" in verdicts \
        else "possible" if verdicts else "n/a"
    return {"combinable": combinable, "pairs": pairs}


# ---------------------------------------------------------------- surface completeness (plan 11 R1b, C2 and K14)

# Axis roles where a fact can sit; an axis of another role does not make the surface incomplete for that fact (K14).
FACT_AXIS_ROLES = {"indicator": ("indicator", "unknown", "human", "generic"),
                   "target": ("target", "unknown", "human", "generic")}


def _pressed_axes(layer) -> set[tuple[str, str]]:
    if "pressed_axes" not in layer._derived:
        layer._derived["pressed_axes"] = {(p.table_id, p.obj_id) for p in getattr(layer.roles, "pressed", None) or []}
    return layer._derived["pressed_axes"]


def surface_facts(layer, facet) -> dict:
    """Per presence fact (indicator, target; a lower item sits where an indicator sits): whether the table surface
    is complete, so that "the name occurs nowhere" can be read as absence: values were read (metadata present), and
    on the axes where the fact can sit no hierarchy anomaly (orphan, cycle, repeated ID: a name may be split over a
    broken chain) and no pressed axis (a name may be split over two levels). Every value and the title are scanned
    for presence whatever the axis role, so a draft axis role does not make the surface incomplete.
    {fact: {complete, why}}."""
    out = {}
    for fact, roles in FACT_AXIS_ROLES.items():
        why = []
        if facet is None or not facet.axes:
            why.append("no_values")
        else:
            objs = {a["obj_id"] for a in facet.axes if a["role"] in roles}
            if any(v.anomaly and v.obj_id in objs for v in facet.values):
                why.append("hierarchy_anomaly")
            if any((facet.table_id, a["obj_id"]) in _pressed_axes(layer) for a in facet.axes):
                why.append("pressed_axis")
        out[fact] = {"complete": not why, "why": why}
    return out


def partial_on_facet(facet, key: str) -> bool:
    """Some part of the name occurs on the table: the name key inside the title or inside a value, or a value key
    (two letters or more) inside the name key (the partial occurrences of ``indicator_facts``)."""
    if facet is None or len(key) < 2:
        return False
    if key in index_key(facet.title):
        return True
    return any(len(v.key) >= 2 and (key in v.key or v.key in key) for v in facet.values)


def target_facts(analysis: dict, rid: str, view, layer) -> list[dict]:
    """Plan 11 R1b: every target of the requirement (and every listed breakdown value, which is covered like a
    target) with whether the table holds it as a value or in its name (``evidence.table_has_name``) and whether
    some part of it occurs (``partial``: absence needs no occurrence of any kind, as K1 for the indicator)."""
    from .evidence import table_has_name

    if view is None:
        return []
    counted, _ = scope(analysis, rid)
    forms = {n: target_forms(analysis, rid, n) for n in target_names(analysis, rid)}
    for c in conditions_of(analysis, "breakdown"):
        value = (c.get("attrs") or {}).get("text") or c.get("value")
        if c["id"] in counted and value and value not in forms:
            forms[value] = [value]
    facet = facet_of(layer, view.table_id)
    return [{"name": n, "present": table_has_name(layer, view, f),
             "partial": any(partial_on_facet(facet, index_key(x)) for x in f)} for n, f in forms.items()]


def lower_item_facts(checks: list[dict], facet=None) -> list[dict]:
    """Plan 11 R1b: the named lower items of the breakdown check as it found them ({text, word, found, under}),
    with ``partial`` (some part of the name occurs on the table)."""
    c = next((x for x in checks if x["name"] == "breakdown"), None)
    return [{**r, "partial": partial_on_facet(facet, index_key(r.get("word") or ""))}
            for r in ((c or {}).get("detail") or {}).get("rows") or []]


# ---------------------------------------------------------------- one requirement


def requirement_facts(analysis: dict, rid: str, pool: list[dict], evidence: dict | None, layer,
                      as_of: str | None) -> dict:
    """{requirement: {identity, period_request, period_anchor}, tables: {table_id: facts}} over the requirement's
    candidate pool (every table the checks ran on)."""
    from .cover import has_evidence

    sel = selection_catalog(layer)
    checks_by = {e["table_id"]: e["checks"] for e in (evidence or {}).get("evidence") or []
                 if e["requirement_id"] == rid}
    request = period_request(analysis, rid, as_of)
    tables: dict[str, dict] = {}
    ends: dict[str, dict[str, int]] = {}
    exclusive = basis_exclusive(analysis, rid)
    for p in pool or []:
        t = p["table_id"]
        view = sel.views.get(t)
        checks = checks_by.get(t, [])
        facet = facet_of(layer, t)
        ind = indicator_facts(analysis, rid, facet, checks)
        conds, cross = condition_facts(analysis, rid, view, checks)
        refuted, unproven = refuted_unproven(analysis, rid, checks)
        period = period_facts(next((c for c in checks if c["name"] == "period"), None))
        cells = cell_facts(_elements(analysis, rid, view, facet, ind, checks, layer), facet, exclusive) if view \
            else {"combinable": "n/a", "pairs": []}
        freq = next((c for c in checks if c["name"] == "frequency"), None)
        tables[t] = {"table_id": t, "entered_by": p.get("entered_by"), "with_evidence": has_evidence(p, checks),
                     "indicator": ind, "conditions": conds, "cross": cross,
                     "frequency": {"verdict": (freq or {}).get("verdict"), "status": (freq or {}).get("source_status")},
                     "period": period, "cells": cells, "refuted": refuted, "unproven": unproven,
                     "surface": surface_facts(layer, facet), "targets": target_facts(analysis, rid, view, layer),
                     "lower_items": lower_item_facts(checks, facet)}
        if view is not None:
            ends[t] = {f: m for f, _s, e in view.periods if (m := _months(e, True)) is not None}
    certain = {t for t, f in tables.items() if f["indicator"]["weak"] is False}
    if not (requirement_of(analysis, rid).get("indicator") or {}):
        certain = set(tables)  # no indicator: every candidate counts for the anchor
    anchors = period_anchors(request, ends, certain, catalog_version(layer))
    for t, f in tables.items():
        view = sel.views.get(t)
        f["period"]["anchor"] = [a for a in anchors if view is not None
                                 and any(fr == a["frequency"] for fr, _s, _e in view.periods)]
        f["period"]["by_anchor"] = _by_anchor(anchors, view) if view is not None else None
    env = request["envelope"]
    return {"requirement": {"identity": requirement_identity(analysis, rid),
                            "period_request": {"requested": request["requested"], "relative": request["relative"],
                                               "frequencies": request["frequencies"],
                                               "start": (env or {}).get("start"), "end": (env or {}).get("end")},
                            "period_anchor": anchors},
            "tables": tables}


# ---------------------------------------------------------------- pool cut (7-12)


def _fused_order(channels: dict) -> list[str]:
    """The fusion order of ``candidates.fuse`` without its cap."""
    fused: dict[str, float] = {}
    rank: dict[str, dict] = {}
    for name, ch in (channels or {}).items():
        rows = ch.get("merged") or []
        if not rows:
            continue
        top = max(r["score"] for r in rows) or 1.0
        for r in rows:
            fused[r["table_id"]] = fused.get(r["table_id"], 0.0) + r["score"] / top
            rank.setdefault(r["table_id"], {})[name] = r["rank"]
    return sorted(fused, key=lambda t: (-round(fused[t], 12), min(rank[t].values()), t))


def pool_cut(analysis: dict, rid: str, channels: dict | None, pool: list[dict], layer, info: dict | None) -> dict:
    """Which caps cut candidates (channel top 10, pool 30, term tables 30, target tables 30) and whether some cut
    table, left outside the checks, holds the indicator name exactly, with a qualifier or as a derived name."""
    from . import candidates as cand

    have = {p["table_id"] for p in pool or []}
    known = selection_catalog(layer).views
    caps: dict[str, int] = {}
    cut: list[str] = []

    def add(cap: str, ids) -> None:
        ids = [t for t in ids if t in known and t not in have]
        caps[cap] = len(ids)
        cut.extend(t for t in ids if t not in cut)

    for name, ch in (channels or {}).items():
        add(f"channel:{name}", [r["table_id"] for r in (ch.get("ranked_full") or [])[cand.CHANNEL_CAP:]])
    if channels:
        add("pool", _fused_order(channels)[cand.POOL_CAP:])
        term_cut: list[str] = []
        cand.term_tables(analysis, rid, channels, term_cut)
        add("term_tables", term_cut)
    target = (info or {}).get("target_search")
    if target and target.get("truncated"):
        add("target_tables", cand.target_tables(analysis, rid, layer)[cand.TARGET_TABLE_CAP:])
    name = indicator_name(requirement_of(analysis, rid))
    key = index_key(name) if name else ""
    matched = []
    for t in cut:
        occ = [o for o in strong_occurrences(facet_of(layer, t), key)] if key else []
        if occ:
            matched.append({"table_id": t, "methods": sorted({o["method"] for o in occ})})
    restore = restoration(analysis, rid, cut, layer, {m["table_id"] for m in matched})
    return {"caps": caps, "cut": len(cut), "matched_count": len(matched), "matched": matched[:CUT_MATCHES_CAP],
            **restore, "pool_cut": restore["restoration_cut"]}


RESTORE_CAP = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))["observation_caps"]["RESTORE_CAP"]


def restoration(analysis: dict, rid: str, cut: list[str], layer, strong: set[str] | None = None) -> dict:
    """Plan 11 R1b (C4): every cut candidate is checked cheaply on the table surface. A table on which the
    requirement's indicator occurs in some way (exact, qualified, derived or partial on the facets, or all or some
    of its words by the word check) is restored; one on which it occurs in no way is left out (it is what the
    observation path would refute as ``indicator_absent``). With no indicator name the indicator words are read
    (the word check), else the targets (every target held); a cut table nothing can tell apart is unchecked.
    ``restoration_cut``: more restorable tables than RESTORE_CAP, or an unchecked one. None restores all. Strong occurrences first,
    then the cut order. The restored tables are used by the observation path only (K13)."""
    from .evidence import indicator_phrase, assess, table_has_name

    strong = strong or set()
    sel = selection_catalog(layer)
    req = requirement_of(analysis, rid)
    key = index_key(indicator_name(req) or "")
    measure = sel.measure() if cut else None
    phrase = indicator_phrase(analysis, rid, measure) if cut else None
    targets = [target_forms(analysis, rid, n) for n in target_names(analysis, rid)]
    restorable, unchecked, left = [], 0, 0
    for t in cut:
        view = sel.views.get(t)
        if view is None:
            continue
        if key or phrase:
            hit = t in strong or (key and partial_on_facet(facet_of(layer, t), key)) or (
                phrase is not None and assess(phrase, view, measure)["status"] != "none")
        elif targets:
            hit = all(table_has_name(layer, view, f) for f in targets)
        else:
            unchecked += 1
            continue
        if hit:
            restorable.append(t)
        else:
            left += 1
    restorable.sort(key=lambda t: (t not in strong, cut.index(t)))
    return {"restorable": len(restorable), "restored": restorable[:RESTORE_CAP], "left_out": left,
            "unchecked": unchecked,
            "restoration_cut": (RESTORE_CAP is not None and len(restorable) > RESTORE_CAP) or unchecked > 0}


# ---------------------------------------------------------------- pruning record, relations (record only)


def pruned_record(cover: dict | None, choice: dict | None, bundle_log: list | None, selection: dict) -> dict:
    """{records, options, options_cut}: what the dominance cut, the least-cover search, the set-dimension filter, the
    representative step and the bundle step left out (each with its reason), and whether the table options of this
    requirement pass the question route's cap (7-12 ``options_cut``)."""
    from ..agent.question_route import MAX_OPTIONS, _groups

    records = [*((cover or {}).get("pruned") or []), *((choice or {}).get("pruned") or []), *(bundle_log or [])]
    options = len(_groups(selection)) if selection.get("status") != "no_table" else 0
    return {"records": records, "options": options, "options_cut": options > MAX_OPTIONS}


def _unit_bases(view) -> list[str]:
    from .evidence import unit_parts

    return sorted({p[1] for p in (unit_parts(u) for u in view.units()) if p})


def relation_record(analysis: dict, selections: list[dict], layer) -> list[dict]:
    """Plan 11 8-2 item 9: for each calculation relation of the interpretation (ratio, difference, share ...), whether
    the operands' chosen tables agree on frequency (a common one), unit base and period (a common stretch of a
    common frequency): agree / differ / unknown. Record only."""
    views = selection_catalog(layer).views
    by_rid = {s["requirement_id"]: s for s in selections}
    out = []
    for rel in (analysis.get("interpretation") or {}).get("relations") or []:
        rids = [r for r in rel.get("requirement_ids") or [] if r in by_rid]
        ops = []
        for r in rids:
            s = by_rid[r]
            tabs = [views[t["table_id"]] for t in s.get("tables") or [] if t["table_id"] in views]
            if s.get("status") != "no_table" and tabs:
                ops.append(tabs)
        rec = {"kind": rel.get("kind"), "requirement_ids": list(rel.get("requirement_ids") or []),
               "frequency": "unknown", "unit": "unknown", "period": "unknown", "operands": len(ops)}
        if len(ops) >= 2:
            freqs = [{f for v in tabs for f, _s, _e in v.periods} for tabs in ops]
            common = set.intersection(*freqs)
            rec["frequency"] = "agree" if common else "differ"
            bases = [{b for v in tabs for b in _unit_bases(v)} for tabs in ops]
            if all(bases):
                rec["unit"] = "agree" if set.intersection(*bases) else "differ"
            if common:
                spans = []
                for tabs in ops:
                    got = [(_months(s), _months(e, True)) for v in tabs for f, s, e in v.periods if f in common]
                    got = [g for g in got if None not in g]
                    spans.append((min(g[0] for g in got), max(g[1] for g in got)) if got else None)
                if all(spans):
                    rec["period"] = "agree" if max(a for a, _ in spans) <= min(b for _, b in spans) else "differ"
        rec["mismatch"] = "differ" in (rec["frequency"], rec["unit"], rec["period"])
        out.append(rec)
    return out
