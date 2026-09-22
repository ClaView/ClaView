#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KOSIS 한국은행 소관 통계표 관찰 스크립트  (1주차 - A 작업)  [로컬 실행용]
------------------------------------------------------------------------
하는 일:
  1) 통계목록 API로 '한국은행' 소관 통계표를 전부 수집 → 개수·분류 집계
  2) 통계표설명(메타) API로 표별 항목/분류/단위/수록기간(주기) 관찰
  3) 통계자료 API로 샘플 표의 실제 수치 조회 (응답 구조 확인)

⚠ 네트워크: KOSIS(kosis.kr)는 해외/클라우드 IP를 막는 경우가 많음.
   → Colab이 아니라 '한국에 있는 로컬 PC'에서 실행할 것.

준비물:
  - KOSIS 공유서비스 오픈API 인증키 (https://kosis.kr/openapi 에서 무료 발급)
  - Python 3.9+, requests   (pip install requests)

실행:
  # Windows(PowerShell):  $env:KOSIS_API_KEY="발급키";  python kosis_explore.py
  # Mac/Linux:            export KOSIS_API_KEY=발급키;   python3 kosis_explore.py
  #
  #   python kosis_explore.py                # 전체 관찰
  #   python kosis_explore.py --list-only    # 목록 수집·집계까지만
  #   python kosis_explore.py --child-root 301_XXXX   # 하강 루트를 직접 지정
  #   python kosis_explore.py --meta-samples 8        # 메타 관찰 샘플 개수

산출물(현재 폴더):
  - boK_tables.csv        수집한 한국은행 통계표 전체 목록
  - boK_tables.json       동일 데이터(원본 필드 보존)
  - boK_summary.txt       개수·분류 집계 요약
  - boK_meta_sample.json  표별 메타(항목/분류/단위/주기) 샘플
  - boK_data_sample.json  통계자료 샘플 조회 결과
"""

import os
import sys
import csv
import json
import time
import argparse
from collections import Counter

import requests

# ------------------------------------------------------------------
# KOSIS Open API 엔드포인트
# ------------------------------------------------------------------
BASE = "https://kosis.kr/openapi"
URL_LIST = f"{BASE}/statisticsList.do"                     # 통계목록
URL_META = f"{BASE}/statisticsData.do"                     # 통계표설명(메타)   method=getMeta
URL_DATA = f"{BASE}/Param/statisticsParameterData.do"      # 통계자료(파라미터)  method=getList

VW_CD = "MT_OTITLE"          # 기관별(작성기관 기준) 목록 뷰
TARGET_ORG_NAME = "한국은행"
EMPTY_ERRS = {"30"}          # err 30 = "데이터가 존재하지 않습니다" → 빈 노드(리프). 에러 아님
SLEEP = 0.15                 # 테스트키 QPM 제한 대비 호출 간격(초)

SESSION = requests.Session()


def api_get(url, params, tolerate_empty=True):
    """공통 GET. err 30(데이터 없음)은 빈 리스트로 처리해 순회가 끊기지 않게 함."""
    p = dict(params)
    p.setdefault("format", "json")
    p.setdefault("jsonVD", "Y")
    p["apiKey"] = API_KEY
    r = SESSION.get(url, params=p, timeout=30)
    r.raise_for_status()
    time.sleep(SLEEP)
    try:
        data = r.json()
    except json.JSONDecodeError:
        raise RuntimeError("JSON 파싱 실패. 응답 앞부분: " + r.text[:300])
    if isinstance(data, dict) and ("err" in data or "errMsg" in data):
        if tolerate_empty and str(data.get("err")) in EMPTY_ERRS:
            return []
        raise RuntimeError("KOSIS API 에러: " + str(data))
    return data


# ------------------------------------------------------------------
# 1) 통계목록 API
# ------------------------------------------------------------------
def list_children(parent_list_id=None):
    """vwCd=MT_OTITLE 트리에서 parent_list_id 하위 노드 목록을 반환.
       반환 노드: LIST 노드(하위 목록) 또는 TBL 노드(실제 통계표, TBL_ID 존재)."""
    params = {"method": "getList", "vwCd": VW_CD}
    if parent_list_id:
        params["parentListId"] = parent_list_id
    data = api_get(URL_LIST, params)
    if isinstance(data, list):
        return data
    return [data] if data else []


def is_table(node):
    return bool(node.get("TBL_ID"))


def find_org_node(org_name=TARGET_ORG_NAME):
    """MT_OTITLE 최상위(기관 목록)에서 '한국은행' 노드를 찾는다.
       최상위 LIST_ID는 orgId(예: 301)와 같다."""
    top = list_children(None)
    print(f"  최상위 기관 노드 {len(top)}개")
    for node in top:
        if org_name in node.get("LIST_NM", ""):
            return node
    return None


def resolve_child_root(node):
    """한국은행 노드의 실제 하위 목록 루트(parentListId)를 찾는다.
       최상위 LIST_ID(=orgId, 예: 301)를 그대로 넣으면 데이터가 없을 수 있어
       'orgId_목록코드' 형태 후보를 순서대로 시도한다."""
    base = str(node.get("LIST_ID"))
    candidates = [base, base + "_MT_DTITLE", base + "_MT_OTITLE", base + "_00"]
    for cid in candidates:
        rows = list_children(cid)
        if rows:
            print(f"  하강 성공: parentListId='{cid}' (rows={len(rows)})")
            return cid
    print("  하강 실패 — 시도한 후보:", candidates)
    return None


def collect_tables(root_list_id):
    """root 아래를 재귀적으로 훑어 모든 통계표(TBL 노드) 수집."""
    tables, stack, seen, path_name = [], [root_list_id], set(), {}
    while stack:
        lid = stack.pop()
        if lid in seen:
            continue
        seen.add(lid)
        for node in list_children(lid):
            if is_table(node):
                node["_PARENT_LIST_ID"] = lid
                node["_PARENT_LIST_NM"] = path_name.get(lid, "")
                tables.append(node)
            else:
                child = node.get("LIST_ID")
                if child and child not in seen:
                    path_name[child] = node.get("LIST_NM", "")
                    stack.append(child)
    return tables


# ------------------------------------------------------------------
# 2) 통계표설명(메타) API — 항목/분류/단위/수록기간
#    type=TBL(표명) / PRD(주기·기간) / ITM(분류·항목) / UNIT(단위) / CMMT(주석) / ORG(기관)
# ------------------------------------------------------------------
def get_meta(org_id, tbl_id, mtype):
    return api_get(URL_META, {"method": "getMeta", "orgId": org_id, "tblId": tbl_id, "type": mtype})


def observe_meta(org_id, tbl_id):
    out = {"orgId": org_id, "tblId": tbl_id}
    for mtype in ("TBL", "PRD", "ITM", "UNIT"):
        try:
            out[mtype] = get_meta(org_id, tbl_id, mtype)
        except Exception as e:
            out[mtype] = {"error": str(e)}
    return out


# ------------------------------------------------------------------
# 3) 통계자료 API — 실제 수치 (응답 구조 확인용 샘플)
# ------------------------------------------------------------------
def get_data_sample(org_id, tbl_id, obj_l1, itm_id, prd_se="M", recent=6):
    return api_get(URL_DATA, {
        "method": "getList", "orgId": org_id, "tblId": tbl_id,
        "objL1": obj_l1, "itmId": itm_id, "prdSe": prd_se, "newEstPrdCnt": recent,
    })


def as_list(x):
    return x if isinstance(x, list) else ([x] if x else [])


# ------------------------------------------------------------------
# 집계·저장
# ------------------------------------------------------------------
def save_tables(tables):
    with open("boK_tables.json", "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2)
    cols = ["ORG_ID", "TBL_ID", "TBL_NM", "_PARENT_LIST_NM", "_PARENT_LIST_ID", "VW_CD"]
    with open("boK_tables.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ORG_ID", "TBL_ID", "TBL_NM", "분류(목록명)", "목록ID", "VW_CD"])
        for t in tables:
            w.writerow([t.get(c, "") for c in cols])


def summarize(tables):
    lines = [f"한국은행 소관 통계표 총 개수: {len(tables)}", ""]
    lines.append("[상위 분류(부모 목록)별 표 개수]")
    for name, cnt in Counter(t.get("_PARENT_LIST_NM", "(미상)") for t in tables).most_common():
        lines.append(f"  {cnt:4d}  {name}")
    lines.append("")
    lines.append("[ORG_ID 분포] (한국은행이 여러 orgId를 쓰는지 확인)")
    for oid, cnt in Counter(str(t.get("ORG_ID")) for t in tables).most_common():
        lines.append(f"  {cnt:4d}  orgId={oid}")
    text = "\n".join(lines)
    with open("boK_summary.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return text


# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child-root", help="한국은행 하위 목록 루트(parentListId)를 직접 지정")
    ap.add_argument("--list-only", action="store_true", help="목록 수집·집계까지만")
    ap.add_argument("--meta-samples", type=int, default=5, help="메타 관찰 샘플 표 개수")
    args = ap.parse_args()

    print("1) 한국은행 기관 노드 탐색 …")
    root = find_org_node()
    if not root:
        print("  최상위에서 '한국은행' 노드를 못 찾음. list_children(None) 응답을 확인하세요.")
        sys.exit(1)
    print("  한국은행 노드:", json.dumps(root, ensure_ascii=False))
    org_id = str(root.get("LIST_ID"))   # 한국은행 orgId (= 최상위 LIST_ID, 예: 301)

    print("2) 하위 목록 루트 탐색 …")
    child_root = args.child_root or resolve_child_root(root)
    if not child_root:
        print("  하위 목록 루트를 못 찾음. 위 '한국은행 노드' 출력을 공유하거나")
        print("  --child-root 301_XXXX 형태로 직접 지정해 다시 실행하세요.")
        sys.exit(1)

    print(f"3) 통계표 수집 … (parentListId={child_root})")
    tables = collect_tables(child_root)
    save_tables(tables)
    print(f"   orgId={org_id} | 수집한 통계표 개수: {len(tables)}")
    print()
    print(summarize(tables))

    if args.list_only or not tables:
        return

    print(f"\n4) 표별 메타(항목/분류/단위/주기) 샘플 {args.meta_samples}개 관찰 …")
    metas = []
    for t in tables[: args.meta_samples]:
        print(f"   - {t['TBL_ID']}  {t.get('TBL_NM','')}")
        metas.append(observe_meta(t["ORG_ID"], t["TBL_ID"]))
    with open("boK_meta_sample.json", "w", encoding="utf-8") as f:
        json.dump(metas, f, ensure_ascii=False, indent=2)

    print("\n5) 통계자료 샘플 조회 (첫 표, ITM 메타에서 코드 추출) …")
    try:
        first = tables[0]
        itm_meta = as_list(get_meta(first["ORG_ID"], first["TBL_ID"], "ITM"))
        prd_meta = as_list(get_meta(first["ORG_ID"], first["TBL_ID"], "PRD"))
        obj_l1 = next((r.get("OBJ_ID") for r in itm_meta if r.get("OBJ_ID")), None)
        itm_id = next((r.get("ITM_ID") for r in itm_meta if r.get("ITM_ID")), None)
        prd_se = next((r.get("PRD_SE") for r in prd_meta if r.get("PRD_SE")), "M")
        sample = get_data_sample(first["ORG_ID"], first["TBL_ID"], obj_l1, itm_id, prd_se)
        with open("boK_data_sample.json", "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)
        n = len(sample) if isinstance(sample, list) else 1
        print(f"   조회 성공 (records={n}) → boK_data_sample.json")
    except Exception as e:
        print(f"   자료 샘플 조회 실패(파라미터 조정 필요): {e}")

    print("\n완료. 생성 파일: boK_tables.csv/.json, boK_summary.txt, boK_meta_sample.json, boK_data_sample.json")


if __name__ == "__main__":
    API_KEY = os.environ.get("KOSIS_API_KEY", "").strip()
    if not API_KEY:
        print("환경변수 KOSIS_API_KEY 를 먼저 설정하세요.")
        print('  Windows(PowerShell):  $env:KOSIS_API_KEY="발급키"')
        print("  Mac/Linux:            export KOSIS_API_KEY=발급키")
        sys.exit(1)
    main()
