"""Deployment live A, tested only with temporary records and fake transports."""

import json

import pytest
from fastapi.testclient import TestClient

from claview_a.budget import BudgetPolicy, LedgerError, key_of
from claview_a.contract import CONTRACT_VERSION
from claview_a.graph import JudgeError
from claview_a.judges import BudgetExceeded
from claview_a.transport.provider import ProviderError
from claview_a_api import http as H
from claview_a_api import live_gate as G
from claview_a_api import runtime as R
from claview_a_api.api import app
from claview_a_api.run_record import RunRecordError, RunStore
from claview_a_api.synthetic_judges import complete

QUERY = "M2 통화량을 월별로 보여줘"
AS_OF = "2026-10-29"


def answer(payload):
    out = {
        "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
        "focuses": [{"for": ["s1"], "text": "M2 통화량", "quotes": ["M2 통화량"]}],
        "periods": [{"quote": "M2 통화량", "for": ["s1"], "meaning": "not_stated", "refers_to": [],
                     "form": "none", "start": "none", "end": "none", "count": "none", "unit": "none",
                     "read": "ok"}],
        "frequencies": [{"quote": "월별", "for": ["s1"], "value": "M", "anchor": "none"}],
        "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
    }
    return complete(out, payload, CONTRACT_VERSION)


def response(text="{}"):
    return json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


class Transport:
    kind = "live"

    def __init__(self, failure=None):
        self.failure, self.sent = failure, []

    def send(self, request):
        self.sent.append(request)
        if self.failure:
            raise self.failure
        payload = json.loads(request.get("input", "{}"))
        return response(json.dumps(answer(payload), ensure_ascii=False)) if "cards" in payload else response()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(G.APPROVAL_ENV, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CLAVIEW_A_RUN_ROOT", str(tmp_path / "deployment"))
    monkeypatch.setenv("CLAVIEW_MODE", "live")
    monkeypatch.setattr(G, "LIVE_ROOT", tmp_path / "evaluation")
    H.get_store.cache_clear()
    yield
    H.get_store.cache_clear()
    app.dependency_overrides.clear()


def test_http_live_without_evaluation_approval_records_answer_and_restart_budget(monkeypatch):
    transport = Transport()
    monkeypatch.setattr(R, "OpenAITransport", lambda: transport)
    with TestClient(app) as client:
        initial = client.post("/a/select", json={"contract_version": "a-http/2026-11-02.1",
                                                "query": QUERY, "as_of": AS_OF})
        assert initial.status_code == 200, initial.text
        body = initial.json()
        assert body["execution_mode"] == "live" and body["meta"]["llm_calls"] == 1
        view = body["view"]
        assert view["status"] == "needs_answer"
        sid = view["session"]["session_id"]
        question, = view["questions"]
        root = H.get_store().root
        assert root.name == "live"
        H.get_store.cache_clear()
        done = client.post("/a/answer", json={"contract_version": "a-http/2026-11-02.1", "session_id": sid,
            "replies": [{"question_id": question["question_id"], "question_version": question["question_version"],
                         "text": "2024년"}]})
        assert done.status_code == 200, done.text
        assert done.json()["view"]["status"] == "ready" and done.json()["meta"]["llm_calls"] == 0
        assert done.json()["view"]["session"]["as_of"] == AS_OF
        assert client.get(f"/a/records/{sid}").json()["execution_mode"] == "live"
        repeated = client.post("/a/select", json={"contract_version": "a-http/2026-11-02.1",
                                                 "query": QUERY, "as_of": AS_OF})
        assert repeated.status_code == 403 and repeated.json()["detail"] == "question_budget_used"
    assert len(transport.sent) == 1
    rows = [json.loads(line) for line in R.ServiceUsage(root).calls_path(key_of(QUERY)).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["raw_response"] and rows[0]["sent"] == "yes"


def test_explicit_evaluation_approval_never_falls_back_to_service(monkeypatch, tmp_path):
    monkeypatch.setenv(G.APPROVAL_ENV, str(tmp_path / "missing.json"))
    made = []
    with pytest.raises(G.ApprovalRefused):
        R.service_live_judges(R.service_store(), transport_factory=lambda: made.append(1))
    assert made == []
    assert R.service_store().root == (tmp_path / "deployment").resolve()


def test_synthetic_and_live_keep_separate_ledgers(monkeypatch):
    assert R.service_store("synthetic").root != R.service_store("live").root
    monkeypatch.setenv("CLAVIEW_MODE", "synthetic")
    transport = Transport()
    monkeypatch.setattr(R, "OpenAITransport", lambda: transport)
    assert H.get_judges(None, R.service_store()).kind == "synthetic"
    assert transport.sent == []


@pytest.mark.parametrize("code", ["http_401", "http_403"])
def test_auth_halt_survives_other_question_and_restart(tmp_path, code):
    store = RunStore(tmp_path / "live")
    transport = Transport(ProviderError(code, "response"))
    judges = R.service_live_judges(store, lambda: transport)
    with store.ledger(key_of(QUERY), BudgetPolicy()) as ledger:
        first, recovery = judges.bind(ledger, key_of(QUERY))
        with pytest.raises(JudgeError):
            first({}, {"model": "test"})
        with pytest.raises(LedgerError, match="credential_halted"):
            recovery({}, {"model": "test"})
        assert ledger.used("a_self_rejudge") == 0
    assert R.credential_halted(judges) == code
    with pytest.raises(LedgerError, match="credential_halted"):
        R.service_live_judges(RunStore(store.root), lambda: transport)
    assert len(transport.sent) == 1


def test_first_and_recovery_keep_one_slot_each(tmp_path):
    store = RunStore(tmp_path / "live")
    transport = Transport()
    judges = R.service_live_judges(store, lambda: transport)
    with store.ledger(key_of(QUERY), BudgetPolicy()) as ledger:
        first, recovery = judges.bind(ledger, key_of(QUERY))
        first({}, {"model": "test"})
        recovery({}, {"model": "test"})
        with pytest.raises(BudgetExceeded):
            recovery({}, {"model": "test"})
    fresh = R.service_live_judges(RunStore(store.root), lambda: transport)
    with store.ledger(key_of(QUERY), BudgetPolicy()) as ledger:
        first, recovery = fresh.bind(ledger, key_of(QUERY))
        with pytest.raises(LedgerError, match="slot_used"):
            first({}, {"model": "test"})
        with pytest.raises(LedgerError, match="slot_used"):
            recovery({}, {"model": "test"})
    assert len(transport.sent) == 2


def test_missing_key_never_opens_connection_or_consumes_slot(tmp_path, monkeypatch):
    import http.client

    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be opened")

    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    store = RunStore(tmp_path / "live")
    judges = R.service_live_judges(store)
    with store.ledger(key_of(QUERY), BudgetPolicy()) as ledger:
        first, _ = judges.bind(ledger, key_of(QUERY))
        with pytest.raises(JudgeError, match="key_unavailable"):
            first({}, {"model": "test"})
        assert ledger.used("a_first") == 0
    assert R.sent_for(judges, QUERY) == 0 and R.credential_halted(judges) is None


def test_missing_key_is_rejected_before_an_a_session_is_created(tmp_path):
    store = RunStore(tmp_path / "live")
    judges = R.service_live_judges(store)
    with pytest.raises(ProviderError, match="key_unavailable"):
        from claview_a_api import screen

        screen.select(QUERY, AS_OF, judges, store=store)
    assert not list(store.root.glob("a_sessions/*/record.json"))
    with store.ledger(key_of(QUERY), BudgetPolicy()) as ledger:
        assert ledger.used("a_first") == 0


def test_corrupt_auth_halt_refuses_before_transport_creation(tmp_path):
    store = RunStore(tmp_path / "live")
    path = store.root / "service_live" / "credential_halt.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RunRecordError, match="run_record_invalid"):
        R.service_live_judges(store, lambda: pytest.fail("no transport"))


def test_send_lock_survives_as_a_file_but_does_not_stay_locked(tmp_path):
    usage = R.ServiceUsage(tmp_path)
    with usage.sending():
        with pytest.raises(ProviderError, match="a_service_busy") as refused:
            with R.ServiceUsage(tmp_path).sending():
                pytest.fail("a concurrent sender must be refused")
        assert refused.value.phase == "before_send"
    assert (usage.folder / "send.lock").exists()
    with R.ServiceUsage(tmp_path).sending():
        pass


def test_http_explicit_bad_approval_is_a_refusal_not_a_service_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv(G.APPROVAL_ENV, str(tmp_path / "missing.json"))
    monkeypatch.setattr(R, "OpenAITransport", lambda: pytest.fail("no service transport"))
    with pytest.raises(H.HTTPException) as refused:
        H.get_judges(None, R.service_store())
    assert refused.value.status_code == 403 and refused.value.detail == "approval_missing"
