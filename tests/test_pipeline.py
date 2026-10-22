"""Offline contract, subgraph, transport, and response-integrity checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from claview.contracts import Config, StageFailure, Trace
from claview.evaluation import condition_counts, scripted_answers
from claview.fetch import KosisClient, build_api_request, fetch_data, normalize_rows
from claview.graphs import build_a_graph, build_b_graph, invoke_pipeline
from claview.metadata import MetadataStore, periods_between
from claview.models import HcxModel, ScriptedModel, object_schema
from claview.search import SearchIndex
from claview.stages import (
    Services,
    canonicalize_selections,
    checked_query_plan,
    expand_selections,
    select_series,
    select_tables,
    validate_a_conditions,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
GOLD = json.loads((ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8-sig"))


def case(number=19):
    return copy.deepcopy(next(c for c in GOLD["cases"] if c["case_id"] == f"CV-AB-{number:02d}"))


@pytest.fixture
def store():
    return MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")


def services(store, c=None, config=None, responses=None):
    c = c or case()
    return Services(
        store,
        SearchIndex(store),
        ScriptedModel(scripted_answers(c) if responses is None else responses),
        config or Config(),
        Trace(),
    )


def test_golden_schema_is_unchanged_contract():
    schema = json.loads(
        (ROOT / "eval/cases/golden_set_v1.schema.json").read_text(encoding="utf-8-sig")
    )
    Draft202012Validator(schema).validate(GOLD)
    broken = copy.deepcopy(GOLD)
    broken["cases"][0]["expected"]["A"]["decision"] = "select"
    assert list(Draft202012Validator(schema).iter_errors(broken))


def test_depth_and_full_path_are_catalog_metadata(store):
    doc = store.catalog["301:DT_401Y018"]
    assert doc["depth"] == len(doc["path_ids"]) - 1
    assert doc["path_names"][-1]
    meta = store.get("301", "DT_401Y018")
    dim = meta["dimensions"]["objL1"]["values"]["13102134729ACC_CD.110AA"]
    assert dim["parent_code"] and len(dim["path"]) > 1


def test_independent_functions_and_subgraphs_agree(store):
    c = case()
    a = select_tables(c["query"], services(store))
    ag = build_a_graph(services(store)).invoke({"query": c["query"]})["stage_outcome"]
    assert a["result"] == ag["result"] == c["expected"]["A"]
    b = select_series(c["query"], a["result"], services(store))
    bg = build_b_graph(services(store)).invoke({"query": c["query"], "a_result": a["result"]})[
        "stage_outcome"
    ]
    assert b["status"] == bg["status"] == "ok"
    assert condition_counts(b["result"]) == condition_counts(c["expected"]["B"])
    assert b["result"] == bg["result"]


def test_parent_state_keeps_outcomes_and_does_not_expose_expected(store):
    svc = services(store)
    state = invoke_pipeline(case()["query"], svc, selection_mode="legacy")
    assert state["completed_stage"] == "B"
    assert set(state["stage_outcomes"]) == {"A", "B"}
    assert "model_output" not in state and "table_metadata" not in state
    for event in svc.trace.events:
        if event["event"] == "model_request":
            assert "expected" not in event["context"] and "case_id" not in event["context"]
    assert "decision" not in state["a_result"] and "decision" not in state["b_result"]


def test_invalid_a_stops_before_b(store):
    svc = services(store, responses={"A": [{"selected_doc_ids": ["301:INVALID"]}]})
    state = invoke_pipeline(case()["query"], svc, selection_mode="legacy")
    assert state["response"]["status"] == "error"
    assert set(state["stage_outcomes"]) == {"A"}
    assert not any(e.get("task", "").startswith("B:") for e in svc.trace.events)


@pytest.mark.parametrize("bad", ["missing_axis", "unknown_code", "duplicate", "extra_field"])
def test_b_rejects_invalid_output(store, bad):
    c = case()
    responses = scripted_answers(c)
    raw = responses["B:301:DT_401Y018"][0]
    row = raw["selections"][0]
    if bad == "missing_axis":
        del row["dimensions"]["objL2"]
    elif bad == "unknown_code":
        row["dimensions"]["objL1"] = "INVALID"
    elif bad == "duplicate":
        raw["selections"].append(copy.deepcopy(row))
    else:
        row["decision"] = "select"
    result = select_series(c["query"], c["expected"]["A"], services(store, responses=responses))
    assert result["status"] == "error" and result["result"] is None


def test_stated_period_comes_from_the_question_not_the_model(store):
    """The question states its years, so B computes the period itself (like a relative one);
    whatever period text the model wrote (even an invalid one) is not used."""
    c = case()
    responses = scripted_answers(c)
    row = responses["B:301:DT_401Y018"][0]["selections"][0]
    row["period"] = {"frequency": "Q", "start": "20241", "end": "202404"}
    result = select_series(c["query"], c["expected"]["A"], services(store, responses=responses))
    assert result["status"] == "ok"
    assert {s["period"]["start"] for s in result["result"]["selections"]} == {"202401"}


def test_relative_period_is_not_filled_from_gold(store):
    """The relative period is resolved from the table's own metadata, never taken
    from the gold answer. Since the 2026-10-06 flow change B no longer stops at
    needs_confirmation: it hands the resolved period to the model and carries on
    (here the empty fixture then fails at the model step, proving B went on)."""
    svc = services(store, responses={})
    result = select_series("최근 3년 수입물가 월별 비교", case()["expected"]["A"], svc)
    assert result["status"] == "error"
    assert result["issues"][0]["code"] == "fixture_response_missing"
    request = next(e for e in svc.trace.events if e["event"] == "model_request")
    assert request["context"]["resolved_period"]["frequency"] == "M"


def test_three_axes_and_multiple_tables(store):
    c = case(14)
    c["query"] = "2020년부터 2024년까지 전산업 매출액영업이익률 연도별 표"
    result = select_series(c["query"], c["expected"]["A"], services(store, c))
    assert result["status"] == "ok"
    assert len(result["result"]["selections"][0]["dimensions"]) == 3
    c1, c2 = case(), case(21)
    both = {"tables": c1["expected"]["A"]["tables"] + c2["expected"]["A"]["tables"]}
    responses = {**scripted_answers(c1), **scripted_answers(c2)}
    svc = services(store, responses=responses)
    state = build_b_graph(svc).invoke(
        {"query": "2024년 월별 수입과 국내공급 물가 비교", "a_result": both}
    )
    assert state["stage_outcome"]["status"] == "ok"
    rows = state["stage_outcome"]["result"]["selections"]
    assert len(rows) == 5 and len({r["series_id"] for r in rows}) == 5


def test_parent_codes_are_not_discarded(store):
    table = store.get("301", "DT_401Y018")
    parent = next(
        code
        for code, value in table["dimensions"]["objL1"]["values"].items()
        if not value["is_leaf"]
    )
    raw = scripted_answers(case())["B:301:DT_401Y018"][0]
    raw["selections"] = raw["selections"][:1]
    raw["selections"][0]["dimensions"]["objL1"] = parent
    result = canonicalize_selections(expand_selections(raw, table), table, Config())
    assert result[0]["dimensions"]["objL1"]["code"] == parent


def test_unit_missing_and_conflicting(store):
    table = copy.deepcopy(store.get("301", "DT_401Y018"))
    raw = scripted_answers(case())["B:301:DT_401Y018"][0]
    table["units"] = []
    for value in table["items"].values():
        value["unit"] = None
    for axis in table["dimensions"].values():
        for value in axis["values"].values():
            value["unit"] = None
    plain = expand_selections(raw, table)
    assert canonicalize_selections(plain, table, Config())[0]["unit"] is None
    first = raw["selections"][0]
    table["items"][first["item_id"]]["unit"] = "원"
    table["dimensions"]["objL1"]["values"][first["dimensions"]["objL1"]]["unit"] = "%"
    with pytest.raises(StageFailure, match="unit_conflict"):
        canonicalize_selections(plain, table, Config())


def test_routing_budget_and_global_fallback(store):
    config = Config(hierarchy=True, max_route_rounds=2, stop_table_count=0)
    responses = scripted_answers(case())
    path = store.catalog["301:DT_401Y018"]["path_ids"]
    responses["route"] = [{"selected_node_ids": [path[1]]}, {"selected_node_ids": [path[2]]}]
    svc = services(store, config=config, responses=responses)
    state = build_a_graph(svc).invoke({"query": case()["query"]})
    assert state["stage_outcome"]["status"] == "ok" and state["route_round"] == 2
    assert {c["doc_id"] for c in state["global_candidates"]} <= {
        c["doc_id"] for c in state["merged_candidates"]
    }
    responses = scripted_answers(case())
    responses["route"] = [{"selected_node_ids": ["INVALID"]}]
    svc = services(store, config=config, responses=responses)
    state = build_a_graph(svc).invoke({"query": case()["query"]})
    assert state["stage_outcome"]["status"] == "ok" and state["route_failed"]


def test_channel_union_protects_original_and_does_not_stack_expressions(store, monkeypatch):
    search = SearchIndex(store)
    ids = sorted(store.catalog)

    def ranked(query, limit, allowed):
        if query == "original":
            return {
                "lexical": [{"doc_id": ids[0], "score": 4}, {"doc_id": ids[1], "score": 3}],
                "documents": [{"doc_id": ids[2], "score": 2, "chunk_id": "c2"}],
            }, None
        return {
            "lexical": [{"doc_id": ids[0], "score": 9}, {"doc_id": ids[2], "score": 8}],
            "documents": [{"doc_id": ids[1], "score": 7, "chunk_id": "c1"}],
        }, None

    monkeypatch.setattr(search, "_ranked_channels", ranked)
    rows = search.retrieve_pool("original", ["extra"], 2, 3, 1)
    assert {r["doc_id"] for r in rows} == set(ids)
    assert len(rows) == 3
    by_id = {r["doc_id"]: r for r in rows}
    assert len([e for e in by_id[ids[0]]["evidence"] if e["channel"] == "lexical"]) == 2
    assert by_id[ids[0]]["score"] == round(1 / 61, 8)
    with pytest.raises(StageFailure, match="original_candidate_budget_exceeded"):
        search.retrieve_pool("original", [], 2, 2, 0)


def test_structured_plan_grounding_and_fallback_preserve_query(store):
    query = "2024년 전산업 매출 대비 영업이익 비율을 연도별로 보여줘"
    plan = {
        "indicators": [{"source_quote": "매출 대비 영업이익 비율", "term": "매출대비영업이익비율"}],
        "subjects": [{"source_quote": "전산업", "term": "전산업"}],
        "statistical_basis": [],
        "explicit_conditions": [{"source_quote": "2024년", "term": "2024년"}],
        "search_queries": ["매출대비영업이익비율"],
        "ambiguities": [],
    }
    checked = checked_query_plan(query, plan)
    assert checked["original_query"] == query
    assert checked["additional_expressions"] == [query + " 매출대비영업이익비율"]
    bad = copy.deepcopy(plan)
    bad["subjects"][0]["term"] = "가계"
    with pytest.raises(StageFailure, match="query_plan_changed_condition"):
        checked_query_plan(query, bad)
    with pytest.raises(StageFailure, match="query_plan_unverified_term"):
        checked_query_plan(
            "GDP 성장률",
            {
                **plan,
                "indicators": [{"source_quote": "GDP 성장률", "term": "GDP 디플레이터"}],
                "subjects": [],
                "explicit_conditions": [],
                "search_queries": ["GDP 디플레이터"],
            },
        )
    alias_plan = copy.deepcopy(plan)
    alias_plan["indicators"][0]["term"] = "매출액영업이익률"
    alias_plan["search_queries"] = ["매출액영업이익률"]
    assert checked_query_plan(query, alias_plan, {("매출 대비 영업이익 비율", "매출액영업이익률")})[
        "additional_expressions"
    ]
    bad = copy.deepcopy(plan)
    bad["indicators"][0]["term"] = "실질 매출액영업이익률"
    with pytest.raises(StageFailure, match="query_plan_changed_condition"):
        checked_query_plan(query, bad)
    config = Config(structure_query=True)
    svc = services(store, config=config, responses=scripted_answers(case()))
    result = select_tables(case()["query"], svc)
    assert result["status"] == "ok"
    assert any(e["event"] == "a_query_plan_fallback" for e in svc.trace.events)
    assert (
        next(e for e in svc.trace.events if e["event"] == "a_search")["query_plan"][
            "original_query"
        ]
        == case()["query"]
    )
    grounded = {
        "indicators": [{"source_quote": "물가 흐름", "term": "물가흐름"}],
        "subjects": [],
        "statistical_basis": [],
        "explicit_conditions": [],
        "search_queries": ["물가흐름"],
        "ambiguities": [],
    }
    responses = scripted_answers(case())
    responses["A:plan"] = [grounded]
    structured = services(store, config=config, responses=responses)
    assert select_tables(case()["query"], structured)["status"] == "ok"
    assert next(e for e in structured.trace.events if e["event"] == "a_query_plan")["plan"][
        "additional_expressions"
    ]


def test_a_modes_budget_conditions_and_function_graph_parity(store):
    c = case()
    path = store.catalog["301:DT_401Y018"]["path_ids"]
    responses = scripted_answers(c)
    responses["route"] = [{"selected_node_ids": [path[1]]}]
    config = Config(search_scope="hierarchy", max_route_rounds=1)
    function = select_tables(c["query"], services(store, config=config, responses=responses))
    graph = build_a_graph(services(store, config=config, responses=responses)).invoke(
        {"query": c["query"]}
    )
    assert function["status"] == graph["stage_outcome"]["status"]
    assert function["result"] == graph["stage_outcome"]["result"]
    assert graph["global_candidates"] == []
    assert graph["route_round"] == 1

    responses["route"] = [{"selected_node_ids": ["INVALID"]}]
    fallback = build_a_graph(services(store, config=config, responses=responses)).invoke(
        {"query": c["query"]}
    )
    assert fallback["route_failed"] and fallback["stage_outcome"]["status"] == "ok"

    limited = services(store, config=Config(model_candidate_limit=1))
    stopped = select_tables(c["query"], limited)
    assert stopped["status"] == "unresolved"
    assert stopped["issues"][0]["code"] == "model_candidate_budget_exceeded"
    assert any(e["event"] == "a_input_refused" for e in limited.trace.events)

    unsupported_period = select_tables(
        "2030년 수입물가지수 월별 자료", services(store, responses=scripted_answers(c))
    )
    assert unsupported_period["status"] == "unresolved"
    assert unsupported_period["issues"][0]["code"] == "metadata_refresh_required"


def test_a_condition_unknown_metadata_and_negative_frequency(store, monkeypatch):
    search = SearchIndex(store)
    doc_id = "301:DT_401Y018"
    original_get = store.get
    table = copy.deepcopy(original_get("301", "DT_401Y018"))
    table["periods"] = {"Y": table["periods"]["Y"]}
    monkeypatch.setattr(
        store, "get", lambda org, tid: table if tid == "DT_401Y018" else original_get(org, tid)
    )
    validate_a_conditions("2024년 연도별 수입물가, 월별 자료는 제외", [doc_id], search)

    def missing(org, tid):
        if tid == "DT_401Y018":
            raise StageFailure("metadata_missing", "unresolved")
        return original_get(org, tid)

    monkeypatch.setattr(store, "get", missing)
    with pytest.raises(StageFailure, match="a_metadata_incomplete"):
        validate_a_conditions("2024년 월별 수입물가", [doc_id], search)


def synthetic_rows(selection):
    p = selection["period"]
    return [
        {
            "ORG_ID": selection["org_id"],
            "TBL_ID": selection["table_id"],
            "ITM_ID": selection["item_id"],
            **{a.replace("objL", "C"): v["code"] for a, v in selection["dimensions"].items()},
            "PRD_SE": p["frequency"],
            "PRD_DE": period,
            "DT": f"100.{i:02d}",
            "UNIT_NM": selection["unit"],
        }
        for i, period in enumerate(periods_between(p["start"], p["end"], p["frequency"]))
    ]


def test_fetch_matches_codes_and_preserves_decimal(store):
    row = case()["expected"]["B"]["selections"][0]
    result, issues = normalize_rows(row, synthetic_rows(row), store.snapshot)
    assert not issues and result["points"][0]["value"] == "100.00"
    assert result["coverage"]["expected"] == 12
    assert "apiKey" not in build_api_request(row)
    assert periods_between("202304", "202401", "Q") == ["202304", "202401"]


@pytest.mark.parametrize("mutation", ["code", "duplicate", "period", "unit"])
def test_fetch_rejects_wrong_series_or_response(store, mutation):
    row = case()["expected"]["B"]["selections"][0]
    rows = synthetic_rows(row)
    if mutation == "code":
        rows[0]["C1"] = "OTHER"
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "period":
        rows[0]["PRD_DE"] = "202501"
    else:
        rows[0]["UNIT_NM"] = "%"
    with pytest.raises(StageFailure):
        normalize_rows(row, rows, store.snapshot)


class FakeData:
    def __init__(self, rows, drop=False, empty=False):
        self.rows = rows
        self.drop = drop
        self.empty = empty

    def fetch(self, params, trace):
        if self.empty:
            return []
        row = next(r for r in self.rows if build_api_request(r) == params)
        result = synthetic_rows(row)
        if self.drop:
            result[0]["DT"] = "-"
        trace.add("data_response", mode="synthetic_numeric_fixture", rows=result)
        return result


def test_full_connection_and_partial_blocks_c(store):
    c = case()
    client = FakeData(c["expected"]["B"]["selections"])
    svc = services(store)
    state = invoke_pipeline(
        c["query"], svc, data_client=client, through="fetch", selection_mode="legacy"
    )
    assert state["completed_stage"] == "fetch"
    assert state["stage_outcomes"]["fetch"]["status"] == "ok"
    called = []
    client.drop = True
    result = invoke_pipeline(
        c["query"],
        services(store),
        selection_mode="legacy",
        data_client=client,
        renderer=lambda *_: called.append(True),
        through="C",
    )
    assert result["response"]["status"] == "partial"
    assert not called and result["completed_stage"] == "B"
    result = fetch_data(c["expected"]["B"], store, FakeData([], empty=True), Trace())
    assert result["status"] == "no_data"
    assert result["result"]["series"]


@pytest.mark.parametrize(
    "finish,expected", [("length", "output_truncated"), ("tool_calls", "unexpected_finish_reason")]
)
def test_hcx_truncation_and_schema_payload(finish, expected):
    trace = Trace()

    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"effort": "none"}
        assert "tools" not in body and body["responseFormat"]["type"] == "json"
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": finish, "message": {"content": "{}"}},
            },
        )

    model = HcxModel("test-only-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(StageFailure, match=expected):
        model.complete("A", "system", {"query": "question"}, object_schema({}), 10, trace)
    assert "test-only-key" not in json.dumps(trace.events)
    model.close()


def test_http_retry_boundaries_and_safe_errors(monkeypatch):
    monkeypatch.setattr("claview.models.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"finishReason": "stop", "message": {"content": "{}"}},
            },
        )

    model = HcxModel("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert model.complete("A", "s", {}, object_schema({}), 10, Trace()) == {}
    assert len(calls) == 2
    model.close()
    calls.clear()

    def unauthorized(request):
        calls.append(1)
        return httpx.Response(401, text="secret-bearing error")

    model = HcxModel("test-key", client=httpx.Client(transport=httpx.MockTransport(unauthorized)))
    with pytest.raises(StageFailure, match="hcx_http_401") as exc:
        model.complete("A", "s", {}, object_schema({}), 10, Trace())
    assert len(calls) == 1 and "secret" not in str(exc.value)
    model.close()


def test_kosis_parameters_and_non_list_error():
    params = build_api_request(case()["expected"]["B"]["selections"][0])
    trace = Trace()

    def handler(request):
        assert request.url.params["prdSe"] == "M"
        assert request.url.params["objL2"]
        return httpx.Response(200, json={"err": "20", "errMsg": "unknown"})

    client = KosisClient("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(StageFailure, match="kosis_service_error"):
        client.fetch(params, trace)
    assert "test-key" not in json.dumps(trace.events)
    client.close()
