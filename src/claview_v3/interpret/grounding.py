"""Split grounding (user decision 10/18 14:39, structure first + a grounding table for every piece).

When the judgement table (2.5) reads two or more requirements (a list, copies, relation operands: every split), each
requirement needs grounding before the judgement can be certain:

- grounding = the requirement's indicator term is an exact catalog name (path exact, an exact variant, or an approved
  dictionary entry) of a table name or an indicator-type value, and at least one table holding it also holds the
  requirement's own target values and own basis values (catalog layer only; table selection is not called).

A requirement without grounding is a piece that may belong to another requirement. When every word of the piece reads
in a grounding table of another requirement as a target value, a classification axis name, a basis value or part of
the table name (context), the piece is attached to that requirement (target, breakdown axis, view, context) and the
split is read again. If every requirement left is grounded, the reattached reading is used and the judgement fails
``reattached`` (never certain); otherwise the drafts stay as they were and the judgement fails ``ungrounded_split``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..catalog.relations import IND_TYPES
from ..catalog.text import index_key
from .lexicon import BYEOL, NAME_SPLIT, NAME_SPLIT_FRONT
from .reading import EXACT_VARIANTS, Term

if TYPE_CHECKING:
    from .reading import Relations
    from .table import Draft, Judged


def _exact(c: dict) -> bool:
    return c.get("path") == "exact" or c.get("variant", "").split(":")[-1] in EXACT_VARIANTS \
        or (c.get("path") == "dictionary" and c.get("status") == "approved")


def indicator_tables(term: Term | None) -> set[str]:
    """Tables of the exact table-name or indicator-type candidates of an indicator term (empty: no grounding)."""
    if term is None or not term.exact or term.tok.lookup is not None:
        return set()
    if getattr(term, "role_read", False):  # a target name read as the indicator: its indicator-axis tables
        return set(term.tables)
    return {t for c in term.cands if _exact(c) and c.get("type") in IND_TYPES for t in c.get("table_ids", [])}


class Grounding:
    def __init__(self, j: Judged, conditions: list[dict], rel: Relations):
        self.j, self.rel = j, rel
        self.catalog = rel.catalog
        self.by_id = {c["id"]: c for c in conditions}

    # ------------------------------------------------------------ catalog
    def _table_key(self, t: str) -> str:
        return index_key(self.rel.rel.table_names.get(t, "")) if self.rel.rel else ""

    def holds_word(self, t: str, word: str) -> bool:
        """The table holds the word as a value name, inside its name, or as (part of) an axis name."""
        k = index_key(word)
        if not k or self.rel.rel is None:
            return False
        if t in self.rel.rel.tables_of(k) or k in self._table_key(t):
            return True
        axis = k.removesuffix(BYEOL)
        return len(axis) >= 2 and self.rel.words is not None \
            and any(axis in a for a in self.rel.words.axis_words.get(t, ()))

    def own_basis(self, d: Draft) -> list[dict]:
        return [self.by_id[cid] for cid, p in self.j.place.items()
                if isinstance(p, list) and d.id in p and cid in self.by_id and self.by_id[cid]["kind"] == "basis"]

    def name_parts(self, t: str, host: Draft | None) -> tuple[list[str], list[str]]:
        """(front words before the host's indicator word, bracket words) of the table name, as keys."""
        name = self.rel.rel.table_names.get(t, "") if self.rel.rel else ""
        cut = name.find("(")
        front_text, inside_text = (name[:cut], name[cut:]) if cut >= 0 else (name, "")
        front = [index_key(w) for w in NAME_SPLIT_FRONT.split(front_text) if w]
        key = host.indicator.key if host is not None and host.indicator is not None else None
        hit = next((n for n, w in enumerate(front) if key and key in w), None)
        if hit is not None:
            front = front[:hit]
        inside = [index_key(w) for w in NAME_SPLIT.split(inside_text) if w]
        return front, inside

    def name_words(self, t: str) -> list[str]:
        name = self.rel.rel.table_names.get(t, "") if self.rel.rel else ""
        return [index_key(w) for w in NAME_SPLIT.split(name) if w]

    def tables(self, d: Draft) -> set[str]:
        """Grounding tables of a requirement: tables of its indicator (its exact candidates, and tables whose name
        has the indicator as one of its words: "가상활동별 가상GDP 및 가상GNI") that hold its targets and own basis
        values."""
        cands = indicator_tables(d.indicator)
        if cands and self.rel.rel is not None:
            cands |= {t for t in self.rel.rel.table_names if d.indicator.key in self.name_words(t)}
        out = set()
        for t in cands:
            if not all(t in tg.tables for tg in d.targets):
                continue
            if not all(self.holds_word(t, str(c.get("value") or c["quote"])) or self.holds_word(t, c["quote"])
                       for c in self.own_basis(d)):
                continue
            out.add(t)
        return out

    # ------------------------------------------------------------ pieces
    @staticmethod
    def pieces(d: Draft) -> list:
        """The words of a requirement without grounding: its indicator term, else the names and unknown words of
        its items (conditions aside)."""
        if d.indicator is not None:
            return [d.indicator]
        out = []
        for r in d.items:
            for t in r.item.toks:
                if t.negated or t.stripped:
                    continue
                if t.kind == "name" or (t.kind == "word" and t.label in ("unknown", "basis_word")):
                    out.append(t)
        return out

    @staticmethod
    def piece_key(p) -> str:
        if isinstance(p, Term):
            return p.key
        return p.key if p.kind == "name" else p.stem

    def reading_in(self, p, t: str, host: Draft | None = None) -> str | None:
        """How a piece reads in table t: target, axis, basis or context; None when it does not."""
        k = self.piece_key(p)
        if len(k) < 2 or self.rel.rel is None:
            return None
        roles = {self.rel.rel.axis_roles.get((t, obj), "unknown")
                 for tid, obj, _item in self.rel.rel.occurrences(k) if tid == t and obj is not None}
        if "indicator" in roles or (roles and roles <= {"unknown"}):
            return None  # an indicator value of the host table is a requirement of its own (a sibling split)
        if roles:
            # plan 05 C: listed items are siblings; a listed target value is never made another item's target
            return "basis" if "basis" in roles else None
        axis = k.removesuffix(BYEOL)
        if self.rel.words is not None and len(axis) >= 2 \
                and any(axis in a for a in self.rel.words.axis_words.get(t, ())):
            return "axis"
        front, inside = self.name_parts(t, host)
        if any(w.startswith(k) for w in front):
            return "context"  # the start of a word of the table name before its indicator ("가상기관", "가상고정")
        if any(w.startswith(k) for w in inside):
            return "basis"  # the start of a bracket word of the table name (a qualifier)
        if any(index_key(it.name or "").startswith(k) for ax in self.axes_of(t) for it in ax.items):
            return "basis"  # the start of a value of a non-indicator axis ("가상계절" of "가상계절조정")
        return None

    def axes_of(self, t: str) -> list:
        table = self.catalog.tables.get(t) if self.catalog is not None else None
        if table is None:
            return []
        return [ax for ax in table.axes if self.rel.rel.axis_roles.get((t, ax.obj_id), "unknown")
                not in ("indicator", "unknown")]


def _attach(g: Draft, piece, how: str, t: str | None, rel: Relations) -> None:
    tok = piece.tok if isinstance(piece, Term) else piece
    if how == "target" and isinstance(piece, Term):
        g.targets.append(piece)
    elif how in ("axis", "target"):
        g.axes.append(tok)
    elif how == "basis":
        g.view.append(("catalog_basis" if tok.kind == "name" else "basis_word", Grounding.piece_key(piece), tok))
    elif isinstance(piece, Term) and g.context is None:
        g.context = piece
    elif g.context is None:  # the words themselves, not a table the reading picked: selection reads them
        stem = tok.stem if tok.kind == "word" else tok.quote
        g.context_name = f"{g.context_name} {stem}" if g.context_name else stem
    g.inferred = True


def verify_splits(j: Judged, conditions: list[dict], rel: Relations) -> str | None:
    """Check every requirement of a split for grounding; reattach pieces when that grounds every requirement.
    Returns "reattached", "ungrounded_split" or None (no split, or every requirement grounded). Changes ``j`` only
    when it returns "reattached"."""
    if len(j.drafts) < 2 or rel.rel is None:
        return None
    gr = Grounding(j, conditions, rel)
    tables = {d.id: gr.tables(d) for d in j.drafts}
    loose = [d for d in j.drafts if not tables[d.id]]
    if not loose:
        return None
    plan: list[tuple] = []  # (piece draft, host draft, [(piece, how, table)])
    for u in loose:
        # hosts of the same phrase group only: a clause or topic segment is a requirement boundary (plan 05 D)
        order = [d for d in reversed(j.drafts[:j.drafts.index(u)]) if tables[d.id] and d.group is u.group] \
            + [d for d in j.drafts[j.drafts.index(u) + 1:] if tables[d.id] and d.group is u.group]
        found = None
        for g in order:
            hits = []
            for t in sorted(tables[g.id]):
                reads = [(p, gr.reading_in(p, t, g)) for p in gr.pieces(u)]
                if reads and all(how for _p, how in reads):
                    hits.append([(p, how, t) for p, how in reads])
            if hits:
                found = (u, g, hits[0])
                break
        if found is None:
            # user decision 10/19 21:00: the split stays as read; the requirements without grounding are marked so
            # that table selection answers them "no table: condition not in the catalog" (partial answer)
            for d in loose:
                d.ungrounded = True
            return "ungrounded_split"
        plan.append(found)
    for u, g, reads in plan:
        for p, how, t in reads:
            _attach(g, p, how, t, rel)
        g.items += [r for r in u.items if r not in g.items]
        j.drafts.remove(u)
        for cid, p in list(j.place.items()):
            if isinstance(p, list) and u.id in p:
                j.place[cid] = list(dict.fromkeys(g.id if x == u.id else x for x in p))
    if len(j.drafts) == 1:
        j.place = {cid: ("common" if isinstance(p, list) else p) for cid, p in j.place.items()}
    return "reattached"  # every host is grounded in the table its pieces were read in
