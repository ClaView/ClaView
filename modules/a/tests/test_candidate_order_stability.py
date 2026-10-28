"""Ranking depends on overlap, not the iteration order of equal-weight grams."""
import itertools
import math
from types import SimpleNamespace

import pytest
from claview_a.search import candidates as c


def lexicon(delta=0.0):
    # Equal multisets of weights, deliberately attached to different gram names.
    weights = dict(a=1e16, b=1.0, c=1.0, d=1.0, e=1.0, f=1e16 + delta)
    docs = {'A': frozenset('abc'), 'B': frozenset('def')}
    inv = {g: [t] for t, grams in docs.items() for g in grams}
    return SimpleNamespace(title=docs, path=docs, items={t: [('value', gs)] for t, gs in docs.items()},
                           idf_title=weights, idf_item=weights, title_inv=inv, path_inv=inv,
                           item_inv={g: [(ts[0], 0)] for g, ts in inv.items()})


@pytest.mark.parametrize('channel', ['title', 'item'])
def test_equal_overlap_keeps_ties_for_every_gram_permutation(channel):
    lex = lexicon()
    scorer = c._title_scores if channel == 'title' else c._item_scores
    expected = math.fsum([1e16, 1.0, 1.0]) / math.sqrt(3)
    if channel == 'title':
        expected += c.PATH_WEIGHT * expected
    for order in itertools.permutations('abcdef'):
        scores = scorer(lex, order)
        assert scores == {'A': expected, 'B': expected}
        rows = c._rank_rows(scores)
        assert [r['table_id'] for r in rows] == ['A', 'B']
        merged = c._channel({'q': rows})
        assert [r['table_id'] for r in c.fuse({channel: merged})] == ['A', 'B']


@pytest.mark.parametrize('channel', ['title', 'item'])
def test_real_score_difference_is_not_rounded_into_a_tie(channel):
    lex = lexicon(delta=100.0)
    scorer = c._title_scores if channel == 'title' else c._item_scores
    for order in ['abcdef', 'fedcba', 'dafbec']:
        scores = scorer(lex, order)
        assert scores['B'] > scores['A']
        assert c._rank_rows(scores)[0]['table_id'] == 'B'


def test_empty_query_has_no_candidates():
    lex = lexicon()
    assert c._title_scores(lex, frozenset()) == {}
    assert c._item_scores(lex, frozenset()) == {}
