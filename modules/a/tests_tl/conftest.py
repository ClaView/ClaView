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


# Contract .4 (plan 56 order 6-3b): the fixed inputs of the tests written before it are of contract .3. Their judges
# (lambdas and plain classes that name no contract) are read with .3 for the test session only; tests of contract .4
# name it on their judges or pass it to parse_output. The product path reads an undeclared judge with the current
# contract (graph.UNDECLARED_CONTRACT), and a stored answer with its record's.
OLD_FIXED_INPUT_CONTRACT = "tl-contract-2026-10-28.3"


@pytest.fixture(autouse=True)
def _old_fixed_inputs(monkeypatch):
    from claview_a import graph

    monkeypatch.setattr(graph, "UNDECLARED_CONTRACT", OLD_FIXED_INPUT_CONTRACT)


def pytest_report_header(config):
    return (f"tests_tl: judges that name no contract are read with {OLD_FIXED_INPUT_CONTRACT} "
            "(fixed inputs of the old shape)")


@pytest.fixture(scope="session")
def layer():
    from claview_a.catalog.layer import load_layer

    return load_layer()


@pytest.fixture(scope="session")
def frozen_metadata_layer(tmp_path_factory):
    """Opt-in historical inputs; the ordinary layer fixture keeps using current runtime metadata."""
    import hashlib
    import json
    from zipfile import ZipFile

    from claview_a.catalog.layer import load_layer

    # The A boundary forbids claview_common imports, including its test helper.
    # Read the same shared, hash-checked snapshot using only the standard library.
    fixture_root = Path(__file__).resolve().parents[3] / "eval" / "fixtures"
    snapshot = fixture_root / "metadata_before_2026_11_03"
    manifest = json.loads(snapshot.with_suffix(".json").read_text(encoding="utf-8"))
    members = {"data/catalog/search_catalog.json", "data/raw/boK_meta_raw.jsonl",
               "data/mappings/runtime/unit_supplement.json"}
    assert set(manifest["files"]) == members
    destination = tmp_path_factory.mktemp("metadata_before_2026_11_03")
    with ZipFile(snapshot.with_suffix(".zip")) as archive:
        assert set(archive.namelist()) == members | {"manifest.json"}
        assert len(archive.namelist()) == len(members) + 1
        assert json.loads(archive.read("manifest.json")) == manifest
        for name in sorted(members):
            data = archive.read(name)
            expected = manifest["files"][name]
            assert len(data) == expected["bytes"]
            assert hashlib.sha256(data).hexdigest() == expected["sha256"]
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return load_layer(search_catalog=destination / "data/catalog/search_catalog.json",
                      meta_raw=destination / "data/raw/boK_meta_raw.jsonl")


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
