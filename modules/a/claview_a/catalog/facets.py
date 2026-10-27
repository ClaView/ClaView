"""Table facets (plan 11, E; R0 record mode): one cached record per table of a catalog layer.

A facet holds what the evidence facts (``select/facts.py``) read about one table, built once per layer:
- subject names with their source: the title core (name before its bracket), the plan 09 derived table names
  (``names.derived_names``, source ``derived:<variant>``) and the last node of the catalog list path;
- bracket qualifiers of the title with their kinds (``select.cover.bracket_words``: basis, frequency, period,
  source, other);
- axes with their role, the rule that decided it (role source) and a draft flag (not approved);
- values (ITEM rows included, role ``series_name``) with ID, value role, parent chain (nearest first) and a
  hierarchy-anomaly flag (an orphan parent link, a cycle, a repeated value ID on the axis);
- per-frequency coverage and the latest point.

Lookups by index key (``text.index_key``): ``by_key`` (the name, footnote-free and mark-free forms), ``by_bare``
(every bracket part removed, when that differs) and ``derived_values`` (a value's derived names). Nothing here
names a table, case or question; record only, no selection reads it to decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .loader import ITEM_OBJ_ID
from .names import DerivedName, derived_names
from .text import collapse, index_key, name_variants, paren_front

TITLE_CORE, PATH_LEAF, DERIVED = "title_core", "path_leaf", "derived"
PATH_SEP = ">"
SAME_NAME_VARIANTS = ("name", "no_footnote", "no_marks")  # exact name forms; ``no_paren`` drops qualifiers


@dataclass(frozen=True)
class ValueFacet:
    obj_id: str
    item_id: str
    name: str
    role: str  # value role of the axis-role table (series_name for the ITEM row, unknown when none)
    parents: tuple[str, ...]  # parent item IDs on the same axis, nearest first
    parent_names: tuple[str, ...]
    anomaly: str | None  # orphan / cycle / repeated_id; None when the chain reads cleanly
    has_children: bool
    root_siblings: int  # other values without a parent on the same axis (for a root value)
    key: str = ""  # index key of the name


@dataclass
class TableFacet:
    table_id: str
    title: str
    subjects: list[dict]  # {name, key, source}
    qualifiers: list[dict]  # {word, kind}
    axes: list[dict]  # {obj_id, name, role, role_source, draft}
    values: list[ValueFacet]
    coverage: list[dict]  # {frequency, start, end}
    latest: dict[str, str | None]  # frequency -> last point
    by_key: dict[str, list[int]] = field(default_factory=dict)  # same-name key -> value positions
    by_bare: dict[str, list[int]] = field(default_factory=dict)  # bracket-free key -> value positions
    derived_values: dict[str, list[int]] = field(default_factory=dict)  # derived value-name key -> positions
    index: dict[tuple[str, str], int] = field(default_factory=dict)  # (obj_id, item_id) -> first position

    def axis(self, obj_id: str) -> dict | None:
        return next((a for a in self.axes if a["obj_id"] == obj_id), None)

    def value(self, obj_id: str, item_id: str) -> ValueFacet | None:
        pos = self.index.get((obj_id, item_id))
        return None if pos is None else self.values[pos]


def _chain(item, by_id: dict) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    out_ids, out_names, seen, up = [], [], {item.item_id}, item.parent_id
    anomaly = "orphan" if item.orphan else None
    while up is not None:
        if up in seen:
            anomaly = "cycle"
            break
        parent = by_id.get(up)
        if parent is None:
            anomaly = anomaly or "orphan"
            break
        seen.add(up)
        out_ids.append(up)
        out_names.append(parent.name or "")
        up = parent.parent_id
    return tuple(out_ids), tuple(out_names), anomaly


def _qualifiers(title: str) -> list[dict]:
    from ..select.cover import bracket_words  # the word classes of the selection policy (data files)

    return [{"word": w, "kind": k} for w, k in bracket_words(title).items()]


def _facet(table, roles, derived: list[DerivedName]) -> TableFacet:
    subjects: list[dict] = []

    def subject(name: str, source: str) -> None:
        k = index_key(name)
        if k and not any(s["key"] == k and s["source"] == source for s in subjects):
            subjects.append({"name": collapse(name), "key": k, "source": source})

    subject(paren_front(table.name), TITLE_CORE)
    for d in derived:
        if d.obj_id is None:
            subject(d.name, f"{DERIVED}:{d.variant}")
    nodes = [n.strip() for n in (table.path or "").split(PATH_SEP) if n.strip()]
    if nodes:
        subject(nodes[-1], PATH_LEAF)
    axes, values = [], []
    for it in table.item_rows:
        values.append(ValueFacet(ITEM_OBJ_ID, it.item_id, it.name or "", "series_name", (), (), None, False, 0,
                                  index_key(it.name)))
    for axis in table.axes:
        ar = roles.axis(table.table_id, axis.obj_id) if roles is not None else None
        axes.append({"obj_id": axis.obj_id, "name": axis.name or "", "role": ar.role if ar else "unknown",
                     "role_source": ar.rule if ar else "none", "draft": (ar.status if ar else "draft") != "approved"})
        by_id: dict = {}
        repeated = set()
        for it in axis.items:
            if it.item_id in by_id:
                repeated.add(it.item_id)
            by_id.setdefault(it.item_id, it)
        has_child = {it.parent_id for it in axis.items if it.parent_id}
        roots = [it for it in axis.items if not it.parent_id]
        for it in axis.items:
            vr = roles.value(table.table_id, axis.obj_id, it.item_id) if roles is not None else None
            parents, names, anomaly = _chain(it, by_id)
            if it.item_id in repeated:
                anomaly = anomaly or "repeated_id"
            values.append(ValueFacet(axis.obj_id, it.item_id, it.name or "", vr.type if vr else "unknown", parents,
                                     names, anomaly, it.item_id in has_child,
                                     len(roots) - 1 if not it.parent_id else 0, index_key(it.name)))
    coverage, latest = [], {}
    for p in table.periods:
        if p.freq is None or p.freq in latest:
            continue
        coverage.append({"frequency": p.freq, "start": p.start, "end": p.end})
        latest[p.freq] = p.end
    facet = TableFacet(table.table_id, table.name, subjects, _qualifiers(table.name), axes, values, coverage, latest)
    for pos, v in enumerate(values):
        facet.index.setdefault((v.obj_id, v.item_id), pos)
        variants = name_variants(v.name)
        for variant in SAME_NAME_VARIANTS:
            if variant in variants:
                facet.by_key.setdefault(variants[variant], []).append(pos)
        bare = variants.get("no_paren")
        if bare and bare not in {variants.get(x) for x in SAME_NAME_VARIANTS}:
            facet.by_bare.setdefault(bare, []).append(pos)
    for d in derived:
        if d.obj_id is not None:
            for pos, v in enumerate(values):
                if v.obj_id == d.obj_id and v.item_id == d.item_id:
                    facet.derived_values.setdefault(d.key, []).append(pos)
    return facet


def table_facets(layer) -> dict[str, TableFacet]:
    """Every table's facet, built once and kept on the layer."""
    if "table_facets" not in layer._derived:
        by_table: dict[str, list[DerivedName]] = {}
        for d in derived_names(layer.catalog, layer.roles):
            by_table.setdefault(d.table_id, []).append(d)
        layer._derived["table_facets"] = {t.table_id: _facet(t, layer.roles, by_table.get(t.table_id, []))
                                          for t in layer.catalog.tables.values()}
    return layer._derived["table_facets"]


def facet_of(layer, table_id: str) -> TableFacet | None:
    return table_facets(layer).get(table_id)


def catalog_version(layer) -> str:
    """The catalog snapshot the facets were read from: source date and the first characters of the raw files'
    SHA-256 (the loader records them)."""
    src = getattr(layer.catalog, "sources", None) or {}
    shas = [str((src.get(k) or {}).get("sha256") or "")[:12] for k in ("search_catalog", "meta_raw")]
    return ":".join([str(src.get("source_date") or "")] + shas)
