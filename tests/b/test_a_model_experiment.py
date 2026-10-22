"""Whole-run bounds, crash containment and honest unmeasured reporting; no live calls."""

import asyncio
import copy
import json

import httpx
import pytest

from claview.a_model_contracts import make_packet
from claview.a_model_experiment import (
    VERSION,
    ExperimentLedger,
    inside,
    seal,
    summarize,
    validate_grant,
    verify_plan,
)
from claview.a_model_providers import AProvider, ModelFault
from claview.a_model_runtime import Limits, Price, RoleRuntime
from claview.contracts import fingerprint as file_hash

RUNNER = None
PRICE = Price("KRW", "1250", "5000", "2026-10-05", "https://example.com/test-price")
LIMITS = {
    "max_calls": 2,
    "max_input_tokens": 254976,
    "max_output_tokens": 1024,
    "max_seconds": 60,
    "max_cost_by_currency": {"KRW": "1000", "USD": "1"},
}


@pytest.fixture(scope="module", autouse=True)
def event_loop_before_network_guard():
    global RUNNER
    with asyncio.Runner() as runner:
        runner.get_loop()
        RUNNER = runner
        yield
    RUNNER = None


def ledger(tmp_path, **kwargs):
    return ExperimentLedger(
        tmp_path / "ledger.sqlite",
        plan_id="test-plan",
        mode="mock",
        limits=kwargs.pop("limits", copy.deepcopy(LIMITS)),
        **kwargs,
    )


def record(**kwargs):
    return {
        "provider": {"mode": "mock"},
        "price": {"currency": "KRW"},
        "reserved_input_tokens": 127488,
        "reserved_output_tokens": 512,
        "reserved_cost": "161.92",
        **kwargs,
    }


def done():
    return {"advice": {"status": "advice"}, "runtime": {"trace": []}}


def test_completed_jobs_resume_without_duplicate_requests(tmp_path):
    first = ledger(tmp_path)
    assert first.claim("one") is None
    ticket, _ = first.reserve(record())
    first.finish(ticket, record())
    first.complete(done())
    resumed = ledger(tmp_path)
    assert resumed.claim("one") == done()
    resumed.claim("two")
    ticket, _ = resumed.reserve(record())
    resumed.finish(ticket, record())
    with pytest.raises(ModelFault, match="experiment_call_token_or_cost_budget"):
        resumed.reserve(record())


@pytest.mark.parametrize("completed_attempt", [False, True])
def test_interrupted_job_is_not_repeated_even_if_attempt_finished(tmp_path, completed_attempt):
    first = ledger(tmp_path)
    first.claim("one")
    ticket, _ = first.reserve(record())
    if completed_attempt:
        first.finish(ticket, record())
    resumed = ledger(tmp_path)
    with pytest.raises(ModelFault, match="indeterminate"):
        resumed.claim("one")
    with pytest.raises(ModelFault, match="indeterminate"):
        resumed.claim("two")


def test_concurrent_ledger_cannot_claim_or_dispatch_active_job(tmp_path):
    first, second = ledger(tmp_path), ledger(tmp_path)
    first.claim("one")
    with pytest.raises(ModelFault):
        second.claim("two")
    with pytest.raises(ModelFault):
        second.reserve(record())


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_calls", 1),
        ("max_input_tokens", 127488),
        ("max_output_tokens", 512),
        ("max_cost_by_currency", {"KRW": "194.304", "USD": "1"}),
    ],
)
def test_each_budget_is_persisted_and_reserved_without_refunds(tmp_path, key, value):
    limits = {**copy.deepcopy(LIMITS), key: value}
    first = ledger(tmp_path, limits=limits)
    first.claim("one")
    ticket, _ = first.reserve(record())
    first.finish(ticket, {**record(), "usage": None, "status": "timeout"})
    resumed = ledger(tmp_path, limits=limits)
    resumed.job_id = "one"
    with pytest.raises(ModelFault, match="budget"):
        resumed.reserve(record())


def test_currencies_are_independent_and_buffer_is_charged(tmp_path):
    run = ledger(
        tmp_path, limits={**LIMITS, "max_cost_by_currency": {"KRW": "194.303", "USD": "9999"}}
    )
    run.claim("one")
    with pytest.raises(ModelFault, match="budget"):
        run.reserve(record())


def test_identity_mode_and_limits_cannot_be_reset_on_resume(tmp_path):
    ledger(tmp_path)
    for overrides in (
        {"mode": "live"},
        {"plan_id": "other"},
        {"limits": {**LIMITS, "max_calls": 3}},
    ):
        args = {"plan_id": "test-plan", "mode": "mock", "limits": LIMITS, **overrides}
        with pytest.raises(ValueError, match="mismatch"):
            ExperimentLedger(tmp_path / "ledger.sqlite", **args)


@pytest.mark.parametrize("now", [161, 99])
def test_deadline_and_backward_clock_fail_closed_across_restart(tmp_path, now):
    first = ledger(tmp_path, clock=lambda: 100)
    first.claim("one")
    resumed = ledger(tmp_path, clock=lambda: now)
    resumed.job_id = "one"
    with pytest.raises(ModelFault, match="time_budget"):
        resumed.reserve(record())


def test_failure_stops_entire_run(tmp_path):
    run = ledger(tmp_path)
    run.claim("one")
    run.complete({"advice": {"status": "fallback"}})
    with pytest.raises(ModelFault, match="stopped"):
        ledger(tmp_path).claim("two")


def test_plan_hash_file_hash_and_repository_boundary(tmp_path):
    data = tmp_path / "input.json"
    data.write_text("original", encoding="utf-8")
    plan = seal(
        {"schema_version": VERSION, "frozen_files": {"input.json": file_hash(data)}, "jobs": []}
    )
    verify_plan(tmp_path, plan)
    with pytest.raises(ValueError, match="identity"):
        verify_plan(tmp_path, {**plan, "extra": True})
    data.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify_plan(tmp_path, plan)
    with pytest.raises(ValueError, match="escapes"):
        inside(tmp_path, "../secret")


@pytest.mark.parametrize(
    "grant",
    [
        {},
        {"allow_external_calls": False},
        {"allow_external_calls": True},
        {"allow_external_calls": True, "max_cost_by_currency": {"KRW": "0", "USD": "1"}},
        {"allow_external_calls": True, "max_cost_by_currency": {"KRW": "NaN", "USD": "1"}},
        {"allow_external_calls": True, "max_cost_by_currency": {"KRW": "1"}},
    ],
)
def test_missing_or_invalid_approval_cannot_enable_live(grant):
    with pytest.raises(ValueError):
        validate_grant({"plan_id": "test", "limits": LIMITS}, {"plan_id": "test", **grant})


@pytest.mark.parametrize(
    "mode,status", [("mock", "advice"), ("offline_preparation", "advice"), ("live", "fallback")]
)
def test_mock_and_fallback_never_count_as_semantic_success(mode, status):
    plan = {
        "plan_id": "test",
        "jobs": [
            {
                "job_id": "j",
                "case_id": "case-1",
                "role": "DecisionClassifier",
                "profile": "title",
                "model": "test",
                "repetition": 0,
            }
        ],
    }
    rows = [{"job_id": "j", "result": {"advice": {"status": status}, "runtime": {"trace": []}}}]
    result = summarize(plan, rows, mode=mode)
    group = next(iter(result["groups"].values()))
    assert group["semantic_accuracy"] is None
    assert group["validated_advice"] == 0
    assert group["attempt_latency_p50_seconds"] is None
    assert result["model_adoption"] == "hold"


def test_runtime_reserves_before_dispatch_and_counts_repair_globally(tmp_path):
    run = ledger(tmp_path, limits={**LIMITS, "max_calls": 1})
    run.claim("one")
    calls = []

    def handler(request):
        # The durable reservation must exist before this callback represents dispatch.
        with run.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM attempts WHERE done=0").fetchone()[0] == 1
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {
                    "message": {"content": json.dumps({"invalid": True})},
                    "finishReason": "stop",
                    "usage": {"promptTokens": 100, "completionTokens": 10},
                },
            },
        )

    async def scenario():
        provider = AProvider("HCX-007", transport=httpx.MockTransport(handler))
        runtime = RoleRuntime(Limits(max_cost="1000"), enabled=True, attempt_ledger=run)
        packet = make_packet(
            "query", {k: "fixture" for k in ("dictionary", "metadata", "catalog", "policy")}
        )
        try:
            result = await runtime.invoke("QueryInterpreter", packet, provider, price=PRICE)
            assert result["reason"] == "experiment_call_token_or_cost_budget"
            assert runtime.summary()["calls"] == 1
            assert runtime.summary()["external_calls"] == 0
            assert runtime.trace[0]["parsed_response"] == {"invalid": True}
        finally:
            await provider.close()

    RUNNER.run(scenario())
    assert len(calls) == 1


def test_pending_attempt_is_visible_even_without_completed_response(tmp_path):
    run = ledger(tmp_path)
    run.claim("crashed")
    run.reserve(record())
    audit = ledger(tmp_path).audit()
    assert audit["pending_attempts"] == audit["reserved_attempts"] == 1
    assert audit["buffered_reserved_cost_by_currency"]["KRW"] == "194.304"
    assert audit["reserved_input_tokens"] == 127488


def test_cache_or_uninvoked_advice_is_not_counted_as_live_model_success():
    job = {
        "job_id": "j",
        "case_id": "c",
        "role": "QueryInterpreter",
        "profile": "title",
        "model": "test",
        "repetition": 0,
    }
    result = summarize(
        {"plan_id": "p", "jobs": [job]}, [{"job_id": "j", "result": done()}], mode="live"
    )
    group = next(iter(result["groups"].values()))
    assert group["called_jobs"] == group["validated_advice"] == 0
    assert group["semantic_accuracy"] is None


def test_costs_separate_repairs_repeats_and_missing_usage():
    base = {
        "job_id": "j",
        "case_id": "c",
        "role": "QueryInterpreter",
        "profile": "title",
        "model": "test",
        "repetition": 0,
    }
    repeat = {**base, "job_id": "jr", "repetition": 1}
    attempt = {
        "event": "attempt",
        "provider": {"mode": "live"},
        "repair": False,
        "elapsed_seconds": 1,
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "estimated_cost": "1",
    }
    first = {
        "advice": {"status": "advice"},
        "runtime": {
            "elapsed_seconds": 2,
            "trace": [attempt, {**attempt, "repair": True, "estimated_cost": None, "usage": None}],
        },
    }
    second = {"advice": {"status": "advice"}, "runtime": {"elapsed_seconds": 1, "trace": [attempt]}}
    result = summarize(
        {"plan_id": "p", "jobs": [base, repeat]},
        [{"job_id": "j", "result": first}, {"job_id": "jr", "result": second}],
        mode="live",
    )
    group = next(iter(result["groups"].values()))
    assert group["distinct_observed_cases"] == 1
    assert group["attempts"] == 3
    assert group["estimated_cost"] is None
    assert group["cost_by_attempt_kind"]["initial_repair"]["unknown_cost_attempts"] == 1
    assert group["cost_by_attempt_kind"]["repeat"]["known_estimated_cost"] == "1"
    assert group["known_input_tokens"] == 20
