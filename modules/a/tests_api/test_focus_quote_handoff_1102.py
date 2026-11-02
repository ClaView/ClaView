"""Spacing-only source variations do not spend recovery or invalidate saved quote provenance."""

from __future__ import annotations

import copy

import pytest

from claview_a_api import screen as S
from claview_a_api import view as V
from claview_a_api.synthetic_judges import ScriptJudge, by_question

# Historical .7 fixtures (quote-only period rows): the focus handoff stays independent of contract .8's
# period rows
CONTRACT_VERSION = "tl-contract-2026-10-28.7"
QUESTION = "2024년 월별 예금은행 대출금리를 신규취급액 기준으로 보여줘."


def response(quote):
    return {
        "statistics": [{"id": "s1", "quote": quote, "name": "대출금리"}],
        "focuses": [{"for": ["s1"], "text": "예금은행 대출금리 신규취급액 기준", "quotes": [quote]}],
        "choices": [{"for": ["s1"], "table": "DT_121Y006"}],
        "splitters": [{"for": ["s1"], "quote": "신규취급액 기준", "axis": "loan_basis",
                       "value": "신규취급액", "polarity": "required"}],
        "periods": [{"for": ["s1"], "quote": "2024년", "meaning": "range", "refers_to": []}],
        "frequencies": [{"for": ["s1"], "quote": "월별"}],
    }


@pytest.mark.parametrize("separator", ["  ", "\t", "\n"])
def test_spacing_only_focus_reaches_handoff_without_recovery(separator):
    quote = "예금은행" + separator + "대출금리"
    script = response(quote)
    before = copy.deepcopy(script)
    judge = ScriptJudge(by_question({QUESTION: script}), answer_contract=CONTRACT_VERSION)
    out = S.select(QUESTION, "2026-10-29", S.Judges(judge, judge, "script"))
    assert judge.calls == 1
    assert script == before
    assert out["view"]["status"] == "ready"
    stat = out["view"]["statistics"][0]
    assert stat["quote"] == quote
    assert stat["focus_provenance"] == {
        "text": script["focuses"][0]["text"], "quotes": [quote], "source": "model_scope", "status": "grounded",
    }
    assert out["view"]["handoff"]["ready"][0]["tables"][0]["focus"] == script["focuses"][0]["text"]


@pytest.fixture(scope="module")
def old_checkpoint():
    script = response("예금은행 대출금리")
    script.pop("focuses")
    judge = ScriptJudge(by_question({QUESTION: script}), answer_contract="tl-contract-2026-10-28.6")
    out = S.select(QUESTION, "2026-10-29", S.Judges(judge, judge, "script"))
    assert judge.calls == 1 and out["view"]["status"] == "ready"
    checkpoint = out["checkpoint"]
    checkpoint["a_state"]["record"]["decisions"][0].pop("focus")
    return checkpoint


@pytest.mark.parametrize("quote,status", [
    ("예금은행  대출금리", "verified_quote_only"),
    ("예금은행\t대출금리", "verified_quote_only"),
    ("예금은행\n대출금리", "verified_quote_only"),
    ("예금은행·대출금리", "invalid"),
    ("예금은행 주택담보대출금리", "invalid"),
])
def test_legacy_fallback_uses_the_same_source_check_without_rewriting(old_checkpoint, quote, status):
    checkpoint = copy.deepcopy(old_checkpoint)
    checkpoint["a_state"]["record"]["decisions"][0]["quote"] = quote
    before = copy.deepcopy(checkpoint)
    view = V.build(checkpoint, S.layer())
    assert checkpoint == before
    assert view["statistics"][0]["focus_provenance"] == {
        "text": quote, "quotes": [quote], "source": "legacy_quote", "status": status,
    }
