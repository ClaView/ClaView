"""Module A's screen service for the new A (plan 54): question -> AView, and answers -> AView.

The pipeline's screen path calls only this for A; it never falls back to the old A (``claview_a_legacy``).

- ``select`` runs the official entry ``claview_a.run`` with the run profile given explicitly (first judgment
  ``gpt-6.1-terra``, one re-search with a re-judgment by ``gpt-6.1-sol``; the entry's default model is not used),
  then returns the AView and the server-held checkpoint.
- ``answer`` applies the user's replies to the checkpoint: basis-axis answers through ``claview_a.answers`` (code
  checks again, no model call), period answers only kept as given (raw text, statistics, source) for a later period
  policy. The original question, ``as_of`` and the run info are kept; every applied batch raises the session's
  question version, so a stale, repeated or forged reply is refused before anything changes.

Judges: ``synthetic_judges`` (wiring only) or a caller's own. ``live_judges`` is off by default (``LiveDisabled``, no
transport made): only an approval record named by ``CLAVIEW_A_LIVE_APPROVAL`` opens it, within that record's questions
and limits, with the run records on (``live_gate``, plan 56 order 13-1).
Period questions are not made by A today: a caller may hand in a supplier (tests and the screen demo only), and its
questions are marked ``synthetic``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

import claview_a
from claview_a import answers as AN
from claview_a.budget import BudgetPolicy, Ledger, key_of
from claview_a.contract import Settings
from claview_a.entry import default_layer
from claview_a.graph import JudgeError
from claview_a.judges import FakeJudge, text_sha

from . import view as V
from .run_record import RunRecordError, RunStore

SCREEN_VERSION = "a-screen/2026-10-30.1"
PROFILE_NAME = "terra-first-sol-research-1"
# The settings the 55-question measurement verified (A measurement doc 2026-10-29): Terra first, Sol for the one
# re-judgment after a re-search; effort low, 4096 output tokens, no retry.
PROFILE = {
    "first": {"model": "gpt-6.1-terra", "effort": "low", "max_output_tokens": 4096},
    "research": {"model": "gpt-6.1-sol", "effort": "low", "max_output_tokens": 4096},
    "research_enabled": True,
    "max_rejudge_calls": 1,
}
PERIOD_TEXT_MAX = 200
ANSWER_ERRORS = AN.ERRORS + ("session_mismatch", "text_not_allowed", "text_invalid", "option_or_text_required",
                             "duplicate_batch")


class AnswerRejected(ValueError):
    """A reply batch that is not applied; ``code`` names why. The checkpoint is unchanged."""

    def __init__(self, code: str, cause: str | None = None):
        super().__init__(code)
        self.code, self.cause = code, cause


class LiveDisabled(RuntimeError):
    """The screen path's live model calls are off (no approval record is set)."""


class _MemoryArchive:
    """The judges' call rows kept in memory (the screen path writes no call file of its own)."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, row: dict) -> None:
        self.rows.append(row)


@dataclass
class Judges:
    """The first judge and the re-judge, each ``judge(payload, request) -> text``. ``kind`` names what they are
    (``synthetic``, ``script``, ...); ``requests`` keeps the model name of every request they were asked. With the
    question's ledger (plan 56 order 3) every call is reserved right before it (``a_first``, ``a_self_rejudge``) and
    settled right after; none of these judges sends anything (``sent: no``).

    Live judges (``live_gate``, plan 56 order 13-1) reserve and settle on the ledger themselves: ``bind(ledger,
    question_key) -> (first, research)`` makes them for one question; ``admit(question_key, ledger)`` refuses before
    anything is reserved or sent; ``rejudge_ok(question_key, ledger)`` is asked before the one re-judgment."""

    first: Callable | None
    research: Callable | None
    kind: str
    requests: list[str] = field(default_factory=list)
    bind: Callable | None = None
    admit: Callable | None = None
    rejudge_ok: Callable | None = None
    gate: object | None = None
    # open_ledger(question_key, policy): the question's ledger of these judges (live: the fixed live ledger; others:
    # the run records' own, ``RunStore.ledger``)
    open_ledger: Callable | None = None

    def counted(self, ledger: Ledger | None = None, question_key: str | None = None) -> tuple[Callable, Callable]:
        if self.bind is not None:
            first, research = self.bind(ledger, question_key)
            return self._noted(first), self._noted(research)

        def wrap(judge, slot):
            def call(payload, request):
                attempt = ledger.reserve(slot, request["model"], text_sha(request)) if ledger is not None else None
                self.requests.append(request["model"])
                try:
                    text = judge(payload, request)
                except JudgeError:
                    if attempt is not None:
                        ledger.settle(attempt, "no", "failed", billing="none_reported")
                    raise
                if attempt is not None:
                    ledger.settle(attempt, "no", self.kind, billing="none_reported")
                return text

            call.answer_contract = getattr(judge, "answer_contract", None)  # the inner judge's contract goes through
            return call

        return wrap(self.first, "a_first"), wrap(self.research, "a_self_rejudge")

    def _noted(self, judge):
        def call(payload, request):
            self.requests.append(request["model"])
            return judge(payload, request)

        call.answer_contract = getattr(judge, "answer_contract", None)
        return call


def synthetic_judges() -> Judges:
    """The fake judge for both rounds (a fixed answer from the first card; says nothing about a model)."""
    archive = _MemoryArchive()
    return Judges(
        FakeJudge(archive, {"case_id": "screen"}), FakeJudge(archive, {"case_id": "screen-research"}), "synthetic"
    )


def live_judges(store: RunStore | None = None, **kwargs) -> Judges:
    """The approved live judges (``live_gate.judges``); ``LiveDisabled`` when no approval is set."""
    from . import live_gate

    return live_gate.judges(store, **kwargs)


def settings() -> tuple[Settings, Settings]:
    return Settings(**PROFILE["first"]), Settings(**PROFILE["research"])


class _OneRejudge:
    """The run's re-judge budget: ``max_rejudge_calls`` second calls per question, then refused (kept beside the
    ledger: the ledger refuses a second re-judgment again right before it would be sent)."""

    def __init__(self, limit: int):
        self.left = limit

    def __call__(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def ledger_for(query: str) -> Ledger:
    """The question's call ledger, in memory until bundle 9 makes the original question durable: the C9 limits, the
    re-judgments the profile allows (``max_rejudge_calls``)."""
    return Ledger.memory(key_of(query), BudgetPolicy(a_self_rejudge=PROFILE["max_rejudge_calls"]))


class AllowanceLedger:
    """The question's live ledger as a re-measurement under an allowance reads it (owner 11-02): a slot is available
    when the allowance's own slot is (``Ledger.allowance_refusal``), never the service's slot; everything else is the
    ledger itself."""

    def __init__(self, ledger: Ledger, context: Callable[[], dict]):
        self.ledger, self.context = ledger, context

    def available(self, slot: str) -> bool:
        return self.ledger.allowance_refusal(slot, self.context()) is None

    def __getattr__(self, name):
        return getattr(self.ledger, name)


def layer():
    return default_layer()


def select(query: str, as_of: str, judges: Judges, period_questions: Callable | None = None,
           store: RunStore | None = None, lineage: dict | None = None) -> dict:
    """{view, checkpoint} of one question. ``period_questions(view) -> [question]``: a test supplier's period
    questions (marked synthetic); None for the real path (A makes none today). ``store`` (plan 56 order 10): the run
    records; the original question's ledger is on disk and shared by every session of it, so a new session or a
    restart never resets its budget. ``lineage`` ``{session_id}``: the previous session of the same question (a
    reworded one included); its record must be there, else nothing is called (``RunRecordError``)."""
    if store is None:
        if judges.bind is not None:  # a live call never goes without the run records (C9)
            from .live_gate import ApprovalRefused

            raise ApprovalRefused("run_record_required")
        return _select(query, as_of, judges, period_questions, ledger_for(query), None, None)
    question_key = store.question_key(lineage["session_id"], SCREEN_VERSION) if lineage else key_of(query)
    opener = judges.open_ledger or store.ledger
    context = getattr(judges.gate, "allowance_context", None) if judges.gate is not None else None
    allowance = context is not None and context() is not None
    with opener(question_key, BudgetPolicy(a_self_rejudge=PROFILE["max_rejudge_calls"])) as opened:
        # a re-measurement under an allowance reads the allowance's slots (the gate refuses before anything is sent);
        # every other run reads the service's
        ledger = AllowanceLedger(opened, context) if allowance else opened
        if not allowance and not ledger.available("a_first"):
            # the original question's first judgment is used (C9): a new session never resets it; the earlier
            # sessions are named for the screen to read back, and nothing is called
            raise RunRecordError("question_budget_used", ",".join(store.sessions_of(question_key)))
        if judges.admit is not None:  # the approval's refusals, before anything is reserved or sent (order 13-1)
            judges.admit(question_key, ledger)
        out = _select(query, as_of, judges, period_questions, ledger, question_key, lineage)
        out["checkpoint"]["runs"] = [{"change_kind": "initial", "question_version": 0,
                                      "parent_session_id": (lineage or {}).get("session_id")}]
        store.append_calls(out["checkpoint"]["session"]["session_id"], ledger.attempts())
    store.save(out["checkpoint"], out["view"])
    store.add_session(question_key, out["checkpoint"]["session"]["session_id"])
    return out


def _select(query, as_of, judges, period_questions, ledger, question_key, lineage) -> dict:
    first, research = settings()
    judge, rejudge = judges.counted(ledger, question_key)
    one = _OneRejudge(PROFILE["max_rejudge_calls"])
    budget = one if judges.rejudge_ok is None else (lambda: judges.rejudge_ok(question_key, ledger) and one())
    state = claview_a.run(
        query,
        judge,
        as_of=as_of,
        settings=first,
        layer=layer(),
        research=PROFILE["research_enabled"],
        research_budget=budget,
        research_judge=rejudge,
        research_settings=research,
        ledger=ledger,
    )
    checkpoint = {
        "contract": SCREEN_VERSION,
        "judges": judges.kind,
        "session": {
            "session_id": uuid4().hex,
            "question_version": 0,
            "question": query,
            "as_of": as_of,
            "profile": PROFILE_NAME,
        },
        "a_state": state,
        "a_ledger": ledger.summary(),
        "question_key": question_key or key_of(query),
        "lineage": lineage,
        # the approval a live run went under and its refusals (plan 56 order 13-1); None for other judges
        "a_gate": None if judges.gate is None else {"approval_id": judges.gate.approval_id,
                                                   "refusals": list(judges.gate.refusals)},
        "period": {"version": 0, "questions": [], "answers": []},
    }
    if period_questions is not None:
        made = period_questions(V.build(checkpoint, layer()))
        checkpoint["period"]["questions"] = [_period_question(q) for q in made]
    checkpoint["initial_period"] = copy.deepcopy(checkpoint["period"])
    return result(checkpoint)


def result(checkpoint: dict) -> dict:
    return {"view": V.build(checkpoint, layer()), "checkpoint": checkpoint}


def _period_question(q: dict) -> dict:
    return {
        "question_id": q["question_id"],
        "question_version": 0,
        "kind": "period",
        "statistics": list(q["statistics"]),
        "axis": None,
        "axis_label": None,
        "text": q["text"],
        "input": q.get("input", "choice_or_text"),
        "options": [
            {
                "option_id": o["option_id"],
                "label": o["label"],
                "value": o["value"],
                "table_id": None,
                "table_name": None,
            }
            for o in q.get("options") or []
        ],
        "synthetic": True,
    }


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def count_external(store: RunStore, checkpoint: dict, view: dict | None, b_model: int, kosis: int,
                   open_ledger: Callable | None = None) -> None:
    """B's model calls and KOSIS fetches of one B request (plan 56 order 13-1): added to the original question's
    ledger (``count_external``, no limit there; ``open_ledger``: a live run's live ledger) and to the session's
    record (``external_counts``)."""
    if not (b_model or kosis):
        return
    opener = open_ledger or store.ledger
    with opener(checkpoint["question_key"], BudgetPolicy(a_self_rejudge=PROFILE["max_rejudge_calls"])) as ledger:
        for what, n in (("b_model", b_model), ("kosis", kosis)):
            if n:
                ledger.count_external(what, n)
    counts = checkpoint.setdefault("external_counts", {"B_model": 0, "KOSIS": 0})
    counts["B_model"] += b_model
    counts["KOSIS"] += kosis
    store.save(checkpoint, view)


def load(store: RunStore, session_id: str) -> dict:
    """{view, checkpoint} of a recorded session (a restart, a new screen process): nothing is called; a missing,
    broken or other-version record raises ``RunRecordError``."""
    return result(store.load(session_id, SCREEN_VERSION))


def answer(checkpoint: dict, session_id: str, replies: list[dict], store: RunStore | None = None) -> dict:
    """``store``: the replies are checked against the session's stored record, not the copy handed in, then applied
    and saved, all under the session's lock (Codex review 11-01: a copy loaded twice, or an older job, can never
    apply a stale or repeated batch); without a record of the session the copy is used (plan 56 order 10)."""
    if store is None:
        return _answer(checkpoint, session_id, replies)
    sid = checkpoint["session"]["session_id"]
    with store.locked(sid):
        try:
            latest = store.load(sid, SCREEN_VERSION)
        except RunRecordError as e:
            if e.code != "run_record_missing":
                raise
            latest = checkpoint
        out = _answer(latest, session_id, replies)
        out["checkpoint"].setdefault("runs", []).append(
            {"change_kind": "user_answer", "question_version": out["checkpoint"]["session"]["question_version"]})
        store.save(out["checkpoint"], out["view"], held=True)
    return out


def latest(store: RunStore | None, checkpoint: dict) -> dict:
    """The session's stored checkpoint (a copy of the record), else the one given: what a reply is checked against."""
    if store is None:
        return checkpoint
    try:
        return store.load(checkpoint["session"]["session_id"], SCREEN_VERSION)
    except RunRecordError:
        return checkpoint


def _answer(checkpoint: dict, session_id: str, replies: list[dict]) -> dict:
    """{view, checkpoint} after the replies: ``{question_id, question_version, option_id}`` for a choice, or
    ``{question_id, question_version, text}`` for a period question that takes text. All replies of a batch are
    checked before any is applied."""
    session = checkpoint["session"]
    if session_id != session["session_id"]:
        raise AnswerRejected("session_mismatch")
    if not isinstance(replies, list) or not replies:
        raise AnswerRejected("no_reply")
    key = batch_key(session_id, session["question_version"], replies)
    if key in (checkpoint.get("applied_batches") or []):  # the same batch again: refused, nothing changes
        raise AnswerRejected("duplicate_batch")
    pending = {q["question_id"]: q for q in V.build(checkpoint, layer())["questions"]}
    a_replies, p_replies, seen = [], [], set()
    for r in replies:
        if (
            not isinstance(r, dict)
            or not {"question_id", "question_version"} <= set(r)
            or set(r) - {"question_id", "question_version", "option_id", "text"}
        ):
            raise AnswerRejected("reply_shape")
        if r["question_id"] in seen:
            raise AnswerRejected("duplicate_reply")
        seen.add(r["question_id"])
        q = pending.get(r["question_id"])
        if q is None:
            raise AnswerRejected("question_not_pending")
        if type(r["question_version"]) is not int or r["question_version"] != q["question_version"]:
            raise AnswerRejected("stale_question")
        has_option, has_text = r.get("option_id") is not None, r.get("text") is not None
        if has_option == has_text:
            raise AnswerRejected("option_or_text_required")
        if has_text and q["input"] == "choice":
            raise AnswerRejected("text_not_allowed")
        if has_option and not any(o["option_id"] == r["option_id"] for o in q["options"]):
            raise AnswerRejected("option_not_offered")
        if has_text and (
            not isinstance(r["text"], str)
            or not r["text"].strip()
            or len(r["text"]) > PERIOD_TEXT_MAX
            or _CONTROL.search(r["text"])
        ):
            raise AnswerRejected("text_invalid")
        # A's own questions (basis axis, period, frequency) go to A; a test supplier's questions are kept as given
        (p_replies if q.get("synthetic") else a_replies).append((q, r))
    new = copy.deepcopy(checkpoint)
    if a_replies:
        try:
            new["a_state"] = AN.apply(
                checkpoint["a_state"],
                [
                    {
                        "question_id": r["question_id"],
                        "question_version": AN.question_version(checkpoint["a_state"]),  # A's own counter
                        **({"text": r["text"]} if r.get("text") is not None else {"option_id": r["option_id"]}),
                    }
                    for _, r in a_replies
                ],
                layer(),
            )
        except AN.AnswerError as e:
            if e.code == "answer_unreadable":  # a typed period A cannot read: refused with why (bundle 9)
                raise AnswerRejected("text_invalid", e.cause) from None
            raise AnswerRejected(e.code) from None
    if p_replies:
        period = new["period"]
        for q, r in p_replies:
            option = next((o for o in q["options"] if o["option_id"] == r.get("option_id")), None)
            period["answers"].append(
                {
                    "question_id": q["question_id"],
                    "question_version": q["question_version"],
                    "statistics": list(q["statistics"]),
                    "kind": "period",
                    "option_id": option and option["option_id"],
                    "value": option["value"] if option else r["text"],
                    "text": r.get("text"),
                    "table_id": None,
                    "source": "user_answer",
                }
            )
            period["questions"] = [x for x in period["questions"] if x["question_id"] != q["question_id"]]
        period["version"] += 1
        for x in period["questions"]:
            x["question_version"] = period["version"]
    for k in ("question", "as_of", "profile"):
        assert new["session"][k] == checkpoint["session"][k]
    new["session"]["question_version"] = session["question_version"] + 1
    new["applied_batches"] = [*(checkpoint.get("applied_batches") or []), key]
    return result(new)


def batch_key(session_id: str, question_version: int, replies) -> str:
    """The idempotency key of a reply batch: (session, question version, the batch's canonical hash)."""
    import hashlib
    import json

    body = json.dumps(replies, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{session_id}|{question_version}|{body}".encode()).hexdigest()


def reset(checkpoint: dict, session_id: str) -> dict:
    """{view, checkpoint} with every answer taken back (plan 56 order 10, minimal reset): A's state as the run left
    it and the period supplier's questions again; the question version still rises (an old reply stays stale) and
    the call ledger is never taken back."""
    session = checkpoint["session"]
    if session_id != session["session_id"]:
        raise AnswerRejected("session_mismatch")
    new = copy.deepcopy(checkpoint)
    state = new["a_state"]
    if "record_before_answers" in state:
        state["record"] = state.pop("record_before_answers")
        state.pop("answer_log", None)
        state["action"] = state["record"]["route"]["action"]
    initial = new.get("initial_period")
    if initial is not None:
        new["period"] = copy.deepcopy(initial)
    new["session"]["question_version"] = session["question_version"] + 1
    new.setdefault("runs", []).append({"change_kind": "user_condition_change", "what": "reset",
                                       "question_version": new["session"]["question_version"]})
    return result(new)


def judges_for(mode: str, store: RunStore | None = None) -> Judges:
    """The judges of a pipeline run: synthetic in synthetic mode; live only with an approval record (``LiveDisabled``
    when none is set)."""
    if mode == "live":
        return live_judges(store)
    return synthetic_judges()
