"""Plan 54: the screen's runner (claview_pipeline.screen) over the new A, with scripted A judges and SYNTHETIC B.

Wiring and stage reporting only, never model or value accuracy. The old A's service must not be called.
"""

from __future__ import annotations

import pytest

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_pipeline.synthetic import SyntheticFetchClient, SyntheticSelectionModel

# plan 56 order 5 (policy ④, user 2026-10-31 17:53 item 1): these tests check the screen's wiring, not frequencies.
# Each made-up question and answer quotes the frequency the resolver's default used to give (연간) next to its period
# (``with_frequency``); before, no frequency was quoted and the longest one was assumed. The assertions are unchanged.
AS_OF = "2026-10-22"
M2 = "M2 통화량 2023년 연간으로 보여줘"
TWO = "M2 통화량과 예금은행 대출금리 2024년 연간"
S1 = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}]
S2 = S1 + [{"id": "s2", "quote": "예금은행 대출금리", "name": "rate"}]
STOCK = {"for": "s1", "axis": "stock", "options": [{"value": "평잔", "table": "DT_161Y013"},
                                                    {"value": "말잔", "table": "DT_161Y014"}]}
LOAN = {"for": "s2", "axis": "loan_basis", "options": [{"value": "신규취급액", "table": "DT_121Y006"},
                                                        {"value": "잔액기준", "table": "DT_121Y015"}]}
SCRIPTS = {
    "selected": {M2: {"statistics": S1, "periods": [{"quote": "2023년", "for": ["s1"]}],
                      "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}},
    "ask2": {TWO: {"statistics": S2, "periods": [{"quote": "2024년", "for": ["s1", "s2"]}], "asks": [STOCK, LOAN]}},
    "partial": {TWO: {"statistics": S2, "periods": [{"quote": "2024년", "for": ["s1", "s2"]}],
                      "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
                      "missing": [{"for": ["s2"], "search_words": ["대출금리"]}]}},
    "error": {M2: {"judge_error": "timeout"}},
}


def with_frequency(scripts: dict, word: str = "연간") -> dict:
    """Every made-up answer with a period also quotes ``word`` for the statistics of its periods."""
    for answers in scripts.values():
        for out in answers.values():
            if out.get("periods"):
                ids = sorted({s for r in out["periods"] for s in r["for"]})
                out.setdefault("frequencies", [{"quote": word, "for": ids}])
    return scripts


with_frequency(SCRIPTS)


def stage_status(state):
    return {s["id"]: s["status"] for s in state["stages"]}




class RecordingB:
    """B's service over the synthetic model, recording what each resolve call received."""

    accepts_period_answers = False
    calls: list = []

    def __init__(self, model, client, mode):
        from claview_b_api.service import BService

        self.inner = BService(model, client, mode)
        self.model, self.client = model, client

    def resolve(self, query, tables, as_of=None, **extra):
        type(self).calls.append({"query": query, "tables": tables, **extra})
        return self.inner.resolve(query, tables, as_of)

    def fetch(self, b_result):
        return self.inner.fetch(b_result)


class PeriodB(RecordingB):
    accepts_period_answers = True  # a synthetic B that takes period answers (the real B contract has no field)


@pytest.fixture()
def screen(base):
    from claview_pipeline.screen import ScreenPipeline

    def build(script=None, *, b=RecordingB, period_questions=None, model=True):
        b.calls = []
        made = []

        def judges():
            j = ScriptJudge(by_question(SCRIPTS[script])) if script else None
            got = a_screen.Judges(j, j, "script") if j else a_screen.synthetic_judges()
            made.append(got)
            return got

        p = ScreenPipeline(model=SyntheticSelectionModel(base.store) if model else None,
                           client=SyntheticFetchClient(), runs_dir=None, session=base, judges_factory=judges,
                           period_questions=period_questions, b_factory=b)
        p.judges_made = made
        return p

    return build


def test_api_runner_is_the_new_a(monkeypatch):
    from claview_pipeline import api
    from claview_pipeline.screen import ScreenPipeline

    assert isinstance(api._make_runner(), ScreenPipeline)


def test_selected_statistic_runs_every_stage_with_focus(screen):
    p = screen("selected")
    state = p.run(M2, as_of=AS_OF)
    assert state["status"] == "ok" and set(stage_status(state).values()) == {"completed"}
    assert state["a_model_mode"] == "new_a" and state["calls"]["A_judge"] == 1
    assert p.judges_made[0].requests == ["gpt-6.1-terra"]
    (call,) = RecordingB.calls
    assert call["query"] == M2
    assert call["tables"] == [{"org_id": call["tables"][0]["org_id"], "table_id": "DT_161Y014",
                               "table_name": call["tables"][0]["table_name"], "focus": "M2 통화량"}]
    prepared = state["a_view"]["handoff"]["ready"][0]["b_request"]
    assert prepared["tables"][0]["period_condition"]["period_source"] == "quoted"
    assert all("period_condition" not in table for table in call["tables"])
    assert {c["tableId"] for c in state["ui_cases"]} == {"DT_161Y014"}


def test_missing_live_key_never_falls_back_to_synthetic(screen, monkeypatch, tmp_path):
    import http.client

    from claview_a_api.run_record import RunStore
    from claview_pipeline.screen import ScreenPipeline

    p = screen()
    state = p.run(M2, as_of=AS_OF)
    assert p.judges_made[0].kind == "synthetic" and state["calls"]["A_judge"] >= 1
    def refuse(*args, **kwargs):
        raise AssertionError("no model transport or synthetic fallback is allowed without a live key")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CLAVIEW_A_LIVE_APPROVAL", raising=False)
    monkeypatch.setattr(http.client, "HTTPSConnection", refuse)
    monkeypatch.setattr(a_screen, "synthetic_judges", refuse)
    live = ScreenPipeline(model=None, client=None, runs_dir=None, session=p, run_store=RunStore(tmp_path / "service"))
    live.mode = "live"
    state = live.run(M2, as_of=AS_OF)
    # Deployment live is enabled now; missing credentials still send nothing and never substitute a fake A.
    assert state["error_code"] == "key_unavailable" and stage_status(state)["question"] == "failed"
    assert "a_view" not in state
    assert state["calls"]["A_judge"] == 0
    assert state["calls"]["HCX"] == state["calls"]["KOSIS"] == 0


def test_explicit_invalid_approval_never_falls_back_to_service_live(screen, monkeypatch, tmp_path):
    from claview_a_api import runtime
    from claview_a_api.run_record import RunStore

    def refuse(*args, **kwargs):
        raise AssertionError("an invalid explicit evaluation approval must not use deployment live")

    approval = tmp_path / "invalid_approval.json"
    approval.write_text("{", encoding="utf-8")
    monkeypatch.setenv("CLAVIEW_A_LIVE_APPROVAL", str(approval))
    monkeypatch.setattr(runtime, "service_live_judges", refuse)
    from claview_pipeline.screen import ScreenPipeline

    p = screen()
    live = ScreenPipeline(model=None, client=None, runs_dir=None, session=p, run_store=RunStore(tmp_path / "eval"))
    live.mode = "live"
    state = live.run(M2, as_of=AS_OF)
    assert state["error_code"] == "approval_invalid" and state["calls"]["A_judge"] == 0
    assert state["calls"]["HCX"] == state["calls"]["KOSIS"] == 0


def answers_for(state, picks):
    card = state["confirmation"]
    return {"session_id": card["session_id"],
            "answers": [{"question_id": g["requirement_id"], "question_version": g["question_version"],
                         "option_id": g["options"][picks[g["requirement_id"]]]["option_id"]}
                        for g in card["groups"] if g["requirement_id"] in picks]}


def test_two_questions_answered_one_at_a_time(screen):
    p = screen("ask2")
    waiting = p.run(TWO, as_of=AS_OF)
    assert waiting["status"] == "needs_confirmation" and waiting["error_code"] == "a_answer_required"
    assert stage_status(waiting)["table"] == "needs_confirmation" and stage_status(waiting)["conditions"] == "not_run"
    assert waiting["calls"]["HCX"] == 0 and RecordingB.calls == []
    card = waiting["confirmation"]
    assert card["kind"] == "a_view" and [g["requirement_id"] for g in card["groups"]] == ["q:s1:stock",
                                                                                          "q:s2:loan_basis"]
    assert card["groups"][0]["options"][1]["tables"][0]["table_id"] == "DT_161Y014"
    one = p.resume(waiting, answers_for(waiting, {"q:s1:stock": 1}))
    assert one["status"] == "needs_confirmation" and one["parent_run_id"] == waiting["run_id"]
    assert one["calls"]["A_judge"] == 0 and RecordingB.calls == []
    card = one["confirmation"]
    assert [g["requirement_id"] for g in card["groups"]] == ["q:s2:loan_basis"]
    assert [(s["statistic"], s["state"]) for s in card["settled"]] == [("s1", "selected")]
    assert one["a_checkpoint"]["session"]["question"] == TWO and one["as_of"] == AS_OF
    done = p.resume(one, answers_for(one, {"q:s2:loan_basis": 0}))
    assert done["status"] == "ok" and done["calls"]["A_judge"] == 0
    assert [c["tables"][0]["table_id"] for c in RecordingB.calls] == ["DT_161Y014", "DT_121Y006"]
    assert [c["tables"][0]["focus"] for c in RecordingB.calls] == ["M2 통화량", "예금은행 대출금리"]
    assert all(c["query"] == TWO for c in RecordingB.calls)


@pytest.mark.parametrize("bad", [
    lambda a: {**a, "session_id": "other"},
    lambda a: {**a, "answers": [{**a["answers"][0], "option_id": "forged"}]},
    lambda a: {**a, "answers": [{**a["answers"][0], "question_version": 9}]},
    lambda a: {**a, "answers": a["answers"] * 2},
    lambda a: {**a, "answers": []},
    lambda a: {"answers": a["answers"]},
    lambda a: {"choices": [], "interpretation_confirmed": False},
])
def test_resume_refuses_forged_stale_and_duplicate_answers(screen, bad):
    p = screen("ask2")
    waiting = p.run(TWO, as_of=AS_OF)
    with pytest.raises(ValueError):
        p.validate_resume(waiting, bad(answers_for(waiting, {"q:s1:stock": 0})))
    assert RecordingB.calls == []


def test_an_answered_checkpoint_cannot_take_the_same_answer_again(screen):
    p = screen("ask2")
    waiting = p.run(TWO, as_of=AS_OF)
    first = answers_for(waiting, {"q:s1:stock": 0})
    one = p.resume(waiting, first)
    with pytest.raises(ValueError):
        p.validate_resume(one, {**first, "session_id": one["confirmation"]["session_id"]})


def test_partial_keeps_the_selected_statistic_and_never_reports_ok(screen):
    p = screen("partial")
    state = p.run(TWO, as_of=AS_OF)
    assert state["status"] == "partial" and state["ui_cases"]
    assert stage_status(state)["table"] == "warning"
    assert state["message"].startswith("일부 통계만 조회했습니다. s2: 후보 표에서 찾지 못했습니다.")
    assert [c["tables"][0]["table_id"] for c in RecordingB.calls] == ["DT_161Y014"]


def test_judge_failure_stops_before_b(screen):
    p = screen("error")
    state = p.run(M2, as_of=AS_OF)
    assert state["status"] == "error" and state["error_code"] == "a_judge_failed"
    assert stage_status(state)["table"] == "failed" and stage_status(state)["conditions"] == "not_run"
    assert RecordingB.calls == [] and state["calls"]["HCX"] == 0


def period_supplier(view):
    return [{"question_id": f"p:{s['statistic']}", "statistics": [s["statistic"]],
             "text": "기간을 확인해 주세요(시험용).",
             "input": "choice_or_text", "options": [{"option_id": f"p:{s['statistic']}:o1", "label": "질문에 적힌 기간",
                                                     "value": "as_quoted"}]}
            for s in view["statistics"] if s["outcome"] == "selected"]


def period_answer(waiting, text):
    card = waiting["confirmation"]
    (g,) = card["groups"]
    assert g["kind"] == "period" and g["input"] == "choice_or_text" and g["synthetic"]
    return {"session_id": card["session_id"],
            "answers": [{"question_id": g["requirement_id"], "question_version": g["question_version"], "text": text}]}


def test_period_text_reaches_only_a_b_that_takes_it(screen):
    p = screen("selected", b=PeriodB, period_questions=period_supplier)
    waiting = p.run(M2, as_of=AS_OF)
    assert waiting["status"] == "needs_confirmation"
    done = p.resume(waiting, period_answer(waiting, "작년 말부터 지금까지"))
    assert done["status"] == "ok"
    (call,) = PeriodB.calls
    assert call["query"] == M2  # never appended to the question
    (a,) = call["period_answers"]
    assert (a["text"], a["statistics"], a["source"]) == ("작년 말부터 지금까지", ["s1"], "user_answer")


def test_period_text_is_not_sent_to_the_real_b_contract(screen):
    p = screen("selected", period_questions=period_supplier)  # RecordingB: like the real B, no period field
    waiting = p.run(M2, as_of=AS_OF)
    done = p.resume(waiting, period_answer(waiting, "작년 말부터 지금까지"))
    assert RecordingB.calls == [] and done["error_code"] == "a_handoff_unsupported"
    assert done["status"] == "unsupported" and stage_status(done)["table"] == "failed"
    (link,) = done["unsupported_links"]
    assert link["code"] == "period_answer_contract_pending" and link["statistics"] == ["s1"]
    assert link["detail"]["period_answers"][0]["text"] == "작년 말부터 지금까지"


# ---------------------------------------------------------------- review fixes (plan 54b): A's conditions kept to B

DIFF = "M2 통화량 2023년과 예금은행 대출금리 2024년 연간"
EDITIONS = "2001년부터 2010년까지 M2 말잔(원계열)을 연간으로 보여줘"
CALC = "2023년 연간 M2 통화량 비중과 M2 통화량 수준을 보여줘"
BASE = "2023년 연간 M2 통화량의 전년 대비 증감률을 보여줘"
WARN = "2024년 연간 수출 공산품 가격을 원화 기준으로 보여줘"
SCRIPTS.update({
    "periods_differ": {DIFF: {"statistics": S2, "periods": [{"quote": "2023년", "for": ["s1"]},
                                                            {"quote": "2024년", "for": ["s2"]}],
                              "choices": [{"for": ["s1"], "table": "DT_161Y014"},
                                          {"for": ["s2"], "table": "DT_121Y015"}]}},
    "same_period": {TWO: {"statistics": S2, "periods": [{"quote": "2024년", "for": ["s1", "s2"]}],
                          "choices": [{"for": ["s1"], "table": "DT_161Y014"}, {"for": ["s2"], "table": "DT_121Y015"}]}},
    "partial_period": {DIFF: {"statistics": S2, "periods": [{"quote": "2023년", "for": ["s1"]}],
                              "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
                              "missing": [{"for": ["s2"], "search_words": ["대출금리"]}]}},
    "editions": {EDITIONS: {"statistics": [{"id": "s1", "quote": "M2 말잔(원계열)", "name": "M2"}],
                            "periods": [{"quote": "2001년부터 2010년까지", "for": ["s1"]}],
                            # the cited value lets code keep it on the older edition (the period rule)
                            "choices": [{"for": ["s1"], "table": "DT_161Y008",
                                         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"},
                                                      {"kind": "item", "text": "M2 (말잔원계열)"},
                                                      {"kind": "bracket", "text": "말잔, 원계열"}]}]}},
    "calc": {CALC: {"statistics": [{"id": "s1", "quote": "M2 통화량 비중", "name": "M2 비중"},
                                   {"id": "s2", "quote": "M2 통화량 수준", "name": "M2"}],
                    "periods": [{"quote": "2023년", "for": ["s1", "s2"]}],
                    "calculations": [{"quote": "비중", "for": ["s1"]}],
                    "choices": [{"for": ["s1", "s2"], "table": "DT_161Y014"}]}},
    "base": {BASE: {"statistics": [{"id": "s1", "quote": "M2 통화량의 전년 대비 증감률", "name": "M2 증감률"}],
                    "periods": [{"quote": "2023년", "for": ["s1"]}],
                    "calculations": [{"quote": "전년 대비 증감률", "for": ["s1"]}],
                    "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}},
    "warning": {WARN: {"statistics": [{"id": "s1", "quote": "수출 공산품 가격", "name": "수출물가"}],
                       "splitters": [{"quote": "원화 기준", "axis": "currency", "for": ["s1"]}],
                       "periods": [{"quote": "2024년", "for": ["s1"]}],
                       "asks": [{"for": "s1", "axis": "classification",
                                 "options": [{"value": "기본분류", "table": "DT_402Y014"},
                                             {"value": "품목별", "table": "DT_402Y016"}]}]}},
})
with_frequency(SCRIPTS)


def test_different_periods_on_different_tables_are_not_sent(screen):
    p = screen("periods_differ")
    state = p.run(DIFF, as_of=AS_OF)
    assert RecordingB.calls == [] and state["calls"]["HCX"] == state["calls"]["KOSIS"] == 0
    assert state["status"] == "unsupported" and state["error_code"] == "a_handoff_unsupported"
    links = state["unsupported_links"]
    assert [(x["statistics"], x["code"]) for x in links] == [(["s1"], "period_differs_between_requests"),
                                                             (["s2"], "period_differs_between_requests")]
    assert [x["detail"][0]["tables"][0]["period"] for x in links] == [
        [{"start": "2023-01", "end": "2023-12"}], [{"start": "2024-01", "end": "2024-12"}]]
    # A's own outcome is kept as it was: both statistics stay selected
    assert {s["statistic"]: s["outcome"] for s in state["a_view"]["statistics"]} == {"s1": "selected",
                                                                                   "s2": "selected"}


def test_the_same_period_on_two_tables_still_runs(screen):
    p = screen("same_period")
    state = p.run(TWO, as_of=AS_OF)
    assert state["status"] == "ok" and state.get("unsupported_links") == []
    assert [c["tables"][0]["table_id"] for c in RecordingB.calls] == ["DT_161Y014", "DT_121Y015"]


def test_b_reading_the_whole_question_again_is_not_shown_as_done(screen):
    # only s1 (2023) goes to B; B reads 2023~2024 from the whole question: nothing is fetched
    p = screen("partial_period")
    state = p.run(DIFF, as_of=AS_OF)
    assert len(RecordingB.calls) == 1 and state["calls"]["KOSIS"] == 0 and state["ui_cases"] == []
    assert state["status"] == "unsupported" and state["error_code"] == "b_period_differs_from_a"
    link = next(x for x in state["unsupported_links"] if x["code"] == "b_period_differs_from_a")
    (row,) = link["detail"]
    assert row["a"] == {"frequency": "Y", "period": [{"start": "2023-01", "end": "2023-12"}]}
    assert (row["b"]["start"], row["b"]["end"]) == ("2023", "2024")


def test_a_period_split_over_editions_is_not_sent(screen):
    p = screen("editions")
    state = p.run(EDITIONS, as_of=AS_OF)
    tables = state["a_view"]["statistics"][0]["tables"]
    assert len(tables) == 2 and state["a_view"]["statistics"][0]["outcome"] == "selected"
    assert RecordingB.calls == [] and state["status"] == "unsupported"
    (link,) = state["unsupported_links"]
    assert link["code"] == "edition_period_split" and len(link["detail"][0]["tables"]) == 2


def test_different_calculations_on_one_table_go_with_a_note(screen):
    p = screen("calc")
    state = p.run(CALC, as_of=AS_OF)
    assert state["status"] == "ok" and len(RecordingB.calls) == 1
    assert RecordingB.calls[0]["tables"][0]["focus"] == "M2 통화량 비중 / M2 통화량 수준"
    assert "calculation_not_in_b_contract" in [n["code"] for n in state["handoff_notes"]]
    # wiring only: synthetic fetch, so nothing here says the share is computed right


def test_a_base_period_b_adds_without_saying_so_is_not_fetched(screen):
    # "전년 대비": B starts one year earlier than A's 2023 and its result does not state why or how far
    p = screen("base")
    state = p.run(BASE, as_of=AS_OF)
    assert len(RecordingB.calls) == 1 and state["calls"]["KOSIS"] == 0 and state["ui_cases"] == []
    assert state["status"] == "unsupported" and state["error_code"] == "b_period_differs_from_a"
    (row,) = next(x for x in state["unsupported_links"] if x["code"] == "b_period_differs_from_a")["detail"]
    assert row["kind"] == "added_before_start" and (row["b"]["start"], row["b"]["end"]) == ("2022", "2023")
    assert not state.get("handoff_notes")


def test_a_warning_is_shown_after_the_answer_and_changes_no_status(screen):
    p = screen("warning")
    waiting = p.run(WARN, as_of=AS_OF)
    assert waiting["status"] == "needs_confirmation"
    done = p.resume(waiting, answers_for(waiting, {"q:s1:classification": 1}))
    assert done["status"] == "ok" and done["ui_cases"]
    assert {w["code"] for w in done["a_warnings"]} == {"stated_basis_unverified"}
    assert all("원화" in w["text"] and w["statistic"] == "s1" for w in done["a_warnings"])
    assert any(x.startswith("기준 미확인 · ") for c in done["ui_cases"] for x in c["warnings"])
    assert "경고" in done["message"]
