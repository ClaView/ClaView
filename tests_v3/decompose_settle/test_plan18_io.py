"""Plan 18 with fake transports only; real network remains blocked by conftest."""

import copy
import json

import pytest

from test_plan15_settle import P, layer
from test_plan15_step5 import simple, QUESTION
from claview_v3 import _guard
from claview_v3.agent.decomposition import CallLedger
from claview_v3.agent.graph import run_outer
from claview_v3.contract.check import check_analysis
from claview_v3.interpret import decompose_provider as DP, decompose_run as DR
from claview_v3.interpret.decompose_archive import ArchiveError, ReplayMiss, ResponseArchive, digest
from claview_v3.interpret.pipeline import interpret_question


def body(proposal, *, status="completed", refusal=False):
    content = {"type": "refusal", "refusal": "declined"} if refusal else {
        "type": "output_text", "text": json.dumps(proposal, ensure_ascii=False)}
    return json.dumps({"id": "synthetic_response", "status": status, "model": "test_model",
                       "output": [{"type": "message", "content": [content]}],
                       "usage": {"input_tokens": 100, "output_tokens": 20,
                                 "output_tokens_details": {"reasoning_tokens": 5}}}, ensure_ascii=False)


class FakeTransport:
    kind = "fake"

    def __init__(self, *, error=None, raw=None):
        self.calls, self.error, self.raw = [], error, raw

    def preflight(self):
        pass

    def send(self, request):
        self.calls.append(copy.deepcopy(request))
        if self.error:
            raise self.error
        return self.raw if self.raw is not None else body(simple(P(json.loads(request["input"]))))


def sources(tmp_path, **kw):
    transport = FakeTransport(**kw)
    archive = ResponseArchive(tmp_path / "archive", 2)
    settings = DP.ResponseSettings("gpt-6-luna", "low")
    live = DP.LiveSource(archive, "sample", settings, approved=True, transport=transport)
    return live, DP.ReplaySource(archive, "sample", settings), transport


def run(layer, source, **kw):
    return run_outer(QUESTION, "router", layer=layer, request_id="test", as_of="2026-10-20",
                     decomposition={"mode": "gpt", "execution_mode": source.execution_mode,
                                    "proposal_source": source}, **kw)


def test_live_and_replay_cross_outer_graph_without_an_extra_call(layer, tmp_path):
    live, replay, transport = sources(tmp_path)
    ledger = CallLedger()
    first = run(layer, live, decomposition_ledger=ledger)
    reused = run(layer, live, decomposition_ledger=ledger)
    played = run(layer, replay)
    assert len(transport.calls) == 1
    for state in (first, reused, played):
        assert check_analysis(state["analysis"]) == []
        assert state["analysis"]["decomposition"]["applied_source"] == "model"
        assert state["jev_record"]["mode"] == "blocked_by_decomposition"
    assert first["analysis"]["interpretation"] == played["analysis"]["interpretation"]
    assert first["selections"] == played["selections"]
    assert first["response"]["decomposition"]["fake_calls"] == 1
    assert first["response"]["decomposition"]["real_calls"] == 0
    assert reused["response"]["decomposition"]["provider_calls"] == 0
    assert played["response"]["decomposition"]["replay_calls"] == 1
    assert played["response"]["decomposition"]["model_calls"] == 0
    assert played["response"]["decomposition"]["usage"] is None
    assert _guard.NETWORK_ATTEMPTS == []


def test_replay_runs_current_verifier_even_when_its_version_changes(layer, tmp_path, monkeypatch):
    live, replay, _ = sources(tmp_path)
    run(layer, live)
    old = DP.versions()
    monkeypatch.setattr(DP, "versions", lambda: {**old, "interpret/decompose_verify.py": "new_version"})
    verify, calls = DR.verify, []

    def check(*args, **kwargs):
        calls.append(True)
        return verify(*args, **kwargs)

    monkeypatch.setattr(DR, "verify", check)
    assert run(layer, replay)["analysis"]["decomposition"]["applied_source"] == "model"
    assert calls == [True]
    assert replay.measurement["archived_versions"] != replay.measurement["current_versions"]


@pytest.mark.parametrize("change", ["missing", "model", "effort", "prompt", "schema", "input", "tamper"])
def test_replay_miss_never_sends_or_reuses_a_different_generation(layer, tmp_path, monkeypatch, change):
    live, replay, transport = sources(tmp_path)
    run(layer, live)
    request = copy.deepcopy(transport.calls[0])
    if change == "missing":
        replay.call_id = "absent"
    elif change in ("model", "effort"):
        replay.settings = DP.ResponseSettings("other" if change == "model" else "gpt-6-luna",
                                              "high" if change == "effort" else "low")
    elif change == "prompt":
        monkeypatch.setattr(DP, "prompt_text", lambda: "different instructions")
    elif change in ("schema", "input"):
        request[change if change == "input" else "text"] = "changed"
        with pytest.raises(ReplayMiss):
            replay.archive.read("sample", request)
        return
    else:
        row = json.loads(replay.archive.path("sample").read_text(encoding="utf-8"))
        row["raw_response"] += " "
        replay.archive.update(row)
    state = run(layer, replay)
    assert state["analysis"]["decomposition"]["fallback_reason"] == "replay_miss"
    assert check_analysis(state["analysis"]) == []
    assert len(transport.calls) == 1


@pytest.mark.parametrize("error", [TimeoutError("secret"), RuntimeError("secret"), DP.ProviderError("secret")])
def test_failure_consumes_one_reservation_and_is_never_retried(layer, tmp_path, error):
    live, replay, transport = sources(tmp_path, error=error)
    first = run(layer, live)
    second = run(layer, live)  # new outer ledger, same durable call ID
    assert len(transport.calls) == 1
    assert first["analysis"]["decomposition"]["fallback_reason"] == "transport"
    assert second["response"]["decomposition"]["fake_calls"] == 0
    raw = live.archive.path("sample").read_text(encoding="utf-8")
    assert "secret" not in raw and "secret" not in json.dumps(first)
    row = json.loads(raw)
    assert row["attempts"] == 1 and row["status"] == "failed" and row["raw_response"] is None
    assert run(layer, replay)["analysis"]["decomposition"]["fallback_reason"] == "replay_miss"


@pytest.mark.parametrize("raw", [body({}, status="incomplete"), body({}, refusal=True), "invalid JSON",
                                json.dumps({"status": "completed", "output": []}), body({"wrong": "schema"})])
def test_received_invalid_outputs_are_saved_and_do_not_become_certain(layer, tmp_path, raw):
    live, replay, transport = sources(tmp_path, raw=raw)
    first = run(layer, live)
    again = run(layer, replay)
    assert first["analysis"]["decomposition"]["applied_source"] == "rules_fallback"
    assert again["analysis"]["decomposition"]["applied_source"] == "rules_fallback"
    assert len(transport.calls) == 1
    assert live.archive.read("sample", transport.calls[0])["raw_response"] == raw


def test_approval_is_checked_before_claiming_or_transport(layer, tmp_path):
    live, _, transport = sources(tmp_path)
    live.approved = False
    ledger = CallLedger()
    with pytest.raises(ValueError, match="approval"):
        run(layer, live, decomposition_ledger=ledger)
    assert not ledger.entries and not transport.calls and not live.archive.root.exists()


def test_durable_cap_includes_started_and_failed_reservations(tmp_path):
    archive = ResponseArchive(tmp_path, 2)
    row = archive.reserve("a", {}, {}, "fake")
    archive.reserve("b", {}, {}, "fake")
    row["status"] = "failed"
    archive.update(row)
    reopened = ResponseArchive(tmp_path, 2)
    with pytest.raises(ArchiveError, match="call_limit"):
        reopened.reserve("c", {}, {}, "fake")
    with pytest.raises(ArchiveError, match="already_reserved"):
        reopened.reserve("a", {}, {}, "fake")
    with pytest.raises(ArchiveError, match="batch_conflict"):
        ResponseArchive(tmp_path, 3).reserve("c", {}, {}, "fake")


def test_request_has_strict_schema_and_no_hidden_followup_or_tools(layer, tmp_path):
    live, _, transport = sources(tmp_path)
    run(layer, live)
    request = transport.calls[0]
    assert request["text"]["format"]["strict"] is True
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] == 8192 and request["store"] is False
    assert request["service_tier"] == "default"
    assert not {"tools", "previous_response_id", "temperature"} & set(request)
    row = live.archive.read("sample", request)
    assert row["request_hash"] == digest(request)
    assert row["usage"]["output_tokens_details"]["reasoning_tokens"] == 5


@pytest.mark.parametrize("status,code", [(401, "http_401"), (307, "http_307"), (200, "unsafe_response")])
def test_production_transport_has_no_retry_redirect_or_secret_error_body(tmp_path, monkeypatch, status, code):
    import http.client

    secret, calls, closed = "sk-test-secret-never-record", [], []
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    class Connection:
        def __init__(self, host, *, timeout):
            assert host == "api.openai.com" and timeout == 60.0

        def request(self, method, path, **kwargs):
            calls.append((method, path))
            assert kwargs["headers"]["Authorization"] == "Bearer " + secret
            assert kwargs["headers"]["Content-Type"] == "application/json"
            assert json.loads(kwargs["body"])["store"] is False

        def getresponse(self):
            return type("Response", (), {"status": status, "read": lambda self: secret.encode("utf-8")})()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    live = DP.LiveSource(ResponseArchive(tmp_path), "a", DP.ResponseSettings("gpt-6-luna"), approved=True)
    with pytest.raises(DP.ProviderError, match=code):
        live({}, {})
    assert calls == [("POST", "/v1/responses")] and closed == [True]
    assert secret not in live.archive.path("a").read_text(encoding="utf-8")
    assert live.measurement["real_calls"] == 1


def test_existing_reservation_lock_blocks_send_without_deleting_the_lock(layer, tmp_path):
    live, _, transport = sources(tmp_path)
    live.archive.root.mkdir()
    lock = live.archive.root / ".reservation.lock"
    lock.write_text("", encoding="utf-8")
    state = run(layer, live)
    assert state["analysis"]["decomposition"]["fallback_reason"] == "transport"
    assert not transport.calls and lock.exists()


def test_no_key_or_oversized_request_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    live = DP.LiveSource(ResponseArchive(tmp_path / "key"), "a", DP.ResponseSettings("gpt-6-luna"), approved=True)
    with pytest.raises(DP.ProviderError, match="key_unavailable"):
        live({}, {})
    assert not live.archive.root.exists() and live.measurement["real_calls"] == 0
    transport = FakeTransport()
    live = DP.LiveSource(ResponseArchive(tmp_path / "size"), "b",
                         DP.ResponseSettings("gpt-6-luna", max_input_bytes=1), approved=True, transport=transport)
    with pytest.raises(DP.ProviderError, match="request_limit"):
        live({}, {})
    assert not transport.calls and not live.archive.root.exists()

