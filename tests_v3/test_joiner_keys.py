"""Joiner keys of catalog names and middle-dot-free name spans (plan 14 I8; catalog/names.py, index.py, spans.py).

A catalog name written with the and-word ("가상식품 및 가상음료") or a list mark ("가상자산/가상부채") is found when a
question writes the join otherwise (a middle dot, a joiner particle, the and-word). Synthetic in-memory catalog only
(made-up "가상" names and table IDs); no network, no model, no question file.
"""

from __future__ import annotations

import pytest

from claview_v3.catalog.index import build_index
from claview_v3.catalog.loader import Axis, Catalog, Item, Table
from claview_v3.catalog.names import joiner_keys
from claview_v3.catalog.spans import build_name_span_index
from claview_v3.catalog.text import paren_front
from claview_v3.interpret import lexicon as LX


def _catalog(rows) -> Catalog:
    """Rows of (table ID, table name, [value names on one axis])."""
    tables = {}
    for tid, name, values in rows:
        items = tuple(Item(tid, "A", f"a{k}", v, None, None, 1, None, None) for k, v in enumerate(values))
        axes = (Axis(tid, "A", "계정항목별", None, 1, items),) if items else ()
        tables[tid] = Table(tid, None, name, None, None, paren_front(name), "가상 > 통계", None, axes, (), (), (), True)
    return Catalog(tables=tables, sources={})


ROWS = [
    ("T1", "가상물가표", ["가상식품 및 가상음료", "가상자산/가상부채", "가상A·가상B", "가상갑, 가상을·가상병",
                       "가상정 및 가상무·가상기"]),
    ("T2", "가상소비표", ["가상식품가상음료"]),  # a catalog name of its own equal to a joiner key of T1's value
    ("T3", "가상생산표", ["가상기계·가상장비", "가상제조", "가상기계 및 가상장비 가상제조"]),
]


@pytest.fixture(scope="module")
def ix():
    return build_index(_catalog(ROWS))


@pytest.fixture(scope="module")
def spans(ix):
    return build_name_span_index(ix)


def _rows(cands):
    return [(c["kind"], c["table_ids"], c["item_id"], c["variant"], c["path"]) for c in cands]


# ---------------------------------------------------------------- joiner_keys


def test_list_joiners_come_from_the_lexicon():
    assert LX.LIST_JOINER_PARTICLES == ("과", "와")
    assert set(LX.LIST_JOINER_PARTICLES) <= set(LX.JOINER_PARTICLES)


def test_and_word_removed_and_written_as_joiner_particles():
    assert joiner_keys("가상a및가상b") == ["가상a가상b", "가상a과가상b", "가상a와가상b"]


def test_marks_written_as_the_and_word_and_joiner_particles():
    assert joiner_keys("가상a·가상b") == ["가상a및가상b", "가상a과가상b", "가상a와가상b"]
    assert joiner_keys("가상a/가상b,가상c") == ["가상a및가상b및가상c", "가상a과가상b과가상c", "가상a와가상b와가상c"]


def test_and_word_and_marks_together_are_also_removed_together():
    got = joiner_keys("가상a,가상b및가상c")
    assert got[-1] == "가상a가상b가상c"
    assert "가상a,가상b가상c" in got and "가상a및가상b및가상c" in got
    assert len(got) == len(set(got)) == 7  # uniform replacement: 3 + 3 + 1, no products


def test_a_key_without_joins_has_no_joiner_keys():
    assert joiner_keys("가상예금") == [] and joiner_keys("") == []


# ---------------------------------------------------------------- term index


def test_and_name_found_without_the_and_word_and_with_a_particle(ix):
    exact = _rows(ix.lookup("가상식품 및 가상음료"))
    assert exact == [("value", ["T1"], "a0", "name", "exact")]
    assert _rows(ix.lookup("가상식품과 가상음료")) == exact  # same candidate, variant and path
    assert _rows(ix.lookup("가상식품와 가상음료")) == exact
    assert {"가상식품과가상음료", "가상식품와가상음료"} <= ix.derived_keys


def test_marks_found_as_the_and_word_and_a_particle(ix):
    exact = _rows(ix.lookup("가상자산/가상부채"))
    assert _rows(ix.lookup("가상자산 및 가상부채")) == exact
    assert _rows(ix.lookup("가상자산과 가상부채")) == exact
    assert _rows(ix.lookup("가상A 및 가상B")) == _rows(ix.lookup("가상A·가상B"))
    # marks and the and-word together: every one removed
    assert _rows(ix.lookup("가상정가상무가상기")) == [("value", ["T1"], "a4", "name", "exact")]
    assert "가상자산및가상부채" in ix.derived_keys


def test_no_candidate_is_added_to_a_catalog_own_key(ix):
    # "가상식품가상음료" is T2's own value name and the and-free key of T1's value: it keeps only its own candidate
    assert _rows(ix.lookup("가상식품가상음료")) == [("value", ["T2"], "a0", "name", "exact")]
    assert "가상식품가상음료" not in ix.derived_keys


# ---------------------------------------------------------------- name spans


@pytest.mark.parametrize("text, quote", [
    ("가상식품·가상음료를 보여줘", "가상식품·가상음료"),  # middle dot for the and-word
    ("가상식품ㆍ가상음료를 보여줘", "가상식품ㆍ가상음료"),  # the Hangul middle dot (NFKC) too
    ("가상식품과 가상음료 보여줘", "가상식품과 가상음료"),  # joiner particle for the and-word
    ("가상자산 및 가상부채 보여줘", "가상자산 및 가상부채"),  # the and-word for a slash
])
def test_a_name_written_with_another_join_is_one_span(spans, ix, text, quote):
    (span,) = [s for s in spans.find(text) if s["quote"] == quote]
    assert text[span["start"]:span["end"]] == quote
    assert span["candidates"] >= 1
    assert {c["item_id"] for c in ix.entries[span["key"]] if c["table_ids"] == ["T1"]} <= {"a0", "a1"}


def test_span_positions_are_those_of_the_original_text(spans):
    text = "  가상식품·가상음료　보여줘"
    (span,) = spans.find(text)
    assert (span["start"], span["end"]) == (2, 11) and span["quote"] == "가상식품·가상음료"


def test_a_name_written_with_its_own_dots_keeps_its_own_key(spans, ix):
    (span,) = spans.find("가상A·가상B 보여줘")
    assert span["key"] == "가상a·가상b" and span["key"] not in ix.derived_keys
    (span,) = spans.find("가상갑, 가상을·가상병 보여줘")  # a comma kept, a middle dot skipped
    assert span["quote"] == "가상갑, 가상을·가상병" and span["key"] == "가상갑,가상을·가상병"
    assert span["key"] not in ix.derived_keys


def test_commas_are_not_skipped(spans):
    assert not any(s["quote"] == "가상식품,가상음료" for s in spans.find("가상식품,가상음료 보여줘"))


def test_a_dot_does_not_make_a_shorter_dotted_name_win(spans):
    # "가상기계·가상장비" + "가상제조" must not beat the one name "가상기계 및 가상장비 가상제조" by counting the dot
    (span,) = spans.find("가상기계·가상장비 가상제조 보여줘")
    assert span["quote"] == "가상기계·가상장비 가상제조" and span["key"] == "가상기계가상장비가상제조"
