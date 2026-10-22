"""Offline user-selection lifecycle, graph resume, and A/B contract tests."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from claview.a_discovery import TableDiscovery, assess_candidates, display_order, field_grams, run_b
from claview.candidate_store import CandidateStore
from claview.contracts import StageFailure, Trace, outcome
from claview.evaluation import scripted_answers
from claview.graphs import build_discovery_graph, invoke_pipeline
from claview.metadata import MetadataStore
from claview.models import ScriptedModel
from claview.search import SearchIndex
from claview.search_documents import build_search_documents
from claview.stages import Services

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
GOLD = json.loads((ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8-sig"))
CASE = next(c for c in GOLD["cases"] if c["case_id"] == "CV-AB-19")
CTX = {"session_id": "session", "request_id": "request", "query_revision": 1}


@pytest.fixture
def discovery(tmp_path):
    store = MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")
    index = SearchIndex(store, corpus=build_search_documents(store))
    return TableDiscovery(index, CandidateStore(tmp_path / "sets.sqlite3"))


def start(discovery, query=CASE["query"], context=None, **kwargs):
    result = discovery.search_tables(query, context or CTX, **kwargs)
    assert result["status"] == "awaiting_user" and result["result"] is None
    assert "decision" not in result
    return result["interaction"]["candidate_set_id"]


def svc(discovery, responses=None):
    return Services(
        discovery.search.store,
        discovery.search,
        ScriptedModel(responses or {}),
        discovery.config,
        Trace(),
    )


def forbid(*args, **kwargs):
    raise AssertionError("Unexpected repeated retrieval or model call")


def test_model_free_pool_order_pages_and_evidence(discovery, monkeypatch):
    set_id = start(discovery)
    bundle = discovery.repository.get(set_id, CTX)
    monkeypatch.setattr(discovery, "retrieve", forbid)
    first = discovery.get_candidates(set_id, CTX, 1, 1)
    second = discovery.get_candidates(set_id, CTX, 2, 1)
    assert first == discovery.get_candidates(set_id, CTX, 1, 1)
    assert first["options"][0]["number"] == 1
    assert second["options"][0]["number"] == 2
    assert [first["options"][0]["doc_id"], second["options"][0]["doc_id"]] == bundle[
        "display_order"
    ][:2]
    original = copy.deepcopy(bundle["candidate_pool"])
    assert display_order(original) == bundle["display_order"]
    assert original == bundle["candidate_pool"]
    for c in original:
        assert c["paths"][0]["depth"] == len(c["paths"][0]["node_ids"]) - 1
        assert c["summary"]["unit_scope"] == "per_related_field_not_table_wide"
        assert not c["summary"]["meaning_verified"]
        assert all("query_quote" in e and "source_field" in e for e in c["evidence"])
    assert "rank_signals" not in first["options"][0]


@pytest.mark.parametrize(
    "selection,code",
    [
        ([], "invalid_selection"),
        ([0], "invalid_selection"),
        ([999], "invalid_selection"),
        ([True], "invalid_selection"),
        ([{"org_id": "301", "table_id": "DT_401Y018", "table_name": "spoof"}], "invalid_selection"),
        (["301:OUTSIDE"], "selection_outside_candidates"),
    ],
)
def test_selection_rejections(discovery, selection, code):
    set_id = start(discovery)
    with pytest.raises(StageFailure, match=code):
        discovery.confirm_tables(set_id, selection, CTX)


@pytest.mark.parametrize(
    "field,value", [("session_id", "other"), ("request_id", "other"), ("query_revision", 2)]
)
def test_context_rejected_everywhere(discovery, field, value):
    set_id = start(discovery)
    context = {**CTX, field: value}
    for action in (
        lambda: discovery.get_candidates(set_id, context),
        lambda: discovery.confirm_tables(set_id, [1], context),
        lambda: run_b(discovery, set_id, context, svc(discovery)),
    ):
        with pytest.raises(StageFailure, match="context_mismatch"):
            action()


def test_stale_question_and_revision_conflict(discovery):
    set_id = start(discovery)
    with pytest.raises(StageFailure, match="query_revision_conflict"):
        start(discovery, "different question")
    start(discovery, "2024년 월별 국내공급물가", {**CTX, "query_revision": 2})
    with pytest.raises(StageFailure, match="stale_query"):
        discovery.confirm_tables(set_id, [1], CTX)
    assert discovery.repository.get(set_id, CTX)["original_query"] == CASE["query"]


def test_expiry_and_snapshot_changes_persist(discovery):
    now = [1000.0]
    discovery.repository.clock = lambda: now[0]
    set_id = start(discovery, ttl_seconds=5)
    now[0] += 6
    with pytest.raises(StageFailure, match="candidate_set_expired"):
        discovery.confirm_tables(set_id, [1], CTX)
    assert discovery.repository.get(set_id, CTX)["status"] == "expired"
    for snapshot in ("catalog", "metadata", "index", "configuration"):
        set_id = start(discovery)
        saved = discovery.snapshots[snapshot]
        discovery.snapshots[snapshot] = "changed"
        with pytest.raises(StageFailure, match="snapshot_mismatch"):
            discovery.confirm_tables(set_id, [1], CTX)
        assert discovery.repository.get(set_id, CTX)["status"] == "expired"
        discovery.snapshots[snapshot] = saved


def test_idempotence_normalizes_set_and_preserves_official_names(discovery, monkeypatch):
    set_id = start(discovery)
    ids = discovery.repository.get(set_id, CTX)["display_order"][:2]
    first = discovery.confirm_tables(set_id, [2, 1, 1], CTX)
    monkeypatch.setattr(discovery, "retrieve", forbid)
    assert discovery.confirm_tables(set_id, ids[::-1], CTX) == first
    for table in first["result"]["tables"]:
        assert (
            table["table_name"]
            == discovery.search.store.catalog[f"{table['org_id']}:{table['table_id']}"]["title"]
        )
    with pytest.raises(StageFailure, match="selection_conflict"):
        discovery.confirm_tables(set_id, [1], CTX)
    discovery.repository.clock = lambda: 1e12
    assert discovery.confirm_tables(set_id, ids, CTX) == first


def test_concurrent_confirms_are_atomic(discovery):
    set_id = start(discovery)
    barrier = Barrier(2)

    def confirm(value):
        barrier.wait()
        try:
            return discovery.confirm_tables(set_id, [value], CTX)
        except StageFailure as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(confirm, (1, 2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "selection_conflict" in results
    winner = discovery.repository.get(set_id, CTX)["selection"]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: discovery.confirm_tables(set_id, winner, CTX), range(2)))
    assert results[0] == results[1]


def test_all_channels_assessed_and_failures_fallback(discovery):
    class Channel:
        version = "fixture/1"

        def retrieve(self, query):
            return [{"doc_id": k} for k in discovery.search.store.catalog] + [
                {"doc_id": "301:fake"}
            ]

    class Failed:
        version = "fixture/1"

        def retrieve(self, query):
            raise RuntimeError("This detail must not be logged")

    base = start(discovery, "2024년 월별 수입물가")
    extended = TableDiscovery(
        discovery.search,
        discovery.repository,
        channels={"hierarchy": Channel(), "embedding_fixture": Channel(), "failed": Failed()},
    )
    set_id = start(extended, "2024년 월별 수입물가")
    bundle = extended.repository.get(set_id, CTX)
    baseline = discovery.repository.get(base, CTX)
    assert set(baseline["display_order"]) <= set(bundle["display_order"])
    assert (
        bundle["diagnostics"]["optional_channels"]["failed"]["failure"] == "optional_channel_failed"
    )
    annual = next(c for c in bundle["candidate_pool"] if c["table_id"] == "DT_501Y006")
    assert annual["conflicts"] and annual["doc_id"] not in bundle["display_order"]
    assert "301:fake" not in {c["doc_id"] for c in bundle["candidate_pool"]}
    assert (
        len(
            [
                e
                for e in annual["retrieval_signals"]
                if e["channel"] in {"hierarchy", "embedding_fixture"}
            ]
        )
        == 2
    )


def test_unknown_cache_bounds_and_missing_metadata_do_not_block_a(discovery):
    set_id = start(discovery, "2099년 월별 수입물가")
    result = discovery.confirm_tables(set_id, ["301:DT_401Y018"], CTX)
    assert result["status"] == "ok"
    candidate = next(
        c
        for c in discovery.repository.get(set_id, CTX)["candidate_pool"]
        if c["table_id"] == "DT_401Y018"
    )
    assert any(e["reason"] == "metadata_refresh_required" for e in candidate["unknowns"])
    search = discovery.search
    search.store.cache.pop("301:DT_401Y018", None)
    del search.store.records["301:DT_401Y018"]
    context = {**CTX, "query_revision": 2}
    set_id = start(discovery, context=context)
    assert discovery.confirm_tables(set_id, ["301:DT_401Y018"], context)["status"] == "ok"


def test_request_diagnostics_and_trace_are_isolated(discovery):
    barrier = Barrier(2)

    def search_one(i):
        query = ("2024년 월별 수입물가", "2024년 연간 기업 매출액")[i]
        context = {**CTX, "request_id": str(i)}
        discovery.search.retrieve_pool(query, [], 10, 30, 10)
        barrier.wait()
        assert discovery.search.last_retrieval["runs"][0]["expression"] == query
        set_id = start(discovery, query, context)
        return discovery.repository.get(set_id, context)

    with ThreadPoolExecutor(2) as pool:
        bundles = list(pool.map(search_one, range(2)))
    assert bundles[0]["trace"]["run_id"] != bundles[1]["trace"]["run_id"]
    for b in bundles:
        assert {e["query"] for e in b["trace"]["events"]} == {b["original_query"]}


def test_langgraph_real_interrupt_resume_and_restart(discovery, monkeypatch):
    graph = build_discovery_graph(discovery, InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    pending = graph.invoke({"query": CASE["query"], "context": CTX}, config)
    assert pending["__interrupt__"][0].value["status"] == "awaiting_user"
    monkeypatch.setattr(discovery, "retrieve", forbid)
    done = graph.invoke(Command(resume=["301:DT_401Y018"]), config)
    assert done["stage_outcome"]["result"] == CASE["expected"]["A"]
    restarted = build_discovery_graph(discovery).invoke(
        {"context": CTX, "candidate_set_id": pending["candidate_set_id"]}
    )
    assert restarted["stage_outcome"] == done["stage_outcome"]


def test_default_pipeline_waits_then_passes_original_a_to_b(discovery, monkeypatch):
    services = svc(discovery, scripted_answers(CASE))
    waiting = invoke_pipeline(CASE["query"], services, context=CTX, repository=discovery.repository)
    assert waiting["response"]["status"] == "awaiting_user"
    assert "a_result" not in waiting and "B" not in waiting["stage_outcomes"]
    assert not services.trace.events
    set_id = waiting["candidate_set_id"]
    monkeypatch.setattr(discovery.search, "retrieve_pool_with_diagnostics", forbid)
    done = invoke_pipeline(
        CASE["query"],
        services,
        context=CTX,
        repository=discovery.repository,
        candidate_set_id=set_id,
        selection=["301:DT_401Y018"],
    )
    assert done["a_result"] == CASE["expected"]["A"]
    assert done["response"]["status"] == "ok"
    assert len(done["b_result"]["selections"]) == 2
    saved = discovery.repository.get(set_id, CTX)
    request = next(
        e for e in saved["b_attempts"][0]["trace"]["events"] if e["event"] == "model_request"
    )
    assert request["context"]["query"] == CASE["query"]
    assert request["context"]["selected_tables"] == done["a_result"]["tables"]
    monkeypatch.setattr(services.model, "complete", forbid)
    repeated = invoke_pipeline(
        CASE["query"],
        services,
        context=CTX,
        repository=discovery.repository,
        candidate_set_id=set_id,
        selection=["301:DT_401Y018"],
    )
    assert repeated["b_result"] == done["b_result"]
    assert repeated["b_execution"]["reused"] is True
    assert repeated["b_execution"]["provider"] == "ScriptedModel"
    assert len(discovery.repository.get(set_id, CTX)["b_attempts"]) == 1


def test_b_failure_and_explicit_retry_keep_a(discovery, monkeypatch):
    set_id = start(discovery)
    a = discovery.confirm_tables(set_id, ["301:DT_401Y018"], CTX)
    monkeypatch.setattr(discovery, "retrieve", forbid)
    failed = run_b(discovery, set_id, CTX, svc(discovery))
    assert failed["a_result"] == a["result"] and failed["response"]["status"] != "ok"
    assert discovery.confirm_tables(set_id, ["301:DT_401Y018"], CTX) == a
    blocked = run_b(discovery, set_id, CTX, svc(discovery, scripted_answers(CASE)))
    assert blocked["response"]["issues"][0]["code"] == "b_retry_required"
    success = run_b(discovery, set_id, CTX, svc(discovery, scripted_answers(CASE)), retry=True)
    assert success["response"]["status"] == "ok"
    assert [a["status"] for a in discovery.repository.get(set_id, CTX)["b_attempts"]] == [
        "failed",
        "succeeded",
    ]


def test_b_in_progress_crash_lease_and_fencing(discovery):
    now = [1000.0]
    discovery.repository.clock = lambda: now[0]
    set_id = start(discovery)
    discovery.confirm_tables(set_id, [1], CTX)
    _, old, _ = discovery.repository.begin_b(set_id, CTX, discovery.snapshots)
    with pytest.raises(StageFailure, match="b_in_progress"):
        discovery.repository.begin_b(set_id, CTX, discovery.snapshots, retry=True)
    now[0] += 301
    assert discovery.repository.get(set_id, CTX)["b_attempts"][0]["status"] == "indeterminate"
    with pytest.raises(StageFailure, match="b_indeterminate"):
        discovery.repository.begin_b(set_id, CTX, discovery.snapshots)
    _, new, _ = discovery.repository.begin_b(set_id, CTX, discovery.snapshots, retry=True)
    with pytest.raises(StageFailure, match="b_attempt_superseded"):
        discovery.repository.finish_b(old, outcome("B", {"selections": []}), {})
    assert old != new
    discovery.snapshots["metadata"] = "new"
    with pytest.raises(StageFailure, match="snapshot_mismatch"):
        discovery.repository.begin_b(set_id, CTX, discovery.snapshots)
    assert discovery.repository.get(set_id, CTX)["a_outcome"]["status"] == "ok"


def test_multiple_tables_without_assuming_indicator_count(discovery):
    set_id = start(discovery, "2024년 월별 수입과 국내공급 물가 비교")
    a = discovery.confirm_tables(set_id, ["301:DT_401Y018", "301:DT_405Y006"], CTX)
    assert len(a["result"]["tables"]) == 2
    case21 = next(c for c in GOLD["cases"] if c["case_id"] == "CV-AB-21")
    responses = {**scripted_answers(CASE), **scripted_answers(case21)}
    result = run_b(discovery, set_id, CTX, svc(discovery, responses))
    assert result["response"]["status"] == "ok"
    assert len(result["b_result"]["selections"]) == 5


def test_official_duplicate_titles_have_distinct_ids_and_paths():
    metadata = ROOT / "data/raw/boK_meta_raw.jsonl"
    if not metadata.exists():
        pytest.skip("Optional full official metadata cache is not distributed with fixtures")
    store = MetadataStore(ROOT / "data/catalog/search_catalog.json", metadata)
    search = SearchIndex(store)
    ids = ["301:DT_101Y001", "301:DT_161Y007"]
    candidates, rejected = assess_candidates(
        "M2 상품별 구성내역", [{"doc_id": k} for k in ids], search
    )
    assert not rejected
    assert candidates[0]["table_name"] == candidates[1]["table_name"]
    assert candidates[0]["paths"] != candidates[1]["paths"]
    assert len(display_order(candidates)) == 2


def test_field_boundaries_do_not_create_matching_grams():
    assert "품과" not in field_grams("음식료품 > 과당")
    assert "과당" in field_grams("음식료품 > 과당")


def test_series_specific_coverage_is_not_flattened(discovery):
    record = discovery.search.store.records["301:DT_401Y018"]["meta"]["PRD"]
    record.append({**record[0], "STRT_PRD_DE": "2024.01", "series_fixture": "different_series"})
    set_id = start(discovery)
    candidate = next(
        c
        for c in discovery.repository.get(set_id, CTX)["candidate_pool"]
        if c["table_id"] == "DT_401Y018"
    )
    assert candidate["summary"]["cached_periods"] == {}
    assert len(candidate["summary"]["coverage_records"]) == len(record)
    assert any(e["reason"] == "coverage_varies_by_record" for e in candidate["unknowns"])


@pytest.mark.parametrize(
    "query", [None, "", " ", 4, "가" * 10001], ids=["null", "empty", "blank", "number", "too_long"]
)
def test_invalid_query(discovery, query):
    with pytest.raises(StageFailure, match="invalid_query"):
        discovery.search_tables(query, CTX)


def test_no_candidates_and_missing_bundle(discovery):
    with pytest.raises(StageFailure, match="retrieval_empty"):
        discovery.search_tables("zzzzzzzzzzzz", CTX)
    with pytest.raises(StageFailure, match="candidate_set_missing"):
        discovery.confirm_tables("absent", [1], CTX)


def test_same_service_new_requests_have_separate_context(discovery):
    services = svc(discovery)
    first = invoke_pipeline(CASE["query"], services, repository=discovery.repository)
    second = invoke_pipeline("2024년 월별 국내공급물가", services, repository=discovery.repository)
    assert first["request_id"] != second["request_id"]
    assert first["candidate_set_id"] != second["candidate_set_id"]
    assert not services.trace.events


def test_b_rejects_unconfirmed_and_records_interruption(discovery):
    set_id = start(discovery)
    waiting = run_b(discovery, set_id, CTX, svc(discovery), runner=forbid)
    assert waiting["response"]["issues"][0]["code"] == "a_not_confirmed"
    discovery.confirm_tables(set_id, [1], CTX)

    def interrupted(*args):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_b(discovery, set_id, CTX, svc(discovery), runner=interrupted)
    assert discovery.repository.get(set_id, CTX)["b_attempts"][0]["status"] == "indeterminate"


def test_vector_failure_keeps_lexical_and_document_channels(discovery):
    class FailedVector:
        def retrieve(self, *args):
            raise RuntimeError("Private exception detail")

    # Patch only retrieval to keep the configured index identity unchanged.
    discovery.search.vectors = FailedVector()
    discovery.search.vectors.embedder = type("Embedder", (), {"model_id": "fixture"})()
    set_id = start(discovery)
    diagnostics = discovery.repository.get(set_id, CTX)["diagnostics"]
    assert diagnostics["runs"][0]["warning"] == "vector_channel_failed"
    assert set(diagnostics["runs"][0]["channels"]) == {"lexical", "documents"}


def test_live_cli_requires_explicit_opt_in_before_loading_data(monkeypatch, capsys):
    from claview.discovery_cli import main

    monkeypatch.setattr("claview.discovery_cli.getpass.getpass", forbid)
    with pytest.raises(SystemExit) as exc:
        main(["run-b", "--b-mode", "live"])
    assert exc.value.code == 2
    assert "--allow-external" in capsys.readouterr().err


def test_cli_fresh_process_search_page_confirm_status_b_retry(tmp_path):
    db = tmp_path / "process.sqlite3"
    common = [
        "--db",
        str(db),
        "--session",
        "s",
        "--request",
        "r",
        "--catalog",
        str(FIXTURES / "catalog.json"),
        "--metadata",
        str(FIXTURES / "metadata.jsonl"),
    ]

    def call(command, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "claview.discovery_cli", command, *common, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    pending = call("search-tables", "--query", CASE["query"])
    set_id = pending["interaction"]["candidate_set_id"]
    first = call("list-candidates", "--set-id", set_id, "--page-size", "1")
    second = call("list-candidates", "--set-id", set_id, "--page-size", "1", "--page", "2")
    assert first["options"][0]["number"] == 1 and second["options"][0]["number"] == 2
    confirmed = call("confirm-tables", "--set-id", set_id, "--select", "301:DT_401Y018")
    assert confirmed["result"] == CASE["expected"]["A"]
    assert call("candidate-status", "--set-id", set_id)["status"] == "confirmed"
    responses = tmp_path / "b.json"
    responses.write_text("{}", encoding="utf-8")
    failure = call("run-b", "--set-id", set_id, "--b-responses", str(responses))
    assert failure["response"]["status"] != "ok"
    responses.write_text(json.dumps(scripted_answers(CASE)), encoding="utf-8")
    resumed = call("run-b", "--set-id", set_id, "--b-responses", str(responses), "--retry")
    assert resumed["response"]["status"] == "ok"


def test_diagrams_cli_uses_user_selection_a_by_default(tmp_path):
    output = tmp_path / "graphs.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "claview.cli",
            "diagrams",
            "--catalog",
            str(FIXTURES / "catalog.json"),
            "--metadata",
            str(FIXTURES / "metadata.jsonl"),
            "--mappings",
            str(ROOT / "data/mappings/review/검색용_의미매핑_초안.json"),
            "--definitions",
            str(ROOT / "data/catalog/search_definitions.json"),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    diagram = json.loads(output.read_text(encoding="utf-8"))["A"]
    assert "retrieve" in diagram and "await_selection" in diagram
    assert "finalize_selection" in diagram and "route(" not in diagram
