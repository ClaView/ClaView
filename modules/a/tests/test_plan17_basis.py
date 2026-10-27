"""Plan 17 basis containment and split-condition proof, without changing interpretation."""

from __future__ import annotations

import copy

import pytest

from claview_a.select.basis import occurrences, values
from claview_a.select.cover import signature
from claview_a.select.decision import assignment_gaps
from claview_a.select.evidence import fit_basis
from claview_a.select.facts import basis_value_facts
from claview_a.select.tables import TableView


def view(title):
    return TableView("T", "O", title, title.replace(" ", ""), "", "", [], [], [], [],
                     [("M", "2020.01", "2026.08")], True)


def condition(value):
    return {"id": "b1", "kind": "basis", "value": value,
            "attrs": {"axis": "seasonal", "value": value, "polarity": "+"}}


@pytest.mark.parametrize("text,expected", [
    ("비계절조정계열", {"원계열"}),
    ("계절조정계열", {"계절조정"}),
    ("비계절조정·계절조정", {"원계열", "계절조정"}),
    ("계절 조정 / 비계절 조정", {"원계열", "계절조정"}),
])
def test_long_occurrence_protects_only_its_own_span(text, expected):
    assert values(text, "seasonal") == expected
    for row in occurrences(text, "seasonal"):
        lo, hi = row["span"]
        assert "".join(text[lo:hi].split()) == "".join(row["form"].split())


@pytest.mark.parametrize("title,wanted,status", [
    ("가상지표(비계절조정)", "계절조정", "conflict"),
    ("가상지표(계절조정)", "원계열", "conflict"),
    ("가상지표(비계절조정)", "원계열", "match"),
    ("가상지표(계절조정)", "계절조정", "match"),
    ("가상지표(비계절조정·계절조정)", "계절조정", "match"),
])
def test_evidence_facts_and_signature_share_the_recognizer(title, wanted, status):
    v = view(title)
    before = copy.deepcopy(v)
    assert fit_basis(condition(wanted), v)[0] == status
    fact = basis_value_facts(v, "seasonal", wanted)
    assert fact["title_conflict"] == (status == "conflict")
    assert ("title" in fact["requested_in"]) == (status == "match")
    assert signature(v)["seasonal"] == frozenset(values(title, "seasonal"))
    assert all(h["field"] == "title" for h in fact["occurrences"])
    assert v == before


def test_split_axis_is_not_an_exemption_from_unproven_condition():
    needs = {"evidence": {"indicator": None, "targets": [], "conditions": [
        {"condition_id": "b1", "kind": "basis", "axis": "seasonal", "polarity": "+", "byeol": False},
        {"condition_id": "b2", "kind": "basis", "axis": "seasonal", "polarity": "+", "byeol": False},
    ]}}
    facts = {"T": {"refuted": [{"check": "basis", "condition_id": "b1"}]}}
    assert "unproven_condition" in assignment_gaps(needs, {"flags": []}, ["T"], facts, True)
