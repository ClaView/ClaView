"""Catalog axes on the cards (plan 48, users 2026-10-29 10:40 and 11:37 UTC): every table's axes by their catalog
names, an axis of at most ``AXIS_LIST_MAX`` values listed whole and citable as item evidence of that table, a larger
one by name and count only. Normal/error pairs on the real catalog with a fake model (no network); cards are found by
name, tables by their catalog axes, never by ID. The path, the evidence check and the scorer read the same facts."""

from __future__ import annotations

import json

from claview_a import check as CH
from claview_a.cards import AXIS_LIST_MAX, build_cards, card_facts, card_values, render_card, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog
from tl_eval import score as S

AS_OF = "2026-10-29"
Q058 = "최근 8개 분기 동안 전자지급결제대행(PG) 이용금액을 결제수단별로 보여 주세요."
Q004 = "은행들의 주택담보대출 태도가 2020년 이후 분기마다 어떻게 바뀌었는지 알고 싶습니다."
Q082 = "원/달러 환율을 2022년부터 월평균으로 그려 주세요."


def cards_of(question, layer):
    return build_cards(question, build_pool(question, layer), layer)


def card(cards, name):
    return next(c for c in cards if c["name"] == name)


def ref(c, table=None):
    t = table or c["tables"][0]["table_id"]
    return f"{c['card_id']}.{table_numbers(c)[t]}", t


def blank():
    return {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                            "missing", "alternatives")}


def choose(quote, table_ref, evidence, period=None, frequency=None):
    out = blank()
    out["statistics"] = [{"id": "s1", "quote": quote, "name": quote}]
    out["choices"] = [{"for": ["s1"], "table": table_ref, "evidence": evidence}]
    out["periods"] = [{"quote": period, "for": ["s1"]}] if period else []
    out["frequencies"] = [{"quote": frequency, "for": ["s1"]}] if frequency else []
    return out


def go(question, out, layer):
    state = run(question, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"),
                AS_OF)
    return state, state["record"]["decisions"][0]


def citation_codes(d):
    return [p["code"] for p in d["citation"].get("problems", [])]


def test_a_small_axis_is_listed_whole_with_its_catalog_name(layer):
    c = card(cards_of(Q058, layer), "전자지급결제대행")
    _, t = ref(c)
    axes = {a["axis"]: a for a in c["axes"][t]}
    assert axes["계정항목별"]["listed"] and axes["계정항목별"]["count"] == 5
    assert {"신용카드", "계좌이체", "가상계좌"} <= set(axes["계정항목별"]["values"])
    text = render_card(c)
    assert "항목 축 전체: 구분코드별(2): 이용건수, 이용금액 / 계정항목별(5): 신용카드, 계좌이체, 가상계좌" in text
    assert "결제수단" not in text  # the axis keeps the catalog's name; nothing is renamed or guessed


def test_the_needed_breakdown_shown_on_the_card_is_valid_item_evidence(layer):
    cards = cards_of(Q058, layer)
    c = card(cards, "전자지급결제대행")
    r, t = ref(c)
    out = choose("전자지급결제대행(PG) 이용금액", r, [{"kind": "title", "text": "전자지급결제대행"},
                                                {"kind": "item", "text": "이용금액"},
                                                {"kind": "item", "text": "신용카드"}],
                 "최근 8개 분기 동안", None)
    state, d = go(Q058, out, layer)
    assert citation_codes(d) == [] and d["citation"]["status"] == "verified"
    assert "신용카드" in d["item_evidence"] and d["outcome"] == "selected"
    assert [x["table_id"] for x in d["final_tables"]] == [t]


def test_a_similar_table_without_the_breakdown_cannot_cite_it(layer):
    cards = cards_of(Q058, layer)
    other = card(cards, "선불전자지급수단")
    _, t = ref(other)
    assert all("신용카드" not in a["values"] for a in other["axes"][t])  # its axes hold 이용건수·이용금액 only
    r, _ = ref(other)
    out = choose("전자지급결제대행(PG) 이용금액", r, [{"kind": "title", "text": "선불전자지급수단"},
                                                {"kind": "item", "text": "신용카드"}], "최근 8개 분기 동안")
    _, d = go(Q058, out, layer)
    assert "evidence_of_other_table" in citation_codes(d)  # the value is another table's fact
    assert d["citation"]["status"] != "verified"


def test_a_question_with_no_table_stays_missing(layer):
    out = blank()
    out["statistics"] = [{"id": "s1", "quote": "원/달러 환율", "name": "원/달러 환율"}]
    out["missing"] = [{"for": ["s1"], "search_words": ["원/달러 환율"]}]
    state, d = go(Q082, out, layer)
    assert d["outcome"] == "not_in_pool" and d["final_tables"] == [] and state["action"] == "fail"
    assert not any("환율" in v for c in state["cards"] for rows in c["axes"].values() for a in rows
                   for v in a["values"])  # the cards gained values, not the asked statistic


def test_a_large_axis_gives_its_name_and_count_and_its_values_are_not_citable(layer):
    q = "2024년 분기별 실질 원계열 국내총생산에 대한 지출을 보여줘"
    cards = cards_of(q, layer)
    c = card(cards, "국내총생산에 대한 지출")
    r, t = ref(c)
    big = [a for a in c["axes"][t] if not a["listed"]]
    assert big and big[0]["count"] > AXIS_LIST_MAX and big[0]["values"] == []
    views = selection_catalog(layer).views
    shown = {it["name"] for it in card_values(c) if t in it["tables"]}
    hidden = next(it.name_raw for it in views[t].items if it.name_raw not in shown)
    out = choose("국내총생산에 대한 지출", r, [{"kind": "title", "text": "국내총생산에 대한 지출"},
                                          {"kind": "item", "text": hidden}], "2024년", "분기별")
    _, d = go(q, out, layer)
    assert "evidence_not_on_card" in citation_codes(d) or "evidence_of_other_table" in citation_codes(d)


def test_repeated_names_on_one_axis_are_told_apart(layer):
    views = selection_catalog(layer).views
    table = next(t for t, v in views.items() for o, _ in v.dims
                 if len([i for i in v.items if i.obj_id == o]) <= AXIS_LIST_MAX
                 and len({i.name_raw for i in v.items if i.obj_id == o})
                 < len([i for i in v.items if i.obj_id == o]))
    q = views[table].title + "를 보여줘"
    c = next(c for c in cards_of(q, layer) if table in [r["table_id"] for r in c["tables"]])
    values = [v for a in c["axes"][table] if a["listed"] for v in a["values"]]
    assert any("‹" in v for v in values)


def test_the_path_the_check_and_the_scorer_read_the_same_item_facts(layer):
    views = selection_catalog(layer).views
    for q in (Q058, Q004):
        cards = cards_of(q, layer)
        lk = CH.Cards(cards, views)
        rec = S.adapt({"cards": cards, "record": {}})
        for c in cards:
            facts = card_facts(c)
            for r in c["tables"]:
                t = r["table_id"]
                items = lk.facts_by_kind(t)["item"]
                assert items <= facts[t]
                assert S._card_texts(rec, t)["item"] == items


def test_q004_card_now_shows_the_bank_household_housing_value(layer):
    c = card(cards_of(Q004, layer), "대출행태서베이")
    r, t = ref(c)
    values = [v for a in c["axes"][t] for v in a["values"]]
    assert "국내은행-가계(주택)" in values  # a catalog value, shown; choosing it stays the next step's job
