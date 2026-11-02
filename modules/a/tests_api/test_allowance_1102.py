"""Re-measurement allowance (owner 11-02 04:22 UTC; plan p13_14 allowance_plan.md): a question whose first judgment
is used opens again only under an approval whose allowance was granted on its ledger, for the approved questions,
slots, code versions and time, never by resetting the ledger or widening the service's per-question limit. A fake
transport counts what is sent; the live ledger is a temporary folder (no key, no network). Made-up answers.
Every approval names its own run records folder (as approvals always do); the live ledger is one for every folder,
so a new folder never opens a question again by itself."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from test_live_gate_13_1 import AS_OF, M2, NOW, OTHER, FakeTransport, missing

from claview_a.budget import BudgetPolicy, Ledger, LedgerError, key_of
from claview_a_api import live_gate as G
from claview_a_api import screen as S
from claview_a_api.run_record import RunRecordError, RunStore

SLOTS = {"a_first": 1, "a_self_rejudge": 1}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "LIVE_ROOT", tmp_path / "live")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(G.APPROVAL_ENV, raising=False)
    FakeTransport.made = 0


def approval(tmp_path, approval_id, questions=(M2,), allowance=True, per_question_a=2, hours=2, approved_hours=0,
             run_root_id=None):
    root = tmp_path / "runs" / (run_root_id or approval_id)
    record = G.make_approval(approval_id, list(questions), per_question_a, {"a": 10, "b_model": 0, "kosis": 0},
                             (NOW + timedelta(hours=hours)).isoformat(), root,
                             allowance={"slots": dict(SLOTS)} if allowance else None)
    record.update(live_calls=True, approved_by="owner", approved_at=(NOW - timedelta(hours=approved_hours)).isoformat())
    path = tmp_path / f"{approval_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def live(store, path, transport, now=NOW):
    return S.live_judges(store, approval_path=str(path), transport_factory=lambda: transport, now=lambda: now)


def store_of(tmp_path, approval_id):
    return RunStore(tmp_path / "runs" / approval_id)


def ledger_path(question):
    return G.live_root() / "a_questions" / key_of(question) / "ledger.json"


def summary(question):
    with Ledger.open(ledger_path(question), key_of(question)) as led:
        return led.summary(), led.attempts()


def refused(code, store, path, fake, question=M2):
    """The approval's refusal before anything is reserved or sent (raised, as every admission refusal is)."""
    with pytest.raises(G.ApprovalRefused, match=code):
        S.select(question, AS_OF, live(store, path, fake), store=store)
    assert fake.sent == []


def used_once(tmp_path):
    """M2's first judgment used under an earlier approval without an allowance (the state BOK20 and GV4 are in)."""
    fake = FakeTransport([])
    store = store_of(tmp_path, "old01")
    S.select(M2, AS_OF, live(store, approval(tmp_path, "old01", allowance=False), fake), store=store)
    assert fake.sent == ["gpt-6.1-terra"] and summary(M2)[0]["by_slot"] == {"a_first": 1}


def test_a_used_question_opens_only_after_its_allowance_is_granted(tmp_path):
    used_once(tmp_path)
    path = approval(tmp_path, "new01")
    fake = FakeTransport([])
    store = store_of(tmp_path, "new01")
    refused("allowance_missing", store, path, fake)  # approved, but not granted on the ledger
    G.grant_allowance(path, now=lambda: NOW)
    fake = FakeTransport([])
    # Admission refusal wrote no run. Reuse the exact approved file and folder; a grant binds their hash.
    S.select(M2, AS_OF, live(store, path, fake), store=store)
    assert fake.sent == ["gpt-6.1-terra"]
    attempts = summary(M2)[1]
    assert [(a["slot"], a["approval_ref"]) for a in attempts] == [("a_first", "old01"), ("a_first", "new01")]


def test_the_ledger_keeps_its_lines_and_only_grows(tmp_path):
    used_once(tmp_path)
    before = ledger_path(M2).read_bytes()
    G.grant_allowance(approval(tmp_path, "new01"), now=lambda: NOW)
    after = ledger_path(M2).read_bytes()
    assert after.startswith(before) and after != before
    tail = [json.loads(x) for x in after[len(before):].decode("utf-8").splitlines() if x.strip()]
    assert [(e["kind"], e["allowance_id"], e["slots"]) for e in tail] == [("allow", "new01", SLOTS)]


def test_an_allowance_is_for_its_own_approval_and_questions_only(tmp_path):
    used_once(tmp_path)
    G.grant_allowance(approval(tmp_path, "new01"), now=lambda: NOW)
    fake = FakeTransport([])
    store = store_of(tmp_path, "new02")
    refused("allowance_missing", store, approval(tmp_path, "new02"), fake)  # another approval's opens nothing
    store = store_of(tmp_path, "new01")
    refused("question_not_approved", store, approval(tmp_path, "new01"), fake, OTHER)


def test_the_service_limit_is_unchanged_by_an_allowance(tmp_path):
    used_once(tmp_path)
    G.grant_allowance(approval(tmp_path, "new01"), now=lambda: NOW)
    fake = FakeTransport([])
    store = store_of(tmp_path, "old02")
    with pytest.raises(RunRecordError, match="question_budget_used"):  # the service path, as before
        S.select(M2, AS_OF, live(store, approval(tmp_path, "old02", allowance=False), fake), store=store)
    assert fake.sent == []
    with Ledger.open(ledger_path(M2), key_of(M2)) as led:
        assert not led.available("a_first") and led.policy == BudgetPolicy()


def test_the_allowance_slots_are_spent_once_and_never_refill(tmp_path):
    used_once(tmp_path)
    path = approval(tmp_path, "new01")
    G.grant_allowance(path, now=lambda: NOW)
    fake = FakeTransport([missing])  # a first judgment that finds no table: the one re-search and re-judgment
    store = store_of(tmp_path, "new01")
    S.select(M2, AS_OF, live(store, path, fake), store=store)
    assert fake.sent == ["gpt-6.1-terra", "gpt-6.1-sol"]  # first +1, recovery +1 of the allowance, no third
    by = [(a["slot"], a["approval_ref"]) for a in summary(M2)[1]]
    assert by == [("a_first", "old01"), ("a_first", "new01"), ("a_self_rejudge", "new01")]
    fake = FakeTransport([])
    refused("allowance_slot_used", store, path, fake)  # the same run records: the allowance is spent
    store = store_of(tmp_path, "new01c")  # another records folder: the live ledger still refuses, nothing refills
    refused("allowance_slot_used", store, approval(tmp_path, "new01", run_root_id="new01c"), fake)


def test_an_old_remaining_recovery_slot_is_not_spent_by_the_new_run(tmp_path):
    """The earlier approval left its recovery slot unused (as BOK20 and GV4 did): the allowance run has its own one
    recovery, so at most two sends happen under it, never the old slot as a second recovery."""
    used_once(tmp_path)
    path = approval(tmp_path, "new01")
    G.grant_allowance(path, now=lambda: NOW)
    fake = FakeTransport([missing, missing])
    store = store_of(tmp_path, "new01")
    S.select(M2, AS_OF, live(store, path, fake), store=store)
    assert len(fake.sent) == 2
    assert [a["approval_ref"] for a in summary(M2)[1] if a["slot"] == "a_self_rejudge"] == ["new01"]


@pytest.mark.parametrize("change, code", [("versions", "allowance_version_mismatch"),
                                          ("valid_until", "allowance_expired")])
def test_an_allowance_for_other_code_or_out_of_time_is_refused_before_any_send(tmp_path, change, code):
    used_once(tmp_path)
    path = approval(tmp_path, "new01")
    record = json.loads(path.read_text(encoding="utf-8"))
    versions = dict(record["conditions"]["versions"])
    until = record["valid_until"]
    if change == "versions":
        versions["prompt"] = "tl-prompt-old"
    else:
        until = (NOW - timedelta(minutes=1)).isoformat()
    with Ledger.open(ledger_path(M2), key_of(M2)) as led:
        led.grant("new01", dict(SLOTS), versions, until, approved_by="owner", approved_at=NOW.isoformat(),
                  approval_sha256="x")
    fake = FakeTransport([])
    store = store_of(tmp_path, "new01")
    refused(code, store, path, fake)


def test_granting_twice_is_a_noop_and_more_than_one_slot_is_refused(tmp_path):
    used_once(tmp_path)
    path = approval(tmp_path, "new01")
    G.grant_allowance(path, now=lambda: NOW)
    before = ledger_path(M2).read_bytes()
    G.grant_allowance(path, now=lambda: NOW)
    assert ledger_path(M2).read_bytes() == before  # resumable preparation never appends or refills twice
    with pytest.raises(G.ApprovalRefused, match="approval_invalid"):
        G.make_approval("new03", [M2], 2, {"a": 10, "b_model": 0, "kosis": 0},
                        (NOW + timedelta(hours=1)).isoformat(), tmp_path / "runs",
                        allowance={"slots": {"a_first": 2, "a_self_rejudge": 1}})


def test_a_draft_approval_grants_nothing(tmp_path):
    path = approval(tmp_path, "new01")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["live_calls"] = False
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(G.ApprovalRefused, match="approval_not_approved"):
        G.grant_allowance(path, now=lambda: NOW)
    assert not (G.live_root() / "a_questions").exists()


def test_the_ledger_refuses_an_allowance_reservation_by_itself_too():
    led = Ledger.memory(key_of(M2))
    led.reserve("a_first", "m", "x", approval_ref="old01")
    versions = {"contract": "c"}
    ctx = {"id": "new01", "versions": versions, "now": NOW.isoformat()}
    with pytest.raises(LedgerError, match="allowance_missing"):
        led.reserve("a_first", "m", "y", approval_ref="new01", allowance=ctx)
    led.grant("new01", dict(SLOTS), versions, (NOW + timedelta(hours=1)).isoformat(), approved_by="owner",
              approved_at=NOW.isoformat(), approval_sha256="x")
    led.reserve("a_first", "m", "y", approval_ref="new01", allowance=ctx)
    with pytest.raises(LedgerError, match="allowance_slot_used"):
        led.reserve("a_first", "m", "z", approval_ref="new01", allowance=ctx)
    with pytest.raises(LedgerError, match="slot_used"):
        led.reserve("a_first", "m", "z", approval_ref="old02")  # the service path stays at its limit
