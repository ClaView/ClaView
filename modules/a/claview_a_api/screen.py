"""Module A's screen service for the new A (plan 54): question -> AView, and answers -> AView.

The pipeline's screen path calls only this for A; it never falls back to the old A (``claview_a_legacy``).

- ``select`` runs the official entry ``claview_a.run`` with the run profile given explicitly (first judgment
  ``gpt-6.1-terra``, one re-search with a re-judgment by ``gpt-6.1-sol``; the entry's default model is not used),
  then returns the AView and the server-held checkpoint.
- ``answer`` applies the user's replies to the checkpoint: basis-axis answers through ``claview_a.answers`` (code
  checks again, no model call), period answers only kept as given (raw text, statistics, source) for a later period
  policy. The original question, ``as_of`` and the run info are kept; every applied batch raises the session's
  question version, so a stale, repeated or forged reply is refused before anything changes.

Judges: ``synthetic_judges`` (wiring only) or a caller's own. ``live_judges`` refuses: the screen path makes no model
call until it is wired to the evaluator's call gate (approved manifest, call limit), so it has no transport of its own.
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
from claview_a.contract import Settings
from claview_a.entry import default_layer
from claview_a.judges import FakeJudge

from . import view as V

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
ANSWER_ERRORS = AN.ERRORS + ("session_mismatch", "text_not_allowed", "text_invalid", "option_or_text_required")


class AnswerRejected(ValueError):
    """A reply batch that is not applied; ``code`` names why. The checkpoint is unchanged."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class LiveDisabled(RuntimeError):
    """The screen path's live model calls are off (no approved call gate is wired to it)."""


class _MemoryArchive:
    """The judges' call rows kept in memory (the screen path writes no call file of its own)."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, row: dict) -> None:
        self.rows.append(row)


@dataclass
class Judges:
    """The first judge and the re-judge, each ``judge(payload, request) -> text``. ``kind`` names what they are
    (``synthetic``, ``script``, ...); ``requests`` keeps the model name of every request they were asked."""

    first: Callable
    research: Callable
    kind: str
    requests: list[str] = field(default_factory=list)

    def counted(self) -> tuple[Callable, Callable]:
        def wrap(judge):
            def call(payload, request):
                self.requests.append(request["model"])
                return judge(payload, request)

            return call

        return wrap(self.first), wrap(self.research)


def synthetic_judges() -> Judges:
    """The fake judge for both rounds (a fixed answer from the first card; says nothing about a model)."""
    archive = _MemoryArchive()
    return Judges(
        FakeJudge(archive, {"case_id": "screen"}), FakeJudge(archive, {"case_id": "screen-research"}), "synthetic"
    )


def live_judges() -> Judges:
    raise LiveDisabled("a_live_disabled")


def settings() -> tuple[Settings, Settings]:
    return Settings(**PROFILE["first"]), Settings(**PROFILE["research"])


class _OneRejudge:
    """The run's re-judge budget: ``max_rejudge_calls`` second calls per question, then refused."""

    def __init__(self, limit: int):
        self.left = limit

    def __call__(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def layer():
    return default_layer()


def select(query: str, as_of: str, judges: Judges, period_questions: Callable | None = None) -> dict:
    """{view, checkpoint} of one question. ``period_questions(view) -> [question]``: a test supplier's period
    questions (marked synthetic); None for the real path (A makes none today)."""
    first, research = settings()
    judge, rejudge = judges.counted()
    state = claview_a.run(
        query,
        judge,
        as_of=as_of,
        settings=first,
        layer=layer(),
        research=PROFILE["research_enabled"],
        research_budget=_OneRejudge(PROFILE["max_rejudge_calls"]),
        research_judge=rejudge,
        research_settings=research,
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
        "period": {"version": 0, "questions": [], "answers": []},
    }
    if period_questions is not None:
        made = period_questions(V.build(checkpoint, layer()))
        checkpoint["period"]["questions"] = [_period_question(q) for q in made]
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


def answer(checkpoint: dict, session_id: str, replies: list[dict]) -> dict:
    """{view, checkpoint} after the replies: ``{question_id, question_version, option_id}`` for a choice, or
    ``{question_id, question_version, text}`` for a period question that takes text. All replies of a batch are
    checked before any is applied."""
    session = checkpoint["session"]
    if session_id != session["session_id"]:
        raise AnswerRejected("session_mismatch")
    if not isinstance(replies, list) or not replies:
        raise AnswerRejected("no_reply")
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
        (a_replies if q["kind"] == "basis_axis" else p_replies).append((q, r))
    new = copy.deepcopy(checkpoint)
    if a_replies:
        try:
            new["a_state"] = AN.apply(
                checkpoint["a_state"],
                [
                    {
                        "question_id": r["question_id"],
                        "question_version": r["question_version"],
                        "option_id": r["option_id"],
                    }
                    for _, r in a_replies
                ],
                layer(),
            )
        except AN.AnswerError as e:
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
    for key in ("question", "as_of", "profile"):
        assert new["session"][key] == checkpoint["session"][key]
    new["session"]["question_version"] = session["question_version"] + 1
    return result(new)


def judges_for(mode: str) -> Judges:
    """The judges of a pipeline run: synthetic in synthetic mode; live is refused (``LiveDisabled``)."""
    if mode == "live":
        return live_judges()
    return synthetic_judges()
