"""Local screen demo of the new A (plan 54): the built screen and the pipeline API on one loopback port.

No model, KOSIS or other external call: the A judge is scripted (``screen_demo_scenarios.json``; other questions get
the fake judge's answer), B and fetch are the synthetic ones. Wiring and display only, never accuracy.

    PYTHONPATH=common;modules/a;modules/b;modules/c;pipeline
    python frontend/build/ui_build.py            (with CLAVIEW_DIST=runs/screen54_demo/dist)
    python pipeline/scripts/screen_demo.py --dist runs/screen54_demo/dist --port 10399
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question, first_card
from claview_b_api.service import synthetic_clients
from claview_pipeline import api
from claview_pipeline.screen import ScreenPipeline

SCENARIOS = Path(__file__).resolve().parent / "screen_demo_scenarios.json"


def runner(scenarios: dict) -> ScreenPipeline:
    def judges():
        j = ScriptJudge(by_question(scenarios["answers"], fallback=first_card))
        return a_screen.Judges(j, j, "script")

    def period_questions(view):
        return scenarios.get("period_questions", {}).get(view["session"]["question"], [])

    model, client = synthetic_clients()
    return ScreenPipeline(
        model=model, client=client, runs_dir=None, judges_factory=judges, period_questions=period_questions
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dist", required=True, help="folder holding the built index.html")
    p.add_argument("--port", type=int, default=10399)
    a = p.parse_args()
    api.STATE._runner = runner(json.loads(SCENARIOS.read_text(encoding="utf-8")))
    # after the API routes, so /api/* and /health keep their handlers; the rest is the built screen
    api.app.mount("/", StaticFiles(directory=a.dist, html=True), name="screen")
    uvicorn.run(api.app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
