"""Compare two ``screen_replay.py`` results case by case (plan 54), in three parts: A's own outcome and final tables,
what may go to B and why not, and the screen result (status, unsupported links, warnings shown).

    python pipeline/scripts/screen_replay_diff.py BEFORE/replay.json AFTER/replay.json [OUT.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PARTS = {
    "a": ("a_final", "a_route"),
    "handoff": ("ready", "blocked", "unsupported_links", "handoff_notes", "b_periods"),
    "screen": ("status", "error_code", "a_warnings", "stages"),
}


def main() -> None:
    before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    changes = {}
    for src, cases in after.items():
        for key, new in cases.items():
            old = before.get(src, {}).get(key)
            if old is None:
                changes[f"{src}|{key}"] = {"new_case": True}
                continue
            got = {
                part: {f: [old.get(f), new.get(f)] for f in fields if old.get(f) != new.get(f)}
                for part, fields in PARTS.items()
            }
            got = {p: v for p, v in got.items() if v}
            if got:
                changes[f"{Path(src).parts[-2]}|{key}"] = got
    out = json.dumps(changes, ensure_ascii=False, indent=1)
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(out, encoding="utf-8")
    for case, got in changes.items():
        print(
            case,
            {p: sorted(v) for p, v in got.items()},
            {k: v for k, v in got.get("screen", {}).items() if k in ("status", "error_code")},
        )


if __name__ == "__main__":
    main()
