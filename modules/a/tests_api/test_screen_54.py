"""Plan 54: A's screen service (claview_a_api.screen) and the AView adapter (claview_a_api.view).

Scripted judges only (no model, no network). Table IDs are catalog facts the cards show for these questions.
"""

from __future__ import annotations

import copy

import pytest

from claview_a_api import screen as S
from claview_a_api import view as V
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_common.contracts.a_view import AView

# plan 56 order 5 (policy ④, user 2026-10-31 17:53 item 1): these tests check the screen's wiring, not frequencies.
# Each made-up question and answer quotes the frequency the resolver's default used to give (연간) next to its period
# (``with_frequency``); before, no frequency was quoted and the longest one was assumed. The assertions are unchanged.
AS_OF = "2026-10-22"
M2 = "M2 통화량 2023년 연간으로 보여줘"
TWO = "M2 통화량과 예금은행 대출금리 2024년 연간"
# two periods in the question: the same table with different conditions (before, s2 had none quoted and the default
# window made it differ; a period missing for one statistic only is no longer filled)
TWO_PERIODS = "M2 통화량 2024년과 예금은행 대출금리 2023년 연간"
STOCK = {"for": "s1", "axis": "stock", "options": [{"value": "평잔", "table": "DT_161Y013"},
                                                    {"value": "말잔", "table": "DT_161Y014"}]}
STATS2 = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}, {"id": "s2", "quote": "예금은행 대출금리", "name": "rate"}]
SCRIPTS = {
    "ask": {M2: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
                 "periods": [{"quote": "2023년", "for": ["s1"]}], "asks": [STOCK]}},
    "selected": {M2: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
                      "periods": [{"quote": "2023년", "for": ["s1"]}],
                      "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}},
    "partial": {TWO: {"statistics": STATS2, "periods": [{"quote": "2024년", "for": ["s1", "s2"]}],
                      "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
                      "missing": [{"for": ["s2"], "search_words": ["대출금리"]}]}},
    "unverified": {M2: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
                        "periods": [{"quote": "2023년", "for": ["s1"]}],
                        "choices": [{"for": ["s1"], "table": "DT_161Y014",
                                     "evidence": [{"kind": "item", "text": "카드에 없는 항목"}]}]}},
    "error": {M2: {"judge_error": "timeout"}},
    "same_table_same": {TWO: {"statistics": STATS2, "periods": [{"quote": "2024년", "for": ["s1", "s2"]}],
                              "choices": [{"for": ["s1"], "table": "DT_161Y014"},
                                          {"for": ["s2"], "table": "DT_161Y014"}]}},
    "same_table_differ": {TWO_PERIODS: {"statistics": STATS2, "periods": [{"quote": "2024년", "for": ["s1"]},
                                                                      {"quote": "2023년", "for": ["s2"]}],
                                "choices": [{"for": ["s1"], "table": "DT_161Y014"},
                                            {"for": ["s2"], "table": "DT_161Y014"}]}},
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


def judges(name):
    j = ScriptJudge(by_question(SCRIPTS[name]))
    return S.Judges(j, j, "script")


def select(name, question=M2, **kw):
    return S.select(question, AS_OF, judges(name), **kw)


def test_profile_is_passed_explicitly(monkeypatch):
    seen = {}

    def run(question, judge, **kw):
        seen.update(kw)
        return {"question": question, "as_of": kw["as_of"], "action": "fail", "record": None, "run_info": {}}

    monkeypatch.setattr(S.claview_a, "run", run)
    S.select(M2, AS_OF, S.synthetic_judges())
    assert seen["settings"].model == "gpt-6.1-terra" and seen["research_settings"].model == "gpt-6.1-sol"
    assert seen["settings"].effort == seen["research_settings"].effort == "low"
    assert seen["settings"].max_output_tokens == seen["research_settings"].max_output_tokens == 4096
    assert seen["research"] is True and seen["research_judge"] is not None
    budget = seen["research_budget"]
    assert [budget(), budget()] == [True, False]  # one re-judgment per question


def test_the_first_request_names_the_first_model():
    j = judges("selected")
    S.select(M2, AS_OF, j)
    assert j.requests == ["gpt-6.1-terra"]


def test_live_judges_are_refused():
    with pytest.raises(S.LiveDisabled):
        S.judges_for("live")
    assert S.judges_for("synthetic").kind == "synthetic"


def test_view_keeps_a_outcomes_and_does_not_change_the_state():
    out = select("selected")
    checkpoint = out["checkpoint"]
    before = copy.deepcopy(checkpoint)
    again = V.build(checkpoint, S.layer())
    assert checkpoint == before and again == out["view"]  # same input, same view, input untouched
    AView(**again)
    state = checkpoint["a_state"]
    d = state["record"]["decisions"][0]
    s = again["statistics"][0]
    assert (s["outcome"], s["tables"][0]["table_id"]) == (d["outcome"], d["final_tables"][0]["table_id"])
    assert again["a_route"] == state["record"]["route"]["action"] == "handoff"
    assert again["status"] == "ready" and again["questions"] == []
    ready = again["handoff"]["ready"]
    assert [t["focus"] for t in ready[0]["tables"]] == ["M2 통화량"] and ready[0]["statistics"] == ["s1"]
    assert ready[0]["tables"][0]["org_id"] and ready[0]["tables"][0]["table_name"]
    assert again["session"]["run"]["model"]["model"] == "gpt-6.1-terra"


def test_ask_view_shows_the_question_and_hands_nothing_over():
    v = select("ask")["view"]
    assert v["status"] == "needs_answer" and v["a_route"] == "ask"
    (q,) = v["questions"]
    assert q["statistics"] == ["s1"] and q["axis"] == "stock" and q["input"] == "choice"
    assert [(o["value"], o["table_id"]) for o in q["options"]] == [("평잔", "DT_161Y013"), ("말잔", "DT_161Y014")]
    assert v["handoff"] == {"ready": [], "blocked": [{"statistics": ["s1"], "code": "awaiting_answer",
                                                      "detail": None}]}


def test_partial_unverified_and_failure_are_never_ready():
    v = select("partial", TWO)["view"]
    assert v["status"] == "partial" and v["a_route"] == "partial"
    assert [s["state"] for s in v["statistics"]] == ["selected", "missing"]
    assert [r["statistics"] for r in v["handoff"]["ready"]] == [["s1"]]
    assert v["handoff"]["blocked"][0]["statistics"] == ["s2"] and v["handoff"]["blocked"][0]["code"] == "not_in_pool"
    v = select("unverified")["view"]
    assert v["statistics"][0]["outcome"] == "selected_unverified" and v["statistics"][0]["verified"] is False
    assert v["handoff"]["ready"] == [] and v["handoff"]["blocked"][0]["code"] == "a_evidence_unverified"
    assert v["status"] == "failed"
    v = select("error")["view"]
    assert v["failure"] == {"stage": "judge", "code": "timeout"} and v["status"] == "failed"
    assert v["statistics"] == [] and v["handoff"]["ready"] == []


def test_same_table_requests_merge_only_with_the_same_conditions():
    v = select("same_table_same", TWO)["view"]
    (r,) = v["handoff"]["ready"]
    assert r["statistics"] == ["s1", "s2"] and r["tables"][0]["focus"] == "M2 통화량 / 예금은행 대출금리"
    v = select("same_table_differ", TWO_PERIODS)["view"]
    assert v["handoff"]["ready"] == []
    (b,) = v["handoff"]["blocked"]
    assert (b["statistics"], b["code"]) == (["s1", "s2"], "same_table_conditions_differ")
    # the detail is A's own assignment per statistic: 2024 quoted for s1, 2023 for s2
    assert [d["tables"][0]["table_id"] for d in b["detail"]] == ["DT_161Y014", "DT_161Y014"]
    assert b["detail"][0]["tables"][0]["period"] == [{"start": "2024-01", "end": "2024-12"}]
    assert b["detail"][0]["tables"][0]["period"] != b["detail"][1]["tables"][0]["period"]
    assert v["status"] == "failed"


def test_focus_too_long_is_reported_not_cut(monkeypatch):
    monkeypatch.setattr(V, "MAX_FOCUS_CHARS", 5)
    v = select("selected")["view"]
    assert v["handoff"]["ready"] == [] and v["handoff"]["blocked"][0]["code"] == "focus_too_long"


def answer_one(out, n=1, **change):
    cp, q = out["checkpoint"], out["view"]["questions"][0]
    r = {"question_id": q["question_id"], "question_version": q["question_version"],
         "option_id": q["options"][n]["option_id"], **change}
    return S.answer(cp, cp["session"]["session_id"], [r])


def test_answer_resumes_with_the_same_session_and_no_model_call():
    j = judges("ask")
    out = S.select(M2, AS_OF, j)
    before = copy.deepcopy(out["checkpoint"])
    done = answer_one(out)
    assert out["checkpoint"] == before and j.requests == ["gpt-6.1-terra"]
    v = done["view"]
    assert v["status"] == "ready" and v["statistics"][0]["tables"][0]["table_id"] == "DT_161Y014"
    assert v["statistics"][0]["answers"][0]["value"] == "말잔"
    for key in ("session_id", "question", "as_of"):
        assert v["session"][key] == out["view"]["session"][key]
    assert v["session"]["run"] == out["view"]["session"]["run"]
    assert v["session"]["question_version"] == out["view"]["session"]["question_version"] + 1


@pytest.mark.parametrize("change, code", [
    ({"question_version": 3}, "stale_question"),
    ({"option_id": "forged"}, "option_not_offered"),
    ({"text": "말잔"}, "option_or_text_required"),
    ({"question_id": "q:s1:price"}, "question_not_pending"),
    ({"extra": 1}, "reply_shape"),
])
def test_bad_answers_are_refused(change, code):
    out = select("ask")
    before = copy.deepcopy(out["checkpoint"])
    with pytest.raises(S.AnswerRejected) as e:
        answer_one(out, **change)
    assert e.value.code == code and out["checkpoint"] == before


def test_session_mismatch_duplicate_and_text_on_a_choice_are_refused():
    out = select("ask")
    cp, q = out["checkpoint"], out["view"]["questions"][0]
    r = {"question_id": q["question_id"], "question_version": q["question_version"],
         "option_id": q["options"][0]["option_id"]}
    with pytest.raises(S.AnswerRejected, match="session_mismatch"):
        S.answer(cp, "other", [r])
    with pytest.raises(S.AnswerRejected, match="duplicate_reply"):
        S.answer(cp, cp["session"]["session_id"], [r, r])
    with pytest.raises(S.AnswerRejected, match="text_not_allowed"):
        S.answer(cp, cp["session"]["session_id"], [{**{k: r[k] for k in ("question_id", "question_version")},
                                                     "text": "말잔"}])
    done = S.answer(cp, cp["session"]["session_id"], [r])
    with pytest.raises(S.AnswerRejected, match="question_not_pending"):  # the same reply again
        S.answer(done["checkpoint"], cp["session"]["session_id"], [r])


def period_supplier(view):
    """A test supplier of one period question per selected statistic (A makes none today)."""
    sid = "statistic"
    return [{"question_id": f"p:{s[sid]}", "statistics": [s[sid]], "text": "기간을 확인해 주세요(시험용).",
             "input": "choice_or_text",
             "options": [{"option_id": f"p:{s[sid]}:o1", "label": "질문에 적힌 기간", "value": "as_quoted"}]}
            for s in view["statistics"] if s["outcome"] == "selected"]


def test_period_text_answer_is_kept_as_given_and_goes_with_the_request_only():
    out = select("selected", period_questions=period_supplier)
    v = out["view"]
    (q,) = v["questions"]
    assert q["kind"] == "period" and q["synthetic"] and q["input"] == "choice_or_text"
    assert v["status"] == "needs_answer" and v["handoff"]["ready"] == []
    cp = out["checkpoint"]
    with pytest.raises(S.AnswerRejected, match="text_invalid"):
        S.answer(cp, cp["session"]["session_id"], [{"question_id": q["question_id"], "question_version": 0,
                                                     "text": "  "}])
    done = S.answer(cp, cp["session"]["session_id"], [{"question_id": q["question_id"], "question_version": 0,
                                                        "text": "작년 말부터 지금까지"}])
    v = done["view"]
    assert v["questions"] == [] and v["status"] == "ready"
    (r,) = v["handoff"]["ready"]
    (a,) = r["period_answers"]
    assert (a["text"], a["value"], a["statistics"], a["source"], a["kind"]) == (
        "작년 말부터 지금까지", "작년 말부터 지금까지", ["s1"], "user_answer", "period")
    # nothing is computed from it and the question is not rewritten
    assert v["session"]["question"] == M2 and v["statistics"][0]["tables"] == out["view"]["statistics"][0]["tables"]
    assert done["checkpoint"]["a_state"] == cp["a_state"]
