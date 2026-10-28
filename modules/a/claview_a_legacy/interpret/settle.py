"""settle_requirements (detail 2.7, plan 04 2-1 and 2-6): the interpretation of a question.

From the judgement it is given (2.5; its drafts, their own condition IDs and its attribution are taken as they are:
"공통·모호는 2.5 결과 그대로") and the reading of the question behind them (``table``, for the parts of the question):
- one requirement per draft requirement, in order (r1, r2, ...). Its quote is one contiguous part of the question:
  for items that each start with their own period ("2014년 A와 2015년 B를 각각 보여줘") the part up to the next
  item (the last one to the end of the question); otherwise the item without its joiner, the last one up to its
  phrase end ("가상조합 총수신금리 차이는"). Copies of one indicator (row 6) and a requirement made of several items
  stand on the part from their first to their last item;
- indicator: the catalog term (name, path) or, without one, the noun words of its item; an item made only of
  conditions shares the noun words of the last item ("2014년 기준과 2015년 기준 성장성 지표"); targets: the
  catalog target values, and the shared modifier of "X A와 B" when B stands alone and X and B are found together in
  one table (catalog evidence);
- conditions: own (``condition_ids``), common to every requirement (``shared_condition_ids`` =
  ``common_condition_ids``), or ambiguous (``ambiguous_condition_ids`` of every requirement: carried, never used to
  pass or fail a table);
- relations: relation words (ratio, diff, share_of, percent_of read as ratio) and a comparison word over two or more
  requirements (compare), with their condition IDs; display words are listed apart;
- status of a requirement: unsupported (out-of-scope term), unresolved (no term, mixed basis axes, appositive), ok;
  context and breakdown as read by the table. Convention guesses are not made here (marks only, cycle 2), branches
  are noted when a term has several official candidates (``reasons``);
- status of the interpretation: unsupported (every requirement), partial (some), ask (the judgement is not certain:
  the router may ask to split requirements), ok.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..contract.shapes import (Condition, Fragment, IndicatorRef, Interpretation, Judgement, Relation, Requirement,
                               TargetRef, Term, TextView)
from .attribution import shared_head
from .lexicon import BASE_WORD_TEXT, JOINERS, LIST_WORD_KEYS, MEASURE_SKIP, RELATION_SEPARATORS
from .phrases import Tok, read_question
from .tokens import trim_span, word_conditions
from .reading import EXACT_VARIANTS, ItemRead
from .table import ATTRIBUTED, Draft, Judged, judge

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

RELATION_KINDS = {"ratio": "ratio", "diff": "diff", "share_of": "share_of", "percent_of": "ratio",
                  "compare": "compare"}
_MEASURE_SKIP = frozenset({"request", "list_words", "negation", "appositive", "combine", "share", "exclude_verb",
                           "clause_ends", "particle"})
_ALL_WORDS = MEASURE_SKIP


def _frag_at(pos: int, fragments: list[Fragment]) -> str | None:
    return next((f["id"] for f in fragments if f["span"][0] <= pos < f["span"][1]), None)


def _measure_tokens(r: ItemRead) -> list[Tok]:
    """Noun words of an item: names, qualifier-free words and hints, without conditions, request and marker words,
    "모두·각각" and the "기준" of a base year."""
    out = []
    prev = None
    for t in r.item.toks:
        if t.kind in ("name", "hint") or (t.kind == "word" and t.label not in _MEASURE_SKIP):
            stem = t.stem if t.kind == "word" else t.quote
            base_year = t.kind == "word" and prev is not None and prev.kind == "cond" and stem == BASE_WORD_TEXT
            if not base_year and not (t.kind == "word" and stem in _ALL_WORDS) and not t.negated and not t.excluded:
                out.append(t)
        prev = t
    return out


def _span_of_tokens(toks: list[Tok], raw: str) -> list[int] | None:
    if not toks:
        return None
    a = toks[0].start
    last = toks[-1]
    b = last.end
    if last.kind == "word":  # a word token carries its particle in the quote
        b = last.stem_end
    return [a, b]


_trim = trim_span  # the token layer trims a quote span (marks, spaces, a joiner at its end)


def _period_parallel(items: list[ItemRead]) -> bool:
    """Every item after the first starts with its own period and the first has one too (the old period split)."""
    if len(items) < 2:
        return False
    first = items[0].item.toks
    if not any(t.kind == "cond" and t.cond_kind == "period" for t in first):
        return False
    return all(r.item.toks[0].kind == "cond" and r.item.toks[0].cond_kind == "period" for r in items[1:])


def _primary(d: Draft) -> ItemRead:
    if d.sources:
        return d.sources[0]
    return next((r for r in d.items if r.kind in ("indicator", "none")), d.items[0])


def _quote_span(d: Draft, j: Judged, raw: str, period_path: bool) -> list[int]:
    items = j.items
    end = j.plan.end if j.plan is not None else None
    index = {id(r): k for k, r in enumerate(items)}
    own_items = [r for r in d.items if id(r) in index]
    if not own_items:
        a = min(r.item.start for r in d.items)
        b = max(r.item.end for r in d.items)
        return _trim(raw, a, b)
    ks = sorted(index[id(r)] for r in own_items)
    if d.sources and len({index.get(id(s)) for s in d.sources} - {None}) >= 1:
        ks = sorted({index[id(s)] for s in d.sources if id(s) in index})
    first, last = ks[0], ks[-1]
    many = len(items) >= 2
    if period_path:
        a = 0 if first == 0 else items[first].item.start
        b = items[last + 1].item.start if last + 1 < len(items) else len(raw)
        return _trim(raw, a, b)
    a = 0 if (first == 0 and many) else items[first].item.start
    if last == len(items) - 1 and end is not None:
        b = max(end, items[last].item.start + 1)
        return _trim(raw, a, b)
    b = items[last].item.end
    return _trim(raw, a, b, joiner=True)


def _catalog_name(term) -> str | None:
    """The catalog name of a term: the first exact candidate's name. A word that only matches bracket-free or
    otherwise trimmed forms of two or more different catalog names ("M2" of "M2(말잔 계절조정계열)", "M2(평잔
    원계열)", ...) is the common front of a family of names: the name stays the question word (family level); which
    member is meant is the table selection's choice, not the interpretation's. One trimmed name only: that name."""
    exact = [c["name"] for c in term.cands if c.get("name") and _exact_candidate(c)]
    if exact:
        return exact[0]
    names = list(dict.fromkeys(c["name"] for c in term.cands if c.get("name")))
    if len(names) >= 2:
        return term.tok.quote
    return names[0] if names else None


def _exact_candidate(c: dict) -> bool:
    return c.get("path") == "exact" or c.get("variant", "").split(":")[-1] in EXACT_VARIANTS \
        or (c.get("path") == "dictionary" and c.get("status") == "approved")


def _indicator(d: Draft, j: Judged, raw: str, fragments: list[Fragment]) -> IndicatorRef | None:
    if d.broad:
        return None  # a broad question over its targets (R-8): the targets stay targets
    if d.indicator is not None:
        t = d.indicator.tok
        chosen = getattr(d.indicator, "model_chosen", None)  # a name the model step chose: not the exact words
        return {"quote": t.quote, "span": [t.start, t.end], "fragment_id": _frag_at(t.start, fragments),
                "name": chosen or _catalog_name(d.indicator),
                "path": "lexical" if chosen or not d.indicator.exact else "exact"}
    for r in [_primary(d)] + ([j.items[-1]] if j.items else []):
        span = _span_of_tokens(_measure_tokens(r), raw)
        if span:
            return {"quote": raw[span[0]:span[1]], "span": span, "fragment_id": _frag_at(span[0], fragments),
                    "name": None, "path": None}
    return None


def _targets(d: Draft, fragments: list[Fragment]) -> list[TargetRef]:
    out: list[TargetRef] = []
    for term in d.targets:
        t = term.tok
        out.append({"quote": t.quote, "span": [t.start, t.end], "fragment_id": _frag_at(t.start, fragments),
                    "name": _catalog_name(term)})
    return out


def _shared_head_target(k: int, drafts: list[Draft], j: Judged, raw: str, conditions: list[Condition],
                        layer, fragments: list[Fragment], span: list[int], words=None) -> TargetRef | None:
    """"X A와 B": X also modifies B (old shared head, with catalog evidence)."""
    if layer is None or k == 0 or drafts[k].indicator is not None and drafts[k].targets:
        return None
    items = j.items
    me, prev = _primary(drafts[k]), _primary(drafts[k - 1])
    if me not in items or prev not in items or items.index(me) != items.index(prev) + 1:
        return None
    mine, theirs = _measure_tokens(me), _measure_tokens(prev)
    if not mine or not theirs:
        return None
    b_tok, a_tok = mine[0], theirs[-1]
    b_span = _span_of_tokens([b_tok], raw)
    a_span = _span_of_tokens([a_tok], raw)
    ranges = [tuple(c["span"]) for c in conditions if c["kind"] != "breakdown"]
    names = layer.list_names()
    hit = shared_head(words, tuple(a_span), tuple(b_span), (b_span[1], span[1]), ranges, names.together)
    if hit is None:
        return None
    a, b = hit
    return {"quote": raw[a:b], "span": [a, b], "fragment_id": _frag_at(a, fragments), "name": None}


SEPARATORS = RELATION_SEPARATORS  # marks that end the phrase of a relation word
LIST_WORDS = LIST_WORD_KEYS  # coordinators: 및, 그리고, 또는, 혹은


def _draft_at(d: Draft) -> int:
    return d.indicator.tok.start if d.indicator is not None else min(r.item.start for r in d.items)


def _coordinated(c: Condition, first: int, toks: list[Tok]) -> bool:
    """The tokens between a relation word and the next requirement hold a coordinator: a list word ("및", "그리고")
    or a joiner particle written against the relation word ("비율과"): the relation word ends its operand phrase there.
    List marks (comma, middle dot) are read by ``between`` (``SEPARATORS``)."""
    a, b = c["span"]
    glued = next((t for t in toks if t.kind != "punct" and t.start <= a and t.end == b), None)
    if glued is not None and glued.send > b and glued.suffix in JOINERS:
        return True
    return any(t.kind == "word" and t.norm in LIST_WORDS and b <= t.start < first for t in toks)


def between(points: list[tuple], c: Condition, rel_conds: list[Condition], toks: list[Tok]) -> bool:
    """A relation word written between its operands ("A 대비 B"): a requirement before it, and a requirement after it
    with no other relation word, no separator (comma, semicolon; the token layer's marks, full-width forms read as
    the half-width ones) and no coordinator in between. ``points``: (key, position) of the requirements."""
    a, b = c["span"]
    nxt = [p for _k, p in points if p >= b]
    if not nxt or not any(p < a for _k, p in points):
        return False
    first = min(nxt)
    return not any(b <= x["span"][0] < first for x in rel_conds if x is not c) \
        and not any(t.kind == "punct" and t.label in SEPARATORS and b <= t.start < first for t in toks) \
        and not _coordinated(c, first, toks)


def relation_operands(points: list[tuple], c: Condition, rel_conds: list[Condition], toks: list[Tok]) -> list:
    """The requirements one relation word is over (finding 7): a word between its operands ("A 대비 B") takes the
    requirement right before and right after it; a word after them ("A와 B의 차이", "A 대비 B 비율, C 대비 D 비율",
    "A 대비 B 비율 및 C 대비 D 비율") takes the requirements since the previous trailing relation word. Keys in
    position order; fewer than two: the caller keeps the group."""
    points = sorted(points, key=lambda kp: kp[1])
    a = c["span"][0]
    if between(points, c, rel_conds, toks):
        before = [k for k, p in points if p < a]
        after = [k for k, p in points if p >= c["span"][1]]
        return [before[-1], after[0]]
    lo = max((x["span"][1] for x in rel_conds if x["span"][1] <= a and not between(points, x, rel_conds, toks)),
             default=-1)
    return [k for k, p in points if lo <= p < a]


def _operands(drafts: list[Draft], c: Condition, rel_conds: list[Condition], toks: list[Tok]) -> list[Draft]:
    by_id = {d.id: d for d in drafts}
    return [by_id[k] for k in relation_operands([(d.id, _draft_at(d)) for d in drafts], c, rel_conds, toks)]


def _relations(j: Judged, rid: dict[str, str], conditions: list[Condition], qtoks: list[Tok]) -> list[Relation]:
    """The relations of the settled requirements; ``qtoks``: the token layer of the question."""
    out: list[Relation] = []
    words = word_conditions(qtoks, "relation")  # relation words of the question, not inside a catalog name
    conditions = [c for c in conditions if c["kind"] != "relation" or c["id"] in words]
    for res, g in zip(j.results, j.groups):
        ids = [rid[d.id] for d in res.drafts if d.id in rid]
        stop = max(g.clause_end, g.end, g.start + 1)
        for d in res.drafts:  # plan 05 A: a share over one requirement (whole = its indicator, part = breakdown)
            if d.share and d.id in rid:
                cond = next((c for c in conditions if c["kind"] == "relation"
                             and g.start <= c["span"][0] < stop), None)
                out.append({"kind": "share_of", "requirement_ids": [rid[d.id]],
                            "condition_id": cond["id"] if cond else None})
        if len(ids) < 2:
            continue
        toks = [t for it in g.modifier + g.items for t in it.toks]
        rel_conds = [c for c in conditions if c["kind"] == "relation" and g.start <= c["span"][0] < stop]
        for c in rel_conds:
            kind = RELATION_KINDS.get(c["attrs"].get("relation"))
            if kind:
                operands = [rid[d.id] for d in _operands(res.drafts, c, rel_conds, qtoks) if d.id in rid]
                out.append({"kind": kind, "requirement_ids": operands if len(operands) >= 2 else ids,
                            "condition_id": c["id"]})
        if not any(r["requirement_ids"] == ids for r in out):
            disp = next((c for c in conditions if c["kind"] == "display" and c["attrs"].get("display") == "compare"
                         and g.start <= c["span"][0] < stop), None)
            if disp is not None or any(t.kind == "cond" and t.cond_kind == "display" for t in toks):
                out.append({"kind": "compare", "requirement_ids": ids,
                            "condition_id": disp["id"] if disp else None})
    return out


def settle(text: TextView, conditions: list[Condition], fragments: list[Fragment], terms: list[Term],
           judgement: Judgement, layer: CatalogLayer | None = None, overrides=None) -> Interpretation:
    """``overrides``: the model step's decisions the judgement was made with (the same reading behind its drafts)."""
    raw = text["raw"]
    reading = read_question(text, conditions, layer)
    j = judge(reading, conditions, layer, overrides)  # the reading behind the drafts (items, spans, terms)
    order = [c["id"] for c in conditions if c["kind"] in ATTRIBUTED]
    # the 2.5 judgement is the source of the drafts and of the attribution (plan 04 2.7: "공통·모호는 2.5 결과
    # 그대로"); the reading is only read for the parts of the question each draft stands on
    by_id = {d.id: d for d in j.drafts}
    given = judgement.get("requirement_draft")
    if given is not None and "attribution" in judgement and all(g["id"] in by_id for g in given):
        drafts = [by_id[g["id"]] for g in given]
        att = judgement["attribution"]
        own_of = {g["id"]: [c for c in order if c in g.get("condition_ids", [])] for g in given}
        status_of = {g["id"]: g.get("status") for g in given}
    else:  # a judgement without its drafts and attribution: the reading's own
        drafts = j.drafts
        att = {cid: ("common" if p == "common" else "ambiguous" if p == "ambiguous" else None)
               for cid, p in j.place.items()}
        own_of = {d.id: [cid for cid in order if isinstance(j.place.get(cid), list) and d.id in j.place[cid]]
                  for d in drafts}
        status_of = {}
    rid = {d.id: f"r{n}" for n, d in enumerate(drafts, 1)}
    owned = {cid for ids in own_of.values() for cid in ids}
    common = [cid for cid in order if att.get(cid) == "common" and cid not in owned]
    ambiguous = [cid for cid in order if att.get(cid) == "ambiguous"]
    single = len(drafts) == 1
    if single:  # one requirement: its conditions are its own
        common = []
    period_path = _period_parallel(j.items)
    requirements: list[Requirement] = []
    unsupported_sources: dict[str, dict] = {}  # plan 11 R0 (8-2 item 10): record only
    for k, d in enumerate(drafts):
        span = _quote_span(d, j, raw, period_path)
        if span[1] <= span[0]:
            span = [d.items[0].item.start, d.items[0].item.end]
        own = [cid for cid in order if cid in own_of.get(d.id, [])
               or (single and att.get(cid) == "common" and cid not in owned)]
        targets = _targets(d, fragments)
        head = _shared_head_target(k, drafts, j, raw, conditions, layer, fragments, span, reading.words)
        if head is not None:
            targets.append(head)
        status = status_of.get(d.id) or d.status
        status = status if status in ("ok", "unresolved") else "ok"
        reasons = []
        if d.indicator is not None and "out_of_scope" in d.indicator.types:
            status = "unsupported"
            reasons.append("out_of_scope")
            types = sorted(t for t in d.indicator.types if t)
            unsupported_sources[rid[d.id]] = {"types": types, "all_out_of_scope": set(types) == {"out_of_scope"}}
        if d.broad:
            reasons.append("broad")
        if d.row is not None:
            reasons.append(f"row:{d.row}")
        if getattr(d, "ungrounded", False):
            reasons.append("ungrounded")  # user decision 10/19: no table holds its own words (selection: no table)
        if d.indicator is not None and len({c.get("name") for c in d.indicator.cands
                                            if c.get("path") == "exact"}) > 1:
            reasons.append("branch")  # several official names: the first is used, the router may ask
        req: Requirement = {
            "id": rid[d.id], "quote": raw[span[0]:span[1]], "span": span,
            "indicator": _indicator(d, j, raw, fragments), "measure_kind": None, "targets": targets,
            "condition_ids": own, "shared_condition_ids": list(common),
            "ambiguous_condition_ids": [c for c in ambiguous if c not in own], "status": status, "reasons": reasons,
            "context": d.context.tok.quote if d.context else d.context_name,
            "breakdown": [t.tok.quote for t in d.lower] + [t.quote for t in d.unnamed_lower]
            + [t.quote for t in d.axes]}
        requirements.append(req)
    statuses = [r["status"] for r in requirements]
    if requirements and all(s == "unsupported" for s in statuses):
        status = "unsupported"
    elif "unsupported" in statuses:
        status = "partial"
    elif not judgement.get("certain", False) or any("branch" in r["reasons"] for r in requirements):
        status = "ask"
    else:
        status = "ok"
    hints = [[rid[x] for x in grp if x in rid] for grp in judgement.get("table_group_hint", [])]
    out: Interpretation = {
        "status": status, "common_condition_ids": common, "relations": _relations(j, rid, conditions, reading.toks),
        "display_condition_ids": [c["id"] for c in conditions if c["kind"] == "display"],
        "requirements": requirements, "reasons": list(judgement.get("failed_conditions", [])),
        "table_group_hint": [h for h in hints if len(h) >= 2]}
    if unsupported_sources:
        out["unsupported_sources"] = unsupported_sources
    return out
