"""Prepare B inputs from A's decisions without new interpretation or transport."""

from __future__ import annotations

import copy

import pytest

from claview_a_api import view as V
from claview_common.contracts import BRequest


def answer(kind, sid="s1", question_id=None, **changes):
    return {"kind": kind, "question_id": question_id or f"{kind}:{sid}", "question_version": 1,
            "statistics": [sid], "option_id": None, "value": "2024년" if kind == "period" else "M",
            "text": "2024년" if kind == "period" else None, "source": "user_answer", **changes}


def case(period_source="quoted", frequency_source="question", count=1, answers=None):
    session = {"session_id": "sender-fixture", "question": "두 지표를 보여줘", "as_of": "2026-10-29"}
    stats = [{"statistic": f"s{i}", "outcome": "selected", "focus": f"지표 {i}", "reasons": [],
              "conditions": {"calculations": []}, "tables": [{"org_id": "301", "table_id": "T_M",
                  "table_name": "설명용 표", "frequency": "M", "period": [{"start": "2024-01", "end": "2024-12"}],
                  "period_source": period_source, "frequency_source": frequency_source,
                  "period_phrase": {"text": "2024년", "source": period_source}}]} for i in range(1, count + 1)]
    ready, blocked = V._handoff(session, stats, set(), answers or {})
    assert len(ready) == 1 and blocked == []
    view = {"session": session, "statistics": stats, "questions": [], "handoff": {"ready": ready, "blocked": blocked}}
    return view, ready[0]


@pytest.mark.parametrize(("period_source", "frequency_source", "kinds"), [
    ("quoted", "question", ()), ("user_answer", "catalog", ("period",)),
    ("quoted", "user_answer", ("frequency",)), ("user_answer", "user_answer", ("period", "frequency")),
])
def test_each_field_keeps_its_own_source_and_original_question(period_source, frequency_source, kinds):
    view, ready = case(period_source, frequency_source, answers={"s1": [answer(k) for k in kinds]})
    before = copy.deepcopy(view)
    got = V.prepare_b_request(view, ready)
    assert got["withheld"] is None and view == before
    request = BRequest.model_validate(got["request"]).model_dump()
    assert request["query"] == view["session"]["question"] and request["as_of"] == "2026-10-29"
    table, = request["tables"]
    assert {k: table[k] for k in ready["tables"][0]} == ready["tables"][0]
    condition = table["period_condition"]
    assert (condition["period_source"], condition["frequency_source"]) == (period_source, frequency_source)
    assert condition["period"] == [{"start": "2024-01", "end": "2024-12"}]
    assert condition["frequency"] == "M"
    assert {a["kind"] for a in condition["answers"]} == set(kinds)
    assert all(a["value"] == a["text"] == "2024년" for a in condition["answers"] if a["kind"] == "period")
    assert "period_condition" not in ready["tables"][0]


def test_separate_answers_for_the_same_table_keep_each_target_and_cover_both():
    answers = {sid: [answer("period", sid)] for sid in ("s1", "s2")}
    view, ready = case("user_answer", "catalog", 2, answers)
    got = V.prepare_b_request(view, ready)
    assert got["withheld"] is None
    condition = got["request"]["tables"][0]["period_condition"]
    assert condition["statistics"] == ["s1", "s2"]
    assert [a["statistics"] for a in condition["answers"]] == [["s1"], ["s2"]]
    assert len({a["question_id"] for a in condition["answers"]}) == 2


def test_shared_answer_duplicates_are_removed_without_changing_their_scope():
    shared = answer("period", question_id="shared", statistics=["s1", "s2"])
    view, ready = case("user_answer", "catalog", 2, {"s1": [shared], "s2": [shared]})
    got = V.prepare_b_request(view, ready)
    assert got["withheld"] is None
    assert got["request"]["tables"][0]["period_condition"]["answers"] == [shared]
    assert len(ready["period_answers"]) == 2  # Existing handoff remains untouched.


@pytest.mark.parametrize("problem", ["missing", "conflict", "wrong_kind", "wrong_statistic", "default", "partial"])
def test_invalid_conditions_withhold_the_new_request_without_changing_a_result(problem):
    view, ready = case("user_answer", "catalog", answers={"s1": [answer("period")]})
    expected = "period_condition_answer_missing"
    if problem == "missing":
        ready["period_answers"] = []
    elif problem == "conflict":
        ready["period_answers"].append(answer("period", value="2023년", text="2023년"))
        expected = "period_condition_answer_conflict"
    elif problem == "wrong_kind":
        ready["period_answers"][0]["kind"] = "basis_axis"
    elif problem == "wrong_statistic":
        ready["period_answers"][0]["statistics"] = ["s9"]
    elif problem == "default":
        view["statistics"][0]["tables"][0]["period_source"] = "default"
        expected = "period_source_not_allowed"
    elif problem == "partial":
        view["statistics"][0]["tables"][0]["period"][0].pop("end")
        expected = "period_condition_invalid"
    before = copy.deepcopy(view)
    assert V.prepare_b_request(view, ready) == {"request": None, "withheld": expected}
    assert view == before


def test_pending_answer_does_not_get_a_prepared_request():
    view, ready = case()
    view["questions"] = [{"statistics": ["s1"], "kind": "period"}]
    assert V.prepare_b_request(view, ready) == {"request": None, "withheld": "awaiting_answer"}


def test_new_request_never_replaces_the_legacy_handoff_fields():
    view, ready = case()
    legacy = copy.deepcopy(ready)
    got = V.prepare_b_request(view, ready)
    assert got["request"]["tables"][0]["period_condition"]["period_source"] == "quoted"
    assert ready == legacy and view["handoff"]["blocked"] == []
