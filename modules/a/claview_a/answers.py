"""Answers to the new A's basis-axis questions, applied by code (plan 54).

A statistic the check left as ``ask`` (one or more ask axes, each with option tables the model gave its own evidence
for) is shown as one question per axis (``questions``). ``apply`` takes the user's option for one or more questions and
changes only the statistics they are for: the asked axis takes the chosen value (source ``user_answer``), and every
other question and statistic stays as it was. When every axis of a statistic is answered, the option table that holds
every answered value (and has the model's valid evidence) becomes the statistic's table, and the checks the check runs
on a decided table run again on it with the same functions and in the same order (``check.verify_table``, the period
and frequency candidates ``check._candidates`` / ``check._select_periods``, the final recheck, ``check.route``). No
model is called, no table is chosen by any other rule, code asks nothing new, and a failed check is a failure, never
passed. Nothing is answered for an ``ask_unverified`` statistic (its card evidence is not verified).

The question, ``as_of``, the pool, the cards, the model's output and the run info are kept unchanged; the record before
the first answer is kept as ``record_before_answers``. Each applied answer raises the question version by one, so an
answer to an older version, to a question that is no longer pending or with an option that was not offered is refused
(``AnswerError``) before anything changes.

Condition questions (plan 56 order 5, policy ②④): a statistic the check left as ``ask_condition`` has a period or
frequency question (``condition_asks``), one per kind for the statistics it names. The answer is an offered option or,
for a period question that takes text, the typed period, read by the period reader before anything changes (a text
it cannot read is refused, ``answer_unreadable``). The answer is kept as a quote of those statistics
(``condition_answers``) and the period checks run again on their decided tables (``check.decide_periods`` with the
same reading as the check); the other condition stays as decided. No model is called.

Nothing here names a table, case or question.
"""

from __future__ import annotations

import copy

from .search.tables import selection_catalog

from . import check as CH
from . import periods as PR
from .cards import ASK_AXES
from .periods.contract import check_conditions

ANSWERS_VERSION = "tl-answers-2026-11-02.5"  # .5: catalog-relative previews and user answer intent stay together
ANSWER_SOURCE = "user_answer"
ERRORS = ("no_reply", "reply_shape", "stale_question", "duplicate_reply", "question_not_pending",
          "option_not_offered", "no_record", "answer_unreadable")
CONDITION_KINDS = ("period", "frequency")


class AnswerError(ValueError):
    """A reply that cannot be applied; ``code`` is one of ``ERRORS``. The state is left unchanged."""

    def __init__(self, code: str, cause: str | None = None):
        if code not in ERRORS:
            raise ValueError(f"unknown answer error {code!r}")
        super().__init__(code)
        self.code, self.cause = code, cause  # cause: why a typed answer is not read (plan 56 order 10)


def question_version(state: dict) -> int:
    """The version the next reply must name: 0 before any answer, one more after each applied reply batch."""
    return (state.get("answer_log") or {}).get("question_version", 0)


def question_id(statistic: str, axis: str) -> str:
    return f"q:{statistic}:{axis}"


def condition_question_id(statistics: list[str], kind: str) -> str:
    return f"q:{'+'.join(statistics)}:{kind}"


def questions(state: dict) -> list[dict]:
    """The pending questions: one per ask axis of every statistic whose outcome is ``ask``, in record order, with the
    options as the check left them (value and option table); then one per condition question of the statistics whose
    outcome is ``ask_condition`` (kind ``period`` or ``frequency``, once for the statistics it names)."""
    out = []
    seen: set[str] = set()
    for d in (state.get("record") or {}).get("decisions") or []:
        if d["outcome"] == CH.CONDITION_OUTCOME:
            for a in d.get("condition_asks") or []:
                qid = condition_question_id(a["statistics"], a["kind"])
                if qid in seen:
                    continue
                seen.add(qid)
                out.append({"question_id": qid, "kind": a["kind"], "statistics": list(a["statistics"]),
                            "statistic": a["statistics"][0], "cause": a["cause"], "input": a["input"],
                            **({"partial_periods": copy.deepcopy(a["partial_periods"])}
                               if a.get("partial_periods") else {}),
                            "options": [{"option_id": f"{qid}:{o['option_id']}", "value": o["value"],
                                         "label": o["label"], "quote": o["quote"], "range": o.get("range"),
                                         **({"ranges": copy.deepcopy(o["ranges"])} if o.get("ranges") else {}),
                                         "table_id": None} for o in a["options"]]})
            out += _basis_questions(d, "condition")  # the basis asks that do not wait for the answer (⑥)
            continue
        if d["outcome"] != "ask":
            continue
        out += _basis_questions(d)
    return out


def _basis_questions(d: dict, screen: str = "ask") -> list[dict]:
    """One question per basis ask of ``d`` (plan 56 order 8, ⑥): the options as the check left them, an unsupported
    one never shown, an unconfirmed one shown as needing a check and not selectable (``selectable``). With a condition
    question pending (``screen`` condition), only the asks that do not wait for it (``after`` empty)."""
    sid, out = d["statistic"], []
    for a in d["asks"]:
        if screen == "condition" and a.get("after", ["period"]):
            continue
        qid = question_id(sid, a["axis"])
        out.append({"question_id": qid, "kind": "basis_axis", "statistic": sid, "axis": a["axis"],
                    "axis_label": ASK_AXES.get(a["axis"], a["axis"]), "source": a["source"],
                    "options": [{"option_id": f"{qid}:o{i}", "value": o["value"], "table_id": o["table_id"],
                                 "status": o.get("status", "supported"), "selectable": o.get("selectable", True)}
                                for i, o in enumerate(a["options"], start=1)
                                if o.get("status", "supported") != "unsupported"]})
    return out


def _answer_partial_problem(state: dict, q: dict, conditions: list[dict], layer) -> str | None:
    """Check an incomplete explicit boundary against the actual catalog-relative window before applying it."""
    partials = q.get("partial_periods") or []
    if not partials or not PR.data_recent(conditions):
        return CH.partial_period_problem(partials, conditions, state["as_of"])
    lk = CH.Cards(state["cards"], selection_catalog(layer).views)
    decisions = {d["statistic"]: d for d in state["record"]["decisions"]}
    for sid in q["statistics"]:
        d = decisions[sid]
        rep = d["representative"]
        if rep is None:
            return "partial_period_reference_unresolved"
        rows = {r["table_id"]: r for r in lk.card_of[rep]["tables"]}
        candidates = CH._candidates(rows, rep)
        frequency = d.get("frequency") or {}
        frequencies = [frequency["value"]] if frequency.get("value") else frequency.get("valid", [])
        if not frequencies:
            return "partial_period_reference_unresolved"
        for f in frequencies:
            reading = CH._with_frequency({"conditions": conditions}, f)
            got = CH._select_periods(d["quote"], rep, candidates, d["item_evidence"], reading, lk, state["as_of"],
                                     "answer_boundary", d["retention"])
            if got["status"] != "selected":
                return "partial_period_reference_unresolved"
            spans = [r for t in got["tables"] for r in t.get("period") or []]
            if why := CH.partial_period_problem(partials, conditions, state["as_of"], spans=spans):
                return why
    return None


def _validate(state: dict, replies, layer) -> list[tuple[dict, dict]]:
    if not (state.get("record") or {}).get("decisions"):
        raise AnswerError("no_record")
    if not isinstance(replies, list) or not replies:
        raise AnswerError("no_reply")
    current = question_version(state)
    pending = {q["question_id"]: q for q in questions(state)}
    seen: set[str] = set()
    out = []
    for r in replies:
        if not isinstance(r, dict) or set(r) not in ({"question_id", "question_version", "option_id"},
                                                     {"question_id", "question_version", "text"}):
            raise AnswerError("reply_shape")
        if type(r["question_version"]) is not int or r["question_version"] != current:
            raise AnswerError("stale_question")
        if r["question_id"] in seen:
            raise AnswerError("duplicate_reply")
        seen.add(r["question_id"])
        q = pending.get(r["question_id"])
        if q is None:
            raise AnswerError("question_not_pending")
        if "text" in r:  # a typed period: only where the question takes text, and only what the reader reads
            if q.get("input") != "choice_or_text" or not isinstance(r["text"], str):
                raise AnswerError("reply_shape")
            got = PR.read_quotes([r["text"]], [])
            if got["problems"] or not any(c["kind"] == "period" for c in got["conditions"]):
                raise AnswerError("answer_unreadable",
                                  (got["problems"][0].get("code") if got["problems"] else None) or "no_period_read")
            if check_conditions(got["conditions"]):
                raise AnswerError("answer_unreadable", "malformed_condition")
            if why := _answer_partial_problem(state, q, got["conditions"], layer):
                raise AnswerError("answer_unreadable", why)
            out.append((q, {"option_id": None, "value": r["text"], "quote": r["text"], "text": r["text"]}))
            continue
        option = next((o for o in q["options"] if o["option_id"] == r["option_id"]), None)
        if option is None or option.get("selectable") is False:  # an unconfirmed option is never chosen (⑥)
            raise AnswerError("option_not_offered")
        if q.get("partial_periods"):
            got = PR.read_quotes([option["quote"]], [])
            if why := _answer_partial_problem(state, q, got["conditions"], layer):
                raise AnswerError("answer_unreadable", why)
        out.append((q, option))
    return out


def apply(state: dict, replies: list[dict], layer) -> dict:
    """A new state with the replies applied (the given state is not changed). ``replies``: ``{question_id,
    question_version, option_id}`` each, for pending questions of the current version, at most one per question."""
    checked = _validate(state, replies, layer)
    current = question_version(state)
    new = copy.deepcopy(state)
    if "record_before_answers" not in new:
        new["record_before_answers"] = copy.deepcopy(state["record"])
    record = new["record"]
    log = new.setdefault("answer_log", {"version": ANSWERS_VERSION, "question_version": 0, "rows": []})
    by_sid = {d["statistic"]: d for d in record["decisions"]}
    touched: list[str] = []
    conditioned: list[str] = []
    for q, option in checked:
        if q["kind"] in CONDITION_KINDS:
            row = {"question_id": q["question_id"], "question_version": current, "statistics": list(q["statistics"]),
                   "kind": q["kind"], "option_id": option["option_id"], "value": option["value"],
                   "quote": option["quote"], "text": option.get("text"), "range": option.get("range"),
                   **({"ranges": copy.deepcopy(option["ranges"])} if option.get("ranges") else {}),
                   "source": ANSWER_SOURCE}
            for sid in q["statistics"]:
                d = by_sid[sid]
                d["condition_asks"] = [a for a in d.get("condition_asks") or [] if a["kind"] != q["kind"]]
                # what the answer replaces for this statistic: only that condition (plan 56 order 10, bundle 9)
                before = [a["quote"] for a in d.get("condition_answers") or [] if a["kind"] == q["kind"]] or \
                    [x["quote"] for x in record["model_output"][{"period": "periods", "frequency": "frequencies"}
                                                                [q["kind"]]] if sid in x["for"]]
                row.setdefault("replaces", []).append({"statistic": sid, "condition": q["kind"],
                                                       "from": before or None, "to": option["quote"]})
                d["condition_answers"] = [a for a in d.get("condition_answers") or [] if a["kind"] != q["kind"]]
                d["condition_answers"].append(row)
                conditioned.append(sid)
            log["rows"].append(row)
            continue
        d = by_sid[q["statistic"]]
        ask = next(a for a in d["asks"] if a["axis"] == q["axis"])
        d["asks"].remove(ask)
        row = {"question_id": q["question_id"], "question_version": current, "statistic": q["statistic"],
               "axis": q["axis"], "option_id": option["option_id"], "value": option["value"],
               "table_id": option["table_id"], "source": ANSWER_SOURCE}
        d.setdefault("answered_asks", []).append({**ask, "answer": row})
        d.setdefault("answers", []).append(row)
        d["basis"][q["axis"]] = {"value": [option["value"]], "source": ANSWER_SOURCE, "question_id": q["question_id"]}
        log["rows"].append(row)
        touched.append(q["statistic"])
    done = [sid for sid in dict.fromkeys(conditioned) if not by_sid[sid]["condition_asks"]]
    if any(not by_sid[sid]["asks"] for sid in touched) or done:
        lk = CH.Cards(new["cards"], selection_catalog(layer).views)
        for sid in dict.fromkeys(touched + done):
            d = by_sid[sid]
            if d.get("condition_asks"):  # a period or frequency answer still to come: the basis answer waits
                continue
            if d["asks"]:
                if sid in done:  # the period answered first: the option statuses on it (⑥)
                    _resettle(new, d, lk)
                continue
            if d.get("answered_asks") and d.get("model_kind") != "ask_answered":
                _decide(new, d, lk)
            elif sid in done:
                _decide_conditions(new, d, lk)
    for d in record["decisions"]:  # the stage, warnings and period hints again after the answers (plan 56 order 11)
        CH.annotate(d, record["model_output"])
    record["route"] = CH.route(record)
    new["action"] = record["route"]["action"]
    log["question_version"] = current + 1
    return new


def _fail(d: dict, code: str, detail=None) -> None:
    d["reasons"].append({"code": code, "detail": detail})


def _finish(d: dict) -> None:
    """The outcome by the check's own rule (``check.check``, last loop)."""
    if d["reasons"]:
        d["outcome"] = "failed"
    elif d.get("condition_asks"):  # the period or frequency only the user can give (plan 56 order 5)
        d["outcome"] = CH.CONDITION_OUTCOME
    elif (d.get("frequency") or {}).get("aggregation"):  # kept, to be aggregated (plan 56 order 6-3)
        d["outcome"] = CH.AGGREGATION_OUTCOME
    elif d["final_tables"]:
        d["outcome"] = "selected" if d["citation"]["status"] == "verified" else "selected_unverified"
    else:
        d["outcome"] = "failed"
        d["reasons"].append({"code": "undecided", "detail": None})


def _context(state: dict) -> dict:
    contract = (state["record"].get("parse") or {}).get("answer_contract")
    return {"question": state["record"]["question"], "out": state["record"]["model_output"],
            "polar": contract in CH.POLARITY_CONTRACTS, "semantic": contract in CH.SEMANTIC_PERIOD_CONTRACTS}


def _resettle(state: dict, d: dict, lk: CH.Cards) -> None:
    """The period (or frequency) answered before the basis asks (plan 56 order 8, ⑥): each option's status again on
    the answer (``check.settle_asks``); the basis asks follow, or the one option the five conditions choose takes the
    period checks, or the statistic stops as the check would."""
    ctx = _context(state)
    reading = CH.condition_reading(d, ctx["question"], ctx["out"], ctx["polar"], semantic=ctx["semantic"],
                                   as_of=state["as_of"])
    d["ask_feasibility"] = CH._ask_feasibility(d, d["quote"], reading, lk, state["as_of"])
    got = CH.settle_asks(d, d["quote"], reading, lk, state["as_of"])
    if got:
        _fail(d, *got)
        return _finish(d)
    if d["asks"]:
        for a in d["asks"]:
            a["after"] = []
            a["combinations"] = CH.ask_combinations(d)
        d["outcome"] = "ask"
        return None
    got = CH.verify_table(d["quote"], d["representative"], d["item_evidence"], lk, d["retention"])
    d["verification"].append(CH._trace("answer:auto_decided", got))
    if not got["ok"]:
        _fail(d, got["code"], {"for": "answer:auto_decided", "table": d["representative"]})
        return _finish(d)
    d["final_tables"] = []
    CH.decide_periods(d, d["quote"], d["representative"], reading, lk, state["as_of"], "answer", ctx)
    return _finish(d)


def _decide_conditions(state: dict, d: dict, lk: CH.Cards) -> None:
    """Every condition question of ``d`` answered: the period checks again on its decided table, with the answers read
    as quotes (``check.decide_periods``); the table and basis stay as decided."""
    reading = _answer_reading(state, d)
    d["final_tables"] = []
    CH.decide_periods(d, d["quote"], d["representative"], reading, lk, state["as_of"], "answer", _context(state))
    _finish(d)


def _decide(state: dict, d: dict, lk: CH.Cards) -> None:
    """Every axis of ``d`` answered: the option table holding every answered value, then the checks of a decided
    table (check steps 4 and 5) on it."""
    quote, as_of = d["quote"], state["as_of"]
    answered = {a["axis"]: a["answer"]["value"] for a in d["answered_asks"]}
    options = list(dict.fromkeys(o["table_id"] for a in d["answered_asks"] for o in a["options"]))
    targets = [t for t in options if (d["option_evidence"].get(t) or {}).get("valid")
               and all(v in (lk.row[t]["basis"].get(axis) or []) for axis, v in answered.items())]
    d["model_kind"] = "ask_answered"
    if len(targets) != 1:
        # several axes answered and no single option table with the model's evidence holds every answered value:
        # never a table made up from siblings (the target-table principle of the check)
        _fail(d, "target_table_unconfirmed", {"stage": "answer", "answered": answered, "tables": targets,
                                              "why": "no single option table with model evidence holds every "
                                                     "answered value"})
        return _finish(d)
    t = targets[0]
    evidence = d["option_evidence"][t]
    d["representative"] = t
    d["item_evidence"] = list(evidence.get("items") or [])
    d["retention"] = {"origin": t, "unverified": list(evidence.get("invalid_items") or [])}
    row = lk.row[t]
    for axis in d.get("unasked_axes") or []:
        if axis in row["basis"] and axis not in d["basis"]:
            d["basis"][axis] = {"value": list(row["basis"][axis]), "source": "model_option_table",
                                "note": "not stated by the question nor asked; the answered option table's value"}
    got = CH.verify_table(quote, t, d["item_evidence"], lk, d["retention"])
    d["verification"].append(CH._trace("answer:option_table", got))
    d["checks_done"].append("answer_table")
    if not got["ok"]:
        _fail(d, got["code"], {"for": "answer:option_table", "table": t,
                               **({"detail": got["detail"]} if "detail" in got else {})})
        return _finish(d)
    # 4 and 5. periods, final tables and their recheck: the check's own function (``check.decide_periods``)
    reading = _answer_reading(state, d)
    CH.decide_periods(d, quote, t, reading, lk, as_of, "answer", _context(state))
    _finish(d)


def _answer_reading(state: dict, d: dict) -> dict:
    """Select a reader by the recorded contract, never reinterpret structured model quotes."""
    ctx = _context(state)
    if ctx["semantic"]:
        return CH.condition_reading(d, ctx["question"], ctx["out"], ctx["polar"], semantic=True, as_of=state["as_of"])
    sid, out = d["statistic"], ctx["out"]
    return PR.read_quotes([r["quote"] for r in out["periods"] if sid in r["for"]],
                          [r["quote"] for r in out["frequencies"] if sid in r["for"]])
