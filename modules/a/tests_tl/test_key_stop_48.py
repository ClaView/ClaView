"""A refused key stops a live exploration run (plan 48, user 2026-10-29 10:40 UTC): the first 401/403 is archived and
counted like any failed send, the next attempt is refused before anything is sent, the run stops and keeps what it
recorded. Approved runs on a fake transport; no network."""

from __future__ import annotations

import json

import pytest

from tl_eval import explore as E
from tl_eval import live as L

MODEL = {"name": "terra", "model": "gpt-6.1-terra", "effort": "low", "max_output_tokens": 4096}
Q = "2024년 1~3월 M2 총액을 평잔 기준으로 보여줘"


class Transport:
    kind = "fake"

    def __init__(self, fail_with=None, fail_on=(1,)):
        self.sent, self.fail_with, self.fail_on = [], fail_with, set(fail_on)

    def preflight(self):
        pass

    def send(self, request):
        from claview_a.transport.provider import ProviderError

        self.sent.append(request["model"])
        if self.fail_with and len(self.sent) in self.fail_on:
            raise ProviderError(self.fail_with)
        out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices",
                               "asks", "missing", "alternatives")}
        return json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                           "output": [{"type": "message", "content": [{"type": "output_text",
                                                                       "text": json.dumps(out)}]}]})


def manifest(tmp_path, n=3):
    cases = [{"case_id": f"T{i}", "question": Q, "as_of": "2026-10-22", "standard": None, "answer_state": {}}
             for i in range(1, n + 1)]
    (tmp_path / "l.json").write_text(json.dumps({"version": "t", "cases": cases}, ensure_ascii=False),
                                     encoding="utf-8")
    m = E.make_manifest("t", MODEL, tmp_path / "l.json")
    m["approval"]["live_calls"] = True
    return m


@pytest.mark.parametrize("code", ["http_401", "http_403"])
def test_the_first_key_refusal_stops_every_later_send(tmp_path, layer, code):
    t = Transport(fail_with=code)
    with pytest.raises(L.CallRefused):
        E.run(manifest(tmp_path), "live", tmp_path / "run", layer=layer, transport=t)
    assert t.sent == ["gpt-6.1-terra"]  # one send only: the refused one
    run = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    calls = [json.loads(x) for x in (tmp_path / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert run["calls"] == 1 and [c["outcome"] for c in calls] == ["failed"]  # the failed send counts as used
    assert run["refused_before_sending"] == [{"case_id": "T2", "model": "terra", "reason": f"key_refused:{code}"}]
    assert run["stopped"]["error"] == "CallRefused"
    assert json.loads((tmp_path / "run" / "records.json").read_text(encoding="utf-8"))  # what ran is kept


def test_other_send_failures_do_not_stop_the_run(tmp_path, layer):
    t = Transport(fail_with="timeout")
    got = E.run(manifest(tmp_path), "live", tmp_path / "run", layer=layer, transport=t)
    assert len(t.sent) == 3 and got["calls"] == 3 and got["stopped"] is None  # a question's own failure


def test_the_gate_refuses_after_a_halt():
    m = {"model": MODEL, "schedule": [{"case_id": "A"}, {"case_id": "B"}], "max_calls": 2}
    gate = L.CallGate(m)
    gate.for_case("A")
    gate.halt("http_403")
    with pytest.raises(L.CallRefused):
        gate.judge("terra", lambda payload, request: "{}")({}, {})
    assert gate.refused == [{"case_id": "A", "model": "terra", "reason": "key_refused:http_403"}]
    assert gate.total() == 0
