"""A가 제시한 후보 묶음 → 사용자 선택 → 기존 B의 tables 계약.

후보와 이름은 서버의 A 결과·공식 카탈로그에서만 읽는다.
브라우저는 요구별 option_id만 보내며 표 코드나 질문을 바꿀 수 없다.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from claview_v3.select.tables import selection_catalog
from catalog_recovery import recovery_candidates


def make_confirmation(analysis, selected, layer):
    interp = analysis.get("interpretation") or {}
    requirements = interp.get("requirements") or []
    if not requirements or interp.get("status") not in {"ok", "ask", "partial"}:
        return None
    if any(r.get("status") not in {"ok", "ask", "unresolved"} for r in requirements):
        return None  # 범위 밖 요구를 후보 선택만으로 지원되는 요구로 바꾸지 않는다.
    views = selection_catalog(layer).views if layer is not None else {}
    by_req = {s["requirement_id"]: s for s in selected.get("selections", [])}
    groups = []
    for req in requirements:
        selection = by_req.get(req["id"], {})
        bundles = [[t["table_id"] for t in selection.get("tables", [])]]
        bundles.extend(selection.get("alternatives") or [])
        query = (analysis.get("text") or {}).get("raw", "")
        recovered = recovery_candidates(query if len(requirements) == 1 else req.get("quote", ""), layer)
        # 미해석 요구라도 실제 메타에 근거한 후보가 있으면 '사람의 표 확인'으로 이어갈 수 있다.
        # A의 status는 그대로 남고, B에는 원질문 전체를 전달하므로 요구를 버리지 않는다.
        bundles.extend([[r["table_id"]] for r in recovered])
        recovery_notes = {r["table_id"]: r["note"] for r in recovered}
        options, seen = [], set()
        for bundle in bundles:
            if not isinstance(bundle, list) or not bundle or any(not isinstance(t, str) for t in bundle):
                continue
            key = tuple(sorted(set(bundle)))
            if key in seen or any(t not in views or not views[t].has_meta for t in key):
                continue
            seen.add(key)
            tables = []
            for tid in dict.fromkeys(bundle):
                view = views[tid]
                if not view.org_id:
                    break
                difference = next((d for d in selection.get("differences", [])
                                   if tid in d.get("table_ids", [])), {})
                tables.append({"org_id": view.org_id, "table_id": tid, "table_name": view.title,
                               "path": view.path, "frequencies": view.frequencies(),
                               "coverage": [{"frequency": f, "start": start, "end": end}
                                            for f, start, end in view.periods],
                               "units": sorted(view.units()),
                               "different_axes": difference.get("only_axes", []),
                               "example_items": difference.get("only_values", [])[:6] or
                                                list(dict.fromkeys(i.name_raw for i in view.items))[:6],
                               "search_note": recovery_notes.get(tid, "A가 제시한 후보")})
            if len(tables) == len(key):
                options.append({"option_id": f"option-{len(options) + 1}", "tables": tables})
        if not options:
            return None  # 후보가 없으면 질문을 고쳐야 한다. 임의의 표를 넣지 않는다.
        groups.append({"requirement_id": req["id"], "quote": req.get("quote", ""),
                       "indicator": (req.get("indicator") or {}).get("name", "미확정"),
                       "breakdown": req.get("breakdown", []), "a_status": selection.get("status"),
                       "reasons": selection.get("reasons", []), "options": options})
    needs_interpretation = interp.get("status") != "ok" or any(
        r.get("status") != "ok" or r.get("ambiguous_condition_ids") for r in requirements)
    return {"requires_interpretation_ack": needs_interpretation,
            "recovery_note": "자동 해석이 미확정인 요구는 표 정보를 직접 확인해 선택합니다. 원래 질문은 B에 그대로 전달합니다."
                             if any(r.get("status") == "unresolved" for r in requirements) else "",
            "interpretation_reasons": interp.get("reasons", []),
            "conditions": [{"kind": c.get("kind"), "quote": c.get("quote", ""),
                            "value": c.get("value")} for c in analysis.get("conditions", [])
                           if c.get("quote")], "groups": groups}


def validate_confirmation(paused, payload):
    """서버에 보관한 중단 지점만 재개한다. A의 원래 결과는 바꾸지 않는다."""
    if paused.get("status") != "needs_confirmation" or paused.get("error_code") not in {
        "a_interpretation_unsettled", "a_selection_unsettled"}:
        raise ValueError("이 실행은 A 후보 표 확인을 기다리는 상태가 아닙니다.")
    confirmation = paused.get("confirmation")
    if not confirmation:
        raise ValueError("선택할 후보가 없습니다. 질문을 구체적으로 고쳐 다시 실행해 주세요.")
    if not isinstance(payload, dict) or set(payload) != {"choices", "interpretation_confirmed"}:
        raise ValueError("후보 표 선택 내용이 올바르지 않습니다.")
    if not isinstance(payload["interpretation_confirmed"], bool):
        raise ValueError("질문 해석 확인 여부를 확인해 주세요.")
    if confirmation["requires_interpretation_ack"] and not payload["interpretation_confirmed"]:
        raise ValueError("화면에 표시한 질문 해석을 확인한 뒤 계속해 주세요.")
    choices = payload["choices"]
    if not isinstance(choices, list) or len(choices) != len(confirmation["groups"]):
        raise ValueError("각 요구마다 후보 표 묶음을 하나씩 선택해 주세요.")
    chosen = {}
    for choice in choices:
        if not isinstance(choice, dict) or set(choice) != {"requirement_id", "option_id"}:
            raise ValueError("후보 표 선택 형식이 올바르지 않습니다.")
        rid, oid = choice["requirement_id"], choice["option_id"]
        if not isinstance(rid, str) or not isinstance(oid, str) or rid in chosen:
            raise ValueError("같은 요구를 중복 선택할 수 없습니다.")
        chosen[rid] = oid
    if set(chosen) != {g["requirement_id"] for g in confirmation["groups"]}:
        raise ValueError("확인할 요구가 원래 질문과 다릅니다.")
    tables, seen, records = [], set(), []
    for group in confirmation["groups"]:
        option = next((o for o in group["options"] if o["option_id"] == chosen[group["requirement_id"]]), None)
        if option is None:
            raise ValueError("제시된 후보 중에서 선택해 주세요.")
        records.append({"requirement_id": group["requirement_id"], "option_id": option["option_id"],
                        "tables": copy.deepcopy(option["tables"])})
        for table in option["tables"]:
            key = table["org_id"], table["table_id"]
            if key not in seen:
                seen.add(key)
                tables.append({k: table[k] for k in ("org_id", "table_id", "table_name")})
    record = {"source": "user_confirmation", "parent_run_id": paused["run_id"],
              "at": datetime.now(timezone.utc).isoformat(), "choices": records,
              "interpretation_confirmed": payload["interpretation_confirmed"],
              "a_original_interpretation_status": (paused["analysis"].get("interpretation") or {}).get("status")}
    return {"tables": tables}, record
