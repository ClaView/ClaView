"""합산 계약: 모든 계층에 일반화하지 않고 검증한 M2 상품 구성만 허용한다.

전체(M2)와 참고용 구 M2는 막대에 포함하지 않는다. 공식 전체는 검증/분모 전용이다.
실행 시에는 같은 조건으로 전체를 추가 조회한다. 저장 결과 재표시는 조회하지 않는다.
"""
import copy
import json
from decimal import Decimal
from functools import lru_cache

from .paths import METADATA, sha

TABLE = "DT_161Y008"
ITEM = "13103844599999"
PREFIX = "13102844599ACC_ITEM.BBGA"
TOTAL = PREFIX + "00"
COMPONENTS = {PREFIX + f"{n:02d}" for n in [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]}
SOURCE = "https://kosis.kr/statHtml/statHtml.do?orgId=301&tblId=DT_161Y008"
TOLERANCE = Decimal("0.05")  # 십억원. 공개 값의 반올림 차이만 허용하는 명시적 정책.


@lru_cache(maxsize=1)
def metadata_contract():
    path = METADATA
    with path.open(encoding="utf-8") as stream:
        table = next(json.loads(line) for line in stream if json.loads(line).get("tblId") == TABLE)
    items = table["meta"]["ITM"]
    children = {i["ITM_ID"] for i in items if i.get("UP_ITM_ID") == TOTAL}
    # 참고용 과거 전체는 현 구성상품이 아니다. 메타 구조가 바뀌면 재검토 전까지 닫는다.
    reference = next(i for i in items if i["ITM_ID"] == PREFIX + "16")
    if children != COMPONENTS | {PREFIX + "16"} or "[참고]" not in reference["ITM_NM"]:
        raise ValueError("M2 구성 메타가 변경되어 합산 근거 재확인이 필요합니다.")
    return {"contract_id": "bok-m2-end-original/1", "source_url": SOURCE,
            "metadata_sha256": sha(path), "component_codes": sorted(COMPONENTS),
            "excluded_codes": [TOTAL, PREFIX + "16"], "tolerance": str(TOLERANCE),
            "reason": "M2 말잔 원계열의 현 상품 14종. 전체 및 참고용 구 M2는 합산에서 제외."}


def total_selection(data):
    """정확한 구성 집합·동일 기간/주기/단위가 있을 때만 전체 조회 조건을 만든다."""
    series = data.get("series", [])
    if len(series) != 14:
        return None
    selections = [s["selection"] for s in series]
    codes = [s.get("dimensions", {}).get("objL1", {}).get("code") for s in selections]
    if set(codes) != COMPONENTS or len(set(codes)) != len(codes):
        return None
    first = selections[0]
    if any(s.get("org_id") != "301" or s.get("table_id") != TABLE or s.get("item_id") != ITEM
           or set(s.get("dimensions", {})) != {"objL1"} or s.get("period") != first["period"]
           or s.get("unit") != "십억원" for s in selections):
        return None
    metadata_contract()
    total = copy.deepcopy(first)
    total.update(series_id="M2_OFFICIAL_TOTAL", label="M2 공식 전체 (말잔 원계열)")
    total["dimensions"] = {"objL1": {"code": TOTAL, "name": "M2 (말잔원계열)"}}
    return total


# attach_live_total (the extra KOSIS fetch of the official total) moved to the pipeline
# (claview_pipeline.staged): C does not fetch. C only builds the selection to fetch.
TOTAL_FETCH_ERROR = "공식 M2 전체 조회가 완전하지 않아 누적·구성비는 제한하고 원자료를 표시합니다."


def number(point):
    if not point or point.get("value_status") != "observed" or point.get("value") is None:
        return None
    value = Decimal(str(point["value"]))
    return value if value.is_finite() else None


def verify_composition(data):
    selection = total_selection(data)
    if selection is None:
        return None
    total = data.get("composition_total")
    if not total:
        return {"verified": False, "reason": data.get("composition_error", "공식 M2 전체 값이 없어 합계를 대조하지 못했습니다.")}
    actual = total.get("selection", {})
    if any(actual.get(k) != selection.get(k) for k in ("org_id", "table_id", "item_id", "period", "unit")) or actual.get("dimensions", {}).get("objL1", {}).get("code") != TOTAL or total.get("unit") != "십억원" or total.get("frequency") != selection["period"]["frequency"]:
        return {"verified": False, "reason": "공식 전체의 표·항목·기간·주기·단위가 구성항목과 다릅니다."}
    from .c_renderer import periods_between
    period = selection["period"]
    periods = periods_between(period["start"], period["end"], period["frequency"])
    all_series = data["series"] + [total]
    lookups = [{p["period"]: p for p in s["points"]} for s in all_series]
    if any(len(l) != len(s["points"]) or set(l) != set(periods) for l, s in zip(lookups, all_series)):
        return {"verified": False, "reason": "중복되거나 빠진 시점이 있어 합산을 허용하지 않습니다."}
    checks = []
    for p in periods:
        values = [number(l.get(p)) for l in lookups]
        if any(v is None for v in values) or any(v < 0 for v in values) or values[-1] <= 0:
            return {"verified": False, "reason": "결측·음수 또는 0인 전체가 있어 합산/비중을 계산하지 않습니다."}
        component_sum, official = sum(values[:-1]), values[-1]
        difference = component_sum - official
        if abs(difference) > TOLERANCE:
            return {"verified": False, "reason": f"{p}: 구성 합계와 공식 전체의 차이가 허용 오차 {TOLERANCE} 십억원을 넘습니다."}
        checks.append({"period": p, "component_sum": str(component_sum), "official_total": str(official),
                       "difference": str(difference), "tolerance": str(TOLERANCE)})
    return {**metadata_contract(), "verified": True, "checks": checks,
            "total_provenance": total.get("provenance", {}), "total_selection": actual,
            "denominator": "해당 시점의 공식 M2 전체. 항목을 숨겨도 분모는 고정합니다."}
