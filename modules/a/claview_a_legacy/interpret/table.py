"""The judgement table of judge_fragments (plan 04, 2-2) and the requirement drafts it makes.

Rows, the first that applies, read after every item has its own conditions (``attribution``):
1 same indicator and same own conditions -> merge; 2 the first item an indicator and every other a lower value of
it (or a value of the table it names) in every common table, with no own conditions or targets of their own, and no
relation word between them -> one requirement with breakdown; 3 two indicators read upper/lower in one common table
and siblings in another -> undecided; 4 target values -> shared by every indicator requirement (none: a broad
question); 5 X-byeol only -> breakdown axes of the previous requirement; 6 basis words, family qualifiers or
frequencies only -> copies of the previous indicator (a copy per distinct item: repeats merge, other own conditions
keep copies apart; two or more axes varying across copies that do not differ by period -> unresolved); 7 same
indicator, other own conditions (or row 2 blocked by them); 8 two indicators found together in a table; 9 not found
together; 10 no term -> unresolved.

A group of a later clause that names only items of an earlier clause (a designation: "가상B금리는 신규취급액 기준으로")
makes no requirement; its conditions are attributed to the items it names. A designation clause holding two
conditions of one kind, or a kind the item clauses already have, is flagged (``designation_conflict``, check 4).

With items in two or more item clauses (2-4 "항목 절"), the conditions of each item clause are attributed over the
items of that clause only (``_clause_scopes``). Values listed under a table name are read with the table as context
and rows 2 and 3 among them; a table name before a relation is the context of its operands, not an operand. An
X-byeol qualifier of another family is a breakdown axis of the indicator, not a family view.
"""

from __future__ import annotations

import dataclasses
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .attribution import KIND_CHAR, Plan, period_scopes, phrase_end, scopes
from .grounding import verify_splits
from .structure import split_grounding
from .lexicon import AXIS_LIST_PUNCT, BYEOL, GENITIVE_OF
from .phrases import GENITIVE, JOINER_SUFFIXES, SCOPE_RELATIONS, Group, Reading, Tok, key_of
from .overrides import Overrides, apply_terms, as_indicator
from .reading import (ItemRead, Relations, Term, attach_foreign_byeol, foreign_byeol, listed_target_peers, read_item,
                      term_of)

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

ATTRIBUTED = ("period", "frequency", "basis", "calc", "unit", "exclude", "breakdown")
SIG_KINDS = ("period", "frequency", "basis", "calc", "unit", "exclude")
VIEW_AXES_EXCLUDED = frozenset()


@dataclass(eq=False)
class Draft:
    group: Group
    items: list  # ItemRead the requirement stands on
    indicator: Term | None = None
    context: Term | None = None
    targets: list = field(default_factory=list)
    lower: list = field(default_factory=list)
    axes: list = field(default_factory=list)
    view: list = field(default_factory=list)
    status: str = "ok"
    broad: bool = False
    row: int | None = None
    id: str = ""
    copy_of: object = None
    sources: list = field(default_factory=list)  # items whose conditions make this copy (row 6)
    base: object = None  # the indicator item of a copy
    unnamed_lower: list = field(default_factory=list)  # breakdown words with no catalog name (never certain)
    context_name: str | None = None  # a table read from the words around the items (``inferred``)
    inferred: bool = False  # read from words around the items, not from exact names (never certain)
    ungrounded: bool = False  # user decision 10/19: a split piece with no table holding its own words (grounding)
    shared: list = field(default_factory=list)  # view values of row 6 that qualify every copy (not one copy's own)
    share: bool = False  # plan 05 A: one requirement over a whole with its part as breakdown (relation share_of)
    own_axes: list = field(default_factory=list)  # plan 05 B(1): the X-byeol axis a "각각" copy stands for
    alternates: list = field(default_factory=list)  # plan 05 F: other name candidates of an appositive ("A, 즉 B")
    axes_unsettled: bool = False  # plan 05 B (decision 2): listed X-byeol axes with no marker (never certain)


@dataclass
class GroupResult:
    drafts: list
    pairs: list  # (left ItemRead, right ItemRead, row, verdict)
    mixed: bool = False
    scope: bool = False
    cross_clause: bool = False
    modifier_used: bool = False
    hints: list = field(default_factory=list)


@dataclass
class Judged:
    drafts: list
    pairs: list
    groups: list
    reads: dict
    results: list
    items: list  # ItemRead of every list item, in order (the attribution segments)
    plan: Plan | None
    place: dict  # condition or hint ID -> draft IDs (own), "common" or "ambiguous"
    hint_ids: dict  # id(hint token) -> pseudo condition ID
    designation_conflict: bool = False  # a designation clause whose conditions would split or redo a requirement
    split_check: str | None = None  # split grounding: "reattached", "ungrounded_split" or None (``grounding``)
    resolved: set = field(default_factory=set)  # group starts whose appositive the model resolved (overrides)
    override_conflicts: list = field(default_factory=list)  # failures an override met (overrides; check names)


def _cond_value(c: dict) -> str:
    if c["kind"] == "calc":
        return f"calc:{key_of(str(c.get('value')))}:{key_of(c['quote'])}"
    if c["kind"] in ("basis", "unit", "frequency"):
        return f"{c['kind']}:{key_of(str(c.get('value')))}"
    if c["kind"] == "exclude":
        return f"exclude:{key_of(c['value'])}"
    return f"{c['kind']}:{key_of(c['quote'])}"


def _view_sig(v: list) -> tuple:
    return tuple(sorted((a, k) for a, k, _ in v))


DICT_FREE_AXES = frozenset({"classification", "family"})  # basis axes that name no slot of their own
OWN_KINDS = ("period", "calc", "unit", "exclude")  # own conditions that make the indicator item a copy of its own


def _basis_slot(entry: tuple) -> str | None:
    """The basis slot of a view entry from the condition word list (seasonal, price, ...), "frequency", or None."""
    a, _k, t = entry
    if a == "frequency":
        return "frequency"
    c = t.cond if t.kind in ("cond", "qual") else None
    if t.kind == "name":  # a basis value name over a condition word ("원화기준" holds 원화): that word's slot
        c = next((x for x in t.inner if x.get("kind") == "basis"), None)
    if c is not None and c["kind"] == "basis":
        axis = c["attrs"].get("axis")
        if axis and axis not in DICT_FREE_AXES:
            return axis
    return None


def _exclusive(x: tuple, y: tuple, rel: Relations) -> bool:
    """Two view values that copies of one indicator differ by: values of one slot of the condition word list, or
    words that stand in for each other in table names ("신규취급액" / "잔액" of two table names). Two family
    qualifiers one table name holds together ("가상기관", "가상규모별") qualify one requirement together."""
    if x[1] == y[1]:
        return False
    sx, sy = _basis_slot(x), _basis_slot(y)
    if sx and sy and sx == sy:
        return True
    return rel.swappable(x[1], y[1])


def _own_differs(base: ItemRead, extra: list[ItemRead], ctx: Context) -> bool:
    """The indicator item and a listed item carry own conditions of one kind (calculation, period, unit, exclusion)
    with different values ("명목 상위 5개와 명목 하위 5개")."""
    def by_kind(r: ItemRead) -> dict[str, set]:
        out: dict[str, set] = {}
        for c in ctx.own_conds(r):
            if c["kind"] in OWN_KINDS:
                out.setdefault(c["kind"], set()).add(_cond_value(c))
        return out
    mine = by_kind(base)
    return any(k in mine and vals != mine[k] for s in extra for k, vals in by_kind(s).items())


def _tail_frequency(cands: list[ItemRead], extra: list[ItemRead]) -> list[tuple]:
    """A frequency written only after the last listed item's own basis value ("A의 명목과 실질 금액을 분기별로")
    is a tail condition of every copy, not a value one copy differs by."""
    if len(cands) < 2:
        return []
    holders = [s for s in cands if any(a == "frequency" for a, _k, _t in s.view)]
    if len(holders) != 1 or holders[0] is not extra[-1]:
        return []
    freq = [e for e in holders[0].view if e[0] == "frequency"]
    others = [e for e in holders[0].view if e[0] != "frequency"]
    if not others or min(e[2].start for e in freq) < max(e[2].start for e in others):
        return []
    return freq


def _appositive_marks(g: Group) -> list[Tok]:
    """Appositive words ("즉") of a group read as an appositive."""
    if not g.appositive:
        return []
    return [t for it in g.modifier + g.items for t in it.toks if t.kind == "word" and t.label == "appositive"]


def _appositive_one(g: Group, drafts: list[Draft], rel: Relations) -> list[Draft]:
    """Plan 05 F (decision 3): "A, 즉 B" names one requirement twice. The appositive is the one list item that starts
    with the appositive word: its indicator is a name candidate of the draft right before it (``alternates``), not
    a requirement of its own; items after it stay requirements. An appositive inside one item ("A(즉 B)") is read
    by ``read_item``. When the two names share no table the requirement stays unresolved."""
    out = list(drafts)
    for mark in _appositive_marks(g):
        app = next((d for d in out if d.indicator is not None and d.items
                    and _first_word(d.items[0].item.toks) is mark), None)
        if app is None:
            continue
        k = out.index(app)
        head = next((d for d in reversed(out[:k]) if d.indicator is not None), None)
        if head is None:
            continue
        _add_alternate(head, app.indicator, rel)
        head.items += [r for r in app.items if r not in head.items]
        moved = [t for t in app.targets if t not in head.targets]
        head.targets = list(head.targets) + moved  # finding 10: the appositive's own targets stay
        head.axes = list(head.axes) + [t for t in app.axes if t not in head.axes]
        if any(not (t.tables & head.indicator.tables) for t in moved):
            head.status = "unresolved"  # a target of the appositive no table of the head holds: not settled
        out.remove(app)
    for d in out:  # an appositive inside one item: the names after the word
        for r in d.items:
            for alt in r.alternates:
                if alt not in d.alternates:
                    _add_alternate(d, alt, rel)
    return out


def _add_alternate(head: Draft, alt: Term, rel: Relations) -> None:
    head.alternates.append(alt)
    if not rel.together(head.indicator, alt):
        head.status = "unresolved"


def _first_word(toks: list[Tok]) -> Tok | None:
    return next((t for t in toks if t.kind != "punct"), None)




def _listed_axes(d: Draft, axes: list[Tok]) -> bool:
    """Two X-byeol axes of a draft written as list members: in different items, or with a comma, a middle dot, a
    slash, a list word or a joiner ending between them (decision 2 knows no exception). Only axes written one after
    the other with a space ("A별 B별로", one stacked phrase) are no list."""
    where = {}
    for r in d.items:
        for n, t in enumerate(r.item.toks):
            where[id(t)] = (id(r.item), n, r.item.toks)
    marks = sorted((where[id(t)] for t in axes if id(t) in where), key=lambda w: (w[0], w[1]))
    for (ia, na, toks), (ib, nb, _t) in zip(marks, marks[1:]):
        if ia != ib:
            return True
        if any(x.kind == "punct" and x.label in AXIS_LIST_PUNCT for x in toks[na + 1:nb]) \
                or any(x.kind == "word" and x.label == "list_words" for x in toks[na + 1:nb]) \
                or toks[na].suffix in JOINER_SUFFIXES:
            return True
    return False


def _each_axis(g: Group, drafts: list[Draft]) -> list[Draft]:
    """Plan 05 B(1), decision 2: a distributive marker ("각각") over two or more different X-byeol axes of one
    requirement ("X를 가상규모별, 가상업종별로 각각"): one requirement per axis. Without it the axes break one
    requirement down together (row 5) when a simultaneous marker ("동시에") says so or the axes are written as one
    phrase ("연령별 지역별로"); axes listed with list words or commas and no marker ("X를 A별, B별로") stay one
    requirement, undecided (``axes_unsettled``)."""
    out = []
    for d in drafts:
        axes = [t for t in d.axes if _byeol_tok(t)]
        keys = list(dict.fromkeys(t.norm for t in axes))
        if d.indicator is None or len(keys) < 2:
            out.append(d)
            continue
        if g.distributive and g.simultaneous:  # finding 5: both markers over the axes of one group: undecided
            d.axes_unsettled = True
            out.append(d)
            continue
        if not g.distributive:
            if not g.simultaneous and _listed_axes(d, axes):
                d.axes_unsettled = True
            out.append(d)
            continue
        for k in keys:
            mine = [t for t in axes if t.norm == k]
            out.append(dataclasses.replace(d, axes=[t for t in d.axes if t not in axes] + mine, copy_of=d,
                                           own_axes=mine, items=list(d.items), targets=list(d.targets),
                                           lower=list(d.lower), view=list(d.view)))
    return out


def _target_values(cands: list[ItemRead], shared: list, base_alt: bool, rel: Relations,
                   ctx: Context) -> list[tuple]:
    """Plan 05 B (decision 2): listed items that each hold one view value, every value a catalog target value
    ("X를 가상기관과 가상비은행으로 (각각)"): values of one target axis, not copies. [(view entry, target term)].
    Read after each item has its own conditions: items whose own conditions other than the values differ ("2020년
    A와 2021년 B") stay apart."""
    if base_alt or len(cands) < 2 or rel.layer is None:
        return []
    own = {tuple(sorted(_cond_value(c) for c in ctx.own_conds(s) if c["kind"] != "basis")) for s in cands}
    hints = {tuple(sorted(t.norm for t in s.item.toks if t.kind == "hint")) for s in cands}
    if len(own) > 1 or len(hints) > 1:
        return []
    out = []
    for s in cands:
        own = _minus(s.view, shared)
        if len(own) != 1 or own[0][2].kind not in ("cond", "qual"):
            return []
        term = term_of(own[0][2], rel.layer)
        if term.rep != "target" or not term.cands:
            return []
        if not term.key:
            term.key = own[0][2].norm
        out.append((own[0], term))
    return out if len({t.key for _e, t in out}) >= 2 else []


def _value_parts(s: ItemRead, rel: Relations, origin: dict) -> list[ItemRead]:
    """One listed item holding two or more values that stand in for each other ("X를 가상유형별, 가상지역별,
    가상유형별로"): one part per distinct value (repeats merge), so each copy differs by one value (plan 05 B)."""
    alt = [e for e in s.view if any(_exclusive(e, f, rel) for f in s.view if f is not e)]
    keys = list(dict.fromkeys(e[1] for e in alt))
    if len(keys) < 2:
        return [s]
    parts = []
    for k in keys:
        part = dataclasses.replace(s, view=[e for e in s.view if e not in alt or e[1] == k])
        origin[id(part)] = s
        parts.append(part)
    return parts


def _minus(view: list, shared: list) -> list:
    ids = {id(e[2]) for e in shared}
    return [e for e in view if id(e[2]) not in ids]


def _union(first: list, then: list) -> list:
    ids = {id(e[2]) for e in first}
    return list(first) + [e for e in then if id(e[2]) not in ids]


def _varying_slots(views: list[list], rel: Relations) -> int:
    """Slots whose values differ across the copies: entries of one slot of the word list, or of the same view label,
    are one slot; two entries that stand in for each other (``_exclusive``) join their slots."""
    entries = [(n, e) for n, v in enumerate(views) for e in v]
    label = {id(e[2]): _basis_slot(e) or e[0] for _n, e in entries}
    parent = {lab: lab for lab in label.values()}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, (n, x) in enumerate(entries):
        for m, y in entries[i + 1:]:
            if n != m and _exclusive(x, y, rel):
                parent[find(label[id(x[2])])] = find(label[id(y[2])])
    slots = {find(lab) for lab in label.values()}
    varying = 0
    for s in slots:
        values = {tuple(sorted(e[1] for e in v if find(label[id(e[2])]) == s)) for v in views}
        varying += len(values) >= 2
    return varying


def _one_table(ind: Term, toks: list[Tok], rel: Relations) -> bool:
    if rel.rel is None:
        return False
    tables = set(ind.tables)
    for t in toks:
        k = key_of(t.cond["value"]) if t.kind == "cond" and t.cond else t.key
        tables &= rel.rel.tables_of(k)
    return bool(tables)


class Context:
    """Item-level own conditions (from the attribution plan) and the signatures rows 1, 2, 6 and 7 compare."""

    def __init__(self, items: list[ItemRead], own: dict[int, list[str]], by_id: dict, hint_ids: dict,
                 negated: set[str] = frozenset()):
        self.own = own
        self.by_id = by_id
        self.hint_ids = hint_ids
        self.items = items
        self.negated = negated  # conditions a negation took out ("A가 아니라 B": A) tell no copy apart

    def own_conds(self, r: ItemRead) -> list[dict]:
        return [self.by_id[c] for c in self.own.get(id(r), []) if c in self.by_id and c not in self.negated]

    def sig(self, r: ItemRead, skip: frozenset = frozenset()) -> tuple:
        """Own conditions (period, frequency, basis, calc, unit, exclude; those in ``skip`` left out), calc hints and
        own targets of an item."""
        conds = tuple(sorted(_cond_value(c) for c in self.own_conds(r) if c["kind"] in SIG_KINDS
                             and c["id"] not in skip))
        hints = tuple(sorted(t.norm for t in r.item.toks if t.kind == "hint"
                             and self.hint_ids.get(id(t)) in self.own.get(id(r), [])))
        targets = tuple(sorted(t.key for t in r.targets))
        return conds + hints + targets

    def time_sig(self, r: ItemRead) -> tuple:
        return tuple(sorted(_cond_value(c) for c in self.own_conds(r) if c["kind"] == "period"))


def judge_group(g: Group, reads: dict, rel: Relations, ctx: Context, ov: dict | None = None) -> GroupResult:
    res = _judge_group(g, reads, rel, ctx, ov)
    for d in res.drafts:  # an X-byeol of another family still in a view tells no copy apart: not settled (check 7)
        if d.indicator is None:
            continue
        views = [v for r in d.items for v in r.view] + list(d.view)
        if any(a == "family" and foreign_byeol(t, d.indicator, rel) for a, _k, t in views):
            res.scope = True
    return res


def _value_axis_words(g: Group, head: Term, lowers: list[ItemRead], rel: Relations, ctx: Context) -> frozenset:
    """Condition IDs of X-byeol words in the complement of "N을 X별 A와 B로" that name an axis of a table holding N
    and every listed value: the axis the values are read on, so no own condition of a value (row 2 holds)."""
    if not g.complement:
        return frozenset()
    names = [t for r in lowers for t in ([r.indicator] + r.lower if r.kind == "indicator" else r.targets)]
    tables = set(head.tables)
    for t in names:
        tables &= set(t.tables)
    out = set()
    for r in lowers:
        for c in ctx.own_conds(r):
            value = str(c.get("value") or "")
            if c["kind"] == "basis" and value.endswith(BYEOL) and tables & rel.axis_tables([value]):
                out.add(c["id"])
    return frozenset(out)


def _own_sig(r: ItemRead, ctx: Context) -> tuple:
    """The own conditions of a lower item (``Context.sig``). With one list item there is no attribution plan (the
    modifier X of "X 중 B" is no attribution segment), so the conditions written inside the item count as its own
    ("X 중 B(2020년)"): finding 8."""
    sig = ctx.sig(r)
    if sig or ctx.own:
        return sig
    inner = []  # conditions inside the bracket part of a protected name ("가상상품수지(평잔)"; review r2-5)
    for t in r.item.toks:
        if t.kind == "name" and not t.negated:
            at = t.bracket_at  # the token layer's bracket (full-width forms too: finding 8)
            inner += [f"{c['kind']}:{key_of(c['quote'])}" for c in t.inner
                      if at >= 0 and c["span"][0] >= at and c["kind"] in SIG_KINDS]
    return tuple(sorted(f"{t.cond_kind}:{t.norm}" for t in r.item.toks
                        if t.kind == "cond" and t.cond_kind in SIG_KINDS and not t.negated)) \
        + tuple(sorted(inner)) + tuple(sorted(t.norm for t in r.item.toks if t.kind == "hint"))


def _own_genitive(items: list[ItemRead]) -> bool:
    """An item holds a target written with a genitive particle of its own ("B의 Y")."""
    return any(t.tok.suffix in GENITIVE for r in items for t in r.targets)


def _judge_group(g: Group, reads: dict, rel: Relations, ctx: Context, ov: dict | None = None) -> GroupResult:
    res = GroupResult(drafts=[], pairs=[])
    if g.designation:
        return res
    items = [reads[id(it)] for it in g.items]
    mods = [reads[id(it)] for it in g.modifier]
    shared_targets: list[Term] = []
    context: Term | None = None
    inds = [r for r in items if r.kind == "indicator"]
    related = g.relation is not None  # a relation word reads the items apart (A-1: before row 2)
    blocked_row7 = False

    # ---- modifier "X의 L" / "X 중 L"
    if mods:
        x_inds = [m for m in mods if m.kind == "indicator"]
        x_tgts = [m for m in mods if m.kind == "target"]
        if len(x_tgts) == len(mods):
            shared_targets = [t for m in mods for t in m.targets]
            res.modifier_used = True
            # "A의 X와 B의 Y": a later item with a genitive target of its own pairs each "N의" with the indicator
            # right after it, so the modifier's targets are the first item's only
            first_only = bool(inds) and _own_genitive(items[1:])
            if first_only and any(r.kind != "indicator" and _own_genitive([r]) for r in items[1:]):
                res.scope = True  # a later "B의 X" with no indicator: what B and X belong to is not settled
            if first_only and items[0] is inds[0]:
                inds[0].targets = shared_targets + [t for t in inds[0].targets if t not in shared_targets]
                shared_targets = []
            elif first_only:
                res.scope = True  # the item right after "N의" is no indicator: where N belongs is not settled
            elif len(inds) >= 2:
                for t in shared_targets:
                    if not all(t.tables & r.indicator.tables for r in inds):
                        res.scope = True
        elif len(x_inds) == 1 and len(mods) == 1 and related and x_inds[0].indicator.series and inds \
                and all(rel.only_in_series(t, x_inds[0].indicator) for r in inds for t in [r.indicator] + r.lower):
            context = x_inds[0].indicator  # "표X의 A 대비 B 비율": the table named before a relation is its context
            res.modifier_used = True
        elif len(x_inds) == 1 and len(mods) == 1 and related and g.relation in SCOPE_RELATIONS and inds \
                and len(inds) == len(items) and all(rel.below(t, x_inds[0].indicator) for r in inds
                                                    for t in [r.indicator] + r.lower) \
                and not any(_own_sig(r, ctx) for r in items) and not any(r.targets for r in items):
            # plan 05 A: "X 중 B가 차지하는 비중" with B under X in every common table: the row 2 check passes, so one
            # requirement over X broken down by B, with the share relation on it (whole X, part B)
            h = x_inds[0].indicator
            d = Draft(group=g, items=mods + items, indicator=h, context=x_inds[0].context,
                      lower=[t for r in inds for t in [r.indicator] + r.lower], targets=list(x_inds[0].targets),
                      view=list(x_inds[0].view), axes=list(x_inds[0].axes) + [t for r in items for t in r.axes],
                      row=2, share=True)
            res.drafts.append(d)
            res.modifier_used = True
            for a, b in zip([x_inds[0]] + items, items):
                res.pairs.append((a, b, 2, "attach"))
            return res
        elif len(x_inds) == 1 and len(mods) == 1 and related:
            items = [x_inds[0]] + items  # "A 중 B가 차지하는 비중": a relation of two requirements (A-1)
            inds = [x_inds[0]] + inds
            res.modifier_used = True
        elif len(x_inds) == 1 and len(mods) == 1:
            h = x_inds[0].indicator
            if inds and len(inds) == len(items) and not related \
                    and all(rel.below(t, h) for r in inds for t in [r.indicator] + r.lower):
                if not any(_own_sig(r, ctx) for r in items):
                    d = Draft(group=g, items=mods + items, indicator=h, context=x_inds[0].context,
                              lower=[r.indicator for r in inds] + [t for r in inds for t in r.lower],
                              targets=list(x_inds[0].targets), view=list(x_inds[0].view),
                              axes=list(x_inds[0].axes) + [t for r in items for t in r.axes], row=2)
                    res.drafts.append(d)
                    res.modifier_used = True
                    for a, b in zip([x_inds[0]] + items, items):
                        res.pairs.append((a, b, 2, "attach"))
                    return res
                context = h  # A-2: lower items with their own conditions: one requirement each, X as context
                res.modifier_used = True
                blocked_row7 = True
            elif inds and h.series and all(rel.in_series(r.indicator, h) for r in inds):
                context = h
                res.modifier_used = True
            elif inds and all(rel.other_axis(r.indicator, h) for r in inds):
                items = [x_inds[0]] + [_as_target(r) for r in items]
                inds = [x_inds[0]]
                res.modifier_used = True
            elif len(items) == 1:
                items[0].extra.append(h)
            else:
                res.scope = True
        else:
            res.scope = True

    # "X의 A, B와 C" with X a basis word read as A's target ("전산업의 X", reading.genitive_targets): like a
    # modifier, X belongs to every listed indicator unless a later item has a genitive target of its own (check 7
    # then decides by the tables of each indicator, row 4)
    lead = list(items[0].gen_targets) if items and items[0].kind == "indicator" else []
    if lead and len(inds) >= 2 and items[0] is inds[0] and not _own_genitive(items[1:]):
        for r in inds[1:]:
            r.targets = lead + [t for t in r.targets if t not in lead]

    # ---- row 3: read differently in different common tables
    for i, a in enumerate(inds):
        for b in inds[i + 1:]:
            if rel.mixed(a.indicator, b.indicator):
                res.mixed = True

    # ---- row 2: one requirement with breakdown
    tail = []
    core = items
    last_r = items[-1] if items else None
    if g.complement and len(items) >= 2 and last_r.kind == "condition" \
            and (last_r.axes or any(_byeol_tok(t) for _, _, t in last_r.view)) \
            and all(a == "frequency" or _byeol_tok(t) for a, _, t in last_r.view):
        core, tail = items[:-1], items[-1:]  # "A·B·C 용도별로 나눠 월별": the X-byeol of the complement goes to N
    if len(core) >= 2 and core[0].kind == "indicator" and not res.mixed and not related \
            and not _appositive_marks(g):  # plan 05 F: "A, 즉 B" names A twice, no breakdown
        head = core[0].indicator

        def lower_item(r: ItemRead) -> bool:
            if r.kind == "indicator":
                return all(rel.lower_of(t, head) for t in [r.indicator] + r.lower)
            if r.kind == "target":  # 10/19: values of target type under the head in every common table: breakdown
                return bool(r.targets) and not r.view and not r.axes and all(rel.lower_of(t, head) for t in r.targets)
            # an unnamed member of a middle-dot list ("가공·운송·여행·건설수지"): read with the others, not exact
            return r.kind == "none" and (r.item.after_dot or r.item.before_dot) and len(r.unknown) == 1 \
                and not r.names

        lowers = core[1:]
        values = [t for r in lowers if r.kind == "indicator" for t in [r.indicator] + r.lower]
        # plan 05 B(2): "X(A)과 X(B)" repeats the head name: parallel copies of X, no values under the table X
        in_table = head.series and values and all(rel.only_in_series(t, head) for t in values) \
            and not any(t.key == head.key for t in values)
        if in_table and any(rel.sibling(t, head) for t in values):
            # the table name is also a value the listed values are siblings of ("A하고 B를", A a table holding B and
            # B's sibling in it): a list of indicators, not values under a table; read in two ways, so undecided
            in_table = False
            res.mixed = True
        if in_table and not g.complement and all(r.kind == "indicator" for r in lowers):
            # the values under the table name are read with the table as context: rows 2 and 3 among them first
            sub = lowers[0].indicator
            if len(lowers) >= 2 and all(rel.lower_of(t, sub) for r in lowers[1:] for t in [r.indicator] + r.lower):
                if not any(ctx.sig(r) for r in lowers) and not any(r.targets for r in lowers):
                    d = Draft(group=g, items=list(items), indicator=sub, context=head,
                              lower=lowers[0].lower + [t for r in lowers[1:] for t in [r.indicator] + r.lower],
                              targets=list(lowers[0].targets) + shared_targets, view=list(lowers[0].view),
                              axes=list(lowers[0].axes), row=2)
                    res.drafts.append(d)
                    for a, b in zip(items, items[1:]):
                        res.pairs.append((a, b, 2, "attach"))
                    return res
                blocked_row7 = True
            elif any(rel.ranked(a.indicator, b.indicator) for i, a in enumerate(lowers) for b in lowers[i + 1:]):
                res.mixed = True  # values under the table name read upper/lower in another order: undecided
            for r in lowers:
                d = Draft(group=g, items=[core[0], r] if r is lowers[0] else [r], indicator=r.indicator,
                          context=head, targets=list(r.targets), lower=list(r.lower), axes=list(r.axes),
                          view=list(r.view), row=8 if len(lowers) > 1 else None)
                res.drafts.append(d)
            for a, b in zip(items, items[1:]):
                res.pairs.append((a, b, 8, "attach" if a is core[0] else "split"))
            # finding 9: the values listed under one table name keep the same-table group hint (as row 8 lists do)
            under = [d for d in res.drafts if d.indicator is not None]
            if len(under) >= 2 and not res.mixed and set.intersection(*[set(d.indicator.tables) for d in under]):
                res.hints.append(under)
            return res
        if all(lower_item(r) for r in lowers) and any(r.kind in ("indicator", "target") for r in lowers):
            # own conditions block row 2 (a target-type lower item's sig ends with its own target keys); in "N을 X별
            # A와 B로" an X-byeol word naming an axis of a table holding N and the values is that breakdown's axis
            axis = _value_axis_words(g, head, lowers, rel, ctx)
            if not any(ctx.sig(r, axis)[:len(ctx.sig(r, axis)) - len(r.targets)] if r.kind == "target"
                       else ctx.sig(r, axis) for r in lowers) \
                    and not any(r.targets for r in lowers if r.kind == "indicator"):
                unnamed = [t for r in lowers if r.kind == "none" for t in r.unknown]
                d = Draft(group=g, items=list(items), indicator=head, context=core[0].context or context,
                          lower=core[0].lower + [t for r in lowers for t in ([r.indicator] + r.lower if r.kind ==
                                                                              "indicator" else r.targets if r.kind ==
                                                                              "target" else [])],
                          unnamed_lower=unnamed,
                          targets=core[0].targets + shared_targets, view=list(core[0].view),
                          axes=list(core[0].axes) + [t for r in tail for t in r.axes]
                          + [t for r in tail for _, _, t in r.view if _byeol_tok(t)], row=2,
                          inferred=bool(in_table) or any(r.inferred_axes for r in items))
                res.drafts.append(d)
                for a, b in zip(items, items[1:]):
                    res.pairs.append((a, b, 2 if b not in tail else 5, "attach"))
                return res
            blocked_row7 = True  # row 2 blocked by own conditions or targets of a lower item (A-3)
        elif g.complement and any(r.kind == "indicator" for r in lowers):
            res.mixed = True  # "N을 A·B로" with A or B not read under N in every table: undecided
    if any(r.dotlist for r in items) and not res.drafts:
        res.mixed = True  # a middle-dot list not read under a head: undecided

    # ---- rows 4-10 in order
    drafts: list[Draft] = []
    last: Draft | None = None
    pending: list[ItemRead] = []
    pending_targets: list[Term] = []
    sources: dict[int, list[ItemRead]] = {}
    prev: ItemRead | None = None
    for r in items:
        row, verdict = None, "split"
        if r.kind == "indicator":
            d = Draft(group=g, items=[r], indicator=r.indicator, context=r.context or context,
                      targets=list(r.targets), lower=list(r.lower), axes=list(r.axes), view=list(r.view),
                      inferred=r.inferred_axes)
            d.row = 2 if r.lower else 5 if r.axes else 4 if r.targets else None
            axes_before = False
            if pending and (last is None or last.indicator is None):
                # a divided list before the phrase it divides ("A와 B를 나눈 N"): breakdown axes of N (row 5)
                axes_only = [p for p in pending if p.item.axis_list and p.axes and not p.view]
                if axes_only:
                    d.axes = [t for p in axes_only for t in p.axes] + d.axes
                    d.inferred = True
                    d.row = d.row or 5
                    axes_before = prev in axes_only
                rest = [p for p in pending if p not in axes_only]
                if rest:
                    sources.setdefault(id(d), []).extend(rest)
                d.items = pending + d.items
                pending = []
            if prev is not None and prev.kind == "indicator":
                if r.indicator.key == prev.indicator.key:
                    row = 1 if ctx.sig(r) == ctx.sig(prev) and r.view_sig == prev.view_sig else 7
                elif res.mixed and rel.mixed(r.indicator, prev.indicator):
                    row = 3
                elif blocked_row7:
                    row = 7
                elif rel.together(r.indicator, prev.indicator):
                    row = 8
                else:
                    row = 9
                verdict = {1: "merge", 3: "undecided"}.get(row, "split")
                d.row = row
            elif prev is not None and prev.kind == "condition":
                row, verdict = (5, "attach") if axes_before else (6, "split")
            drafts.append(d)
            last = d
        elif r.kind == "condition":
            if last is not None and last.indicator is None:  # after an unnamed item: its own conditions
                row, verdict = 6, "attach"
                last.items.append(r)
                last.view += r.view
                last.axes += r.axes
            elif r.axes and all(a == "frequency" for a, _, _ in r.view):
                row, verdict = 5, "attach"  # X-byeol (with a frequency of its own): breakdown axes
                if last is not None:
                    last.axes += r.axes
                    last.row = last.row or 5
                    last.items.append(r)
                    last.inferred = last.inferred or r.inferred_axes
                else:
                    pending.append(r)
            else:
                row = 6
                if last is not None:
                    sources.setdefault(id(last), []).append(r)
                    last.items.append(r)
                    last.axes += r.axes
                else:
                    pending.append(r)
        elif r.kind == "target" and inds and not g.complement and not res.modifier_used \
                and _listed_with_indicators(r, items, rel):
            # plan 05 C: a target item listed with indicator items ("A 및 B", "A, B와 C") is their sibling, not
            # their target: its own requirement over that target (a broad one); where it belongs stays unsettled
            row = 4
            d = Draft(group=g, items=[r], targets=list(r.targets), broad=True, row=4, view=list(r.view),
                      axes=list(r.axes))
            drafts.append(d)
            last = d
            res.scope = True
        elif r.kind == "target":
            row, verdict = 4, "attach"
            pending_targets += r.targets
            if last is not None:
                last.items.append(r)
        elif r.kind == "none":
            row, verdict = 10, "undecided"
            d = Draft(group=g, items=[r], status="unresolved", row=10, view=list(r.view), axes=list(r.axes))
            if pending:
                d.items = pending + d.items
                d.view = [x for p in pending for x in p.view] + d.view
                pending = []
            drafts.append(d)
            last = d
        if prev is not None and row is not None:
            res.pairs.append((prev, r, row, verdict))
        prev = r
    held = pending if last is None else []
    if held and not (pending_targets or shared_targets):
        drafts.append(Draft(group=g, items=held, view=[x for r in held for x in r.view],
                            axes=[t for r in held for t in r.axes], status="unresolved", row=10))
        held = []

    # ---- row 6 copies: one per distinct item that carries a view
    out: list[Draft] = []
    for d in drafts:
        extra = sources.get(id(d))
        if not extra or d.indicator is None:
            out.append(d)
            continue
        base = next(r for r in d.items if r.kind == "indicator")
        extra_entries = [e for s in extra for e in s.view]
        # the indicator item is one of the copies only when it differs from a listed item by a value of one basis
        # slot or by its own calculation, period or unit ("가상GDP 명목과 실질", "명목 상위 5개와 명목 하위 5개");
        # otherwise its view qualifies every copy ("계절조정 가상GDP를 명목과 실질로", "가상기관 가상대출금을
        # 가상규모별로": no second value to copy by)
        base_alt = any(_exclusive(x, y, rel) for x in base.view for y in extra_entries) \
            or _own_differs(base, extra, ctx)
        shared: list = []
        if base_alt:  # values of other slots written before the listed value qualify every copy
            alt = [x for x in base.view if any(_exclusive(x, y, rel) for y in extra_entries)]
            first = min((x[2].start for x in alt), default=d.indicator.tok.start)
            shared = [x for x in base.view if x[2].start < first and x not in alt]
        elif base.view:
            shared = list(base.view)
        cands = ([base] if base_alt else []) + list(extra)
        shared += _tail_frequency(cands, extra)
        # plan 05 B: values listed bare ("X를 A와 B로") that are catalog target values are values of one axis: one
        # requirement over them as targets, "각각" or not (decision 2)
        targets = _target_values(cands, shared, base_alt, rel, ctx)
        if targets:
            d.targets = list(dict.fromkeys(d.targets + [t for _e, t in targets]))
            keep = {id(e[2]) for e, _t in targets}
            d.view = [e for e in _union(shared, [x for s in cands for x in s.view]) if id(e[2]) not in keep]
            d.sources, d.base, d.shared, d.row = list(cands), base, list(shared), 4
            out.append(d)
            continue
        # one listed item holding two values that stand in for each other ("X를 A, B, A로"): one copy per value
        origin: dict[int, ItemRead] = {}
        cands = [part for s in cands for part in _value_parts(s, rel, origin)]
        groups_: list[list[ItemRead]] = []
        keys: list[tuple] = []
        for s in cands:
            key = (_view_sig(_minus(s.view, shared)), ctx.sig(s))
            if key in keys:
                groups_[keys.index(key)].append(s)
            else:
                keys.append(key)
                groups_.append([s])
        views = [_minus(g_[0].view, shared) for g_ in groups_]
        by_period = len({ctx.time_sig(g_[0]) for g_ in groups_}) == len(groups_) and all(
            ctx.time_sig(g_[0]) for g_ in groups_)
        if len(groups_) <= 1:
            d.view = _union(shared, views[0]) if views else d.view
            d.row = 1 if len(cands) > 1 else 6
            d.sources = [origin.get(id(s), s) for g_ in groups_ for s in g_]
            d.base = base
            d.shared = list(shared)
            if not base_alt and base.view:  # "가상기관 가상대출금을 가상규모별로 나눠": the X-byeol is an axis
                d.axes += [t for s in d.sources if s.item.phrase == "complement" for a, _k, t in s.view
                           if _byeol_tok(t) and t not in d.axes]
            out.append(d)
        elif _varying_slots(views, rel) >= 2 and not by_period and ov and ov.get("reading") == "copy" \
                and _combo_copies(g, d, groups_, views, shared, base, origin) is not None:
            # override (model step): copies of the indicator per combination of the values of each basis slot
            out += _combo_copies(g, d, groups_, views, shared, base, origin)
        elif _varying_slots(views, rel) >= 2 and not by_period:
            d.view = _union(shared, [x for v in views for x in v])
            d.status, d.row = "unresolved", 6
            out.append(d)
        else:
            copies = []
            for g_ in groups_:
                c = Draft(group=g, items=list(dict.fromkeys([base] + [origin.get(id(s), s) for s in g_] + g_)),
                          indicator=d.indicator,
                          context=d.context, targets=list(d.targets), lower=list(d.lower), axes=list(d.axes),
                          view=_union(shared, g_[0].view), row=6, copy_of=d, sources=list(g_), base=base,
                          shared=list(shared))
                copies.append(c)
            out += copies
            if all(a not in ("family", "frequency") for v in views for a, _, _ in v) \
                    and _one_table(d.indicator, [t for v in views for _, _, t in v], rel):
                res.hints.append(copies)
    drafts = _each_axis(g, out)

    # ---- row 1 merges of identical requirements
    merged, seen = [], {}
    for d in drafts:
        if d.indicator is None:
            merged.append(d)
            continue
        sig = (d.indicator.key, _view_sig(d.view), tuple(sorted(t.key for t in d.targets)),
               tuple(sorted(t.key for t in d.lower)), tuple(sorted(t.norm for t in d.axes)),
               tuple(sorted(x for r in (d.sources if d.copy_of is not None else d.items) for x in ctx.sig(r))))
        if sig in seen:
            seen[sig].items += [r for r in d.items if r not in seen[sig].items]
            seen[sig].row = 1
            continue
        seen[sig] = d
        merged.append(d)
    drafts = merged

    drafts = _named_by_words(g, items, drafts, rel, related, res)

    # ---- row 4: targets of the group
    targets = shared_targets + pending_targets
    ind_drafts = [d for d in drafts if d.indicator is not None]
    for d in ind_drafts:  # a target no table of its indicator holds: where it belongs is not settled (check 7)
        if any(not (t.tables & d.indicator.tables) for t in d.targets + targets):
            res.scope = True
    if targets:
        if ind_drafts:
            for d in ind_drafts:
                d.targets = list(dict.fromkeys(d.targets + targets))
                if d.row is None:
                    d.row = 4
        else:
            drafts.insert(0, Draft(group=g, items=[r for r in items if r.kind == "target"] + mods + held,
                                   targets=list(dict.fromkeys(targets)), broad=True, row=4,
                                   view=[x for r in held for x in r.view], axes=[t for r in held for t in r.axes]))
    if g.unsure_periods:  # periods with words but no joiner between them: which part is which is not settled
        for d in drafts:
            d.status = "unresolved"
    drafts = _appositive_one(g, drafts, rel)
    # ---- row 8 groups
    inds_d = [d for d in drafts if d.indicator is not None and d.copy_of is None]
    if len(inds_d) >= 2 and not res.mixed:
        common = set.intersection(*[d.indicator.tables for d in inds_d])
        if common and any(p[2] == 8 for p in res.pairs):
            res.hints.append(inds_d)
    res.drafts = drafts
    return res


def _listed_with_indicators(r: ItemRead, items: list[ItemRead], rel: Relations) -> bool:
    """Plan 05 C: a target item listed with indicator items is their sibling, whatever the order of the list, unless
    the structure attaches it: it is coordinated with a target that modifies an indicator inside that indicator's own
    item ("A와 B X를" / "X를 A와 B로": B is written against X), or it is a lower value of a listed indicator (row
    2). A neighbour that is a target item of its own attaches nothing."""
    n = items.index(r)
    near = []  # (name at the joint, the item it stands in), past a run of target items in each direction
    for step, pick in ((-1, -1), (1, 0)):
        m = n + step
        while 0 <= m < len(items) and items[m].kind == "target":
            m += step
        if 0 <= m < len(items) and items[m].names:
            near.append((items[m].names[pick], items[m]))
    if not near:
        return False
    if any(t.rep == "target" and it.kind == "indicator" for t, it in near):
        return False
    heads = [x.indicator for x in items if x.kind == "indicator" and x.indicator is not None]
    return not any(rel.lower_of(t, h) for t in r.targets for h in heads)


def _named_by_words(g: Group, items: list[ItemRead], drafts: list[Draft], rel: Relations, related: bool,
                    res: GroupResult) -> list[Draft]:
    """"수입 A와 B의 물가": values A and B of one table whose name holds the words around them (the modifier before
    A and the head after "B의") are one requirement on that table, broken down by A and B. Read from words, so never
    certain (``inferred``)."""
    named = [d for d in drafts if d.indicator is not None and d.copy_of is None]
    if related or len(named) < 2 or len(named) != len(drafts) or rel.rel is None:
        return drafts
    if any(d.indicator.series for d in named) or len({id(d.items[0]) for d in named}) != len(named):
        return drafts
    last = named[-1].items[0]
    gen = named[-1].indicator.tok
    if gen.suffix != GENITIVE_OF:
        return drafts
    trail = []
    for t in last.item.toks:
        if t.start <= gen.start:
            continue
        if t.kind != "word":
            break
        if t.label == "unknown":
            trail.append(t)
    first = named[0].items[0]
    lead = []
    for t in first.item.toks:
        if t.kind == "name":
            break
        if t.kind == "qual" or (t.kind == "word" and t.label == "unknown") or (t.kind == "cond" and t.qual):
            lead.append(t)
    words = [t.key if t.kind == "qual" else t.qual if t.kind == "cond" else t.stem for t in lead + trail]
    if not lead or not trail or not words:
        return drafts
    tables = set.intersection(*[d.indicator.tables for d in named])
    hits = [tid for tid in sorted(tables) if all(w in key_of(rel.rel.table_names.get(tid, "")) for w in words)]
    if len(hits) != 1:
        return drafts
    merged = Draft(group=g, items=[r for d in named for r in d.items], lower=[d.indicator for d in named],
                   context_name=rel.rel.table_names[hits[0]], inferred=True, row=2)
    res.pairs = [(a, b, 2, "attach") if row in (8, 9) else (a, b, row, v) for a, b, row, v in res.pairs]
    return [merged]


def _byeol_tok(t: Tok) -> bool:
    return t.kind == "cond" and t.cond_kind == "basis" and str(t.cond.get("value", "")).endswith(BYEOL)


def _as_target(r: ItemRead) -> ItemRead:
    return ItemRead(item=r.item, kind="target", targets=[r.indicator] + r.targets, names=r.names,
                    first_name=r.first_name, last_name=r.last_name, unknown=r.unknown)


# ---------------------------------------------------------------- whole question


def _known(layer):
    if layer is None:
        return None
    names = layer.list_names()
    return lambda w: names.known(w) or names.in_titles(w)


def _request_at(reading: Reading):
    starts = {t.start for t in reading.toks if (t.kind == "word" and t.label in ("request", "clause_ends"))
              or (t.kind == "cond" and t.cond_kind == "display")
              or (t.kind == "cond" and t.cond_kind == "function" and t.cond["attrs"].get("function") == "each")}
    return lambda p: p in starts


def plan_attribution(reading: Reading, conditions: list[dict], items: list[ItemRead], layer) -> tuple:
    """(plan, own by item, hint IDs): the attribution over the list items (``attribution.scopes``)."""
    hint_ids = {id(t): f"h{n}" for n, t in enumerate((t for t in reading.toks if t.kind == "hint"), 1)}
    if len(items) < 2:
        return None, {}, hint_ids
    words = reading.words
    starts = [r.item.start for r in items]
    request_at, known = _request_at(reading), _known(layer)
    spans = [tuple(c["span"]) for c in conditions if c["kind"] in ATTRIBUTED]

    def in_condition(p: int) -> bool:
        return any(a <= p < b for a, b in spans)

    end = phrase_end(words, starts[-1], request_at, known, in_condition) or items[-1].item.end
    end = max(end, items[-1].item.start + 1)
    # a list member condition is placed at its last letter: its quote may start in the words before the item
    conds = [(c["id"], KIND_CHAR[c["kind"]], c["span"][1] - 1 if c["kind"] == "breakdown" else c["span"][0],
              c["span"][1], c["attrs"].get("axis") if c["kind"] == "basis" else None)
             for c in conditions if c["kind"] in ATTRIBUTED]
    conds += [(hid, "K", t.start, t.end, None) for t in reading.toks if t.kind == "hint"
              for hid in [hint_ids[id(t)]]]
    groups = [g for g in reading.groups if not g.designation and g.items]
    item_clause = [g.clause for g in reading.groups if not g.designation for _ in g.items]
    topics = topic_ranges(reading, groups)
    if len(groups) == 1 and groups[0].by_period:
        plan = period_scopes(words, starts, conds)
    elif topics is not None:  # plan 05 D: topic segments ("A는 …, B는 …") are requirement boundaries
        keys, ranges, topic_keys = topics
        plan = _clause_scopes(reading, words, items, keys, starts, end, conds, request_at, known, ranges=ranges,
                              topic_keys=topic_keys, in_condition=in_condition)
    elif len(set(item_clause)) >= 2:
        plan = _clause_scopes(reading, words, items, item_clause, starts, end, conds, request_at, known,
                              in_condition=in_condition)
    else:
        plan = scopes(words, starts, end, conds)
    own = {id(r): plan.own[k] + [cid for cid, ks in plan.shared.items() if k in ks] for k, r in enumerate(items)}
    return plan, own, hint_ids


def clause_ranges(reading: Reading) -> dict[int, tuple[int, int]]:
    """Clause index -> (start, end) in the question: each clause runs from the end of the clause before it."""
    ends: dict[int, int] = {}
    for g in reading.groups:
        ends[g.clause] = max(ends.get(g.clause, 0), g.clause_end)
    out, prev = {}, 0
    order = sorted(ends)
    for n, c in enumerate(order):
        out[c] = (prev, len(reading.raw) if n == len(order) - 1 else max(ends[c], prev))
        prev = out[c][1]
    return out


def topic_ranges(reading: Reading, groups: list[Group]) -> tuple | None:
    """Topic segments read as requirement boundaries (plan 05 D; plan 04 2-4 R3-03 for every topic head, indicator
    or target), clause by clause: in a clause whose item groups are all topic segments (two or more), segment n
    runs from its topic head (the first one) or its start to the next segment, the last one to the clause end;
    words before the first head (a leading period) stay with the clause. Other clauses keep their clause range.
    (item keys, ranges by key, topic keys), or None when no clause is cut into topic segments."""
    by_clause: dict[int, list[int]] = {}
    for n, g in enumerate(groups):
        by_clause.setdefault(g.clause, []).append(n)
    topical = {c for c, ns in by_clause.items()
               if len(ns) >= 2 and all(it.phrase == "topic" for n in ns for it in groups[n].items)}
    if not topical:
        return None
    clause_rng = clause_ranges(reading)
    keys, ranges, topic_keys = [], {}, set()
    for n, g in enumerate(groups):
        if g.clause in topical:
            ns = by_clause[g.clause]
            k = ("t", n)
            first = ns.index(n) == 0
            head = next((t.start for it in g.items for t in it.toks if t.kind in ("name", "qual")), g.start)
            nxt = next((groups[m].start for m in ns if m > n), None)
            ranges[k] = (head if first else g.start, nxt if nxt is not None else clause_rng[g.clause][1])
            topic_keys.add(k)
        else:
            k = ("c", g.clause)
            ranges[k] = clause_rng[g.clause]
        keys += [k] * len(g.items)
    return keys, ranges, topic_keys


def _clause_scopes(reading: Reading, words, items: list[ItemRead], item_clause: list[int], starts: list[int],
                   end: int, conds: list[tuple], request_at, known, ranges: dict | None = None,
                   topic_keys: set | None = None, in_condition=None) -> Plan:
    """Items in two or more item clauses (2-4 "항목 절"): the conditions of an item clause are read over the items of
    that clause only (the rest of the question blanked, positions kept); a condition common to the clause is common
    to its items (``Plan.shared``). Conditions outside every item clause keep the reading over the whole question.
    ``ranges`` replaces the clause ranges (topic segments, ``topic_ranges``)."""
    full = scopes(words, starts, end, conds)
    plan = Plan(segments=full.segments, end=full.end, own=[[] for _ in items])
    topic_keys = topic_keys or set()
    ranges = ranges or clause_ranges(reading)
    done: set[str] = set()
    for c in sorted(set(item_clause)):
        a, b = ranges[c]
        ks = [k for k, cl in enumerate(item_clause) if cl == c]
        sub = words.clip(a, b)  # the item clause read alone, positions kept
        sub_conds = [x for x in conds if x[2] is not None and a <= x[2] < b]
        if c in topic_keys and len(ks) == 1:  # a topic segment of one item: its conditions are that item's (R3-03)
            plan.own[ks[0]] += [x[0] for x in sub_conds]
            done |= {x[0] for x in sub_conds}
            continue
        last = items[ks[-1]]
        sub_end = phrase_end(sub, starts[ks[-1]], request_at, known, in_condition) or last.item.end
        sub_end = min(max(sub_end, last.item.start + 1), max(b, last.item.start + 1))
        part = scopes(sub, [starts[k] for k in ks], sub_end, sub_conds)
        for n, k in enumerate(ks):
            plan.own[k] += part.own[n]
        for cid in part.common:
            if len(ks) == 1:
                plan.own[ks[0]].append(cid)
            else:
                plan.shared[cid] = list(ks)
        plan.ambiguous += part.ambiguous
        done |= {x[0] for x in sub_conds}
    for x in conds:
        cid = x[0]
        if cid in done:
            continue
        if cid in full.common:
            plan.common.append(cid)
        elif cid in full.ambiguous:
            plan.ambiguous.append(cid)
        else:
            k = next((k for k, owned in enumerate(full.own) if cid in owned), None)
            (plan.own[k] if k is not None else plan.ambiguous).append(cid)
    return plan


# ---------------------------------------------------------------- overrides of the model step (``overrides``)


def _combo_copies(g: Group, d: Draft, groups_: list, views: list, shared: list, base: ItemRead,
                  origin: dict) -> list[Draft] | None:
    """Copies of one indicator per combination of values of two or more basis slots ("X를 명목과 실질, 원계열과
    계절조정으로": four copies), each value group holding one view value; None when a value has no slot."""
    by_slot: dict[str, list[int]] = {}
    for n, v in enumerate(views):
        if len(v) != 1:
            return None
        slot = _basis_slot(v[0]) or v[0][0]
        by_slot.setdefault(slot, []).append(n)
    if len(by_slot) < 2:
        return None
    copies = []
    for combo in itertools.product(*by_slot.values()):
        srcs = [s for n in combo for s in groups_[n]]
        view = _union(shared, [x for n in combo for x in views[n]])
        items = list(dict.fromkeys([base] + [origin.get(id(s), s) for s in srcs]))
        copies.append(Draft(group=g, items=items, indicator=d.indicator, context=d.context, targets=list(d.targets),
                            lower=list(d.lower), axes=list(d.axes), view=view, row=6, copy_of=d, sources=srcs,
                            base=base, shared=list(shared)))
    return copies


def _ordered_items(drafts: list[Draft]) -> list[ItemRead]:
    seen: dict[int, ItemRead] = {}
    for d in drafts:
        for r in d.items:
            seen.setdefault(id(r), r)
    return sorted(seen.values(), key=lambda r: r.item.start)


def _ov_one(g: Group, res: GroupResult, spec: dict, rel: Relations, conflicts: list) -> None:
    inds = [d for d in res.drafts if d.indicator is not None and d.copy_of is None]
    if spec.get("members"):  # the drafts the model merged (their indicator token starts)
        inds = [d for d in inds if d.indicator.tok.start in spec["members"]]
    head = next((d for d in inds if d.indicator.tok.start == spec.get("head")), inds[0] if inds else None)
    if head is None or len(inds) < 2:
        conflicts.append("mixed_reading")
        return
    others = [d for d in inds if d is not head]
    new = Draft(group=g, items=_ordered_items(inds), indicator=head.indicator, context=head.context,
                lower=list(head.lower) + [t for o in others for t in [o.indicator] + list(o.lower)],
                targets=list(dict.fromkeys(list(head.targets) + [t for o in others for t in o.targets])),
                view=list(head.view), axes=list(head.axes) + [t for o in others for t in o.axes], row=2,
                inferred=head.inferred or any(o.inferred for o in others))
    at = res.drafts.index(inds[0])
    res.drafts = [x for x in res.drafts[:at] if x not in inds] + [new] + [x for x in res.drafts[at:] if x not in inds]
    ids = {id(r) for r in new.items}
    res.pairs = [(a, b, 2, "attach") if id(a) in ids and id(b) in ids else (a, b, row, v)
                 for a, b, row, v in res.pairs]
    res.hints = [grp for grp in res.hints if not any(d in others for d in grp)]
    res.mixed = False


def _ov_split(g: Group, res: GroupResult, rel: Relations, conflicts: list) -> None:
    d = next((x for x in res.drafts if x.indicator is not None and x.lower and not x.share), None)
    if d is None:
        conflicts.append("row_2_or_7")
        return
    out = []
    taken: set[int] = set()
    for term in d.lower:
        r = next((r for r in d.items if r.indicator is not None and r.indicator.tok is term.tok), None)
        if r is None:  # a lower value inside the head's own item: no item of its own to stand on
            conflicts.append("row_2_or_7")
            return
        taken.add(id(r))
        out.append(Draft(group=g, items=[r], indicator=term, context=r.context, targets=list(d.targets),
                         lower=list(r.lower), axes=list(r.axes), view=list(r.view),
                         row=8 if rel.together(term, d.indicator) else 9))
    head = dataclasses.replace(d, items=[r for r in d.items if id(r) not in taken], lower=[], row=None)
    at = res.drafts.index(d)
    res.drafts[at:at + 1] = [head] + out
    res.pairs = [(a, b, 8 if row == 2 else row, "split" if row == 2 else v) for a, b, row, v in res.pairs]


def _ov_scope(g: Group, res: GroupResult, reads: dict, how: str, spans: list, conflicts: list) -> None:
    keys = {tuple(s) for s in spans}
    xs = []
    for r in [reads[id(it)] for it in g.modifier + g.items if id(it) in reads]:
        xs += [t for t in r.targets if (t.tok.start, t.tok.end) in keys and t not in xs]
    inds = [d for d in res.drafts if d.indicator is not None]
    if not xs or not inds:
        conflicts.append("scope_ambiguous")
        return
    sibling = [d for d in res.drafts if d.broad and d.indicator is None and set(d.targets) <= set(xs)]
    for s in sibling:
        res.drafts.remove(s)
        if how in ("all", "first"):
            inds[0].items = _ordered_items([inds[0], s])
    for k, d in enumerate(inds):
        if how == "all" or (how == "first" and k == 0):
            d.targets = list(dict.fromkeys(list(d.targets) + xs))
        else:
            d.targets = [t for t in d.targets if t not in xs]
    if how == "separate":
        holders = [reads[id(it)] for it in g.modifier + g.items if id(it) in reads
                   and any(t in xs for t in reads[id(it)].targets)]
        new = Draft(group=g, items=holders + [r for s in sibling for r in s.items if r not in holders],
                    targets=list(xs), broad=True, row=4)
        res.drafts.insert(res.drafts.index(inds[0]), new)
    res.scope = False


def apply_overrides(reading: Reading, results: list, reads: dict, rel: Relations, ov: Overrides, resolved: set,
                    conflicts: list) -> None:
    """The model step's decisions on the drafts of each group (``overrides``): readings, scopes, appositives and
    attached names. A decision the drafts cannot take is a conflict (the check it fails)."""
    for g, res in zip(reading.groups, results):
        spec = ov.groups.get(g.start) or {}
        if spec.get("reading") == "one":
            _ov_one(g, res, spec, rel, conflicts)
        elif spec.get("reading") == "split":
            _ov_split(g, res, rel, conflicts)
        if spec.get("scope"):
            _ov_scope(g, res, reads, spec["scope"], spec.get("x") or [], conflicts)
    drafts = [d for r in results for d in r.drafts]
    by_start = {d.indicator.tok.start: d for d in drafts if d.indicator is not None}
    for start, spec in ov.drafts.items():
        d = by_start.get(start)
        if d is None:
            conflicts.append("row_2_or_7")
            continue
        if spec.get("appositive") in ("a", "b") and d.alternates:
            if spec["appositive"] == "b":
                alt = next((t for t in d.alternates if t.tok.start == spec["alt"]), None) if "alt" in spec \
                    else d.alternates[0]
                if alt is None:  # the chosen candidate is not one of this draft's: the choice cannot apply
                    conflicts.append("row_2_or_7")
                    continue
                d.indicator, d.alternates = alt, [d.indicator] + [t for t in d.alternates if t is not alt]
        elif spec.get("gloss") in ("a", "b", "keep"):
            b = next((t for t in d.lower if t.tok.start == spec.get("b")), None)
            if b is None:
                conflicts.append("row_2_or_7")
                continue
            if spec["gloss"] in ("a", "b"):
                d.lower = [t for t in d.lower if t is not b]
                if spec["gloss"] == "b":
                    d.indicator, d.alternates = b, [d.indicator] + list(d.alternates)
                else:
                    d.alternates = list(d.alternates) + [b]
        else:
            continue
        if d.status == "unresolved" and not d.group.unsure_periods:
            d.status = "ok"  # the two names are read as one indicator
        resolved.add(d.group.start)
    names = {(t.start, t.end): t for t in reading.toks if t.kind == "name"}
    for span, start, role in ov.attach:
        tok, d = names.get(tuple(span)), by_start.get(start)
        if tok is None or d is None:
            conflicts.append("unused_name")
            continue
        term = term_of(tok, rel.layer)
        if any((t.tok.start, t.tok.end) == (tok.start, tok.end) for t in list(d.targets) + list(d.lower)):
            continue  # already a field of the draft
        if role == "breakdown":
            if not rel.lower_of(term, d.indicator):
                conflicts.append("mixed_reading")
            d.lower = list(d.lower) + [term]
        else:
            if not set(term.tables) & set(d.indicator.tables):
                conflicts.append("scope_ambiguous")
            d.targets = list(d.targets) + [term]


def _read_groups(reading: Reading, conditions: list[dict], layer, rel: Relations, groups_ov: dict) -> tuple:
    """Items read with their terms, the attribution plan and the judgement table over every group."""
    reads = {}
    for g in reading.groups:
        for it in g.modifier + g.items:
            reads[id(it)] = read_item(it, layer, rel)
    attach_foreign_byeol(reading.groups, reads, rel)
    listed_target_peers(reading.groups, reads, layer, rel)  # plan 09 (10/19): listed values of one axis, one role
    items = [reads[id(it)] for g in reading.groups if not g.designation for it in g.items]
    plan, own, hint_ids = plan_attribution(reading, conditions, items, layer)
    by_id = {c["id"]: c for c in conditions}
    negated = {t.cond["id"] for t in reading.negated if t.cond is not None}
    ctx = Context(items, own, by_id, hint_ids, negated)
    results = [judge_group(g, reads, rel, ctx, groups_ov.get(g.start)) for g in reading.groups]
    return reads, items, plan, hint_ids, ctx, results


def _on_indicator_axis_with(term: Term, others: list[Term], rel: Relations) -> bool:
    """The name is a value of an indicator-role axis in a catalog table that also holds every other name."""
    r = rel.rel
    tables = {t for t, obj, _item in r.occurrences(term.key)
              if obj is not None and r.axis_roles.get((t, obj)) == "indicator"}
    return any(all(t in (set(o.tables) | r.tables_of(o.key)) for o in others) for t in tables)


def _pair_heads(reading: Reading, reads: dict, rel: Relations) -> list[Tok]:
    """"A의 X와 B의 Y" (each "N의" pairs with the item right after it) where X reads as a target value only (a total
    header, say): X is the head noun of "A의", so when the catalog also holds X on an indicator axis of a table holding
    A it is read as an indicator (the role rule's test), like Y after "B의"."""
    if rel.rel is None:
        return []
    out = []
    for g in reading.groups:
        mods = [reads[id(it)] for it in g.modifier]
        items = [reads[id(it)] for it in g.items]
        if not mods or not items or any(m.kind != "target" for m in mods) or not _own_genitive(items[1:]):
            continue
        for n, r in enumerate(items):
            if r.kind != "target":
                continue
            owners = [t for m in mods for t in m.targets] if n == 0 else \
                [t for t in r.targets if t.tok.suffix in GENITIVE]
            rest = [t for t in r.targets if t not in owners]
            if owners and len(rest) == 1 and _on_indicator_axis_with(rest[0], owners, rel):
                out.append(rest[0].tok)
    return out


def _indicator_by_role(results: list, rel: Relations) -> list[Tok]:
    """Role rule (user decision 10/19): a requirement with targets and no indicator whose one target name is also a
    value of an indicator axis in a catalog table holding its other targets reads that name as its indicator (the
    others stay targets): "기관의 X" with X on an indicator axis of a table of the institution. Two or more such names:
    no rule (the requirement stays as read; check 9 keeps it undecided). Returns the name tokens to read again."""
    if rel.rel is None:
        return []
    out = []
    for res in results:
        for d in res.drafts:
            if d.indicator is not None or not d.targets:
                continue
            found = [t for t in d.targets if _on_indicator_axis_with(t, [o for o in d.targets if o is not t], rel)]
            if len(found) == 1:
                out.append(found[0].tok)
    return out


def _read_as_names(drafts: list[Draft], reads: dict) -> set[str]:
    """Condition IDs whose words are part of a requirement's catalog name, so no condition of their own: a condition
    inside an indicator or context name ("원화" of "예금은행 원화예금"; the name token's span, brackets after it
    excluded), and a basis word an item read as a target name ("전산업의 X")."""
    out = {cid for r in reads.values() for cid in r.taken}
    for d in drafts:
        for term in (d.indicator, d.context):
            if term is None:
                continue
            tok = term.tok
            at = tok.bracket_at  # bracket words of a name ("X(원계열, 명목)") stay conditions (qualifiers)
            end = at if at >= 0 else tok.end
            # only basis values of a value slot (currency, price, ...): family qualifiers and X-byeol words inside a
            # name tell its table family apart and stay conditions (selection evidence)
            out |= {c["id"] for c in tok.inner if tok.start <= c["span"][0] and c["span"][1] <= end
                    and c["kind"] == "basis" and c["attrs"].get("axis") not in DICT_FREE_AXES
                    and not str(c.get("value") or "").endswith(BYEOL)}
    return out


def owner_view(d: Draft) -> tuple:
    """The view values a copy of one indicator is told apart by (the chosen owner of a condition, model step)."""
    return tuple(sorted(k for _a, k, _t in d.view))


def _owner_of(drafts: list[Draft], where) -> Draft | None:
    """The draft a model attribution chose: (indicator token start, place among the drafts on that token, view
    values); it must be that very requirement (same view values), else None (the choice cannot apply)."""
    if not isinstance(where, (tuple, list)) or len(where) != 3:
        return None
    start, n, view = where
    same = [d for d in drafts if d.indicator is not None and d.indicator.tok.start == start]
    if n < len(same) and owner_view(same[n]) == tuple(view):
        return same[n]
    hits = [d for d in same if owner_view(d) == tuple(view)]
    return hits[0] if len(hits) == 1 else None


def judge(reading: Reading, conditions: list[dict], layer: CatalogLayer | None,
          overrides: Overrides | None = None) -> Judged:
    """The judgement table over every group. ``overrides`` (the model step's decisions, ``overrides``) fixes
    chosen names, readings, scopes, appositives, attached names and condition places; None is the rule path. The
    terms fixed on the reading's (cached) name tokens are restored afterwards: a later run is not affected."""
    saved = [(t, t.__dict__.get("_term")) for t in reading.toks]
    try:
        return _judge(reading, conditions, layer, overrides)
    finally:
        for t, term in saved:
            if term is None:
                t.__dict__.pop("_term", None)
            else:
                t._term = term


def _judge(reading: Reading, conditions: list[dict], layer: CatalogLayer | None,
           overrides: Overrides | None = None) -> Judged:
    rel = Relations(layer)
    apply_terms(reading, layer, overrides)
    groups_ov = overrides.groups if overrides is not None else {}
    reads, items, plan, hint_ids, ctx, results = _read_groups(reading, conditions, layer, rel, groups_ov)
    role = _pair_heads(reading, reads, rel) + _indicator_by_role(results, rel)
    if role:  # role rule (user decision 10/19): read the groups again with those names as indicators
        for tok in role:
            term = as_indicator(tok, layer)
            if term is not None:
                tok._term = term
        reads, items, plan, hint_ids, ctx, results = _read_groups(reading, conditions, layer, rel, groups_ov)
    resolved: set = set()
    conflicts: list = []
    if overrides is not None:
        apply_overrides(reading, results, reads, rel, overrides, resolved, conflicts)
    drafts = [d for r in results for d in r.drafts]
    for n, d in enumerate(drafts, 1):
        d.id = f"d{n}"
    pairs = [p for r in results for p in r.pairs]
    place = settle_places(reading, conditions, items, plan, drafts, ctx)
    if overrides is not None:
        for cid, where in overrides.places.items():
            if where == "common":
                place[cid] = "common"
                continue
            owner = _owner_of(drafts, where)
            if owner is not None:
                place[cid] = [owner.id]
            else:
                conflicts.append("ambiguous_attribution")  # the chosen requirement is not one of the new drafts
    for cid in _read_as_names(drafts, reads):
        place.pop(cid, None)
    for t in reading.toks:  # the words inside an excluded or negated name are part of it, no condition of any
        if t.kind == "name" and (t.excluded or t.negated):  # requirement ("가상수출 전망은 빼줘": review of plan 08)
            cut = t.bracket_at if t.bracket_at >= 0 else t.end
            for c in t.inner:
                if c["span"][1] <= cut:
                    place.pop(c["id"], None)
    out = Judged(drafts=drafts, pairs=pairs, groups=reading.groups, reads=reads, results=results, items=items,
                 plan=plan, place=place, hint_ids=hint_ids,
                 designation_conflict=designation_conflict(reading, conditions), resolved=resolved,
                 override_conflicts=conflicts)
    # split grounding (user decision 10/18): every requirement of a split needs a grounding table
    out.split_check = verify_splits(out, conditions, rel) if split_grounding() else None
    return out


SLOT_KINDS = ("period", "frequency", "basis", "calc", "unit")


def _slot(c: dict) -> tuple:
    return (c["kind"], c["attrs"].get("axis") if c["kind"] == "basis" else None)


def designation_ranges(reading: Reading) -> list[tuple[int, int]]:
    """Spans of the designation groups: each runs to the next group of its clause, the last one to its clause end."""
    out = []
    for n, g in enumerate(reading.groups):
        if not g.designation:
            continue
        nxt = next((h.start for h in reading.groups[n + 1:] if h.clause == g.clause), None)
        out.append((g.start, nxt if nxt is not None else max(g.clause_end, g.end)))
    return out


def designation_conflict(reading: Reading, conditions: list[dict]) -> bool:
    """A designation clause only points at items of an earlier clause (2-4 rule 1). When it holds two conditions of
    one kind (one basis axis) or a condition of a kind the item clauses already have ("A를 보여주고 A는 명목과 실질로",
    "2020년 A를 보여주고 2021년 A는"), it asks for more than a designation: which requirements it makes is not settled
    (check 4)."""
    ranges = designation_ranges(reading)
    if not ranges:
        return False
    inside: dict[int, list[tuple]] = {}
    outside: set[tuple] = set()
    for c in conditions:
        if c["kind"] not in SLOT_KINDS:
            continue
        k = next((n for n, (a, b) in enumerate(ranges) if a <= c["span"][0] < b), None)
        if k is None:
            outside.add(_slot(c))
        else:
            inside.setdefault(k, []).append(_slot(c))
    for slots in inside.values():
        if len(slots) != len(set(slots)) or set(slots) & outside:
            return True
    return False


def _condition_only(r: ItemRead) -> bool:
    return r.kind == "condition" and not r.names


def settle_places(reading: Reading, conditions: list[dict], items: list[ItemRead], plan: Plan | None,
                  drafts: list[Draft], ctx: Context) -> dict:
    """Condition (and hint) ID -> list of owner draft IDs, "common" or "ambiguous"."""
    # a negated period or frequency ("연간이 아니라 분기로": 연간) carries no polarity: it belongs to no requirement
    ids = [c["id"] for c in conditions if c["kind"] in ATTRIBUTED
           and not (c["kind"] in ("period", "frequency") and c["id"] in ctx.negated)] + list(ctx.hint_ids.values())
    if len(drafts) <= 1:  # one requirement: every condition is common to it
        return {cid: "common" for cid in ids}
    out: dict[str, object] = {}
    if plan is None:  # several requirements over one item (copies of one indicator)
        seg_of = {cid: items[0] for cid in ids} if items else {}
        common, ambiguous = [], []
    else:
        seg_of = {}
        for k, owned in enumerate(plan.own):
            for cid in owned:
                seg_of[cid] = items[k]
        common, ambiguous = plan.common, plan.ambiguous
    kinds = {c["id"]: c["kind"] for c in conditions}
    freq_items = {id(r) for r in items for c in ctx.own_conds(r) if c["kind"] == "frequency"}
    view_tok = {}
    for d in drafts:
        for r in d.sources:
            for _a, _k, t in r.view:
                if t.kind in ("cond", "qual") and t.cond is not None:
                    view_tok[t.cond["id"]] = r
    shared = plan.shared if plan is not None else {}
    shared_view: dict[str, list[Draft]] = {}  # a view value that qualifies every copy of row 6 (``Draft.shared``)
    for d in drafts:
        # with the X-byeol axes of the indicator item, which every copy carries
        toks = [t for _a, _k, t in d.shared] + (list(d.axes) if d.copy_of is not None and d.shared else [])
        for t in toks:
            if t.kind in ("cond", "qual") and t.cond is not None and d not in shared_view.get(t.cond["id"], []):
                shared_view.setdefault(t.cond["id"], []).append(d)
    own_axis: dict[str, list[Draft]] = {}  # plan 05 B(1): an X-byeol axis a "각각" copy stands for
    for d in drafts:
        for t in d.own_axes:
            if t.cond is not None:
                own_axis.setdefault(t.cond["id"], []).append(d)
    for cid in ids:
        if cid in own_axis:
            out[cid] = [d.id for d in drafts if d in own_axis[cid]]
            continue
        if cid in shared:  # common to the items of one clause: the requirements standing on those items
            owners = [d for k in shared[cid] for d in _owners(cid, items[k], drafts, view_tok, kinds, ctx)]
            out[cid] = [d.id for d in drafts if d in owners] or "ambiguous"
            continue
        if cid in common:
            out[cid] = "common"
            continue
        if cid in ambiguous:
            out[cid] = "ambiguous"
            continue
        if cid in shared_view:
            out[cid] = [d.id for d in drafts if d in shared_view[cid]]
            continue
        r = seg_of.get(cid)
        if r is None:
            out[cid] = "common" if kinds.get(cid) in ("period", "frequency") else "ambiguous"
            continue
        if kinds.get(cid) == "frequency" and _condition_only(r) and len(freq_items) == 1 and not r.view[1:] \
                and not any(a == "frequency" for d in drafts for s in d.sources if s is not r for a, _, _ in s.view):
            out[cid] = "common"  # a frequency standing alone after the list ("2020년 월별"): every requirement's
            continue
        owners = _owners(cid, r, drafts, view_tok, kinds, ctx)
        out[cid] = [d.id for d in owners] if owners else "ambiguous"
    return out


def _owners(cid: str, r: ItemRead, drafts: list[Draft], view_tok: dict, kinds: dict, ctx: Context) -> list[Draft]:
    holders = [d for d in drafts if r in d.items]
    copies = [d for d in holders if d.sources]
    if not copies:
        return holders
    if cid in view_tok:  # a view value: the copy made from that item
        return [d for d in copies if view_tok[cid] in d.sources]
    in_source = [d for d in copies if r in d.sources]
    if in_source and any(s is r for d in in_source for s in d.sources if s is not d.base):
        return in_source
    # a condition of the indicator item shared by its copies, unless another source has its own of the same kind
    kind = kinds.get(cid)
    others = [d for d in copies for s in d.sources if s is not r
              and any(c["kind"] == kind for c in ctx.own_conds(s))]
    if others and in_source:
        return in_source
    return copies
