"""Owner 11-02 (screen review, item 3): a period window whose whole span lies after a kept table's latest period stays
offered (the candidate and latest_unpublished policy are unchanged), but it is never shown as a window with data to
fetch: its label says there is none in the current catalog (A's check keeps ``no_current_data`` with the option; the
AView option has no field for it yet, a contract change left to the owner). Windows with data are unchanged.
Scripted judges only; the tables are catalog facts (DT_511Y001 ends in 2008, DT_161Y014 is current)."""

from __future__ import annotations

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


def test_a_window_with_nothing_to_fetch_now_is_kept_and_marked():
    q = period_question(OLD)
    assert [o["value"] for o in q["options"]] == ["최근 3년", "최근 5년", "최근 10년"]  # still offered
    for o in q["options"]:
        assert o["selectable"] and "현재 카탈로그상 조회 자료 없음" in o["label"]


def test_a_window_with_data_is_unchanged():
    q = period_question(NOW)
    option = next(o for o in q["options"] if o["value"] == "최근 5년")
    assert option["label"] == "최근 5년 (2022년~2026년)"
