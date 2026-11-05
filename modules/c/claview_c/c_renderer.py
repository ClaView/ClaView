"""B 조회 결과를 차트 도구와 무관한 C 결과로 정리한다. API를 호출하지 않는다.

읽는 순서: renderer → prepare → 기간/값 검사 → views·table·source → 결과 봉투.
실제 파이프라인에는 renderer(query, data_result)를 주입하면 된다.
서비스 흐름(pipeline/claview_pipeline/staged.py)이 조회 ok·partial 결과를 정렬한 뒤 claview_c_api.service.present로 이 함수에 보낸다.
"""
from __future__ import annotations

import copy
import math
import re
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from claview_common import periods

WIDTH = periods.WIDTH


# Period arithmetic and labels live in claview_common.periods (shared with B); C keeps its names.
period_index = periods.period_index
periods_between = periods.periods_between
period_label = periods.period_label


def prepare(query: str, data_result: dict) -> dict:
    """1. 입력 검증. 2. 빠진 시점 채우기(null). 3. 공통 차트/표 모델 만들기."""
    if not isinstance(query, str) or not query.strip() or not isinstance(data_result, dict):
        raise ValueError("질문 문자열과 조회 결과 객체가 필요합니다.")
    raw_series = data_result.get("series")
    if not isinstance(raw_series, list) or len(raw_series) > 30:
        raise ValueError("계열은 배열이며 최대 30개까지 표시합니다.")
    warnings, sources, datasets, rows, groups = [], [], [], [], {}
    identifiers = set()
    for item in raw_series:
        selection = item.get("selection", {})
        sid = item.get("series_id")
        if not sid or sid in identifiers or sid != selection.get("series_id"):
            raise ValueError("계열 ID가 없거나 중복되거나 selection과 다릅니다.")
        identifiers.add(sid)
        # 정렬(claview_b.align)로 주기·기간이 바뀐 계열은 aligned_period가 기준이다.
        frequency = item.get("frequency")
        period = item.get("aligned_period") or selection.get("period", {})
        if frequency != period.get("frequency"):
            raise ValueError("조회 결과와 선택 조건의 주기가 다릅니다.")
        # 단위가 없으면 % 등을 만들어 붙이지 않는다. 응답 단위와 메타 단위 충돌은 중단한다.
        unit = item.get("unit")
        converted = bool(item.get("source_unit"))  # 같은 통화의 배율 환산(원값은 source_unit과 함께 보존)
        if unit is not None and selection.get("unit") is not None and unit != selection["unit"] and not converted:
            raise ValueError("조회 결과와 선택 조건의 단위가 다릅니다.")
        if unit is None:
            unit = selection.get("unit")
        label = selection.get("label")
        if not label or not all(selection.get(k) for k in ("org_id", "table_id", "item_id")):
            raise ValueError("계열 이름·기관·통계표·항목 코드가 필요합니다.")
        expected = periods_between(period.get("start"), period.get("end"), frequency)
        by_period = {}
        for point in item.get("points", []):
            when = point.get("period")
            if when in by_period or when not in expected:
                raise ValueError("조회 기간 밖의 시점 또는 중복 시점이 있습니다.")
            status, value = point.get("value_status"), point.get("value")
            if status not in {"observed", "missing", "parse_error"}:
                raise ValueError("value_status 형식을 확인해 주세요.")
            # null과 숫자 0은 다르다. observed의 0은 선/막대에 그대로 포함한다.
            if status == "observed":
                try:
                    if value is None or isinstance(value, bool):
                        raise InvalidOperation
                    number = Decimal(str(value))
                    if not number.is_finite() or not math.isfinite(float(number)):
                        raise InvalidOperation
                except (InvalidOperation, ValueError):
                    raise ValueError("정상값(observed)에 유효하지 않은 숫자가 있습니다.") from None
                value = str(value)  # 원 정밀도 보존. 화면에서만 자릿수를 줄인다.
            else:
                if value is not None:
                    raise ValueError("결측·파싱 오류의 value는 null이어야 합니다.")
                value = None
            by_period[when] = {"period": when, "value": value,
                               "raw_value": point.get("raw_value"), "value_status": status}
        absent = [p for p in expected if p not in by_period]
        coverage = item.get("coverage", {})
        if coverage.get("expected") != len(expected) or coverage.get("returned") != len(by_period):
            raise ValueError("coverage의 기대/반환 개수가 실제 데이터와 다릅니다.")
        if sorted(coverage.get("missing_periods", [])) != absent:
            raise ValueError("누락 시점 목록과 실제 points가 일치하지 않습니다.")
        missing, parse_errors = 0, 0
        points = []
        for when in expected:
            point = by_period.get(when, {"period": when, "value": None, "raw_value": None,
                                          "value_status": "missing_period"})
            missing += point["value_status"] in {"missing", "missing_period"}
            parse_errors += point["value_status"] == "parse_error"
            points.append(point)
            rows.append({"series_id": sid, "label": label, "frequency": frequency, "unit": unit,
                         "period_label": period_label(when, frequency), **point})
        if missing:
            warnings.append({"code": "missing_values", "series_id": sid, "count": missing,
                             "periods": [p["period"] for p in points if p["value_status"] in {"missing", "missing_period"}],
                             "message": f"{label}: 자료 없는 시점 {missing}개. 0으로 채우거나 선으로 연결하지 않습니다."})
        if parse_errors:
            warnings.append({"code": "parse_errors", "series_id": sid, "count": parse_errors,
                             "periods": [p["period"] for p in points if p["value_status"] == "parse_error"],
                             "message": f"{label}: 숫자로 읽지 못한 {parse_errors}개 값은 표시에서 제외했습니다."})
        if unit is None:
            warnings.append({"code": "unit_not_provided", "series_id": sid,
                             "message": f"{label}: 단위 미제공."})
        # 서로 다른 주기·단위는 같은 축에 섞지 않고 별도 그룹으로 그린다.
        key = (frequency, unit)
        if key not in groups:
            groups[key] = {"group_id": f"G{len(groups) + 1}", "frequency": frequency,
                           "unit": unit, "series_ids": [], "periods": set()}
        group = groups[key]
        group["series_ids"].append(sid)
        group["periods"].update(expected)
        short = label.split(" / ", 1)[-1]
        datasets.append({"series_id": sid, "label": label, "short_label": short,
                         "frequency": frequency, "unit": unit, "group_id": group["group_id"],
                         "points": points, "selection": copy.deepcopy(selection)})
        sources.append({"series_id": sid, "org_id": selection["org_id"],
                        "table_id": selection["table_id"], "item_id": selection["item_id"],
                        "dimensions": copy.deepcopy(selection.get("dimensions", {})),
                        "period": copy.deepcopy(period), "unit": unit,
                        "provenance": copy.deepcopy(item.get("provenance", {})),
                        **({"source_unit": item["source_unit"]} if converted else {}),
                        **({"source_frequency": item["source_frequency"]} if item.get("source_frequency") else {})})
    relations = copy.deepcopy(data_result.get("relations", []))
    for relation in relations:
        if (relation.get("ancestor_series_id") not in identifiers or
                relation.get("descendant_series_id") not in identifiers):
            raise ValueError("상하위 관계가 존재하지 않는 계열을 가리킵니다.")
    # 합산 근거는 관계 정보와 다르다. 상하위 관계가 없다는 이유만으로 더하지 않는다.
    blocked_reason = ("전체 값과 그 안에 포함된 세부 항목이 함께 있습니다. 누적하면 같은 내용을 두 번 더하게 됩니다."
                      if relations else
                      "이 항목들을 더해 하나의 전체로 볼 수 있는지 확인되지 않았습니다. 합산 근거를 확인한 뒤 사용할 수 있습니다.")
    requested = copy.deepcopy(data_result.get("requested_period"))
    if requested is not None:
        if not isinstance(requested, dict):
            raise ValueError("requested_period는 frequency/start/end 객체여야 합니다.")
        requested_values = periods_between(requested.get("start"), requested.get("end"), requested.get("frequency"))
        if any(g["frequency"] != requested["frequency"] for g in groups.values()):
            raise ValueError("혼합 주기에는 단일 requested_period를 적용할 수 없습니다.")
        if any(not set(requested_values) <= g["periods"] for g in groups.values()):
            raise ValueError("요청 표시 기간이 조회된 기간 밖에 있습니다.")
    # 이중 축: 정렬 결과(alignment.axes)가 단위 2종·단일 주기를 확인한 경우에만 만든다.
    alignment = data_result.get("alignment") or {}
    if alignment.get("status") == "needs_choice":
        raise ValueError("주기를 어떻게 맞출지 선택이 필요한 자료입니다.")
    axes = copy.deepcopy(alignment.get("axes"))
    if axes is not None:
        axis_series = [i for axis in axes for i in axis.get("series_ids", [])]
        if sorted(axis_series) != sorted(identifiers):
            raise ValueError("축 정보가 조회 결과의 계열과 일치하지 않습니다.")
        if any(axis.get("side") not in {"left", "right"} for axis in axes):
            raise ValueError("축 위치는 left 또는 right여야 합니다.")
    dual = bool(axes) and len(axes) == 2 and len({g["frequency"] for g in groups.values()}) == 1
    if dual:
        if re.search(r"꺾은선|선그래프|선\s*그래프|라인", query) and "막대" not in query:
            for axis in axes:
                axis["draw"] = "line"
        elif "막대" in query:
            axes[0]["draw"], axes[1]["draw"] = "column", "line"
    if data_result.get("_synthetic"):
        warnings.append({"code": "synthetic_fixture", "message": "결측 처리용 합성 시험 자료입니다. 실제 경제 상황을 해석하지 마세요."})
    group_list = []
    for group in groups.values():
        group["periods"] = sorted(group["periods"])
        group_list.append(group)
    spec = {"groups": group_list, "series": datasets, "requested_period": requested,
            "relations": relations, "connect_nulls": False, "display_max_decimals": 1,
            "axes": axes if dual else None}
    # 템플릿은 질문만 보지 않고 실제 시점 수·항목 수를 함께 확인한다.
    # 표시 기간이 따로 있으면 기준 계산용으로 조회한 시점은 선택 판단에서 제외한다.
    displayed = lambda g: [p for p in g["periods"] if not requested or requested["start"] <= p <= requested["end"]]
    has_trend = any(len(displayed(g)) >= 2 for g in group_list)
    has_comparison = any(len(g["series_ids"]) >= 2 for g in group_list)
    base = "table" if re.search(r"수치표|표로|표를|표 형태", query) else "bar" if "막대" in query else "line"
    if not datasets:
        base = "table"
    if base == "line" and not has_trend:
        base = "bar"
    default = "small_multiples" if base != "table" and len(datasets) > 8 and has_trend else base
    reason = ("질문에서 요청한 수치표를 먼저 보여줍니다." if base == "table" else
              "질문에서 요청한 막대로 기간별 값을 비교합니다." if "막대" in query else
              "시간에 따른 흐름을 비교하기 위해 선그래프를 추천합니다." if base == "line" else
              "표시 시점이 하나이므로 막대로 항목별 값을 비교합니다.")
    if default == "small_multiples":
        reason += f" 항목이 {len(datasets)}개라 겹치지 않도록 항목별로 나눠 보여줍니다."
    choices = [("dual_axis", "이중 축", "dual", dual and has_trend),
               ("line", "선그래프", "line", has_trend and not dual),
               ("bar", "묶음막대", "bar", bool(datasets) and not dual),
               ("small_multiples", "항목별 나눠 보기", "bar" if base == "bar" else "line", len(datasets) >= 2 and has_trend),
               ("horizontal", "한 시점 가로막대", "horizontal", has_comparison and not dual),
               ("table", "수치표", "table", True)]
    views = [{"view_id": kind, "kind": kind, "title": title, "chart_kind": chart_kind, "spec": spec}
             for kind, title, chart_kind, allowed in choices if allowed]
    blocked = [{"kind": "stacked", "title": "누적막대", "reason": blocked_reason},
               {"kind": "percent_stacked", "title": "100% 누적막대", "reason":
                "비중을 구하려면 서로 겹치지 않는 구성 항목과 전체의 범위가 확인되어야 합니다. 현재 입력에는 그 근거가 없습니다."}]
    if dual:
        unit_text = " · ".join(str(a["unit"] or "단위 미제공") for a in axes)
        why = f"단위가 서로 달라({unit_text}) 한 축에 그릴 수 없습니다. 이중 축 보기를 사용하세요."
        blocked += [{"kind": "line", "title": "선그래프", "reason": why},
                    {"kind": "bar", "title": "묶음막대", "reason": why}]
        default = "table" if base == "table" or not has_trend else "dual_axis"
        reason = (
            f"단위가 서로 다른 지표({unit_text})를 왼쪽·오른쪽 축에 나눠 함께 보여줍니다. 축 눈금이 달라 높이를 직접 비교할 수 없습니다."
            if has_trend else
            "표시 시점이 하나여서 이중 축 그래프 대신 수치표를 먼저 보여줍니다.")
    elif not has_trend:
        blocked.append({"kind": "line", "title": "선그래프", "reason": "시간 변화를 그리려면 표시 시점이 두 개 이상 필요합니다."})
    if not has_comparison:
        blocked.append({"kind": "horizontal", "title": "한 시점 가로막대", "reason": "같은 주기·단위의 비교 항목이 두 개 이상 필요합니다."})
    missing_rows = [r for r in rows if r["value_status"] in {"missing", "missing_period"}]
    error_rows = [r for r in rows if r["value_status"] == "parse_error"]
    quality = {"total_cells": len(rows), "observed_values": len(rows) - len(missing_rows) - len(error_rows),
               "missing_values": len(missing_rows), "parse_error_values": len(error_rows),
               "affected_series": len({r["series_id"] for r in missing_rows + error_rows})}
    return {"schema_version": "claview-c/1.1-demo", "query": query,
            "default_view_id": default, "views": views, "table": rows,
            "warnings": warnings, "notices": copy.deepcopy(data_result.get("period_notices", [])),
            "source": sources, "recommendation": reason,
            "blocked_views": blocked, "quality_summary": quality,
            "synthetic": bool(data_result.get("_synthetic"))}


def renderer(query: str, data_result: dict) -> dict:
    """파이프라인에서 부를 C 진입점. 오류도 stage/status/result 봉투로 반환한다."""
    trace_ref = uuid4().hex
    try:
        result = prepare(query, data_result)
        observed = sum(row["value_status"] == "observed" for row in result["table"])
        missing = any(row["value_status"] != "observed" for row in result["table"])
        status = "no_data" if observed == 0 else "partial" if missing else "ok"
        return {"stage": "C", "status": status, "result": result, "issues": [], "trace_ref": trace_ref}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"stage": "C", "status": "error", "result": None,
                "issues": [{"code": "invalid_c_input", "message": str(exc), "field": None,
                            "retryable": False}], "trace_ref": trace_ref}
