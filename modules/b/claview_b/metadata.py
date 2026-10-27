"""Saved metadata adapter; does not perform HTTP requests."""

from __future__ import annotations

import json
import re
from pathlib import Path

from claview_common import periods

from .contracts import Json, StageFailure, fingerprint

# "반기"(half-year/semiannual) added alongside the original 년/분기/월: 14 of the
# 349 BOK tables report only(or also) on a half-year cadence (PRD_SE=="반기"),
# and without an entry here MetadataStore silently dropped those periods,
# which made DT_105Y001 (반기-only) fail as metadata_incomplete. No table in
# the current catalog reports daily ("일") periods, so that unit is not added.
FREQUENCIES = {"년": "Y", "반기": "H", "분기": "Q", "월": "M"}

# Periods per year, used to convert a year-index into this frequency's own
# index space (Y has no sub-year component, so it is handled separately).
WIDTHS = {"H": 2, "Q": 4, "M": 12}


def _b_failure(exc: periods.PeriodError) -> StageFailure:
    code = "invalid_metadata_period" if exc.code == "invalid_metadata_period" else "invalid_period"
    return StageFailure(code)


# Period arithmetic lives in claview_common.periods (shared with C); B keeps its names and
# reports failures as StageFailure codes, as before the move.
def period_number(value: str, frequency: str) -> int:
    try:
        return periods.period_index(value, frequency)
    except periods.PeriodError as exc:
        raise _b_failure(exc) from None


def periods_between(start: str, end: str, frequency: str) -> list[str]:
    try:
        return periods.periods_between(start, end, frequency)
    except periods.PeriodError as exc:
        raise _b_failure(exc) from None


def normalize_period(value: str, frequency: str) -> str:
    try:
        return periods.normalize_meta_period(value, frequency)
    except periods.PeriodError as exc:
        raise _b_failure(exc) from None


class MetadataStore:
    def __init__(self, catalog_path: Path, metadata_path: Path):
        self.snapshot = fingerprint(metadata_path)
        self.catalog_snapshot = fingerprint(catalog_path)
        documents = json.loads(catalog_path.read_text(encoding="utf-8-sig"))["documents"]
        self.catalog = {}
        for d in documents:
            key = f"{d['org_id']}:{d['table_id']}"
            if key in self.catalog:
                raise StageFailure("duplicate_catalog_id")
            ids = [s.strip() for s in d["path_ids"].split(">")]
            names = [s.strip() for s in d["path"].split(">")]
            if len(ids) != len(names) or not all(ids):
                raise StageFailure("invalid_catalog_path")
            self.catalog[key] = {
                **d,
                "doc_id": key,
                "path_ids": ids,
                "path_names": names,
                "depth": len(ids) - 1,
            }
        self.records = {}
        self.cache = {}
        for line in metadata_path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = f"{row['orgId']}:{row['tblId']}"
            if key in self.records:
                raise StageFailure("duplicate_metadata_id")
            self.records[key] = row

    def get(self, org_id: str, table_id: str) -> Json:
        key = f"{org_id}:{table_id}"
        if key in self.cache:
            return self.cache[key]
        if key not in self.records:
            raise StageFailure("metadata_missing", "unresolved")
        raw = self.records[key]["meta"]
        if not raw.get("ITM") or not raw.get("PRD"):
            raise StageFailure("metadata_incomplete", "unresolved")
        if not all(
            isinstance(row, dict) and {"ITM_ID", "ITM_NM", "OBJ_ID"} <= row.keys()
            for row in raw["ITM"]
        ):
            raise StageFailure("metadata_incomplete", "unresolved")
        if not all(
            isinstance(row, dict) and {"PRD_SE", "STRT_PRD_DE", "END_PRD_DE"} <= row.keys()
            for row in raw["PRD"]
        ):
            raise StageFailure("metadata_incomplete", "unresolved")
        table = {
            "org_id": org_id,
            "table_id": table_id,
            "table_name": self.records[key]["name"],
            "items": {},
            "dimensions": {},
            "periods": {},
            "notes": raw.get("CMMT", []),
            "units": sorted({r["UNIT_NM"] for r in raw.get("UNIT", []) if r.get("UNIT_NM")}),
            "snapshot_id": self.snapshot,
            "warnings": [],
        }
        for row in raw["ITM"]:
            value = {
                "name": row["ITM_NM"],
                "unit": row.get("UNIT_NM") or None,
                "parent_code": row.get("UP_ITM_ID") or None,
            }
            if row["OBJ_ID"] == "ITEM":
                values = table["items"]
            else:
                axis = "objL" + str(row.get("OBJ_ID_SN", ""))
                if not re.fullmatch(r"objL[1-8]", axis):
                    raise StageFailure("invalid_metadata_axis")
                values = table["dimensions"].setdefault(
                    axis, {"name": row["OBJ_NM"], "values": {}}
                )["values"]
            if row["ITM_ID"] in values and values[row["ITM_ID"]] != value:
                raise StageFailure("metadata_code_conflict")
            values[row["ITM_ID"]] = value
        for axis, dim in table["dimensions"].items():
            values = dim["values"]
            parents = {v["parent_code"] for v in values.values() if v["parent_code"]}
            for code, value in values.items():
                seen = {code}
                path = [{"code": code, "name": value["name"]}]
                parent = value["parent_code"]
                while parent:
                    if parent in seen:
                        raise StageFailure("metadata_parent_cycle")
                    seen.add(parent)
                    if parent not in values:
                        table["warnings"].append(
                            {"axis": axis, "code": code, "missing_parent": parent}
                        )
                        break
                    path.insert(0, {"code": parent, "name": values[parent]["name"]})
                    parent = values[parent]["parent_code"]
                value["path"] = path
                value["is_leaf"] = code not in parents
        for row in raw["PRD"]:
            freq = FREQUENCIES.get(row["PRD_SE"])
            if freq:
                table["periods"][freq] = {
                    "start": normalize_period(row["STRT_PRD_DE"], freq),
                    "end": normalize_period(row["END_PRD_DE"], freq),
                }
        if not table["items"] or not table["periods"]:
            raise StageFailure("metadata_incomplete", "unresolved")
        self.cache[key] = table
        return table


def selected_unit(table: Json, item_id: str, dimensions: Json) -> str | None:
    values = [table["items"][item_id]] + [
        table["dimensions"][axis]["values"][code] for axis, code in dimensions.items()
    ]
    units = {v["unit"] for v in values if v["unit"]}
    if not units:
        units = set(table["units"])
    if len(units) > 1:
        raise StageFailure("unit_conflict", "unresolved")
    return next(iter(units)) if units else None
