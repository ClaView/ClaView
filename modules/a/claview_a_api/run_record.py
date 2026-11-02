"""Run records of the new A's screen path (plan 56 order 10, bundle 9, C9): what a session asked and answered, kept on
disk so a restart, a new screen session or a new approval never starts a question's budget again.

Layout (a PoC on JSON files; a database later):

- ``<root>/a_sessions/<session_id>/record.json``: the session's record (``RECORD_VERSION``) with its checkpoint, the
  answers, the questions left, the statistics, the hand-off, the run versions, the question key and the runs;
  written whole to a temporary file and moved in place (``os.replace``) under the session's lock file.
- ``<root>/a_sessions/<session_id>/calls.jsonl``: the call attempts of this session (one line each).
- ``<root>/a_questions/<question_key>/ledger.json``: the call ledger of the original question (``budget.Ledger``,
  one event per line), shared by every session of that question.

The original question of a session is told by its lineage first (the previous session the screen names), then by the
normalized text (``budget.key_of``); a reworded question with a lineage keeps the lineage's question. A record that
is missing, broken or of another version stops before any call (``run_record_missing``, ``run_record_invalid``,
``run_record_version_mismatch``); nothing is run again by itself. Nothing here calls a model.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from claview_a.budget import BudgetPolicy, Ledger

RECORD_VERSION = "a-run-record/2026-10-31.1"
ERRORS = ("run_record_missing", "run_record_invalid", "run_record_version_mismatch", "run_record_busy",
          "question_budget_used")
_ID = re.compile(r"[0-9a-f]{8,64}")


class RunRecordError(RuntimeError):
    """A record that cannot be used; ``code`` is one of ``ERRORS``. Nothing was called."""

    def __init__(self, code: str, detail: str = ""):
        if code not in ERRORS:
            raise ValueError(code)
        super().__init__(code)
        self.code, self.detail = code, detail


class RunStore:
    """The run records under ``root``."""

    def __init__(self, root):
        self.root = Path(root)

    def _session(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
            raise RunRecordError("run_record_invalid", "session_id")
        return self.root / "a_sessions" / session_id

    def ledger(self, question_key: str, policy: BudgetPolicy) -> Ledger:
        """The original question's ledger, created on first use (the caller closes it)."""
        folder = self.root / "a_questions" / question_key
        folder.mkdir(parents=True, exist_ok=True)
        return Ledger.open(folder / "ledger.json", question_key, policy, create=True)

    @contextmanager
    def locked(self, session_id: str):
        """The session's lock held around a read, a change and its save (Codex review 11-01: an answer is checked
        against the stored record, applied and saved in one step); another holder makes it ``run_record_busy``."""
        folder = self._session(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        lock = folder / "record.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RunRecordError("run_record_busy", session_id) from None
        os.close(fd)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def save(self, checkpoint: dict, view: dict | None = None, held: bool = False) -> Path:
        """The session's record, written whole and moved in place under the session's lock (``held``: the caller
        holds it, ``locked``)."""
        sid = checkpoint["session"]["session_id"]
        folder = self._session(sid)
        if not held:
            with self.locked(sid):
                return self.save(checkpoint, view, held=True)
        body = record_of(checkpoint, view)
        tmp = folder / "record.json.tmp"
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, folder / "record.json")
        return folder / "record.json"

    def sessions_of(self, question_key: str) -> list[str]:
        """The sessions recorded for an original question, oldest first."""
        path = self.root / "a_questions" / question_key / "sessions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line)["session_id"] for line in path.read_text(encoding="utf-8").splitlines() if line]

    def add_session(self, question_key: str, session_id: str) -> None:
        folder = self.root / "a_questions" / question_key
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "sessions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": session_id}) + "\n")

    def append_calls(self, session_id: str, rows: list[dict]) -> None:
        folder = self._session(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "calls.jsonl").open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def load(self, session_id: str, screen_version: str) -> dict:
        """The session's checkpoint, or why it cannot be used (never a run of its own)."""
        path = self._session(session_id) / "record.json"
        if not path.exists():
            raise RunRecordError("run_record_missing", session_id)
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            checkpoint = body["checkpoint"]
            checkpoint["session"]["session_id"]
        except (ValueError, KeyError, TypeError):
            raise RunRecordError("run_record_invalid", session_id) from None
        if body.get("version") != RECORD_VERSION or checkpoint.get("contract") != screen_version:
            raise RunRecordError("run_record_version_mismatch", f"{body.get('version')} / {checkpoint.get('contract')}")
        return checkpoint

    def question_key(self, previous_session_id: str, screen_version: str) -> str:
        """The original question of a lineage: the previous session's question key (its record must be there)."""
        return self.load(previous_session_id, screen_version)["question_key"]


def record_of(checkpoint: dict, view: dict | None = None) -> dict:
    """record.json v1 (plan 56 bundle 9): the checkpoint and what a reader needs without opening it."""
    session, state = checkpoint["session"], checkpoint.get("a_state") or {}
    log = state.get("answer_log") or {}
    view = view or {}
    return {
        "version": RECORD_VERSION,
        "session_id": session["session_id"],
        "question_version": session["question_version"],
        "question": session["question"],
        "as_of": session["as_of"],
        "profile": session.get("profile"),
        "question_key": checkpoint.get("question_key"),
        "lineage": checkpoint.get("lineage"),
        "versions": {**((state.get("run_info") or {}).get("versions") or {}), "screen": checkpoint.get("contract"),
                     "record": RECORD_VERSION, "a_view": (view.get("contract_version"))},
        "answers": [*(log.get("rows") or []), *((checkpoint.get("period") or {}).get("answers") or [])],
        "remaining_questions": [q["question_id"] for q in view.get("questions") or []],
        "statistics": [{"statistic": s["statistic"], "outcome": s["outcome"], "state": s["state"]}
                       for s in view.get("statistics") or []],
        "handoff": {"ready": [r["request_id"] for r in (view.get("handoff") or {}).get("ready") or []],
                    "blocked": [b.get("code") for b in (view.get("handoff") or {}).get("blocked") or []]},
        "downstream": {},
        "ledger_ref": f"a_questions/{checkpoint.get('question_key')}/ledger.json",
        # B model calls and KOSIS fetches of the session, as the pipeline counted them (plan 56 order 13-1)
        "external_counts": {"B_model": 0, "KOSIS": 0, **(checkpoint.get("external_counts") or {})},
        "runs": list(checkpoint.get("runs") or []),
        "applied_batches": list(checkpoint.get("applied_batches") or []),
        "status": view.get("status"),
        "checkpoint": checkpoint,
    }
