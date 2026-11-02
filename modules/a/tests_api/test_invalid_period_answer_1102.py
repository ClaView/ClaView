"""Malformed calendar answers must leave a stored question open for correction."""

from __future__ import annotations

import copy

import pytest

from claview_a_api import screen as S
from claview_a_api.run_record import RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question

QUESTION = "M2 통화량 추이를 보여줘"
SCRIPT = {QUESTION: {
    "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
    "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
}}


def pending(out, kind):
    (question,) = [q for q in out["view"]["questions"] if q["kind"] == kind]
    return question


def answer(out, question, store, **value):
    checkpoint = out["checkpoint"]
    return S.answer(checkpoint, checkpoint["session"]["session_id"], [{
        "question_id": question["question_id"],
        "question_version": question["question_version"],
        **value,
    }], store)


def with_frequency(store, frequency):
    judge = ScriptJudge(by_question(SCRIPT))
    out = S.select(QUESTION, "2026-10-22", S.Judges(judge, judge, "script"), store=store)
    question = pending(out, "frequency")
    option = next(o for o in question["options"] if o["value"] == frequency)
    return answer(out, question, store, option_id=option["option_id"])


def disk_snapshot(store):
    return {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("invalid,frequency,correct,start,end", [
    ("2024년 0월", "M", "2024년 1월", "2024-01", "2024-01"),
    ("2024년 13월", "M", "2024년 12월", "2024-12", "2024-12"),
    ("2024년 14월", "M", "2024년 2월", "2024-02", "2024-02"),
    ("2024년 1월~13월", "M", "2024년 1월~12월", "2024-01", "2024-12"),
    ("2024년 5분기", "Q", "2024년 4분기", "2024-10", "2024-12"),
])
def test_invalid_period_preserves_stored_session_and_accepts_corrected_answer(
    tmp_path, invalid, frequency, correct, start, end,
):
    store = RunStore(tmp_path / "runs")
    first = with_frequency(store, frequency)
    sid = first["checkpoint"]["session"]["session_id"]
    out = S.load(store, sid)
    before = copy.deepcopy(out)
    before_files = disk_snapshot(store)
    assert out["checkpoint"]["session"]["question_version"] == 1
    assert len(out["view"]["statistics"][0]["answers"]) == 1

    with pytest.raises(S.AnswerRejected) as exc:
        answer(out, pending(out, "period"), store, text=invalid)

    assert (exc.value.code, exc.value.cause) == ("text_invalid", "malformed_condition")
    assert out == before
    assert S.load(store, sid) == before
    assert disk_snapshot(store) == before_files  # Answers, question version, records and ledger stay unchanged.

    done = answer(out, pending(out, "period"), store, text=correct)
    assert not done["view"]["questions"]
    (statistic,) = done["view"]["statistics"]
    assert statistic["outcome"] in ("selected", "selected_unverified")
    assert statistic["tables"][0]["period"] == [{"start": start, "end": end}]
    assert statistic["tables"][0]["frequency"] == frequency
    assert statistic["answers"][0] == before["view"]["statistics"][0]["answers"][0]
    assert len(statistic["answers"]) == 2
    assert done["checkpoint"]["session"]["question_version"] == 2
    assert done["checkpoint"]["a_ledger"] == before["checkpoint"]["a_ledger"]


@pytest.mark.parametrize("frequency,text,start,end", [
    ("M", "2024년 1월", "2024-01", "2024-01"),
    ("M", "2024년 12월", "2024-12", "2024-12"),
    ("Q", "2024년 1분기", "2024-01", "2024-03"),
    ("Q", "2024년 4분기", "2024-10", "2024-12"),
])
def test_valid_calendar_boundaries_remain_accepted(tmp_path, frequency, text, start, end):
    store = RunStore(tmp_path / "runs")
    out = with_frequency(store, frequency)
    done = answer(out, pending(out, "period"), store, text=text)
    (statistic,) = done["view"]["statistics"]
    assert statistic["outcome"] in ("selected", "selected_unverified")
    assert statistic["tables"][0]["period"] == [{"start": start, "end": end}]
    assert statistic["tables"][0]["frequency"] == frequency
    assert done["checkpoint"]["a_ledger"] == out["checkpoint"]["a_ledger"]
