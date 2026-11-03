"""Future partial coverage retains A's request and uses B's query/missing display."""

from test_screen_period_condition_1103 import AS_OF, runner


def test_explicit_future_tail_reaches_b_without_a_assignment_truncation(base):
    query = "M2 통화량 2020년부터 2027년까지 월별로 보여줘"
    script = {
        "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
        "choices": [{"for": ["s1"], "table": "DT_161Y014"}],
        "periods": [{"quote": "2020년부터 2027년까지", "for": ["s1"]}],
        "frequencies": [{"quote": "월별", "for": ["s1"]}],
    }
    state = runner(base, query, script).run(query, as_of=AS_OF)
    assert state["status"] == "ok"
    (request,) = state["b_requests"]
    (table,) = request["tables"]
    assert table["period_condition"]["period"] == [{"start": "2020-01", "end": "2027-12"}]
    (card,) = state["ui_cases"]
    (period,) = card["periodInfo"]
    assert period["requested"] == ["2020년 1월 ~ 2027년 12월"]
    assert period["missing"] == ["2026년 8월 ~ 2027년 12월"]
    notes = [w for w in card["warnings"] if "요청 기간은 그대로 보존" in w]
    assert len(notes) == 1 and notes[0].startswith("기간 수록 안내 · ")
    assert not any("period_partial_coverage" in w for w in card["warnings"])
