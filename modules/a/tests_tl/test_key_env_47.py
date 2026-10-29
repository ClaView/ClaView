"""The live runners take the key from the team's ``.env`` (user 2026-10-29 09:51 UTC): only the OPENAI_API_KEY line
is read, a value there replaces the process's own, an empty file falls back to the process variable and the run
records the source, never the key. Temporary files with made-up values only; the real .env is never read here, no
network."""

from __future__ import annotations

import json

import pytest

from tl_eval import explore as E
from tl_eval import live as L

FAKE = "sk-test-made-up-value"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv(L.KEY_NAME, raising=False)
    return monkeypatch


@pytest.mark.parametrize("line", [f"OPENAI_API_KEY={FAKE}", f"export OPENAI_API_KEY={FAKE}",
                                  f'OPENAI_API_KEY="{FAKE}"', f"OPENAI_API_KEY='{FAKE}'",
                                  f"OPENAI_API_KEY = {FAKE}  # a comment"])
def test_the_key_line_is_read_in_its_usual_forms(tmp_path, env, line):
    import os

    path = tmp_path / ".env"
    path.write_text(f"# team settings\nCLAVIEW_MODE=synthetic\n{line}\nOTHER=1\n", encoding="utf-8")
    assert L.key_from_env_file(path) == "env_file" and os.environ[L.KEY_NAME] == FAKE


def test_the_file_value_replaces_the_process_value(tmp_path, env):
    import os

    env.setenv(L.KEY_NAME, "sk-old-made-up-value")
    path = tmp_path / ".env"
    path.write_text(f"OPENAI_API_KEY={FAKE}\n", encoding="utf-8")
    assert L.key_from_env_file(path) == "env_file" and os.environ[L.KEY_NAME] == FAKE


def test_an_empty_or_missing_file_value_is_recorded_as_the_fallback(tmp_path, env):
    import os

    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=\nCLAVIEW_MODE=synthetic\n", encoding="utf-8")
    assert L.key_from_env_file(path) == "none" and L.KEY_NAME not in os.environ
    assert L.key_from_env_file(tmp_path / "absent.env") == "none"
    env.setenv(L.KEY_NAME, "sk-old-made-up-value")
    assert L.key_from_env_file(path) == "process_environment"
    assert os.environ[L.KEY_NAME] == "sk-old-made-up-value"


def test_the_env_file_is_the_repository_root_one():
    assert L.ENV_FILE == L.ROOT / ".env" and (L.ROOT / ".env.example").is_file()


def test_a_run_with_a_given_transport_reads_no_env_file(tmp_path, layer, monkeypatch):
    def refuse(path=None):
        raise AssertionError("the .env file must not be read for a given transport")

    monkeypatch.setattr(L, "key_from_env_file", refuse)

    class Transport:
        kind = "fake"

        def preflight(self):
            pass

        def send(self, request):
            out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices",
                                   "asks", "missing", "alternatives")}
            return json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                               "output": [{"type": "message", "content": [{"type": "output_text",
                                                                           "text": json.dumps(out)}]}]})

    lst = tmp_path / "l.json"
    lst.write_text(json.dumps({"version": "t", "cases": [{"case_id": "T1", "question": "M2 총액을 보여줘",
                                                          "as_of": "2026-10-22", "standard": None,
                                                          "answer_state": {}}]}, ensure_ascii=False),
                   encoding="utf-8")
    m = E.make_manifest("t", {"name": "terra", "model": "m", "effort": "low", "max_output_tokens": 16}, lst)
    m["approval"]["live_calls"] = True
    got = E.run(m, "live", tmp_path / "run", layer=layer, transport=Transport())
    assert got["key_source"] is None
