"""Codex review 11-01 ①: a reply is checked against the session's stored record, applied and saved under its lock.
A copy of the session loaded twice, or an older job's copy, can never apply a stale or repeated batch, and a refused
batch changes nothing on disk. Scripted judges only (synthetic answers)."""

from __future__ import annotations

import json

import pytest

from claview_a_api import screen as S
from claview_a_api.run_record import RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question

AS_OF = "2026-10-22"
Q = "M2 통화량을 연간으로 보여줘"
SCRIPT = {Q: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
              "frequencies": [{"quote": "연간", "for": ["s1"]}],
              "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}}  # no period quoted: A asks it


def reply(view, text):
    (q,) = [x for x in view["questions"] if x["kind"] == "period"]
    return [{"question_id": q["question_id"], "question_version": q["question_version"], "text": text}]


def record(store, sid):
    return json.loads((store.root / "a_sessions" / sid / "record.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("second_text", ["최근 5년", "최근 3년"])  # the same batch again, or another one
def test_a_session_loaded_twice_applies_one_batch_only(tmp_path, second_text):
    store = RunStore(tmp_path / "runs")
    j = ScriptJudge(by_question(SCRIPT))
    out = S.select(Q, AS_OF, S.Judges(j, j, "script"), store=store)
    sid = out["checkpoint"]["session"]["session_id"]
    first, second = S.load(store, sid), S.load(store, sid)  # two copies of the same stored session
    S.answer(first["checkpoint"], sid, reply(first["view"], "최근 5년"), store)
    kept = record(store, sid)
    assert kept["question_version"] == 1 and len(kept["answers"]) == 1
    with pytest.raises(S.AnswerRejected):
        S.answer(second["checkpoint"], sid, reply(second["view"], second_text), store)
    assert record(store, sid) == kept  # nothing on disk changed
    assert not (store.root / "a_sessions" / sid / "record.lock").exists()  # the lock is let go
