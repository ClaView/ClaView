# ruff: noqa: E501  (user-facing Korean text: ruff counts full-width characters as two columns)
"""화면 경로의 새 A 실행기: 질문 → 새 A(claview_a_api.screen) → AView → B → 조회 → C (계획 54).

A 단계만 `staged.Pipeline`과 다르다. 새 A의 공개 결과(AView)만 읽고 A 내부 상태(record·decisions)는 읽지 않는다.
옛 A(`claview_a_api.service`, claview_a_legacy)를 부르지 않으며, 새 A가 실패해도 옛 A로 대신하지 않는다.

- 되묻기: AView의 질문을 확인 카드(kind `a_view`)로 보여 주고 멈춘다. 브라우저는 세션 ID와
  {question_id, question_version, option_id | text}만 돌려보낸다. 서버가 보관한 새 A 체크포인트에 적용하고
  (모델 재호출 없음), 질문이 남으면 다시 멈추며, 다 답하면 B로 넘긴다.
- B 전달: `handoff.ready`만 보낸다(표마다 focus). `blocked`와 B가 받을 수 없는 요청은 보내지 않고 미지원 연결 항목으로
  남기며 결과는 partial이다. B가 `accepts_period_condition`을 밝히면 A가 준비한 `b_request`(표마다 `period_condition`,
  사용자 답 포함)를 보내고, 그렇지 않은 B에는 기존 요청을 보내며 기간 답변은 `period_answer_contract_pending`으로 보류한다.
- 지원 B로 보낸 요청은 B의 `period_resolutions`로 확인한다. 사용자가 고른 기간·주기를 B가 그대로 적용하지 않았으면
  (`b_answered_period_not_applied`) 조회하지 않고, 아무도 답하지 않은 필드의 A·B 차이는 중단하지 않고 기록만 한다.
- 기간 답변은 원문·적용 통계·출처를 그대로 넘길 뿐 날짜를 계산하거나 기본 기간을 채우지 않는다.
"""

from __future__ import annotations

import copy

from claview_a_api import screen as a_screen
from claview_common.periods import PeriodError, period_index, periods_between

from .confirmation import _label_card, _table_card, _table_index, validate_confirmation
from .staged import MESSAGES, Pipeline, StageFailure, now

STATE_LABEL = {
    "selected": "확정",
    "unverified": "근거 미확인(조회하지 않음)",
    "asking": "기준 확인 필요",
    "missing": "후보 표 없음",
    "failed": "실패",
}
BLOCKED_TEXT = {
    "awaiting_answer": "답변을 기다립니다.",
    "a_evidence_unverified": "A가 카드 근거를 확인하지 못해 B로 넘기지 않았습니다.",
    "not_in_pool": "후보 표에서 찾지 못했습니다.",
    "a_failed": "A 검사를 통과하지 못했습니다.",
    "same_table_conditions_differ": "같은 표를 쓰는 통계에 A가 정한 기간·주기가 달라 현재 B 계약으로 나눠 전달할 수 없습니다(합치지 않음).",
    "focus_too_long": "표별 지표 문구가 B가 받는 길이를 넘어 전달하지 않았습니다(자르지 않음).",
    "period_answer_contract_pending": "기간 답변을 받을 B 계약이 아직 없어 전달하지 않았습니다(기간 정책 확정 후 연결).",
    "edition_period_split": "A가 기간을 판본별 표로 나눠 정했는데, B 계약에는 표별 기간 칸이 없어 전달하지 않았습니다.",
    "disjoint_period_not_in_b_contract": "A가 정한 기간이 떨어진 구간인데, B 계약은 한 범위만 받아 전달하지 않았습니다.",
    "period_differs_between_requests": "통계마다 A가 정한 기간이 달라, 질문 전체에서 기간을 다시 읽는 현재 B 계약으로 전달하지 않았습니다.",
    "b_period_differs_from_a": "B가 고른 기간·주기가 A가 정한 것과 달라 조회하지 않았습니다(계산용 기준 시점 추가도 B가 그 범위를 결과에 밝히기 전까지는 같게 봅니다).",
    "period_condition_not_prepared": "A가 이용자가 고른 기간·주기를 B로 보낼 형태로 만들지 못해 전달하지 않았습니다(답을 빼고 조회하지 않음).",
    "b_answered_period_not_applied": "B가 이용자가 고른 기간·주기를 그대로 적용했다고 결과에서 확인하지 못해 조회하지 않았습니다.",
}
# Links the current B contract cannot carry (reported as unsupported links, apart from A's own outcomes)
UNSUPPORTED = (
    "same_table_conditions_differ",
    "focus_too_long",
    "period_answer_contract_pending",
    "edition_period_split",
    "disjoint_period_not_in_b_contract",
    "period_differs_between_requests",
    "b_period_differs_from_a",
    "period_condition_not_prepared",
    "b_answered_period_not_applied",
)
NOTE_TEXT = {
    "calculation_not_in_b_contract": "같은 표로 함께 넘긴 통계의 계산 요청이 서로 다릅니다. B 계약에는 통계별 계산 칸이 없어 원질문으로만 전달됩니다.",
}


def _a_periods(spans, frequency) -> set:
    """A's requested months as KOSIS period strings of ``frequency`` (format only; nothing is read again)."""
    out = set()
    for span in spans or []:
        (y0, m0), (y1, m1) = (map(int, span["start"].split("-")), map(int, span["end"].split("-")))
        for n in range(y0 * 12 + m0 - 1, y1 * 12 + m1):
            y, m = divmod(n, 12)
            out.add(
                {"Y": f"{y}", "H": f"{y}{m // 6 + 1:02d}", "Q": f"{y}{m // 3 + 1:02d}", "M": f"{y}{m + 1:02d}"}[
                    frequency
                ]
            )
    return out


def warning_text(w: dict) -> str:
    d = w["detail"] if isinstance(w.get("detail"), dict) else {}
    head = f"{w['statistic']} ‘{w['quote']}’"
    if w["code"] == "stated_basis_unverified":
        stated = ", ".join(d.get("stated") or []) or "명시 값"
        return (
            f"{head}: 질문에 적힌 {d.get('axis_label') or d.get('axis') or '기준'}({stated})을 표 {d.get('table')}에서 "
            "확인하지 못했습니다. 다른 검사는 통과했습니다."
        )
    return f"{head}: A 검사 경고 {w['code']}" + (f" ({d.get('axis')})" if d.get("axis") else "")


ANSWER_MESSAGES = {
    "session_mismatch": "다른 실행의 답변입니다. 같은 질문을 다시 입력해 주세요.",
    "stale_question": "이미 답했거나 바뀐 질문입니다. 남은 질문에 다시 답해 주세요.",
    "question_not_pending": "이미 답했거나 제시하지 않은 질문입니다.",
    "duplicate_reply": "같은 질문에 두 번 답할 수 없습니다.",
    "option_not_offered": "제시된 선택지 중에서 골라 주세요.",
    "text_not_allowed": "이 질문은 선택지에서만 고를 수 있습니다.",
    "text_invalid": "직접 입력은 1~200자로 적어 주세요.",
    "option_or_text_required": "선택지 하나를 고르거나 직접 입력 중 하나만 보내 주세요.",
}


def _answer_rejection_message(exc: a_screen.AnswerRejected) -> str:
    if exc.code == "text_invalid" and exc.cause:
        return {
            "period_condition_not_representable": "입력한 기간은 선택한 주기와 맞지 않습니다. 주기 경계에 맞는 기간을 입력해 주세요.",
            "malformed_condition": "유효한 연도·월·분기로 기간을 입력해 주세요.",
            "stated_period_boundary_changed": "질문에 명시한 시작·끝을 유지하는 기간을 입력해 주세요.",
        }.get(exc.cause, "입력한 기간을 해석하지 못했습니다. 시작과 끝을 확인해 다시 입력해 주세요.")
    return ANSWER_MESSAGES.get(exc.code, "답변을 적용하지 못했습니다. 선택 내용을 다시 확인해 주세요.")


def _label(view_stat: dict) -> str:
    return f"{view_stat['statistic']}: {view_stat['quote']} → {STATE_LABEL[view_stat['state']]}"


def a_view_confirmation(view: dict, store) -> dict:
    """The UI card of the AView's questions (one group per question, the existing card shape) plus what is already
    settled, so the screen keeps showing it while the other questions wait."""
    index = _table_index(store)
    by_sid = {s["statistic"]: s for s in view["statistics"]}
    groups = []
    for q in view["questions"]:
        options = []
        for o in q["options"]:
            if o.get("table_id"):
                card = _table_card(store, index, o["table_id"], [])
                card["different_axes"] = [f"{q['axis_label'] or q['axis']}: {o['value']}"]
                card["search_note"] = "새 A가 제시한 선택지 표"
            else:
                card = _label_card(o["label"], "시험용 선택지" if q["synthetic"] else "선택지")
            options.append({"option_id": o["option_id"], "label": o["label"], "tables": [card]})
        groups.append(
            {
                "requirement_id": q["question_id"],
                "question_version": q["question_version"],
                "kind": q["kind"],
                "input": q["input"],
                "statistics": q["statistics"],
                "synthetic": q["synthetic"],
                "quote": " · ".join(by_sid[s]["quote"] for s in q["statistics"] if s in by_sid),
                "indicator": q["text"],
                "breakdown": [],
                "a_status": "question",
                "reasons": [q.get("axis") or q["kind"]],
                "options": options,
            }
        )
    settled = [
        {
            "statistic": s["statistic"],
            "quote": s["quote"],
            "state": s["state"],
            "label": STATE_LABEL[s["state"]],
            "tables": [f"{t['table_name']} [{t['table_id']}]" for t in s["tables"]],
            "answers": [a.get("text") or a.get("value") for a in s["answers"]],
        }
        for s in view["statistics"]
        if s["state"] != "asking"
    ]
    return {
        "kind": "a_view",
        "session_id": view["session"]["session_id"],
        "question_version": view["session"]["question_version"],
        "allow_partial": True,
        "requires_interpretation_ack": False,
        "recovery_note": "",
        "interpretation_reasons": [],
        "conditions": [],
        "groups": groups,
        "settled": settled,
        "question_text": " / ".join(q["text"] for q in view["questions"]),
        "dimension": "a_view",
    }


class ScreenPipeline(Pipeline):
    """The screen's runner: the new A, then the shared B/fetch/C steps of `staged.Pipeline`."""

    a_model_mode = "new_a"
    a_running_text = "새 A가 질문을 읽고 통계표를 고릅니다(모델 판단 + 코드 검사)."

    def __init__(self, *args, judges_factory=None, period_questions=None, b_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        # judges_factory() -> claview_a_api.screen.Judges; the default refuses live calls (a_live_disabled)
        self.judges_factory = judges_factory or self._default_judges
        self.period_questions = period_questions  # tests and the screen demo only (synthetic period questions)
        if b_factory is not None:
            self.b_factory = b_factory

    def _default_judges(self):
        # A's approved transport is available through its API, but this main screen path does not yet connect
        # persistent records and downstream call caps. Keep its previous live-disabled behavior until that PR.
        if self.mode == "live":
            raise a_screen.LiveDisabled()
        return a_screen.judges_for(self.mode)

    # ---- resume -----------------------------------------------------------------------
    def validate_resume(self, paused, confirmation):
        card = paused.get("confirmation") or {}
        if card.get("kind") != "a_view":
            return validate_confirmation(paused, confirmation)  # the frequency alignment question
        if paused.get("status") != "needs_confirmation" or not paused.get("a_checkpoint"):
            raise ValueError("이 실행은 A의 확인 질문을 기다리는 상태가 아닙니다.")
        if not isinstance(confirmation, dict) or set(confirmation) != {"session_id", "answers"}:
            raise ValueError("답변 형식이 올바르지 않습니다.")
        if not isinstance(confirmation["session_id"], str) or not isinstance(confirmation["answers"], list):
            raise ValueError("답변 형식이 올바르지 않습니다.")
        try:  # a dry run on a copy: nothing is kept, no model is called
            a_screen.answer(paused["a_checkpoint"], confirmation["session_id"], copy.deepcopy(confirmation["answers"]))
        except a_screen.AnswerRejected as exc:
            raise ValueError(_answer_rejection_message(exc)) from None
        return {
            "kind": "a_view",
            "session_id": confirmation["session_id"],
            "answers": copy.deepcopy(confirmation["answers"]),
        }

    def resume(self, paused, confirmation, *, emit=lambda x: None):
        answer = self.validate_resume(paused, confirmation)
        if answer.get("kind") == "alignment":
            return super().resume(paused, confirmation, emit=emit)
        return self.run(paused["query"], as_of=paused["as_of"], emit=emit, _resume=(paused, answer))

    # ---- A ----------------------------------------------------------------------------
    def _run_a(self, state, query, resume, update):
        resumed = bool(resume)
        if resume:
            paused, answer = resume
            state["parent_run_id"] = paused["run_id"]
            state["resume_kind"] = "a_answer"
            out = a_screen.answer(paused["a_checkpoint"], answer["session_id"], answer["answers"])
            state["user_confirmation"] = {
                "source": "user_confirmation",
                "parent_run_id": paused["run_id"],
                "at": now(),
                "session_id": answer["session_id"],
                "answers": copy.deepcopy(answer["answers"]),
            }
            state["extra_calls"] = {"A_judge": 0}
            judges_kind = out["checkpoint"]["judges"]
        else:
            try:
                judges = self.judges_factory()
            except a_screen.LiveDisabled:
                update("question", "failed", MESSAGES["a_live_disabled"], [["질문", query]])
                raise StageFailure("a_live_disabled", "error") from None
            out = a_screen.select(query, state["as_of"], judges, self.period_questions)
            state["extra_calls"] = {"A_judge": len(judges.requests)}
            state["a_judge_models"] = list(judges.requests)
            judges_kind = judges.kind
        view, checkpoint = out["view"], out["checkpoint"]
        state["a_checkpoint"] = checkpoint  # server-held; the response carries only the card
        state["a_view"] = view
        state["as_of"] = view["session"]["as_of"]
        return self._report_view(state, view, update, resumed, judges_kind)

    def _report_view(self, state, view, update, resumed, judges_kind):
        stats = view["statistics"]
        run = view["session"]["run"]
        research = run.get("research_model") or {}
        update(
            "question",
            "completed" if not view["failure"] else "failed",
            f"통계 {len(stats)}개를 읽었습니다." if stats else "질문을 해석하지 못했습니다.",
            [
                ["질문", view["session"]["question"]],
                ["판단 방식", "새 A · 모델 판단 + 코드 검사"],
                ["판단기", "합성 판단기(실제 모델 미호출)" if judges_kind != "live" else "실제 모델"],
                ["실행 설정", f"최초 {run['model'].get('model')} · 재검색 뒤 {research.get('model') or '재판단 없음'}"],
                ["기준일", view["session"]["as_of"]],
            ],
            [_label(s) for s in stats],
            "사용자 답변을 적용하고 코드 검사를 다시 했습니다(모델 재호출 없음)." if resumed else "",
        )
        if view["failure"]:
            update(
                "table",
                "failed",
                MESSAGES["a_judge_failed"],
                [["실패 단계", view["failure"]["stage"]], ["실패 코드", view["failure"]["code"]]],
            )
            raise StageFailure("a_judge_failed", "error")
        listing = [
            _label(s)
            + (" · " + ", ".join(f"{t['table_name']} [{t['table_id']}]" for t in s["tables"]) if s["tables"] else "")
            for s in stats
        ]
        if view["questions"]:
            state["confirmation"] = a_view_confirmation(view, self.store)
            update(
                "table",
                "needs_confirmation",
                "통계별 기준을 확인해 주세요. 확정된 통계는 그대로 보존합니다.",
                listing=listing,
            )
            raise StageFailure("a_answer_required", "needs_confirmation")
        b_requests, unsupported = [], []
        # A B that applies A's structured period condition gets A's prepared request (per table `period_condition`,
        # the user's answers inside it); any other B keeps today's request, and answers stay pending for it.
        takes_condition = getattr(self.b_factory, "accepts_period_condition", False) is True
        for request in view["handoff"]["ready"]:
            prepared = request.get("b_request") if takes_condition else None
            if takes_condition and prepared is None and request["period_answers"]:
                # never deliver the request without the answers the user gave
                unsupported.append(
                    {
                        "statistics": request["statistics"],
                        "code": "period_condition_not_prepared",
                        "detail": {
                            "issue": request.get("b_request_issue"),
                            "period_answers": copy.deepcopy(request["period_answers"]),
                        },
                    }
                )
                continue
            if prepared is None and request["period_answers"] and not getattr(
                self.b_factory, "accepts_period_answers", False
            ):
                unsupported.append(
                    {
                        "statistics": request["statistics"],
                        "code": "period_answer_contract_pending",
                        "detail": {"period_answers": copy.deepcopy(request["period_answers"])},
                    }
                )
                continue
            b_requests.append(
                {
                    "request_id": request["request_id"],
                    "requirement_ids": request["statistics"],
                    "tables": [
                        {k: v for k, v in t.items() if v is not None}
                        for t in (prepared["tables"] if prepared else request["tables"])
                    ],
                    "period_answers": copy.deepcopy(request["period_answers"]),
                    "assignments": copy.deepcopy(request["assignments"]),
                    "calculations": copy.deepcopy(request["calculations"]),
                    **({"period_condition": True} if prepared else {}),
                }
            )
        # The wording of statistics that are NOT handed to B (blocked, failed, awaiting an answer) stays in the question:
        # B must hide it from the statistics it does read, so it travels with every request (see `_period_context`).
        handed = {sid for request in b_requests for sid in request["requirement_ids"]}
        unhanded = [
            {"ref": s["statistic"], "period_phrase": " / ".join(q for q in s["conditions"]["periods"] if q)}
            for s in stats
            if s["statistic"] not in handed and any((s.get("conditions") or {}).get("periods") or [])
        ]
        for request in b_requests:
            request["unhanded_period_wording"] = copy.deepcopy(unhanded)
        blocked = view["handoff"]["blocked"] + unsupported
        state["b_requests"] = copy.deepcopy(b_requests)
        state["unsupported_links"] = [copy.deepcopy(b) for b in blocked if b["code"] in UNSUPPORTED]
        state["handoff_notes"] = [
            {"statistics": r["statistics"], **copy.deepcopy(n)} for r in view["handoff"]["ready"] for n in r["notes"]
        ]
        sent = {s for r in b_requests for s in r["requirement_ids"]}
        state["a_warnings"] = [
            {"statistic": s["statistic"], "quote": s["quote"], "tables": [t["table_id"] for t in s["tables"]], **w}
            for s in stats
            if s["statistic"] in sent
            for w in s["warnings"]
        ]
        for w in state["a_warnings"]:
            w["text"] = warning_text(w)
        notes = [f"{', '.join(b['statistics'])}: {BLOCKED_TEXT.get(b['code'], b['code'])}" for b in blocked]
        notes += [
            f"{', '.join(n['statistics'])}: {NOTE_TEXT.get(n['code'], n['code'])}" for n in state["handoff_notes"]
        ]
        notes += [f"경고 · {w['text']}" for w in state["a_warnings"]]
        if not b_requests:
            code = "a_handoff_unsupported" if state["unsupported_links"] else "a_no_table"
            update("table", "failed", MESSAGES[code], listing=listing, note="\n".join(notes))
            raise StageFailure(code, "unsupported" if state["unsupported_links"] else "unresolved")
        update(
            "table",
            "warning" if blocked else "completed",
            f"통계 {sum(len(r['requirement_ids']) for r in b_requests)}개의 표를 B로 넘깁니다."
            + (" 일부 통계는 넘기지 않았습니다." if blocked else ""),
            [
                [
                    "B 전달 정보",
                    "원래 질문 + 기관·표 코드·표 이름 + 표별 지표(focus)"
                    + (" + 표별 기간 조건" if any(r.get("period_condition") for r in b_requests) else ""),
                ],
                ["확정 방식", "사용자 답변 적용 후 코드 검사" if resumed else "새 A 검사 통과"],
            ],
            [
                f"{', '.join(r['requirement_ids'])} · "
                + ", ".join(f"{t.get('table_name')} [{t['table_id']}] · focus: {t.get('focus')}" for t in r["tables"])
                for r in b_requests
            ],
            "\n".join(notes),
        )
        state["a_blocked"] = copy.deepcopy(blocked)
        a_failures = [("table", b["code"], "unresolved") for b in blocked]
        return b_requests, a_failures, listing

    def _b_conditions_lost(self, state, group, b_outcome):
        """Each table B selected must keep A's frequency and requested periods exactly; anything else is a lost
        condition: not fetched, reported. Periods B adds before A's start for a calculation are not allowed either:
        B's result does not state the calculation or the base range it added, so they cannot be told apart from a
        wider reading of the question (``kind: added_before_start`` in the detail; allowed only once B states them)."""
        if group.get("period_condition"):
            return self._answered_period_lost(state, group, b_outcome)
        if not group.get("assignments"):
            return None
        by_table = {a["table_id"]: a for a in group["assignments"]}
        lost = []
        for sel in (b_outcome.get("result") or {}).get("selections") or []:
            a, p = by_table.get(sel.get("table_id")), sel.get("period") or {}
            row = {
                "table_id": sel.get("table_id"),
                "a": {"frequency": a and a["frequency"], "period": a and a["period"]},
                "b": copy.deepcopy(p),
            }
            if a is None or p.get("frequency") != a["frequency"]:
                lost.append(row)
                continue
            try:
                got = set(periods_between(p["start"], p["end"], p["frequency"]))
            except (PeriodError, KeyError, TypeError):
                lost.append(row)
                continue
            want = _a_periods(a["period"], a["frequency"])
            if got == want:
                continue
            first = min(period_index(x, a["frequency"]) for x in want) if want else None
            before = bool(want) and want <= got and all(period_index(x, a["frequency"]) < first for x in got - want)
            lost.append({**row, "kind": "added_before_start" if before else "differs"})
        if not lost:
            return None
        link = {"statistics": list(group["requirement_ids"]), "code": "b_period_differs_from_a", "detail": lost}
        state.setdefault("unsupported_links", []).append(link)
        state.setdefault("a_blocked", []).append(link)
        return "b_period_differs_from_a"

    def _answered_period_lost(self, state, group, b_outcome):
        """B took A's period conditions: it must say, per table, what it applied (`period_resolutions`).

        What stops the run is only a condition the USER set not being kept: a table without a resolution, an
        answered period or frequency that B did not apply as given, or a selection outside the range B reports as
        fetched. A difference in a field nobody answered (a relative wording, a catalog frequency) is B's own reading
        and not a reason to stop; it is recorded in ``period_reference_differences``."""
        result = b_outcome.get("result") or {}
        rows = {r["table_id"]: r for r in result.get("period_resolutions") or []}
        lost, differences = [], []
        for table in group["tables"]:
            condition, row = table["period_condition"], rows.get(table["table_id"])
            if row is None:
                lost.append({"table_id": table["table_id"], "kinds": ["resolution_missing"]})
                continue
            applied = row.get("applied_sources") or {}
            differs = (row.get("reference") or {}).get("differs") or {}
            kinds = []
            if condition["period_source"] == "user_answer" and (
                applied.get("period") != "a_user_answer" or differs.get("period") is not False
            ):
                kinds.append("answered_period_not_applied")
            if condition["frequency_source"] == "user_answer" and (
                applied.get("frequency") != "a_user_answer" or row.get("frequency") != condition["frequency"]
            ):
                kinds.append("answered_frequency_not_applied")
            fetched = row.get("fetched") or []
            first, last = min((x["start"] for x in fetched), default=None), max((x["end"] for x in fetched), default=None)
            for sel in result.get("selections") or []:
                p = sel.get("period") or {}
                if sel.get("table_id") == table["table_id"] and (
                    p.get("frequency") != row.get("frequency")
                    or first is None
                    or not first <= str(p.get("start")) <= str(p.get("end")) <= last
                ):
                    kinds.append("selection_outside_resolution")
                    break
            if kinds:
                lost.append(
                    {
                        "table_id": table["table_id"],
                        "kinds": kinds,
                        "a": {
                            "frequency": condition["frequency"],
                            "period": copy.deepcopy(condition["period"]),
                            "sources": {"period": condition["period_source"], "frequency": condition["frequency_source"]},
                        },
                        "b": {
                            "frequency": row.get("frequency"),
                            "requested": copy.deepcopy(row.get("requested")),
                            "applied_sources": copy.deepcopy(applied),
                        },
                    }
                )
            elif differs.get("period") or differs.get("frequency"):
                differences.append({"table_id": table["table_id"], "differs": copy.deepcopy(differs)})
        state.setdefault("period_reference_differences", []).extend(differences)
        if not lost:
            return None
        link = {"statistics": list(group["requirement_ids"]), "code": "b_answered_period_not_applied", "detail": lost}
        state.setdefault("unsupported_links", []).append(link)
        state.setdefault("a_blocked", []).append(link)
        return "b_answered_period_not_applied"

    def _finish(self, state, emit):
        warnings = state.get("a_warnings") or []
        if warnings and state.get("ui_cases"):
            # shown on the result cards of the tables they are about; the run status does not change for them
            for case in state["ui_cases"]:
                shown = set(str(case.get("tableId", "")).split(" · "))
                case["warnings"] = list(case.get("warnings") or []) + [
                    "기준 미확인 · " + w["text"] for w in warnings if shown & set(w["tables"])
                ]
            if state.get("status") == "ok":
                state["message"] = (
                    f"조회를 마쳤습니다. A 검사 경고 {len(warnings)}건이 있습니다(결과 카드와 아래 안내 참고)."
                )
        # a partial run names the statistics A did not hand over, in words (B and fetch failures stay as codes)
        if state.get("status") == "partial" and state.get("a_blocked"):
            lines = [
                f"{', '.join(b['statistics'])}: {BLOCKED_TEXT.get(b['code'], b['code'])}" for b in state["a_blocked"]
            ]
            mine = {b["code"] for b in state["a_blocked"]}
            others = [c for c in (state.get("message") or "").partition(": ")[2].split(", ") if c and c not in mine]
            state["message"] = (
                "일부 통계만 조회했습니다. "
                + " / ".join(lines)
                + (f" 그 밖의 중단 사유: {', '.join(others)}" if others else "")
            )
        return super()._finish(state, emit)

    def _resolve_extra(self, group, b):
        if group.get("period_answers") and getattr(b, "accepts_period_answers", False):
            return {"period_answers": copy.deepcopy(group["period_answers"])}
        return {}
