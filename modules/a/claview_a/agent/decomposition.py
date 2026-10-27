"""Request-scoped decomposition execution. No provider or network client is created here.

A shared CallLedger protects only executions sharing that object, not other processes. The graph also carries a
serialisable local ledger so direct graph invocations retain the result across node re-entry. Claims are atomic;
conflicts and in-progress visits never overwrite the owner's entry. Only completed analyses are replayed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import replace

from ..interpret.condition_fields import DEFAULT_AS_OF
from ..interpret.decompose_run import _rules_result, parse_config
from ..interpret.pipeline import interpret_question

_LOCK = threading.RLock()


class CallLedger:
    def __init__(self, entries: dict | None = None):
        self.entries = entries if entries is not None else {}

    def claim(self, key: str, fingerprint: str) -> tuple[str, dict]:
        with _LOCK:
            entry = self.entries.get(key)
            if entry is not None:
                if entry["fingerprint"] != fingerprint:
                    return "ledger_conflict", copy.deepcopy(entry)
                if entry["status"] == "started":
                    return "ledger_in_progress", copy.deepcopy(entry)
                return "reused", copy.deepcopy(entry)
            entry = {"fingerprint": fingerprint, "status": "started", "provider_calls": 0,
                     "fake_calls": 0, "model_calls": 0, "real_calls": 0, "replay_calls": 0,
                     "latency_ms": None, "usage": None,
                     "analysis": None, "error_class": None}
            self.entries[key] = entry
            return "owner", copy.deepcopy(entry)

    def attempted(self, key: str, mode: str = "fake") -> None:
        with _LOCK:
            if self.entries[key]["provider_calls"]:
                raise RuntimeError("decomposition provider already attempted")
            self.entries[key]["provider_calls"] += 1
            self.entries[key]["fake_calls"] += int(mode == "fake")

    def measured(self, key: str, measurement: dict) -> None:
        with _LOCK:
            entry = self.entries[key]
            for name in ("fake_calls", "real_calls", "replay_calls", "usage"):
                entry[name] = measurement.get(name, 0 if name != "usage" else None)
            entry["model_calls"] = entry["real_calls"]
            entry["source_measurement"] = copy.deepcopy(measurement)

    def finish(self, key: str, analysis: dict | None, elapsed: float, error: str | None = None) -> dict:
        with _LOCK:
            entry = self.entries[key]
            entry.update(status="failed" if error else "completed", analysis=copy.deepcopy(analysis),
                         latency_ms=elapsed, error_class=error)
            return copy.deepcopy(entry)

    def snapshot(self) -> dict:
        with _LOCK:
            return copy.deepcopy(self.entries)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf8")).hexdigest()


def execute(state: dict, config: dict) -> tuple[dict, dict | None, dict]:
    """analysis, per-visit measurement, local ledger. Settings errors are raised before claiming a call."""
    cfg = parse_config(config.get("decomposition"))
    layer = config.get("layer")
    entries = state.get("decomposition_ledger") or {}
    if cfg is None:
        return interpret_question(state["question"], layer), None, entries
    request_as_of = state.get("as_of") or cfg.as_of
    cfg = replace(cfg, as_of=request_as_of or DEFAULT_AS_OF)
    if cfg.mode == "rules":
        return interpret_question(state["question"], layer, decomposition=cfg), None, entries
    request_id, version = state["request_id"], state["question_version"]
    key = json.dumps([request_id, version], ensure_ascii=False)
    shared = config.get("decomposition_ledger")
    if shared is not None and not isinstance(shared, CallLedger):
        raise TypeError("decomposition_ledger must be a CallLedger")
    ledger = shared if shared is not None else CallLedger(entries)
    # In-memory identity is intentionally conservative: a different provider/catalog must not reuse old output.
    source_identity = getattr(cfg.proposal_source, "fingerprint", None)
    fingerprint = _digest([state["question"], cfg.mode, cfg.execution_mode, request_as_of,
                           source_identity() if callable(source_identity) else id(cfg.proposal_source), id(layer)])
    action, entry = ledger.claim(key, fingerprint)
    meta = {"request_id": request_id, "question_version": version, "requested_mode": cfg.mode,
            "execution_mode": cfg.execution_mode, "visit": action, "provider_calls": 0, "fake_calls": 0,
            "model_calls": 0, "real_calls": 0, "replay_calls": 0, "latency_ms": None, "usage": None}
    if action == "reused" and entry["analysis"] is not None:
        analysis = entry["analysis"]
    elif action != "owner":
        rules = interpret_question(state["question"], layer)
        reason = action if action != "reused" else "transport"
        analysis = _rules_result(cfg, rules, cfg.as_of, "rules_fallback", fallback_reason=reason,
                                 error_class=entry["error_class"] if reason == "transport" else None)
    else:
        start = time.perf_counter()

        def once(payload, schema):
            ledger.attempted(key, cfg.execution_mode)
            try:
                return cfg.proposal_source(payload, schema)
            finally:
                if cfg.execution_mode != "fake":
                    ledger.measured(key, cfg.proposal_source.measurement)

        if cfg.execution_mode != "fake":
            once.execution_mode = cfg.execution_mode
            once.validate = cfg.proposal_source.validate

        try:
            analysis = interpret_question(state["question"], layer, decomposition=replace(cfg, proposal_source=once))
        except Exception as exc:
            # Keep a terminal claim even for programming errors; propagate rather than silently hiding a bug.
            ledger.finish(key, None, (time.perf_counter() - start) * 1000, type(exc).__name__)
            raise
        entry = ledger.finish(key, analysis, (time.perf_counter() - start) * 1000)
        meta.update({k: entry[k] for k in ("provider_calls", "fake_calls", "model_calls", "real_calls",
                                          "replay_calls", "latency_ms", "usage")})
        if "source_measurement" in entry:
            meta["source_measurement"] = entry["source_measurement"]
    record = analysis["decomposition"]
    meta.update(applied_source=record["applied_source"], fallback_reason=record["fallback_reason"],
                status=entry["status"], ledger_provider_calls=entry["provider_calls"])
    return analysis, meta, ledger.snapshot()
