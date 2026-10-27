"""POST /c/present over the data_result fixtures (synthetic) and C's saved real KOSIS results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claview_c_api.api import app

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "common" / "fixtures"
GOLDEN = ROOT / "eval" / "cases" / "golden" / "golden_set_v1.json"
QUERIES = {c["case_id"]: c["query"] for c in json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["service"] == "module-c"


@pytest.mark.parametrize("path", sorted((FIXTURES / "data_result").glob("*.json")), ids=lambda p: p.stem)
def test_present_synthetic(client, path):
    data = json.loads(path.read_text(encoding="utf-8"))["result"]
    out = client.post("/c/present", json={"query": QUERIES[path.stem], "data_result": data}).json()["outcome"]
    expected = json.loads((FIXTURES / "view" / path.name).read_text(encoding="utf-8"))
    assert out["stage"] == "C" and out["status"] == expected["status"]
    assert out["result"]["default_view_id"] == expected["result"]["default_view_id"]


@pytest.mark.parametrize("path", sorted((FIXTURES / "data_result_saved").glob("*.json")), ids=lambda p: p.stem)
def test_present_saved_real_results(client, path):
    case = "-".join(path.stem.split("_")[0].split("-")[:3])
    data = json.loads(path.read_text(encoding="utf-8"))
    out = client.post("/c/present", json={"query": QUERIES[case], "data_result": data}).json()["outcome"]
    assert out["status"] in ("ok", "partial") and out["result"]["views"]
