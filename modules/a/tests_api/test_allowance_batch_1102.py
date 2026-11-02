"""Atomic activation and resumable append-only preparation of a re-measurement allowance.

Every ledger and approval is synthetic and temporary. A failing second write must not activate the first grant.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from test_allowance_1102 import M2, NOW, OTHER, FakeTransport, approval, ledger_path, live, store_of

from claview_a.budget import Ledger
from claview_a_api import live_gate as G
from claview_a_api import screen as S


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "LIVE_ROOT", tmp_path / "live")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(G.APPROVAL_ENV, raising=False)


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def grants(question):
    path = ledger_path(question)
    return [row for row in records(path) if row["kind"] == "allow"] if path.exists() else []


def marker(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    return G.live_root() / "a_approvals" / record["approval_id"] / "allowance_batch.json"


def test_interrupted_preparation_is_inactive_and_resumes_only_missing_grants(tmp_path, monkeypatch):
    path = approval(tmp_path, "batch01", questions=(M2, OTHER))
    append = Ledger._append

    def fail_second(ledger, row):
        if row["kind"] == "allow" and ledger.question_key == S.key_of(OTHER):
            raise OSError("synthetic second-grant failure")
        append(ledger, row)

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, "_append", fail_second)
        with pytest.raises(OSError, match="second-grant"):
            G.grant_allowance(path, now=lambda: NOW)
    first_before = ledger_path(M2).read_bytes()
    assert [len(grants(q)) for q in (M2, OTHER)] == [1, 0]
    fake = FakeTransport([])
    with pytest.raises(G.ApprovalRefused, match="allowance_missing"):
        S.select(M2, "2026-10-29", live(store_of(tmp_path, "batch01"), path, fake),
                 store=store_of(tmp_path, "batch01"))
    assert fake.sent == []
    assert json.loads(marker(path).read_text(encoding="utf-8"))["status"] == "pending"
    assert G.grant_allowance(path, now=lambda: NOW) == [S.key_of(q) for q in (M2, OTHER)]
    assert ledger_path(M2).read_bytes() == first_before
    assert [len(grants(q)) for q in (M2, OTHER)] == [1, 1]
    assert json.loads(marker(path).read_text(encoding="utf-8"))["status"] == "committed"
    S.select(M2, "2026-10-29", live(store_of(tmp_path, "batch01"), path, fake),
             store=store_of(tmp_path, "batch01"))
    assert fake.sent == ["gpt-6.1-terra"]
    spent = ledger_path(M2).read_bytes()
    G.grant_allowance(path, now=lambda: NOW)
    assert ledger_path(M2).read_bytes() == spent


def test_commit_write_failure_leaves_every_grant_inactive_and_is_resumable(tmp_path, monkeypatch):
    path = approval(tmp_path, "batch02", questions=(M2, OTHER))
    write = G._write_allowance_batch

    def fail_commit(file, identity, status):
        if status == "committed":
            raise OSError("synthetic commit write failure")
        write(file, identity, status)

    with monkeypatch.context() as patch:
        patch.setattr(G, "_write_allowance_batch", fail_commit)
        with pytest.raises(OSError, match="commit write"):
            G.grant_allowance(path, now=lambda: NOW)
    assert [len(grants(q)) for q in (M2, OTHER)] == [1, 1]
    before = [ledger_path(q).read_bytes() for q in (M2, OTHER)]
    for q in (M2, OTHER):
        fake = FakeTransport([])
        with pytest.raises(G.ApprovalRefused, match="allowance_missing"):
            S.select(q, "2026-10-29", live(store_of(tmp_path, "batch02"), path, fake),
                     store=store_of(tmp_path, "batch02"))
        assert fake.sent == []
    G.grant_allowance(path, now=lambda: NOW)
    assert [ledger_path(q).read_bytes() for q in (M2, OTHER)] == before
    assert json.loads(marker(path).read_text(encoding="utf-8"))["status"] == "committed"


@pytest.mark.parametrize("change", ["approval_bytes", "slots", "question_keys", "valid_until", "versions"])
def test_a_different_identity_never_reuses_a_committed_batch(tmp_path, change):
    path = approval(tmp_path, "batch03")
    G.grant_allowance(path, now=lambda: NOW)
    record = json.loads(path.read_text(encoding="utf-8"))
    if change == "approval_bytes":
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(G.ApprovalRefused, match="approval_changed"):
            G.grant_allowance(path, now=lambda: NOW)
    else:
        data = json.loads(marker(path).read_text(encoding="utf-8"))
        data[change] = {"slots": {"a_first": 0, "a_self_rejudge": 1}, "question_keys": [S.key_of(OTHER)],
                        "valid_until": "2099-01-01T00:00:00+00:00", "versions": {"contract": "other"}}[change]
        marker(path).write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(G.ApprovalRefused, match="approval_changed"):
            G.allowance_batch_status(record, hashlib.sha256(path.read_bytes()).hexdigest())
    fake = FakeTransport([])
    with pytest.raises(G.ApprovalRefused, match="approval_changed"):
        S.select(M2, "2026-10-29", live(store_of(tmp_path, "batch03"), path, fake),
                 store=store_of(tmp_path, "batch03"))
    assert fake.sent == [] and len(grants(M2)) == 1


def test_duplicate_questions_are_rejected_before_any_allowance_write(tmp_path):
    path = approval(tmp_path, "batch04", questions=(M2, M2))
    with pytest.raises(G.ApprovalRefused, match="approval_invalid"):
        G.grant_allowance(path, now=lambda: NOW)
    assert not G.live_root().exists()


def test_a_damaged_commit_marker_is_refused_before_any_send(tmp_path):
    path = approval(tmp_path, "batch05")
    G.grant_allowance(path, now=lambda: NOW)
    marker(path).write_text("{broken", encoding="utf-8")
    fake = FakeTransport([])
    with pytest.raises(G.ApprovalRefused, match="approval_changed"):
        S.select(M2, "2026-10-29", live(store_of(tmp_path, "batch05"), path, fake),
                 store=store_of(tmp_path, "batch05"))
    assert fake.sent == []


def test_a_committed_batch_never_recreates_a_missing_ledger_grant(tmp_path):
    path = approval(tmp_path, "batch06")
    G.grant_allowance(path, now=lambda: NOW)
    file = ledger_path(M2)
    # Simulate damaged temporary evidence; production code must not refill it from the committed marker.
    kept = [row for row in records(file) if row["kind"] != "allow"]
    file.write_text("".join(json.dumps(row) + "\n" for row in kept), encoding="utf-8")
    before = file.read_bytes()
    with pytest.raises(G.ApprovalRefused, match="approval_changed"):
        G.grant_allowance(path, now=lambda: NOW)
    assert file.read_bytes() == before and grants(M2) == []


def test_a_changed_ledger_grant_is_refused_even_with_an_intact_commit_marker(tmp_path):
    path = approval(tmp_path, "batch07")
    G.grant_allowance(path, now=lambda: NOW)
    file = ledger_path(M2)
    rows = records(file)
    next(r for r in rows if r["kind"] == "allow")["approval_sha256"] = "0" * 64
    file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    fake = FakeTransport([])
    with pytest.raises(G.ApprovalRefused, match="approval_changed"):
        S.select(M2, "2026-10-29", live(store_of(tmp_path, "batch07"), path, fake),
                 store=store_of(tmp_path, "batch07"))
    assert fake.sent == []


def test_the_commit_marker_is_checked_again_immediately_before_sending(tmp_path, monkeypatch):
    path = approval(tmp_path, "batch08")
    G.grant_allowance(path, now=lambda: NOW)
    reserve = Ledger.reserve

    def reserve_then_remove_marker(ledger, *args, **kwargs):
        attempt = reserve(ledger, *args, **kwargs)
        marker(path).unlink()  # synthetic damage between the admission check and send
        return attempt

    monkeypatch.setattr(Ledger, "reserve", reserve_then_remove_marker)
    fake = FakeTransport([])
    judges = live(store_of(tmp_path, "batch08"), path, fake)
    out = S.select(M2, "2026-10-29", judges, store=store_of(tmp_path, "batch08"))
    assert fake.sent == []
    assert out["checkpoint"]["a_state"]["record"]["error"]
    assert judges.gate.refusals[-1]["code"] == "allowance_missing"
    with Ledger.open(ledger_path(M2), S.key_of(M2)) as ledger:
        assert ledger.used_under("a_first", "batch08") == 0
        assert ledger.attempts()[-1]["sent"] == "no"
