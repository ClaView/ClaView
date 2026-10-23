"""Latency / token / cost arithmetic shared by verify_expand_hcx.py and compare_runs.py.

Pure functions, no network. Every figure comes from the per-call measurements the
verify script stores in each result row (`call_metrics`): milliseconds on the wire for
the last HTTP attempt, and the token counts the endpoint reported. Prices are never
built in -- the caller passes `price_in` / `price_out` (per 1,000,000 tokens, any
currency) so the numbers are whatever the user's own price sheet says.
"""

from __future__ import annotations

import math
import statistics


def percentile(values, q):
    """Nearest-rank percentile (q in 0..100); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def stats(values):
    """n / mean / median / p95 / min / max of a list of numbers (None fields when empty)."""
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": round(statistics.median(values), 1),
        "p95": percentile(values, 95),
        "min": min(values),
        "max": max(values),
    }


def call_cost(metrics, price_in, price_out):
    """Cost of one call, or None when its token counts are unknown."""
    if metrics is None or metrics.get("prompt_tokens") is None:
        return None
    if metrics.get("completion_tokens") is None:
        return None
    return (
        metrics["prompt_tokens"] * price_in + metrics["completion_tokens"] * price_out
    ) / 1_000_000


def summarize(results, price_in=None, price_out=None):
    """Latency, tokens and (when both prices are given) cost over a list of result rows.

    Rows without `call_metrics` (older result files, dry runs) add nothing; the counts
    in the output say how many calls were actually measured so a partial file is
    never mistaken for a full one.
    """
    calls = [m for r in results for m in (r.get("call_metrics") or []) if m]
    latencies = [m["latency_ms"] for m in calls if m.get("latency_ms") is not None]
    with_tokens = [
        m
        for m in calls
        if m.get("prompt_tokens") is not None and m.get("completion_tokens") is not None
    ]
    run_latency = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    out = {
        "runs": len(results),
        "calls_measured": len(calls),
        "latency_ms_per_call": stats(latencies),
        "latency_ms_per_run": stats(run_latency),
        "calls_with_tokens": len(with_tokens),
        "prompt_tokens": sum(m["prompt_tokens"] for m in with_tokens),
        "completion_tokens": sum(m["completion_tokens"] for m in with_tokens),
        "reasoning_tokens": sum(m.get("reasoning_tokens") or 0 for m in with_tokens),
        "retried_calls": sum(1 for m in calls if (m.get("attempts") or 1) > 1),
    }
    n = len(with_tokens)
    out["prompt_tokens_per_call"] = round(out["prompt_tokens"] / n, 1) if n else None
    out["completion_tokens_per_call"] = round(out["completion_tokens"] / n, 1) if n else None
    if price_in is not None and price_out is not None:
        costs = [call_cost(m, price_in, price_out) for m in with_tokens]
        total = sum(costs)
        priced_runs = [
            r
            for r in results
            if r.get("call_metrics")
            and all(
                m and m.get("prompt_tokens") is not None and m.get("completion_tokens") is not None
                for m in r["call_metrics"]
            )
        ]
        out["cost"] = {
            "price_in_per_1m": price_in,
            "price_out_per_1m": price_out,
            "total": total,
            "per_call": total / n if n else None,
            # a "run" is one question through B (one or more calls)
            "per_run": (
                sum(
                    call_cost(m, price_in, price_out)
                    for r in priced_runs
                    for m in r["call_metrics"]
                )
                / len(priced_runs)
                if priced_runs
                else None
            ),
            "runs_priced": len(priced_runs),
        }
    return out


def seconds(ms):
    return "-" if ms is None else f"{ms / 1000:.2f}s"


def money(value, currency=""):
    if value is None:
        return "-"
    digits = 6 if abs(value) < 0.01 else 4 if abs(value) < 1 else 2
    return f"{value:,.{digits}f}{(' ' + currency) if currency else ''}"
