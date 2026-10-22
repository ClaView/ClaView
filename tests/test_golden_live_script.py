"""Offline checks for scripts/ab/run_golden_live.py: no network, no keys."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from claview.aflow.model import HcxClient, ModelError, ModelReply
from claview.contracts import StageFailure, Trace
from claview.fetch import KosisClient
from claview.models import HcxModel, ScriptedModel

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ab/run_golden_live.py"
spec = importlib.util.spec_from_file_location("run_golden_live", SCRIPT)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def counts(**budget):
    return live.Counts({"A": 10, "B": 10, "KOSIS": 10, **budget})


def test_cache_dir_must_be_outside_repo(tmp_path):
    with pytest.raises(SystemExit):
        live.resolve_cache("live", ROOT / "runs" / "cache")
    assert live.resolve_cache("dry", None) is None
    assert live.resolve_cache("replay", tmp_path) == tmp_path.resolve()


def test_live_mode_uses_only_real_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-hcx")
    monkeypatch.setenv("KOSIS_API_KEY", "fake-kosis")
    a, b, k = live.build_components("live", live.DiskCache(tmp_path, False), counts())
    assert isinstance(a.inner, HcxClient) and a.inner.token_version == "q6g_tokens_v2"
    assert isinstance(b.inner, HcxModel) and not isinstance(b.inner, ScriptedModel)
    assert isinstance(k.inner, KosisClient)
    assert b.inner.retries == k.inner.retries == live.TRANSPORT_RETRIES
    b.inner.close()
    k.inner.close()


def test_replay_mode_has_no_clients(tmp_path):
    a, b, k = live.build_components("replay", live.DiskCache(tmp_path, True), counts())
    assert a.inner is None and b.inner is None and k.inner is None


def test_synthetic_replay_event_stops_live_run():
    trace = Trace()
    trace.add("model_request", mode="synthetic_replay")
    with pytest.raises(live.StopRun):
        live.assert_live_path("live", trace)
    live.assert_live_path("dry", trace)


class FakeA:
    name = "HCX-007"
    token_version = "q6g_tokens_v2"

    def __init__(self, outcomes):
        self.outcomes, self.sent = list(outcomes), 0

    def complete(self, node, system, payload, schema):
        self.sent += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def test_a_retries_transient_once_then_replays_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    c = counts()
    inner = FakeA([ModelError("transient", "x"), ModelReply('{"ok": 1}', 3, 4)])
    a = live.AClient(inner, live.DiskCache(tmp_path, False), c, attempts=2)
    assert a.complete("Q6G", "sys", {"q": 1}, {}).text == '{"ok": 1}'
    assert inner.sent == 2 and c.physical["A"] == 2
    again = live.AClient(None, live.DiskCache(tmp_path, True), c, attempts=0)
    assert again.complete("Q6G", "sys", {"q": 1}, {}).tokens_out == 4
    assert c.cache_hits["A"] == 1 and c.physical["A"] == 2
    with pytest.raises(live.StopRun):
        again.complete("Q6G", "sys", {"q": 2}, {})


def test_a_budget_is_not_hidden_as_fallback(tmp_path):
    a = live.AClient(
        FakeA([ModelReply("{}", 0, 0)]), live.DiskCache(tmp_path, False), counts(A=0), attempts=2
    )
    with pytest.raises(live.StopRun):
        a.complete("Q6G", "sys", {}, {})
    assert not issubclass(live.StopRun, Exception)


class FakeKosis:
    def __init__(self):
        self.sent = 0

    def fetch(self, params, trace):
        self.sent += 1
        trace.add("transport_attempt", service="KOSIS", attempt=1)
        if params.get("bad"):
            raise StageFailure("kosis_service_error")
        return [{"DT": "1"}]


def test_kosis_cache_keeps_rows_and_errors(tmp_path):
    c = counts()
    k = live.DataClientWrap(FakeKosis(), live.DiskCache(tmp_path, False), c)
    assert k.fetch({"tblId": "T"}, Trace()) == [{"DT": "1"}]
    with pytest.raises(StageFailure):
        k.fetch({"bad": 1}, Trace())
    replay = live.DataClientWrap(None, live.DiskCache(tmp_path, True), c)
    assert replay.fetch({"tblId": "T"}, Trace()) == [{"DT": "1"}]
    with pytest.raises(StageFailure, match="kosis_service_error"):
        replay.fetch({"bad": 1}, Trace())
    assert c.physical["KOSIS"] == 2 and c.cache_hits["KOSIS"] == 2


def test_kosis_budget_checked_before_call(tmp_path):
    inner = FakeKosis()
    k = live.DataClientWrap(inner, live.DiskCache(tmp_path, False), counts(KOSIS=1))
    with pytest.raises(live.StopRun):
        k.fetch({"tblId": "T"}, Trace())
    assert inner.sent == 0
