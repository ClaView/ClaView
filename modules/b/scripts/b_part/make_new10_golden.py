"""Builds eval/cases/b/golden_set_v2_new10.json: 10 B-stage questions on tables that the
22 original goldens do not use. Codes and names are read from the stored metadata, so a
typo in a code cannot slip into the answer key. Run from the project root:

    python modules/b/scripts/b_part/make_new10_golden.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
META = ROOT / "data" / "raw" / "boK_meta_raw.jsonl"
STATUS = ROOT / "data" / "catalog" / "boK_meta_status.csv"
OUT = ROOT / "eval" / "cases" / "b" / "golden_set_v2_new10.json"

# (case, table, query, purpose, {axis name keyword: [code suffixes]}, frequency, start, end, unit)
CASES = [
    (
        "CV-NEW-01",
        "DT_403Y005",
        "2020~2025년 연간 소득교역조건지수 추이를 꺾은선 그래프로 보여줘",
        "비슷한 이름 2개 중 하나 고르기(순상품교역조건지수 vs 소득교역조건지수), 연간",
        {"교역조건": ["B"]},
        "Y",
        "2020",
        "2025",
        "2020=100",
    ),
    (
        "CV-NEW-02",
        "DT_512Y013",
        "최근 2년간 제조업 업황 BSI(실적)를 월별 꺾은선 그래프로 보여줘",
        "2개 분류축 동시 선택, 상위(전산업)·하위(제조업) 중 질문에 쓰인 값, 상대 기간",
        {"업종": ["C0000"], "BSI": ["AA"]},
        "M",
        "202409",
        "202608",
        None,
    ),
    (
        "CV-NEW-03",
        "DT_151Y002",
        "2024년 이후 예금취급기관 가계대출을 업권별로 나눠 월별 막대그래프로 보여줘",
        "깊이를 말하지 않은 '~별' 펼침(최하위 6개), 3단 계층",
        {"계정항목": ["1111000", "1112000", "1113100", "1113200", "1113300", "1114000"]},
        "M",
        "202401",
        "202607",
        "십억원",
    ),
    (
        "CV-NEW-04",
        "DT_171Y002",
        "2023년 이후 금융기관유동성(Lf, 말잔 원계열)을 상품별로 나눠 월별 누적 막대그래프로 그려줘",
        "'~별' 펼침, 형제 깊이가 다른 계층(M2는 한 단계 아래, 비예금취급기관은 바로 아래)",
        {"계정항목": ["LE01100", "LE01200", "LE02000"]},
        "M",
        "202301",
        "202607",
        "십억원",
    ),
    (
        "CV-NEW-05",
        "DT_131Y013",
        "최근 5년간 예금은행의 제조업 대출금 추이를 분기별 꺾은선 그래프로 보여줘",
        "하위 값이 많은 상위 값 하나만 고르기(펼치면 안 됨), 상대 기간 5년 분기",
        {"계정항목": ["BDDE13"]},
        "Q",
        "202103",
        "202602",
        "십억원",
    ),
    (
        "CV-NEW-06",
        "DT_181Y006",
        "2018년 이후 은행 차주당 가계대출 잔액을 연령대별로 연도별 비교하는 그래프를 그려줘",
        "2개 분류축 중 한 축은 고정(은행), 다른 축은 상·하위가 없는 5개 값 전부",
        {"구분코드": ["C001", "C002", "C003", "C004", "C005"], "계정항목": ["E010"]},
        "Y",
        "2018",
        "2025",
        "십만원",
    ),
    (
        "CV-NEW-07",
        "DT_121Y002",
        "최근 3년간 예금은행의 1년 만기 정기예금 금리(신규취급액 기준)를 "
        "월별 꺾은선 그래프로 보여줘",
        "28개 값 중 이름이 비슷한 항목 고르기(정기예금 / 정기예금(1년) / 정기적금(1-2년))",
        {"계정항목": ["BEABAA2118"]},
        "M",
        "202308",
        "202607",
        "연리%",
    ),
    (
        "CV-NEW-08",
        "DT_311Y006",
        "2020년 이후 순대외채권을 단기와 장기로 나눠 분기별 막대그래프로 보여줘",
        "상위 값의 하위 중 질문이 이름으로 가리킨 2개만 선택(나머지 부문별 단기·장기는 제외)",
        {"계정코드": ["C500000", "C600000"]},
        "Q",
        "202001",
        "202602",
        "백만달러",
    ),
    (
        "CV-NEW-09",
        "DT_511Y003",
        "최근 3년간 향후 1년 기대인플레이션율 추이를 월별로 보여줘",
        "하위 값(응답 구성비)이 있는 상위 값 하나를 펼치지 않고 선택, 상대 기간",
        {"CSI": ["FMB"]},
        "M",
        "202309",
        "202608",
        "%",
    ),
    (
        "CV-NEW-10",
        "DT_151Y005",
        "최근 2년간 예금취급기관의 주택관련대출과 기타대출을 월별로 나눠 비교해줘",
        "같은 이름이 여러 곳에 있는 값(주택관련대출-예금은행 등) 중 상위 2개를 이름으로 고르기",
        {"계정항목": ["11100A0", "11100B0"]},
        "M",
        "202408",
        "202607",
        "십억원",
    ),
]


def clean(name: str) -> str:
    import re

    return re.sub(r"\s*\d\)\s*$", "", name).strip()


def unit_of(d, fallback):
    listed = d["meta"].get("UNIT") or []
    return (listed[0]["UNIT_NM"] if listed else "") or fallback or None


def main():
    tables = {c[1] for c in CASES}
    meta = {}
    for line in META.open(encoding="utf-8"):
        d = json.loads(line)
        if d["tblId"] in tables:
            meta[d["tblId"]] = d
    units = {
        r["tblId"]: r["단위"]
        for r in csv.DictReader(STATUS.open(encoding="utf-8-sig"))
        if r["tblId"] in tables
    }
    cases = []
    for case_id, table, query, purpose, picks, freq, start, end, unit in CASES:
        d = meta[table]
        items = d["meta"]["ITM"]
        item = next(i for i in items if i.get("OBJ_ID") == "ITEM")
        axes = {}
        for i in items:
            if i is item:
                continue
            axes.setdefault((int(i.get("OBJ_ID_SN") or 1), i["OBJ_NM"]), []).append(i)
        ordered = [vals for _key, vals in sorted(axes.items())]
        names = [key[1] for key in sorted(axes)]
        chosen = {}
        for key, suffixes in picks.items():
            idx = next(k for k, n in enumerate(names) if key in n)
            found = []
            for s in suffixes:
                hit = [i for i in ordered[idx] if i["ITM_ID"].split(".")[-1] == s]
                assert len(hit) == 1, (case_id, key, s, len(hit))
                found.append(hit[0])
            chosen[idx] = found
        combos = [{}]
        for idx, vals in chosen.items():
            combos = [{**c, idx: v} for c in combos for v in vals]
        selections = []
        for n, combo in enumerate(combos, 1):
            dims = {
                f"objL{idx + 1}": {"code": v["ITM_ID"], "name": clean(v["ITM_NM"])}
                for idx, v in sorted(combo.items())
            }
            label = " / ".join(v["name"] for v in dims.values())
            selections.append(
                {
                    "series_id": f"{case_id}-S{n}",
                    "label": label,
                    "org_id": d["orgId"],
                    "table_id": table,
                    "item_id": item["ITM_ID"],
                    "dimensions": dims,
                    "period": {"frequency": freq, "start": start, "end": end},
                    "unit": unit_of(d, units[table]),
                }
            )
        cases.append(
            {
                "case_id": case_id,
                "query": query,
                "source": {
                    "used_part": "신규 검증용 질문(공식 게시물 출처 없음): " + purpose,
                    "url": "해당 없음",
                },
                "expected": {
                    "decision": "select",
                    "A": {
                        "tables": [
                            {"org_id": d["orgId"], "table_id": table, "table_name": d["name"]}
                        ]
                    },
                    "B": {"selections": selections},
                },
            }
        )
    OUT.write_text(
        json.dumps(
            {
                "schema_version": "claview-ab/0.4-draft",
                "dataset_id": "claview-b-new10-v1",
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "wrote",
        OUT,
        len(cases),
        "cases,",
        sum(len(c["expected"]["B"]["selections"]) for c in cases),
        "series",
    )


if __name__ == "__main__":
    main()
