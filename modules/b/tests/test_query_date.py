"""Config.as_of defaults to the query date instead of a fixed calendar date."""

import subprocess
import sys
from datetime import date

from claview_b import contracts
from claview_b.contracts import Config, today_iso


def test_default_as_of_is_the_query_date(monkeypatch):
    monkeypatch.setattr(contracts, "_today", lambda: date(2027, 3, 5))
    assert Config().as_of == "2027-03-05"
    assert today_iso() == "2027-03-05"


def test_default_as_of_is_read_when_each_config_is_built(monkeypatch):
    monkeypatch.setattr(contracts, "_today", lambda: date(2026, 12, 31))
    first = Config()
    monkeypatch.setattr(contracts, "_today", lambda: date(2027, 1, 1))
    assert (first.as_of, Config().as_of) == ("2026-12-31", "2027-01-01")


def test_explicit_as_of_is_kept(monkeypatch):
    monkeypatch.setattr(contracts, "_today", lambda: date(2027, 3, 5))
    assert Config(as_of="2025-12-15").as_of == "2025-12-15"


def test_unpinned_default_is_the_system_date():
    # A fresh interpreter does not load the suite's pin, so this is the real default.
    code = "from claview_b.contracts import Config; print(Config().as_of)"
    before = date.today().isoformat()
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    after = date.today().isoformat()
    assert out.stdout.strip() in {before, after}


def test_pinned_suite_date_is_2026_10_01():
    assert Config().as_of == "2026-10-01"
