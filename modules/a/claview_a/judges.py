"""The judges of the new A: the only model access of ``claview_a.run``.

``judge(payload, request) -> text``. Moved unchanged from the table-level evaluator (``tl_eval.live``), which imports
them from here, so the command line and the evaluator use one implementation:

- ``LiveJudge``: one call = one transport attempt (``OpenAITransport``: one ``http.client`` request, no retry). A call
  budget refuses any call beyond the limit before it is sent; failures are archived and raised as ``JudgeError``.
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

from .graph import JudgeError


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


class LiveJudge:
    """One transport attempt per call within the budget; failures archived and raised as ``JudgeError``."""

    def __init__(self, transport, max_calls: int, archive: Archive, case_ref: dict):
        self.transport, self.max_calls, self.archive, self.case_ref = transport, max_calls, archive, case_ref
        self.calls = 0

    def __call__(self, payload: dict, request: dict) -> str:
        from .transport.provider import ProviderError, extract

        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"call {self.calls + 1} beyond the limit {self.max_calls}")
        self.calls += 1
        row = {"case_id": self.case_ref.get("case_id"), "call": self.calls, "started": now(),
               "request_sha256": text_sha(request), "request": request}
        t0 = time.monotonic()
        try:
            raw = self.transport.send(request)
        except ProviderError as e:
            code, provider = _cause(e)
            self.archive.add({**row, "elapsed_s": round(time.monotonic() - t0, 2), "outcome": "failed",
                              "error": code, "provider_code": provider, "usage": None, "raw_response": None})
            raise JudgeError(code) from None
        elapsed = round(time.monotonic() - t0, 2)
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        row.update({"elapsed_s": elapsed, "status": body.get("status"), "usage": body.get("usage"),
                    "incomplete_details": body.get("incomplete_details"), "raw_response": raw})
        try:
            text, _ = extract(raw)
        except ProviderError as e:
            code = str(e) if str(e) in ("incomplete", "refusal", "empty_output", "response_format") \
                else "response_format"
            self.archive.add({**row, "outcome": "failed", "error": code, "provider_code": str(e)})
            raise JudgeError(code) from None
        self.archive.add({**row, "outcome": "answered", "error": None})
        return text


class FakeJudge:
    """A fixed valid answer: the whole question as one statistic, the first table of the first card as its
    representative with the card name as title evidence. Wiring only: it says nothing about a model."""

    def __init__(self, archive: Archive, case_ref: dict):
        self.archive, self.case_ref, self.calls = archive, case_ref, 0

    def __call__(self, payload: dict, request: dict) -> str:
        self.calls += 1
        out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices",
                               "asks", "missing", "alternatives")}
        out["statistics"] = [{"id": "s1", "quote": payload["question"], "name": "fake"}]
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
