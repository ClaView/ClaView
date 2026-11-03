"""The default live screen uses the durable A service; all providers here are synthetic."""

import json

import httpx

from claview_a_api import runtime
from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_pipeline.runtime import ServiceTransports
from claview_pipeline.screen import ScreenPipeline


def build(base, tmp_path, monkeypatch, query, script):
    made = []

    def judges(store):
        made.append(store.root)
        judge = ScriptJudge(by_question({query: script}))
        return a_screen.Judges(judge, judge, "script")

    monkeypatch.setenv("CLAVIEW_MODE", "live")
    monkeypatch.setenv("CLAVIEW_A_RUN_ROOT", str(tmp_path / "a"))
    monkeypatch.delenv("CLAVIEW_A_LIVE_APPROVAL", raising=False)
    monkeypatch.setattr(runtime, "service_live_judges", judges)
    runner = ScreenPipeline(model=SyntheticSelectionModel(base.store), client=SyntheticFetchClient(),
                            session=base, runs_dir=tmp_path / "pipeline")
    return runner, made


def test_default_live_path_uses_service_and_preserves_question_budget(base, tmp_path, monkeypatch):
    query = "M2 통화량 2024년 월별로 보여줘"
    script = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
              "periods": [{"quote": "2024년", "for": ["s1"]}],
              "frequencies": [{"quote": "월별", "for": ["s1"]}]}
    runner, made = build(base, tmp_path, monkeypatch, query, script)
    events = []
    first = runner.run(query, as_of="2026-10-29", emit=events.append)
    assert first["status"] == "ok" and first["ui_cases"]
    emitted = next(event["result"] for event in events if event["type"] == "result")
    saved = json.loads((runner.runs_dir / first["run_id"] / "response.json").read_text(encoding="utf-8"))
    raw = json.loads((runner.runs_dir / first["run_id"] / "result.json").read_text(encoding="utf-8"))
    assert first["claview_mode"] == emitted["claview_mode"] == saved["claview_mode"] == raw["claview_mode"] == "live"
    assert emitted == saved and saved["ui_cases"] == first["ui_cases"]
    assert made == [tmp_path / "a" / "live"]
    second = runner.run(query, as_of="2026-10-29")
    assert second["error_code"] == "question_budget_used"
    assert second["calls"]["HCX"] == second["calls"]["KOSIS"] == 0
    assert second["saved_records"][0]["run_id"] == first["run_id"]


def test_service_answer_uses_persisted_a_record_without_another_judgment(base, tmp_path, monkeypatch):
    query = "M2 통화량 월별로 보여줘"
    script = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
              "frequencies": [{"quote": "월별", "for": ["s1"]}]}
    runner, made = build(base, tmp_path, monkeypatch, query, script)
    first = runner.run(query, as_of="2026-10-29")
    assert first["status"] == "needs_confirmation"
    view = first["a_view"]
    question = next(q for q in view["questions"] if q["kind"] == "period")
    confirmation = {"session_id": view["session"]["session_id"], "answers": [{
        "question_id": question["question_id"], "question_version": question["question_version"], "text": "2024년"}]}
    second = runner.resume(first, confirmation)
    assert second["status"] == "ok" and second["calls"]["A_judge"] == 0
    assert len(made) == 1
    stored = runner.run_store.load(view["session"]["session_id"], a_screen.SCREEN_VERSION)
    assert stored["session"]["question_version"] == second["a_view"]["session"]["question_version"]


def test_actual_http_count_survives_finish_and_skips_auth_followups(base, tmp_path, monkeypatch):
    query = "M2 통화량과 예금은행 대출금리 2024년 연간"
    script = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"},
                              {"id": "s2", "quote": "예금은행 대출금리", "name": "금리"}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"},
                          {"for": ["s2"], "table": "DT_121Y006"}],
              "periods": [{"quote": "2024년", "for": ["s1", "s2"]}],
              "frequencies": [{"quote": "연간", "for": ["s1", "s2"]}]}
    runner, _ = build(base, tmp_path, monkeypatch, query, script)
    transports = ServiceTransports()
    wire = []

    def denied(request):
        wire.append(1)
        return httpx.Response(401, json={"error": {"code": "invalid_key"}})

    model, client = transports.clients("not-a-real-credential", None, model_transport=httpx.MockTransport(denied))
    runner.model, runner.client, runner.service_runtime = model, client, transports
    events = []
    try:
        state = runner.run(query, as_of="2026-10-29", emit=events.append)
    finally:
        model.close()
    assert wire == [1] and state["logical_calls"]["HCX"] == 2
    assert state["calls"]["HCX"] == 1 and state["calls"]["KOSIS"] == 0
    assert state["transport_halt"] == "b_model_http_401"
    response = next(event["result"] for event in events if event["type"] == "result")
    stored = json.loads((runner.runs_dir / state["run_id"] / "response.json").read_text(encoding="utf-8"))
    assert response["calls"] == stored["calls"] == state["calls"]
    assert response["claview_mode"] == stored["claview_mode"] == "live"
