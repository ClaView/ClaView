"""Plan 56 order 3 (bundle 8, L-06): the evaluation runners use the same guard as A.

Every scheduled question of a run has its own call ledger (C9 slots), recorded in run.json (``ledgers``); a 401/403
halts the key in the run's credential ledger, so the next send is refused before anything is reserved or sent. The
exploration runner keeps its gate (``CallGate``/``KeyGuard``) in front, as before; the comparison and table-level
runners use the same pieces (``guarded``, ``entry_ledger``, ``bind``), exercised here on their own. Fake transports;
no network.
"""

from __future__ import annotations

import json

import pytest
from test_key_stop_48 import Transport, manifest
from tl_eval import explore as E
from tl_eval import live as L

from claview_a.budget import LedgerError
from claview_a.contract import Settings
from claview_a.graph import JudgeError
from claview_a.judges import Archive, LiveJudge
from claview_a.transport.provider import ProviderError


def test_an_exploration_run_records_one_ledger_per_scheduled_question(tmp_path, layer):
    t = Transport()
    got = E.run(manifest(tmp_path), "live", tmp_path / "run", layer=layer, transport=t)
    assert got["ledgers"] == {f"terra|1|T{i}": {"a_first": 1} for i in (1, 2, 3)} and len(t.sent) == 3
    calls = [json.loads(x) for x in (tmp_path / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(c["slot"], c["sent"], c["billing"]) for c in calls] == [("a_first", "yes", "billed")] * 3


@pytest.mark.parametrize("code", ["http_401", "http_403"])
def test_after_a_key_refusal_the_next_question_reserves_nothing(tmp_path, layer, code):
    t = Transport(fail_with=code)
    with pytest.raises(L.CallRefused):
        E.run(manifest(tmp_path), "live", tmp_path / "run", layer=layer, transport=t)
    run = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert run["ledgers"] == {"terra|1|T1": {"a_first": 1}, "terra|1|T2": {}} and t.sent == ["gpt-6.1-terra"]


def test_the_comparison_and_table_level_runners_refuse_a_halted_key_before_reserving(tmp_path):
    """The loop of compare.run and live.run on its own: one live judge, a ledger per scheduled question."""
    t = Transport(fail_with="http_403")
    transport, credentials = L.guarded(t, "run-x")
    judge = LiveJudge(transport, 10, Archive(tmp_path / "calls.jsonl"), {"case_id": "c"}, credential_ref=L.CREDENTIAL)
    request = Settings(model="gpt-6.1-terra", effort="low", max_output_tokens=4096).request(
        {"question": "q", "cards": "", "unresolved": []})
    first, second = L.entry_ledger("q1"), L.entry_ledger("q2")
    L.bind([judge], first)
    with pytest.raises(JudgeError):
        judge({}, request)
    L.bind([judge], second)
    with pytest.raises(LedgerError) as e:
        judge({}, request)
    assert e.value.code == "credential_halted" and t.sent == ["gpt-6.1-terra"]
    assert first.summary()["by_slot"] == {"a_first": 1} and second.attempts() == []
    assert credentials.summary()["halted"] == {L.CREDENTIAL: "http_403"}


def test_a_server_error_does_not_halt_the_key(tmp_path):
    t = Transport(fail_with="http_500", fail_on=(1,))
    transport, credentials = L.guarded(t, "run-y")
    judge = LiveJudge(transport, 10, Archive(tmp_path / "calls.jsonl"), {"case_id": "c"}, credential_ref=L.CREDENTIAL)
    request = Settings(model="m", effort="low", max_output_tokens=10).request({"question": "q", "cards": "",
                                                                               "unresolved": []})
    for n, q in enumerate(("q1", "q2"), 1):
        L.bind([judge], L.entry_ledger(q))
        if n == 1:
            with pytest.raises(JudgeError):
                judge({}, request)
        else:
            judge({}, request)
    assert len(t.sent) == 2 and credentials.summary()["halted"] == {}


def test_a_provider_error_without_a_phase_is_still_told_apart():
    from claview_a.judges import sent_of

    assert sent_of(ProviderError("key_unavailable")) == ("no", "none_reported")
    assert sent_of(ProviderError("http_403")) == ("yes", "none_reported")
    assert sent_of(ProviderError("http_500")) == ("yes", "unknown")
    assert sent_of(ProviderError("transport")) == ("unknown", "unknown")
