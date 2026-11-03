"""Actual HTTP sends are counted; rejected sends and authentication follow-ups never leave the client."""

import httpx
import pytest

from claview_b.contracts import StageFailure, Trace
from claview_pipeline.runtime import ServiceTransports


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("first", ["B_model", "KOSIS"])
def test_auth_failure_blocks_both_followup_services(status, first):
    guard = ServiceTransports()
    sent = []

    def respond(request):
        sent.append(request.method)
        return httpx.Response(status, json={"error": "denied"})

    transports = {name: guard.transport(name, httpx.MockTransport(respond)) for name in ("B_model", "KOSIS")}
    request = httpx.Request("POST", "https://example.invalid/api", json={})
    transports[first].handle_request(request)
    for name in transports:
        with pytest.raises(StageFailure, match="service_credential_halted"):
            transports[name].handle_request(request)
    assert sent == ["POST"]
    assert sum(guard.sends.values()) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_kosis_service_has_no_http_retry(status):
    guard = ServiceTransports()
    calls = []

    def respond(request):
        calls.append(1)
        return httpx.Response(status, json={"err": "30"})

    model, client = guard.clients(None, "fixture-secret", data_transport=httpx.MockTransport(respond))
    assert model is None and client.retries == 0
    try:
        with pytest.raises(StageFailure, match=f"kosis_http_{status}"):
            client.fetch({"orgId": "301"}, Trace())
        assert calls == [1] and guard.sends == {"B_model": 0, "KOSIS": 1}
    finally:
        client.close()


def test_independent_service_run_starts_its_own_http_counts():
    guard = ServiceTransports()
    guard.before("KOSIS")
    guard.after("KOSIS", 401)
    guard.start()
    assert guard.sends == {"B_model": 0, "KOSIS": 0} and guard.halted is None
