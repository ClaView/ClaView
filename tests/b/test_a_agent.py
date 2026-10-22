"""Offline A-agent lifecycle and boundary tests. Synthetic outputs do not measure model quality."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from claview.b.a_agent import AAgent, AgentConfig
from claview.b.a_discovery import TableDiscovery, run_b
from claview.b.a_model_providers import AProvider
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import StageFailure, Trace, outcome
from claview.b.graphs import build_discovery_graph, invoke_pipeline
from claview.b.metadata import MetadataStore
from claview.b.models import ScriptedModel
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents
from claview.b.stages import Services

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/b/fixtures"
CTX = {"session_id": "agent-test", "request_id": "one", "query_revision": 1}
RUNNER = None


@pytest.fixture(scope="module", autouse=True)
def loop_before_network_guard():
    global RUNNER
    with asyncio.Runner() as runner:
        runner.get_loop()
        RUNNER = runner
        yield
    RUNNER = None


@pytest.fixture
def factory(tmp_path):
    clients = []
    calls = []

    def make(
        *,
        action="ask_clarification",
        config=None,
        semantic=True,
        handlers=None,
        database=None,
        provider_revision="fixture/1",
    ):
        path = tmp_path / "catalog.json"
        catalog = json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))
        if semantic:
            for row, title in zip(
                catalog["documents"],
                ("국내총생산 명목", "국내총생산 실질", "생산자물가"),
                strict=True,
            ):
                row.update(title=title, search_text=title)
        path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        store = MetadataStore(path, FIXTURES / "metadata.jsonl")
        search = SearchIndex(store, corpus=build_search_documents(store))
        providers = {}
        for role in ("QueryInterpreter", "EvidenceJudge", "DecisionClassifier"):

            def handler(request, role=role):
                packet = json.loads(json.loads(request.content)["messages"][-1]["content"])
                calls.append((role, packet))
                if handlers and role in handlers:
                    value = handlers[role](packet)
                    if isinstance(value, httpx.Response):
                        return value
                elif role == "QueryInterpreter":
                    value = {"mentions": [], "expansions": []}
                elif role == "EvidenceJudge":
                    value = {
                        "judgments": [
                            {
                                "doc_id": key,
                                "condition_id": c["condition_id"],
                                "verdict": "insufficient",
                                "citations": [],
                            }
                            for key in packet["candidate_ids"]
                            for c in packet["conditions"]
                        ]
                    }
                else:
                    value = {"action": action}
                return httpx.Response(
                    200,
                    json={
                        "status": {"code": "20000"},
                        "result": {
                            "message": {"content": json.dumps(value, ensure_ascii=False)},
                            "finishReason": "stop",
                            "usage": {"promptTokens": 300, "completionTokens": 80},
                        },
                    },
                )

            providers[role] = AProvider("HCX-007", transport=httpx.MockTransport(handler))
            clients.append(providers[role])
        agent = AAgent(
            config or AgentConfig(enabled=True),
            providers,
            run_async=RUNNER.run,
            provider_revision=provider_revision,
        )
        discovery = TableDiscovery(
            search, CandidateStore(database or tmp_path / "sets.sqlite"), agent=agent
        )
        return discovery, calls, path

    yield make
    for client in clients:
        RUNNER.run(client.close())


def start(discovery, query="국내총생산", context=None):
    result = discovery.search_tables(query, context or CTX)
    assert result["status"] == "awaiting_user" and result["result"] is None
    return result["interaction"]


def test_clarification_preserves_root_and_rechecks_new_revision(factory):
    discovery, calls, _ = factory()
    pending = start(discovery)
    assert pending["kind"] == "clarification"
    assert pending["clarification"]["axis"] == "price_basis"
    set_id = pending["candidate_set_id"]
    with pytest.raises(StageFailure, match="clarification_required"):
        discovery.confirm_tables(set_id, [1], CTX)
    result = discovery.clarify_tables(set_id, "real", CTX)
    page = result["interaction"]
    assert page["context"]["query_revision"] == 2
    assert page["kind"] == "table_selection"
    assert page["original_query"] == "국내총생산"
    assert page["clarified_context"][0]["label"] == "실질"
    assert all("명목" not in item["table_name"] for item in page["options"])
    bundle = discovery.repository.get(page["candidate_set_id"], page["context"])
    assert "실질" in bundle["search_query"]
    assert bundle["agent_state"]["b_deferred"] == ["ITEM", "classification", "period", "frequency"]
    with pytest.raises(StageFailure, match="stale_query"):
        discovery.confirm_tables(set_id, [1], CTX)
    count = len(calls)
    confirmed = discovery.confirm_tables(page["candidate_set_id"], [1], page["context"])
    assert set(confirmed["result"]) == {"tables"}
    assert discovery.confirm_tables(page["candidate_set_id"], [1], page["context"]) == confirmed
    assert len(calls) == count


def test_repeat_search_pages_and_restart_do_not_repeat_model_or_retrieval(factory, monkeypatch):
    discovery, calls, _ = factory(action="show_candidates")
    page = start(discovery)
    before = len(calls)
    resumed, _, _ = factory(action="show_candidates", database=discovery.repository.path)
    monkeypatch.setattr(resumed, "retrieve", lambda *_: pytest.fail("Repeated retrieval"))
    assert start(resumed)["candidate_set_id"] == page["candidate_set_id"]
    resumed.get_candidates(page["candidate_set_id"], CTX, page_size=1)
    resumed.get_candidates(page["candidate_set_id"], CTX, page=2, page_size=1)
    assert len(calls) == before


def test_graph_interrupt_clarify_then_selection_and_restart(factory, monkeypatch):
    discovery, calls, _ = factory()
    graph = build_discovery_graph(discovery, InMemorySaver())
    config = {"configurable": {"thread_id": "meaning"}}
    first = graph.invoke({"query": "국내총생산", "context": CTX}, config)
    assert first["__interrupt__"][0].value["interaction"]["kind"] == "clarification"
    second = graph.invoke(Command(resume="real"), config)
    assert second["context"]["query_revision"] == 2
    assert second["__interrupt__"][0].value["interaction"]["kind"] == "table_selection"
    before = len(calls)
    monkeypatch.setattr(discovery, "retrieve", lambda *_: pytest.fail("Repeated retrieval"))
    selected = graph.invoke(Command(resume=[1]), config)
    assert selected["stage_outcome"]["status"] == "ok"
    again = build_discovery_graph(discovery).invoke(
        {"context": second["context"], "candidate_set_id": second["candidate_set_id"]}
    )
    assert again["stage_outcome"] == selected["stage_outcome"]
    assert len(calls) == before


@pytest.mark.parametrize("action", ["show_candidates", "enrich_metadata", "insufficient"])
def test_decisions_are_bounded_advice_not_confirmation(factory, action):
    discovery, calls, _ = factory(action=action)
    page = start(discovery)
    bundle = discovery.repository.get(page["candidate_set_id"], CTX)
    assert bundle["a_outcome"] is None
    assert bundle["agent_state"]["metadata_rounds"] <= 1
    assert bundle["agent_state"]["research_rounds"] <= 1
    assert len(calls) <= 4
    assert not bundle["b_attempts"]
    assert all("rank_signals" not in row for row in page["options"])
    assert page["model_usage"]["external_calls"] == 0


@pytest.mark.parametrize("limit", [0, 1])
def test_whole_request_budget_falls_back_without_losing_candidates(factory, limit):
    discovery, calls, _ = factory(
        action="enrich_metadata", config=AgentConfig(enabled=True, max_calls=limit)
    )
    page = start(discovery)
    assert page["options"]
    assert len(calls) <= limit
    assert page["termination_reason"] == "call_token_or_cost_budget"


def test_provider_failure_is_saved_and_not_retried_on_restart(factory):
    discovery, calls, _ = factory(handlers={"DecisionClassifier": lambda _: httpx.Response(429)})
    page = start(discovery)
    assert page["termination_reason"] == "quota"
    before = len(calls)
    assert start(discovery)["candidate_set_id"] == page["candidate_set_id"]
    assert len(calls) == before


def test_crash_after_model_before_publish_cannot_repeat_call(factory, monkeypatch):
    discovery, calls, _ = factory()

    def crash(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(discovery, "save_candidates", crash)
    with pytest.raises(KeyboardInterrupt):
        start(discovery)
    before = len(calls)
    with pytest.raises(StageFailure, match="in_progress_or_indeterminate"):
        start(discovery)
    assert len(calls) == before


@pytest.mark.parametrize("answer", ["USD", "", None, {"choice": "real"}])
def test_invalid_answer_does_not_advance_revision(factory, answer):
    discovery, _, _ = factory()
    page = start(discovery)
    with pytest.raises(StageFailure, match="invalid_clarification_answer"):
        discovery.clarify_tables(page["candidate_set_id"], answer, CTX)
    assert discovery.get_candidates(page["candidate_set_id"], CTX)["context"] == CTX


def test_policy_model_revision_and_other_session_cannot_confirm_old_set(factory):
    discovery, _, _ = factory(action="show_candidates")
    page = start(discovery)
    with pytest.raises(StageFailure, match="context_mismatch"):
        discovery.confirm_tables(page["candidate_set_id"], [1], {**CTX, "session_id": "other"})
    changed, _, _ = factory(
        action="show_candidates", database=discovery.repository.path, provider_revision="fixture/2"
    )
    with pytest.raises(StageFailure, match="snapshot_mismatch"):
        changed.confirm_tables(page["candidate_set_id"], [1], CTX)


def test_default_off_matches_baseline_and_never_uses_injected_provider(factory):
    discovery, calls, _ = factory(config=AgentConfig(enabled=False))
    page = start(discovery)
    assert "model_usage" not in page
    assert not calls
    assert discovery.agent is None


def test_item_period_only_does_not_become_a_clarification(factory):
    discovery, _, _ = factory(semantic=False)
    page = start(discovery, "2024년 수입물가 원화 월별")
    assert page["kind"] == "table_selection"
    assert page["clarification"] is None


def test_invalid_id_and_unconfirmed_b_stub_boundary(factory):
    discovery, calls, _ = factory(action="show_candidates")
    page = start(discovery)
    with pytest.raises(StageFailure, match="selection_outside_candidates"):
        discovery.confirm_tables(page["candidate_set_id"], ["301:NOT_OFFICIAL"], CTX)
    invoked = []

    def stub(query, a_result, services):
        invoked.append((query, a_result))
        return outcome("B", {"selections": []})

    services = Services(
        discovery.search.store, discovery.search, ScriptedModel({}), discovery.config, Trace()
    )
    blocked = run_b(discovery, page["candidate_set_id"], CTX, services, runner=stub)
    assert blocked["response"]["issues"][0]["code"] == "a_not_confirmed"
    assert not invoked
    discovery.confirm_tables(page["candidate_set_id"], [1], CTX)
    before = len(calls)
    result = run_b(discovery, page["candidate_set_id"], CTX, services, runner=stub)
    assert result["response"]["status"] == "ok" and len(invoked) == 1
    assert len(calls) == before
    assert result["a_context"]["original_query"] == "국내총생산"


def test_live_provider_is_rejected_without_dispatch():
    provider = AProvider("HCX-007")
    try:
        with pytest.raises(StageFailure, match="not_adopted"):
            AAgent(AgentConfig(enabled=True), {"DecisionClassifier": provider})
    finally:
        RUNNER.run(provider.close())


def test_duplicate_clarification_recovers_same_child_and_conflicting_answer_is_rejected(factory):
    discovery, calls, _ = factory()
    parent = start(discovery)
    child = discovery.clarify_tables(parent["candidate_set_id"], "real", CTX)
    before = len(calls)
    assert discovery.clarify_tables(parent["candidate_set_id"], "real", CTX) == child
    with pytest.raises(StageFailure, match="answer_conflict"):
        discovery.clarify_tables(parent["candidate_set_id"], "nominal", CTX)
    assert len(calls) == before


def test_expansion_preserves_initial_pool_and_obeys_single_shared_addition_budget(
    factory, monkeypatch
):
    def expand(packet):
        return {
            "mentions": [],
            "expansions": [
                {
                    "term": "국내공급",
                    "span": {
                        "source_id": "query",
                        "start": 0,
                        "end": 2,
                        "quote": packet["sources"]["query"][:2],
                    },
                }
            ],
        }

    discovery, calls, _ = factory(
        semantic=False, action="show_candidates", handlers={"QueryInterpreter": expand}
    )
    original = discovery.retrieve
    initial_ids = []

    def bounded(query):
        rows, diagnostics = original(query)
        rows = rows[:1]
        initial_ids.extend(row["doc_id"] for row in rows)
        diagnostics["mapping_resolution"]["trace"].append(
            {"action": "deferred", "source_spans": [{"start": 0, "end": 2}]}
        )
        diagnostics["final_pool"]["added_count"] = 0
        return rows, diagnostics

    monkeypatch.setattr(discovery, "retrieve", bounded)
    page = start(discovery, "수입물가 원화")
    bundle = discovery.repository.get(page["candidate_set_id"], CTX)
    pool = {c["doc_id"] for c in bundle["candidate_pool"]}
    assert set(initial_ids) <= pool
    assert pool <= discovery.search.store.catalog.keys()
    assert len(pool) <= discovery.config.candidate_pool_limit
    assert len(pool - set(initial_ids)) <= discovery.config.expansion_limit
    assert bundle["agent_state"]["research_rounds"] == 1
    assert sum(role == "QueryInterpreter" for role, _ in calls) == 1
    assert bundle["original_query"] == "수입물가 원화"


def test_malformed_or_unofficial_expansion_never_reaches_search(factory, monkeypatch):
    discovery, calls, _ = factory(
        handlers={
            "QueryInterpreter": lambda packet: {
                "mentions": [],
                "expansions": [
                    {
                        "term": "301:DT_FORGED",
                        "span": {
                            "source_id": "query",
                            "start": 0,
                            "end": 2,
                            "quote": packet["sources"]["query"][:2],
                        },
                    }
                ],
            }
        }
    )
    original = discovery.retrieve

    def deferred(query):
        rows, diag = original(query)
        diag["mapping_resolution"]["trace"].append({"action": "deferred", "source_spans": [1]})
        return rows, diag

    monkeypatch.setattr(discovery, "retrieve", deferred)
    page = start(discovery)
    bundle = discovery.repository.get(page["candidate_set_id"], CTX)
    assert bundle["agent_state"]["research_rounds"] == 0
    assert page["termination_reason"] == "generated_official_id"
    assert len(calls) == 2


def test_model_support_cannot_restore_official_conflicting_candidate(factory):
    def supported(packet):
        result = []
        for key in packet["candidate_ids"]:
            ref_id, ref = next(
                (rid, ref) for rid, ref in packet["references"].items() if ref["doc_id"] == key
            )
            result.extend(
                {
                    "doc_id": key,
                    "condition_id": condition["condition_id"],
                    "verdict": "support",
                    "citations": [{"ref_id": ref_id, "quote": ref["quote"]}],
                }
                for condition in packet["conditions"]
            )
        return {"judgments": result}

    discovery, _, _ = factory(action="show_candidates", handlers={"EvidenceJudge": supported})
    page = start(discovery, "국내총생산 실질")
    assert all("명목" not in option["table_name"] for option in page["options"])
    with pytest.raises(StageFailure, match="selection_outside_candidates"):
        discovery.confirm_tables(page["candidate_set_id"], ["301:DT_401Y018"], CTX)


def test_agent_off_graph_and_function_share_condition_assessment(factory):
    discovery, calls, _ = factory(config=AgentConfig(enabled=False))
    direct = start(discovery)
    graph = build_discovery_graph(discovery).invoke(
        {"query": "국내총생산", "context": {**CTX, "request_id": "graph"}}
    )
    assert [r["doc_id"] for r in direct["options"]] == [
        r["doc_id"] for r in graph["stage_outcome"]["interaction"]["options"]
    ]
    assert not calls


def test_cli_fresh_process_clarification_confirm_and_no_b(tmp_path, factory):
    _, _, catalog = factory()
    fixture = tmp_path / "responses.json"
    fixture.write_text(
        json.dumps(
            {
                "QueryInterpreter": [{"mentions": [], "expansions": []}],
                "DecisionClassifier": [{"action": "ask_clarification"}],
            }
        ),
        encoding="utf-8",
    )
    db = tmp_path / "cli.sqlite"
    common = [
        "--db",
        str(db),
        "--session",
        "cli",
        "--request",
        "one",
        "--catalog",
        str(catalog),
        "--metadata",
        str(FIXTURES / "metadata.jsonl"),
        "--a-agent-mode",
        "mock",
        "--a-responses",
        str(fixture),
    ]

    def call(command, *args, success=True):
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                "-m",
                "claview.b.discovery_cli",
                command,
                *common,
                *args,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert (process.returncode == 0) == success, process.stderr
        return json.loads(process.stdout) if success else process.stderr

    first = call("search-tables", "--query", "국내총생산")["interaction"]
    assert first["kind"] == "clarification"
    assert "clarification_required" in call(
        "confirm-tables", "--set-id", first["candidate_set_id"], "--select", "1", success=False
    )
    child = call("clarify-tables", "--set-id", first["candidate_set_id"], "--answer", "real")[
        "interaction"
    ]
    assert child["context"]["query_revision"] == 2
    page = call("list-candidates", "--set-id", child["candidate_set_id"], "--revision", "2")
    assert page["kind"] == "table_selection"
    confirmed = call(
        "confirm-tables", "--set-id", child["candidate_set_id"], "--revision", "2", "--select", "1"
    )
    assert confirmed["status"] == "ok"
    status = call("candidate-status", "--set-id", child["candidate_set_id"], "--revision", "2")
    assert status["b_attempts"] == []
    assert status["original_query"] == "국내총생산" and "실질" in status["search_query"]
    assert status["agent_state"]["model_usage"]["external_calls"] == 0


def test_completed_agent_search_and_graph_replay_keep_confirmation(factory):
    discovery, calls, _ = factory(action="show_candidates")
    page = start(discovery)
    confirmed = discovery.confirm_tables(page["candidate_set_id"], [1], CTX)
    before = len(calls)
    replay = discovery.search_tables("국내총생산", CTX)
    assert replay["result"] == confirmed["result"]
    graph = build_discovery_graph(discovery).invoke({"query": "국내총생산", "context": CTX})
    assert graph["stage_outcome"]["result"] == confirmed["result"]
    assert graph["candidate_set_id"] == page["candidate_set_id"]
    assert len(calls) == before


def test_pipeline_a_only_can_clarify_and_resume_without_b(factory):
    discovery, _, _ = factory()
    services = Services(
        discovery.search.store, discovery.search, ScriptedModel({}), discovery.config, Trace()
    )
    first = invoke_pipeline(
        "국내총생산",
        services,
        through="A",
        context=CTX,
        repository=discovery.repository,
        agent=discovery.agent,
    )
    second = invoke_pipeline(
        "국내총생산",
        services,
        through="A",
        context=CTX,
        candidate_set_id=first["candidate_set_id"],
        clarification_answer="real",
        repository=discovery.repository,
        agent=discovery.agent,
    )
    assert second["query_revision"] == 2
    final = invoke_pipeline(
        "국내총생산",
        services,
        through="A",
        context={**CTX, "query_revision": 2},
        candidate_set_id=second["candidate_set_id"],
        selection=[1],
        repository=discovery.repository,
        agent=discovery.agent,
    )
    assert final["response"]["completed_stage"] == "A"
    assert "B" not in final["stage_outcomes"]


def test_agent_claim_precedes_provider_dispatch(factory):
    def reentrant(packet):
        with pytest.raises(StageFailure, match="in_progress_or_indeterminate"):
            discovery.search_tables("국내총생산", CTX)
        return {"action": "show_candidates"}

    discovery, calls, _ = factory(handlers={"DecisionClassifier": reentrant})
    start(discovery)
    assert sum(role == "DecisionClassifier" for role, _ in calls) == 1
