"""What B says about each table's period reaches the screen card (SYNTHETIC model and fetch, wiring only).

Pinned: a calculation range read before the shown period is not shown (the display range is stated to C), and the
card says what was asked, read, missing, substituted and why; B's visible notices appear once; a card whose data B
did not describe stays as before; and a display range C refuses falls back to the whole fetched range.
"""

from __future__ import annotations

import json

from test_screen_54 import RecordingB
from test_screen_period_condition_1103 import (
    AS_OF,
    QUARTERLY,
    SCRIPT,
    ConditionB,
    TamperedB,
    answered,
    runner,
)

from claview_pipeline import staged
from claview_pipeline.staged import display_range, period_info, visible_period_notices

YOY = "M2 통화량 2024년 월별 전년 대비 증감 보여줘"
PERIODS = {"frequencies": [{"quote": "월별", "for": ["s1"]}]}


def script(quote):
    return {**SCRIPT, "periods": [{"quote": quote, "for": ["s1"]}], **PERIODS}


def card(state):
    """The one raw card. A year-over-year request on an amount series also gets C's derived rate card (2026-11-04)."""
    (only,) = [c for c in state["ui_cases"] if not c.get("derived")]
    return only


def row(requested, fetched, extra=(), missing=(), freq="M", notices=(), table="T1"):
    def span(a, b):
        return {"frequency": freq, "start": a, "end": b}

    return {
        "table_id": table,
        "frequency": freq,
        "requested": [span(*requested)],
        "fetched": [span(*fetched)],
        "calculation_extra": [{**span(*e), "purpose": "yoy_baseline"} for e in extra],
        "missing": [span(*m) for m in missing],
        "substituted": False,
        "applied_sources": {"period": "b_computed", "frequency": "b_computed"},
        "frequency_alternatives": [],
        "notices": list(notices),
    }


# ---- display range (pure) --------------------------------------------------------------------
def test_display_range_is_the_requested_part_of_what_was_read():
    rows = [row(("202401", "202412"), ("202301", "202412"), extra=[("202301", "202312")])]
    assert display_range(rows) == {"frequency": "M", "start": "202401", "end": "202412"}


def test_display_range_is_cut_by_what_the_table_holds():
    rows = [row(("202601", "202610"), ("202501", "202607"), extra=[("202501", "202512")], missing=[("202608", "202610")])]
    assert display_range(rows) == {"frequency": "M", "start": "202601", "end": "202607"}


def test_display_range_is_not_stated_without_a_calculation_range():
    assert display_range([row(("202401", "202412"), ("202401", "202412"))]) is None
    assert display_range([]) is None and display_range(None) is None


def test_display_range_needs_every_table_to_agree():
    a = row(("202401", "202412"), ("202301", "202412"), extra=[("202301", "202312")], table="T1")
    b = row(("202402", "202412"), ("202302", "202412"), extra=[("202302", "202401")], table="T2")
    assert display_range([a, b]) is None
    assert display_range([a, row(("202401", "202412"), ("202301", "202412"), extra=[("202301", "202312")], table="T2")])
    assert display_range([a, row(("2024", "2024"), ("2023", "2024"), extra=[("2023", "2023")], freq="Y", table="T3")]) is None


def test_display_range_gives_up_when_nothing_requested_is_read():
    assert display_range([row(("202701", "202712"), ("202601", "202607"), extra=[("202601", "202612")])]) is None


def test_visible_notices_are_listed_once_and_only_for_the_cards_tables():
    rows = [
        row(("202401", "202412"), ("202401", "202412"), notices=[
            {"code": "table_lag", "visible": True, "message": "A"},
            {"code": "reference_period_differs", "visible": False, "message": None},
        ]),
        row(("202401", "202412"), ("202401", "202412"), notices=[{"code": "table_lag", "visible": True, "message": "A"}], table="T2"),
        row(("202401", "202412"), ("202401", "202412"), notices=[{"code": "x", "visible": True, "message": "B"}], table="T3"),
    ]
    assert visible_period_notices(rows, {"T1", "T2"}) == ["A"]


def test_period_info_words_the_row_for_the_screen():
    r = row(("202601", "202610"), ("202501", "202607"), extra=[("202501", "202512")], missing=[("202608", "202610")])
    r["applied_sources"] = {"period": "a_user_answer", "frequency": "b_computed"}
    (info,) = period_info([r], {"T1"}, {"T1": "표 이름"})
    assert info["tableName"] == "표 이름" and info["freqName"] == "월별"
    assert info["requested"] == ["2026년 1월 ~ 2026년 10월"] and info["fetched"] == ["2025년 1월 ~ 2026년 7월"]
    assert info["missing"] == ["2026년 8월 ~ 2026년 10월"]
    assert info["extra"] == [{"range": "2025년 1월 ~ 2025년 12월", "purpose": "전년 같은 기간과 비교하기 위한 기준 기간"}]
    assert info["sources"] == {"period": "사용자가 고른 값", "frequency": "질문 문구로 B가 계산"}
    assert period_info([r], {"OTHER"}, {}) == []


# ---- screen path -----------------------------------------------------------------------------
def test_a_calculation_range_is_read_but_not_shown(base):
    p = runner(base, YOY, script("2024년"))
    state = p.run(YOY, as_of=AS_OF)
    assert state["status"] == "ok"
    c = card(state)
    assert c["requestedPeriod"] == {"frequency": "M", "start": "202401", "end": "202412"}
    assert (c["start"], c["end"], len(c["periods"])) == ("2024년 1월", "2024년 12월", 12)
    (info,) = c["periodInfo"]
    assert info["requested"] == ["2024년 1월 ~ 2024년 12월"]
    assert info["fetched"] == ["2023년 1월 ~ 2024년 12월"]
    assert info["extra"][0]["range"] == "2023년 1월 ~ 2023년 12월"
    assert not any("기준 시점이 포함될 수" in w for w in c["warnings"])
    json.dumps(c, ensure_ascii=False)
    assert "period_condition" not in str(state["ui_cases"])


def test_an_answered_period_is_reported_with_its_source(base):
    state = answered(runner(base, QUARTERLY, SCRIPT))
    c = card(state)
    (info,) = c["periodInfo"]
    assert info["sources"]["period"] == "사용자가 고른 값"
    assert info["requested"] == info["fetched"] == ["2024년 1분기 ~ 2024년 2분기"]
    assert info["missing"] == [] and info["extra"] == [] and not info["substituted"]
    assert c["requestedPeriod"] is None  # nothing was read beyond what is shown


def test_a_period_the_table_lacks_is_reported_and_said_once(historical_metadata):
    base = historical_metadata
    q = "M2 통화량 올해 월별로 보여줘"
    state = runner(base, q, script("올해")).run(q, as_of=AS_OF)
    c = card(state)
    (info,) = c["periodInfo"]
    assert info["requested"] == ["2026년 1월 ~ 2026년 10월"] and info["fetched"] == ["2026년 1월 ~ 2026년 7월"]
    assert info["missing"] == ["2026년 8월 ~ 2026년 10월"]
    said = [t for t in [*c["notices"], *c["warnings"]] if "조회 가능한 기간으로" in t]
    assert len(said) == 1


def test_a_substituted_period_is_reported(historical_metadata):
    base = historical_metadata
    q = "M2 통화량 작년 월별로 보여줘"
    state = runner(base, q, script("작년")).run(q, as_of="2028-03-01")
    (info,) = card(state)["periodInfo"]
    assert info["substituted"] is True and info["missing"] == ["2027년 1월 ~ 2027년 12월"]
    assert info["fetched"] == ["2026년 1월 ~ 2026년 7월"]


def test_no_baseline_warning_when_b_read_no_calculation_range(base):
    q = "M2 통화량 올해 월별 전년 대비 증감 보여줘"
    state = runner(base, q, script("올해")).run(q, as_of=AS_OF)
    c = card(state)
    assert c["periodInfo"][0]["extra"] == []
    assert not any("기준 시점이 포함될 수" in w for w in c["warnings"])


def test_a_b_notice_the_card_does_not_carry_yet_is_added(base):
    class Noisy(TamperedB):
        @staticmethod
        def tamper(outcome):
            for r in outcome["result"]["period_resolutions"]:
                r["notices"].append({"code": "a_quoted_period_used", "visible": True, "message": "B 안내 시험 문구"})
                r["notices"].append({"code": "calculation_range_extended", "visible": False, "message": None})
            return outcome

    q = "M2 통화량 2023년 월별로 보여줘"
    state = runner(base, q, script("2023년"), b=Noisy).run(q, as_of=AS_OF)
    c = card(state)
    assert c["warnings"].count("B 안내 시험 문구") == 1
    assert "calculation_range_extended" not in str(c)


def test_a_b_that_does_not_report_periods_leaves_the_card_as_before(base):
    q = "M2 통화량 2023년 월별로 보여줘"
    state = runner(base, q, script("2023년"), b=RecordingB).run(q, as_of=AS_OF)
    assert state["status"] == "ok"
    c = card(state)
    assert "periodInfo" not in c and c["requestedPeriod"] is None


def test_a_display_range_c_refuses_falls_back_to_the_fetched_range(base, monkeypatch):
    real = staged.c_service.present

    def picky(query, data):
        if data.get("requested_period"):
            return {"stage": "C", "status": "invalid_input", "issues": [{"code": "requested_period_refused"}], "result": None}
        return real(query, data)

    monkeypatch.setattr(staged.c_service, "present", picky)
    state = runner(base, YOY, script("2024년")).run(YOY, as_of=AS_OF)
    assert state["status"] in {"ok", "partial"} and state["display_range_dropped"]
    c = card(state)
    assert c["requestedPeriod"] is None and len(c["periods"]) == 24
    assert c["periodInfo"][0]["extra"]  # the card still says a calculation range is inside what is shown
    assert any("기준 시점이 포함될 수" in w for w in c["warnings"])
