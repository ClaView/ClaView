"""Command line for the one flow (A session -> B port -> fetch), offline only.

    python -m claview.pipeline run --question "GDP 2023년 보여줘"
    python -m claview.pipeline run --synthetic-b --synthetic-answer --question "GDP 2023년"
    python -m claview.pipeline evaluate --as-of 2026-10-22 --out runs/pipeline-golden

Network is blocked for the whole process. `run` without --synthetic-b builds no B port (A reports
b_port_not_configured); --synthetic-b uses the SYNTHETIC B model and fetch client of
`claview.pipeline.synthetic`: a wiring check, not model or value accuracy. `evaluate` replays the
stored golden B answers (see `claview.pipeline.evaluate`). Live HCX/KOSIS runs are not wired here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "eval" / "cases" / "golden_set_v1.json"


def summary(state: dict) -> dict:
    response = state["response"]
    b = response.get("b") or {}
    return {
        "question": state["question"],
        "status": response["status"],
        "execution_status": response.get("execution_status"),
        "b_status": b.get("status"),
        "b_reason": b.get("reason"),
        "tables": {
            s["requirement_id"]: [t["table_id"] for t in s.get("tables") or []]
            for s in state.get("selections") or []
        },
        "skipped": [
            {"requirement_id": s.get("requirement_id"), "reason": s.get("reason")}
            for s in b.get("skipped") or []
        ],
        "scopes": [
            sc["status"]
            for r in b.get("requirements") or []
            for sc in (r.get("result") or {}).get("scopes", [])
        ],
        "fetch": [
            ((r.get("result") or {}).get("fetch") or {}).get("status")
            for r in b.get("requirements") or []
        ],
        "pending_question": (response.get("question") or {}).get("dimension"),
        "answer_error": response.get("answer_error"),
    }


def out_dir(value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).resolve()
    if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / "runs"):
        raise SystemExit("--out must be outside the repository or under runs/")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run(args, guard) -> int:
    from .session import FlowSession, make_answer
    from .synthetic import LABEL, SyntheticFetchClient, SyntheticSelectionModel

    target = out_dir(args.out)
    base = FlowSession.load()
    label = "offline, network blocked; live B (HCX/KOSIS) is not wired in this step"
    if args.synthetic_b:
        label += f"; B model and fetch: {LABEL}"
    print(f"[pipeline run] {label}")
    rows = []
    for number, question in enumerate(args.question, 1):
        session = base
        if args.synthetic_b:
            session = FlowSession.load(
                b_model=SyntheticSelectionModel(base.store),
                fetch_client=SyntheticFetchClient(),
                layer=base.layer,
                store=base.store,
            )
        state = session.run_question(question, as_of=args.as_of)
        turns = 0
        while (
            args.synthetic_answer and state["response"]["status"] == "awaiting_answer" and turns < 5
        ):
            state = session.answer(state, make_answer(state))
            turns += 1
        row = {**summary(state), "synthetic_answer_turns": turns}
        if args.synthetic_b:
            row["synthetic_calls"] = {
                "model": len(session.b_model.calls),
                "fetch": len(session.fetch_client.calls),
            }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if target is not None:
            (target / f"q{number:02d}.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
            )
    if target is not None:
        (target / "summary.json").write_text(
            json.dumps(
                {"note": label, "rows": rows, "network_attempts": len(guard.NETWORK_ATTEMPTS)},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    return 0


def _evaluate(args, guard) -> int:
    from .evaluate import evaluate, load_cases

    target = out_dir(args.out)
    report = evaluate(load_cases(args.cases), as_of=args.as_of, answer_policy=args.answer_policy)
    report["network_attempts"] = len(guard.NETWORK_ATTEMPTS)
    print(f"[pipeline evaluate] {report['scope']}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if target is not None:
        (target / "golden_eval.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claview-pipeline", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="questions through A -> B -> fetch")
    run.add_argument("--question", action="append", required=True, help="repeatable")
    run.add_argument(
        "--synthetic-b", action="store_true", help="SYNTHETIC B model and fetch client"
    )
    run.add_argument(
        "--synthetic-answer",
        action="store_true",
        help="answer a pending question with its first option (synthetic user)",
    )
    ev = sub.add_parser("evaluate", help="golden end-to-end replay (development baseline)")
    ev.add_argument("--cases", type=Path, default=GOLDEN)
    ev.add_argument("--answer-policy", choices=["none", "first_option"], default="first_option")
    for p in (run, ev):
        p.add_argument("--as-of", default=None, help="reference date YYYY-MM-DD (default: today)")
        p.add_argument("--out", default=None, help="output folder (outside repo or under runs/)")
    args = parser.parse_args(argv)

    sys.dont_write_bytecode = True
    from claview.a import _guard

    _guard.install_network_block()
    code = _run(args, _guard) if args.command == "run" else _evaluate(args, _guard)
    if _guard.NETWORK_ATTEMPTS:
        print("[pipeline] network attempts were refused", file=sys.stderr)
        return 1
    return code
