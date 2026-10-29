"""A stated basis checked apart from the card's splits (user 2026-10-29 04:59 UTC): the model's own splitters for a
statistic against its chosen table's title basis, also for a single table or a card without that split. Normal/error
pairs through the straight graph on the real catalog (fake model, no network). Tables are found by card name and
title basis, never by ID; nothing is switched or asked by this check."""

from __future__ import annotations

import json

from conftest import fill_option_evidence  # noqa: E402

from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"


def ref(cards, name, layer, **basis):
    """The card reference of the newest table of card ``name`` whose title basis has these values (``None``: none)."""
    card = next(c for c in cards if c["name"] == name)
    views = selection_catalog(layer).views
    rows = [r for r in card["tables"]
            if all((r["basis"].get(a) or None) == ([v] if v else None) for a, v in basis.items())]
    rows.sort(key=lambda r: PR._end_key(views[r["table_id"]]), reverse=True)
    return f"{card['card_id']}.{table_numbers(card)[rows[0]['table_id']]}", rows[0]["table_id"], card


def output(stats, splitters, choices, period="2024년", frequency="월별") -> dict:
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    ids = [sid for sid, _ in stats]
    out["statistics"] = [{"id": sid, "quote": q, "name": q} for sid, q in stats]
    out["splitters"] = list(splitters)
    out["periods"] = [{"quote": period, "for": ids}]
    out["frequencies"] = [{"quote": frequency, "for": ids}] if frequency else []
    out["choices"] = [{"for": [sid], "table": t, "evidence": [{"kind": "title", "text": name}]}
                      for sid, t, name in choices]
    return out


def go(question, layer, build):
    cards = build_cards(question, build_pool(question, layer), layer)
    out = fill_option_evidence(build(cards), cards)
    state = run(question, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}


def codes(d):
    return [r["code"] for r in d["reasons"]]


def no_switch_or_ask(d):
    assert d["asks"] == [] and not any(c["code"] in ("basis_switched", "unneeded_ask_to_choice")
                                       for c in d["corrections"])


# ---------------------------------------------------------------- a single table (no siblings, no split)

CA = "경상수지"


def single(layer, word):
    q = f"2024년 월별 {word} 경상수지를 보여줘"

    def build(cards):
        r, _, card = ref(cards, CA, layer, seasonal="계절조정")
        return output([("s1", "경상수지")], [{"quote": word, "axis": "seasonal", "for": ["s1"]}],
                      [("s1", r, card["name"])])
    return go(q, layer, build)


def test_a_single_table_with_the_stated_basis_is_selected(layer):
    state, d = single(layer, "계절조정")
    assert d["s1"]["outcome"] == "selected" and state["action"] == "handoff"
    (row,) = d["s1"]["stated_basis_checks"]
    assert row["result"] == "match" and row["stated"] == ["계절조정"] and row["table_value"] == ["계절조정"]
    assert row["quotes"] == ["계절조정"] and row["evidence"] == [{"kind": "bracket", "text": "계절조정"}]


def test_a_single_table_against_the_stated_basis_fails_without_switch_or_ask(layer):
    """The reproduced case: 원계열 stated, 경상수지(계절조정) chosen, selected and handed off before this check."""
    state, d = single(layer, "원계열")
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
    assert state["action"] == "fail" and d["s1"]["final_tables"] == []
    (row,) = d["s1"]["stated_basis_checks"]
    assert (row["statistic"], row["axis"], row["result"], row["card_splits_axis"]) == ("s1", "seasonal", "conflict",
                                                                                       False)
    assert row["stated"] == ["원계열"] and row["table_value"] == ["계절조정"]
    no_switch_or_ask(d["s1"])


# ---------------------------------------------------------------- the table shows no value on the axis


def test_a_table_without_a_value_on_the_axis_is_unverified_not_a_conflict(layer):
    q = "2024년 월별 원계열 국제수지 경상수지를 보여줘"

    def build(cards):
        r, _, card = ref(cards, "국제수지", layer, seasonal=None)
        return output([("s1", "경상수지")], [{"quote": "원계열", "axis": "seasonal", "for": ["s1"]}],
                      [("s1", r, card["name"])])
    state, d = go(q, layer, build)
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []
    (row,) = d["s1"]["stated_basis_checks"]
    assert row["result"] == "table_value_unknown" and row["table_value"] is None
    assert {"code": "stated_basis_unverified", "axis": "seasonal", "stated": ["원계열"], "table": row["table"]} in \
        d["s1"]["warnings"]


# ---------------------------------------------------------------- one statistic's basis is not spread


def test_a_basis_stated_for_one_statistic_is_not_spread_to_the_other(layer):
    q = "2024년 월별 원계열 경상수지와 상품수지를 보여줘"

    def build(cards):
        r, _, card = ref(cards, CA, layer, seasonal="계절조정")
        basis = [{"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
        choices = [("s1", r, card["name"]), ("s2", r, card["name"])]
        return output([("s1", "경상수지"), ("s2", "상품수지")], basis, choices)
    _, d = go(q, layer, build)
    assert codes(d["s1"]) == ["stated_basis_conflict"]
    assert d["s2"]["outcome"] == "selected" and d["s2"]["stated_basis_checks"] == []


# ---------------------------------------------------------------- siblings that split the axis (unchanged)

M2 = "M2 상품별 구성내역"


def m2(layer, seasonal_word, chosen):
    q = f"2024년 1~3월 M2 총액을 평잔·{seasonal_word} 기준으로 보여줘"

    def build(cards):
        r, _, card = ref(cards, M2, layer, stock="평잔", seasonal=chosen)
        out = output([("s1", "M2 총액")], [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                                         {"quote": seasonal_word, "axis": "seasonal", "for": ["s1"]}],
                     [("s1", r, card["name"])], period="2024년 1~3월", frequency=None)
        out["choices"][0]["evidence"] = [{"kind": "item", "text": f"M2(평잔 {chosen})"}]
        return out
    return go(q, layer, build)


def test_siblings_split_on_the_axis_keep_the_normal_selection(layer):
    _, d = m2(layer, "원계열", "원계열")
    assert d["s1"]["outcome"] == "selected" and d["s1"]["basis"]["seasonal"]["source"] == "splitter"
    assert d["s1"]["stated_basis_checks"] == []  # the split axis is checked by the existing step


def test_siblings_split_on_the_axis_keep_the_conflict_without_evidence(layer):
    _, d = m2(layer, "계절조정", "원계열")
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
    assert d["s1"]["reasons"][0]["detail"]["stated_value_tables"]  # a sibling of the stated value exists
    no_switch_or_ask(d["s1"])


# ---------------------------------------------------------------- an edition change keeps the stated basis

DC = "예금취급기관 개관표"


def edition(layer, word):
    q = f"2002년 월별 {word} 기준 예금취급기관 개관표의 국내신용부문을 보여줘"

    def build(cards):
        r, _, card = ref(cards, DC, layer, stock="말잔")  # the newest edition as the representative
        out = output([("s1", "국내신용부문")], [{"quote": word, "axis": "stock", "for": ["s1"]}],
                     [("s1", r, card["name"])], period="2002년")
        out["choices"][0]["evidence"].append({"kind": "item", "text": "국내신용부문"})
        return out
    return go(q, layer, build)


def test_an_edition_chosen_by_period_keeps_the_stated_basis(layer):
    _, d = edition(layer, "말잔")
    s1 = d["s1"]
    assert s1["outcome"] == "selected" and s1["stated_basis_checks"][0]["result"] == "match"
    final = [r["table_id"] for r in s1["final_tables"]]
    assert final and final != [s1["representative"]]  # the period chose an older edition
    assert any(c["code"] == "final_tables_by_period" for c in s1["corrections"])
    assert "final_recheck" in s1["checks_done"] and codes(s1) == []  # the recheck found the same basis


def test_an_edition_card_against_the_stated_basis_fails_before_periods(layer):
    _, d = edition(layer, "평잔")
    s1 = d["s1"]
    assert codes(s1) == ["stated_basis_conflict"] and "periods" not in s1["checks_done"]
    no_switch_or_ask(s1)
