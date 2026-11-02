"""Plan 56 order 3 (bundle 8, policy ⑧ and C9's A part): the call ledger and the guard right before a send.

L-01..09 on the ledger, the live judge and the graph, with fake transports (no network): a question solved by its
first judgment uses no second slot; a second re-judgment is refused before sending; a failed send counts and is
never sent again; a reservation never settled counts as used with billing unknown; a 401/403 halts the credential
(the next send is refused before the transport, 500 and a timeout do not halt); B's slots stay closed while B's request
model is undecided (one bundle per statistic); a missing ledger is never created by a read and a held one is busy; a
failed first send never opens the re-judgment while an output error is told apart as correctable. The package has no
retry loop.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import fill_option_evidence  # noqa: E402
from test_explicit_conditions_53 import M2, STOCK, output, ref

from claview_a.budget import BudgetPolicy, Ledger, LedgerError, key_of
from claview_a.cards import build_cards
from claview_a.contract import Settings
from claview_a.graph import JudgeError, run
from claview_a.judges import Archive, GuardedTransport, LiveJudge
from claview_a.pool import build_pool
from claview_a.transport.provider import ProviderError

AS_OF = "2026-10-22"
SETTINGS = Settings(model="synthetic-model", effort="low", max_output_tokens=4096)
Q = "월말 잔액 기준의 광의통화를 상품별로 원계열로 보여줘"


def raw(text: str, usage=None) -> str:
    return json.dumps({"status": "completed", "usage": usage,
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]},
                      ensure_ascii=False)


class Transport:
    """A fake transport: ``answers`` in order, a ``ProviderError`` item is raised instead of answered."""

    kind = "live"

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def preflight(self):
        pass

    def send(self, request):
        self.sent.append(request["model"])
        a = self.answers.pop(0) if self.answers else self.answers_last
        if isinstance(a, Exception):
            raise a
        return a

    answers_last = None


def answer_text(layer, question=Q, seasonal="원계열"):
    cards = build_cards(question, build_pool(question, layer), layer)
    r, _, card = ref(cards, M2, layer, seasonal=seasonal, stock="말잔")
    return json.dumps(fill_option_evidence(output([("s1", "광의통화")], [STOCK], [("s1", r, card["name"])]), cards),
                      ensure_ascii=False)


def missing_text():
    return json.dumps({"statistics": [{"id": "s1", "quote": "광의통화", "name": "광의통화"}], "splitters": [],
                       "periods": [], "frequencies": [], "calculations": [], "choices": [], "asks": [],
                       "missing": [{"for": ["s1"], "search_words": ["광의통화 상품별"]}], "alternatives": []},
                      ensure_ascii=False)


def judges(tmp_path, transport, ledger, cred="key-1"):
    archive = Archive(tmp_path / "calls.jsonl")
    ref_ = {"case_id": "c1"}
    guarded = GuardedTransport(transport, ledger, cred)
    first = LiveJudge(guarded, 10, archive, ref_, ledger=ledger, slot="a_first", credential_ref=cred)
    second = LiveJudge(guarded, 10, archive, ref_, ledger=ledger, slot="a_self_rejudge", credential_ref=cred)
    # the transport's fixed answers are of contract .3 (plan 56 order 6-3b: a live judge names the current one)
    first.answer_contract = second.answer_contract = "tl-contract-2026-10-28.3"
    return first, second, archive


# ---------------------------------------------------------------- L-01, L-02


def test_l01_a_question_solved_by_its_first_judgment_uses_no_second_slot(tmp_path, layer):
    ledger = Ledger.memory(key_of(Q))
    t = Transport(raw(answer_text(layer), usage={"input_tokens": 1}))
    first, second, archive = judges(tmp_path, t, ledger)
    state = run(Q, layer, first, SETTINGS, AS_OF, research=True, research_judge=second, ledger=ledger)
    assert state["action"] != "fail" and t.sent == ["synthetic-model"]
    assert ledger.summary()["by_slot"] == {"a_first": 1}
    (row,) = archive.rows
    assert (row["slot"], row["sent"], row["billing"]) == ("a_first", "yes", "billed")


def test_l02_a_second_rejudgment_is_refused_before_it_is_sent(tmp_path, layer):
    ledger = Ledger.memory(key_of(Q))
    t = Transport(raw(missing_text()), raw(missing_text()))
    first, second, _ = judges(tmp_path, t, ledger)
    state = run(Q, layer, first, SETTINGS, AS_OF, research=True, research_judge=second, ledger=ledger)
    assert ledger.summary()["by_slot"] == {"a_first": 1, "a_self_rejudge": 1} and len(t.sent) == 2
    assert state["research"]["status"] == "rejudged" or state["research"]["end"] in ("still_missing", "rejudged")
    assert not ledger.available("a_self_rejudge")
    with pytest.raises(LedgerError) as e:
        second({}, SETTINGS.request({"question": Q, "cards": "", "unresolved": []}))
    assert e.value.code == "slot_used" and len(t.sent) == 2


def test_l02_the_graph_reads_the_ledger_before_a_rejudgment(tmp_path, layer):
    """A ledger whose re-judgment is already used: the graph ends the re-search without a second call."""
    ledger = Ledger.memory(key_of(Q))
    ledger.settle(ledger.reserve("a_self_rejudge", "m", "x"), "yes", "answered", billing="billed")
    t = Transport(raw(missing_text()))
    first, second, _ = judges(tmp_path, t, ledger)
    state = run(Q, layer, first, SETTINGS, AS_OF, research=True, research_judge=second, ledger=ledger)
    assert len(t.sent) == 1 and state["research"]["end"] == "rejudge_call_limit"
    assert state["research"]["refusal"] == "slot_used"


# ---------------------------------------------------------------- L-03, L-04


def test_l03_a_failed_send_counts_and_is_never_sent_again(tmp_path, layer):
    ledger = Ledger.memory(key_of(Q))
    t = Transport(ProviderError("transport", "send"))
    first, second, archive = judges(tmp_path, t, ledger)
    state = run(Q, layer, first, SETTINGS, AS_OF, research=True, research_judge=second, ledger=ledger)
    assert len(t.sent) == 1 and ledger.used("a_first") == 1 and ledger.used("a_self_rejudge") == 0
    (a,) = ledger.attempts()
    assert (a["sent"], a["billing"], a["outcome"]) == ("unknown", "unknown", "failed")
    assert archive.rows[0]["outcome"] == "failed" and state["action"] == "fail"


def test_l04_a_reservation_never_settled_counts_as_used_with_billing_unknown(tmp_path):
    path = tmp_path / "q.ledger.jsonl"
    with Ledger.open(path, key_of(Q), create=True) as led:
        led.reserve("a_first", "m", "sha")  # the process stops before the settlement
    with Ledger.open(path, key_of(Q)) as led:
        assert led.used("a_first") == 1 and not led.available("a_first")
        (a,) = led.attempts()
        assert (a["settled"], a["sent"], a["billing"]) == (False, "unknown", "unknown")
        assert led.summary()["unsettled"] == 1


# ---------------------------------------------------------------- L-05


@pytest.mark.parametrize("code", ["http_401", "http_403"])
def test_l05_a_key_refusal_halts_the_credential_before_the_next_send(tmp_path, code):
    ledger = Ledger.memory(key_of(Q), BudgetPolicy(a_first=5))
    t = Transport(ProviderError(code, "response"), raw("{}"))
    first, _, _ = judges(tmp_path, t, ledger)
    request = SETTINGS.request({"question": Q, "cards": "", "unresolved": []})
    with pytest.raises(JudgeError):
        first({}, request)
    with pytest.raises(LedgerError) as e:
        first({}, request)
    assert e.value.code == "credential_halted" and len(t.sent) == 1
    assert ledger.summary()["halted"] == {"key-1": code} and ledger.used("a_first") == 1
    assert ledger.attempts()[0]["billing"] == "none_reported"


@pytest.mark.parametrize("error", [ProviderError("http_500", "response"), ProviderError("transport", "send")])
def test_l05_a_server_error_or_a_timeout_does_not_halt(tmp_path, error):
    ledger = Ledger.memory(key_of(Q), BudgetPolicy(a_first=5))
    t = Transport(error, error)
    first, _, _ = judges(tmp_path, t, ledger)
    request = SETTINGS.request({"question": Q, "cards": "", "unresolved": []})
    for _ in range(2):  # each call is its own attempt: no automatic resend, each counted
        with pytest.raises(JudgeError):
            first({}, request)
    assert len(t.sent) == 2 and ledger.used("a_first") == 2 and ledger.summary()["halted"] == {}


# ---------------------------------------------------------------- L-07, L-08


def test_l07_b_slots_stay_closed_while_b_request_model_is_undecided():
    ledger = Ledger.memory(key_of(Q))
    assert not ledger.available("b_first:s1")
    with pytest.raises(LedgerError) as e:
        ledger.reserve("b_first:s1", "b-model", "sha")
    assert e.value.code == "b_request_model_undecided" and ledger.attempts() == []


def test_l07_one_statistic_is_one_b_bundle():
    """The period and the frequency of one statistic are one bundle: one first B call for both."""
    ledger = Ledger.memory(key_of(Q), BudgetPolicy(b_request_model="b-model"))
    ledger.reserve("b_first:s1", "b-model", "sha-period-and-frequency")
    with pytest.raises(LedgerError) as e:
        ledger.reserve("b_first:s1", "b-model", "sha-again")
    assert e.value.code == "slot_used" and ledger.available("b_first:s2")


def test_l07_the_total_per_question_is_recorded_never_refused():
    ledger = Ledger.memory(key_of(Q), BudgetPolicy(total_per_question=1, b_request_model="b"))
    ledger.reserve("a_first", "m", "1")
    ledger.reserve("b_first:s1", "b", "2")
    s = ledger.summary()
    assert s["total"] == 2 and s["total_cap"] == 1 and s["over_total"] is True


def test_l08_a_missing_ledger_is_never_created_by_a_read(tmp_path):
    path = tmp_path / "absent.jsonl"
    with pytest.raises(LedgerError) as e:
        Ledger.open(path, key_of(Q))
    assert e.value.code == "ledger_missing" and not path.exists()


def test_l08_a_held_ledger_is_busy(tmp_path):
    path = tmp_path / "q.jsonl"
    with Ledger.open(path, key_of(Q), create=True):
        with pytest.raises(LedgerError) as e:
            Ledger.open(path, key_of(Q))
        assert e.value.code == "ledger_busy"
    with Ledger.open(path, key_of(Q)):  # released on close
        pass


def test_l08_a_ledger_of_another_question_is_refused(tmp_path):
    path = tmp_path / "q.jsonl"
    with Ledger.open(path, key_of(Q), create=True):
        pass
    with pytest.raises(LedgerError) as e:
        Ledger.open(path, key_of("다른 질문"))
    assert e.value.code == "question_key_mismatch"


# ---------------------------------------------------------------- L-09


def test_l09_a_failed_first_send_never_opens_the_rejudgment(tmp_path, layer):
    ledger = Ledger.memory(key_of(Q))
    t = Transport(ProviderError("http_500", "response"))
    first, second, _ = judges(tmp_path, t, ledger)
    state = run(Q, layer, first, SETTINGS, AS_OF, research=True, research_judge=second, ledger=ledger)
    assert state["research"]["end"] == "first_round_error" and len(t.sent) == 1
    assert state["research"]["first_round"] == {"kind": "first_round_transport_error", "code": "transport",
                                                "correctable": False}
    assert ledger.used("a_self_rejudge") == 0 and ledger.available("a_self_rejudge")


def test_l09_an_output_error_is_told_apart_as_correctable(tmp_path, layer):
    ledger = Ledger.memory(key_of(Q))
    t = Transport(raw('{"statistics": "not a list"}'), raw('{"statistics": "still not a list"}'))
    first, second, _ = judges(tmp_path, t, ledger)
    state = run(Q, layer, first, SETTINGS, AS_OF, research=True, research_judge=second, ledger=ledger)
    fr = state["research"]["first_round"]
    assert fr["kind"] == "first_round_output_error" and fr["correctable"] is True
    # Codex review 11-01 ③ (owner 08:54): the correctable first answer gets the one recovery (before: the slot stayed
    # unused and one send); a second broken answer leaves the error and there is no third judgment
    assert not ledger.available("a_self_rejudge") and len(t.sent) == 2 and state["record"].get("error")


# ---------------------------------------------------------------- the package has no retry loop

RETRY_LOOP = re.compile(r"\bfor\s+\w+\s+in\s+range\([^)]*(retr|attempt)|\bwhile\b.*\b(retr|attempt|send)\b",
                        re.IGNORECASE)


def test_no_retry_loop_in_the_a_package():
    """claview_a here; claview_a_api is read by its own suite (tests_api/test_no_retry_api_56.py)."""
    root = Path(__file__).resolve().parents[1]
    hits = [f"{p.relative_to(root)}:{n}" for p in (root / "claview_a").rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if RETRY_LOOP.search(line)]
    assert hits == []
    assert BudgetPolicy().transport_retries == 0
