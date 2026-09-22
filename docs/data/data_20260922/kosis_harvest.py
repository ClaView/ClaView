# -*- coding: utf-8 -*-
"""
KOSIS 한국은행 통계표 메타 전수 수집 & 확보 현황 정리  (1주차 · 할일 1·2)
------------------------------------------------------------------------
입력 : boK_tables.csv  (kosis_explore.py 가 생성 — 표ID·표명·분류·orgId)
출력 :
  - boK_meta_raw.jsonl     표별 메타 원본(TBL/PRD/ITM/UNIT/CMMT) — 재개용 캐시
  - boK_meta_status.csv    표별 상태 + 항목/분류/주기/단위/주석 요약 (엑셀에서 분석)

특징: 중단돼도 이어받기(이미 수집한 표는 건너뜀), 레이트리밋 대비 간격·재시도.

준비(Windows PowerShell):
  pip install requests
  $env:KOSIS_API_KEY="발급키"
  # boK_tables.csv 가 없으면 먼저:  python kosis_explore.py
  python kosis_harvest.py
"""
import os, sys, csv, json, time, re
import requests

# KOSIS 응답에 규격 위반 이스케이프(예: 원화기호가 "\100 million" 처럼 홑 백슬래시)가
# 섞여 표준 JSON 파서가 실패하는 표가 있음 → 허용되지 않는 백슬래시를 이중화해 보정.
_BAD_ESC = re.compile(r'\\(?![\"\\/bfnrtu])')
def _loads_lenient(text):
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_BAD_ESC.sub(r'\\\\', text))

BASE = "https://kosis.kr/openapi"
URL_META = f"{BASE}/statisticsData.do"       # method=getMeta, type=TBL/PRD/ITM/UNIT/CMMT
EMPTY_ERRS = {"30"}                           # err30 = 데이터 없음 → 빈응답
SLEEP = 0.15
SESSION = requests.Session()
API_KEY = os.environ.get("KOSIS_API_KEY", "").strip()

RAW = "boK_meta_raw.jsonl"
OUT = "boK_meta_status.csv"
META_TYPES = ["TBL", "PRD", "ITM", "UNIT", "CMMT"]


def get_meta(org_id, tbl_id, mtype):
    """반환: (status, data)  status ∈ ok/empty/error"""
    params = {"method": "getMeta", "orgId": org_id, "tblId": tbl_id,
              "type": mtype, "format": "json", "jsonVD": "Y", "apiKey": API_KEY}
    for attempt in range(3):
        try:
            r = SESSION.get(URL_META, params=params, timeout=30)
            r.raise_for_status()
            time.sleep(SLEEP)
            data = _loads_lenient(r.text)
            if isinstance(data, dict) and ("err" in data or "errMsg" in data):
                if str(data.get("err")) in EMPTY_ERRS:
                    return "empty", []
                # 그 외 에러코드 → 재시도 후 error
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1)); continue
                return "error", data
            rows = data if isinstance(data, list) else ([data] if data else [])
            return ("ok" if rows else "empty"), rows
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1)); continue
            return "error", {"exception": str(e)}


def as_list(x): return x if isinstance(x, list) else ([x] if x else [])


def load_tables():
    if not os.path.exists("boK_tables.csv"):
        print("boK_tables.csv 가 없습니다. 먼저 python kosis_explore.py 를 실행하세요.")
        sys.exit(1)
    rows = []
    with open("boK_tables.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({"orgId": r.get("ORG_ID", "301"),
                         "tblId": r.get("TBL_ID"),
                         "name": r.get("TBL_NM", ""),
                         "cat": r.get("분류(목록명)", "")})
    return [r for r in rows if r["tblId"]]


def done_ids():
    ids = set()
    if os.path.exists(RAW):
        with open(RAW, encoding="utf-8") as f:
            for line in f:
                try: ids.add(json.loads(line)["tblId"])
                except Exception: pass
    return ids


def retry_errors():
    """이전 수집에서 error 였던 표를 jsonl 에서 제거 → 다음 harvest 가 재수집."""
    if not os.path.exists(RAW):
        print(RAW, "가 없습니다. 먼저 python kosis_harvest.py 를 실행하세요."); return
    keep, drop = [], []
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if "error" in (r.get("status") or {}).values():
                drop.append(r.get("tblId"))
            else:
                keep.append(line.rstrip("\n"))
    if not drop:
        print("재수집할 오류 표가 없습니다."); return
    with open(RAW, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + ("\n" if keep else ""))
    print(f"오류 {len(drop)}개 재수집 대상:", ", ".join(drop))


def harvest():
    tables = load_tables()
    have = done_ids()
    todo = [t for t in tables if t["tblId"] not in have]
    print(f"전체 {len(tables)}개 · 이미 수집 {len(have)}개 · 수집 대상 {len(todo)}개")
    with open(RAW, "a", encoding="utf-8") as out:
        for i, t in enumerate(todo, 1):
            rec = {"tblId": t["tblId"], "orgId": t["orgId"], "name": t["name"], "cat": t["cat"], "meta": {}, "status": {}}
            for mt in META_TYPES:
                st, data = get_meta(t["orgId"], t["tblId"], mt)
                rec["status"][mt] = st
                rec["meta"][mt] = data
            out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
            if i % 20 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} … 최근: {t['tblId']} {t['name'][:20]}")
    print("메타 수집 완료 →", RAW)


def summarize():
    cols = ["orgId","tblId","표명","분류","TBL","PRD","ITM","UNIT","CMMT",
            "주기","수록시작","수록끝","단위","분류축수","항목수","주석유무","종합상태","비고"]
    n = 0
    with open(RAW, encoding="utf-8") as f, open(OUT, "w", encoding="utf-8-sig", newline="") as w:
        wr = csv.writer(w); wr.writerow(cols)
        for line in f:
            r = json.loads(line); m = r["meta"]; st = r["status"]
            prd = as_list(m.get("PRD")); itm = as_list(m.get("ITM"))
            unit = as_list(m.get("UNIT")); cmmt = as_list(m.get("CMMT"))
            periods = sorted({str(p.get("PRD_SE")) for p in prd if p.get("PRD_SE")})
            month = next((p for p in prd if p.get("PRD_SE") in ("월","M")), (prd[0] if prd else {}))
            units_tbl = {str(u.get("UNIT_NM")) for u in unit if u.get("UNIT_NM")}
            units_itm = {str(i.get("UNIT_NM")) for i in itm if i.get("UNIT_NM")}
            units = sorted(units_tbl | units_itm)
            objs = {i.get("OBJ_NM") for i in itm if i.get("OBJ_ID") and i.get("OBJ_ID") != "ITEM"}
            n_itm = len([i for i in itm if i.get("OBJ_ID") != "ITEM"])
            # 종합상태
            if "error" in st.values():
                overall = "오류"
            elif st.get("ITM") == "empty" or st.get("PRD") == "empty":
                overall = "미확인(핵심메타 누락)"
            elif all(v in ("ok","empty") for v in st.values()) and st.get("ITM")=="ok" and st.get("PRD")=="ok":
                overall = "ok"
            else:
                overall = "부분"
            note = []
            if units_itm and not units_tbl: note.append("단위 항목레벨 혼재")
            if len(periods) > 1: note.append("다주기")
            wr.writerow([r["orgId"], r["tblId"], r["name"], r["cat"],
                         st.get("TBL"), st.get("PRD"), st.get("ITM"), st.get("UNIT"), st.get("CMMT"),
                         "/".join(periods), month.get("STRT_PRD_DE",""), month.get("END_PRD_DE",""),
                         ", ".join(units), len(objs), n_itm,
                         "Y" if cmmt else "N", overall, "; ".join(note)])
            n += 1
    print(f"요약표 {n}행 →", OUT)
    # 미확인/오류 점검 요약
    import collections
    c = collections.Counter()
    with open(OUT, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): c[r["종합상태"]] += 1
    print("종합상태 분포:", dict(c))


if __name__ == "__main__":
    if not API_KEY:
        print("환경변수 KOSIS_API_KEY 를 먼저 설정하세요."); sys.exit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "retry":
        retry_errors()   # 오류였던 표만 캐시에서 제거
    harvest()            # 남은/제거된 표만 (재)수집
    summarize()          # 요약표 갱신
