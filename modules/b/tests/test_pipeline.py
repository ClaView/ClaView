"""Offline contract, subgraph, transport, and response-integrity checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from claview_b.contracts import Config, StageFailure, Trace
from claview_b.evaluation import condition_counts, scripted_answers
from claview_b.fetch import KosisClient, build_api_request, fetch_data, normalize_rows
from claview_b.graphs import build_b_graph
from claview_b.metadata import MetadataStore, periods_between
from claview_b.models import HcxModel, ScriptedModel, object_schema
from claview_b.search import SearchIndex
from claview_b.stages import (
    Services,
    canonicalize_selections,
    expand_selections,
    select_series,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "modules/b/tests/fixtures"
GOLD = json.loads((ROOT / "eval/cases/golden/golden_set_v1.json").read_text(encoding="utf-8-sig"))


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
        (ROOT / "eval/cases/golden/golden_set_v1.schema.json").read_text(encoding="utf-8-sig")
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


@pytest.mark.parametrize("bad", ["missing_axis", "unknown_code", "extra_field"])
def test_b_rejects_invalid_output(store, bad):
    c = case()
    responses = scripted_answers(c)
    raw = responses["B:301:DT_401Y018"][0]
    row = raw["selections"][0]
    if bad == "missing_axis":
        del row["dimensions"]["objL2"]
    elif bad == "unknown_code":
        row["dimensions"]["objL1"] = "INVALID"
    else:
        row["decision"] = "select"
    result = select_series(c["query"], c["expected"]["A"], services(store, responses=responses))
    assert result["status"] == "error" and result["result"] is None


def test_b_drops_a_row_the_model_repeated_and_reports_it(store):
    """A repeated identical row adds no series: B keeps one and says so (it used to stop)."""
    c = case()
    responses = scripted_answers(c)
    raw = responses["B:301:DT_401Y018"][0]
    raw["selections"].append(copy.deepcopy(raw["selections"][0]))
    result = select_series(c["query"], c["expected"]["A"], services(store, responses=responses))
    assert result["status"] == "ok", result
    assert len(result["result"]["selections"]) == len(
        scripted_answers(c)["B:301:DT_401Y018"][0]["selections"]
    )
    assert [n["reason"] for n in result["result"]["expand_notices"]] == ["repeated_row_dropped"]


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
    monkeypatch.setattr("claview_b.models.time.sleep", lambda _: None)
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


# B parts of two tests removed with old A in plan 26 (step 4); the old A halves were dropped.
def test_b_function_and_subgraph_agree(store):
    c = case()
    a_result = c["expected"]["A"]
    b = select_series(c["query"], a_result, services(store))
    bg = build_b_graph(services(store)).invoke({"query": c["query"], "a_result": a_result})[
        "stage_outcome"
    ]
    assert b["status"] == bg["status"] == "ok"
    assert condition_counts(b["result"]) == condition_counts(c["expected"]["B"])
    assert b["result"] == bg["result"]


def test_fetch_of_golden_b_is_ok_and_an_empty_response_is_no_data(store):
    c = case()
    ok = fetch_data(c["expected"]["B"], store, FakeData(c["expected"]["B"]["selections"]), Trace())
    assert ok["status"] == "ok"
    result = fetch_data(c["expected"]["B"], store, FakeData([], empty=True), Trace())
    assert result["status"] == "no_data"
    assert result["result"]["series"]
