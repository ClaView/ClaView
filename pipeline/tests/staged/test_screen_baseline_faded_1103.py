"""A YoY calculation range read before the shown period reaches the card as a separate `baseline` block
(SYNTHETIC model and fetch, wiring only): the card's own periods/labels/values stay the asked range, so the table,
CSV and quality counts are unchanged, while the chart can draw the base year faded.
"""

from __future__ import annotations

import json

from test_screen_period_condition_1103 import AS_OF, runner
from test_screen_period_display_1103 import PERIODS, SCRIPT, YOY, card, script  # noqa: F401

from claview_pipeline.staged import baseline_block


def test_the_base_year_is_carried_apart_from_the_shown_values(base):
    state = runner(base, YOY, script("2024년")).run(YOY, as_of=AS_OF)
    assert state["status"] == "ok"
    c = card(state)
    assert (c["start"], c["end"], len(c["periods"])) == ("2024년 1월", "2024년 12월", 12)
    b = c["baseline"]
    assert b["periods"] == [f"2023{m:02d}" for m in range(1, 13)]
    assert b["labels"][0] == "2023년 1월" and b["labels"][-1] == "2023년 12월"
    assert set(b["values"]) == {s["id"] for s in c["series"]}
    assert all(len(v) == 12 for v in b["values"].values())
    assert all(len(s["values"]) == 12 for s in c["series"])
    json.dumps(c, ensure_ascii=False)


def test_the_baseline_is_not_counted_in_what_is_shown(base):
    c = card(runner(base, YOY, script("2024년")).run(YOY, as_of=AS_OF))
    assert "2023" not in "".join(c["periods"][0:1]) and c["periods"][0] == "202401"
    assert "baseline" not in card(runner(base, "M2 통화량 2024년 월별 보여줘", script("2024년")).run(
        "M2 통화량 2024년 월별 보여줘", as_of=AS_OF))


def test_baseline_block_needs_a_value():
    series = [{"series_id": "S1"}]
    assert baseline_block([], series, {"S1": {}}, "M") is None
    assert baseline_block(["202301"], series, {"S1": {}}, "M") is None
    got = baseline_block(["202301"], series, {"S1": {"202301": {"value": 1.5}}}, "M")
    assert got == {"periods": ["202301"], "labels": ["2023년 1월"], "values": {"S1": [1.5]}}
