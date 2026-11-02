"""Plan 56 order 3 (bundle 8): the screen's question goes through the call ledger (C9 slots); nothing is sent."""

from __future__ import annotations

from claview_a_api import screen as S

M2 = "2024년 1~3월 M2 총액을 평잔 기준으로 보여줘"
AS_OF = "2026-10-22"


def test_the_screen_records_its_calls_in_the_question_ledger():
    out = S.select(M2, AS_OF, S.synthetic_judges())
    ledger = out["checkpoint"]["a_ledger"]
    assert ledger["by_slot"]["a_first"] == 1 and ledger["total"] in (1, 2) and ledger["unsettled"] == 0
    assert ledger["policy"]["a_self_rejudge"] == S.PROFILE["max_rejudge_calls"]
    assert ledger["policy"]["b_request_model"] is None and ledger["total_cap"] is None


def test_a_used_slot_is_refused_by_the_counted_judge_before_it_runs():
    calls = []
    judges = S.Judges(lambda payload, request: calls.append(1) or "{}", lambda payload, request: "{}", "script")
    ledger = S.ledger_for(M2)
    first, _ = judges.counted(ledger)
    first({}, {"model": "m", "input": "x"})
    try:
        first({}, {"model": "m", "input": "y"})
    except Exception as e:  # LedgerError: the second first judgment of one question
        assert getattr(e, "code", None) == "slot_used"
    else:
        raise AssertionError("a second first judgment was not refused")
    assert calls == [1] and judges.requests == ["m"]
