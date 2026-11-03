"""The new A's state -> AView (claview_common.contracts.a_view), the only shape the pipeline and the screen read.

The adapter reads; it never changes the state it is given and adds no judgement of its own. It copies A's outcome
per statistic, its final tables, the basis it recorded and its reasons, and A's questions as they are. It chooses no
table, drops no question and turns no failure into a success. What may go to B:

- a statistic A ``selected`` (verified) with final tables, and no pending question of its own -> ``handoff.ready``;
- everything else -> ``handoff.blocked`` with the reason (``awaiting_answer``, ``a_evidence_unverified``,
  ``not_in_pool``, ``a_failed``);
- B's table list carries no period or frequency: B reads them again from the whole question. So a request is sent
  only when that cannot drop what A decided, compared on A's own structured decisions (final tables, requested
  months, frequency), never on the quoted words and never read again here:
  - a statistic whose period A split over editions (several final tables) -> ``edition_period_split``;
  - a table with separate requested spans (a gap between them) -> ``disjoint_period_not_in_b_contract``;
  - requests whose requested months differ from each other -> ``period_differs_between_requests`` (all of them);
  - statistics with the same final tables are one request only when their tables, months, frequencies and period
    answers are the same (the focus joined with " / "), else ``same_table_conditions_differ``. Different calculation
    quotes do not stop them; the request keeps them and notes ``calculation_not_in_b_contract``.
  The request keeps A's assignments so the pipeline can check B's answer against them. A focus longer than B
  accepts is reported (``focus_too_long``), never cut.
- warnings and reasons keep every field A wrote (code, axis, stated values, table, stage), never only the code.
"""

from __future__ import annotations

import copy

from pydantic import ValidationError

from claview_a.cards import ASK_AXES
from claview_a.focus import FOCUS_JOIN, MAX_FOCUS_CHARS
from claview_a.quotes import quote_in_question
from claview_a.search.tables import selection_catalog
from claview_common.contracts.a_view import AView
from claview_common.contracts.b import BRequest

# The check and handoff share B's existing focus limit; B checks it again.
STATE_OF = {
    "selected": "selected",
    "selected_unverified": "unverified",
    "ask": "asking",
    "ask_unverified": "unverified",
    "ask_condition": "asking",
    "aggregation_needed": "unverified",
    "not_in_pool": "missing",
    "failed": "failed",
}
BLOCKED_CODE = {
    "ask": "awaiting_answer",
    "ask_condition": "awaiting_answer",
    "aggregation_needed": "aggregation_needed",
    "ask_unverified": "a_evidence_unverified",
    "selected_unverified": "a_evidence_unverified",
    "not_in_pool": "not_in_pool",
    "failed": "a_failed",
}
CONDITION_KEYS = ("periods", "frequencies", "calculations")


def _table(views: dict, table_id: str, **extra) -> dict:
    view = views.get(table_id)
    return {
        "org_id": (view.org_id if view else None) or "",
        "table_id": table_id,
        "table_name": view.title if view else None,
        **extra,
    }


def _catalog_range(views: dict, table_id: str, frequency: str | None) -> dict | None:
    """The table's range in ``frequency`` as the A catalog records it (the source of A's coverage)."""
    view = views.get(table_id)
    for f, start, end in (view.periods if view else ()) or ():
        if f == frequency:
            return {"frequency": f, "start": start, "end": end, "source": "a_catalog"}
    return None


def _conditions(out: dict | None, sid: str) -> dict:
    if not out:
        return {}
    got = {k: [r["quote"] for r in out[k] if sid in r["for"]] for k in CONDITION_KEYS}
    got["splitters"] = [{"quote": r["quote"], "axis": r["axis"]} for r in out["splitters"] if sid in r["for"]]
    return got


def _polarity(out: dict | None, sid: str, axis: str) -> tuple[str | None, dict]:
    """(mode, value -> quotes) of the model's polarity rows of ``sid`` on ``axis`` (none for an older answer)."""
    rows = [r for r in (out or {}).get("splitters") or []
            if sid in r["for"] and r["axis"] == axis and r.get("polarity")]
    required = [r for r in rows if r["polarity"] == "required"]
    any_of = [r for r in rows if r["polarity"] == "any_of"]
    quotes: dict[str, list[str]] = {}
    for r in required or any_of:
        quotes.setdefault(r["value"], [])
        if r["quote"] not in quotes[r["value"]]:
            quotes[r["value"]].append(r["quote"])
    return ("all" if required else "any" if any_of else None), quotes


def _basis(d: dict, out: dict | None = None) -> list[dict]:
    rows = []
    for axis, b in (d.get("basis") or {}).items():
        value = b.get("value")
        polar = out and b.get("source") == "splitter"
        mode, quotes = _polarity(out, d.get("statistic"), axis) if polar else (None, {})
        rows.append(
            {
                "axis": axis,
                "label": ASK_AXES.get(axis, axis),
                "value": list(value) if isinstance(value, list | tuple) else [str(value)],
                "source": b.get("source") or "unknown",
                "excluded": list(b.get("excluded") or []),
                "note": b.get("note"),
                "mode": mode,
                "value_quotes": quotes,
            }
        )
    return rows


def _reasons(rows) -> list[dict]:
    """A's reason or warning rows: the code, and as detail either the row's own ``detail`` or, for rows written
    flat (``stated_basis_unverified``: axis, stated, table, stage), every other field."""
    out = []
    for r in rows or []:
        rest = {k: v for k, v in r.items() if k != "code"}
        detail = rest["detail"] if set(rest) == {"detail"} else rest or None
        out.append({"code": r.get("code") or "unknown", "detail": copy.deepcopy(detail)})
    return out


def _warnings(rows) -> list[dict]:
    """``_reasons`` plus the label of the axis a warning names (display only)."""
    out = _reasons(rows)
    for w in out:
        if isinstance(w["detail"], dict) and w["detail"].get("axis") in ASK_AXES:
            w["detail"]["axis_label"] = ASK_AXES[w["detail"]["axis"]]
    return out


def _answers(d: dict) -> list[dict]:
    return [
        {
            "question_id": a["question_id"],
            "question_version": a["question_version"],
            "statistics": [a["statistic"]],
            "kind": "basis_axis",
            "option_id": a["option_id"],
            "value": a["value"],
            "table_id": a["table_id"],
            "source": a["source"],
        }
        for a in d.get("answers") or []
    ] + [
        {
            "question_id": a["question_id"],
            "question_version": a["question_version"],
            "statistics": list(a["statistics"]),
            "kind": a["kind"],
            "option_id": a["option_id"],
            "value": a["value"],
            "text": a.get("text"),
            "table_id": None,
            "source": a["source"],
        }
        for a in d.get("condition_answers") or []
    ]


def _question_text(quote: str, label: str) -> str:
    return f"‘{quote}’의 {label} 기준을 골라 주세요. 질문에 기준이 없고 표마다 달라 임의로 정하지 않았습니다."


CONDITION_TEXT = {
    "period": "‘{quote}’의 기간을 골라 주세요. 질문에 기간이 없어 임의로 정하지 않았습니다.",
    "frequency": "‘{quote}’의 주기를 골라 주세요. 표가 여러 주기를 주고 질문에 주기가 없어 임의로 정하지 않았습니다.",
}


def _condition_question(q: dict, quotes: list[str], version: int) -> dict:
    """An AView question of A's own period or frequency question (plan 56 order 5)."""
    text = CONDITION_TEXT[q["kind"]].format(quote="’, ‘".join(quotes))
    if q.get("partial_periods"):
        bounds = " · ".join(("시작 " if k == "start" else "끝 ") + p[k]
                            for p in q["partial_periods"] for k in ("start", "end") if p.get(k))
        text = f"‘{'’, ‘'.join(quotes)}’의 {bounds} 조건은 유지합니다. 빠진 경계를 정해 전체 기간을 입력해 주세요."
    return {
        "question_id": q["question_id"],
        "question_version": version,
        "kind": q["kind"],
        "statistics": list(q["statistics"]),
        "cause": q["cause"],
        "axis": None,
        "axis_label": None,
        "text": text,
        "input": q["input"],
        "options": [
            {"option_id": o["option_id"], "label": o["label"], "value": o["value"], "range": copy.deepcopy(o["range"])}
            for o in q["options"]
        ],
    }


def build(checkpoint: dict, layer) -> dict:
    """The AView of a screen checkpoint (``claview_a_api.screen``): session, the new A state, the period questions
    and answers of the test supplier. Validated against the contract and returned as a plain dict."""
    from claview_a import answers as AN

    session, state = checkpoint["session"], checkpoint["a_state"]
    period = checkpoint.get("period") or {"questions": [], "answers": []}
    views = selection_catalog(layer).views
    record = state.get("record") or {}
    out = record.get("model_output")
    stats_by_id = {s["id"]: s for s in (out or {}).get("statistics") or []}
    a_version = checkpoint["session"]["question_version"]  # one question version for every question (bundle 9)

    questions = []
    for q in AN.questions(state):
        if q["kind"] in AN.CONDITION_KINDS:
            quotes = [stats_by_id.get(s, {}).get("quote", "") for s in q["statistics"]]
            questions.append(_condition_question(q, quotes, a_version))
            continue
        quote = stats_by_id.get(q["statistic"], {}).get("quote", "")
        questions.append(
            {
                "question_id": q["question_id"],
                "question_version": a_version,
                "kind": "basis_axis",
                "statistics": [q["statistic"]],
                "axis": q["axis"],
                "axis_label": q["axis_label"],
                "text": _question_text(quote, q["axis_label"]),
                "input": "choice",
                "options": [
                    {
                        "option_id": o["option_id"],
                        "label": o["value"],
                        "value": o["value"],
                        "table_id": o["table_id"],
                        "table_name": _table(views, o["table_id"])["table_name"],
                        "status": o.get("status"),
                        "selectable": o.get("selectable", True),
                    }
                    for o in q["options"]
                ],
            }
        )
    questions += [{**copy.deepcopy(q), "question_version": a_version} for q in period.get("questions") or []]
    asked = {sid for q in questions for sid in q["statistics"]}
    period_answers = {}
    for a in period.get("answers") or []:
        for sid in a["statistics"]:
            period_answers.setdefault(sid, []).append(copy.deepcopy(a))

    statistics = []
    for d in record.get("decisions") or []:
        sid = d["statistic"]
        outcome = d["outcome"]
        statistics.append(
            {
                "request_id": f"{session['session_id']}:{sid}",
                "statistic": sid,
                "quote": d["quote"],
                "name": d["name"],
                "outcome": outcome,
                "state": STATE_OF[outcome],
                "verified": outcome in ("selected", "ask", "ask_condition"),
                "representative": d.get("representative"),
                "tables": [
                    _table(
                        views,
                        r["table_id"],
                        period=copy.deepcopy(r.get("period")),
                        frequency=r.get("frequency"),
                        catalog_range=_catalog_range(views, r["table_id"], r.get("frequency")),
                        latest_unpublished=copy.deepcopy(r.get("latest_unpublished")),
                        period_hint=copy.deepcopy(r.get("period_hint")),
                        period_phrase=copy.deepcopy(r.get("period_phrase")),
                        period_source=(r.get("period_phrase") or {}).get("source"),
                        frequency_source=(d.get("frequency") or {}).get("source"),
                        default_reason=(d.get("period") or {}).get("default_reason"),
                    )
                    for r in d.get("final_tables") or []
                ],
                "basis": _basis(d, out),
                "focus": (d.get("focus") or {}).get("text", d["quote"]),
                "focus_provenance": copy.deepcopy(d.get("focus") or {
                    "text": d["quote"], "quotes": [d["quote"]], "source": "legacy_quote",
                    "status": ("verified_quote_only"
                               if quote_in_question(d["quote"], session["question"]) else "invalid"),
                }),
                "conditions": _conditions(out, sid),
                "reasons": _reasons(d.get("reasons")),
                "warnings": _warnings(d.get("warnings")),
                "search_words": list(d.get("search_words") or []),
                "answers": _answers(d) + period_answers.get(sid, []),
                "aggregation": copy.deepcopy((d.get("frequency") or {}).get("aggregation")),
                "auto_decided": [{"axis": c["axis"], "value": c["value"], "message": c["message"]}
                                 for c in d.get("corrections") or [] if c["code"] == "auto_decided"],
                "a_stage": d.get("a_stage"),
                "warning_rows": copy.deepcopy(d.get("warning_rows") or []),
                "end_point_status": copy.deepcopy(d.get("end_point_status") or []),
            }
        )

    # A's own period and frequency answers (plan 56 order 5) go with the request too: B's table list has no field for
    # them, so a request carrying them waits for a B that takes them (plan 56 0.5, period_answer_contract_pending)
    handoff_answers = {sid: list(rows) for sid, rows in period_answers.items()}
    for s in statistics:
        handoff_answers.setdefault(s["statistic"], []).extend(
            copy.deepcopy(a) for a in s["answers"] if a["kind"] in AN.CONDITION_KINDS and a not in
            handoff_answers.get(s["statistic"], []))
    ready, blocked = _handoff(session, statistics, asked, handoff_answers)
    failure = None
    if record.get("error"):
        failure = {"stage": record["error"].get("stage") or "a", "code": record["error"].get("code") or "unknown"}
    route = (record.get("route") or {}).get("action") or state.get("action") or "fail"
    if failure:
        status = "failed"
    elif questions:
        status = "needs_answer"
    elif ready and not blocked:
        status = "ready"
    elif ready:
        status = "partial"
    else:
        status = "failed"
    info = state.get("run_info") or {}
    view = {
        "session": {
            "session_id": session["session_id"],
            "question_version": session["question_version"],
            "question": session["question"],
            "as_of": session["as_of"],
            "run": {
                "versions": copy.deepcopy(info.get("versions") or {}),
                "model": copy.deepcopy(info.get("model") or {}),
                "research_model": copy.deepcopy(info.get("research_model")),
                "profile": session["profile"],
                "period_policy": copy.deepcopy(info.get("period_policy")),
            },
        },
        "status": status,
        "a_route": route,
        "statistics": statistics,
        "questions": questions,
        "handoff": {"ready": ready, "blocked": blocked},
        "failure": failure,
        "research": copy.deepcopy(state.get("research")),
        "warnings": [
            {"code": p.get("code") or "unknown", "detail": copy.deepcopy(p)}
            for p in (record.get("parse") or {}).get("problems") or []
        ],
    }
    for request in ready:
        prepared = prepare_b_request(view, request)
        request["b_request"] = prepared["request"]
        request["b_request_issue"] = prepared["withheld"]
    return AView(**view).model_dump()


def _spans(table: dict) -> tuple:
    return tuple((p.get("start"), p.get("end")) for p in table.get("period") or ())


def _assignment(stat: dict) -> tuple:
    """A's decision for the statistic as structured values: (table, frequency, requested spans) per final table."""
    return tuple(sorted((t["table_id"], t.get("frequency") or "", _spans(t)) for t in stat["tables"]))


def _months(stat: dict) -> frozenset:
    """Every requested month of the statistic (spans kept apart: a gap stays a gap)."""
    out = set()
    for t in stat["tables"]:
        for start, end in _spans(t):
            (y0, m0), (y1, m1) = (map(int, start.split("-")), map(int, end.split("-")))
            out.update(range(y0 * 12 + m0 - 1, y1 * 12 + m1))
    return frozenset(out)


def _answer_key(answers: list[dict]) -> tuple:
    return tuple(sorted((a.get("option_id") or "", a.get("value") or "", a.get("text") or "") for a in answers))


def _assigned(stats: list[dict]) -> list[dict]:
    return [
        {
            "statistic": s["statistic"],
            "tables": [{k: t.get(k) for k in ("table_id", "frequency", "period")} for t in s["tables"]],
        }
        for s in stats
    ]


def _handoff(session: dict, statistics: list[dict], asked: set, period_answers: dict) -> tuple[list, list]:
    blocked, groups = [], {}
    for s in statistics:
        sid = s["statistic"]
        if s["outcome"] != "selected" or not s["tables"]:
            blocked.append(
                {
                    "statistics": [sid],
                    "code": BLOCKED_CODE.get(s["outcome"], "a_failed"),
                    "detail": [r["code"] for r in s["reasons"]] or None,
                }
            )
        elif sid in asked:
            blocked.append({"statistics": [sid], "code": "awaiting_answer", "detail": None})
        elif len(s["tables"]) > 1:
            # one statistic over several editions, each with its own months: B would read one range for all
            blocked.append({"statistics": [sid], "code": "edition_period_split", "detail": _assigned([s])})
        elif any(len(_spans(t)) != 1 for t in s["tables"]):
            blocked.append({"statistics": [sid], "code": "disjoint_period_not_in_b_contract", "detail": _assigned([s])})
        else:
            groups.setdefault(tuple(t["table_id"] for t in s["tables"]), []).append(s)
    candidates = []
    for key, members in groups.items():
        sids = [m["statistic"] for m in members]
        if len({(_assignment(m), _answer_key(period_answers.get(m["statistic"], []))) for m in members}) > 1:
            blocked.append({"statistics": sids, "code": "same_table_conditions_differ", "detail": _assigned(members)})
            continue
        candidates.append((sids, members))
    if len({_months(members[0]) for _, members in candidates}) > 1:
        # B reads one period from the whole question for every request: different months cannot all survive
        blocked += [
            {"statistics": sids, "code": "period_differs_between_requests", "detail": _assigned(members)}
            for sids, members in candidates
        ]
        candidates = []
    ready = []
    for sids, members in candidates:
        focus = FOCUS_JOIN.join(dict.fromkeys(m["focus"].strip() for m in members if m["focus"]))
        if len(focus) > MAX_FOCUS_CHARS:
            blocked.append(
                {
                    "statistics": sids,
                    "code": "focus_too_long",
                    "detail": {"chars": len(focus), "limit": MAX_FOCUS_CHARS},
                }
            )
            continue
        calculations = {m["statistic"]: list(m["conditions"].get("calculations") or []) for m in members}
        notes = []
        if len(members) > 1 and len({tuple(v) for v in calculations.values()}) > 1:
            notes.append({"code": "calculation_not_in_b_contract", "detail": calculations})
        first = members[0]["tables"]
        ready.append(
            {
                "request_id": f"{session['session_id']}:{'+'.join(sids)}",
                "statistics": sids,
                "tables": [
                    {
                        "org_id": t["org_id"],
                        "table_id": t["table_id"],
                        "table_name": t["table_name"],
                        "focus": focus or None,
                    }
                    for t in first
                ],
                "period_answers": [a for m in members for a in period_answers.get(m["statistic"], [])],
                "assignments": [
                    {
                        "table_id": t["table_id"],
                        "frequency": t.get("frequency"),
                        "period": copy.deepcopy(t.get("period")),
                        "statistics": sids,
                        **{k: copy.deepcopy(t[k]) for k in ("catalog_range", "latest_unpublished") if t.get(k)},
                    }
                    for t in first
                ],
                "calculations": calculations,
                "notes": notes,
            }
        )
    return ready, blocked


# ---------------------------------------------------------------- the C2 conditions of a request (plan draft 57 step 1)

CONDITION_SOURCES = ("quoted", "user_answer")  # a period source the C2 delivery allows (no default)
PHRASE_MAX = 60  # the C2 delivery's period_phrase limit


def handoff_conditions(view: dict, request: dict) -> dict:
    """The C2 conditions (delivery rev 11, 3.1) of one ready request, assembled from the same AView statistics the
    screen shows: per table the statistics, frequency, requested months, period and frequency sources, the period
    phrase and the unpublished months, and A's period hint apart. Nothing is read again from the question and
    nothing is decided here. {conditions, period_hints, withheld}: ``withheld`` names why no condition is made for
    the request (a period source the delivery does not allow, the members' values or sources not one), and then
    ``conditions`` is empty: the request keeps today's path. Owner 11-01 13:39 / 14:08: A assembles, the pipeline only
    converts, checks and sends."""
    stats = {s["statistic"]: s for s in view.get("statistics") or []}
    members = [stats.get(sid) for sid in request["statistics"]]
    if any(m is None or m.get("outcome") != "selected" for m in members):
        return {"conditions": [], "period_hints": [], "withheld": "statistic_not_selected"}
    conditions, hints = [], []
    for t in request["tables"]:
        rows = [next((x for x in m["tables"] if x["table_id"] == t["table_id"]), None) for m in members]
        if any(r is None for r in rows):
            return {"conditions": [], "period_hints": [], "withheld": "member_table_missing"}
        values = {(r.get("frequency"), _span_key(r.get("period")), r.get("period_source"), r.get("frequency_source"))
                  for r in rows}
        if len(values) != 1:
            return {"conditions": [], "period_hints": [], "withheld": "member_values_differ"}
        first = rows[0]
        if first.get("period_source") not in CONDITION_SOURCES:
            return {"conditions": [], "period_hints": [], "withheld": "period_source_not_allowed"}
        if not first.get("frequency") or len(first.get("period") or []) != 1:
            return {"conditions": [], "period_hints": [], "withheld": "period_not_one_span"}
        texts = list(dict.fromkeys((r.get("period_phrase") or {}).get("text") for r in rows if
                                   (r.get("period_phrase") or {}).get("text")))
        phrase = FOCUS_JOIN.join(texts) or None
        row = {"table_id": t["table_id"], "statistics": list(request["statistics"]), "frequency": first["frequency"],
               "period": copy.deepcopy(first["period"]), "period_source": first["period_source"],
               "frequency_source": first.get("frequency_source")}
        if phrase and len(phrase) <= PHRASE_MAX:
            row["period_phrase"] = phrase
        if first.get("latest_unpublished"):
            row["latest_unpublished"] = copy.deepcopy(first["latest_unpublished"])
        conditions.append(row)
        if first.get("period_hint"):
            hints.append({"table_id": t["table_id"], **copy.deepcopy(first["period_hint"])})
    return {"conditions": conditions, "period_hints": hints, "withheld": None}


def _span_key(spans) -> tuple:
    return tuple((s.get("start"), s.get("end")) for s in spans or [])


def prepare_b_request(view: dict, request: dict) -> dict:
    """Prepare the versioned B input without changing A's decisions or enabling transport.

    Legacy handoff tables and all existing gates remain intact. Answers keep their original scope;
    the shared contract checks coverage by kind and deduplicates shared answers. No text is re-read.
    A preparation error is reported beside the ready request, not converted into a table-selection failure.
    """
    targets = set(request["statistics"])
    if any(targets.intersection(q["statistics"]) for q in view.get("questions") or []):
        return {"request": None, "withheld": "awaiting_answer"}
    assembled = handoff_conditions(view, request)
    if assembled["withheld"]:
        return {"request": None, "withheld": assembled["withheld"]}
    conditions = {c["table_id"]: c for c in assembled["conditions"]}
    tables = []
    for table in request["tables"]:
        condition = copy.deepcopy(conditions[table["table_id"]])
        condition.pop("table_id")
        condition["answers"] = [
            {k: copy.deepcopy(v) for k, v in answer.items() if k != "table_id"}
            for answer in request.get("period_answers") or []
            if answer.get("kind") in ("period", "frequency")
            and targets.intersection(answer.get("statistics") or [])
        ]
        tables.append({**copy.deepcopy(table), "period_condition": condition})
    try:
        model = BRequest(query=view["session"]["question"], as_of=view["session"]["as_of"], tables=tables)
    except ValidationError as exc:
        codes = [e["type"] for e in exc.errors(include_input=False, include_url=False)]
        code = next((c for c in codes if c.startswith("period_condition_")), "period_condition_invalid")
        return {"request": None, "withheld": code}
    return {"request": model.model_dump(), "withheld": None}
