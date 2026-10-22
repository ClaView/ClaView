"""저장 자료로 ACatalog를 만든다 (v1.8 1절). KOSIS API는 부르지 않는다.

입력: 기준본 search_catalog.json(349표 목록)과 boK_meta_raw.jsonl(메타 스냅숏).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .contract import ACatalog

FREQ_CODE = {"년": "Y", "반기": "H", "분기": "Q", "월": "M"}
_PUNCT = re.compile(r"[\s·ㆍ,()\[\]{}<>/\-_.:;'\"]+")
_ABBR = re.compile(r"([가-힣][가-힣\s·]{1,30}?)\s*\(([A-Za-z][A-Za-z0-9]{1,9})\)")


def nospace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def norm_v1(text: str) -> str:
    """검색 정규화 판본 v1: NFKC, 소문자, 공백·구두점 제거."""
    return _PUNCT.sub("", unicodedata.normalize("NFKC", text).lower())


def grams_v1(text_norm: str) -> frozenset:
    """정규화본(v1)의 문자 2-gram. 한 글자 이름은 그 글자 하나."""
    if len(text_norm) < 2:
        return frozenset({text_norm}) if text_norm else frozenset()
    return frozenset(text_norm[i : i + 2] for i in range(len(text_norm) - 1))


def _idf(docs: dict) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for gs in docs.values():
        for g in gs:
            counts[g] += 1
    n = len(docs)
    return {g: math.log(1 + n / c) for g, c in counts.items()}


def build_lexicon(title_index: dict, meta_store: dict) -> dict:
    """S1·S2 문자 2-gram 색인 (단계 1). 표명·경로·분류값 이름(ITEM 행 제외)."""
    title = {t: grams_v1(norm_v1(d["title"])) for t, d in title_index.items()}
    path = {t: grams_v1(norm_v1(d["path"])) for t, d in title_index.items()}
    items = {}
    for t, meta in meta_store.items():
        names = sorted({it["name_norm"] for it in meta["items"] if it["name_norm"]})
        items[t] = tuple((name, grams_v1(name)) for name in names)
    item_docs = {t: frozenset().union(*(g for _, g in rows)) if rows else frozenset() for t, rows in items.items()}
    return {"title": title, "path": path, "items": items,
            "idf": {"title": _idf({t: title[t] | path[t] for t in title}), "item": _idf(item_docs)}}


_BRACKET = re.compile(r"\s*\(.*?\)\s*")


def build_families(title_index: dict) -> dict:
    """S6 형제 묶음 두 판본 (D2 비교용, 단계 3). 표명에서 괄호를 비탐욕으로 지운 이름이 같으면 형제다.

    - parent_v1: 같은 부모 경로 안에서만 (docs/02 저장 EDA #5: 85묶음 199표)
    - stem_v1: 부모 경로와 무관
    """
    out = {}
    for version, keyed in (("parent_v1", True), ("stem_v1", False)):
        groups: dict[tuple, list] = defaultdict(list)
        for tid, d in title_index.items():
            stem = _BRACKET.sub(" ", d["title"]).strip()
            groups[(d["path"] if keyed else "", stem)].append(tid)
        relation = "same_parent_stem" if keyed else "same_stem"
        fam = {}
        for members in groups.values():
            if len(members) < 2:
                continue
            for tid in members:
                fam[tid] = [{"table_id": t, "relation_type": relation} for t in sorted(members) if t != tid]
        out[version] = fam
    return out


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _prd_by_freq(prd_rows: list) -> dict:
    out = {}
    for row in prd_rows or []:
        freq = FREQ_CODE.get(row.get("PRD_SE", ""))
        if freq:
            out[freq] = {"start": row.get("STRT_PRD_DE"), "end": row.get("END_PRD_DE")}
    return out


def _meta_entry(doc: dict, raw: dict | None, snapshot: str) -> dict:
    meta = (raw or {}).get("meta", {})
    status = (raw or {}).get("status", {})
    itm_rows = meta.get("ITM") or []
    item_itm = next(
        (
            {"itm_id": r.get("ITM_ID"), "name": r.get("ITM_NM"), "unit": r.get("UNIT_NM")}
            for r in itm_rows
            if r.get("OBJ_ID") == "ITEM"
        ),
        None,
    )
    dims, seen_dims, items = [], set(), []
    for r in itm_rows:
        if r.get("OBJ_ID") == "ITEM":
            continue  # ITEM 행은 분류값이 아니다 (v1.8 2절 meta.items)
        key = (r.get("OBJ_ID"), r.get("OBJ_ID_SN"))
        if key not in seen_dims:
            seen_dims.add(key)
            dims.append({"obj_id": key[0], "obj_id_sn": key[1], "obj_nm": r.get("OBJ_NM")})
        name = r.get("ITM_NM") or ""
        items.append(
            {
                "obj_id": r.get("OBJ_ID"),
                "item_id": r.get("ITM_ID"),
                "name_raw": name,
                "name_nospace": nospace(name),
                "name_norm": norm_v1(name),
                "up_item_id": r.get("UP_ITM_ID"),
                "unit": r.get("UNIT_NM"),
            }
        )
    prd = _prd_by_freq(meta.get("PRD"))
    units = meta.get("UNIT") or []
    missing = sorted(k for k, v in status.items() if v != "ok") if raw else ["TBL", "PRD", "ITM", "UNIT", "CMMT"]
    return {
        "org_id": doc["org_id"],
        "title": doc["title"],
        "catalog_paths": list(doc["catalog_paths"]),
        "prd_by_freq": prd,
        "coverage_end": {f: v["end"] for f, v in prd.items()},
        "publication_status": "unknown",
        "item_itm": item_itm,
        "dims": dims,
        "items": items,
        "unit": units[0].get("UNIT_NM") if units else None,
        "cmmt": [{"text": (c.get("CMMT_DC") or "").strip()} for c in meta.get("CMMT") or []],
        "source_snapshot": snapshot,
        "fetched_at": "unknown",
        "missing_fields": missing,
    }


def build_catalog(search_catalog: Path, meta_raw: Path) -> ACatalog:
    raw_catalog = json.loads(search_catalog.read_text(encoding="utf-8"))
    meta_rows = {}
    for line in meta_raw.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            meta_rows[row["tblId"]] = row

    catalog_snapshot = f"{raw_catalog.get('source_date', 'unknown')}#{_file_hash(search_catalog)}"
    meta_snapshot = f"{raw_catalog.get('source_date', 'unknown')}#{_file_hash(meta_raw)}"

    documents, title_index, meta_store = {}, {}, {}
    for doc in raw_catalog["documents"]:
        tid = doc["table_id"]
        documents[tid] = {"org_id": doc["org_id"], "title": doc["title"], "catalog_paths": [doc["path"]]}
        title_index[tid] = {
            "title": doc["title"],
            "path": doc["path"],
            "text_norm": norm_v1(doc["title"] + " " + doc["path"]),
        }
        meta_store[tid] = _meta_entry(documents[tid], meta_rows.get(tid), meta_snapshot)

    entries: dict[str, list] = defaultdict(list)
    for tid, meta in meta_store.items():
        for it in meta["items"]:
            if it["name_norm"]:
                entries[it["name_norm"]].append((tid, it["obj_id"], it["item_id"]))
    df = {name: len({t for t, _, _ in rows}) for name, rows in entries.items()}

    abbr = []
    for tid, meta in meta_store.items():
        fields = [("title", meta["title"])] + [("item", it["name_raw"]) for it in meta["items"]]
        for field_name, text in fields:
            for m in _ABBR.finditer(text):
                abbr.append({"long": m.group(1).strip(), "abbr": m.group(2), "table_id": tid, "source_field": field_name})

    from .q6g import tables_from_rows
    from .candidate_rules import catalog_properties
    return ACatalog(
        documents=documents,
        title_index=title_index,
        item_index={"entries": {"v1": dict(entries)}, "df": {"v1": df}},
        families=build_families(title_index),  # 판본 선택(D2)은 config.rule_versions.family, 기본 none
        lexicon={"v1": build_lexicon(title_index, meta_store)},
        meta_store=meta_store,
        titles=frozenset(d["title"] for d in documents.values()),
        aliases=(),  # 승인 별칭 0/79
        abbr_pairs=tuple({(a["long"], a["abbr"], a["table_id"], a["source_field"]): a for a in abbr}.values()),
        catalog_snapshot=catalog_snapshot,
        meta_snapshot=meta_snapshot,
        q6g_tables=tables_from_rows(documents, meta_rows),
        arbitration_properties=catalog_properties(raw_catalog["documents"], meta_store),
    )


def default_catalog(repo_root: Path) -> ACatalog:
    base = repo_root / "reference" / "baseline-1006" / "data"
    return build_catalog(base / "catalog" / "search_catalog.json", base / "raw" / "boK_meta_raw.jsonl")
