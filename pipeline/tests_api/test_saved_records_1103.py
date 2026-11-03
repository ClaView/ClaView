"""Persistent screen snapshots are server-owned and resume only once across process state resets."""

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from claview_pipeline import api, saved_runs


def save(root, run_id, status="ok", *, mode="synthetic"):
    state = {"run_id": run_id, "query": "M2 2024년 월별", "as_of": "2026-11-03", "status": status,
             "claview_mode": mode, "finished_at": "2026-11-03T00:00:00Z", "ui_cases": [], "stages": [],
             "calls": {"A_judge": 0, "HCX": 0, "KOSIS": 0}}
    if status == "needs_confirmation":
        state["confirmation"] = {"kind": "a_view", "session_id": "abc"}
        state["a_checkpoint"] = {"server_only": True}
    folder = root / run_id
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps(state), encoding="utf-8")
    response = {key: value for key, value in state.items() if key != "a_checkpoint"}
    (folder / "response.json").write_text(json.dumps(response), encoding="utf-8")
    return state


@pytest.fixture()
def records(tmp_path, monkeypatch):
    calls = []

    def resume(state, confirmation, emit):
        calls.append(state["run_id"])
        result = save(tmp_path, "b" * 32)
        emit({"type": "result", "result": result})
        return result

    runner = SimpleNamespace(runs_dir=tmp_path, mode="synthetic", resume=resume,
                             validate_resume=lambda *args: None)
    monkeypatch.setattr(api, "STATE", api._State())
    api.STATE._runner = runner
    with TestClient(api.app) as client:
        yield client, {"X-Claview-Token": api.STATE.token}, tmp_path, calls, runner


def test_completed_load_returns_result_without_execution(records):
    client, headers, root, calls, _ = records
    save(root, "a" * 32)
    rows = client.get("/api/records", headers=headers).json()["records"]
    assert rows[0]["run_id"] == "a" * 32 and rows[0]["claview_mode"] == "synthetic"
    loaded = client.post("/api/records/load", headers=headers, json={"run_id": "a" * 32}).json()
    job = client.get("/api/jobs/" + loaded["job_id"], headers=headers).json()
    assert job["done"] and job["result"]["status"] == "ok" and calls == []
    assert "a_checkpoint" not in job["result"]


def test_restored_pending_record_can_resume_once(records, monkeypatch):
    client, headers, root, calls, runner = records
    save(root, "a" * 32, "needs_confirmation")
    one = client.post("/api/records/load", headers=headers, json={"run_id": "a" * 32}).json()["job_id"]
    two = client.post("/api/records/load", headers=headers, json={"run_id": "a" * 32}).json()["job_id"]
    out = client.post("/api/resume", headers=headers, json={"job_id": one, "confirmation": {}})
    assert out.status_code == 202
    for _ in range(100):
        if not api.STATE.busy.locked():
            break
        time.sleep(.01)
    assert calls == ["a" * 32]
    assert client.post("/api/resume", headers=headers,
                       json={"job_id": two, "confirmation": {}}).status_code == 409
    monkeypatch.setattr(api, "STATE", api._State())
    api.STATE._runner = runner
    headers = {"X-Claview-Token": api.STATE.token}
    assert client.post("/api/records/load", headers=headers, json={"run_id": "a" * 32}).status_code == 409
    assert client.post("/api/records/load", headers=headers, json={"run_id": "b" * 32}).status_code == 200
    assert calls == ["a" * 32]


@pytest.mark.parametrize("body", [{"run_id": "../anything"}, {"run_id": "a" * 32, "checkpoint": {}},
                                  {"path": "anything"}])
def test_client_cannot_supply_paths_or_checkpoints(records, body):
    client, headers, _, calls, _ = records
    assert client.post("/api/records/load", headers=headers, json=body).status_code in (400, 404)
    assert calls == [] and not api.STATE.busy.locked()


def test_records_require_token_and_current_mode(records):
    client, headers, root, calls, _ = records
    save(root, "a" * 32, mode="live")
    assert client.get("/api/records").status_code == 403
    assert client.post("/api/records/load", headers=headers, json={"run_id": "a" * 32}).status_code == 409
    assert calls == []


def test_saved_identity_must_match_filename(records):
    _, _, root, _, _ = records
    save(root, "a" * 32)
    path = root / ("a" * 32) / "response.json"
    body = json.loads(path.read_text())
    body["run_id"] = "b" * 32
    path.write_text(json.dumps(body))
    with pytest.raises(saved_runs.SavedRunError):
        saved_runs.read(root, "a" * 32)


@pytest.mark.parametrize("missing", ["openai", "kosis"])
def test_missing_live_key_blocks_query_and_resume_before_any_work(records, monkeypatch, missing):
    client, headers, _, calls, runner = records
    runner.deployed_service = True
    cfg = SimpleNamespace(live=True, mode="live", openai_key=None if missing == "openai" else object(),
                          kosis_key=None if missing == "kosis" else object())
    monkeypatch.setattr(api, "load", lambda: cfg)
    for endpoint, body in (("/api/query", {"query": "M2"}),
                           ("/api/resume", {"job_id": "a" * 32, "confirmation": {}})):
        response = client.post(endpoint, headers=headers, json=body)
        assert response.status_code == 503 and missing + "_key_missing" in response.json()["error"]
    assert calls == [] and api.STATE.jobs == {}
    # Reading an existing result is still possible when sending is unavailable.
    assert client.get("/api/records", headers=headers).status_code == 200


def test_load_actual_screen_snapshot_after_http_process_restart(base, tmp_path, monkeypatch):
    from claview_a_api import screen as a_screen
    from claview_a_api.run_record import RunStore
    from claview_a_api.synthetic_judges import ScriptJudge, by_question
    from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
    from claview_pipeline.screen import ScreenPipeline

    query = "M2 통화량 2024년 월별로 보여줘"
    script = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
              "periods": [{"quote": "2024년", "for": ["s1"]}],
              "frequencies": [{"quote": "월별", "for": ["s1"]}]}
    judge = ScriptJudge(by_question({query: script}))
    model, data = SyntheticSelectionModel(base.store), SyntheticFetchClient()
    runner = ScreenPipeline(model=model, client=data, session=base, runs_dir=tmp_path / "pipeline",
                            run_store=RunStore(tmp_path / "a"),
                            judges_factory=lambda: a_screen.Judges(judge, judge, "script"))
    state = runner.run(query, as_of="2026-10-29")
    assert state["status"] == "ok" and state["ui_cases"]
    counts = judge.calls, len(model.calls), len(data.calls)
    monkeypatch.setattr(api, "STATE", api._State())
    api.STATE._runner = runner
    with TestClient(api.app) as client:
        headers = {"X-Claview-Token": api.STATE.token}
        result = client.post("/api/records/load", headers=headers, json={"run_id": state["run_id"]})
        assert result.status_code == 200
        job = client.get("/api/jobs/" + result.json()["job_id"], headers=headers).json()
    assert job["done"] and job["result"]["ui_cases"] == state["ui_cases"]
    assert job["result"]["claview_mode"] == state["claview_mode"] == "synthetic"
    assert (judge.calls, len(model.calls), len(data.calls)) == counts
