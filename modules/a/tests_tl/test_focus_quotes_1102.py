"""Question quotes share whitespace handling without accepting altered words or punctuation."""

from __future__ import annotations

import pytest

from claview_a.check import _in_question
from claview_a.focus import check_scope


@pytest.mark.parametrize("question,quote", [
    ("월별 예금은행 대출금리를 보여줘", "예금은행  대출금리"),
    ("월별 예금은행 대출금리를 보여줘", "예금은행\t대출금리"),
    ("월별 예금은행 대출금리를 보여줘", "예금은행\n대출금리"),
    ("월별 예금은행\t\n대출금리를 보여줘", "예금은행 대출금리"),
])
def test_whitespace_quotes_pass_both_checks_and_keep_the_original(question, quote):
    assert _in_question(quote, question)
    scope, reasons = check_scope(question, {"text": "예금은행 대출금리", "quotes": [quote]})
    assert reasons == []
    assert scope["status"] == "grounded"
    assert scope["quotes"] == [quote]


@pytest.mark.parametrize("quote", [
    "기업대출 및 가계대출",  # A conjunction cannot replace punctuation.
    "기업대출 가계대출",  # Punctuation cannot be discarded.
    "기업대출, 가계대출",
    "기업대출·주택담보대출",
    "질문에 없는 대상",
    "",
    " \t\n ",
])
def test_non_whitespace_changes_and_missing_quotes_fail_both_checks(quote):
    question = "2024년 기업대출·가계대출 금리를 보여줘"
    assert not _in_question(quote, question)
    scope, reasons = check_scope(question, {"text": "대출금리", "quotes": [quote]})
    assert scope["status"] == "invalid"
    assert reasons == [{"code": "focus_quote_not_in_question", "detail": {"quotes": [quote]}}]
    assert scope["quotes"] == [quote]
