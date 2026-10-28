"""The grammar-word dictionary of question interpretation (plan 08 T2): ``data/lexicon.json`` and
``interpret/lexicon.py``.

- every class says what it is, and every word of it is a word (a non-empty string without surrounding spaces);
- the property lists of a class name words of that class (final request forms are request words, ...);
- word classes that would read one word two ways share no word (a request word is no exclusion predicate, ...);
- no Hangul word is written in the code of ``interpret/`` (docstrings and comments aside): the code names a class.

Kept for the new A (2026-10-28), where the dictionary is ``claview_a.text.lexicon`` and ``interpret/`` is
``claview_a/text/``. The check that the stages after 2.2 read only the token layer stays out: those stage modules
were not moved (target code not moved).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from claview_a.catalog.text import index_key
from claview_a.text import lexicon as LX

INTERPRET = Path(LX.__file__).resolve().parent
HANGUL = re.compile(f"[{LX.HANGUL_ANY}]")
CHARACTER_CLASSES = ("script", "punctuation")  # character sets and ranges, not words


def _leaves(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            if k in ("regex", "value", "start", "end"):  # pattern entries: checked by the 2.2 tests
                continue
            yield from _leaves(x)
    elif isinstance(v, list):
        for x in v:
            yield from _leaves(x)


def _keys(words) -> set[str]:
    return {index_key(w) for w in words}


def test_every_class_says_what_it_is():
    classes = [k for k in LX.LEX if not k.startswith("_")]
    assert classes
    for name in classes:
        cls = LX.LEX[name]
        assert isinstance(cls, dict) and cls.get("_about"), name


@pytest.mark.parametrize("name", [k for k in LX.LEX if not k.startswith("_") and k not in CHARACTER_CLASSES])
def test_every_word_of_a_class_is_a_word(name):
    cls = LX.LEX[name]
    words = [w for k, v in cls.items() if not k.startswith("_") for w in _leaves(v)]
    patterns = cls.get("patterns", [])
    assert words or patterns, name
    assert all(isinstance(w, str) and w and w == w.strip() for w in words), name
    for e in patterns:  # word patterns: a regex, its value and the word boundaries it needs
        assert e["regex"] and e["value"] and e["start"] in ("any", "word") and e["end"] in ("any", "word"), e
        re.compile(e["regex"])


def test_property_lists_name_words_of_their_class():
    rq = LX.LEX["request"]
    forms = rq["forms"]
    for form, words in forms.items():
        assert _keys(words) <= _keys(rq["words"]), form
    seen: dict[str, str] = {}
    for form, words in forms.items():
        for k in _keys(words):
            assert k not in seen, f"{k} is {seen.get(k)} and {form}"
            seen[k] = form
    co = LX.LEX["coordinator"]
    assert set(co["after_name_only"]) <= set(co["particles"])
    assert set(co["conjunctive"]) <= set(co["words"])
    pa = LX.LEX["particles"]
    assert set(pa["restrictive"]) <= set(pa["designate"])
    assert set(pa["aux"]) <= set(pa["endings"])
    assert LX.LEX["byeol"]["marker"] and LX.EXCLUSION["negated_by"]


@pytest.mark.parametrize("a, b", LX.CONFLICTS)
def test_conflicting_classes_share_no_word(a, b):
    assert not LX.class_words(a) & LX.class_words(b), sorted(LX.class_words(a) & LX.class_words(b))


def test_no_exclusion_predicate_is_a_request_form():
    """A predicate that excludes ('필요 없어') is never a complete request: its words are no final form."""
    final = LX.REQUEST_FINAL
    assert not final & LX.REQUEST_EXIST_NEGATIVE
    assert not final & _keys(LX.EXCLUDE_VERBS)


def _code_strings(tree: ast.AST):
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node


@pytest.mark.parametrize("path", sorted(INTERPRET.glob("*.py")), ids=lambda p: p.name)
def test_no_hangul_word_in_interpret_code(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [(n.lineno, n.value) for n in _code_strings(tree) if HANGUL.search(n.value)]
    assert found == []
