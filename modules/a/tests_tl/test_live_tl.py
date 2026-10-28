"""Plan 37 step 5 preparation (user 2026-10-28 04:44 UTC): the live runner without a live call. The transport is the
current path's ``OpenAITransport`` with ``http.client`` replaced by a counting fake, or a fake transport object; the
network guard of conftest refuses any real socket. The key value used here is a test placeholder set in the test
environment, never a real key."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from claview_a.graph import JudgeError  # noqa: E402
from claview_a.transport.provider import OpenAITransport, ProviderError  # noqa: E402
from tl_eval import live as L  # noqa: E402

CASES = ["SB:DA3-01", "CV-AB-05"]


def answer(text: str, usage=None, status="completed") -> str:
    return json.dumps({"status": status, "usage": usage or {"input_tokens": 100, "output_tokens": 20},
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


EMPTY = json.dumps({k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices",
                                    "asks", "missing", "alternatives")})


class FakeTransport:
    kind = "fake"

    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def preflight(self):
        pass

    def send(self, request):
        self.sent.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# ------------------------------------------------------------------ the transport: one request, no retry


class CountingConnection:
    made: list = []
    behaviour = "timeout"

    def __init__(self, host, timeout=None):
        self.host, self.timeout, self.requests = host, timeout, 0
        CountingConnection.made.append(self)

    def request(self, method, path, body=None, headers=None):
        self.requests += 1

    def getresponse(self):
        if CountingConnection.behaviour == "timeout":
            raise TimeoutError("read timed out")
        if CountingConnection.behaviour == "reset":
            raise ConnectionResetError("reset")

        class Response:
            status = 429 if CountingConnection.behaviour == "429" else 200

            @staticmethod
            def read():
                return answer(EMPTY).encode("utf-8")

        return Response()

    def close(self):
        pass


@pytest.mark.parametrize("behaviour,code,cause", [("timeout", "transport", "timeout"),
                                                  ("reset", "transport", "transport"),
                                                  ("429", "http_429", "transport")])
def test_transport_sends_once_without_retry(monkeypatch, behaviour, code, cause):
    import http.client

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-not-a-key")
    monkeypatch.setattr(http.client, "HTTPSConnection", CountingConnection)
    CountingConnection.made, CountingConnection.behaviour = [], behaviour
    with pytest.raises(ProviderError) as e:
        OpenAITransport().send({"model": "x"})
    assert str(e.value) == code
    assert len(CountingConnection.made) == 1 and CountingConnection.made[0].requests == 1
    assert CountingConnection.made[0].timeout == 60.0
    assert L._cause(e.value)[0] == cause


def test_transport_missing_key_sends_nothing(monkeypatch):
    import http.client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(http.client, "HTTPSConnection", CountingConnection)
    CountingConnection.made = []
    with pytest.raises(ProviderError, match="key_unavailable"):
        OpenAITransport().send({"model": "x"})
    assert CountingConnection.made == []


# ------------------------------------------------------------------ the judge: budget, causes, usage, no re-call


def judge(tmp_path, replies, max_calls=6):
    t = FakeTransport(replies)
    archive = L.Archive(tmp_path / "calls.jsonl")
    return t, archive, L.LiveJudge(t, max_calls, archive, {"case_id": "c"})


def test_budget_refuses_before_sending(tmp_path):
    t, archive, j = judge(tmp_path, [answer(EMPTY)] * 7)
    for _ in range(6):
        j({}, {"n": 1})
    with pytest.raises(L.BudgetExceeded):
        j({}, {"n": 1})
    assert len(t.sent) == 6 and len(archive.rows) == 6
    assert len((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()) == 6


@pytest.mark.parametrize("raw,code", [
    (json.dumps({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                 "usage": {"input_tokens": 900, "output_tokens": 4096}, "output": []}), "incomplete"),
    (json.dumps({"status": "completed", "usage": {"input_tokens": 900, "output_tokens": 5},
                 "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}), "refusal"),
    (json.dumps({"status": "completed", "usage": {"input_tokens": 900, "output_tokens": 0}, "output": []}),
     "empty_output"),
    ("not json", "response_format")])
def test_failed_answers_keep_cause_and_usage(tmp_path, raw, code):
    t, archive, j = judge(tmp_path, [raw])
    with pytest.raises(JudgeError) as e:
        j({}, {"n": 1})
    assert e.value.code == code and len(t.sent) == 1
    row = archive.rows[0]
    assert row["outcome"] == "failed" and row["error"] == code and row["raw_response"] == raw
    if code != "response_format":
        assert row["usage"]["input_tokens"] == 900
    if code == "incomplete":
        assert row["incomplete_details"] == {"reason": "max_output_tokens"}


def test_transport_failure_is_archived(tmp_path):
    err = None
    try:  # the way OpenAITransport.send raises it
        try:
            raise TimeoutError()
        except Exception:
            raise ProviderError("transport") from None
    except ProviderError as e:
        err = e
    t, archive, j = judge(tmp_path, [err])
    with pytest.raises(JudgeError, match="timeout"):
        j({}, {"n": 1})
    assert archive.rows[0]["error"] == "timeout" and archive.rows[0]["usage"] is None


# ------------------------------------------------------------------ manifest, gates, approval, runs


@pytest.fixture(scope="module")
def manifest():
    return L.make_manifest("test", "test", CASES)


def test_manifest_starts_unapproved(manifest):
    assert manifest["approval"]["live_calls"] is False and manifest["max_calls"] == 2 and manifest["retries"] == 0
    assert all(r["ok"] for r in L.gates(manifest))


def test_gates_refuse_a_changed_condition(manifest):
    changed = json.loads(json.dumps(manifest))
    changed["conditions"]["run_info"]["versions"]["prompt_sha256"] = "0" * 64
    assert [r["check"] for r in L.gates(changed) if not r["ok"]] == ["run_info"]
    changed = json.loads(json.dumps(manifest))
    changed["max_calls"] = 3
    assert [r["check"] for r in L.gates(changed) if not r["ok"]] == ["call_limit"]


def test_live_refused_without_approval(manifest, layer, tmp_path):
    t = FakeTransport([answer(EMPTY)])
    with pytest.raises(PermissionError):
        L.run(manifest, "live", tmp_path / "r", layer=layer, transport=t)
    assert t.sent == [] and not (tmp_path / "r").exists()


def test_existing_folder_refused(manifest, layer, tmp_path):
    (tmp_path / "r").mkdir()
    with pytest.raises(FileExistsError):
        L.run(manifest, "fake", tmp_path / "r", layer=layer)


def test_approved_run_one_call_per_case_and_failure_kept(manifest, layer, tmp_path):
    approved = json.loads(json.dumps(manifest))
    approved["approval"]["live_calls"] = True
    incomplete = json.dumps({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                             "usage": {"input_tokens": 50, "output_tokens": 4096}, "output": []})
    t = FakeTransport([incomplete, answer(EMPTY)])
    got = L.run(approved, "live", tmp_path / "r", layer=layer, transport=t)
    assert len(t.sent) == 2 and got["calls"] == 2
    records = json.loads((tmp_path / "r" / "records.json").read_text(encoding="utf-8"))
    assert records[CASES[0]]["error"]["code"] == "incomplete" and records[CASES[0]]["route"] == "fail"
    assert got["failed_calls"][0]["error"] == "incomplete" and got["tokens"]["output"] == 4096 + 20
    # the same responses replayed: no transport, same records
    again = L.run(approved, "replay", tmp_path / "p", layer=layer, replay_from=tmp_path / "r")
    replayed = json.loads((tmp_path / "p" / "records.json").read_text(encoding="utf-8"))
    for cid in CASES:
        assert {k: v for k, v in replayed[cid].items() if k != "mode"} == \
               {k: v for k, v in records[cid].items() if k != "mode"}
    assert again["calls"] == 2 and len(t.sent) == 2


def test_combine_refuses_mixed_conditions(manifest, layer, tmp_path):
    L.run(manifest, "fake", tmp_path / "a", layer=layer)
    other = L.make_manifest("test2", "test", ["CV-AB-07"])
    L.run(other, "fake", tmp_path / "b", layer=layer)
    assert set(L.combine([tmp_path / "a", tmp_path / "b"])) == {*CASES, "CV-AB-07"}
    with pytest.raises(ValueError, match="more than one run"):
        L.combine([tmp_path / "a", tmp_path / "a"])
    m = json.loads((tmp_path / "b" / "manifest.json").read_text(encoding="utf-8"))
    m["conditions"]["run_info"]["versions"]["prompt_sha256"] = "0" * 64
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError, match="different conditions"):
        L.combine([tmp_path / "a", tmp_path / "b"])


# ------------------------------------------------------------------ 05:30 UTC: the 40-question run's stops and subsets


def test_subsets_are_scored_apart(layer, tmp_path):
    m = L.make_manifest("sub", "test", CASES, subsets={"one": [CASES[0]], "other": [CASES[1]]})
    got = L.run(m, "fake", tmp_path / "r", layer=layer)
    assert {k: v["aggregate"]["all"]["questions"] for k, v in got["report"]["subsets"].items()} == {"one": 1, "other": 1}
    with pytest.raises(ValueError):
        L.make_manifest("sub", "test", CASES, subsets={"x": ["CV-AB-07"]})


def test_a_condition_changing_during_the_run_stops_it(layer, tmp_path, monkeypatch, manifest):
    real, seen = L.gates, []

    def flaky(m):
        seen.append(1)
        rows = real(m)
        return rows if len(seen) < 3 else [{**rows[0], "ok": False}] + rows[1:]  # start, case 1 fine, case 2 changed

    monkeypatch.setattr(L, "gates", flaky)
    with pytest.raises(L.RunStopped, match="fixed conditions changed"):
        L.run(manifest, "fake", tmp_path / "r", layer=layer)
    run = json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))
    assert run["stopped"]["case_id"] == CASES[1] and run["stopped"]["records_kept"] == 1
    assert len(json.loads((tmp_path / "r" / "records.json").read_text(encoding="utf-8"))) == 1


def test_more_than_one_call_for_a_question_stops_the_run(layer, tmp_path, monkeypatch, manifest):
    real = L.FakeJudge.__call__

    def twice(self, payload, request):
        self.calls += 1
        return real(self, payload, request)

    monkeypatch.setattr(L.FakeJudge, "__call__", twice)
    with pytest.raises(L.RunStopped, match="calls for one question"):
        L.run(manifest, "fake", tmp_path / "r", layer=layer)


def test_an_archive_that_cannot_be_written_stops_the_run(layer, tmp_path, monkeypatch, manifest):
    def broken(self, row):
        raise OSError("disk full")

    monkeypatch.setattr(L.Archive, "add", broken)
    with pytest.raises(OSError):
        L.run(manifest, "fake", tmp_path / "r", layer=layer)
    assert json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))["stopped"]["error"] == "OSError"


def test_a_question_error_is_recorded_and_the_run_goes_on(layer, tmp_path, manifest):
    approved = json.loads(json.dumps(manifest))
    approved["approval"]["live_calls"] = True
    t = FakeTransport([json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                                   "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "x"}]}]}),
                       answer("not json")])
    got = L.run(approved, "live", tmp_path / "r", layer=layer, transport=t)
    records = json.loads((tmp_path / "r" / "records.json").read_text(encoding="utf-8"))
    assert got["stopped"] is None and len(t.sent) == 2
    assert records[CASES[0]]["error"]["code"] == "refusal" and records[CASES[1]]["error"]["stage"] == "output"


# ------------------------------------------------------------------ 08:13 UTC: the answer file is chosen per manifest


def test_the_chosen_standard_hash_is_the_same_in_manifest_run_record_and_report(layer, tmp_path):
    golden = L.F.external("standard_golden26.json")
    sha = L.file_sha(golden)
    m = L.make_manifest("g", "test", ["GV4-01", "CV-AB-10"], standard_file=golden)
    assert m["conditions"]["standard"] == {"path": "eval/tl_eval/standard_golden26.json",
                                           "version": "tl-standard-golden-2026-10-28.2", "sha256": sha}
    assert m["approval"]["live_calls"] is False and all(r["ok"] for r in L.gates(m))
    got = L.run(m, "fake", tmp_path / "r", layer=layer)
    run = json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "r" / "report.json").read_text(encoding="utf-8"))
    assert run["standard"]["sha256"] == report["standard"]["sha256"] == sha
    assert {r["case_id"] for r in got["report"]["results"]} == {"GV4-01", "CV-AB-10"}
    with pytest.raises(ValueError):  # a case of another standard is refused
        L.make_manifest("g", "test", ["SB:DA3-01"], standard_file=golden)


def test_a_manifest_without_the_option_keeps_standard_v2(manifest):
    assert manifest["conditions"]["standard"]["path"] == "eval/tl_eval/standard_v2.json"
    assert L.standard_path(manifest) == L.ST.OUT_V2
    assert L.standard_path({"conditions": {"standard": {"sha256": "x"}}}) == L.ST.OUT_V2  # made before the option


def test_a_changed_standard_or_decisions_file_fails_the_gates(manifest):
    changed = json.loads(json.dumps(manifest))
    changed["conditions"]["standard"]["sha256"] = "0" * 64
    assert [r["check"] for r in L.gates(changed) if not r["ok"]] == ["standard"]
    changed = json.loads(json.dumps(manifest))
    changed["conditions"]["decisions"]["decisions"] = "0" * 64
    assert [r["check"] for r in L.gates(changed) if not r["ok"]] == ["decisions"]
    changed = json.loads(json.dumps(manifest))
    changed["conditions"]["standard"]["path"] = "eval/tl_eval/standard_v3.json"
    assert "standard" in [r["check"] for r in L.gates(changed) if not r["ok"]]
