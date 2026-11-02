"""A structured end-of-period anchor resolves only a redundant stock-basis ask.

Made-up answers over real catalog cards; no model or network. Explicit stock
conditions, missing evidence and invalid frequency rows remain authoritative.
"""

from __future__ import annotations

import copy
import json

import pytest

from claview_a import check as CH
from claview_a import contract as K
from claview_a import periods as PR
from claview_a.cards import build_cards
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog


def case(layer, frequency="Y", anchor="end", stock=None, polarity="required", two=False):
    names = ["M2 상품별 구성내역"] + (["Lf 상품별 구성내역"] if two else [])
    word = {"Y": "연말", "Q": "분기말", "M": "월말"}[frequency]
    question = f"2024년 {word} 원계열 " + " 및 ".join(names)
    if stock:
        question += f" {stock} 기준"
    cards = build_cards(question, build_pool(question, layer), layer)
    out = {key: [] for key in K.output_schema()["properties"]}
    views = selection_catalog(layer).views
    expected = {}
    for index, name in enumerate(names, 1):
        sid = f"s{index}"
        card = next(c for c in cards if c["name"] == name)
        tables = {}
        for value in ("말잔", "평잔"):
            offered = [r["table_id"] for r in card["tables"]
                       if value in r["basis"].get("stock", [])
                       and "원계열" in r["basis"].get("seasonal", [])]
            tables[value] = max(offered, key=lambda table: PR._end_key(views[table]))
        expected[sid] = tables
        out["statistics"].append({"id": sid, "quote": name, "name": name})
        out["focuses"].append({"for": [sid], "text": name, "quotes": [name]})
        out["periods"].append({"for": [sid], "quote": "2024년", "meaning": "range", "refers_to": [],
                               "form": "absolute", "start": "2024", "end": "same", "count": "none",
                               "unit": "none", "read": "ok"})
        out["frequencies"].append({"for": [sid], "quote": word, "value": frequency, "anchor": anchor})
        out["splitters"].append({"for": [sid], "quote": "원계열", "axis": "seasonal", "value": "원계열",
                                 "polarity": "required"})
        if stock:
            out["splitters"].append({"for": [sid], "quote": f"{stock} 기준", "axis": "stock", "value": stock,
                                     "polarity": polarity})
        out["asks"].append({"for": sid, "axis": "stock", "options": [
            {"value": value, "table": tables[value], "evidence": [{"kind": "title", "text": name}]}
            for value in ("말잔", "평잔")]})
    return question, cards, out, expected


def checked(layer, data, contract=K.CONTRACT_VERSION):
    question, cards, out, _ = data
    parsed = K.parse_output(json.dumps(out, ensure_ascii=False), cards, contract)
    record = CH.check(question, cards, parsed, selection_catalog(layer).views, "2026-10-29")
    return {d["statistic"]: d for d in record["decisions"]}


def end_corrections(decision):
    return [r for r in decision["corrections"] if r.get("source") == "frequency_anchor"]


@pytest.mark.parametrize("frequency", ["Y", "Q", "M"])
def test_valid_end_anchor_resolves_stock_ask_without_changing_period_or_frequency(layer, frequency):
    data = case(layer, frequency)
    decision = checked(layer, data)["s1"]
    assert decision["outcome"] == "selected", decision["reasons"]
    assert not decision["asks"]
    assert decision["basis"]["stock"] == {"value": ["말잔"], "source": "frequency_anchor"}
    assert [r["table_id"] for r in decision["final_tables"]] == [data[3]["s1"]["말잔"]]
    assert decision["final_tables"][0]["frequency"] == frequency
    assert decision["final_tables"][0]["period"] == [{"start": "2024-01", "end": "2024-12"}]
    correction, = end_corrections(decision)
    assert correction["code"] == "unneeded_ask_to_choice"
    assert correction["frequency"] == data[2]["frequencies"][0]


def test_shared_end_anchor_resolves_each_statistics_own_ask(layer):
    data = case(layer, frequency="M", two=True)
    data[2]["frequencies"] = [{**data[2]["frequencies"][0], "for": ["s1", "s2"]}]
    decisions = checked(layer, data)
    assert [d["outcome"] for d in decisions.values()] == ["selected", "selected"]
    for sid, decision in decisions.items():
        assert decision["final_tables"][0]["table_id"] == data[3][sid]["말잔"]


def test_other_statistics_anchor_is_not_borrowed(layer):
    data = case(layer, frequency="M", two=True)
    data[2]["frequencies"][0]["anchor"] = "none"
    decisions = checked(layer, data)
    assert decisions["s2"]["outcome"] == "selected"
    assert end_corrections(decisions["s2"])
    assert decisions["s1"]["outcome"] == "ask"
    assert not end_corrections(decisions["s1"])


def test_no_end_anchor_keeps_the_real_stock_question(layer):
    decision = checked(layer, case(layer, anchor="none"))["s1"]
    assert decision["outcome"] == "ask" and decision["asks"]
    assert not end_corrections(decision)


@pytest.mark.parametrize("stock", ["평잔", "말잔"])
def test_explicit_stock_is_not_replaced_by_end_anchor(layer, stock):
    data = case(layer, stock=stock)
    decision = checked(layer, data)["s1"]
    assert decision["outcome"] == "selected"
    assert decision["basis"]["stock"]["value"] == [stock]
    assert decision["basis"]["stock"]["source"] == "splitter"
    assert decision["final_tables"][0]["table_id"] == data[3]["s1"][stock]
    assert not end_corrections(decision)


@pytest.mark.parametrize("polarity", ["excluded", "allowed", "undetermined"])
def test_end_anchor_does_not_override_other_stock_polarities(layer, polarity):
    decision = checked(layer, case(layer, stock="평잔", polarity=polarity))["s1"]
    assert not end_corrections(decision)
    assert decision["outcome"] != "selected"


@pytest.mark.parametrize("change", ["missing", "unreadable", "multiple", "bad_quote", "mixed_anchor"])
def test_invalid_or_ambiguous_frequency_does_not_resolve_ask(layer, change):
    data = case(layer)
    rows = data[2]["frequencies"]
    if change == "missing":
        rows.clear()
    elif change == "unreadable":
        rows[0]["value"] = "unreadable"
    elif change == "multiple":
        rows.append({**rows[0], "value": "Q"})
    elif change == "bad_quote":
        rows[0]["quote"] = "not in the question"
    else:
        rows.append({**rows[0], "anchor": "none"})
    decision = checked(layer, data)["s1"]
    assert not end_corrections(decision)
    assert decision["outcome"] != "selected"


def test_option_evidence_still_has_to_be_valid(layer):
    data = case(layer)
    data[2]["asks"][0]["options"][0]["evidence"][0]["text"] = "invented table title"
    decision = checked(layer, data)["s1"]
    assert decision["outcome"] == "failed"
    assert not end_corrections(decision)


def test_final_period_validation_still_blocks_unavailable_data(layer):
    data = case(layer)
    data = (data[0].replace("2024", "1800"), data[1], copy.deepcopy(data[2]), data[3])
    data[2]["periods"][0].update(quote="1800년", start="1800")
    decision = checked(layer, data)["s1"]
    assert decision["outcome"] == "failed" and not decision["final_tables"]


def test_old_quote_contract_keeps_its_existing_stock_question(layer):
    data = case(layer)
    data[2]["periods"] = [{k: r[k] for k in ("quote", "for", "meaning", "refers_to")}
                           for r in data[2]["periods"]]
    data[2]["frequencies"] = [{k: r[k] for k in ("quote", "for")} for r in data[2]["frequencies"]]
    decision = checked(layer, data, "tl-contract-2026-10-28.7")["s1"]
    assert decision["outcome"] == "ask"
    assert not end_corrections(decision)
