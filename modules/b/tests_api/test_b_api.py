"""POST /b/resolve, /b/fetch, /b/run over the b_request fixtures (synthetic model and fetch)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claview_b_api import api
from claview_b_api.service import BService

FIXTURES = Path(__file__).resolve().parents[3] / "common" / "fixtures"
REQUESTS = sorted((FIXTURES / "b_request").glob("*.json"))


@pytest.fixture(scope="module")
def client():
    with TestClient(api.app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["service"] == "module-b"


@pytest.mark.parametrize("path", REQUESTS[:5], ids=lambda p: p.stem)
def test_resolve_then_fetch(client, path):
    body = json.loads(path.read_text(encoding="utf-8"))
    resolved = client.post("/b/resolve", json=body).json()
    assert resolved["outcome"]["stage"] == "B" and resolved["outcome"]["status"] == "ok"
    assert resolved["meta"]["llm_calls"] >= 1 and resolved["meta"]["mode"] == "synthetic"
    fetched = client.post("/b/fetch", json={"b_result": resolved["outcome"]["result"]}).json()
    assert fetched["outcome"]["status"] == "ok" and fetched["meta"]["external_calls"] >= 1
    expected = json.loads((FIXTURES / "data_result" / path.name).read_text(encoding="utf-8"))
    assert [s["series_id"] for s in fetched["outcome"]["result"]["series"]] == [
        s["series_id"] for s in expected["result"]["series"]
    ]


def test_run_is_resolve_plus_fetch(client):
    body = json.loads(REQUESTS[0].read_text(encoding="utf-8"))
    run = client.post("/b/run", json=body).json()
    assert run["outcome"]["stage"] == "fetch" and run["outcome"]["status"] == "ok"


def test_without_model_reports_failure_instead_of_inventing(client, monkeypatch):
    monkeypatch.setattr(api, "_service", BService(None, None, "live"))
    body = json.loads(REQUESTS[0].read_text(encoding="utf-8"))
    out = client.post("/b/resolve", json=body).json()["outcome"]
    assert out["status"] != "ok" and out["issues"][0]["code"] == "hcx_key_missing"
    fetched = client.post("/b/fetch", json={"b_result": {"selections": []}}).json()["outcome"]
    assert fetched["status"] == "not_run"


def test_invalid_request(client):
    assert client.post("/b/resolve", json={"query": "GDP", "tables": []}).status_code == 422
