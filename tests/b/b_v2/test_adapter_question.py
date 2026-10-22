"""Question-level table selection -> one-scope B request (user decision 8)."""

from __future__ import annotations

from claview.b.adapter_question import QUESTION_SCOPE_ID, to_b_request_question_level
from claview.b.adapter_v3 import to_legacy_a_result
from claview.b.contract import check_request


def _a_result() -> dict:
    return {
        "tables": [
            {"org_id": "301", "table_id": "T1", "table_name": "one"},
            {"org_id": "301", "table_id": "T2", "table_name": None},
            {"org_id": "301", "table_id": "T1", "table_name": "one"},
        ]
    }


def test_all_tables_become_one_scope_each_table_once_in_order():
    request = to_b_request_question_level("q", _a_result(), as_of="2026-10-20")
    assert check_request(request) == []
    assert request["source"] == "question_level"
    assert [s["scope_id"] for s in request["scopes"]] == [QUESTION_SCOPE_ID]
    assert [t["table_id"] for t in request["scopes"][0]["tables"]] == ["T1", "T2"]
    assert request["scopes"][0]["hints"] == {}
    assert request["as_of"] == "2026-10-20"


def test_round_trip_to_the_current_b_input_keeps_the_tables():
    request = to_b_request_question_level("q", _a_result())
    assert to_legacy_a_result(request)["tables"] == [
        {"org_id": "301", "table_id": "T1", "table_name": "one"},
        {"org_id": "301", "table_id": "T2", "table_name": None},
    ]


def test_hints_and_decision_are_passed_through():
    hints = {"text": "q", "negated": ["x"]}
    request = to_b_request_question_level("q", _a_result(), decision="confirm", hints=hints)
    scope = request["scopes"][0]
    assert scope["decision"] == "confirm" and scope["hints"] == hints
    assert scope["hints"] is not hints
    assert check_request(request) == []


def test_no_table_gives_no_scope_and_a_skipped_entry():
    request = to_b_request_question_level("q", {"tables": []})
    assert request["scopes"] == []
    assert request["skipped"] == [{"scope_id": QUESTION_SCOPE_ID, "reason": "no_table"}]
    assert check_request(request) == []


def test_a_table_without_org_is_reported_by_the_check():
    request = to_b_request_question_level("q", {"tables": [{"table_id": "T1"}]})
    assert any("org_id or table_id empty" in p for p in check_request(request))
