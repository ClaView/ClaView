"""verify (plan 15 r3, 6): the code check of a decomposition proposal.

Outcomes (6.1): ``fallback`` (the whole question goes to the rules path; no requirement is mixed in from the rules),
``accept`` (the structure is kept; its findings are kept as assumption material, scoped to requirements, segments and
conditions), and per alternative ``dropped`` (only the alternative failed; the primary reading stays).

Format (step 3 fix 1): a violation of the top-level contract (a required top-level field missing, a top-level field
not allowed, a primary reading or ambiguity that breaks the schema, an alternative that is no reading or null) is a
fallback; only an error inside an alternative reading that is there (an object) drops that alternative.

Fallback (primary reading or the proposal's ambiguities; in the alternative only: the alternative is dropped):
an ID is unknown, a sense is not one of the senses of what its ref quotes, a subquote is not found exactly once in
its single segment, a meaningful segment (name, condition, marker or word that is no filler) or the meaningful rest of
a segment beside its subquotes is used nowhere (requirement, condition binding, polarity link, request, unused refs,
ambiguity), a condition is not bound, bound twice in different ways or bound inconsistently, a sure target-only sense
or name is the indicator (fix 2: the model's own role ambiguity is no ground), request refs swallow a name or an
unbound condition, a limit is exceeded, and the polarity rows of 6.2 (fix 3): a polarity link without a negation or
exclusion marker; a code-sure "not excluded" (an exclusion stem with the lexicon's negated ending and negating stem)
applied as an exclusion; a clear (or certain) object of a marker missing from the negative side of the link that
cites the marker, another name negated by a clear marker, the object put on the positive side, or required
(indicator, target, basis or breakdown value) by a requirement the exclusion acts on (an unresolved binding: by every
candidate requirement); a marker of clear (or certain) scope put only in the request refs, the unused refs or the
ambiguities, its effect lost.

Accept with findings: an unclear polarity scope (``unclear_scope``: kept, never certain), an unmapped concept (a ref,
or a subquote, with no catalog name), an unconfirmed breakdown value, an unresolved indicator, sense or condition
attribution, a role the code finds open (a non-sure target sense, a basis or breakdown sense or name as the
indicator: ``role_ambiguous``; a sense of a role its place does not take: ``role_mismatch``; an unclassified role), a
breakdown marker inside a name or a breakdown value written twice (``breakdown_incomplete``), a meaningful word set
aside as request or unattached words without the code confirming it as such (``unused_unresolved``, fix 5; a
confirmed request word of the data file only within a request phrase, fix round 2), the ambiguities the model listed.
A condition shared by several requirements is allowed.

Subquotes (fix 4): the code computes the raw span of a subquote and reads it by the name mentions written exactly
there (``local["mention_spans"]``); the sense of the whole name is never handed down to a part of it. Plan 19 stage 3:
the source range of every ref is read by one rule (``decompose_refs.RefReader``) that the settling and the selection
refs use too; a whole ref does not read an inner mention (a name strictly inside one segment), so a sense only a part
has on a whole ref is a ``wrong_sense`` (fatal, as a whole-name sense on a part is).

Plan 19 stage 3, accepted with a scoped finding (never certain; the model choosing one reading resolves none):
``basis_merged`` (``data/decompose_policy.json`` ``basis_merge``: non-fatal by default, user decision 10/21; set to
fatal it is ``basis_merged_fatal``, a fallback reason): two different positive basis conditions bound to one
requirement (requirements scope, or question scope with it) that the existing rule of the rules path holds exclusive
(``table._exclusive``: one slot of the condition word list, or words that stand in for each other in table names); the
facts are made by ``build_payload`` in ``local["basis_exclusive"]`` (verify reads no catalog; without them nothing is
flagged). Left out: negated conditions (polarity ``-``, on the negative side of a polarity link, inside an exclusion
condition), name content and unresolved bindings, and two conditions with a choice word of the lexicon between them
(the coordinator words that are not conjunctive). Basis words of different slots (a price word and a valuation word),
survey view words and other choice expressions are not read: the verification stays silent there (unresolved scope).
``ratio_operands``: a ratio relation (relation kind ``ratio`` of ``settle.RELATION_KINDS``) that does not name exactly
two distinct requirements (fewer than two distinct stays ``relation_incomplete``). ``ratio_order_conflict``: ratio
relations over the same two requirements in different orders: the direction is unknown (the order is never read as
numerator and denominator; the relation contract is unchanged). ``added_measure``: a chosen sense matched by the
lexical path whose catalog name holds a measurement meaning the question does not (a basis word of a slot axis of the
condition word list, or a rate word of its calc hints), scoped to that ref.

A clear scope is read from the segments only (data file ``polarity``): one name segment, at most one object particle
and spaces, then the marker; before the name nothing, a closing particle, a sentence-end mark or a request word. When
the name before the marker is the last member of a list, the scope is unclear but that name is excluded under every
reading (the certain part, user decision 10/20 22:10). The rules' ``negated``/``excluded`` readings and the clause
exclusions of 2.2 are hints and never decide a pass. Shared tables are never a pass condition, and same-axis facts
never force a merge or a split.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import get_args

from ..catalog.text import index_key
from ..contract.check import scope_index
from ..contract.shapes import Condition, FallbackCode, Mention, Segment, TextView
from ..reused.q_nodes import normalize
from . import lexicon as LX
from .condition_rules import WORDS as CONDITION_WORDS
from .condition_rules import q4_rows
from .decompose_payload import POLICY, condition_segments, is_input_limit, segment_markers
from .decompose_refs import RefReader
from .decompose_refs import occurrences as _occurrences
from .decompose_schema import ID_FIELDS, build_schema, validate
from .settle import RELATION_KINDS
from .table import DICT_FREE_AXES

FALLBACK = frozenset(get_args(FallbackCode))
_POL = POLICY["polarity"]
POLARITY_MARKERS = frozenset(_POL["markers"])
OBJECT_PARTICLES = frozenset(index_key(p) for p in _POL["object_particles"])
CLOSING_PARTICLES = frozenset(index_key(p) for p in _POL["closing_particles"])
CERTAIN_BEFORE = frozenset(_POL["certain_before_markers"])
NEGATED_FORMS = tuple(index_key(s + LX.EXCLUDE_NEGATED_ENDING) for s in POLICY["negated_exclusion"]["stems"])
NEGATING = tuple(index_key(w) for w in LX.EXCLUDE_NEGATED_BY)
AUX = tuple(index_key(a) for a in LX.AUX)
_COV = POLICY["coverage"]
MEANINGFUL = frozenset(_COV["meaningful_kinds"])
IGNORABLE = frozenset(index_key(w) for w in _COV["ignorable_words"])
CONFIRMED_WORDS = frozenset(index_key(w) for w in _COV["confirmed_words"])
REMAINDER_PARTICLES = frozenset(index_key(w) for w in _COV["remainder_particles"])
_ROLES = POLICY["roles"]
INDICATOR_ROLES = frozenset(_ROLES["indicator_roles"])
TARGET_ONLY = frozenset(_ROLES["target_only_roles"])
PLACE_ROLES = {k: frozenset(v) for k, v in _ROLES["place_roles"].items()}
SURE_PATHS = frozenset(_ROLES["sure_paths"])
UNCLASSIFIED = "unclassified"
POSITIVE_PLACES = ("indicator", "target", "basis", "breakdown_value")
REQUIREMENT_PLACES = ("indicator", "target", "basis", "context", "breakdown_axis", "breakdown_value",
                      "breakdown_marker")
# plan 19 stage 3
BASIS_MERGE_CODES = {"non_fatal": "basis_merged", "fatal": "basis_merged_fatal"}
_COORD = LX.LEX["coordinator"]
CHOICE_WORDS = frozenset(index_key(w) for w in _COORD["words"] if w not in _COORD["conjunctive"])  # 또는, 혹은
RATIO = "ratio"
MEASURE_PLACES = ("indicator", "target", "basis", "context", "breakdown_axis", "breakdown_value")
RATE_PATTERNS = tuple(re.compile(p["regex"]) for p in CONDITION_WORDS["calc_hints"]["patterns"]
                      if p["kind"] == "rate")
RATE = "rate"
COMPARISON_AXIS = "comparison"


def basis_merge_code() -> str:
    """The finding code of a basis merge by the policy (``basis_merge.effect``, read at call time)."""
    return BASIS_MERGE_CODES[POLICY["basis_merge"]["effect"]]


def measure_meanings(text: str) -> set[str]:
    """Measurement meanings written in a text, read by the rules' own condition reader (``q4_rows``): basis words and
    comparison or calculation phrases of a slot axis of the condition word list (axis:value; the classification and
    family axes name no slot) and, as one meaning, a rate word of the calc hints."""
    norm = normalize(text)["query"]
    out = {f"{r['axis']}:{r['value']}" for r in q4_rows(norm) if r["axis"] not in DICT_FREE_AXES}
    if any(p.search(norm) for p in RATE_PATTERNS):
        out.add(RATE)
    return out


def _issue(code: str, reading: str, rids=(), sids=(), cids=(), detail: str = "") -> dict:
    return {"code": code, "reading": reading, "fatal": code in FALLBACK, "requirement_ids": list(dict.fromkeys(rids)),
            "segment_ids": list(dict.fromkeys(sids)), "condition_ids": list(dict.fromkeys(cids)), "detail": detail}


def _dedupe(issues: list[dict]) -> list[dict]:
    out, seen = [], set()
    for it in issues:
        key = (it["code"], it["reading"], tuple(it["requirement_ids"]), tuple(it["segment_ids"]),
               tuple(it["condition_ids"]), it["detail"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _ignorable_char(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch)[0] in ("P", "S", "Z")


# ---------------------------------------------------------------- question context


class _Ctx:
    def __init__(self, payload: dict, local: dict, text: TextView, conditions: list[Condition],
                 segments: list[Segment], mentions: list[Mention]):
        self.segments = segments
        self.order = {s["segment_id"]: n for n, s in enumerate(segments)}
        self.seg = {s["segment_id"]: s for s in segments}
        self.markers = local.get("segment_markers") or segment_markers(segments, mentions)
        self.cond = {c["id"]: c for c in conditions}
        self.cond_ids = [c["id"] for c in payload["conditions"]]
        cs = local.get("condition_segments") or condition_segments(conditions, mentions)
        self.cond_segs = {cid: set(v) for cid, v in cs.items()}
        self.names = [m for m in mentions if m["kind"] == "name"]
        self.name_segs = {s for m in self.names for s in m["segment_ids"]} | {s["segment_id"] for s in segments
                                                                             if s["kind"] == "name"}
        self.kept = local["mention_kept"]
        self.senses = local["senses"]
        self.spans = local.get("mention_spans") or {}
        self.refs = RefReader(segments, mentions, local)
        self.text = text
        self.conditions = conditions
        # plan 19 stage 3: pairs of basis conditions the rules hold exclusive (made with the catalog by build_payload)
        self.basis_exclusive = {frozenset(p) for p in local.get("basis_exclusive") or [] if len(set(p)) == 2}
        self._question_measures: set[str] | None = None
        self.negated_spans = self._negated_exclusions()
        self.negated_segs = {sid for span in self.negated_spans for sid in span}
        self.polarity = [s["segment_id"] for s in segments if self.markers.get(s["segment_id"]) in POLARITY_MARKERS
                         and s["segment_id"] not in self.negated_segs]
        self.clear = {sid: self._object_before(self.order[sid]) for sid in self.polarity}
        # the object every reading excludes: the clear one, else the last member of a list right before the marker
        self.certain = {sid: self.clear[sid] or self._listed_before(self.order[sid]) for sid in self.polarity}
        self.negated_objects = {o for span in self.negated_spans
                                for o in [self._object_before(self.order[span[0]])] if o is not None}

    # -- segments

    def key(self, sid: str) -> str:
        return index_key(self.seg[sid]["quote"])

    def _skip_space(self, k: int, step: int) -> int:
        while 0 <= k < len(self.segments) and self.segments[k]["kind"] == "space":
            k += step
        return k

    def _negated_exclusions(self) -> list[list[str]]:
        """Code-sure "not excluded" spans: an exclusion stem with the negated ending, then a negating stem (in the
        same written word or the next one)."""
        out = []
        for i, s in enumerate(self.segments):
            k = index_key(s["quote"])
            for form in NEGATED_FORMS:
                if not k.startswith(form):
                    continue
                rest = k[len(form):]
                if rest.startswith(NEGATING):
                    out.append([s["segment_id"]])
                elif rest == "" or rest in AUX:
                    j = self._skip_space(i + 1, 1)
                    if j < len(self.segments) and index_key(self.segments[j]["quote"]).startswith(NEGATING):
                        out.append([x["segment_id"] for x in self.segments[i:j + 1]])
                break
        return out

    def _name_before(self, k: int) -> int | None:
        """Position of the name segment a marker at ``k`` follows (spaces and at most one object particle between)."""
        j = self._skip_space(k - 1, -1)
        if j >= 0 and self.segments[j]["kind"] == "grammar" and index_key(self.segments[j]["quote"]) in \
                OBJECT_PARTICLES:
            j -= 1
        if j < 0 or self.segments[j]["kind"] != "name":
            return None
        return j

    def _object_before(self, k: int) -> str | None:
        """The name segment a marker at position ``k`` clearly takes, or None (6.2: unclear scope)."""
        j = self._name_before(k)
        if j is None:
            return None
        b = self._skip_space(j - 1, -1)
        if b < 0:
            return self.segments[j]["segment_id"]
        before = self.segments[b]
        if (before["kind"] == "grammar" and index_key(before["quote"]) in CLOSING_PARTICLES) \
                or (before["kind"] == "punct" and unicodedata.normalize("NFKC", before["quote"]) in LX.SENTENCE_END) \
                or before["kind"] == "request":
            return self.segments[j]["segment_id"]
        return None

    def _listed_before(self, k: int) -> str | None:
        """The name a marker at ``k`` takes when that name is the last member of a list (a list mark right before
        it): excluded under every reading of the unclear scope."""
        j = self._name_before(k)
        if j is None:
            return None
        b = self._skip_space(j - 1, -1)
        if b >= 0 and self.markers.get(self.segments[b]["segment_id"]) in CERTAIN_BEFORE:
            return self.segments[j]["segment_id"]
        return None

    # -- refs

    def sub_range(self, ref: dict) -> tuple[int, int] | None:
        """Raw [start, end) of a subquote found exactly once in its one segment (``RefReader.sub_range``)."""
        return self.refs.sub_range(ref)

    def name_mentions_in(self, ref: dict) -> list[Mention]:
        """Name mentions a ref quotes (``RefReader.name_mentions``): for a whole ref those over its segments but no
        inner one; for a subquote only those written exactly over the subquote (by their raw span; without spans,
        those whose own catalog name is the subquote)."""
        return self.refs.name_mentions(ref)

    def ref_codes(self, ref: dict) -> list[tuple[str, str]]:
        out = []
        sids = ref["segment_ids"]
        pos = [self.order[s] for s in sids]
        if len(set(sids)) != len(sids) or pos != list(range(pos[0], pos[0] + len(pos))):
            out.append(("ref_not_contiguous", f"segments {sids}"))
        if ref["subquote"] is not None:
            if len(sids) != 1:
                out.append(("subquote_multi_segment", f"subquote over {len(sids)} segments"))
            else:
                n = _occurrences(self.seg[sids[0]]["quote"], ref["subquote"])
                if n == 0:
                    out.append(("subquote_not_found", f"segment {sids[0]}"))
                elif n > 1:
                    out.append(("subquote_repeated", f"segment {sids[0]}: {n} times"))
        if out:
            return out
        z = ref["sense_id"]
        if z is not None and not any(z in self.kept.get(m["mention_id"], []) for m in self.name_mentions_in(ref)):
            where = f"subquote {ref['subquote']!r} of {sids[0]}" if ref["subquote"] is not None else f"segments {sids}"
            out.append(("wrong_sense", f"{z} is no sense of {where}"))
        return out

    def candidates(self, ref: dict) -> list[dict]:
        return [c for m in self.name_mentions_in(ref) for c in m["candidates"]]

    def has_candidates(self, ref: dict) -> bool:
        return bool(self.candidates(ref))

    def mention_span(self, m: Mention) -> tuple[int, int]:
        return self.refs.mention_span(m)

    # -- plan 19 stage 3

    def question_measures(self) -> set[str]:
        """Measurement meanings the question writes (``measure_meanings`` of the raw question) or holds as a basis or
        calc condition of a slot axis; a comparison base counts as the rate meaning too."""
        if self._question_measures is None:
            out = measure_meanings(self.text["raw"])
            for c in self.conditions:
                axis = (c.get("attrs") or {}).get("axis")
                if c["kind"] in ("basis", "calc") and axis and axis not in DICT_FREE_AXES:
                    out.add(f"{axis}:{c.get('value')}")
            if any(m.startswith(COMPARISON_AXIS + ":") for m in out):
                out.add(RATE)
            self._question_measures = out
        return self._question_measures

    def negative_basis(self, cid: str, negative_segs: set[str]) -> bool:
        """A basis condition that is not asked for positively: polarity ``-``, on the negative side of a polarity
        link, or inside an exclusion condition."""
        c = self.cond[cid]
        if (c.get("attrs") or {}).get("polarity") != "+":
            return True
        if self.cond_segs.get(cid, set()) & negative_segs:
            return True
        a, b = c["span"]
        return any(x["kind"] == "exclude" and x["span"][0] <= a and b <= x["span"][1] for x in self.cond.values())

    def choice_between(self, a: str, b: str) -> bool:
        """A choice word of the lexicon (a coordinator word that does not join) written between two conditions."""
        (lo_a, hi_a), (lo_b, hi_b) = self.cond[a]["span"], self.cond[b]["span"]
        lo, hi = min(hi_a, hi_b), max(lo_a, lo_b)
        return any(lo <= s["span"][0] and s["span"][1] <= hi and index_key(s["quote"]) in CHOICE_WORDS
                   for s in self.segments)

    def confirmed_form(self, sid: str) -> bool:
        """A word segment that is a confirmed word of the data file, alone or with one remainder particle."""
        k = index_key(self.seg[sid]["quote"])
        return any(k.startswith(w) and (k == w or k[len(w):] in REMAINDER_PARTICLES) for w in CONFIRMED_WORDS)

    def in_request_phrase(self, sid: str) -> bool:
        """Fix round 2 (E): from the segment rightwards over spaces, grammar segments, ignorable words and other
        confirmed words, the next segment is a request segment."""
        k = self.order[sid] + 1
        while k < len(self.segments):
            s = self.segments[k]
            if s["kind"] == "request":
                return True
            filler = s["kind"] == "word" and (index_key(s["quote"]) in IGNORABLE
                                              or self.confirmed_form(s["segment_id"]))
            if s["kind"] not in ("space", "grammar") and not filler:
                return False
            k += 1
        return False

    def confirmed_word(self, sid: str) -> bool:
        """A segment no meaning is lost by setting aside: not a word, or a word the code confirms (a filler word, a
        word of a code-sure 'not excluded', or a confirmed request word of the data file that is part of a request
        phrase; the same word elsewhere may carry meaning and stays unresolved)."""
        s = self.seg[sid]
        if s["kind"] != "word":
            return True
        k = index_key(s["quote"])
        if k in IGNORABLE or sid in self.negated_segs:
            return True
        return self.confirmed_form(sid) and self.in_request_phrase(sid)


# ---------------------------------------------------------------- one reading


def _reading_refs(reading: dict):
    """(place, requirement ID or None, owner index, ref) of every ref of a reading."""
    for r in reading["requirements"]:
        rid = r["id"]
        if r["indicator"] is not None:
            yield "indicator", rid, None, r["indicator"]
        for x in r["targets"]:
            yield "target", rid, None, x
        for x in r["basis_refs"]:
            yield "basis", rid, None, x
        if r["context"] is not None:
            yield "context", rid, None, r["context"]
        for b in r["breakdowns"]:
            if b["axis"] is not None:
                yield "breakdown_axis", rid, None, b["axis"]
            for x in b["values"]:
                yield "breakdown_value", rid, None, x
            for x in b["marker_refs"]:
                yield "breakdown_marker", rid, None, x
    for n, b in enumerate(reading["condition_bindings"]):
        for x in b["name_refs"]:
            yield "name_ref", None, n, x
    for n, p in enumerate(reading["polarity_links"]):
        for field in ("marker_refs", "negative_refs", "positive_refs"):
            for x in p[field]:
                yield f"polarity_{field.removesuffix('_refs')}", None, n, x
    for x in reading["request_refs"]:
        yield "request", None, None, x
    for u in reading["unused_refs"]:
        yield "unused", None, None, u["ref"]


def _segs(refs) -> set[str]:
    return {s for r in refs for s in r["segment_ids"]}


def _binding_rids(b: dict, all_rids: list[str]) -> list[str]:
    if b["scope"] == "question":
        return list(all_rids)
    if b["scope"] == "requirements":
        return list(b["requirement_ids"])
    return []


def _indicator_issue(ctx: _Ctx, ind: dict) -> tuple[str, str] | None:
    """Fix 2: the role check of an indicator ref (module docstring); None when its role fits."""
    z = ind["sense_id"]
    if z is not None:
        s = ctx.senses[z]
        role, path = s["role"], s["path"]
        if role in INDICATOR_ROLES:
            return None
        if role in TARGET_ONLY and path in SURE_PATHS:
            return "target_as_indicator", f"indicator sense {z}: {role} {path}"
        if role == UNCLASSIFIED:
            return "indicator_role_unclassified", f"sense {z}"
        return "role_ambiguous", f"indicator sense {z}: {role} {path}"
    cands = ctx.candidates(ind)
    if not cands:
        return "unmapped_concept", "indicator"
    if any(c["role"] in INDICATOR_ROLES for c in cands):
        return "sense_unresolved", "indicator"
    if all(c["role"] in TARGET_ONLY and c["path"] in SURE_PATHS for c in cands):
        return "target_as_indicator", "indicator name: sure target-only candidates"
    if any(c["role"] == UNCLASSIFIED for c in cands):
        return "indicator_role_unclassified", "indicator name"
    return "role_ambiguous", "indicator name: no indicator candidate"


def _place_role_issue(ctx: _Ctx, place: str, ref: dict) -> str | None:
    """Fix 2: the detail of a role_mismatch of a non-indicator place, or None."""
    allowed = PLACE_ROLES.get(place)
    if allowed is None:
        return None
    z = ref["sense_id"]
    if z is not None:
        role = ctx.senses[z]["role"]
        return None if role in allowed else f"{place}: sense {z} {role}"
    cands = ctx.candidates(ref)
    if cands and not any(c["role"] in allowed for c in cands):
        return f"{place}: no candidate of a fitting role"
    return None


def _coverage_missing(ctx: _Ctx, refs: list, bound_ids: set[str], ambiguities: list[dict]) -> list[str]:
    """Meaningful segments used nowhere, and segments whose rest beside their subquotes is meaningful (fix 4)."""
    whole: set[str] = set()
    parts: dict[str, list[tuple[int, int]]] = {}

    def cover(ref: dict) -> None:
        if ref["subquote"] is None:
            whole.update(ref["segment_ids"])
            return
        rng = ctx.sub_range(ref)
        if rng is not None:
            parts.setdefault(ref["segment_ids"][0], []).append(rng)

    for place, _, _, ref in refs:
        if place != "name_ref":  # a name ref only locates a condition inside a name: the name itself is not used
            cover(ref)
    for a in ambiguities:
        for ref in a["refs"]:
            cover(ref)
    for cid in bound_ids:
        c = ctx.cond.get(cid)
        sids = ctx.cond_segs.get(cid, set())
        if c is None or not c.get("span"):
            whole.update(sids)
            continue
        a, b = c["span"]
        for sid in sids:
            lo, hi = ctx.seg[sid]["span"]
            if a <= lo and hi <= b:
                whole.add(sid)
            else:
                parts.setdefault(sid, []).append((max(a, lo), min(b, hi)))
    missing = []
    for s in ctx.segments:
        sid = s["segment_id"]
        if s["kind"] not in MEANINGFUL or sid in whole:
            continue
        if sid not in parts:
            if not (s["kind"] == "word" and index_key(s["quote"]) in IGNORABLE):
                missing.append(sid)
            continue
        lo = s["span"][0]
        taken = [False] * len(s["quote"])
        for a, b in parts[sid]:
            for i in range(max(a, lo) - lo, min(b - lo, len(taken))):
                taken[i] = True
        runs, cur = [], ""
        for ch, t in zip(s["quote"], taken):
            if t or _ignorable_char(ch):
                if cur:
                    runs.append(cur)
                cur = ""
            else:
                cur += ch
        if cur:
            runs.append(cur)
        if any(index_key(r) not in REMAINDER_PARTICLES for r in runs):
            missing.append(sid)
    return missing


def _check_reading(reading: dict, which: str, ctx: _Ctx, ambiguities: list[dict]) -> list[dict]:
    issues: list[dict] = []
    add = issues.append
    reqs = reading["requirements"]
    rids = [r["id"] for r in reqs]
    known = set(rids)
    if len(known) != len(rids):
        add(_issue("format", which, detail="duplicate requirement IDs"))
    refs = list(_reading_refs(reading))
    for place, rid, _, ref in refs:
        for code, detail in ctx.ref_codes(ref):
            add(_issue(code, which, [rid] if rid else [], ref["segment_ids"], detail=f"{place}: {detail}"))
    if any(it["fatal"] for it in issues):
        return issues  # later checks read the refs as valid

    # -- requirements (fix 2: roles by place; the model's role ambiguity is no ground)
    for r in reqs:
        rid = r["id"]
        ind = r["indicator"]
        if ind is None:
            add(_issue("indicator_unresolved", which, [rid]))
        else:
            found = _indicator_issue(ctx, ind)
            if found is not None:
                add(_issue(found[0], which, [rid], ind["segment_ids"], detail=found[1]))
        for t in r["targets"]:
            if t["sense_id"] is None:
                code = "sense_unresolved" if ctx.has_candidates(t) else "unmapped_concept"
                add(_issue(code, which, [rid], t["segment_ids"], detail="target"))
        for b in r["breakdowns"]:
            if (b["kind"] == "axis" and b["axis"] is None) or (b["kind"] == "values" and not b["values"]):
                add(_issue("breakdown_incomplete", which, [rid], detail=f"breakdown {b['kind']}"))
            seen_values: set[tuple] = set()
            for v in b["values"]:
                if v["sense_id"] is None:
                    add(_issue("breakdown_value_unconfirmed", which, [rid], v["segment_ids"]))
                key = (tuple(v["segment_ids"]), v["subquote"])
                if key in seen_values:
                    add(_issue("breakdown_incomplete", which, [rid], v["segment_ids"], detail="value repeated"))
                seen_values.add(key)
            for x in b["marker_refs"]:  # a character inside a name is no marker of the question
                if any(s in ctx.name_segs for s in x["segment_ids"]):
                    add(_issue("breakdown_incomplete", which, [rid], x["segment_ids"], detail="marker inside a name"))
    for place, rid, _, ref in refs:
        if place != "indicator" and rid is not None:
            detail = _place_role_issue(ctx, place, ref)
            if detail is not None:
                add(_issue("role_mismatch", which, [rid], ref["segment_ids"], detail=detail))
    for place, rid, _, ref in refs:  # plan 19 stage 3: a lexical sense that adds a measurement meaning
        if rid is not None and place in MEASURE_PLACES:
            extra = _added_measures(ctx, ref)
            if extra:
                add(_issue("added_measure", which, [rid], ref["segment_ids"],
                           detail=f"{place}: sense {ref['sense_id']} adds {'+'.join(extra)}"))

    # -- condition bindings and relations
    bound: dict[str, list[dict]] = {}
    for b in reading["condition_bindings"]:
        bound.setdefault(b["condition_id"], []).append(b)
    for b in reading["condition_bindings"]:
        unknown = [x for x in b["requirement_ids"] if x not in known]
        if unknown:
            add(_issue("unknown_id", which, sids=[], cids=[b["condition_id"]], detail=f"requirements {unknown}"))
    for cid in ctx.cond_ids:
        rows = bound.get(cid, [])
        cs = sorted(ctx.cond_segs.get(cid, set()), key=ctx.order.get)
        if not rows:
            add(_issue("condition_missing", which, sids=cs, cids=[cid]))
            continue
        canon = {json.dumps(b, sort_keys=True, ensure_ascii=False) for b in rows}
        if len(canon) > 1:
            add(_issue("condition_conflict", which, sids=cs, cids=[cid], detail=f"{len(rows)} bindings"))
            continue
        b = rows[0]
        scope, brids = b["scope"], b["requirement_ids"]
        if scope == "question" and brids and set(brids) != known:
            add(_issue("condition_binding_invalid", which, brids, cs, [cid], "question scope names some requirements"))
        elif scope == "requirements" and not brids:
            add(_issue("condition_binding_invalid", which, [], cs, [cid], "requirements scope names none"))
        elif scope == "unresolved":
            add(_issue("condition_unresolved", which, [x for x in brids if x in known], cs, [cid]))
        elif scope == "name_content" and not _inside_name(ctx, cid, b["name_refs"]):
            add(_issue("condition_binding_invalid", which, brids, cs, [cid], "not inside the named name"))
    seen_rel: set[str] = set()
    ratio_orders: dict[frozenset, list[tuple[tuple, str]]] = {}  # plan 19 stage 3: the operand orders of each pair
    for rel in reading["relations"]:
        cid, rr = rel["condition_id"], rel["requirement_ids"]
        unknown = [x for x in rr if x not in known]
        if unknown:
            add(_issue("unknown_id", which, cids=[cid], detail=f"requirements {unknown}"))
            continue
        if cid in seen_rel:
            add(_issue("condition_conflict", which, rr, cids=[cid], detail="two relations of one condition"))
        seen_rel.add(cid)
        if len(set(rr)) < 2:
            add(_issue("relation_incomplete", which, rr, cids=[cid]))
        elif _is_ratio(ctx, cid):
            if len(rr) != 2:  # three or more operands, or one named twice
                add(_issue("ratio_operands", which, rr, cids=[cid],
                           detail=f"ratio over {len(rr)} operands, {len(set(rr))} distinct"))
            else:
                ratio_orders.setdefault(frozenset(rr), []).append((tuple(rr), cid))
        b = (bound.get(cid) or [None])[0]
        if b is not None and b["scope"] == "requirements" and not set(rr) <= set(b["requirement_ids"]):
            add(_issue("condition_conflict", which, rr, cids=[cid], detail="relation outside its binding"))
    for rows in ratio_orders.values():
        if len({order for order, _ in rows}) > 1:  # the direction is unknown; no order is read as a direction
            add(_issue("ratio_order_conflict", which, list(rows[0][0]), cids=[c for _, c in rows],
                       detail="ratio relations of one pair in different orders: direction unknown"))
    for it in _basis_merges(ctx, reading, which, bound, rids):
        add(it)
    bound_ids = set(bound)
    bound_segs = {s for cid in bound_ids for s in ctx.cond_segs.get(cid, set())}

    # -- polarity (6.2, fix 3)
    links = reading["polarity_links"]
    link_markers = _segs(m for p in links for m in p["marker_refs"])
    req_by_id = {r["id"]: r for r in reqs}

    def positive_use(rid: str, obj: str) -> bool:
        return any(place in POSITIVE_PLACES and owner == rid and obj in ref["segment_ids"]
                   for place, owner, _, ref in refs)

    for n, p in enumerate(links):
        unknown = [x for x in p["requirement_ids"] if x not in known]
        if unknown:
            add(_issue("unknown_id", which, detail=f"polarity link {n}: requirements {unknown}"))
            continue
        prids = p["requirement_ids"] or rids
        msegs = _segs(p["marker_refs"])
        tag = f"polarity link {n} ({p['kind']})"
        if msegs & ctx.negated_segs:
            code = "polarity_negated_exclusion" if p["kind"] == "exclude" else "polarity_no_marker"
            add(_issue(code, which, prids, sorted(msegs, key=ctx.order.get), detail=tag))
            continue
        if not p["marker_refs"] or not msegs & set(ctx.polarity):
            add(_issue("polarity_no_marker", which, prids, sorted(msegs, key=ctx.order.get), detail=tag))
            continue
        neg, pos = _segs(p["negative_refs"]), _segs(p["positive_refs"])
        if p["kind"] == "exclude" and neg & ctx.negated_objects:
            add(_issue("polarity_negated_exclusion", which, prids, sorted(neg & ctx.negated_objects, key=ctx.order.get),
                       detail=tag))
        live = sorted(msegs & set(ctx.polarity), key=ctx.order.get)
        objs = {ctx.certain[m] for m in live if ctx.certain[m] is not None}
        if not p["negative_refs"] and not objs:
            add(_issue("polarity_incomplete", which, prids, sorted(msegs, key=ctx.order.get), detail=tag))
        for m in live:
            obj = ctx.certain[m]
            if obj is None:
                continue
            if obj in pos:
                add(_issue("polarity_inverted", which, prids, [m, obj], detail=tag))
            elif obj not in neg:
                code = "polarity_target_mismatch" if p["negative_refs"] else "polarity_effect_lost"
                add(_issue(code, which, prids, [m, obj], detail=f"{tag}: object not negated"))
            for rid in prids:
                if rid in req_by_id and positive_use(rid, obj):
                    add(_issue("polarity_excluded_required", which, [rid], [m, obj], detail=tag))
        if live and all(ctx.clear[m] is not None for m in live) and not objs & pos:
            extra = sorted((neg & ctx.name_segs) - objs, key=ctx.order.get)
            if extra:
                add(_issue("polarity_target_mismatch", which, prids, live + extra,
                           detail=f"{tag}: other names negated"))
    # an exclusion written as a bound condition ("A를 뺀") acts on the requirements of its binding; an unresolved
    # binding acts on one of its candidates, so the object required by every candidate is required by the one it acts on
    for cid in bound_ids:
        b = bound[cid][0]
        for m in [s for s in ctx.polarity if s in ctx.cond_segs.get(cid, set())]:
            obj = ctx.certain[m]
            if obj is None or ctx.markers.get(m) != "exclusion":
                continue
            if b["scope"] == "unresolved":
                cands = [x for x in b["requirement_ids"] if x in known] or rids
                if cands and all(positive_use(rid, obj) for rid in cands):
                    add(_issue("polarity_excluded_required", which, cands, [m, obj], [cid],
                               f"condition {cid} (unresolved)"))
                continue
            for rid in _binding_rids(b, rids):
                if positive_use(rid, obj):
                    add(_issue("polarity_excluded_required", which, [rid], [m, obj], [cid], f"condition {cid}"))
    request_segs = _segs(reading["request_refs"])
    aside = _segs(ref for place, _, _, ref in refs if place == "unused") | _segs(r for a in ambiguities
                                                                                 for r in a["refs"])
    for m in ctx.polarity:
        used = m in link_markers or m in bound_segs
        if m in request_segs and not used:
            add(_issue("polarity_marker_request_only", which, sids=[m]))
        elif not used and m in aside:
            if ctx.certain[m] is not None:
                add(_issue("polarity_effect_lost", which, sids=[m, ctx.certain[m]], detail="marker set aside"))
            else:
                add(_issue("polarity_unattached", which, sids=[m]))
        if ctx.clear[m] is None:
            scope = [x for p in links if m in _segs(p["marker_refs"]) for x in (p["requirement_ids"] or rids)]
            scope += [x for cid in bound_ids if m in ctx.cond_segs.get(cid, set())
                      for x in _binding_rids(bound[cid][0], rids)]
            cids = [cid for cid in ctx.cond_ids if m in ctx.cond_segs.get(cid, set())]
            add(_issue("unclear_scope", which, scope, [m], cids))

    # -- request refs (6.1: no name or unbound condition may be swallowed as request words)
    other = _segs(ref for place, _, _, ref in refs if place != "request") | _segs(r for a in ambiguities
                                                                                  for r in a["refs"])
    for sid in sorted(request_segs, key=ctx.order.get):
        if sid in ctx.name_segs and sid not in other and sid not in bound_segs:
            add(_issue("request_swallow", which, sids=[sid], detail="name"))
        for cid, segs in ctx.cond_segs.items():
            if sid in segs and cid not in bound_ids:
                add(_issue("request_swallow", which, sids=[sid], cids=[cid], detail="condition"))

    # -- words set aside (fix 5): only the code confirms that a meaningful word needs no meaning
    placed = _segs(ref for place, _, _, ref in refs if place in REQUIREMENT_PLACES or place.startswith("polarity_"))
    for sid in sorted(request_segs, key=ctx.order.get):
        if not ctx.confirmed_word(sid) and sid not in placed:
            add(_issue("unused_unresolved", which, sids=[sid], detail="request"))
    for u in reading["unused_refs"]:
        if u["reason"] == "unmapped":
            add(_issue("unmapped_concept", which, sids=u["ref"]["segment_ids"], detail="unused"))
        elif u["reason"] == "unresolved":
            add(_issue("unused_unresolved", which, sids=u["ref"]["segment_ids"]))
        else:
            for sid in u["ref"]["segment_ids"]:
                if not ctx.confirmed_word(sid) and sid not in placed:
                    add(_issue("unused_unresolved", which, sids=[sid], detail="unattached"))

    # -- segment preservation: every meaningful segment (and the meaningful rest beside subquotes) is used somewhere
    missing = _coverage_missing(ctx, refs, bound_ids, ambiguities)
    if missing:
        add(_issue("segment_unused", which, sids=missing))
    return issues


def _is_ratio(ctx: _Ctx, cid: str) -> bool:
    c = ctx.cond.get(cid)
    return c is not None and RELATION_KINDS.get((c.get("attrs") or {}).get("relation")) == RATIO


def _basis_merges(ctx: _Ctx, reading: dict, which: str, bound: dict[str, list[dict]], rids: list[str]) -> list[dict]:
    """Plan 19 stage 3 (module docstring): one finding per requirement and pair of exclusive positive basis
    conditions applied to it. A condition with conflicting bindings is left to ``condition_conflict``."""
    if not ctx.basis_exclusive:
        return []
    known = set(rids)
    negative = _segs(ref for p in reading["polarity_links"] for ref in p["negative_refs"])
    applied: dict[str, list[str]] = {}
    for cid in ctx.cond_ids:
        rows = bound.get(cid) or []
        c = ctx.cond.get(cid)
        if c is None or c["kind"] != "basis" or not rows or len({json.dumps(b, sort_keys=True) for b in rows}) > 1:
            continue
        b = rows[0]
        if b["scope"] not in ("question", "requirements") or ctx.negative_basis(cid, negative):
            continue
        for rid in _binding_rids(b, rids):
            if rid in known and cid not in applied.setdefault(rid, []):
                applied[rid].append(cid)
    code = basis_merge_code()
    out = []
    for rid in rids:
        cids = applied.get(rid, [])
        for i, a in enumerate(cids):
            for b in cids[i + 1:]:
                if frozenset((a, b)) in ctx.basis_exclusive and not ctx.choice_between(a, b):
                    sids = sorted(ctx.cond_segs.get(a, set()) | ctx.cond_segs.get(b, set()), key=ctx.order.get)
                    out.append(_issue(code, which, [rid], sids, [a, b], f"exclusive basis conditions {a} and {b}"))
    return out


def _added_measures(ctx: _Ctx, ref: dict) -> list[str]:
    """Plan 19 stage 3: the measurement meanings the chosen lexical sense's catalog name adds to the question."""
    z = ref["sense_id"]
    if z is None or ctx.senses[z]["path"] != "lexical":
        return []
    return sorted(measure_meanings(ctx.senses[z]["name"] or "") - ctx.question_measures())


def _inside_name(ctx: _Ctx, cid: str, name_refs: list[dict]) -> bool:
    """name_content: the condition's words lie inside a name mention the name refs point at."""
    cond = ctx.cond.get(cid)
    if cond is None or not name_refs:
        return False
    a, b = cond["span"]
    for ref in name_refs:
        for m in ctx.name_mentions_in(ref):
            lo, hi = ctx.mention_span(m)
            if lo <= a and b <= hi:
                return True
    return False


# ---------------------------------------------------------------- the proposal


def _schema_code(e: dict) -> str:
    if e["field"] == "requirements" and e["rule"] == "maxItems":
        return "limit_exceeded"
    if e["field"] in ID_FIELDS and (e["rule"] == "enum" or (e["rule"] == "type" and e.get("got") == "str")
                                    or (e["rule"] == "maxItems" and e.get("bound") == 0)):
        return "unknown_id"
    return "format"


def _schema_issues(errors: list[dict], reading: str) -> list[dict]:
    return _dedupe([_issue(_schema_code(e), reading, detail=f"{e['path']}: {e['rule']}") for e in errors])


def _inside_alternative(path: str) -> bool:
    """A schema error path strictly inside the alternative reading (not the top-level field itself, not a top-level
    field whose name merely starts the same way)."""
    return path.startswith("$.alternative.") or path.startswith("$.alternative[")


def _ambiguity_issues(ambiguities: list[dict], primary: dict, ctx: _Ctx) -> list[dict]:
    out = []
    own = {r["id"]: _segs(ref for place, rid, _, ref in _reading_refs(primary)
                          if rid == r["id"]) for r in primary["requirements"]}
    bound = {b["condition_id"]: b for b in primary["condition_bindings"]}
    all_rids = [r["id"] for r in primary["requirements"]]
    for a in ambiguities:
        segs = _segs(a["refs"])
        cids = list(a["condition_ids"])
        for cid, cs in ctx.cond_segs.items():
            if cs & segs and cid not in cids:
                cids.append(cid)
        rids = [rid for rid, s in own.items() if s & segs]
        rids += [x for cid in cids if cid in bound for x in _binding_rids(bound[cid], all_rids)]
        out.append(_issue("ambiguity", "proposal", rids, sorted(segs, key=ctx.order.get), cids,
                          f"{a['kind']}:{a['reason_code']}"))
    return out


def _result(outcome: str, reasons: list[dict], issues: list[dict], alternative: str, alt_issues: list[dict]) -> dict:
    accept = outcome == "accept"
    return {"outcome": outcome, "fallback_reasons": reasons, "issues": issues if accept else [],
            "alternative": alternative, "alternative_issues": alt_issues,
            "assumption_seed": accept and bool(issues or alternative == "kept"),
            "scopes": scope_index(issues if accept else [])}


def _fallback(reasons: list[dict], proposal) -> dict:
    has_alt = isinstance(proposal, dict) and proposal.get("alternative") is not None
    return _result("fallback", _dedupe(reasons), [], "dropped" if has_alt else "absent", [])


def verify(proposal, payload: dict, local: dict, text: TextView, conditions: list[Condition],
           segments: list[Segment], mentions: list[Mention]) -> dict:
    """The ``VerifyResult`` of a proposal (a parsed dict or its JSON text) for the payload it answers."""
    if is_input_limit(payload):
        return _fallback([_issue("limit_exceeded", "proposal", detail=payload["input_limit"]["reason"])], None)
    if isinstance(proposal, (str, bytes)):
        try:
            proposal = json.loads(proposal)
        except ValueError:
            return _fallback([_issue("format", "proposal", detail="not JSON")], None)
    errors = validate(proposal, build_schema(payload))
    alt_value = proposal.get("alternative") if isinstance(proposal, dict) else None
    in_alt = [e for e in errors if isinstance(alt_value, dict) and _inside_alternative(e["path"])]
    top = [e for e in errors if not any(e is x for x in in_alt)]
    if top:
        return _fallback(_schema_issues(top, "primary"), proposal)
    ctx = _Ctx(payload, local, text, conditions, segments, mentions)
    ambiguities = proposal["ambiguities"]
    reasons = [_issue(code, "proposal", sids=ref["segment_ids"], detail=f"ambiguity: {detail}")
               for a in ambiguities for ref in a["refs"] for code, detail in ctx.ref_codes(ref)]
    if reasons:
        return _fallback(reasons, proposal)
    primary = _dedupe(_check_reading(proposal["primary"], "primary", ctx, ambiguities))
    fatal = [it for it in primary if it["fatal"]]
    if fatal:
        return _fallback(fatal, proposal)
    alt = proposal["alternative"]
    if alt is None:
        state, alt_issues = "absent", []
    elif in_alt:
        state, alt_issues = "dropped", _schema_issues(in_alt, "alternative")
    else:
        alt_issues = _dedupe(_check_reading(alt, "alternative", ctx, ambiguities))
        dropped = [it for it in alt_issues if it["fatal"]]
        state, alt_issues = ("dropped", dropped) if dropped else ("kept", alt_issues)
    issues = _dedupe(_ambiguity_issues(ambiguities, proposal["primary"], ctx) + primary)
    return _result("accept", [], issues, state, alt_issues)


def polarity_grounds(payload: dict, local: dict, text: TextView, conditions: list[Condition],
                     segments: list[Segment], mentions: list[Mention]) -> dict:
    """The verification's own polarity facts of one question, for the kept structure (plan 19 contract joint 3; read
    there only, never a new rule): ``certain``: live polarity marker segment -> the name segment it excludes under every
    reading (the clear object, or the last member of a list right before it; None: none is certain)."""
    ctx = _Ctx(payload, local, text, conditions, segments, mentions)
    return {"certain": {m: ctx.certain[m] for m in ctx.polarity}}
