"""Regression cases for candidate loss, ambiguous constraints and A-only evaluation."""

import copy
import json
from pathlib import Path

import httpx
import pytest

from claview.b.a_conditions import assess_candidate, query_constraints, validate_selected
from claview.b.a_evaluation import evaluate_a
from claview.b.contracts import Config, StageFailure, Trace
from claview.b.graphs import build_a_graph
from claview.b.metadata import MetadataStore
from claview.b.models import HcxModel, ScriptedModel, object_schema
from claview.b.search import SearchIndex
from claview.b.stages import Services, checked_query_plan

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def search():
    return SearchIndex(
        MetadataStore(ROOT / "tests/b/fixtures/catalog.json", ROOT / "tests/b/fixtures/metadata.jsonl")
    )


@pytest.mark.parametrize(
    "query,frequency",
    [
        ("월별 말고 연도별 자료", "Y"),
        ("연간 자료는 제외, 월별 수입물가", "M"),
        ("월별과 연간 자료를 각각 비교", None),
        ("월별 데이터를 연간 평균으로 변환", None),
        ("2024년 1월부터 12월까지", None),
        ("분기별 자료", "Q"),
        ("월별 자료를 제외하지 말아줘", None),
    ],
)
def test_frequency_requires_affirmative_unambiguous_native_request(query, frequency):
    assert query_constraints(query)["frequency"] == frequency


def test_missing_metadata_is_kept_but_cannot_be_success(search, monkeypatch):
    key = "301:DT_401Y018"

    def missing(*args):
        raise StageFailure("metadata_missing", "unresolved")

    monkeypatch.setattr(search.store, "get", missing)
    report = assess_candidate("수입물가", key, search)
    assert not report["excluded"]
    assert report["checks"][0]["state"] == "unknown"
    with pytest.raises(StageFailure, match="a_metadata_incomplete"):
        validate_selected("수입물가", [key], search)


def test_cached_period_conflict_is_not_candidate_exclusion(search):
    report = assess_candidate("2030년 월별 수입물가", "301:DT_401Y018", search)
    assert not report["excluded"]
    assert any(c["reason"] == "metadata_refresh_required" for c in report["checks"])


def test_review_preserves_unknown_and_removes_only_frequency_conflict(search, monkeypatch):
    original = search.store.get

    def annual(org, tid):
        table = copy.deepcopy(original(org, tid))
        if tid == "DT_401Y018":
            table["periods"] = {"Y": table["periods"]["Y"]}
        return table

    monkeypatch.setattr(search.store, "get", annual)
    model = ScriptedModel({"A": [{"selected_doc_ids": []}]})
    svc = Services(search.store, search, model, Config(), Trace())
    state = build_a_graph(svc).invoke({"query": "2024년 월별 수입물가"})
    assert "301:DT_401Y018" in {c["doc_id"] for c in state["merged_candidates"]}
    assert "301:DT_401Y018" not in {c["doc_id"] for c in state["selection_candidates"]}
    assert state["condition_assessments"]["301:DT_401Y018"]["excluded"]


def test_unapproved_gdp_semantic_substitution_is_rejected():
    raw = {
        "indicators": [{"source_quote": "GDP 성장률", "term": "GDP 디플레이터"}],
        "subjects": [],
        "statistical_basis": [],
        "explicit_conditions": [],
        "search_queries": ["GDP 디플레이터"],
        "ambiguities": [],
    }
    with pytest.raises(StageFailure, match="query_plan_unverified_term"):
        checked_query_plan("GDP 성장률", raw)


def test_a_evaluation_calls_no_b_and_no_gold_in_context(search, tmp_path):
    cases = json.loads((ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8-sig"))[
        "cases"
    ]
    case = next(c for c in cases if c["case_id"] == "CV-AB-19")

    class Model:
        def complete(self, task, system, context, schema, max_tokens, trace):
            assert task == "A"
            assert "expected" not in context and "source" not in context
            assert "case_id" not in context
            return {"selected_doc_ids": ["301:DT_401Y018"]}

    report = evaluate_a(
        [case], search.store, search, Config(), "live", Model(), tmp_path / "a.json"
    )
    assert report["summary"]["exact_count"] == 1 and report["complete"]
    offline = evaluate_a([case], search.store, search, Config())
    assert offline["summary"]["exact_count"] is None
    assert offline["summary"]["candidate_inclusion_count"] == 1


def test_hcx_does_not_record_bare_key_echo():
    secret = "fake-secret-canary-only"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": secret}},
            },
        )

    trace = Trace()
    model = HcxModel(secret, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(StageFailure, match="hcx_sensitive_response"):
            model.complete("A", "s", {}, object_schema({}), 10, trace)
        assert secret not in json.dumps(trace.events)
    finally:
        model.close()


@pytest.mark.parametrize("seconds,expected_calls", [(23, 2), (90, 1)])
def test_rate_limit_respects_reset_and_preserves_safe_diagnostics(
    monkeypatch, seconds, expected_calls
):
    waits, calls = [], []
    monkeypatch.setattr("claview.b.models.time.sleep", waits.append)

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                429,
                headers={
                    "x-ratelimit-reset-tokens": f"{seconds}s",
                    "x-ratelimit-remaining-tokens": "0",
                    "x-private": "never-record",
                },
                json={"status": {"code": "42901", "message": "never-record"}},
            )
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": "{}"}},
            },
        )

    trace = Trace()
    model = HcxModel("fake-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        if seconds > 60:
            with pytest.raises(StageFailure, match="hcx_http_429"):
                model.complete("A", "s", {}, object_schema({}), 10, trace)
            assert not waits
        else:
            model.complete("A", "s", {}, object_schema({}), 10, trace)
            assert waits == [seconds]
        assert len(calls) == expected_calls
        assert "never-record" not in json.dumps(trace.events)
        assert any(e.get("service_code") == "42901" for e in trace.events)
    finally:
        model.close()


def test_hcx_paces_successive_requests(monkeypatch):
    now, waits = [0.0], []
    monkeypatch.setattr("claview.b.models.time.monotonic", lambda: now[0])

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("claview.b.models.time.sleep", sleep)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": "{}"}},
            },
        )

    model = HcxModel(
        "fake-key", min_interval=20, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    try:
        for _ in range(2):
            model.complete("A", "s", {}, object_schema({}), 10, Trace())
        assert waits == [20]
    finally:
        model.close()


def test_raw_selection_accuracy_is_separate_from_validation(search):
    cases = json.loads((ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8-sig"))[
        "cases"
    ]
    c = copy.deepcopy(next(c for c in cases if c["case_id"] == "CV-AB-19"))
    c["query"] = "2030년 월별 수입물가지수"
    result = evaluate_a([c], search.store, search, Config(), mode="replay")
    assert result["summary"]["raw_selection_exact_count"] == 1
    assert result["summary"]["exact_count"] == 0
    assert result["cases"][0]["outcome"]["issues"][0]["code"] == "metadata_refresh_required"
