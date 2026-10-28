# ruff: noqa: E501  (user-facing Korean text: ruff counts full-width characters as two columns)
"""질문 → A(질문 해석·표 선택) → B(항목·기간 결정) → KOSIS 조회 → C(표시 자료).

각 모듈은 자기 service 함수로만 부른다(모듈 API `/a/select`·`/b/resolve`·`/b/fetch`·
`/c/present`와 같은 코드): A `claview_a_api.service.select/answer` → A가 넘기는 요구 묶음마다
B `claview_b_api.service.BService.resolve` → `fetch` → C `claview_c_api.service.present`.
여기서는 (1) 키/모델/조회 클라이언트를 주입하고, (2) 결과를 5단계(STAGES) 진행 기록으로 바꾸고,
(3) C 결과를 화면 카드로 옮긴다. 읽는 순서: Pipeline.run → _assemble → _render → ui_cases.

A의 확인 질문이 오면 실행을 멈추고(status=needs_confirmation) 서버가 A의 전체 상태를
보관한다. 브라우저는 option_id만 돌려보내며 A의 `answer`로 이어 실행한다.
정답/샘플 숫자는 읽지 않는다. 미확정 결과를 성공으로 바꾸지 않는다.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import hashlib
import os

from claview_a_api import service as a_service
from claview_b_api import service as b_service
from claview_c_api import service as c_service
from claview_common.contracts import CONTRACT_VERSION
from claview_common.settings import PATHS
from claview_common.settings import load as load_settings

from .confirmation import (
    make_alignment_confirmation,
    make_confirmation,
    table_names,
    validate_confirmation,
)
from .request_capabilities import unsupported_request

ROOT = PATHS.root

# External tracing stays off: questions and answers are not sent to LangSmith.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


VERSIONS = {
    "flow": "module-services/1",
    "contracts": CONTRACT_VERSION,
    "metadata_sha256": _sha(b_service.CATALOG),
    "raw_metadata_sha256": _sha(b_service.METADATA),
}


class StageFailure(Exception):
    """A stop of this run at one stage: `code` explains it, `status` is the run status."""

    def __init__(self, code: str, status: str = "error"):
        super().__init__(code)
        self.code, self.status = code, status


def attach_live_total(data, b):
    """Fetch the official M2 total that C needs to verify a composition. C itself does not fetch."""
    selection = c_service.total_selection(data)
    if selection is None:
        return
    outcome, _ = b.fetch({"selections": [selection], "relations": []})
    if outcome.get("status") == "ok" and outcome.get("result", {}).get("series"):
        data["composition_total"] = outcome["result"]["series"][0]
    else:
        data["composition_error"] = c_service.TOTAL_FETCH_ERROR

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
    "a_selection_unsettled": "통계표 선택에 확인이 필요합니다. 후보를 확정된 표로 간주하지 않았습니다.",
    "a_no_table": "질문에 맞는 통계표를 확정하지 못했습니다. 지표 이름이나 분류를 더 구체적으로 적어 주세요.",
    "hcx_key_missing": "실제 B 실행에 필요한 HCX 키가 서버에 설정되지 않았습니다.",
    "kosis_key_missing": "실제 자료 조회에 필요한 KOSIS 키가 서버에 설정되지 않았습니다.",
    "b_connection_error": "A와 B 사이의 전달 계약 검사를 통과하지 못했습니다. 질문을 바꿔 다시 시도해 주세요.",
    "answer_invalid": "선택한 답변을 적용하지 못했습니다. 같은 질문을 다시 입력해 주세요.",
    "kosis_no_data": "KOSIS에 요청한 조건의 자료가 없습니다(err 30). A가 고른 표나 분류가 질문에 맞는지 확인해 주세요. 아래 요청 조건을 KOSIS 화면에서 직접 조회해 볼 수 있습니다.",
    "kosis_service_error": "KOSIS가 조회 요청을 처리하지 못했습니다. 아래 KOSIS 응답과 요청 조건을 확인해 주세요.",
    "no_data": "조회 응답에 값이 없습니다. 기간이나 항목을 바꿔 다시 질문해 주세요.",
    "frequency_choice_required": "함께 요청한 지표의 주기가 서로 다릅니다. 주기를 맞추는 방법을 선택해 주세요. 맞추면 값이 바뀌므로 임의로 정하지 않았습니다.",
    "alignment_period_too_short": "조회 기간이 맞추려는 주기 한 기간보다 짧아 주기를 맞출 수 없습니다. 기간을 늘려 다시 질문해 주세요.",
    "alignment_policy_not_allowed": "선택한 방법은 이 지표에 쓸 수 없습니다(비율·지수와 잔액 지표는 합계로 바꿀 수 없음).",
    "alignment_policy_invalid": "선택한 주기 맞춤 방법을 적용하지 못했습니다. 같은 질문을 다시 입력해 주세요.",
}
# 여러 요구를 한 차트에 합치라는 표현이 있을 때만 요구별 결과를 합친다(그 외에는 요구별 카드).
COMBINE_REQUEST = re.compile(
    r"한\s*(?:그래프|차트|화면)|하나의\s*(?:그래프|차트|화면)|같은\s*(?:그래프|차트)|이중\s*축|양축|겹쳐|한꺼번에"
)
SKIP_REASONS = {
    "no_table": "질문에 맞는 표 후보를 찾지 못했습니다.",
    "execution_unresolved": "조회에 필요한 조건이 확정되지 않았습니다.",
    "indicator_meaning_unsupported": "지표의 의미를 확정하지 못해 조회하지 않았습니다.",
}
BLOCKED_STATUSES = {"needs_confirmation", "unresolved", "unsupported", "awaiting_user"}
# Stops that no user choice can fix: shown as failed, not as waiting for confirmation.
HARD_STOPS = {
    "a_no_table",
    "hcx_key_missing",
    "kosis_key_missing",
    "answer_invalid",
    "b_connection_error",
}


def now():
    return datetime.now(UTC).isoformat()


def validate_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("질문을 1~2,000자로 입력해 주세요.")
    # 비밀값을 질문으로 잘못 붙여 넣어 모델/로그로 보내지 않도록 차단한다.
    if re.search(
        r"\bnv-[A-Za-z0-9]{16,}|\bBearer\s+\S+|apiKey\s*[=:]|API[_ ]?KEY\s*[=:]|[A-Za-z0-9+/]{32,}={0,2}",
        query,
        re.I,
    ):
        raise ValueError("키는 질문에 입력하지 마세요. 서버 터미널에서만 설정합니다.")
    return query.strip()


def notices_text(notices):
    if isinstance(notices, dict):
        notices = list(notices.values())
    return [
        n if isinstance(n, str) else n.get("message") or json.dumps(n, ensure_ascii=False)
        for n in notices
    ]


def ui_cases(chart, data_result, run_id, tables):
    """C의 결과를 시안 A 카드 모델로 옮긴다. 단위/주기가 다르면 별도 카드."""
    spec = chart["views"][0]["spec"]
    names = {t["table_id"]: t["table_name"] for t in tables}
    out = []
    groups = spec["groups"]
    axes = spec.get("axes")
    if axes:
        # 이중 축: 단위가 다른 그룹을 한 카드로 합치고, 계열마다 단위와 축 번호를 싣는다.
        groups = [
            {
                "group_id": "G1",
                "frequency": groups[0]["frequency"],
                "unit": " · ".join(str(a["unit"] or "단위 미제공") for a in axes),
                "series_ids": [s["series_id"] for s in spec["series"]],
                "periods": sorted({p for g in groups for p in g["periods"]}),
            }
        ]
    axis_of = {i: n for n, a in enumerate(axes or []) for i in a["series_ids"]}
    for group in groups:
        series = [s for s in spec["series"] if s["series_id"] in group["series_ids"]]
        requested = spec.get("requested_period")
        periods = [
            p
            for p in group["periods"]
            if not requested or requested["start"] <= p <= requested["end"]
        ]
        freq = group["frequency"]
        unit = group["unit"] if group["unit"] is not None else "단위 미제공"
        lookup = {s["series_id"]: {p["period"]: p for p in s["points"]} for s in series}
        rows = [p for s in series for p in s["points"] if p["period"] in periods]
        quality = {
            "total_cells": len(periods) * len(series),
            "observed_values": sum(p["value_status"] == "observed" for p in rows),
            "missing_values": sum(p["value_status"] != "observed" for p in rows)
            + len(periods) * len(series)
            - len(rows),
        }
        table_ids = list(dict.fromkeys(s["selection"]["table_id"] for s in series))
        cards = []
        # 각 그룹의 시점 수·항목 수로 선택지를 다시 검사한다.
        allowed = [
            v
            for v in chart["views"]
            if not (v["kind"] in {"line", "small_multiples"} and len(periods) < 2)
            and not (v["kind"] in {"horizontal", "small_multiples"} and len(series) < 2)
        ]
        default = chart["default_view_id"]
        if default not in {v["kind"] for v in allowed}:
            default = "table"
        for s in series:
            cards.append(
                {
                    "id": s["series_id"],
                    "name": (
                        f"{s['short_label']} ({s['unit'] or '단위 미제공'}, "
                        f"{'왼쪽' if axes[axis_of[s['series_id']]]['side'] == 'left' else '오른쪽'} 축)"
                        if axes
                        else s["short_label"]
                    ),
                    "full": s["label"],
                    "unit": (s["unit"] or "단위 미제공") if axes else unit,
                    "values": [lookup[s["series_id"]].get(p, {}).get("value") for p in periods],
                    **({"axis": axis_of[s["series_id"]]} if axes else {}),
                }
            )
        sources = [s for s in chart["source"] if s["series_id"] in group["series_ids"]]
        fetched = [
            s["provenance"].get("fetched_at") for s in sources if s["provenance"].get("fetched_at")
        ]
        warnings = notices_text(chart["warnings"])
        # B는 기준기간만 확장하며 계산 결과를 만들지 않는다. 가격수준을 증감률로 오인시키지 않는다.
        if not chart.get("calculation") and re.search(
            r"전월|전년|가장.*변|증감|증가율|기여도|비율", chart["query"]
        ):
            warnings.append(
                "KOSIS가 제공한 값을 그대로 표시합니다. 공식 증감률 항목은 그대로 사용하며, "
                "C에서 별도 증감 계산이나 최대 변화 시점 판정을 하지는 않습니다."
            )
        if (
            not requested
            and not data_result.get("user_periods")
            and re.search(r"전월|전년|가장.*변", chart["query"])
        ):
            warnings.append(
                "요청 표시 기간이 별도로 전달되지 않아 조회된 전체 기간을 보여줍니다. "
                "비교 계산용 기준 시점이 포함될 수 있습니다."
            )
        out.append(
            {
                "id": f"{run_id}-{group['group_id']}",
                "query": chart["query"],
                "mode": "live",
                "tableName": " · ".join(names.get(t, t) for t in table_ids),
                "tableId": " · ".join(table_ids),
                "org": "한국은행",
                "freq": freq,
                "freqName": FREQ[freq],
                "unit": unit,
                "start": c_service.period_label(periods[0], freq),
                "end": c_service.period_label(periods[-1], freq),
                "requestedPeriod": copy.deepcopy(requested),
                "periods": periods,
                "labels": [c_service.period_label(p, freq) for p in periods],
                "series": cards,
                "kinds": [v["kind"] for v in allowed],
                "kindTitles": {v["kind"]: v["title"] for v in allowed},
                "defaultView": default,
                "smallType": "bar" if "막대" in chart["query"] else "line",
                **(
                    {
                        "axes": [
                            {
                                "side": a["side"],
                                "unit": a["unit"] or "단위 미제공",
                                "draw": a["draw"],
                            }
                            for a in axes
                        ]
                    }
                    if axes
                    else {}
                ),
                "maxDecimals": 1,
                "recommend": chart["recommendation"],
                "blocked": chart["blocked_views"],
                "quality": quality,
                "synthetic": chart["synthetic"],
                "warnings": warnings,
                "notices": notices_text(chart["notices"]),
                "fetchedAt": max(fetched) if fetched else "조회 시각 미제공",
                "params": {"계열별 실제 조회 조건": json.dumps(sources, ensure_ascii=False)},
                "sourceUrl": "https://kosis.kr/",
                "sourceRows": sources,
            }
        )
    return c_service.decorate_cards(chart, out)


def _remember(traces, trace):
    """Keep each B run's trace (once) so failures can be diagnosed from the local run record."""
    if all(t is not trace for t in traces):
        traces.append(trace)


def _service_error_rows(traces):
    """Rows for the stage card: what KOSIS answered and which request failed (no credential)."""
    for trace in reversed(traces):
        for event in reversed(trace.events):
            if event.get("event") != "data_response_error":
                continue
            rows = event.get("rows")
            first = (
                rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else rows
            )
            if isinstance(first, dict):
                answer = {k: first[k] for k in ("err", "errMsg") if k in first}
            else:
                answer = {"형식": type(rows).__name__}
            rows_out = [
                ["KOSIS 응답", json.dumps(answer or first, ensure_ascii=False)[:300]],
                ["요청 조건", json.dumps(event.get("params"), ensure_ascii=False)[:500]],
            ]
            return answer.get("err") if isinstance(answer, dict) else None, rows_out
    return None, []


class _ModelProbe:
    """Counts B's model calls and tells the run when B starts. Delegates everything else."""

    def __init__(self, model, on_first, traces):
        self._model, self._on_first, self._traces, self.calls = model, on_first, traces, 0

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.calls += 1
        _remember(self._traces, trace)
        if self.calls == 1:
            self._on_first()
        return self._model.complete(task, system, context, schema, max_tokens, trace)

    def __getattr__(self, name):
        return getattr(self._model, name)


class _ClientProbe:
    """Counts KOSIS calls and tells the run when fetching starts."""

    def __init__(self, client, on_first, traces):
        self._client, self._on_first, self._traces, self.calls = client, on_first, traces, 0

    def fetch(self, params, trace):
        self.calls += 1
        _remember(self._traces, trace)
        if self.calls == 1:
            self._on_first()
        return self._client.fetch(params, trace)

    def __getattr__(self, name):
        return getattr(self._client, name)


class Pipeline:
    def __init__(
        self,
        hcx_key=None,
        kosis_key=None,
        *,
        runs_dir=PATHS.runs / "pipeline",
        model=None,
        client=None,
        session=None,
    ):
        # `session` (anything with a loaded `.store`) only saves reloading the catalog in tests.
        self.store = session.store if session is not None else b_service.store()
        live_model, live_client = b_service.live_clients(
            hcx_key=hcx_key, kosis_key=kosis_key, min_interval=35, retries=0
        )
        self.model = model or live_model
        self.client = client or live_client
        self.mode = load_settings().mode
        self.runs_dir = Path(runs_dir) if runs_dir else None
        self.secrets = tuple(k for k in (hcx_key, kosis_key) if k)

    def close(self):
        for obj in (self.model, self.client):
            if obj and hasattr(obj, "close"):
                obj.close()

    # ---- resume (answer to A's question) --------------------------------------------
    def validate_resume(self, paused, confirmation):
        """Check the browser's choice before accepting the request; no model/KOSIS call."""
        return validate_confirmation(paused, confirmation)

    def resume(self, paused, confirmation, *, emit=lambda x: None):
        answer = self.validate_resume(paused, confirmation)
        if answer.get("kind") == "alignment":
            # 조회·모델 호출 없이, 보관한 조회 결과에 선택한 주기 맞춤 방법만 적용한다.
            return self.run(
                paused["query"],
                as_of=paused["as_of"],
                emit=emit,
                _resume_alignment=(paused, answer["option_id"]),
            )
        return self.run(paused["query"], as_of=paused["as_of"], emit=emit, _resume=(paused, answer))

    # ---- one run ---------------------------------------------------------------------
    def run(
        self,
        query,
        *,
        emit=lambda x: None,
        as_of=None,
        through="C",
        _resume=None,
        _resume_alignment=None,
    ):
        query = validate_query(query)
        if any(secret in query for secret in self.secrets):
            raise ValueError("질문에 키를 포함할 수 없습니다.")
        if through not in {"A", "B", "C"}:
            raise ValueError("지원하지 않는 마지막 단계입니다.")
        run_id = uuid4().hex
        state = {
            "run_id": run_id,
            "query": query,
            "as_of": as_of or date.today().isoformat(),
            "versions": VERSIONS,
            "status": "running",
            "started_at": now(),
            "stages": [
                {"id": s, "status": "pending", "summary": "앞 단계 완료 후 실행합니다."}
                for s in STAGES
            ],
            "stage_outcomes": {},
            "ui_cases": [],
            "execution_mode": "actual_pipeline",
            "a_model_mode": "rules_only",
        }
        current = "question"

        def update(stage, status, summary, rows=None, listing=None, note=""):
            nonlocal current
            current = stage
            entry = {
                "id": stage,
                "status": status,
                "summary": summary,
                "rows": rows or [],
                "list": listing or [],
                "note": note,
                "at": now(),
            }
            state["stages"][STAGES.index(stage)] = entry
            emit({"type": "stage", "stage": copy.deepcopy(entry), "run_id": run_id})

        # A's question/table cards and B's condition card are written in full before the next
        # module is called, so these only mark the stage that starts (they must not overwrite).
        def b_started():
            update("conditions", "running", "B가 조회 항목·기간·주기를 결정하고 검증합니다.")

        def fetch_started():
            update("fetch", "running", "확정된 조건으로 KOSIS에서 실제 숫자를 조회합니다.")

        traces: list = []
        model = (
            _ModelProbe(self.model, b_started, traces)
            if self.model is not None and through != "A"
            else None
        )
        client = (
            _ClientProbe(self.client, fetch_started, traces)
            if self.client is not None and through == "C"
            else None
        )
        try:
            if _resume_alignment:
                self._resume_alignment(state, _resume_alignment, update, traces)
            else:
                update("question", "running", "A(규칙 기반)가 질문을 해석하고 통계표를 고릅니다.")
                capability = unsupported_request(query, self.store.catalog)
                if capability:
                    state["capability"] = capability
                    raise StageFailure(capability["code"], capability["status"])
                if _resume:
                    paused, answer = _resume
                    state["parent_run_id"] = paused["run_id"]
                    state["resume_kind"] = "a_question"
                    a_out = a_service.answer(paused["a_state"], answer["option_id"])
                    a_state = a_out["state"]
                    if a_state["response"].get("answer_error"):
                        state["answer_error"] = a_state["response"]["answer_error"]
                        raise StageFailure("answer_invalid", "unresolved")
                    state["user_confirmation"] = {
                        "source": "user_confirmation",
                        "parent_run_id": paused["run_id"],
                        "at": now(),
                        "question_id": answer["question_id"],
                        "option_id": answer["option_id"],
                    }
                else:
                    a_out = a_service.select(query, state["as_of"])
                    a_state = a_out["state"]
                state["a_state"] = a_state
                state["as_of"] = a_state["as_of"]
                state["b_requests"] = a_out["b_requests"]
                b = b_service.BService(model, client, self.mode)
                self._assemble(
                    state, a_state, a_out["b_requests"], b, through, update, run_id, bool(_resume), traces
                )
        except StageFailure as exc:
            self._fail(state, exc, update, current)
        except Exception as exc:  # noqa: BLE001 - never show a stored sample instead of a result
            state.update(
                status="error",
                error_code="integration_error",
                exception_type=type(exc).__name__,
                message="실행 중 연결 오류가 발생했습니다. 저장 샘플로 대체하지 않았습니다.",
            )
            update(current, "failed", state["message"], [["오류 종류", type(exc).__name__]])
        state["calls"] = {
            "HCX": model.calls if model else 0,
            "KOSIS": client.calls if client else 0,
        }
        # Diagnostics stay in the local run record only (requests carry no credential).
        state["trace_events"] = [
            *state.pop("parent_trace_events", []),
            *(copy.deepcopy(e) for t in traces for e in t.events),
        ]
        return self._finish(state, emit)

    # ---- A result -> B (per requirement group) -> fetch -> C -> stages ---------------
    def _assemble(self, state, a_state, b_requests, b, through, update, run_id, resumed, traces):
        model, client = b.model, b.client
        response, analysis = a_state["response"], a_state.get("analysis") or {}
        interp = analysis.get("interpretation") or {}
        requirements = interp.get("requirements") or []
        status = response["status"]
        notes = list(
            dict.fromkeys(
                item["adopted"]["interpretation"]
                for item in (response.get("assumptions") or {}).get("items", [])
                if not item.get("confirmed") and (item.get("adopted") or {}).get("interpretation")
            )
        )
        update(
            "question",
            "completed",
            f"요구 {len(requirements)}개를 해석했습니다."
            if requirements
            else "질문을 해석하지 못했습니다.",
            [
                ["질문", state["query"]],
                ["해석 상태", interp.get("status", "미확정")],
                ["해석 방식", "규칙 기반 · HCX 미호출"],
            ],
            [f"{r['id']}: {r.get('quote', '')} ({r.get('status', '')})" for r in requirements],
            "\n".join(notes) + ("\n사용자가 선택한 답변으로 이어 실행했습니다." if resumed else ""),
        )
        selected = [
            f"{s['requirement_id']} · {s['status']} · "
            + (
                ", ".join(f"{t['table_name']} [{t['table_id']}]" for t in s.get("tables", []))
                or "후보 없음"
            )
            for s in a_state.get("selections") or []
        ]
        if status == "awaiting_answer":
            state["confirmation"] = make_confirmation(a_state, self.store)
            update(
                "table", "needs_confirmation", "통계표·해석 후보를 확인해 주세요.", listing=selected
            )
            raise StageFailure("a_selection_unsettled", "needs_confirmation")
        if status == "no_result":
            reasons = [
                SKIP_REASONS.get(s.get("reason"), s.get("reason", ""))
                for s in response.get("skipped") or []
            ]
            update(
                "table",
                "failed",
                "질문에 맞는 통계표를 확정하지 못했습니다.",
                listing=selected,
                note="\n".join(dict.fromkeys(r for r in reasons if r)),
            )
            raise StageFailure("a_no_table", "unresolved")
        names = table_names(self.store)
        selections = a_state.get("selections") or []
        unsure = [x for x in selections if x.get("status") != "auto"]
        method = "A 자동 확정" if not unsure else "A 1순위 표로 진행(A가 확정하지 못함)"
        skipped = response.get("skipped") or []
        alternatives = [
            f"{x['requirement_id']}: A가 이 표를 확정하지 못하고 1순위로 진행했습니다. 다른 후보: "
            + ", ".join(f"{names.get(b[0], b[0])} [{b[0]}]" for b in x["alternatives"][:3] if b)
            for x in unsure
            if x.get("alternatives") and not resumed
        ]
        update(
            "table",
            "completed" if status in {"proceed", "assume"} else "warning",
            f"통계표 {len({t['table_id'] for s in a_state.get('selections') or [] for t in s.get('tables', [])})}개를 정했습니다."
            + (" 일부 요구는 제외됐습니다." if status == "partial" else ""),
            [
                ["B 전달 정보", "원래 질문 + 기관·표 코드·표 이름"],
                ["확정 방식", "사용자 후보 선택" if resumed else method],
            ],
            selected,
            "\n".join(
                [
                    *(SKIP_REASONS.get(s.get("reason"), s.get("reason", "")) for s in skipped),
                    *alternatives,
                ]
            ),
        )
        if through == "A":
            state["status"] = "a_verified"
            return
        if model is None:
            update("conditions", "failed", MESSAGES["hcx_key_missing"])
            raise StageFailure("hcx_key_missing", "unresolved")
        if not b_requests:
            update("table", "failed", MESSAGES["a_no_table"], listing=selected)
            raise StageFailure("a_no_table", "unresolved")
        # One B call per requirement group A hands over (related requirements share a call).
        results = []
        for group in b_requests:
            b_outcome, _ = b.resolve(state["query"], group["tables"], state["as_of"])
            ok = b_outcome["status"] == "ok"
            result = {
                "b_outcome": b_outcome,
                "a_result": {"tables": copy.deepcopy(group["tables"])},
                "selections": ((b_outcome["result"] or {}).get("selections") or []) if ok else [],
            }
            results.append(({"status": "returned", "requirement_ids": group["requirement_ids"]}, result))
        failures, ready = [], []
        for req, result in results:
            outcome = result.get("b_outcome") or {}
            if req.get("status") != "returned" or outcome.get("status") != "ok":
                issues = outcome.get("issues") or []
                code = next((i["code"] for i in issues), None) or (
                    "b_connection_error"
                    if req.get("status") != "returned"
                    else outcome.get("status", "b_failed")
                )
                failures.append(("conditions", code, outcome.get("status") or "unresolved"))
            else:
                ready.append((req, result))
        listings = [
            f"{s['label']} · {s['period']['start']}~{s['period']['end']} "
            f"{FREQ.get(s['period']['frequency'], s['period']['frequency'])} · {s['unit'] or '단위 미제공'}"
            for _, r in ready
            for s in r["selections"]
        ]
        extra = [
            n
            for _, r in ready
            for n in notices_text((r["b_outcome"]["result"] or {}).get("period_notices", []))
        ]
        if any((r["b_outcome"]["result"] or {}).get("frequency_alternatives") for _, r in ready):
            extra.append("다른 주기도 대안으로 제공됐지만 자동으로 추가 조회하지 않았습니다.")
        state["stage_outcomes"]["B"] = [r.get("b_outcome") for _, r in results]
        if not ready:
            update(
                "conditions",
                "failed",
                MESSAGES.get(
                    failures[0][1], f"B 단계에서 중단했습니다. 확인 코드: {failures[0][1]}"
                ),
                [["중단 사유", failures[0][1]]],
            )
            raise StageFailure(failures[0][1], failures[0][2])
        update(
            "conditions",
            "warning" if failures else "completed",
            f"조회 항목 {len(listings)}개를 결정했습니다.",
            [["기준일", state["as_of"]], ["기간 결정", "B 코드·표 메타 기준"]],
            listings,
            "\n".join(extra),
        )
        if through == "B":
            state["status"] = "b_verified"
            return
        rendered_partial, jobs = False, []
        for index, (req, result) in enumerate(ready, 1):
            fetched = b.fetch(result["b_outcome"]["result"])[0] if result["selections"] else {}
            if client is None or fetched.get("status") == "not_run":
                failures.append(("fetch", "kosis_key_missing", "unresolved"))
            elif fetched.get("status") not in {"ok", "partial"} or not fetched.get("result"):
                failures.append(
                    (
                        "fetch",
                        next((i["code"] for i in fetched.get("issues", [])), None)
                        or fetched.get("status", "fetch_failed"),
                        fetched.get("status", "unresolved"),
                    )
                )
            else:
                rendered_partial |= fetched["status"] == "partial"
                jobs.append(
                    {
                        "data": self._render_data(result, fetched, b),
                        "tables": result["a_result"]["tables"],
                        "run_id": run_id + f"-{index}",
                    }
                )
        jobs = self._combine_jobs(state["query"], jobs)
        self._render_stage(state, jobs, None, update, traces, failures, rendered_partial)

    # ---- fetched series -> aligned -> C -> UI cards -----------------------------------
    def _render_data(self, result, fetched, b):
        """One requirement: fetched series plus B's relations/periods, ready for alignment."""
        b_result = result["b_outcome"]["result"]
        data = copy.deepcopy(fetched["result"])
        # 조회 결과에 없는 B 필드(상하위 관계, 기간 안내)를 연결층에서 보존한다.
        data["relations"] = copy.deepcopy(b_result.get("relations", []))
        for field in ("requested_period", "period_notices", "user_periods"):
            if field in b_result and field not in data:
                data[field] = copy.deepcopy(b_result[field])
        if b.client is not None:
            attach_live_total(data, b)
        return data

    def _combine_jobs(self, query, jobs):
        """Several requirements share one chart only when the question asks for one chart."""
        if len(jobs) < 2 or not COMBINE_REQUEST.search(query):
            return jobs
        tables = list({t["table_id"]: t for j in jobs for t in j["tables"]}.values())
        return [
            {
                "data": b_service.combine_results([j["data"] for j in jobs]),
                "tables": tables,
                "run_id": jobs[0]["run_id"],
            }
        ]

    def _render_stage(self, state, jobs, policy, update, traces, failures, rendered_partial):
        """Align (units/frequency/period), render with C, and finish the fetch/chart stages.

        When the frequencies differ and no `policy` was chosen yet, the run pauses with a
        confirmation card; the fetched data is kept so the answer needs no new HCX/KOSIS call.
        """
        aligned = []
        choices = []
        for job in jobs:
            try:
                data = b_service.align(job["data"], frequency_policy=policy)
            except b_service.AlignmentError as exc:
                failures.append(("chart", exc.code, "error"))
                state.setdefault("alignment_errors", []).append(str(exc))
                continue
            if data["alignment"]["status"] == "needs_choice":
                # Look at every job before asking: one answer is applied to all of them, so the
                # question must cover all of them and offer only what each of them allows.
                choices.append(data["alignment"]["choice"])
                continue
            aligned.append((job, data))
        if choices:
            choice = b_service.merge_frequency_choices(choices)
            count = sum(len(j["data"].get("series", [])) for j in jobs)
            fetch_failed = any(f[0] == "fetch" for f in failures)
            update(
                "fetch",
                "warning" if fetch_failed or rendered_partial else "completed",
                f"{count}개 계열을 실제 조회했습니다.",
                [
                    ["조회 상태", "일부 실패" if fetch_failed else "정상"],
                    ["자료", "이번 실행의 KOSIS 응답 · 저장 샘플 대체 없음"],
                ],
            )
            update(
                "chart",
                "needs_confirmation",
                "주기가 다른 지표를 어떻게 맞출지 선택해 주세요.",
                [
                    ["필요한 선택", "주기 맞춤 방법"],
                    ["조회 호출", "추가 호출 없음(이미 받은 값을 사용)"],
                ],
                [o["label"] for o in choice["options"]],
            )
            state["confirmation"] = make_alignment_confirmation(choice)
            state["render_jobs"] = copy.deepcopy(jobs)
            state["fetch_partial"] = rendered_partial
            state["pre_failures"] = [list(f) for f in failures]
            raise StageFailure("frequency_choice_required", "needs_confirmation")
        cases = []
        for job, data in aligned:
            out = self._render(state, job, data)
            if out is None:
                failures.append(("chart", "invalid_c_input", "error"))
            else:
                rendered_partial |= out[1] == "partial"
                cases.extend(out[0])
        fetch_failures = [f for f in failures if f[0] == "fetch"]
        if not cases:
            stage, code, kind = next(
                (f for f in failures if f[0] in {"fetch", "chart"}), failures[0]
            )
            error_rows = []
            if code == "kosis_service_error":
                err, error_rows = _service_error_rows(traces)
                if err == "30":
                    code = "kosis_no_data"
            elif stage == "chart":
                detail = (state.get("alignment_errors") or [None])[-1]
                if detail is None:
                    outs = state["stage_outcomes"].get("C") or [{}]
                    detail = next((i.get("message") for i in outs[-1].get("issues", [])), None)
                if detail:
                    error_rows = [["상세", detail]]
            update(
                stage,
                "failed",
                MESSAGES.get(code, f"해당 단계에서 중단했습니다. 확인 코드: {code}"),
                [["중단 사유", code], *error_rows],
            )
            raise StageFailure(code, kind)
        state["ui_cases"] = cases
        total_series = sum(len(c["series"]) for c in cases)
        update(
            "fetch",
            "warning" if fetch_failures or rendered_partial else "completed",
            f"{total_series}개 계열을 실제 조회했습니다.",
            [
                ["조회 상태", "일부 실패" if fetch_failures else "정상"],
                ["자료", "이번 실행의 KOSIS 응답 · 저장 샘플 대체 없음"],
            ],
        )
        state["status"] = "partial" if failures or rendered_partial else "ok"
        if failures:
            state["message"] = "일부 요구는 완료하지 못했습니다: " + ", ".join(
                dict.fromkeys(f[1] for f in failures)
            )
        update(
            "chart",
            "warning" if state["status"] == "partial" else "completed",
            "차트·표 표시 자료 준비 완료",
            note="그래프는 브라우저에서 Highcharts로 그립니다. 누적은 합산 근거가 없으면 제한합니다.",
        )

    def _render(self, state, job, data):
        """One aligned data_result -> C -> UI cards (None when C refuses the input)."""
        out = c_service.present(state["query"], data)
        state["stage_outcomes"].setdefault("C", []).append(out)
        if out["status"] not in {"ok", "partial"}:
            return None
        state.setdefault("chart_results", []).append(out["result"])
        return ui_cases(out["result"], data, job["run_id"], job["tables"]), out["status"]

    def _resume_alignment(self, state, resume, update, traces):
        """Apply the user's frequency choice to the stored fetch result (no HCX/KOSIS call)."""
        paused, policy = resume
        state["parent_run_id"] = paused["run_id"]
        state["resume_kind"] = "alignment"
        state["as_of"] = paused["as_of"]
        state["a_state"] = paused.get("a_state")
        # The stopped run did the B and fetch work; this run only re-aligns. Keep that record
        # (B outcomes, requests, trace events) so the saved result still shows what B did.
        for key, value in (paused.get("stage_outcomes") or {}).items():
            if key != "C":
                state["stage_outcomes"][key] = copy.deepcopy(value)
        if paused.get("b_requests") is not None:
            state["b_requests"] = copy.deepcopy(paused["b_requests"])
        state["parent_trace_events"] = copy.deepcopy(paused.get("trace_events") or [])
        state["user_confirmation"] = {
            "source": "user_confirmation",
            "parent_run_id": paused["run_id"],
            "at": now(),
            "question_id": "frequency_alignment",
            "option_id": policy,
        }
        for entry in paused["stages"]:
            if entry["id"] != "chart":
                update(
                    entry["id"],
                    entry["status"],
                    entry["summary"],
                    entry.get("rows"),
                    entry.get("list"),
                    entry.get("note", ""),
                )
        update("chart", "running", "선택한 방법으로 주기를 맞춰 그래프 자료를 만듭니다.")
        self._render_stage(
            state,
            copy.deepcopy(paused["render_jobs"]),
            policy,
            update,
            traces,
            [tuple(f) for f in paused.get("pre_failures", [])],
            bool(paused.get("fetch_partial")),
        )

    def _fail(self, state, exc, update, current):
        state["status"], state["error_code"] = exc.status, exc.code
        state["message"] = state.get("capability", {}).get("message") or MESSAGES.get(
            exc.code, f"해당 단계에서 중단했습니다. 확인 코드: {exc.code}"
        )
        if exc.code == "frequency_choice_required" and state.get("confirmation"):
            state["message"] = MESSAGES["frequency_choice_required"]
        if exc.code == "a_selection_unsettled" and state.get("confirmation"):
            state["message"] = (
                "후보 표를 확인해 주세요. 선택한 표로 B의 조건 선택·KOSIS 조회·그래프 표시를 이어갑니다."
            )
        stage = current
        original = state["stages"][STAGES.index(stage)]
        waiting = exc.status in BLOCKED_STATUSES and exc.code not in HARD_STOPS
        update(
            stage,
            "needs_confirmation" if waiting else "failed",
            state["message"],
            original.get("rows"),
            original.get("list"),
            original.get("note", ""),
        )

    def _finish(self, state, emit):
        for stage in state["stages"]:
            if stage["status"] in {"pending", "running"}:
                stage.update(
                    status="not_run",
                    summary="앞 단계 미확정 또는 지정된 검수 범위 때문에 실행하지 않았습니다.",
                )
        state["finished_at"] = now()
        # 진단 원본은 로컬 runs에만 저장한다. 키 문자열이 섞이면 저장 전에 막는다.
        raw = json.dumps(state, ensure_ascii=False)
        if any(secret in raw for secret in self.secrets):
            raise RuntimeError("민감정보 검사가 출력을 차단했습니다.")
        response = {
            k: state[k]
            for k in (
                "run_id",
                "query",
                "as_of",
                "status",
                "stages",
                "ui_cases",
                "versions",
                "started_at",
                "finished_at",
                "calls",
                "a_model_mode",
            )
        }
        response["message"] = state.get("message", "실제 실행 결과입니다.")
        response["error_code"] = state.get("error_code")
        for key in ("confirmation", "parent_run_id", "user_confirmation", "resume_kind"):
            if key in state:
                response[key] = copy.deepcopy(state[key])
        if self.runs_dir:
            folder = self.runs_dir / state["run_id"]
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "result.json").write_text(raw, encoding="utf-8")
            (folder / "response.json").write_text(
                json.dumps(response, ensure_ascii=False), encoding="utf-8"
            )
        emit({"type": "result", "result": response})
        return state
