"""Module B HTTP API. Run: uvicorn claview_b_api.api:app --port 8888 (PYTHONPATH: common, modules/b)."""

from __future__ import annotations

from claview_common.api import create_app, timed
from claview_common.contracts import BRequest, BResult, FetchRequest, FetchResult, Meta, Outcome

from .service import BService

app = create_app("module-b", "0.1.0", info=lambda: {"role": "series/period selection and KOSIS fetch"})
_service: BService | None = None


def service() -> BService:
    global _service
    if _service is None:
        _service = BService.from_env()
    return _service


def _resolve(body: BRequest) -> tuple[dict, int]:
    tables = [t.model_dump() for t in body.tables]
    return service().resolve(body.query, tables, body.as_of)


@app.post("/b/resolve", response_model=BResult)
def resolve(body: BRequest) -> BResult:
    (envelope, calls), ms = timed(lambda: _resolve(body))
    return BResult(outcome=Outcome(**envelope), meta=Meta(latency_ms=ms, llm_calls=calls, mode=service().mode))


@app.post("/b/fetch", response_model=FetchResult)
def fetch(body: FetchRequest) -> FetchResult:
    (envelope, calls), ms = timed(lambda: service().fetch(body.b_result))
    return FetchResult(outcome=Outcome(**envelope), meta=Meta(latency_ms=ms, external_calls=calls, mode=service().mode))


@app.post("/b/run", response_model=FetchResult)
def run(body: BRequest) -> FetchResult:
    """resolve + fetch in one call (module evaluation). A failed resolve is returned as the outcome."""

    def both():
        b, llm = _resolve(body)
        if b["status"] != "ok":
            return b, llm, 0
        f, ext = service().fetch(b["result"])
        return f, llm, ext

    (envelope, llm, ext), ms = timed(both)
    return FetchResult(
        outcome=Outcome(**envelope), meta=Meta(latency_ms=ms, llm_calls=llm, external_calls=ext, mode=service().mode)
    )
