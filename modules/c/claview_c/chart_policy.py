"""질문의 표시 요청과 검증된 데이터 형태로 첫 보기를 고른다.

평가용 문항/정답/사례 번호는 읽지 않는다. 지원하지 못한 요청은 숨기지 않고
이유를 안내하며, 단위·기간·합산 검증을 통과시키기 위해 값을 바꾸지 않는다.
"""
import re
import unicodedata

VERSION = "chart-policy/2026-10-21-v1"
TITLES = {"line":"선그래프","bar":"묶음막대","horizontal":"한 시점 가로막대",
          "small_multiples":"항목별 나눠 보기","dual_axis":"이중 축","table":"수치표","stacked":"누적막대","percent_stacked":"100% 누적막대"}
PATTERNS = [
    ("percent_stacked",r"(?:100%|100퍼센트)(?:누적(?:막대)?(?:그래프|차트)?)?|구성비|비중누적"),
    ("dual_axis",r"이중축|양축|듀얼축|보조축|좌우축"),
    ("small_multiples",r"항목별나눠보기|각각나눠(?:보기)?|분할(?:그래프|차트)"),
    ("horizontal",r"가로막대(?:그래프|차트)?|수평막대(?:그래프|차트)?"),
    ("stacked",r"누적(?:막대)?(?:그래프|차트)?"),
    ("table",r"수치표|데이터테이블|데이터표|표(?:형식|형태|로|를)"),
    ("line",r"선형그래프|선그래프|꺾은선(?:그래프)?|라인차트|선으로"),
    ("bar",r"묶음막대(?:그래프)?|세로막대(?:그래프)?|막대(?:그래프|차트)?|바차트"),
    ("unsupported",r"원그래프|파이(?:차트|그래프)|도넛(?:차트)?|산점도|히스토그램|히트맵|상자그림|박스플롯"),
]


def requests(query):
    q=re.sub(r"\s+","",unicodedata.normalize("NFKC",query))
    found=[];occupied=[]
    for kind,pattern in PATTERNS:
        for match in re.finditer(pattern,q):
            if any(match.start()<b and match.end()>a for a,b in occupied):continue
            if re.match(r"(?:가|이|는|은)?(?:말고|대신|아니라|아닌|제외|빼고)",q[match.end():]):continue
            found.append((match.start(),kind,match.group()));occupied.append(match.span())
    found.sort()
    # '100% 누적막대'는 두 종류를 요청한 것이 아니다.
    if any(k=="percent_stacked" for _,k,_ in found):found=[x for x in found if x[1]!="stacked"]
    # '각각 나눠 선으로'의 선은 작은 그래프의 그리는 방식이고, 별도 레이아웃 요구가 아니다.
    if any(k=="small_multiples" for _,k,_ in found):found=[x for x in found if x[1] not in {"line","bar"}]
    result=[]
    for _,kind,text in found:
        if kind not in [r["kind"] for r in result]:result.append({"kind":kind,"text":text})
    return result


def choose(query, available, *, periods, series_count, composition=False, fallback=None):
    available=set(available);wanted=requests(query);notices=[]
    if not fallback:
        fallback = "line" if periods >= 2 else "bar"
        if "dual_axis" in available:
            fallback = "dual_axis"
    if fallback not in available:fallback="bar" if "bar" in available else "table"
    chosen=fallback
    for item in wanted:
        if item["kind"]=="unsupported":
            notices.append({"code":"unsupported_chart_request","message":f"‘{item['text']}’는 현재 제공하지 않는 표현입니다. 같은 자료를 지원하는 그래프·표로 보여주며, 요청을 그대로 구현한 것은 아닙니다."})
    if len(wanted)>1:
        notices.append({"code":"multiple_chart_requests","message":"여러 표현을 요청했습니다. 첫 번째로 가능한 표현을 먼저 보여주고 지원하는 다른 표현은 그래프·표 선택에서 바꿔 볼 수 있습니다. 두 그래프를 동시에 추가 배치하지는 않습니다."})
    selected_request=None
    for item in wanted:
        kind=item["kind"]
        if kind=="unsupported":
            continue
        reason=None
        if kind in {"stacked","percent_stacked"} and not composition:
            reason="항목을 합산해 전체로 볼 수 있다는 검증된 근거가 없습니다. 금리·지수 또는 확인되지 않은 구성은 쌓거나 100%로 만들지 않습니다."
        elif kind=="line" and periods<2:reason="표시 시점이 하나여서 선으로 이어 시간 변화를 표현할 수 없습니다."
        elif kind=="horizontal" and periods>1:reason="한 시점 가로막대는 선택한 시점만 보여줍니다. 전체 기간을 임의로 합산하거나 마지막 시점만 남기지 않고, 우선 전체 기간의 흐름을 표시합니다. 원하면 가로막대에서 시점을 선택하세요."
        elif kind == "dual_axis" and kind not in available:
            reason = "이중 축은 단위가 서로 다른 지표 두 종류를 같은 주기로 함께 볼 때만 제공합니다."  # noqa: E501
        elif kind not in available:reason="현재 시점 수·항목 수로는 이 표현을 제공할 수 없습니다."
        if reason:
            notices.append({"code":"chart_request_not_available","message":f"요청한 {TITLES[kind]} 대신 가능한 표현을 보여줍니다. {reason}"})
            continue
        chosen=kind;selected_request=item
        break
    if selected_request:
        reason="질문에서 요청한 "+TITLES[chosen]+"를 먼저 보여줍니다."
    elif chosen == "dual_axis":
        reason = "단위가 서로 다른 두 지표를 왼쪽·오른쪽 축에 나눠 함께 보여줍니다. 축 눈금이 달라 높이를 직접 비교할 수 없습니다."  # noqa: E501
    elif chosen=="line":reason="종류를 지정하지 않았거나 요청한 표현을 사용할 수 없어, 시간에 따른 흐름을 비교하는 선그래프를 먼저 보여줍니다."
    elif chosen=="bar":reason="표시 시점이 하나이거나 변화량의 크기를 비교하므로 막대그래프를 먼저 보여줍니다."
    else:reason="현재 입력에서 안전하게 제공할 수 있는 수치표를 먼저 보여줍니다."
    if series_count>8 and chosen!="small_multiples":
        reason+=" 같은 주기·단위의 모든 항목을 한 그래프에서 함께 비교합니다. 항목이 많아 복잡하면 ‘항목별 나눠 보기’나 ‘수치표’로 바꿔 보세요."
    if chosen=="percent_stacked":reason+=" 검증한 같은 시점의 공식 전체를 100% 분모로 사용합니다."
    elif chosen=="stacked":reason+=" 항목 합계를 공식 전체와 대조한 자료입니다."
    return {"version":VERSION,"requested":wanted,"selected":chosen,"reason":reason,"notices":notices,
            "facts":{"period_count":periods,"series_count":series_count,"composition_verified":composition}}


def apply(chart,query):
    groups=chart["views"][0]["spec"]["groups"]
    requested=chart["views"][0]["spec"].get("requested_period")
    counts=[len([p for p in g["periods"] if not requested or requested["start"]<=p<=requested["end"]]) for g in groups]
    decision=choose(query,[v["kind"] for v in chart["views"]],periods=max(counts,default=0),
                    series_count=len(chart["views"][0]["spec"]["series"]),composition=bool((chart.get("composition") or {}).get("verified")))
    chart["default_view_id"]=decision["selected"];chart["recommendation"]=decision["reason"]
    chart["notices"].extend(decision["notices"]);chart["chart_policy"]=decision
