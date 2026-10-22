"""Catalog name lookups for the list and exclude rules of find_conditions (detail 2.2), and the derived names of the
term index (plan 09 P1).

``CatalogNames``: port of the old Q5 helper ``_CatalogNames`` (src/claview/aflow/q_nodes.py:101-140, commit 55fd41b)
onto the new catalog: classification values (ITEM rows excluded, as before) and table names. Keys drop whitespace and
list middle dots; lookups return the value name as written in the catalog. Derived names are not part of it.

``derived_names`` / ``unmarked_key``: names a question writes for a table or value that the catalog does not hold as
written (the name without its subject word, a list-path node, a tail word, a bracket abbreviation; marks inside a
name). The term index (``index.py``) and the catalog relations (``relations.py``) both take them from here.

``joiner_keys`` (plan 14 I8): a catalog key with its and-word or list marks written as another list join (the and-word
left out or written as a joiner particle, a mark written as the and-word or a joiner particle); the term index adds them
the way it adds ``unmarked_key``.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .text import collapse, index_key, paren_front, strip_parens

if TYPE_CHECKING:
    from .loader import Catalog
    from .roles import AxisRoleTable

# List middle dots: "·", the NFKC form of "ㆍ" (U+119E) and "・" (U+30FB, kept by NFKC).
LIST_DOTS = "·ᆞ・"
_DOT = re.compile(f"[{LIST_DOTS}]")
_SPACE = re.compile(r"\s+")

# ---------------------------------------------------------------- derived names (plan 09 P1)

# Marks inside a name that a question writes as a space or leaves out ("가상A/가상B 지표" -> "가상A 가상B 지표").
NAME_MARKS = "/," + LIST_DOTS
_NAME_MARKS = re.compile(f"[{re.escape(NAME_MARKS)}]")
_LATIN_ABBR = re.compile(r"\(([A-Z][A-Z0-9]+)\)")  # a Latin abbreviation in brackets ("가상총소득(GNI)")
PATH_SEP = ">"
PATH_SKIP = 2  # the organisation and the top category of a catalog list path are no names of the tables below
PATH_NODE_MAX_TABLES = 30
DERIVED_MIN_CHARS = 2
TABLE_CORE, PATH_NODE, NO_TAIL, ABBR = "table_core", "path_node", "no_tail", "abbr"
DERIVED_VARIANTS = (TABLE_CORE, PATH_NODE, NO_TAIL, ABBR)


def unmarked_key(k: str) -> str | None:
    """An index key without the marks of ``NAME_MARKS`` ("자산/자본지표" -> 자산자본지표); None when it has none or
    nothing is left."""
    out = _NAME_MARKS.sub("", k or "")
    return out if out and out != k else None


def joiner_keys(k: str) -> list[str]:
    """Plan 14 I8: an index key as a question writes its list joins otherwise; uniform replacement, no products.

    - with the and-word (lexicon ``coordinator.and_word``): every and-word removed, every and-word as each list
      joiner particle (lexicon ``coordinator.list_joiners``) ("식료품및비주류음료" -> 식료품비주류음료, 식료품과비주류음료)
    - with marks of ``NAME_MARKS``: every mark as the and-word and as each list joiner particle (the mark-free form is
      ``unmarked_key``)
    - with both: every mark and every and-word removed
    Keys equal to ``k`` or empty are left out; the order is fixed and repeats are dropped."""
    from ..interpret.lexicon import AND_WORD, LIST_JOINER_PARTICLES

    k = k or ""
    and_key = index_key(AND_WORD)
    joiners = [index_key(p) for p in LIST_JOINER_PARTICLES]
    has_and = bool(and_key) and and_key in k
    has_marks = bool(_NAME_MARKS.search(k))
    out: list[str] = []
    if has_and:
        out += [k.replace(and_key, "")] + [k.replace(and_key, p) for p in joiners]
    if has_marks:
        out += [_NAME_MARKS.sub(and_key, k)] + [_NAME_MARKS.sub(p, k) for p in joiners]
    if has_and and has_marks:
        out.append(_NAME_MARKS.sub("", k).replace(and_key, ""))
    return [x for x in dict.fromkeys(out) if x and x != k]


@dataclass(frozen=True)
class DerivedName:
    """A name derived from a catalog name (plan 09 P1). ``table_id`` with ``obj_id`` None: a table name; with an
    ``obj_id``: a value (abbr of a value name keeps its axis and value)."""

    name: str
    key: str
    variant: str
    table_id: str
    obj_id: str | None = None
    obj_name: str | None = None
    item_id: str | None = None


def _words_of(text: str) -> list[str]:
    return [w for w in collapse(text).split(" ") if w]


def _has_words(name_words: list[str], node_words: list[str]) -> bool:
    """``node_words`` stand in ``name_words`` as one run of whole words."""
    n = len(node_words)
    keys = [index_key(w) for w in name_words]
    want = [index_key(w) for w in node_words]
    return n > 0 and any(keys[i:i + n] == want for i in range(len(keys) - n + 1))


def derived_names(catalog: Catalog, roles: AxisRoleTable | None = None) -> list[DerivedName]:
    """Names a question writes for a catalog table or value that the catalog does not hold (plan 09 P1). The words of
    the rules (filler and request words, joiner particles, the X-byeol mark, the tail words) come from
    ``data/lexicon.json``. A derived name is never one of the catalog's own name keys: those keep their candidates.

    - table_core: the table name before its bracket without its first word, when that first word starts the names of
      two or more name families (tables whose names before the bracket differ) or a node of the table's list path
      stands in its name as whole words (a node equal to the whole name aside)
    - path_node: a node of the list path below the top category that stands in the names of the tables below it as
      whole words -> those tables (at most ``PATH_NODE_MAX_TABLES``; not when it is a target value)
    - no_tail: the table name before its bracket without a tail word of the lexicon ("가상물가지수" -> 가상물가)
    - abbr: a Latin abbreviation in brackets of a table or value name ("가상총소득(GNI)" -> GNI; a value's keeps the
      value)
    - and_part of plan 09 r2 (the other side of "A 및 B" in a table name) is not made (plan 09, 6; user decision
      pending)
    Blocked: shorter than two letters, a filler or request word, a list word, an X-byeol word, a catalog name of its
    own (table or value)."""
    from ..interpret.lexicon import AND_WORD, BYEOL, DERIVED_TAIL_WORDS, JOINER_PARTICLES, KNOWN_WORDS, LIST_WORDS

    tables = list(catalog.tables.values())
    own: set[str] = set()  # the catalog's own name keys (and their mark-free forms)
    values: set[str] = set()
    targets: set[str] = set()
    from .text import name_variants

    for t in tables:
        for n in (t.name, t.meta_name, t.name_front, t.name_eng):
            if n:
                own.update(name_variants(n).values())
    for ax in catalog.axes():
        for it in ax.items:
            for n in (it.name, it.name_eng):
                if not n:
                    continue
                ks = set(name_variants(n).values())
                own |= ks
                values |= ks
                vr = roles.value(it.table_id, it.obj_id, it.item_id) if roles is not None else None
                if vr is not None and vr.type == "target":
                    targets |= ks
    own |= {u for k in list(own) if (u := unmarked_key(k))}
    list_words = {index_key(w) for w in LIST_WORDS}

    def blocked(name: str) -> bool:
        k = index_key(name)
        words = _words_of(name)
        return len(k) < DERIVED_MIN_CHARS or k in KNOWN_WORDS or k in own or k in list_words \
            or (bool(words) and all(w.endswith(BYEOL) for w in words))

    out: list[DerivedName] = []

    def add(name: str, variant: str, t, obj=None, obj_name=None, item=None) -> None:
        out.append(DerivedName(collapse(name), index_key(name), variant, t, obj, obj_name, item))

    fronts = {t.table_id: collapse(paren_front(t.name)) for t in tables}
    nodes_of = {t.table_id: [collapse(n) for n in (t.path or "").split(PATH_SEP)][PATH_SKIP:] for t in tables}
    first_families: dict[str, set[str]] = collections.defaultdict(set)
    for t in tables:
        words = _words_of(fronts[t.table_id])
        if len(words) >= 2:
            first_families[index_key(words[0])].add(index_key(fronts[t.table_id]))

    def node_in_name(t) -> bool:
        words = _words_of(strip_parens(t.name)) or _words_of(fronts[t.table_id])
        return any(n and index_key(n) != index_key(fronts[t.table_id]) and _has_words(words, _words_of(n))
                   for n in nodes_of[t.table_id])

    abbr_rows: list[tuple] = [(m.group(1), it.table_id, ax.obj_id, ax.name, it.item_id)
                              for ax in catalog.axes() for it in ax.items for m in _LATIN_ABBR.finditer(it.name or "")]
    for t in tables:
        front = fronts[t.table_id]
        words = _words_of(front)
        # ---- table_core
        if len(words) >= 2 and not words[0].endswith(tuple(JOINER_PARTICLES)) and words[0] != AND_WORD:
            rest = " ".join(words[1:])
            if (len(first_families[index_key(words[0])]) >= 2 or node_in_name(t)) and not blocked(rest):
                add(rest, TABLE_CORE, t.table_id)
        # ---- no_tail
        for tail in DERIVED_TAIL_WORDS:
            if front.endswith(tail) and len(front) > len(tail):
                rest = front[:-len(tail)].rstrip()
                if rest and not blocked(rest) and index_key(rest) not in values:
                    add(rest, NO_TAIL, t.table_id)
        # ---- abbr of the table name
        for m in _LATIN_ABBR.finditer(t.name):
            abbr_rows.append((m.group(1), t.table_id, None, None, None))
    for name, tid, obj, obj_name, item in abbr_rows:
        if not blocked(name):
            add(name, ABBR, tid, obj, obj_name, item)
    # ---- path_node
    below: dict[str, list[str]] = collections.defaultdict(list)
    shown: dict[str, str] = {}
    for t in tables:
        words = _words_of(strip_parens(t.name)) or _words_of(fronts[t.table_id])
        for n in nodes_of[t.table_id]:
            k = index_key(n)
            if not k or k == index_key(fronts[t.table_id]) or not _has_words(words, _words_of(n)):
                continue
            below[k].append(t.table_id)
            shown.setdefault(k, n)
    for k, tids in below.items():
        if len(tids) > PATH_NODE_MAX_TABLES or k in targets or blocked(shown[k]):
            continue
        for tid in tids:
            add(shown[k], PATH_NODE, tid)
    return out


class CatalogNames:
    """Value names and table names of a catalog; an empty lookup when the catalog is None."""

    def __init__(self, catalog: Catalog | None):
        self._items: dict[str, dict[str, str]] = {}
        self._titles: set[str] = set()
        self._tables: list[str] = []  # per table: table name key and value keys joined (names found together)
        if catalog is None:
            return
        for table in catalog.tables.values():
            self._titles.add(self.key(table.name.split("(")[0]))
            names = [it.name or "" for ax in table.axes for it in ax.items]
            for name in names:
                self._items.setdefault(self.key(name), {}).setdefault(table.table_id, name)
            self._tables.append("|".join([self.key(table.name)] + [self.key(n) for n in names]))

    @staticmethod
    def key(text: str) -> str:
        return _DOT.sub("", _SPACE.sub("", text or ""))

    def tables(self, text: str) -> set[str]:
        return set(self._items.get(self.key(text), {}))

    def item(self, text: str, tables=None) -> str | None:
        """The value name with this key (as written in the catalog); with ``tables`` only within those tables."""
        rows = self._items.get(self.key(text), {})
        tid = next((t for t in sorted(rows) if tables is None or t in tables), None)
        return rows[tid] if tid else None

    def known(self, text: str) -> bool:
        k = self.key(text)
        return k in self._items or k in self._titles

    def together(self, a: str, b: str) -> bool:
        """Both words appear in the names of one table (table name or values)."""
        ka, kb = self.key(a), self.key(b)
        return len(ka) >= 2 and len(kb) >= 2 and any(ka in t and kb in t for t in self._tables)

    def in_titles(self, text: str) -> bool:
        """The word is inside a table name ("가상생산자물가" inside "가상생산자물가지수")."""
        k = self.key(text)
        return len(k) >= 2 and any(k in t for t in self._titles)
