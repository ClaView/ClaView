"""Legacy /a/legacy/select and /a/legacy/answer fixtures (rules only, no model, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claview_a_api.api import app

FIXTURES = Path(__file__).resolve().parents[3] / "common" / "fixtures"
CASES = ["CV-AB-01", "CV-AB-10", "CV-AB-12"]  # assume, no_result, proceed
WAIT_Q = "총고정자본형성 2023년"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def fixture(kind, name):
    return json.loads((FIXTURES / kind / f"{name}.json").read_text(encoding="utf-8"))


def test_health(client):
    assert client.get("/health").json()["service"] == "module-a"


@pytest.mark.parametrize("name", CASES)
def test_select_matches_fixture(client, name):
    response = client.post("/a/legacy/select", json=fixture("a_request", name))
    assert response.status_code == 200
    body = response.json()
    expected = fixture("a_result", name)
    assert body["decision"] == expected["decision"] and body["tables"] == expected["tables"]
    assert body["meta"]["latency_ms"] >= 0 and body["meta"]["llm_calls"] == 0


def test_waiting_question_then_answer(client):
    first = client.post("/a/legacy/select", json={"query": WAIT_Q, "as_of": "2026-10-22"}).json()
    assert first["decision"] == "awaiting_answer" and first["pending_question"]["options"]
    option = first["pending_question"]["options"][0]["option_id"]
    second = client.post("/a/legacy/answer", json={"state": first["state"], "option_id": option})
    assert second.status_code == 200 and second.json()["decision"] != "awaiting_answer"
    assert client.post("/a/legacy/answer", json={"state": {}, "option_id": option}).status_code == 400


def test_invalid_request(client):
    assert client.post("/a/legacy/select", json={"query": ""}).status_code == 422
