"""Compatibility and safe A-to-B transitions after importing the B bundle."""

import json
from pathlib import Path

import httpx
import pytest

from claview import a_graphs, a_stages, graphs, services, stages
from claview.a_discovery import TableDiscovery
from claview.candidate_store import CandidateStore
from claview.contracts import StageFailure, Trace
from claview.evaluation import scripted_answers
from claview.fetch import KosisClient
from claview.metadata import MetadataStore, periods_between
from claview.models import ScriptedModel
from claview.search import SearchIndex
from claview.search_documents import build_search_documents

FIXTURES = Path(__file__).parent / "fixtures"
GOLD = json.loads(
    (Path(__file__).parents[2] / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8-sig")
)


def test_legacy_imports_share_one_implementation():
    assert stages.ASteps is a_stages.ASteps
    assert stages.select_tables is a_stages.select_tables
    assert stages.Services is services.Services
    assert graphs.build_a_graph is a_graphs.build_a_graph
    assert graphs.build_discovery_graph is a_graphs.build_discovery_graph
    assert stages.ASteps.__module__ == "claview.a_stages"
    assert stages.BSteps.__module__ == "claview.stages"


class _MonthlyClient:
    """Answers any request with one well-formed row per requested month."""

    def __init__(self):
        self.requests = []

    def fetch(self, params, trace):
        self.requests.append(params)
        return [
            {
                "ORG_ID": params["orgId"],
                "TBL_ID": params["tblId"],
                "ITM_ID": params["itmId"],
                "PRD_SE": "M",
                "PRD_DE": period,
                "DT": "1",
                "C1": params.get("objL1", ""),
                "C2": params.get("objL2", ""),
            }
            for period in periods_between(params["startPrdDe"], params["endPrdDe"], "M")
        ]


def test_b_period_notice_is_stored_and_pipeline_proceeds_to_fetch(tmp_path, monkeypatch):
    """2026-10-06 flow change: when the table's latest data lags what a relative
    period ("최근 3년") implies, B stores the notice (status "needs_confirmation")
    and the pipeline goes on to fetch with the period that is actually available.
    The notice only informs -- nothing asks the user whether to continue."""
    store = MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")
    search = SearchIndex(store, corpus=build_search_documents(store))
    repository = CandidateStore(tmp_path / "candidates.sqlite3")
    discovery = TableDiscovery(search, repository)
    gold = next(c for c in GOLD["cases"] if c["case_id"] == "CV-AB-19")
    responses = {"B:301:DT_401Y018": scripted_answers(gold)["B:301:DT_401Y018"]}
    svc = services.Services(store, search, ScriptedModel(responses), discovery.config, Trace())
    query = "최근 3년 수입물가 월별 비교"
    context = {"session_id": "session", "request_id": "request", "query_revision": 1}

    initial = graphs.invoke_pipeline(query, svc, context=context, repository=repository)
    assert initial["response"]["status"] == "awaiting_user"
    client = _MonthlyClient()
    kwargs = dict(
        context=context,
        repository=repository,
        candidate_set_id=initial["candidate_set_id"],
        selection=["301:DT_401Y018"],
        through="fetch",
        data_client=client,
    )
    result = graphs.invoke_pipeline(query, svc, **kwargs)

    assert result["response"]["status"] == "ok"
    assert result["response"]["stopped_stage"] == "fetch"
    assert result["response"]["completed_stage"] == "fetch"
    assert "confirmation" not in result["response"]
    (notice,) = result["response"]["notices"]
    assert notice["status"] == "needs_confirmation"
    assert notice["code"] == "period_requires_confirmation"
    assert notice["table"] == "301:DT_401Y018"
    assert "?" not in notice["message"]
    resolved = notice["resolved_period"]
    assert result["b_result"]["period_notices"] == [notice]
    assert result["data_result"]["period_notices"] == [notice]
    assert client.requests, "fetch must have run"
    assert {(r["startPrdDe"], r["endPrdDe"]) for r in client.requests} == {
        (resolved["start"], resolved["end"])
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("A stored B result must not be recomputed")

    monkeypatch.setattr(search, "retrieve_pool_with_diagnostics", forbidden)
    monkeypatch.setattr(stages.BSteps, "context", forbidden)
    monkeypatch.setattr(svc.model, "complete", forbidden)
    kwargs["repository"] = CandidateStore(repository.path)
    resumed = graphs.invoke_pipeline(query, svc, retry_b=True, **kwargs)
    assert resumed["stage_outcomes"]["B"] == result["stage_outcomes"]["B"]
    assert resumed["b_execution"]["reused"]
    assert resumed["response"]["notices"] == [notice]
    saved = repository.get(initial["candidate_set_id"], context)
    assert saved["original_query"] == query
    assert len(saved["b_attempts"]) == 1
    attempt = saved["b_attempts"][0]
    assert attempt["status"] == "succeeded"
    assert attempt["outcome"]["result"]["period_notices"] == [notice]


@pytest.mark.parametrize(
    "payload", [{"err": "20", "errMsg": "echo dummy-secret"}, [{"DT": "dummy-secret"}]]
)
def test_reflected_credentials_never_enter_b_fetch_trace(payload):
    trace = Trace()
    client = KosisClient(
        "dummy-secret",
        retries=0,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ),
    )
    try:
        with pytest.raises(StageFailure, match="kosis_credential_reflected"):
            client.fetch({"prdSe": "M"}, trace)
        assert "dummy-secret" not in json.dumps(trace.events)
        assert trace.events[-1]["event"] == "data_response_redacted"
    finally:
        client.close()
