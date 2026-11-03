"""Main pipeline HTTP API: the only API the frontend calls (through nginx, under /api).

Run: uvicorn claview_pipeline.api:app --port 8888 (PYTHONPATH: common, modules/a, modules/b, modules/c, pipeline)

Endpoints keep the shape of the former local C server (research/c/legacy_web/server.py) so the
existing screen works unchanged:

    GET  /api/info            readiness, a per-process token, mode (no key values)
    POST /api/query           {"query"} -> 202 {"job_id"}; the run continues in a thread
    POST /api/resume          {"job_id", "confirmation"} -> 202 {"job_id"} (answer to A's question;
                              the new A's card takes {"session_id", "answers": [...]})
    GET  /api/jobs/{job_id}   stage events and the result so far (header X-Claview-Token)

The flow runs in this process: claview_pipeline.screen (the new A through claview_a_api.screen) and
claview_pipeline.staged (B, fetch, C) call each module's service functions, the same code the module
APIs serve. The module-a/b/c containers are for each module's own tests and evaluation.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import threading
from uuid import uuid4

from fastapi import Header, Query, Request
from fastapi.responses import JSONResponse

from claview_common.api import create_app
from claview_common.settings import B_MODEL_ID, PATHS, load, reveal

from . import saved_runs
from .runtime import ServiceTransports
from .screen import ScreenPipeline
from .staged import Pipeline, validate_query

MAX_JOBS = 30
MAX_BODY = 16000

app = create_app("pipeline", "0.1.0", info=lambda: {"role": "A -> B -> fetch -> C flow"})


class _State:
    """One runner and the jobs of this process. One question at a time (the flow is not thread-safe)."""

    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.busy, self.lock = threading.Lock(), threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.checkpoints: dict[str, dict] = {}
        self._runner: Pipeline | None = None

    @property
    def runner(self) -> Pipeline:
        if self._runner is None:
            self._runner = _make_runner()
        return self._runner


def _make_runner() -> Pipeline:
    """The new A service and the actual B/fetch/C path, with no evaluation manifest required."""
    cfg = load()
    if cfg.live:
        runtime = ServiceTransports()
        openai, kosis = reveal(cfg.openai_key), reveal(cfg.kosis_key)
        model, client = runtime.clients(openai, kosis)
        runner = ScreenPipeline(model=model, client=client, service_runtime=runtime,
                                runs_dir=PATHS.runs / "pipeline" / "live")
        runner.secrets = tuple(key for key in (openai, kosis) if key)
        runner.deployed_service = True
        return runner
    from claview_b_api.service import synthetic_clients

    model, client = synthetic_clients()
    return ScreenPipeline(model=model, client=client, runs_dir=PATHS.runs / "pipeline" / "synthetic")


STATE = _State()


def _error(code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": message})


def _service_issue(runner, cfg):
    """No transport or credential preflight; stored configuration and halt flags only."""
    if not cfg.live or not getattr(runner, "deployed_service", False):
        return None
    if cfg.openai_key is None:
        return "openai_key_missing"
    if cfg.kosis_key is None:
        return "kosis_key_missing"
    if getattr(runner.service_runtime, "halted", None):
        return "service_credential_halted"
    if not os.environ.get("CLAVIEW_A_LIVE_APPROVAL"):
        from claview_a_api.runtime import ServiceUsage

        try:
            if ServiceUsage(runner.run_store.root).halted():
                return "credential_halted"
        except (OSError, ValueError, RuntimeError):
            return "a_record_unavailable"
    return None


def _ready_error():
    issue = _service_issue(STATE.runner, load())
    if issue:
        return _error(503, f"실행 설정을 확인해 주세요({issue}). 모델·자료 조회는 시작하지 않았습니다.")
    return None


async def _payload(request: Request) -> dict | None:
    raw = await request.body()
    if not 0 < len(raw) <= MAX_BODY or request.headers.get("content-type", "").split(";")[0] != "application/json":
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


@app.get("/api/info")
def api_info() -> dict:
    cfg = load()
    runner = STATE.runner
    issue = _service_issue(runner, cfg)
    return {
        "token": STATE.token,
        "mode": "actual_pipeline" if cfg.live else "synthetic_pipeline",
        "claview_mode": cfg.mode,
        "confirmation_resume": True,
        "b_condition_resume": False,
        "flow": "A->B->fetch->C (claview_pipeline)",
        "server_pid": os.getpid(),
        "busy": STATE.busy.locked(),
        "a_mode": runner.a_model_mode,
        "a_ready": not cfg.live or (cfg.openai_key is not None and issue not in {
            "credential_halted", "service_credential_halted", "a_record_unavailable"}),
        "a_unavailable_reason": issue if issue != "kosis_key_missing" else None,
        "ready": not cfg.live or (cfg.openai_key is not None and cfg.kosis_key is not None and issue is None),
        "unavailable_reason": issue,
        "hcx_ready": runner.model is not None,  # name kept for the frontend; it means "B model ready"
        "model_ready": runner.model is not None,
        "b_model": B_MODEL_ID if cfg.live else "synthetic",
        "kosis_ready": runner.client is not None,
    }


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, x_claview_token: str | None = Header(default=None)):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return _error(404, "not_found")
    if x_claview_token != STATE.token:
        return _error(403, "request_not_allowed")
    with STATE.lock:
        job = copy.deepcopy(STATE.jobs.get(job_id))
    return job if job else _error(404, "not_found")


@app.get("/api/records")
def api_records(limit: int = Query(default=30, ge=1, le=100), x_claview_token: str | None = Header(default=None)):
    if x_claview_token != STATE.token:
        return _error(403, "request_not_allowed")
    return {"records": saved_runs.listing(STATE.runner.runs_dir, limit=limit),
            "claview_mode": STATE.runner.mode}


@app.post("/api/records/load")
async def api_load_record(request: Request, x_claview_token: str | None = Header(default=None)):
    if x_claview_token != STATE.token:
        return _error(403, "request_not_allowed")
    body = await _payload(request)
    if not isinstance(body, dict) or set(body) != {"run_id"}:
        return _error(400, "record_invalid")
    if not STATE.busy.acquire(blocking=False):
        return _error(409, "앞선 실행이 끝난 뒤 기록을 불러와 주세요.")
    try:
        state, response = saved_runs.read(STATE.runner.runs_dir, body["run_id"])
        if saved_runs.claimed(STATE.runner.runs_dir, body["run_id"]):
            return _error(409, "이미 이어 실행한 기록입니다. 서버 기록에서 가장 최근 결과를 불러와 주세요.")
        if state.get("claview_mode") != STATE.runner.mode:
            return _error(409, "기록의 실행 모드가 현재 서버와 다릅니다.")
        job_id = uuid4().hex
        with STATE.lock:
            if len(STATE.jobs) >= MAX_JOBS:
                oldest = next((key for key, value in STATE.jobs.items() if value["done"]), None)
                if oldest:
                    STATE.jobs.pop(oldest)
                    STATE.checkpoints.pop(oldest, None)
            STATE.jobs[job_id] = {"job_id": job_id, "done": True, "events": [], "result": response,
                                  "loaded": True}
            if state.get("confirmation") and state.get("status") == "needs_confirmation":
                STATE.checkpoints[job_id] = state
        return {"job_id": job_id, "loaded": True}
    except saved_runs.SavedRunError:
        return _error(404, "저장된 실행 기록을 찾거나 확인하지 못했습니다.")
    finally:
        STATE.busy.release()


@app.post("/api/query", status_code=202)
async def api_query(request: Request, x_claview_token: str | None = Header(default=None)):
    if x_claview_token != STATE.token:
        return _error(403, "request_not_allowed")
    if (failure := _ready_error()) is not None:
        return failure
    payload = await _payload(request)
    try:
        if payload is None or set(payload) != {"query"}:
            raise ValueError()
        query = validate_query(payload["query"])
        if any(k in query for k in STATE.runner.secrets):
            raise ValueError()
    except (ValueError, TypeError):
        return _error(400, "요청 형식이 올바르지 않습니다. 인증키는 질문에 넣지 마세요.")
    return _start(lambda emit: STATE.runner.run(query, emit=emit))


@app.post("/api/resume", status_code=202)
async def api_resume(request: Request, x_claview_token: str | None = Header(default=None)):
    if x_claview_token != STATE.token:
        return _error(403, "request_not_allowed")
    if (failure := _ready_error()) is not None:
        return failure
    payload = await _payload(request)
    if payload is None or set(payload) != {"job_id", "confirmation"} or not isinstance(payload["job_id"], str):
        return _error(400, "요청 형식이 올바르지 않습니다.")
    source = payload["job_id"]
    with STATE.lock:
        original = STATE.jobs.get(source)
        if original is None:
            return _error(
                410, "서버가 재시작되었거나 보관 한도가 지났습니다. 서버 기록에서 이전 실행을 불러와 주세요."
            )
        if not original["done"] or original.get("resumed_job_id"):
            return _error(409, "이미 이어 실행했거나 아직 처리 중인 기록입니다. 중복 조회하지 않습니다.")
        paused = STATE.checkpoints.get(source)
    if paused is None:
        return _error(400, "후보 확인을 기다리는 실행이 아닙니다.")
    try:
        STATE.runner.validate_resume(paused, payload["confirmation"])
    except ValueError as exc:
        return _error(400, str(exc) or "선택 내용을 다시 확인해 주세요.")
    except (TypeError, KeyError):
        return _error(400, "선택 형식이 올바르지 않습니다.")
    return _start(lambda emit: STATE.runner.resume(paused, payload["confirmation"], emit=emit), source=source)


def _start(work, source: str | None = None):
    if not STATE.busy.acquire(blocking=False):
        return _error(409, "앞선 질문을 처리 중입니다. 중복 호출하지 않고 기다립니다.")
    job_id = uuid4().hex
    source_run_id = None
    if source:
        with STATE.lock:
            source_run_id = (STATE.checkpoints.get(source) or {}).get("run_id")
        if source_run_id and STATE.runner.runs_dir:
            try:
                saved_runs.claim(STATE.runner.runs_dir, source_run_id)
            except (saved_runs.SavedRunError, OSError):
                STATE.busy.release()
                return _error(409, "이미 이어 실행했거나 유효하지 않은 기록입니다. 최근 서버 기록을 불러와 주세요.")
    with STATE.lock:
        if len(STATE.jobs) >= MAX_JOBS:  # drop the oldest finished job, never a running one
            oldest = next((k for k in STATE.jobs if k != source and STATE.jobs[k]["done"]), None)
            if oldest:
                STATE.jobs.pop(oldest)
                STATE.checkpoints.pop(oldest, None)
        STATE.jobs[job_id] = {"job_id": job_id, "done": False, "events": [], "result": None}
        if source:
            STATE.jobs[source]["resumed_job_id"] = job_id

    def emit(event):
        with STATE.lock:
            STATE.jobs[job_id]["events"].append(event)
            if event["type"] == "result":
                STATE.jobs[job_id]["result"] = event["result"]

    def run():
        try:
            state = work(emit)
            if source_run_id and STATE.runner.runs_dir:
                saved_runs.complete(STATE.runner.runs_dir, source_run_id, state)
            if state.get("confirmation"):
                with STATE.lock:
                    STATE.checkpoints[job_id] = state
        except Exception:
            with STATE.lock:
                STATE.jobs[job_id]["error"] = "실행을 완료하지 못했습니다. 원인을 확인한 뒤 다시 시도하세요."
        finally:
            with STATE.lock:
                STATE.jobs[job_id]["done"] = True
            STATE.busy.release()

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id}
