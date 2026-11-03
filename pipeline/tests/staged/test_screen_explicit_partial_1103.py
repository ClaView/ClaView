"""Stated years that only partly lie in the table reach the card as read-part + missing part (SYNTHETIC model and fetch)."""

from __future__ import annotations

from test_screen_period_condition_1103 import AS_OF, runner


def test_stated_years_partly_after_the_table_are_read_up_to_its_end_and_said_once(base):
    q = "M2 통화량 2020년부터 2027년까지 월별로 보여줘"
    script = {
        "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
        "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
        "periods": [{"quote": "2020년부터 2027년까지", "for": ["s1"]}],
        "frequencies": [{"quote": "월별", "for": ["s1"]}],
    }
    state = runner(base, q, script).run(q, as_of=AS_OF)
    assert state["status"] == "ok", state.get("error_code")
    (card,) = state["ui_cases"]
    assert (card["start"], card["end"], card["freq"]) == ("2020년 1월", "2026년 7월", "M")
    (info,) = card["periodInfo"]
    assert info["requested"] == ["2020년 1월 ~ 2027년 12월"] and info["missing"] == ["2026년 8월 ~ 2027년 12월"]
    said = [t for t in [*card["notices"], *card["warnings"]] if "표에 수록되어 있지 않아" in t]
    assert len(said) == 1 and "2026" in said[0]
