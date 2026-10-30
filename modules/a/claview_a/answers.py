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

Nothing here names a table, case or question.
"""

from __future__ import annotations

import copy

from .search.tables import selection_catalog

from . import check as CH
from . import periods as PR
from .cards import ASK_AXES

ANSWERS_VERSION = "tl-answers-2026-10-30.1"
ANSWER_SOURCE = "user_answer"
ERRORS = ("no_reply", "reply_shape", "stale_question", "duplicate_reply", "question_not_pending",
          "option_not_offered", "no_record")


class AnswerError(ValueError):
    """A reply that cannot be applied; ``code`` is one of ``ERRORS``. The state is left unchanged."""

    def __init__(self, code: str):
        if code not in ERRORS:
            raise ValueError(f"unknown answer error {code!r}")
        super().__init__(code)
        self.code = code


def question_version(state: dict) -> int:
    """The version the next reply must name: 0 before any answer, one more after each applied reply batch."""
    return (state.get("answer_log") or {}).get("question_version", 0)


def question_id(statistic: str, axis: str) -> str:
    return f"q:{statistic}:{axis}"


def questions(state: dict) -> list[dict]:
    """The pending basis-axis questions: one per ask axis of every statistic whose outcome is ``ask``, in record
    order, with the options as the check left them (value and option table)."""
    out = []
    for d in (state.get("record") or {}).get("decisions") or []:
        if d["outcome"] != "ask":
            continue
        sid = d["statistic"]
        for a in d["asks"]:
            qid = question_id(sid, a["axis"])
            out.append({"question_id": qid, "kind": "basis_axis", "statistic": sid, "axis": a["axis"],
                        "axis_label": ASK_AXES.get(a["axis"], a["axis"]), "source": a["source"],
                        "options": [{"option_id": f"{qid}:o{i}", "value": o["value"], "table_id": o["table_id"]}
                                    for i, o in enumerate(a["options"], start=1)]})
    return out


def _validate(state: dict, replies) -> list[tuple[dict, dict]]:
    if not (state.get("record") or {}).get("decisions"):
        raise AnswerError("no_record")
    if not isinstance(replies, list) or not replies:
        raise AnswerError("no_reply")
    current = question_version(state)
    pending = {q["question_id"]: q for q in questions(state)}
    seen: set[str] = set()
    out = []
    for r in replies:
        if not isinstance(r, dict) or set(r) != {"question_id", "question_version", "option_id"}:
            raise AnswerError("reply_shape")
        if type(r["question_version"]) is not int or r["question_version"] != current:
            raise AnswerError("stale_question")
        if r["question_id"] in seen:
            raise AnswerError("duplicate_reply")
        seen.add(r["question_id"])
        q = pending.get(r["question_id"])
        if q is None:
            raise AnswerError("question_not_pending")
        option = next((o for o in q["options"] if o["option_id"] == r["option_id"]), None)
        if option is None:
            raise AnswerError("option_not_offered")
        out.append((q, option))
    return out


def apply(state: dict, replies: list[dict], layer) -> dict:
    """A new state with the replies applied (the given state is not changed). ``replies``: ``{question_id,
    question_version, option_id}`` each, for pending questions of the current version, at most one per question."""
    checked = _validate(state, replies)
    current = question_version(state)
    new = copy.deepcopy(state)
    if "record_before_answers" not in new:
        new["record_before_answers"] = copy.deepcopy(state["record"])
    record = new["record"]
    log = new.setdefault("answer_log", {"version": ANSWERS_VERSION, "question_version": 0, "rows": []})
    by_sid = {d["statistic"]: d for d in record["decisions"]}
    touched: list[str] = []
    for q, option in checked:
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
    if any(not by_sid[sid]["asks"] for sid in touched):
        lk = CH.Cards(new["cards"], selection_catalog(layer).views)
        for sid in dict.fromkeys(touched):
            if not by_sid[sid]["asks"]:
                _decide(new, by_sid[sid], lk)
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
    elif d["final_tables"]:
        d["outcome"] = "selected" if d["citation"]["status"] == "verified" else "selected_unverified"
    else:
        d["outcome"] = "failed"
        d["reasons"].append({"code": "undecided", "detail": None})


def _decide(state: dict, d: dict, lk: CH.Cards) -> None:
    """Every axis of ``d`` answered: the option table holding every answered value, then the checks of a decided
    table (check steps 4 and 5) on it."""
    out = state["record"]["model_output"]
    sid, quote, as_of = d["statistic"], d["quote"], state["as_of"]
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
    # 4. periods and final tables, as for a decided table
    reading = PR.read_quotes([r["quote"] for r in out["periods"] if sid in r["for"]],
                             [r["quote"] for r in out["frequencies"] if sid in r["for"]])
    rows = {r["table_id"]: r for r in lk.card_of[t]["tables"]}
    d["period"] = {"readings": reading["readings"]}
    if reading["problems"]:
        for p in reading["problems"]:
            _fail(d, p["code"], p)
        return _finish(d)
    candidates = CH._candidates(rows, t)
    d["checks_done"].append("periods")
    got = CH._select_periods(quote, t, candidates, d["item_evidence"], reading, lk, as_of, "answer", d["retention"])
    d["verification"] += got.pop("trace")
    quoted = any(c["kind"] == "period" for c in reading["conditions"])
    d["period"].update({"candidates": candidates, "excluded": [x for x in rows if x not in candidates],
                        "default_applied": not quoted,
                        "default_reason": None if quoted else
                        ("only base-year marks were quoted" if any(r["kind"] == "period" for r in reading["readings"])
                         else "no period quoted"), **got})
    if got["status"] != "selected":
        _fail(d, got["reason"], got.get("detail"))
        return _finish(d)
    # 5. recheck every final table
    d["checks_done"].append("final_recheck")
    problems = []
    for r in got["tables"]:
        x = r["table_id"]
        again = CH.verify_table(quote, x, d["item_evidence"], lk, d["retention"])
        d["verification"].append({**CH._trace("final_table", again), "period": r["period"], "frequency": r["frequency"]})
        if not again["ok"]:
            problems.append(f"{x}: {again['code']}")
        if CH._basis(lk.row[x]) != CH._basis(lk.row[t]):
            problems.append(f"{x}: basis differs")
        if x not in got["usable"]:
            problems.append(f"{x}: content not verified")
    if problems:
        _fail(d, "final_recheck_failed", problems)
        return _finish(d)
    d["final_tables"] = got["tables"]
    if [r["table_id"] for r in got["tables"]] != [t]:
        d["corrections"].append({"code": "final_tables_by_period", "from": t, "to": [r["table_id"] for r in got["tables"]]})
    _finish(d)
