"""Recovery preserves each typed condition; all responses are scripted and offline."""
from __future__ import annotations

import copy

import pytest

from claview_a import contract as K
from claview_a import graph as G

V8 = "tl-contract-2026-10-28.8"
V9 = "tl-contract-2026-10-28.9"
QUESTION = "2023년과 2024년 상반기 및 2024년 한 해 월별과 분기별 대출금리"


def p(quote="2024년 한 해", **fields):
    return {"for": ["s1"], "quote": quote, "meaning": "range", "form": "absolute", "start": "none",
            "end": "none", "count": "none", "unit": "none", "read": "ok", "refers_to": [], **fields}


def f(quote="월별", value="M", **fields):
    return {"for": ["s1"], "quote": quote, "value": value, "anchor": "none", **fields}


def record(periods, frequencies, *, version=V8, question=QUESTION, stats=None):
    return {"question": question, "as_of": "2026-10-29", "parse": {"answer_contract": version},
            "model_output": {"statistics": stats or [{"id": "s1", "quote": "대출금리"}],
                             "periods": periods, "frequencies": frequencies, "splitters": []},
            "decisions": [{"statistic": "s1", "outcome": "failed", "reasons": []}]}


def lost(periods, next_periods, frequencies=None, next_frequencies=None, **kwargs):
    first = record(periods, frequencies or [f()], **kwargs)
    second = record(next_periods, next_frequencies or frequencies or [f()], **kwargs)
    return G.condition_preservation(first, second, "s1", "s1")


@pytest.mark.parametrize("replacement", [[], [p(meaning="not_stated", form="none")],
                                         [p(meaning="user_undecided", form="none")],
                                         [p(meaning="latest_available", form="none")]])
def test_failed_period_cannot_disappear_or_change_meaning(replacement):
    valid = p("2023년", start="2023", end="same")
    bad = p(read="failed", form="none")
    assert lost([valid, bad], [valid, *replacement])


def test_frequency_failure_does_not_unprotect_period_with_same_quote():
    assert lost([p(start="2024", end="same")], [p(start="2023", end="same")],
                [f("2024년 한 해", "unreadable")], [f("2024년 한 해", "Y")])


def test_period_conflict_keeps_the_valid_range():
    assert lost([p(start="2024", end="same"), p("대출금리", meaning="not_stated", form="none")],
                [p(start="2023", end="same")])


def test_frequency_conflict_keeps_both_explicit_values():
    valid = [p(start="2024", end="same")]
    assert lost(valid, valid, [f(), f("분기별", "Q")], [f()])


def test_only_conflicting_absence_declaration_may_be_removed():
    valid = p(start="2024", end="same")
    assert not lost([valid, p("대출금리", meaning="not_stated", form="none")], [valid])
    assert not lost([valid], [valid], [f(), f("대출금리", "not_stated")], [f()])


@pytest.mark.parametrize("before, after", [("2024년 한 해", "2024년"), ("한 해", "2024년 한 해")])
def test_a_failed_quote_can_be_repaired_with_a_uniquely_corresponding_excerpt(before, after):
    assert not lost([p(before, form="none", read="failed")], [p(after, start="2024", end="same")])


def test_a_different_clause_is_not_a_corresponding_excerpt():
    assert lost([p("2024년 한 해", form="none", read="failed")], [p("2023년", start="2023", end="same")])


def test_failed_frequency_cannot_become_not_stated():
    valid = [p(start="2024", end="same")]
    assert lost(valid, valid, [f(value="unreadable")], [f(value="not_stated")])


def test_a_missing_kind_can_be_repaired_without_changing_valid_other_kind():
    valid = [p(start="2024", end="same")]
    first, second = record(valid, []), record(valid, [f()])
    assert not G.condition_preservation(first, second, "s1", "s1")


def test_declared_absence_can_be_corrected_only_during_an_internal_recovery(monkeypatch):
    monkeypatch.setattr(K, "SEMANTIC_PERIOD_CONTRACTS", (*K.SEMANTIC_PERIOD_CONTRACTS, V9))
    unknown = [p("대출금리", meaning="not_stated", form="none")]
    explicit = [p(start="2024", end="same")]
    assert not lost(unknown, explicit, [f(value="unreadable")], [f()], version=V9)
    assert lost(unknown, explicit, [f()], [f()], version=V9)
    assert lost(unknown, explicit, [f(value="unreadable")], [f()], version=V8)


def test_declared_absence_cannot_cover_deletion_of_a_failed_explicit_row(monkeypatch):
    monkeypatch.setattr(K, "SEMANTIC_PERIOD_CONTRACTS", (*K.SEMANTIC_PERIOD_CONTRACTS, V9))
    unknown = p("대출금리", meaning="not_stated", form="none")
    failed = p("2024년 상반기", read="failed", form="none")
    assert lost([unknown, failed], [p("2023년", start="2023", end="same")], version=V9)


def test_renumbered_same_period_reference_compares_the_original_statistics():
    stats = [{"id": "s1", "quote": "대출금리"}, {"id": "s2", "quote": "다른 통계"}]
    row = p("같은 기간", meaning="same_as", form="none", refers_to=["s2"])
    first = record([row], [f(value="unreadable")], stats=stats,
                   question="대출금리와 다른 통계를 같은 기간 월별로")
    changed = G._rename_refs(first["model_output"], {"s1": "s8", "s2": "s9"})
    changed["statistics"] = [{"id": "s8", "quote": "대출금리"}, {"id": "s9", "quote": "다른 통계"}]
    changed["frequencies"] = [f(**{"for": ["s8"]})]
    second = copy.deepcopy(first)
    second["model_output"] = changed
    assert not G.condition_preservation(first, second, "s1", "s8")


def test_correction_records_only_its_typed_row_problem():
    bad_period = p("2024년 한 해", form="none", read="failed")
    bad_freq = f("월별", "unreadable")
    first = record([bad_period], [bad_freq])
    second = record([p("2024년", start="2024", end="same")], [f()])
    fixes = G.condition_corrections(first, second, "s1", "s1")
    periods = next(row for row in fixes if row["kind"] == "periods")
    frequencies = next(row for row in fixes if row["kind"] == "frequencies")
    assert periods["why"] == ["period_structure_invalid"]
    assert frequencies["why"] == ["frequency_unreadable"]
    assert periods["first"][0]["quote"] == "2024년 한 해"
    assert periods["second"][0]["quote"] == "2024년"


def test_applied_user_answers_are_protected_by_the_merge_guard():
    first = record([p(start="2024", end="same")], [f()])
    second = copy.deepcopy(first)
    first["decisions"][0]["condition_answers"] = [{"kind": "period", "text": "2024년"}]
    assert "user_answers" in G.condition_preservation(first, second, "s1", "s1")


def test_contradictory_absence_can_leave_while_the_failed_explicit_row_is_repaired():
    absent = p("대출금리", meaning="not_stated", form="none")
    failed = p(read="failed", form="none")
    assert not lost([absent, failed], [p(start="2024", end="same")])
