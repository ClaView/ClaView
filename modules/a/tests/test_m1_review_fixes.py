"""Fixes of the M1-1 review (plan 04 M1): name protection before the list/exclude rules, the word-boundary choice of
name spans, and frequency/period words glued to a general noun. Synthetic catalog only ("가상" names).

Kept for the new A (2026-10-28): the name-span choice (``claview_a.catalog.spans``). The tests of 1) and 3) and of the
calc words call ``find_conditions``, ``find_exclude_conditions``, ``compound_time_word`` and ``find_calc_hints``,
which were not moved; they stay out (target code not moved)."""

from __future__ import annotations

from claview_a.catalog.index import TermIndex
from claview_a.catalog.spans import NameSpanIndex, read_particles


# ---------------------------------------------------------------- 2) boundary-valid choice of name spans


def _finder(keys: list[str]) -> NameSpanIndex:
    entries = {k: [{"key": k, "name": k, "type": "indicator", "table_ids": ["T"], "obj_id": "A", "obj_name": "A",
                    "item_id": k, "path": "exact", "variant": "name", "kind": "value", "status": "draft"}]
               for k in keys}
    return NameSpanIndex(index=TermIndex(entries=entries, span_keys=frozenset(keys)), particles=read_particles())


def test_a_valid_split_survives_when_the_longest_names_do_not_fit():
    finder = _finder(["abcde", "ghijk", "fg", "hijk", "abcd", "ef"])
    got = [s["quote"] for s in finder.find("abcdefghijk")]
    assert got == ["abcde", "fg", "hijk"]  # valid splits: abcde|fg|hijk or abcd|ef|ghijk (equal; longer name first)
    assert "".join(got) == "abcdefghijk"


def test_the_choice_covers_most_characters_and_keeps_boundaries():
    finder = _finder(["abcdef", "ab", "cdefgh", "gh"])
    # abcdef|gh and ab|cdefgh are both valid and cover the word; the longer name further left wins
    assert [s["quote"] for s in finder.find("abcdefgh")] == ["abcdef", "gh"]
    # no valid split: "cd" has no right boundary (nothing chosen starts after it), so "ab" has none either
    assert _finder(["ab", "cd"]).find("abcdx") == []
    # a name with a particle after it is valid alone
    assert [s["quote"] for s in _finder(["abcd", "bcdef"]).find("abcd를")] == ["abcd"]
