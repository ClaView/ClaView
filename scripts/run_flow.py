"""Run questions through the one flow (A session -> B port -> fetch), offline only.

Examples (repository root, `uv run --locked python -B -X utf8` in front of each):
    scripts/run_flow.py --question "GDP 2023년 보여줘"
    scripts/run_flow.py --synthetic-b --question "GDP 2023년 보여줘"
    scripts/run_flow.py --synthetic-b --synthetic-answer --question "총고정자본형성 2023년"
    scripts/run_flow.py --synthetic-b --question "GDP 2023년 보여줘" --out runs/flow-demo

Network is blocked for the whole process. Without --synthetic-b no B port is built
(A reports b_port_not_configured). --synthetic-b uses the SYNTHETIC B model and fetch
client of tests_flow/synthetic_b.py: a wiring check, not model or value accuracy.
Live HCX/KOSIS runs are not wired into this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--question", action="append", required=True, help="repeatable")
    parser.add_argument("--as-of", default=None, help="reference date YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--synthetic-b",
        action="store_true",
        help="SYNTHETIC B model and fetch client (wiring check only)",
    )
    parser.add_argument(
        "--synthetic-answer",
        action="store_true",
        help="answer a pending question with its first option (synthetic user)",
    )
    parser.add_argument(
        "--out", default=None, help="folder for full states (outside repo or runs/)"
    )
    args = parser.parse_args(argv)

    sys.dont_write_bytecode = True
    from claview_v3 import _guard

    _guard.install_network_block()
    from claview_flow import FlowSession, make_answer

    target = out_dir(args.out)
    base = FlowSession.load()
    label = "offline, network blocked; live B (HCX/KOSIS) is not wired in this step"
    if args.synthetic_b:
        sys.path.append(str(ROOT / "tests_flow"))
        from synthetic_b import LABEL, SyntheticFetchClient, SyntheticSelectionModel

        label += f"; B model and fetch: {LABEL}"
    print(f"[run_flow] {label}")
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
                {"note": label, "rows": rows, "network_attempts": len(_guard.NETWORK_ATTEMPTS)},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    if _guard.NETWORK_ATTEMPTS:
        print("[run_flow] network attempts were refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
