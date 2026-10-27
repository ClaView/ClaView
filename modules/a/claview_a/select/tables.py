"""Per-table views of the catalog layer used by table selection (plan 04, 4-1), built once per layer.

- ``TableView``: what the evidence checks read about one table (old J1 ``meta`` entry, rebuilt from the new loader):
  title and path (nospace), classification values (ITEM rows apart), axis names, units, periods per frequency, and
  lookups of the values by raw name, nospace name and search-normalised name.
- ``Lexicon``: character 2-gram indexes of table titles, paths and value names (old S1/S2 lexicon,
  modules/a/claview_aflow/catalog.py:39-58, commit 55fd41b) as inverted indexes, so a query is scored without a pass over
  every table.
- ``MeasureIndex``: normalised text fields of every table and catalog abbreviation pairs ("가상생산(XYZ)"), the
  indicator evidence of old ``measure.py`` (:24-75).

Nothing here names a table, case or question.
"""

from __future__ import annotations

import collections
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field

from .. import paths

WORDS = json.loads(paths.EVIDENCE_WORDS.read_text(encoding="utf-8"))
_PUNCT = re.compile(WORDS["search_punct"])
_ABBR = re.compile(WORDS["abbreviation"])
_MEASURE_DROP = re.compile(r"[^가-힣a-z0-9]")
FREQ_ORDER = "YHQM"


def nospace(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


# Source: modules/a/claview_aflow/catalog.py:27-29 (commit 55fd41b): NFKC, lower case, spaces and punctuation removed.
def norm_v1(text: str | None) -> str:
    return _PUNCT.sub("", unicodedata.normalize("NFKC", text or "").lower())


# Source: modules/a/claview_aflow/catalog.py:32-36 (commit 55fd41b).
def grams_v1(text_norm: str) -> frozenset:
    if len(text_norm) < 2:
        return frozenset({text_norm}) if text_norm else frozenset()
    return frozenset(text_norm[i:i + 2] for i in range(len(text_norm) - 1))


# Source: modules/a/claview_aflow/measure.py:24-25 (commit 55fd41b).
def measure_norm(text: str | None) -> str:
    return _MEASURE_DROP.sub("", unicodedata.normalize("NFKC", text or "").lower())


@dataclass(frozen=True)
class ItemView:
    index: int
    obj_id: str
    item_id: str
    name_raw: str
    name_nospace: str
    name_norm: str
    parent_id: str | None
    unit: str | None


@dataclass
class TableView:
    table_id: str
    org_id: str | None
    title: str
    title_ns: str
    path: str
    paths_ns: str
    items: list[ItemView]
    dims: list[tuple[str, str]]  # (obj_id, axis name)
    itm_rows: list[tuple[str, str | None]]  # ITEM rows: (name, unit)
    table_units: list[str]
    periods: list[tuple[str, str | None, str | None]]  # (frequency, start, end) in table order, one per frequency
    has_meta: bool
    by_raw: dict[str, list[ItemView]] = field(default_factory=dict)
    by_nospace: dict[str, list[ItemView]] = field(default_factory=dict)
    by_norm: dict[str, list[ItemView]] = field(default_factory=dict)
    parents: dict[tuple[str, str], ItemView] = field(default_factory=dict)
    axis_roles: dict[str, str] = field(default_factory=dict)  # obj_id -> indicator / target / basis / ...

    @property
    def item_names_ns(self) -> list[str]:
        return [it.name_nospace for it in self.items]

    @property
    def dim_names_ns(self) -> list[str]:
        return [nospace(name) for _, name in self.dims]

    def units(self) -> set[str]:
        """Table units, ITEM-row units and value units (old J4 ``units``)."""
        return {u for u in [*self.table_units, *(u for _, u in self.itm_rows), *(it.unit for it in self.items)] if u}

    def frequencies(self) -> list[str]:
        return sorted({f for f, _, _ in self.periods}, key=FREQ_ORDER.index)

    def coverage(self, freq: str) -> tuple[str | None, str | None] | None:
        for f, start, end in self.periods:
            if f == freq:
                return start, end
        return None

    def ancestors(self, it: ItemView) -> list[dict]:
        """Ancestors on the same axis, nearest first; stops at a cycle (old J2 parent path)."""
        out, seen, up = [], {it.item_id}, it.parent_id
        while up is not None and (it.obj_id, up) in self.parents and up not in seen:
            seen.add(up)
            parent = self.parents[(it.obj_id, up)]
            out.append({"item_id": up, "name": parent.name_raw})
            up = parent.parent_id
        return out


def _table_view(table) -> TableView:
    items: list[ItemView] = []
    dims: list[tuple[str, str]] = []
    for axis in table.axes:
        dims.append((axis.obj_id, axis.name or ""))
        for it in axis.items:
            name = it.name or ""
            items.append(ItemView(len(items), axis.obj_id, it.item_id, name, nospace(name), norm_v1(name),
                                  it.parent_id, it.unit))
    periods, seen = [], set()
    for p in table.periods:
        if p.freq is None or p.freq in seen:
            continue
        seen.add(p.freq)
        periods.append((p.freq, p.start, p.end))
    view = TableView(
        table_id=table.table_id, org_id=table.org_id, title=table.name, title_ns=nospace(table.name),
        path=table.path or "", paths_ns=nospace(table.path or ""), items=items, dims=dims,
        itm_rows=[(r.name or "", r.unit) for r in table.item_rows],
        table_units=[u["name"] for u in table.units if u.get("name")], periods=periods, has_meta=table.has_meta)
    for it in items:
        view.by_raw.setdefault(it.name_raw, []).append(it)
        view.by_nospace.setdefault(it.name_nospace, []).append(it)
        if it.name_norm:
            view.by_norm.setdefault(it.name_norm, []).append(it)
        view.parents.setdefault((it.obj_id, it.item_id), it)
    return view


# ---------------------------------------------------------------- lexicon (old S1/S2)


def _idf(docs: dict) -> dict:
    """Source: modules/a/claview_aflow/catalog.py:39-45 (commit 55fd41b)."""
    counts: dict[str, int] = collections.defaultdict(int)
    for gs in docs.values():
        for g in gs:
            counts[g] += 1
    n = len(docs)
    return {g: math.log(1 + n / c) for g, c in counts.items()}


@dataclass
class Lexicon:
    title: dict[str, frozenset]
    path: dict[str, frozenset]
    items: dict[str, list[tuple[str, frozenset]]]
    idf_title: dict[str, float]
    idf_item: dict[str, float]
    title_inv: dict[str, list[str]]
    path_inv: dict[str, list[str]]
    item_inv: dict[str, list[tuple[str, int]]]


def build_lexicon(views: dict[str, TableView]) -> Lexicon:
    """S1/S2 lexicon (catalog.py:48-58): titles, paths and value names (ITEM rows left out)."""
    title = {t: grams_v1(norm_v1(v.title)) for t, v in views.items()}
    path = {t: grams_v1(norm_v1(v.path)) for t, v in views.items()}
    items = {}
    for t, v in views.items():
        names = sorted({it.name_norm for it in v.items if it.name_norm})
        items[t] = [(name, grams_v1(name)) for name in names]
    item_docs = {t: frozenset().union(*(g for _, g in rows)) if rows else frozenset() for t, rows in items.items()}
    title_inv: dict[str, list[str]] = collections.defaultdict(list)
    path_inv: dict[str, list[str]] = collections.defaultdict(list)
    item_inv: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    for t in views:
        for g in title[t]:
            title_inv[g].append(t)
        for g in path[t]:
            path_inv[g].append(t)
        for k, (_, grams) in enumerate(items[t]):
            for g in grams:
                item_inv[g].append((t, k))
    return Lexicon(title, path, items, _idf({t: title[t] | path[t] for t in title}), _idf(item_docs),
                   dict(title_inv), dict(path_inv), dict(item_inv))


# ---------------------------------------------------------------- measure index (old measure.py)


class MeasureIndex:
    """Source: modules/a/claview_aflow/measure.py:28-65 (commit 55fd41b), over the new table views."""

    def __init__(self, views: dict[str, TableView]):
        self.table_fields = {tid: [(measure_norm(f["text"]), f) for f in fields(v)] for tid, v in views.items()}
        self.labels = tuple({s for entries in self.table_fields.values() for s, _ in entries})
        self.aliases: dict[str, list] = {}
        for pair in abbreviation_pairs(views):
            a, b = measure_norm(pair["abbr"]), measure_norm(pair["long"])
            if len(a) >= 2 and len(b) >= 2:
                for key, value in ((a, b), (b, a)):
                    self.aliases.setdefault(key, []).append((value, dict(pair)))
        self.known: dict[str, bool] = {}

    def forms(self, term: str) -> list:
        key = measure_norm(term)
        return [(key, None)] + self.aliases.get(key, [])

    def recognizes(self, term: str) -> bool:
        key = measure_norm(term)
        if key not in self.known:
            self.known[key] = len(key) >= 2 and any(f in s for f, _ in self.forms(term) for s in self.labels)
        return self.known[key]


def fields(view: TableView):
    """Text fields of one table (measure.py:28-41): ITEM row, values, title, path segments."""
    if view.itm_rows and view.itm_rows[0][0]:
        yield {"field": "item", "text": view.itm_rows[0][0]}
    for it in view.items:
        if it.name_raw:
            yield {"field": "item", "text": it.name_raw}
    if view.title:
        yield {"field": "title", "text": view.title}
    for segment in (view.path or "").split(">"):
        if segment.strip():
            yield {"field": "catalog_path", "text": segment.strip()}


def abbreviation_pairs(views: dict[str, TableView]) -> list[dict]:
    """Long name and abbreviation written as "LONG(ABBR)" in a title or value (old catalog.py:182-187)."""
    out: dict[tuple, dict] = {}
    for tid, v in views.items():
        for field_name, text in [("title", v.title)] + [("item", it.name_raw) for it in v.items]:
            for m in _ABBR.finditer(text or ""):
                pair = {"long": m.group(1).strip(), "abbr": m.group(2), "table_id": tid, "source_field": field_name}
                out.setdefault((pair["long"], pair["abbr"], tid, field_name), pair)
    return list(out.values())


# ---------------------------------------------------------------- per-layer cache


@dataclass
class SelectionCatalog:
    views: dict[str, TableView]
    order: list[str]  # catalog order of the table list
    _lexicon: Lexicon | None = None
    _measure: MeasureIndex | None = None

    def lexicon(self) -> Lexicon:
        if self._lexicon is None:
            self._lexicon = build_lexicon(self.views)
        return self._lexicon

    def measure(self) -> MeasureIndex:
        if self._measure is None:
            self._measure = MeasureIndex(self.views)
        return self._measure


def selection_catalog(layer) -> SelectionCatalog:
    """The table views of a catalog layer, built once and kept on the layer."""
    if "selection_catalog" not in layer._derived:
        views = {t.table_id: _table_view(t) for t in layer.catalog.tables.values()}
        for t, v in views.items():
            for obj, _ in v.dims:
                role = layer.roles.axis(t, obj) if layer.roles is not None else None
                v.axis_roles[obj] = role.role if role else "unknown"
        layer._derived["selection_catalog"] = SelectionCatalog(views, list(views))
    return layer._derived["selection_catalog"]
