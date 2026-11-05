"""A-only results must not claim retrieval or fail because B/C were not requested."""

import json

from test_c_flow import AS_OF, PROCEED_Q, WAIT_Q


def test_a_only_explains_scope_in_event_and_saved_result(make_pipeline, tmp_path):
    pipeline = make_pipeline()
    pipeline.runs_dir = tmp_path
    events = []
    state = pipeline.run(PROCEED_Q, as_of=AS_OF, through="A", emit=events.append)
    assert state["status"] == "a_verified"
    assert state["calls"] == {"HCX": 0, "KOSIS": 0, "A_judge": 1}
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


def test_basis_warning_does_not_claim_that_retrieval_ran():
    from claview_pipeline.screen import warning_text

    text = warning_text({"statistic": "s1", "quote": "통화량", "code": "stated_basis_unverified",
                         "detail": {"axis": "basis", "stated": ["원계열"], "table": "T"}})
    assert "다른 검사는 통과했습니다" in text
    assert "조회했습니다" not in text
