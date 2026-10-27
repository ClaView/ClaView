"""One way to build a service app: /health, /info, error handler, CORS, response timing."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import settings as settings_module
from ..contracts.versions import CONTRACT_VERSION

log = logging.getLogger("claview")


def _git_sha() -> str:
    sha = os.environ.get("GIT_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2, cwd=settings_module.ROOT
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def create_app(
    name: str, version: str, *, info: Callable[[], dict[str, Any]] | None = None, root_path: str = ""
) -> FastAPI:
    """A FastAPI app with the endpoints every ClaView service has.

    - GET /health: liveness for the compose healthcheck.
    - GET /info: service, version, git SHA, contract version, mode and key presence (no values).
    - X-Process-Time-Ms on every response; errors as JSON without stack traces.
    """
    app = FastAPI(title=f"ClaView {name}", version=version, root_path=root_path)
    started = datetime.now(UTC).isoformat()
    git_sha = _git_sha()

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"]
    )

    @app.middleware("http")
    async def _timing(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
        return response

    @app.exception_handler(Exception)
    async def _error(request: Request, exc: Exception):
        log.error("unhandled error on %s: %s", request.url.path, type(exc).__name__, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "internal_error", "type": type(exc).__name__})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": name}

    @app.get("/info")
    def service_info() -> dict:
        body = {
            "service": name,
            "version": version,
            "git_sha": git_sha,
            "started_at": started,
            "contract_version": CONTRACT_VERSION,
            **settings_module.load().public(),
        }
        if info:
            body.update(info())
        return body

    return app


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    """Run fn and return (result, elapsed milliseconds)."""
    start = time.perf_counter()
    result = fn()
    return result, round((time.perf_counter() - start) * 1000, 1)
