"""Every contract fixture parses as its contract model (common/fixtures, built from the golden 22)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claview_common.contracts import A_HTTP_VERSION, AHTTPSelectRequest, BRequest, Outcome

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load(kind: str) -> list[tuple[str, dict]]:
    files = sorted((FIXTURES / kind).glob("*.json"))
    assert files, f"no fixtures in {kind}/ (run pipeline/scripts/build_fixtures.py)"
    return [(f.stem, json.loads(f.read_text(encoding="utf-8"))) for f in files]


def test_every_golden_case_has_requests():
    names = {name for name, _ in load("a_request")}
    assert len(names) == 22 and names == {name for name, _ in load("b_request")}


@pytest.mark.parametrize(("name", "body"), load("a_request"))
def test_a_request(name, body):
    AHTTPSelectRequest(contract_version=A_HTTP_VERSION, **body)




@pytest.mark.parametrize(("name", "body"), load("b_request"))
def test_b_request(name, body):
    BRequest(**body)


@pytest.mark.parametrize(("kind", "stage"), [("b_result", "B"), ("data_result", "fetch"), ("view", "C")])
def test_envelopes(kind, stage):
    for name, body in load(kind):
        outcome = Outcome(**body)
        assert outcome.stage == stage and outcome.status, name
        if outcome.ok:
            assert isinstance(outcome.result, dict), name


def test_saved_real_data_results_have_series():
    for name, body in load("data_result_saved"):
        assert body["series"] and all(s["points"] for s in body["series"]), name


def test_json_schemas_export():
    from claview_common import contracts

    for model in (
        contracts.AHTTPSelectRequest,
        contracts.AHTTPResult,
        contracts.BRequest,
        contracts.BResult,
        contracts.FetchResult,
        contracts.CRequest,
        contracts.ViewModel,
    ):
        assert model.model_json_schema()["title"] == model.__name__


@pytest.mark.parametrize(("name", "body"), load("a_view"))
def test_a_view_focus_provenance_stays_outside_b_table_fields(name, body):
    from claview_common.contracts.a_view import AView

    view = AView.model_validate(body)
    provenance = view.statistics[0].focus_provenance
    assert provenance["source"] in {"model_scope", "legacy_quote"}, name
    table = view.handoff.ready[0].tables[0].model_dump()
    assert table["focus"] == view.statistics[0].focus
    assert set(table) == {"org_id", "table_id", "table_name", "focus"}
