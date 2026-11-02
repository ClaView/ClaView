"""Structured model rows never go back through the quote reader when applying an answer."""

import pytest

from claview_a import answers as A
from claview_a import contract as K
from claview_a_api import screen as S
from claview_a_api.run_record import RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question


def test_answer_recheck_reads_semantic_model_rows_only(monkeypatch):
    question = "2024년 한 해 월별 M2"
    out = {"periods": [{"quote": "2024년 한 해", "for": ["s1"]}],
           "frequencies": [{"quote": "월별", "for": ["s1"]}]}
    state = {"as_of": "2026-10-29", "record": {"question": question, "model_output": out,
             "parse": {"answer_contract": K.CONTRACT_VERSION}}}
    d = {"statistic": "s1", "quote": "M2", "representative": "chosen", "final_tables": []}
    sentinel = {"conditions": [], "readings": [], "problems": []}
    seen = []

    def semantic_read(*args, **kwargs):
        seen.append((args, kwargs))
        return sentinel

    def legacy_read(*args, **kwargs):
        raise AssertionError("legacy reader received structured model quotes")

    monkeypatch.setattr(A.PR, "read_quotes", legacy_read)
    monkeypatch.setattr(A.CH, "condition_reading", semantic_read)
    monkeypatch.setattr(A.CH, "decide_periods", lambda *args: seen.append(args[3]))
    monkeypatch.setattr(A, "_finish", lambda d: None)
    A._decide_conditions(state, d, None)
    assert seen and seen[-1] is sentinel


def test_partial_period_answer_preserves_endpoint_and_can_be_corrected(tmp_path):
    question = "M2 통화량을 2023년까지 월별로 보여줘"
    script = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
              "focuses": [{"for": ["s1"], "text": "M2 통화량", "quotes": ["M2 통화량"]}],
              "periods": [{"quote": "2023년까지", "for": ["s1"], "meaning": "user_undecided", "refers_to": [],
                           "form": "absolute", "start": "none", "end": "2023", "count": "none", "unit": "none",
                           "read": "ok"}],
              "frequencies": [{"quote": "월별", "for": ["s1"], "value": "M", "anchor": "none"}]}
    judge = ScriptJudge(by_question({question: script}), answer_contract=K.CONTRACT_VERSION)
    store = RunStore(tmp_path / "runs")
    first = S.select(question, "2026-10-29", S.Judges(judge, judge, "script"), store=store)
    q, = first["view"]["questions"]
    assert q["kind"] == "period" and "끝 2023-12" in q["text"]
    assert judge.calls == 1
    checkpoint = first["checkpoint"]
    sid = checkpoint["session"]["session_id"]
    reply = {"question_id": q["question_id"], "question_version": q["question_version"]}
    before = S.load(store, sid)
    with pytest.raises(S.AnswerRejected) as error:
        S.answer(checkpoint, sid, [{**reply, "text": "2022년~2024년"}], store)
    assert error.value.cause == "stated_period_boundary_changed"
    assert S.load(store, sid) == before
    done = S.answer(checkpoint, sid, [{**reply, "text": "2022년~2023년"}], store)
    stat, = done["view"]["statistics"]
    assert stat["tables"][0]["period"] == [{"start": "2022-01", "end": "2023-12"}]
    assert stat["tables"][0]["frequency"] == "M"
    assert judge.calls == 1


def test_generic_synthetic_demo_does_not_claim_explicit_period_is_absent():
    judges = S.synthetic_judges()
    out = S.select("M2 통화량과 예금은행 대출금리를 2024년 월별로 보여줘", "2026-10-29", judges)
    assert out["view"]["status"] == "ready"
    assert out["view"]["questions"] == []
    assert out["checkpoint"]["a_state"]["record"]["parse"]["answer_contract"] == "tl-contract-2026-10-28.7"
