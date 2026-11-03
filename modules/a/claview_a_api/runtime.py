"""The deployed service's live A entry point, separate from evaluation approvals.

The deployment chooses one durable RunStore. Each question keeps the existing first
judgment and recovery slots; no approval, restart or new session resets those slots.
An explicitly configured evaluation approval always takes the existing evaluation path.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from claview_a.budget import LedgerError
from claview_a.judges import KEY_REFUSALS, Archive, LiveJudge
from claview_a.transport.provider import OpenAITransport, ProviderError

from . import live_gate, screen
from .run_record import RunRecordError, RunStore

# Public service refusal interface. Callers handle these exceptions through the A
# API boundary; they need not import the ledger or transport implementation.
__all__ = [
    "LedgerError", "ProviderError", "credential_halted", "sent_for",
    "service_live_judges", "service_store",
]

CREDENTIAL = "openai:service"


def service_store(mode: str | None = None) -> RunStore:
    """Resolve server configuration only; HTTP clients cannot choose the store or mode."""
    mode = mode or os.environ.get("CLAVIEW_MODE", "synthetic").strip().lower()
    if mode not in ("live", "synthetic"):
        raise ValueError("a_mode_invalid")
    root = Path(os.environ.get("CLAVIEW_A_RUN_ROOT") or
                Path(__file__).resolve().parents[3] / "runs" / "a_service").resolve()
    # Existing evaluation manifests bind the exact configured run_root.
    if mode != "live" or not os.environ.get(live_gate.APPROVAL_ENV):
        root = root / mode
    return RunStore(root)


class ServiceUsage:
    """Durable credential halt and per-question call archive, without evaluation allowances."""

    def __init__(self, root: Path):
        self.folder = root / "service_live"

    def calls_path(self, question_key: str) -> Path:
        return self.folder / "questions" / question_key / "calls.jsonl"

    def halted(self, credential: str = CREDENTIAL) -> str | None:
        del credential
        path = self.folder / "credential_halt.json"
        if not path.exists():
            return None
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            code = row["code"]
            if code not in KEY_REFUSALS:
                raise ValueError("invalid credential halt")
            return code
        except (ValueError, KeyError, TypeError):
            raise RunRecordError("run_record_invalid") from None

    def halt(self, code: str) -> None:
        # Called with the shared send lock held. No credential values are persisted.
        path = self.folder / "credential_halt.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"code": code}), encoding="utf-8")
        os.replace(temporary, path)

    @contextmanager
    def sending(self):
        """Serialize the halt check and transport across the service's A processes."""
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / "send.lock"
        # OS-owned locks disappear on process/container exit; the file may safely remain.
        with path.open("a+b") as handle:
            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ProviderError("a_service_busy", "before_send") from None
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def sent(self, question_key: str) -> int:
        path = self.calls_path(question_key)
        if not path.exists():
            return 0
        try:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            return sum(row.get("sent") in ("yes", "unknown") for row in rows)
        except (ValueError, TypeError, AttributeError):
            raise RunRecordError("run_record_invalid") from None


class ServiceTransport:
    """One existing provider send; 401/403 persist across questions and process restarts."""

    def __init__(self, inner, usage: ServiceUsage, ledger):
        self.inner, self.usage, self.ledger = inner, usage, ledger
        self.kind = "live"

    def refuse_if_halted(self):
        code = self.usage.halted() or self.ledger.halted(CREDENTIAL)
        if code is not None:
            raise LedgerError("credential_halted", code)
        check = getattr(self.inner, "refuse_if_halted", None)
        if check is not None:
            check()

    def preflight(self):
        self.refuse_if_halted()
        self.inner.preflight()

    def send(self, request):
        with self.usage.sending():
            # A halt can occur after LiveJudge's pre-reservation check.
            try:
                self.refuse_if_halted()
            except LedgerError:
                raise ProviderError("credential_halted", "before_send") from None
            try:
                return self.inner.send(request)
            except ProviderError as exc:
                if str(exc) in KEY_REFUSALS:
                    self.usage.halt(str(exc))
                    self.ledger.halt(CREDENTIAL, str(exc))
                raise


def service_live_judges(store: RunStore, transport_factory=None) -> screen.Judges:
    """Use deployment live mode, or the strict evaluation gate when explicitly configured."""
    if os.environ.get(live_gate.APPROVAL_ENV):
        return live_gate.judges(store, transport_factory=transport_factory)
    if store is None:
        raise RunRecordError("run_record_missing")
    usage = ServiceUsage(store.root)
    if (code := usage.halted()) is not None:
        raise LedgerError("credential_halted", code)
    transports = {}

    def transport_for(question_key):
        if question_key not in transports:
            transports[question_key] = (transport_factory or OpenAITransport)()
        return transports[question_key]

    def admit(question_key, ledger):
        # Missing server credentials are configuration failures, not an A decision/session.
        transport = ServiceTransport(transport_for(question_key), usage, ledger)
        transport.refuse_if_halted()
        preflight = getattr(transport.inner, "preflight", None)
        if preflight is not None:
            preflight()

    def bind(ledger, question_key):
        path = usage.calls_path(question_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        archive = Archive(path)
        transport = ServiceTransport(transport_for(question_key), usage, ledger)
        ref = {"case_id": question_key[:12]}
        first = LiveJudge(transport, 1, archive, ref, ledger, "a_first", CREDENTIAL)
        recovery = LiveJudge(transport, 1, archive, {**ref, "round": 2}, ledger, "a_self_rejudge", CREDENTIAL)
        return first, recovery

    result = screen.Judges(None, None, "live", bind=bind, admit=admit)
    # Keep gate=None: service calls do not borrow evaluation allowances or approved slots.
    result.service_usage = usage
    return result


def credential_halted(judges: screen.Judges) -> str | None:
    """The pipeline checks this before continuing from A to B after an authentication failure."""
    usage = getattr(judges, "service_usage", None)
    if usage is not None:
        return usage.halted()
    if judges.gate is not None:
        return judges.gate.usage.halted(live_gate.CREDENTIAL)
    return None


def sent_for(judges: screen.Judges, query: str) -> int:
    usage = getattr(judges, "service_usage", None)
    return usage.sent(screen.key_of(query)) if usage is not None else 0
