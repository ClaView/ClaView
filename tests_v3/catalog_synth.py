"""Synthetic catalog for the catalog-layer tests.

Writes the two raw files in the format the loader reads (``search_catalog.json`` with ``documents`` and
``boK_meta_raw.jsonl`` with TBL/PRD/ITM/UNIT rows) and the axis-role input CSVs into a temporary folder.
Table IDs and names here are made up for the tests; nothing comes from questions or answers.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from claview_v3.catalog.import_overrides import machine_columns
from claview_v3.catalog.roles import OVERRIDE_FIELDS

# (table_id, title, english title, axes); axis = (obj_id, axis name or None, english, items)
# item = (item_id, name or None, english, parent_id, unit)
TABLES = [
    ("T_ORG", "기관별 잔액(말잔)", "Balances by Institution", [
        ("ORG", "기관코드별", "INSTITUTION", [
            ("o1", "비은행금융기관", "Non-bank Financial Institutions", None, None),
            ("o2", "상호저축은행", "Mutual Savings Banks", None, None),
            ("o3", "비금융법인", "Non-financial Corporations", None, None),
            ("o4", "가계", "Households", None, None),
            ("o5", "기업", "Corporations", None, None),
            ("o6", "기타기관", "Other Institutions", None, None),
            ("o7", "합계", "Total", None, None),
        ]),
    ]),
    ("T_REG", "지역별 대출금(말잔)", "Loans by Region", [
        ("REG", "지역코드별", "REGION", [
            ("r0", "합계", "Total", None, None),
            ("r1", "서울", "Seoul", "r0", None),
            ("r2", "부산", "Busan", "r0", None),
        ]),
        ("ACC", "계정항목별", "ACCOUNT", [
            ("a1", "가계대출", "Loans to Households", None, "십억원"),
            ("a2", "기업대출", "Loans to Corporations", None, "십억원"),
            ("a3", "현금통화 1)", "Currency in Circulation", None, None),
        ]),
    ]),
    ("T_IND", "산업별 생산(명목)", "Output by Industry", [
        ("IND", "업종코드별", "INDUSTRY", [
            ("i1", "C10 제조업", "Manufacturing", None, None),
            ("i2", "건설업", "Construction", None, None),
            ("i3", "기타산업", "Other Industries", None, None),
            ("i4", "농업자금", "Agricultural Funds", None, None),
        ]),
    ]),
    ("T_GEN", "산업별대출금", "Loans by Industry", [
        ("ACC", "계정항목별", "ACCOUNT", [
            ("g1", "제조업", "Manufacturing", None, None),
            ("g2", "건설업", "Construction", None, None),
            ("g3", "현금통화", "Currency", None, None),
        ]),
    ]),
    ("T_CSV", "부문별 자금(말잔)", "Funds by Sector", [
        ("ACC", "계정항목별", "ACCOUNT", [
            ("c1", "제조업", "Manufacturing", None, None),
            ("c2", "요구불예금", "Demand Deposits", None, None),
        ]),
    ]),
    ("T_L3", "예금 금리(신규취급액 기준)", "Deposit Rates", [
        ("ACC", "계정항목별", "ACCOUNT", [
            ("d0", "총예금", "Total Deposits", None, None),
            ("d1", "정기예금-2%미만", "Time Deposits - below 2%", "d0", None),
            ("d2", "비금융법인(사용)", "Non-financial Corporations (Uses)", "d0", None),
            ("d3", "가계신용", "Household Credit", "d0", None),
            ("d4", "신규취급액 기준", "New Loans Basis", "d0", None),
            ("d5", "원화", "Won", "d0", None),
            ("d6", "가계", "Households", "d0", None),
            ("d7", "가계-주택", "Households - Housing", "d0", None),
            ("d8", "저축(원천)", "Saving (Sources)", "d0", None),
            ("b0", "기관별", "By Institution", None, None),
            ("b1", "국내은행", "Domestic Banks", "b0", None),
            ("b2", "비은행금융기관", "Non-bank Financial Institutions", "b0", None),
            ("b3", "상호저축은행", "Mutual Savings Banks", "b0", None),
        ]),
    ]),
    ("T_PR", "예금은행 원화예금(예금주별)", "Deposits by Holder", [
        ("ACC", "계정항목별", "ACCOUNT", [
            ("p1", "요구불예금", "Demand Deposits", None, None),
            ("p11", "가계", "Households", "p1", None),
            ("p12", "기업", "Corporations", "p1", None),
            ("p13", "기타", "Others", "p1", None),
            ("p2", "저축성예금", "Savings Deposits", None, None),
            ("p21", "가계", "Households", "p2", None),
            ("p22", "기업", "Corporations", "p2", None),
            ("p23", "기타", "Others", "p2", None),
            ("p3", "외화예금", "Foreign Currency Deposits", None, None),
        ]),
    ]),
    ("T_PR2", "채권 보유(만기별)", "Bonds by Maturity", [
        ("ACC", "계정항목별", "ACCOUNT", [
            ("q1", "단기", "Short-term", None, None),
            ("q11", "국채", "Government Bonds", "q1", None),
            ("q2", "장기", "Long-term", None, None),
            ("q21", "국채", "Government Bonds", "q2", None),
        ]),
    ]),
    ("T_FUND", "용도별 대출금", "Loans by Purpose", [
        ("FUND", "자금구분별", "FUND", [
            ("f1", "시설자금", "Facilities", None, None),
            ("f2", "운전자금", "Working Capital", None, None),
        ]),
        ("BASE", "기준년도구분별", "BASE YEAR", [
            ("y1", "2020년기준", "2020 base", None, None),
        ]),
        ("BSI", "BSI코드별", "BSI", [
            ("s1", "업황실적BSI", "Business Conditions BSI", None, None),
        ]),
    ]),
    ("T_AGE", "연령별 부채", "Debt by Age", [
        ("AGE", "구분코드별", "CODE", [
            ("e1", "20대", "20s", None, None),
            ("e2", "30대", "30s", None, None),
        ]),
    ]),
    ("T_DUP", "중복 축 표", "Duplicate Axes", [
        ("ACC1", "계정항목별", "ACCOUNT", [("x1", "현금통화", "Currency", None, None)]),
        ("ACC2", "계정항목별", "ACCOUNT", [("x2", "요구불예금", "Demand Deposits", None, None)]),
        ("NONAME", None, None, [("DATA", None, None, None, None)]),
    ]),
    ("T_SAME1", "같은 이름 표(월)", "Same Name", [("ACC", "계정항목별", "ACCOUNT", [("z1", "현금통화", None, None, None)])]),
    ("T_SAME2", "같은 이름 표(월)", "Same Name", [("ACC", "계정항목별", "ACCOUNT", [("z1", "현금통화", None, None, None)])]),
]

LAYER1 = [
    ("계정항목별", "축마다 판정"), ("구분코드별", "축마다 판정"), ("업종코드별", "대상"), ("지역코드별", "대상"),
    ("기관코드별", "대상"), ("기준년도구분별", "기준"), ("BSI코드별", "지표"), ("자금구분별", "확인"),
]

# (table_id, axis name, role) of the user's per-axis CSV. T_NONE does not exist; T_DUP has two such axes.
GENERIC = [
    ("T_REG", "계정항목별", "지표"), ("T_CSV", "계정항목별", "지표"), ("T_L3", "계정항목별", "지표"),
    ("T_PR", "계정항목별", "지표"), ("T_PR2", "계정항목별", "지표"),
    ("T_NONE", "계정항목별", "대상"), ("T_DUP", "계정항목별", "지표"),
]

# (table_id, axis name, repeated children, parents) of the user's pressed-axis CSV.
COLLAPSED = [
    ("T_PR", "계정항목별", "가계 / 기업 / 기타", "요구불예금 / 저축성예금"),
    ("T_PR2", "계정항목별", "국채", "단기 / 장기"),
]

# (구분, 표 ID, subject, free-text recommendation, note); write_inputs fills the machine columns from the free text
# with the importer's reader, as import_overrides does for the real file.
OVERRIDES = [
    ("1 애매한 축 이름", "T_FUND", "자금구분별", "지표", ""),
    ("2 경계 축", "T_CSV", "계정항목별", "대상 축(제조업 등)", ""),
    ("2 경계 축", "T_GEN", "계정항목별", "눌린 축(위 지표: 제조업 / 아래 대상: 건설업)", ""),
    ("2 경계 축", "T_NONE", "계정항목별", "대상 축", ""),
    ("3 눌린 축", "T_PR", "계정항목별", "위 대상(예금 종류) / 아래 지표(예금주)", ""),
    ("3 눌린 축", "T_PR2", "계정항목별", "맨 위 합계 머리 / 가운데 대상 / 아래 기준", ""),
    ("4 기관/상품 겸용 대상 값", "T_L3 / T_ZZ", "가계", "지표", ""),
    ("5 구성 항목", "T_REG", "계정항목별", "지표", ""),
]


def _meta_row(table_id, title, eng, axes) -> dict:
    itm = [{"OBJ_ID": "ITEM", "OBJ_NM": "항목", "OBJ_NM_ENG": "Item", "ITM_ID": f"{table_id}_ITEM",
            "ITM_NM": title, "ITM_NM_ENG": eng, "TBL_ID": table_id, "ORG_ID": "301"}]
    for sn, (obj_id, name, axis_eng, items) in enumerate(axes, start=1):
        for item_id, item_name, item_eng, parent, unit in items:
            row = {"OBJ_ID": obj_id, "OBJ_ID_SN": str(sn), "ITM_ID": item_id, "TBL_ID": table_id, "ORG_ID": "301"}
            if name is not None:
                row["OBJ_NM"], row["OBJ_NM_ENG"] = name, axis_eng
            if item_name is not None:
                row["ITM_NM"], row["ITM_NM_ENG"] = item_name, item_eng
            if parent:
                row["UP_ITM_ID"] = parent
            if unit:
                row["UNIT_NM"], row["UNIT_ENG_NM"] = unit, "Billion Won"
            itm.append(row)
    return {"tblId": table_id, "orgId": "301", "name": title, "cat": "합성",
            "meta": {"TBL": [{"TBL_NM": title, "TBL_NM_ENG": eng}],
                     "PRD": [{"PRD_SE": "월", "STRT_PRD_DE": "2000.01", "END_PRD_DE": "2026.06"},
                             {"PRD_SE": "년", "STRT_PRD_DE": "2000", "END_PRD_DE": "2025"}],
                     "ITM": itm, "UNIT": [{"UNIT_NM": "십억원", "UNIT_NM_ENG": "Billion Won"}], "CMMT": []},
            "status": ["TBL", "PRD", "ITM", "UNIT"]}


def write_raw(folder: Path, tables=TABLES) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    search = folder / "search_catalog.json"
    meta = folder / "boK_meta_raw.jsonl"
    docs = [{"doc_id": f"301:{t[0]}", "org_id": "301", "table_id": t[0], "title": t[1], "category": "합성",
             "path": f"한국은행 > 합성 > {t[1]}"} for t in tables]
    search.write_text(json.dumps({"schema_version": "1.0", "source_date": "2026-01-01", "documents": docs},
                                 ensure_ascii=False), encoding="utf-8")
    meta.write_text("".join(json.dumps(_meta_row(*t), ensure_ascii=False) + "\n" for t in tables),
                    encoding="utf-8")
    return search, meta


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def override_row(n: int, section: str, tid: str, subject: str, rec: str, decision: str = "") -> dict:
    """One row of the overrides file: free text as given, machine columns read from it (decision likewise)."""
    read = machine_columns(section, rec)
    dec = machine_columns(section, decision) if decision else {"role": "", "level_roles": "", "value_role": "",
                                                               "value_exceptions": ""}
    return {"구분": section, "표 ID": tid, "축 이름 또는 값": subject, "recommended": rec, "confidence": "high",
            "evidence": "", "role": read["role"], "level_roles": read["level_roles"], "value_role": read["value_role"],
            "value_exceptions": read["value_exceptions"], "parse_note": read["parse_note"], "status": "draft",
            "decision_role": dec["role"], "decision_level_roles": dec["level_roles"],
            "decision_value_role": dec["value_role"], "decision_value_exceptions": dec["value_exceptions"],
            "source_row": f"synthetic.csv:{n}"}


def write_inputs(folder: Path, overrides=OVERRIDES, decisions: dict | None = None) -> dict:
    """Axis-role CSVs and overrides. decisions: {(table_id, subject): free-text decision} read into decision_*."""
    folder.mkdir(parents=True, exist_ok=True)
    decisions = decisions or {}
    layer1 = _write_csv(folder / "layer1.csv", ["축 이름", "표 수", "값 수", "역할 초안", "값 예"],
                        [{"축 이름": n, "표 수": "1", "값 수": "1", "역할 초안": r, "값 예": ""} for n, r in LAYER1])
    generic = _write_csv(folder / "generic.csv", ["표 ID", "표 이름", "축 이름", "역할 판정", "근거", "값 수", "값 예"],
                         [{"표 ID": t, "표 이름": "", "축 이름": a, "역할 판정": r, "근거": "", "값 수": "", "값 예": ""}
                          for t, a, r in GENERIC])
    collapsed = _write_csv(folder / "collapsed.csv",
                           ["표 ID", "표 이름", "축 이름", "반복되는 자식 이름 수", "반복되는 자식 이름", "그 부모들"],
                           [{"표 ID": t, "표 이름": "", "축 이름": a, "반복되는 자식 이름 수": str(len(c.split(" / "))),
                             "반복되는 자식 이름": c, "그 부모들": p} for t, a, c, p in COLLAPSED])
    ov_rows = [override_row(n, section, tid, subject, rec, decisions.get((tid, subject), ""))
               for n, (section, tid, subject, rec, _note) in enumerate(overrides, start=2)]
    overrides_path = _write_csv(folder / "overrides.csv", list(OVERRIDE_FIELDS), ov_rows)
    return {"layer1_path": layer1, "generic_path": generic, "collapsed_path": collapsed,
            "overrides_path": overrides_path}


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path
