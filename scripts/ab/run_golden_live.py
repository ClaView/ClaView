"""Run the 22 golden questions through A v1 (model on) -> B v6 (HCX) -> KOSIS fetch.

Modes (no golden answer is ever replayed into the pipeline in live/replay; only the query is
passed in, and `expected` is copied into the report for comparison):

  dry     Network blocked at the socket level. A's client fails every call (A falls back to
          rules), B replays the golden B reply, fetch returns synthetic rows. Only for counting
          how many calls a live run needs; the numbers are not results.
  live    Real HCX-007 for A (Q6G) and B, real KOSIS. Every response is written to
          --cache-dir (must be outside this repository). Cached requests are not sent again.
  replay  Cache only. A request missing from the cache stops the run; zero network calls.

Keys are read from CLOVASTUDIO_API_KEY and KOSIS_API_KEY (never printed or stored):

    uv run --locked python scripts/ab/run_golden_live.py --mode dry
    uv run --env-file .env --locked python scripts/ab/run_golden_live.py --mode live \
        --cache-dir ../_cache/claview-golden-live --as-of 2026-10-16
    uv run --locked python scripts/ab/run_golden_live.py --mode replay \
        --cache-dir ../_cache/claview-golden-live
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

from claview.b.ab_pipeline import load_a_catalog, run_ab  # noqa: E402
from claview.b.aflow import ARunConfig  # noqa: E402
from claview.b.aflow.model import HcxClient, ModelError, ModelReply  # noqa: E402
from claview.b.contracts import Config, StageFailure, Trace, today_iso  # noqa: E402
from claview.b.fetch import KosisClient  # noqa: E402
from claview.b.metadata import MetadataStore  # noqa: E402
from claview.b.models import HcxModel  # noqa: E402
from claview.b.search import SearchIndex  # noqa: E402
from claview.b.services import Services  # noqa: E402

GOLDEN = ROOT / "eval/cases/golden_set_v1.json"
# N5 judgment transport (lock2_transport_v1): Q6G output cap 1536, 2 attempts, >=3 s apart.
A_TOKEN_VERSION = "q6g_tokens_v2"
A_MIN_INTERVAL = 3.0
A_ATTEMPTS = 2
B_MIN_INTERVAL = 3.0
TRANSPORT_RETRIES = 1  # B HcxModel and KosisClient: 2 attempts each
# Physical attempts. Dry run: A 22, B 21, KOSIS ~72 (34 counted + golden selections where the
# dry B replay missed). A: 22 x 2 attempts. B: ~21 tables x 2 (expand retry) x 2 attempts.
DEFAULT_BUDGET = {"A": 44, "B": 100, "KOSIS": 200}


class StopRun(BaseException):
    """Budget reached, cache miss in replay, or a mixed-in replay path. BaseException so that
    A's `except Exception` fallback cannot hide it."""


class Counts:
    def __init__(self, budget: dict[str, int]):
        self.budget = budget
        self.logical: Counter = Counter()
        self.physical: Counter = Counter()
        self.cache_hits: Counter = Counter()
        self.by_node: Counter = Counter()

    def check(self, service: str, need: int = 1) -> None:
        """Stop before a call that could pass the cap (need = attempts the call may use)."""
        if self.physical[service] + need > self.budget[service]:
            raise StopRun(f"{service} budget {self.budget[service]} reached")

    def reserve(self, service: str) -> None:
        self.check(service)
        self.physical[service] += 1

    def snapshot(self) -> dict:
        return {
            "logical": dict(self.logical),
            "physical": dict(self.physical),
            "cache_hits": dict(self.cache_hits),
            "by_node": dict(self.by_node),
        }


def key_of(*parts: Any) -> str:
    text = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DiskCache:
    """One JSON file per request under cache_dir/<kind>/. Errors are cached too."""

    def __init__(self, root: Path | None, replay: bool):
        self.root, self.replay = root, replay

    def get(self, kind: str, key: str) -> dict | None:
        if self.root is None:
            return None
        path = self.root / kind / f"{key}.json"
        if not path.exists():
            if self.replay:
                raise StopRun(f"replay cache miss: {kind}/{key[:12]}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise StopRun(f"corrupt cache entry: {kind}/{key[:12]}") from None

    def put(self, kind: str, key: str, entry: dict) -> None:
        if self.root is None:
            return
        folder = self.root / kind
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{key}.tmp"
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(folder / f"{key}.json")


# ---------------------------------------------------------------- A client (Q6G)


class AClient:
    """Cache + transient retry + budget around A's HcxClient (or a dry stub)."""

    def __init__(self, inner, cache: DiskCache, counts: Counts, attempts: int):
        self.inner, self.cache, self.counts, self.attempts = inner, cache, counts, attempts
        self.name = getattr(inner, "name", "none")
        self.token_version = getattr(inner, "token_version", A_TOKEN_VERSION)

    def complete(self, node, system, payload, schema):
        self.counts.logical["A"] += 1
        self.counts.by_node[f"A:{node}"] += 1
        key = key_of("A", "HCX-007", self.token_version, node, system, payload, schema)
        hit = self.cache.get("a", key)
        if hit is not None:
            self.counts.cache_hits["A"] += 1
            if "error_class" in hit:
                raise ModelError(hit["error_class"], "cached error")
            return ModelReply(hit["text"], hit["tokens_in"], hit["tokens_out"])
        if self.inner is None:
            raise StopRun("A has no client in this mode")
        last = None
        for attempt in range(1, self.attempts + 1):
            self.counts.reserve("A")
            try:
                reply = self.inner.complete(node, system, payload, schema)
            except ModelError as err:
                last = err
                if err.error_class == "transient" and attempt < self.attempts:
                    time.sleep(min(30.0, max(5.0 * 2 ** (attempt - 1), err.retry_after or 0)))
                    continue
                break
            entry = {
                "node": node,
                "text": reply.text,
                "tokens_in": reply.tokens_in,
                "tokens_out": reply.tokens_out,
                "attempts": attempt,
            }
            self.cache.put("a", key, entry)
            return reply
        self.cache.put(
            "a",
            key,
            {
                "node": node,
                "error_class": last.error_class,
                "detail": str(last)[:200],
                "attempts": self.attempts,
            },
        )
        raise last


class DryAClient:
    """Dry run: counts the call and fails it like an outage, so A uses its rule path."""

    name = "dry-run-no-network"
    token_version = A_TOKEN_VERSION

    def complete(self, node, system, payload, schema):
        raise ModelError("transient", "dry run")


# ---------------------------------------------------------------- B model and KOSIS client


class BModel:
    def __init__(self, inner, cache: DiskCache, counts: Counts):
        self.inner, self.cache, self.counts = inner, cache, counts

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.counts.logical["B"] += 1
        self.counts.by_node["B:select"] += 1
        if context.get("retry_feedback"):
            self.counts.by_node["B:select_expand_retry"] += 1
        key = key_of("B", "HCX-007", task, system, context, schema, max_tokens)
        hit = self.cache.get("b", key)
        if hit is not None:
            self.counts.cache_hits["B"] += 1
            trace.add("model_cache_hit", task=task)
            if "error" in hit:
                raise StageFailure(hit["error"])
            return hit["result"]
        if self.inner is None:
            raise StopRun("B has no model in this mode")
        self.counts.check("B", TRANSPORT_RETRIES + 1)
        start = len(trace.events)
        try:
            result = self.inner.complete(task, system, context, schema, max_tokens, trace)
        except StageFailure as exc:
            self._count(trace, start)
            self.cache.put("b", key, {"task": task, "error": exc.code})
            raise
        self._count(trace, start)
        self.cache.put("b", key, {"task": task, "result": result})
        return result

    def _count(self, trace, start):
        for e in trace.events[start:]:
            if (e.get("event") == "transport_attempt" and e.get("service") == "HCX") or (
                e.get("event") == "model_request" and e.get("mode") == "synthetic_replay"
            ):  # the second form only occurs in dry runs
                self.counts.physical["B"] += 1


class DataClientWrap:
    def __init__(self, inner, cache: DiskCache, counts: Counts):
        self.inner, self.cache, self.counts = inner, cache, counts

    def fetch(self, params, trace):
        self.counts.logical["KOSIS"] += 1
        key = key_of("KOSIS", params)
        hit = self.cache.get("kosis", key)
        if hit is not None:
            self.counts.cache_hits["KOSIS"] += 1
            trace.add("data_cache_hit", params=params)
            if "error" in hit:
                raise StageFailure(hit["error"])
            return hit["rows"]
        if self.inner is None:
            raise StopRun("KOSIS has no client in this mode")
        self.counts.check("KOSIS", TRANSPORT_RETRIES + 1)
        start = len(trace.events)
        try:
            rows = self.inner.fetch(params, trace)
        except StageFailure as exc:
            self._count(trace, start)
            self.cache.put("kosis", key, {"params": params, "error": exc.code})
            raise
        self._count(trace, start)
        self.cache.put("kosis", key, {"params": params, "rows": rows})
        return rows

    def _count(self, trace, start):
        attempts = [e for e in trace.events[start:] if e.get("event") == "transport_attempt"]
        # The synthetic dry-run client has no transport events; count it as one attempt.
        self.counts.physical["KOSIS"] += len(attempts) or 1


# ---------------------------------------------------------------- components per mode


def block_network() -> None:
    def refuse(*args, **kwargs):
        raise OSError("network blocked (dry run)")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse  # type: ignore[assignment]


def build_components(mode: str, cache: DiskCache, counts: Counts, case: dict | None = None):
    """Return (a_client, b_model, data_client). Golden answers enter only in dry mode."""
    if mode == "live":
        b_key = os.environ.get("CLOVASTUDIO_API_KEY")
        k_key = os.environ.get("KOSIS_API_KEY")
        if not b_key or not k_key:
            raise SystemExit("CLOVASTUDIO_API_KEY and KOSIS_API_KEY must be set")
        a_inner = HcxClient(min_interval=A_MIN_INTERVAL, token_version=A_TOKEN_VERSION)
        b_inner = HcxModel(b_key, retries=TRANSPORT_RETRIES, min_interval=B_MIN_INTERVAL)
        k_inner = KosisClient(k_key, retries=TRANSPORT_RETRIES)
        return (
            AClient(a_inner, cache, counts, A_ATTEMPTS),
            BModel(b_inner, cache, counts),
            DataClientWrap(k_inner, cache, counts),
        )
    if mode == "replay":
        return (
            AClient(None, cache, counts, 0),
            BModel(None, cache, counts),
            DataClientWrap(None, cache, counts),
        )
    if mode == "dry":
        # Imported here so live/replay never load the golden replay or synthetic rows.
        sys.path.insert(0, str(ROOT / "tests"))
        from test_ab_pipeline import SyntheticDataClient

        from claview.b.evaluation import scripted_answers
        from claview.b.models import ScriptedModel

        answers = {k: v for k, v in scripted_answers(case).items() if k != "A"}
        return (
            AClient(DryAClient(), cache, counts, 1),
            BModel(ScriptedModel(answers), cache, counts),
            DataClientWrap(SyntheticDataClient(), cache, counts),
        )
    raise ValueError(mode)


def assert_live_path(mode: str, trace: Trace) -> None:
    if mode == "dry":
        return
    for e in trace.events:
        if e.get("mode") == "synthetic_replay":
            raise StopRun("synthetic replay event in a live/replay run")


# ---------------------------------------------------------------- one case


def summarize(case: dict, final: dict, trace: Trace, calls: dict, error: str | None) -> dict:
    a_v1 = final.get("a_v1") or {}
    a_issues = a_v1.get("issues") or []
    fallback = [i for i in a_issues if isinstance(i, dict) and i.get("code") == "q6g_fallback"]
    b = final.get("b_result") or {}
    data = final.get("data_result") or {}
    series = []
    for s in data.get("series", []):
        pts = s.get("points", [])
        series.append(
            {
                "series_id": s["series_id"],
                "table_id": s["selection"]["table_id"],
                "label": s["selection"].get("label"),
                "unit": s.get("unit"),
                "period": s["selection"]["period"],
                "expected_points": s["coverage"]["expected"],
                "returned_points": s["coverage"]["returned"],
                "missing_periods": len(s["coverage"]["missing_periods"]),
                "missing_values": sum(p["value_status"] != "observed" for p in pts),
            }
        )
    response = final.get("response") or {}
    status = response.get("status", "error") if error is None else "error"
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "a_tables": [t["table_id"] for t in (final.get("a_result") or {}).get("tables", [])],
        "a_expected": [t["table_id"] for t in case["expected"]["A"]["tables"]],
        "a_model": "fallback:" + str(fallback[0].get("detail")) if fallback else "ok",
        "b_selections": [
            {
                "table_id": s["table_id"],
                "item_id": s["item_id"],
                "label": s.get("label"),
                "dimensions": {a: v.get("code") for a, v in s.get("dimensions", {}).items()},
                "period": s["period"],
            }
            for s in b.get("selections", [])
        ],
        "b_expected": [
            {"table_id": s["table_id"], "item_id": s["item_id"], "period": s["period"]}
            for s in case["expected"]["B"]["selections"]
        ],
        "period_notices": b.get("period_notices"),
        "kosis": series,
        "status": status,
        "stopped_stage": response.get("stopped_stage"),
        "issues": response.get("issues"),
        "calls": calls,
        "error": error,
        "trace_ref": trace.run_id,
    }


def run_case(case, mode, cache, counts, store, catalog, as_of, out_dir):
    before = counts.snapshot()
    trace = Trace()
    a_client, b_model, data_client = build_components(mode, cache, counts, case)
    services = Services(store, SearchIndex(store), b_model, Config(as_of=as_of), trace)
    error, final = None, {}
    try:
        final = run_ab(
            case["query"],  # the golden `expected` is never passed in
            services,
            catalog,
            a_config=ARunConfig(model="on", as_of=as_of),
            a_client=a_client,
            data_client=data_client,
            through="fetch",
        )
        assert_live_path(mode, trace)
    except StopRun:
        raise
    except Exception as exc:  # keep going with the other cases
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    after = counts.snapshot()
    calls = {
        kind: {
            k: v - before[kind].get(k, 0)
            for k, v in after[kind].items()
            if v - before[kind].get(k, 0)
        }
        for kind in after
    }
    row = summarize(case, final, trace, calls, error)
    (out_dir / "cases").mkdir(parents=True, exist_ok=True)
    (out_dir / "cases" / f"{case['case_id']}.json").write_text(
        json.dumps(
            {"summary": row, "final": final, "trace": trace.events},
            ensure_ascii=False,
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    return row


# ---------------------------------------------------------------- main


def resolve_cache(mode: str, cache_dir: Path | None) -> Path | None:
    if mode == "dry":
        return None
    if cache_dir is None:
        raise SystemExit("--cache-dir is required for live and replay")
    path = cache_dir.resolve()
    if path == ROOT or ROOT in path.parents:
        raise SystemExit("--cache-dir must be outside the repository")
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mode", choices=["dry", "live", "replay"], default="dry")
    p.add_argument("--cases", nargs="*", help="case ids (default: all 22)")
    p.add_argument("--cache-dir", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--as-of", help="YYYY-MM-DD; replay defaults to the cached run's date")
    for svc, cap in DEFAULT_BUDGET.items():
        p.add_argument(
            f"--max-{svc.lower()}",
            type=int,
            default=cap,
            help=f"physical attempt cap for {svc} (default {cap})",
        )
    args = p.parse_args(argv)

    cache_root = resolve_cache(args.mode, args.cache_dir)
    manifest_path = cache_root / "manifest.json" if cache_root else None
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path and manifest_path.exists()
        else {}
    )
    as_of = args.as_of or manifest.get("as_of") or today_iso()
    if manifest.get("as_of") and manifest["as_of"] != as_of:
        raise SystemExit(f"cache was built with as_of={manifest['as_of']}; pass the same date")
    if args.mode == "replay" and not manifest:
        raise SystemExit("replay needs a cache with manifest.json from a live run")
    if args.mode == "dry":
        block_network()
    if args.mode == "live" and manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "as_of": as_of,
                    "a_token_version": A_TOKEN_VERSION,
                    "b_model": "HCX-007",
                    "created": manifest.get("created") or datetime.now(UTC).isoformat(),
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    cases = json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in cases if c["case_id"] in wanted]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or ROOT / "runs" / "golden-live" / f"{args.mode}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    counts = Counts({"A": args.max_a, "B": args.max_b, "KOSIS": args.max_kosis})
    cache = DiskCache(cache_root, replay=args.mode == "replay")
    store = MetadataStore(
        ROOT / "data/catalog/search_catalog.json", ROOT / "data/raw/boK_meta_raw.jsonl"
    )
    catalog = load_a_catalog(ROOT)
    rows, stopped = [], None
    for case in cases:
        try:
            row = run_case(case, args.mode, cache, counts, store, catalog, as_of, out_dir)
        except StopRun as exc:
            stopped = f"{case['case_id']}: {exc}"
            break
        rows.append(row)
        print(
            f"{row['case_id']} {row['status']:<8} A={row['a_tables']} "
            f"B={len(row['b_selections'])} K={len(row['kosis'])} calls={row['calls']['logical']}"
        )
    summary = {
        "mode": args.mode,
        "as_of": as_of,
        "cases_run": len(rows),
        "stopped": stopped,
        "status": dict(Counter(r["status"] for r in rows)),
        "calls": counts.snapshot(),
        "budget": counts.budget,
        "cache_dir": str(cache_root) if cache_root else None,
    }
    (out_dir / "summary.json").write_text(
        json.dumps({**summary, "cases": rows}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"wrote {out_dir}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
