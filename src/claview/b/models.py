"""HCX structured-output adapter and explicitly synthetic test adapter."""

from __future__ import annotations

import copy
import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx
from jsonschema import Draft202012Validator

from .contracts import Json, StageFailure, Trace


def object_schema(properties: Json) -> Json:
    return {"type": "object", "properties": properties, "required": list(properties)}


def strict_schema(schema: Json) -> Json:
    result = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
            for value in list(node.values()):
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


def check_output(value: Json, schema: Json) -> None:
    if list(Draft202012Validator(strict_schema(schema)).iter_errors(value)):
        raise StageFailure("invalid_model_output")


class SelectionModel(Protocol):
    def complete(
        self, task: str, system: str, context: Json, schema: Json, max_tokens: int, trace: Trace
    ) -> Json: ...


def without_unoffered_expand(result: Json, schema: Json) -> Json:
    """A real model only fills the fields its schema offers; replay does the same.

    B offers `expand` only for questions that ask for a breakdown, while the shared
    fixtures always carry it.
    """
    try:
        offered = "expand" in schema["properties"]["selections"]["items"]["properties"]
        rows = result["selections"]
    except (KeyError, TypeError):
        return result
    if offered:
        return result
    return {**result, "selections": [{k: v for k, v in r.items() if k != "expand"} for r in rows]}


class ScriptedModel:
    """Only for fixture replay; never a retrieval or model-accuracy baseline."""

    def __init__(self, responses: dict[str, list[Json]]):
        self.responses = defaultdict(
            deque, {k: deque(copy.deepcopy(v)) for k, v in responses.items()}
        )

    def complete(self, task, system, context, schema, max_tokens, trace):
        trace.add(
            "model_request",
            task=task,
            mode="synthetic_replay",
            system=system,
            context=context,
            schema=schema,
            max_tokens=max_tokens,
        )
        if not self.responses[task]:
            raise StageFailure("fixture_response_missing")
        result = self.responses[task].popleft()
        result = without_unoffered_expand(result, schema)
        trace.add("model_response", task=task, mode="synthetic_replay", response=result)
        return result


def call_metrics(request_ms, attempts, usage, prompt_key, completion_key):
    """What one answered call cost: seconds on the wire and tokens billed.

    `latency_ms` is the last HTTP attempt only (request sent -> response received), so
    retry back-offs and the caller's own pacing are not counted. A token count the
    endpoint did not report is None, never 0. `reasoning_tokens` (OpenAI) is part of
    `completion_tokens`, not on top of it.
    """
    usage = usage if isinstance(usage, dict) else {}

    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    details = usage.get("completion_tokens_details")
    return {
        "latency_ms": request_ms,
        "attempts": attempts,
        "prompt_tokens": count(usage.get(prompt_key)),
        "completion_tokens": count(usage.get(completion_key)),
        "reasoning_tokens": count(details.get("reasoning_tokens"))
        if isinstance(details, dict)
        else None,
    }


class HcxModel:
    url = "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007"

    def __init__(
        self,
        api_key: str,
        retries: int = 2,
        client: httpx.Client | None = None,
        min_interval: float = 0,
    ):
        if not api_key:
            raise StageFailure("hcx_key_missing", "unresolved")
        self._key = api_key
        if not math.isfinite(min_interval) or not 0 <= min_interval <= 60:
            raise ValueError("HCX interval must be between 0 and 60 seconds")
        self.min_interval = min_interval
        self._last_request_at = None
        self.retries = retries
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=False)
        # Per-call measurements of the most recent complete(): see call_metrics().
        self.last_metrics = None

    def close(self):
        self.client.close()

    def complete(self, task, system, context, schema, max_tokens, trace):
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "thinking": {"effort": "none"},
            "responseFormat": {"type": "json", "schema": schema},
            "temperature": 0,
            "seed": 42,
            "maxCompletionTokens": max_tokens,
        }
        trace.add("model_request", task=task, mode="live", model="HCX-007", body=body)
        self.last_metrics = None
        start = time.monotonic()
        for attempt in range(self.retries + 1):
            if self._last_request_at is not None:
                wait = self.min_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    trace.add("transport_wait", service="HCX", reason="min_interval", seconds=wait)
                    time.sleep(wait)
            self._last_request_at = time.monotonic()
            trace.add("transport_attempt", service="HCX", task=task, attempt=attempt + 1)
            attempt_started = time.monotonic()
            try:
                response = self.client.post(
                    self.url,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + self._key,
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                        "Content-Type": "application/json",
                    },
                )
            except httpx.TransportError as exc:
                # Do not retain exception text, URLs, headers or credentials.
                trace.add(
                    "transport_error",
                    service="HCX",
                    task=task,
                    attempt=attempt + 1,
                    exception_type=type(exc).__name__,
                    elapsed_ms=round((time.monotonic() - attempt_started) * 1000),
                )
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
                raise StageFailure("hcx_transport_error") from None
            request_ms = round((time.monotonic() - attempt_started) * 1000)
            rate = safe_rate_headers(response.headers)
            service_code = None
            if response.status_code >= 400:
                try:
                    code = str(response.json()["status"]["code"])
                    if re.fullmatch(r"\d{5}", code):
                        service_code = code
                except (ValueError, KeyError, TypeError):
                    pass
            trace.add(
                "transport_response",
                service="HCX",
                task=task,
                http_status=response.status_code,
                service_code=service_code,
                rate_limits=rate,
            )
            if response.status_code == 429:
                wait = max(
                    [
                        rate[k]
                        for k in (
                            "retry_after_seconds",
                            "reset_requests_seconds",
                            "reset_tokens_seconds",
                        )
                        if k in rate
                    ],
                    default=30.0 * (2**attempt),
                )
                wait = max(1.0, wait)
                if attempt < self.retries and wait <= 60:
                    trace.add("transport_wait", service="HCX", reason="rate_limit", seconds=wait)
                    time.sleep(wait)
                    continue
            elif response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
            if response.status_code >= 400:
                raise StageFailure(f"hcx_http_{response.status_code}")
            try:
                payload = response.json()
            except ValueError:
                raise StageFailure("hcx_invalid_response") from None
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("status"), dict)
                or str(payload.get("status", {}).get("code")) != "20000"
            ):
                raise StageFailure("hcx_service_error")
            result = payload.get("result", {})
            if not isinstance(result, dict):
                raise StageFailure("hcx_invalid_response")
            if self._key in json.dumps(result, ensure_ascii=False):
                raise StageFailure("hcx_sensitive_response")
            self.last_metrics = call_metrics(
                request_ms, attempt + 1, result.get("usage"), "promptTokens", "completionTokens"
            )
            trace.add(
                "model_response",
                task=task,
                mode="live",
                response=result,
                elapsed_ms=round((time.monotonic() - start) * 1000),
            )
            if result.get("finishReason") == "length":
                raise StageFailure("output_truncated")
            if result.get("finishReason") != "stop":
                raise StageFailure("unexpected_finish_reason")
            try:
                value = json.loads(result["message"]["content"])
            except (ValueError, KeyError, TypeError):
                raise StageFailure("invalid_model_output") from None
            if not isinstance(value, dict):
                raise StageFailure("invalid_model_output")
            return value
        raise StageFailure("hcx_transport_error")


class OpenAIModel:
    """Chat Completions adapter for OpenAI and OpenAI-compatible endpoints.

    Used to run B on a model other than HCX-007 over the same prompts and schemas.
    The key is passed in by the caller (the CLI reads it from an environment variable);
    it is never written to the trace, to exceptions or to `last_error`.

    `schema_mode`:
      * "strict" -- `response_format=json_schema` with `strict: true`. Strict mode rejects
        some keywords, so `minItems`/`maxItems` are removed from the copy that is sent;
        the answer is still checked against the full schema afterwards.
      * "json_object" -- the schema is appended to the system text instead. For providers
        or schemas (very large `enum` lists) that strict mode refuses.
    Only parameters the caller sets are sent: `temperature` and `reasoning_effort` default
    to None because reasoning models reject or ignore some of them.
    """

    url = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        schema_mode: str = "strict",
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        retries: int = 2,
        client: httpx.Client | None = None,
        min_interval: float = 0,
    ):
        if not api_key:
            raise StageFailure("openai_key_missing", "unresolved")
        if not model:
            raise ValueError("model name is required")
        if schema_mode not in ("strict", "json_object"):
            raise ValueError("schema_mode must be 'strict' or 'json_object'")
        if not math.isfinite(min_interval) or not 0 <= min_interval <= 60:
            raise ValueError("interval must be between 0 and 60 seconds")
        self._key = api_key
        self.model = model
        if base_url:
            self.url = base_url.rstrip("/") + "/chat/completions"
        self.schema_mode = schema_mode
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.retries = retries
        self.min_interval = min_interval
        self._last_request_at = None
        self.client = client or httpx.Client(timeout=120.0, follow_redirects=False)
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self.last_error = None
        # Per-call measurements of the most recent complete(): see call_metrics().
        self.last_metrics = None

    def close(self):
        self.client.close()

    @staticmethod
    def sendable_schema(schema: Json) -> Json:
        result = strict_schema(schema)

        def visit(node):
            if isinstance(node, dict):
                for key in ("minItems", "maxItems"):
                    node.pop(key, None)
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(result)
        return result

    def _body(self, system, context, schema, max_tokens):
        sent = self.sendable_schema(schema)
        if self.schema_mode == "strict":
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "claview_output", "strict": True, "schema": sent},
            }
        else:
            response_format = {"type": "json_object"}
            system = (
                system
                + "\n\n반드시 아래 JSON schema를 따르는 JSON 객체 하나만 출력한다.\n"
                + json.dumps(sent, ensure_ascii=False)
            )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "response_format": response_format,
            "max_completion_tokens": max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***")[:400]

    def complete(self, task, system, context, schema, max_tokens, trace):
        body = self._body(system, context, schema, max_tokens)
        trace.add("model_request", task=task, mode="live", model=self.model, body=body)
        self.last_metrics = None
        start = time.monotonic()
        for attempt in range(self.retries + 1):
            if self._last_request_at is not None:
                wait = self.min_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    trace.add(
                        "transport_wait", service="OpenAI", reason="min_interval", seconds=wait
                    )
                    time.sleep(wait)
            self._last_request_at = time.monotonic()
            trace.add("transport_attempt", service="OpenAI", task=task, attempt=attempt + 1)
            attempt_started = time.monotonic()
            try:
                response = self.client.post(
                    self.url,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + self._key,
                        "Content-Type": "application/json",
                    },
                )
            except httpx.TransportError as exc:
                trace.add(
                    "transport_error",
                    service="OpenAI",
                    task=task,
                    attempt=attempt + 1,
                    exception_type=type(exc).__name__,
                    elapsed_ms=round((time.monotonic() - attempt_started) * 1000),
                )
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
                raise StageFailure("openai_transport_error") from None
            request_ms = round((time.monotonic() - attempt_started) * 1000)
            rate = safe_rate_headers(response.headers)
            error_type = None
            if response.status_code >= 400:
                error_message = None
                try:
                    error = response.json()["error"]
                    error_type = str(error.get("code") or error.get("type") or "")[:60]
                    error_message = self._scrub(str(error.get("message", "")))
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
                self.last_error = {
                    "http_status": response.status_code,
                    "type": error_type,
                    "message": error_message,
                }
            trace.add(
                "transport_response",
                service="OpenAI",
                task=task,
                http_status=response.status_code,
                service_code=error_type,
                rate_limits=rate,
            )
            if response.status_code == 429:
                wait = max(
                    [
                        rate[k]
                        for k in (
                            "retry_after_seconds",
                            "reset_requests_seconds",
                            "reset_tokens_seconds",
                        )
                        if k in rate
                    ],
                    default=10.0 * (2**attempt),
                )
                wait = max(1.0, wait)
                if attempt < self.retries and wait <= 60:
                    trace.add("transport_wait", service="OpenAI", reason="rate_limit", seconds=wait)
                    time.sleep(wait)
                    continue
            elif response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
            if response.status_code >= 400:
                raise StageFailure(f"openai_http_{response.status_code}")
            try:
                payload = response.json()
                choice = payload["choices"][0]
                message = choice["message"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise StageFailure("openai_invalid_response") from None
            if self._key in json.dumps(payload, ensure_ascii=False):
                raise StageFailure("openai_sensitive_response")
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self.usage["calls"] += 1
                for field in ("prompt_tokens", "completion_tokens"):
                    if isinstance(usage.get(field), int):
                        self.usage[field] += usage[field]
            self.last_metrics = call_metrics(
                request_ms, attempt + 1, usage, "prompt_tokens", "completion_tokens"
            )
            trace.add(
                "model_response",
                task=task,
                mode="live",
                response={"finish_reason": choice.get("finish_reason"), "usage": usage},
                elapsed_ms=round((time.monotonic() - start) * 1000),
            )
            if message.get("refusal"):
                raise StageFailure("model_refusal")
            if choice.get("finish_reason") == "length":
                raise StageFailure("output_truncated")
            if choice.get("finish_reason") != "stop":
                raise StageFailure("unexpected_finish_reason")
            try:
                value = json.loads(message["content"])
            except (ValueError, KeyError, TypeError):
                raise StageFailure("invalid_model_output") from None
            if not isinstance(value, dict):
                raise StageFailure("invalid_model_output")
            return value
        raise StageFailure("openai_transport_error")


def safe_rate_headers(headers) -> Json:
    """Retain only parsed numeric quota metadata, never arbitrary response headers."""
    result = {}
    for kind in ("requests", "tokens"):
        for field in ("limit", "remaining"):
            raw = headers.get(f"x-ratelimit-{field}-{kind}", "")
            if re.fullmatch(r"-?\d{1,12}", raw):
                result[f"{field}_{kind}"] = int(raw)
    for header, key in (
        ("retry-after", "retry_after_seconds"),
        ("x-ratelimit-reset-requests", "reset_requests_seconds"),
        ("x-ratelimit-reset-tokens", "reset_tokens_seconds"),
    ):
        match = re.fullmatch(r"(\d{1,8}(?:\.\d{1,3})?)(ms|s|m)?", headers.get(header, ""))
        composite = re.fullmatch(r"(\d{1,4})m(\d{1,2}(?:\.\d{1,3})?)s", headers.get(header, ""))
        if match:
            result[key] = float(match[1]) * {None: 1, "s": 1, "ms": 0.001, "m": 60}[match[2]]
        elif composite:
            result[key] = int(composite[1]) * 60 + float(composite[2])
        elif header == "retry-after":
            try:
                when = parsedate_to_datetime(headers.get(header, ""))
                if when.tzinfo is not None:
                    result[key] = max(0.0, (when - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return result
