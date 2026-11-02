"""The screen path's live A calls behind an approval record (plan 56 order 13-1; off by default).

Off unless ``CLAVIEW_A_LIVE_APPROVAL`` names an approval record: then ``screen.live_judges`` refuses
(``LiveDisabled``) and makes no transport. An approval record (``APPROVAL_VERSION``, made by ``make_approval`` with
``live_calls`` false; only the user sets it true) names its id, the original questions it covers, the A calls per
question, the run's caps on A, B model and KOSIS calls and when it expires. Its ``conditions`` are the A versions,
settings, catalog and sources as they were when it was made, so an approval made for other code is refused
(``approval_stale``). Every refusal happens before anything is reserved or sent:

- the record: approval_missing, approval_invalid, approval_not_approved, approval_stale, approval_expired;
- the question: question_not_approved (an original question not in the list), question_a_limit_reached (the
  question's A calls in its ledger, over every session and approval), run_a_cap_reached (this approval's A sends);
- the key: credential_halted (a 401 or 403 under this approval, kept in its usage file);
- the records: run_record_required (no run records, so the budget would start again);
- a re-measurement allowance (an approval with ``allowance``, owner 11-02 04:22 UTC): allowance_missing (not
  granted on the question's ledger by ``grant_allowance``), allowance_expired, allowance_version_mismatch,
  allowance_slot_used. Under an allowance the question's A calls are counted under this approval only, against the
  allowance's own slots (``budget.Ledger.grant``); the service's per-question limit is never widened.

The live ledger is apart from the run records and fixed (coordinator 2026-10-31 23:19, C9 and 5.3 (c)): every
live call of an original question is reserved in ``<LIVE_ROOT>/a_questions/<question_key>/ledger.json``, where only
real sends are counted (synthetic and replayed judgments keep the run records' own ledger and never use its slots).
``LIVE_ROOT`` is one folder per machine (the user's ``.claview/a_live``), not a setting: an approval names it and the
run records folder it is for (``ledger_root``, ``run_root``), and a run under another run records folder or another
live folder is refused before anything is sent (``run_root_mismatch``, ``ledger_root_mismatch``). A new approval
writes to the same live ledger, so a question's budget goes on across approvals and folders.

A send is counted in the approval's usage file right before it goes; a 500 or a timeout stays counted and is never
sent again (``LiveJudge``: one transport attempt per call). The usage file is
``<LIVE_ROOT>/a_approvals/<approval_id>/usage.jsonl`` (one JSON line per event, synced to disk), next to the call
archive ``calls.jsonl``. The pipeline adds the run's B model calls and KOSIS fetches to it (``Usage.add_external``)
and stops the next B request at a cap or after B's 401 or 403 (``Gate.b_refusal``). One server process is assumed
(a thread lock); several processes sharing one approval are outside the first stage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import claview_a
from claview_a.budget import A_SLOTS, BudgetPolicy, Ledger, LedgerError
from claview_a.graph import JudgeError, run_info
from claview_a.judges import Archive, GuardedTransport, LiveJudge, sent_of

from . import screen as S
from .run_record import RECORD_VERSION

APPROVAL_ENV = "CLAVIEW_A_LIVE_APPROVAL"
APPROVAL_VERSION = "a-screen-approval/2026-11-01.1"
CREDENTIAL = "openai:screen"  # the A key's name in the usage file (never its value)
B_CREDENTIAL = "b_service"  # B's keys together: a 401 or 403 from B halts B's next request
CAPS = ("a", "b_model", "kosis")
LIVE_ROOT = Path.home() / ".claview" / "a_live"  # the machine's live ledger folder (tests point it elsewhere)
REFUSALS = ("approval_missing", "approval_invalid", "approval_not_approved", "approval_stale", "approval_expired",
            "question_not_approved", "question_a_limit_reached", "run_a_cap_reached", "credential_halted",
            "run_record_required", "approval_changed", "run_root_mismatch", "ledger_root_mismatch",
            "allowance_missing", "allowance_expired", "allowance_version_mismatch", "allowance_slot_used")
B_REFUSALS = ("b_model_cap_reached", "kosis_cap_reached", "b_credential_halted", "approval_expired",
              "approval_changed")
_ID = re.compile(r"[A-Za-z0-9_-]{4,64}")
_LOCK = threading.Lock()
ALLOWANCE_BATCH_VERSION = "a-allowance-batch/2026-11-02.1"


class ApprovalRefused(RuntimeError):
    """A live call refused before anything is reserved or sent; ``code`` is one of ``REFUSALS``."""

    def __init__(self, code: str, detail: str = ""):
        if code not in REFUSALS:
            raise ValueError(code)
        super().__init__(code)
        self.code, self.detail = code, detail


def _utcnow() -> datetime:
    return datetime.now(UTC)


def live_root() -> Path:
    return Path(LIVE_ROOT).resolve()


def open_ledger(question_key: str, policy) -> Ledger:
    """The original question's live ledger (real sends only), created on first use (the caller closes it)."""
    folder = live_root() / "a_questions" / question_key
    folder.mkdir(parents=True, exist_ok=True)
    return Ledger.open(folder / "ledger.json", question_key, policy, create=True)


def _sources_sha() -> str:
    """The sha256 of A's two packages (every file but caches), by relative path."""
    h = hashlib.sha256()
    for base in (Path(claview_a.__file__).parent, Path(__file__).parent):
        for p in sorted(x for x in base.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
            h.update(f"{base.name}/{p.relative_to(base).as_posix()}\n".encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def conditions() -> dict:
    """What an approval is made for: A's versions, settings, catalog and sources now."""
    first, _ = S.settings()
    info = run_info("", first)
    return {"approval": APPROVAL_VERSION, "screen": S.SCREEN_VERSION, "record": RECORD_VERSION,
            "profile": S.PROFILE_NAME, "settings": copy.deepcopy(S.PROFILE), "versions": info["versions"],
            "catalog": info["catalog"], "period_policy": info["period_policy"], "sources_sha256": _sources_sha()}


def make_approval(approval_id: str, questions: list[str], per_question_a: int, run_caps: dict, valid_until: str,
                  run_root, note: str = "", allowance: dict | None = None) -> dict:
    """A new approval record for the code as it is now, not approved yet (only the user sets ``live_calls``), for the
    run records folder ``run_root`` and this machine's live ledger. ``allowance`` ({"slots": {"a_first": 0|1,
    "a_self_rejudge": 0|1}}): a re-measurement of questions whose first judgment is used, opened per question by
    ``grant_allowance`` once the user approved it."""
    record = {"version": APPROVAL_VERSION, "approval_id": approval_id, "live_calls": False, "approved_by": None,
              "approved_at": None, "valid_until": valid_until, "questions": list(questions),
              "per_question_a": per_question_a, "run_caps": dict(run_caps), "note": note,
              "run_root": str(Path(run_root).resolve()), "ledger_root": str(live_root()),
              "conditions": conditions()}
    if allowance is not None:
        record["allowance"] = copy.deepcopy(allowance)
    _validate(record)
    return record


def _validate(r) -> datetime:
    """The record's form (``approval_invalid``); its expiry as a time."""
    ok = (isinstance(r, dict) and isinstance(r.get("approval_id"), str) and _ID.fullmatch(r["approval_id"])
          and isinstance(r.get("live_calls"), bool)
          and isinstance(r.get("questions"), list) and r["questions"]
          and all(isinstance(q, str) and q.strip() for q in r["questions"])
          and r.get("per_question_a") in (1, 2) and type(r["per_question_a"]) is int
          and isinstance(r.get("run_caps"), dict) and set(r["run_caps"]) == set(CAPS)
          and all(type(v) is int and v >= 0 for v in r["run_caps"].values())
          and isinstance(r.get("conditions"), dict) and isinstance(r.get("valid_until"), str)
          and isinstance(r.get("run_root"), str) and isinstance(r.get("ledger_root"), str))
    if not ok:
        raise ApprovalRefused("approval_invalid")
    if "allowance" in r:
        slots = (r["allowance"] or {}).get("slots") if isinstance(r["allowance"], dict) else None
        if not (isinstance(slots, dict) and slots and set(slots) <= set(A_SLOTS)
                and all(type(v) is int and v in (0, 1) for v in slots.values())):
            raise ApprovalRefused("approval_invalid", "allowance")
    try:
        until = datetime.fromisoformat(r["valid_until"])
    except ValueError:
        raise ApprovalRefused("approval_invalid", "valid_until") from None
    if until.tzinfo is None:
        raise ApprovalRefused("approval_invalid", "valid_until without a time zone")
    return until


def load_approval(path, now=_utcnow) -> dict:
    """The approval record at ``path``, or why it cannot open live calls (``ApprovalRefused``)."""
    path = Path(path)
    if not path.is_file():
        raise ApprovalRefused("approval_missing", path.name)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ApprovalRefused("approval_invalid", path.name) from None
    if isinstance(record, dict) and record.get("version") != APPROVAL_VERSION:
        raise ApprovalRefused("approval_stale", f"version {record.get('version')}")
    until = _validate(record)
    if record["live_calls"] is not True:
        raise ApprovalRefused("approval_not_approved", record["approval_id"])
    if record["conditions"] != conditions():
        changed = sorted(k for k in set(record["conditions"]) | set(conditions())
                         if record["conditions"].get(k) != conditions().get(k))
        raise ApprovalRefused("approval_stale", ",".join(changed))
    if now() >= until:
        raise ApprovalRefused("approval_expired", record["valid_until"])
    return record


class Usage:
    """One approval's usage file: A sends, B model calls and KOSIS fetches, credential halts."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "usage.jsonl"
        self.folder.mkdir(parents=True, exist_ok=True)

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append(self, event: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**event, "at": _utcnow().isoformat()}, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def count(self, what: str) -> int:
        return sum(e.get("n", 1) for e in self.events() if e["kind"] == "count" and e["what"] == what)

    def add(self, what: str, n: int = 1, **detail) -> None:
        self._append({"kind": "count", "what": what, "n": n, **detail})

    # the ledger's halt interface, so GuardedTransport keeps a 401 or 403 here
    def halted(self, credential_ref: str | None) -> str | None:
        return next((e["code"] for e in self.events() if e["kind"] == "halt" and e["credential_ref"] == credential_ref),
                    None)

    def halt(self, credential_ref: str, code: str) -> None:
        if self.halted(credential_ref) is None:
            self._append({"kind": "halt", "credential_ref": credential_ref, "code": code})


def a_used(ledger) -> int:
    """The original question's A calls (every session, every approval): its ledger's reservations."""
    return ledger.used("a_first") + ledger.used("a_self_rejudge")


class Gate:
    """One approval's checks over its usage file."""

    def __init__(self, approval: dict, usage: Usage, now=_utcnow, approval_sha256: str | None = None):
        self.approval, self.usage, self.now = approval, usage, now
        self.approval_sha256 = approval_sha256
        self.keys = {S.key_of(q) for q in approval["questions"]}
        self.until = datetime.fromisoformat(approval["valid_until"])
        self.refusals: list[dict] = []  # the refusals of this process, for the run record

    @property
    def approval_id(self) -> str:
        return self.approval["approval_id"]

    def _refuse(self, code: str, detail: str = ""):
        self.refusals.append({"code": code, "detail": detail, "at": _utcnow().isoformat()})
        raise ApprovalRefused(code, detail)

    def allowance_context(self) -> dict | None:
        """The context a reservation under this approval's allowance names (its id, the versions it is for, now)."""
        if "allowance" not in self.approval:
            return None
        return {"id": self.approval_id, "versions": self.approval["conditions"]["versions"],
                "now": self.now().isoformat()}

    def check(self, question_key: str, ledger, reserved: bool = False) -> None:
        """Every refusal for the next A call of ``question_key`` (read only). ``reserved``: the call's own reservation
        is already in the ledger (right before the send). Under an allowance the question's calls are this
        approval's only, and the next one is its first judgment until that is used, then its re-judgment."""
        if self.now() >= self.until:
            self._refuse("approval_expired", self.approval["valid_until"])
        if question_key not in self.keys:
            self._refuse("question_not_approved", question_key[:12])
        if (code := self.usage.halted(CREDENTIAL)) is not None:
            self._refuse("credential_halted", code)
        allowance = self.allowance_context()
        if allowance is not None:
            used = ledger.used_under("a_first", self.approval_id) + ledger.used_under("a_self_rejudge",
                                                                                      self.approval_id)
            if not reserved:
                slot = "a_first" if ledger.used_under("a_first", self.approval_id) == 0 else "a_self_rejudge"
                if (why := ledger.allowance_refusal(slot, allowance)) is not None:
                    self._refuse("allowance_slot_used" if why in ("allowance_slot_used", "allowance_slot_missing")
                                 else why, slot)
            if allowance_batch_status(self.approval, self.approval_sha256) != "committed":
                self._refuse("allowance_missing", "batch_not_committed")
            if not _matching_allowance(ledger, _allowance_identity(self.approval, self.approval_sha256)):
                self._refuse("allowance_missing", "batch_ledger_missing")
        else:
            used = a_used(ledger)
        if used - (1 if reserved else 0) >= self.approval["per_question_a"]:
            self._refuse("question_a_limit_reached", str(self.approval["per_question_a"]))
        if self.usage.count("a") >= self.approval["run_caps"]["a"]:
            self._refuse("run_a_cap_reached", str(self.approval["run_caps"]["a"]))

    def rejudge_ok(self, question_key: str, ledger) -> bool:
        """Whether the one re-judgment may be sent; a refusal is kept and ends the re-search (no Sol call)."""
        try:
            self.check(question_key, ledger)
        except ApprovalRefused:
            return False
        return True

    def b_refusal(self, step: str = "resolve") -> str | None:
        """Why the run's next B request (``step`` resolve, or fetch: KOSIS only) is not sent (``B_REFUSALS``), or
        None. A resolve is not sent once either cap is reached (its fetch could not follow)."""
        caps = self.approval["run_caps"]
        if self.now() >= self.until:
            return "approval_expired"
        if self.usage.halted(B_CREDENTIAL) is not None:
            return "b_credential_halted"
        if step == "resolve" and self.usage.count("b_model") >= caps["b_model"]:
            return "b_model_cap_reached"
        if self.usage.count("kosis") >= caps["kosis"]:
            return "kosis_cap_reached"
        return None


class GatedTransport:
    """``inner`` behind the gate: checked before the ledger reservation (``refuse_if_halted``, which ``LiveJudge``
    asks first) and again right before the send, where the send is counted; a 401 or 403 halts the A key in the usage
    file (``GuardedTransport``)."""

    kind = "live"

    def __init__(self, inner, gate: Gate, ledger, question_key: str):
        self.guarded = GuardedTransport(inner, gate.usage, CREDENTIAL)
        self.gate, self.ledger, self.question_key = gate, ledger, question_key

    def preflight(self) -> None:
        self.guarded.preflight()

    def refuse_if_halted(self) -> None:
        self.gate.check(self.question_key, self.ledger)

    def send(self, request: dict) -> str:
        from claview_a.transport.provider import ProviderError

        with _LOCK:
            try:
                self.gate.check(self.question_key, self.ledger, reserved=True)
            except ApprovalRefused as e:  # settled by LiveJudge as never sent
                raise ProviderError(e.code, "before_send") from None
            self.gate.usage.add("a", question_key=self.question_key, model=request.get("model"))
        try:
            return self.guarded.send(request)
        except ProviderError as e:
            if sent_of(e)[0] == "no":  # nothing left the process: the count is taken back (Codex review 11-01)
                self.gate.usage.add("a", -1, question_key=self.question_key, reason="not_sent")
            raise


def _refusing(judge):
    """A refusal before anything is sent reaches the graph as a judge failure (recorded, never retried)."""

    def call(payload, request):
        try:
            return judge(payload, request)
        except (ApprovalRefused, LedgerError) as e:
            raise JudgeError(e.code) from None

    call.answer_contract = judge.answer_contract
    return call


def judges(store, approval_path=None, transport_factory=None, now=_utcnow) -> S.Judges:
    """The live judges of one approval; ``LiveDisabled`` when no approval is set (no transport is made)."""
    path = approval_path or os.environ.get(APPROVAL_ENV)
    if not path:
        raise S.LiveDisabled("a_live_disabled")
    if store is None:
        raise ApprovalRefused("run_record_required")
    gate = gate_for(store, None, approval_path=path, now=now)
    if (code := gate.usage.halted(CREDENTIAL)) is not None:
        raise ApprovalRefused("credential_halted", code)
    archive = Archive(gate.usage.folder / "calls.jsonl")

    def bind(ledger, question_key):
        if transport_factory is None:
            from claview_a.transport.provider import OpenAITransport

            factory = OpenAITransport
        else:
            factory = transport_factory
        transport = GatedTransport(factory(), gate, ledger, question_key)
        ref = {"case_id": question_key[:12]}
        context = gate.allowance_context if "allowance" in gate.approval else None
        first = LiveJudge(transport, 1, archive, ref, ledger, "a_first", CREDENTIAL, approval_ref=gate.approval_id,
                          allowance=context)
        research = LiveJudge(transport, 1, archive, {**ref, "round": 2}, ledger, "a_self_rejudge", CREDENTIAL,
                             approval_ref=gate.approval_id, allowance=context)
        return _refusing(first), _refusing(research)

    return S.Judges(None, None, "live", bind=bind, admit=gate.check, rejudge_ok=gate.rejudge_ok, gate=gate,
                    open_ledger=open_ledger)


def _approval_digest(path: Path, approval: dict) -> str:
    """Bind the validated approval to exactly the bytes read, before writing or opening a live gate."""
    raw = path.read_bytes()
    try:
        same = json.loads(raw) == approval
    except (ValueError, UnicodeError):
        same = False
    if not same:
        raise ApprovalRefused("approval_changed", "approval changed while being read")
    return hashlib.sha256(raw).hexdigest()


def _allowance_identity(approval: dict, digest: str | None) -> dict:
    """The immutable batch description. Duplicate question keys are refused before any write."""
    keys = [S.key_of(q) for q in approval["questions"]]
    if len(keys) != len(set(keys)):
        raise ApprovalRefused("approval_invalid", "duplicate allowance questions")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ApprovalRefused("approval_changed", "allowance approval hash missing")
    return {"version": ALLOWANCE_BATCH_VERSION, "approval_id": approval["approval_id"],
            "question_keys": sorted(keys), "approval_sha256": digest,
            "slots": copy.deepcopy(approval["allowance"]["slots"]),
            "versions": copy.deepcopy(approval["conditions"]["versions"]),
            "valid_until": approval["valid_until"], "approved_by": approval["approved_by"],
            "approved_at": approval["approved_at"]}


def _allowance_batch_path(approval: dict) -> Path:
    return live_root() / "a_approvals" / approval["approval_id"] / "allowance_batch.json"


def allowance_batch_status(approval: dict, approval_sha256: str | None) -> str:
    """Read-only batch status: missing, pending or committed. A different or damaged identity is refused."""
    expected = _allowance_identity(approval, approval_sha256)
    path = _allowance_batch_path(approval)
    if not path.exists():
        return "missing"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ApprovalRefused("approval_changed", "allowance batch is damaged") from None
    if not isinstance(record, dict) or record.get("status") not in ("pending", "committed") or \
            {k: v for k, v in record.items() if k != "status"} != expected:
        raise ApprovalRefused("approval_changed", "allowance batch differs")
    return record["status"]


def _write_allowance_batch(path: Path, identity: dict, status: str) -> None:
    """Atomically expose a complete batch state, leaving any earlier state intact on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump({**identity, "status": status}, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _matching_allowance(ledger: Ledger, identity: dict) -> bool:
    events = [e for e in ledger.events if e["kind"] == "allow" and e["allowance_id"] == identity["approval_id"]]
    if not events:
        return False
    fields = ("slots", "versions", "valid_until", "approved_by", "approved_at", "approval_sha256")
    if len(events) != 1 or any(events[0].get(k) != identity[k] for k in fields):
        raise ApprovalRefused("approval_changed", "allowance ledger differs")
    return True


def grant_allowance(approval_path, now=_utcnow) -> list[str]:
    """Prepare all approved question grants, then activate them with one atomic committed marker.

    A failed preparation may leave append-only allow events, but no gate can use them before the batch commits.
    Retrying exactly the same approval validates existing events and appends only missing ones. A completed grant
    is a no-op, including after a slot is spent. No earlier event is removed or refilled.
    """
    path = Path(approval_path)
    approval = load_approval(path, now)
    if "allowance" not in approval:
        raise ApprovalRefused("approval_invalid", "no allowance")
    if not (isinstance(approval.get("approved_by"), str) and approval["approved_by"].strip()
            and isinstance(approval.get("approved_at"), str)):
        raise ApprovalRefused("approval_not_approved", "approved_by and approved_at")
    if Path(approval["ledger_root"]).resolve() != live_root():
        raise ApprovalRefused("ledger_root_mismatch")
    digest = _approval_digest(path, approval)
    identity = _allowance_identity(approval, digest)
    keys = [S.key_of(q) for q in approval["questions"]]
    with _LOCK:
        status = allowance_batch_status(approval, digest)
        missing = []
        for key in keys:  # validate every existing grant before appending anything
            file = live_root() / "a_questions" / key / "ledger.json"
            if not file.exists():
                missing.append(key)
            else:
                with Ledger.open(file, key) as led:
                    if not _matching_allowance(led, identity):
                        missing.append(key)
        if status == "committed":
            if missing:
                raise ApprovalRefused("approval_changed", "committed allowance is missing ledger events")
            return keys
        batch = _allowance_batch_path(approval)
        if status == "missing":
            _write_allowance_batch(batch, identity, "pending")
        for key in missing:
            with open_ledger(key, BudgetPolicy()) as led:
                if not _matching_allowance(led, identity):
                    led.grant(approval["approval_id"], dict(approval["allowance"]["slots"]),
                              approval["conditions"]["versions"], approval["valid_until"], approval["approved_by"],
                              approval["approved_at"], digest)
        if _approval_digest(path, approval) != digest:
            raise ApprovalRefused("approval_changed", "allowance approval bytes changed")
        if now() >= datetime.fromisoformat(approval["valid_until"]):
            raise ApprovalRefused("approval_expired", approval["valid_until"])
        _write_allowance_batch(batch, identity, "committed")
    return keys


def gate_for(store, approval_id: str | None, approval_path=None, now=_utcnow) -> Gate:
    """The gate of the approval set now; with ``approval_id`` (a resumed run) it must be the same approval."""
    path = approval_path or os.environ.get(APPROVAL_ENV)
    if not path:
        raise S.LiveDisabled("a_live_disabled")
    approval = load_approval(path, now)
    if approval_id is not None and approval["approval_id"] != approval_id:
        raise ApprovalRefused("approval_changed", approval["approval_id"])
    if Path(approval["ledger_root"]).resolve() != live_root():
        raise ApprovalRefused("ledger_root_mismatch")
    if Path(approval["run_root"]).resolve() != Path(store.root).resolve():  # another folder never starts anew
        raise ApprovalRefused("run_root_mismatch")
    return Gate(approval, Usage(live_root() / "a_approvals" / approval["approval_id"]), now,
                approval_sha256=_approval_digest(Path(path), approval))
