"""A's `period_condition` through B's service and HTTP boundary (synthetic model and fetch client).

The unit-level rules live in modules/b/tests/b_part/test_b_period_condition_receiver.py; this checks the
wiring: the condition reaches B over HTTP, the model never sees it, the answered period is applied in
code, `period_resolutions` comes back in the shared result model, and legacy requests are unchanged.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from claview_b_api import api
from claview_b_api.service import BService, synthetic_clients
from claview_common.contracts import BResult
from claview_common.contracts.period_condition import PeriodResolution

TABLE = {"org_id": "301", "table_id": "DT_301Y013", "table_name": "국제수지"}  # Y 1980~2025, Q ~202602, M ~202607
AS_OF = "2026-10-22"


def answer(kind, value):
    return {
        "kind": kind,
        "question_id": f"q-{kind}",
        "question_version": 1,
        "statistics": ["s1"],
        "option_id": None,
        "value": value,
        "text": None,
        "source": "user_answer",
    }


def condition(**overrides):
    base = {
        "frequency": "Q",
        "frequency_source": "catalog",
        "period": [{"start": "2020-01", "end": "2024-12"}],
        "period_source": "user_answer",
        "statistics": ["s1"],
        "period_phrase": "2020~2024년",
        "answers": [answer("period", "2020~2024년")],
    }
    base.update(overrides)
    return base


def request(query="경상수지를 보여줘", **overrides):
    return {"query": query, "as_of": AS_OF, "tables": [{**TABLE, "period_condition": condition(**overrides)}]}


@pytest.fixture(scope="module")
def client():
    with TestClient(api.app) as c:
        yield c


def resolve(client, body):
    response = client.post("/b/resolve", json=body)
    assert response.status_code == 200
    BResult.model_validate(response.json())  # the response is a valid shared result
    return response.json()["outcome"]


def test_answered_period_is_applied_with_the_longest_exact_frequency_and_reported(client):
    outcome = resolve(client, request())
    assert outcome["status"] == "ok"
    result = outcome["result"]
    (row,) = [PeriodResolution.model_validate(r) for r in result["period_resolutions"]]
    assert row.table_id == "DT_301Y013" and row.statistics == ["s1"]
    assert row.frequency == "Y" and row.frequency_alternatives == ["Q", "M"]
    assert [(r.start, r.end) for r in row.requested] == [("2020", "2024")]
    assert [(r.start, r.end) for r in row.fetched] == [("2020", "2024")]
    assert row.missing == [] and row.substituted is False
    assert row.applied_sources.period == "a_user_answer" and row.applied_sources.frequency == "b_computed"
    assert row.reference.differs.frequency is True and row.reference.differs.period is False
    assert {s["period"]["start"] for s in result["selections"]} == {"2020"}
    assert {s["period"]["end"] for s in result["selections"]} == {"2024"}
    # the other frequencies are offered with the same period, ready to switch to
    assert [a["frequency"] for a in result["frequency_alternatives"]] == ["Q", "M"]
    assert result["frequency_alternatives"][0]["period"] == {"frequency": "Q", "start": "202001", "end": "202404"}


def test_resolve_and_run_both_carry_the_condition(client):
    body = request()
    run = client.post("/b/run", json=body).json()
    assert run["outcome"]["stage"] == "fetch" and run["outcome"]["status"] == "ok"
    assert run["meta"]["llm_calls"] >= 1


def test_frequency_answer_alone_keeps_B_computing_the_period(client):
    body = request(
        "최근 3년 경상수지를 보여줘",
        frequency="Q",
        frequency_source="user_answer",
        period_source="quoted",
        answers=[answer("frequency", "Q")],
    )
    outcome = resolve(client, body)
    assert outcome["status"] == "ok"
    (row,) = outcome["result"]["period_resolutions"]
    assert row["frequency"] == "Q" and row["applied_sources"] == {"period": "b_computed", "frequency": "a_user_answer"}
    assert row["requested"] == row["fetched"] == [{"frequency": "Q", "start": "202303", "end": "202602"}]


def test_confirmed_period_outside_the_table_stops_instead_of_moving(client):
    outcome = resolve(client, request(period=[{"start": "2027-01", "end": "2027-12"}]))
    assert outcome["status"] != "ok"
    assert outcome["issues"][0]["code"] == "period_condition_out_of_coverage"


def test_confirmed_months_that_a_longer_frequency_cannot_show_stay_months(client):
    outcome = resolve(client, request(period=[{"start": "2024-02", "end": "2024-05"}]))
    assert outcome["status"] == "ok"  # 2024-02~05 is not a quarter or a year: shown by month, never widened
    (row,) = outcome["result"]["period_resolutions"]
    assert row["frequency"] == "M" and row["requested"] == [{"frequency": "M", "start": "202402", "end": "202405"}]
    assert row["frequency_alternatives"] == []


def test_two_tables_keep_the_same_confirmed_range(client):
    shared = condition(
        frequency="Y", frequency_source="user_answer", answers=[answer("period", "x"), answer("frequency", "Y")]
    )
    other = {"org_id": "301", "table_id": "DT_200Y101", "table_name": "국민계정"}
    body = {
        "query": "두 표를 비교해줘",
        "as_of": AS_OF,
        "tables": [{**TABLE, "period_condition": shared}, {**other, "period_condition": copy.deepcopy(shared)}],
    }
    outcome = resolve(client, body)
    assert outcome["status"] == "ok"
    rows = outcome["result"]["period_resolutions"]
    assert [r["table_id"] for r in rows] == ["DT_301Y013", "DT_200Y101"]
    assert all(r["requested"] == r["fetched"] == [{"frequency": "Y", "start": "2020", "end": "2024"}] for r in rows)


def test_frequency_the_table_lacks_stops_instead_of_changing_it(client):
    body = request(
        frequency="H", frequency_source="user_answer", answers=[answer("period", "x"), answer("frequency", "H")]
    )
    outcome = resolve(client, body)
    assert outcome["status"] != "ok" and outcome["issues"][0]["code"] == "period_condition_frequency_unsupported"


def test_period_condition_without_answers_is_refused_over_http(client):
    body = request(answers=[])
    response = client.post("/b/resolve", json=body)
    assert response.status_code == 422  # the shared model rejects it before B (period_condition_answer_missing)


def test_legacy_request_has_no_period_resolutions(client):
    body = {"query": "2020~2024년 연간 경상수지", "as_of": AS_OF, "tables": [dict(TABLE)]}
    outcome = resolve(client, body)
    assert outcome["status"] == "ok" and "period_resolutions" not in outcome["result"]


class SpyModel:
    """The synthetic model, remembering what it was shown."""

    def __init__(self):
        self.inner, self.seen = synthetic_clients()[0], []

    def complete(self, name, system, context, schema, *args, **kwargs):
        self.seen.append(copy.deepcopy(context))
        return self.inner.complete(name, system, context, schema, *args, **kwargs)


def test_the_model_never_sees_the_condition_but_is_shown_the_period_the_user_answered():
    """2026-11-03 (B-1): the condition itself (sources, answers, A's wording) stays out of the model request, but the
    period the user answered is not in the question, so `resolved_period` carries it: a question without any period
    made the model return no selection."""
    model = SpyModel()
    service = BService(model, synthetic_clients()[1], "synthetic")
    envelope, _calls = service.resolve("경상수지를 보여줘", [{**TABLE, "period_condition": condition()}], AS_OF)
    assert envelope["status"] == "ok"
    (context,) = model.seen
    assert all("period_condition" not in table for table in context["selected_tables"])
    assert "period_condition" not in str(context) and "user_answer" not in str(context)
    shown = context["resolved_period"]
    assert shown is not None and shown["start"][:4] == "2020" and shown["end"][:4] == "2024"
    selected = envelope["result"]["selections"][0]["period"]
    assert (selected["frequency"], selected["start"], selected["end"]) == (
        shown["frequency"], shown["start"], shown["end"]
    )  # what the model is told is what the code selects
