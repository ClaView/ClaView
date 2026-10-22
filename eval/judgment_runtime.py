"""Evaluation-only durable attempt budget, pacing, versioned cache and retries.

The gate counts transport attempts (including failures), before dispatch. A crash
after reservation conservatively consumes that attempt. Reuse one ledger across
all invocations of an experiment. Mock ledgers cannot be used for live runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time

from claview.b.aflow.model import ModelError, ModelReply

POLICY = {"version": "lock2_transport_v1", "token_version": "q6g_tokens_v2",
          "q6g_max_tokens": 1536, "max_attempts": 2, "min_interval_seconds": 3.0,
          "backoff_base_seconds": 5.0, "backoff_cap_seconds": 30.0,
          "retry": "transient only (429/5xx/timeout/connection); no schema/auth retry"}


class BudgetStop(BaseException):
    """Deliberately escapes Q6g's broad Exception-to-fallback conversion."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf8")).hexdigest()


def append_record(path, value):
    with Path(path).open("a", encoding="utf8", newline="\n") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class VirtualClock:
    """Mock only: advance a virtual wall clock without waiting three real seconds."""
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class AttemptGate:
    def __init__(self, ledger, cap, *, mode, clock=time):
        if type(cap) is not int or cap < 0 or mode not in ("live", "mock"):
            raise ValueError("Nonnegative integer cap and live/mock mode required")
        if mode == "live" and clock is not time:
            raise ValueError("Live gate requires the real clock")
        self.path, self.cap, self.mode, self.clock = Path(ledger), cap, mode, clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.Lock()
        self._lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        try:
            self._lock.seek(0, os.SEEK_END)
            if self._lock.tell() == 0:
                self._lock.write(b"0")
                self._lock.flush()
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            rows = [json.loads(line) for line in self.path.read_text(encoding="utf8").splitlines()] if self.path.exists() else []
            if any(r["mode"] != mode for r in rows):
                raise ValueError("Cannot mix mock/live budget ledgers")
            attempts = [r for r in rows if r["event"] == "reserved"]
            if [r["ordinal"] for r in attempts] != list(range(1, len(attempts) + 1)):
                raise ValueError("Invalid attempt ledger")
            self.used = self.initial_used = len(attempts)
            self.last = attempts[-1]["started_at"] if attempts else None
        except BaseException:
            self._lock.close()
            raise

    def close(self):
        self._lock.close()  # OS releases the process lock, including on crash.

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def dispatch(self, operation, context, *, delay=0.0):
        with self._mutex:
            if self.used >= self.cap:
                raise BudgetStop("Cumulative transport attempt cap reached")
            wait = max(0.0, delay, (self.last + 3.0 - self.clock.time()) if self.last is not None else 0.0)
            if wait:
                self.clock.sleep(wait)
            self.used += 1
            self.last = self.clock.time()
            row = {"mode": self.mode, "ordinal": self.used, "started_at": self.last,
                   "wait_seconds": wait, **context}
            append_record(self.path, {**row, "event": "reserved"})
            try:
                result = operation()
            except BaseException as error:
                append_record(self.path, {**row, "event": "finished", "ok": False,
                    "error_class": getattr(error, "error_class", type(error).__name__),
                    "http_status": getattr(error, "http_status", None)})
                raise
            append_record(self.path, {**row, "event": "finished", "ok": True})
            return result


class JudgmentClient:
    """Retry/cache around exactly one transport operation per inner.complete.

    Repeat index separates independent runs. J5-only flags are deliberately not
    part of the key: an auxiliary run reuses the first run's identical request.
    Mock and live caches have distinct namespaces. Terminal failures are cached
    too so auxiliary replays preserve the first run's fallback.
    """
    def __init__(self, inner, gate, cache, *, repeat=1, version=None):
        self.inner, self.gate, self.cache = inner, gate, Path(cache)
        self.repeat, self.version = repeat, version or POLICY["version"]
        self.cache.mkdir(parents=True, exist_ok=True)
        self.name = inner.name
        self.rows = []
        self.context = {}

    def complete(self, node, system, payload, schema):
        key = digest([self.version, POLICY, self.gate.mode, self.inner.name, self.context.get("case_id"), self.repeat, node, system, payload, schema])
        path = self.cache / (key + ".json")
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf8"))
                if record["key"] != key or type(record["ok"]) is not bool or not isinstance(record["attempts"], list):
                    raise ValueError("Cache key/schema mismatch")
                required = ("text", "tokens_in", "tokens_out") if record["ok"] else ("error_class", "http_status", "retry_after")
                if any(k not in record for k in required):
                    raise ValueError("Incomplete cache record")
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise BudgetStop("Invalid evaluation cache; repair before resuming") from error
            self.rows.append({**self.context, "node": node, "cache_key": key, "cache_hit": True,
                              "attempt": 0, "ok": record["ok"], "transport_version": self.version,
                              "source_attempts": record["attempts"]})
            return self._outcome(record)
        delay = 0.0
        attempts = []
        for attempt in range(1, POLICY["max_attempts"] + 1):
            row = {**self.context, "node": node, "cache_key": key, "cache_hit": False,
                   "attempt": attempt, "transport_version": self.version,
                   "token_version": POLICY["token_version"], "backoff_seconds": delay}
            try:
                reply = self.gate.dispatch(lambda: self.inner.complete(node, system, payload, schema), row, delay=delay)
            except ModelError as error:
                row.update(ok=False, error_class=error.error_class, http_status=error.http_status,
                           retry_after=error.retry_after)
                record = {"ok": False, "error_class": error.error_class,
                          "http_status": error.http_status, "retry_after": error.retry_after}
                delay = min(POLICY["backoff_cap_seconds"], max(POLICY["backoff_base_seconds"] * 2 ** (attempt - 1), error.retry_after))
                if hasattr(self.inner, "_cooldown"):
                    self.inner._cooldown = 0.0  # Evaluation gate owns backoff; do not wait twice.
            except Exception as error:
                row.update(ok=False, error_class=getattr(error, "error_class", type(error).__name__))
                self.rows.append(row)
                raise  # ReplayMiss still reaches the runner's explicit stop path.
            else:
                row.update(ok=True, error_class=None, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out)
                record = {"ok": True, "text": reply.text, "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out}
            attempts.append(row)
            self.rows.append(row)
            if record["ok"] or record["error_class"] != "transient":
                break
        record.update(key=key, attempts=attempts)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf8") as stream:
            stream.write(canonical(record))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return self._outcome(record)

    @staticmethod
    def _outcome(record):
        if record["ok"]:
            return ModelReply(record["text"], record["tokens_in"], record["tokens_out"])
        raise ModelError(record["error_class"], "Recorded transport failure", http_status=record["http_status"], retry_after=record["retry_after"])
