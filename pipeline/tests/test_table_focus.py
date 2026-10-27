"""The optional per-table `focus` of the A -> B table list (port side)."""

from __future__ import annotations

import pytest

from claview_pipeline.b_port import check_table_request, to_table_request


def entry(table_id, **extra):
    return {"org_id": "301", "table_id": table_id, "table_name": f"표 {table_id}", **extra}


def request(*tables):
    return to_table_request(
        {
            "selections": [{"tables": list(t)} for t in tables],
            "question": "경상수지와 GDP 성장률",
            "as_of": "2026-10-22",
            "requirement_keys": [{"requirement_id": "R1"}],
        },
        a_assumptions={},
    )


def test_focus_is_passed_through_when_a_gives_it():
    req = request([entry("T1", focus="경상수지"), entry("T2", focus="GDP 성장률")])
    assert [t.get("focus") for t in req["a_result"]["tables"]] == ["경상수지", "GDP 성장률"]
    assert check_table_request(req) == []


def test_without_focus_the_request_is_what_it_was():
    req = request([entry("T1"), entry("T2")])
    assert all(set(t) == {"org_id", "table_id", "table_name"} for t in req["a_result"]["tables"])
    assert check_table_request(req) == []


def test_other_extra_fields_are_still_dropped():
    req = request([entry("T1", indicator="x", reason="y")])
    assert set(req["a_result"]["tables"][0]) == {"org_id", "table_id", "table_name"}


def test_a_table_serving_two_requirements_lists_both_indicators_once():
    req = request(
        [entry("T1", focus="경상수지")],
        [entry("T1", focus="서비스수지")],
        [entry("T1", focus="경상수지")],
    )
    tables = req["a_result"]["tables"]
    assert len(tables) == 1 and tables[0]["focus"] == "경상수지 / 서비스수지"


def test_a_later_focus_fills_a_table_that_had_none():
    req = request([entry("T1")], [entry("T1", focus="경상수지")])
    assert req["a_result"]["tables"][0]["focus"] == "경상수지"


@pytest.mark.parametrize("bad", ["", "  ", 3, ["a"]])
def test_check_rejects_an_unusable_focus(bad):
    req = request([entry("T1")])
    req["a_result"]["tables"][0]["focus"] = bad
    assert check_table_request(req) == ["table_focus"]


def test_check_still_rejects_unknown_fields_and_missing_required_ones():
    req = request([entry("T1")])
    req["a_result"]["tables"][0]["extra"] = 1
    assert check_table_request(req) == ["table_fields"]
    req = request([entry("T1")])
    del req["a_result"]["tables"][0]["table_name"]
    assert check_table_request(req) == ["table_fields"]
