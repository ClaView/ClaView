"""New HTTP period conditions must never silently enter an unsupported B implementation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claview_b_api import api
from claview_b_api.service import BService
from claview_common.contracts import BRequest

FIXTURE = Path(__file__).resolve().parents[3] / "common/fixtures/b_period_request/both_answers.json"


def request():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("endpoint", ["/b/resolve", "/b/run"])
@pytest.mark.parametrize("capability", [None, False, 1, "true"])
def test_unsupported_receiver_refuses_before_resolve_or_fetch(monkeypatch, endpoint, capability):
    # a receiver that does not say it applies conditions (an older B or another implementation);
    # BService itself says so since B applies them (see test_b_service_accepts_period_conditions)
    class Receiver:
        mode = "synthetic"

        def resolve(self, *args, **kwargs):
            raise AssertionError("must not be reached")

        def fetch(self, *args, **kwargs):
            raise AssertionError("must not be reached")

    receiver = Receiver()
    if capability is not None:
        receiver.accepts_period_condition = capability

    reached = []

    def resolve(*args, **kwargs):
        reached.append("resolve")
        return {"stage": "B", "status": "ok", "result": {"selections": []}}, 1

    def fetch(*args, **kwargs):
        reached.append("fetch")
        return {"stage": "fetch", "status": "ok", "result": {"series": []}}, 1

    monkeypatch.setattr(receiver, "resolve", resolve)
    monkeypatch.setattr(receiver, "fetch", fetch)
    monkeypatch.setattr(api, "_service", receiver)
    with TestClient(api.app) as client:
        response = client.post(endpoint, json=request())
    assert response.status_code == 200
    body = response.json()
    assert reached == []
    assert body["outcome"]["stage"] == "B"
    assert body["outcome"]["status"] == "unsupported"
    assert body["outcome"]["result"] is None
    assert body["outcome"]["issues"][0]["code"] == "period_condition_not_supported"
    assert body["meta"]["llm_calls"] == body["meta"]["external_calls"] == 0


class SupportedReceiver:
    accepts_period_condition = True
    mode = "synthetic"

    def __init__(self):
        self.received = []
        self.fetches = []

    def resolve(self, query, tables, as_of):
        self.received.append(copy.deepcopy({"query": query, "tables": tables, "as_of": as_of}))
        return {"stage": "B", "status": "ok", "result": {"selections": []}}, 1

    def fetch(self, result):
        self.fetches.append(copy.deepcopy(result))
        return {"stage": "fetch", "status": "ok", "result": {"series": []}}, 1


@pytest.mark.parametrize("endpoint", ["/b/resolve", "/b/run"])
def test_explicit_support_preserves_conditions_at_the_service_boundary(monkeypatch, endpoint):
    receiver = SupportedReceiver()
    monkeypatch.setattr(api, "_service", receiver)
    payload = request()
    with TestClient(api.app) as client:
        response = client.post(endpoint, json=payload)
    assert response.status_code == 200 and response.json()["outcome"]["status"] == "ok"
    assert receiver.received == [BRequest.model_validate(payload).model_dump()]
    assert receiver.fetches == ([{"selections": []}] if endpoint == "/b/run" else [])


@pytest.mark.parametrize("endpoint", ["/b/resolve", "/b/run"])
def test_legacy_request_needs_no_new_capability(monkeypatch, endpoint):
    receiver = SupportedReceiver()
    receiver.accepts_period_condition = False
    monkeypatch.setattr(api, "_service", receiver)
    payload = request()
    for table in payload["tables"]:
        table.pop("period_condition")
    with TestClient(api.app) as client:
        response = client.post(endpoint, json=payload)
    assert response.status_code == 200 and response.json()["outcome"]["status"] == "ok"
    assert receiver.received == [BRequest.model_validate(payload).model_dump()]


def test_b_service_accepts_period_conditions():
    assert BService(None, None, "synthetic").accepts_period_condition is True
