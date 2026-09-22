# -*- coding: utf-8 -*-
"""
KOSIS 한국은행 통계표 수치(자료) 조회 검수  (1주차 · 수치 검수 단계)
------------------------------------------------------------------------
메타(ITM/PRD)를 읽어 표별 조회 파라미터를 자동 구성한 뒤 실제 DT 값을 뽑아,
"수치가 조회되는가 / 어떤 항목·기간·값인가"를 검수한다.

핵심(이전 빈 응답 원인 해결):
  · itmId   = 항목축(OBJ_ID == 'ITEM') 의 ITM_ID 값
  · objL1..N= 분류축(그 외 OBJ_ID) 의 ITM_ID 값 (OBJ_ID_SN 순서대로 objL1,objL2,…)
  · 각 축은 '리프(자식이 없는 항목)'를 골라야 데이터가 나옴(부모 노드는 err30)
  · prdSe   = 주기 코드(월M/분기Q/년Y/반기H), startPrdDe/endPrdDe = 최근 구간

출력: data_check_log.csv (표별 조회 결과·상태) + 콘솔에 요약

준비(Windows PowerShell):
  pip install requests
  $env:KOSIS_API_KEY="발급키"
  python kosis_data_check.py                 # 대표 12표 검수
  python kosis_data_check.py DT_121Y006 ...  # 특정 표만
  python kosis_data_check.py --from-csv      # boK_meta_status.csv 전체 표
"""
import os, sys, csv, json, time, re
import requests

BASE = "https://kosis.kr/openapi"
URL_META = f"{BASE}/statisticsData.do"                       # getMeta
URL_DATA = f"{BASE}/Param/statisticsParameterData.do"        # getList (자료)
API_KEY = os.environ.get("KOSIS_API_KEY", "").strip()
SLEEP = 0.2
N_PERIODS = 3          # 최근 몇 구간 조회할지
SESSION = requests.Session()

_BAD_ESC = re.compile(r'\\(?![\"\\/bfnrtu])')
def _loads_lenient(text):
    try: return json.loads(text)
    except ValueError: return json.loads(_BAD_ESC.sub(r'\\\\', text))

PRD_CODE = {"월":"M","분기":"Q","년":"Y","연":"Y","반기":"H","일":"D",
            "M":"M","Q":"Q","Y":"Y","H":"H","D":"D"}
PRD_PREF = ["M","Q","Y","H","D"]     # 조회 선호 주기(작은 단위 우선)


def api(url, params):
    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=40); r.raise_for_status()
            time.sleep(SLEEP)
            data = _loads_lenient(r.text)
            if isinstance(data, dict) and ("err" in data or "errMsg" in data):
                return {"_err": str(data.get("err")), "_msg": data.get("errMsg","")}
            return data if isinstance(data, list) else [data]
        except Exception as e:
            if attempt < 2: time.sleep(1.5*(attempt+1)); continue
            return {"_err": "EXC", "_msg": str(e)}


def leaves(group):
    """group(같은 OBJ_ID의 ITM 행들)에서 리프(다른 행의 UP_ITM_ID가 아닌 항목)만."""
    parents = {g.get("UP_ITM_ID") for g in group if g.get("UP_ITM_ID")}
    lv = [g for g in group if g.get("ITM_ID") not in parents]
    return lv or group


def build_query(org, tbl, itm_rows, prd_rows):
    """메타 → getList 파라미터(dict) 구성. 반환: (params, 설명dict) 또는 (None, 사유)."""
    # --- 축 분리 ---
    groups = {}
    for row in itm_rows:
        groups.setdefault(row.get("OBJ_ID"), []).append(row)
    item_axis = groups.pop("ITEM", None)
    # 분류축: OBJ_ID_SN 순
    clsf = sorted(groups.values(), key=lambda g: int(g[0].get("OBJ_ID_SN") or 99))
    params = {"method":"getList","apiKey":API_KEY,"orgId":org,"tblId":tbl,
              "format":"json","jsonVD":"Y"}
    desc = {}
    # itmId (항목축)
    if item_axis:
        pick = leaves(item_axis)[0]
        params["itmId"] = pick["ITM_ID"]; desc["항목(itmId)"] = pick.get("ITM_NM")
    else:
        # 항목축이 없으면 첫 분류축을 itmId 로 대체(드묾)
        if not clsf: return None, "축 정보 없음"
        pick = leaves(clsf.pop(0))[0]
        params["itmId"] = pick["ITM_ID"]; desc["항목(itmId·대체)"] = pick.get("ITM_NM")
    # objL1..N (분류축)
    for i, g in enumerate(clsf[:8], 1):
        pick = leaves(g)[0]
        params[f"objL{i}"] = pick["ITM_ID"]
        desc[f"{g[0].get('OBJ_NM','분류')}(objL{i})"] = pick.get("ITM_NM")
    # --- 주기·기간 ---
    prd_by = {}
    for p in prd_rows:
        code = PRD_CODE.get(str(p.get("PRD_SE")), str(p.get("PRD_SE")))
        prd_by[code] = p
    code = next((c for c in PRD_PREF if c in prd_by), None)
    if not code: return None, "주기 정보 없음"
    p = prd_by[code]
    params["prdSe"] = code
    params["endPrdDe"] = str(p.get("END_PRD_DE",""))
    params["startPrdDe"] = _back(code, str(p.get("END_PRD_DE","")), N_PERIODS-1) or str(p.get("STRT_PRD_DE",""))
    desc["주기"] = f"{code}"; desc["기간"] = f'{params["startPrdDe"]}~{params["endPrdDe"]}'
    return params, desc


def _back(code, end, n):
    """endPrdDe 에서 n구간 이전 시점 문자열 계산(간단 추정). 실패 시 None."""
    d = re.sub(r"\D","", end)
    try:
        if code == "M" and len(d) >= 6:
            y, m = int(d[:4]), int(d[4:6]); m -= n
            while m <= 0: m += 12; y -= 1
            return f"{y}{m:02d}"
        if code == "Y" and len(d) >= 4:
            return str(int(d[:4]) - n)
        if code == "Q" and len(d) >= 5:
            y, q = int(d[:4]), int(d[4]); q -= n
            while q <= 0: q += 4; y -= 1
            return f"{y}{q}"
        if code == "H" and len(d) >= 5:
            y, h = int(d[:4]), int(d[4]); h -= n
            while h <= 0: h += 2; y -= 1
            return f"{y}{h}"
    except Exception:
        return None
    return None


def sanitized(params):
    return "&".join(f"{k}={v}" for k, v in params.items() if k != "apiKey")


def check_table(org, tbl):
    itm = api(URL_META, {"method":"getMeta","apiKey":API_KEY,"orgId":org,"tblId":tbl,"type":"ITM","format":"json","jsonVD":"Y"})
    prd = api(URL_META, {"method":"getMeta","apiKey":API_KEY,"orgId":org,"tblId":tbl,"type":"PRD","format":"json","jsonVD":"Y"})
    if isinstance(itm, dict): return {"tblId":tbl,"상태":"메타오류","비고":f"ITM {itm.get('_err')}"}
    if isinstance(prd, dict): return {"tblId":tbl,"상태":"메타오류","비고":f"PRD {prd.get('_err')}"}
    params, desc = build_query(org, tbl, itm, prd)
    if params is None:
        return {"tblId":tbl,"상태":"구성실패","비고":desc}
    data = api(URL_DATA, params)
    if isinstance(data, dict):
        st = "데이터없음(err30)" if data.get("_err")=="30" else f"조회오류({data.get('_err')})"
        return {"tblId":tbl,"상태":st,"쿼리":sanitized(params),"선택":json.dumps(desc,ensure_ascii=False),"비고":data.get("_msg","")}
    # 성공: DT 값 요약
    sample = data[0] if data else {}
    vals = "; ".join(f'{d.get("PRD_DE")}={d.get("DT")}{d.get("UNIT_NM","")}' for d in data[:N_PERIODS])
    return {"tblId":tbl,"상태":"조회성공","건수":len(data),
            "항목명":sample.get("ITM_NM",""),"단위":sample.get("UNIT_NM",""),
            "값(최근)":vals,"쿼리":sanitized(params),"선택":json.dumps(desc,ensure_ascii=False),"비고":""}


DEFAULT = ["DT_121Y006","DT_121Y010","DT_101Y004","DT_404Y014","DT_403Y001","DT_105Y001",
           "DT_301Y013","DT_200Y101","DT_511Y002","DT_512Y013","DT_501Y086","DT_404Y016"]

def main():
    if not API_KEY:
        print("환경변수 KOSIS_API_KEY 를 먼저 설정하세요."); sys.exit(1)
    args = sys.argv[1:]
    if args and args[0] == "--from-csv":
        tbls = [(r.get("orgId","301"), r["tblId"]) for r in csv.DictReader(open("boK_meta_status.csv",encoding="utf-8-sig"))]
    elif args:
        tbls = [("301", a) for a in args]
    else:
        tbls = [("301", t) for t in DEFAULT]
    print(f"검수 대상 {len(tbls)}개")
    results = []
    for i, (org, tbl) in enumerate(tbls, 1):
        res = check_table(org, tbl); results.append(res)
        print(f"[{i}/{len(tbls)}] {tbl:12} {res['상태']:14} "
              f"{res.get('값(최근)', res.get('비고',''))[:60]}")
    cols = ["tblId","상태","건수","항목명","단위","값(최근)","선택","쿼리","비고"]
    with open("data_check_log.csv","w",encoding="utf-8-sig",newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in results: w.writerow({c: r.get(c,"") for c in cols})
    ok = sum(1 for r in results if r["상태"]=="조회성공")
    print(f"\n조회성공 {ok}/{len(results)} → data_check_log.csv")
    print("실패 표는 log의 '선택'에서 축 값을 바꿔(다른 리프) 재시도하거나, 데이터없음이면 부모노드/기간 밖 여부 확인.")

if __name__ == "__main__":
    main()
