"""The development command is exercised with a synthetic transport, never an API call."""

import copy

import pytest

from test_plan15_settle import layer
from test_plan15_step5 import QUESTION
from test_plan18_io import FakeTransport
from claview_a import development as BATCH
from claview_a.interpret.decompose_provider import LiveSource


def test_proposed_sample_contains_only_twenty_development_questions():
    sample = BATCH.read(BATCH.SAMPLE)
    rows = BATCH.sample_rows(sample)
    assert len(rows) == 20 and all(x["dataset"] == "development" for x in rows)
    assert len(sample["comparison_ids"]) == 5 and sample["coverage_gaps"]
    policy = BATCH.read(BATCH.MODELS)
    assert [p["count"] for p in policy["profiles"]] == [20, 5, 5]
    assert policy["repetitions"] == 3
    assert sum(p["count"] * policy["repetitions"] for p in policy["profiles"]) == policy["max_calls"] == 90
    bad = copy.deepcopy(sample)
    bad["cases"][1] = bad["cases"][0]
    with pytest.raises(ValueError):
        BATCH.sample_rows(bad)


def test_approval_hash_is_checked_before_any_client_or_catalog(tmp_path):
    manifest = {"version": 1}
    manifest["approval_hash"] = BATCH.digest(manifest)
    path = tmp_path / "manifest.json"
    BATCH.write(path, manifest)
    with pytest.raises(ValueError, match="approval"):
        BATCH.run_batch(path, tmp_path / "out", "live", False, None)
    with pytest.raises(ValueError, match="differs"):
        BATCH.run_batch(path, tmp_path / "out", "live", True, "wrong_hash")
    assert not (tmp_path / "out").exists() and not (tmp_path / "archive").exists()


def test_usage_cost_counts_reasoning_once_and_respects_cached_tokens():
    profile = {"input_usd_per_million": 2.0, "cached_input_usd_per_million": 0.1, "output_usd_per_million": 10.0}
    usage = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 400}, "output_tokens": 100,
             "output_tokens_details": {"reasoning_tokens": 80}}
    assert BATCH.usage_cost(usage, profile) == pytest.approx(0.00224)
    assert BATCH.usage_cost(None, profile) is None
    assert BATCH.usage_cost({**usage, "input_tokens": 1}, profile) is None


def test_batch_archives_and_replays_then_refuses_changed_generation(layer, tmp_path, monkeypatch):
    policy = BATCH.read(BATCH.MODELS)
    profile = policy["profiles"][0]
    rules, payload, schema = BATCH.input_of(QUESTION, layer)
    task = {"call_id": "synthetic:one", "case_id": "one", "profile": profile, "repeat": 1,
            "request_hash": BATCH.digest(BATCH.settings(profile, policy).request(payload, schema))}
    manifest = {"inputs": [{"case_id": "one", "question": QUESTION, "as_of": None}], "tasks": [task],
                "policy": policy, "approval_hash": "synthetic_approved",
                "runtime_versions": BATCH.runtime_versions(), "catalog_version": BATCH.catalog_version(layer)}
    monkeypatch.setattr(BATCH, "check_manifest", lambda *args: manifest)
    monkeypatch.setattr(BATCH, "load_layer", lambda **kwargs: layer)
    transport = FakeTransport()
    monkeypatch.setattr(BATCH, "LiveSource", lambda *args, **kwargs: LiveSource(*args, **kwargs, transport=transport))
    path = tmp_path / "manifest.json"
    first = BATCH.run_batch(path, tmp_path / "first", "live", True, "synthetic_approved")
    replay = BATCH.run_batch(path, tmp_path / "replay", "replay", False, None, tmp_path / "first")
    assert first["real_calls"] == replay["real_calls"] == 0
    assert first["adopted"] == replay["adopted"] == 1 and replay["replay_calls"] == 1
    assert replay["replay_comparisons"] == {"synthetic:one": "same"}
    assert replay["cost_usd_from_reported_usage"] == 0
    assert len(transport.calls) == 1
    manifest["tasks"][0]["request_hash"] = "changed"
    with pytest.raises(ValueError, match="settings changed"):
        BATCH.run_batch(path, tmp_path / "changed", "live", True, "synthetic_approved")
    assert len(transport.calls) == 1 and not (tmp_path / "changed").exists()
