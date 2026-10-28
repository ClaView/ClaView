"""Unit tests of the reused copies on their own (no old module is imported here).

Sentences are synthetic examples written for these rules. No model or network call.
Kept for the new A (2026-10-28): the Q1 and Q3 rules moved into ``claview_a.conditions``; the tests of the node
contract wrapper, set scoring and the old model clients stay out (target code not moved).
"""

from __future__ import annotations

import pytest

from claview_a.conditions import q_rules
from claview_a.conditions.q_nodes import frequencies, normalize, q1, q3

# ---------------------------------------------------------------- Q1


def test_normalize_collapses_space_and_maps_offsets():
    question = "  가계　대출   잔액\t "
    out = normalize(question)
    assert out["query"] == "가계 대출 잔액"
    assert out["query_nospace"] == "가계대출잔액"
    assert len(out["offsets"]) == len(out["query"])
    for k, ch in enumerate(out["query"]):
        if ch != " ":
            assert question[out["offsets"][k]] == ch


def test_normalize_nfkc_expansion_keeps_source_offset():
    out = normalize("㈜ ①")
    assert out["query"] == "(주) 1"
    assert out["offsets"][:3] == [0, 0, 0]


def test_q1_node_shape_matches_pure_entry():
    assert q1({"question": "수출액 보여줘"}) == normalize("수출액 보여줘")


# ---------------------------------------------------------------- Q3


def _freq(question):
    n = normalize(question)
    return frequencies(question, n["query"], n["offsets"])


def test_frequency_row_shape_and_span():
    question = "수출액이  매달 얼마였는지"
    (row,) = _freq(question)
    assert set(row) == {"condition_id", "status", "quote", "span", "value"}
    assert (row["condition_id"], row["status"], row["value"], row["quote"]) == ("F1", "explicit", "M", "매달")
    assert question[row["span"][0]:row["span"][1]] == "매달"


def test_frequency_end_anchor():
    (row,) = _freq("잔액을 분기말마다 보여줘")
    assert (row["value"], row["anchor"]) == ("Q", "end")


def test_period_words_are_not_frequency():
    assert _freq("최근 6개월간 실적") == []
    assert _freq("2026년 1분기 실적") == []


def test_q3_node_reads_query_nospace():
    with pytest.raises(KeyError):
        q3({"question": "매달", "query": "매달", "offsets": [0, 1]})


def test_find_periods_spans_without_as_of():
    text = "2024년 1분기부터 2025년 4분기까지 분기별 잔액"
    (p,) = q_rules.find_periods(text, None)
    assert text[p["i"]:p["j"]].strip() == "2024년 1분기부터 2025년 4분기까지"
    assert (p["kind"], p["start"], p["end"], p["status"]) == ("range", "2024-Q1", "2025-Q4", "explicit")


def test_stamp_months():
    assert q_rules.stamp_months("2024-Q2") == (2024 * 12 + 3, 2024 * 12 + 5)
    assert q_rules.stamp_months("2024-13") is None
