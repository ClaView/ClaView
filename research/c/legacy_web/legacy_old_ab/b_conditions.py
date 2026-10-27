"""B에서 미확정된 조건을 사람이 수정하는 연결층. 공유 B 파일은 수정하지 않는다.

브라우저의 이름·단위·수치는 신뢰하지 않고 서버 메타와 기존 B 검증기로 재구성한다.
원질문/A/B 실패 기록은 보존하며, 사용자 선택을 모델이 정한 답으로 기록하지 않는다.
"""
import copy
import re
from datetime import datetime, timezone

from claview.contracts import Config, StageFailure
from claview.metadata import WIDTHS, period_number, periods_between
from claview.stages import (canonicalize_selections, find_relations, validate_period,
                            decide_frequency, resolve_explicit_period, resolve_relative_period,
                            RELATIVE_WORDS, detect_baseline_extension, expand_selections,
                            with_default_expand, normalize_model_periods, UNRESOLVED_CALC_WORDS)

RECOVERABLE = {
    "conditions_incomplete", "invalid_period", "metadata_refresh_required",
    "explicit_period_mismatch", "explicit_frequency_mismatch",
    "selection_count_requires_confirmation", "broad_scope_context_incomplete",
    "invalid_model_output", "schema_validation_failed", "duplicate_selection",
    "expand_target_has_no_children", "expand_multiple_axes",
    "published_rate_period_mismatch", "named_conditions_require_confirmation",
}
REASONS = {
    "conditions_incomplete": "조회할 기간이나 항목을 확정하지 못했습니다.",
    "metadata_refresh_required": "요청한 기간이 저장된 메타정보의 수록 범위를 벗어납니다.",
    "selection_count_requires_confirmation": "항목이 너무 많습니다. 필요한 항목을 30개 이내로 줄여 주세요.",
    "broad_scope_context_incomplete": "항목 범위가 넓어 자동 선택을 멈췄습니다. 조회할 항목을 직접 고르세요.",
    "named_conditions_require_confirmation": "질문에서 지정한 항목과 자동 선택이 다릅니다. 조회할 항목을 직접 확인해 주세요.",
}


def shifted(value, freq, offset):
    n = period_number(value, freq) + offset
    return str(n) if freq == "Y" else f"{n // WIDTHS[freq]:04d}{n % WIDTHS[freq] + 1:02d}"


def baseline_count(query, table, frequency, rows):
    # 이미 공표된 증감률을 다시 계산하거나 기준기간을 더 늘리지 않는다.
    if all(r["unit"] == "%" or re.search("증감률|성장률|기여도", r["label"]) for r in rows):
        return 0
    return detect_baseline_extension(query, frequency)


def make_editor(state, store):
    code = state.get("error_code")
    if code not in RECOVERABLE or not state.get("a_result") or store is None:
        return None
    b = state.get("b_state", {})
    groups = []
    for ref in state["a_result"]["tables"]:
        table = store.get(ref["org_id"], ref["table_id"])
        key = ref["org_id"] + ":" + ref["table_id"]
        draft_rows = []
        selected = b.get("selections_by_table", {}).get(key, [])
        if selected:
            draft_rows = [{"item_id": r["item_id"], "dimensions": {a:v["code"] for a,v in r["dimensions"].items()}} for r in selected]
        elif b.get("active_table") == key and b.get("model_output"):
            try:
                raw = expand_selections(with_default_expand(normalize_model_periods(b["model_output"])), table)
                draft_rows = [{"item_id": r["item_id"], "dimensions": r["dimensions"]} for r in raw["selections"]]
            except (AttributeError, KeyError, TypeError, ValueError, StageFailure):
                pass
        # 잘못된 코드/축은 기본 선택으로 옮기지 않는다. 사용자가 빈 칸부터 확인한다.
        draft_rows = [r for r in draft_rows if isinstance(r["item_id"], str) and r["item_id"] in table["items"] and
                      isinstance(r["dimensions"], dict) and
                      set(r["dimensions"]) == set(table["dimensions"]) and
                      all(isinstance(c,str) and c in table["dimensions"][a]["values"] for a,c in r["dimensions"].items())]
        draft_rows = list({str((r["item_id"], sorted(r["dimensions"].items()))): r for r in draft_rows}.values())
        draft_notice = ""
        if len(draft_rows) > 100:
            draft_notice = f"이전 자동 선택 후보가 {len(draft_rows)}개라 입력란을 비웠습니다. 필요한 항목을 직접 30개 이내로 선택해 주세요."
            draft_rows = []  # 거대 후보를 조용히 일부만 채택하지 않는다.
        draft_period = None
        try:
            from query_contract import normalize_b_query
            period_query=state.get('b_query') or normalize_b_query(state['query'])[0]
            freq, _ = decide_frequency(period_query, table)
            if re.search(RELATIVE_WORDS, period_query):
                res = resolve_relative_period(period_query, table, Config(as_of=state["as_of"]), freq)
                draft_period = res.get("period") or res.get("result", {}).get("resolved_period")
            else:
                draft_period = resolve_explicit_period(period_query, table, freq)
            if draft_period:
                draft_period = copy.deepcopy(draft_period)
                # 입력칸에는 표시하고 싶은 기간만. 계산용 기준시점은 서버에서 추가한다.
                extension = 0 if re.search(RELATIVE_WORDS, period_query) else detect_baseline_extension(period_query, draft_period["frequency"])
                draft_period["start"] = shifted(draft_period["start"], draft_period["frequency"], extension)
        except StageFailure:
            pass
        groups.append({"table_key": key, "table_name": table["table_name"],
                       "periods": table["periods"], "items": table["items"],
                       "dimensions": {axis: {"name": dim["name"], "values": [
                           {"code": c, "label": " > ".join(p["name"] for p in v["path"]), "unit": v["unit"]}
                           for c,v in dim["values"].items()]} for axis,dim in table["dimensions"].items()},
                       "draft_notice": draft_notice, "draft": {"period": draft_period, "selections": draft_rows}})
    return {"kind": "b_conditions", "reason_code": code,
            "reason": REASONS.get(code, "기간·주기 또는 항목 선택을 확인해야 합니다."),
            "max_selections": 30, "max_cells": 20000,
            "metadata_snapshot": store.snapshot, "tables": groups}


def validate_edit(state, payload, store):
    spec = state.get("condition_editor")
    if not spec or state.get("error_code") not in RECOVERABLE:
        raise ValueError("B 조건 수정을 기다리는 실행이 아닙니다.")
    if spec["metadata_snapshot"] != store.snapshot:
        raise ValueError("메타정보가 바뀌었습니다. 질문을 다시 실행해 주세요.")
    if not isinstance(payload, dict) or set(payload) != {"tables", "acknowledged"} or payload["acknowledged"] is not True:
        raise ValueError("변경한 기간·항목을 확인했다는 체크가 필요합니다.")
    edits = payload["tables"]
    offered = {t["table_key"] for t in spec["tables"]}
    if not isinstance(edits, list) or len(edits) != len(offered) or any(not isinstance(t,dict) for t in edits):
        raise ValueError("확정된 통계표마다 조회 조건을 입력해 주세요.")
    if any(not isinstance(t.get("table_key"), str) for t in edits) or {t.get("table_key") for t in edits} != offered:
        raise ValueError("A에서 확정한 통계표는 이 단계에서 바꿀 수 없습니다.")
    selections, relations, periods, notices = [], [], [], []
    config = Config(as_of=state["as_of"])
    cells = 0
    for edit in edits:
        if set(edit) != {"table_key", "period", "selections"}:
            raise ValueError("지원하지 않는 조건이 포함되어 있습니다.")
        table = store.get(*edit["table_key"].split(":", 1))
        period, rows = edit["period"], edit["selections"]
        if not isinstance(period,dict) or set(period) != {"frequency", "start", "end"} or not all(isinstance(v,str) for v in period.values()):
            raise ValueError("주기·시작·종료 기간을 올바르게 입력해 주세요.")
        if not isinstance(rows,list) or not 1 <= len(rows) <= 30 or any(not isinstance(r,dict) or set(r) != {"item_id", "dimensions"} for r in rows):
            raise ValueError("표마다 조회 항목을 1~30개 선택해 주세요.")
        try:
            validate_period(period, table)
            canonical = canonicalize_selections({"selections": [{**r,"period":period} for r in rows]}, table, config)
            if re.search(UNRESOLVED_CALC_WORDS, state["query"]) and not all(re.search("증감률|성장률|기여도", r["label"]) for r in canonical):
                raise StageFailure("calculation_definition_required", "unresolved")
            extension = baseline_count(state["query"], table, period["frequency"], canonical)
            fetched_period = {**period, "start": shifted(period["start"], period["frequency"], -extension)}
            validate_period(fetched_period, table)
        except StageFailure as exc:
            bounds = table['periods'].get(period['frequency'])
            messages = {
                'invalid_period': '시작·종료 순서와 주기별 입력 형식을 확인해 주세요. 월은 01~12, 분기는 01~04, 반기는 01~02입니다.',
                'metadata_refresh_required': ('선택한 주기의 수록 범위는 '+bounds['start']+'~'+bounds['end']+'입니다. 표시 기간과 계산용 이전 기준기간 모두 이 범위 안이어야 합니다.') if bounds else '표에서 지원하는 주기를 선택해 주세요.',
                'invalid_model_output': '이 표에 있는 항목과 모든 분류를 빠짐없이 선택해 주세요.',
                'duplicate_selection': '같은 항목·분류 조합이 중복되었습니다. 중복 행 하나를 삭제해 주세요.',
                'calculation_definition_required': '기여도·연쇄가중 계산 기준은 기간·항목 수정만으로 확정할 수 없습니다. 공식 계산 항목이나 별도 기준 확인이 필요합니다.',
            }
            raise ValueError(messages.get(exc.code, '항목·분류·주기·기간을 확인해 주세요. 선택한 조건을 공식 메타정보로 검증하지 못했습니다.')) from None
        except (KeyError, TypeError, ValueError):
            raise ValueError('항목·분류·주기·기간의 입력 형식을 확인해 주세요.') from None
        for row in canonical:
            row["period"] = copy.deepcopy(fetched_period)
            row["series_id"] = f"S{len(selections)+1:03d}"
            selections.append(row)
        cells += len(canonical)*len(periods_between(fetched_period["start"], fetched_period["end"], period["frequency"]))
        relations.extend(find_relations(canonical, table))
        periods.append({"table_key":edit["table_key"], "requested":copy.deepcopy(period), "fetch":fetched_period})
        notices.append({"message":f"사용자 확인: {table['table_name']} 표시 기간 {period['start']}~{period['end']} ({period['frequency']}). 조회 기간 {fetched_period['start']}~{fetched_period['end']}."})
    if len(selections)>30 or cells>20000:
        raise ValueError("한 번에 항목 30개·시점×항목 20,000개 이내로 조회해 주세요.")
    result = {"selections":selections, "relations":relations, "period_notices":notices,
              "user_periods":periods}
    record = {"source":"user_condition_edit", "confirmed_at":datetime.now(timezone.utc).isoformat(),
              "original_query":state["query"], "original_b_issue":state["error_code"],
              "previous_b":copy.deepcopy(state.get("stage_outcomes", {}).get("B")),
              "applied":copy.deepcopy(result), "metadata_snapshot":store.snapshot}
    return result, record
