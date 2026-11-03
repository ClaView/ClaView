"""Period wording of the question's OTHER requests (`period_context`): B hides it from the tables of this call.

The pipeline sends B one request per requirement group, so a table never sees the other groups' tables. The optional
`period_context` (`[{"ref", "period_phrase"}]`) hands that wording over; B uses it only to hide it, like the wording of
another table inside the same call. Synthetic model and fetch (no external call).
"""

from __future__ import annotations

import pytest
from tests.b_part.test_b_table_period_context import CASES, request_tables

from claview_b.period_condition import period_context_phrases, table_period_query
from claview_b.synthetic import SyntheticSelectionModel
from claview_b_api.service import BService, store

QUERY = "M2 통화량 2023년과 예금은행 대출금리 2024년 연간"


def own(phrase):
    return {"m2": {"period_phrase": phrase}}


def test_the_wording_of_a_request_outside_this_call_is_hidden():
    got = table_period_query(QUERY, "m2", own("2023년"), outside=["2024년"])
    assert "2023년" in got and "2024" not in got and len(got) == len(QUERY)
    assert "연간" in got  # a frequency wording is nobody's period phrase


def test_a_phrase_shared_with_another_request_stays_visible():
    got = table_period_query("M2 통화량과 예금은행 대출금리 2024년 연간", "m2", own("2024년"), outside=["2024년"])
    assert "2024년" in got


def test_the_longer_phrase_of_another_request_keeps_the_short_one_inside_it():
    got = table_period_query(
        "M2 통화량 2024년 그리고 예금은행 대출금리 2023년부터 2024년까지", "m2", own("2024년"),
        outside=["2023년부터 2024년까지"],
    )
    assert "2024년 그리고" in got and "2023" not in got


def test_without_its_own_wording_the_table_still_reads_the_whole_question():
    # known limit: nothing proves which words are this table's, so nothing is hidden (as inside one call)
    assert table_period_query(QUERY, "m2", {"m2": {}}, outside=["2024년"]) == QUERY
    assert table_period_query(QUERY, "m2", {}, outside=["2024년"]) == QUERY


def test_no_context_changes_nothing():
    assert table_period_query(QUERY, "m2", own("2023년")) == table_period_query(QUERY, "m2", own("2023년"), outside=[])


@pytest.mark.parametrize(
    "raw",
    [
        "x", {"ref": "a"}, [{"ref": "a"}], [{"period_phrase": "2024년"}], [{"ref": 1, "period_phrase": "x"}],
        [{"ref": "a", "period_phrase": "가" * 201}], [{"ref": "a", "period_phrase": "x"}] * 51,
    ],
)
def test_a_malformed_context_is_refused(raw):
    from claview_b.contracts import StageFailure

    with pytest.raises(StageFailure) as caught:
        period_context_phrases(raw)
    assert caught.value.code == "invalid_a_result"


def test_context_phrases_are_taken_as_they_come():
    assert period_context_phrases(None) == [] and period_context_phrases([]) == []
    assert period_context_phrases([{"ref": "T", "period_phrase": "2024년"}]) == ["2024년"]


def test_the_service_refuses_a_malformed_context():
    service = BService(SyntheticSelectionModel(store()), None, "synthetic")
    case = next(c for c in CASES if c["id"] == "TP-01")
    out, _ = service.resolve(case["query"], request_tables(case)[:1], case["as_of"], period_context=[{"ref": 1}])
    assert out["status"] != "ok" and out["issues"][0]["code"] == "invalid_a_result"


# the multi-table cases of the table-period file, one B call per table (what the pipeline does)
MULTI = [c for c in CASES if len(c["tables"]) > 1 and all(t["condition"] for t in c["tables"])]


@pytest.mark.parametrize("case", MULTI, ids=[c["id"] for c in MULTI])
def test_one_call_per_table_reads_what_one_call_for_all_tables_reads(case):
    service = BService(SyntheticSelectionModel(store()), None, "synthetic")
    tables = request_tables(case)
    for index, table in enumerate(tables):
        others = [
            {"ref": t["table_id"], "period_phrase": t["period_condition"]["period_phrase"]}
            for j, t in enumerate(tables)
            if j != index and t["period_condition"].get("period_phrase")
        ]
        out, _ = service.resolve(case["query"], [table], case["as_of"], period_context=others or None)
        assert out["status"] == "ok", (case["id"], out.get("issues"))
        (row,) = out["result"]["period_resolutions"]
        expected = case["expect"][row["table_id"]]
        assert [row["frequency"], row["requested"][0]["start"], row["requested"][0]["end"]] == expected["requested"]
        assert row["applied_sources"] == expected["sources"]
