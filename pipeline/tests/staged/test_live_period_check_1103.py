"""The live period check (pipeline/scripts/live_period_check.py): call limits, key handling, dry run (no external call)."""

from __future__ import annotations

import json

import pytest
import live_period_check as check


def test_a_call_over_the_limit_is_never_made():
    budget = check.Budget(openai=1, kosis=1)
    made = []

    class Inner:
        def complete(self, *args, **kwargs):
            made.append("model")

        def fetch(self, params, trace):
            made.append("kosis")

    model, client = check.CountedModel(Inner(), budget), check.CountedClient(Inner(), budget)
    model.complete()
    client.fetch({}, None)
    with pytest.raises(check.CapReached):
        model.complete()
    with pytest.raises(check.CapReached):
        client.fetch({}, None)
    assert made == ["model", "kosis"] and budget.used == {"openai": 1, "kosis": 1}


def test_the_limit_is_not_an_exception_that_a_broad_handler_could_swallow():
    swallowed = []
    with pytest.raises(check.CapReached):
        try:
            raise check.CapReached("openai")
        except Exception as exc:  # noqa: BLE001
            swallowed.append(exc)
    assert swallowed == []


def test_without_keys_a_live_run_stops_before_any_call(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("KOSIS_API_KEY", "not usable because of a space")
    with pytest.raises(SystemExit) as stop:
        check.main(["--out", str(tmp_path)])
    assert "OPENAI_API_KEY" in str(stop.value) and "KOSIS_API_KEY" in str(stop.value)
    assert "not usable" not in str(stop.value) and "not usable" not in capsys.readouterr().out
    assert not (tmp_path / "summary.json").exists()


def test_dry_run_writes_the_summary_and_stops_at_the_limit(tmp_path, base):
    code = check.main(["--dry-run", "--out", str(tmp_path), "--only", "L1_quoted_year,L4_yoy_calc_range", "--max-openai", "1"])
    assert code == 1  # the second case could not call the model
    rows = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert [(r["case"], r["status"]) for r in rows] == [("L1_quoted_year", "ok"), ("L4_yoy_calc_range", "stopped")]
    assert rows[0]["calls"] == {"openai": 1, "kosis": 1} and all(v is True for v in rows[0]["checks"].values())
    assert "중단" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_dry_run_of_every_case_passes_its_own_checks(tmp_path, base):
    assert check.main(["--dry-run", "--out", str(tmp_path)]) == 0
    rows = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert len(rows) == len(check.CASES)
    assert all(v is True for r in rows for v in r["checks"].values())


def test_the_new_cases_are_part_of_the_check():
    ids = [c["id"] for c in check.CASES]
    assert ids[-4:] == [
        "L7_two_tables_own_years",
        "L8_partly_outside_table",
        "L9_first_year_yoy",
        "L10_one_statistic_dropped",
    ]


def test_the_lift_of_a_block_is_undone(base):
    from claview_a_api import view

    original = view._months
    with check.lifted_a_block(True):
        assert view._months is not original
    assert view._months is original
    with check.lifted_a_block(False):
        assert view._months is original
