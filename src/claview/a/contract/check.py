"""Shape checks for the outputs defined in ``shapes.py`` (plan 02, 4-1).

Each function returns a list of problems (strings with a path); an empty list means the data has the shape.
Checked: field names and types (from the TypedDicts), enum values, ``span`` inside the question and ``quote`` equal
to the question text there, unique IDs, and that every condition / fragment / requirement / evidence-row ID that is
referred to exists. Plan 15 r3: the optional ``segments``, ``mentions`` and ``ref_map`` of an analysis may be absent
(an analysis of the old shape); when present, the segments cover the question exactly once in order and every
mention and ref-map ID is known (``check_segment_fields``). Step 4: the scopes of the failed sources, requirement
refs, and the optional ``decomposition`` record with its condition fields and hints (``check_step4_fields``).
Plan 19 contract joint: the kept structure of a reading (``check_structure``, ``structure_status``: absent, null,
unsupported version, violation, valid) and the optional public ``bundle`` / ``reference_candidates`` of a selection
(``check_selections``; the bundle's period parts by the shared period package). Plan 19 decision of 2026-10-21:
confirmed effects are held empty (a non-empty list is a violation), and the candidates of a ref are read by the
generator's exact range rule over the optional ``Mention.span`` (``_quoted_candidates``; without spans, by segments).

``partial=True`` is for specification cases that write down only the fields an old test asserted: missing fields are
allowed, unknown fields, wrong types and broken references are still problems. A field given as None is a wrong
type unless its annotation in ``shapes.py`` admits None (``text``, ``conditions``, ``fragments``, ``terms``,
``checks`` ... never do; ``judgement``, ``interpretation`` do).
"""

import types
import typing
from typing import Any, Literal, NotRequired, Union, get_args, get_origin

from . import shapes

_NONE = type(None)
JUDGEMENT_ROWS = 10  # rows of the judgement table (plan 04, 2-2)


# ---------------------------------------------------------------- generic type check


def _type_problems(value, tp, path: str, partial: bool) -> list[str]:
    out: list[str] = []
    _check_type(value, tp, path, partial, out)
    return out


def _check_type(value, tp, path: str, partial: bool, out: list[str]) -> None:
    if tp is Any:
        return
    if get_origin(tp) is NotRequired:  # an optional field: checked as its inner type when present
        (tp,) = get_args(tp)
    if typing.is_typeddict(tp):
        _check_typed_dict(value, tp, path, partial, out)
        return
    origin = get_origin(tp)
    if origin is Literal:
        if value not in get_args(tp):
            out.append(f"{path}: {value!r} not in {list(get_args(tp))}")
        return
    if origin in (Union, types.UnionType):
        arms = get_args(tp)
        if any(not _type_problems(value, arm, path, partial) for arm in arms):
            return
        out.append(f"{path}: {value!r} does not match {tp}")
        return
    if origin is list:
        if not isinstance(value, list):
            out.append(f"{path}: expected a list, got {type(value).__name__}")
            return
        (item,) = get_args(tp) or (Any,)
        for k, v in enumerate(value):
            _check_type(v, item, f"{path}[{k}]", partial, out)
        return
    if origin is dict:
        if not isinstance(value, dict):
            out.append(f"{path}: expected a dict, got {type(value).__name__}")
            return
        key_tp, val_tp = get_args(tp) or (Any, Any)
        for k, v in value.items():
            _check_type(k, key_tp, f"{path}.<key>", partial, out)
            _check_type(v, val_tp, f"{path}.{k}", partial, out)
        return
    if tp is _NONE:
        if value is not None:
            out.append(f"{path}: expected None, got {value!r}")
        return
    if tp is bool:
        if not isinstance(value, bool):
            out.append(f"{path}: expected bool, got {value!r}")
        return
    if tp is int:
        if not isinstance(value, int) or isinstance(value, bool):
            out.append(f"{path}: expected int, got {value!r}")
        return
    if tp is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            out.append(f"{path}: expected a number, got {value!r}")
        return
    if tp in (str, dict, list):
        if not isinstance(value, tp):
            out.append(f"{path}: expected {tp.__name__}, got {type(value).__name__}")
        return
    out.append(f"{path}: unsupported annotation {tp!r}")


def _check_typed_dict(value, tp, path: str, partial: bool, out: list[str]) -> None:
    if not isinstance(value, dict):
        out.append(f"{path}: expected {tp.__name__} (a dict), got {type(value).__name__}")
        return
    fields = tp.__annotations__
    optional = getattr(tp, "__optional_keys__", frozenset())
    for name in value:
        if name not in fields:
            out.append(f"{path}: unknown field {name!r} for {tp.__name__}")
    for name, field_tp in fields.items():
        if name not in value:
            if not partial and name not in optional:
                out.append(f"{path}: missing field {name!r} of {tp.__name__}")
            continue
        _check_type(value[name], field_tp, f"{path}.{name}", partial, out)


def fields_of(tp) -> dict:
    """Field name -> annotation of a shape (used by the attribute map test)."""
    return dict(tp.__annotations__)


def _top_level(value, tp, path: str) -> list[str]:
    """Only the field names of a container shape; its pieces are checked by their own functions.

    A field that is present with None while its annotation does not admit None is a problem here (the piece
    functions are not called for None), with or without ``partial``.
    """
    if not isinstance(value, dict):
        return [f"{path}: expected {tp.__name__} (a dict), got {type(value).__name__}"]
    out = [f"{path}: unknown field {name!r} for {tp.__name__}" for name in value if name not in tp.__annotations__]
    for name, field_tp in tp.__annotations__.items():
        if name in value and value[name] is None and not allows_none(field_tp):
            out.append(f"{path}.{name}: None is not allowed for {tp.__name__}.{name}")
    return out


def allows_none(tp) -> bool:
    """True if the annotation admits None (``X | None``, ``Optional[X]``, ``None`` or ``Any``)."""
    if tp is Any or tp is _NONE or tp is None:
        return True
    if get_origin(tp) in (Union, types.UnionType):
        return any(allows_none(arm) for arm in get_args(tp))
    return False


# ---------------------------------------------------------------- positions and references


def _span_problems(obj: dict, raw: str | None, path: str) -> list[str]:
    span = obj.get("span")
    if span is None or raw is None:
        return []
    if not (isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) and not isinstance(x, bool)
                                                              for x in span)):
        return [f"{path}.span: expected [start, end], got {span!r}"]
    a, b = span
    if not 0 <= a < b <= len(raw):
        return [f"{path}.span: {span} outside the question (length {len(raw)})"]
    if "quote" in obj and obj["quote"] != raw[a:b]:
        return [f"{path}.quote: {obj['quote']!r} != question[{a}:{b}] {raw[a:b]!r}"]
    return []


def _unique(items: list, path: str) -> tuple[set, list[str]]:
    seen, out = set(), []
    for k, it in enumerate(items):
        if not isinstance(it, dict) or "id" not in it:
            continue
        if it["id"] in seen:
            out.append(f"{path}[{k}].id: duplicate {it['id']!r}")
        seen.add(it["id"])
    return seen, out


def _refs(ids, known: set | None, path: str) -> list[str]:
    if known is None or not isinstance(ids, list):
        return []
    return [f"{path}: unknown ID {i!r}" for i in ids if i not in known]


def _ref(i, known: set | None, path: str) -> list[str]:
    if known is None or i is None:
        return []
    return [] if i in known else [f"{path}: unknown ID {i!r}"]


# ---------------------------------------------------------------- interpretation pieces


def check_text(text: dict, question: str | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(text, shapes.TextView, "text", partial)
    if out:
        return out
    raw = text.get("raw")
    if question is not None and raw is not None and raw != question:
        out.append("text.raw: differs from the question")
    norm, offsets = text.get("norm"), text.get("offsets")
    if norm is not None and offsets is not None:
        if len(offsets) != len(norm):
            out.append(f"text.offsets: length {len(offsets)} != len(norm) {len(norm)}")
        if any(b < a for a, b in zip(offsets, offsets[1:])):
            out.append("text.offsets: not in order")
        if raw is not None and any(not 0 <= o < len(raw) for o in offsets):
            out.append("text.offsets: index outside the question")
    if norm is not None and text.get("nospace") is not None and text["nospace"] != "".join(norm.split()):
        out.append("text.nospace: not norm without whitespace")
    return out


def check_conditions(conditions: list, question: str | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(conditions, list[shapes.Condition], "conditions", partial)
    _, dup = _unique(conditions if isinstance(conditions, list) else [], "conditions")
    out += dup
    for k, c in enumerate(conditions if isinstance(conditions, list) else []):
        if not isinstance(c, dict):
            continue
        path = f"conditions[{k}]"
        out += _span_problems(c, question, path)
        kind = c.get("kind")
        attrs_tp = shapes.ATTRS_BY_KIND.get(kind)
        if "attrs" in c and attrs_tp is not None:
            out += _type_problems(c["attrs"], attrs_tp, f"{path}.attrs", partial)
            attrs = c["attrs"] if isinstance(c["attrs"], dict) else {}
            if kind in ("frequency", "basis", "unit", "calc") and "value" in c and "value" in attrs \
                    and c["value"] != attrs["value"]:
                out.append(f"{path}.value: {c['value']!r} != attrs.value {attrs['value']!r}")
            if kind in ("breakdown", "exclude") and "value" in c and "text" in attrs and c["value"] != attrs["text"]:
                out.append(f"{path}.value: {c['value']!r} != attrs.text {attrs['text']!r}")
            if kind == "period":
                out += _period_problems(attrs, f"{path}.attrs")
        prefix = shapes.ID_PREFIX.get(kind)
        if prefix and isinstance(c.get("id"), str) and not c["id"].startswith(prefix):
            out.append(f"{path}.id: {c['id']!r} should start with {prefix!r} for kind {kind}")
    return out


def _period_problems(attrs: dict, path: str) -> list[str]:
    out = []
    form = attrs.get("form")
    if form == "recent" and ("start_expr" in attrs and attrs["start_expr"] is not None):
        out.append(f"{path}.start_expr: recent periods keep amount/unit, not an end expression")
    if form in ("since", "point") and attrs.get("end_expr") is not None:
        out.append(f"{path}.end_expr: only a range has an end expression")
    if form == "since" and "open_end" in attrs and attrs["open_end"] is not True:
        out.append(f"{path}.open_end: a since period is open-ended")
    if form == "recent" and "amount" in attrs and attrs["amount"] is None:
        out.append(f"{path}.amount: a recent period has an amount")
    return out


def check_fragments(fragments: list, question: str | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(fragments, list[shapes.Fragment], "fragments", partial)
    _, dup = _unique(fragments if isinstance(fragments, list) else [], "fragments")
    out += dup
    for k, f in enumerate(fragments if isinstance(fragments, list) else []):
        if isinstance(f, dict):
            out += _span_problems(f, question, f"fragments[{k}]")
    return out


def check_terms(terms: list, fragment_ids: set | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(terms, list[shapes.Term], "terms", partial)
    for k, t in enumerate(terms if isinstance(terms, list) else []):
        if isinstance(t, dict):
            out += _ref(t.get("fragment_id"), fragment_ids, f"terms[{k}].fragment_id")
    return out


def check_judgement(judgement: dict, fragment_ids: set | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(judgement, shapes.Judgement, "judgement", partial)
    if not isinstance(judgement, dict):
        return out
    for k, p in enumerate(judgement.get("pairs") or []):
        if not isinstance(p, dict):
            continue
        out += _ref(p.get("left"), fragment_ids, f"judgement.pairs[{k}].left")
        out += _ref(p.get("right"), fragment_ids, f"judgement.pairs[{k}].right")
        if isinstance(p.get("row"), int) and not 1 <= p["row"] <= JUDGEMENT_ROWS:
            out.append(f"judgement.pairs[{k}].row: {p['row']} not in 1..{JUDGEMENT_ROWS}")
    draft_ids, dup = _unique(judgement.get("requirement_draft") or [], "judgement.requirement_draft")
    out += dup
    for k, d in enumerate(judgement.get("requirement_draft") or []):
        if isinstance(d, dict):
            out += _refs(d.get("fragment_ids"), fragment_ids, f"judgement.requirement_draft[{k}].fragment_ids")
            out += _ref(d.get("indicator_fragment_id"), fragment_ids,
                        f"judgement.requirement_draft[{k}].indicator_fragment_id")
            if isinstance(d.get("row"), int) and not 1 <= d["row"] <= JUDGEMENT_ROWS:
                out.append(f"judgement.requirement_draft[{k}].row: {d['row']} not in 1..{JUDGEMENT_ROWS}")
    drafts = draft_ids if isinstance(judgement.get("requirement_draft"), list) else None
    for k, group in enumerate(judgement.get("table_group_hint") or []):
        out += _refs(group, drafts, f"judgement.table_group_hint[{k}]")
    for cid, place in (judgement.get("attribution") or {}).items():
        if place not in ("common", "ambiguous") and drafts is not None and place not in drafts:
            out.append(f"judgement.attribution.{cid}: unknown place {place!r}")
    for k, h in enumerate(judgement.get("calc_hints") or []):
        if isinstance(h, dict):
            out += _ref(h.get("requirement_id"), drafts, f"judgement.calc_hints[{k}].requirement_id")
    if judgement.get("certain") is True and judgement.get("failed_conditions"):
        out.append("judgement.certain: true while failed_conditions is not empty")
    return out


def check_interpretation(interp: dict, question: str | None = None, condition_ids: set | None = None,
                         fragment_ids: set | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(interp, shapes.Interpretation, "interpretation", partial)
    if not isinstance(interp, dict):
        return out
    reqs = interp.get("requirements") or []
    req_ids, dup = _unique(reqs, "interpretation.requirements")
    out += dup
    out += _refs(interp.get("common_condition_ids"), condition_ids, "interpretation.common_condition_ids")
    out += _refs(interp.get("display_condition_ids"), condition_ids, "interpretation.display_condition_ids")
    for k, rel in enumerate(interp.get("relations") or []):
        if isinstance(rel, dict):
            out += _refs(rel.get("requirement_ids"), req_ids, f"interpretation.relations[{k}].requirement_ids")
            out += _ref(rel.get("condition_id"), condition_ids, f"interpretation.relations[{k}].condition_id")
    common = interp.get("common_condition_ids")
    for k, r in enumerate(reqs):
        if not isinstance(r, dict):
            continue
        path = f"interpretation.requirements[{k}]"
        out += _span_problems(r, question, path)
        for field in ("condition_ids", "shared_condition_ids", "ambiguous_condition_ids"):
            out += _refs(r.get(field), condition_ids, f"{path}.{field}")
        if isinstance(common, list) and isinstance(r.get("shared_condition_ids"), list) \
                and r["shared_condition_ids"] != common:
            out.append(f"{path}.shared_condition_ids: differs from interpretation.common_condition_ids")
        own, amb = r.get("condition_ids"), r.get("ambiguous_condition_ids")
        if isinstance(own, list) and isinstance(amb, list) and set(own) & set(amb):
            out.append(f"{path}: a condition is both own and ambiguous")
        ind = r.get("indicator")
        if isinstance(ind, dict):
            out += _span_problems(ind, question, f"{path}.indicator")
            out += _ref(ind.get("fragment_id"), fragment_ids, f"{path}.indicator.fragment_id")
        for t, target in enumerate(r.get("targets") or []):
            if isinstance(target, dict):
                out += _span_problems(target, question, f"{path}.targets[{t}]")
                out += _ref(target.get("fragment_id"), fragment_ids, f"{path}.targets[{t}].fragment_id")
    return out


def check_analysis(analysis: dict, question: str | None = None, partial: bool = False) -> list[str]:
    """The whole question-interpretation output. With question=None the raw text inside the analysis is used."""
    out = _top_level(analysis, shapes.Analysis, "analysis")  # pieces are checked below
    if not isinstance(analysis, dict):
        return out
    if not partial:
        optional = getattr(shapes.Analysis, "__optional_keys__", frozenset())
        out += [f"analysis: missing field {f!r}" for f in shapes.Analysis.__annotations__
                if f not in analysis and f not in optional]
    text = analysis.get("text")
    if text is not None:  # None is reported by _top_level (text is not nullable); a non-dict by check_text
        out += check_text(text, question, partial)
    raw = question if question is not None else (text.get("raw") if isinstance(text, dict) else None)
    conditions = analysis.get("conditions")
    fragments = analysis.get("fragments")
    cond_ids = {c.get("id") for c in conditions if isinstance(c, dict)} if isinstance(conditions, list) else None
    frag_ids = {f.get("id") for f in fragments if isinstance(f, dict)} if isinstance(fragments, list) else None
    if conditions is not None:
        out += check_conditions(conditions, raw, partial)
    if fragments is not None:
        out += check_fragments(fragments, raw, partial)
    if analysis.get("terms") is not None:
        out += check_terms(analysis["terms"], frag_ids, partial)
    if analysis.get("judgement") is not None:
        out += check_judgement(analysis["judgement"], frag_ids, partial)
    if analysis.get("interpretation") is not None:
        out += check_interpretation(analysis["interpretation"], raw, cond_ids, frag_ids, partial)
    out += check_segment_fields(analysis, raw, cond_ids, frag_ids, partial)
    out += check_step4_fields(analysis, raw, partial)
    return out


# ---------------------------------------------------------------- plan 15 r3: segments, mentions, ref_map


def check_segments(segments: list, raw: str | None, partial: bool = False) -> list[str]:
    """Shape, unique IDs, and exact coverage: the segments follow each other without gap or overlap from 0 to the end
    of the question, each quote is the question there, and joined in order they are the question."""
    out = _type_problems(segments, list[shapes.Segment], "segments", partial)
    if not isinstance(segments, list):
        return out
    out += _unique_by(segments, "segment_id", "segments")
    rows = [s for s in segments if isinstance(s, dict)]
    pos = 0
    for k, s in enumerate(rows):
        span = s.get("span")
        if not (isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) and not isinstance(x, bool)
                                                                  for x in span)):
            continue  # reported by the type check
        a, b = span
        if a != pos:
            out.append(f"segments[{k}].span: starts at {a}, the previous segment ends at {pos}")
        if b <= a:
            out.append(f"segments[{k}].span: empty or reversed {span}")
        if raw is not None and isinstance(s.get("quote"), str) and s["quote"] != raw[a:b]:
            out.append(f"segments[{k}].quote: {s['quote']!r} != question[{a}:{b}] {raw[a:b]!r}")
        pos = max(pos, b)
    if raw is not None:
        if pos != len(raw):
            out.append(f"segments: cover the question up to {pos} of {len(raw)}")
        joined = "".join(s["quote"] for s in rows if isinstance(s.get("quote"), str))
        if joined != raw:
            out.append("segments: the quotes joined in order are not the question")
    return out


def check_mentions(mentions: list, segment_ids: list | None, condition_ids: set | None,
                   partial: bool = False) -> list[str]:
    """Shape, unique IDs, known and contiguous segments (in text order), known conditions and overlaps."""
    out = _type_problems(mentions, list[shapes.Mention], "mentions", partial)
    if not isinstance(mentions, list):
        return out
    out += _unique_by(mentions, "mention_id", "mentions")
    mention_ids = {m.get("mention_id") for m in mentions if isinstance(m, dict)}
    order = {sid: n for n, sid in enumerate(segment_ids)} if segment_ids is not None else None
    for k, m in enumerate(mentions):
        if not isinstance(m, dict):
            continue
        path = f"mentions[{k}]"
        sids = m.get("segment_ids")
        if isinstance(sids, list):
            if not sids:
                out.append(f"{path}.segment_ids: empty")
            if order is not None:
                unknown = [s for s in sids if s not in order]
                out += [f"{path}.segment_ids: unknown ID {s!r}" for s in unknown]
                if not unknown and sids:
                    pos = [order[s] for s in sids]
                    if pos != list(range(pos[0], pos[0] + len(pos))):
                        out.append(f"{path}.segment_ids: not contiguous in text order")
        out += _refs(m.get("condition_ids"), condition_ids, f"{path}.condition_ids")
        overlaps = m.get("overlaps")
        out += _refs(overlaps, mention_ids, f"{path}.overlaps")
        if isinstance(overlaps, list) and m.get("mention_id") in overlaps:
            out.append(f"{path}.overlaps: holds the mention itself")
    return out


def _span_rows(segments) -> list[dict]:
    """The segments whose span can be read (a two-item list; its type is reported by the segment check)."""
    return [s for s in segments or [] if isinstance(s, dict) and isinstance(s.get("span"), list)
            and len(s["span"]) == 2]


def _mention_span_error(m: dict, rows: list[dict], raw: str | None) -> str | None:
    """Why a written ``Mention.span`` is invalid (None: valid): not [start, end] of two integers, outside the question
    or reversed, or overlapping other segments than the mention's ``segment_ids``. Only a valid span is ever handed to
    the range rule (``_quoted_candidates``)."""
    sp = m["span"]
    if not (isinstance(sp, list) and len(sp) == 2 and all(isinstance(x, int) and not isinstance(x, bool)
                                                          for x in sp)):
        return f"expected [start, end], got {sp!r}"
    if not 0 <= sp[0] < sp[1] or (raw is not None and sp[1] > len(raw)):
        return f"{sp} outside the question"
    over = [s.get("segment_id") for s in rows if sp[0] < s["span"][1] and s["span"][0] < sp[1]]
    if isinstance(m.get("segment_ids"), list) and over != m["segment_ids"]:
        return f"overlaps the segments {over}, not its segment_ids"
    return None


def _mention_span_problems(mentions: list, segments: list, raw: str | None) -> list[str]:
    """The optional ``Mention.span`` (plan 19, 2026-10-21; absent in older runs: nothing to check): [start, end)
    inside the question, and the segments it overlaps are exactly the mention's ``segment_ids``."""
    out: list[str] = []
    rows = _span_rows(segments)
    for k, m in enumerate(mentions):
        if not isinstance(m, dict) or "span" not in m:
            continue
        err = _mention_span_error(m, rows, raw)
        if err is not None:
            out.append(f"mentions[{k}].span: {err}")
    return out


def check_ref_map(ref_map: dict, segment_ids: set | None, mention_ids: set | None, fragment_ids: set | None,
                  partial: bool = False) -> list[str]:
    """Shape and references: every key and value names a known segment, mention or fragment."""
    out = _type_problems(ref_map, shapes.RefMap, "ref_map", partial)
    if not isinstance(ref_map, dict):
        return out
    known = {"segment": segment_ids, "mention": mention_ids, "fragment": fragment_ids}
    for field, (key_kind, value_kind) in {"segment_mentions": ("segment", "mention"),
                                          "segment_fragments": ("segment", "fragment"),
                                          "fragment_segments": ("fragment", "segment"),
                                          "mention_fragments": ("mention", "fragment")}.items():
        rows = ref_map.get(field)
        if not isinstance(rows, dict):
            continue
        out += _refs(list(rows), known[key_kind], f"ref_map.{field}.<key>")
        for k, ids in rows.items():
            out += _refs(ids, known[value_kind], f"ref_map.{field}.{k}")
    return out


def check_segment_fields(analysis: dict, raw: str | None, condition_ids: set | None = None,
                         fragment_ids: set | None = None, partial: bool = False) -> list[str]:
    """The optional plan 15 r3 fields of an analysis (absent: nothing to check). Mentions and the ref map need the
    segments they point at."""
    out: list[str] = []
    segments, mentions, ref_map = analysis.get("segments"), analysis.get("mentions"), analysis.get("ref_map")
    seg_list = None
    if segments is not None:
        out += check_segments(segments, raw, partial)
        if isinstance(segments, list):
            seg_list = [s.get("segment_id") for s in segments if isinstance(s, dict)]
    elif mentions is not None or ref_map is not None:
        out.append("analysis.segments: missing while mentions or ref_map are present")
    if mentions is not None:
        out += check_mentions(mentions, seg_list, condition_ids, partial)
        if isinstance(segments, list):
            out += _mention_span_problems(mentions, segments, raw)
    if ref_map is not None:
        mention_ids = ({m.get("mention_id") for m in mentions if isinstance(m, dict)}
                       if isinstance(mentions, list) else None)
        out += check_ref_map(ref_map, set(seg_list) if seg_list is not None else None, mention_ids, fragment_ids,
                             partial)
    return out


# ---------------------------------------------------------------- plan 15 r3 step 4: condition fields, refs, record


def _ids(rows, field: str = "id") -> set | None:
    return {r.get(field) for r in rows if isinstance(r, dict)} if isinstance(rows, list) else None


def check_condition_fields(rows, conditions: list, path: str = "decomposition.condition_fields") -> list[str]:
    """7.4 and appendix A 1: condition ID -> ``ConditionFields``, one row per condition of the analysis; ``applied``
    None in A; ``period_diagnosis`` exactly on the period conditions; a period confirmed by the interpretation is one
    the resolver resolves (diagnosis ok)."""
    out = _type_problems(rows, dict[str, shapes.ConditionFields], path, False)
    if not isinstance(rows, dict):
        return out
    by_id = {c.get("id"): c for c in conditions if isinstance(c, dict)}
    out += _refs(list(rows), set(by_id), f"{path}.<key>")
    out += [f"{path}: no row for condition {cid!r}" for cid in by_id if cid not in rows]
    for cid, row in rows.items():
        if not isinstance(row, dict) or cid not in by_id:
            continue
        if row.get("applied") is not None:
            out.append(f"{path}.{cid}.applied: None in the interpretation (B fills it)")
        if ("period_diagnosis" in row) != (by_id[cid].get("kind") == "period"):
            out.append(f"{path}.{cid}.period_diagnosis: present exactly on a period condition")
        if row.get("confirmed") is True and row.get("source") == "question" and by_id[cid].get("kind") == "period" \
                and row.get("period_diagnosis") != "ok":
            out.append(f"{path}.{cid}.confirmed: a period the resolver does not resolve is not confirmed")
    return out


def _counted(req: dict) -> tuple[set, set]:
    amb = set(req.get("ambiguous_condition_ids") or [])
    return (set(req.get("condition_ids") or []) | set(req.get("shared_condition_ids") or [])) - amb, amb


def check_inferred(inferred, interp: dict | None, conditions: list, path: str = "decomposition.inferred") -> list[str]:
    """7.5: requirement ID -> hints, one entry per requirement; a hint comes from a period condition the requirement
    counts, and only when the requirement has no frequency condition (own, shared or ambiguous)."""
    out = _type_problems(inferred, dict[str, list[shapes.InferredHint]], path, False)
    if not isinstance(inferred, dict):
        return out
    reqs = {r.get("id"): r for r in (interp or {}).get("requirements") or [] if isinstance(r, dict)}
    if set(inferred) != set(reqs):
        out.append(f"{path}: not one entry per requirement of the interpretation")
    by_id = {c.get("id"): c for c in conditions if isinstance(c, dict)}
    for rid, hints in inferred.items():
        if rid not in reqs or not isinstance(hints, list) or not hints:
            continue
        counted, amb = _counted(reqs[rid])
        if any(by_id.get(c, {}).get("kind") == "frequency" for c in counted | amb):
            out.append(f"{path}.{rid}: a requirement with a frequency condition gets no inferred hint")
        if len(hints) > 1:
            out.append(f"{path}.{rid}: at most one frequency hint")
        for j, h in enumerate(hints):
            cid = h.get("from_condition_id") if isinstance(h, dict) else None
            if by_id.get(cid, {}).get("kind") != "period" or cid not in counted:
                out.append(f"{path}.{rid}[{j}].from_condition_id: {cid!r} is no period condition of the requirement")
    return out


def check_requirement_refs(interp: dict, segment_ids: set | None, mention_ids: set | None,
                           path: str = "interpretation") -> list[str]:
    """Step 4 requirement fields of a model structure: ``refs`` name known segments and mentions; ``reading_id``."""
    out: list[str] = []
    for k, r in enumerate((interp or {}).get("requirements") or []):
        if not isinstance(r, dict):
            continue
        p = f"{path}.requirements[{k}]"
        refs = r.get("refs")
        if isinstance(refs, dict):
            out += _refs(refs.get("segment_ids"), segment_ids, f"{p}.refs.segment_ids")
            out += _refs(refs.get("mention_ids"), mention_ids, f"{p}.refs.mention_ids")
        if "reading_id" in r and not (isinstance(r["reading_id"], str) and r["reading_id"]):
            out.append(f"{p}.reading_id: empty")
    return out


def check_failed_sources(judgement: dict, requirement_ids: set | None, condition_ids: set | None,
                         fragment_ids: set | None, path: str = "judgement") -> list[str]:
    """Plan 15 r3 7.2 (select/needs.py reads them by scope): every failed source names known requirements,
    conditions and fragments, and its condition is one of ``failed_conditions``."""
    out: list[str] = []
    if not isinstance(judgement, dict):
        return out
    failed = judgement.get("failed_conditions")
    for k, row in enumerate(judgement.get("failed_sources") or []):
        if not isinstance(row, dict):
            continue
        p = f"{path}.failed_sources[{k}]"
        out += _refs(row.get("requirement_ids"), requirement_ids, f"{p}.requirement_ids")
        out += _refs(row.get("condition_ids"), condition_ids, f"{p}.condition_ids")
        out += _refs(row.get("fragment_ids"), fragment_ids, f"{p}.fragment_ids")
        if isinstance(failed, list) and row.get("condition") not in failed:
            out.append(f"{p}.condition: {row.get('condition')!r} is not among failed_conditions")
    return out


def check_step4_fields(analysis: dict, raw: str | None, partial: bool = False) -> list[str]:
    """The optional step 4 fields of an analysis (absent: nothing to check): requirement refs, the scopes of the
    failed sources, and the ``decomposition`` record."""
    conditions = analysis.get("conditions")
    interp = analysis.get("interpretation")
    out: list[str] = []
    if isinstance(interp, dict):
        out += check_requirement_refs(interp, _ids(analysis.get("segments"), "segment_id"),
                                      _ids(analysis.get("mentions"), "mention_id"))
        out += check_failed_sources(analysis.get("judgement"), _ids(interp.get("requirements")), _ids(conditions),
                                    _ids(analysis.get("fragments")))
    record = analysis.get("decomposition")
    if record is not None:
        out += check_decomposition(record, analysis, raw)
    return out


def _check_seeds(seeds, readings: dict[str, set], seg_ids, men_ids, cond_ids, path: str) -> list[str]:
    out: list[str] = []
    seen: set = set()
    for k, s in enumerate(seeds if isinstance(seeds, list) else []):
        if not isinstance(s, dict):
            continue
        p = f"{path}[{k}]"
        if s.get("seed_id") in seen:
            out.append(f"{p}.seed_id: duplicate {s.get('seed_id')!r}")
        seen.add(s.get("seed_id"))
        if s.get("reading_id") not in readings:
            out.append(f"{p}.reading_id: unknown reading {s.get('reading_id')!r}")
        for j, key in enumerate(s.get("requirement_keys") or []):
            if not isinstance(key, dict):
                continue
            known = readings.get(key.get("reading_id"))
            if known is None or key.get("requirement_id") not in known:
                out.append(f"{p}.requirement_keys[{j}]: unknown requirement {key.get('reading_id')!r}/"
                           f"{key.get('requirement_id')!r}")
        refs = s.get("refs") if isinstance(s.get("refs"), dict) else {}
        out += _refs(refs.get("segment_ids"), seg_ids, f"{p}.refs.segment_ids")
        out += _refs(refs.get("mention_ids"), men_ids, f"{p}.refs.mention_ids")
        out += _refs(refs.get("condition_ids"), cond_ids, f"{p}.refs.condition_ids")
    return out


def _quoted_candidates(ref: dict, analysis: dict, segs: dict) -> list[dict]:
    """The raw candidate rows a ``SelectionRef`` may carry: those of the name mentions it quotes, by the generator's
    range rule (``interpret.decompose_refs``, the same pure functions; plan 19 decision of 2026-10-21):

    - a whole ref (its span is its segments' span): the name mentions over its segments, never an inner mention (one
      strictly inside one segment);
    - a partial quote (any other span): only the name mentions over its one segment written exactly over its span,
      the span being the quote's core (the spaces around it left out, as ``RefReader.sub_range`` reads a subquote).

    A mention without ``span`` (an analysis of an older run) is read as before: any name mention over the ref's
    segments. A mention whose written ``span`` is invalid (``_mention_span_error``; reported by
    ``_mention_span_problems``) is quoted by no ref: its span never reaches the range rule."""
    from ..interpret.decompose_refs import core_range, subquote_quotes, whole_ref_quotes

    ids, span = ref["segment_ids"], ref["span"]
    known = bool(ids) and all(s in segs for s in ids)
    seg_spans = {sid: s["span"] for sid, s in segs.items()}
    whole = known and list(span) == [segs[ids[0]]["span"][0], segs[ids[-1]]["span"][1]]
    rng = core_range(span[0], ref["quote"]) if known and not whole else None
    exact = rng is not None and len(ids) == 1 and (rng[0], rng[1]) == (span[0], span[1])
    rows = _span_rows(analysis.get("segments"))
    raw = (analysis.get("text") or {}).get("raw")
    out = []
    for m in analysis.get("mentions") or []:
        if m["kind"] != "name":
            continue
        if "span" in m and _mention_span_error(m, rows, raw) is not None:
            continue  # an invalid span: a violation of its own, never read as a range nor as an older run
        own = m.get("span")
        if own is None or not known:
            ok = set(m["segment_ids"]) <= set(ids)  # no recorded range: the segment-level rule of older runs
        elif whole:
            ok = whole_ref_quotes(m["segment_ids"], own, ids, seg_spans)
        else:
            ok = exact and bool(subquote_quotes(m["segment_ids"], own, ids[0], rng))
        if ok:
            out += m["candidates"]
    return out


def _ref_problems(ref: dict, analysis: dict, p: str, within: set | None = None) -> list[str]:
    """One ``SelectionRef`` (a selection ref or a ref of the kept structure) against the question: the span inside the
    question and its quote the question there, known segments holding the span (and, with ``within``, inside those
    segments), candidates that are raw candidates of the name mentions it quotes (``_quoted_candidates``: the exact
    range of a partial quote, never an inner mention for a whole ref), a name exactly with candidates and the
    candidates' catalog name (``same_catalog_name``: the spelling variants one sense merges carry its one name)."""
    raw = analysis["text"]["raw"]
    segs = {s["segment_id"]: s for s in analysis.get("segments") or []}
    span = ref["span"]
    if len(span) != 2 or not 0 <= span[0] < span[1] <= len(raw) or raw[span[0]:span[1]] != ref["quote"]:
        return [f"{p}.span: quote or bounds differ"]
    out = []
    ids = ref["segment_ids"]
    if not ids or any(s not in segs for s in ids):
        out.append(f"{p}.segment_ids: unknown or empty")
    elif span[0] < segs[ids[0]]["span"][0] or span[1] > segs[ids[-1]]["span"][1]:
        out.append(f"{p}.span: outside reference")
    if within is not None and not set(ids) <= within:
        out.append(f"{p}.segment_ids: outside requirement references")
    candidates = _quoted_candidates(ref, analysis, segs)
    if any(c not in candidates for c in ref["candidates"]):
        out.append(f"{p}.candidates: not a raw mention candidate of this reference")
    if ref["name"] is not None and not ref["candidates"]:
        out.append(f"{p}.candidates: a named reference requires its raw candidates")
    if any(not same_catalog_name(c["value_name"], ref["name"]) for c in ref["candidates"]):
        out.append(f"{p}.name: differs from candidates")
    return out


def same_catalog_name(a: str | None, b: str | None) -> bool:
    """A candidate's catalog name and a ref's name are the same name: equal, or one sense of the sense table by its
    approved spelling equivalence (``decompose_payload.spelling_key``: spacing and a footnote mark written apart; the
    one definition, never a copy). Brackets and their contents stay apart; None only equals None."""
    from ..interpret.decompose_payload import spelling_key

    if a is None or b is None:
        return a is b
    return a == b or spelling_key(a) == spelling_key(b)


def _selection_refs_problems(record: dict, analysis: dict, interp: dict, path: str) -> list[str]:
    rows = record.get("selection_refs")
    if rows is None:
        return [f"{path}.selection_refs: required for an adopted model reading"]
    out = []
    reqs = {r["id"]: r for r in interp.get("requirements") or []}
    if set(rows) != set(reqs):
        out.append(f"{path}.selection_refs: requirement keys differ")
    for rid, refs in rows.items():
        required_ids = set((reqs.get(rid, {}).get("refs") or {}).get("segment_ids") or [])
        if {sid for ref in refs for sid in ref["segment_ids"]} != required_ids:
            out.append(f"{path}.selection_refs.{rid}: missing or extra requirement references")
        if reqs.get(rid, {}).get("indicator") is not None and not any(r["place"] == "indicator" for r in refs):
            out.append(f"{path}.selection_refs.{rid}: missing indicator reference")
        within = set((reqs[rid].get("refs") or {}).get("segment_ids") or []) if rid in reqs else None
        for i, ref in enumerate(refs):
            out += _ref_problems(ref, analysis, f"{path}.selection_refs.{rid}[{i}]", within)
    return out


# ---------------------------------------------------------------- plan 19 contract joint: the kept structure

STRUCTURE_STATES = ("absent", "null", "unsupported_version", "invalid", "valid")
_STRUCTURE_PLACES = {"targets": "target", "basis": "basis", "context": "context", "axis": "breakdown_axis",
                     "values": "breakdown_value", "markers": "breakdown_marker"}
_POLARITY_PLACES = {"markers": "polarity_marker", "negative": "polarity_negative", "positive": "polarity_positive"}


def _without_structure(record):
    """The record as the older shape checks it: the kept structure is checked apart (``check_structure``), so that an
    unsupported version is told apart from a violation."""
    if not isinstance(record, dict):
        return record
    out = {k: v for k, v in record.items() if k != "structure"}
    if isinstance(out.get("alternative"), dict):
        out["alternative"] = {k: v for k, v in out["alternative"].items() if k != "structure"}
    return out


def _positive_refs(rs: dict):
    """(field path, place, ref) of a requirement's structure, in the order of its ``selection_refs`` (indicator left
    out): targets, basis, context, then per group its axis, values and markers."""
    for j, ref in enumerate(rs["targets"]):
        yield f"targets[{j}]", _STRUCTURE_PLACES["targets"], ref
    for j, ref in enumerate(rs["basis"]):
        yield f"basis[{j}]", _STRUCTURE_PLACES["basis"], ref
    if rs["context"] is not None:
        yield "context", _STRUCTURE_PLACES["context"], rs["context"]
    for k, g in enumerate(rs["breakdowns"]):
        if g["axis"] is not None:
            yield f"breakdowns[{k}].axis", _STRUCTURE_PLACES["axis"], g["axis"]
        for j, ref in enumerate(g["values"]):
            yield f"breakdowns[{k}].values[{j}]", _STRUCTURE_PLACES["values"], ref
        for j, ref in enumerate(g["markers"]):
            yield f"breakdowns[{k}].markers[{j}]", _STRUCTURE_PLACES["markers"], ref


def _exclusion_scope(link_markers: list[dict], holder: dict, analysis: dict, known: list[str]) -> tuple[bool, set]:
    """(bound, requirements) of an exclusion also written as a condition: the bindings of the exclude conditions whose
    condition mention holds a marker of the evidence (settled IDs; an unresolved or name-content binding: None)."""
    from ..interpret.decompose_payload import condition_segments  # the one definition of a condition's segments

    conditions = [c for c in analysis.get("conditions") or [] if isinstance(c, dict)]
    segs = condition_segments(conditions, analysis.get("mentions") or [])
    msegs = {s for ref in link_markers for s in ref["segment_ids"]}
    cids = {c["id"] for c in conditions if c.get("kind") == "exclude" and set(segs.get(c["id"], [])) & msegs}
    settled_of = {p: r for r, p in (holder.get("requirement_ids") or {}).items()}
    held: set | None = set()
    found = False
    for b in holder.get("bindings") or []:
        if b.get("condition_id") not in cids:
            continue
        found = True
        if b.get("scope") == "question":
            held |= set(known)
        elif b.get("scope") == "requirements":
            held |= {settled_of[x] for x in b.get("requirement_ids") or [] if x in settled_of}
        else:
            return True, None
    return found, held


def _structure_problems(s: dict, holder: dict, analysis: dict, interp: dict, path: str) -> list[str]:
    """Contract rules of a type-valid kept structure against its own reading (module docstring of the section)."""
    out: list[str] = []
    req_ids = [r.get("id") for r in interp.get("requirements") or [] if isinstance(r, dict)]
    known = set(req_ids)
    if set(s["requirements"]) != known:
        out.append(f"{path}.requirements: keys differ from the reading's requirement IDs")
    sel = holder.get("selection_refs") if isinstance(holder.get("selection_refs"), dict) else {}
    for rid, rs in s["requirements"].items():
        p = f"{path}.requirements.{rid}"
        flat = list(_positive_refs(rs))
        for fpath, place, ref in flat:
            if ref["place"] != place:
                out.append(f"{p}.{fpath}.place: {ref['place']!r} is not {place!r}")
            out += _ref_problems(ref, analysis, f"{p}.{fpath}")
        expected = [x for x in sel.get(rid) or [] if isinstance(x, dict) and x.get("place") != "indicator"]
        if rid in sel and [ref for _, _, ref in flat] != expected:
            out.append(f"{p}: positive refs differ from selection_refs.{rid} (place, range, whole or partial quote, "
                       "candidate rows or name)")
        groups: set = set()
        for k, g in enumerate(rs["breakdowns"]):
            gp = f"{p}.breakdowns[{k}]"
            if g["group_id"] in groups:
                out.append(f"{gp}.group_id: duplicate {g['group_id']!r}")
            groups.add(g["group_id"])
            if (g["kind"] == "descendants") != (g["descendants"] is not None):
                out.append(f"{gp}.descendants: present exactly for the kind 'descendants'")
            elif g["descendants"] is not None:
                want = ([g["axis"]] if g["axis"] is not None else []) + list(g["values"])
                if g["descendants"]["source_refs"] != want:
                    out.append(f"{gp}.descendants.source_refs: not the group's axis and value refs")
    links: dict[str, dict] = {}
    shared = [x for refs in sel.values() if isinstance(refs, list) for x in refs if isinstance(x, dict)]
    for k, link in enumerate(s["polarity"]):
        lp = f"{path}.polarity[{k}]"
        if link["link_id"] in links:
            out.append(f"{lp}.link_id: duplicate {link['link_id']!r}")
        links[link["link_id"]] = link
        rids = link["requirement_ids"]
        if len(set(rids)) != len(rids):
            out.append(f"{lp}.requirement_ids: a requirement named twice")
        out += _refs(rids, known, f"{lp}.requirement_ids")
        if not link["markers"]:
            out.append(f"{lp}.markers: a polarity link names its markers")
        for field, place in _POLARITY_PLACES.items():
            for j, ref in enumerate(link[field]):
                if ref["place"] != place:
                    out.append(f"{lp}.{field}[{j}].place: {ref['place']!r} is not {place!r}")
                out += _ref_problems(ref, analysis, f"{lp}.{field}[{j}]")
        for j, ref in enumerate(link["positive"]):  # a positive ref read like a requirement's ref of the same range
            same = [x for x in shared if x.get("span") == ref["span"] and x.get("segment_ids") == ref["segment_ids"]
                    and x.get("name") == ref["name"]]
            if same and not any(x.get("candidates") == ref["candidates"] for x in same):
                out.append(f"{lp}.positive[{j}]: candidate rows differ from the selection ref of that range and name")
    if s["confirmed_effects"]:  # held empty in this version (user decision of 2026-10-21)
        out.append(f"{path}.confirmed_effects: held empty in {s['version']!r} (no confirmed effect is made this "
                   f"round), got {len(s['confirmed_effects'])}")
    effects: set = set()  # the effect rules below still read a list written anyway (each finding reported)
    for k, e in enumerate(s["confirmed_effects"]):
        ep = f"{path}.confirmed_effects[{k}]"
        link = links.get(e["link_id"])
        if link is None:
            out.append(f"{ep}.link_id: unknown link {e['link_id']!r}")
            continue
        i = e["negative_ref_index"]
        if not 0 <= i < len(link["negative"]):
            out.append(f"{ep}.negative_ref_index: {i} is no negative ref of {e['link_id']!r}")
            continue
        if (e["link_id"], i) in effects:
            out.append(f"{ep}: duplicate effect of {e['link_id']!r}[{i}]")
        effects.add((e["link_id"], i))
        rids = e["requirement_ids"]
        if not rids or len(set(rids)) != len(rids):
            out.append(f"{ep}.requirement_ids: empty or a requirement named twice")
        out += _refs(rids, known, f"{ep}.requirement_ids")
        scope = set(link["requirement_ids"]) or known
        if not set(rids) <= scope:
            out.append(f"{ep}.requirement_ids: outside the link's scope")
        negative = link["negative"][i]
        if negative not in e["evidence_refs"]:
            out.append(f"{ep}.evidence_refs: the negative ref is not among them")
        if any(x not in link["markers"] and x != negative for x in e["evidence_refs"]):
            out.append(f"{ep}.evidence_refs: not the link's markers and that negative ref")
        markers = [x for x in e["evidence_refs"] if x in link["markers"]]
        if not markers:
            out.append(f"{ep}.evidence_refs: no marker of the link")
        bound, held = _exclusion_scope(markers, holder, analysis, req_ids)
        if bound and held is None:
            out.append(f"{ep}: an exclusion bound unresolved or as name content confirms no effect")
        elif bound and not set(rids) <= held:
            out.append(f"{ep}.requirement_ids: outside the exclusion condition's binding (condition_bindings)")
    return out


def check_structure(holder: dict, analysis: dict, interp: dict, path: str = "decomposition",
                    adopted: bool = True) -> dict:
    """``{state, problems}`` of the kept structure of one reading record (the decomposition record, or its kept
    alternative checked with its own requirement IDs, selection refs and bindings). ``state``:

    - ``absent``: no ``structure`` field (a record of an older run): no structure, not a violation;
    - ``null``: no structure; the rules adopted or a fallback name ``structure_reason`` (an adopted model reading
      may not be null);
    - ``unsupported_version``: a version outside ``shapes.STRUCTURE_VERSIONS`` (not read further: a reader skips it);
    - ``invalid``: a contract violation (shape, ID spaces, refs against ``selection_refs`` and the question, group
      IDs, polarity scope IDs, confirmed effects: any effect at all -- held empty in this version (user decision
      of 2026-10-21) --, and for a list written anyway its index, scope, evidence, exclusion binding);
    - ``valid``.
    ``problems`` is empty exactly for ``absent`` with no stray reason, ``null`` as allowed, and ``valid``."""
    if not isinstance(holder, dict) or "structure" not in holder:
        stray = isinstance(holder, dict) and holder.get("structure_reason") is not None
        return {"state": "absent", "problems": [f"{path}.structure_reason: set without a structure"] if stray else []}
    s, reason = holder["structure"], holder.get("structure_reason")
    if s is None:
        if adopted:
            return {"state": "null", "problems": [f"{path}.structure: an adopted model reading keeps its structure"]}
        if reason is None:
            return {"state": "null", "problems": [f"{path}.structure_reason: a null structure names its reason"]}
        return {"state": "null", "problems": []}
    out: list[str] = []
    if reason is not None:
        out.append(f"{path}.structure_reason: only beside a null structure")
    if not adopted:
        out.append(f"{path}.structure: the rules adopted keep no model structure")
    version = s.get("version") if isinstance(s, dict) else None
    if isinstance(version, str) and version not in shapes.STRUCTURE_VERSIONS:
        supported = list(shapes.STRUCTURE_VERSIONS)
        return {"state": "unsupported_version",
                "problems": out + [f"{path}.structure.version: unsupported version {version!r} (supported: {supported})"]}
    types = _type_problems(s, shapes.DecompositionStructure, f"{path}.structure", False)
    if types:
        return {"state": "invalid", "problems": out + types}
    out += _structure_problems(s, holder, analysis, interp or {}, f"{path}.structure")
    return {"state": "invalid" if out else "valid", "problems": out}


def structure_status(analysis: dict, alternative: bool = False) -> dict:
    """``check_structure`` of an analysis' adopted reading (or of its kept alternative): what a reader of
    ``decomposition.structure`` checks first (``absent`` / ``null``: no structure; ``unsupported_version``: do not
    read; ``invalid``: contract violation; ``valid``)."""
    record = analysis.get("decomposition") if isinstance(analysis, dict) else None
    if not isinstance(record, dict):
        return {"state": "absent", "problems": []}
    if alternative:
        alt = record.get("alternative")
        if not isinstance(alt, dict):
            return {"state": "absent", "problems": []}
        return check_structure(alt, analysis, alt.get("interpretation") or {}, "decomposition.alternative", True)
    return check_structure(record, analysis, analysis.get("interpretation") or {}, "decomposition",
                           record.get("applied_source") == "model")


def check_decomposition(record: dict, analysis: dict, raw: str | None = None) -> list[str]:
    """Step 4: the ``decomposition`` record. Shape; the adopted source fits the requested mode (rules: rules, no
    execution mode; gpt: model or rules_fallback); a fallback names its reason (an error class only with transport)
    and keeps no structure; the condition fields and hints of the adopted structure; an adopted model structure has
    an accepted verification, its reading on every requirement, the settled -> proposal ID map over the
    interpretation's requirements and a certainty basis that fits ``judgement.certain``; bindings, seeds and the
    alternative name known IDs."""
    path = "decomposition"
    out = _type_problems(_without_structure(record), shapes.DecompositionRecord, path, False)
    if not isinstance(record, dict) or out:
        return out
    conditions = analysis.get("conditions") if isinstance(analysis.get("conditions"), list) else []
    cond_ids = _ids(conditions)
    seg_ids = _ids(analysis.get("segments"), "segment_id")
    men_ids = _ids(analysis.get("mentions"), "mention_id")
    interp = analysis.get("interpretation") or {}
    judgement = analysis.get("judgement") or {}
    req_ids = _ids(interp.get("requirements"))
    mode, applied, reason = record["requested_mode"], record["applied_source"], record["fallback_reason"]
    if mode == "rules" and (applied != "rules" or record["execution_mode"] is not None):
        out.append(f"{path}: the rules mode adopts the rules and has no execution mode")
    if mode == "gpt" and applied not in ("model", "rules_fallback"):
        out.append(f"{path}.applied_source: the gpt mode adopts the model structure or falls back")
    if mode == "gpt" and record["execution_mode"] is None:
        out.append(f"{path}.execution_mode: the gpt mode names its execution mode")
    if (applied == "rules_fallback") != (reason is not None):
        out.append(f"{path}.fallback_reason: set exactly when the rules are adopted after a fallback")
    if record["error_class"] is not None and reason != "transport":
        out.append(f"{path}.error_class: only for a transport failure")
    if reason in ("format", "limit", "semantic") and (record["verify"] is None
                                                    or record["verify"]["outcome"] != "fallback"):
        out.append(f"{path}.verify: a {reason} fallback keeps the verification that sent it back")
    out += _rule_diagnostics_problems(record["rule_diagnostics"], path)
    out += check_condition_fields(record["condition_fields"], conditions, f"{path}.condition_fields")
    out += check_inferred(record["inferred"], interp, conditions, f"{path}.inferred")
    if applied != "model":
        out += check_structure(record, analysis, interp, path, adopted=False)["problems"]
        if record.get("selection_refs"):
            out.append(f"{path}.selection_refs: rules adopted keep no model references")
        if record["bindings"] or record["assumption_seeds"] or record["requirement_ids"] or record["reading_id"] \
                or record["certainty"] is not None or record["alternative"] is not None or record["rule_checks"]:
            out.append(f"{path}: the rules adopted keep no model structure (bindings, seeds, IDs, certainty, "
                       "alternative, rule checks)")
        if record["verify"] is not None:
            out += check_verify_result(record["verify"], seg_ids, cond_ids)
        return out
    verify = record["verify"]
    if verify is None or verify["outcome"] != "accept":
        out.append(f"{path}.verify: an adopted model structure was verified and accepted")
    reading = record["reading_id"]
    out += _selection_refs_problems(record, analysis, interp, path)
    out += check_structure(record, analysis, interp, path, adopted=True)["problems"]
    if not reading:
        out.append(f"{path}.reading_id: an adopted model structure names its reading")
    if set(record["requirement_ids"]) != (req_ids or set()):
        out.append(f"{path}.requirement_ids: keys differ from the interpretation's requirement IDs")
    for k, r in enumerate(interp.get("requirements") or []):
        if isinstance(r, dict) and r.get("reading_id") != reading:
            out.append(f"interpretation.requirements[{k}].reading_id: differs from the adopted reading {reading!r}")
    if verify is not None:
        out += check_verify_result(verify, seg_ids, cond_ids, set(record["requirement_ids"].values()))
    out += _bindings_problems(record["bindings"], cond_ids, set(record["requirement_ids"].values()),
                              f"{path}.bindings")
    readings = {reading: req_ids or set()}
    alt = record["alternative"]
    if alt is not None:
        apath = f"{path}.alternative"
        alt_interp, alt_frags = alt["interpretation"], alt["fragments"]
        alt_frag_ids = _ids(alt_frags)
        alt_req_ids = _ids(alt_interp.get("requirements"))
        if alt["reading_id"] == reading:
            out.append(f"{apath}.reading_id: the same as the adopted reading")
        readings[alt["reading_id"]] = alt_req_ids or set()
        out += [f"{apath}.{x}" for x in check_fragments(alt_frags, raw)]
        out += [f"{apath}.{x}" for x in check_terms(alt["terms"], alt_frag_ids)]
        out += [f"{apath}.{x}" for x in check_judgement(alt["judgement"], alt_frag_ids)]
        out += [f"{apath}.{x}" for x in check_interpretation(alt_interp, raw, cond_ids, alt_frag_ids)]
        out += check_requirement_refs(alt_interp, seg_ids, men_ids, f"{apath}.interpretation")
        out += check_failed_sources(alt["judgement"], alt_req_ids, cond_ids, alt_frag_ids, f"{apath}.judgement")
        if set(alt["requirement_ids"]) != (alt_req_ids or set()):
            out.append(f"{apath}.requirement_ids: keys differ from its requirement IDs")
        out += _bindings_problems(alt["bindings"], cond_ids, set(alt["requirement_ids"].values()),
                                  f"{apath}.bindings")
        out += check_condition_fields(alt["condition_fields"], conditions, f"{apath}.condition_fields")
        out += check_inferred(alt["inferred"], alt_interp, conditions, f"{apath}.inferred")
        out += _selection_refs_problems(alt, analysis, alt_interp, apath)
        out += check_structure(alt, analysis, alt_interp, apath, adopted=True)["problems"]  # its own IDs
        for k, r in enumerate(alt_interp.get("requirements") or []):
            if isinstance(r, dict) and r.get("reading_id") != alt["reading_id"]:
                out.append(f"{apath}.interpretation.requirements[{k}].reading_id: not its reading")
    out += _check_seeds(record["assumption_seeds"], readings, seg_ids, men_ids, cond_ids, f"{path}.assumption_seeds")
    if alt is not None:
        out += _check_seeds(alt["assumption_seeds"], readings, seg_ids, men_ids, cond_ids,
                            f"{path}.alternative.assumption_seeds")
    basis = record["certainty"]
    if basis is None:
        out.append(f"{path}.certainty: an adopted model structure has a certainty basis")
    else:
        want = basis["rules_certain"] and basis["same_structure"] and basis["verified"] \
            and not basis["seeds_left"] and not basis["failures_left"]
        if judgement.get("certain") is not want:
            out.append("judgement.certain: does not fit the certainty basis (7.2)")
        if basis["seeds_left"] != bool(record["assumption_seeds"]):
            out.append(f"{path}.certainty.seeds_left: differs from the assumption seeds")
        if basis["failures_left"] != bool(judgement.get("failed_conditions")):
            out.append(f"{path}.certainty.failures_left: differs from failed_conditions")
        if basis["same_structure"] == bool(basis["differences"]):
            out.append(f"{path}.certainty.differences: listed exactly when the structures differ")
        if ("reason" in basis) != ("certain" in basis):
            out.append(f"{path}.certainty: certain and reason are recorded together")
        elif "reason" in basis and (basis["certain"] is not False or basis["rules_certain"]
                                    or not basis["same_structure"] or not basis["verified"] or basis["seeds_left"]
                                    or basis["failures_left"] or judgement.get("failed_sources")
                                    or any(c["outcome"] != "resolved_by_code" for c in record["rule_checks"])):
            out.append(f"{path}.certainty.reason: only when the rules being uncertain is the one reason left (B)")
    return out


def _rule_diagnostics_problems(diag: dict, path: str) -> list[str]:
    if diag["certain"] and diag["failed_conditions"]:
        return [f"{path}.rule_diagnostics.certain: true while failed_conditions is not empty"]
    return []


def _bindings_problems(bindings: list, cond_ids: set | None, proposal_ids: set, path: str) -> list[str]:
    out: list[str] = []
    seen: set = set()
    for k, b in enumerate(bindings):  # one condition may be bound twice in the same way (verification allows it)
        out += _ref(b["condition_id"], cond_ids, f"{path}[{k}].condition_id")
        out += _refs(b["requirement_ids"], proposal_ids, f"{path}[{k}].requirement_ids")
        seen.add(b["condition_id"])
    if cond_ids is not None and bindings and seen != cond_ids:
        out.append(f"{path}: not one binding per condition")
    return out


def check_verify_result(result: dict, segment_ids: set | None = None, condition_ids: set | None = None,
                        requirement_ids: set | None = None) -> list[str]:
    """Plan 15 r3 step 3: a ``VerifyResult``. Shape; a fallback has reasons, every reason is fatal and no structure
    findings, scopes or seed are kept; an accepted result has no reasons and its issues are not fatal; a dropped
    alternative has a fatal reason, a kept one none; the seed is set exactly when an accepted result keeps a finding
    or an alternative; the scopes index the issues; every issue names known IDs and fits its fatal flag."""
    out = _type_problems(result, shapes.VerifyResult, "verify_result", False)
    if not isinstance(result, dict) or out:
        return out
    fallback_codes = set(get_args(shapes.FallbackCode))
    reasons, issues = result["fallback_reasons"], result["issues"]
    alt, alt_issues = result["alternative"], result["alternative_issues"]
    for field, rows in (("fallback_reasons", reasons), ("issues", issues), ("alternative_issues", alt_issues)):
        for k, it in enumerate(rows):
            path = f"verify_result.{field}[{k}]"
            if it["fatal"] != (it["code"] in fallback_codes):
                out.append(f"{path}.fatal: {it['fatal']} does not fit code {it['code']!r}")
            if not _scoped_fits(it):
                out.append(f"{path}: a {it['code']!r} finding does not name its scope")
            out += _refs(it["segment_ids"], segment_ids, f"{path}.segment_ids")
            out += _refs(it["condition_ids"], condition_ids, f"{path}.condition_ids")
            if it["reading"] != "alternative":
                out += _refs(it["requirement_ids"], requirement_ids, f"{path}.requirement_ids")
    if result["outcome"] == "fallback":
        if not reasons:
            out.append("verify_result.fallback_reasons: a fallback names its reasons")
        if issues or result["assumption_seed"] or any(result["scopes"][k] for k in shapes.VerifyScopes.__annotations__):
            out.append("verify_result: a fallback keeps no structure findings, scopes or seed")
    else:
        if reasons:
            out.append("verify_result.fallback_reasons: an accepted result has none")
        if any(it["fatal"] for it in issues):
            out.append("verify_result.issues: an accepted result keeps no fatal issue")
        if result["assumption_seed"] != bool(issues or alt == "kept"):
            out.append("verify_result.assumption_seed: set exactly when a finding or an alternative is kept")
        want = scope_index(issues)
        if result["scopes"] != want:
            out.append("verify_result.scopes: do not index the issues")
    if alt == "dropped" and result["outcome"] == "accept" and not any(it["fatal"] for it in alt_issues):
        out.append("verify_result.alternative_issues: a dropped alternative names a fatal reason")
    if alt == "kept" and any(it["fatal"] for it in alt_issues):
        out.append("verify_result.alternative_issues: a kept alternative has no fatal issue")
    if alt == "absent" and alt_issues:
        out.append("verify_result.alternative_issues: no alternative, no findings")
    return out


# plan 19 stage 3: findings recorded as scoped failures (select/needs.py reads the scope): what each one names
# (requirement, condition and segment ID counts)
_SCOPED = {
    "basis_merged": lambda r, c, s: r == 1 and c == 2 and s > 0,  # one requirement, the two conditions
    "basis_merged_fatal": lambda r, c, s: r == 1 and c == 2 and s > 0,
    "ratio_order_conflict": lambda r, c, s: r == 2 and c >= 2,  # the pair and its relation conditions
    "ratio_operands": lambda r, c, s: r >= 2 and c == 1,  # the operands of the one relation
    "added_measure": lambda r, c, s: r == 1 and c == 0 and s > 0,  # the one ref
}


def _scoped_fits(it: dict) -> bool:
    fits = _SCOPED.get(it.get("code"))
    return fits is None or fits(len(it["requirement_ids"]), len(it["condition_ids"]), len(it["segment_ids"]))


def scope_index(issues: list[dict]) -> dict:
    """``VerifyScopes`` of issues: codes per requirement, segment and condition (in issue order, no repeats); an issue
    without any requirement, segment or condition is question-level."""
    out: dict = {"requirements": {}, "segments": {}, "conditions": {}, "question": []}
    for it in issues:
        for field, key in (("requirement_ids", "requirements"), ("segment_ids", "segments"),
                           ("condition_ids", "conditions")):
            for i in it[field]:
                codes = out[key].setdefault(i, [])
                if it["code"] not in codes:
                    codes.append(it["code"])
        if not (it["requirement_ids"] or it["segment_ids"] or it["condition_ids"]) and it["code"] not in out["question"]:
            out["question"].append(it["code"])
    return out


def _unique_by(items: list, field: str, path: str) -> list[str]:
    seen, out = set(), []
    for k, it in enumerate(items):
        if not isinstance(it, dict) or field not in it:
            continue
        if it[field] in seen:
            out.append(f"{path}[{k}].{field}: duplicate {it[field]!r}")
        seen.add(it[field])
    return out


# ---------------------------------------------------------------- evidence


def check_check(check: dict, path: str = "check", condition_ids: set | None = None, row_ids: set | None = None,
                rows: dict | None = None, partial: bool = False) -> list[str]:
    out = _type_problems(check, shapes.Check, path, partial)
    if not isinstance(check, dict):
        return out
    name = check.get("name")
    detail_tp = shapes.DETAIL_BY_NAME.get(name)
    detail = check.get("detail")
    if detail_tp is not None and detail is not None:
        out += _type_problems(detail, detail_tp, f"{path}.detail", partial)
    detail = detail if isinstance(detail, dict) else {}
    if check.get("state") not in (None, "computed") and check.get("verdict") not in (None, "n/a"):
        out.append(f"{path}.verdict: a check that was not computed has verdict n/a")
    out += _refs(check.get("evidence_ids"), row_ids, f"{path}.evidence_ids")
    out += _ref(detail.get("condition_id"), condition_ids, f"{path}.detail.condition_id")
    for k, row in enumerate(detail.get("rows") or []):
        if isinstance(row, dict):
            out += _ref(row.get("condition_id"), condition_ids, f"{path}.detail.rows[{k}].condition_id")
            if isinstance(row.get("parent_path"), list) and isinstance(row.get("depth"), int) \
                    and row["depth"] != len(row["parent_path"]):
                out.append(f"{path}.detail.rows[{k}].depth: {row['depth']} != len(parent_path)")
    if isinstance(detail.get("axes"), dict):
        out += _refs(list(detail["axes"]), condition_ids, f"{path}.detail.axes")
    if isinstance(detail.get("request"), dict):
        out += _refs(detail["request"].get("condition_ids"), condition_ids, f"{path}.detail.request.condition_ids")
    if name == "indicator":
        out += _indicator_problems(check, detail, rows or {}, path)
    return out


def _indicator_problems(check: dict, detail: dict, rows: dict, path: str) -> list[str]:
    """Not extracted (available false) and extracted-but-no-match (result none) must stay different values."""
    out = []
    if "available" not in detail:
        return out
    stances = [rows[e]["stance"] for e in check.get("evidence_ids") or [] if isinstance(rows.get(e), dict)
               and "stance" in rows[e]]
    if detail["available"] is False:
        if detail.get("result", None) is not None:
            out.append(f"{path}.detail.result: must be None when no indicator was extracted")
        if check.get("verdict") not in (None, "n/a"):
            out.append(f"{path}.verdict: n/a when no indicator was extracted")
        if any(s != "unknown" for s in stances):
            out.append(f"{path}.evidence_ids: stance must be unknown when no indicator was extracted")
    elif "result" in detail and detail["result"] is None:
        out.append(f"{path}.detail.result: an extracted indicator has a result (match/partial/none)")
    elif detail.get("result") == "none" and any(s != "against" for s in stances):
        out.append(f"{path}.evidence_ids: stance must be against when the indicator does not match")
    return out


def _bundle_problems(bundle, table_ids: set, path: str) -> list[str]:
    """Plan 19 contract joint item 6 (agreed with B): an optional ``Selection.bundle`` (absent: none). Its shape is
    checked with the selection (``shapes.SelectionBundle``); here: the context and every table's assignment by the
    shared period package (no rule copied), the bundle ID of the context, and assigned tables among the selection's."""
    if not isinstance(bundle, dict):
        return []
    import claview_period  # the shared period package (as period.py reads it)

    out: list[str] = []
    context = bundle.get("context")
    if isinstance(context, dict):
        out += [f"{path}.{x}" for x in claview_period.check_context(context)]
        if context.get("bundle_id") != bundle.get("bundle_id"):
            out.append(f"{path}.bundle_id: differs from context.bundle_id")
    assignments = bundle.get("assignments")
    for table_id, segments in (assignments.items() if isinstance(assignments, dict) else []):
        if table_id not in table_ids:
            out.append(f"{path}.assignments.{table_id}: not a table of the selection")
        out += [f"{path}.assignments.{table_id}.{x}" for x in claview_period.check_assignment(segments)]
    return out


def check_selections(selections: list, groups: list | None = None, requirement_ids: set | None = None) -> list[str]:
    """Selections (plan 04, 5-2) and their query groups: shapes, one selection per requirement, a no_table selection
    without tables, groups over known requirements and tables. Plan 19 contract joint item 6: the optional public
    ``bundle`` (``_bundle_problems``) and ``reference_candidates`` (raw mention candidates, each naming its table; kept
    for B, never fetched)."""
    out = _type_problems(selections, list[shapes.Selection], "selections", False)
    seen: set = set()
    tables_of: dict = {}
    for k, s in enumerate(selections if isinstance(selections, list) else []):
        if not isinstance(s, dict):
            continue
        rid = s.get("requirement_id")
        if rid in seen:
            out.append(f"selections[{k}]: duplicate requirement {rid!r}")
        seen.add(rid)
        out += _ref(rid, requirement_ids, f"selections[{k}].requirement_id")
        tables_of[rid] = {t.get("table_id") for t in s.get("tables") or [] if isinstance(t, dict)}
        if s.get("status") == "no_table" and s.get("tables"):
            out.append(f"selections[{k}]: no_table with tables")
        out += _bundle_problems(s.get("bundle"), tables_of[rid], f"selections[{k}].bundle")
        for j, c in enumerate(s.get("reference_candidates") or [] if isinstance(s.get("reference_candidates"), list)
                              else []):
            if isinstance(c, dict) and not (isinstance(c.get("table_id"), str) and c["table_id"]):
                out.append(f"selections[{k}].reference_candidates[{j}].table_id: empty")
    if groups is not None:
        out += _type_problems(groups, list[shapes.SelectionGroup], "selection_groups", False)
        for k, g in enumerate(groups if isinstance(groups, list) else []):
            if not isinstance(g, dict):
                continue
            out += _refs(g.get("requirement_ids"), set(tables_of), f"selection_groups[{k}].requirement_ids")
            known = set().union(*(tables_of.get(r, set()) for r in g.get("requirement_ids") or []))
            out += _refs(g.get("table_ids"), known, f"selection_groups[{k}].table_ids")
    return out


def check_selection_route(route: dict, requirement_ids: set | None = None) -> list[str]:
    """Plan 07 r4 S5: the question-level way (shape, known requirements, at most 5 options)."""
    out = _type_problems(route, shapes.SelectionRoute, "selection_route", False)
    if not isinstance(route, dict):
        return out
    out += _refs(route.get("requirement_ids"), requirement_ids, "selection_route.requirement_ids")
    if isinstance(route.get("options"), list) and len(route["options"]) > 5:
        out.append("selection_route.options: more than 5")
    # plan 10 section 4: per question 2 to 5 options over known requirements, every option a whole cover bundle;
    # at most three questions asked in one turn (the rest deferred)
    questions = route.get("questions")
    if isinstance(questions, list):
        active = 0
        for k, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            path = f"selection_route.questions[{k}]"
            out += _refs(q.get("requirement_ids"), requirement_ids, f"{path}.requirement_ids")
            opts = q.get("options") if isinstance(q.get("options"), list) else []
            # plan 11 R1: an open question (fewer than two options, answered by a text) and a question whose
            # options are no table bundles (rephrase, drop_requirement) are checked by _question_contract; every
            # other question keeps the plan 10 checks as they were
            if q.get("open") is not True and q.get("effect_kind", "pick_assignment") in shapes.TABLE_EFFECT_KINDS:
                if not 2 <= len(opts) <= 5:
                    out.append(f"{path}.options: {len(opts)} options (2 to 5)")
                for j, o in enumerate(opts):
                    if not isinstance(o, dict) or not o.get("table_ids"):
                        out.append(f"{path}.options[{j}]: no table_ids")
                    elif o.get("complete") is not True:
                        out.append(f"{path}.options[{j}]: not a whole cover bundle")
            active += not q.get("deferred")
        if active > 3:
            out.append("selection_route.questions: more than 3 asked in one turn")
        out += _question_contract([q for q in questions if isinstance(q, dict)])
    return out


# ---------------------------------------------------------------- plan 11 R1: question contract, answers, conditions


def version_of(value) -> str:
    """Content version of a JSON-like value (plan 11 R1, 7-8): the first 16 hex digits of the sha256 of its canonical
    JSON. Two questions asked over the same analysis and selections carry the same version."""
    import hashlib
    import json

    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _question_contract(questions: list[dict]) -> list[str]:
    """The new fields of plan 11 R1 on the questions that carry them (``shapes.QUESTION_CONTRACT_FIELDS``, all or
    none): unique question IDs, one version over the route, a cause, the open branch (0 to 5 options, no table
    bundle needed; ``rephrase`` is always open), option shapes (``shapes.RouteOption``) with unique IDs, assignments
    over the question's requirements, and effects that fit the effect kind."""
    out: list[str] = []
    seen: set = set()
    versions: set = set()
    for k, q in enumerate(questions):
        path = f"selection_route.questions[{k}]"
        present = [f for f in shapes.QUESTION_CONTRACT_FIELDS if f in q]
        if not present:
            continue
        out += [f"{path}: missing field {f!r} of the question contract" for f in shapes.QUESTION_CONTRACT_FIELDS
                if f not in q]
        qid = q.get("id")
        if isinstance(qid, str):
            if not qid:
                out.append(f"{path}.id: empty")
            if qid in seen:
                out.append(f"{path}.id: duplicate {qid!r}")
            seen.add(qid)
        version = q.get("version")
        if isinstance(version, dict):
            if not all(isinstance(version.get(f), str) and version.get(f) for f in ("analysis", "selection")):
                out.append(f"{path}.version: analysis and selection versions are required")
            versions.add((version.get("analysis"), version.get("selection")))
        if "cause" in q and not (isinstance(q["cause"], str) and q["cause"]):
            out.append(f"{path}.cause: an ask cause code is required")
        kind = q.get("effect_kind")
        opts = q.get("options") if isinstance(q.get("options"), list) else []
        is_open = q.get("open") is True
        if is_open and len(opts) > 5:
            out.append(f"{path}.options: {len(opts)} options (an open question has at most 5)")
        if not is_open and "open" in q and len(opts) < 2:
            out.append(f"{path}.open: a question with fewer than two options is open")
        if kind == "rephrase" and not is_open:
            out.append(f"{path}.open: a rephrase question is open (its answer is a new question text)")
        if kind == "drop_requirement" and not is_open and not 1 <= len(opts) <= 5:
            out.append(f"{path}.options: {len(opts)} options (1 to 5)")
        rids = set(q.get("requirement_ids") or [])
        option_ids: set = set()
        drops = 0
        for j, o in enumerate(opts):
            opath = f"{path}.options[{j}]"
            out += _type_problems(o, shapes.RouteOption, opath, False)
            if not isinstance(o, dict):
                continue
            if isinstance(o.get("id"), str):
                if o["id"] in option_ids:
                    out.append(f"{opath}.id: duplicate {o['id']!r}")
                option_ids.add(o["id"])
            assignment = o.get("assignment") if isinstance(o.get("assignment"), dict) else {}
            out += [f"{opath}.assignment: {r!r} is not a requirement of the question" for r in assignment
                    if r not in rids]
            drops += any(v == [] for v in assignment.values())
            if kind in ("pick_assignment", "set_condition") and not is_open:
                if not assignment or any(not v for v in assignment.values()):
                    out.append(f"{opath}.assignment: every assigned requirement gets tables")
                tables = o.get("table_ids")
                if isinstance(tables, list):
                    out += [f"{opath}.assignment: table {t!r} is not among the option's table_ids"
                            for v in assignment.values() if isinstance(v, list) for t in v if t not in tables]
            effect = o.get("effect") if isinstance(o.get("effect"), dict) else {}
            if kind == "set_condition":
                if effect.get("value") is None:
                    out.append(f"{opath}.effect.value: a set_condition option sets one value")
                if effect.get("dimension") != q.get("dimension"):
                    out.append(f"{opath}.effect.dimension: differs from the question's dimension")
        if kind == "drop_requirement" and opts and not drops:
            out.append(f"{path}.options: a drop_requirement question has an option that leaves a requirement out")
    if len(versions) > 1:
        out.append("selection_route.questions: questions of one route carry different versions")
    return out


def check_selection_answer(answer: dict, questions: list | None = None, path: str = "answer") -> list[str]:
    """Plan 11 R1 (C, 7-8): one ``SelectionAnswer``: its shape, exactly one of ``option_id`` and ``text``; with the
    questions asked: a known question and option and the same version (``version: stale``: the answer is not applied,
    the question is asked again). Shape only: nothing here applies the answer."""
    out = _type_problems(answer, shapes.SelectionAnswer, path, False)
    if not isinstance(answer, dict):
        return out
    has_option, has_text = "option_id" in answer, "text" in answer
    if has_option == has_text:
        out.append(f"{path}: exactly one of option_id and text")
    if has_text and isinstance(answer.get("text"), str) and not answer["text"].strip():
        out.append(f"{path}.text: empty")
    if questions is None:
        return out
    by_id = {q.get("id"): q for q in questions if isinstance(q, dict) and q.get("id")}
    q = by_id.get(answer.get("question_id"))
    if q is None:
        out.append(f"{path}.question_id: unknown question {answer.get('question_id')!r}")
        return out
    if has_option and answer.get("option_id") not in {o.get("id") for o in q.get("options") or []
                                                      if isinstance(o, dict)}:
        out.append(f"{path}.option_id: unknown option {answer.get('option_id')!r} of {q['id']!r}")
    if answer.get("version") != q.get("version"):
        out.append(f"{path}.version: stale (differs from the version of question {q['id']!r})")
    return out


def check_selection_answers(answers: list, questions: list | None = None) -> list[str]:
    """Every answer of a list (``check_selection_answer``). Several answers to one question are allowed: the later
    one replaces the earlier (7-8)."""
    if not isinstance(answers, list):
        return [f"answers: expected a list, got {type(answers).__name__}"]
    out: list[str] = []
    for k, a in enumerate(answers):
        out += check_selection_answer(a, questions, f"answers[{k}]")
    return out


CONFIRMED_CONDITION_FIELDS = ("requirement_key", "requirement_id", "dimension", "value", "polarity", "source", "from")


def check_confirmed_condition(detail: dict, requirement_ids: set | None = None, condition_ids: set | None = None,
                              path: str = "confirmed") -> list[str]:
    """Plan 11 R1 (C, 8-3-1, F6): a confirmed condition (``ConfirmedDetail`` with every new field). Source
    ``interpretation``: a condition of the analysis (``condition_id``), no ``from``; source ``user``: ``from`` names
    the question (and option) answered."""
    out = _type_problems(detail, shapes.ConfirmedDetail, path, False)
    if not isinstance(detail, dict):
        return out
    out += [f"{path}: missing field {f!r} of a confirmed condition" for f in CONFIRMED_CONDITION_FIELDS
            if f not in detail]
    for f in ("requirement_key", "requirement_id", "dimension", "value"):
        if isinstance(detail.get(f), str) and not detail[f]:
            out.append(f"{path}.{f}: empty")
    out += _ref(detail.get("requirement_id"), requirement_ids, f"{path}.requirement_id")
    out += _ref(detail.get("condition_id"), condition_ids, f"{path}.condition_id")
    source = detail.get("source")
    if source == "interpretation":
        if detail.get("condition_id") is None:
            out.append(f"{path}.condition_id: a condition of the interpretation names its condition")
        if detail.get("from") is not None:
            out.append(f"{path}.from: only a user answer has a question it came from")
    elif source == "user" and not isinstance(detail.get("from"), dict):
        out.append(f"{path}.from: a user answer names the question it came from")
    return out


# ---------------------------------------------------------------- plan 11 R1 (F2): interpretation fields


def _unwrap(tp):
    if get_origin(tp) is NotRequired:
        (tp,) = get_args(tp)
    return tp


def _typed_dicts_in(tp) -> list:
    tp = _unwrap(tp)
    if typing.is_typeddict(tp):
        return [tp]
    return [x for arm in get_args(tp) for x in _typed_dicts_in(arm)]


def interpretation_fields() -> dict[str, tuple | None]:
    """Every leaf field of the interpretation output (``Analysis`` and the shapes it holds, the condition attributes
    of every kind included): ``"Shape.field"`` -> its literal values (a field typed by one ``Literal``) or None. A
    field holding other shapes is no leaf (its shapes' fields are listed); ``Condition.attrs`` is covered by the
    attribute shapes of ``ATTRS_BY_KIND``. Read by the ``field_effects`` coverage test (data/decision_policy.json)."""
    out: dict[str, tuple | None] = {}
    todo = [shapes.Analysis, *shapes.ATTRS_BY_KIND.values()]
    done: set = set()
    while todo:
        tp = todo.pop(0)
        if tp in done:
            continue
        done.add(tp)
        for name, field_tp in tp.__annotations__.items():
            inner = _typed_dicts_in(field_tp)  # also through list[...], dict[...] and unions
            if inner:
                todo += [x for x in inner if x not in done]
                continue
            if tp is shapes.Condition and name == "attrs":
                continue
            f = _unwrap(field_tp)
            out[f"{tp.__name__}.{name}"] = get_args(f) if get_origin(f) is Literal else None
    return out


def check_evidence(doc: dict, requirement_ids: set | None = None, condition_ids: set | None = None,
                   table_ids: set | None = None, partial: bool = False) -> list[str]:
    out = _top_level(doc, shapes.EvidenceDoc, "evidence_doc")  # entries are checked below
    if not isinstance(doc, dict):
        return out
    if "evidence" in doc and not isinstance(doc["evidence"], list):
        return out + ["evidence_doc.evidence: expected a list"]
    if "evidence_rows" in doc and not isinstance(doc["evidence_rows"], dict):
        return out + ["evidence_doc.evidence_rows: expected a dict"]
    if not partial:
        out += [f"evidence_doc: missing field {f!r}" for f in shapes.EvidenceDoc.__annotations__ if f not in doc]
    rows = doc.get("evidence_rows")
    row_ids = set(rows) if isinstance(rows, dict) else None
    if isinstance(rows, dict):
        for rid, row in rows.items():
            out += _type_problems(row, shapes.EvidenceRow, f"evidence_rows.{rid}", partial)
    seen = set()
    for k, entry in enumerate(doc.get("evidence") or []):
        path = f"evidence[{k}]"
        out += _top_level(entry, shapes.EvidenceEntry, path)
        if not isinstance(entry, dict):
            continue
        for f in ("requirement_id", "table_id"):
            if f in entry and not isinstance(entry[f], str):
                out.append(f"{path}.{f}: expected str")
        if not partial:
            out += [f"{path}: missing field {f!r}" for f in shapes.EvidenceEntry.__annotations__ if f not in entry]
        out += _ref(entry.get("requirement_id"), requirement_ids, f"{path}.requirement_id")
        out += _ref(entry.get("table_id"), table_ids, f"{path}.table_id")
        pair = (entry.get("requirement_id"), entry.get("table_id"))
        if pair in seen:
            out.append(f"{path}: duplicate requirement x table {pair}")
        seen.add(pair)
        checks = entry.get("checks")
        if checks is not None and not isinstance(checks, list):
            out.append(f"{path}.checks: expected a list")
            continue
        for c, check in enumerate(checks or []):
            out += check_check(check, f"{path}.checks[{c}]", condition_ids, row_ids,
                               rows if isinstance(rows, dict) else None, partial)
    return out
