"""Plan 53, change B (user 2026-10-30 02:07 UTC): a representative tied on the newest end stays.

The final choice among period/edition candidates is newest first; when the newest end ties, the candidates' order
(the card position) is no priority, so a representative that passes the check and covers the requested frequency and
period stays. A newer candidate still wins, a representative that fails a check or the request keeps no priority,
and stitching is unchanged. This is no proof that tied tables hold the same statistic (plan 53, section 4.4).

Tables are taken from the catalog in the tests only (the newest-end ties of one title the catalog has, told apart by
their catalog path where the title is the same); nothing in the code names a table.
"""

from __future__ import annotations

import json

import pytest
from conftest import fill_option_evidence  # noqa: E402

from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"


@pytest.fixture(scope="module")
def views(layer):
    return selection_catalog(layer).views


def pick(views, rep, others, periods=(), frequencies=(), usable=None):
    candidates = [rep, *others]  # the representative first: on a tied end it lost to the later candidate before
    conditions = PR.read_quotes(list(periods), list(frequencies))["conditions"]
    usable = usable if usable is not None else {t: "representative_checked" if t == rep else "same_value_names"
                                               for t in candidates}
    return PR.final_tables(rep, candidates, usable, views, conditions, AS_OF)


def chosen(got):
    return [r["table_id"] for r in got["tables"]]


# the four new-loan/balance pairs of one title each (same items, same frequencies and ends, paths differ)
PAIRS = [("DT_181Y003", "DT_181Y004"), ("DT_181Y005", "DT_181Y006"), ("DT_181Y007", "DT_181Y008"),
         ("DT_181Y009", "DT_181Y010")]


@pytest.mark.parametrize("a, b", PAIRS)
def test_both_members_of_a_tied_pair_stay_when_chosen(views, a, b):
    assert views[a].title == views[b].title and views[a].path != views[b].path
    assert PR._end_key(views[a]) == PR._end_key(views[b])
    for rep, other in ((a, b), (b, a)):
        got = pick(views, rep, [other])
        assert got["status"] == "selected" and chosen(got) == [rep]
        assert got["tie"] == {"tables": got["tie"]["tables"], "kept": "representative"}
        assert set(got["tie"]["tables"]) == {a, b}


@pytest.mark.parametrize("periods, frequencies", [((), ()), (("2020년부터 2024년까지",), ("연간",)),
                                                  (("최근 3년",), ()), (("2024년",), ("분기별",))])
def test_the_tie_holds_with_stated_periods_and_frequencies(views, periods, frequencies):
    a, b = PAIRS[0]
    for rep, other in ((a, b), (b, a)):
        assert chosen(pick(views, rep, [other], periods, frequencies)) == [rep]


def test_the_candidates_order_does_not_change_the_tied_representative(views):
    a, b = PAIRS[0]
    for order in ([a, b], [b, a]):
        assert chosen(PR.final_tables(a, order, {a: "representative_checked", b: "same_value_names"}, views, [],
                                      AS_OF)) == [a]


def test_a_representative_that_covers_is_kept_against_a_newer_edition(views):
    """Policy ⑦ (plan 56 order 7, registered expectation change): the representative covering the request is the
    final table, a newer edition included. Before (plan 53): newest first, the newer DT_102Y002 won."""
    old, new = "DT_102Y102", "DT_102Y002"
    assert PR._end_key(views[new]) > PR._end_key(views[old])
    got = pick(views, old, [new], ("2003년",), ("연간",))
    assert chosen(got) == [old] and "tie" not in got


def test_an_old_representative_without_the_latest_period_is_switched_as_before(views):
    old, new = "DT_101Y009", "DT_161Y015"
    assert chosen(pick(views, old, [new])) == [new]


def test_a_representative_without_the_requested_frequency_keeps_no_priority(views):
    year, quarter = "DT_200Y121", "DT_200Y162"
    assert PR._end_key(views[year]) == PR._end_key(views[quarter])
    assert chosen(pick(views, year, [quarter], (), ("분기별",))) == [quarter]
    assert chosen(pick(views, year, [quarter])) == [year]


def test_a_representative_that_failed_a_check_keeps_no_priority(views):
    a, b = PAIRS[0]
    got = pick(views, a, [b], usable={b: "same_value_names"})
    assert chosen(got) == [b]


def test_a_tie_without_the_representative_is_recorded_as_order(views):
    # plan 56 order 7 (input only): the representative outside the tie shares the pair's unit, since an edition of
    # another unit is a confirmed incompatibility now (before: DT_101Y009, 십억원 against 십만원)
    a, b = PAIRS[0]
    got = PR.final_tables(PAIRS[1][0], [a, b], {a: "same_value_names", b: "same_value_names"}, views, [], AS_OF)
    assert got["tie"]["kept"] == "candidate_order"


def test_stitching_is_unchanged(views):
    old, new = "DT_102Y102", "DT_102Y002"
    for rep in (old, new):
        got = pick(views, rep, [new if rep == old else old], ("1990년부터 2010년까지",), ("연간",))
        assert got["status"] == "selected" and got["detail"] == "stitched"
        assert chosen(got) == [new, old]


# ---------------------------------------------------------------- through the graph

# plan 56 order 5 (policies ②④, user 2026-10-31 17:53 item 1): the tie of the newest end is checked here, not the
# period, so the question and the made-up answer quote a period and a frequency (before, none was quoted and the
# default window and the longest frequency, 연간, were assumed). The assertions are unchanged.
Q = "최근 5년 차주당 가계대출 신규취급액을 연령별 지역별로 연간으로 보여줘"


def through_graph(layer, rep_word, item=False):
    views = selection_catalog(layer).views

    def build(cards):
        card = next(c for c in cards if any(r["table_id"] == PAIRS[0][0] for r in c["tables"]))
        t = next(r["table_id"] for r in card["tables"] if rep_word in (views[r["table_id"]].path or "")
                 and r["table_id"] in PAIRS[0])
        ref = f"{card['card_id']}.{table_numbers(card)[t]}"
        evidence = [{"kind": "title", "text": card["name"]}]
        out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                               "missing", "alternatives")}
        out["statistics"] = [{"id": "s1", "quote": "차주당 가계대출 신규취급액", "name": "차주당 가계대출"}]
        out["choices"] = [{"for": ["s1"], "table": ref, "evidence": evidence}]
        out["periods"] = [{"quote": "최근 5년", "for": ["s1"]}]
        out["frequencies"] = [{"quote": "연간", "for": ["s1"]}]
        return out, t
    cards = build_cards(Q, build_pool(Q, layer), layer)
    out, rep = build(cards)
    out = fill_option_evidence(out, cards)
    state = run(Q, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return rep, {d["statistic"]: d for d in state["record"]["decisions"]}


@pytest.mark.parametrize("word", ["신규취급액", "잔액"])
def test_the_model_representative_reaches_the_final_table(layer, word):
    """Before plan 53 the new-loan representative was handed off as the balance table (card order decided)."""
    rep, d = through_graph(layer, word)
    assert d["s1"]["outcome"] == "selected"
    assert [r["table_id"] for r in d["s1"]["final_tables"]] == [rep]
    assert not any(c["code"] == "final_tables_by_period" for c in d["s1"]["corrections"])
    assert d["s1"]["period"]["tie"]["kept"] == "representative"
