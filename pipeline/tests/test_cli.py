"""The public CLI exercises the AView pipeline and keeps synthetic results distinct from accuracy."""

import json

import pytest

from claview_pipeline import cli


def test_run_and_evaluate_use_the_service_and_preserve_records(tmp_path):
    for command in ("run", "evaluate"):
        out = tmp_path / command
        args = [command, "--as-of", "2026-10-29", "--out", str(out)]
        if command == "run":
            args += ["--question", "가계신용"]
        assert cli.main(args) == 0
        report = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert report["external_calls"] == 0 and report["accuracy_evaluation"] is False
        assert len(report["cases"]) == (1 if command == "run" else 22)
        if command == "run":
            assert report["cases"][0]["status"] == "needs_confirmation"
        assert all((out / "records" / row["run_id"] / "response.json").is_file() for row in report["cases"])
        assert all("A_judge" in row["calls"] for row in report["cases"])
        original = (out / "summary.json").read_bytes()
        with pytest.raises(SystemExit):
            cli.main(args)
        assert (out / "summary.json").read_bytes() == original
