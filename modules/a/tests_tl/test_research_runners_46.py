"""Re-judgments by another model in the exploration runner (users 2026-10-29 07:51 and 08:18 UTC): off by default;
only listed cases, one re-judgment each, by the re-judge model; limits per case, per model and for the run, fixed in
the manifest and its gates; a used-up limit recorded without a call; more calls than allowed stop the run.
Approved runs on a counting fake transport; no model, no network."""

from __future__ import annotations

import json

import pytest

from tl_eval import explore as E
from tl_eval import live as L

FIRST = {"name": "terra", "model": "gpt-6.1-terra", "effort": "low", "max_output_tokens": 4096}
SECOND = {"name": "sol", "model": "gpt-6.1-sol", "effort": "low", "max_output_tokens": 4096}
Q = "2024년에 국내에서 생산해 국내에 팔거나 수출한 것들의 전체 물가 흐름을 월별로 보여줘"


def answer(text: str) -> str:
    return json.dumps({"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 20},
                       "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


def missing_text() -> str:
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": "s1", "quote": "전체 물가 흐름", "name": "물가"}]
    out["missing"] = [{"for": ["s1"], "search_words": ["총산출물가지수"]}]
    return json.dumps(out, ensure_ascii=False)


class MissingTransport:
    """Every answer leaves the statistic missing; keeps the model id of each request."""

    kind = "fake"

    def __init__(self, fail_on=()):
        self.models, self.fail_on = [], set(fail_on)

    def preflight(self):
        pass

    def send(self, request):
        self.models.append(request["model"])
        if len(self.models) in self.fail_on:
            from claview_a.transport.provider import ProviderError

            raise ProviderError("timeout")
        return answer(missing_text())


def write_list(path, n):
    cases = [{"case_id": f"T{i}", "question": Q, "as_of": "2026-10-22", "standard": None,
              "answer_state": {"representative": {"state": "insufficient", "why": "test"}}} for i in range(1, n + 1)]
    path.write_text(json.dumps({"version": "test-list.1", "cases": cases}, ensure_ascii=False), encoding="utf-8")
    return path


def approve(m):
    m = json.loads(json.dumps(m))
    m["approval"]["live_calls"] = True
    return m


def manifest(tmp_path, n=3, cases=("T2", "T3")):
    return E.make_manifest("t", FIRST, write_list(tmp_path / "l.json", n),
                           research={"model": SECOND, "cases": list(cases)} if cases else None)


def test_the_limits_per_case_model_and_run_are_fixed_in_the_manifest(tmp_path):
    m = manifest(tmp_path)
    assert m["max_calls"] == 5 and m["max_calls_per_model"] == {"terra": 3, "sol": 2}
    assert m["max_calls_per_case"] == {"T1": 1, "T2": 2, "T3": 2} and m["retries"] == 0
    assert m["research"] == L.research_conditions(SECOND, ["T2", "T3"]) and all(r["ok"] for r in E.gates(m))
    for key, value in (("max_calls", 6), ("max_calls_per_model", {"terra": 3, "sol": 3}),
                       ("max_calls_per_case", {"T1": 2, "T2": 2, "T3": 2})):
        bad = json.loads(json.dumps(m))
        bad[key] = value  # any widened limit fails the gates
        assert not all(r["ok"] for r in E.gates(bad)), key
    bad = json.loads(json.dumps(m))
    bad["research"]["cases"].append("T1")
    assert not all(r["ok"] for r in E.gates(bad))
    with pytest.raises(ValueError):
        manifest(tmp_path, cases=("T9",))
    with pytest.raises(ValueError):
        E.make_manifest("t", FIRST, write_list(tmp_path / "l.json", 1),
                        research={"model": dict(SECOND, name="terra"), "cases": ["T1"]})


def test_off_by_default_and_an_older_manifest_reads_as_off(tmp_path, layer):
    m = manifest(tmp_path, n=2, cases=())
    assert m["research"]["enabled"] is False and m["max_calls"] == 2
    del m["research"], m["max_calls_per_model"], m["max_calls_per_case"]
    assert all(r["ok"] for r in E.gates(m))
    t = MissingTransport()
    got = E.run(approve(m), "live", tmp_path / "run", layer=layer, transport=t)
    assert t.models == ["gpt-6.1-terra"] * 2 and got["rejudge_calls"] == 0 and got["research_ends"] == {}


def test_only_listed_cases_get_one_re_judgment_by_the_other_model(tmp_path, layer):
    t = MissingTransport()
    got = E.run(approve(manifest(tmp_path)), "live", tmp_path / "run", layer=layer, transport=t)
    assert t.models == ["gpt-6.1-terra", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6.1-sol"]
    assert got["calls"] == 5 and got["calls_per_model"] == {"terra": 3, "sol": 2} and got["rejudge_calls"] == 2
    assert got["research_ends"] == {"terra|1|T2": "still_missing", "terra|1|T3": "still_missing"}
    q = got["summary"]["questions"]
    assert "research" not in q["terra|1|T1"] and q["terra|1|T2"]["research"]["end"] == "still_missing"
    by = got["summary"]["usage"]["by_model"]
    assert by["gpt-6.1-terra"]["calls"] == 3 and by["gpt-6.1-sol"]["calls"] == 2  # re-judgments are paid calls


def test_a_failed_call_counts_and_is_never_retried(tmp_path, layer):
    t = MissingTransport(fail_on={2})  # the first judgment of T2 fails: no retry, no re-search for T2
    got = E.run(approve(manifest(tmp_path)), "live", tmp_path / "run", layer=layer, transport=t)
    assert t.models == ["gpt-6.1-terra", "gpt-6.1-terra", "gpt-6.1-terra", "gpt-6.1-sol"] and got["calls"] == 4
    assert got["research_ends"]["terra|1|T2"] == "first_round_error"
    t = MissingTransport(fail_on={3})  # the re-judgment of T2 fails: counted, not retried, not moved to T3
    got = E.run(approve(manifest(tmp_path)), "live", tmp_path / "run2", layer=layer, transport=t)
    assert t.models.count("gpt-6.1-sol") == 2 and got["calls"] == 5
    assert got["research_ends"]["terra|1|T2"] == "second_round_error"


def test_the_budget_refuses_unlisted_used_and_over_limit_re_judgments():
    research = L.research_conditions(SECOND, ["A", "B"])
    counts = {"all": 0, "re": 0}
    b = L.RejudgeBudget(research, lambda: counts["all"], lambda: counts["re"], max_calls=4)
    assert b.for_case("C")() is False  # not listed
    assert b.for_case("A")() is True and b.for_case("A")() is False  # one per case, never twice
    counts["re"] = 2
    assert b.for_case("B")() is False  # the re-judge model's limit
    counts["re"], counts["all"] = 1, 4
    assert b.for_case("B")() is False  # the run's limit
    assert b.used == ["A"]


def broken_path(monkeypatch, case, extra):
    """A broken graph path: after the real graph of ``case`` (T1, T2 or T3, run in that order), one more call
    through ``extra(judge, settings, kw)``."""
    real = E.graph_run
    calls = {"n": 0}

    def wrapped(question, layer, judge, settings, as_of, **kw):
        calls["n"] += 1
        state = real(question, layer, judge, settings, as_of, **kw)
        if calls["n"] == {"T1": 1, "T2": 2, "T3": 3}[case]:
            extra(judge, settings, kw)
        return state

    monkeypatch.setattr(E, "graph_run", wrapped)


def run_broken(tmp_path, layer):
    t = MissingTransport()
    with pytest.raises(L.CallRefused):
        E.run(approve(manifest(tmp_path)), "live", tmp_path / "run", layer=layer, transport=t)
    kept = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    return t, kept


def again(judge, settings):
    judge({"question": Q, "cards": "", "ask_axes": {}}, settings.request({"question": Q}))


def test_a_second_first_judge_call_is_refused_before_it_is_sent(tmp_path, layer, monkeypatch):
    # user 2026-10-29 09:04 UTC: was "two calls sent, then the run stops"; now the second Terra attempt is refused
    # before sending, so the fake transport receives one call
    broken_path(monkeypatch, "T1", lambda judge, settings, kw: again(judge, settings))
    t, kept = run_broken(tmp_path, layer)
    assert t.models == ["gpt-6.1-terra"] and kept["calls"] == 1
    assert kept["stopped"]["error"] == "CallRefused"
    assert kept["refused_before_sending"] == [{"case_id": "T1", "model": "terra", "reason": "case_limit"}]


def test_a_second_re_judge_call_is_refused_before_it_is_sent(tmp_path, layer, monkeypatch):
    broken_path(monkeypatch, "T2", lambda judge, settings, kw: again(kw["research_judge"], kw["research_settings"]))
    t, kept = run_broken(tmp_path, layer)
    assert t.models == ["gpt-6.1-terra", "gpt-6.1-terra", "gpt-6.1-sol"] and kept["calls"] == 3
    assert kept["refused_before_sending"] == [{"case_id": "T2", "model": "sol", "reason": "case_limit"}]


def test_a_re_judge_call_for_an_unlisted_case_is_refused_before_it_is_sent(tmp_path, layer, monkeypatch):
    broken_path(monkeypatch, "T1", lambda judge, settings, kw: again(kw["research_judge"], kw["research_settings"]))
    t, kept = run_broken(tmp_path, layer)
    assert t.models == ["gpt-6.1-terra"] and kept["calls"] == 1
    assert kept["refused_before_sending"] == [{"case_id": "T1", "model": "sol", "reason": "case_limit"}]


def test_the_gate_checks_the_model_and_run_limits_and_counts_failed_sends(tmp_path):
    from claview_a.graph import JudgeError

    gate = L.CallGate(manifest(tmp_path))
    sent = []

    def ok(payload, request):
        sent.append(1)
        return "{}"

    def fails(payload, request):
        sent.append(1)
        raise JudgeError("timeout")

    terra, sol = gate.judge("terra", fails), gate.judge("sol", ok)
    gate.for_case("T2")
    with pytest.raises(JudgeError):
        terra({}, {})
    assert gate.calls("terra") == 1 and gate.total() == 1  # a failed send is a used call
    with pytest.raises(L.CallRefused):
        terra({}, {})  # no retry: the case's one first judgment is used
    sol({}, {})
    gate.per_model["sol"] = 1  # a model limit tighter than the cases allow
    gate.for_case("T3")
    with pytest.raises(L.CallRefused):
        sol({}, {})
    gate.max_calls = 2  # the run's limit
    with pytest.raises(L.CallRefused):
        gate.judge("terra", ok)({}, {})
    assert len(sent) == 2 and [r["reason"] for r in gate.refused] == ["case_limit", "model_limit", "run_limit"]
