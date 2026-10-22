"""Certainty check 8 "verified structure" (decision 10/18): the structure signature, its policy switch and the
verified list built from the specification cases (src/claview/a/data/verified_structures.json).

Synthetic catalogs only (test_spec_judgement). Each test sets the policy it needs through the environment variable, so
the file passes with the policy on (default) and off alike.
"""

from __future__ import annotations

import json
import re

import pytest
import spec_catalogs
import test_spec_judgement as tj
import test_spec_requirements as tr
from spec_support import Layers

from claview.a.contract import check as shape
from claview.a.interpret import structure
from claview.a.interpret.pipeline import interpret_question

_HANGUL = re.compile(r"[가-힣]")


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    return tj.SpecLayers(tmp_path_factory, tj.CATALOGS)


@pytest.fixture(scope="module")
def req_layers(tmp_path_factory):
    return Layers(tmp_path_factory, spec_catalogs.CATALOGS)


def _judge(question: str, layer, monkeypatch, on: bool) -> dict:
    monkeypatch.setenv(structure.POLICY_ENV, "1" if on else "0")
    return interpret_question(question, layer)


def test_policy_file_sets_the_check(monkeypatch):
    """User decision 2026-10-19: the policy file turns check 8 off; the environment variable still overrides it."""
    monkeypatch.delenv(structure.POLICY_ENV, raising=False)
    assert structure.policy()["verified_structures_only"] is False
    assert structure.verified_only() is False
    monkeypatch.setenv(structure.POLICY_ENV, "0")
    assert structure.verified_only() is False
    monkeypatch.setenv(structure.POLICY_ENV, "1")
    assert structure.verified_only() is True


def test_verified_list_is_the_signatures_of_the_certain_cases(layers, req_layers, monkeypatch):
    """The list holds exactly the signatures of the cases expected certain (scripts/a/build_verified_structures.py)."""
    monkeypatch.setenv(structure.POLICY_ENV, "0")
    want = set()
    for c in tj.CASES:
        if c["expect"].get("certain") is True:
            j = interpret_question(c["question"], layers.get(c["layer"]))["judgement"]
            assert j["certain"], (c["id"], j["failed_conditions"])
            want.add(j["structure"])
    for c in tr.CASES:
        if (c["expect"].get("judgement") or {}).get("certain") is True:
            j = interpret_question(c["question"], req_layers.get(c.get("layer")))["judgement"]
            assert j["certain"], j["failed_conditions"]
            want.add(j["structure"])
    doc = json.loads(structure.VERIFIED_FILE.read_text(encoding="utf-8"))
    have = {structure.signature_key(s["signature"]) for s in doc["structures"]}
    assert have == want
    assert sum(s["cases"] for s in doc["structures"]) == doc["cases"]


def test_unverified_structure_is_undecided_only_with_the_policy_on(layers, monkeypatch):
    layer = layers.get("jt_basis")
    q = "가상GDP를 연간과 분기와 월간으로 각각 보여줘"  # three copies by frequency: no case verifies it
    off = _judge(q, layer, monkeypatch, on=False)
    assert off["judgement"]["certain"] is True and off["interpretation"]["status"] == "ok"
    on = _judge(q, layer, monkeypatch, on=True)
    assert on["judgement"]["certain"] is False
    assert on["judgement"]["failed_conditions"] == ["unverified_structure"]
    assert on["interpretation"]["status"] == "ask"
    assert on["judgement"]["requirement_draft"] == off["judgement"]["requirement_draft"]  # the reading is the same
    assert shape.check_analysis(on, q) == []


def test_verified_structure_stays_certain_with_the_policy_on(layers, monkeypatch):
    on = _judge("가상GDP를 명목과 실질로 보여줘", layers.get("jt_basis"), monkeypatch, on=True)
    assert on["judgement"]["certain"] is True, on["judgement"]["failed_conditions"]


def test_check_8_comes_after_the_seven_checks(layers, monkeypatch):
    on = _judge("가상갑지수와 가상을지수를 보여줘", layers.get("jt_mixed"), monkeypatch, on=True)
    assert "mixed_reading" in on["judgement"]["failed_conditions"]
    assert "unverified_structure" not in on["judgement"]["failed_conditions"]


@pytest.mark.parametrize("question, layer", [
    ("가상A금리와 가상B금리를 보여주고 가상C금리와 가상D금리를 신규취급액 기준으로 각각 보여줘", "jt_rates"),
    ("가상국제수지표의 가상경상수지 대비 가상상품수지 비율을 보여줘", "jt_bop"),
    ("2020년 가상경상수지를 보여주고 2021년 가상경상수지는 보여줘", "jt_bop"),
])
def test_signature_holds_no_words(question, layer, layers, monkeypatch):
    j = _judge(question, layers.get(layer), monkeypatch, on=True)["judgement"]
    assert not _HANGUL.search(j["structure"]), j["structure"]
    sig = json.loads(j["structure"])
    assert set(sig) == {"clauses", "other_clauses", "items", "phrases", "context", "relation_operands", "rows",
                        "drafts", "conditions"}


def test_two_item_clauses_are_not_verified(layers, monkeypatch):
    """Conservative list: no case expected certain has two item clauses, so such a question stays undecided."""
    on = _judge("가상A금리와 가상B금리를 보여주고 가상C금리와 가상D금리를 신규취급액 기준으로 각각 보여줘",
                layers.get("jt_rates"), monkeypatch, on=True)
    assert on["judgement"]["failed_conditions"] == ["unverified_structure"]
