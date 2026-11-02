"""Server-owned HTTP execution of the new A; no B, C or client-provided checkpoints."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException

from claview_a.budget import LedgerError
from claview_common.api import timed
from claview_common.contracts import Meta
from claview_common.contracts.a_http import A_HTTP_VERSION, AHTTPAnswerRequest, AHTTPResult, AHTTPSelectRequest

from . import live_gate, screen
from .run_record import RunRecordError, RunStore

SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")
_CONFLICTS = {"stale_question", "duplicate_batch", "duplicate_reply", "question_not_pending"}
_LEDGER_REFUSALS = {
    "slot_used", "credential_halted", "allowance_missing", "allowance_expired", "allowance_version_mismatch",
    "allowance_slot_missing", "allowance_slot_used", "allowance_exists",
}


def current_day() -> str:
    """The Korean calendar day, fixed once at the start of a new question."""
    return datetime.now(SEOUL).date().isoformat()


def server_mode() -> str:
    """Read the mode alone; the HTTP handlers do not read credential settings."""
    mode = os.environ.get("CLAVIEW_MODE", "synthetic").strip().lower()
    if mode == "replay":
        raise HTTPException(status_code=503, detail="replay_source_required")
    if mode not in ("synthetic", "live"):
        raise HTTPException(status_code=503, detail="a_mode_invalid")
    return mode


@lru_cache(maxsize=1)
def get_store() -> RunStore:
    """The configured server location, fixed on first use; constructing it writes nothing."""
    root = os.environ.get("CLAVIEW_A_RUN_ROOT") or Path(__file__).resolve().parents[3] / "runs" / "a_http"
    return _guarded(lambda: RunStore(Path(root).resolve()))


def _http_error(exc: Exception) -> HTTPException:
    """Expose fixed codes only, never record contents, file paths or provider exceptions."""
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, screen.LiveDisabled):
        return HTTPException(status_code=503, detail="a_live_disabled")
    if isinstance(exc, RunRecordError):
        status = {"run_record_missing": 404, "run_record_busy": 409, "question_budget_used": 403}.get(exc.code, 410)
        return HTTPException(status_code=status, detail=exc.code)
    if isinstance(exc, screen.AnswerRejected):
        code = exc.code if exc.code in screen.ANSWER_ERRORS else "answer_invalid"
        return HTTPException(status_code=409 if code in _CONFLICTS else 400, detail=code)
    if isinstance(exc, live_gate.ApprovalRefused):
        return HTTPException(status_code=403, detail=exc.code)
    if isinstance(exc, LedgerError):
        if exc.code == "ledger_busy":
            return HTTPException(status_code=409, detail="ledger_busy")
        if exc.code in {"ledger_missing", "question_key_mismatch"}:
            return HTTPException(status_code=410, detail="a_record_incompatible")
        return HTTPException(status_code=403, detail=exc.code if exc.code in _LEDGER_REFUSALS else "a_budget_refused")
    if isinstance(exc, OSError):
        return HTTPException(status_code=503, detail="a_record_unavailable")
    return HTTPException(status_code=500, detail="a_execution_failed")


def _guarded[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except Exception as exc:
        raise _http_error(exc) from None


def get_judges(body: AHTTPSelectRequest, store: Annotated[RunStore, Depends(get_store)]) -> screen.Judges:
    """Resolve only the server's mode after the versioned body validates; there is no replay fallback."""
    del body  # Its validation must precede opening an approval; it supplies no execution controls.
    return _guarded(lambda: screen.live_judges(store) if server_mode() == "live" else screen.synthetic_judges())


def _record_mode(checkpoint: dict) -> str:
    kind = checkpoint.get("judges")
    if kind == "live":
        return "live"
    if kind in ("synthetic", "script"):
        return "synthetic"
    # A stored replay or unknown judge must not be relabelled as a synthetic or live execution.
    raise RunRecordError("run_record_version_mismatch")


def _read(store: RunStore, session_id: str) -> dict:
    try:
        out = screen.load(store, session_id)
        _record_mode(out["checkpoint"])
        return out
    except (KeyError, TypeError, ValueError):
        raise RunRecordError("run_record_invalid") from None


def _response(out: dict, elapsed: float, llm_calls: int = 0) -> AHTTPResult:
    mode = _record_mode(out["checkpoint"])
    return AHTTPResult(contract_version=A_HTTP_VERSION, view=out["view"], execution_mode=mode,
                       meta=Meta(latency_ms=elapsed, llm_calls=llm_calls, external_calls=0, mode=mode))


def _sent_for(judges: screen.Judges, query: str) -> int:
    """The gate's real sends for this question only; synthetic judgments do not count as model calls."""
    if judges.kind != "live" or judges.gate is None:
        return 0
    key = screen.key_of(query)
    return sum(row.get("n", 1) for row in judges.gate.usage.events()
               if row.get("kind") == "count" and row.get("what") == "a" and row.get("question_key") == key)


def select(body: AHTTPSelectRequest, store: RunStore, judges: screen.Judges) -> AHTTPResult:
    def execute():
        as_of = body.as_of or current_day()
        _record_mode({"judges": judges.kind})
        before = _sent_for(judges, body.query)
        out, elapsed = timed(lambda: screen.select(body.query, as_of, judges, store=store))
        # A refusal arising inside the graph is still a refusal, not a successful HTTP execution.
        failure = (out["view"].get("failure") or {}).get("code")
        if failure in live_gate.REFUSALS or failure in _LEDGER_REFUSALS:
            raise HTTPException(status_code=403, detail=failure)
        return _response(out, elapsed, max(0, _sent_for(judges, body.query) - before))

    return _guarded(execute)


def answer(body: AHTTPAnswerRequest, store: RunStore) -> AHTTPResult:
    def execute():
        checkpoint = _read(store, body.session_id)["checkpoint"]
        replies = [reply.model_dump(exclude_none=True) for reply in body.replies]
        out, elapsed = timed(lambda: screen.answer(checkpoint, body.session_id, replies, store=store))
        return _response(out, elapsed)

    return _guarded(execute)


def load(session_id: str, store: RunStore) -> AHTTPResult:
    def execute():
        out, elapsed = timed(lambda: _read(store, session_id))
        return _response(out, elapsed)

    return _guarded(execute)
