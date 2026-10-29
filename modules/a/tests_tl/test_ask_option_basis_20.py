"""Every ask option checked against the basis its statistic states on the other axes (user 2026-10-29 06:20 UTC):
an option of another stated value stops the statistic, never a verified ask; nothing is replaced or added. Pairs
through the straight graph on the real catalog (fake model, no network); tables found by card name and basis."""

from __future__ import annotations

import json

from conftest import fill_option_evidence  # noqa: E402

from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool

M2 = "M2 상품별 구성내역"
Q = "2024년 1~3월 M2 총액을 평잔 기준으로 보여줘"
Q2 = "2024년 1~3월 M2 총액과 M2 현금통화를 보여줘. M2 총액은 평잔 기준"


def ref(card, **basis):
    rows = [r for r in card["tables"] if all(r["basis"].get(a) == [v] for a, v in basis.items())]
    return f"{card['card_id']}.{table_numbers(card)[sorted(r['table_id'] for r in rows)[-1]]}"


def ask(sid, quote, card, stock, splitters=()):
    return {"for": sid, "axis": "seasonal",
            "options": [{"value": v, "table": ref(card, stock=stock, seasonal=v)} for v in ("원계열", "계절조정")]}


def go(question, build):
    from claview_a.catalog.layer import load_layer
    layer = load_layer()
    cards = build_cards(question, build_pool(question, layer), layer)
    card = next(c for c in cards if c["name"] == M2)
    out = fill_option_evidence(build(card), cards)
    state = run(question, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"),
                "2026-10-22")
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}


def out_of(stats, splitters, asks, period="2024년 1~3월"):
    o = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                         "missing", "alternatives")}
    o["statistics"] = [{"id": sid, "quote": q, "name": q} for sid, q in stats]
    o["splitters"], o["asks"] = list(splitters), list(asks)
    o["periods"] = [{"quote": period, "for": [sid for sid, _ in stats]}]
    return o


def one(stock):
    return go(Q, lambda card: out_of([("s1", "M2 총액")], [{"quote": "평잔", "axis": "stock", "for": ["s1"]}],
                                     [ask("s1", "M2 총액", card, stock)]))


def test_ask_options_of_the_stated_basis_pass_as_a_verified_ask():
    state, d = one("평잔")
    assert d["s1"]["outcome"] == "ask" and state["action"] == "ask"
    rows = [r for r in d["s1"]["stated_basis_checks"] if r["stage"] == "ask_option"]
    assert {r["result"] for r in rows} == {"match"} and {r["option"] for r in rows} == {"원계열", "계절조정"}


def test_ask_options_of_another_stated_basis_stop_the_statistic_without_replacing_them():
    state, d = one("말잔")
    s1 = d["s1"]
    assert s1["outcome"] == "failed" and [r["code"] for r in s1["reasons"]] == ["stated_basis_conflict"]
    detail = s1["reasons"][0]["detail"]
    assert detail["stage"] == "ask_option" and detail["stated"] == ["평잔"] and detail["option_value"] == ["말잔"]
    assert state["action"] != "ask" and s1["final_tables"] == []
    assert not any(c["code"] in ("basis_switched", "unneeded_ask_to_choice") for c in s1["corrections"])
    assert [o["value"] for a in s1["asks"] for o in a["options"]] == ["원계열", "계절조정"]  # nothing added


def test_the_asked_axis_itself_is_not_checked_as_a_stated_basis():
    _, d = one("평잔")
    assert all(r["axis"] != "seasonal" for r in d["s1"]["stated_basis_checks"])


def test_a_basis_stated_for_one_statistic_does_not_reach_another_statistic_s_ask():
    def build(card):
        return out_of([("s1", "M2 총액"), ("s2", "M2 현금통화")],
                      [{"quote": "평잔", "axis": "stock", "for": ["s1"]}],
                      [ask("s1", "M2 총액", card, "평잔"), ask("s2", "M2 현금통화", card, "말잔")])
    _, d = go(Q2, build)
    assert d["s1"]["outcome"] == "ask"
    assert not any(r["code"] == "stated_basis_conflict" for r in d["s2"]["reasons"])
    assert not any(r.get("stage") == "ask_option" for r in d["s2"]["stated_basis_checks"])
