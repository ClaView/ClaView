"""Tests of the table-level path (plan 37). The claview_a boundary guard (no old pipeline, no network) is installed
before any test module is imported, as in the current path's tests_v3. Run from the repository root:
    PYTHONHASHSEED=0 <python> -B -X utf8 -m pytest modules/a/tests -q -p no:cacheprovider
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODULE = str(Path(__file__).resolve().parents[1])  # modules/a
if _MODULE not in sys.path:
    sys.path.insert(0, _MODULE)

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")  # modules/a/scripts: the table-level evaluator
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from claview_a import _guard  # noqa: E402

_already = _guard.loaded_forbidden()
if _already:
    raise RuntimeError(f"table-level tests: forbidden modules loaded before collection: {_already}")
_guard.install()

import pytest  # noqa: E402

from tl_eval import files as _eval_files  # noqa: E402

# The evaluation questions and the files built from them stay outside the repository (one fixed bundle, named by
# CLAVIEW_A_EVAL_DIR). Without them, the tests below that read them are skipped with this reason; with them, they run.
_EVAL_FILES = ("standard_v1.json", "standard_v2.json", "standard_v3.json", "standard_golden26.json",
               "decisions_v2.json", "decisions_v3.json", "check40_v1.json", "golden16_v1.json")
_EVAL_PRESENT = _eval_files.present(*_EVAL_FILES)
_EVAL_REASON = f"evaluation files absent: set {_eval_files.EVAL_DIR_ENV} to the bundle's tl_eval folder"
collect_ignore = [] if _EVAL_PRESENT else ["test_scope_0719.py"]  # reads the standard when imported
_NEEDS_EVAL_FILES = {
    "test_golden_prep.py": "*",
    "test_live_tl.py": "*",
    "test_requirements_0813.py": "*",
    "test_eval_tl.py": {"test_standard_v2_lays_the_decisions_with_their_status_and_evidence",
                        "test_the_report_keeps_run_time_information_and_rejects_records_without_it",
                        "test_the_standard_keeps_its_sources_and_marks_what_is_not_confirmed"},
    "test_pool_recall.py": {"test_pool_recall_and_input_size_as_measured[check40-_check40]"},
}


def pytest_collection_modifyitems(config, items):
    if _EVAL_PRESENT:
        return
    skip = pytest.mark.skip(reason=_EVAL_REASON)
    for item in items:
        names = _NEEDS_EVAL_FILES.get(item.path.name)
        if names == "*" or (names and item.name in names):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def layer():
    from claview_a.catalog.layer import load_layer

    return load_layer()


@pytest.fixture(autouse=True)
def _no_refusal():
    v0, n0 = len(_guard.VIOLATIONS), len(_guard.NETWORK_ATTEMPTS)
    yield
    assert _guard.VIOLATIONS[v0:] == [] and _guard.NETWORK_ATTEMPTS[n0:] == [], "guard refused an operation"


def pytest_sessionfinish(session, exitstatus):
    found = _guard.problems()
    if found:
        sys.stderr.write("table-level tests boundary check failed at session end:\n")
        for line in found:
            sys.stderr.write(f"  {line}\n")
        session.exitstatus = 1


def fill_option_evidence(out: dict, cards: list[dict]) -> dict:
    """Contract .3 (user 2026-10-28 06:47 UTC) needs evidence per ask option. A made-up output that does not test
    option evidence gets, where the key is missing, the option table's card name as title evidence (a fact of that
    card). Outputs that test option evidence set it themselves."""
    from claview_a.cards import refs

    by_ref = refs(cards)
    card_of = {r["table_id"]: c for c in cards for r in c["tables"]}
    for a in out.get("asks", []):
        for o in a.get("options", []):
            if "evidence" not in o:
                table = by_ref.get(o["table"], o["table"])
                card = card_of.get(table)
                o["evidence"] = [{"kind": "title", "text": card["name"]}] if card else []
    return out
