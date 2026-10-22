"""Golden end-to-end replay and the pipeline command line, offline (plan 26 step 2).

These check that every golden case runs to the end and that the report has its shape; the
numbers are a development baseline, not asserted here (the golden cases are not an
independent test set, B answers are replayed and fetch values are synthetic).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from claview.pipeline import cli
from claview.pipeline.evaluate import (
    ANSWER_POLICIES,
    SCOPE,
    _b_envelope,
    _b_success,
    evaluate,
    load_cases,
)

AS_OF = "2026-10-22"
ROW_KEYS = {
    "case_id",
    "a_first_status",
    "a_status",
    "answer_turns",
    "execution_status",
    "b_reason",
    "a_tables",
    "golden_tables",
    "table_exact",
    "table_recall",
    "b_status",
    "b_success",
    "b_exact",
    "fetch_statuses",
    "fetch_calls",
}


def test_golden_replay_runs_every_case_to_the_end(base):
    cases = load_cases(cli.GOLDEN)
    report = evaluate(cases, as_of=AS_OF, base=base)
    rows, summary = report["cases"], report["summary"]
    assert report["scope"] == SCOPE and report["answer_policy"] in ANSWER_POLICIES
    assert [r["case_id"] for r in rows] == [c["case_id"] for c in cases]
    assert all(set(r) == ROW_KEYS for r in rows)
    assert summary["cases"] == len(cases)
    for key in ("table_exact", "b_success", "b_exact"):
        assert summary[f"{key}_count"] == sum(r[key] for r in rows)
    assert summary["completed_count"] == sum(r["execution_status"] == "completed" for r in rows)
    assert sum(summary["execution_status"].values()) == len(cases)
    for r in rows:
        assert 0 <= r["table_recall"] <= 1
        # With the golden tables, the replayed golden answers reproduce the golden selections.
        if r["table_exact"]:
            assert r["b_success"] and r["b_exact"], r["case_id"]
        if r["execution_status"] == "completed":
            assert r["b_status"] == "ok" and set(r["fetch_statuses"]) == {"ok"}
        if r["b_status"] == "not_run":
            assert r["fetch_calls"] == 0
        # first_option answers every pending question: no case is left waiting.
        assert r["execution_status"] != "awaiting_answer", r["case_id"]


def test_command_line_run_and_evaluate_write_their_outputs(tmp_path, capsys):
    out = tmp_path / "run"
    code = cli.main(
        [
            "run",
            "--synthetic-b",
            "--as-of",
            AS_OF,
            "--question",
            "GDP 2023년 보여줘",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    saved = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert saved["network_attempts"] == 0
    assert [r["execution_status"] for r in saved["rows"]] == ["completed"]

    subset = tmp_path / "cases.json"  # a copy of two golden cases; the golden file is not touched
    golden = json.loads(cli.GOLDEN.read_text(encoding="utf-8-sig"))
    subset.write_text(
        json.dumps({"cases": golden["cases"][:2]}, ensure_ascii=False), encoding="utf-8"
    )
    out = tmp_path / "eval"
    code = cli.main(["evaluate", "--cases", str(subset), "--as-of", AS_OF, "--out", str(out)])
    assert code == 0
    report = json.loads((out / "golden_eval.json").read_text(encoding="utf-8"))
    assert report["summary"]["cases"] == 2 and report["network_attempts"] == 0
    assert "[pipeline evaluate]" in capsys.readouterr().out


def _req(status, selections=None, resolved=None, returned=True):
    if not returned:
        return {"status": "connection_error"}
    result = {"selections": selections or []}
    if resolved is not None:
        result["resolved_period"] = resolved
    return {
        "status": "returned",
        "result": {"b_outcome": {"stage": "B", "status": status, "result": result}},
    }


def test_b_envelope_counts_every_requirement_and_ignores_order():
    """A failed or missing requirement is never hidden by a successful one, in either order."""
    golden = json.loads(cli.GOLDEN.read_text(encoding="utf-8-sig"))["cases"][0]["expected"]["B"]
    rows = golden["selections"]
    period = rows[0]["period"]
    ok = _req("ok", rows)
    assert _b_success(_b_envelope([ok]), golden)
    for pair in ([ok, _req("ok", returned=False)], [_req("ok", returned=False), ok]):
        assert _b_envelope(pair)["status"] == "connection_error"
        assert not _b_success(_b_envelope(pair), golden)
    asked = _req("needs_confirmation", resolved=period)
    for pair in ([asked, _req("error")], [_req("error"), asked], [ok, asked], [asked, ok]):
        assert _b_envelope(pair)["status"] == "mixed"
        assert not _b_success(_b_envelope(pair), golden)
    assert _b_envelope([]) is None and not _b_success(None, golden)


def test_command_line_blocks_the_network_before_loading_the_flow():
    code = (
        "import sys\n"
        "import claview.pipeline.cli\n"
        "from claview.a import _guard\n"
        "loaded = [m for m in sys.modules if m.startswith(('claview.b', 'claview.pipeline.'))"
        " and m != 'claview.pipeline.cli']\n"
        "print(_guard.installed()['network'], loaded)\n"
    )
    src = str(Path(cli.__file__).resolve().parents[2])
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("KEY", "TOKEN"))}
    env["PYTHONPATH"] = src
    done = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "True []"
