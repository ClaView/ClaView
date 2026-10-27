"""Requests of the interpretation model run (plan 06, 5): disk cache, retries, request ledger and stop rules.

``RunClient`` is the model client ``modules/a/scripts/run_interp_model.py`` hands to ``interpret_question`` (it lives in the
package so that tests can drive it with a fake transport; the script only wires it):
- disk cache (jsonl): key = sha256 of [node, prompt sha256, payload, schema]. A cached reply is returned without a
  request, so a run that stopped can be run again and resumes; ``replay`` mode reads the cache only (a missing reply
  is recorded as missing, no request);
- HTTP 429: recorded as deferred (not a failure, not billed), nothing cached; the HCX client waits the retry-after
  before its next request and the run moves on, so a later run fills the question;
- other transient errors: retried at most ``TRANSIENT_RETRIES`` times; schema errors are cached (the same request
  would fail the same way), auth and other errors are not retried and not cached;
- stop rules: billed requests reach ``max_billed``; the mean tokens_in of the first ``TOKEN_CHECK_REQUESTS``
  successful requests exceeds ``TOKEN_FACTOR`` x ``est_in``; ``HARD_ERROR_STREAK`` auth/other errors in a row.
  After a stop no request is sent (``stop_reason`` says why);
- ledger (jsonl), one row per request sent or missing: case ID, number of points, request sha256, status, error class,
  HTTP status, tokens, latency, response text (at most ``LEDGER_TEXT_MAX`` characters). No key, no header.
- runs over one output folder add up (plan 06 review, 10/19): on start the ledger already there gives back the billed
  requests of earlier runs (``billed_before``), the tokens_in of the first successful requests and the run of hard
  errors at its end, so the billed cap counts across runs and a stop rule that fired stays fired; a stopped run sends
  nothing unless ``resume_after_stop`` is given (the billed cap is never lifted). ``max_billed`` is at most
  ``MAX_BILLED_CAP``.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..reused.model import ModelError, ModelReply
from .lexicon import HANGUL_CHAR

LEDGER_TEXT_MAX = 2000
TRANSIENT_RETRIES = 2
TOKEN_CHECK_REQUESTS = 10
TOKEN_FACTOR = 1.5
HARD_ERROR_STREAK = 3
MAX_BILLED_CAP = 300  # plan 06, 6: at most 300 billed requests, retries included, over every run of one folder
MODES = ("live", "replay")
_HANGUL = HANGUL_CHAR


def request_key(node: str, prompt_sha: str, payload: dict, schema: dict) -> str:
    body = json.dumps([node, prompt_sha, payload, schema], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """A rough token count from characters: Hangul about 1.5 characters a token, other characters about 3.5."""
    hangul = len(_HANGUL.findall(text))
    return round(hangul / 1.5 + (len(text) - hangul) / 3.5)


class RunClient:
    def __init__(self, *, mode: str, cache_path: Path, ledger_path: Path, prompt_sha: str, inner=None,
                 max_billed: int = MAX_BILLED_CAP, est_in: int = 4000, retries: int = TRANSIENT_RETRIES,
                 resume_after_stop: bool = False):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == "live" and inner is None:
            raise ValueError("live mode needs a model client")
        if not isinstance(max_billed, int) or not 1 <= max_billed <= MAX_BILLED_CAP:
            raise ValueError(f"max_billed must be 1..{MAX_BILLED_CAP}")
        self.mode, self.inner, self.prompt_sha = mode, inner, prompt_sha
        self.name = getattr(inner, "name", "replay")
        self.cache_path, self.ledger_path = Path(cache_path), Path(ledger_path)
        self.max_billed, self.est_in, self.retries = max_billed, est_in, retries
        self.cache = self._load()
        self.case_id: str | None = None
        self.last: str | None = None  # cached, answered, deferred, failed, missing, stopped
        self.billed = self.deferred = self.cache_hits = self.missing = self.answered = 0
        self.tokens_in = self.tokens_out = 0
        self.first_in: list[int] = []
        self.stop_reason: str | None = None
        self._streak = 0
        self.billed_before = 0  # billed requests of earlier runs over the same ledger (``billed``: this run's)
        self._restore(resume_after_stop)

    def _restore(self, resume: bool) -> None:
        """Billed requests, the first tokens_in and the hard-error run of the ledger already written; the stop rule
        they meet is in force again unless ``resume`` (the billed cap is checked before every request anyway)."""
        if not self.ledger_path.exists():
            return
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            status = row.get("status")
            if status not in ("ok", "error"):
                continue  # deferred (429) and missing rows are not billed
            self.billed_before += 1
            if status == "ok":
                self._streak = 0
                if len(self.first_in) < TOKEN_CHECK_REQUESTS:
                    tokens = row.get("tokens_in")
                    self.first_in.append(tokens if isinstance(tokens, int) else 0)
            elif row.get("error_class") in ("auth", "other"):
                self._streak += 1
            else:
                self._streak = 0
        reason = None
        if len(self.first_in) == TOKEN_CHECK_REQUESTS:
            mean = sum(self.first_in) / TOKEN_CHECK_REQUESTS
            if mean > TOKEN_FACTOR * self.est_in:
                reason = (f"mean tokens_in of the first {TOKEN_CHECK_REQUESTS} requests {mean:.0f} > {TOKEN_FACTOR} "
                          f"x --est-in {self.est_in}")
        if self._streak >= HARD_ERROR_STREAK:
            reason = f"{self._streak} auth/other errors in a row"
        if resume:
            self._streak = 0
        elif reason is not None:
            self.stop_reason = f"{reason} (an earlier run; --resume-after-stop to continue)"

    def __repr__(self) -> str:
        return f"RunClient({self.mode})"

    def _load(self) -> dict:
        out: dict = {}
        if not self.cache_path.exists():
            return out
        for line in self.cache_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("key"), str):
                out[row["key"]] = row
        return out

    @staticmethod
    def _append(path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()

    def _ledger(self, **row) -> None:
        self._append(self.ledger_path, {"case_id": self.case_id, **row})

    def _cache(self, row: dict) -> None:
        self.cache[row["key"]] = row
        self._append(self.cache_path, row)

    def complete(self, node: str, system: str, payload: dict, schema: dict) -> ModelReply:
        key = request_key(node, self.prompt_sha, payload, schema)
        points = len(payload.get("points") or []) if isinstance(payload, dict) else 0
        hit = self.cache.get(key)
        if hit is not None:
            self.cache_hits += 1
            self.last = "cached"
            if hit.get("status") == "ok":
                return ModelReply(hit.get("text", ""), hit.get("tokens_in", 0), hit.get("tokens_out", 0))
            raise ModelError("schema" if hit.get("error_class") == "schema" else "other", "cached error")
        if self.mode == "replay":
            self.missing += 1
            self.last = "missing"
            self._ledger(points=points, request_sha256=key, status="missing")
            raise ModelError("other", "not in the cache")
        attempt = 0
        while True:
            if self.stop_reason is None and self.billed_before + self.billed >= self.max_billed:
                self.stop_reason = f"billed requests of this folder reached --max-billed {self.max_billed}"
            if self.stop_reason is not None:
                self.last = "stopped"
                raise ModelError("other", "run stopped")
            t0 = time.monotonic()
            try:
                reply = self.inner.complete(node, system, payload, schema)
            except ModelError as err:
                ms = round((time.monotonic() - t0) * 1000)
                if err.http_status == 429:
                    self.deferred += 1
                    self.last = "deferred"
                    self._ledger(points=points, request_sha256=key, status="deferred", error_class=err.error_class,
                                 http_status=429, retry_after=err.retry_after, latency_ms=ms, attempt=attempt)
                    raise
                self.billed += 1
                self._ledger(points=points, request_sha256=key, status="error", error_class=err.error_class,
                             http_status=err.http_status, latency_ms=ms, attempt=attempt)
                if err.error_class == "transient" and attempt < self.retries:
                    attempt += 1
                    continue
                if err.error_class in ("auth", "other"):
                    self._streak += 1
                    if self._streak >= HARD_ERROR_STREAK:
                        self.stop_reason = f"{self._streak} auth/other errors in a row"
                else:
                    self._streak = 0
                if err.error_class == "schema":
                    self._cache({"key": key, "status": "error", "error_class": "schema"})
                self.last = "failed"
                raise
            ms = round((time.monotonic() - t0) * 1000)
            self.billed += 1
            self.answered += 1
            self._streak = 0
            self.tokens_in += reply.tokens_in
            self.tokens_out += reply.tokens_out
            self._cache({"key": key, "status": "ok", "text": reply.text, "tokens_in": reply.tokens_in,
                         "tokens_out": reply.tokens_out})
            self._ledger(points=points, request_sha256=key, status="ok", tokens_in=reply.tokens_in,
                         tokens_out=reply.tokens_out, latency_ms=ms, attempt=attempt,
                         response=reply.text[:LEDGER_TEXT_MAX])
            if len(self.first_in) < TOKEN_CHECK_REQUESTS:
                self.first_in.append(reply.tokens_in)
                if len(self.first_in) == TOKEN_CHECK_REQUESTS:
                    mean = sum(self.first_in) / TOKEN_CHECK_REQUESTS
                    if mean > TOKEN_FACTOR * self.est_in:
                        self.stop_reason = (f"mean tokens_in of the first {TOKEN_CHECK_REQUESTS} requests "
                                            f"{mean:.0f} > {TOKEN_FACTOR} x --est-in {self.est_in}")
            self.last = "answered"
            return reply

    def counts(self) -> dict:
        return {"billed": self.billed, "billed_total": self.billed_before + self.billed,
                "billed_before": self.billed_before, "answered": self.answered, "deferred": self.deferred,
                "cache_hits": self.cache_hits, "missing": self.missing, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "first_tokens_in": list(self.first_in),
                "stop_reason": self.stop_reason}
