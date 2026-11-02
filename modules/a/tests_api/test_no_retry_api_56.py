"""Plan 56 order 3 (bundle 8): A's HTTP and screen layer (claview_a_api) has no retry loop around a model call."""

from __future__ import annotations

import re
from pathlib import Path

RETRY_LOOP = re.compile(
    r"\bfor\s+\w+\s+in\s+range\([^)]*(retr|attempt)|\bwhile\b.*\b(retr|attempt|send)\b", re.IGNORECASE
)


def test_no_retry_loop_in_the_api_package():
    root = Path(__file__).resolve().parents[1] / "claview_a_api"
    hits = [
        f"{p.name}:{n}"
        for p in root.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if RETRY_LOOP.search(line)
    ]
    assert hits == []
