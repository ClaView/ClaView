"""Plan 56 order 10 (bundle 9, 1차): the answers of A's own period questions on the screen path.

A typed period is read by the reader or refused with why; an answer replaces only that statistic's condition and is
recorded with what it replaced; every question carries the one session question version; the same batch again is
refused; a reset takes the answers back, never the call ledger. Scripted judges only (synthetic answers).
"""

from __future__ import annotations

import copy

import pytest

from claview_a_api import screen as S
from claview_a_api.synthetic_judges import ScriptJudge, by_question

AS_OF = "2026-10-22"
Q = "M2 통화량을 연간으로 보여줘"
SCRIPT = {Q: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "frequencies": [{"quote": "연간", "for": ["s1"]}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}}  # no period quoted: A asks it (synthetic)


def select():
    j = ScriptJudge(by_question(SCRIPT))
    return S.select(Q, AS_OF, S.Judges(j, j, "script"))


def period_question(out):
    (q,) = [x for x in out["view"]["questions"] if x["kind"] == "period"]
    return q


def typed(out, text):
    cp, q = out["checkpoint"], period_question(out)
    return S.answer(cp, cp["session"]["session_id"], [{"question_id": q["question_id"],
                                                       "question_version": q["question_version"], "text": text}])


def test_a_typed_period_replaces_only_that_condition_and_says_what_it_replaced():
    out = select()
    assert all(q["question_version"] == out["checkpoint"]["session"]["question_version"]
               for q in out["view"]["questions"])  # one question version for every question
    done = typed(out, "최근 5년")
    d = done["checkpoint"]["a_state"]["record"]["decisions"][0]
    (row,) = d["condition_answers"]
    assert row["replaces"] == [{"statistic": "s1", "condition": "period", "from": None, "to": "최근 5년"}]
    assert not d.get("condition_asks") and d["outcome"] in ("selected", "selected_unverified")
    assert done["checkpoint"]["session"]["question_version"] == 1
    assert done["checkpoint"]["a_state"]["record"]["model_output"]["frequencies"][0]["quote"] == "연간"  # kept


def test_an_unreadable_typed_period_is_refused_with_why_and_nothing_changes():
    out = select()
    before = copy.deepcopy(out["checkpoint"])
    with pytest.raises(S.AnswerRejected) as e:
        typed(out, "언젠가")
    assert e.value.code == "text_invalid" and e.value.cause and out["checkpoint"] == before


def test_the_same_batch_again_is_refused_and_a_stale_one_too():
    out = select()
    cp, q = out["checkpoint"], period_question(out)
    reply = [{"question_id": q["question_id"], "question_version": q["question_version"], "text": "최근 5년"}]
    done = S.answer(cp, cp["session"]["session_id"], reply)
    with pytest.raises(S.AnswerRejected) as e:
        S.answer({**done["checkpoint"], "session": cp["session"]}, cp["session"]["session_id"], reply)
    assert e.value.code == "duplicate_batch"
    with pytest.raises(S.AnswerRejected):
        S.answer(done["checkpoint"], cp["session"]["session_id"], reply)  # an old version: refused


def test_a_reset_takes_the_answers_back_and_never_the_ledger():
    out = select()
    done = typed(out, "최근 5년")
    again = S.reset(done["checkpoint"], done["checkpoint"]["session"]["session_id"])
    cp = again["checkpoint"]
    assert [q["kind"] for q in again["view"]["questions"]] == ["period"]  # asked again
    assert cp["session"]["question_version"] == 2 and cp["a_ledger"] == out["checkpoint"]["a_ledger"]
    assert cp["runs"][-1]["what"] == "reset"
    typed(again, "최근 3년")  # answering again after the reset
    with pytest.raises(S.AnswerRejected, match="session_mismatch"):
        S.reset(cp, "other")
