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
        if match:
            result[key] = float(match[1]) * {None: 1, "s": 1, "ms": 0.001, "m": 60}[match[2]]
        elif header == "retry-after":
            try:
                when = parsedate_to_datetime(headers.get(header, ""))
                if when.tzinfo is not None:
                    result[key] = max(0.0, (when - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return result
