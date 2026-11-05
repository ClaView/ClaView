"""The versioned HTTP API uses the production screen flow and server-held records.

Only scripted judges, temporary RunStores and TestClient requests are used.
Only the versioned AView HTTP boundary is served.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from claview_a import contract as K
from claview_a_api import http as H
from claview_a_api import screen as S
from claview_a_api.api import app
from claview_a_api.run_record import RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question

VERSION = "a-http/2026-11-02.1"
AS_OF = "2026-10-29"
PERIOD_QUERY = "M2 통화량을 월별로 보여줘"
CHOICE_QUERY = "M2 통화량을 2024년 월별로 보여줘"


def script(question=PERIOD_QUERY):
    period = {"quote": "M2 통화량", "for": ["s1"], "meaning": "not_stated", "refers_to": [],
              "form": "none", "start": "none", "end": "none", "count": "none", "unit": "none", "read": "ok"}
    out = {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
           "focuses": [{"for": ["s1"], "text": "M2 통화량", "quotes": ["M2 통화량"]}],
           "periods": [period],
           "frequencies": [{"quote": "월별", "for": ["s1"], "value": "M", "anchor": "none"}],
           "choices": [{"for": ["s1"], "table": "DT_161Y014"}]}
    if question == CHOICE_QUERY:
        period.update(quote="2024년", meaning="range", form="absolute", start="2024", end="same")
        out.pop("choices")
        out["asks"] = [{"for": "s1", "axis": "stock", "options": [
            {"value": "평잔", "table": "DT_161Y013"}, {"value": "말잔", "table": "DT_161Y014"}]}]
    return out


def judges(question=PERIOD_QUERY):
    judge = ScriptJudge(by_question({question: script(question)}), answer_contract=K.CONTRACT_VERSION)
    return S.Judges(judge, judge, "script"), judge


@pytest.fixture
def http_case(tmp_path):
    store = RunStore(tmp_path / "http_records")
    supplied, judge = judges()
    app.dependency_overrides[H.get_store] = lambda: store
    app.dependency_overrides[H.get_judges] = lambda: supplied
    try:
        with TestClient(app) as client:
            yield client, store, supplied, judge
    finally:
        app.dependency_overrides.clear()


def select(client, query=PERIOD_QUERY):
    response = client.post("/a/select", json={"contract_version": VERSION, "query": query, "as_of": AS_OF})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract_version"] == VERSION and body["execution_mode"] == "synthetic"
    assert "checkpoint" not in body and "state" not in body
    assert body["meta"]["llm_calls"] == body["meta"]["external_calls"] == 0
    return body


def answer_body(view, **answer):
    question, = view["questions"]
    return {"contract_version": VERSION, "session_id": view["session"]["session_id"],
            "replies": [{"question_id": question["question_id"], "question_version": question["question_version"],
                         **answer}]}


def no_judgments():
    raise AssertionError("answer/load must not resolve a judge")


def test_period_question_answer_and_reload_survive_a_new_store_without_judgments(http_case):
    client, store, _, judge = http_case
    first = select(client)
    view = first["view"]
    assert view["status"] == "needs_answer" and len(view["questions"]) == 1
    assert view["questions"][0]["kind"] == "period" and judge.calls == 1
    assert view["handoff"]["ready"] == []
    session_id = view["session"]["session_id"]
    app.dependency_overrides[H.get_judges] = no_judgments
    app.dependency_overrides[H.get_store] = lambda: RunStore(store.root)
    with TestClient(app) as restarted:
        loaded = restarted.get(f"/a/records/{session_id}")
        assert loaded.status_code == 200 and loaded.json()["view"] == view
        done = restarted.post("/a/answer", json=answer_body(view, text="2024년"))
        assert done.status_code == 200, done.text
        result = done.json()
        assert result["meta"]["llm_calls"] == result["meta"]["external_calls"] == 0
        assert result["execution_mode"] == "synthetic" and result["view"]["status"] == "ready"
        assert result["view"]["session"]["as_of"] == AS_OF
        stat, = result["view"]["statistics"]
        assert stat["tables"][0]["period"] == [{"start": "2024-01", "end": "2024-12"}]
        assert stat["tables"][0]["frequency"] == "M"
        assert stat["tables"][0]["period_source"] == "user_answer"
        assert stat["focus"] == view["statistics"][0]["focus"] == "M2 통화량"
        assert stat["conditions"] == view["statistics"][0]["conditions"]
        again = restarted.get(f"/a/records/{session_id}")
        assert again.status_code == 200 and again.json()["view"] == result["view"]
        assert again.json()["meta"]["llm_calls"] == again.json()["meta"]["external_calls"] == 0
    assert judge.calls == 1


def test_http_and_direct_screen_calls_preserve_the_same_focus_conditions_and_handoff(http_case, tmp_path):
    client, _, _, _ = http_case
    via_http = select(client)["view"]
    direct_judges, _ = judges()
    direct = S.select(PERIOD_QUERY, AS_OF, direct_judges, store=RunStore(tmp_path / "direct"))
    for field in ("focus", "focus_provenance", "conditions", "basis", "tables", "outcome"):
        assert via_http["statistics"][0][field] == direct["view"]["statistics"][0][field]
    via_http_done = client.post("/a/answer", json=answer_body(via_http, text="2024년")).json()["view"]
    body = answer_body(direct["view"], text="2024년")
    direct_done = S.answer(direct["checkpoint"], body["session_id"], body["replies"],
                           RunStore(tmp_path / "direct"))["view"]
    for field in ("focus", "focus_provenance", "conditions", "basis", "tables", "outcome"):
        assert via_http_done["statistics"][0][field] == direct_done["statistics"][0][field]
    normalized = [json.dumps(v["handoff"], sort_keys=True).replace(v["session"]["session_id"], "<session>")
                  for v in (via_http_done, direct_done)]
    assert normalized[0] == normalized[1]


def test_choice_reply_uses_the_server_option_and_no_judge(http_case):
    client, _, _, _ = http_case
    supplied, judge = judges(CHOICE_QUERY)
    app.dependency_overrides[H.get_judges] = lambda: supplied
    first = select(client, CHOICE_QUERY)["view"]
    question, = first["questions"]
    assert question["input"] == "choice" and question["axis"] == "stock"
    option = next(o for o in question["options"] if o["value"] == "말잔")
    app.dependency_overrides[H.get_judges] = no_judgments
    done = client.post("/a/answer", json=answer_body(first, option_id=option["option_id"]))
    assert done.status_code == 200, done.text
    assert done.json()["view"]["statistics"][0]["tables"][0]["table_id"] == option["table_id"]
    assert judge.calls == 1


@pytest.mark.parametrize("kind", ["invalid_period", "forged_question", "stale_version", "duplicate"])
def test_bad_replies_do_not_mutate_the_stored_session_or_call_a_judge(http_case, kind):
    client, store, _, judge = http_case
    view = select(client)["view"]
    session_id = view["session"]["session_id"]
    before = S.load(RunStore(store.root), session_id)
    body = answer_body(view, text="2024년")
    if kind == "invalid_period":
        body["replies"][0]["text"] = "2024-13"
    elif kind == "forged_question":
        body["replies"][0]["question_id"] = "forged-question"
    elif kind == "stale_version":
        body["replies"][0]["question_version"] += 1
    else:
        body["replies"].append(copy.deepcopy(body["replies"][0]))
    app.dependency_overrides[H.get_judges] = no_judgments
    response = client.post("/a/answer", json=body)
    assert response.status_code in (400, 409, 422), response.text
    assert S.load(RunStore(store.root), session_id) == before and judge.calls == 1


def test_repeating_an_already_applied_reply_is_rejected_without_mutation(http_case):
    client, store, _, judge = http_case
    view = select(client)["view"]
    body = answer_body(view, text="2024년")
    assert client.post("/a/answer", json=body).status_code == 200
    kept = S.load(RunStore(store.root), body["session_id"])
    app.dependency_overrides[H.get_judges] = no_judgments
    repeated = client.post("/a/answer", json=body)
    assert repeated.status_code in (400, 409), repeated.text
    assert S.load(RunStore(store.root), body["session_id"]) == kept and judge.calls == 1


def test_repeating_a_question_after_store_restart_keeps_its_call_budget(http_case):
    client, store, _, judge = http_case
    first = select(client)
    session_id = first["view"]["session"]["session_id"]
    kept = S.load(RunStore(store.root), session_id)
    app.dependency_overrides[H.get_store] = lambda: RunStore(store.root)
    repeated = client.post("/a/select", json={"contract_version": VERSION, "query": PERIOD_QUERY, "as_of": AS_OF})
    assert repeated.status_code == 403 and repeated.json()["detail"] == "question_budget_used"
    assert judge.calls == 1 and S.load(RunStore(store.root), session_id) == kept
    assert len(list(store.root.glob("a_sessions/*/record.json"))) == 1


def test_unknown_sessions_are_404_for_answer_and_reload(http_case):
    client, _, _, judge = http_case
    session_id = "ab" * 16
    app.dependency_overrides[H.get_judges] = no_judgments
    assert client.get(f"/a/records/{session_id}").status_code == 404
    response = client.post("/a/answer", json={"contract_version": VERSION, "session_id": session_id,
                                             "replies": [{"question_id": "q", "question_version": 0,
                                                          "text": "2024년"}]})
    assert response.status_code == 404 and judge.calls == 0


@pytest.mark.parametrize("extra", [
    {"checkpoint": {}}, {"state": {}}, {"mode": "live"}, {"execution_mode": "live"},
    {"approval_id": "unapproved"}, {"live_calls": True},
])
def test_select_rejects_client_state_or_execution_controls(http_case, extra):
    client, _, _, judge = http_case
    response = client.post("/a/select", json={"contract_version": VERSION, "query": PERIOD_QUERY,
                                             "as_of": AS_OF, **extra})
    assert response.status_code == 422 and judge.calls == 0


@pytest.mark.parametrize("extra", [{"checkpoint": {}}, {"state": {}}, {"mode": "live"}, {"approval_id": "x"}])
def test_answer_rejects_injected_state_or_execution_controls(http_case, extra):
    client, store, _, judge = http_case
    view = select(client)["view"]
    kept = S.load(RunStore(store.root), view["session"]["session_id"])
    response = client.post("/a/answer", json={**answer_body(view, text="2024년"), **extra})
    assert response.status_code == 422
    assert S.load(RunStore(store.root), view["session"]["session_id"]) == kept and judge.calls == 1


def test_unversioned_legacy_bodies_cannot_reach_new_routes(http_case):
    client, _, _, judge = http_case
    assert client.post("/a/select", json={"query": PERIOD_QUERY, "as_of": AS_OF}).status_code == 422
    assert client.post("/a/answer", json={"state": {}, "option_id": "x"}).status_code == 422
    assert judge.calls == 0


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_unavailable_server_modes_never_fall_back_to_synthetic(http_case, monkeypatch, mode):
    client, store, _, judge = http_case
    app.dependency_overrides.pop(H.get_judges)
    monkeypatch.setenv("CLAVIEW_MODE", mode)
    monkeypatch.delenv("CLAVIEW_A_LIVE_APPROVAL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(S, "synthetic_judges", no_judgments)
    response = client.post("/a/select", json={"contract_version": VERSION, "query": PERIOD_QUERY, "as_of": AS_OF})
    assert response.status_code == 503, response.text
    # Deployment live mode now needs credentials, not an evaluation approval file.
    assert response.json()["detail"] == ("key_unavailable" if mode == "live" else "replay_source_required")
    assert judge.calls == 0
    assert not list(store.root.glob("a_sessions/*/record.json"))


def test_legacy_body_in_live_mode_is_rejected_before_opening_any_approval(http_case, monkeypatch):
    client, _, _, judge = http_case
    app.dependency_overrides.pop(H.get_judges)
    monkeypatch.setenv("CLAVIEW_MODE", "live")
    monkeypatch.setattr(S, "live_judges", no_judgments)
    response = client.post("/a/select", json={"query": PERIOD_QUERY, "as_of": AS_OF})
    assert response.status_code == 422 and judge.calls == 0


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_answer_and_load_use_recorded_mode_when_server_configuration_changes(http_case, monkeypatch, mode):
    client, store, _, judge = http_case
    view = select(client)["view"]
    monkeypatch.setenv("CLAVIEW_MODE", mode)
    monkeypatch.setenv("CLAVIEW_A_LIVE_APPROVAL", str(store.root / "missing_approval.json"))
    monkeypatch.setattr(S, "live_judges", no_judgments)
    monkeypatch.setattr(S, "synthetic_judges", no_judgments)
    app.dependency_overrides[H.get_judges] = no_judgments
    loaded = client.get(f"/a/records/{view['session']['session_id']}")
    assert loaded.status_code == 200 and loaded.json()["execution_mode"] == "synthetic"
    answered = client.post("/a/answer", json=answer_body(view, text="2024년"))
    assert answered.status_code == 200 and answered.json()["execution_mode"] == "synthetic"
    assert answered.json()["view"]["session"]["as_of"] == AS_OF and judge.calls == 1


@pytest.mark.parametrize("kind", ["replay", "unknown"])
def test_incompatible_record_modes_are_not_reported_as_synthetic(http_case, kind):
    client, store, supplied, judge = http_case
    supplied.kind = kind
    out = S.select(PERIOD_QUERY, AS_OF, supplied, store=store)
    session_id = out["view"]["session"]["session_id"]
    kept = S.load(RunStore(store.root), session_id)
    app.dependency_overrides[H.get_judges] = no_judgments
    assert client.get(f"/a/records/{session_id}").status_code == 410
    assert client.post("/a/answer", json=answer_body(out["view"], text="2024년")).status_code == 410
    assert S.load(RunStore(store.root), session_id) == kept and judge.calls == 1


def test_omitted_as_of_is_fixed_once_and_not_recomputed_when_answering(http_case, monkeypatch):
    client, _, _, judge = http_case
    days = []

    def first_day():
        days.append(AS_OF)
        return AS_OF

    monkeypatch.setattr(H, "current_day", first_day)
    response = client.post("/a/select", json={"contract_version": VERSION, "query": PERIOD_QUERY})
    assert response.status_code == 200 and days == [AS_OF]
    view = response.json()["view"]
    monkeypatch.setattr(H, "current_day", no_judgments)
    app.dependency_overrides[H.get_judges] = no_judgments
    done = client.post("/a/answer", json=answer_body(view, text="2024년"))
    assert done.status_code == 200 and done.json()["view"]["session"]["as_of"] == AS_OF
    assert client.get(f"/a/records/{view['session']['session_id']}").status_code == 200
    assert days == [AS_OF] and judge.calls == 1
