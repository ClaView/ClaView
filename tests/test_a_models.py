"""Synthetic provider contracts and failure containment, not model-quality measurements."""

import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from claview.a_discovery import TableDiscovery
from claview.a_model_contracts import (
    ACTIONS,
    clarification_template,
    make_packet,
    role_schema,
    validate_grounding,
)
from claview.a_model_providers import AProvider
from claview.a_model_runtime import AdviceCache, Limits, Price, RoleRuntime
from claview.candidate_store import CandidateStore
from claview.contracts import StageFailure
from claview.document_corpus import source_entry
from claview.metadata import MetadataStore
from claview.models import check_output
from claview.search import SearchIndex
from claview.search_documents import build_search_documents, digest

FIXTURES = Path(__file__).parent / "fixtures"
VERSIONS = {k: "fixture/1" for k in ("dictionary", "metadata", "catalog", "policy")}
KEY = "301:DT_401Y018"
QUERY = "수입물가 원화"
SPAN = {"source_id": "query", "start": 0, "end": 4, "quote": "수입물가"}
INTERPRETED = {
    "mentions": [{"kind": "concept", "axis": "concept", "polarity": "affirmed", "span": SPAN}],
    "expansions": [{"term": "수입 가격", "span": SPAN}],
}
DECISION = {"action": "show_candidates"}
RUNNER = None


@pytest.fixture(scope="module", autouse=True)
def event_loop_before_network_guard():
    # Windows creates a loopback self-pipe. Create it before per-test network blocking.
    # All inference and test execution still runs under the existing network guard.
    global RUNNER
    with asyncio.Runner() as runner:
        runner.get_loop()
        RUNNER = runner
        yield
    RUNNER = None


@pytest.fixture
def store():
    return MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")


@pytest.fixture
def documents(store):
    row = store.catalog[KEY]
    ref = source_entry("catalog", "/title", row["title"], "title", digest(row))
    ref = {**ref["ref"], "quote": ref["quote"], "start": 0, "end": len(ref["quote"])}
    return [
        {
            "doc_id": KEY,
            "chunk_id": "fixture-title",
            "text": row["title"],
            "text_hash": digest(row["title"]),
            "source_refs": [ref],
        }
    ]


@pytest.fixture
def packet(store, documents):
    return make_packet(
        QUERY,
        VERSIONS,
        candidate_ids=[KEY],
        store=store,
        documents=documents,
        conditions=[{"condition_id": "c1", "axis": "concept", "span": SPAN}],
        clarification_axes=["price_basis"],
    )


def hcx(value, model="HCX-007", *, usage=True):
    result = {
        "message": {"content": json.dumps(value, ensure_ascii=False)},
        "finishReason": "stop",
        "stopReason": "end_token",
    }
    if usage:
        result.update(
            usage={"promptTokens": 500, "completionTokens": 100}, inputLength=500, outputLength=100
        )
    return {"status": {"code": "20000"}, "result": result}


def jev(action="show_candidates"):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": action,
                "confidence": 0.7,
                "probabilities": {k: (0.7 if k == action else 0.1) for k in ACTIONS},
            }
        },
        "usage": {"input_tokens": 500, "output_tokens": 40},
    }


def invoke(
    packet,
    responses,
    *,
    model="HCX-007",
    role="QueryInterpreter",
    runtime=None,
    price=None,
    seen=None,
    key=None,
):
    queue = iter(responses)

    def handler(request):
        if seen is not None:
            seen.append(request)
        response = next(queue)
        if isinstance(response, Exception):
            raise response
        return (
            response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
        )

    runtime = runtime or RoleRuntime(enabled=True)
    provider = AProvider(model, transport=httpx.MockTransport(handler), api_key=key)

    async def run():
        try:
            return await runtime.invoke(role, packet, provider, price=price)
        finally:
            await provider.close()

    return RUNNER.run(run()), runtime


@pytest.mark.parametrize("model", ["HCX-003", "HCX-007"])
def test_hcx_wire_formats_and_unchanged_query(packet, model):
    seen = []
    result, runtime = invoke(packet, [hcx(INTERPRETED)], model=model, seen=seen)
    assert result["status"] == "advice"
    assert not result["validation"]["meaning_verified"]
    request = seen[0]
    body = json.loads(request.content)
    assert json.loads(body["messages"][1]["content"])["sources"]["query"] == QUERY
    if model == "HCX-007":
        assert body["thinking"] == {"effort": "none"}
        assert "additionalProperties" not in json.dumps(body["responseFormat"])
        assert "maxLength" not in json.dumps(body["responseFormat"])
        assert body["responseFormat"]["type"] == "json" and "maxTokens" not in body
        assert "/v3/" in str(request.url)
    else:
        assert "responseFormat" not in body and "thinking" not in body
        assert body["maxTokens"] == 512 and "/v1/" in str(request.url)
    assert runtime.summary()["external_calls"] == 0
    assert runtime.summary()["actual_cost"] == "0"


def test_jev_fixed_choices_not_text_generation(packet):
    seen = []
    result, _ = invoke(packet, [jev()], model="jev-1.13.0", role="DecisionClassifier", seen=seen)
    body = json.loads(seen[0].content)
    assert set(body) == {"state", "model", "questions"}
    assert body["questions"]["next_action"]["type"] == "choice"
    assert result["value"] == DECISION
    assert result["diagnostics"]["a_correctness_probability"] is None


@pytest.mark.parametrize("variant", ["choice", "probability", "sum", "version", "question", "nan"])
def test_jev_invalid_response_is_not_a_decision(packet, variant):
    wire = jev()
    answer = wire["answers"]["next_action"]
    if variant == "choice":
        answer["choice"] = "confirm_tables"
    elif variant == "probability":
        answer["probabilities"]["show_candidates"] = -1
    elif variant == "sum":
        answer["probabilities"]["show_candidates"] = 0.2
    elif variant == "version":
        wire["model"] = "jev-9.9.9"
    elif variant == "question":
        wire["answers"]["extra"] = copy.deepcopy(answer)
    else:
        answer["confidence"] = "NaN"
    result, _ = invoke(
        packet,
        [wire],
        model="jev-1.13.0",
        role="DecisionClassifier",
        runtime=RoleRuntime(Limits(max_retries=0), enabled=True),
    )
    assert result["status"] == "fallback"


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview", "made-up-model"])
def test_aliases_and_unknown_models_not_pinned(model):
    with pytest.raises(ValueError):
        AProvider(model)


def judgment(packet):
    ref_id, ref = next(iter(packet["references"].items()))
    return {
        "judgments": [
            {
                "doc_id": KEY,
                "condition_id": "c1",
                "verdict": "support",
                "citations": [{"ref_id": ref_id, "quote": ref["quote"]}],
            }
        ]
    }


@pytest.mark.parametrize("model", ["HCX-003", "HCX-007"])
def test_judgment_contract_survives_model_swap(packet, model):
    result, _ = invoke(packet, [hcx(judgment(packet))], role="EvidenceJudge", model=model)
    assert result["status"] == "advice" and result["value"]["judgments"][0]["doc_id"] == KEY
    assert result["validation"]["source_grounding_valid"]
    assert result["validation"]["meaning_verified"] is False


@pytest.mark.parametrize(
    "variant", ["id", "quote", "ref", "condition", "no_ref", "duplicate", "missing"]
)
def test_invalid_judgments_fall_back(packet, variant):
    value = judgment(packet)
    row = value["judgments"][0]
    if variant == "id":
        row["doc_id"] = "301:FAKE"
    elif variant == "quote":
        row["citations"][0]["quote"] = "원문에 없는 값"
    elif variant == "ref":
        row["citations"][0]["ref_id"] = "fake"
    elif variant == "condition":
        row["condition_id"] = "invented"
    elif variant == "no_ref":
        row["citations"] = []
    elif variant == "duplicate":
        value["judgments"].append(copy.deepcopy(row))
    else:
        value["judgments"] = []
    result, _ = invoke(
        packet,
        [hcx(value)],
        role="EvidenceJudge",
        runtime=RoleRuntime(Limits(max_retries=0), enabled=True),
    )
    assert result["status"] == "fallback"


@pytest.mark.parametrize(
    "variant", ["span", "invented_condition", "extra_id", "id_in_expansion", "enum"]
)
def test_interpreter_cannot_invent_source_condition_or_id(packet, variant):
    value = copy.deepcopy(INTERPRETED)
    if variant == "span":
        value["mentions"][0]["span"]["end"] = 3
    elif variant == "invented_condition":
        value["mentions"][0].update(kind="condition", axis="period")
        value["mentions"][0]["span"]["quote"] = "2024년"
    elif variant == "extra_id":
        value["table_id"] = "DT_999"
    elif variant == "id_in_expansion":
        value["expansions"][0]["term"] = "301:DT_999"
    else:
        value["mentions"][0]["polarity"] = "certain"
    result, _ = invoke(
        packet, [hcx(value)], runtime=RoleRuntime(Limits(max_retries=0), enabled=True)
    )
    assert result["status"] == "fallback"


def test_source_grounding_not_semantic_truth(packet):
    wrong_meaning = copy.deepcopy(INTERPRETED)
    wrong_meaning["mentions"][0].update(axis="period", polarity="negated")
    check_output(wrong_meaning, role_schema("QueryInterpreter", packet))
    verdict = validate_grounding("QueryInterpreter", wrong_meaning, packet)
    assert verdict["source_grounding_valid"] and not verdict["meaning_verified"]


def test_prior_turn_spans_and_no_overwrite():
    packet = make_packet("실질로 볼게", VERSIONS, prior_turns=["국내총생산"])
    value = {
        "mentions": [
            {
                "kind": "concept",
                "axis": "concept",
                "polarity": "affirmed",
                "span": {"source_id": "prior:0", "start": 0, "end": 5, "quote": "국내총생산"},
            }
        ],
        "expansions": [],
    }
    result, _ = invoke(packet, [hcx(value)])
    assert result["status"] == "advice" and packet["sources"]["query"] == "실질로 볼게"


@pytest.mark.parametrize("variant", ["record", "hash", "offset", "unofficial", "condition"])
def test_packet_rechecks_official_sources(store, documents, variant):
    docs = copy.deepcopy(documents)
    ids = [KEY]
    conditions = [{"condition_id": "c1", "axis": "concept", "span": copy.deepcopy(SPAN)}]
    if variant == "record":
        docs[0]["source_refs"][0]["record_hash"] = "changed"
    elif variant == "hash":
        docs[0]["text_hash"] = "changed"
    elif variant == "offset":
        docs[0]["source_refs"][0]["end"] = 1
    elif variant == "unofficial":
        ids = ["301:unknown"]
    else:
        conditions[0]["span"]["quote"] = "2025"
    with pytest.raises(StageFailure):
        make_packet(
            QUERY, VERSIONS, store=store, documents=docs, candidate_ids=ids, conditions=conditions
        )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "model_unavailable"),
        (403, "model_unavailable"),
        (404, "model_unavailable"),
        (429, "quota"),
        (500, "provider_http_error"),
        (529, "overloaded"),
    ],
)
def test_http_errors_are_sanitized_no_hidden_retries(packet, status, code):
    result, runtime = invoke(
        packet, [httpx.Response(status, text="credential-sensitive-server-text")]
    )
    assert result["reason"] == code and runtime.calls == 1
    assert "credential" not in json.dumps(runtime.summary())


def test_timeout_and_transport_error(packet):
    for error in (httpx.ReadTimeout("secret-url"), httpx.ConnectError("secret-key")):
        result, runtime = invoke(packet, [error])
        assert result["status"] == "fallback" and runtime.calls == 1
        assert "secret" not in json.dumps(runtime.summary())


def test_total_deadline_cancels_slow_transport(packet):
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=hcx(INTERPRETED))

    async def run():
        provider = AProvider("HCX-007", transport=httpx.MockTransport(handler))
        runtime = RoleRuntime(Limits(max_seconds=0.02), enabled=True)
        try:
            result = await runtime.invoke("QueryInterpreter", packet, provider)
            assert result["reason"] == "time_budget"
            assert runtime.calls == 1
        finally:
            await provider.close()

    RUNNER.run(run())


def test_deadline_reason_does_not_depend_on_second_clock_read(packet, monkeypatch):
    async def expired(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    async def run():
        provider = AProvider("HCX-007", transport=httpx.MockTransport(lambda _: None))
        runtime = RoleRuntime(Limits(max_seconds=1), enabled=True, clock=lambda: 0)
        try:
            result = await runtime.invoke("QueryInterpreter", packet, provider)
            assert result["reason"] == "time_budget"
            assert runtime.calls == 1
            assert runtime.trace[0]["status"] == "time_budget"
        finally:
            await provider.close()

    monkeypatch.setattr(asyncio, "wait_for", expired)
    RUNNER.run(run())


def test_disabled_no_request_and_template_default(packet):
    result, runtime = invoke(packet, [], runtime=RoleRuntime())
    assert result["reason"] == "disabled" and runtime.calls == 0
    result, runtime = invoke(packet, [], role="ClarificationWriter")
    assert result["reason"] == "template_preferred_writer_deferred" and runtime.calls == 0
    assert "명목" in clarification_template("price_basis")


def test_real_transport_blocked_without_live_opt_in(packet):
    async def run():
        provider = AProvider("HCX-007")
        try:
            result = await RoleRuntime(enabled=True).invoke("QueryInterpreter", packet, provider)
            assert result["reason"] == "live_disabled"
            result = await RoleRuntime(enabled=True, allow_live=True).invoke(
                "QueryInterpreter", packet, provider
            )
            assert result["reason"] == "verified_price_required"
            runtime = RoleRuntime(enabled=True, allow_live=True)
            result = await runtime.invoke(
                "QueryInterpreter",
                packet,
                provider,
                price=Price("KRW", "1", "1", "2026-10-05", "synthetic-test-price"),
            )
            assert result["reason"] == "model_unavailable"
            assert runtime.external_calls == runtime.calls == 0
        finally:
            await provider.close()

    RUNNER.run(run())


def test_format_repair_spends_shared_budget_and_records_usage(packet):
    seen = []
    bad = hcx(INTERPRETED)
    bad["result"]["message"]["content"] = "invalid JSON"
    runtime = RoleRuntime(Limits(max_cost="1"), enabled=True)
    price = Price("KRW", "1", "2", "2026-10-05", "synthetic-test-price")
    result, runtime = invoke(
        packet, [bad, hcx(INTERPRETED)], runtime=runtime, price=price, seen=seen
    )
    assert result["status"] == "advice" and runtime.calls == 2
    assert runtime.reserved_tokens == 256000
    assert runtime.trace[1]["repair"] and runtime.trace[0]["usage"]["input_tokens"] == 500
    assert runtime.reserved_cost > 0 and runtime.trace[0]["estimated_cost"] == "0.0007"
    assert "앞선 응답" in json.loads(seen[1].content)["messages"][0]["content"]


@pytest.mark.parametrize(
    "field,value", [("max_calls", 1), ("max_tokens", 128000), ("max_cost", "0.13")]
)
def test_repair_cannot_bypass_any_budget(packet, field, value):
    limits = replace(Limits(max_cost="1"), **{field: value})
    runtime = RoleRuntime(limits, enabled=True)
    price = Price("KRW", "1", "1", "2026-10-05", "synthetic-test-price")
    result, runtime = invoke(packet, [hcx({})], runtime=runtime, price=price)
    assert result["reason"] == "call_token_or_cost_budget" and runtime.calls == 1


@pytest.mark.parametrize("usage", [None, (128001, 5), (500, 513)])
def test_unknown_or_excess_usage_stops_request(packet, usage):
    wire = hcx(INTERPRETED, usage=usage is not None)
    if usage:
        wire["result"]["usage"] = {"promptTokens": usage[0], "completionTokens": usage[1]}
    result, runtime = invoke(packet, [wire])
    assert result["status"] == "fallback" and runtime.blocked
    second, _ = invoke(packet, [], runtime=runtime)
    assert second["reason"] == "request_budget_blocked"


def test_cache_hit_and_each_identity_change_misses(packet, tmp_path):
    cache = AdviceCache(tmp_path / "advice.sqlite3")
    first, _ = invoke(packet, [hcx(INTERPRETED)], runtime=RoleRuntime(enabled=True, cache=cache))
    again, runtime = invoke(packet, [], runtime=RoleRuntime(enabled=True, cache=cache))
    assert first["value"] == again["value"] and again["cache_hit"] and runtime.calls == 0
    for field in VERSIONS:
        changed = copy.deepcopy(packet)
        changed["versions"][field] = "changed"
        value, runtime = invoke(
            changed, [hcx(INTERPRETED)], runtime=RoleRuntime(enabled=True, cache=cache)
        )
        assert not value["cache_hit"] and runtime.calls == 1
    for settings in ({"prompt_version": "changed"}, {"model_revision": "changed"}):
        _, runtime = invoke(
            packet, [hcx(INTERPRETED)], runtime=RoleRuntime(enabled=True, cache=cache, **settings)
        )
        assert runtime.calls == 1
    _, runtime = invoke(
        packet, [hcx(INTERPRETED)], model="HCX-003", runtime=RoleRuntime(enabled=True, cache=cache)
    )
    assert runtime.calls == 1
    changed = copy.deepcopy(packet)
    changed["sources"]["prior:0"] = "added context"
    _, runtime = invoke(changed, [hcx(INTERPRETED)], runtime=RoleRuntime(enabled=True, cache=cache))
    assert runtime.calls == 1


def test_cache_expiry_and_failure_not_cached(packet, tmp_path):
    now = [0]
    cache = AdviceCache(tmp_path / "advice.sqlite3", ttl_seconds=1, clock=lambda: now[0])
    invoke(packet, [hcx(INTERPRETED)], runtime=RoleRuntime(enabled=True, cache=cache))
    now[0] = 2
    _, runtime = invoke(
        packet, [httpx.Response(429)], runtime=RoleRuntime(enabled=True, cache=cache)
    )
    assert runtime.calls == 1
    _, runtime = invoke(packet, [hcx(INTERPRETED)], runtime=RoleRuntime(enabled=True, cache=cache))
    assert runtime.calls == 1


def test_fault_preserves_saved_candidates_and_user_confirmation(store, packet, tmp_path):
    discovery = TableDiscovery(
        SearchIndex(store, corpus=build_search_documents(store)),
        CandidateStore(tmp_path / "candidates.sqlite3"),
    )
    context = {"session_id": "s", "request_id": "r", "query_revision": 1}
    search = discovery.search_tables(QUERY, context)
    set_id = search["interaction"]["candidate_set_id"]
    before = discovery.repository.get(set_id, context)
    result, _ = invoke(packet, [httpx.Response(429)])
    assert result["status"] == "fallback"
    assert discovery.repository.get(set_id, context) == before
    assert search["status"] == "awaiting_user" and search["result"] is None
    confirmed = discovery.confirm_tables(set_id, [1], context)
    assert confirmed["status"] == "ok" and confirmed["result"]["tables"]


def test_full_prompt_preflight_and_secret_echo(packet):
    huge = copy.deepcopy(packet)
    huge["sources"]["query"] = "가" * 7600
    result, runtime = invoke(huge, [], model="HCX-003")
    assert result["reason"] == "input_preflight_limit" and runtime.calls == 0
    result, runtime = invoke(
        packet, [hcx({"leak": "synthetic-test-secret"})], key="synthetic-test-secret"
    )
    assert result["reason"] == "unsafe_provider_response"
    assert "synthetic-test-secret" not in json.dumps(runtime.summary())


def test_semantic_guard_reuses_explicit_negation():
    query = "원화 말고 달러"
    packet = make_packet(query, VERSIONS)
    value = {
        "mentions": [
            {
                "kind": "condition",
                "axis": "currency",
                "polarity": "affirmed",
                "span": {"source_id": "query", "start": 0, "end": 2, "quote": "원화"},
            }
        ],
        "expansions": [],
    }
    result, runtime = invoke(
        packet, [hcx(value)], runtime=RoleRuntime(Limits(max_retries=0), enabled=True)
    )
    assert result["reason"] == "explicit_condition_conflict"
    assert runtime.trace[0]["schema_valid"] and runtime.trace[0]["source_grounding_valid"]


def test_assistant_question_is_not_a_user_condition():
    packet = make_packet(
        "물가", VERSIONS, prior_turns=[{"role": "assistant", "text": "원화 기준인가요?"}]
    )
    value = {
        "mentions": [
            {
                "kind": "condition",
                "axis": "currency",
                "polarity": "affirmed",
                "span": {"source_id": "prior:0", "start": 0, "end": 2, "quote": "원화"},
            }
        ],
        "expansions": [],
    }
    result, _ = invoke(
        packet, [hcx(value)], runtime=RoleRuntime(Limits(max_retries=0), enabled=True)
    )
    assert result["reason"] == "query_span_mismatch"


def test_same_request_budget_is_shared_across_roles(packet):
    runtime = RoleRuntime(Limits(max_calls=1), enabled=True)
    invoke(packet, [hcx(INTERPRETED)], runtime=runtime)
    result, _ = invoke(packet, [], role="DecisionClassifier", runtime=runtime)
    assert result["reason"] == "call_token_or_cost_budget" and runtime.calls == 1


def test_missing_metadata_allows_only_uncited_insufficient(packet):
    packet["references"] = {}
    value = {
        "judgments": [
            {"doc_id": KEY, "condition_id": "c1", "verdict": "insufficient", "citations": []}
        ]
    }
    result, _ = invoke(packet, [hcx(value)], role="EvidenceJudge")
    assert result["status"] == "advice"
    value["judgments"][0]["verdict"] = "support"
    result, _ = invoke(
        packet,
        [hcx(value)],
        role="EvidenceJudge",
        runtime=RoleRuntime(Limits(max_retries=0), enabled=True),
    )
    assert result["reason"] == "missing_supporting_citation"
