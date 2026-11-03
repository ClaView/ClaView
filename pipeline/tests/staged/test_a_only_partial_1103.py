"""An A-only run preserves unresolved statistics without starting B or claiming full A completion."""

import json

from test_screen_54 import M2, SCRIPTS, TWO
from test_screen_period_condition_1103 import AS_OF, runner


def test_a_only_keeps_partial_outcome_in_event_and_record(base, tmp_path):
    pipeline = runner(base, TWO, SCRIPTS["partial"][TWO])
    pipeline.runs_dir = tmp_path
    events = []
    state = pipeline.run(TWO, as_of=AS_OF, through="A", emit=events.append)
    assert state["a_view"]["status"] == "partial"
    assert state["status"] == "a_partial"
    assert state["message"] == "A 일부 조건 미확정 · 조회 미실행"
    assert state["a_blocked"] == [{"statistics": ["s2"], "code": "not_in_pool", "detail": None}]
    assert next(s for s in state["stages"] if s["id"] == "table")["status"] == "warning"
    assert state["calls"]["HCX"] == state["calls"]["KOSIS"] == 0
    assert not state["ui_cases"]
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["status"] == "a_partial"
    for name in ("result.json", "response.json"):
        record = json.loads((tmp_path / state["run_id"] / name).read_text(encoding="utf-8"))
        assert record["status"] == "a_partial" and record["finished_at"]
        assert record["message"] == state["message"]
        for stage in record["stages"]:
            if stage["id"] in ("conditions", "fetch", "chart"):
                assert stage["status"] == "not_run"
                assert "A 조건 확인까지만" in stage["summary"]


def test_fully_confirmed_a_still_has_verified_status(base):
    state = runner(base, M2, SCRIPTS["selected"][M2]).run(M2, as_of=AS_OF, through="A")
    assert state["status"] == "a_verified"
    assert state["message"] == "A 조건 확정 · 조회 미실행"
    assert not state["a_blocked"]


def test_partial_c_run_keeps_its_cards_and_original_status(base):
    state = runner(base, TWO, SCRIPTS["partial"][TWO]).run(TWO, as_of=AS_OF)
    assert state["status"] == "partial"
    assert state["ui_cases"]
    assert state["calls"]["HCX"] > 0 and state["calls"]["KOSIS"] > 0
    assert "일부 통계만 조회했습니다" in state["message"]
