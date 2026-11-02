"""The call ledger of one original question (plan 56 order 3, bundle 8; policy ⑧ and the A part of C9).

Every model call of the new A is reserved here right before it is sent and settled right after, so a limit is a
fact of the ledger, not of a counter that a run, a process or a ``select`` resets. Slots (``BudgetPolicy``):

- ``a_first``: the first judgment of the question, 1.
- ``a_self_rejudge``: A's own re-judgment, 1, shared by the re-search and a correction. A failed send never opens it.
- ``b_first:<bundle>`` / ``b_rejudge:<bundle>``: B's request bundles (every condition of one statistic is one
  bundle), 1 each. Closed while ``b_request_model`` is undecided (``b_request_model_undecided``, nothing is sent).
- transport retries: 0. A failed send counts as used.

The total per question (``total_per_question``) is not agreed: it is recorded in the summary and never refuses.
A 401 or 403 halts the credential it came from; every later reservation with that credential is refused before
sending (``credential_halted``) until a new approval opens a new ledger. B's model calls and KOSIS fetches are
counted apart (``count_external``), with no limit here.

A file ledger (``Ledger.open``) appends one JSON line per event and syncs it to disk before the call goes on; it is
held by one writer (a lock file next to it: ``ledger_busy``) and is never created by a read (``ledger_missing``).
A reservation that was never settled counts as used with ``sent`` and ``billing`` unknown. A reservation settled as
never sent (``sent`` no, outcome ``not_sent``: the call failed before anything left the process) does not use its
slot (Codex review 11-01); a failed send that may have reached the provider (500, timeout, unknown) stays used.
``Ledger.memory`` keeps the same events in memory (a replay, a test, the screen until bundle 9 makes the original
question durable).

A re-measurement allowance (``grant``, owner 11-02 04:22 UTC) is one appended ``allow`` event: its id (the approval
that granted it), its own slots (``a_first`` and ``a_self_rejudge``, at most one each), the code versions it is for
and its end. A reservation that names it (``allowance``: its id, the versions of the code now and the time now) is
counted against the allowance's slots only, among the reservations under that id; anything else about it refused
(``allowance_missing``, ``allowance_expired``, ``allowance_version_mismatch``, ``allowance_slot_missing``,
``allowance_slot_used``). The service's per-question limit is untouched: a reservation without an allowance counts
every reservation of the ledger, the allowance's included, against the policy as before. No earlier line is changed.
Standard library only (A's package rule).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# .2: a settled 'not_sent' attempt uses no slot; .3: re-measurement allowances (``allow`` events, ``grant``)
BUDGET_VERSION = "tl-budget-2026-11-02.3"
NOT_SENT = "not_sent"  # the outcome of an attempt that failed before anything left the process
A_SLOTS = ("a_first", "a_self_rejudge")
B_KINDS = ("b_first", "b_rejudge")
SENT = ("yes", "no", "unknown")
BILLING = ("billed", "none_reported", "unknown")
EXTERNAL = ("b_model", "kosis")
# the graph's re-search end names before the ledger and now (bundle 8: research.end may change name)
END_NAMES = {"rejudge_call_limit": "slot_used"}


class LedgerError(RuntimeError):
    """A refusal or misuse of the ledger. ``code``: ledger_missing, ledger_busy, question_key_mismatch, unknown_slot,
    slot_used, credential_halted, b_request_model_undecided, attempt_unknown, attempt_settled, bad_value, and of an
    allowance: allowance_missing, allowance_expired, allowance_version_mismatch, allowance_slot_missing,
    allowance_slot_used, allowance_exists. A refusal happens before anything is sent."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class BudgetPolicy:
    version: str = BUDGET_VERSION
    a_first: int = 1
    a_self_rejudge: int = 1
    b_first_per_bundle: int = 1
    b_rejudge_per_bundle: int = 1
    transport_retries: int = 0
    total_per_question: int | None = None
    b_request_model: str | None = None

    def limit(self, slot: str) -> int:
        if slot in A_SLOTS:
            return getattr(self, slot)
        kind, sep, bundle = slot.partition(":")
        if sep and bundle and kind in B_KINDS:
            return self.b_first_per_bundle if kind == "b_first" else self.b_rejudge_per_bundle
        raise LedgerError("unknown_slot", slot)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_of(question: str) -> str:
    """The original question's key: the sha256 of its text (never a session id)."""
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, question_key: str, policy: BudgetPolicy, events: list[dict], path: Path | None = None,
                 lock: Path | None = None):
        self.question_key, self.policy, self.events, self.path, self._lock = question_key, policy, events, path, lock

    # ---- opening

    @classmethod
    def memory(cls, question_key: str, policy: BudgetPolicy | None = None) -> Ledger:
        policy = policy or BudgetPolicy()
        return cls(question_key, policy, [{"kind": "open", "question_key": question_key, "policy": asdict(policy),
                                           "at": _now()}])

    @classmethod
    def open(cls, path, question_key: str, policy: BudgetPolicy | None = None, create: bool = False) -> Ledger:
        path = Path(path)
        lock = path.with_name(path.name + ".lock")
        if not path.exists() and not create:
            raise LedgerError("ledger_missing", str(path.name))
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LedgerError("ledger_busy", str(path.name)) from None
        os.close(fd)
        try:
            if path.exists():
                events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                head = events[0] if events else {}
                if head.get("question_key") != question_key:
                    raise LedgerError("question_key_mismatch", str(path.name))
                ledger = cls(question_key, BudgetPolicy(**head["policy"]), events, path, lock)
            else:
                policy = policy or BudgetPolicy()
                ledger = cls(question_key, policy, [], path, lock)
                ledger._append({"kind": "open", "question_key": question_key, "policy": asdict(policy), "at": _now()})
        except BaseException:
            lock.unlink(missing_ok=True)
            raise
        return ledger

    def close(self) -> None:
        if self._lock is not None:
            self._lock.unlink(missing_ok=True)
            self._lock = None

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _append(self, event: dict) -> None:
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())

    # ---- reading

    def used(self, slot: str) -> int:
        unsent = {e["attempt_id"] for e in self.events
                  if e["kind"] == "settle" and e["sent"] == "no" and e["outcome"] == NOT_SENT}
        return sum(1 for e in self.events if e["kind"] == "reserve" and e["slot"] == slot
                   and e["attempt_id"] not in unsent)

    def used_under(self, slot: str, approval_ref: str) -> int:
        """``slot``'s reservations under ``approval_ref`` (a settled 'not_sent' one apart)."""
        unsent = {e["attempt_id"] for e in self.events
                  if e["kind"] == "settle" and e["sent"] == "no" and e["outcome"] == NOT_SENT}
        return sum(1 for e in self.events if e["kind"] == "reserve" and e["slot"] == slot
                   and e.get("approval_ref") == approval_ref and e["attempt_id"] not in unsent)

    def allowance(self, allowance_id: str) -> dict | None:
        return next((e for e in self.events if e["kind"] == "allow" and e["allowance_id"] == allowance_id), None)

    def allowance_refusal(self, slot: str, allowance: dict) -> str | None:
        """Why ``slot`` cannot be reserved under ``allowance`` ({id, versions, now}), or None (read only)."""
        event = self.allowance(allowance["id"])
        if event is None:
            return "allowance_missing"
        if datetime.fromisoformat(allowance["now"]) >= datetime.fromisoformat(event["valid_until"]):
            return "allowance_expired"
        if event["versions"] != allowance["versions"]:
            return "allowance_version_mismatch"
        if slot not in event["slots"]:
            return "allowance_slot_missing"
        if self.used_under(slot, allowance["id"]) >= event["slots"][slot]:
            return "allowance_slot_used"
        return None

    def halted(self, credential_ref: str | None) -> str | None:
        if credential_ref is None:
            return None
        return next((e["code"] for e in self.events if e["kind"] == "halt" and e["credential_ref"] == credential_ref),
                    None)

    def _refusal(self, slot: str) -> str | None:
        if slot.partition(":")[0] in B_KINDS and self.policy.b_request_model is None:
            self.policy.limit(slot)  # an unknown B slot is still an error
            return "b_request_model_undecided"
        if self.used(slot) >= self.policy.limit(slot):
            return "slot_used"
        return None

    def available(self, slot: str) -> bool:
        """Whether ``slot`` can still be reserved (read only: nothing is used)."""
        return self._refusal(slot) is None

    def attempts(self) -> list[dict]:
        """Every reservation with its settlement; one never settled reads ``sent`` and ``billing`` unknown."""
        settled = {e["attempt_id"]: e for e in self.events if e["kind"] == "settle"}
        out = []
        for e in self.events:
            if e["kind"] == "reserve":
                s = settled.get(e["attempt_id"])
                out.append({**{k: v for k, v in e.items() if k != "kind"},
                            "settled": s is not None, "sent": s["sent"] if s else "unknown",
                            "billing": s["billing"] if s else "unknown", "outcome": s["outcome"] if s else None,
                            "provider_code": s.get("provider_code") if s else None})
        return out

    def summary(self) -> dict:
        attempts = self.attempts()
        slots = sorted({a["slot"] for a in attempts})
        total = len(attempts)
        cap = self.policy.total_per_question
        return {"version": BUDGET_VERSION, "question_key": self.question_key, "policy": asdict(self.policy),
                "by_slot": {s: self.used(s) for s in slots}, "total": total, "total_cap": cap,
                "over_total": cap is not None and total > cap,
                "unsettled": sum(1 for a in attempts if not a["settled"]),
                "not_sent": sum(1 for a in attempts if a["sent"] == "no" and a["outcome"] == NOT_SENT),
                "halted": {e["credential_ref"]: e["code"] for e in self.events if e["kind"] == "halt"},
                "external": {k: sum(e["n"] for e in self.events if e["kind"] == "external" and e["what"] == k)
                             for k in EXTERNAL}}

    # ---- writing

    def reserve(self, slot: str, model: str, request_sha256: str, credential_ref: str | None = None,
                question_version: int = 0, approval_ref: str | None = None, allowance: dict | None = None) -> str:
        """Use one of ``slot`` for a call about to be sent; the attempt id, or ``LedgerError`` before anything is
        sent (``credential_halted``, ``b_request_model_undecided``, ``slot_used``, or an allowance's refusal).
        ``allowance`` ({id, versions, now}; its id is the ``approval_ref``): counted against that allowance only."""
        if (code := self.halted(credential_ref)) is not None:
            raise LedgerError("credential_halted", code)
        if allowance is not None:
            if slot not in A_SLOTS or allowance.get("id") != approval_ref:
                raise LedgerError("bad_value", f"allowance for {slot} under {approval_ref}")
            if (why := self.allowance_refusal(slot, allowance)) is not None:
                raise LedgerError(why, slot)
        elif (why := self._refusal(slot)) is not None:
            raise LedgerError(why, slot)
        seq = sum(1 for e in self.events if e["kind"] == "reserve") + 1
        attempt_id = f"{self.question_key[:12]}-{seq}"
        self._append({"kind": "reserve", "attempt_id": attempt_id, "seq": seq, "slot": slot, "model": model,
                      "request_sha256": request_sha256, "credential_ref": credential_ref,
                      "question_version": question_version, "approval_ref": approval_ref, "at": _now()})
        return attempt_id

    def settle(self, attempt_id: str, sent: str, outcome: str, provider_code: str | None = None, usage=None,
               billing: str = "unknown") -> None:
        if sent not in SENT or billing not in BILLING:
            raise LedgerError("bad_value", f"sent={sent} billing={billing}")
        if not any(e["kind"] == "reserve" and e["attempt_id"] == attempt_id for e in self.events):
            raise LedgerError("attempt_unknown", attempt_id)
        if any(e["kind"] == "settle" and e["attempt_id"] == attempt_id for e in self.events):
            raise LedgerError("attempt_settled", attempt_id)
        self._append({"kind": "settle", "attempt_id": attempt_id, "sent": sent, "outcome": outcome,
                      "provider_code": provider_code, "usage": usage, "billing": billing, "at": _now()})

    def grant(self, allowance_id: str, slots: dict, versions: dict, valid_until: str, approved_by: str,
              approved_at: str, approval_sha256: str) -> None:
        """Append a re-measurement allowance (owner 11-02): its own slots (``a_first`` and ``a_self_rejudge``, 0 or 1
        each) for the code ``versions`` until ``valid_until``. Never twice for one id; no earlier line changes."""
        if self.allowance(allowance_id) is not None:
            raise LedgerError("allowance_exists", allowance_id)
        if not (isinstance(slots, dict) and slots and set(slots) <= set(A_SLOTS)
                and all(type(v) is int and v in (0, 1) for v in slots.values())):
            raise LedgerError("bad_value", f"slots {slots}")
        until = datetime.fromisoformat(valid_until)
        if until.tzinfo is None or not isinstance(versions, dict) or not versions:
            raise LedgerError("bad_value", "valid_until or versions")
        self._append({"kind": "allow", "allowance_id": allowance_id, "slots": dict(slots), "versions": versions,
                      "valid_until": valid_until, "approved_by": approved_by, "approved_at": approved_at,
                      "approval_sha256": approval_sha256, "at": _now()})

    def halt(self, credential_ref: str, code: str) -> None:
        if self.halted(credential_ref) is None:
            self._append({"kind": "halt", "credential_ref": credential_ref, "code": code, "at": _now()})

    def count_external(self, what: str, n: int = 1) -> None:
        """B's model calls and KOSIS fetches of this question: counted, never limited here."""
        if what not in EXTERNAL:
            raise LedgerError("bad_value", what)
        self._append({"kind": "external", "what": what, "n": n, "at": _now()})
