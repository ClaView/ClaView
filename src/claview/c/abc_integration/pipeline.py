"""질문 → 새 A → 기존 B → 실제 fetch → C.

읽는 순서: Pipeline.run. 정답/샘플 숫자는 여기서 읽지 않는다.
검증용 모의 객체는 tests에서만 주입한다. 미확정 결과를 성공으로 바꾸지 않는다.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dependencies import HERE, B_ROOT, configure

VERSIONS = configure()
from claview_v3.catalog.layer import load_layer
from claview_v3.interpret.pipeline import interpret_question
from claview_v3.select.graph import select_tables
from claview.contracts import Config, StageFailure, Trace, outcome
from claview.metadata import MetadataStore
from claview.services import Services
from claview.graphs import build_b_graph
from claview.fetch import KosisClient, fetch_data
from claview.models import HcxModel
from c_renderer import period_label
from c_analysis import renderer, decorate_cards
from composition import attach_live_total
from confirmation import make_confirmation, validate_confirmation
from kosis_compat import UnitCompatibleKosisClient
from b_conditions import make_editor, validate_edit
from query_contract import normalize_b_query, preserve_rate_periods
from request_capabilities import unsupported_request
from named_selections import complete_named_selections
from model_bridge import ScopedModel,table_requirements

STAGES = ("question", "table", "conditions", "fetch", "chart")
FREQ = {"Y": "연간", "H": "반기", "Q": "분기", "M": "월별"}
MESSAGES = {
    "conditions_incomplete": "기간 등 조회 조건이 부족합니다. 질문에 기간과 주기를 구체적으로 적어 주세요.",
    "metadata_refresh_required": "요청 기간이 저장된 메타정보의 수록 범위를 벗어납니다. 메타 갱신 또는 기간 확인이 필요합니다.",
    "selection_count_requires_confirmation": "항목이 30개를 넘습니다. 조회할 대상을 좁혀 주세요.",
    "calculation_definition_required": "계산 기준을 먼저 확인해야 합니다. 원값을 계산 결과로 대신 표시하지 않습니다.",
    "hcx_http_429": "HCX 호출 한도에 도달했습니다. 결과를 임의로 대신 만들지 않았습니다. 잠시 후 다시 질문해 주세요.",
    "hcx_http_401": "HCX 인증 정보를 확인해 주세요.",
    "hcx_http_403": "HCX 키의 권한을 확인해 주세요.",
    "unexpected_response_period": "조회 응답의 주기가 요청 조건과 다릅니다.",
    "response_unit_conflict": "KOSIS 응답의 단위·지수 기준이 메타정보와 다릅니다. 같은 단위라고 추측하지 않고 조회 결과 표시를 중단했습니다.",
    "a_interpretation_unsettled": "질문 해석이 아직 확정되지 않았습니다. 아래 해석 내용과 사유를 확인하고 질문을 구체화해 주세요.",
    "a_selection_unsettled": "통계표 선택에 확인이 필요합니다. 후보를 확정된 표로 간주하지 않았습니다.",
    "a_no_table": "질문에 맞는 통계표를 확정하지 못했습니다.",
    "hcx_key_missing": "실제 B 실행에 필요한 HCX 키가 서버에 설정되지 않았습니다.",
    "kosis_key_missing": "실제 자료 조회에 필요한 KOSIS 키가 서버에 설정되지 않았습니다.",
    "published_rate_period_mismatch": "공표된 증감률의 기간이 질문과 다릅니다. 조회하기 전에 기간을 확인해 주세요.",
}
REASONS = {
    "family_alternatives": "비슷한 통계표가 여러 개라 선택 확인이 필요합니다.",
    "interpretation_undecided": "질문 해석에 아직 정해지지 않은 조건이 있습니다.",
    "partial_cover": "후보 표가 질문의 요구를 모두 만족하는지 확인되지 않았습니다.",
    "period_unknown": "후보 표와 요청 기간의 대응이 확정되지 않았습니다.",
    "unspecified": "통계 기준 등 필요한 조건이 지정되지 않았습니다.",
    "unproven_condition": "요청 조건을 후보 표가 지원하는지 근거가 부족합니다.",
    "no_evidence": "표를 선택할 근거를 찾지 못했습니다.",
    "no_cover": "질문의 요구를 충족하는 표를 찾지 못했습니다.",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("질문을 1~2,000자로 입력해 주세요.")
    # 비밀값을 질문으로 잘못 붙여 넣어 모델/로그로 보내지 않도록 차단한다.
    if re.search(r"\bnv-[A-Za-z0-9]{16,}|\bBearer\s+\S+|apiKey\s*[=:]|API[_ ]?KEY\s*[=:]|[A-Za-z0-9+/]{32,}={0,2}", query, re.I):
        raise ValueError("키는 질문에 입력하지 마세요. 서버 터미널에서만 설정합니다.")
    return query.strip()


def b_handoff(analysis, selected):
    """전달 계약: 확정된 해석 + 확정된 표만 query/tables의 기존 B 계약으로 변환."""
    interp = analysis.get("interpretation") or {}
    if interp.get("status") != "ok":
        raise StageFailure("a_interpretation_unsettled", "needs_confirmation")
    requirements = interp.get("requirements") or []
    if not requirements or any(r.get("status") != "ok" or r.get("ambiguous_condition_ids")
                               for r in requirements):
        raise StageFailure("a_interpretation_unsettled", "needs_confirmation")
    choices = selected.get("selections") or []
    if {r["id"] for r in requirements} != {s.get("requirement_id") for s in choices}:
        raise StageFailure("a_no_table", "unresolved")
    if any(s.get("status") != "auto" for s in choices):
        raise StageFailure("a_selection_unsettled", "needs_confirmation")
    tables, seen = [], set()
    for selection in choices:
        for table in selection.get("tables", []):
            row = {k: table.get(k) for k in ("org_id", "table_id", "table_name")}
            if not all(isinstance(v, str) and v for v in row.values()):
                raise StageFailure("a_no_table", "unresolved")
            key = (row["org_id"], row["table_id"])
            if key not in seen:
                seen.add(key)
                tables.append(row)
    if not tables:
        raise StageFailure("a_no_table", "unresolved")
    return {"tables": tables}


def notices_text(notices):
    return [n if isinstance(n, str) else n.get("message") or json.dumps(n, ensure_ascii=False)
            for n in notices]


def ui_cases(chart, data_result, run_id, tables):
    """C의 결과를 기존 시안 A 카드 모델로 옮긴다. 단위/주기가 다르면 별도 카드."""
    spec = chart["views"][0]["spec"]
    names = {t["table_id"]: t["table_name"] for t in tables}
    out = []
    for group in spec["groups"]:
        series = [s for s in spec["series"] if s["series_id"] in group["series_ids"]]
        requested = spec.get("requested_period")
        periods = [p for p in group["periods"]
                   if not requested or requested["start"] <= p <= requested["end"]]
        freq = group["frequency"]
        unit = group["unit"] if group["unit"] is not None else "단위 미제공"
        lookup = {s["series_id"]: {p["period"]: p for p in s["points"]} for s in series}
        rows = [p for s in series for p in s["points"] if p["period"] in periods]
        quality = {"total_cells": len(periods) * len(series),
                   "observed_values": sum(p["value_status"] == "observed" for p in rows),
                   "missing_values": sum(p["value_status"] != "observed" for p in rows)
                                     + len(periods) * len(series) - len(rows)}
        table_ids = list(dict.fromkeys(s["selection"]["table_id"] for s in series))
        cards = []
        # 각 그룹의 시점 수·항목 수로 선택지를 다시 검사한다.
        allowed = [v for v in chart["views"]
                   if not (v["kind"] in {"line", "small_multiples"} and len(periods) < 2)
                   and not (v["kind"] in {"horizontal", "small_multiples"} and len(series) < 2)]
        default = chart["default_view_id"]
        if default not in {v["kind"] for v in allowed}:
            default = "table"
        for s in series:
            cards.append({"id": s["series_id"], "name": s["short_label"], "full": s["label"],
                          "unit": unit, "values": [lookup[s["series_id"]].get(p, {}).get("value")
                                                    for p in periods]})
        sources = [s for s in chart["source"] if s["series_id"] in group["series_ids"]]
        fetched = [s["provenance"].get("fetched_at") for s in sources if s["provenance"].get("fetched_at")]
        warnings = notices_text(chart["warnings"])
        # B는 기준기간만 확장하며 계산 결과를 만들지 않는다. 가격수준을 증감률로 오인시키지 않는다.
        if not chart.get("calculation") and re.search(r"전월|전년|가장.*변|증감|증가율|기여도|비율", chart["query"]):
            warnings.append("KOSIS가 제공한 값을 그대로 표시합니다. 공식 증감률 항목은 그대로 사용하며, C에서 별도 증감 계산이나 최대 변화 시점 판정을 하지는 않습니다.")
        if not requested and not data_result.get("user_periods") and re.search(r"전월|전년|가장.*변", chart["query"]):
            warnings.append("요청 표시 기간이 별도로 전달되지 않아 조회된 전체 기간을 보여줍니다. 비교 계산용 기준 시점이 포함될 수 있습니다.")
        out.append({"id": f"{run_id}-{group['group_id']}", "query": chart["query"], "mode": "live",
                    "tableName": " · ".join(names.get(t, t) for t in table_ids), "tableId": " · ".join(table_ids),
                    "org": "한국은행", "freq": freq, "freqName": FREQ[freq], "unit": unit,
                    "start": period_label(periods[0], freq), "end": period_label(periods[-1], freq),
                    "requestedPeriod": copy.deepcopy(requested),
                    "periods": periods, "labels": [period_label(p, freq) for p in periods], "series": cards,
                    "kinds": [v["kind"] for v in allowed], "kindTitles": {v["kind"]: v["title"] for v in allowed},
                    "defaultView": default, "smallType": "bar" if "막대" in chart["query"] else "line",
                    "maxDecimals": 1, "recommend": chart["recommendation"],
                    "blocked": chart["blocked_views"], "quality": quality, "synthetic": chart["synthetic"],
                    "warnings": warnings, "notices": notices_text(chart["notices"]),
                    "fetchedAt": max(fetched) if fetched else "조회 시각 미제공",
                    "params": {"계열별 실제 조회 조건": json.dumps(sources, ensure_ascii=False)},
                    "sourceUrl": "https://kosis.kr/", "sourceRows": sources})
    return decorate_cards(chart, out)


class Pipeline:
    def __init__(self, hcx_key=None, kosis_key=None, *, runs_dir=HERE / "runs", model=None, client=None):
        self.layer = load_layer(include_external_synonyms=False)
        self.store = MetadataStore(B_ROOT / "data/catalog/search_catalog.json", B_ROOT / "data/raw/boK_meta_raw.jsonl")
        self.model = model or (HcxModel(hcx_key, retries=0, min_interval=35) if hcx_key else None)
        self.client = client or (UnitCompatibleKosisClient(kosis_key) if kosis_key else None)
        self.runs_dir = Path(runs_dir) if runs_dir else None
        self.secrets = tuple(k for k in (hcx_key, kosis_key) if k)

    def close(self):
        for obj in (self.model, self.client):
            if obj and hasattr(obj, "close"):
                obj.close()

    def validate_resume(self, paused, confirmation):
        # API 요청을 수락하기 전에, 새 HCX/KOSIS 호출 없이 선택을 검사한다.
        return validate_confirmation(paused, confirmation)

    def resume(self, paused, confirmation, *, emit=lambda x: None):
        handoff, record = self.validate_resume(paused, confirmation)
        return self.run(paused["query"], as_of=paused["as_of"], emit=emit,
                        _confirmed=(copy.deepcopy(paused), handoff, record))

    def validate_condition_resume(self, paused, conditions):
        return validate_edit(paused, conditions, self.store)

    def resume_conditions(self, paused, conditions, *, emit=lambda x: None):
        result, record = self.validate_condition_resume(paused, conditions)
        return self.run(paused["query"], as_of=paused["as_of"], emit=emit,
                        _confirmed=(copy.deepcopy(paused), copy.deepcopy(paused["a_result"]), paused.get("user_confirmation")),
                        _b_confirmed=(result, record))

    def run(self, query, *, emit=lambda x: None, as_of=None, through="C", _confirmed=None, _b_confirmed=None):
        query = validate_query(query)
        if any(secret in query for secret in self.secrets):
            raise ValueError("질문에 키를 포함할 수 없습니다.")
        if through not in {"A", "B", "C"}:
            raise ValueError("지원하지 않는 마지막 단계입니다.")
        trace, run_id = Trace(), uuid4().hex
        config = Config(**({"as_of": as_of} if as_of else {}))
        state = {"run_id": run_id, "query": query, "as_of": config.as_of, "versions": VERSIONS,
                 "status": "running", "started_at": now(), "stages": [
                     {"id": stage, "status": "pending", "summary": "앞 단계 완료 후 실행합니다."} for stage in STAGES],
                 "stage_outcomes": {}, "ui_cases": [], "chart_result": None,
                 "execution_mode": "actual_pipeline", "a_model_mode": "rules_only"}
        current = "question"

        def update(stage, status, summary, rows=None, listing=None, note=""):
            nonlocal current
            current = stage
            entry = {"id": stage, "status": status, "summary": summary,
                     "rows": rows or [], "list": listing or [], "note": note, "at": now()}
            if _b_confirmed and stage in {"question", "table"}:
                entry = copy.deepcopy(_confirmed[0]["stages"][STAGES.index(stage)])
                entry["note"] = entry.get("note", "") + " · 이전 확정 결과 유지. A를 재실행하지 않았습니다."
            state["stages"][STAGES.index(stage)] = entry
            emit({"type": "stage", "stage": copy.deepcopy(entry), "run_id": run_id})

        try:
            if _confirmed:
                paused, handoff, record = _confirmed
                analysis, selected = paused["analysis"], paused["a_selection"]
                state["parent_run_id"] = paused["run_id"]
                if record:
                    state["user_confirmation"] = record
                if _b_confirmed:
                    state["condition_confirmation"] = _b_confirmed[1]
                    state["resume_kind"] = "b_conditions"
            else:
                update("question", "running", "공유된 A 코드로 질문을 해석하고 있습니다.")
                analysis = interpret_question(query, self.layer)
            state["analysis"] = analysis
            interp = analysis.get("interpretation") or {}
            reqs = interp.get("requirements") or []
            condition_rows = [["질문", query], ["해석 상태", interp.get("status", "미확정")],
                              ["해석 방식", "10/20 규칙 기반 · HCX 미호출"]]
            listing = [f"{r['id']}: {r.get('quote', '')} ({r.get('status', '')})" for r in reqs]
            update("question", "completed" if _confirmed or interp.get("status") == "ok" else "needs_confirmation",
                   ("사용자가 질문 해석을 확인했습니다." if interp.get("status") != "ok" else "앞서 확정한 질문 해석을 유지합니다.")
                   if _confirmed else (f"요구 {len(reqs)}개를 해석했습니다." if interp.get("status") == "ok" else "질문 해석 확인이 필요합니다."),
                   condition_rows, listing, " · ".join(interp.get("reasons", [])) +
                   (" · 사용자 확인 후 재개. A 원본 판단은 실행 기록에 보존합니다." if _confirmed else ""))
            capability=unsupported_request(query,self.layer)
            if capability:
                state['capability']=capability
                raise StageFailure(capability['code'],capability['status'])
            if not _confirmed:
                update("table", "running", "카탈로그와 공식 메타정보로 통계표 후보를 확인하고 있습니다.")
                selected = select_tables(analysis, self.layer, config.as_of)
            state["a_selection"] = selected
            selected_list = [f"{s['requirement_id']} · {s['status']} · " +
                             ", ".join(t["table_name"] + " [" + t["table_id"] + "]" for t in s.get("tables", []))
                             for s in selected.get("selections", [])]
            state["stages"][1]["list"] = selected_list
            state["stages"][1]["note"] = "\n".join(dict.fromkeys(
                REASONS.get(reason, reason) for s in selected.get("selections", [])
                for reason in s.get("reasons", [])))
            if not _confirmed and (interp.get("status") != "ok" or
                                   any(s.get("status") != "auto" for s in selected.get("selections", []))):
                # 해석도 확인 중인 경우라도 표 후보 탐색은 이미 실행했다.
                # 이를 '실행 안 함'으로 표시하지 않고, 두 단계의 확인 대기를 각각 남긴다.
                update("table", "needs_confirmation", "통계표 후보를 찾았습니다. 조회할 표를 확인해 주세요.",
                       listing=selected_list, note=state["stages"][1]["note"])
            if not _confirmed:
                handoff = b_handoff(analysis, selected)
            state["a_result"] = handoff
            confirmed_list = [f"{t['table_name']} [{t['table_id']}]" for t in handoff["tables"]]
            update("table", "completed", f"{'사용자가 ' if _confirmed else ''}통계표 {len(handoff['tables'])}개를 확정했습니다.",
                   [["B 전달 정보", "원래 질문 + 기관·표 코드·표 이름"],
                    ["확정 방식", "사용자 후보 선택" if _confirmed else "A 자동 확정"]],
                   confirmed_list if _confirmed else selected_list,
                   "최신 A의 상세 해석 조건은 아직 기존 B 모델에 직접 전달하지 않습니다.")
            if through == "A":
                state["status"] = "a_verified"
                return self._finish(state, trace, emit)

            update("conditions", "running", "기존 B가 조회 항목·기간·주기를 결정하고 검증합니다.")
            if _b_confirmed:
                b_out = outcome("B", _b_confirmed[0], trace_ref=trace.run_id)
            else:
                if self.model is None:
                    raise StageFailure("hcx_key_missing", "unresolved")
                scopes=table_requirements(analysis,selected,state.get('user_confirmation'))
                common=[c['quote'] for c in analysis.get('conditions',[]) if c.get('quote') and c.get('kind') in {'period','frequency','basis'}]
                services = Services(self.store, None, ScopedModel(self.model,scopes,common), config, trace)
                b_query,date_notices=normalize_b_query(query)
                if date_notices:
                    state['b_query']=b_query
                    state['query_normalization_notices']=date_notices
                b_state = build_b_graph(services).invoke(
                    {"query": b_query, "a_result": handoff},
                    {"recursion_limit": 10 + 4 * len(handoff["tables"])})
                b_out = b_state["stage_outcome"]
                if len(handoff['tables'])>1:state['b_table_requirements']=scopes
                state["b_state"] = {k: copy.deepcopy(b_state[k]) for k in (
                    "active_table", "model_output", "selections_by_table", "resolved_period") if k in b_state}
            state["stage_outcomes"]["B"] = b_out
            if b_out["status"] != "ok":
                state["b_confirmation"] = b_out.get("result")
                raise StageFailure(next((i["code"] for i in b_out.get("issues", [])), b_out["status"]), b_out["status"])
            b_result = b_out["result"]
            if not _b_confirmed:
                b_result,named_notices=complete_named_selections(state.get('b_query',query),b_result,self.store)
                if named_notices:state['b_named_adjustments']=named_notices
                b_result,adjustments=preserve_rate_periods(state.get('b_query',query),b_result,self.store)
                if adjustments:state['b_period_adjustments']=adjustments
            state["b_result"] = b_result
            listings = [f"{s['label']} · {s['period']['start']}~{s['period']['end']} "
                        f"{FREQ[s['period']['frequency']]} · {s['unit'] or '단위 미제공'}" for s in b_result["selections"]]
            update("conditions", "completed", f"조회 항목 {len(listings)}개를 결정했습니다.",
                   [["기준일", config.as_of], ["기간 결정", "사용자 수정 · B 검증기와 공식 메타로 재검증" if _b_confirmed else "B 코드·표 메타 기준"]], listings,
                   "\n".join(notices_text(b_result.get("period_notices", []))) +
                   ("\n"+" · ".join(state['query_normalization_notices']) if state.get('query_normalization_notices') else '') +
                   ("\nB 모델은 재호출하지 않았습니다. 사용자가 확정한 조건으로 조회합니다." if _b_confirmed else "") +
                   ("\n다른 주기도 대안으로 제공됐지만 자동으로 추가 조회하지 않았습니다."
                    if b_result.get("frequency_alternatives") else ""))
            if through == "B":
                state["status"] = "b_verified"
                return self._finish(state, trace, emit)

            update("fetch", "running", "확정된 조건으로 KOSIS에서 실제 숫자를 조회합니다.")
            if self.client is None:
                raise StageFailure("kosis_key_missing", "unresolved")
            fetched = fetch_data(b_result, self.store, self.client, trace)
            state["stage_outcomes"]["fetch"] = fetched
            if fetched["status"] not in {"ok", "partial"} or not fetched.get("result"):
                raise StageFailure(next((i["code"] for i in fetched.get("issues", [])), fetched["status"]), fetched["status"])
            data = copy.deepcopy(fetched["result"])
            # 팀 fetch를 수정하지 않고, 확인된 필드만 연결층에서 보존한다.
            data["relations"] = copy.deepcopy(b_result.get("relations", []))
            for field in ("requested_period", "period_notices", "user_periods"):
                if field in b_result:
                    data[field] = copy.deepcopy(b_result[field])
            # 검증된 M2 구성 집합이면 전체를 같은 기간/주기로 1회 추가 조회한다.
            # 전체는 막대에 끼워 넣지 않고 합계 대조와 비중의 분모로만 전달한다.
            attach_live_total(data, self.store, self.client, trace)
            state["data_result"] = data
            update("fetch", "warning" if fetched["status"] == "partial" else "completed",
                   f"{len(data['series'])}개 계열을 실제 조회했습니다.",
                   [["조회 상태", fetched["status"]], ["자료", "이번 실행의 KOSIS 응답 · 저장 샘플 대체 없음"]])
            update("chart", "running", "원값·단위·결측을 검사하고 Highcharts 표시 자료를 만듭니다.")
            c_out = renderer(query, data)
            state["stage_outcomes"]["C"] = c_out
            if c_out["status"] not in {"ok", "partial"}:
                raise StageFailure(next((i["code"] for i in c_out.get("issues", [])), c_out["status"]), c_out["status"])
            state["chart_result"] = c_out["result"]
            state["ui_cases"] = ui_cases(c_out["result"], data, run_id, handoff["tables"])
            state["status"] = "partial" if "partial" in {fetched["status"], c_out["status"]} else "ok"
            quality = c_out["result"]["quality_summary"]
            update("chart", "warning" if state["status"] == "partial" else "completed",
                   "차트·표 표시 자료 준비 완료",
                   [["정상값", str(quality["observed_values"])], ["빠진 값", str(quality["missing_values"])],
                    ["정상 0", str(sum(p["value_status"] == "observed" and float(p["value"]) == 0
                                      for s in data["series"] for p in s["points"]))]],
                   note="그래프는 브라우저에서 Highcharts로 그립니다. 누적은 합산 근거가 없으면 제한합니다.")
        except StageFailure as exc:
            state["status"], state["error_code"] = exc.status, exc.code
            state["message"] = state.get('capability',{}).get('message') or MESSAGES.get(exc.code, f"해당 단계에서 중단했습니다. 확인 코드: {exc.code}")
            # 해석이 불확실한 상태를 표 선택 완료로 감추지 않는다.
            stage = "question" if exc.code == "a_interpretation_unsettled" else current
            original = state["stages"][STAGES.index(stage)]
            update(stage, "needs_confirmation" if exc.status in {"needs_confirmation", "unresolved", "unsupported", "awaiting_user"} else "failed",
                   state["message"], original.get("rows"), original.get("list"), original.get("note", ""))
            if exc.code in {"a_interpretation_unsettled", "a_selection_unsettled"}:
                state["confirmation"] = make_confirmation(state["analysis"], state["a_selection"], self.layer)
                if state["confirmation"]:
                    state["message"] = "후보 표와 질문 해석을 확인해 주세요. 선택한 표로 B의 조건 선택·KOSIS 조회·그래프 표시를 이어갑니다."
            elif stage == "conditions":
                try:
                    state["condition_editor"] = make_editor(state, self.store)
                except (StageFailure, KeyError, TypeError, AttributeError, ValueError) as editor_error:
                    # 편집용 메타를 준비하지 못해도 원래 B 중단 기록을 잃지 않는다.
                    state["condition_editor"] = None
                    state["editor_error_type"] = type(editor_error).__name__
                    state["message"] += " 수정 선택지를 준비하지 못했습니다. 메타정보를 확인해 주세요."
                if state["condition_editor"]:
                    state["message"] = "B에서 조회 조건을 확정하지 못했습니다. 아래에서 기간·항목을 확인하고 수정하면 실제 조회와 그래프 표시를 이어갑니다."
                    update("conditions", "needs_confirmation", state["condition_editor"]["reason"],
                           [["중단 사유", exc.code]], note="아직 KOSIS 조회와 C는 실행하지 않았습니다.")
        except Exception as exc:
            state.update(status="error", error_code="integration_error", exception_type=type(exc).__name__,
                         message="실행 중 연결 오류가 발생했습니다. 저장 샘플로 대체하지 않았습니다.")
            update(current, "failed", state["message"], [["오류 종류", type(exc).__name__]])
        return self._finish(state, trace, emit)

    def _finish(self, state, trace, emit):
        for stage in state["stages"]:
            if stage["status"] in {"pending", "running"}:
                stage.update(status="not_run", summary="앞 단계 미확정 또는 지정된 검수 범위 때문에 실행하지 않았습니다.")
        state["finished_at"] = now()
        state["calls"] = {service: sum(e.get("event") == "transport_attempt" and
                                      e.get("service") == service for e in trace.events) for service in ("HCX", "KOSIS")}
        # 실패 원인도 재현할 수 있게 모델·API의 진단 기록을 로컬 원본에만 보관한다.
        # transport 기록은 인증 헤더/키를 담지 않으며 아래 비밀값 검사도 통과해야 한다.
        state["trace_events"] = copy.deepcopy(trace.events)
        # 작은 화면 응답에는 전체 모델 문맥을 넣지 않는다. 진단 원본은 로컬 runs에만 저장한다.
        response = {k: state[k] for k in ("run_id", "query", "as_of", "status", "stages", "ui_cases",
                                          "versions", "started_at", "finished_at", "calls", "a_model_mode")}
        response["message"] = state.get("message", "실제 실행 결과입니다.")
        response["error_code"] = state.get("error_code")
        for key in ("confirmation", "condition_editor", "parent_run_id", "user_confirmation", "condition_confirmation", "resume_kind"):
            if key in state:
                response[key] = copy.deepcopy(state[key])
        raw = json.dumps(state, ensure_ascii=False)
        if any(secret in raw for secret in self.secrets):
            raise RuntimeError("민감정보 검사가 출력을 차단했습니다.")
        if self.runs_dir:
            folder = self.runs_dir / state["run_id"]
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "result.json").write_text(raw, encoding="utf-8")
            (folder / "response.json").write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        emit({"type": "result", "result": response})
        return state
