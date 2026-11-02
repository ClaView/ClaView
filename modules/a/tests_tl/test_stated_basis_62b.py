"""Plan 56 order 6-2b (bundle 5): stated-basis rows in three states, cited items as a fact source, alternatives.

A stated value on an axis the chosen table's title does not show is compared with the model's cited items verified
on that same table (CV-AB-11: '국내총생산(GDP)(실질 원계열 전년동기비)' carries 실질): the row matches with fact_source
cited_item and no warning is left; with no such item the row stays unknown with its warning. Each row has status
(match, differ, unknown), fact_source and alternative_present; a differ or unknown row with an alternative is a
re-judgment target (record only). A basis that excludes values carries a basis_excluded warning (the AView keeps
the excluded values: tests_api/test_aview_excluded_62b.py). Real catalog, fake model, no network; tables found by
card name, never by ID.
"""

from __future__ import annotations

import json

import pytest
from conftest import fill_option_evidence  # noqa: E402
from test_explicit_conditions_53 import NEGATED, STOCK, m2

from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool

AS_OF = "2026-10-22"
Q = "2015년 이후 실질 GDP 성장률(전년동기비) 추이를 분기별 꺾은선 그래프로 보여줘"
ITEM = "국내총생산(GDP)(실질 원계열 전년동기비)"


def gdp(layer, evidence):
    cards = build_cards(Q, build_pool(Q, layer), layer)
    card = next(c for c in cards if c["name"] == "주요지표")
    table = next(r["table_id"] for r in card["tables"] if "Q" in {p["frequency"] for p in r["periods"]})
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": "s1", "quote": "GDP 성장률(전년동기비)", "name": "GDP 성장률"}]
    out["splitters"] = [{"quote": "실질", "axis": "price", "for": ["s1"]}]
    out["periods"] = [{"quote": "2015년 이후", "for": ["s1"]}]
    out["frequencies"] = [{"quote": "분기별", "for": ["s1"]}]
    out["choices"] = [{"for": ["s1"], "table": f"{card['card_id']}.{table_numbers(card)[table]}",
                       "evidence": evidence}]
    out = fill_option_evidence(out, cards)
    state = run(Q, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state["record"]["decisions"][0]


def test_a_cited_item_of_the_chosen_table_shows_the_stated_value(layer):
    d = gdp(layer, [{"kind": "item", "text": ITEM}])
    assert d["outcome"] == "selected"
    (row,) = d["stated_basis_checks"]
    assert (row["result"], row["status"], row["fact_source"]) == ("match", "match", "cited_item")
    assert not [w for w in d["warnings"] if w["code"] == "stated_basis_unverified"]
    assert d["basis"]["price"] == {"value": ["실질"], "source": "splitter", "fact_source": "cited_item"}


@pytest.mark.parametrize("evidence", [[{"kind": "title", "text": "주요지표"}], [{"kind": "item", "text": "없는 항목"}]])
def test_without_a_verified_cited_item_the_value_stays_unknown(layer, evidence):
    d = gdp(layer, evidence)
    (row,) = d["stated_basis_checks"]
    assert (row["result"], row["status"], row["fact_source"]) == ("table_value_unknown", "unknown", None)
    assert [w["code"] for w in d["warnings"] if w["code"] == "stated_basis_unverified"] == ["stated_basis_unverified"]
    assert row["alternative_present"] is False and not d.get("rejudge_targets")


def test_an_excluded_value_is_warned(layer):
    _, d = m2(layer, "원계열", [STOCK, {"quote": NEGATED, "axis": "seasonal", "for": ["s1"]}])
    s = d["s1"]
    assert s["outcome"] == "selected" and s["basis"]["seasonal"]["excluded"] == ["계절조정"]
    assert {"code": "basis_excluded", "axis": "seasonal", "excluded": ["계절조정"]} in s["warnings"]
