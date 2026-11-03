"""A-only results must not claim retrieval or fail because B/C were not requested."""

import json

import pytest

from test_c_flow import AS_OF, PROCEED_Q, WAIT_Q


def test_a_only_explains_scope_in_event_and_saved_result(make_pipeline, tmp_path):
    pipeline = make_pipeline()
    pipeline.runs_dir = tmp_path
    events = []
    state = pipeline.run(PROCEED_Q, as_of=AS_OF, through="A", emit=events.append)
    assert state["status"] == "a_verified"
    assert state["calls"] == {"HCX": 0, "KOSIS": 0}
    assert state["message"] == "A 조건 확정 · 조회 미실행"
    skipped = [stage for stage in state["stages"] if stage["status"] == "not_run"]
    assert skipped
    assert all(stage["summary"] == "A 조건 확인까지만 실행하여 이후 조회·표현 단계는 실행하지 않았습니다."
               for stage in skipped)
    assert events[-1]["result"]["message"] == state["message"]
    record = json.loads((tmp_path / state["run_id"] / "result.json").read_text(encoding="utf-8"))
    assert record["message"] == state["message"]


def test_a_only_does_not_hide_required_confirmation(make_pipeline):
    state = make_pipeline().run(WAIT_Q, as_of=AS_OF, through="A")
    assert state["status"] == "needs_confirmation"
    assert state["confirmation"]
    assert state["message"] != "A 조건 확정 · 조회 미실행"


def test_b_only_retains_its_existing_status_and_default_message(make_pipeline):
    events = []
    state = make_pipeline().run(PROCEED_Q, as_of=AS_OF, through="B", emit=events.append)
    assert state["status"] == "b_verified"
    assert events[-1]["result"]["message"] == "실제 실행 결과입니다."


@pytest.mark.parametrize("reason", ["explicit_condition_missing", "explicit_condition_unreadable"])
def test_explicit_condition_failure_is_not_a_missing_table_or_user_question(base, tmp_path, monkeypatch, reason):
    pipeline, view = _condition_failure_pipeline(base, monkeypatch, reason)
    pipeline.runs_dir = tmp_path
    events = []
    state = pipeline.run(PROCEED_Q, as_of=AS_OF, through="A", emit=events.append)
    assert state["status"] == "unresolved"
    assert state["error_code"] == "a_explicit_condition_unverified"
    assert state["message"] == "질문에 명시된 조건을 확인하지 못해 전달하지 않았습니다. 아래 검사 사유를 확인해 주세요."
    assert "더 구체적으로" not in state["message"]
    assert not state.get("confirmation")
    table = next(stage for stage in state["stages"] if stage["id"] == "table")
    assert table["status"] == "failed"
    assert ("누락" if reason.endswith("missing") else "해석하지 못해") in table["note"]
    assert view["statistics"][0]["tables"][0]["table_id"] in table["list"][0]
    assert all(value == 0 for value in state["calls"].values())
    assert events[-1]["result"]["error_code"] == state["error_code"]
    record = json.loads((tmp_path / state["run_id"] / "result.json").read_text(encoding="utf-8"))
    assert record["message"] == state["message"]
    assert record["stages"] == state["stages"]


@pytest.mark.parametrize("blocked,expected_status,expected_code", [
    ("not_in_pool", "unresolved", "a_no_table"),
    ("period_answer_contract_pending", "unsupported", "a_handoff_unsupported"),
])
def test_other_handoff_failures_keep_their_existing_message(base, monkeypatch, blocked, expected_status, expected_code):
    reason = "explicit_condition_missing" if expected_status == "unsupported" else "not_in_pool"
    pipeline, view = _condition_failure_pipeline(base, monkeypatch, reason)
    view["handoff"]["blocked"][0]["code"] = blocked
    if blocked == "not_in_pool":
        view["statistics"][0]["tables"] = []
    state = pipeline.run(PROCEED_Q, as_of=AS_OF, through="A")
    assert state["status"] == expected_status
    assert state["error_code"] == expected_code


def _condition_failure_pipeline(base, monkeypatch, reason):
    from claview_pipeline.screen import ScreenPipeline

    pipeline = ScreenPipeline(model=None, client=None, runs_dir=None, session=base)
    view = {
        "statistics": [{"statistic": "s1", "quote": "M2 원계열", "state": "failed",
                        "tables": [{"table_id": "DT_161Y014", "table_name": "M2"}],
                        "reasons": [{"code": reason, "detail": None}], "warnings": []}],
        "session": {"run": {"model": {"model": "test"}}, "question": PROCEED_Q, "as_of": AS_OF},
        "failure": None, "questions": [],
        "handoff": {"ready": [], "blocked": [{"statistics": ["s1"], "code": "a_failed", "detail": [reason]}]},
    }

    def report(state, query, resume, update):
        return pipeline._report_view(state, view, update, False, "script")

    monkeypatch.setattr(pipeline, "_run_a", report)
    return pipeline, view
