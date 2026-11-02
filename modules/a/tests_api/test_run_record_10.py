"""Plan 56 order 10 (bundle 9, C9): run records on disk, and the original question's ledger they share.

A session's record survives a restart; a new session (or a reworded question with its lineage) of the same original
question keeps the same ledger, so a re-judgment already used is never sent again; a missing, broken or other-version
record stops before any call. Scripted judges, temporary folders only (synthetic answers).
"""

from __future__ import annotations

import json

import pytest
from test_screen_54 import AS_OF, M2, SCRIPTS, TWO, judges

from claview_a_api import screen as S
from claview_a_api.run_record import RECORD_VERSION, RunRecordError, RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question


def test_a_session_is_recorded_and_read_back_after_a_restart(tmp_path):
    store = RunStore(tmp_path)
    out = S.select(M2, AS_OF, judges("ask"), store=store)
    sid = out["checkpoint"]["session"]["session_id"]
    body = json.loads((tmp_path / "a_sessions" / sid / "record.json").read_text(encoding="utf-8"))
    assert body["version"] == RECORD_VERSION and body["question"] == M2 and body["remaining_questions"]
    assert (tmp_path / "a_questions" / out["checkpoint"]["question_key"] / "ledger.json").exists()
    again = S.load(RunStore(tmp_path), sid)  # a new process reads it: no call
    assert again["checkpoint"] == out["checkpoint"]
    q = again["view"]["questions"][0]
    done = S.answer(again["checkpoint"], sid, [{"question_id": q["question_id"],
                                                "question_version": q["question_version"],
                                                "option_id": q["options"][0]["option_id"]}], store=store)
    assert S.load(store, sid)["checkpoint"]["session"]["question_version"] == 1
    assert [r["change_kind"] for r in done["checkpoint"]["runs"]] == ["initial", "user_answer"]


def counting(script):
    j = ScriptJudge(by_question(script))
    calls = []

    def judge(payload, request):
        calls.append(1)
        return j(payload, request)
    judge.answer_contract = j.answer_contract
    return S.Judges(judge, judge, "script"), calls


def test_a_new_session_of_the_same_question_keeps_its_ledger_and_calls_nothing(tmp_path):
    store = RunStore(tmp_path)
    first, calls1 = counting(SCRIPTS["partial"])
    out = S.select(TWO, AS_OF, first, store=store)
    assert len(calls1) >= 1
    second, calls2 = counting(SCRIPTS["partial"])
    with pytest.raises(RunRecordError) as e:  # after a restart or a 410, the question typed again
        S.select(TWO, AS_OF, second, store=store)
    assert e.value.code == "question_budget_used" and calls2 == []  # no first call, no re-judgment again
    assert out["checkpoint"]["session"]["session_id"] in e.value.detail  # the earlier session to read back


def test_a_reworded_question_with_its_lineage_keeps_the_original_question(tmp_path):
    store = RunStore(tmp_path)
    out = S.select(M2, AS_OF, judges("ask"), store=store)
    sid = out["checkpoint"]["session"]["session_id"]
    script = {"M2 통화량 2023년을 연간으로": SCRIPTS["ask"][M2]}
    j, calls = counting(script)
    with pytest.raises(RunRecordError) as e:  # the same original question: its budget, never a new one
        S.select("M2 통화량 2023년을 연간으로", AS_OF, j, store=store, lineage={"session_id": sid})
    assert e.value.code == "question_budget_used" and calls == [] and sid in e.value.detail


def test_a_lineage_whose_record_is_gone_calls_nothing(tmp_path):
    store = RunStore(tmp_path)
    j, calls = counting(SCRIPTS["ask"])
    with pytest.raises(RunRecordError) as e:
        S.select(M2, AS_OF, j, store=store, lineage={"session_id": "ab" * 16})
    assert e.value.code == "run_record_missing" and calls == []


def test_a_broken_or_other_version_record_is_refused(tmp_path):
    store = RunStore(tmp_path)
    out = S.select(M2, AS_OF, judges("ask"), store=store)
    sid = out["checkpoint"]["session"]["session_id"]
    path = tmp_path / "a_sessions" / sid / "record.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**body, "version": "a-run-record/old"}), encoding="utf-8")
    with pytest.raises(RunRecordError, match="run_record_version_mismatch"):
        S.load(store, sid)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RunRecordError, match="run_record_invalid"):
        S.load(store, sid)
    path.unlink()
    with pytest.raises(RunRecordError, match="run_record_missing"):
        S.load(store, sid)
