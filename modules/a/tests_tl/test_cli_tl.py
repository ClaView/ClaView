"""The command line of the new A runs fake and replay only (review 2026-10-28, Major 1 and 2).

A live run or a manifest is refused before any folder, transport or request is made, even when the manifest
approves live calls and gives a call limit; model calls go through the evaluator (gates and call limit). Sends are
counted with a fake ``http.client`` connection and a counting ``OpenAITransport``; the network guard of conftest
refuses any real socket. The key value used here is a test placeholder, never a real key."""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from claview_a import __main__ as cli
from claview_a import entry, judges
from claview_a.transport.provider import OpenAITransport

QUESTION = "2015~2025년 연간 실질 GDP 성장률 추이를 그래프로 보여줘."
NOTHING_SENT = {"transport": 0, "live_judge": 0, "preflight": 0, "send": 0, "connection": 0}


@pytest.fixture
def sends(monkeypatch):
    """Every way a request could leave: building the transport or a live judge, the transport's preflight and send,
    and an HTTPS connection."""
    count = {"transport": 0, "live_judge": 0, "preflight": 0, "send": 0, "connection": 0}
    live_init = judges.LiveJudge.__init__

    class CountingConnection:
        def __init__(self, *args, **kwargs):
            count["connection"] += 1

        def request(self, *args, **kwargs):
            raise AssertionError("a request was made")

        def close(self):
            pass

    def transport(self):
        count["transport"] += 1

    def live_judge(self, *args, **kwargs):
        count["live_judge"] += 1
        live_init(self, *args, **kwargs)

    def preflight(self):
        count["preflight"] += 1

    def send(self, request):
        count["send"] += 1
        raise AssertionError("a request was sent")

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-key")
    monkeypatch.setattr(http.client, "HTTPSConnection", CountingConnection)
    monkeypatch.setattr(OpenAITransport, "__init__", transport, raising=False)
    monkeypatch.setattr(judges.LiveJudge, "__init__", live_judge)
    monkeypatch.setattr(OpenAITransport, "preflight", preflight)
    monkeypatch.setattr(OpenAITransport, "send", send)
    return count


@pytest.fixture
def approved(tmp_path):
    """A manifest that approves live calls with a call limit: still refused by the command line."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"approval": {"live_calls": True, "approved_by": "test", "approved_at": "test"},
                                "max_calls": 3, "retries": 0,
                                "model": {"model": "gpt-6-luna", "effort": "low", "max_output_tokens": 4096}}),
                    encoding="utf-8")
    return path


def _refused(argv: list[str]) -> str:
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert isinstance(stop.value.code, str), stop.value.code
    return stop.value.code


def test_live_is_refused_even_with_an_approved_manifest(tmp_path, approved, sends):
    out = tmp_path / "out"
    for argv in ([QUESTION, "--judge", "live", "--manifest", str(approved), "--out", str(out)],
                 [QUESTION, "--judge=live", f"--manifest={approved}", "--out", str(out)],
                 [QUESTION, "--judge", "live", "--out", str(out)],
                 [QUESTION, "--manifest", str(approved), "--out", str(out)],
                 [QUESTION, "--judge", "fake", "--manifest", str(approved), "--out", str(out)]):
        message = _refused(argv)
        assert "modules/a/scripts/tl_eval/live.py" in message and "gates" in message
        assert not out.exists()
    assert sends == NOTHING_SENT


def test_an_abbreviated_option_is_not_read_as_live(tmp_path, approved, sends):
    for argv in ([QUESTION, "--judg", "live"], [QUESTION, "--man", str(approved)]):
        with pytest.raises(SystemExit) as stop:
            cli.main(argv + ["--out", str(tmp_path / "out")])
        assert stop.value.code == 2  # argparse: unknown option
    assert not (tmp_path / "out").exists()
    assert sends == NOTHING_SENT


def test_running_twice_with_the_same_manifest_sends_nothing(tmp_path, approved, sends, layer, monkeypatch, capsys):
    monkeypatch.setattr(entry, "_LAYER", [layer])
    for k in range(2):
        _refused([QUESTION, "--judge", "live", "--manifest", str(approved), "--out", str(tmp_path / f"live{k}")])
        assert cli.main([QUESTION, "--as-of", "2026-10-22", "--out", str(tmp_path / f"fake{k}")]) == 0
        shown = json.loads(capsys.readouterr().out)
        assert shown["calls"] == 1 and shown["action"] in ("handoff", "ask", "fail")
        rows = [json.loads(x) for x in (tmp_path / f"fake{k}" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [r["outcome"] for r in rows] == ["fake"]
    assert sends == NOTHING_SENT
    assert not any((tmp_path / f"live{k}").exists() for k in range(2))


def test_help_shows_fake_and_replay_only(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    assert "{fake,replay}" in text
    assert "{fake,replay,live}" not in text and "--manifest" not in text


def test_the_command_line_and_the_entry_build_no_live_judge():
    package = Path(cli.__file__).resolve().parent
    for name in ("__main__.py", "entry.py", "__init__.py"):
        text = (package / name).read_text(encoding="utf-8")
        assert "LiveJudge" not in text and "OpenAITransport" not in text, name
    assert cli.JUDGES == ("fake", "replay")


def test_the_counters_see_a_built_transport_and_live_judge(sends, tmp_path):
    """The counters are live: building the transport or a live judge directly is counted (nothing is sent)."""
    judges.LiveJudge(OpenAITransport(), 1, judges.Archive(tmp_path / "calls.jsonl"), {})
    assert sends["transport"] == 1 and sends["live_judge"] == 1 and sends["send"] == 0
