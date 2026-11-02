"""Plan 56 order 13-1: the screen's live A calls stay off by default and open only by an approval record, within its
questions and limits. A fake transport counts what it is asked to send (no network, no key: ``.env`` and credentials
are never read). Its answers are made-up wiring answers and say nothing about a model."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from claview_a.budget import Ledger, key_of
from claview_a.contract import CONTRACT_VERSION
from claview_a.transport.provider import ProviderError
from claview_a_api import live_gate as G
from claview_a_api import screen as S
from claview_a_api.run_record import RunRecordError, RunStore
from claview_a_api.synthetic_judges import card_index, complete

AS_OF = "2026-10-22"
M2 = "M2 통화량 2023년 연간으로 보여줘"
OTHER = "M2 통화량 2024년 연간으로 보여줘"
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def chosen(payload):
    """The made-up answer: the M2 table of its card, quoted as the question quotes it (current contract)."""
    ref, name = card_index(payload["cards"])["DT_161Y014"]
    out = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
           "focuses": [{"for": ["s1"], "text": "M2 통화량", "quotes": ["M2 통화량"]}],
           # the live path reads the current contract: its structured period and frequency fields (contract .8)
           "periods": [{"quote": payload["question"][7:12], "for": ["s1"], "meaning": "range", "refers_to": [],
                        "form": "absolute", "start": payload["question"][7:11], "end": "same", "count": "none",
                        "unit": "none", "read": "ok"}],
           "frequencies": [{"quote": "연간", "for": ["s1"], "value": "Y", "anchor": "none"}],
           "choices": [{"for": ["s1"], "table": ref, "evidence": [{"kind": "title", "text": name}]}]}
    return complete(out, payload, CONTRACT_VERSION)


def missing(payload):
    """A first answer that finds no table: the one re-search and its re-judgment follow."""
    out = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
           "focuses": [{"for": ["s1"], "text": "M2 통화량", "quotes": ["M2 통화량"]}],
           "missing": [{"for": ["s1"], "search_words": ["M2 기관별"]}]}
    return complete(out, payload, CONTRACT_VERSION)


def body(text):
    return json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


class FakeTransport:
    """Counts each request it is asked to send; ``plan`` answers each send in turn (a maker or an error)."""

    kind = "live"
    made = 0

    def __init__(self, plan):
        type(self).made += 1
        self.plan, self.sent = list(plan), []

    def preflight(self):
        pass

    def send(self, request):
        self.sent.append(request["model"])
        step = self.plan.pop(0) if self.plan else chosen
        if isinstance(step, Exception):
            raise step
        return body(json.dumps(step(json.loads(request["input"])), ensure_ascii=False))


@pytest.fixture(autouse=True)
def _no_key_no_setting(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "LIVE_ROOT", tmp_path / "live")  # the machine's live ledger, here a temporary folder
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(G.APPROVAL_ENV, raising=False)
    FakeTransport.made = 0
    FACTORY_CALLS.clear()


def approval(tmp_path, approval_id="appr01", questions=(M2, OTHER), per_question_a=2, caps=None, live=True,
             hours=2, run_root=None, **change):
    record = G.make_approval(approval_id, list(questions), per_question_a,
                             caps or {"a": 10, "b_model": 0, "kosis": 0}, (NOW + timedelta(hours=hours)).isoformat(),
                             run_root or tmp_path / "runs")
    record["live_calls"] = live
    record.update(change)
    path = tmp_path / f"{approval_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


FACTORY_CALLS = []


def live(store, path, transport, now=NOW):
    def factory():
        FACTORY_CALLS.append(1)
        return transport

    return S.live_judges(store, approval_path=str(path), transport_factory=factory, now=lambda: now)


def usage(approval_id="appr01"):
    return G.Usage(G.live_root() / "a_approvals" / approval_id)


def ledger_of(store, question):
    """The question's live ledger (real sends only), apart from the run records'."""
    with Ledger.open(G.live_root() / "a_questions" / key_of(question) / "ledger.json", key_of(question)) as led:
        return led.summary(), led.attempts()


def test_off_by_default_makes_no_transport_and_refuses(tmp_path):
    store = RunStore(tmp_path / "runs")
    with pytest.raises(S.LiveDisabled, match="a_live_disabled"):
        S.judges_for("live", store)
    with pytest.raises(S.LiveDisabled):
        S.live_judges(store, transport_factory=lambda: FakeTransport([]))
    assert FakeTransport.made == 0


def test_an_approved_question_sends_one_first_judgment(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([])
    judges = live(store, approval(tmp_path), fake)
    out = S.select(M2, AS_OF, judges, store=store)
    assert fake.sent == ["gpt-6.1-terra"] and judges.requests == ["gpt-6.1-terra"]
    assert out["checkpoint"]["judges"] == "live" and out["checkpoint"]["a_gate"]["approval_id"] == "appr01"
    summary, attempts = ledger_of(store, M2)
    assert summary["by_slot"] == {"a_first": 1}
    ((a),) = attempts
    assert (a["sent"], a["billing"], a["approval_ref"], a["credential_ref"]) == ("yes", "billed", "appr01",
                                                                                 G.CREDENTIAL)
    assert usage().count("a") == 1
    assert [s["outcome"] for s in out["view"]["statistics"]] == ["selected"]


def test_a_re_judgment_sends_sol_once_and_only_when_the_approval_allows_it(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([missing])
    S.select(M2, AS_OF, live(store, approval(tmp_path), fake), store=store)
    assert fake.sent == ["gpt-6.1-terra", "gpt-6.1-sol"]
    assert ledger_of(store, M2)[0]["by_slot"] == {"a_first": 1, "a_self_rejudge": 1}

    fake = FakeTransport([missing])
    judges = live(store, approval(tmp_path, "appr02", per_question_a=1), fake)
    out = S.select(OTHER, AS_OF, judges, store=store)
    assert fake.sent == ["gpt-6.1-terra"]  # the approval allows one A call for the question: no Sol
    assert out["checkpoint"]["a_gate"]["refusals"][0]["code"] == "question_a_limit_reached"
    assert out["checkpoint"]["a_state"]["research"]["end"] == "rejudge_call_limit"


@pytest.mark.parametrize("case, code", [
    ("no_file", "approval_missing"), ("broken", "approval_invalid"), ("not_approved", "approval_not_approved"),
    ("old_version", "approval_stale"), ("old_conditions", "approval_stale"), ("expired", "approval_expired"),
])
def test_an_unusable_approval_is_refused_before_any_transport(tmp_path, case, code):
    store = RunStore(tmp_path / "runs")
    if case == "no_file":
        path = tmp_path / "none.json"
    elif case == "broken":
        path = approval(tmp_path)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["per_question_a"] = 3  # beyond Terra 1 and Sol 1
        path.write_text(json.dumps(record), encoding="utf-8")
    elif case == "not_approved":
        path = approval(tmp_path, live=False)
    elif case == "old_version":
        path = approval(tmp_path, version="a-screen-approval/2026-10-31.1")
    elif case == "old_conditions":
        record = json.loads(approval(tmp_path).read_text(encoding="utf-8"))
        record["conditions"]["versions"]["check"] = "tl-check-2026-10-31.31"  # made for the code before
        path = tmp_path / "old.json"
        path.write_text(json.dumps(record), encoding="utf-8")
    else:
        path = approval(tmp_path, hours=-1)
    fake = FakeTransport([])
    with pytest.raises(G.ApprovalRefused) as e:
        live(store, path, fake)
    assert e.value.code == code and FACTORY_CALLS == [] and fake.sent == []


def test_a_question_outside_the_list_and_a_used_cap_send_nothing(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([])
    with pytest.raises(G.ApprovalRefused) as e:
        S.select("예금은행 대출금리 2024년 연간", AS_OF, live(store, approval(tmp_path), fake), store=store)
    assert e.value.code == "question_not_approved" and fake.sent == [] and FACTORY_CALLS == []
    path = approval(tmp_path, "appr03", caps={"a": 1, "b_model": 0, "kosis": 0})
    S.select(M2, AS_OF, live(store, path, fake), store=store)
    with pytest.raises(G.ApprovalRefused) as e:
        S.select(OTHER, AS_OF, live(store, path, fake), store=store)
    assert e.value.code == "run_a_cap_reached" and fake.sent == ["gpt-6.1-terra"]
    assert ledger_of(store, OTHER)[0]["total"] == 0  # nothing reserved for the refused question


def test_the_approval_expiring_during_a_run_refuses_the_next_call(tmp_path):
    clock = {"now": NOW}

    def expire_then_missing(payload):  # the approval expires while the first judgment is out
        clock["now"] = NOW + timedelta(hours=3)
        return missing(payload)

    store, fake = RunStore(tmp_path / "runs"), FakeTransport([expire_then_missing])
    judges = S.live_judges(store, approval_path=str(approval(tmp_path)), transport_factory=lambda: fake,
                           now=lambda: clock["now"])
    out = S.select(M2, AS_OF, judges, store=store)
    assert fake.sent == ["gpt-6.1-terra"]  # the re-judgment came after the expiry: not sent
    assert [r["code"] for r in out["checkpoint"]["a_gate"]["refusals"]] == ["approval_expired"]


@pytest.mark.parametrize("error, sent", [(ProviderError("http_500", "response"), "yes"),
                                         (ProviderError("transport", "send"), "unknown")])
def test_a_500_or_a_timeout_counts_as_used_and_is_never_sent_again(tmp_path, error, sent):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([error])
    out = S.select(M2, AS_OF, live(store, approval(tmp_path), fake), store=store)
    assert fake.sent == ["gpt-6.1-terra"] and out["view"]["failure"]["stage"] == "judge"
    summary, (a,) = ledger_of(store, M2)
    assert summary["by_slot"] == {"a_first": 1} and a["sent"] == sent
    assert usage().count("a") == 1
    with pytest.raises(RunRecordError, match="question_budget_used"):  # a new session never sends it again
        S.select(M2, AS_OF, live(store, approval(tmp_path), fake), store=store)
    assert fake.sent == ["gpt-6.1-terra"]


def test_a_401_halts_every_later_call_of_the_approval(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([ProviderError("http_401", "response")])
    path = approval(tmp_path)
    S.select(M2, AS_OF, live(store, path, fake), store=store)
    with pytest.raises(G.ApprovalRefused) as e:  # another approved question, a new process: refused before sending
        S.select(OTHER, AS_OF, live(store, path, fake), store=store)
    assert e.value.code == "credential_halted" and fake.sent == ["gpt-6.1-terra"]


def test_the_question_budget_goes_on_across_sessions_rewording_and_a_new_approval(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([])
    first = S.select(M2, AS_OF, live(store, approval(tmp_path), fake), store=store)
    sid = first["checkpoint"]["session"]["session_id"]
    for question, lineage in ((M2, None), ("M2 통화량을 2023년 연간으로", {"session_id": sid})):
        with pytest.raises(RunRecordError, match="question_budget_used"):
            S.select(question, AS_OF, live(store, approval(tmp_path, "appr09"), fake), store=store, lineage=lineage)
    assert fake.sent == ["gpt-6.1-terra"]


def test_synthetic_judgments_never_use_the_live_ledger(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([])
    S.select(M2, AS_OF, S.synthetic_judges(), store=store)  # the run records' own ledger
    with pytest.raises(RunRecordError, match="question_budget_used"):  # kept there as before (order 10)
        S.select(M2, AS_OF, S.synthetic_judges(), store=store)
    S.select(M2, AS_OF, live(store, approval(tmp_path), fake), store=store)
    assert fake.sent == ["gpt-6.1-terra"] and ledger_of(store, M2)[0]["by_slot"] == {"a_first": 1}


def test_another_folder_never_starts_a_question_again(tmp_path):
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([])
    old = approval(tmp_path)
    S.select(M2, AS_OF, live(store, old, fake), store=store)
    other = RunStore(tmp_path / "runs-new")
    with pytest.raises(G.ApprovalRefused, match="run_root_mismatch"):  # the approval names its own folder
        live(other, old, fake)
    new = approval(tmp_path, "appr05", run_root=other.root)  # a new approval for the new folder
    with pytest.raises(RunRecordError, match="question_budget_used"):  # the live ledger goes on
        S.select(M2, AS_OF, live(other, new, fake), store=other)
    assert fake.sent == ["gpt-6.1-terra"] and ledger_of(store, M2)[0]["total"] == 1


def test_an_approval_for_another_live_ledger_is_refused(tmp_path):
    path = approval(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["ledger_root"] = str(tmp_path / "elsewhere")
    path.write_text(json.dumps(record), encoding="utf-8")
    fake = FakeTransport([])
    with pytest.raises(G.ApprovalRefused, match="ledger_root_mismatch"):
        live(RunStore(tmp_path / "runs"), path, fake)
    assert fake.sent == [] and FACTORY_CALLS == []


def test_live_judges_never_go_without_the_run_records(tmp_path):
    with pytest.raises(G.ApprovalRefused, match="run_record_required"):
        live(None, approval(tmp_path), FakeTransport([]))


class KeyMissingTransport(FakeTransport):
    """A transport whose key is not there: it fails before anything leaves the process (nothing is sent)."""

    def send(self, request):
        raise ProviderError("key_unavailable", "before_send")


def test_a_failure_before_sending_uses_no_slot_and_no_count(tmp_path):
    # Codex review 11-01 ⑤: sent no used to leave usage a=1 and a_first=1, so the question was blocked for good
    store, path = RunStore(tmp_path / "runs"), approval(tmp_path)
    out = S.select(M2, AS_OF, live(store, path, KeyMissingTransport([])), store=store)
    assert out["view"]["failure"]["stage"] == "judge"
    summary, (a,) = ledger_of(store, M2)
    assert (a["sent"], a["outcome"]) == ("no", "not_sent") and summary["by_slot"] == {"a_first": 0}
    assert usage().count("a") == 0
    fake = FakeTransport([])  # the same question may be judged once the key is there
    S.select(M2, AS_OF, live(store, path, fake), store=store)
    assert fake.sent == ["gpt-6.1-terra"] and ledger_of(store, M2)[0]["by_slot"] == {"a_first": 1}
    assert usage().count("a") == 1


def test_a_refusal_right_before_the_send_uses_no_slot(tmp_path):
    # the approval expires between the reservation and the send: refused as never sent, the slot stays unused
    store, fake = RunStore(tmp_path / "runs"), FakeTransport([])
    ticks = iter([NOW, NOW, NOW])  # loading the approval, admit, the check before the reservation; then the send

    def clock():
        return next(ticks, NOW + timedelta(hours=3))

    judges = S.live_judges(store, approval_path=str(approval(tmp_path)), transport_factory=lambda: fake, now=clock)
    out = S.select(M2, AS_OF, judges, store=store)
    assert fake.sent == [] and out["view"]["failure"]["stage"] == "judge"
    summary, (a,) = ledger_of(store, M2)
    assert (a["sent"], a["outcome"], a["provider_code"]) == ("no", "not_sent", "approval_expired")
    assert summary["by_slot"] == {"a_first": 0} and usage().count("a") == 0
