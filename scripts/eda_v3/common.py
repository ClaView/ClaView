"""Shared loaders, normalizers and the four-layer axis role rules for the eda_v3 study.

Offline only: reads the stored catalog/meta snapshot, the user's axis-role draft, and the golden + development
questions. The official (new200) rows are dropped at load time and never counted or written. No network, no model.
Standard library only.
"""

from __future__ import annotations

import collections
import csv
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "data"
META = REF / "raw/boK_meta_raw.jsonl"
CATALOG = REF / "catalog/search_catalog.json"
MAPPINGS = REF / "mappings"
DRAFT = ROOT / "data/a/axis-roles-20261016"
CORPUS = ROOT.parent / "clabi/claview-runs/q-improve-20261013-01/jt/work/sh/data"
OUT = ROOT / "runs/eda_v3"
DATASETS = ("golden", "development")  # official (new200) is never loaded into the study

GENERIC_AXES = ("계정항목별", "계정코드별", "구분코드별", "금액구분코드별")


# ------------------------------------------------------------------ text

_PUNCT = re.compile(r"[\s·ㆍ・,()\[\]{}<>/\-_.:;'\"~]+")
_FOOTNOTE = re.compile(r"(?:\s*\d{1,2}\))+\s*$")
_LEAD_NUM = re.compile(r"^\s*(?:\(\d+\)|\d+\)|[①-⑳]|\[\d+\]|(?:[A-Z]\s*-\s*)?(?:(?:\d+|[가-하])\.)+(?!\d))\s*")
_LEAD_CODE = re.compile(r"^(?:[A-Z](?:\d+(?:-\d+)?)?\s+)+(?=[가-힣])")
_PAREN = re.compile(r"\s*[(\[][^()\[\]]*[)\]]")


def nfkc(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "")


def nospace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def key(text: str | None) -> str:
    """Match key: NFKC, lower case, spaces and punctuation removed."""
    return _PUNCT.sub("", nfkc(text).lower())


def clean_value(text: str | None) -> str:
    """Display-level cleanup of a value name: footnote marks, list numbering, industry code prefixes."""
    s = nfkc(text).strip()
    s = _FOOTNOTE.sub("", s)
    prev = None
    while prev != s:
        prev = s
        s = _LEAD_NUM.sub("", s)
    s = _LEAD_CODE.sub("", s)
    return s.strip()


def vkey(text: str | None) -> str:
    return key(clean_value(text))


def bkey(text: str | None) -> str:
    """Base key: cleaned value with every parenthesised part removed ("비금융법인(사용)" -> "비금융법인")."""
    return key(strip_parens(clean_value(text)))


def paren_keys(text: str | None) -> list[str]:
    return [key(x) for x in re.findall(r"[(\[]([^()\[\]]*)[)\]]", clean_value(text)) if key(x)]


def strip_parens(text: str) -> str:
    prev = None
    s = text
    while prev != s:
        prev = s
        s = _PAREN.sub("", s)
    return s.strip()


def paren_front(title: str) -> str:
    """Table name before its first parenthesis (the trailing qualifier)."""
    t = nfkc(title)
    i = t.find("(")
    return t[:i].strip() if i > 0 else t.strip()


# ------------------------------------------------------------------ loaders


def load_meta() -> dict:
    """tblId -> {name, name_eng, items, units, prd}. ITEM rows and the unnamed axis are kept apart."""
    tables = {}
    for line in META.read_text("utf8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        meta = row.get("meta") or {}
        tbl = (meta.get("TBL") or [{}])[0]
        itm = [x for x in meta.get("ITM") or [] if isinstance(x, dict)]
        tables[row["tblId"]] = {
            "id": row["tblId"],
            "name": row.get("name") or tbl.get("TBL_NM") or "",
            "name_eng": tbl.get("TBL_NM_ENG") or "",
            "item_rows": [x for x in itm if x.get("OBJ_ID") == "ITEM"],
            "values": [x for x in itm if x.get("OBJ_ID") != "ITEM"],
            "units": [u for u in meta.get("UNIT") or [] if isinstance(u, dict)],
            "prd": meta.get("PRD") or [],
        }
    return tables


def load_catalog() -> dict:
    data = json.loads(CATALOG.read_text("utf8"))
    return {d["table_id"]: d for d in data["documents"]}


def axes_of(table: dict) -> "collections.OrderedDict[tuple[str, str|None], list[dict]]":
    axes = collections.OrderedDict()
    for x in table["values"]:
        axes.setdefault((x["OBJ_ID"], x.get("OBJ_NM")), []).append(x)
    return axes


def load_questions() -> list[dict]:
    """Golden + development questions only. Other datasets are discarded before anything is counted."""
    rows = json.loads((CORPUS / "questions_417.json").read_text("utf8"))
    return [q for q in rows if q.get("dataset") in DATASETS]


def load_groups() -> list[dict]:
    out = []
    for line in (CORPUS / "Q_candidate_group.jsonl").read_text("utf8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("dataset") in DATASETS:
            out.append(row)
    return out


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0].keys()) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf8")
