"""Generic named-target guards: source scope is frozen before the one recovery."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_period_semantics_1102 import F, P

from claview_a import check as CH
from claview_a import contract as K
from claview_a import graph as G
from claview_a import reselection as R
from claview_a.search.tables import ItemView, TableView


def named_fixture(question="2024년 산업조사 신판으로 고용을 보여줘", quote="산업조사 신판 고용"):
    cards = [{"name": name, "tables": [{"table_id": tid, "title": name, "brackets": [edition]}]}
             for tid, name, edition in (("T1", "산업조사 신판", "2020년 기준"),
                                       ("T2", "산업조사 구판", "2015년 기준"),
                                       ("T3", "기업고용조사", "2020년 기준"))]
    views = {tid: SimpleNamespace(path=f"{agency} > {survey} > {name}")
             for tid, agency, survey, name in (("T1", "기관가나다", "산업조사", "산업조사 신판"),
                                             ("T2", "기관가나다", "산업조사", "산업조사 구판"),
                                             ("T3", "기관라마바", "기업조사", "기업고용조사"))}
    record = {"question": question,
              "model_output": {"statistics": [{"id": "s1", "quote": quote}], "focuses": []},
              "decisions": [{"statistic": "s1", "representative": "T1", "final_tables": []}]}
    return record, cards, views


def swapped(record, table):
    after = deepcopy(record)
    after["decisions"][0].update(representative=table, final_tables=[table])
    return after


def test_explicit_title_cannot_be_swapped_but_its_own_table_can_be_fixed():
    first, cards, views = named_fixture()
    R.identities(first, cards, views)
    assert R.identity_losses(first, swapped(first, "T2"), "s1", "s1")
    assert R.identity_losses(first, swapped(first, "T1"), "s1", "s1") == []


def test_agency_survey_and_edition_are_protected_separately():
    for mention, replacement in (("기관가나다", "T3"), ("산업조사", "T3"), ("2020년 기준", "T2")):
        first, cards, views = named_fixture(f"{mention} 고용", f"{mention} 고용")
        R.identities(first, cards, views)
        assert R.identity_losses(first, swapped(first, replacement), "s1", "s1")


def test_other_statistics_named_table_is_not_borrowed():
    first, cards, views = named_fixture("산업조사 신판 고용과 생산 비교", "생산")
    first["model_output"]["statistics"].append({"id": "s2", "quote": "산업조사 신판 고용"})
    R.identities(first, cards, views)
    assert "s1" not in first["recovery_identities"]
    assert R.identity_losses(first, swapped(first, "T3"), "s1", "s1") == []


def test_wrong_initial_table_can_only_be_corrected_toward_the_named_identity():
    first, cards, views = named_fixture()
    first["decisions"][0]["representative"] = "T2"
    R.identities(first, cards, views)
    assert R.identity_losses(first, swapped(first, "T1"), "s1", "s1") == []
    assert R.identity_losses(first, swapped(first, "T3"), "s1", "s1")


def test_fixing_another_component_cannot_keep_an_already_wrong_named_table():
    first, cards, views = named_fixture()
    first["decisions"][0].update(representative="T2", final_tables=["T2"])
    R.identities(first, cards, views)
    assert R.identity_losses(first, swapped(first, "T2"), "s1", "s1")


def test_short_catalog_agency_name_is_still_an_explicit_identity():
    first, cards, views = named_fixture("통계청 고용", "통계청 고용")
    views["T1"].path = "통계청 > 산업조사 > 산업조사 신판"
    R.identities(first, cards, views)
    assert R.identity_losses(first, swapped(first, "T3"), "s1", "s1")


def period_fixture():
    views, cards = {}, []
    for i, (tid, start, name) in enumerate((("T1", "2012", "산업 세부 통계"),
                                          ("T2", "2000", "산업 장기 통계")), 1):
        items = [ItemView(j, "axis", f"{tid}.{j}", n, n, n, None, "명")
                 for j, n in enumerate(("취업자", "실업자"))]
        v = TableView(tid, "001", name, name, "기관가나다 > 산업조사 > " + name, "", items,
                      [("axis", "대상")], [], ["명"], [("Y", start, "2025")], True)
        v.by_raw = {it.name_raw: [it] for it in items}
        v.by_norm = v.by_nospace = v.by_raw
        views[tid] = v
        cards.append({"card_id": f"g{i}", "family": f"table:{tid}", "name": name, "source": "question",
                      "tables": [{"table_id": tid, "title": name, "brackets": [], "bracket_words": [],
                                  "periods": [{"frequency": "Y", "start": start, "end": "2025"}],
                                  "basis": {}, "item_basis": {}}],
                      "splits": [], "editions": [], "other_words": [], "axes": {}, "axis_items": [],
                      "matched": [{"word": it.name_raw, "items": [{"name": it.name_raw, "parent": None,
                                   "unit": "명", "tables": [tid], "item_ids": {tid: [it.item_id]}}]}
                                  for it in items]})
    q = "2010~2025년 연간 취업자와 실업자를 보여줘"
    output = {key: [] for key in K.output_schema()["properties"]}
    output.update(statistics=[{"id": "s1", "name": "취업자와 실업자", "quote": "취업자와 실업자"}],
                  focuses=[{"for": ["s1"], "text": "취업자와 실업자", "quotes": ["취업자와 실업자"]}],
                  choices=[{"for": ["s1"], "table": "g1.t1", "evidence": [
                      {"kind": "item", "text": n} for n in ("취업자", "실업자")]}],
                  periods=[P("2010~2025년", start="2010", end="2025")], frequencies=[F("연간", "Y")])
    return q, output, cards, views


def checked(q, output, cards, views):
    r = CH.check(q, cards, K.parse_output(json.dumps(output, ensure_ascii=False), cards, K.CONTRACT_VERSION),
                 views, "2026-10-29")
    R.identities(r, cards, views)
    R.prepare(r, cards, views)
    r["rejudge"] = G.rejudge_targets(r)
    return r


def test_verified_alternative_opens_review_and_requires_new_evidence():
    q, out, cards, views = period_fixture()
    first = checked(q, out, cards, views)
    target, = first["rejudge"]["targets"]
    assert target["code"] == "period_table_reselection"
    assert target["fixes"][0]["candidates"][0]["table_id"] == "T2"
    new = deepcopy(out)
    new["choices"][0]["table"] = "g2.t1"
    second = checked(q, new, cards, views)
    assert R.period_losses(first, second, "s1", "s1") == []
    merged = G.merge_rounds(first, second, [target])
    assert [r["table_id"] for r in merged["decisions"][0]["final_tables"]] == ["T2"]
    assert merged["decisions"][0]["period"]["tables"][0]["period"] == [
        {"start": "2010-01", "end": "2025-12"}]


@pytest.mark.parametrize("change", ["no_alternative", "coverage", "frequency", "items", "agency", "units",
                                    "basis", "user_answer", "explicit_table", "relative", "open", "aggregation",
                                    "other_failure", "unverified", "duplicate_item"])
def test_reselection_is_not_a_general_failure_retry(change):
    q, out, cards, views = period_fixture()
    if change == "no_alternative":
        cards.pop()
    elif change == "coverage":
        views["T2"].periods = [("Y", "2012", "2025")]
    elif change == "frequency":
        views["T2"].periods = [("M", "2000.01", "2025.12")]
    elif change == "items":
        cards[1]["matched"].pop()
    elif change == "agency":
        views["T2"].org_id = "002"
    elif change == "units":
        views["T2"].table_units = ["천명"]
    elif change == "basis":
        cards[1]["tables"][0]["basis"] = {"seasonal": ["계절조정"]}
    elif change == "relative":
        out["periods"][0].update(form="relative", start="none", end="none", count="20", unit="Y")
    elif change == "open":
        out["periods"][0]["end"] = "open"
    elif change == "duplicate_item":
        cards[1]["matched"][0]["items"][0]["item_ids"]["T2"].append("T2.99")
    # Check first, then apply facts that are normally recorded after user answer / validation.
    r = CH.check(q, cards, K.parse_output(json.dumps(out, ensure_ascii=False), cards, K.CONTRACT_VERSION),
                 views, "2026-10-29")
    d = r["decisions"][0]
    if change == "user_answer":
        d["condition_answers"] = [{"kind": "period", "quote": "2010~2025년"}]
    elif change == "explicit_table":
        r["question"] += " 산업 세부 통계 표로"
    elif change == "aggregation":
        r["model_output"]["aggregations"] = [{"for": ["s1"], "method": "sum"}]
    elif change == "other_failure":
        d["reasons"].append({"code": "focus_quote_not_in_question"})
    elif change == "unverified":
        d["citation"]["status"] = "unverified"
    R.identities(r, cards, views)
    R.prepare(r, cards, views)
    assert "period_reselection" not in d


@pytest.mark.parametrize("change", ["new_pool_table", "shorter_period", "items", "focus", "unverified"])
def test_second_response_cannot_escape_the_frozen_reselection_scope(change):
    q, out, cards, views = period_fixture()
    first = checked(q, out, cards, views)
    new = deepcopy(out)
    new["choices"][0]["table"] = "g2.t1"
    if change == "items":
        new["choices"][0]["evidence"].pop()
    if change == "focus":
        new["focuses"][0]["text"] = "취업자"
    if change == "shorter_period":
        new["periods"][0]["start"] = "2012"
    second = checked(q, new, cards, views)
    d = second["decisions"][0]
    if change == "new_pool_table":
        d.update(representative="NEW", final_tables=["NEW"])
    if change == "unverified":
        d["citation"]["status"] = "unverified"
    got = G.merge_rounds(first, second, first["rejudge"]["targets"])
    assert got["decisions"][0]["outcome"] == "failed"
    assert got["rejudge"]["rejected"]


def test_graph_uses_the_existing_one_slot_and_keeps_the_failure_when_exhausted(monkeypatch):
    q, out, cards, views = period_fixture()
    monkeypatch.setattr(G, "build_pool", lambda *a, **kw: {})
    monkeypatch.setattr(G, "build_cards", lambda *a, **kw: cards)
    monkeypatch.setattr(G, "selection_catalog", lambda _: SimpleNamespace(views=views))
    new = deepcopy(out)
    new["choices"][0]["table"] = "g2.t1"
    calls = []

    def judge(payload, request):
        calls.append(payload)
        return json.dumps(out if len(calls) == 1 else new, ensure_ascii=False)

    judge.answer_contract = K.CONTRACT_VERSION
    state = G.run(q, None, judge, K.Settings("fake"), "2026-10-29", research=True)
    assert len(calls) == 2 and [r["table_id"] for r in state["record"]["decisions"][0]["final_tables"]] == ["T2"]
    assert calls[1]["targets"][0]["facts"][0]["code"] == "period_table_reselection"
    assert G.after_check({"round": 2}) == "research_end"
    calls.clear()
    state = G.run(q, None, judge, K.Settings("fake"), "2026-10-29", research=True,
                  ledger=SimpleNamespace(available=lambda slot: False))
    assert len(calls) == 1 and state["record"]["decisions"][0]["outcome"] == "failed"
    assert state["research"]["end"] == "rejudge_call_limit"
