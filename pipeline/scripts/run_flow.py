"""Shortcut for the offline AView CLI (same runner as the screen).

    python pipeline/scripts/run_flow.py --as-of 2026-10-29 --question "가계신용" --out runs/flow-demo

A and B judgments and fetched values are synthetic. Pending confirmations are recorded,
never answered automatically. This is a wiring check, not an accuracy evaluation.
"""

from __future__ import annotations

import sys

from claview_pipeline.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
