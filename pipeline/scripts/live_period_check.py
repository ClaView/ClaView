"""Live check of the period path: scripted A -> real B model -> real KOSIS -> C cards (approved calls only).

What runs live: B's selection model (`gpt-6.1-terra`) and KOSIS. A's judgments are SCRIPTED (no A model call; the
new A's live calls stay off) and C needs no model. So this checks how B's period decision (`period_resolutions`),
the fetched values and the card's period block behave on real answers; it says nothing about A's accuracy.

Call limits are enforced here, not only promised: every B model call and every KOSIS request is counted by a wrapper
that stops the whole check (the case in progress is recorded as stopped) when a limit would be exceeded.

    python -B -X utf8 pipeline/scripts/live_period_check.py --out runs/live-period-1103 --max-openai 12 --max-kosis 30
    python -B -X utf8 pipeline/scripts/live_period_check.py --dry-run --out runs/live-period-dry    (synthetic, no key)

Keys are read from the environment only (OPENAI_API_KEY, KOSIS_API_KEY) and never printed or written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_b_api import service as b_service
from claview_common.settings import usable_key
from claview_pipeline import FlowSession
from claview_pipeline.screen import ScreenPipeline

STATISTIC = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}]
TABLE = "DT_161Y014"  # M2 기관별 구성내역(말잔): the table every case reads
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


def first(state):
    return state["ui_cases"][0]


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


def run_case(case: dict, model, client, session, runs_dir: Path):
    judge = ScriptJudge(by_question({case["query"]: case["script"]}))
    pipeline = ScreenPipeline(
        model=model,
        client=client,
        runs_dir=runs_dir,
        session=session,
        judges_factory=lambda: a_screen.Judges(judge, judge, "script"),
    )
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
    b = (state.get("stage_outcomes", {}).get("B") or [{}])[0] or {}
    rows = (b.get("result") or {}).get("period_resolutions") or []
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
                "start": c["start"],
                "end": c["end"],
                "freq": c["freq"],
                "periods": len(c["periods"]),
                "requestedPeriod": c["requestedPeriod"],
                "periodInfo": c.get("periodInfo"),
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
    session = FlowSession.load()
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
