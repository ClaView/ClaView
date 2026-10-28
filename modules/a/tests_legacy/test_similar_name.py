"""User decision 10/19 21:45 (decision 2 with fix A): which names read through a derived or similar catalog name pass
certainty check 1 (``certainty._similar_name``). Stub terms only; the spec cases (DN*, SN*, HA1, NP1, OS1, NS1) run the
same rule end to end."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from claview_a_legacy.interpret.certainty import _similar_name


def _term(*cands: dict, glued: bool = False, words=("가상A",)):
    return SimpleNamespace(tok=SimpleNamespace(glued=glued, spaced_words=list(words)), cands=list(cands))


def _cand(kind: str = "table", path: str = "lexical", variant: str = "table_core") -> dict:
    return {"kind": kind, "path": path, "variant": variant}


LAYER = SimpleNamespace(index=SimpleNamespace(entries={"가상a": [], "가상b": [], "가상c": []}))


@pytest.mark.parametrize("term, ok", [
    (_term(_cand()), True),  # a derived table name
    (_term(_cand("value", variant="abbr")), True),  # a value's derived name
    (_term(_cand("value", path="exact", variant="no_marks")), True),
    (_term(_cand("out_of_scope", path="exact", variant="out_of_scope")), False),  # an out-of-scope entry
    (_term(_cand("table", path="dictionary", variant="synonym")), False),  # a dictionary entry
    (_term(_cand("value", path="exact", variant="no_paren")), False),  # bracket-free form of a bracket name
    (_term(_cand("value", path="exact", variant="eng:no_paren")), False),
    (_term(_cand(), glued=True), False),  # read inside a word written without spaces
    (_term(_cand(), words=("가상A", "및", "가상B")), False),  # "A 및 B" of two names
    (_term(_cand(), words=("가상A", "및", "가상X")), True),  # one side is no name
    (_term(), False),  # no candidate
])
def test_similar_name(term, ok):
    assert _similar_name(term, LAYER) is ok
