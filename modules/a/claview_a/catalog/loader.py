"""Catalog loader: the two raw files -> tables, axes, classification values, periods and units.

Inputs (paths in ``claview_a.paths``): ``search_catalog.json`` (the 349-table list: ID, title, catalog path) and
``boK_meta_raw.jsonl`` (one metadata snapshot row per table: TBL, PRD, ITM, UNIT, CMMT). Nothing is fetched.

Written new for the current-path package (now extracted here). The old loader (src/claview/aflow/catalog.py) was read for reference only; unlike it this
loader keeps the English names (``*_ENG``), the parent link and depth of every value, and marks unnamed axes and
values instead of dropping them. The SHA-256 of both raw files is recorded.

The ITEM rows (``OBJ_ID == "ITEM"``) carry the table name and are kept apart from the classification axes.
"""

from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from .. import paths
from .text import collapse, paren_front


FREQ_CODE = {"년": "Y", "반기": "H", "분기": "Q", "월": "M"}
ITEM_OBJ_ID = "ITEM"


@dataclass(frozen=True)
class Item:
    """One classification value (or an ITEM row)."""

    table_id: str
    obj_id: str
    item_id: str
    name: str | None
    name_eng: str | None
    parent_id: str | None
    depth: int
    unit: str | None
    unit_eng: str | None
    orphan: bool = False  # parent_id given but not found in the same axis

    @property
    def named(self) -> bool:
        return bool(self.name and self.name.strip())


@dataclass(frozen=True)
class Axis:
    table_id: str
    obj_id: str
    name: str | None
    name_eng: str | None
    order: int | None
    items: tuple[Item, ...]

    @property
    def named(self) -> bool:
        return bool(self.name and self.name.strip())

    def children(self) -> dict[str | None, list[Item]]:
        """Values grouped by their raw parent ID (None for roots; an orphan stays under its missing parent ID)."""
        out: dict[str | None, list[Item]] = {}
        for it in self.items:
            out.setdefault(it.parent_id, []).append(it)
        return out


@dataclass(frozen=True)
class Period:
    freq: str | None  # Y, H, Q, M; None for an unknown PRD_SE
    label: str  # PRD_SE as given
    start: str | None
    end: str | None


@dataclass(frozen=True)
class Table:
    table_id: str
    org_id: str | None
    name: str  # catalog title (full name)
    meta_name: str | None  # TBL_NM in the metadata snapshot
    name_eng: str | None
    name_front: str  # name before the first parenthesis
    path: str | None  # catalog list path
    category: str | None
    axes: tuple[Axis, ...]
    item_rows: tuple[Item, ...]
    units: tuple[dict, ...]  # table units: {name, name_eng}
    periods: tuple[Period, ...]
    has_meta: bool

    def axes_named(self, name: str) -> list[Axis]:
        target = collapse(name)
        return [ax for ax in self.axes if ax.named and collapse(ax.name) == target]


@dataclass
class Catalog:
    tables: dict[str, Table]
    sources: dict  # {"search_catalog": {path, sha256}, "meta_raw": {path, sha256}, "source_date": ...}
    unnamed: list[dict] = field(default_factory=list)  # unnamed axes and values: {table_id, obj_id, item_id, what}
    meta_without_catalog: list[str] = field(default_factory=list)

    def axes(self):
        for table in self.tables.values():
            yield from table.axes

    def items(self):
        for axis in self.axes():
            yield from axis.items


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _depths(rows: list[dict]) -> dict[str, tuple[int, bool]]:
    """ITM_ID -> (depth, orphan). Root depth is 1. A missing parent makes the value an orphan root; a cycle stops."""
    parent = {r["ITM_ID"]: _text(r.get("UP_ITM_ID")) for r in rows}
    out: dict[str, tuple[int, bool]] = {}
    for item_id in parent:
        depth, node, seen, orphan = 1, item_id, {item_id}, False
        while True:
            up = parent.get(node)
            if up is None:
                break
            if up not in parent:
                orphan = node == item_id
                break
            if up in seen:
                break
            seen.add(up)
            depth += 1
            node = up
        out[item_id] = (depth, orphan)
    return out


def _item(table_id: str, row: dict, depth: int, orphan: bool) -> Item:
    return Item(
        table_id=table_id,
        obj_id=str(row.get("OBJ_ID")),
        item_id=str(row.get("ITM_ID")),
        name=_text(row.get("ITM_NM")),
        name_eng=_text(row.get("ITM_NM_ENG")),
        parent_id=_text(row.get("UP_ITM_ID")),
        depth=depth,
        unit=_text(row.get("UNIT_NM")),
        unit_eng=_text(row.get("UNIT_ENG_NM")) or _text(row.get("UNIT_NM_ENG")),
        orphan=orphan,
    )


def _periods(rows) -> tuple[Period, ...]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("PRD_SE") or "")
        out.append(Period(FREQ_CODE.get(label), label, _text(row.get("STRT_PRD_DE")), _text(row.get("END_PRD_DE"))))
    return tuple(out)


def _table(doc: dict, raw: dict | None, unnamed: list[dict]) -> Table:
    table_id = doc["table_id"]
    meta = (raw or {}).get("meta") or {}
    tbl = next((t for t in meta.get("TBL") or [] if isinstance(t, dict)), {})
    rows = [r for r in meta.get("ITM") or [] if isinstance(r, dict)]
    item_rows = [r for r in rows if r.get("OBJ_ID") == ITEM_OBJ_ID]
    value_rows = [r for r in rows if r.get("OBJ_ID") != ITEM_OBJ_ID]

    grouped: dict[str, list[dict]] = {}
    for r in value_rows:
        grouped.setdefault(str(r.get("OBJ_ID")), []).append(r)
    axes = []
    for obj_id, axis_rows in grouped.items():
        depths = _depths(axis_rows)
        items = tuple(_item(table_id, r, *depths[r["ITM_ID"]]) for r in axis_rows)
        first = axis_rows[0]
        names = {_text(r.get("OBJ_NM")) for r in axis_rows} - {None}
        name = _text(first.get("OBJ_NM")) or (sorted(names)[0] if names else None)
        order = first.get("OBJ_ID_SN")
        axis = Axis(table_id, obj_id, name, _text(first.get("OBJ_NM_ENG")),
                    int(order) if str(order or "").isdigit() else None, items)
        axes.append(axis)
        if not axis.named:
            unnamed.append({"table_id": table_id, "obj_id": obj_id, "item_id": None, "what": "axis"})
        for it in items:
            if not it.named:
                unnamed.append({"table_id": table_id, "obj_id": obj_id, "item_id": it.item_id, "what": "value"})
    axes.sort(key=lambda a: (a.order is None, a.order or 0))

    title = collapse(doc.get("title")) or collapse(tbl.get("TBL_NM")) or collapse((raw or {}).get("name")) or table_id
    units = tuple({"name": _text(u.get("UNIT_NM")), "name_eng": _text(u.get("UNIT_NM_ENG"))}
                  for u in meta.get("UNIT") or [] if isinstance(u, dict))
    return Table(
        table_id=table_id,
        org_id=_text(doc.get("org_id")) or _text((raw or {}).get("orgId")),
        name=title,
        meta_name=collapse(tbl.get("TBL_NM") or (raw or {}).get("name")) or None,
        name_eng=_text(tbl.get("TBL_NM_ENG")),
        name_front=paren_front(title),
        path=_text(doc.get("path")),
        category=_text(doc.get("category")) or _text((raw or {}).get("cat")),
        axes=tuple(axes),
        item_rows=tuple(_item(table_id, r, 1, False) for r in item_rows),
        units=units,
        periods=_periods(meta.get("PRD")),
        has_meta=raw is not None,
    )


def load_catalog(search_catalog: Path | None = None, meta_raw: Path | None = None) -> Catalog:
    """Read the table list and the metadata snapshot. Tables follow the order of the table list."""
    search_catalog = Path(search_catalog or paths.CATALOG_SEARCH)
    meta_raw = Path(meta_raw or paths.CATALOG_META_RAW)
    listing = json.loads(search_catalog.read_text(encoding="utf-8"))
    raw_rows: dict[str, dict] = {}
    with meta_raw.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                raw_rows[row["tblId"]] = row

    unnamed: list[dict] = []
    tables: dict[str, Table] = {}
    for doc in listing["documents"]:
        tables[doc["table_id"]] = _table(doc, raw_rows.get(doc["table_id"]), unnamed)
    sources = {
        "search_catalog": {"path": str(search_catalog), "sha256": sha256_of(search_catalog)},
        "meta_raw": {"path": str(meta_raw), "sha256": sha256_of(meta_raw)},
        "source_date": listing.get("source_date"),
    }
    return Catalog(tables=tables, sources=sources, unnamed=unnamed,
                   meta_without_catalog=sorted(set(raw_rows) - set(tables)))
