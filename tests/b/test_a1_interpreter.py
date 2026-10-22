"""Source validation and live-boundary tests use only mocked HTTP and temporary credentials."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from claview.b.a1_interpreter import QueryInterpretation, interpretation_gate, validate_interpretation
from claview.b.a1_query import prepare_question
from claview.b.a_agent import AAgent
from claview.b.a_discovery import TableDiscovery
from claview.b.a_model_contracts import make_packet
from claview.b.a_model_experiment import ExperimentLedger
from claview.b.a_model_providers import AProvider, ModelFault
from claview.b.a_model_runtime import AdviceCache, Limits, Price, RoleRuntime
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import StageFailure
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents

VERSIONS = {k: "test/1" for k in ("dictionary", "catalog", "metadata", "policy")}
PRICE = Price("KRW", "1250", "5000", "2026-10-05", "fixture-not-a-price-source")
RUNNER = None


@pytest.fixture(scope="module", autouse=True)
def event_loop_before_network_guard():
    global RUNNER
    with asyncio.Runner() as runner:
        runner.get_loop()
        RUNNER = runner
        yield
    RUNNER = None


def span(text, quote):
    start = text.index(quote)
    return {"source_id": "query", "start": start, "end": start + len(quote), "quote": quote}


def value(query="수입물가 원화", term="수입물가"):
    return {
        "mentions": [
            {
                "kind": "concept",
                "axis": "concept",
                "polarity": "affirmed",
                "span": span(query, "수입물가"),
            }
        ],
        "expansions": [{"term": term, "span": span(query, "수입물가")}],
    }


def mock(value, sent, status=200):
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            status,
            json={
                "status": {"code": "20000"},
                "result": {
                    "finishReason": "stop",
                    "message": {"content": json.dumps(value, ensure_ascii=False)},
                    "usage": {"promptTokens": 150, "completionTokens": 80},
                },
            },
        )

    return AProvider(
        "HCX-007", api_key="temporary-test-key", transport=httpx.MockTransport(handler)
    )


def hook(provider, **kwargs):
    runtime = RoleRuntime(
        Limits(max_calls=1, max_retries=0, max_output_tokens=2048), enabled=True, **kwargs
    )
    return QueryInterpretation(provider, runtime, versions=VERSIONS, run_async=RUNNER.run)


def test_default_and_explicit_resolved_gate_make_no_requests():
    sent = []
    provider = mock(value(), sent)
    try:
        runtime = RoleRuntime(Limits(max_calls=1, max_retries=0))
        interpreter = QueryInterpretation(
            provider, runtime, versions=VERSIONS, run_async=RUNNER.run
        )
        assert interpreter(prepare_question("수입물가 원화"))["reason"] == "disabled"
        active = hook(provider)
        result = active(prepare_question("통계표 「수입물가지수(용도별)」"))
        assert result["status"] == "not_called" and not sent
        assert not interpretation_gate(prepare_question("통계표 「가계신용」"))["needed"]
    finally:
        RUNNER.run(provider.close())


@pytest.mark.parametrize(
    "term", ["실질 수입물가", "달러 수입물가", "2020년 수입물가", "월별 수입물가"]
)
def test_condition_addition_or_reversal_falls_back(term):
    sent = []
    provider = mock(value(term=term), sent)
    try:
        result = hook(provider)(prepare_question("수입물가 원화"))
        assert result["status"] == "fallback" and result["reason"] == "interpreter_condition_change"
        assert result["auxiliaries"] == [] and len(sent) == 1
    finally:
        RUNNER.run(provider.close())


def test_bad_span_rejects_without_retry():
    bad = value()
    bad["mentions"][0]["span"]["start"] = 1
    sent = []
    provider = mock(bad, sent)
    try:
        result = hook(provider)(prepare_question("수입물가 원화"))
        assert result["status"] == "fallback" and result["reason"] == "query_span_mismatch"
        assert len(sent) == 1
    finally:
        RUNNER.run(provider.close())


def test_frequency_condition_axis_and_polarity_validated():
    query = "월별 수입물가"
    output = value(query)
    output["mentions"] = [
        {
            "kind": "condition",
            "axis": "frequency",
            "polarity": "negated",
            "span": span(query, "월별"),
        }
    ]
    with pytest.raises(StageFailure, match="interpreter_condition_change"):
        validate_interpretation(output, make_packet(query, VERSIONS), prepare_question(query))


def test_negative_concept_cannot_become_positive_auxiliary():
    query = "수입물가 제외"
    with pytest.raises(StageFailure, match="interpreter_negation_change"):
        validate_interpretation(value(query), make_packet(query, VERSIONS), prepare_question(query))


def test_cache_revalidates_and_preflight_never_sends_oversized_input(tmp_path):
    sent = []
    provider = mock(value(), sent)
    try:
        cache = AdviceCache(tmp_path / "cache.db")
        first = hook(provider, cache=cache)(prepare_question("수입물가 원화"))
        second = hook(provider, cache=cache)(prepare_question("수입물가 원화"))
        assert first["status"] == second["status"] == "advice"
        assert second["advice"]["cache_hit"] and second["runtime"]["external_calls"] == 0
        assert len(sent) == 1
        huge = make_packet("수입물가", VERSIONS)
        huge["sources"]["query"] = "가" * 100000
        runtime = RoleRuntime(Limits(max_calls=1, max_retries=0), enabled=True)
        result = RUNNER.run(runtime.invoke("QueryInterpreter", huge, provider))
        assert result["reason"] == "input_preflight_limit" and len(sent) == 1
    finally:
        RUNNER.run(provider.close())


def test_live_injection_and_existing_agent_are_still_blocked():
    provider = AProvider("HCX-007", api_key="temporary-test-key")
    try:
        with pytest.raises(StageFailure, match="stage5_live_scope_required"):
            QueryInterpretation(
                provider,
                RoleRuntime(Limits(max_calls=1, max_retries=0), enabled=True, allow_live=True),
                versions=VERSIONS,
            )
        with pytest.raises(StageFailure, match="a_live_roles_not_adopted"):
            AAgent(providers={"QueryInterpreter": provider})
    finally:
        RUNNER.run(provider.close())


def test_actual_a1_path_preserves_question_rules_and_original_candidates(tmp_path):
    fixture = Path(__file__).parent / "fixtures"
    store = MetadataStore(fixture / "catalog.json", fixture / "metadata.jsonl")
    search = SearchIndex(store, corpus=build_search_documents(store))
    query = "수입물가 원화"
    sent = []
    provider = mock(value(term="가격 지수"), sent)
    try:
        original = TableDiscovery(search, CandidateStore(tmp_path / "off.db"))
        enabled = TableDiscovery(
            search, CandidateStore(tmp_path / "on.db"), interpreter=hook(provider)
        )
        context = {"session_id": "fixture", "request_id": "one", "query_revision": 1}
        a = original.search_tables(query, context)
        b = enabled.search_tables(query, context)
        before = original.repository.get(a["interaction"]["candidate_set_id"], context)
        after = enabled.repository.get(b["interaction"]["candidate_set_id"], context)
        assert {c["doc_id"] for c in before["candidate_pool"]} <= {
            c["doc_id"] for c in after["candidate_pool"]
        }
        assert after["original_query"] == query
        assert before["a1_state"]["requirements"] == after["a1_state"]["requirements"]
        assert after["a1_state"]["interpretation"]["status"] == "advice"
        assert any(q["kind"] == "model_hypothesis" for q in after["a1_state"]["search_queries"])
        assert after["selection_decision"]["auto_eligible"] is False
        assert len(sent) == 1
    finally:
        RUNNER.run(provider.close())


def test_independent_failure_cannot_repeat_job_or_exceed_total_budget(tmp_path):
    limits = {
        "max_calls": 1,
        "max_input_tokens": 128000,
        "max_output_tokens": 2048,
        "max_seconds": 300,
        "max_cost_by_currency": {"KRW": "1000"},
    }
    ledger = ExperimentLedger(
        tmp_path / "ledger.db", plan_id="fixture", mode="mock", limits=limits, stop_on_failure=False
    )
    ledger.claim("one")
    record = {
        "provider": {"mode": "mock"},
        "price": {"currency": "KRW"},
        "reserved_cost": "1",
        "reserved_input_tokens": 100,
        "reserved_output_tokens": 100,
    }
    ticket, _ = ledger.reserve(record)
    ledger.finish(ticket, {"status": "format_error"})
    ledger.complete({"advice": {"status": "fallback"}, "runtime": {}})
    with pytest.raises(ModelFault, match="already_attempted"):
        ledger.claim("one")
    ledger.claim("two")
    with pytest.raises(ModelFault, match="budget"):
        ledger.reserve(copy.deepcopy(record))
