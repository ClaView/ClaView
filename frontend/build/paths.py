"""Locations for the screen build (replaces the old C dependencies.py for the build scripts)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

BUILD = Path(__file__).resolve().parent
FRONTEND = BUILD.parent
SRC = FRONTEND / "src"                  # screen JS/CSS
UI_ROOT = SRC / "template"              # Highcharts screen draft (kept unchanged; patched at build time)
VENDOR = SRC / "vendor"
DIST = Path(os.environ.get("CLAVIEW_DIST", FRONTEND / "dist"))
# Repository root (eval/cases/golden for the example questions). In the image build: /build.
ROOT = Path(os.environ.get("CLAVIEW_ROOT", FRONTEND.parent))


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
