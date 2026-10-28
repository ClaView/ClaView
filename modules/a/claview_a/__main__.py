"""Command line of the new A: ``python -m claview_a QUESTION [--judge fake|replay] ...``.

It calls the official entry ``claview_a.run`` once and writes the call archive and the state to a new folder.
- ``fake`` (default): a fixed valid answer built from the cards; no network; wiring only.
- ``replay --replay-from DIR``: the archived response of the same request in ``DIR/calls.jsonl``; no network.

Model calls are not made from this command line. They run only through the evaluator
(``modules/a/scripts/tl_eval/live.py``), which checks the approved manifest's fixed conditions (``gates``) and the
call limit before each call. ``--judge live`` and ``--manifest`` are refused before anything else is done.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import run
from .judges import Archive, FakeJudge, ReplayJudge

JUDGES = ("fake", "replay")
LIVE_REFUSED = ("live calls are not made from python -m claview_a (fake and replay only); run them with the evaluator "
                "modules/a/scripts/tl_eval/live.py, which checks the approved manifest (gates) and the call limit")


def _refuse_live(argv: list[str]) -> None:
    """Refuses a live run (``--judge live``, ``--judge=live``) or a manifest, before parsing or any folder."""
    for k, arg in enumerate(argv):
        if arg == "--manifest" or arg.startswith("--manifest="):
            raise SystemExit(LIVE_REFUSED)
        if arg == "--judge=live" or (arg == "--judge" and argv[k + 1:k + 2] == ["live"]):
            raise SystemExit(LIVE_REFUSED)


def _out_dir(given: str | None) -> Path:
    if given:
        out = Path(given)
    else:
        out = Path.cwd() / "runs" / "a_cli" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if out.exists():
        raise SystemExit(f"{out} exists: a run is never resumed or overwritten")
    out.mkdir(parents=True)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _refuse_live(argv)
    p = argparse.ArgumentParser(prog="python -m claview_a", allow_abbrev=False,
                                description="New A: one question, table-level path (fake or replay judge).",
                                epilog="Model calls run only through the evaluator modules/a/scripts/tl_eval.")
    p.add_argument("question")
    p.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    p.add_argument("--judge", choices=JUDGES, default="fake")
    p.add_argument("--replay-from", help="folder holding an archived calls.jsonl (replay)")
    p.add_argument("--out", help="new folder for calls.jsonl and state.json (default runs/a_cli/<time>)")
    a = p.parse_args(argv)
    if a.judge == "replay" and not a.replay_from:
        raise SystemExit("replay: --replay-from is required")
    out = _out_dir(a.out)
    archive, ref = Archive(out / "calls.jsonl"), {"case_id": "cli"}
    if a.judge == "fake":
        judge = FakeJudge(archive, ref)
    else:
        lines = (Path(a.replay_from) / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        judge = ReplayJudge([json.loads(x) for x in lines], archive, ref)
    state = run(a.question, judge, as_of=a.as_of)
    (out / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    json.dump({"action": state.get("action"), "record": state.get("record"), "calls": judge.calls, "out": str(out)},
              sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
