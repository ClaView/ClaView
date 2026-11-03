"""A rejected typed period keeps the pending answer and displays its actual cause."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_pipeline.screen import ScreenPipeline


@pytest.mark.parametrize("cause,message", [
    ("period_condition_not_representable", "선택한 주기와 맞지 않습니다"),
    ("malformed_condition", "유효한 연도·월·분기"),
    ("stated_period_boundary_changed", "질문에 명시한 시작·끝"),
    ("period_partially_read", "기간을 해석하지 못했습니다"),
    (None, "1~200자"),
])
def test_period_failure_cause_is_not_reported_as_a_length_error(monkeypatch, cause, message):
    paused = {"confirmation": {"kind": "a_view"}, "status": "needs_confirmation", "a_checkpoint": {"id": "kept"}}
    before = copy.deepcopy(paused)

    def reject(*args, **kwargs):
        raise a_screen.AnswerRejected("text_invalid", cause)

    monkeypatch.setattr(a_screen, "answer", reject)
    with pytest.raises(ValueError, match=message):
        ScreenPipeline.validate_resume(SimpleNamespace(run_store=None), paused,
                                       {"session_id": "session", "answers": []})
    assert paused == before


def test_quarter_boundary_rejection_can_be_corrected_in_the_same_session(base):
    query = "M2 통화량을 분기별로 보여줘"
    script = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
              "frequencies": [{"quote": "분기별", "for": ["s1"]}]}
    judge = ScriptJudge(by_question({query: script}))
    runner = ScreenPipeline(model=None, client=None, runs_dir=None, session=base,
                            judges_factory=lambda: a_screen.Judges(judge, judge, "script"))
    waiting = runner.run(query, as_of="2026-10-29")
    assert waiting["status"] == "needs_confirmation"
    before = copy.deepcopy(waiting)
    card = waiting["confirmation"]
    question, = card["groups"]
    assert question["kind"] == "period"

    def reply(text):
        return {"session_id": card["session_id"], "answers": [{
            "question_id": question["requirement_id"], "question_version": question["question_version"], "text": text,
        }]}

    with pytest.raises(ValueError, match="선택한 주기와 맞지 않습니다"):
        runner.resume(waiting, reply("2024년 2월부터 5월까지"))
    assert waiting == before and judge.calls == 1

    corrected = runner.resume(waiting, reply("2024년 1월부터 6월까지"))
    assert not corrected["a_view"]["questions"]
    statistic, = corrected["a_view"]["statistics"]
    table, = statistic["tables"]
    assert table["period"] == [{"start": "2024-01", "end": "2024-06"}]
    assert table["frequency"] == "Q" and table["period_source"] == "user_answer"
    assert judge.calls == 1
    assert corrected["calls"]["HCX"] == corrected["calls"]["KOSIS"] == 0
