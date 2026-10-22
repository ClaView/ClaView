"""B model and KOSIS client checks (no network: httpx MockTransport).

Moved verbatim from test_a_review.py and test_ab_separation.py when old A and its tests were removed
(plan 26 step 4); these four tests check B only.
"""

from __future__ import annotations

import json

import httpx
import pytest

from claview.b.contracts import StageFailure, Trace
from claview.b.fetch import KosisClient
from claview.b.models import HcxModel, object_schema


def test_hcx_does_not_record_bare_key_echo():
    secret = "fake-secret-canary-only"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": secret}},
            },
        )

    trace = Trace()
    model = HcxModel(secret, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(StageFailure, match="hcx_sensitive_response"):
            model.complete("A", "s", {}, object_schema({}), 10, trace)
        assert secret not in json.dumps(trace.events)
    finally:
        model.close()


@pytest.mark.parametrize("seconds,expected_calls", [(23, 2), (90, 1)])
def test_rate_limit_respects_reset_and_preserves_safe_diagnostics(
    monkeypatch, seconds, expected_calls
):
    waits, calls = [], []
    monkeypatch.setattr("claview.b.models.time.sleep", waits.append)

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={
                    "x-ratelimit-reset-tokens": f"{seconds}s",
                    "x-ratelimit-remaining-tokens": "0",
                    "x-private": "never-record",
                },
                json={"status": {"code": "42901", "message": "never-record"}},
            )
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": "{}"}},
            },
        )

    trace = Trace()
    model = HcxModel("fake-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        if seconds > 60:
            with pytest.raises(StageFailure, match="hcx_http_429"):
                model.complete("A", "s", {}, object_schema({}), 10, trace)
            assert not waits
        else:
            model.complete("A", "s", {}, object_schema({}), 10, trace)
            assert waits == [seconds]
        assert len(calls) == expected_calls
        assert "never-record" not in json.dumps(trace.events)
        assert any(e.get("service_code") == "42901" for e in trace.events)
    finally:
        model.close()


def test_hcx_paces_successive_requests(monkeypatch):
    now, waits = [0.0], []
    monkeypatch.setattr("claview.b.models.time.monotonic", lambda: now[0])

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("claview.b.models.time.sleep", sleep)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": "{}"}},
            },
        )

    model = HcxModel(
        "fake-key", min_interval=20, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    try:
        for _ in range(2):
            model.complete("A", "s", {}, object_schema({}), 10, Trace())
        assert waits == [20]
    finally:
        model.close()


@pytest.mark.parametrize(
    "payload", [{"err": "20", "errMsg": "echo dummy-secret"}, [{"DT": "dummy-secret"}]]
)
def test_reflected_credentials_never_enter_b_fetch_trace(payload):
    trace = Trace()
    client = KosisClient(
        "dummy-secret",
        retries=0,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ),
    )
    try:
        with pytest.raises(StageFailure, match="kosis_credential_reflected"):
            client.fetch({"prdSe": "M"}, trace)
        assert "dummy-secret" not in json.dumps(trace.events)
        assert trace.events[-1]["event"] == "data_response_redacted"
    finally:
        client.close()
