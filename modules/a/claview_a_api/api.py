"""Module A HTTP API. Run: uvicorn claview_a_api.api:app --port 8888 (PYTHONPATH: common, modules/a)."""

from __future__ import annotations

from fastapi import HTTPException

from claview_common.api import create_app, timed
from claview_common.contracts import AAnswerRequest, ARequest, AResult, Meta
from claview_common.settings import load

from . import service

app = create_app(
    "module-a", "0.1.0", info=lambda: {"role": "question interpretation and table selection", "a_mode": "rules_only"}
)


@app.post("/a/select", response_model=AResult)
def select(body: ARequest) -> AResult:
    out, ms = timed(lambda: service.select(body.query, body.as_of))
    return AResult(**out, meta=Meta(latency_ms=ms, mode=load().mode))


@app.post("/a/answer", response_model=AResult)
def answer(body: AAnswerRequest) -> AResult:
    try:
        out, ms = timed(lambda: service.answer(body.state, body.option_id))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="answer_invalid") from exc
    return AResult(**out, meta=Meta(latency_ms=ms, mode=load().mode))
