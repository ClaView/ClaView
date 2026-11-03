"""Current metadata through real A/B/C code; model choices and values are synthetic."""

from test_screen_period_condition_1103 import AS_OF, runner


def script(period):
    return {
        "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
        "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
        "periods": [{"quote": period, "for": ["s1"]}],
        "frequencies": [{"quote": "월별", "for": ["s1"]}],
    }


def test_refreshed_coverage_extends_fetch_without_shortening_user_request(base):
    query = "M2 통화량 2020년부터 2027년까지 월별로 보여줘"
    state = runner(base, query, script("2020년부터 2027년까지")).run(query, as_of=AS_OF)
    assert state["status"] == "ok"
    (request,) = state["b_requests"]
    (table,) = request["tables"]
    assert table["period_condition"]["period"] == [{"start": "2020-01", "end": "2027-12"}]
    (card,) = state["ui_cases"]
    assert (card["start"], card["end"]) == ("2020년 1월", "2026년 8월")
    (info,) = card["periodInfo"]
    assert info["requested"] == ["2020년 1월 ~ 2027년 12월"]
    assert info["missing"] == ["2026년 9월 ~ 2027년 12월"]


def test_refreshed_latest_metadata_moves_recent_window_together(base):
    query = "M2 통화량 최근 3년 월별로 보여줘"
    state = runner(base, query, script("최근 3년")).run(query, as_of=AS_OF)
    assert state["status"] == "ok"
    (request,) = state["b_requests"]
    (table,) = request["tables"]
    assert table["period_condition"]["period"] == [{"start": "2023-09", "end": "2026-08"}]
    (card,) = state["ui_cases"]
    assert (card["start"], card["end"], len(card["periods"])) == ("2023년 9월", "2026년 8월", 36)
