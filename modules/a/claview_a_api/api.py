"""Module A HTTP API. Run: uvicorn claview_a_api.api:app --port 8888 (PYTHONPATH: common, modules/a)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Path

from claview_common.api import create_app
from claview_common.contracts.a_http import A_HTTP_VERSION, AHTTPAnswerRequest, AHTTPResult, AHTTPSelectRequest

from . import http, screen
from .run_record import RunStore

app = create_app(
    "module-a", "0.2.0", info=lambda: {"role": "question interpretation and table selection", "a_mode": "new_a",
                                      "a_http_contract": A_HTTP_VERSION}
)


@app.post("/a/select", response_model=AHTTPResult)
def select(body: AHTTPSelectRequest, store: Annotated[RunStore, Depends(http.get_store)],
           judges: Annotated[screen.Judges, Depends(http.get_judges)]) -> AHTTPResult:
    return http.select(body, store, judges)


@app.post("/a/answer", response_model=AHTTPResult)
def answer(body: AHTTPAnswerRequest, store: Annotated[RunStore, Depends(http.get_store)]) -> AHTTPResult:
    return http.answer(body, store)


@app.get("/a/records/{session_id}", response_model=AHTTPResult)
def load_record(session_id: Annotated[str, Path(pattern=r"^[0-9a-f]{8,64}$")],
                store: Annotated[RunStore, Depends(http.get_store)]) -> AHTTPResult:
    return http.load(session_id, store)
