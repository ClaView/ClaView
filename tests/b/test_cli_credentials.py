"""Exercise credential boundaries with synthetic values and no external transport."""

import argparse
import getpass
import json
from pathlib import Path

import httpx
import pytest

from claview.b import cli, discovery_cli
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


@pytest.mark.parametrize("value", [None, "", "   "])
def test_live_b_missing_key_fails_before_data_or_store(tmp_path, monkeypatch, capsys, value):
    if value is not None:
        monkeypatch.setenv("CLOVASTUDIO_API_KEY", value)
    monkeypatch.setattr(discovery_cli, "CandidateStore", forbid)
    monkeypatch.setattr(discovery_cli.getpass, "getpass", forbid)
    with pytest.raises(SystemExit) as exc:
        discovery_cli.main(live_b_args(tmp_path))
    assert exc.value.code == 2
    assert "Missing environment variables: CLOVASTUDIO_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "candidates.sqlite3").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_live_b_env_key_reaches_provider_without_tty_or_disclosure(
    tmp_path, monkeypatch, capsys, failure
):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setattr(discovery_cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(discovery_cli.getpass, "getpass", forbid)
    received, closed = [], []

    class Provider:
        def __init__(self, key):
            received.append(key)

        def close(self):
            closed.append(True)

    def run_b_stub(*args):
        if failure:
            raise StageFailure("hcx_http_401")
        return {"status": "fixture_only", "external_calls": 0}

    monkeypatch.setattr(discovery_cli, "HcxModel", Provider)
    monkeypatch.setattr(discovery_cli, "run_b", run_b_stub)
    output = tmp_path / "result.json"
    args = live_b_args(tmp_path) + ["--output", str(output)]
    if failure:
        with pytest.raises(SystemExit) as exc:
            discovery_cli.main(args)
        assert exc.value.code == 2
    else:
        discovery_cli.main(args)
        assert json.loads(output.read_text(encoding="utf8"))["external_calls"] == 0
    assert received == [SYNTHETIC_KEY]
    assert closed == [True]
    captured = capsys.readouterr()
    assert SYNTHETIC_KEY not in captured.out + captured.err
    if output.exists():
        assert SYNTHETIC_KEY not in output.read_text(encoding="utf8")
    assert SYNTHETIC_KEY.encode() not in (tmp_path / "candidates.sqlite3").read_bytes()


def test_env_key_never_enables_live_b_without_opt_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setattr(discovery_cli, "CandidateStore", forbid)
    args = live_b_args(tmp_path)
    args.remove("--allow-external")
    with pytest.raises(SystemExit):
        discovery_cli.main(args)
    captured = capsys.readouterr()
    assert "--allow-external" in captured.err
    assert SYNTHETIC_KEY not in captured.out + captured.err


def test_explicit_prompt_can_override_environment(monkeypatch, capsys):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setattr(discovery_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(discovery_cli.getpass, "getpass", lambda _: "synthetic-hidden-input")
    assert discovery_cli._hcx_key(argparse.ArgumentParser(), True) == "synthetic-hidden-input"
    assert SYNTHETIC_KEY not in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure", [EOFError(), getpass.GetPassWarning("fixture input unavailable")]
)
def test_failed_hidden_input_has_controlled_error(monkeypatch, capsys, failure):
    def prompt(_):
        raise failure

    monkeypatch.setattr(discovery_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(discovery_cli.getpass, "getpass", prompt)
    with pytest.raises(SystemExit):
        discovery_cli._hcx_key(argparse.ArgumentParser(), True)
    assert "Secure hidden input unavailable" in capsys.readouterr().err


def test_explicit_prompt_requires_tty_even_with_environment_key(monkeypatch, capsys):
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setattr(discovery_cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(discovery_cli.getpass, "getpass", forbid)
    with pytest.raises(SystemExit):
        discovery_cli._hcx_key(argparse.ArgumentParser(), True)
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.err
    assert SYNTHETIC_KEY not in captured.out + captured.err


def test_offline_cli_rejects_prompt_option_without_reading_credentials(monkeypatch, capsys):
    monkeypatch.setattr(discovery_cli.getpass, "getpass", forbid)
    with pytest.raises(SystemExit):
        discovery_cli.main(["evaluate-search", "--prompt-key"])
    assert "--prompt-key requires run-b" in capsys.readouterr().err


def test_legacy_cli_missing_key_fails_before_data(monkeypatch, capsys):
    monkeypatch.setattr(cli, "MetadataStore", forbid)
    monkeypatch.setattr(
        cli.sys, "argv", ["claview", "evaluate-a", "--mode", "live", "--allow-external"]
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
