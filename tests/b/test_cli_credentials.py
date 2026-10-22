"""Exercise credential boundaries with synthetic values and no external transport."""

from pathlib import Path

import httpx
import pytest

from claview.b import cli
from claview.b.contracts import StageFailure, Trace
from claview.b.models import HcxModel, object_schema

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_KEY = "fixture-credential-must-not-appear-in-output"


def forbid(*args, **kwargs):
    raise AssertionError("Unexpected credential prompt or data load")


@pytest.fixture(autouse=True)
def synthetic_environment(monkeypatch):
    for name in ("CLOVASTUDIO_API_KEY", "KOSIS_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def live_b_args(tmp_path):
    mappings = tmp_path / "mappings.json"
    definitions = tmp_path / "definitions.json"
    mappings.write_text('{"mappings": []}', encoding="utf8")
    definitions.write_text('{"definitions": []}', encoding="utf8")
    return [
        "run-b",
        "--b-mode",
        "live",
        "--allow-external",
        "--session",
        "credential-test",
        "--request",
        "query-1",
        "--set-id",
        "stored-fixture",
        "--db",
        str(tmp_path / "candidates.sqlite3"),
        "--catalog",
        str(FIXTURES / "catalog.json"),
        "--metadata",
        str(FIXTURES / "metadata.jsonl"),
        "--mappings",
        str(mappings),
        "--definitions",
        str(definitions),
    ]
















def test_legacy_cli_missing_key_fails_before_data(monkeypatch, capsys):
    monkeypatch.setattr(cli, "MetadataStore", forbid)
    monkeypatch.setattr(
        cli.sys, "argv", ["claview", "evaluate", "--mode", "live", "--allow-external"]
    )
    with pytest.raises(SystemExit):
        cli.main()
    assert "Missing environment variables: CLOVASTUDIO_API_KEY" in capsys.readouterr().err


def test_transport_error_never_records_credentials(tmp_path):
    class FailingClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("Private transport detail: " + SYNTHETIC_KEY)

    trace = Trace()
    model = HcxModel(SYNTHETIC_KEY, client=FailingClient(), retries=0, min_interval=0)
    with pytest.raises(StageFailure, match="hcx_transport_error"):
        model.complete("A", "fixture", {"query": "fixture question"}, object_schema({}), 32, trace)
    assert SYNTHETIC_KEY not in trace.save(tmp_path).read_text(encoding="utf8")
