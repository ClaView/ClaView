"""Recent option labels show their actual catalog-relative ranges, including discontinued tables' old dates.

The original no-data-label regression is retained under the historical calendar policy: a kept window with no
available data says so. Scripted judges only; DT_511Y001 ends in 2008 and DT_161Y014 is current.
"""

from __future__ import annotations

import dataclasses

from claview_a.periods import resolver as R
from claview_a_api import screen as S
from claview_a_api.synthetic_judges import ScriptJudge, by_question

AS_OF = "2026-11-02"
OLD = "소비자동향조사 추이 보여줘"
NOW = "M2 통화량을 연간으로 보여줘"
SCRIPT = {
    OLD: {"statistics": [{"id": "s1", "quote": "소비자동향조사", "name": "CSI"}],
          "choices": [{"for": ["s1"], "table": "DT_511Y001"}]},
    NOW: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
          "frequencies": [{"quote": "연간", "for": ["s1"]}], "choices": [{"for": ["s1"], "table": "DT_161Y014"}]},
}


def period_question(question):
    j = ScriptJudge(by_question(SCRIPT))
    view = S.select(question, AS_OF, S.Judges(j, j, "script"))["view"]
    (q,) = [x for x in view["questions"] if x["kind"] == "period"]
    return q


def test_a_window_with_nothing_to_fetch_now_is_kept_and_marked(monkeypatch):
    # Keep the original no-data-label regression under the historical calendar policy.
    monkeypatch.setattr(R, "_POLICY", dataclasses.replace(R.default_policy(), recent_anchor_rule="as_of_calendar"))
    q = period_question(OLD)
    assert [o["value"] for o in q["options"]] == ["최근 3년", "최근 5년", "최근 10년"]  # still offered
    for o in q["options"]:
        assert o["selectable"] and "현재 카탈로그상 조회 자료 없음" in o["label"]


def test_a_window_with_data_shows_the_catalog_relative_range():
    q = period_question(NOW)
    option = next(o for o in q["options"] if o["value"] == "최근 5년")
    assert option["label"] == "최근 5년 (2021년~2025년)"


def test_a_discontinued_table_shows_its_actual_old_dates_under_the_latest_data_policy():
    q = period_question(OLD)
    option = next(o for o in q["options"] if o["value"] == "최근 3년")
    assert option["label"] == "최근 3년 (2005년 3분기~2008년 2분기)"
    assert "현재 카탈로그상 조회 자료 없음" not in option["label"]
