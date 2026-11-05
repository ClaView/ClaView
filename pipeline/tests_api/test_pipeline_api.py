"""The pipeline HTTP API (claview_pipeline.api), driven in process with FastAPI's TestClient.

Same checks as the former local server tests (now research/c/legacy_web/tests/test_c_server.py).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from flow_support import WAIT_Q, answer_payload

from claview_pipeline import api


@pytest.fixture()
def client(make_pipeline, monkeypatch):
    monkeypatch.setattr(api, "STATE", api._State())
    api.STATE._runner = make_pipeline()
    with TestClient(api.app) as c:
        yield c


def wait(client, token, job_id):
    for _ in range(600):
        job = client.get(f"/api/jobs/{job_id}", headers={"X-Claview-Token": token}).json()
        if job["done"]:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def post(client, path, body, token):
    headers = {"X-Claview-Token": token} if token else {}
    return client.post(path, json=body, headers=headers)


def test_health_and_info(client):
    assert client.get("/health").json() == {"status": "ok", "service": "pipeline"}
    info = client.get("/info").json()
    assert info["service"] == "pipeline" and "kosis_key_set" in info and "token" not in info


def test_query_confirm_resume_over_http(client):
    info = client.get("/api/info").json()
    assert info["hcx_ready"] and info["kosis_ready"]
    assert info["model_ready"] and info["b_model"] == "synthetic"
    assert info["confirmation_resume"] is True and info["b_condition_resume"] is False
    token = info["token"]
    started = post(client, "/api/query", {"query": WAIT_Q}, token)
    assert started.status_code == 202
    job = wait(client, token, started.json()["job_id"])
    assert job["result"]["status"] == "needs_confirmation"
    good = answer_payload(job["result"], 1)
    bad = {**good, "session_id": "forged"}
    job_id = started.json()["job_id"]
    assert post(client, "/api/resume", {"job_id": job_id, "confirmation": bad}, token).status_code == 400
    resumed = post(client, "/api/resume", {"job_id": job_id, "confirmation": good}, token)
    assert resumed.status_code == 202
    done = wait(client, token, resumed.json()["job_id"])
    assert done["result"]["status"] == "ok" and done["result"]["ui_cases"]
    # The same checkpoint cannot be resumed twice (no duplicate calls).
    assert post(client, "/api/resume", {"job_id": job_id, "confirmation": good}, token).status_code == 409
    assert post(client, "/api/conditions", {"job_id": "x", "conditions": {}}, token).status_code == 404


def test_requests_without_token_or_with_key_text_are_refused(client):
    assert post(client, "/api/query", {"query": "GDP 2023년"}, None).status_code == 403
    token = client.get("/api/info").json()["token"]
    assert post(client, "/api/query", {"query": "GDP apiKey=abc"}, token).status_code == 400
    assert post(client, "/api/query", {"query": "GDP", "extra": 1}, token).status_code == 400
