"""Recovery-only approval: real gate/judge wiring, temporary ledgers and a fake transport."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from test_live_gate_13_1 import M2, NOW, OTHER

from claview_a.budget import BudgetPolicy, LedgerError, key_of
from claview_a.graph import JudgeError
from claview_a.judges import Archive, LiveJudge
from claview_a.transport.provider import ProviderError
from claview_a_api import live_gate as G
from claview_a_api.run_record import RunStore


class Transport:
    kind = "live"

    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def preflight(self):
        pass

    def send(self, request):
        self.sent.append(request["model"])
        if self.error is not None:
            raise self.error
        return json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                           "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]})


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "LIVE_ROOT", tmp_path / "temporary_live")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(G.APPROVAL_ENV, raising=False)


def prepare(tmp_path, slots=None, *, grant=True, run_cap=1, enabled=True):
    slots = {"a_first": 0, "a_self_rejudge": 1} if slots is None else slots
    root = tmp_path / "records"
    approval = G.make_approval("recovery_only", [M2], sum(slots.values()),
                               {"a": run_cap, "b_model": 0, "kosis": 0},
                               (NOW + timedelta(hours=1)).isoformat(), root,
                               allowance={"slots": slots})
    approval.update(live_calls=enabled, approved_by="test", approved_at=NOW.isoformat())
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval), encoding="utf-8")
    if grant:
        G.grant_allowance(path, now=lambda: NOW)
    return path, RunStore(root)


def judges(path, store, transport, *, now=NOW):
    return G.judges(store, approval_path=path, transport_factory=lambda: transport, now=lambda: now)


def bind_raw(live, ledger, transport):
    """Use the production transport and judge without the graph's exception adapter."""
    gated = G.GatedTransport(transport, live.gate, ledger, key_of(M2))
    archive = Archive(live.gate.usage.folder / "calls.jsonl")
    return tuple(LiveJudge(gated, 1, archive, {"case_id": "recovery-only-test"}, ledger, slot, G.CREDENTIAL,
                           approval_ref=live.gate.approval_id, allowance=live.gate.allowance_context)
                 for slot in ("a_first", "a_self_rejudge"))


@pytest.mark.parametrize("slots", [{"a_first": 0, "a_self_rejudge": 1}, {"a_self_rejudge": 1}])
def test_only_recovery_is_sent_and_no_first_reservation_is_fabricated(tmp_path, slots):
    path, store = prepare(tmp_path, slots)
    fake = Transport()
    live = judges(path, store, fake)
    with G.open_ledger(key_of(M2), BudgetPolicy()) as ledger:
        before = list(ledger.events)
        live.admit(key_of(M2), ledger)
        assert live.rejudge_ok(key_of(M2), ledger)
        first, recovery = bind_raw(live, ledger, fake)
        with pytest.raises(LedgerError, match="allowance_slot_(used|missing)"):
            first({}, {"model": "gpt-6.1-terra"})
        assert ledger.events == before and fake.sent == []
        assert recovery({}, {"model": "gpt-6.1-sol"}) == "{}"
        assert fake.sent == ["gpt-6.1-sol"]
        assert ledger.events[:len(before)] == before
        assert ledger.used_under("a_first", "recovery_only") == 0
        assert ledger.used_under("a_self_rejudge", "recovery_only") == 1
        assert live.gate.usage.count("a") == 1
        # A new judge object (as after a restart) cannot open another recovery.
        again = judges(path, store, fake)
        _, second = bind_raw(again, ledger, fake)
        with pytest.raises(G.ApprovalRefused, match="allowance_slot_used"):
            second({}, {"model": "gpt-6.1-sol"})
        assert not again.rejudge_ok(key_of(M2), ledger)
        assert fake.sent == ["gpt-6.1-sol"]


def test_first_plus_recovery_keeps_its_existing_order_and_budget(tmp_path):
    path, store = prepare(tmp_path, {"a_first": 1, "a_self_rejudge": 1}, run_cap=2)
    fake = Transport()
    live = judges(path, store, fake)
    with G.open_ledger(key_of(M2), BudgetPolicy()) as ledger:
        live.admit(key_of(M2), ledger)
        first, recovery = live.bind(ledger, key_of(M2))
        assert first({}, {"model": "gpt-6.1-terra"}) == "{}"
        assert live.rejudge_ok(key_of(M2), ledger)
        assert recovery({}, {"model": "gpt-6.1-sol"}) == "{}"
        assert fake.sent == ["gpt-6.1-terra", "gpt-6.1-sol"]
        assert live.gate.usage.count("a") == 2


@pytest.mark.parametrize("case,code", [
    ("missing_grant", "allowance_missing"),
    ("version", "allowance_version_mismatch"),
    ("expired_grant", "allowance_expired"),
    ("wrong_question", "question_not_approved"),
    ("run_cap", "run_a_cap_reached"),
    ("halted", "credential_halted"),
])
def test_recovery_only_keeps_the_gate_refusals_before_sending(tmp_path, case, code):
    path, store = prepare(tmp_path, grant=case != "missing_grant", run_cap=0 if case == "run_cap" else 1)
    fake = Transport()
    live = judges(path, store, fake)
    with G.open_ledger(key_of(M2), BudgetPolicy()) as ledger:
        if case == "version":
            ledger.allowance("recovery_only")["versions"] = {"old": "version"}
        elif case == "expired_grant":
            ledger.allowance("recovery_only")["valid_until"] = (NOW - timedelta(seconds=1)).isoformat()
        elif case == "halted":
            live.gate.usage.halt(G.CREDENTIAL, "http_401")
        before = list(ledger.events)
        with pytest.raises(G.ApprovalRefused, match=code):
            live.admit(key_of(OTHER if case == "wrong_question" else M2), ledger)
        assert ledger.events == before and fake.sent == []


@pytest.mark.parametrize("case,code", [("draft", "approval_not_approved"), ("expired", "approval_expired")])
def test_a_recovery_approval_must_still_be_enabled_and_current(tmp_path, case, code):
    path, store = prepare(tmp_path, grant=False, enabled=case != "draft")
    fake = Transport()
    clock = NOW + timedelta(hours=2) if case == "expired" else NOW
    with pytest.raises(G.ApprovalRefused, match=code):
        judges(path, store, fake, now=clock)
    assert fake.sent == []


@pytest.mark.parametrize("provider_code", ["http_401", "http_403", "http_429", "http_500", "transport"])
def test_a_sent_recovery_failure_stays_used_and_cannot_be_retried(tmp_path, provider_code):
    path, store = prepare(tmp_path)
    error = ProviderError(provider_code, "send" if provider_code == "transport" else "response")
    fake = Transport(error)
    live = judges(path, store, fake)
    with G.open_ledger(key_of(M2), BudgetPolicy()) as ledger:
        _, recovery = live.bind(ledger, key_of(M2))
        with pytest.raises(JudgeError, match="transport"):
            recovery({}, {"model": "gpt-6.1-sol"})
        assert ledger.used_under("a_first", "recovery_only") == 0
        assert ledger.used_under("a_self_rejudge", "recovery_only") == 1
        assert live.gate.usage.count("a") == 1
        assert not live.rejudge_ok(key_of(M2), ledger)
        if provider_code in {"http_401", "http_403"}:
            assert live.gate.usage.halted(G.CREDENTIAL) == provider_code
        assert fake.sent == ["gpt-6.1-sol"]
