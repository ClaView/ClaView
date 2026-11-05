# ruff: noqa: E501  (user-facing Korean text: ruff counts full-width characters as two columns)
"""다중 통계표·이중 축 골든셋(v3) 재생기: 같은 파이프라인(B 조건 결정 → 조회 → 정렬 → C)으로 시험한다.

A 판단은 평가하지 않으며, 골든 표를 AView 경로의 B 입력으로 명시적으로 전달한다.
B 이후(`claview_b_api.service`의 조건 결정·조회·정렬, C 표시)는 서비스 흐름(`claview_pipeline.staged`)이 그대로 돈다.

모드
  replay (기본) : B 선택 = 골든 정답 재생, 조회 값 = 합성 시험값. 외부 호출 없음(네트워크 차단).
  kosis         : B 선택 = 골든 정답 재생, 조회 = 실제 KOSIS. KOSIS 키 필요, --allow-external 필요.
  hcx           : B 선택 = 실제 HCX, 조회 = 실제 KOSIS. HCX·KOSIS 키 필요, --allow-external 필요.

명령 (저장소 루트 기준, PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline)
  python -B -X utf8 pipeline/scripts/golden_replay.py check
  python -B -X utf8 pipeline/scripts/golden_replay.py check --mode kosis --allow-external --prompt-keys

키는 환경변수(CLOVASTUDIO_API_KEY, KOSIS_API_KEY) 또는 숨김 입력으로만 받으며 파일·출력에 남기지 않는다.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import getpass
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GOLDEN = ROOT / "eval" / "cases" / "golden" / "golden_set_v3_multi_table.json"
REPLAY_VALUES = ROOT / "eval" / "cases" / "golden" / "golden_set_v3_multi_table.replay_values.json"
AS_OF = "2026-10-22"
SYNTHETIC_NOTICE = "시험용 재생: 이 값은 합성한 시험값이며 KOSIS 응답이 아닙니다."

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _install_guard(mode: str):
    """Block every network transport in replay mode (the same guard the test suites use)."""
    if mode == "replay":
        from claview_a import _guard

        _guard.install_network_block()
        return _guard
    return None


def load_cases(path: Path = GOLDEN) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))["cases"]


def load_replay_values(path: Path = REPLAY_VALUES) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))["series"]


def series_key(table_id: str, item_id: str, dimensions: dict) -> tuple:
    """One series as KOSIS identifies it: table, item and the code on each classification axis."""
    codes = {a: (v["code"] if isinstance(v, dict) else v) for a, v in dimensions.items()}
    return (table_id, item_id, tuple(sorted(codes.items())))


# ---- synthetic fetch client -------------------------------------------------------------
class ReplayFetchClient:
    """KOSIS stand-in that returns deterministic SYNTHETIC values (never real statistics).

    It answers only the series of the golden cases and reports each series' own unit in `UNIT_NM`
    like KOSIS does, so B's unit check and the alignment run on realistic input.
    """

    label = SYNTHETIC_NOTICE

    def __init__(self, cases: list[dict], values: dict):
        self.values = values
        self.specs: dict[tuple, tuple[str, str]] = {}
        for case in cases:
            for s in case["expected"]["B"]["selections"]:
                self.specs[series_key(s["table_id"], s["item_id"], s["dimensions"])] = (
                    s["series_id"],
                    s["unit"],
                )
        self.calls: list[dict] = []
        self._cache: dict[str, list[Decimal]] = {}

    def _values(self, series_id: str, count: int) -> list[Decimal]:
        spec = self.values[series_id]
        if "same_as" in spec:
            base = self._values(spec["same_as"], count)
            return [v * Decimal(spec["factor"]) for v in base]
        out = []
        for i in range(count):
            raw = (
                spec["base"]
                + spec.get("step", 0) * i
                + spec.get("wave", 0) * math.sin(i * spec.get("speed", 1.3) + spec.get("phase", 0))
            )
            out.append(Decimal(str(round(raw, spec.get("decimals", 0)))))
        return out

    def fetch(self, params: dict, trace) -> list[dict]:
        from claview_b.contracts import StageFailure
        from claview_b.fetch import _RESPONSE_PRD_SE
        from claview_b.metadata import periods_between

        self.calls.append(dict(params))
        dims = {k: v for k, v in params.items() if k.startswith("objL")}
        key = series_key(params["tblId"], params["itmId"], dims)
        if key not in self.specs:
            raise StageFailure("replay_series_missing")
        series_id, unit = self.specs[key]
        periods = periods_between(params["startPrdDe"], params["endPrdDe"], params["prdSe"])
        values = self._values(series_id, len(periods))
        return [
            {
                "ORG_ID": params["orgId"],
                "TBL_ID": params["tblId"],
                "ITM_ID": params["itmId"],
                "PRD_SE": _RESPONSE_PRD_SE[params["prdSe"]],
                "PRD_DE": period,
                "DT": format(value, "f"),
                "UNIT_NM": unit,
                **{k.replace("objL", "C"): v for k, v in dims.items()},
            }
            for period, value in zip(periods, values, strict=True)
        ]


def query_key(text: str) -> str:
    """Spacing and a trailing period/question mark do not make a different golden question."""
    return "".join(text.split()).rstrip(".!?。")


NOT_GOLDEN_MESSAGE = (
    "골든셋 재생입니다. 골든셋(v3)에 없는 질문은 A 단계에서 멈춥니다. "
    "골든셋의 5개 질문 중 하나를 그대로 넣어 주세요."
)


# ---- A stand-in: the golden tables --------------------------------------------------------
def make_golden_a(cases: list[dict], *, table_focus: bool = False):
    """Explicit preselected-table handoff for B/C replay; it does not evaluate A."""
    by_query = {query_key(c["query"]): c for c in cases}

    class GoldenA:
        @staticmethod
        def select(query, as_of=None):
            from claview_common.contracts import BRequest
            from claview_pipeline.staged import StageFailure

            case = by_query.get(query_key(query))
            if case is None:
                raise StageFailure("a_no_table", "unresolved")
            tables = [{k: t[k] for k in ("org_id", "table_id", "table_name") if k in t}
                      | ({"focus": t["indicator"]} if table_focus and t.get("indicator") else {})
                      for t in case["expected"]["A"]["tables"]]
            checked = BRequest(query=query, as_of=as_of, tables=tables)
            return [{"requirement_ids": ["R1"],
                     "tables": [t.model_dump(exclude_none=True) for t in checked.tables],
                     "period_answers": [], "assignments": []}]

    return GoldenA


class GoldenAnswerModel:
    """B's selection model for replay: answers each B task with the stored golden answer, any number of times.

    B's own `ScriptedModel` is single-use per task, so a fresh one is built for every call (the same
    trace events and schema trimming apply). Counts its calls like the real model does.
    """

    label = "golden B answers replayed (not a model run)"

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls = 0

    def complete(self, task, system, context, schema, max_tokens, trace):
        from claview_b.models import ScriptedModel

        self.calls += 1
        return ScriptedModel({task: copy.deepcopy(self.answers[task])}).complete(
            task, system, context, schema, max_tokens, trace
        )


class AlignRecorder:
    """Wraps B's `align` (as the service flow calls it) to keep every alignment block it produced."""

    def __init__(self, real):
        self.real, self.blocks = real, []

    def __call__(self, data, **kw):
        out = self.real(data, **kw)
        self.blocks.append(copy.deepcopy(out["alignment"]))
        return out


@contextlib.contextmanager
def patched_pipeline(cases: list[dict], *, table_focus: bool = False, multi_table_prompt: bool = True):
    """Use a replay-only A adapter; production A selection is never monkeypatched."""
    from types import SimpleNamespace

    from claview_b import stages
    from claview_pipeline import staged as cp
    from claview_pipeline.screen import ScreenPipeline

    handoff = make_golden_a(cases, table_focus=table_focus)

    class GoldenPipeline(ScreenPipeline):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, judges_factory=lambda: None, **kwargs)

        def _run_a(self, state, query, resume, update):
            groups = handoff.select(query, state["as_of"])
            state["b_requests"] = groups
            state["a_model_mode"] = "preselected_tables"
            state["extra_calls"] = {"A_judge": 0}
            labels = [t["table_id"] for g in groups for t in g["tables"]]
            update("question", "completed", "골든 재생 입력 · A 판단 미실행")
            update("table", "completed", "골든 표를 B 입력으로 사용", listing=labels)
            return groups, [], labels

    original = (cp.b_service.align, cp.MESSAGES["a_no_table"], stages.MULTI_TABLE_PROMPT)
    recorder = AlignRecorder(cp.b_service.align)
    if not multi_table_prompt:
        stages.MULTI_TABLE_PROMPT = ""
    cp.b_service.align = recorder
    cp.MESSAGES["a_no_table"] = NOT_GOLDEN_MESSAGE
    namespace = SimpleNamespace(**vars(cp))
    namespace.Pipeline = GoldenPipeline
    try:
        yield namespace, recorder
    finally:
        cp.b_service.align, cp.MESSAGES["a_no_table"], stages.MULTI_TABLE_PROMPT = original


def make_runner(
    cp,
    cases: list[dict],
    mode: str,
    keys: dict,
    *,
    synthetic: ReplayFetchClient | None = None,
    hcx_retries: int = 0,
):
    """AView pipeline with explicitly preselected tables, with the model/client the mode asks for."""
    from claview_b.evaluation import scripted_answers

    answers: dict = {}
    for case in cases:
        for task, rows in scripted_answers(case).items():
            if task != "A":
                answers[task] = rows
    model = GoldenAnswerModel(answers) if mode in {"replay", "kosis"} else None
    if mode == "hcx":
        from claview_b.models import HcxModel

        # A 429 waits for the reset time the service reports (at most 60 s) and sends the same request again.
        model = HcxModel(keys["hcx"], retries=hcx_retries, min_interval=35)
    client = synthetic if mode == "replay" else None
    runner_cls = _replay_pipeline_class(cp.Pipeline) if mode == "replay" else cp.Pipeline
    return runner_cls(
        keys.get("hcx") if mode == "hcx" else None,
        keys.get("kosis") if mode in {"kosis", "hcx"} else None,
        model=model,
        client=client,
        runs_dir=None,
    )


def _replay_pipeline_class(base):
    class _ReplayPipeline(base):
        """Marks every replay result so synthetic values can never pass for KOSIS data."""

        def _finish(self, state, emit):
            for stage in state.get("stages", []):
                stage["rows"] = [
                    [k, "합성 시험값 · KOSIS 응답 아님" if k == "자료" else v]
                    for k, v in stage.get("rows") or []
                ]
            for card in state.get("ui_cases", []):
                card["notices"] = [SYNTHETIC_NOTICE, *card.get("notices", [])]
            state["synthetic_replay"] = True
            return super()._finish(state, emit)

    return _ReplayPipeline


# ---- checks -------------------------------------------------------------------------------
def _axes(axes) -> list[dict]:
    return [
        {"side": a["side"], "unit": a["unit"], "kind": a["kind"], "draw": a["draw"]}
        for a in axes or []
    ]


def _freq_conv(rows, table_of: dict) -> list[dict]:
    return [
        {
            "table_id": table_of[r["series_id"]],
            "from": r["from"],
            "to": r["to"],
            "method": r["method"],
        }
        for r in rows or []
    ]


def _unit_conv(rows, table_of: dict) -> list[dict]:
    return [
        {
            "table_id": table_of[r["series_id"]],
            "from_unit": r["from_unit"],
            "to_unit": r["to_unit"],
            "factor": r["factor"],
        }
        for r in rows or []
    ]


def compare_alignment(expected: dict, block: dict, table_of: dict, where: str) -> list[str]:
    got = {
        "status": block["status"],
        "common_frequency": (block.get("common_period") or {}).get("frequency"),
        "frequency_conversions": _freq_conv(block.get("frequency_conversions"), table_of),
        "unit_conversions": _unit_conv(block.get("unit_conversions"), table_of),
        "axes": _axes(block.get("axes")),
        "notice_codes": sorted(n["code"] for n in block.get("notices") or []),
    }
    want = {
        "status": expected["status"],
        "common_frequency": expected["common_frequency"],
        "frequency_conversions": expected["frequency_conversions"],
        "unit_conversions": expected["unit_conversions"],
        "axes": expected["axes"],
        "notice_codes": sorted(expected["notice_codes"]),
    }
    return [f"{where}.{k}: 기대 {want[k]} / 실제 {got[k]}" for k in want if got[k] != want[k]]


def describe_selection(s: dict) -> str:
    """One selection as a reviewer reads it: table, item/classification names and codes, period."""
    dims = ", ".join(f"{d['name']}[{d['code']}]" for d in s["dimensions"].values())
    p = s["period"]
    return f"{s['table_id']} · {dims} · {p['frequency']} {p['start']}~{p['end']}"


def run_case(
    cp, recorder, runner, case: dict, *, as_of: str = AS_OF, compare_selections: bool = True
) -> dict:
    """Run one golden case through the pipeline (answering the frequency question if asked)."""
    from claview_b.stages import selection_key

    expected = case["expected"]
    recorder.blocks.clear()
    problems: list[str] = []
    # Alignment and series-count differences: they follow from which series B chose, so they are
    # sorted out below once it is known whether B only ADDED series to the golden ones.
    dependent: list[str] = []
    first = runner.run(case["query"], as_of=as_of)
    stopped = [o for o in first["stage_outcomes"].get("B") or [] if (o or {}).get("status") != "ok"]
    if stopped:
        # B or the service stopped before choosing: nothing to compare (not a selection mismatch).
        codes = sorted(
            {i.get("code", "?") for o in stopped for i in (o or {}).get("issues", [])}
        ) or ["사유 없음"]
        problems.append(
            f"B 단계가 중단되어 비교하지 못함: {', '.join(codes)} (골든과의 차이가 아님. 외부 호출 오류면 잠시 뒤 이 사례만 다시 실행)"
        )
        return {
            "case_id": case["case_id"],
            "problems": problems,
            "notes": [],
            "missing": [],
            "extra": [],
            "first": first,
            "final": first,
            "card": None,
            "blocks": [],
        }
    # B renames series (S001, S002, ...): map them back to their tables for the comparison.
    table_of = {
        s["series_id"]: s["table_id"]
        for o in first["stage_outcomes"].get("B") or []
        for s in ((o or {}).get("result") or {}).get("selections", [])
    }
    question = expected["alignment"].get("frequency_question")
    final = first
    if question:
        block = recorder.blocks[-1] if recorder.blocks else {}
        if (
            first.get("status") != "needs_confirmation"
            or first.get("error_code") != "frequency_choice_required"
        ):
            problems.append(
                f"주기 질문이 나오지 않음: status={first.get('status')} code={first.get('error_code')}"
            )
        else:
            options = [o["option_id"] for o in (block.get("choice") or {}).get("options", [])]
            if options != question["option_ids"]:
                problems.append(f"질문 선택지: 기대 {question['option_ids']} / 실제 {options}")
            if (block.get("choice") or {}).get("target_frequency") != question["target_frequency"]:
                problems.append(
                    f"질문 목표 주기: 기대 {question['target_frequency']} / 실제 {(block.get('choice') or {}).get('target_frequency')}"
                )
            group = first["confirmation"]["groups"][0]
            answer = {
                "choices": [
                    {
                        "requirement_id": group["requirement_id"],
                        "option_id": question["answer_used_for_checks"],
                    }
                ],
                "interpretation_confirmed": False,
            }
            before = len(recorder.blocks)
            final = runner.resume(first, answer)
            if len(recorder.blocks) <= before:
                problems.append("답변 후 정렬이 다시 실행되지 않음")
            dependent += compare_alignment(
                expected["alignment"]["after_answer"],
                recorder.blocks[-1],
                table_of,
                "정렬(답변 후)",
            )
    else:
        if not recorder.blocks:
            problems.append("정렬 결과가 없음")
        else:
            dependent += compare_alignment(
                expected["alignment"], recorder.blocks[-1], table_of, "정렬"
            )
    if final.get("status") not in {"ok", "partial"}:
        problems.append(f"최종 상태: {final.get('status')} ({final.get('error_code')})")
    outcomes = final["stage_outcomes"].get("B") or first["stage_outcomes"].get("B") or []
    for o in outcomes:
        if (o or {}).get("status") != "ok":
            codes = (
                ", ".join(i.get("code", "?") for i in (o or {}).get("issues", [])) or "사유 없음"
            )
            problems.append(f"B 상태: {(o or {}).get('status')} ({codes})")
    got = {
        selection_key(s): s
        for o in outcomes
        for s in ((o or {}).get("result") or {}).get("selections", [])
    }
    want = {selection_key(s): s for s in expected["B"]["selections"]}
    only_golden = [describe_selection(want[k]) for k in want.keys() - got.keys()]
    only_b = [describe_selection(got[k]) for k in got.keys() - want.keys()]
    notes: list[str] = []
    if compare_selections and only_golden:
        problems.append(
            f"B 선택 차이 · 골든에만 있음: {only_golden} / B만 선택함: {only_b or '없음'}"
        )
    elif compare_selections and only_b:
        # Every golden series was chosen; B only added some. Reported as its own category.
        notes.append(f"추가 선택 {len(only_b)}개(골든 계열은 모두 포함): {only_b}")
    cards = final.get("ui_cases") or []
    card = cards[0] if cards else None
    if card is None:
        problems.append("표시 카드가 없음")
    else:
        if card.get("defaultView") != expected["C"]["default_view"]:
            dependent.append(
                f"기본 보기: 기대 {expected['C']['default_view']} / 실제 {card.get('defaultView')}"
            )
        blocked = {b["kind"] for b in card.get("blocked", [])}
        missing = set(expected["C"]["blocked_views"]) - blocked
        if missing:
            dependent.append(f"막혀야 할 보기가 막히지 않음: {sorted(missing)}")
        if not expected["C"]["blocked_views"] and "dual_axis" in card.get("kinds", []):
            dependent.append("이중 축이 필요 없는 질문에 이중 축이 나옴")
        if len(cards) != 1:
            # e.g. three unit kinds cannot share axes, so C shows each series on a card of its own
            dependent.append(f"카드 수: 기대 1 / 실제 {len(cards)} (카드가 나뉨)")
        shown = sum(len(c.get("series", [])) for c in cards)
        if shown != len(expected["B"]["selections"]):
            dependent.append(f"계열 수: 기대 {len(expected['B']['selections'])} / 실제 {shown}")
    if only_b and not only_golden and compare_selections:
        notes += [f"{p} (추가 계열의 영향일 수 있음)" for p in dependent]
    else:
        problems += dependent
    return {
        "case_id": case["case_id"],
        "problems": problems,
        "notes": notes,
        "missing": only_golden if compare_selections else [],
        "extra": only_b if compare_selections else [],
        "first": first,
        "final": final,
        "card": card,
        "blocks": copy.deepcopy(recorder.blocks),
    }


def verdict_of(row: dict) -> str:
    """통과 / 골든 계열 포함·추가 N개 / 불일치 (B only added series to the golden ones)."""
    if row["problems"]:
        return "불일치"
    if row["extra"]:
        return f"골든 계열 포함·추가 계열 {len(row['extra'])}개"
    return "통과"


# ---- CLI -----------------------------------------------------------------------------------
def obtain_keys(args) -> dict:
    keys = {"hcx": os.environ.get("CLOVASTUDIO_API_KEY"), "kosis": os.environ.get("KOSIS_API_KEY")}
    if args.prompt_keys:
        if args.mode == "hcx":
            keys["hcx"] = getpass.getpass("HCX key (hidden, memory only): ").strip() or keys["hcx"]
        keys["kosis"] = (
            getpass.getpass("KOSIS key (hidden, memory only): ").strip() or keys["kosis"]
        )
    return keys


def planned_calls(cases: list[dict], mode: str) -> dict:
    series = sum(len(c["expected"]["B"]["selections"]) for c in cases)
    tables = sum(len(c["expected"]["A"]["tables"]) for c in cases)
    return {
        "KOSIS": series if mode in {"kosis", "hcx"} else 0,
        "HCX": tables if mode == "hcx" else 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # The old local "serve" screen is not carried over: the service flow's screen is frontend/ + the pipeline API.
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--mode", choices=["replay", "kosis", "hcx"], default="replay")
    parser.add_argument(
        "--case", action="append", help="CV-BC-01 같은 사례 ID(반복 가능). 기본: 전체"
    )
    parser.add_argument(
        "--allow-external", action="store_true", help="kosis/hcx 모드의 실제 호출 허용"
    )
    parser.add_argument("--prompt-keys", action="store_true", help="키를 숨김 입력으로 받음")
    parser.add_argument(
        "--table-focus",
        action="store_true",
        help="A가 표별 지표(focus)를 B에 넘기는 상황을 흉내 냄: 골든의 표별 indicator를 focus로 전달",
    )
    parser.add_argument(
        "--no-multi-table-prompt",
        action="store_true",
        help="비교용: B의 '통계표가 여럿일 때' 프롬프트 규칙을 끄고 실행(--table-focus와 함께 쓰면 규칙 없이 focus만 확인)",
    )
    parser.add_argument(
        "--hcx-retries",
        type=int,
        choices=range(0, 4),
        default=0,
        help="hcx 모드: 429(한도 초과) 때 재시도 횟수(0~3, 기본 0). 재시도도 HCX 호출에 포함됨",
    )
    parser.add_argument("--out", help="결과 JSON 저장 폴더(저장소 밖 또는 runs/ 아래)")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    args = parser.parse_args(argv)

    guard = _install_guard(args.mode)
    cases = load_cases(args.golden)
    if args.command == "check" and args.case:
        unknown = set(args.case) - {c["case_id"] for c in cases}
        if unknown:
            parser.error(f"알 수 없는 사례: {sorted(unknown)}")
        cases = [c for c in cases if c["case_id"] in set(args.case)]
    plan = planned_calls(cases, args.mode)
    if args.mode != "replay":
        if not args.allow_external:
            parser.error("kosis/hcx 모드는 실제 호출을 하므로 --allow-external이 필요합니다.")
        print(
            f"[계획] 실제 호출 예정: KOSIS {plan['KOSIS']}회"
            + (
                f", HCX {plan['HCX']}회"
                + (
                    f"(429 재시도 포함 최대 {plan['HCX'] * (1 + args.hcx_retries)}회)"
                    if args.hcx_retries
                    else ""
                )
                if plan["HCX"]
                else ""
            )
            + f" (사례 {len(cases)}건)"
        )
    keys = obtain_keys(args) if args.mode != "replay" else {}
    needed = {"kosis": "KOSIS_API_KEY"} | (
        {"hcx": "CLOVASTUDIO_API_KEY"} if args.mode == "hcx" else {}
    )
    if args.mode != "replay":
        missing = [name for k, name in needed.items() if not keys.get(k)]
        if missing:
            parser.error("키가 없습니다(환경변수 또는 --prompt-keys): " + ", ".join(missing))

    with patched_pipeline(
        cases,
        table_focus=args.table_focus,
        multi_table_prompt=not args.no_multi_table_prompt,
    ) as (cp, recorder):
        synthetic = (
            ReplayFetchClient(cases, load_replay_values()) if args.mode == "replay" else None
        )
        runner = make_runner(
            cp,
            cases,
            args.mode,
            keys,
            synthetic=synthetic,
            hcx_retries=args.hcx_retries,
        )
        label = (
            {
                "replay": "합성 시험값 재생(외부 호출 없음)",
                "kosis": "골든 B 선택 + 실제 KOSIS 조회",
                "hcx": "실제 HCX B 선택 + 실제 KOSIS 조회",
            }[args.mode]
            + (" · 표별 지표(focus) 전달" if args.table_focus else "")
            + (" · 다중 표 규칙 끔" if args.no_multi_table_prompt else "")
        )
        try:
            print(f"[골든셋 v3 점검 · {label}] 사례 {len(cases)}건")
            results = []
            for case in cases:
                row = run_case(cp, recorder, runner, case)
                results.append(row)
                verdict = verdict_of(row)
                print(
                    f"- {case['case_id']} {verdict} · {case['purpose'] if 'purpose' in case else ''}"
                )
                for p in row["problems"]:
                    print(f"    · {p}")
                for p in row["notes"]:
                    print(f"    - 참고 · {p}")
            calls = {"HCX": 0, "KOSIS": 0}
            for row in results:
                for state in {
                    id(row["first"]): row["first"],
                    id(row["final"]): row["final"],
                }.values():
                    for name in calls:
                        calls[name] += (state.get("calls") or {}).get(name, 0)
            if args.mode == "replay":
                print(
                    f"호출 횟수: 실제 HCX·KOSIS 호출 0회 (골든 B 선택 재생 {calls['HCX']}회, 합성 값 조회 {calls['KOSIS']}회)"
                )
            elif args.mode == "kosis":
                # The model here is the golden answer replay, not HCX: report it as such.
                print(
                    f"호출 횟수: 실제 KOSIS {calls['KOSIS']}회, 실제 HCX 0회 (B 선택은 골든 답 재생 {calls['HCX']}회)"
                )
                calls = {"KOSIS": calls["KOSIS"], "HCX": 0, "golden_b_replay": calls["HCX"]}
            else:

                def http_count(state: dict) -> int:
                    return sum(
                        1
                        for e in (state.get("trace_events") or [])
                        if e.get("event") == "transport_response" and e.get("service") == "HCX"
                    )

                http = sum(max(http_count(r["first"]), http_count(r["final"])) for r in results)
                print(
                    f"호출 횟수: 실제 HCX {http}회(429 재시도 포함), 실제 KOSIS {calls['KOSIS']}회"
                )
            failed = [r["case_id"] for r in results if r["problems"]]
            extras = {
                r["case_id"]: len(r["extra"]) for r in results if r["extra"] and not r["problems"]
            }
            exact = len(results) - len(failed) - len(extras)
            print(
                f"결과: 통과 {exact} · 골든 계열 포함·추가 계열 있음 {len(extras)} · 불일치 {len(failed)} (전체 {len(results)}건)"
                + (f" · 추가 계열 {extras}" if extras else "")
                + (f" · 불일치 {failed}" if failed else "")
            )
            if args.out:
                target = Path(args.out).resolve()
                if target.is_relative_to(ROOT) and not target.is_relative_to(ROOT / "runs"):
                    raise SystemExit("--out은 저장소 밖 또는 runs/ 아래여야 합니다.")
                target.mkdir(parents=True, exist_ok=True)
                for row in results:
                    (target / f"{row['case_id']}.json").write_text(
                        json.dumps(
                            {
                                k: row[k]
                                for k in (
                                    "case_id",
                                    "problems",
                                    "notes",
                                    "missing",
                                    "extra",
                                    "card",
                                    "blocks",
                                )
                            }
                            | {"state": row["final"]},
                            ensure_ascii=False,
                            indent=1,
                            default=str,
                        ),
                        encoding="utf-8",
                    )
                (target / "summary.json").write_text(
                    json.dumps(
                        {
                            "mode": args.mode,
                            "label": label,
                            "calls": calls,
                            "cases": {
                                r["case_id"]: {
                                    "verdict": verdict_of(r),
                                    "problems": r["problems"],
                                    "missing_golden_series": len(r["missing"]),
                                    "extra_series": len(r["extra"]),
                                }
                                for r in results
                            },
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    encoding="utf-8",
                )
                print(f"기록 저장: {target}")
            if guard is not None and guard.NETWORK_ATTEMPTS:
                print("[경고] 네트워크 호출 시도가 차단되었습니다.", file=sys.stderr)
                return 1
            return 1 if failed else 0
        finally:
            runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
