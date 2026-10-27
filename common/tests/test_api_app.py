"""The shared FastAPI app: /health, /info (no key values), timing header, JSON errors."""

from __future__ import annotations

from fastapi.testclient import TestClient

from claview_common.api import create_app


def test_health_info_timing_and_errors(monkeypatch):
    monkeypatch.setenv("KOSIS_API_KEY", "secret-value-for-test")
    monkeypatch.setenv("CLAVIEW_MODE", "synthetic")
    app = create_app("probe", "9.9.9", info=lambda: {"role": "probe"})

    @app.get("/boom")
    def boom():
        raise RuntimeError("detail that must not leak")

    client = TestClient(app, raise_server_exceptions=False)
    health = client.get("/health")
    assert health.json() == {"status": "ok", "service": "probe"} and "x-process-time-ms" in health.headers
    info = client.get("/info").json()
    assert info["service"] == "probe" and info["role"] == "probe" and info["kosis_key_set"] is True
    assert "secret-value-for-test" not in str(info)
    error = client.get("/boom")
    assert error.status_code == 500 and "must not leak" not in error.text
