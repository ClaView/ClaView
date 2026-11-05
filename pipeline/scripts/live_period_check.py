"""Live check of the period path: scripted A -> real B model -> real KOSIS -> C cards (approved calls only).

What runs live: B's selection model (`gpt-6.1-terra`) and KOSIS. A's judgments are SCRIPTED (no A model call; the
new A's live calls stay off) and C needs no model. So this checks how B's period decision (`period_resolutions`),
the fetched values and the card's period block behave on real answers; it says nothing about A's accuracy.

Call limits are enforced here, not only promised: every B model call and every KOSIS request is counted by a wrapper
that stops the whole check (the case in progress is recorded as stopped) when a limit would be exceeded.

    python -B -X utf8 pipeline/scripts/live_period_check.py --out runs/live-period-1103 --max-openai 12 --max-kosis 30
    python -B -X utf8 pipeline/scripts/live_period_check.py --dry-run --out runs/live-period-dry    (synthetic, no key)

Cases L7-L10 (2026-11-03) cover the later period changes: one B call per requirement group with the other groups'
period wording handed over (L7, L10), stated years only partly inside the table (L8, L9) and the faded base period
of a year-over-year request (L4). L7 lifts A's block on statistics with different periods for this check only
(`view._months`, the way it would be after A relaxes it); nothing else in A changes.

L4 also checks the % card C now calculates for a year-over-year request on an amount series (2026-11-04), and L11 the
month-wise period a user answered (B-1: the answered period is part of the model request). The expected end of L8
follows the table's metadata, which is refreshed from time to time.

Keys are read from the environment only (OPENAI_API_KEY, KOSIS_API_KEY) and never printed or written.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from claview_a_api import screen as a_screen
from claview_a_api import view as a_view
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_b_api import service as b_service
from claview_b_api.service import store as load_store
from claview_common.settings import usable_key
from claview_pipeline.screen import ScreenPipeline

STATISTIC = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}]
TWO_STATISTICS = [*STATISTIC, {"id": "s2", "quote": "예금은행 대출금리", "name": "rate"}]
TABLE = "DT_161Y014"  # M2 기관별 구성내역(말잔): the table most cases read
RATE_TABLE = "DT_121Y006"  # 예금은행 대출금리(신규취급액 기준): the second table of L7 and L10
AS_OF = "2026-11-03"


class CapReached(BaseException):
    """A call limit would be exceeded: not an Exception, so no broad `except Exception` can swallow it."""


class Budget:
    def __init__(self, openai: int, kosis: int):
        self.limit = {"openai": openai, "kosis": kosis}
        self.used = {"openai": 0, "kosis": 0}

    def take(self, kind: str):
        if self.used[kind] >= self.limit[kind]:
            raise CapReached(kind)
        self.used[kind] += 1


class CountedModel:
    """The B selection model; counts a call before it is made."""

    def __init__(self, inner, budget: Budget):
        self._inner, self._budget = inner, budget

    def complete(self, *args, **kwargs):
        self._budget.take("openai")
        return self._inner.complete(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class CountedClient:
    """The KOSIS client; counts a request before it is made."""

    def __init__(self, inner, budget: Budget):
        self._inner, self._budget = inner, budget

    def fetch(self, params, trace):
        self._budget.take("kosis")
        return self._inner.fetch(params, trace)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def script(period: str, frequency: str | None = "월별") -> dict:
    return {
        "statistics": STATISTIC,
        "choices": [{"for": ["s1"], "table": TABLE}],
        "periods": [{"quote": period, "for": ["s1"]}],
        **({"frequencies": [{"quote": frequency, "for": ["s1"]}]} if frequency else {}),
    }


def card_ok(state):
    return bool(state.get("ui_cases"))


def card_of(state, table_id):
    """The card of one table (cards of different tables are separate), or None."""
    return next((c for c in state.get("ui_cases") or [] if c["tableId"] == table_id), None)


def card_range(state, table_id):
    c = card_of(state, table_id)
    return (c["freq"], c["start"], c["end"]) if c else None


def first(state):
    """The first RAW card (a rate C calculates puts its derived card in front of the raw one)."""
    return next(c for c in state["ui_cases"] if not c.get("derived"))


def derived_cards(state):
    return [c for c in state.get("ui_cases") or [] if c.get("derived")]


def m2_coverage():
    """(label of the table's last month, label of the first month after it) from the metadata B reads.

    The expected end of a partly-outside request follows the metadata, which is refreshed from time to time."""
    end = b_service.store().get("301", TABLE)["periods"]["M"]["end"]
    year, month = int(end[:4]), int(end[4:])
    after = (year + (month == 12), month % 12 + 1)
    return f"{year}년 {month}월", f"{after[0]}년 {after[1]}월"


def info(state):
    return (first(state).get("periodInfo") or [{}])[0]


def shown(state):
    """(frequency, first label, last label) of the first card."""
    c = first(state)
    return c["freq"], c["start"], c["end"]


def said(state):
    """How many times the card tells that the table could only be read for part of the period."""
    c = first(state)
    return len([t for t in [*c["notices"], *c["warnings"]] if "조회 가능한 기간으로" in t])


# id, query, A script, as_of, answer to A's period question (None: no question), checks (name -> predicate on state)
CASES = [
    {
        "id": "L1_quoted_year",
        "query": "M2 통화량 2023년 연간으로 보여줘",
        "script": script("2023년", "연간"),
        "checks": {
            "카드 있음": card_ok,
            "연간 2023년 한 시점": lambda s: shown(s) == ("Y", "2023년", "2023년"),
            "미수록 없음": lambda s: info(s).get("missing") == [],
            "조회된 값 있음": lambda s: first(s)["quality"]["observed_values"] >= 1,
        },
    },
    {
        "id": "L2_answered_period",
        "query": "M2 통화량을 분기별로 보여줘",
        "script": {
            "statistics": STATISTIC,
            "choices": [{"for": ["s1"], "table": TABLE}],
            "frequencies": [{"quote": "분기별", "for": ["s1"]}],
        },
        "answer": "2024년 1월부터 6월까지",
        "checks": {
            "카드 있음": card_ok,
            "기간 출처가 사용자 답": lambda s: info(s).get("sources", {}).get("period") == "사용자가 고른 값",
            "분기 2024년 1~2분기": lambda s: shown(s) == ("Q", "2024년 1분기", "2024년 2분기"),
            "요청과 조회가 같음": lambda s: info(s).get("requested") == info(s).get("fetched"),
        },
    },
    {
        "id": "L3_this_year_lag",
        "query": "M2 통화량 올해 월별로 보여줘",
        "script": script("올해"),
        "checks": {
            "카드 있음": card_ok,
            "2026년 1월부터": lambda s: first(s)["start"] == "2026년 1월",
            "표에 없는 기간을 알림": lambda s: bool(info(s).get("missing")),
            "안내 문구가 한 번": lambda s: said(s) == 1,
        },
    },
    {
        "id": "L4_yoy_calc_range",
        "query": "M2 통화량 2024년 월별 전년 대비 증감 보여줘",
        "script": script("2024년"),
        "checks": {
            "카드 있음": card_ok,
            "표시는 2024년 12개월": lambda s: shown(s) == ("M", "2024년 1월", "2024년 12월")
            and len(first(s)["periods"]) == 12,
            "계산용 구간(2023년) 조회": lambda s: bool(info(s).get("extra"))
            and info(s).get("fetched") == ["2023년 1월 ~ 2024년 12월"],
            "표시 범위가 요청 기간": lambda s: first(s)["requestedPeriod"]
            == {"frequency": "M", "start": "202401", "end": "202412"},
            "기준 연도(2023년)를 따로 실음": lambda s: first(s).get("baseline", {}).get("labels", [])[:1]
            == ["2023년 1월"]
            and len(first(s)["baseline"]["periods"]) == 12,
            "기준 값이 실제로 조회됨": lambda s: any(
                v is not None for values in first(s)["baseline"]["values"].values() for v in values
            ),
            "표 값은 요청 기간만": lambda s: all(len(x["values"]) == 12 for x in first(s)["series"]),
            # 2026-11-04: M2 금액 계열의 전년 동월 대비를 C가 직접 계산한다(원자료 카드는 그대로 두고 앞에 둔다)
            "증감률(%) 계산 카드가 12개월": lambda s: any(
                c["unit"] == "%"
                and len(c["series"][0]["values"]) == 12
                and all(v is not None for v in c["series"][0]["values"])
                and "baseline" not in c
                for c in derived_cards(s)
            ),
        },
    },
    {
        "id": "L5_last_year_substituted",
        "query": "M2 통화량 작년 월별로 보여줘",
        "script": script("작년"),
        "as_of": "2028-03-01",
        "checks": {
            "카드 있음": card_ok,
            "대체 조회로 표시": lambda s: info(s).get("substituted") is True,
            "요청한 2027년이 미수록": lambda s: info(s).get("missing") == ["2027년 1월 ~ 2027년 12월"],
        },
    },
    {
        "id": "L6_recent_three_years",
        "query": "M2 통화량 최근 3년 연간으로 보여줘",
        "script": script("최근 3년", "연간"),
        "checks": {
            "카드 있음": card_ok,
            "연간 3시점": lambda s: (first(s)["freq"], len(first(s)["periods"])) == ("Y", 3),
        },
    },
    {
        "id": "L7_two_tables_own_years",
        "query": "M2 통화량 2023년과 예금은행 대출금리 2024년 연간",
        "script": {
            "statistics": TWO_STATISTICS,
            "periods": [{"quote": "2023년", "for": ["s1"]}, {"quote": "2024년", "for": ["s2"]}],
            "frequencies": [{"quote": "연간", "for": ["s1", "s2"]}],
            "choices": [{"for": ["s1"], "table": TABLE}, {"for": ["s2"], "table": RATE_TABLE}],
        },
        "lift_a_block": True,
        "checks": {
            "카드 두 장": lambda s: len(s.get("ui_cases") or []) == 2,
            "M2는 2023년만": lambda s: card_range(s, TABLE) == ("Y", "2023년", "2023년"),
            "대출금리는 2024년만": lambda s: card_range(s, RATE_TABLE) == ("Y", "2024년", "2024년"),
            "표마다 B를 따로 호출": lambda s: len(s["stage_outcomes"]["B"]) == 2,
        },
    },
    {
        "id": "L8_partly_outside_table",
        "query": "M2 통화량 2020년부터 2027년까지 월별로 보여줘",
        "script": script("2020년부터 2027년까지"),
        "checks": {
            "카드 있음": card_ok,
            "수록된 부분만 표시": lambda s: first(s)["start"] == "2020년 1월" and first(s)["end"] == m2_coverage()[0],
            "미수록 구간을 알림": lambda s: info(s).get("missing") == [f"{m2_coverage()[1]} ~ 2027년 12월"],
            "안내 문구가 한 번": lambda s: said(s) == 1,
            "조회된 값 있음": lambda s: first(s)["quality"]["observed_values"] >= 1,
        },
    },
    {
        "id": "L9_first_year_yoy",
        "query": "M2 통화량 2003년 연간 전년 대비 증감 보여줘",
        "script": script("2003년", "연간"),
        "checks": {
            "멈추지 않고 카드 있음": card_ok,
            "2003년 한 시점": lambda s: shown(s) == ("Y", "2003년", "2003년"),
            "표에 없는 기준 연도는 읽지 않음": lambda s: not info(s).get("extra") and "baseline" not in first(s),
        },
    },
    {
        "id": "L10_one_statistic_dropped",
        "query": "M2 통화량 2023년과 예금은행 대출금리 2024년 연간",
        "script": {
            "statistics": TWO_STATISTICS,
            "periods": [{"quote": "2023년", "for": ["s1"]}, {"quote": "2024년", "for": ["s2"]}],
            "frequencies": [{"quote": "연간", "for": ["s1", "s2"]}],
            "choices": [{"for": ["s2"], "table": RATE_TABLE}],
            "missing": [{"for": ["s1"], "search_words": ["M2"]}],
        },
        "checks": {
            "남은 표의 카드 있음": lambda s: card_range(s, RATE_TABLE) is not None,
            "남은 표는 2024년만": lambda s: card_range(s, RATE_TABLE) == ("Y", "2024년", "2024년"),
            "일부 통계만 조회": lambda s: s["status"] == "partial",
        },
    },
    {
        # 2026-11-04 (B-1): the period the user answered is not in the question; the model request carries it
        "id": "L11_answered_monthly",
        "query": "M2 통화량을 월별로 보여줘",
        "script": {
            "statistics": STATISTIC,
            "choices": [{"for": ["s1"], "table": TABLE}],
            "frequencies": [{"quote": "월별", "for": ["s1"]}],
        },
        "answer": "2024년 월별",
        "checks": {
            "카드 있음": card_ok,
            "기간 출처가 사용자 답": lambda s: info(s).get("sources", {}).get("period") == "사용자가 고른 값",
            "월별 2024년 1~12월": lambda s: shown(s) == ("M", "2024년 1월", "2024년 12월")
            and len(first(s)["periods"]) == 12,
            "요청과 조회가 같음": lambda s: info(s).get("requested") == info(s).get("fetched"),
        },
    },
]


def build_clients(dry_run: bool, budget: Budget):
    if dry_run:
        model, client = b_service.synthetic_clients()
    else:
        openai, kosis = usable_key(os.environ.get("OPENAI_API_KEY")), usable_key(os.environ.get("KOSIS_API_KEY"))
        if not openai or not kosis:
            missing = [n for n, k in (("OPENAI_API_KEY", openai), ("KOSIS_API_KEY", kosis)) if not k]
            raise SystemExit("키가 없거나 사용할 수 없습니다: " + ", ".join(missing) + " (값은 출력하지 않습니다)")
        model, client = b_service.live_clients(openai_key=openai, kosis_key=kosis, retries=0)
    return CountedModel(model, budget), CountedClient(client, budget)


@contextlib.contextmanager
def lifted_a_block(on: bool):
    """For this check only: A's block on statistics with different periods is lifted (A relaxes it later)."""
    if not on:
        yield
        return
    original = a_view._months
    a_view._months = lambda stat: frozenset()
    try:
        yield
    finally:
        a_view._months = original


def run_case(case: dict, model, client, session, runs_dir: Path):
    judge = ScriptJudge(by_question({case["query"]: case["script"]}))
    pipeline = ScreenPipeline(
        model=model,
        client=client,
        runs_dir=runs_dir,
        session=session,
        judges_factory=lambda: a_screen.Judges(judge, judge, "script"),
    )
    with lifted_a_block(case.get("lift_a_block", False)):
        state = pipeline.run(case["query"], as_of=case.get("as_of", AS_OF))
    if case.get("answer") and state["status"] == "needs_confirmation":
        card = state["confirmation"]
        (question,) = card["groups"]
        state = pipeline.resume(
            state,
            {
                "session_id": card["session_id"],
                "answers": [
                    {
                        "question_id": question["requirement_id"],
                        "question_version": question["question_version"],
                        "text": case["answer"],
                    }
                ],
            },
        )
    return state


def summarize(case: dict, state: dict, used_before: dict, used: dict) -> dict:
    rows = [
        row
        for b in state.get("stage_outcomes", {}).get("B") or []
        for row in ((b or {}).get("result") or {}).get("period_resolutions") or []
    ]
    cards = state.get("ui_cases") or []
    checks = {}
    for name, predicate in case["checks"].items():
        try:
            checks[name] = bool(predicate(state))
        except Exception as exc:  # a missing key is a failed check, not a crashed check run
            checks[name] = f"error: {type(exc).__name__}"
    return {
        "case": case["id"],
        "query": case["query"],
        "as_of": case.get("as_of", AS_OF),
        "status": state["status"],
        "error_code": state.get("error_code"),
        "message": state.get("message"),
        "calls": {k: used[k] - used_before[k] for k in used},
        "period_resolutions": rows,
        "cards": [
            {
                "tableId": c["tableId"],
                "start": c["start"],
                "end": c["end"],
                "freq": c["freq"],
                "periods": len(c["periods"]),
                "requestedPeriod": c["requestedPeriod"],
                "periodInfo": c.get("periodInfo"),
                **(
                    {"baseline": {"periods": len(c["baseline"]["periods"]), "labels": c["baseline"]["labels"][:1]
                                  + c["baseline"]["labels"][-1:]}}
                    if c.get("baseline")
                    else {}
                ),
                "warnings": c["warnings"],
                "notices": c["notices"],
                "quality": c["quality"],
                "series": [
                    {"name": s["name"], "first": s["values"][:1], "last": s["values"][-1:]} for s in c["series"]
                ],
            }
            for c in cards
        ],
        "checks": checks,
    }


def markdown(results: list[dict], budget: Budget, stopped: str | None) -> str:
    lines = [
        "# 기간 경로 실호출 점검 결과",
        "",
        f"- B 모델 호출 {budget.used['openai']}/{budget.limit['openai']}회, "
        f"KOSIS 요청 {budget.used['kosis']}/{budget.limit['kosis']}회",
        "- A 판정은 스크립트(모델 호출 없음). 이 점검은 A의 정확도를 보여 주지 않음.",
    ]
    if stopped:
        lines.append(f"- 호출 한도({stopped})에 도달해 점검을 중단함. 진행 중이던 질문은 '중단'으로 기록함.")
    lines += ["", "| 질문 | 상태 | 호출(모델/KOSIS) | 확인 항목 |", "|---|---|---|---|"]
    for r in results:
        checks = r.get("checks") or {}
        good = sum(v is True for v in checks.values())
        calls = r.get("calls") or {}
        lines.append(
            f"| {r['case']} | {r['status']}{' / ' + r['error_code'] if r.get('error_code') else ''} "
            f"| {calls.get('openai', 0)}/{calls.get('kosis', 0)} | {good}/{len(checks)} 통과 |"
        )
    for r in results:
        failed = [k for k, v in (r.get("checks") or {}).items() if v is not True]
        if failed:
            lines += ["", f"## {r['case']} 확인하지 못한 항목", "", *[f"- {k}: {r['checks'][k]}" for k in failed]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, help="folder for summary.json, summary.md and the run records")
    p.add_argument("--max-openai", type=int, default=12)
    p.add_argument("--max-kosis", type=int, default=30)
    p.add_argument("--dry-run", action="store_true", help="synthetic model and fetch: no key, no external call")
    p.add_argument("--only", help="run only these case ids (comma separated)")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    budget = Budget(a.max_openai, a.max_kosis)
    model, client = build_clients(a.dry_run, budget)
    session = SimpleNamespace(store=load_store())
    wanted = set(a.only.split(",")) if a.only else None
    results, stopped = [], None
    for case in CASES:
        if wanted and case["id"] not in wanted:
            continue
        before = dict(budget.used)
        try:
            state = run_case(case, model, client, session, out / "runs")
            result = summarize(case, state, before, budget.used)
        except CapReached as exc:
            stopped = str(exc)
            result = {
                "case": case["id"],
                "query": case["query"],
                "status": "stopped",
                "error_code": f"cap_{stopped}",
                "calls": {k: budget.used[k] - before[k] for k in before},
                "checks": {},
            }
        results.append(result)
        print(f"{result['case']}: {result['status']} calls={result['calls']}", flush=True)
        if stopped:
            break
    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.md").write_text(markdown(results, budget, stopped), encoding="utf-8")
    print(
        f"used: openai {budget.used['openai']}/{budget.limit['openai']}, "
        f"kosis {budget.used['kosis']}/{budget.limit['kosis']}"
    )
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
