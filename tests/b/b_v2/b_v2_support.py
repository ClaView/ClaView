"""Shared paths and loaders of the b_v2 tests (plain module: test folders are not packages)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = ROOT / "data" / "catalog" / "search_catalog.json"
METADATA_PATH = ROOT / "data" / "raw" / "boK_meta_raw.jsonl"
V3_FIXTURES = sorted((Path(__file__).parent / "fixtures" / "v3").glob("v3_fx*.json"))
# outputs of the current A working tree with the decomposition record (plan 05)
V3CUR_FIXTURES = sorted((Path(__file__).parent / "fixtures" / "v3cur").glob("v3cur_fx*.json"))
# period conditions as the A working tree writes them, with A's own diagnosis (stage 3 follow-up)
A_PERIOD_SHAPES = Path(__file__).parent / "fixtures" / "a_period_shapes" / "a_period_shapes.json"


def load_v3(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
