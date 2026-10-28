"""settle_reading (plan 15 r3 step 4, 7.1-7.3 and appendix A 3): an accepted, verified reading of a decomposition
proposal settled into the existing contract, so that the selection reads it unchanged. ``settle.py`` reads the
question again by the rules and is not used for a model structure.

From one reading (the primary one, or the kept alternative) and its verification findings:

- requirements r1, r2, ... in proposal order (the proposal IDs are kept in ``requirement_ids``): the indicator
  (``IndicatorRef``: the chosen sense's catalog name and match path, else the words), targets (``TargetRef``),
  context (the context sense's name, else its words) and breakdown entries (the words of the axis and the values),
  from the verified refs; ``span`` is the compatibility span from the first to the last ref of the requirement (the
  requirement's own words are its ``refs``: segment and mention IDs), ``reading_id`` the reading it belongs to;
- conditions projected from ``condition_bindings`` (the source of truth, kept in the record), for the condition kinds
  the rules attribute (``table.ATTRIBUTED``): a binding of the question -> ``common_condition_ids`` and every
  requirement's ``shared_condition_ids``; a binding of some requirements -> each one's ``condition_ids`` (a
  condition shared by several stays with each); unresolved -> ``ambiguous_condition_ids`` of its candidate
  requirements (all of them when none is named); name content -> no list. ``Judgement.attribution`` only holds the
  entries the old shape can express (common, ambiguous, one draft requirement);
- relations from the relation proposals (their kind from the condition, operand order kept) and a compare display
  bound to two or more requirements; display condition IDs as the rules list them;
- compatibility pieces from the chosen structure: one fragment per distinct ref span of a requirement (and per
  negated ref of a polarity link, phrase ``negated``), terms from the catalog rows of the refs' senses (every kept
  sense of the mentions the ref quotes when none is chosen, read by the verification's rule ``decompose_refs``: a
  subquote never takes the whole name's senses nor another part's; plan 19 stage 3; of a sense only the rows that are
  candidate rows of the quoted range, as ``decompose_structure.selection_ref``, so that a sense shared by several
  mentions never brings another mention's tables, axes or path: plan 19 F2; an out-of-scope term of the rules
  over the same words is carried), one draft
  requirement per requirement, ``pairs = []`` (no judgement-table row is made up), ``negated`` (the negated side of
  the polarity links outside an exclusion condition), ``unused_names`` and ``leftover``;
- failures of this structure: every verification finding becomes a failed source (``data/decompose_policy.json``
  ``certainty_failures``) scoped to its requirements, fragments and conditions (no ID at all: the whole question); a
  finding about a name mapping carries the name part in its detail (``select/needs.py`` name-only sources). The
  rules' failures are never copied because the structures are the same: ``carry_rule_failures`` (user decision
  10/20 22:10, fix round 2) re-checks every rule source by its kind and detail (``rule_failures.checks``) and records
  the outcome (``rule_checks``: resolved_by_code, still_valid, not_rechecked); it leaves out the ones the code
  resolved and keeps the others mapped to the reading's IDs (scope not mappable: the whole question,
  ``scope_unmapped``);
- internal assumption seeds: one per finding, with ``refs {segment_ids, mention_ids, condition_ids}``, the reason
  code, the source (``assumption_sources``) and the requirement keys ``(reading_id, requirement_id)``;
- statuses (7.3): a requirement is unsupported when an out-of-scope term of the rules lies over its indicator,
  unresolved when it has no indicator or its indicator is not mapped (unresolved, unmapped, unclassified or
  ambiguous role), else ok; the interpretation unsupported / partial / ask / ok as in the rules.

``compare_structures`` compares two settled structures by meaning (indicator, targets, breakdown, context,
condition attribution, status, relations, polarity); a shared table is never a ground. ``finish_certainty`` applies
7.2: certain only when the rules are certain, the structures are the same, the proposal was verified and no failure
or seed is left; a structure that differs gets an ``unverified_structure`` failure and seed, a kept alternative a
``mixed_reading`` one, both scoped to the whole requirements that differ (not localised: the whole question). A
reading that is not certain names a failed source (none left: the residual question-level row of
``rule_failures``), so that the selection never reads it as settled, except when the rules being uncertain is the
one reason left (user decision 10/20 22:48: ``uncertain_only``): then no scope-less row is written and the
certainty basis records ``certain`` false with the reason.
"""

from __future__ import annotations

import json
from collections import Counter

from ..catalog.text import index_key
from ..contract.check import version_of
from ..contract.shapes import Condition, Fragment, Mention, Segment, Term, TextView
from .decompose_payload import POLICY, _best_path
from .decompose_refs import RefReader
from .mentions import build_ref_map
from .settle import RELATION_KINDS
from .table import ATTRIBUTED

SETTLE = POLICY["settle"]
PHRASES: dict[str, str] = SETTLE["phrases"]
TERM_TYPES: dict[str, str] = SETTLE["term_types"]
NAME_PARTS: dict[str, str] = SETTLE["name_parts"]
FAIL = POLICY["certainty_failures"]
FAIL_CODES: dict[str, str] = FAIL["codes"]
NAME_CODES = frozenset(FAIL["name_codes"])
AMBIGUITY_FAILURES: dict[str, str] = FAIL["ambiguity_kinds"]
SEED_SOURCES: dict[str, str] = POLICY["assumption_sources"]["codes"]
RULE_FAIL = POLICY["rule_failures"]
RULE_CHECKS: dict[str, dict[str, str]] = {k: v for k, v in RULE_FAIL["checks"].items() if not k.startswith("_")}
RELATION_COND_KINDS = frozenset(POLICY["relation_condition_kinds"]["kinds"])
RESOLVED, STILL_VALID, NOT_RECHECKED = "resolved_by_code", "still_valid", "not_rechecked"
ATTACHED_PLACES = ("target", "basis", "breakdown_value", "breakdown_axis")  # names a requirement is narrowed by
PRIMARY, ALTERNATIVE = "primary", "alternative"
# findings that leave a requirement's indicator unmapped (the requirement is unresolved)
UNRESOLVING = frozenset({"indicator_unresolved", "indicator_role_unclassified", "role_ambiguous"})
INDICATOR_NAME_CODES = frozenset({"unmapped_concept", "sense_unresolved"})


def _key(x) -> str | None:
    return index_key(x) if x else None


class Question:
    """What one question gives the settling: text, conditions, segments, mentions, the local sense table of the
    payload and the rule terms (only their out-of-scope marks are read)."""

    def __init__(self, text: TextView, conditions: list[Condition], segments: list[Segment],
                 mentions: list[Mention], local: dict, rule_terms: list[Term] | None = None):
        self.text, self.raw = text, text["raw"]
        self.conditions = conditions
        self.cond = {c["id"]: c for c in conditions}
        self.segments, self.mentions = segments, mentions
        self.seg = {s["segment_id"]: s for s in segments}
        self.order = {s["segment_id"]: n for n, s in enumerate(segments)}
        self.senses = local["senses"]
        self.kept = local["mention_kept"]
        self.cond_segs: dict[str, list[str]] = {cid: list(v) for cid, v in (local.get("condition_segments")
                                                                            or {}).items()}
        self.rule_terms = list(rule_terms or [])
        # plan 19 stage 3: the source range of a ref is read by the verification's rule (decompose_refs)
        self.refs = RefReader(segments, mentions, local)

    def span(self, ref: dict) -> list[int]:
        return self.refs.span(ref)

    def quote(self, ref: dict) -> str:
        a, b = self.span(ref)
        return self.raw[a:b]

    def name_mentions(self, ref: dict) -> list[Mention]:
        """The name mentions the ref quotes (``RefReader.name_mentions``: a subquote never takes the whole name's
        mention or another part of its segment; a whole ref never takes an inner mention)."""
        return self.refs.name_mentions(ref)

    def sense_ids(self, ref: dict) -> list[str]:
        return self.refs.sense_ids(ref)

    def candidate_rows(self, ref: dict) -> list[dict]:
        return self.refs.candidate_rows(ref)

    def mentions_within(self, segment_ids) -> list[str]:
        segs = set(segment_ids)
        return [m["mention_id"] for m in self.mentions if set(m["segment_ids"]) <= segs]

    def sorted_segments(self, segment_ids) -> list[str]:
        return sorted(dict.fromkeys(segment_ids), key=self.order.get)


# ---------------------------------------------------------------- refs of a reading


def requirement_refs(r: dict):
    """(place, ref) of one proposed requirement, in place order (breakdown markers as ``breakdown_marker``)."""
    if r["indicator"] is not None:
        yield "indicator", r["indicator"]
    for x in r["targets"]:
        yield "target", x
    for x in r["basis_refs"]:
        yield "basis", x
    if r["context"] is not None:
        yield "context", r["context"]
    for b in r["breakdowns"]:
        if b["axis"] is not None:
            yield "breakdown_axis", b["axis"]
        for x in b["values"]:
            yield "breakdown_value", x
        for x in b["marker_refs"]:
            yield "breakdown_marker", x


def _inside(span: list[int], outer: list[int]) -> bool:
    return outer[0] <= span[0] and span[1] <= outer[1]


def _overlap(a: list[int], b: list[int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def binding_requirements(b: dict, proposal_ids: list[str]) -> list[str]:
    """Proposal requirement IDs a binding applies to (question: all; requirements: the named ones; unresolved: the
    named candidates, all when none is named; name content: none)."""
    if b["scope"] == "question":
        return list(proposal_ids)
    if b["scope"] == "requirements":
        return list(b["requirement_ids"])
    if b["scope"] == "unresolved":
        return [x for x in b["requirement_ids"] if x in proposal_ids] or list(proposal_ids)
    return []


# ---------------------------------------------------------------- settling one reading


def _negative_spans(q: Question, reading: dict) -> list[list[int]]:
    """Spans of the negated side of the polarity links that no exclusion condition holds."""
    excl = [c["span"] for c in q.conditions if c["kind"] == "exclude"]
    out = []
    for p in reading["polarity_links"]:
        for ref in p["negative_refs"]:
            sp = q.span(ref)
            if not any(_inside(sp, e) for e in excl) and sp not in out:
                out.append(sp)
    return out


def _fragments(q: Question, reading: dict, negative: list[list[int]]) -> list[Fragment]:
    phrase_of: dict[tuple, str] = {}
    for r in reading["requirements"]:
        for place, ref in requirement_refs(r):
            if place in PHRASES:
                phrase_of.setdefault(tuple(q.span(ref)), PHRASES[place])
    for sp in negative:
        phrase_of.setdefault(tuple(sp), PHRASES["negative"])
    out: list[Fragment] = []
    for n, (sp, phrase) in enumerate(sorted(phrase_of.items(), key=lambda kv: (kv[0][0], kv[0][1])), start=1):
        a, b = sp
        out.append({"id": f"g{n}", "quote": q.raw[a:b], "span": [a, b], "role": "core", "protected_by": None,
                    "phrase": phrase})
    return out


def _frag_of(fragments: list[Fragment], span: list[int]) -> str | None:
    return next((f["id"] for f in fragments if f["span"] == span), None)


def _terms(q: Question, reading: dict, fragments: list[Fragment]) -> list[Term]:
    out: list[Term] = []
    seen: set[tuple] = set()

    def add(t: Term) -> None:
        key = (t["fragment_id"], t["name"], t["type"])
        if key not in seen:
            seen.add(key)
            out.append(t)

    for r in reading["requirements"]:
        for place, ref in requirement_refs(r):
            if place not in PHRASES:
                continue
            sp = q.span(ref)
            fid = _frag_of(fragments, sp)
            local_rows = q.candidate_rows(ref)  # plan 19 F2: the rows of the quoted range only, as selection_ref
            for z in q.sense_ids(ref):
                s = q.senses[z]
                own = [row for row in s["rows"] if row in local_rows]
                if not own:
                    continue
                groups: dict[str, list[dict]] = {}
                for row in own:
                    groups.setdefault(TERM_TYPES.get(row["type"], row["type"]), []).append(row)
                path = _best_path(own)  # the sense table's own path order (one definition)
                for typ, rows in groups.items():
                    objs = {row["obj_id"] for row in rows}
                    obj = next(iter(objs)) if len(objs) == 1 else None
                    add({"fragment_id": fid, "name": s["name"], "type": typ,
                         "table_ids": list(dict.fromkeys(row["table_id"] for row in rows)),
                         "obj_id": None if obj == "ITEM" else obj, "obj_name": None, "path": path,
                         "status": "approved", "score": None})
            for t in q.rule_terms:  # out-of-scope marks of the rules over the same words
                if t.get("type") == "out_of_scope" and _out_of_scope_over(q, t, sp):
                    add({**t, "fragment_id": fid})
    return out


def _out_of_scope_over(q: Question, term: dict, span: list[int]) -> bool:
    key = index_key(term.get("name") or "")
    return bool(key) and key in index_key(q.raw[span[0]:span[1]])


def _projection(q: Question, reading: dict, rid_of: dict[str, str], draft_of: dict[str, str]):
    """(own, common, ambiguous, attribution) from the condition bindings (module docstring)."""
    pids = [r["id"] for r in reading["requirements"]]
    order = [c["id"] for c in q.conditions if c["kind"] in ATTRIBUTED]
    own: dict[str, set[str]] = {rid_of[p]: set() for p in pids}
    amb: dict[str, set[str]] = {rid_of[p]: set() for p in pids}
    common: set[str] = set()
    attribution: dict[str, str] = {}
    for b in reading["condition_bindings"]:
        cid = b["condition_id"]
        if cid not in order:
            continue  # relation, function, display: relations and display lists only (the binding is recorded)
        scope = b["scope"]
        rids = [rid_of[p] for p in binding_requirements(b, pids) if p in rid_of]
        if scope == "question":
            common.add(cid)
            attribution[cid] = "common"
        elif scope == "requirements":
            for rid in rids:
                own[rid].add(cid)
            if len(rids) == 1:
                attribution[cid] = draft_of[rids[0]]
        elif scope == "unresolved":
            for rid in rids:
                amb[rid].add(cid)
            attribution[cid] = "ambiguous"
    ordered = {k: [c for c in order if c in v] for k, v in own.items()}
    common_l = [c for c in order if c in common]
    ambiguous = {k: [c for c in order if c in v and c not in own[k]] for k, v in amb.items()}
    return ordered, common_l, ambiguous, {c: attribution[c] for c in order if c in attribution}


def _relations(q: Question, reading: dict, rid_of: dict[str, str]) -> list[dict]:
    out = []
    for rel in reading["relations"]:
        c = q.cond.get(rel["condition_id"])
        kind = RELATION_KINDS.get((c.get("attrs") or {}).get("relation")) if c else None
        if kind:
            out.append({"kind": kind, "requirement_ids": [rid_of[p] for p in rel["requirement_ids"]],
                        "condition_id": rel["condition_id"]})
    pids = [r["id"] for r in reading["requirements"]]
    for b in reading["condition_bindings"]:
        c = q.cond.get(b["condition_id"])
        if c is None or c["kind"] != "display" or (c.get("attrs") or {}).get("display") != "compare":
            continue
        rids = [rid_of[p] for p in binding_requirements(b, pids) if b["scope"] != "unresolved"]
        if len(rids) >= 2 and not any(r["requirement_ids"] == rids for r in out):
            out.append({"kind": "compare", "requirement_ids": rids, "condition_id": c["id"]})
    return out


def _issue_failure(it: dict) -> str | None:
    if it["code"] == "ambiguity":
        return AMBIGUITY_FAILURES.get(it["detail"].split(":", 1)[0])
    return FAIL_CODES.get(it["code"])


def _name_part(it: dict, places: dict[str, list[tuple[str, list[str]]]], pids: list[str]) -> str | None:
    segs = set(it["segment_ids"])
    if not segs:
        return None
    for pid in pids:
        for place, sids in places.get(pid, []):
            if place in NAME_PARTS and segs & set(sids):
                return NAME_PARTS[place]
    return None


def add_source(sources: list[dict], code: str, rids, detail: str, cids=(), fids=()) -> None:
    """One failed source per (condition, requirement IDs, detail); the IDs of repeated rows merged (as the rules)."""
    key = {"condition": code, "requirement_ids": list(dict.fromkeys(rids)), "detail": detail}
    row = next((x for x in sources if all(x[k] == v for k, v in key.items())), None)
    if row is None:
        row = {**key, "condition_ids": [], "fragment_ids": []}
        sources.append(row)
    row["condition_ids"] += [c for c in cids if c and c not in row["condition_ids"]]
    row["fragment_ids"] += [f for f in fids if f and f not in row["fragment_ids"]]


def make_seed(q: Question, reading_id: str, reason: str, rids, segment_ids=(), condition_ids=()) -> dict:
    segs = q.sorted_segments(segment_ids)
    seed = {"stage": "A", "reading_id": reading_id,
            "requirement_keys": [{"reading_id": reading_id, "requirement_id": r} for r in dict.fromkeys(rids)],
            "refs": {"segment_ids": segs, "mention_ids": q.mentions_within(segs),
                     "condition_ids": list(dict.fromkeys(condition_ids))},
            "reason_code": reason, "source": SEED_SOURCES.get(reason, "default")}
    return {"seed_id": "seed-" + version_of(seed), **seed}


def _fragments_over(fragments: list[Fragment], q: Question, segment_ids) -> list[str]:
    spans = [q.seg[s]["span"] for s in segment_ids if s in q.seg]
    return [f["id"] for f in fragments if any(_overlap(f["span"], sp) for sp in spans)]


def settle_reading(q: Question, reading: dict, issues: list[dict], reading_id: str = PRIMARY) -> dict:
    """{fragments, terms, judgement, interpretation, ref_map, requirement_ids, bindings, assumption_seeds,
    reading_id} of one verified reading (module docstring). ``issues``: its verification findings (for the primary
    reading the proposal-level ones too); the certainty is decided later by ``finish_certainty``."""
    reqs = reading["requirements"]
    pids = [r["id"] for r in reqs]
    rid_of = {p: f"r{n}" for n, p in enumerate(pids, start=1)}
    draft_of = {f"r{n}": f"d{n}" for n in range(1, len(pids) + 1)}
    negative = _negative_spans(q, reading)
    fragments = _fragments(q, reading, negative)
    terms = _terms(q, reading, fragments)
    own, common, ambiguous, attribution = _projection(q, reading, rid_of, draft_of)
    places = {r["id"]: [(place, list(ref["segment_ids"])) for place, ref in requirement_refs(r)] for r in reqs}
    by_rid_issues: dict[str, list[str]] = {}
    for it in issues:
        for p in it["requirement_ids"]:
            by_rid_issues.setdefault(p, []).append(it["code"])

    requirements, drafts, unsupported_sources = [], [], {}
    for r in reqs:
        pid, rid = r["id"], rid_of[r["id"]]
        refs = list(requirement_refs(r))
        spans = [q.span(ref) for _, ref in refs]
        span = [min(s[0] for s in spans), max(s[1] for s in spans)] if spans else [0, len(q.raw)]
        frag_ids = list(dict.fromkeys(f for place, ref in refs if place in PHRASES
                                      for f in [_frag_of(fragments, q.span(ref))] if f))
        ind = r["indicator"]
        indicator = None
        if ind is not None:
            z = ind["sense_id"]
            sp = q.span(ind)
            indicator = {"quote": q.quote(ind), "span": sp, "fragment_id": _frag_of(fragments, sp),
                         "name": q.senses[z]["name"] if z else None, "path": q.senses[z]["path"] if z else None}
        targets = [{"quote": q.quote(t), "span": q.span(t), "fragment_id": _frag_of(fragments, q.span(t)),
                    "name": q.senses[t["sense_id"]]["name"] if t["sense_id"] else None} for t in r["targets"]]
        ctx = r["context"]
        context = (q.senses[ctx["sense_id"]]["name"] if ctx["sense_id"] else q.quote(ctx)) if ctx else None
        breakdown = [q.quote(ref) for place, ref in refs if place in ("breakdown_axis", "breakdown_value")]
        status, reasons = "ok", []
        codes = by_rid_issues.get(pid, [])
        ind_segs = set(ind["segment_ids"]) if ind else set()
        if ind is None or any(c in UNRESOLVING for c in codes) or any(
                it["code"] in INDICATOR_NAME_CODES and pid in it["requirement_ids"]
                and set(it["segment_ids"]) & ind_segs for it in issues):
            status = "unresolved"
        oos = [t for t in terms if t["type"] == "out_of_scope" and indicator is not None
               and t["fragment_id"] == indicator["fragment_id"]]
        if oos:
            status = "unsupported"
            reasons.append("out_of_scope")
            types = sorted({t["type"] for t in terms if indicator and t["fragment_id"] == indicator["fragment_id"]})
            unsupported_sources[rid] = {"types": types, "all_out_of_scope": set(types) == {"out_of_scope"}}
        seg_ids = q.sorted_segments(s for _, ref in refs for s in ref["segment_ids"])
        requirements.append({
            "id": rid, "quote": q.raw[span[0]:span[1]], "span": span, "indicator": indicator, "measure_kind": None,
            "targets": targets, "condition_ids": own[rid], "shared_condition_ids": list(common),
            "ambiguous_condition_ids": ambiguous[rid], "status": status, "reasons": reasons, "context": context,
            "breakdown": breakdown, "refs": {"segment_ids": seg_ids, "mention_ids": q.mentions_within(seg_ids)},
            "reading_id": reading_id})
        drafts.append({"id": draft_of[rid], "fragment_ids": frag_ids,
                       "indicator_fragment_id": indicator["fragment_id"] if indicator else None,
                       "indicator": indicator["quote"] if indicator else None, "context": context,
                       "targets": [t["quote"] for t in targets], "breakdown": list(breakdown),
                       "condition_ids": list(own[rid]), "status": status,
                       "broad": False})

    # failures and seeds of this structure only
    sources: list[dict] = []
    seeds: list[dict] = []
    for it in issues:
        code = _issue_failure(it)
        rids = [rid_of[p] for p in it["requirement_ids"] if p in rid_of]
        if code is None:
            continue
        detail = it["code"]
        part = _name_part(it, places, it["requirement_ids"]) if it["code"] in NAME_CODES else None
        if part and not it["condition_ids"]:
            detail = f"{it['code']}:{part}"
        elif it["code"] == "ambiguity":
            detail = f"ambiguity:{it['detail']}"
        add_source(sources, code, rids, detail, it["condition_ids"], _fragments_over(fragments, q, it["segment_ids"]))
        seed = make_seed(q, reading_id, it["code"], rids, it["segment_ids"], it["condition_ids"])
        if all(x["seed_id"] != seed["seed_id"] for x in seeds):
            seeds.append(seed)

    relations = _relations(q, reading, rid_of)
    used = {s for r in reqs for _, ref in requirement_refs(r) for s in ref["segment_ids"]}
    used |= {s for b in reading["condition_bindings"] for ref in b["name_refs"] for s in ref["segment_ids"]}
    used |= {s for p in reading["polarity_links"] for f in ("negative_refs", "positive_refs") for ref in p[f]
             for s in ref["segment_ids"]}
    used |= {s for b in reading["condition_bindings"] for s in q.cond_segs.get(b["condition_id"], [])}
    unused_names = list(dict.fromkeys(q.quote(ref) for m in q.mentions if m["kind"] == "name" and m["candidates"]
                                      and not set(m["segment_ids"]) & used
                                      for ref in [{"segment_ids": m["segment_ids"], "subquote": None}]))
    leftover = [q.quote(u["ref"]) for u in reading["unused_refs"] if u["reason"] == "unresolved"]
    negated = [q.raw[a:b] for a, b in negative]
    judgement = {"pairs": [], "requirement_draft": drafts, "certain": False, "failed_conditions": [],
                 "attribution": attribution, "table_group_hint": [], "calc_hints": [], "negated": negated,
                 "leftover": leftover, "unused_names": unused_names, "failed_sources": sources}
    interpretation = {"status": "ok", "common_condition_ids": list(common), "relations": relations,
                      "display_condition_ids": [c["id"] for c in q.conditions if c["kind"] == "display"],
                      "requirements": requirements, "reasons": [], "table_group_hint": []}
    if unsupported_sources:
        interpretation["unsupported_sources"] = unsupported_sources
    return {"reading_id": reading_id, "requirement_ids": {rid_of[p]: p for p in pids},
            "bindings": [dict(b) for b in reading["condition_bindings"]], "fragments": fragments, "terms": terms,
            "judgement": judgement, "interpretation": interpretation, "assumption_seeds": seeds,
            "ref_map": build_ref_map(q.segments, q.mentions, fragments), "rule_checks": []}


def carry_calc_hints(settled: dict, rule_hints: list[dict]) -> None:
    """The change marks of the rules (2.5 calc hints: marks, not conditions) with the requirements of this structure
    whose span holds them."""
    reqs = settled["interpretation"]["requirements"]
    draft = {r["id"]: d["id"] for r, d in zip(reqs, settled["judgement"]["requirement_draft"])}
    out = []
    for h in rule_hints or []:
        ids = [draft[r["id"]] for r in reqs if _inside(h["span"], r["span"])]
        out.append({"quote": h["quote"], "span": list(h["span"]), "kind": h["kind"],
                    "requirement_id": ids[0] if len(ids) == 1 else None, "requirement_ids": ids})
    settled["judgement"]["calc_hints"] = out


# ---------------------------------------------------------------- comparison and certainty (7.2)


def _req_canon(r: dict) -> str:
    ind = r.get("indicator") or None
    return json.dumps([
        _key(ind.get("name") or ind.get("quote")) if ind else None,
        sorted(_key(t.get("name") or t.get("quote")) or "" for t in r.get("targets") or []),
        sorted(_key(b) or "" for b in r.get("breakdown") or []),
        _key(r.get("context")),
        sorted(set(r.get("condition_ids") or []) | set(r.get("shared_condition_ids") or [])),
        sorted(r.get("ambiguous_condition_ids") or []),
        r.get("status")], ensure_ascii=False)


def _structure(interp: dict, judgement: dict) -> dict:
    reqs = (interp or {}).get("requirements") or []
    canon = {r["id"]: _req_canon(r) for r in reqs}
    rels = sorted(json.dumps([x["kind"], x.get("condition_id"), [canon.get(i) for i in x["requirement_ids"]]],
                             ensure_ascii=False) for x in (interp or {}).get("relations") or [])
    neg = sorted(_key(w) for w in (judgement or {}).get("negated") or [] if w)
    return {"canon": canon, "requirements": Counter(canon.values()), "relations": rels, "negated": neg}


def compare_structures(a_interp: dict, a_judgement: dict, b_interp: dict, b_judgement: dict) -> dict:
    """{same, differences, requirement_ids}: whether structure b means what structure a means (indicator, targets,
    breakdown, context, condition attribution, status of every requirement; relations; negated words), and the
    requirements of b that differ (empty: not localised)."""
    a, b = _structure(a_interp, a_judgement), _structure(b_interp, b_judgement)
    diffs: list[str] = []
    rids: list[str] = []
    if a["requirements"] != b["requirements"]:
        diffs.append("requirements")
        left = Counter(a["requirements"])
        for rid, c in b["canon"].items():
            if left[c] > 0:
                left[c] -= 1
            else:
                rids.append(rid)
    if a["relations"] != b["relations"]:
        diffs.append("relations")
        changed = set(rids)  # a relation over a requirement that already differs differs by that alone
        for x in (b_interp or {}).get("relations") or []:
            key = json.dumps([x["kind"], x.get("condition_id"), [b["canon"].get(i) for i in x["requirement_ids"]]],
                             ensure_ascii=False)
            if key not in a["relations"] and not changed & set(x["requirement_ids"]):
                rids += [i for i in x["requirement_ids"] if i not in rids]
    if a["negated"] != b["negated"]:
        diffs.append("polarity")
    return {"same": not diffs, "differences": diffs, "requirement_ids": rids}


def mark_difference(q: Question, settled: dict, cmp: dict, reason: str, failure: str) -> None:
    """A structure difference as a failure and a seed of the settled reading, scoped to the whole requirements that
    differ (requirement IDs only: the requirement as a whole is open), or to the whole question when the difference is
    not localised (no ID at all)."""
    by_id = {r["id"]: r for r in settled["interpretation"]["requirements"]}
    rids = [r for r in cmp["requirement_ids"] if r in by_id]
    segs = q.sorted_segments(s for rid in rids for s in by_id[rid]["refs"]["segment_ids"])
    add_source(settled["judgement"]["failed_sources"], failure, rids, f"{reason}:{'+'.join(cmp['differences'])}")
    seed = make_seed(q, settled["reading_id"], reason, rids, segs)
    if all(x["seed_id"] != seed["seed_id"] for x in settled["assumption_seeds"]):
        settled["assumption_seeds"].append(seed)


def _segments_over(q: Question, word: str) -> list[str]:
    """Segments over every written occurrence of a word of the rules (by index key; none: not found)."""
    key = index_key(word)
    if not key:
        return []
    raw_key, pos = [], []  # index key of the raw text and the raw position of each of its characters
    for i, ch in enumerate(q.raw):
        k = index_key(ch)
        raw_key.append(k)
        pos += [i] * len(k)
    joined = "".join(raw_key)
    out: list[str] = []
    start = joined.find(key)
    while start >= 0:
        a, b = pos[start], pos[start + len(key) - 1] + 1
        out += [s["segment_id"] for s in q.segments if a < s["span"][1] and s["span"][0] < b
                and s["segment_id"] not in out]
        start = joined.find(key, start + 1)
    return q.sorted_segments(out)


def _reading_segments(q: Question, reading: dict) -> set[str]:
    """Segments a reading places: any ref of it, and the words of its bound conditions."""
    out = {s for r in reading["requirements"] for _, ref in requirement_refs(r) for s in ref["segment_ids"]}
    out |= {s for b in reading["condition_bindings"] for ref in b["name_refs"] for s in ref["segment_ids"]}
    out |= {s for p in reading["polarity_links"] for f in ("marker_refs", "negative_refs", "positive_refs")
            for ref in p[f] for s in ref["segment_ids"]}
    out |= {s for ref in reading["request_refs"] for s in ref["segment_ids"]}
    out |= {s for u in reading["unused_refs"] for s in u["ref"]["segment_ids"]}
    out |= {s for b in reading["condition_bindings"] for s in q.cond_segs.get(b["condition_id"], [])}
    return out


def _combine(outcomes: list[str]) -> str:
    """One outcome of several parts: any still valid part keeps it valid, any part not re-checked keeps it so."""
    if not outcomes:
        return NOT_RECHECKED
    if STILL_VALID in outcomes:
        return STILL_VALID
    if NOT_RECHECKED in outcomes:
        return NOT_RECHECKED
    return RESOLVED


class _Recheck:
    """What the re-checks of one adopted reading read: the rule analysis, the settled reading, the proposal reading
    and its verification findings."""

    def __init__(self, q: Question, rules: dict, settled: dict, reading: dict, issues: list[dict]):
        self.q, self.rules, self.settled, self.reading = q, rules, settled, reading
        self.rules_j = rules.get("judgement") or {}
        self.open_segs = {s for it in issues for s in it["segment_ids"]}
        self.open_cids = {c for it in issues for c in it["condition_ids"]}
        self.rule_spans = {f["id"]: f["span"] for f in rules.get("fragments") or []}

    def fragment_segments(self, fid: str) -> list[str]:
        sp = self.rule_spans.get(fid)
        return [s["segment_id"] for s in self.q.segments if sp and _overlap(s["span"], sp)] if sp else []


def _leftover_placed(c: _Recheck, row: dict) -> str:
    """Every leftover word of the rules lies on segments the reading places, with no finding on them."""
    words = [w for w in c.rules_j.get("leftover") or [] if w]
    placed = _reading_segments(c.q, c.reading)
    outs = []
    for w in words:
        sids = _segments_over(c.q, w)
        if not sids:
            outs.append(NOT_RECHECKED)  # the word cannot be found again: nothing to check it against
        elif any(s not in placed or s in c.open_segs for s in sids):
            outs.append(STILL_VALID)
        else:
            outs.append(RESOLVED)
    return _combine(outs)


def _name_used(c: _Recheck, row: dict) -> str:
    """No unused name of the rules is unused by the reading (the reading's unused names are code-made)."""
    words = [w for w in c.rules_j.get("unused_names") or [] if w]
    if not words:
        return NOT_RECHECKED
    now = {index_key(w) for w in c.settled["judgement"].get("unused_names") or []}
    return STILL_VALID if any(index_key(w) in now for w in words) else RESOLVED


def _condition_bound(c: _Recheck, row: dict) -> str:
    """Unresolved or with a finding on it: still valid. A condition bound to concrete requirements (question or
    requirements scope) is not re-checked: the binding is the model's choice and no independent attribution check
    exists yet, so this never resolves a failure. Name content or a condition not found: not re-checked either."""
    first: dict[str, dict] = {}
    for b in c.reading["condition_bindings"]:
        first.setdefault(b["condition_id"], b)
    outs = []
    for cid in row.get("condition_ids") or []:
        b = first.get(cid)
        if b is None or cid not in c.q.cond:
            outs.append(NOT_RECHECKED)
        elif b["scope"] == "unresolved" or cid in c.open_cids or set(c.q.cond_segs.get(cid, [])) & c.open_segs:
            outs.append(STILL_VALID)
        elif (b["scope"] == "question" and c.reading["requirements"]) or (
                b["scope"] == "requirements" and b["requirement_ids"]):
            outs.append(NOT_RECHECKED)  # concrete binding: the model's choice, no independent ground
        else:
            outs.append(NOT_RECHECKED)
    return _combine(outs)


def _tables(c: _Recheck, ref: dict | None) -> set[str]:
    z = ref["sense_id"] if ref else None
    return {row["table_id"] for row in c.q.senses[z]["rows"]} if z else set()


def _group_scope_held(c: _Recheck, row: dict) -> str:
    """Still valid when the group's words carry a finding (on them or their conditions), a name there is not placed
    by a requirement with a chosen sense, or a target, basis or breakdown name is attached to requirements other than
    exactly the requirements of the group whose indicator or context holds it in a table. The catalog check is a
    compatibility check only: without textual evidence that the scope is unique the failure stays not re-checked, so
    this never resolves a failure. A placement the model merely chose is never enough."""
    segs = list(dict.fromkeys(s for fid in row.get("fragment_ids") or [] for s in c.fragment_segments(fid)))
    if not segs:
        return NOT_RECHECKED
    cids = {cid for cid, ss in c.q.cond_segs.items() if set(ss) & set(segs)}
    if set(segs) & c.open_segs or cids & c.open_cids:
        return STILL_VALID
    names = [s for s in segs if c.q.seg[s]["kind"] == "name"]
    if not names:
        return NOT_RECHECKED
    reqs = c.reading["requirements"]
    places = [(r["id"], place, ref) for r in reqs for place, ref in requirement_refs(r) if place != "breakdown_marker"]
    holders = {r["id"]: _tables(c, r["indicator"]) | _tables(c, r["context"]) for r in reqs}
    group = [r["id"] for r in reqs if any(s in segs for x in (r["indicator"], r["context"]) if x
                                          for s in x["segment_ids"])] or [r["id"] for r in reqs]
    for s in names:
        at = [(rid, place, ref) for rid, place, ref in places if s in ref["segment_ids"]]
        if not at or any(ref["sense_id"] is None for _, _, ref in at):
            return STILL_VALID
        for _rid, _place, ref in at:
            attached = {rid for rid, place, x in at if place in ATTACHED_PLACES and x["sense_id"] == ref["sense_id"]}
            if not attached:
                continue  # an indicator or context of the group
            held = {rid for rid in group if _tables(c, ref) & holders[rid]}
            if attached != held:
                return STILL_VALID
    return NOT_RECHECKED  # catalog compatible only: no textual proof that the scope is unique


def _pair_relation(c: _Recheck, row: dict) -> str:
    """The two fragments of the pair are the indicators of two requirements that one relation condition of the
    question joins in the reading, with no finding on them or the condition; a finding on them: still valid; any other
    reading of them (the model merely chose one): no re-check."""
    fids = list(dict.fromkeys(row.get("fragment_ids") or []))
    if len(fids) != 2:
        return NOT_RECHECKED
    left, right = c.fragment_segments(fids[0]), c.fragment_segments(fids[1])
    if not left or not right:
        return NOT_RECHECKED
    if (set(left) | set(right)) & c.open_segs:
        return STILL_VALID
    ind = {s: r["id"] for r in c.reading["requirements"] if r["indicator"] for s in r["indicator"]["segment_ids"]}
    ra, rb = {ind[s] for s in left if s in ind}, {ind[s] for s in right if s in ind}
    if len(ra) == 1 and len(rb) == 1 and ra != rb:
        pair = ra | rb
        for rel in c.reading["relations"]:
            cond = c.q.cond.get(rel["condition_id"])
            if cond is not None and cond["kind"] in RELATION_COND_KINDS and pair <= set(rel["requirement_ids"]) \
                    and rel["condition_id"] not in c.open_cids:
                return RESOLVED
    return NOT_RECHECKED


RECHECKS = {"leftover_placed": _leftover_placed, "name_used": _name_used, "condition_bound": _condition_bound,
            "group_scope_held": _group_scope_held, "pair_relation": _pair_relation}


def recheck_rule_failure(c: _Recheck, row: dict) -> tuple[str, str]:
    """(outcome, check) of one rule failure source (``data/decompose_policy.json`` ``rule_failures.checks``)."""
    name = RULE_CHECKS.get(row.get("condition") or "", {}).get(row.get("detail") or "")
    if name is None:
        return NOT_RECHECKED, ""
    return RECHECKS[name](c, row), name


def _map_requirements(q: Question, rids: list[str], rules: dict, settled: dict) -> list[str] | None:
    """Rule requirement IDs -> requirement IDs of the settled reading: the same meaning first, else the requirements
    whose refs overlap the rule requirement's indicator (or its span); None when one of them maps to none."""
    rule_reqs = {r["id"]: r for r in (rules.get("interpretation") or {}).get("requirements") or []}
    new_reqs = settled["interpretation"]["requirements"]
    by_canon: dict[str, list[str]] = {}
    for r in new_reqs:
        by_canon.setdefault(_req_canon(r), []).append(r["id"])
    out: list[str] = []
    for rid in rids:
        rr = rule_reqs.get(rid)
        if rr is None:
            return None
        hit = by_canon.get(_req_canon(rr))
        if not hit:
            ind = rr.get("indicator") or {}
            sp = ind.get("span") or rr.get("span")
            hit = [r["id"] for r in new_reqs if sp and any(_overlap(q.seg[s]["span"], sp)
                                                           for s in r["refs"]["segment_ids"] if s in q.seg)]
        if not hit:
            return None
        out += [x for x in hit if x not in out]
    return out


def _map_fragments(fids: list[str], rules: dict, settled: dict) -> list[str] | None:
    spans = {f["id"]: f["span"] for f in rules.get("fragments") or []}
    out: list[str] = []
    for fid in fids:
        sp = spans.get(fid)
        hit = [f["id"] for f in settled["fragments"] if sp and _overlap(f["span"], sp)]
        if not hit:
            return None
        out += [x for x in hit if x not in out]
    return out


def carry_rule_failures(q: Question, settled: dict, rules: dict, reading: dict, issues: list[dict]) -> None:
    """User decision 10/20 22:10 and fix round 2 (C): every rule failure source is re-checked against the adopted
    reading (``recheck_rule_failure``) and recorded in ``settled["rule_checks"]``. A source the code resolved is left
    out; a still valid one, and one with no re-check (kept conservatively, recorded as not re-checked), is mapped to
    the reading's requirement, fragment and condition IDs (a scope that cannot be mapped: kept for the whole question
    with the ``scope_unmapped`` detail). Never a copy because the structures are the same."""
    rules_j = rules.get("judgement") or {}
    cond_ids = {c["id"] for c in q.conditions}
    prefix, unmapped = RULE_FAIL["detail_prefix"], RULE_FAIL["scope_unmapped"]
    sources = settled["judgement"]["failed_sources"]
    ctx = _Recheck(q, rules, settled, reading, issues)
    checks: list[dict] = []
    for row in rules_j.get("failed_sources") or []:
        kind = row.get("condition")
        if not kind:
            continue
        outcome, name = recheck_rule_failure(ctx, row)
        checks.append({"condition": kind, "detail": row.get("detail") or "", "outcome": outcome, "check": name})
        if outcome == RESOLVED:
            continue
        detail = f"{prefix}:{row.get('detail') or ''}"
        rids = _map_requirements(q, list(row.get("requirement_ids") or []), rules, settled)
        fids = _map_fragments(list(row.get("fragment_ids") or []), rules, settled)
        cids = list(row.get("condition_ids") or [])
        if rids is None or fids is None or any(c not in cond_ids for c in cids):
            add_source(sources, kind, [], f"{prefix}:{unmapped}:{row.get('detail') or ''}")
            continue
        add_source(sources, kind, rids, detail, cids, fids)
    settled["rule_checks"] = checks


def uncertain_only(settled: dict, rules_certain: bool, cmp: dict, verified: bool, rules_failed,
                   alternative_kept: bool) -> bool:
    """User decision 10/20 22:48 (B): the one reason the reading is not certain is 7.2's "the rules are certain".
    All of: the rules were not certain; the new structure was verified and means what the rules mean; every rule
    failure source was re-checked and resolved by the code, and every failed condition of the rules had such a source;
    the reading has no failed source, seed, leftover, unresolved requirement or ambiguous condition of its own, and no
    alternative reading is kept beside it (nor is it that alternative)."""
    j, interp = settled["judgement"], settled["interpretation"]
    checks = settled.get("rule_checks") or []
    return bool(not rules_certain and verified and cmp["same"] and not alternative_kept
                and checks and all(c["outcome"] == RESOLVED for c in checks)
                and set(rules_failed or []) <= {c["condition"] for c in checks}
                and not j["failed_sources"] and not settled["assumption_seeds"] and not j.get("leftover")
                and not any(r["status"] == "unresolved" or r.get("ambiguous_condition_ids")
                            for r in interp["requirements"]))


def finish_certainty(settled: dict, rules_certain: bool, cmp: dict, verified: bool, rules_failed=(),
                     alternative_kept: bool = False) -> dict:
    """7.2 on a settled reading (its failures, seeds and rule checks complete): ``certain``, ``failed_conditions``,
    the interpretation status and reasons. Returns the ``CertaintyBasis``. ``rules_failed``: the failed conditions of
    the rules; ``alternative_kept``: an alternative reading is kept beside this one (or this one is it).

    When the rules being uncertain is the one reason left (``uncertain_only``, user decision 10/20 22:48), no row is
    written: ``select/needs.py`` would read a scope-less row as question-wide uncertainty. The basis records it
    (``certain`` false, ``reason``). In every other case a reading that is not certain names a failed source (none
    left: the residual question-level row of ``rule_failures``). The interpretation status is unchanged by this (not
    certain: ask; asking is decided later by ``question_route``)."""
    j, interp = settled["judgement"], settled["interpretation"]
    failures = list(dict.fromkeys(row["condition"] for row in j["failed_sources"]))
    seeds_left = bool(settled["assumption_seeds"])
    certain = bool(rules_certain and cmp["same"] and verified and not failures and not seeds_left)
    only = not certain and uncertain_only(settled, rules_certain, cmp, verified, rules_failed, alternative_kept)
    if not certain and not j["failed_sources"] and not only:
        residual = RULE_FAIL["residual"]
        add_source(j["failed_sources"], residual["condition"], [], residual["detail"])
        failures = list(dict.fromkeys(row["condition"] for row in j["failed_sources"]))
    j["certain"], j["failed_conditions"] = certain, failures
    statuses = [r["status"] for r in interp["requirements"]]
    if statuses and all(s == "unsupported" for s in statuses):
        status = "unsupported"
    elif "unsupported" in statuses:
        status = "partial"
    elif not certain:
        status = "ask"
    else:
        status = "ok"
    interp["status"], interp["reasons"] = status, list(failures)
    basis = {"rules_certain": bool(rules_certain), "same_structure": bool(cmp["same"]), "verified": bool(verified),
             "seeds_left": seeds_left, "failures_left": bool(failures), "differences": list(cmp["differences"])}
    if only:
        basis.update(certain=False, reason=RULE_FAIL["uncertain_only"])
    return basis
