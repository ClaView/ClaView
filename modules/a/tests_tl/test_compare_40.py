"""Model comparison runner (plan 40) without a live call: the schedule, the limits, the gates, a fake run and an
approved run on a counting fake transport. Real golden standards from the evaluation bundle; no model, no network."""

from __future__ import annotations

import json

import pytest

from tl_eval import compare as C
from tl_eval import files as F
from tl_eval import standard as ST

pytestmark = pytest.mark.skipif(not F.present("standard_v3.json", "standard_golden26.json"),
                                reason="evaluation files absent")

MODELS = [{"name": "a", "model": "test-model-a", "effort": "low", "max_output_tokens": 4096},
          {"name": "b", "model": "test-model-b", "effort": "low", "max_output_tokens": 4096},
          {"name": "c", "model": "test-model-c", "effort": "low", "max_output_tokens": 4096}]
EMPTY = json.dumps({k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices",
                                    "asks", "missing", "alternatives")})


def answer(text: str) -> str:
    return json.dumps({"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 20},
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


class FakeTransport:
    kind = "fake"

    def __init__(self):
        self.sent = []

    def preflight(self):
        pass

    def send(self, request):
        self.sent.append(request)
        return answer(EMPTY)


@pytest.fixture()
def manifest():
    parts = [{"standard": F.external("standard_v3.json"), "cases": ["CV-AB-01", "CV-AB-17"]},
             {"standard": ST.F.external("standard_golden26.json"), "cases": ["GV4-07"]}]
    return C.make_manifest("cmp-test", MODELS, parts, repeat=["CV-AB-17"],
                           prices={"a": {"input": 1.0, "cached_input": 0.1, "output": 2.0}})


def test_schedule_runs_every_case_once_per_model_then_the_repeats(manifest):
    plan = manifest["schedule"]
    assert manifest["max_calls"] == len(plan) == 3 * 3 + 1 * 3 * 2
    assert manifest["max_calls_per_model"] == 5 and manifest["retries"] == 0 and not manifest["approval"]["live_calls"]
    first = [e for e in plan if e["round"] == 1]
    assert {(e["case_id"], e["model"]) for e in first} == {(c, m["name"]) for c in ("CV-AB-01", "CV-AB-17", "GV4-07")
                                                         for m in MODELS}
    assert {(e["case_id"], e["round"]) for e in plan if e["round"] > 1} == {("CV-AB-17", 2), ("CV-AB-17", 3)}
    orders = [tuple(e["model"] for e in plan[i:i + 3]) for i in range(0, len(plan), 3)]
    assert len(set(orders)) > 1  # the model order is shuffled per case and round, not fixed
    again = C.schedule([m["name"] for m in MODELS], ["CV-AB-01", "CV-AB-17", "GV4-07"], ["CV-AB-17"], 2,
                       manifest["seed"])
    assert again == plan  # the same seed gives the same schedule


def test_manifest_refuses_unknown_or_repeated_cases():
    parts = [{"standard": F.external("standard_v3.json"), "cases": ["CV-AB-01", "CV-AB-01"]}]
    with pytest.raises(ValueError):
        C.make_manifest("x", MODELS, parts, repeat=[])
    parts = [{"standard": F.external("standard_v3.json"), "cases": ["CV-AB-01"]}]
    with pytest.raises(ValueError):
        C.make_manifest("x", MODELS, parts, repeat=["GV4-07"])
    with pytest.raises(ValueError):
        C.make_manifest("x", MODELS + [dict(MODELS[0])], parts, repeat=[])


def test_gates_refuse_a_changed_schedule_or_limit(manifest):
    assert all(r["ok"] for r in C.gates(manifest))
    bad = json.loads(json.dumps(manifest))
    bad["schedule"].append({"case_id": "CV-AB-01", "model": "a", "round": 4})
    assert not all(r["ok"] for r in C.gates(bad))
    bad = json.loads(json.dumps(manifest))
    bad["retries"] = 1
    assert not all(r["ok"] for r in C.gates(bad))


def test_live_refused_without_approval(manifest, layer, tmp_path):
    with pytest.raises(PermissionError):
        C.run(manifest, "live", tmp_path / "r", layer=layer, transport=FakeTransport())


def test_fake_run_keeps_models_and_rounds_apart(manifest, layer, tmp_path):
    got = C.run(manifest, "fake", tmp_path / "r", layer=layer)
    assert got["calls"] == 15 and got["calls_per_model"] == {"a": 5, "b": 5, "c": 5} and got["stopped"] is None
    records = json.loads((tmp_path / "r" / "records.json").read_text(encoding="utf-8"))
    assert len(records) == 15 and "b|3|CV-AB-17" in records and records["b|3|CV-AB-17"]["round"] == 3
    s = got["summary"]
    assert set(s["models"]) == {"a", "b", "c"} and s["models"]["a"]["first_round"]["questions"] == 3
    rep = s["repeat"]["CV-AB-17"]["a"]
    assert len(rep["completed"]) == 3 and rep["same_decision"] is True  # the fake judge answers the same way
    assert s["note"].startswith("fake judge")


def test_approved_run_sends_one_request_per_entry_with_each_model_id(manifest, layer, tmp_path):
    approved = json.loads(json.dumps(manifest))
    approved["approval"]["live_calls"] = True
    t = FakeTransport()
    got = C.run(approved, "live", tmp_path / "r", layer=layer, transport=t)
    assert len(t.sent) == 15 and got["calls"] == 15
    assert sorted({r["model"] for r in t.sent}) == ["test-model-a", "test-model-b", "test-model-c"]
    assert [r["model"] for r in t.sent].count("test-model-a") == 5
    calls = [json.loads(x) for x in (tmp_path / "r" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 15 and all(c["case_id"].count("|") == 2 for c in calls)
    a = got["summary"]["models"]["a"]
    assert a["tokens"]["input"] == 500 and a["cost_estimate"] == round((500 * 1.0 + 100 * 2.0) / 1e6, 4)
    for c in calls:  # every request is new: no earlier answer goes into a later request
        assert EMPTY not in c["request"]["input"]


def test_a_per_model_limit_stops_the_run(manifest, layer, tmp_path):
    approved = json.loads(json.dumps(manifest))
    approved["approval"]["live_calls"] = True
    approved["max_calls_per_model"] = 4  # one short: the gate refuses before any call
    t = FakeTransport()
    with pytest.raises(RuntimeError, match="gates failed"):
        C.run(approved, "live", tmp_path / "r", layer=layer, transport=t)
    assert t.sent == []
