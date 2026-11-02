"""Plan 54: the pipeline HTTP API over the new A's screen runner (scripted A judges, synthetic B; loopback only)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from claview_a_api import screen as a_screen
from claview_a_api import service as a_legacy_service
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_pipeline import api
from claview_pipeline.screen import ScreenPipeline

# plan 56 order 5 (policy ④, user 2026-10-31 17:53 item 1): the HTTP flow is checked here, not the frequency: the
# question and the made-up answer quote 연간 with the period (before, the longest frequency was assumed)
TWO = "M2 통화량과 예금은행 대출금리 2024년 연간"
SCRIPT = {TWO: {
    "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"},
                   {"id": "s2", "quote": "예금은행 대출금리", "name": "r"}],
    "periods": [{"quote": "2024년", "for": ["s1", "s2"]}], "frequencies": [{"quote": "연간", "for": ["s1", "s2"]}],
    "asks": [{"for": "s1", "axis": "stock", "options": [{"value": "평잔", "table": "DT_161Y013"},
                                                        {"value": "말잔", "table": "DT_161Y014"}]},
             {"for": "s2", "axis": "loan_basis", "options": [{"value": "신규취급액", "table": "DT_121Y006"},
                                                             {"value": "잔액기준", "table": "DT_121Y015"}]}]}}


@pytest.fixture()
def client(base, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the old A service was called on the screen path")

    monkeypatch.setattr(a_legacy_service, "select", refuse)
    monkeypatch.setattr(a_legacy_service, "answer", refuse)
    monkeypatch.setattr(api, "STATE", api._State())

    def judges():
        j = ScriptJudge(by_question(SCRIPT))
        return a_screen.Judges(j, j, "script")

    api.STATE._runner = ScreenPipeline(model=SyntheticSelectionModel(base.store), client=SyntheticFetchClient(),
                                       runs_dir=None, session=base, judges_factory=judges)
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
    return client.post(path, json=body, headers={"X-Claview-Token": token})


def reply(card, picks):
    return {"session_id": card["session_id"],
            "answers": [{"question_id": g["requirement_id"], "question_version": g["question_version"],
                         "option_id": g["options"][picks[g["requirement_id"]]]["option_id"]}
                        for g in card["groups"] if g["requirement_id"] in picks]}


def test_query_answer_resume_over_http(client):
    info = client.get("/api/info").json()
    assert info["a_mode"] == "new_a" and info["confirmation_resume"] is True
    token = info["token"]
    started = post(client, "/api/query", {"query": TWO}, token)
    job = wait(client, token, started.json()["job_id"])
    result = job["result"]
    assert result["status"] == "needs_confirmation" and result["confirmation"]["kind"] == "a_view"
    assert "a_checkpoint" not in result and "a_view" not in result  # the checkpoint stays on the server
    first_job = started.json()["job_id"]
    card = result["confirmation"]
    forged = reply(card, {"q:s1:stock": 0})
    forged["answers"][0]["option_id"] = "forged"
    assert post(client, "/api/resume", {"job_id": first_job, "confirmation": forged}, token).status_code == 400
    good = reply(card, {"q:s1:stock": 1})
    resumed = post(client, "/api/resume", {"job_id": first_job, "confirmation": good}, token)
    assert resumed.status_code == 202
    # the same checkpoint cannot be resumed twice (no duplicate run)
    assert post(client, "/api/resume", {"job_id": first_job, "confirmation": good}, token).status_code == 409
    second = wait(client, token, resumed.json()["job_id"])["result"]
    assert second["status"] == "needs_confirmation" and second["resume_kind"] == "a_answer"
    card2 = second["confirmation"]
    assert [g["requirement_id"] for g in card2["groups"]] == ["q:s2:loan_basis"]
    assert [s["statistic"] for s in card2["settled"]] == ["s1"]
    stale = {**good, "session_id": card2["session_id"]}  # the first answer again, on the new checkpoint
    assert post(client, "/api/resume", {"job_id": resumed.json()["job_id"], "confirmation": stale},
                token).status_code == 400
    last = post(client, "/api/resume", {"job_id": resumed.json()["job_id"],
                                        "confirmation": reply(card2, {"q:s2:loan_basis": 1})}, token)
    done = wait(client, token, last.json()["job_id"])["result"]
    assert done["status"] == "ok" and done["ui_cases"]
    assert done["calls"]["A_judge"] == 0
