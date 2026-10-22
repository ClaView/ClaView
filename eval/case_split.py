"""Evaluation case helpers shared by the case-role tests (moved from the archived stage runners).

split_of: the fixed dev/hold split by dataset and question id.
BOUNDARY: the stage-2 boundary case ids (docs/01 7절).
"""
from __future__ import annotations

import hashlib


def split_of(dataset: str, question_id: str) -> str:
    return "dev" if hashlib.sha256(f"{dataset}:{question_id}".encode()).digest()[0] % 2 == 0 else "hold"


BOUNDARY = {"PC:EQ-16", "A:CX-Q067", "B:CX-Q084", "B:CX-Q070", "PC:EQ-22"}
