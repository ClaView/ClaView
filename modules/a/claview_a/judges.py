"""The judges of the new A: the only model access of ``claview_a.run``.

``judge(payload, request) -> text``. Moved unchanged from the table-level evaluator (``tl_eval.live``), which imports
them from here, so the command line and the evaluator use one implementation:

- ``LiveJudge``: one call = one transport attempt (``OpenAITransport``: one ``http.client`` request, no retry). A call
  budget refuses any call beyond the limit before it is sent; failures are archived and raised as ``JudgeError``.
  With a ledger (``budget.Ledger``, plan 56 order 3) every call is reserved right before it is sent and settled right
  after (``sent``: yes, no, unknown; ``billing``: billed, none_reported, unknown); a refused reservation sends nothing.
- ``GuardedTransport``: a 401 or 403 halts the credential in the ledger, so the next reservation with it is refused
  before anything is sent.
- ``FakeJudge``: a fixed valid answer built from the cards (wiring only; says nothing about a model).
- ``ReplayJudge``: the archived answer of the same request (by request hash), no network.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .budget import NOT_SENT, Ledger, LedgerError
from .contract import CONTRACT_VERSION
from .graph import JudgeError

KEY_REFUSALS = ("http_401", "http_403")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class BudgetExceeded(RuntimeError):
    pass


class Archive:
    """Call rows, each written to ``calls.jsonl`` as it happens (a stopped run keeps what it paid for)."""

    def __init__(self, path: Path):
        self.path, self.rows = path, []

    def add(self, row: dict) -> None:
        self.rows.append(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _cause(e: Exception) -> tuple[str, str]:
    """(JudgeError code, provider code) of a transport failure. The transport hides the original exception with
    ``from None`` but keeps it as ``__context__``; only its type is read, never its message."""
    code = str(e)
    if code == "key_unavailable":
        return "key_unavailable", code
    if code == "transport" and isinstance(e.__context__, TimeoutError):
        return "timeout", "transport:" + type(e.__context__).__name__
    if code == "transport" and e.__context__ is not None:
        return "transport", "transport:" + type(e.__context__).__name__
    return "transport", code


def sent_of(e: Exception) -> tuple[str, str]:
    """(sent, billing) of a failed send, from the transport's ``phase`` (or its code when it has none)."""
    code, phase = str(e), getattr(e, "phase", None)
    if phase is None:
        phase = "before_send" if code == "key_unavailable" else "response" if code.startswith("http_") else "send"
    if phase == "before_send":
        return "no", "none_reported"
    if phase == "send":
        return "unknown", "unknown"
    return "yes", "none_reported" if code.startswith("http_4") else "unknown"


class LiveJudge:
    """One transport attempt per call within the budget; failures archived and raised as ``JudgeError``. With a
    ``ledger``, ``slot`` is reserved (``credential_ref`` names the key) before the call and settled after it."""

    answer_contract = CONTRACT_VERSION  # its answers are written to the current contract

    def __init__(self, transport, max_calls: int, archive: Archive, case_ref: dict, ledger: Ledger | None = None,
                 slot: str = "a_first", credential_ref: str | None = None, approval_ref: str | None = None,
                 allowance=None):
        self.transport, self.max_calls, self.archive, self.case_ref = transport, max_calls, archive, case_ref
        self.ledger, self.slot, self.credential_ref = ledger, slot, credential_ref
        self.approval_ref = approval_ref  # the approval the call goes under, kept with its reservation
        self.allowance = allowance  # () -> the re-measurement allowance's context ({id, versions, now}), or None
        self.calls = 0

    def __call__(self, payload: dict, request: dict) -> str:
        from .transport.provider import ProviderError, extract

        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"call {self.calls + 1} beyond the limit {self.max_calls}")
        halted = getattr(self.transport, "refuse_if_halted", None)
        if halted is not None:  # a halted key is refused before anything is reserved or sent
            halted()
        attempt = None
        if self.ledger is not None:  # a refusal (LedgerError) sends nothing and uses nothing
            attempt = self.ledger.reserve(self.slot, request.get("model"), text_sha(request), self.credential_ref,
                                          approval_ref=self.approval_ref,
                                          allowance=self.allowance() if self.allowance is not None else None)
        self.calls += 1
        row = {"case_id": self.case_ref.get("case_id"), "call": self.calls, "started": now(),
               "request_sha256": text_sha(request), "request": request}
        if attempt is not None:
            row.update(attempt_id=attempt, slot=self.slot)

        def settle(sent: str, billing: str, outcome: str, provider_code=None, usage=None) -> dict:
            if attempt is None:
                return {}
            self.ledger.settle(attempt, sent, outcome, provider_code, usage, billing)
            return {"sent": sent, "billing": billing}

        t0 = time.monotonic()
        try:
            raw = self.transport.send(request)
        except ProviderError as e:
            code, provider = _cause(e)
            sent, billing = sent_of(e)
            # a failure before anything left the process uses no slot (outcome not_sent); one that may have reached
            # the provider stays used (Codex review 11-01)
            self.archive.add({**row, "elapsed_s": round(time.monotonic() - t0, 2), "outcome": "failed",
                              "error": code, "provider_code": provider, "usage": None, "raw_response": None,
                              **settle(sent, billing, NOT_SENT if sent == "no" else "failed", provider)})
            raise JudgeError(code) from None
        elapsed = round(time.monotonic() - t0, 2)
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        usage = body.get("usage")
        row.update({"elapsed_s": elapsed, "status": body.get("status"), "usage": usage,
                    "incomplete_details": body.get("incomplete_details"), "raw_response": raw})
        try:
            text, _ = extract(raw)
        except ProviderError as e:
            code = str(e) if str(e) in ("incomplete", "refusal", "empty_output", "response_format") \
                else "response_format"
            self.archive.add({**row, "outcome": "failed", "error": code, "provider_code": str(e),
                              **settle("yes", "billed" if usage else "unknown", "failed", str(e), usage)})
            raise JudgeError(code) from None
        self.archive.add({**row, "outcome": "answered", "error": None,
                          **settle("yes", "billed" if usage else "none_reported", "answered", None, usage)})
        return text


class GuardedTransport:
    """``inner`` with the ledger's credential halt: a 401 or 403 halts ``credential_ref`` (then re-raised as it was);
    a send with a halted credential is refused before it reaches ``inner`` (``credential_halted``)."""

    def __init__(self, inner, ledger: Ledger, credential_ref: str):
        self.inner, self.ledger, self.credential_ref = inner, ledger, credential_ref
        self.kind = getattr(inner, "kind", None)

    def preflight(self) -> None:
        self.inner.preflight()

    def refuse_if_halted(self) -> None:
        if (code := self.ledger.halted(self.credential_ref)) is not None:
            raise LedgerError("credential_halted", code)

    def send(self, request: dict) -> str:
        from .transport.provider import ProviderError

        self.refuse_if_halted()
        try:
            return self.inner.send(request)
        except ProviderError as e:
            if str(e) in KEY_REFUSALS:
                self.ledger.halt(self.credential_ref, str(e))
            raise


class FakeJudge:
    """A fixed valid answer: the whole question as one statistic, the first table of the first card as its
    representative with the card name as title evidence. Wiring only: it says nothing about a model. With a
    ``ledger`` its calls are reserved and settled as never sent (``sent: no``), like any other call."""

    # This wiring stub has no semantic interpreter. Keep its original quote-only contract;
    # structured-contract validation uses explicit ScriptJudge fixtures instead.
    answer_contract = "tl-contract-2026-10-28.7"

    def __init__(self, archive: Archive, case_ref: dict, ledger: Ledger | None = None, slot: str = "a_first"):
        self.archive, self.case_ref, self.calls = archive, case_ref, 0
        self.ledger, self.slot = ledger, slot

    def __call__(self, payload: dict, request: dict) -> str:
        if self.ledger is not None:
            self.ledger.settle(self.ledger.reserve(self.slot, request.get("model"), text_sha(request)), "no", "fake",
                               billing="none_reported")
        self.calls += 1
        out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices",
                               "asks", "missing", "alternatives", "aggregations", "changes", "focuses")}
        out["statistics"] = [{"id": "s1", "quote": payload["question"], "name": "fake"}]
        out["focuses"] = [{"for": ["s1"], "text": "synthetic wiring only", "quotes": [payload["question"]]}]
        m = re.search(r"^\[(g\d+)\] (.+)$", payload["cards"], re.M)
        if m:
            out["choices"] = [{"for": ["s1"], "table": f"{m.group(1)}.t1",
                               "evidence": [{"kind": "title", "text": m.group(2)}]}]
        else:
            out["missing"] = [{"for": ["s1"], "search_words": ["fake"]}]
        text = json.dumps(out, ensure_ascii=False)
        self.archive.add({"case_id": self.case_ref.get("case_id"), "call": self.calls, "outcome": "fake",
                          "request_sha256": text_sha(request), "payload_chars": len(json.dumps(
                              payload, ensure_ascii=False)), "usage": None, "raw_response": None, "text": text})
        return text


class ReplayJudge:
    """The archived response of the same request; a miss or an archived failure is raised, never called live."""

    def __init__(self, rows: list[dict], archive: Archive, case_ref: dict):
        self.by = {r["request_sha256"]: r for r in rows if r.get("outcome") in ("answered", "failed")}
        self.archive, self.case_ref, self.calls = archive, case_ref, 0

    def __call__(self, payload: dict, request: dict) -> str:
        from .transport.provider import extract

        self.calls += 1
        got = self.by.get(text_sha(request))
        self.archive.add({"case_id": self.case_ref.get("case_id"), "call": self.calls, "outcome": "replay",
                          "request_sha256": text_sha(request), "hit": got is not None, "usage": None})
        if got is None:
            raise LookupError(f"no archived response for {self.case_ref.get('case_id')}")
        if got["outcome"] == "failed":
            raise JudgeError(got["error"])
        return extract(got["raw_response"])[0]
