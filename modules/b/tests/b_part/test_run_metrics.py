"""Latency / token / cost recording: model metrics, summary arithmetic, compare table."""

import json
import sys
from pathlib import Path

import httpx
import pytest

from claview_b.contracts import StageFailure, Trace
from claview_b.models import HcxModel, OpenAIModel, object_schema

SCRIPTS = Path(__file__).resolve().parents[4] / "modules" / "b" / "scripts" / "b_part"
sys.path.insert(0, str(SCRIPTS))

import compare_runs  # noqa: E402
import run_metrics  # noqa: E402
import verify_expand_hcx as verify  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": object_schema({"code": {"type": "string", "enum": ["a", "b"]}}),
        }
    },
    "required": ["selections"],
}


def hcx_reply(content='{"selections": []}', finish="stop", usage=True):
    result = {"message": {"content": content}, "finishReason": finish}
    if usage:
        result["usage"] = {"promptTokens": 500, "completionTokens": 100, "totalTokens": 600}
    return httpx.Response(200, json={"status": {"code": "20000"}, "result": result})


def openai_reply(finish="stop", usage=None):
    usage = (
        {
            "prompt_tokens": 400,
            "completion_tokens": 90,
            "completion_tokens_details": {"reasoning_tokens": 60},
        }
        if usage is None
        else usage
    )
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": finish, "message": {"content": '{"selections": []}'}}],
            "usage": usage,
        },
    )


def hcx_model(handler):
    return HcxModel("k-test", client=httpx.Client(transport=httpx.MockTransport(handler)))


def openai_model(handler):
    return OpenAIModel(
        "sk-test", "m", client=httpx.Client(transport=httpx.MockTransport(handler)), retries=0
    )


# ---------------------------------------------------------------- model metrics


def test_hcx_records_latency_and_tokens():
    model = hcx_model(lambda request: hcx_reply())
    assert model.last_metrics is None
    model.complete("B", "s", {}, SCHEMA, 100, Trace())
    m = model.last_metrics
    assert m["prompt_tokens"] == 500 and m["completion_tokens"] == 100
    assert m["reasoning_tokens"] is None and m["attempts"] == 1
    assert isinstance(m["latency_ms"], int) and m["latency_ms"] >= 0


def test_hcx_missing_usage_is_none_not_zero():
    model = hcx_model(lambda request: hcx_reply(usage=False))
    model.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert model.last_metrics["prompt_tokens"] is None
    assert model.last_metrics["completion_tokens"] is None


def test_hcx_http_error_clears_metrics_but_truncated_answer_keeps_them():
    model = hcx_model(lambda request: hcx_reply())
    model.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert model.last_metrics is not None

    failing = hcx_model(lambda request: httpx.Response(400, json={"status": {"code": "40000"}}))
    with pytest.raises(StageFailure):
        failing.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert failing.last_metrics is None

    truncated = hcx_model(lambda request: hcx_reply(finish="length"))
    with pytest.raises(StageFailure) as caught:
        truncated.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert caught.value.code == "output_truncated"
    # the endpoint billed the truncated answer, so its tokens are kept
    assert truncated.last_metrics["completion_tokens"] == 100


def test_hcx_metrics_reset_on_each_call():
    answers = iter([hcx_reply(), httpx.Response(400, json={"status": {"code": "40000"}})])
    model = hcx_model(lambda request: next(answers))
    model.complete("B", "s", {}, SCHEMA, 100, Trace())
    with pytest.raises(StageFailure):
        model.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert model.last_metrics is None  # not the previous call's numbers


def test_openai_records_reasoning_tokens_inside_completion():
    model = openai_model(lambda request: openai_reply())
    model.complete("B", "s", {}, SCHEMA, 100, Trace())
    m = model.last_metrics
    assert (m["prompt_tokens"], m["completion_tokens"], m["reasoning_tokens"]) == (400, 90, 60)
    assert model.usage == {"calls": 1, "prompt_tokens": 400, "completion_tokens": 90}


def test_openai_without_usage_or_on_error():
    model = openai_model(lambda request: openai_reply(usage={}))
    model.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert model.last_metrics["prompt_tokens"] is None

    failing = openai_model(lambda request: httpx.Response(400, json={"error": {"code": "x"}}))
    with pytest.raises(StageFailure):
        failing.complete("B", "s", {}, SCHEMA, 100, Trace())
    assert failing.last_metrics is None


# ---------------------------------------------------------------- arithmetic


def test_percentile_and_stats():
    assert run_metrics.percentile([], 95) is None
    assert run_metrics.percentile([5], 95) == 5
    values = list(range(1, 21))  # 1..20
    assert run_metrics.percentile(values, 95) == 19
    assert run_metrics.percentile(values, 50) == 10
    s = run_metrics.stats([1000, 2000, 3000, None])
    assert s == {"n": 3, "mean": 2000.0, "median": 2000.0, "p95": 3000, "min": 1000, "max": 3000}
    assert run_metrics.stats([])["n"] == 0


def row(passed, calls, latency=None):
    return {
        "case_id": "C",
        "pass": passed,
        "latency_ms": latency,
        "call_metrics": calls,
    }


def metrics(ms, p, c, attempts=1, reasoning=None):
    return {
        "latency_ms": ms,
        "attempts": attempts,
        "prompt_tokens": p,
        "completion_tokens": c,
        "reasoning_tokens": reasoning,
    }


def test_summarize_tokens_latency_and_cost():
    results = [
        row(True, [metrics(1000, 1_000_000, 100_000)], 1000),
        row(True, [metrics(3000, 1_000_000, 100_000, attempts=2, reasoning=50)], 3000),
    ]
    out = run_metrics.summarize(results, price_in=2.0, price_out=10.0)
    assert out["calls_measured"] == 2 and out["calls_with_tokens"] == 2
    assert out["prompt_tokens"] == 2_000_000 and out["completion_tokens"] == 200_000
    assert out["reasoning_tokens"] == 50 and out["retried_calls"] == 1
    assert out["latency_ms_per_call"]["median"] == 2000.0
    assert out["prompt_tokens_per_call"] == 1_000_000.0
    cost = out["cost"]  # per call: 1M*2 + 0.1M*10 = 3.0
    assert cost["total"] == pytest.approx(6.0)
    assert cost["per_call"] == pytest.approx(3.0) and cost["per_run"] == pytest.approx(3.0)


def test_summarize_cost_needs_both_prices_and_skips_unknown_tokens():
    results = [
        row(True, [metrics(10, 1_000_000, 0)], 10),
        row(True, [metrics(10, None, None)], 10),
    ]
    assert "cost" not in run_metrics.summarize(results, price_in=1.0)
    out = run_metrics.summarize(results, 1.0, 1.0)
    assert out["calls_measured"] == 2 and out["calls_with_tokens"] == 1
    assert out["cost"]["total"] == pytest.approx(1.0)
    assert out["cost"]["runs_priced"] == 1  # the run with unknown tokens is not averaged in


def test_summarize_multi_call_run_costs_the_whole_question():
    results = [row(True, [metrics(10, 1_000_000, 0), metrics(10, 1_000_000, 0)], 20)]
    out = run_metrics.summarize(results, 1.0, 0.0)
    assert out["cost"]["per_call"] == pytest.approx(1.0)
    assert out["cost"]["per_run"] == pytest.approx(2.0)


def test_summarize_old_result_files_report_nothing_measured():
    out = run_metrics.summarize([{"case_id": "C", "pass": True}], 1.0, 1.0)
    assert out["calls_measured"] == 0
    assert out["latency_ms_per_call"]["n"] == 0
    assert out["cost"]["per_call"] is None


def test_money_and_seconds_formatting():
    assert run_metrics.seconds(None) == "-" and run_metrics.seconds(1500) == "1.50s"
    assert run_metrics.money(None) == "-"
    assert run_metrics.money(0.00123, "USD") == "0.001230 USD"
    assert run_metrics.money(12.3456) == "12.35"


# ---------------------------------------------------------------- verify script


class FakeInner:
    def __init__(self, script):
        self.script, self.last_metrics, self.calls = list(script), None, 0

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.calls += 1
        outcome = self.script.pop(0)
        self.last_metrics = outcome.get("metrics")
        if "error" in outcome:
            raise StageFailure(outcome["error"])
        return outcome["raw"]


EXPAND_SCHEMA = {
    "properties": {"selections": {"items": {"properties": {"expand": {}}}}},
}


def test_recording_model_keeps_metrics_and_counts_429_waits(monkeypatch):
    monkeypatch.setattr(verify.time, "sleep", lambda s: None)
    good = metrics(1200, 300, 40)
    inner = FakeInner([{"error": "openai_http_429"}, {"raw": {"selections": []}, "metrics": good}])
    recorder = verify.RecordingModel(inner)
    recorder.complete("B", "s", {}, EXPAND_SCHEMA, 10, Trace())
    (record,) = recorder.calls
    assert record["metrics"] == good and record["rate_limit_waits"] == 1
    assert inner.calls == 2


def test_recording_model_keeps_billed_metrics_of_a_failed_call():
    billed = metrics(900, 300, 6144)
    inner = FakeInner([{"error": "output_truncated", "metrics": billed}])
    recorder = verify.RecordingModel(inner)
    with pytest.raises(StageFailure):
        recorder.complete("B", "s", {}, EXPAND_SCHEMA, 10, Trace())
    assert recorder.calls[0]["metrics"] == billed and recorder.calls[0]["error"]


def test_baseline_wrapper_forwards_metrics():
    inner = FakeInner([{"raw": {"selections": []}, "metrics": metrics(5, 1, 1)}])
    wrapped = verify.BaselineModel(inner)
    assert wrapped.last_metrics is None
    schema = {
        "properties": {
            "selections": {"items": {"properties": {"expand": {}}, "required": ["expand"]}}
        }
    }
    wrapped.complete("B", "s", {}, schema, 10, Trace())
    assert wrapped.last_metrics["latency_ms"] == 5


def test_run_totals_sums_and_handles_unmeasured_runs():
    calls = [
        {"metrics": metrics(1000, 300, 40), "rate_limit_waits": 0},
        {"metrics": metrics(500, 200, 10), "rate_limit_waits": 2},
    ]
    assert verify.run_totals(calls) == {
        "latency_ms": 1500,
        "prompt_tokens": 500,
        "completion_tokens": 50,
        "rate_limit_waits": 2,
    }
    empty = verify.run_totals([{"metrics": None, "rate_limit_waits": 0}])
    assert empty["latency_ms"] is None and empty["prompt_tokens"] is None


def test_timing_note():
    base = {"latency_ms": None, "prompt_tokens": None, "completion_tokens": None}
    assert verify.timing_note({**base, "rate_limit_waits": 0}) == ""
    note = verify.timing_note(
        {"latency_ms": 1820, "prompt_tokens": 6400, "completion_tokens": 110, "rate_limit_waits": 1}
    )
    assert "time=1.82s" in note and "tokens=6400+110" in note and "429 waits: 1" in note


def test_price_options_must_come_as_a_pair(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["verify", "--dry-run", "--price-in", "1.0"])
    with pytest.raises(SystemExit) as caught:
        verify.main()
    assert caught.value.code == 2
    assert "go together" in capsys.readouterr().err


def test_dry_run_still_works_without_metrics(monkeypatch, tmp_path, capsys):
    out = tmp_path / "r.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--dry-run",
            "CV-AB-01",
            "--price-in",
            "1",
            "--price-out",
            "2",
            "--output",
            str(out),
        ],
    )
    verify.main()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["results"][0]["call_metrics"] == [None]
    assert "latency_ms_per_call" not in data["summary"]  # nothing measured in a replay


# ---------------------------------------------------------------- compare script


def write_results(path, rows):
    path.write_text(json.dumps({"summary": {}, "results": rows}), encoding="utf-8")


def sample_rows(case_ids, latency, p, c):
    return [
        {
            "case_id": case_id,
            "pass": True,
            "latency_ms": latency,
            "call_metrics": [metrics(latency, p, c)],
        }
        for case_id in case_ids
    ]


def test_compare_table_prices_excludes_and_notes(tmp_path, capsys):
    hcx = tmp_path / "hcx.json"
    gpt = tmp_path / "gpt.json"
    old = tmp_path / "old.json"
    write_results(hcx, sample_rows(["CV-AB-01", "CV-NEW-07"], 2000, 1_000_000, 0))
    write_results(gpt, sample_rows(["CV-AB-01", "CV-AB-02"], 1000, 2_000_000, 0))
    write_results(old, [{"case_id": "CV-AB-01", "pass": True}])
    md = tmp_path / "t.md"
    table = compare_runs.main(
        [
            f"HCX={hcx}",
            f"GPT={gpt}",
            f"OLD={old}",
            "--price",
            "HCX=1.0,0",
            "--price",
            "GPT=0.5,0",
            "--currency",
            "USD",
            "--exclude",
            "CV-NEW-07",
            "--markdown",
            str(md),
        ]
    )
    by = {r["label"]: r for r in table}
    assert by["HCX"]["runs"] == 1  # CV-NEW-07 excluded
    assert by["HCX"]["lat_mean"] == "2.00s" and by["GPT"]["lat_mean"] == "1.00s"
    assert by["HCX"]["cost_call"] == "1.00 USD" and by["GPT"]["cost_call"] == "1.00 USD"
    assert by["GPT"]["cost_total"] == "2.00 USD"
    assert by["OLD"]["measured"] is False
    out = capsys.readouterr().out
    assert "OLD has no latency/token numbers" in out
    assert md.read_text(encoding="utf-8").startswith("| 모델 |")


def test_compare_pools_files_with_the_same_label(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_results(a, sample_rows(["CV-AB-01"], 1000, 100, 10))
    write_results(b, sample_rows(["CV-NEW-01", "CV-NEW-02"], 4000, 100, 10))
    table = compare_runs.main([f"GPT={a}", f"GPT={b}"])
    assert table[0]["runs"] == 3 and table[0]["calls"] == 3
    assert table[0]["cost_call"] == "-"  # no price given -> empty, not zero


def test_compare_rejects_bad_price_and_bad_spec(tmp_path):
    f = tmp_path / "x.json"
    write_results(f, [])
    with pytest.raises(SystemExit):
        compare_runs.main([f"A={f}", "--price", "A=1"])
    with pytest.raises(SystemExit):
        compare_runs.main([f"A={f}", "--price", "A=-1,2"])
    with pytest.raises(SystemExit):
        compare_runs.main([str(f)])
