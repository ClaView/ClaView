"""Offline entry into the same AView pipeline used by HTTP; synthetic checks are not accuracy scores."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "evaluate"):
        command = sub.add_parser(name)
        command.add_argument("--as-of", required=True, help="Explicit date used to interpret relative periods")
        command.add_argument("--out", type=Path, required=True,
                             help="New output directory; existing runs are preserved")
        if name == "run":
            command.add_argument("--question", action="append", required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("--out already exists; choose a new directory")

    from claview_a import _guard

    _guard.install_network_block()
    os.environ["CLAVIEW_MODE"] = "synthetic"
    from claview_a_api import screen as a_screen
    from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
    from claview_b_api.service import store
    from claview_common.settings import PATHS

    from .screen import ScreenPipeline

    cases = ([{"case_id": f"q{i:02d}", "query": query} for i, query in enumerate(args.question, 1)]
             if args.command == "run" else json.loads(PATHS.golden_file.read_text(encoding="utf-8-sig"))["cases"])
    args.out.mkdir(parents=True)
    rows = []
    for case in cases:
        runner = ScreenPipeline(model=SyntheticSelectionModel(store()), client=SyntheticFetchClient(),
                                runs_dir=args.out / "records", judges_factory=a_screen.synthetic_judges)
        try:
            state = runner.run(case["query"], as_of=args.as_of)
        finally:
            runner.close()
        rows.append({"case_id": case["case_id"], "run_id": state["run_id"], "status": state["status"],
                     "error_code": state.get("error_code"), "calls": state["calls"],
                     "cards": len(state["ui_cases"])})
    report = {"mode": "synthetic", "accuracy_evaluation": False, "external_calls": 0,
              "as_of": args.as_of, "cases": rows,
              "notice": "A and B judgments and fetched values are synthetic. Confirmation requires user answers."}
    (args.out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return int(any(row["status"] == "error" for row in rows))
