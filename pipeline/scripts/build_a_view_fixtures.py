"""Build synthetic AView focus examples only; no model, catalog query or network."""

from __future__ import annotations

import json
from pathlib import Path

from claview_common.contracts.a_view import AView


def main() -> None:
    folder = Path(__file__).resolve().parents[2] / "common" / "fixtures" / "a_view"
    folder.mkdir(parents=True, exist_ok=True)
    for source, status in (("model_scope", "grounded"), ("legacy_quote", "verified_quote_only")):
        # This hand-written example demonstrates the public shape, not model quality.
        focus = "예금은행 대출금리"
        body = {
            "session": {"session_id": "synthetic-focus", "question_version": 1,
                        "question": "2024년 예금은행 대출금리를 월별로 보여줘", "as_of": "2026-10-29",
                        "run": {"versions": {}, "model": {"name": "synthetic"}, "profile": "fixture"}},
            "status": "ready", "a_route": "handoff",
            "statistics": [{"request_id": "synthetic-focus:s1", "statistic": "s1", "quote": focus,
                            "name": focus, "outcome": "selected", "state": "selected", "verified": True,
                            "focus": focus, "focus_provenance": {"text": focus, "quotes": [focus],
                                                                    "source": source, "status": status}}],
            "handoff": {"ready": [{"request_id": "synthetic-focus:s1", "statistics": ["s1"],
                                    "tables": [{"org_id": "301", "table_id": "DT_121Y006", "focus": focus}]}]},
        }
        parsed = AView.model_validate(body).model_dump(mode="json")
        (folder / f"focus_{source}.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
