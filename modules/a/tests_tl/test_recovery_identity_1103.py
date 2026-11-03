"""Catalog-name mentions cannot all be positive targets when they conflict."""
from copy import deepcopy

import pytest
from test_reselection_guard_1103 import checked, named_fixture, period_fixture, swapped

from claview_a import graph as G
from claview_a import reselection as R


def selector_fixture():
    _, output, cards, views = period_fixture()
    views["T1"].periods = [("Y", "2000", "2025")]
    cards[0]["tables"][0]["periods"][0]["start"] = "2000"
    cards[0]["catalog_matches"] = [{"table_id": "T1", "axis": "분류 판", "value": "개정 13판", "item_id": "i13"}]
    question = "개정 13판 2010~2025년 연간 산업 장기 통계 말고 산업 세부 통계의 취업자와 실업자를 보여줘"
    first = checked(question, output, cards, views)
    fixed = deepcopy(output)
    fixed["focuses"][0]["quotes"].append("개정 13판")
    fixed["focuses"][0]["text"] += " 개정 13판"
    second = checked(question, fixed, cards, views)
    return first, second


def test_conflicting_mentions_allow_unrelated_repair_at_the_original_table():
    first, second = selector_fixture()
    assert first["decisions"][0]["outcome"] == "failed"
    assert second["decisions"][0]["outcome"] == "selected"
    ambiguity = first["recovery_identity_ambiguities"]["s1"]
    assert ambiguity["fixed_tables"] == ["T1"]
    assert not ambiguity["positive_target_verified"]
    assert "s1" not in first["recovery_identities"]
    before = deepcopy(first)
    merged = G.merge_rounds(first, second, first["rejudge"]["targets"])
    assert merged["decisions"][0]["outcome"] == "selected"
    assert merged["recovery_identity_ambiguities"] == first["recovery_identity_ambiguities"]
    assert first == before


@pytest.mark.parametrize("table", ["T2", "NEW"])
def test_conflicting_mentions_do_not_license_a_different_table(table):
    first, second = selector_fixture()
    second["decisions"][0].update(representative=table, final_tables=[{"table_id": table}])
    assert R.identity_losses(first, second, "s1", "s1") == ["explicit_target:ambiguous_table_change"]


def test_ambiguity_without_an_original_choice_cannot_make_a_choice():
    first, _, _ = named_fixture("산업조사 신판과 산업조사 구판 고용", "고용")
    original, cards, views = named_fixture(first["question"], "고용")
    original["decisions"][0].update(representative=None, final_tables=[])
    R.identities(original, cards, views)
    assert R.identity_losses(original, swapped(original, "T1"), "s1", "s1")


def test_unambiguous_explicit_target_still_rejects_a_wrong_original_table():
    first, cards, views = named_fixture()
    first["decisions"][0].update(representative="T2", final_tables=["T2"])
    R.identities(first, cards, views)
    assert not first["recovery_identity_ambiguities"]
    assert R.identity_losses(first, swapped(first, "T2"), "s1", "s1")
    assert R.identity_losses(first, swapped(first, "T1"), "s1", "s1") == []


def test_consistent_agency_survey_and_title_keep_the_original_protection():
    first, cards, views = named_fixture("기관가나다 산업조사 신판 고용", "기관가나다 산업조사 신판 고용")
    R.identities(first, cards, views)
    assert not first["recovery_identity_ambiguities"]
    assert R.identity_losses(first, swapped(first, "T1"), "s1", "s1") == []
    assert R.identity_losses(first, swapped(first, "T3"), "s1", "s1")


def test_agency_and_specific_title_conflict_are_recorded_without_picking_one():
    first, cards, views = named_fixture("기관라마바 산업조사 신판 고용", "기관라마바 산업조사 신판 고용")
    R.identities(first, cards, views)
    ambiguity = first["recovery_identity_ambiguities"]["s1"]
    assert {m["kind"] for m in ambiguity["mentions"]} == {"table", "survey_or_agency"}
    assert not ambiguity["positive_target_verified"]
    assert R.identity_losses(first, swapped(first, "T1"), "s1", "s1") == []
    assert R.identity_losses(first, swapped(first, "T3"), "s1", "s1")


def test_conflicting_mentions_never_open_the_new_period_reselection():
    question, output, cards, views = period_fixture()
    question += " 산업 세부 통계 또는 산업 장기 통계"
    first = checked(question, output, cards, views)
    assert first["recovery_identity_ambiguities"]["s1"]
    assert "period_reselection" not in first["decisions"][0]


def test_single_excluded_mention_remains_an_explicit_known_limit():
    # There is no target polarity field in the current contract. Do not infer it from a phrase.
    first, cards, views = named_fixture("산업조사 구판 제외하고 고용", "고용")
    R.identities(first, cards, views)
    assert not first["recovery_identity_ambiguities"]
    assert R.identity_losses(first, swapped(first, "T1"), "s1", "s1")
