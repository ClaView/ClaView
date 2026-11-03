"""Screen path with a B that applies A's structured period condition (SYNTHETIC model and fetch, wiring only).

What is pinned: a B that says `accepts_period_condition` gets A's prepared request (per table `period_condition`,
the user's answers inside it) and the answers no longer wait for a B contract; any other B keeps today's request;
a request whose answers could not be prepared is never sent without them; and the run stops on a B that does not
keep what the USER set (not on a difference in a field nobody answered), never fetching the changed range.
"""

from __future__ import annotations

import copy

import pytest
from test_screen_54 import RecordingB, stage_status  # noqa: F401  (RecordingB: the unsupported B)

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_pipeline.screen import ScreenPipeline
from claview_pipeline.synthetic import SyntheticFetchClient, SyntheticSelectionModel

AS_OF = "2026-10-29"
QUARTERLY = "M2 통화량을 분기별로 보여줘"
STATISTIC = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}]
SCRIPT = {
    "statistics": STATISTIC,
    "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
    "frequencies": [{"quote": "분기별", "for": ["s1"]}],
}
RELATIVE = "M2 통화량 2020년부터 월별로 보여줘"
RELATIVE_SCRIPT = {
    "statistics": STATISTIC,
    "periods": [{"quote": "2020년부터", "for": ["s1"]}],
    "frequencies": [{"quote": "월별", "for": ["s1"]}],
    "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
}


class ConditionB(RecordingB):
    """B's real service over the synthetic model, saying that it applies period conditions."""

    accepts_period_condition = True


class TamperedB(ConditionB):
    """A B whose reply to the condition is changed by `tamper(outcome)` (what a wrong B would return)."""

    @staticmethod
    def tamper(outcome):
        raise NotImplementedError

    def resolve(self, query, tables, as_of=None, **extra):
        outcome, calls = super().resolve(query, tables, as_of, **extra)
        return self.tamper(copy.deepcopy(outcome)), calls


def runner(base, query, script, b=ConditionB):
    judge = ScriptJudge(by_question({query: script}))
    b.calls = []
    return ScreenPipeline(
        model=SyntheticSelectionModel(base.store),
        client=SyntheticFetchClient(),
        runs_dir=None,
        session=base,
        judges_factory=lambda: a_screen.Judges(judge, judge, "script"),
        b_factory=b,
    )


def answered(p, text="2024년 1월부터 6월까지"):
    """Runs the quarterly question and answers A's period question with `text`."""
    waiting = p.run(QUARTERLY, as_of=AS_OF)
    assert waiting["status"] == "needs_confirmation"
    card = waiting["confirmation"]
    (question,) = card["groups"]
    return p.resume(
        waiting,
        {
            "session_id": card["session_id"],
            "answers": [
                {
                    "question_id": question["requirement_id"],
                    "question_version": question["question_version"],
                    "text": text,
                }
            ],
        },
    )


def b_result(state):
    return state["stage_outcomes"]["B"][0]["result"]


def test_a_b_that_applies_conditions_gets_the_prepared_request(base):
    p = runner(base, "M2 통화량 2023년 연간으로 보여줘", {**SCRIPT, "periods": [{"quote": "2023년", "for": ["s1"]}],
                                                      "frequencies": [{"quote": "연간", "for": ["s1"]}]})
    state = p.run("M2 통화량 2023년 연간으로 보여줘", as_of=AS_OF)
    assert state["status"] == "ok" and state["unsupported_links"] == []
    (call,) = ConditionB.calls
    (table,) = call["tables"]
    assert table["table_id"] == "DT_161Y014" and table["focus"] == "M2 통화량"
    assert table["period_condition"]["period_source"] == "quoted"
    assert table["period_condition"]["period"] == [{"start": "2023-01", "end": "2023-12"}]
    (row,) = b_result(state)["period_resolutions"]
    assert row["requested"] == row["fetched"] == [{"frequency": "Y", "start": "2023", "end": "2023"}]
    assert "B 전달 정보" in str(state["stages"]) and "표별 기간 조건" in str(state["stages"])
    # the cards read the table, never the condition
    assert state["ui_cases"] and "period_condition" not in str(state["ui_cases"])


def test_an_answered_period_reaches_b_and_is_checked_against_its_reply(base):
    state = answered(runner(base, QUARTERLY, SCRIPT))
    assert state["status"] == "ok" and state["unsupported_links"] == []
    (table,) = ConditionB.calls[0]["tables"]
    condition = table["period_condition"]
    assert condition["period_source"] == "user_answer" and condition["period"] == [{"start": "2024-01", "end": "2024-06"}]
    assert [(a["kind"], a["text"]) for a in condition["answers"]] == [("period", "2024년 1월부터 6월까지")]
    (row,) = b_result(state)["period_resolutions"]
    assert row["applied_sources"]["period"] == "a_user_answer" and row["frequency"] == "Q"
    assert row["requested"] == [{"frequency": "Q", "start": "202401", "end": "202402"}]
    assert state["calls"]["KOSIS"] >= 1  # fetched with the answered range


def test_a_b_without_the_capability_keeps_the_pending_hold_for_answers(base):
    state = answered(runner(base, QUARTERLY, SCRIPT, b=RecordingB))
    assert RecordingB.calls == [] and state["status"] == "unsupported"
    (link,) = state["unsupported_links"]
    assert link["code"] == "period_answer_contract_pending"


def test_answers_that_could_not_be_prepared_are_never_sent_without_them(base, monkeypatch):
    from claview_a_api import view

    monkeypatch.setattr(view, "prepare_b_request", lambda v, r: {"request": None, "withheld": "period_source_not_allowed"})
    state = answered(runner(base, QUARTERLY, SCRIPT))
    assert ConditionB.calls == [] and state["status"] == "unsupported"
    (link,) = state["unsupported_links"]
    assert link["code"] == "period_condition_not_prepared" and link["detail"]["issue"] == "period_source_not_allowed"
    assert link["detail"]["period_answers"][0]["text"] == "2024년 1월부터 6월까지"
    assert state["calls"]["KOSIS"] == 0


def test_without_answers_a_request_that_could_not_be_prepared_keeps_the_existing_request(base, monkeypatch):
    from claview_a_api import view

    monkeypatch.setattr(view, "prepare_b_request", lambda v, r: {"request": None, "withheld": "period_source_not_allowed"})
    p = runner(base, "M2 통화량 2023년 연간으로 보여줘", {**SCRIPT, "periods": [{"quote": "2023년", "for": ["s1"]}],
                                                      "frequencies": [{"quote": "연간", "for": ["s1"]}]})
    state = p.run("M2 통화량 2023년 연간으로 보여줘", as_of=AS_OF)
    assert state["status"] == "ok"
    assert all("period_condition" not in t for t in ConditionB.calls[0]["tables"])


def test_a_difference_in_a_field_nobody_answered_does_not_stop_the_run(base):
    p = runner(base, RELATIVE, RELATIVE_SCRIPT)
    state = p.run(RELATIVE, as_of="2026-10-22")
    assert state["status"] == "ok" and state["unsupported_links"] == [] and state["calls"]["KOSIS"] >= 1
    (row,) = b_result(state)["period_resolutions"]
    # A read "2020년부터" up to the as-of month, B up to what the table holds: B's range is the one fetched
    assert row["reference"]["differs"]["period"] is True and row["applied_sources"]["period"] == "b_computed"
    assert state["period_reference_differences"] == [{"table_id": "DT_161Y014", "differs": {"period": True, "frequency": False}}]
    assert [n["code"] for n in row["notices"] if n["visible"]] == ["table_lag"]


def test_the_same_difference_still_stops_a_b_without_the_capability(base):
    state = runner(base, RELATIVE, RELATIVE_SCRIPT, b=RecordingB).run(RELATIVE, as_of="2026-10-22")
    assert state["status"] == "unsupported" and state["error_code"] == "b_period_differs_from_a"
    assert state["calls"]["KOSIS"] == 0


@pytest.mark.parametrize(
    ("name", "change", "kind"),
    [
        ("period_not_applied", lambda r: r["applied_sources"].update(period="b_computed"), "answered_period_not_applied"),
        ("requested_changed", lambda r: r["reference"]["differs"].update(period=True), "answered_period_not_applied"),
        ("frequency_ignored", lambda r: r.update(frequency="M"), "selection_outside_resolution"),
        ("range_widened", lambda r: r.update(fetched=[{"frequency": "Q", "start": "202401", "end": "202401"}]),
         "selection_outside_resolution"),
        ("row_missing", None, "resolution_missing"),
    ],
)
def test_a_b_that_did_not_keep_the_users_answer_is_not_fetched(base, name, change, kind):
    class Wrong(TamperedB):
        @staticmethod
        def tamper(outcome):
            result = outcome["result"]
            if change is None:
                result["period_resolutions"] = []
            else:
                change(result["period_resolutions"][0])
            return outcome

    state = answered(runner(base, QUARTERLY, SCRIPT, b=Wrong))
    assert state["status"] == "unsupported" and state["error_code"] == "b_answered_period_not_applied", name
    assert state["calls"]["KOSIS"] == 0 and state["ui_cases"] == []
    (link,) = state["unsupported_links"]
    assert link["code"] == "b_answered_period_not_applied" and kind in link["detail"][0]["kinds"]
    assert link["detail"][0]["table_id"] == "DT_161Y014"


@pytest.mark.parametrize(
    "code",
    ["period_condition_not_representable", "period_condition_frequency_unsupported", "period_condition_out_of_coverage"],
)
def test_a_condition_b_cannot_keep_stops_with_its_reason_and_never_another_period(base, code):
    class Refusing(ConditionB):
        def resolve(self, query, tables, as_of=None, **extra):
            return {"stage": "B", "status": "unresolved", "result": None,
                    "issues": [{"code": code, "message": None, "retryable": False}], "trace_ref": None}, 0

    state = answered(runner(base, QUARTERLY, SCRIPT, b=Refusing))
    assert state["status"] in {"unresolved", "needs_confirmation", "error", "unsupported"}
    assert state["error_code"] == code and state["calls"]["KOSIS"] == 0
    assert "조회하지 않았습니다" in state["message"]


def test_resolutions_are_not_required_of_a_request_sent_without_conditions(base):
    # a request sent in the existing form is still checked the existing way (nothing to look up in period_resolutions)
    state = runner(base, "M2 통화량 2023년 연간으로 보여줘", {**SCRIPT, "periods": [{"quote": "2023년", "for": ["s1"]}],
                                                       "frequencies": [{"quote": "연간", "for": ["s1"]}]},
                   b=RecordingB).run("M2 통화량 2023년 연간으로 보여줘", as_of=AS_OF)
    assert state["status"] == "ok" and "period_resolutions" not in (b_result(state) or {})
