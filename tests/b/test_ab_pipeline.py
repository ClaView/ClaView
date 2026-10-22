"""A v1 (claview.aflow) -> B v6 connection over the existing A.tables schema. Offline only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claview.ab_pipeline import B_TABLE_FIELDS, load_a_catalog, run_ab, to_b_a_result
from claview.aflow import ARunConfig
from claview.contracts import Config, Trace
from claview.evaluation import scripted_answers
from claview.metadata import MetadataStore, periods_between
from claview.models import ScriptedModel
from claview.search import SearchIndex
from claview.services import Services

ROOT = Path(__file__).resolve().parents[2]
GOLD = {
    c["case_id"]: c
    for c in json.loads((ROOT / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8-sig"))[
        "cases"
    ]
}
AS_OF = "2026-10-01"


class SyntheticDataClient:
    """One row per requested period with value 1; synthetic, never a statistics result."""

    def __init__(self):
        self.requests = []

    def fetch(self, params, trace):
        self.requests.append(params)
        dims = {k.replace("objL", "C"): v for k, v in params.items() if k.startswith("objL")}
        return [
            {
                "ORG_ID": params["orgId"],
                "TBL_ID": params["tblId"],
                "ITM_ID": params["itmId"],
                "PRD_SE": params["prdSe"],
                "PRD_DE": period,
                "DT": "1",
                **dims,
            }
            for period in periods_between(params["startPrdDe"], params["endPrdDe"], params["prdSe"])
        ]


class ForbiddenModel:
    def complete(self, *args, **kwargs):
        raise AssertionError("B must not be called")


@pytest.fixture(scope="module")
def a_catalog():
    return load_a_catalog(ROOT)


@pytest.fixture(scope="module")
def store():
    return MetadataStore(
        ROOT / "data/catalog/search_catalog.json", ROOT / "data/raw/boK_meta_raw.jsonl"
    )


def make_services(store, model):
    return Services(store, SearchIndex(store), model, Config(as_of=AS_OF), Trace())


def test_b_gets_only_the_existing_table_schema_in_a_order():
    handoff = {
        "query": "q",
        "tables": [
            {"org_id": "301", "table_id": "DT_B", "table_name": "b", "pick_source": "rule"},
            {"org_id": "301", "table_id": "DT_A", "table_name": "a", "scope_ids": ["r1"]},
        ],
        "scopes": [{"scope_id": "r1"}],
        "matched_items": [{"item_id": "x"}],
    }
    assert to_b_a_result(handoff) == {
        "tables": [
            {"org_id": "301", "table_id": "DT_B", "table_name": "b"},
            {"org_id": "301", "table_id": "DT_A", "table_name": "a"},
        ]
    }


def test_a_and_b_read_the_same_stored_catalog(a_catalog, store):
    b_tables = {key.split(":")[1] for key in store.catalog}
    assert set(a_catalog.meta_store) == b_tables


def test_empty_a_selection_stops_before_b(a_catalog, store):
    final = run_ab("zzqx wvvk", make_services(store, ForbiddenModel()), a_catalog)
    assert final["response"]["stopped_stage"] == "A"
    assert final["response"]["status"] == "unresolved"
    assert final["a_v1"]["b_handoff"]["tables"] == []
    assert "B" not in final["stage_outcomes"]


def test_cv_ab_19_runs_a_then_b_then_fetch_offline(a_catalog, store):
    """Sample run: A model off, B scripted from the golden B answer, synthetic data."""
    case = GOLD["CV-AB-19"]
    answers = scripted_answers(case)
    svc = make_services(store, ScriptedModel({k: v for k, v in answers.items() if k != "A"}))
    client = SyntheticDataClient()
    final = run_ab(case["query"], svc, a_catalog, data_client=client, through="fetch")

    assert final["a_v1"]["config"]["model"] == "off"
    assert final["a_v1"]["config"]["as_of"] == AS_OF
    expected = [{k: t[k] for k in B_TABLE_FIELDS} for t in case["expected"]["A"]["tables"]]
    assert final["a_result"]["tables"] == expected
    requests = [e for e in svc.trace.events if e["event"] == "model_request"]
    assert [r["context"]["selected_tables"] for r in requests] == [expected] * len(requests)
    assert final["stage_outcomes"]["B"]["status"] == "ok"
    assert final["response"] | {"trace_ref": None} == {
        "status": "ok",
        "completed_stage": "fetch",
        "stopped_stage": "fetch",
        "issues": [],
        "trace_ref": None,
    }
    assert client.requests
    assert len(final["b_result"]["selections"]) == len(case["expected"]["B"]["selections"])


def test_a_model_on_without_client_is_rejected_or_falls_back(a_catalog, store):
    """An explicit model-on config without a client never makes a live call."""
    svc = make_services(store, ForbiddenModel())
    final = run_ab(
        GOLD["CV-AB-19"]["query"],
        svc,
        a_catalog,
        a_config=ARunConfig(model="on", as_of=AS_OF),
        through="A",
    )
    assert final["a_v1"]["config"]["model"] == "on"
    assert "missing_client" in json.dumps(final["a_v1"], ensure_ascii=False)
