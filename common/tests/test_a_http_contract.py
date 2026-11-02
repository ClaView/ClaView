"""The new HTTP contract is explicit, bounded, and separate from legacy AResult."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claview_common.contracts import A_HTTP_VERSION, AHTTPAnswerRequest, AHTTPResult, AHTTPSelectRequest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize("as_of", ["2024-02-29", "2026-10-29", None])
def test_fixed_calendar_day_or_omitted(as_of):
    body = AHTTPSelectRequest(contract_version=A_HTTP_VERSION, query="2024년 대출금리", as_of=as_of)
    assert body.as_of == as_of


@pytest.mark.parametrize("as_of", ["today", "2024-02-30", "2024-13-01", "20241029", "2026-1-2", 0])
def test_no_guessing_or_coercion_of_reference_day(as_of):
    with pytest.raises(ValidationError):
        AHTTPSelectRequest(contract_version=A_HTTP_VERSION, query="대출금리", as_of=as_of)


@pytest.mark.parametrize("extra", [{"mode": "live"}, {"checkpoint": {}}, {"approval": "x"}, {"state": {}}])
def test_request_cannot_change_execution_or_server_state(extra):
    with pytest.raises(ValidationError):
        AHTTPSelectRequest(contract_version=A_HTTP_VERSION, query="대출금리", **extra)


@pytest.mark.parametrize("body", [{"query": "GDP"}, {"contract_version": "old", "query": "GDP"},
                                  {"contract_version": A_HTTP_VERSION, "query": "  "}])
def test_legacy_or_unsupported_version_and_empty_question_refused(body):
    with pytest.raises(ValidationError):
        AHTTPSelectRequest(**body)


def test_reply_has_one_answer_and_strict_question_version():
    common = {"contract_version": A_HTTP_VERSION, "session_id": "a" * 32}
    good = {"question_id": "period:s1", "question_version": 0, "text": "2024년"}
    assert AHTTPAnswerRequest(**common, replies=[good]).replies[0].text == "2024년"
    for changes in ({"option_id": "recent3"}, {"text": None}, {"question_version": True},
                    {"question_version": "0"}, {"text": "x" * 201}):
        with pytest.raises(ValidationError):
            AHTTPAnswerRequest(**common, replies=[{**good, **changes}])


@pytest.mark.parametrize("name", ["focus_model_scope", "focus_legacy_quote"])
def test_fixtures_preserve_public_view_and_focus(name):
    view = json.loads((FIXTURES / "a_view" / f"{name}.json").read_text(encoding="utf-8"))
    request = AHTTPSelectRequest.model_validate_json(
        (FIXTURES / "a_http_request" / f"{name}.json").read_text(encoding="utf-8"))
    result = AHTTPResult.model_validate_json(
        (FIXTURES / "a_http_result" / f"{name}.json").read_text(encoding="utf-8"))
    assert result.view.model_dump() == view
    assert request.query == view["session"]["question"]
    assert result.execution_mode == result.meta.mode == "synthetic"
    assert "checkpoint" not in result.model_dump()
