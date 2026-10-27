"""Run questions through the one flow (A session -> B port -> fetch), offline only.

Examples (repository root, `uv run --locked python -B -X utf8` in front of each):
    scripts/pipeline/run_flow.py --question "GDP 2023년 보여줘"
    scripts/pipeline/run_flow.py --synthetic-b --question "GDP 2023년 보여줘"
    scripts/pipeline/run_flow.py --synthetic-b --synthetic-answer --question "총고정자본형성 2023년"
    scripts/pipeline/run_flow.py --synthetic-b --question "GDP 2023년 보여줘" --out runs/flow-demo

The same as `python -m claview_pipeline run ...` (`claview_pipeline.cli`): network blocked for
the whole process; without --synthetic-b no B port is built; --synthetic-b uses the SYNTHETIC
B model and fetch client of `claview_pipeline.synthetic` (a wiring check, not model or value
accuracy). Live HCX/KOSIS runs are not wired into this script.
"""

from __future__ import annotations

import sys

from claview_pipeline.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
