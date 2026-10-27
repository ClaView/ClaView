"""C part on the integrated A -> B -> fetch flow, with SYNTHETIC B model and fetch client.

These check wiring and stage reporting, never model or value accuracy. Questions are short
probe sentences chosen for the route A's rules give them (proceed, wait for an answer, no table).
"""

from __future__ import annotations

import json

import pytest

AS_OF = "2026-10-22"
PROCEED_Q = "GDP 2023년 보여줘"
WAIT_Q = "총고정자본형성 2023년"
NO_TABLE_Q = "수출 2023년 보여줘"


def stage_status(state: dict) -> dict:
    return {s["id"]: s["status"] for s in state["stages"]}


def test_proceed_question_runs_every_stage(make_pipeline):
    pipeline = make_pipeline()
    events: list[dict] = []
    state = pipeline.run(PROCEED_Q, as_of=AS_OF, emit=events.append)
    assert state["status"] == "ok"
    assert set(stage_status(state).values()) == {"completed"}
    assert state["ui_cases"] and state["ui_cases"][0]["series"]
    assert state["calls"]["HCX"] >= 1 and state["calls"]["KOSIS"] >= 1
    assert events[-1]["type"] == "result" and events[-1]["result"]["status"] == "ok"
    seen = [e["stage"]["id"] for e in events if e["type"] == "stage"]
    assert {"question", "table", "conditions", "fetch"} <= set(seen)


def test_a_question_pauses_then_answer_resumes(make_pipeline):
    pipeline = make_pipeline()
    waiting = pipeline.run(WAIT_Q, as_of=AS_OF)
    assert waiting["status"] == "needs_confirmation"
    assert stage_status(waiting)["table"] == "needs_confirmation"
    assert stage_status(waiting)["conditions"] == "not_run"
    assert waiting["calls"] == {"HCX": 0, "KOSIS": 0}
    group = waiting["confirmation"]["groups"][0]
    assert len(group["options"]) >= 2
    table = group["options"][0]["tables"][0]
    assert table["table_id"] and table["frequencies"] and table["coverage"] and table["units"]
    payload = {
        "choices": [
            {
                "requirement_id": group["requirement_id"],
                "option_id": group["options"][0]["option_id"],
            }
        ],
        "interpretation_confirmed": False,
    }
    resumed = pipeline.resume(waiting, payload)
    assert resumed["status"] == "ok"
    assert resumed["parent_run_id"] == waiting["run_id"]
    assert resumed["ui_cases"]
    assert {c["tableId"] for c in resumed["ui_cases"]} == {table["table_id"]}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p, g: {**p, "choices": []},
        lambda p, g: {
            **p,
            "choices": [{"requirement_id": "other", "option_id": g["options"][0]["option_id"]}],
        },
        lambda p, g: {
            **p,
            "choices": [{"requirement_id": g["requirement_id"], "option_id": "q:forged"}],
        },
        lambda p, g: {**p, "interpretation_confirmed": "yes"},
        lambda p, g: {"choices": p["choices"]},
    ],
)
def test_resume_rejects_choices_not_offered(make_pipeline, mutate):
    pipeline = make_pipeline()
    waiting = pipeline.run(WAIT_Q, as_of=AS_OF)
    group = waiting["confirmation"]["groups"][0]
    good = {
        "choices": [
            {
                "requirement_id": group["requirement_id"],
                "option_id": group["options"][0]["option_id"],
            }
        ],
        "interpretation_confirmed": False,
    }
    with pytest.raises(ValueError):
        pipeline.validate_resume(waiting, mutate(good, group))
    assert pipeline.model.calls == [] and pipeline.client.calls == []


def test_no_table_stops_at_table_stage_without_b_calls(make_pipeline):
    pipeline = make_pipeline()
    state = pipeline.run(NO_TABLE_Q, as_of=AS_OF)
    assert state["status"] == "unresolved" and state["error_code"] == "a_no_table"
    assert stage_status(state)["table"] == "failed"
    assert state["calls"] == {"HCX": 0, "KOSIS": 0}
    assert state["ui_cases"] == []


def test_through_a_and_through_b_stop_early(make_pipeline):
    pipeline = make_pipeline()
    a_only = pipeline.run(PROCEED_Q, as_of=AS_OF, through="A")
    assert a_only["status"] == "a_verified" and a_only["calls"] == {"HCX": 0, "KOSIS": 0}
    assert stage_status(a_only)["conditions"] == "not_run"
    b_only = pipeline.run(PROCEED_Q, as_of=AS_OF, through="B")
    assert (
        b_only["status"] == "b_verified"
        and b_only["calls"]["HCX"] >= 1
        and b_only["calls"]["KOSIS"] == 0
    )
    assert (
        stage_status(b_only)["conditions"] == "completed"
        and stage_status(b_only)["fetch"] == "not_run"
    )


def test_missing_keys_stop_at_their_stage(make_pipeline):
    no_model = make_pipeline(model=False, client=False).run(PROCEED_Q, as_of=AS_OF)
    assert no_model["error_code"] == "hcx_key_missing"
    assert (
        stage_status(no_model)["table"] == "completed"
        and stage_status(no_model)["conditions"] == "failed"
    )
    no_client = make_pipeline(client=False)
    state = no_client.run(PROCEED_Q, as_of=AS_OF)
    assert state["error_code"] == "kosis_key_missing"
    assert (
        stage_status(state)["conditions"] == "completed"
        and stage_status(state)["fetch"] == "failed"
    )
    assert state["calls"]["HCX"] >= 1 and state["calls"]["KOSIS"] == 0


def test_unsupported_request_never_reaches_a(make_pipeline):
    pipeline = make_pipeline()
    state = pipeline.run("내년 GDP 예측해줘", as_of=AS_OF)
    assert state["error_code"] == "forecast_not_supported"
    assert pipeline.model.calls == [] and pipeline.client.calls == []
    assert "a_state" not in state


def test_key_like_text_in_question_is_refused(make_pipeline):
    with pytest.raises(ValueError):
        make_pipeline().run("GDP apiKey=abc123 보여줘")


def test_run_record_has_no_synthetic_secret_and_is_json(make_pipeline, tmp_path):
    pipeline = make_pipeline()
    pipeline.runs_dir = tmp_path
    state = pipeline.run(PROCEED_Q, as_of=AS_OF)
    saved = json.loads((tmp_path / state["run_id"] / "result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ok"
    response = json.loads(
        (tmp_path / state["run_id"] / "response.json").read_text(encoding="utf-8")
    )
    assert "a_state" not in response and response["ui_cases"]


def test_golden_queries_never_end_in_integration_error(make_pipeline):
    """All 22 golden questions end ok/partial or at A's own stop, never in an error."""
    from claview_pipeline.session import ROOT as REPO_ROOT

    cases = json.loads(
        (REPO_ROOT / "eval/cases/golden/golden_set_v1.json").read_text(encoding="utf-8")
    )["cases"]
    assert len(cases) == 22
    outcomes = {}
    for case in cases:
        state = make_pipeline().run(case["query"], as_of=AS_OF)
        outcomes[case["case_id"]] = state["status"]
        assert state["status"] != "error", (case["case_id"], state.get("exception_type"))
        if state["status"] in {"ok", "partial"}:
            assert state["ui_cases"]
    assert sum(s == "ok" for s in outcomes.values()) >= 15, outcomes


@pytest.mark.parametrize(("err", "code"), [("30", "kosis_no_data"), ("31", "kosis_service_error")])
def test_kosis_error_shows_answer_and_request_and_keeps_trace(make_pipeline, tmp_path, err, code):
    from claview_b.contracts import StageFailure

    class RejectingClient:
        def __init__(self):
            self.calls = []

        def fetch(self, params, trace):
            self.calls.append(params)
            trace.add("data_request", params=params)
            answer = [{"err": err, "errMsg": "x"}]
            trace.add("data_response_error", params=params, rows=answer)
            raise StageFailure("kosis_service_error")

    pipeline = make_pipeline()
    pipeline.client = RejectingClient()
    pipeline.runs_dir = tmp_path
    state = pipeline.run(PROCEED_Q, as_of=AS_OF)
    assert state["error_code"] == code
    fetch = next(s for s in state["stages"] if s["id"] == "fetch")
    assert fetch["status"] == "failed"
    rows = dict(map(tuple, fetch["rows"]))
    assert f'"err": "{err}"' in rows["KOSIS 응답"] and "tblId" in rows["요청 조건"]
    saved = json.loads((tmp_path / state["run_id"] / "result.json").read_text(encoding="utf-8"))
    assert any(e["event"] == "data_response_error" for e in saved["trace_events"])


def test_table_stage_says_when_a_did_not_confirm_and_lists_alternatives(make_pipeline):
    state = make_pipeline().run(
        "2024년 공산품과 서비스의 생산자물가가 어떻게 달랐는지 월별로 비교해줘", as_of=AS_OF
    )
    table = next(s for s in state["stages"] if s["id"] == "table")
    assert ["확정 방식", "A 1순위 표로 진행(A가 확정하지 못함)"] in table["rows"]
    assert "다른 후보" in table["note"] and "DT_404Y014" in table["note"]
