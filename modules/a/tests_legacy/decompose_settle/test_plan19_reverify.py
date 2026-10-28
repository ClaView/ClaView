"""Plan 19 stages 1-2: frozen baseline and offline re-verification on a synthetic stored batch.

Synthetic catalog and hand-written fake responses only (no model, no network; the session guard blocks sockets).
The stored batch is built in a temporary folder with the same layout as the plan 18 store.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_plan15_settle import P, SPLIT_Q, TWO_Q, layer  # noqa: F401  (layer is a fixture)
from test_plan15_step5 import QUESTION, simple
from test_plan18_io import body
from claview_a_legacy import _guard, development as BATCH, development_reverify as RV
from claview_a_legacy.catalog.facets import catalog_version
from claview_a_legacy.interpret.decompose_archive import canonical, digest
from claview_a_legacy.interpret.pipeline import interpret_question

AS_OF = "2026-10-21"
TESTS = Path(__file__).resolve().parents[1]
ROOT = TESTS.parents[2]


def test_package_is_loaded_from_this_checkout():
    import claview_a_legacy

    assert Path(claview_a_legacy.__file__).resolve().is_relative_to((ROOT / "modules" / "a").resolve())
    assert Path(RV.__file__).resolve().is_relative_to((ROOT / "modules" / "a").resolve())


def write_row(archive: Path, row: dict) -> None:
    RV.call_file(archive, row["call_id"]).write_text(canonical(row), encoding="utf-8")


def make_batch(layer, root: Path, repeats: int = 2):
    """A stored batch of one synthetic question: manifest dict (inputs as the manifest stores them) and archive."""
    rules, payload, schema = BATCH.input_of(QUESTION, layer)
    stored = json.loads(json.dumps({"rules": rules, "payload": payload, "schema": schema}, ensure_ascii=False))
    policy = BATCH.read(BATCH.MODELS)
    profile = policy["profiles"][0]
    request = BATCH.settings(profile, policy).request(payload, schema)
    archive = root / RV.ARCHIVE
    archive.mkdir(parents=True)
    raw = body(simple(P(payload)))
    tasks = []
    for n in range(1, repeats + 1):
        call_id = f"synthetic:one:repeat{n}"
        write_row(archive, {"version": 1, "call_id": call_id, "request_hash": digest(request), "request": request,
                            "versions": {}, "transport_kind": "fake", "status": "received", "raw_response": raw,
                            "response_hash": digest(raw), "usage": None})
        tasks.append({"call_id": call_id, "case_id": "one", "repeat": n, "profile": profile,
                      "request_hash": digest(request)})
    manifest = {"inputs": [{"case_id": "one", "question": QUESTION, "as_of": AS_OF, **stored}], "tasks": tasks,
                "catalog_version": catalog_version(layer)}
    return manifest, archive


def frozen_of(manifest, layer) -> dict:
    record, _ = RV.frozen_input(manifest["inputs"][0], layer, manifest["catalog_version"], catalog_version(layer))
    return {"one": record}


def rewrite(archive: Path, task: dict, change) -> None:
    """Change a stored request and keep the archive self-consistent (hashes recomputed), so only that part differs."""
    path = RV.call_file(archive, task["call_id"])
    row = json.loads(path.read_text(encoding="utf-8"))
    change(row["request"])
    row["request_hash"] = task["request_hash"] = digest(row["request"])
    path.write_text(canonical(row), encoding="utf-8")


def stored_primaries(layer, manifest, folder: Path) -> Path:
    """The primary analyses a live run would have stored (fake source, same proposal)."""
    folder.mkdir()
    for task in manifest["tasks"]:
        analysis = interpret_question(QUESTION, layer, decomposition={
            "mode": "gpt", "execution_mode": "fake", "proposal_source": lambda p, s: simple(P(p)), "as_of": AS_OF})
        RV.call_file(folder, task["call_id"]).write_text(
            json.dumps({"primary_analysis": analysis}, ensure_ascii=False), encoding="utf-8")
    return folder


def hashes(folder: Path) -> dict:
    return {p.name: RV.file_sha256(p) for p in sorted(folder.iterdir()) if p.is_file()}


# ---------------------------------------------------------------- compatibility


def test_compatibility_names_each_changed_part_and_never_passes_a_missing_value():
    base = {"payload": "p", "schema": "s", "rows": "r", "catalog": "c"}
    assert RV.compatibility(base, dict(base)) == []
    for key in RV.COMPAT_KEYS:
        assert RV.compatibility(base, {**base, key: "changed"}) == [key]
    assert RV.compatibility({**base, "rows": None}, {**base, "rows": None}) == ["rows"]


def test_unchanged_store_is_reverified_by_the_current_code_and_matches_the_stored_outcome(layer, tmp_path):
    manifest, archive = make_batch(layer, tmp_path)
    frozen = frozen_of(manifest, layer)
    mapping = frozen["one"]["mapping"]
    assert mapping["status"] == RV.RECONSTRUCTED and mapping["recorded_at_call_time"] is False
    assert all(mapping["checks"][k] for k in ("payload_regenerated_equal", "schema_regenerated_equal",
                                              "rows_current_equal_stored_mentions", "payload_labels_match_mapping",
                                              "catalog_equal"))
    live = stored_primaries(layer, manifest, tmp_path / "live")
    result = RV.reverify_batch(manifest, archive, frozen, layer, live)
    assert result["totals"]["status"] == {RV.REVERIFIED: 2, RV.INCOMPATIBLE: 0, RV.UNVERIFIED: 0}
    assert result["totals"]["comparison"] == {"same": 2, "changed": 0, "no_stored": 0}
    assert all(r["outcome"]["applied_source"] == "model" for r in result["rows"])
    assert result["real_calls"] == 0 and _guard.NETWORK_ATTEMPTS == []


@pytest.mark.parametrize("change", ["payload", "schema", "rows", "catalog"])
def test_one_changed_part_makes_the_call_incompatible_with_that_reason_only(layer, tmp_path, change):
    manifest, archive = make_batch(layer, tmp_path, repeats=1)
    task = manifest["tasks"][0]
    if change == "payload":
        def edit(request):
            payload = json.loads(request["input"])
            payload["raw"] += " "
            request["input"] = canonical(payload)
        rewrite(archive, task, edit)
    elif change == "schema":
        rewrite(archive, task, lambda request: request["text"]["format"]["schema"].update(description="changed"))
    elif change == "rows":
        # same catalog name, role and path (so the same payload), but another raw row behind the sense
        mention = next(m for m in manifest["inputs"][0]["rules"]["mentions"]
                       if m["kind"] == "name" and m["candidates"])
        mention["candidates"][0]["table_id"] += "_changed"
    else:
        manifest["catalog_version"] = "changed"
    frozen = frozen_of(manifest, layer)
    if change == "rows":
        assert frozen["one"]["mapping"]["checks"]["payload_labels_match_mapping"]
        assert frozen["one"]["mapping"]["status"] == RV.UNVERIFIED
    result = RV.reverify_batch(manifest, archive, frozen, layer)
    (row,) = result["rows"]
    assert row["status"] == RV.INCOMPATIBLE and row["reasons"] == [change]
    assert row["outcome"] is None  # never turned into a rules fallback
    assert result["totals"]["sum_equals_planned"]


def test_unverified_mapping_provenance_is_its_own_status(layer, tmp_path):
    manifest, archive = make_batch(layer, tmp_path, repeats=1)
    frozen = frozen_of(manifest, layer)
    frozen["one"]["mapping"]["status"] = RV.UNVERIFIED
    (row,) = RV.reverify_batch(manifest, archive, frozen, layer)["rows"]
    assert row["status"] == RV.UNVERIFIED and row["reasons"] == ["mapping_provenance"] and row["outcome"] is None


def test_denominator_keeps_missing_tampered_and_incompatible_calls(layer, tmp_path):
    manifest, archive = make_batch(layer, tmp_path, repeats=4)
    first, second, third, _ = manifest["tasks"]
    RV.call_file(archive, first["call_id"]).unlink()
    rewrite(archive, second, lambda request: request["text"]["format"]["schema"].update(description="changed"))
    path = RV.call_file(archive, third["call_id"])
    row = json.loads(path.read_text(encoding="utf-8"))
    row["raw_response"] += " "
    path.write_text(canonical(row), encoding="utf-8")
    result = RV.reverify_batch(manifest, archive, frozen_of(manifest, layer), layer)
    totals = result["totals"]
    assert totals["planned"] == totals["rows"] == 4 and totals["sum_equals_planned"]
    assert totals["status"] == {RV.REVERIFIED: 1, RV.INCOMPATIBLE: 3, RV.UNVERIFIED: 0}
    assert totals["reasons"] == {"archive:missing": 1, "schema": 1, "archive:response_hash": 1}
    assert [r["call_id"] for r in result["rows"]] == [t["call_id"] for t in manifest["tasks"]]
    assert all(r["outcome"] is None for r in result["rows"] if r["status"] != RV.REVERIFIED)
    assert totals["reverified_applied"] == {"model": 1, "rules_fallback": 0}


# ---------------------------------------------------------------- the stored files are only read


def test_baseline_and_reverify_only_read_the_store_and_write_new_folders(layer, tmp_path):
    store = tmp_path / "store"
    manifest, _ = make_batch(layer, store)
    (store / RV.MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    before = RV.store_hashes(store)
    archive_before = hashes(store / RV.ARCHIVE)
    baseline = RV.build_baseline(store, tmp_path / "baseline", layer)
    assert baseline["store_unchanged"] and baseline["real_calls"] == 0
    assert baseline["totals"]["rows"] == baseline["totals"]["archive_ok"] == 2
    assert baseline["totals"]["mapping_status"] == {RV.RECONSTRUCTED: 1, RV.UNVERIFIED: 0}
    assert baseline["totals"]["stored_input_equals_manifest"] == 2
    calls = json.loads((tmp_path / "baseline" / "calls.json").read_text(encoding="utf-8"))
    assert [r["archive_sha256"] for r in calls] == [before[r["archive_file"]] for r in calls]
    assert all(r["response_hash"] and r["request_hash"] for r in calls)
    summary = RV.run_reverify(store, tmp_path / "baseline", tmp_path / "reverify", layer)
    assert summary["totals"]["status"][RV.REVERIFIED] == 2
    assert summary["store_unchanged"] and summary["store_equals_baseline"]
    with pytest.raises(FileExistsError):
        RV.run_reverify(store, tmp_path / "baseline", tmp_path / "reverify", layer)
    with pytest.raises(FileExistsError):
        RV.build_baseline(store, tmp_path / "baseline", layer)
    assert RV.store_hashes(store) == before
    assert hashes(store / RV.ARCHIVE) == archive_before


def test_baseline_of_a_fake_live_batch_recomputes_the_strict_metric_and_reverify_matches(layer, tmp_path, monkeypatch):
    """The plan 18 batch code writes a store (fake transport through the outer graph); stage 1 recomputes the stored
    strict agreement exactly and stage 2 reproduces every stored primary analysis."""
    from test_plan18_io import FakeTransport
    from claview_a_legacy.interpret.decompose_provider import LiveSource

    store = tmp_path / "store"
    policy = BATCH.read(BATCH.MODELS)
    profile = policy["profiles"][0]
    rules, payload, schema = BATCH.input_of(QUESTION, layer)
    request_hash = digest(BATCH.settings(profile, policy).request(payload, schema))
    tasks = [{"call_id": f"synthetic:one:repeat{n}", "case_id": "one", "profile": profile, "repeat": n,
              "request_hash": request_hash} for n in (1, 2, 3)]
    stored = json.loads(json.dumps({"rules": rules, "payload": payload, "schema": schema}, ensure_ascii=False))
    manifest = {"inputs": [{"case_id": "one", "question": QUESTION, "as_of": AS_OF, **stored}], "tasks": tasks,
                "policy": policy, "approval_hash": "synthetic_approved",
                "runtime_versions": BATCH.runtime_versions(), "catalog_version": BATCH.catalog_version(layer)}
    path = store / RV.MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(BATCH, "check_manifest", lambda *args: manifest)
    monkeypatch.setattr(BATCH, "load_layer", lambda **kwargs: layer)
    transport = FakeTransport()
    monkeypatch.setattr(BATCH, "LiveSource", lambda *a, **k: LiveSource(*a, **k, transport=transport))
    BATCH.run_batch(path, store / RV.LIVE, "live", True, "synthetic_approved")
    assert len(transport.calls) == 3
    baseline = RV.build_baseline(store, tmp_path / "baseline", layer)
    measured = baseline["metrics"]
    assert measured["strict_equals_stored_summary"]
    assert measured["signatures_equal_stored"] == {"structure": 3, "selection": 3, "calls": 3}
    overall = measured["supplementary"]["overall"]
    assert overall["core"] == {"agree": 1, "eligible": 1, "rate": 1.0}
    assert overall["bundle"]["agree"] == overall["bundle"]["eligible"] == 1
    assert baseline["totals"]["live_results"] == 3 and baseline["store_unchanged"]
    summary = RV.run_reverify(store, tmp_path / "baseline", tmp_path / "reverify", layer)
    assert summary["totals"]["status"] == {RV.REVERIFIED: 3, RV.INCOMPATIBLE: 0, RV.UNVERIFIED: 0}
    assert summary["totals"]["comparison"] == {"same": 3, "changed": 0, "no_stored": 0}
    assert len(transport.calls) == 3 and summary["real_calls"] == 0


# ---------------------------------------------------------------- determinism of the payload and sense IDs


def test_payload_and_sense_ids_are_the_same_for_the_same_question(layer):
    first = RV.current_input(SPLIT_Q, layer)
    RV.current_input(TWO_Q, layer)  # another question in between shares no state
    again = RV.current_input(SPLIT_Q, layer)
    assert canonical(first["payload"]) == canonical(again["payload"])
    assert canonical(first["schema"]) == canonical(again["schema"])
    assert list(first["local"]["senses"]) == list(again["local"]["senses"])
    assert RV.mapping_digest(first["local"]["senses"]) == RV.mapping_digest(again["local"]["senses"])
    assert first["local"]["mention_kept"] == again["local"]["mention_kept"]


CHILD = """
import json, sys
from pathlib import Path
sys.path[:0] = sys.argv[1:4]
from test_plan15_settle import TABLES, SPLIT_Q
from catalog_synth import write_inputs, write_raw
from claview_a_legacy.catalog.layer import load_layer
from claview_a_legacy import development_reverify as RV
from claview_a_legacy.interpret.decompose_archive import digest
folder = Path(sys.argv[4])
search, meta = write_raw(folder / "raw", TABLES)
inputs = write_inputs(folder / "inputs", overrides=[])
empty = folder / "empty.jsonl"
empty.write_text("", encoding="utf-8")
layer = load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                   synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)
now = RV.current_input(SPLIT_Q, layer)
print(json.dumps([digest(now["payload"]), digest(now["schema"]), RV.mapping_digest(now["local"]["senses"])]))
"""


def test_payload_and_sense_ids_do_not_depend_on_the_hash_seed(layer, tmp_path):
    now = RV.current_input(SPLIT_Q, layer)
    here = [digest(now["payload"]), digest(now["schema"]), RV.mapping_digest(now["local"]["senses"])]
    seen = []
    for seed in ("0", "1", "12345"):
        folder = tmp_path / f"seed{seed}"
        folder.mkdir()
        done = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-c", CHILD, str(ROOT / "modules" / "a"), str(TESTS),
             str(Path(__file__).resolve().parent), str(folder)],
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, encoding="utf-8",
            timeout=600, check=False)
        assert done.returncode == 0, done.stderr[-2000:]
        seen.append(json.loads(done.stdout.strip().splitlines()[-1]))
    assert all(x == here for x in seen)
