"""Uncollapsed catalog candidates of a name (plan 15 r3, 3.3): the lookup layer of the new decomposition.

``OccurrenceIndex.lookup(text)`` expands every candidate of ``NameSpanIndex.lookup`` to one row per table, keeping the
axis (``obj_id``) and the raw ``item_id`` (as ``value_id``), and adds what the term index does not hold:

- ITEM rows (``Table.item_rows``, kept apart from the axes by the loader): looked up under the key variants of their
  names (and English names) like a value, with ``obj_id`` "ITEM"; an ITEM row holds the table's indicator, so its
  role is ``indicator``;
- classification-axis names for the X-byeol form of a word (``axis(text)``): a word written with the lexicon's
  X-byeol mark names the axes whose names, without that mark, are the word (path exact) or hold it (path lexical);
  role ``breakdown``, ``obj_id`` only, no value;
- family qualifiers (``qualifier(key)``): the tables whose names hold the qualifier, role ``basis``.

Roles come from the candidate type (``ROLE_OF_TYPE``): table names -> table_name, indicator and compound values and
ITEM rows -> indicator, target and total-header values -> target, basis -> basis; a value whose type is unknown (or any type not
mapped) stays ``unclassified`` and is never read as an indicator. Out-of-scope entries name no table and give no row.
The path is the match path: exact (the name itself, or a footnote-free, mark-free or year-range-free form: decision 6),
dictionary (an approved or draft synonym), else lexical.

The existing term index, name spans and their results are not changed: this layer only reads them (built once per
layer, kept in ``layer._derived``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .index import MIN_SPAN_CHARS
from .loader import ITEM_OBJ_ID
from .text import index_key, name_variants, strip_year_range

if TYPE_CHECKING:
    from .layer import CatalogLayer

ROLE_OF_TYPE = {"series_name": "table_name", "indicator": "indicator", "compound": "indicator", "target": "target",
                "total_header": "target", "basis": "basis", "item_row": "indicator", "axis_name": "breakdown"}
UNCLASSIFIED = "unclassified"
EXACT_VARIANTS = frozenset({"no_marks", "no_footnote", "no_year_range"})
_PATH_RANK = {"exact": 0, "dictionary": 1, "lexical": 2}
_CACHE_KEY = "occurrences"


def role_of(cand: dict) -> str:
    """The candidate's role: table kind -> table_name, else by its type; unmapped types stay unclassified."""
    if cand.get("kind") == "table":
        return "table_name"
    return ROLE_OF_TYPE.get(cand.get("type") or "", UNCLASSIFIED)


def path_of(cand: dict) -> str:
    """exact (the name itself or a form decision 6 counts as the name), dictionary, else lexical."""
    if cand.get("path") == "dictionary":
        return "dictionary"
    if cand.get("path") == "exact" or (cand.get("variant") or "").split(":")[-1] in EXACT_VARIANTS:
        return "exact"
    return "lexical"


def _row(table_id: str, obj_id, value_id, value_name, role: str, path: str, type_: str) -> dict:
    return {"table_id": table_id, "obj_id": obj_id, "value_id": value_id, "value_name": value_name, "role": role,
            "path": path, "type": type_}


def _type_of(cand: dict) -> str:
    t = cand.get("type") or "unknown"
    return "series_name" if cand.get("kind") == "table" else t


def merge(rows: list[dict]) -> list[dict]:
    """One row per (table, axis, value, role), the strongest path kept; in a stable order."""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["table_id"], r["obj_id"], r["value_id"], r["role"])
        old = best.get(k)
        if old is None or _PATH_RANK[r["path"]] < _PATH_RANK[old["path"]]:
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["table_id"], r["obj_id"] or "", r["value_id"] or "", r["role"],
                                                r["path"]))


@dataclass
class OccurrenceIndex:
    layer: CatalogLayer
    items: dict[str, list[dict]] = field(default_factory=dict)  # key -> ITEM-row rows
    axes: dict[str, list[dict]] = field(default_factory=dict)  # axis key (without the X-byeol mark) -> axis rows

    def __post_init__(self):
        from ..interpret.lexicon import BYEOL  # the one word dictionary (no catalog import there)

        self._byeol = BYEOL
        for table in self.layer.catalog.tables.values():
            for it in table.item_rows:
                for name in (it.name, it.name_eng):
                    if not name:
                        continue
                    forms = [(sub, k) for sub, k in name_variants(name).items()]
                    stripped = strip_year_range(name)
                    if stripped is not None:  # spans.year_range_entries: the bracket-free form is not exact
                        forms += [("no_year_range", k) for v, k in name_variants(stripped).items() if v != "no_paren"]
                    for sub, k in forms:
                        path = "exact" if sub == "name" or sub in EXACT_VARIANTS else "lexical"
                        self.items.setdefault(k, []).append(
                            _row(it.table_id, ITEM_OBJ_ID, it.item_id, it.name, "indicator", path, "item_row"))
            for ax in table.axes:
                if not ax.named:
                    continue
                k = self.axis_key(ax.name)
                if len(k) >= MIN_SPAN_CHARS:
                    self.axes.setdefault(k, []).append(_row(table.table_id, ax.obj_id, None, ax.name, "breakdown",
                                                            "exact", "axis_name"))

    def axis_key(self, text: str | None) -> str:
        return index_key(text).removesuffix(self._byeol)

    def lookup(self, text: str, guess: bool = False) -> list[dict]:
        """Candidate rows of a name: the name spans' candidates (term index and year-range-free names) one per table,
        and the ITEM rows of that key. ``guess``: the written words are not the name itself (a completed list member
        or an alias): every path is lexical."""
        rows = self.expand(self.layer.name_spans().lookup(text))
        rows += [dict(r) for r in self.items.get(index_key(text), [])]
        if guess:
            rows = [dict(r, path="lexical") for r in rows]
        return merge(rows)

    @staticmethod
    def expand(cands: list[dict]) -> list[dict]:
        """One row per table of every candidate (a candidate held by several tables is never collapsed)."""
        out = []
        for c in cands:
            if c.get("type") == "out_of_scope":
                continue  # names no catalog table
            role, path, type_ = role_of(c), path_of(c), _type_of(c)
            value_id = c.get("item_id") if c.get("kind") != "table" else None
            obj_id = c.get("obj_id") if c.get("kind") != "table" else None
            for t in c.get("table_ids") or []:
                out.append(_row(t, obj_id, value_id, c.get("name"), role, path, type_))
        return out

    def axis(self, text: str) -> list[dict]:
        """Axis rows of an X-byeol word ("<axis words><mark>"): axes named by the word (exact) or whose names hold it
        (lexical). A word without the mark, or too short, names no axis."""
        k = index_key(text)
        if not k.endswith(self._byeol):
            return []
        k = k.removesuffix(self._byeol)
        if len(k) < MIN_SPAN_CHARS:
            return []
        rows = [dict(r) for r in self.axes.get(k, [])]
        rows += [dict(r, path="lexical") for ak, rs in self.axes.items() if ak != k and k in ak for r in rs]
        return merge(rows)

    def qualifier(self, key: str) -> list[dict]:
        """Tables whose names hold a family qualifier (``catalog.relations``): role basis, no axis or value."""
        from .relations import relations_of

        tables = relations_of(self.layer).qualifier_tables.get(index_key(key), ())
        return merge([_row(t, None, None, key, "basis", "exact", "basis") for t in sorted(tables)])


def occurrences_of(layer: CatalogLayer) -> OccurrenceIndex:
    """The occurrence index of a layer, built once."""
    if _CACHE_KEY not in layer._derived:
        layer._derived[_CACHE_KEY] = OccurrenceIndex(layer)
    return layer._derived[_CACHE_KEY]
