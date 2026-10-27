"""기존 C의 검증/원값은 보존하고 합산 검증 및 명시적 지수 변화 계산을 추가한다.

LLM이 수치를 만들지 않는다. Decimal로 계산하며 결과와 원지수는 별도 카드로 구분한다.
"""
import copy
import re
import unicodedata
from decimal import Decimal, localcontext

from .c_renderer import renderer as base_renderer, period_label, period_index, periods_between
from .composition import verify_composition, number
from .chart_policy import apply as apply_chart_policy, choose as choose_chart_view

VERSION = "c-analysis/2026-10-21-v3-policy"


def display_units(data_result):
    """Recover explicitly written units only when API/metadata UNIT fields are absent.

    The original data_result is preserved. The canonical dimension name is checked
    by fetch against metadata; a name such as 금액(십억원) is explicit evidence.
    """
    data=copy.deepcopy(data_result);notices=[]
    for series in data.get('series',[]):
        selection=series['selection']
        if series.get('unit') is not None or selection.get('unit') is not None:continue
        hints=[]
        for dim in selection['dimensions'].values():
            name=unicodedata.normalize('NFKC',dim['name'])
            match=re.fullmatch(r'(?:금액|계좌수|건수|잔액)\((십억원|억원|백만원|천원|원|천좌|좌|천건|건|백만달러|천달러|달러)\)',name)
            if match:hints.append((name,match[1]))
        if len({u for _,u in hints})==1:
            unit=hints[0][1];series['unit']=unit;selection['unit']=unit
            notices.append({'code':'unit_from_dimension_label','series_id':series['series_id'],
                            'message':f"API 단위 칸이 비어 있어 공식 분류명 ‘{hints[0][0]}’에 적힌 {unit}을 표시 단위로 사용합니다. 원응답은 보존합니다."})
    return data,notices


def calculate_rates(query, data):
    series = data["series"]
    tables = {s["selection"]["table_id"] for s in series}
    freq = {s["frequency"] for s in series}
    units = {unicodedata.normalize("NFKC", s.get("unit") or "") for s in series}
    # 기존에 %로 제공된 통계에는 증감률을 다시 계산하지 않는다.
    if len(units) != 1 or not re.fullmatch(r"\d{4}=100", next(iter(units), "")):
        return None
    years = set(re.findall(r"(?<!\d)(20\d{2})년", query))
    manual_periods = [r["requested"] for r in data.get("user_periods", [])]
    manual = manual_periods[0] if len(manual_periods) == 1 else None
    if len(years) != 1 and not manual:
        return None
    year = next(iter(years), None)
    if tables == {"DT_404Y016"} and freq == {"M"} and len(series) == 1 and "산업용전력" in series[0]["selection"]["label"].replace(" ", "") and re.search(r"가장.*변", query):
        kind, periods = "mom_peak", periods_between(manual["start"], manual["end"], "M") if manual else periods_between(year + "01", year + "12", "M")
        policy = "전월 대비 변화율의 절댓값이 가장 큰 달을 찾습니다. 하락도 포함하며 동률은 모두 표시합니다. 전기요금이 아닌 산업용 전력 물가지수 기준입니다."
        formula = "(이번 달 지수 ÷ 전달 지수 − 1) × 100"
    elif tables == {"DT_402Y014"} and freq == {"Y"} and re.search(r"전년|작년", query):
        kind, periods = "yoy", periods_between(manual["start"], manual["end"], "Y") if manual else [year]
        policy = "월별이라는 요청이 없어 현재 B의 연간 주기를 유지합니다. KOSIS 연간 원화기준 지수를 전년도 연간 지수와 비교합니다. 전년동월 대비가 아닙니다."
        formula = "(해당 연도 지수 ÷ 전년도 지수 − 1) × 100"
        if manual:
            policy = "사용자가 확인한 연간 표시 기간을 적용합니다. 각 연간 원화기준 지수를 전년도 연간 지수와 비교합니다. 전년동월 대비가 아닙니다."
    else:
        return None
    frequency = next(iter(freq))
    rows, results = [], []
    for s in series:
        lookup = {p["period"]: p for p in s["points"]}
        values = []
        for p in periods:
            if kind == "mom_peak":
                n = period_index(p, "M") - 1
                base = f"{n // 12:04d}{n % 12 + 1:02d}"
            else:
                base = str(int(p) - 1)
            now, before = number(lookup.get(p)), number(lookup.get(base))
            issue = "현재값 또는 기준값 없음" if now is None or before is None else "기준값이 0 이하" if before <= 0 else None
            with localcontext() as ctx:
                ctx.prec = 28
                rate = (now / before - 1) * 100 if issue is None else None
            values.append(None if rate is None else str(rate))
            rows.append({"series_id": s["series_id"], "name": s["selection"]["label"].split(" / ", 1)[-1],
                         "period": p, "baseline_period": base, "current_value": None if now is None else str(now),
                         "baseline_value": None if before is None else str(before), "value": values[-1], "issue": issue})
        results.append({"id": s["series_id"], "name": s["selection"]["label"].split(" / ", 1)[-1],
                        "full": s["selection"]["label"], "unit": "%", "values": values})
    valid = [r for r in rows if r["value"] is not None]
    complete = len(valid) == len(rows)
    highlights, summary = [], ""
    if kind == "mom_peak" and valid:
        maximum = max(abs(Decimal(r["value"])) for r in valid)
        if maximum == 0:
            summary = "계산 가능한 모든 월의 전월 대비 변화율이 0%입니다."
        else:
            winners = [r for r in valid if abs(Decimal(r["value"])) == maximum]
            highlights = [r["period"] for r in winners]
            summary = ("가장 크게 움직인 달: " if complete else "계산 가능한 달 중 최대 변화 (일부 월 미확인): ") + ", ".join(
                period_label(r["period"], "M") + f" {Decimal(r['value']):+.2f}%" for r in winners)
    elif kind == "yoy":
        summary = " · ".join(r["name"] + (f" {Decimal(r['value']):+.2f}%" if r["value"] is not None else " 계산 불가") for r in rows)
    if not valid:
        summary = "유효한 비교값이 없어 변화율을 계산하지 않았습니다. 원자료를 확인하세요."
    return {"kind": kind, "periods": periods, "frequency": frequency, "unit": "%", "source_unit": next(iter(units)),
            "policy": policy, "formula": formula, "series": results, "rows": rows,
            "complete": complete, "summary": summary, "highlights": highlights, "version": VERSION}


def renderer(query, data_result):
    prepared,unit_notices=display_units(data_result)
    out = base_renderer(query, prepared)
    if out["status"] not in {"ok", "partial"}:
        return out
    chart = out["result"]
    # 항목 수가 많다는 이유만으로 첫 화면을 개별 그래프로 나누지 않는다.
    # 기존 주기·단위 그룹과 나눠 보기 선택지는 그대로 보존한다.
    if chart["default_view_id"] == "small_multiples":
        allowed = {view["kind"] for view in chart["views"]}
        preferred = "bar" if "막대" in query else "line"
        chart["default_view_id"] = preferred if preferred in allowed else "table"
        chart["recommendation"] = "같은 주기·단위의 항목을 한 그래프에서 함께 비교합니다. 항목이 많아 복잡하면 ‘항목별 나눠 보기’를 선택할 수 있습니다."
    chart['notices'].extend(unit_notices)
    if unit_notices:
        resolved={n['series_id'] for n in unit_notices}
        chart['warnings']=[w for w in chart['warnings'] if not (isinstance(w,dict) and w.get('code')=='unit_not_provided' and w.get('series_id') in resolved)]
    chart["analysis_version"] = VERSION
    composition = verify_composition(data_result)
    chart["composition"] = composition
    if composition and composition["verified"]:
        spec = chart["views"][0]["spec"]
        for kind, title in (("stacked", "누적막대"), ("percent_stacked", "100% 누적막대")):
            chart["views"].append({"view_id": kind, "kind": kind, "title": title, "chart_kind": kind, "spec": spec})
        chart["blocked_views"] = [b for b in chart["blocked_views"] if b["kind"] not in {"stacked", "percent_stacked"}]
        if "누적" in query:
            chart["default_view_id"] = "percent_stacked" if "100%" in query or "구성비" in query else "stacked"
        chart["recommendation"] = "14개 구성항목의 합계를 공식 M2 전체와 대조했습니다. 요청한 누적막대로 상품 구성과 전체 규모를 함께 봅니다."
    elif composition:
        for blocked in chart["blocked_views"]:
            if blocked["kind"] in {"stacked", "percent_stacked"}:
                blocked["reason"] = composition["reason"]
        chart["warnings"].append({"code": "composition_not_verified", "message": composition["reason"]})
    chart["calculation"] = calculate_rates(query, data_result)
    apply_chart_policy(chart,query)
    return out


def decorate_cards(chart, raw_cards):
    """원본 카드 숫자는 바꾸지 않는다. 계산 결과 카드를 별도로 앞에 추가한다."""
    for card in raw_cards:
        card["analysisVersion"] = VERSION
        card["chartPolicyVersion"] = chart["chart_policy"]["version"]
        card["chartPolicy"] = copy.deepcopy(chart["chart_policy"])
        if (chart.get("composition") or {}).get("verified"):
            evidence = chart["composition"]
            total = {r["period"]: Decimal(r["official_total"]) for r in evidence["checks"]}
            card["composition"] = evidence
            card["percentSeries"] = [{**s, "unit": "%", "values": [str(Decimal(v) / total[p] * 100) for p, v in zip(card["periods"], s["values"])]} for s in card["series"]]
            card["notices"].append("합산 근거 확인: 14개 상품만 합산합니다. 전체·참고용 구 M2는 제외하며, 비중 분모는 항목을 숨겨도 공식 M2 전체로 고정됩니다.")
    calculation = chart.get("calculation")
    if not calculation or len(raw_cards) != 1:
        return raw_cards
    raw = raw_cards[0]
    derived = copy.deepcopy(raw)
    derived.update(id=raw["id"] + "-" + calculation["kind"], derived=True, calculation=calculation,
                   tableName=("전월 대비 변화율 · 최대 변화 월" if calculation["kind"] == "mom_peak" else "전년 대비 증감률") + " · " + raw["tableName"],
                   series=calculation["series"], periods=calculation["periods"], labels=[period_label(p, raw["freq"]) for p in calculation["periods"]],
                   unit="%", maxDecimals=2, defaultView="bar", kinds=["bar", "table"],
                   kindTitles={"bar": "변화율 막대", "line": "변화율 선", "horizontal": "변화율 가로막대", "table": "계산값 표"},
                   recommend=calculation["summary"], highlights=calculation["highlights"],
                   warnings=[] if calculation["complete"] else ["일부 변화율은 기준값 누락·0 이하로 계산하지 않았습니다. 빈 값을 0으로 대체하지 않습니다."],
                   notices=[calculation["policy"], "C에서 계산한 값입니다. 한국은행이 별도로 공표한 증감률이 아니며, 원지수는 아래 원자료 카드에 보존됩니다."], blocked=[])
    if len(derived["periods"]) > 1:
        derived["kinds"].insert(1, "line")
    elif len(derived["series"]) > 1:
        # 한 시점의 여러 증감률은 가로막대도 허용한다. 계산식·기본 추천은 그대로다.
        derived["kinds"].insert(1, "horizontal")
    decision=choose_chart_view(chart["query"],derived["kinds"],periods=len(derived["periods"]),series_count=len(derived["series"]),fallback="bar")
    derived["defaultView"]=decision["selected"];derived["chartPolicy"]=decision
    derived["notices"].extend(n["message"] for n in decision["notices"])
    derived["start"], derived["end"] = derived["labels"][0], derived["labels"][-1]
    size = len(calculation["rows"])
    missing = sum(r["value"] is None for r in calculation["rows"])
    derived["quality"] = {"total_cells": size, "observed_values": size - missing, "missing_values": missing}
    derived["params"] = {**raw["params"], "계산식": calculation["formula"], "계산 정책": calculation["policy"], "원지수 단위": calculation["source_unit"]}
    raw["notices"].append("원자료 카드입니다. 위 계산 결과와 구분해 보세요. 비교 계산에 필요한 이전 기준 시점도 보존합니다.")
    raw["recommend"] = "계산에 사용한 원지수입니다. 값과 기준 시점은 수정하지 않았습니다."
    return [derived, raw]
