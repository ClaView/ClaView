"""Catalog reading of names and list items (plan 04, 2-4 and 2-5); the judgement table is in ``table``.

- A name token is read through its catalog candidates: indicator (table name, indicator value, compound, or a value
  of an axis without a known role), target, or basis. It is exact when one candidate is the name itself, its
  footnote-free or numbering-free form, or its year-range-free form (decision 6); a bracket-free form is not exact.
  Mixed type (indicator and target candidates) and weak words (two Hangul letters or fewer) are not certain.
- An item is read with the conditions inside it: the indicator (a value inside a named table makes the table its
  context; an upper value with lower values inside one item takes them as breakdown; values on another axis are
  targets; the members of a middle-dot list inside one phrase are its breakdown), targets, the view (basis words,
  family qualifiers and frequencies: what copies of one indicator differ by), the X-byeol breakdown axes, unknown
  words. An item with unknown words and no name is a requirement without a term (row 10).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..catalog.relations import IND_TYPES, TGT_TYPES, relations_of
from .catalog_words import catalog_words
from .lexicon import BYEOL
from .phrases import BASIS_TAIL_NOUNS, CLOSE as CLOSE_MARKS, COMPLEMENT, DOTS, GENITIVE, MODIFIER_ENDINGS, Item, \
    Reading, Tok, key_of

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

ATTRIBUTED = ("period", "frequency", "basis", "calc", "unit", "exclude")
TIME_KINDS = ("period", "frequency")
WEAK_CHARS = 2
EXACT_VARIANTS = frozenset({"no_marks", "no_footnote", "no_year_range"})


# ---------------------------------------------------------------- names


@dataclass(eq=False)
class Term:
    tok: Tok
    key: str
    cands: list
    types: set
    rep: str  # indicator, target, basis
    exact: bool
    mixed: bool
    weak: bool
    unknown_type: bool
    tables: set
    series: bool
    family: str | None

    @property
    def quote(self) -> str:
        return self.tok.quote


def term_of(tok: Tok, layer: CatalogLayer | None) -> Term:
    cached = getattr(tok, "_term", None)
    if cached is not None:
        return cached
    if tok.alias:  # plan 05 H: a table head alias carries its own candidates
        cands = [dict(c, table_ids=list(c["table_ids"])) for c in tok.alias]
    else:
        cands = layer.name_spans().lookup(tok.lookup or tok.quote) if layer is not None else []
    types = {c["type"] for c in cands}
    ind, tgt = types & IND_TYPES, types & TGT_TYPES
    if types and types <= {"basis"}:
        rep = "basis"
    elif ind or "unknown" in types:
        rep = "indicator"
    elif tgt:
        rep = "target"
    elif "basis" in types:
        rep = "basis"
    else:
        rep = "indicator"
    exact = tok.lookup is None and not tok.alias and any(c.get("path") == "exact" or c.get("variant", "").split(":")[-1]
                                       in EXACT_VARIANTS or (c.get("path") == "dictionary"
                                                             and c.get("status") == "approved") for c in cands)
    hangul = tok.hangul_len
    weak = rep == "indicator" and 0 < hangul <= WEAK_CHARS and hangul == len(tok.key)
    rel = relations_of(layer) if layer is not None else None
    term = Term(tok=tok, key=tok.key, cands=cands, types=types, rep=rep, exact=exact,
                mixed=bool(ind or "unknown" in types) and bool(tgt), weak=weak,
                unknown_type=bool(types) and types <= {"unknown"},
                tables={t for c in cands for t in c.get("table_ids", [])},
                series=any(c.get("kind") == "table" for c in cands),
                family=rel.family_of_key(tok.key) if rel else None)
    tok._term = term
    return term


def name_kind(tok: Tok, layer: CatalogLayer | None) -> str:
    return term_of(tok, layer).rep if layer is not None else "unknown"


class Relations:
    """Readings of two names (catalog.relations) with the shortcuts the table uses."""

    def __init__(self, layer: CatalogLayer | None):
        self.rel = relations_of(layer) if layer is not None else None
        self.words = catalog_words(layer) if layer is not None else None
        self.catalog = layer.catalog if layer is not None else None
        self.layer = layer

    def swappable(self, a: str, b: str) -> bool:
        """Two words that stand in for each other in table names (one basis slot, plan 04 2-2 row 6)."""
        return self.words is not None and self.words.swappable(a, b)

    def axis_tables(self, words: list[str]) -> set[str]:
        return self.words.axis_tables(words) if self.words is not None else set()

    def readings(self, a: Term, b: Term) -> dict[str, set]:
        return self.rel.readings(a.key, b.key) if self.rel else {}

    def below(self, child: Term, head: Term) -> bool:
        """child is a lower value of head in every table that holds both."""
        rds = self.readings(child, head)
        return bool(rds) and all(r == {"below"} for r in rds.values())

    def lower_of(self, child: Term, head: Term) -> bool:
        """child is a lower value of head, or a value of the table head names, in every table holding both."""
        rds = self.readings(child, head)
        return bool(rds) and all(r and r <= {"below", "in_series"} for r in rds.values())

    def only_in_series(self, value: Term, series: Term) -> bool:
        """value is a value of the table series names, and lower than it in no table (R-9)."""
        rds = list(self.readings(value, series).values())
        return any("in_series" in r for r in rds) and not any(r & {"below", "above"} for r in rds)

    def sibling(self, a: Term, b: Term) -> bool:
        """a and b are values of one axis, neither above the other, in some table holding both."""
        return any("sibling" in r for r in self.readings(a, b).values())

    def in_series(self, value: Term, series: Term) -> bool:
        return any("in_series" in r for r in self.readings(value, series).values())

    def other_axis(self, a: Term, b: Term) -> bool:
        rds = self.readings(a, b)
        return bool(rds) and all(r <= {"other_axis", "in_series", "series_of"} and "other_axis" in r
                                 for r in rds.values())

    def mixed(self, a: Term, b: Term) -> bool:
        """Upper/lower in one common table and siblings in another (row 3, read in every common table)."""
        found = set()
        for r in list(self.readings(a, b).values()) + list(self.readings(b, a).values()):
            found |= r
        return "sibling" in found and bool(found & {"below", "above"})

    def qualifier_applies(self, qual_key: str, term: Term) -> bool:
        """A family qualifier applies to a term when it qualifies the term's family or a table carrying it holds the
        term (a value of the family's tables)."""
        if self.rel is None:
            return False
        return self.rel.qualifies(qual_key, term.family) or bool(self.rel.qualifier_tables.get(qual_key, set())
                                                                  & term.tables)

    def ranked(self, a: Term, b: Term) -> bool:
        """a and b are read upper/lower (either way) in some table that holds both."""
        found = set()
        for r in list(self.readings(a, b).values()) + list(self.readings(b, a).values()):
            found |= r
        return bool(found & {"below", "above"})

    def together(self, a: Term, b: Term) -> bool:
        return bool(a.tables & b.tables)


# ---------------------------------------------------------------- items


@dataclass(eq=False)
class ItemRead:
    item: Item
    kind: str  # indicator, target, condition, none, empty
    indicator: Term | None = None
    context: Term | None = None
    targets: list = field(default_factory=list)
    lower: list = field(default_factory=list)
    axes: list = field(default_factory=list)  # X-byeol tokens read as breakdown axes
    view: list = field(default_factory=list)  # (axis, key, token)
    extra: list = field(default_factory=list)  # names the item could not read (check 6)
    names: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    first_name: int | None = None
    last_name: int | None = None
    dotlist: bool = False
    inferred_axes: bool = False  # axes read from words of a divided list, not from catalog names
    fillers: list = field(default_factory=list)  # general nouns after a basis value ("명목 금액"): read as nothing
    modifiers: list = field(default_factory=list)  # adnominal verbs before a condition ("보유한 X별"): no indicator
    alternates: list = field(default_factory=list)  # plan 05 F: names after an appositive word inside the item
    taken: list = field(default_factory=list)  # condition IDs whose words the item reads as catalog names
    gen_targets: list = field(default_factory=list)  # targets read from a basis word before "의" (genitive_targets)

    @property
    def view_sig(self) -> tuple:
        return tuple(sorted((a, k) for a, k, _ in self.view))


def _is_byeol(tok: Tok) -> bool:
    return tok.kind == "cond" and tok.cond_kind == "basis" and tok.cond["attrs"].get("axis") == "classification" \
        and tok.cond["value"].endswith(BYEOL)


def byeol_value(tok: Tok) -> bool:
    """A basis condition whose value is an X-byeol word (classification or family axis)."""
    return tok.kind == "cond" and tok.cond_kind == "basis" and str(tok.cond.get("value", "")).endswith(BYEOL)


def foreign_byeol(tok: Tok, indicator: Term | None, rel: Relations) -> bool:
    """An X-byeol word that is a family qualifier which does not apply to ``indicator`` (2-2 rows 5 and 6: only an
    X-byeol of the indicator's own family splits it; another family's X-byeol is read as a breakdown axis)."""
    return indicator is not None and bool(tok.qual) and byeol_value(tok) \
        and not rel.qualifier_applies(tok.qual, indicator)


def attach_foreign_byeol(groups: list, reads: dict, rel: Relations) -> None:
    """Condition-only items read their X-byeol qualifiers against the indicator they attach to (the indicator item
    before them in the group, else the one after): a qualifier of another family becomes a breakdown axis."""
    for g in groups:
        rs = [reads[id(it)] for it in g.items]
        for n, r in enumerate(rs):
            if r.kind != "condition":
                continue
            ind = next((x.indicator for x in reversed(rs[:n]) if x.kind == "indicator"), None) \
                or next((x.indicator for x in rs[n + 1:] if x.kind == "indicator"), None)
            moved = [v for v in r.view if v[0] == "family" and foreign_byeol(v[2], ind, rel)]
            if moved:
                r.view = [v for v in r.view if v not in moved]
                r.axes += [t for _a, _k, t in moved]


def _choose(inds: list[Term], rel: Relations, related: bool = False) -> tuple[Term, Term | None, list, list, list]:
    """(indicator, context, lower, targets, extra) of the indicator names of one item."""
    if len(inds) == 1:
        return inds[0], None, [], [], []
    series = [t for t in inds if t.series]
    values = [t for t in inds if not t.series]
    inside = [v for v in values if any(rel.in_series(v, s) for s in series)]
    if series and inside:
        rest = [v for v in values if v not in inside]
        head = next(s for s in series if rel.in_series(inside[0], s))
        chosen, others = (inside[0], inside[1:]) if len(inside) >= 1 else (None, [])
        lower = [o for o in others if rel.below(o, chosen)]
        extra = [o for o in others if o not in lower] + rest + [s for s in series if s is not head]
        return chosen, head, lower, [], extra
    for u in inds:
        if not related and all(o is u or rel.below(o, u) for o in inds):
            return u, None, [o for o in inds if o is not u], [], []
    for c in inds:
        tl = [o for o in inds if o is not c and rel.other_axis(o, c)]
        if len(tl) == len(inds) - 1:
            return c, None, [], tl, []
    chosen = sorted(inds, key=lambda t: (len(t.tables) or 10 ** 6, -len(t.key)))[0]
    return chosen, None, [], [], [t for t in inds if t is not chosen]


def _all_byeol(t: Tok) -> bool:
    words = t.spaced_words
    return len(words) >= 2 and all(len(w) >= 3 and w.endswith(BYEOL) for w in words)


def _basis_head(t: Tok | None) -> bool:
    """A basis value token: a basis condition, a family qualifier, or a catalog name over a basis condition word
    ("원화기준" holding 원화)."""
    return t is not None and (t.kind == "qual" or (t.kind == "cond" and t.cond_kind == "basis")
                              or (t.kind == "name" and any(c.get("kind") == "basis" for c in t.inner)))


def basis_fillers(item: Item, layer: CatalogLayer | None) -> list[Tok]:
    """General nouns written right after a basis value ("명목 금액과 실질 금액", "잔액 기준 값"): they name no value
    of their own (plan 04 2-2 row 6: the basis values are what the copies differ by), so they are read as nothing.
    Names among them are basis-type catalog names only (a value named "금액" of a basis axis)."""
    out = []
    for prev, t in zip([None] + item.toks, item.toks):
        if not _basis_head(prev) or t.negated:
            continue
        if t.kind == "word" and t.label in ("basis_word", "ignore") \
                and t.stem in BASIS_TAIL_NOUNS:
            out.append(t)
        elif t.kind == "name" and t.key in BASIS_TAIL_NOUNS and layer is not None \
                and term_of(t, layer).rep == "basis":
            out.append(t)
    return out


def modifier_verbs(item: Item) -> list[Tok]:
    """Unknown words with an adnominal verb ending right before a condition, qualifier or name of the same item
    ("보유한 가상주체별로"): they modify what follows and name no indicator (they stay leftover words, check 5)."""
    out = []
    for t, nxt in zip(item.toks, item.toks[1:]):
        if t.kind != "word" or t.label != "unknown" or nxt.kind not in ("cond", "qual", "name"):
            continue
        if t.stem and t.ending in MODIFIER_ENDINGS and t.send == t.end:
            out.append(t)
    return out


def _without_ending(t: Tok) -> Tok:
    """A listed axis word of a dividing verb without the particle or ending written in it ("가상업과" -> 가상업; plan
    09, 10/19): a copy of the word token cut at its stem (the lexicon's endings, ``Tok.stem_end``); other tokens as
    they are (a name's particle is its suffix already)."""
    if t.kind != "word" or t.stem_end >= t.end or t.stem_end <= t.start:
        return t
    out = copy.copy(t)
    out.__dict__.pop("_term", None)
    out.quote, out.end = t.quote[:t.stem_end - t.start], t.stem_end
    return out


def _basis_name_term(t: Tok, layer) -> Term:
    """The catalog reading of a basis condition word as a name (its words as written)."""
    return term_of(Tok(kind="name", start=t.start, end=t.end, quote=t.quote, send=t.send, suffix=t.suffix,
                       key=t.norm), layer)


def _same_axis(a: Term, b: Term, rel: Relations) -> bool:
    """a and b are values of one classification axis of some table."""
    if rel.rel is None:
        return False
    places = {(t, obj) for t, obj, _i in rel.rel.occurrences(a.key) if obj is not None}
    return any((t, obj) in places for t, obj, _i in rel.rel.occurrences(b.key) if obj is not None)


def listed_target_peers(groups: list, reads: dict, layer: CatalogLayer | None, rel: Relations) -> None:
    """Values listed together of one axis keep one role (plan 09, 10/19): an item that is only a basis condition word
    ("전산업" read as a family qualifier) joined in a list to an item of target names, where that word is a catalog
    name of target type only (a target value or a total header) on the same axis as a listed target, is read as a
    target like them ("가상전산업과 가상비제조업으로 각각"); its condition is taken (no basis value of its own). A
    basis word alone, or listed with no target of its axis, keeps its reading."""
    if layer is None:
        return
    for g in groups:
        for rows in ([reads[id(it)] for it in g.modifier], [reads[id(it)] for it in g.items]):
            _peers_of_list(rows, layer, rel)


def _peers_of_list(rows: list, layer, rel: Relations) -> None:
    """``listed_target_peers`` over the items of one list (neighbours there are cut at a list joint)."""
    for n, r in enumerate(rows):
        toks = [t for t in r.item.toks if t.kind != "punct"]
        if r.kind != "condition" or len(toks) != 1 or toks[0].kind != "cond" or toks[0].cond_kind != "basis" \
                or toks[0].negated or toks[0].excluded:
            continue
        t = toks[0]
        peers = [x for x in rows[max(0, n - 1):n] + rows[n + 1:n + 2] if x.kind == "target" and x.targets]
        if not peers:
            continue
        term = _basis_name_term(t, layer)
        if not term.cands or not term.types <= TGT_TYPES:
            continue
        if not any(_same_axis(term, p, rel) for x in peers for p in x.targets):
            continue
        r.kind, r.targets = "target", [term]
        r.view = [v for v in r.view if v[2] is not t]
        r.taken.append(t.cond["id"])


def genitive_targets(item: Item, indicator: Term | None, layer: CatalogLayer | None) -> list[tuple[Tok, Term]]:
    """A basis condition word written "N의" right before the item's indicator name that is also a catalog name of
    target type only (a target value or total header), held by a table of the indicator ("전산업의 인건비"): read as
    the indicator's target there, not as a basis value."""
    if indicator is None or layer is None:
        return []
    out = []
    for t, nxt in zip(item.toks, item.toks[1:]):
        if t.kind != "cond" or t.cond_kind != "basis" or t.negated or t.excluded or t.suffix not in GENITIVE \
                or nxt is not indicator.tok:
            continue
        name = Tok(kind="name", start=t.start, end=t.end, quote=t.quote, send=t.send, suffix=t.suffix,
                   key=t.norm)
        term = term_of(name, layer)
        if term.cands and term.types <= TGT_TYPES and term.tables & indicator.tables:
            out.append((t, term))
    return out


def read_item(item: Item, layer: CatalogLayer | None, rel: Relations) -> ItemRead:
    fillers = basis_fillers(item, layer)
    modifiers = modifier_verbs(item)
    skip = {id(t) for t in fillers + modifiers}
    names = [t for t in item.toks if t.kind == "name" and not t.negated and not t.excluded and id(t) not in skip]
    # a name made only of X-byeol words ("연령별 지역별", a table name too) is read as breakdown axes (row 5)
    byeol_names = [t for t in names if _all_byeol(t)
                   and (item.phrase == "complement" or t.suffix in COMPLEMENT)]
    names = [t for t in names if t not in byeol_names]
    if item.axis_list:  # "나이와 사는 곳을 나눠": the members of a divided list are breakdown axes, not names
        out = ItemRead(item=item, kind="condition", names=[], inferred_axes=True)
        out.axes = [_without_ending(t) for t in item.toks if t.kind in ("name", "word", "qual") and not t.stripped
                    and t.label not in ("request", "ignore", "particle")]
        return out
    # plan 05 F: "A(즉 B)" inside one item: B is a name candidate of A, not a second name of the item
    mark = next((t for t in item.toks if t.kind == "word" and t.label == "appositive"), None)
    close = next((t.start for t in item.toks if mark is not None and t.start > mark.start
                  and t.kind == "punct" and t.label in CLOSE_MARKS), None)
    # the candidates run to the closing bracket; names after it keep their own roles (targets, ...)
    later = [t for t in names if mark is not None and t.start > mark.start and (close is None or t.start < close)]
    if later and len(later) < len(names):
        names = [t for t in names if t not in later]
    else:
        later = []
    terms = [term_of(t, layer) for t in names]
    out = ItemRead(item=item, kind="empty", names=terms)
    out.alternates = [term_of(t, layer) for t in later]
    out.axes.extend(byeol_names)
    out.inferred_axes = bool(byeol_names)
    if names:
        out.first_name, out.last_name = names[0].start, names[-1].end
    inds = [t for t in terms if t.rep == "indicator"]
    tgts = [t for t in terms if t.rep == "target"]
    basis_names = [t for t in terms if t.rep == "basis"]
    dotted = any(t.kind == "punct" and t.label in DOTS for t in item.toks)
    if inds and dotted and len(inds) >= 2:
        # "A·B·C X" kept as one phrase (2.3): the members are a breakdown list of the phrase
        out.indicator, out.lower, out.dotlist = inds[0], inds[1:], True
        out.targets = tgts
    elif inds:
        ind, ctx, lower, targets, extra = _choose(inds, rel, item.related)
        out.indicator, out.context, out.lower, out.extra = ind, ctx, lower, extra
        out.targets = targets + tgts
    else:
        out.targets = tgts
    family = out.indicator.family if out.indicator else None
    for t, term in genitive_targets(item, out.indicator, layer):  # "전산업의 X": a target, not a basis value
        skip.add(id(t))
        out.targets.append(term)
        out.gen_targets.append(term)
        out.taken.append(t.cond["id"])
    out.fillers, out.modifiers = fillers, modifiers
    for t in item.toks:
        if t.negated or id(t) in skip:
            continue
        if t.kind == "qual":
            out.view.append(("family", t.key, t))
        elif t.kind == "cond" and t.cond_kind == "basis":
            if foreign_byeol(t, out.indicator, rel):
                out.axes.append(t)  # an X-byeol qualifier of another family: a breakdown axis of this indicator
            elif _is_byeol(t) and not (t.qual and (family is None or relations_of(layer).qualifies(t.qual, family))):
                out.axes.append(t)
            elif t.qual:
                out.view.append(("family", t.qual, t))
            else:
                out.view.append((t.cond["attrs"].get("axis", "basis"), key_of(t.cond["value"]), t))
        elif t.kind == "cond" and t.cond_kind == "frequency":
            out.view.append(("frequency", t.cond["value"], t))  # "연간과 분기로": copies by frequency (A-5)
        elif t.kind == "word" and t.label == "basis_word":
            out.view.append(("basis_word", t.stem, t))
        elif t.kind == "word" and t.label == "unknown":
            out.unknown.append(t)
    for b in basis_names:
        out.view.append(("catalog_basis", b.key, b.tok))
    has_cond = any((t.kind == "cond" and t.cond_kind in ("calc", "unit")) or t.kind == "hint" for t in item.toks)
    if out.indicator is not None:
        out.kind = "indicator"
    elif out.targets:
        out.kind = "target"
    elif out.unknown and not names and not any(t.kind == "name" for t in fillers):
        out.kind = "none"  # unnamed words (with their own conditions): a requirement without a term (row 10)
    elif out.view or out.axes or has_cond:
        out.kind = "condition"
    elif out.unknown or any(t.kind == "name" for t in item.toks
                            if not t.negated and not t.excluded and id(t) not in skip):
        out.kind = "none"
    return out


def leftover_words(reading: Reading) -> list[str]:
    """Check 5: unknown words outside names, conditions, qualifiers, hints and the known word lists."""
    out = []
    for t in reading.toks:
        if t.kind != "word" or t.label != "unknown" or t.stripped:
            continue
        if not t.stem or t.numeric:
            continue
        out.append(t.quote)
    return out
