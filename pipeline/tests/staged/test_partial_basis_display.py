"""A partial basis reply must not advertise another basis as the final table."""
from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question


def test_remaining_basis_card_keeps_answer_without_stale_example_table(make_pipeline):
    query = "2024년 월별 M2 통화량을 보여줘."
    script = {query: {
        "statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
        "periods": [{"quote": "2024년", "for": ["s1"]}],
        "frequencies": [{"quote": "월별", "for": ["s1"]}],
        "asks": [
            {"for": "s1", "axis": "stock", "options": [
                {"value": "평잔", "table": "DT_161Y005"},
                {"value": "말잔", "table": "DT_161Y007"}]},
            {"for": "s1", "axis": "seasonal", "options": [
                {"value": "계절조정", "table": "DT_161Y005"},
                {"value": "원계열", "table": "DT_161Y006"}]},
        ],
    }}
    judge = ScriptJudge(by_question(script))
    judges = a_screen.Judges(judge, judge, "script")
    runner = make_pipeline()
    runner.judges_factory = lambda: judges
    initial = runner.run(query, as_of="2026-11-05")

    def reply(state, question_id, label):
        card = state["confirmation"]
        question = next(g for g in card["groups"] if g["requirement_id"] == question_id)
        option = next(o for o in question["options"] if o["label"] == label)
        return {"session_id": card["session_id"], "answers": [{
            "question_id": question_id, "question_version": question["question_version"],
            "option_id": option["option_id"],
        }]}

    partial = runner.resume(initial, reply(initial, "q:s1:stock", "말잔"))
    assert partial["status"] == "needs_confirmation"
    (group,) = partial["confirmation"]["groups"]
    assert "말잔" in group["indicator"] and "유지" in group["indicator"]
    assert all(not table["table_id"] for option in group["options"] for table in option["tables"])
    assert [o["label"] for o in group["options"]] == ["계절조정", "원계열"]
    assert partial["calls"] == {"A_judge": 0, "HCX": 0, "KOSIS": 0}

    completed = runner.resume(partial, reply(partial, "q:s1:seasonal", "계절조정"))
    assert completed["status"] == "ok"
    assert {c["tableId"] for c in completed["ui_cases"]} == {"DT_161Y007"}
    assert completed["calls"]["A_judge"] == 0
    assert len(judges.requests) == 1
