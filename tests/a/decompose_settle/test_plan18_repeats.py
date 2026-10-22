"""Repeat accounting and meaningful agreement, using fake Responses bodies only."""

import copy
import json

import pytest

from test_plan15_settle import P, layer
from test_plan15_step5 import QUESTION, simple
from test_plan18_io import FakeTransport, body
from claview.a import development as BATCH, development_metrics as METRICS
from claview.a.interpret.decompose_archive import ArchiveError, ResponseArchive
from claview.a.interpret.decompose_provider import LiveSource


def fixture_batch(layer, monkeypatch, repeats=3, transport=None):
    policy = BATCH.read(BATCH.MODELS)
    profile = policy["profiles"][0]
    _, payload, schema = BATCH.input_of(QUESTION, layer)
    tasks = [{"call_id": f"synthetic:one:repeat{n}", "case_id": "one", "profile": profile, "repeat": n,
              "request_hash": BATCH.digest(BATCH.settings(profile, policy).request(payload, schema))}
             for n in range(1, repeats + 1)]
    manifest = {"inputs": [{"case_id": "one", "question": QUESTION, "as_of": "2026-10-21"}],
                "tasks": tasks, "policy": policy, "approval_hash": "synthetic_approved",
                "runtime_versions": BATCH.runtime_versions(), "catalog_version": BATCH.catalog_version(layer)}
    monkeypatch.setattr(BATCH, "check_manifest", lambda *args: manifest)
    monkeypatch.setattr(BATCH, "load_layer", lambda **kwargs: layer)
    transport = transport or FakeTransport()
    monkeypatch.setattr(BATCH, "LiveSource", lambda *args, **kwargs: LiveSource(*args, **kwargs, transport=transport))
    return manifest, transport


def test_ninety_tasks_have_three_independent_ids_with_identical_generation_inputs(layer):
    sample = BATCH.read(BATCH.SAMPLE)
    policy = BATCH.read(BATCH.MODELS)
    _, payload, schema = BATCH.input_of(QUESTION, layer)
    inputs = [{"case_id": x["case_id"], "payload": payload, "schema": schema} for x in sample["cases"]]
    tasks = BATCH.make_tasks(inputs, sample, policy)
    assert len(tasks) == len({t["call_id"] for t in tasks}) == 90
    assert [sum(t["profile"]["id"] == p["id"] for t in tasks) for p in policy["profiles"]] == [60, 15, 15]
    for p in policy["profiles"]:
        for cid in {t["case_id"] for t in tasks if t["profile"] == p}:
            group = [t for t in tasks if t["profile"] == p and t["case_id"] == cid]
            assert {t["repeat"] for t in group} == {1, 2, 3}
            assert len({t["request_hash"] for t in group}) == 1


def test_reservation_budget_is_shared_and_survives_restart(tmp_path):
    for n in range(90):
        ResponseArchive(tmp_path, 90).reserve(f"repeat{n}", {}, {}, "fake")
    with pytest.raises(ArchiveError, match="call_limit"):
        ResponseArchive(tmp_path, 90).reserve("over_budget", {}, {}, "fake")
    with pytest.raises(ArchiveError, match="already_reserved"):
        ResponseArchive(tmp_path, 90).reserve("repeat1", {}, {}, "fake")
    with pytest.raises(ValueError):
        ResponseArchive(tmp_path, 91)


def test_prepared_ninety_call_manifest_runs_all_tasks_with_fake_transport(layer, tmp_path, monkeypatch):
    sample = {"cases": [{"case_id": f"case{n}", "focus": "synthetic"} for n in range(20)],
              "comparison_ids": [f"case{n}" for n in range(5)], "coverage_gaps": ["synthetic only"]}
    sample_path = tmp_path / "sample.json"
    BATCH.write(sample_path, sample)
    monkeypatch.setattr(BATCH, "SAMPLE", sample_path)
    monkeypatch.setattr(BATCH, "sample_rows", lambda s: [{**x, "question": QUESTION} for x in s["cases"]])
    monkeypatch.setattr(BATCH, "load_layer", lambda **kwargs: layer)
    transport = FakeTransport()
    monkeypatch.setattr(BATCH, "LiveSource", lambda *args, **kwargs: LiveSource(*args, **kwargs, transport=transport))
    prepared = BATCH.prepare(tmp_path / "prepared")
    path = tmp_path / "prepared" / "manifest.json"
    manifest = BATCH.check_manifest(path, prepared["approval_hash"])
    assert len(manifest["tasks"]) == prepared["max_calls"] == 90
    assert all(row["as_of"] and row["as_of_source"] == "preparation_date" for row in manifest["inputs"])
    result = BATCH.run_batch(path, tmp_path / "out", "live", True, prepared["approval_hash"])
    assert result["finished"] == result["fake_calls"] == len(transport.calls) == 90
    assert result["real_calls"] == 0 and result["repeat_agreement"]["overall"]["groups"] == 30
    assert result["repeat_agreement"]["overall"]["structure"]["agree"] == 30
    assert result["repeat_agreement"]["overall"]["selection"]["agree"] == 30
    # The same approved batch cannot spend the budget again with fresh in-memory ledgers.
    again = BATCH.run_batch(path, tmp_path / "again", "live", True, prepared["approval_hash"])
    assert again["real_calls"] == again["fake_calls"] == 0 and again["stop_reason"] == "transport"
    assert len(transport.calls) == 90
    # Even a self-consistent hash cannot make duplicate repeat IDs a valid manifest.
    manifest["tasks"][1]["call_id"] = manifest["tasks"][0]["call_id"]
    manifest["approval_hash"] = BATCH.digest({k: v for k, v in manifest.items() if k != "approval_hash"})
    bad_path = tmp_path / "bad.json"
    BATCH.write(bad_path, manifest)
    with pytest.raises(ValueError, match="task IDs"):
        BATCH.check_manifest(bad_path)


def test_three_repeats_run_outer_graph_then_replay_without_cross_repeat_cache(layer, tmp_path, monkeypatch):
    manifest, transport = fixture_batch(layer, monkeypatch)
    path = tmp_path / "manifest.json"
    first = BATCH.run_batch(path, tmp_path / "first", "live", True, manifest["approval_hash"])
    assert len(transport.calls) == first["fake_calls"] == first["finished"] == 3
    assert first["real_calls"] == 0 and first["stop_reason"] is None
    assert first["repeat_agreement"]["overall"]["structure"] == {"agree": 1, "eligible": 1, "rate": 1.0}
    assert first["repeat_agreement"]["overall"]["selection"]["agree"] == 1
    ledgers = BATCH.read(tmp_path / "first" / "ledger.json")
    assert len(ledgers) == 3 and all(len(entries) == 1 for entries in ledgers.values())
    for task in manifest["tasks"]:
        result = BATCH.read(tmp_path / "first" / (BATCH.digest(task["call_id"]) + ".json"))
        assert result["repeat"] == task["repeat"] and result["as_of"] == "2026-10-21"
        assert result["selections"] and result["response"]["finished_by"]
        assert result["execution"]["provider_calls"] == 1
    replay = BATCH.run_batch(path, tmp_path / "replay", "replay", False, None, tmp_path / "first")
    assert replay["replay_calls"] == 3 and replay["real_calls"] == 0 and len(transport.calls) == 3
    assert set(replay["replay_comparisons"].values()) == {"same"}
    assert replay["repeat_agreement"] == first["repeat_agreement"]


@pytest.mark.parametrize("failure,expected,stop", [("transport", 1, "transport"),
                                                  ("format", 2, "consecutive_format_failures"),
                                                  ("semantic", 3, None)])
def test_failures_stop_without_retry_but_semantic_fallback_continues(layer, tmp_path, monkeypatch, failure, expected, stop):
    if failure == "transport":
        transport = FakeTransport(error=TimeoutError("synthetic private error"))
    elif failure == "format":
        transport = FakeTransport(raw=body({}))
    else:
        _, payload, _ = BATCH.input_of(QUESTION, layer)
        proposal = simple(P(payload))
        proposal["primary"]["requirements"][0]["indicator"]["subquote"] = "absent_subquote"
        transport = FakeTransport(raw=body(proposal))
    manifest, transport = fixture_batch(layer, monkeypatch, transport=transport)
    result = BATCH.run_batch(tmp_path / "manifest.json", tmp_path / "out", "live", True, manifest["approval_hash"])
    assert len(transport.calls) == result["finished"] == expected
    assert result["stop_reason"] == stop
    metric = result["repeat_agreement"]["overall"]
    assert metric["structure"]["eligible"] == metric["selection"]["eligible"] == 0
    assert metric["status_counts"]["all_rules" if failure == "semantic" else "incomplete"] == 1


@pytest.mark.parametrize("field", ["runtime_versions", "catalog_version"])
def test_live_rejects_changed_execution_context_before_transport(layer, tmp_path, monkeypatch, field):
    manifest, transport = fixture_batch(layer, monkeypatch)
    manifest[field] = "changed"
    with pytest.raises(ValueError, match="runtime or catalog"):
        BATCH.run_batch(tmp_path / "manifest.json", tmp_path / "out", "live", True, manifest["approval_hash"])
    assert transport.calls == [] and not (tmp_path / "out").exists()


def test_replay_uses_current_execution_context_and_records_it(layer, tmp_path, monkeypatch):
    manifest, _ = fixture_batch(layer, monkeypatch)
    BATCH.run_batch(tmp_path / "manifest.json", tmp_path / "first", "live", True, manifest["approval_hash"])
    monkeypatch.setattr(BATCH, "runtime_versions", lambda: {"changed_verifier": "new"})
    result = BATCH.run_batch(tmp_path / "manifest.json", tmp_path / "replay", "replay", False, None)
    assert result["replay_calls"] == 3 and result["real_calls"] == 0
    assert BATCH.read(tmp_path / "replay" / "run.json")["runtime_versions"] == {"changed_verifier": "new"}


def proposal_two():
    def ref(sid):
        return {"segment_ids": [sid], "subquote": None, "sense_id": None}
    reqs = [{"id": rid, "indicator": ref(sid), "targets": [], "basis_refs": [], "context": None,
             "breakdowns": []} for rid, sid in (("r1", "s1"), ("r2", "s2"))]
    return {"primary": {"requirements": reqs,
        "condition_bindings": [{"condition_id": "c1", "scope": "requirements", "requirement_ids": ["r1"], "name_refs": []}],
        "relations": [{"condition_id": "c2", "requirement_ids": ["r1", "r2"]}],
        "polarity_links": [{"kind": "exclude", "marker_refs": [ref("s4")], "negative_refs": [ref("s3")],
                            "positive_refs": [], "requirement_ids": ["r2"]}], "request_refs": [], "unused_refs": []},
        "alternative": None, "ambiguities": []}


def test_structure_ignores_only_ids_and_unordered_collection_order():
    one = proposal_two()
    two = json.loads(json.dumps(one).replace('"r1"', '"tmp"').replace('"r2"', '"r1"').replace('"tmp"', '"r2"'))
    two["primary"]["requirements"].reverse()
    assert METRICS.proposal_signature(one) == METRICS.proposal_signature(two)


@pytest.mark.parametrize("change", ["binding", "direction", "polarity", "sense", "subquote", "role", "unused", "alternative"])
def test_semantic_differences_cannot_disappear_in_structure_metric(change):
    one = proposal_two()
    two = copy.deepcopy(one)
    reading = two["primary"]
    if change == "binding": reading["condition_bindings"][0]["requirement_ids"] = ["r2"]
    if change == "direction": reading["relations"][0]["requirement_ids"].reverse()
    if change == "polarity": reading["polarity_links"][0]["kind"] = "replace"
    if change == "sense": reading["requirements"][0]["indicator"]["sense_id"] = "z1"
    if change == "subquote": reading["requirements"][0]["indicator"]["subquote"] = "part"
    if change == "role": reading["requirements"][0]["targets"] = [reading["requirements"][0].pop("indicator")]
    if change == "unused": reading["unused_refs"] = [{"ref": {"segment_ids": ["s5"]}, "reason": "unmapped"}]
    if change == "alternative": two["alternative"] = copy.deepcopy(reading)
    assert METRICS.proposal_signature(one) != METRICS.proposal_signature(two, alternative_kept=change == "alternative")


def test_duplicate_requirements_are_not_silently_merged():
    proposal = proposal_two()
    proposal["primary"]["requirements"][1] = {**proposal["primary"]["requirements"][0], "id": "r2"}
    assert METRICS.proposal_signature(proposal) is None


def test_all_rules_mixed_incomplete_and_changed_groups_have_explicit_denominators():
    tasks, rows = [], []
    for case, modes in (("all_rules", [False]*3), ("mixed", [True, False, True]),
                        ("changed", [True]*3), ("incomplete", [True]*2)):
        for n in range(1, 4):
            tasks.append({"profile": {"id": "p"}, "case_id": case, "repeat": n})
        for n, model in enumerate(modes, 1):
            rows.append({"profile_id": "p", "case_id": case, "repeat": n,
                         "primary_analysis": {"decomposition": {"applied_source": "model" if model else "rules_fallback"}},
                         "structure_signature": str(n) if case == "changed" else "same",
                         "selection_signature": str(n) if case == "mixed" else "same", "selections": []})
    report = METRICS.repeat_report(tasks, rows)["overall"]
    assert report["status_counts"] == {"all_rules": 1, "mixed": 1, "all_model": 1, "incomplete": 1}
    assert report["structure"] == {"agree": 0, "eligible": 1, "rate": 0.0}
    assert report["selection"] == {"agree": 1, "eligible": 2, "rate": 0.5}


def test_selection_metric_retains_requirement_to_table_assignment():
    analysis = {"interpretation": {"requirements": [{"id": "r1", "context": "first"}, {"id": "r2", "context": "second"}]}}
    selections = [{"requirement_id": rid, "status": "selected", "tables": [{"table_id": tid}]}
                  for rid, tid in (("r1", "t1"), ("r2", "t2"))]
    assert METRICS.selection_signature(analysis, selections) == METRICS.selection_signature(analysis, selections[::-1])
    swapped = copy.deepcopy(selections)
    swapped[0]["tables"], swapped[1]["tables"] = swapped[1]["tables"], swapped[0]["tables"]
    assert METRICS.selection_signature(analysis, selections) != METRICS.selection_signature(analysis, swapped)
