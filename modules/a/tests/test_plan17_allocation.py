"""Cover proof, all bundle members, frame isolation and bounded-search transparency."""

import copy

import pytest

from claview_a.select.allocation import rank_assignments


def test_possible_singleton_does_not_hide_proven_three_table_bundle():
    possible = {"unknown": {1, 2, 3}, "A": {1}, "B": {2}, "C": {3}}
    proven = {"unknown": set(), "A": {1}, "B": {2}, "C": {3}}
    got = rank_assignments({1, 2, 3}, possible, proven, {t: 1 for t in possible},
                           dict.fromkeys(possible, "frame"))
    first = got["assignments"][0]
    assert first["proof_status"] == "proven" and first["tables"] == ["A", "B", "C"]
    assert len(first["by_table"]) == 3 and first["missing"] == set()
    assert next(a for a in got["assignments"] if a["tables"] == ["unknown"])["proof_status"] == "possible"


def test_unknown_or_different_execution_frames_do_not_combine():
    for frames in ({"A": None, "B": None}, {"A": "x", "B": "y"}):
        got = rank_assignments({1, 2}, {"A": {1}, "B": {2}}, {"A": {1}, "B": {2}}, {}, frames)
        assert all(len(a["tables"]) == 1 and a["proof_status"] == "partial" for a in got["assignments"])


def test_partial_bundle_preserves_missing_request_cells():
    got = rank_assignments({1, 2, 3}, {"A": {1}, "B": {2}}, {"A": {1}, "B": {2}}, {},
                           {"A": "frame", "B": "frame"})
    first = got["assignments"][0]
    assert first["tables"] == ["A", "B"] and first["proof_status"] == "partial"
    assert first["missing"] == {3}


def test_search_cut_does_not_remove_tables_or_claim_a_refutation():
    possible = {"A": {1}, "B": {2}, "C": {1}, "D": {2}}
    got = rank_assignments({1, 2}, possible, possible, {}, dict.fromkeys(possible, "frame"), cap=1)
    assert not got["search_complete"] and got["cut_reasons"]
    assert {t for a in got["assignments"] for t in a["tables"]} == set(possible)
    assert "refutations" not in got


def test_independent_requirements_do_not_share_cell_state():
    possible, proven = {"T": {1, 2}}, {"T": {1}}
    before = copy.deepcopy((possible, proven))
    got = rank_assignments({1, 2}, possible, proven, {}, {"T": "frame"})
    got["assignments"][0]["by_table"]["T"]["possible"].clear()
    assert (possible, proven) == before
    other = rank_assignments({3}, {"T": {3}}, {"T": {3}}, {}, {"T": "other"})
    assert other["assignments"][0]["proof_status"] == "proven"


@pytest.mark.parametrize("proven", [{"T": {2}}, {"other": {1}}])
def test_invalid_proof_domain_is_rejected(proven):
    with pytest.raises(ValueError):
        rank_assignments({1}, {"T": {1}}, proven, {}, {"T": "frame"})
