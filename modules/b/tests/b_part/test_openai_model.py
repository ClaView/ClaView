"""OpenAIModel: request shape, key hygiene, failure codes (no network)."""

import json

import httpx
import pytest

from claview_b.contracts import StageFailure, Trace
from claview_b.models import OpenAIModel, object_schema, safe_rate_headers

SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "minItems": 0,
            "maxItems": 5,
            "items": object_schema({"code": {"type": "string", "enum": ["a", "b"]}}),
        }
    },
    "required": ["selections"],
}


def reply(content, finish="stop", **message):
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": finish, "message": {"content": content, **message}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 7},
        },
    )


def make(handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIModel("sk-test-only", "some-model", client=client, **kwargs)


def test_strict_request_shape_and_usage():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return reply('{"selections": []}')

    model = make(handler, reasoning_effort="low")
    trace = Trace()
    assert model.complete("B", "sys", {"q": 1}, SCHEMA, 500, trace) == {"selections": []}
    body = seen["body"]
    assert body["model"] == "some-model" and body["max_completion_tokens"] == 500
    assert body["reasoning_effort"] == "low" and "temperature" not in body
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    sent = json.dumps(fmt["json_schema"]["schema"])
    assert "minItems" not in sent and "maxItems" not in sent
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert seen["auth"] == "Bearer sk-test-only"
    assert "sk-test-only" not in json.dumps(trace.events)
    assert model.usage == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 7}
    model.close()


def test_json_object_mode_puts_schema_in_system_text():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return reply("{}")

    model = make(handler, schema_mode="json_object", temperature=0)
    model.complete("B", "sys", {}, SCHEMA, 50, Trace())
    body = seen["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert "selections" in body["messages"][0]["content"] and body["temperature"] == 0
    model.close()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (reply("{}", finish="length"), "output_truncated"),
        (reply("{}", finish="content_filter"), "unexpected_finish_reason"),
        (reply(None, refusal="no"), "model_refusal"),
        (reply("not json"), "invalid_model_output"),
        (reply("[1]"), "invalid_model_output"),
        (httpx.Response(200, json={"choices": []}), "openai_invalid_response"),
        (
            httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": "sk-test-only"}}]
                },
            ),
            "openai_sensitive_response",
        ),
    ],
)
def test_failure_codes(response, expected):
    model = make(lambda request: response)
    with pytest.raises(StageFailure, match=expected):
        model.complete("B", "s", {}, SCHEMA, 10, Trace())
    model.close()


def test_http_errors_keep_endpoint_message_but_never_the_key(monkeypatch):
    monkeypatch.setattr("claview_b.models.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(
            400,
            json={"error": {"code": "invalid_json_schema", "message": "bad schema sk-test-only"}},
        )

    model = make(handler)
    with pytest.raises(StageFailure, match="openai_http_400"):
        model.complete("B", "s", {}, SCHEMA, 10, Trace())
    assert len(calls) == 1
    assert model.last_error["type"] == "invalid_json_schema"
    assert "sk-test-only" not in model.last_error["message"]
    model.close()


def test_429_is_retried_then_succeeds_and_5xx_too(monkeypatch):
    monkeypatch.setattr("claview_b.models.time.sleep", lambda _: None)
    answers = [
        httpx.Response(429, headers={"x-ratelimit-reset-tokens": "0m2s"}),
        httpx.Response(503),
        reply("{}"),
    ]
    model = make(lambda request: answers.pop(0))
    assert model.complete("B", "s", {}, SCHEMA, 10, Trace()) == {}
    assert not answers
    model.close()


def test_missing_key_and_bad_mode():
    with pytest.raises(StageFailure, match="openai_key_missing"):
        OpenAIModel("", "m")
    with pytest.raises(ValueError):
        OpenAIModel("k", "m", schema_mode="loose")


def test_composite_reset_durations_are_parsed():
    rate = safe_rate_headers(
        {"x-ratelimit-reset-tokens": "6m0s", "x-ratelimit-reset-requests": "1.5s"}
    )
    assert rate["reset_tokens_seconds"] == 360 and rate["reset_requests_seconds"] == 1.5


def test_429_with_a_long_wait_is_not_retried(monkeypatch):
    monkeypatch.setattr("claview_b.models.time.sleep", lambda _: None)
    model = make(lambda request: httpx.Response(429, headers={"x-ratelimit-reset-tokens": "6m0s"}))
    with pytest.raises(StageFailure, match="openai_http_429"):
        model.complete("B", "s", {}, SCHEMA, 10, Trace())
    model.close()
