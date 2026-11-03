"""Source omissions, attribution and bounded recovery; scripted responses only."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_focus_contract_1102 import ScopeJudge
from test_period_semantics_1102 import AS_OF, F, P, checked, output
from test_recovery_scope_preservation_1103 import adopted, basis_change, record

from claview_a import check as CH
from claview_a import condition_coverage as CC
from claview_a import contract as K
from claview_a import graph as G


def decision(sid="s1", quote="지수", text="지수", quotes=None):
    return {"statistic": sid, "quote": quote, "reasons": [],
            "focus": {"status": "grounded", "text": text, "quotes": quotes or [quote]},
            "final_tables": [{"table_id": sid + "-table"}], "model_evidence": []}


def splitter(value, *, sid="s1", quote=None, polarity="required"):
    return {"for": [sid], "axis": "other", "quote": quote or value, "value": value, "polarity": polarity}


@pytest.mark.parametrize("text", ["2024년 지수", "2024년 기준 산업분류", "원재료 공급", "원계열 금리"])
def test_calendar_years_and_word_fragments_are_not_unit_or_index_anchors(text):
    assert CC.anchors(text) == []


@pytest.mark.parametrize("left,right", [("백만달러", "백만 달러"), ("백만 달러", "백만 USD"),
                                        ("2017=100", "2017 = 100.0")])
def test_equivalent_spacing_currency_notation_and_numeric_index_values(left, right):
    d = decision(text="지수 " + right, quotes=["지수", left])
    rows = CC.assess("지수 " + left, {}, [d])
    assert len(rows) == 1 and rows[0]["status"] == "accounted"
    assert rows[0]["semantic_fulfillment"] == "not_checked"


@pytest.mark.parametrize("value", ["달러", "천달러", "백만원"])
def test_scale_and_currency_both_must_match(value):
    d = decision(text="지수 " + value, quotes=["지수", "백만달러"])
    assert CC.assess("지수 백만달러", {}, [d])[0]["status"] == "missing"


def test_currency_without_a_requested_scale_does_not_require_an_unscaled_catalog_unit():
    d = decision()
    d["model_evidence"] = [{"kind": "item", "text": "금액(백만달러)", "table": "s1-table",
                             "fact_of_table": ["item"]}]
    assert CC.assess("지수 달러", {}, [d])[0]["status"] == "accounted"


def test_index_numeric_prefix_does_not_cover_a_different_base_value():
    d = decision(text="지수 2017=1000", quotes=["지수", "2017=100"])
    assert CC.assess("지수 2017=100", {}, [d])[0]["status"] == "missing"
    assert CC.anchors("2017=100,000") == []
    assert len(CC.anchors("2017=100, 월별")) == 1
    assert len(CC.anchors("2017=100. 월별")) == 1
    assert CC.anchors("2017=100.5")[0]["key"][1] == "100.5"


def test_raw_statistic_quote_or_unused_focus_evidence_does_not_hide_omission():
    q = "지수 2017=100"
    d = decision(quote=q, quotes=[q])
    assert CC.apply(q, {}, [d])[0]["status"] == "missing"
    assert d["reasons"][0]["code"] == CC.MISSING


@pytest.mark.parametrize("polarity", ["required", "any_of", "excluded", "allowed", "not_basis"])
def test_model_polarity_is_recorded_not_converted_to_required(polarity):
    out = {"splitters": [splitter("달러", polarity=polarity)]}
    original = copy.deepcopy(out)
    result = CC.assess("달러 환율 지수", out, [decision()])
    assert result[0]["status"] == "accounted"
    assert result[0]["evidence"][0]["polarity"] == polarity
    assert out == original


@pytest.mark.parametrize("polarity", ["unreadable", "undetermined"])
def test_unreadable_scope_does_not_become_a_new_user_question(polarity):
    d = decision()
    row = CC.apply("지수 2017=100", {"splitters": [splitter("2017=100", polarity=polarity)]}, [d])[0]
    assert row["status"] == "unreadable" and d["reasons"][0]["code"] == CC.UNREADABLE
    d["outcome"] = "failed"
    assert G.rejudge_targets({"decisions": [d]})["targets"][0]["kind"] == "correction"


def test_selected_valid_evidence_covers_a_literal_without_a_focus_repeat():
    d = decision()
    d["model_evidence"] = [{"kind": "item", "text": "지수(2017=100)", "table": "s1-table",
                             "fact_of_table": ["item"]}]
    row = CC.assess("지수 2017=100", {}, [d])[0]
    assert row["status"] == "accounted" and row["evidence"][0]["source"] == "selected_card_evidence"


@pytest.mark.parametrize("wrong", ["table", "kind"])
def test_unselected_or_invalid_evidence_never_covers_a_literal(wrong):
    d = decision()
    ev = {"kind": "item", "text": "지수(2017=100)", "table": "s1-table", "fact_of_table": ["item"]}
    ev["table" if wrong == "table" else "fact_of_table"] = "other-table" if wrong == "table" else ["title"]
    d["model_evidence"] = [ev]
    assert CC.assess("지수 2017=100", {}, [d])[0]["status"] == "missing"


def test_another_statistic_cannot_claim_a_locally_quoted_condition():
    q = "물가 지수 2017=100, 수출 지수 2017=100"
    one = decision(quote="물가 지수 2017=100")
    two = decision("s2", quote="수출 지수 2017=100", text="수출 지수 2017=100")
    rows = CC.assess(q, {}, [one, two])
    assert [(r["status"], r["review_statistics"]) for r in rows] == [("missing", ["s1"]), ("accounted", ["s2"])]


def test_shared_omission_requests_review_not_an_automatic_condition_for_every_statistic():
    ds = [decision(), decision("s2", quote="수출")]
    out = {"splitters": []}
    row = CC.apply("지수와 수출을 2017=100으로", out, ds)[0]
    assert row["review_statistics"] == ["s1", "s2"] and row["scope"] == "unassigned"
    assert out == {"splitters": []}


def test_fixed_statistic_context_is_kept_when_only_another_statistic_is_repaired():
    q = "지수와 수출을 2017=100으로"
    fixed = decision("s2", quote="수출", text="수출 2017=100", quotes=["수출", "2017=100"])
    rows = CC.assess(q, {}, [decision()], {"output": {}, "decisions": [fixed]})
    assert rows[0]["status"] == "accounted" and rows[0]["evidence"][0]["statistic"] == "s2"


def test_omission_does_not_turn_a_real_pool_miss_into_correction_without_search():
    d = decision()
    d.update(model_kind="missing", search_words=["지수"])
    CC.apply("지수 2017=100", {}, [d])
    assert not d["reasons"]


def with_catalog(d, *, units=(), item_units=(), links=(), inherited=()):
    items = [SimpleNamespace(item_id=f"i{i}", obj_id="o1", name_raw=f"지표{i}", unit=unit, parent_id=None)
             for i, unit in enumerate(item_units)]
    view = SimpleNamespace(has_meta=True, table_units=list(units), itm_rows=list(inherited), items=items)
    d["model_evidence"] = [{"kind": "item", "text": f"지표{i}", "table": d["statistic"] + "-table",
                            "fact_of_table": ["item"], "item": {"item_ids": ids}}
                           for i, ids in enumerate(links)]
    d["condition_catalog_facts"] = CC.catalog_facts(d, {d["statistic"] + "-table": view})
    return d


@pytest.mark.parametrize("unit", ["백만달러", "2017=100"])
def test_uniform_table_metadata_avoids_unnecessary_focus_repeat_or_recovery(unit):
    d = with_catalog(decision(), units=[unit], item_units=[None, None])
    row = CC.apply("지수 " + unit, {}, [d])[0]
    assert row["status"] == "accounted" and row["support_basis"] == "catalog_guaranteed"
    assert row["evidence"] == [] and row["catalog_evidence"][0]["scope"] == "table"
    assert not d["reasons"] and row["semantic_fulfillment"] == "not_checked"


@pytest.mark.parametrize("units,overrides", [(["달러"], []), (["백만달러", "백만원"], []),
                                           (["백만달러"], ["천달러"]), ([], [None, None])])
def test_absent_or_conflicting_metadata_does_not_claim_global_support(units, overrides):
    d = with_catalog(decision(), units=units, item_units=overrides)
    assert CC.assess("지수 백만달러", {}, [d])[0]["status"] == "missing"


def test_mixed_table_uses_only_all_uniquely_linked_cited_items():
    d = with_catalog(decision(), item_units=["2017=100", "2017=100", "2019=100"],
                     links=[["i0"], ["i1"]])
    row = CC.assess("지수 2017=100", {}, [d])[0]
    assert row["status"] == "accounted" and row["catalog_evidence"][0]["scope"] == "cited_items"
    assert [x["item_id"] for x in row["catalog_evidence"][0]["items"]] == ["i0", "i1"]


@pytest.mark.parametrize("links,units", [([["i0"], ["absent"]], ["2017=100", "2017=100"]),
                                        ([["i0"], ["i0", "i1"]], ["2017=100", "2017=100"]),
                                        ([["i0"], ["i1"]], ["2017=100", None]),
                                        ([["i0"], ["i1"]], ["2017=100", "2019=100"])])
def test_partial_ambiguous_unitless_or_conflicting_item_link_requires_recovery(links, units):
    d = with_catalog(decision(), item_units=units, links=links)
    assert CC.assess("지수 2017=100", {}, [d])[0]["status"] == "missing"


def test_table_title_and_a_parent_name_do_not_supply_missing_units():
    d = with_catalog(decision(), item_units=[None], links=[["i0"]])
    d["condition_catalog_facts"][0]["title"] = "2017=100 지수"
    assert CC.assess("지수 2017=100", {}, [d])[0]["status"] == "missing"


@pytest.mark.parametrize("hierarchy", [True, False])
def test_a_parent_or_unconfirmed_hierarchy_cannot_prove_the_child_unit(hierarchy):
    parent = SimpleNamespace(item_id="p", obj_id="o1", name_raw="지수", unit="2017=100", parent_id=None)
    child = SimpleNamespace(item_id="c", obj_id="o1", name_raw="자식지수", unit="2019=100", parent_id="p")
    if not hierarchy:
        del parent.parent_id
        del child.parent_id
    view = SimpleNamespace(has_meta=True, table_units=[], itm_rows=[], items=[parent, child])
    d = decision(quote="지수 아래 자식지수")
    d["model_evidence"] = [{"kind": "item", "text": "지수", "table": "s1-table", "fact_of_table": ["item"],
                            "item": {"item_ids": ["p"]}}]
    d["condition_catalog_facts"] = CC.catalog_facts(d, {"s1-table": view})
    row = CC.assess("지수 아래 자식지수를 2017=100으로", {}, [d])[0]
    assert row["status"] == "missing" and row["catalog_evidence"] == []
    expected = "parent_item_does_not_prove_children" if hierarchy else "item_hierarchy_unconfirmed"
    assert expected in d["condition_catalog_facts"][0]["item_link_problems"]


def test_one_table_guarantee_does_not_hide_another_statistics_missing_unit():
    one = with_catalog(decision(), units=["백만달러"])
    two = with_catalog(decision("s2", quote="수출"), units=["백만원"])
    row = CC.assess("지수와 수출을 백만달러로", {}, [one, two])[0]
    assert row["status"] == "missing" and row["catalog_unconfirmed_statistics"] == ["s2"]


@pytest.mark.parametrize("polarity", ["unreadable", "undetermined", "excluded", "not_basis"])
def test_metadata_never_overrides_unreadable_or_negative_model_scope(polarity):
    d = with_catalog(decision(), units=["백만달러"])
    row = CC.assess("지수 백만달러", {"splitters": [splitter("백만달러", polarity=polarity)]}, [d])[0]
    assert row["catalog_evidence"] == []
    if polarity in ("unreadable", "undetermined"):
        assert row["status"] == "unreadable"
    else:
        assert row["evidence"][0]["polarity"] == polarity


def test_metadata_never_erases_a_preexisting_polarity_conflict():
    d = with_catalog(decision(), units=["백만달러"])
    d["reasons"] = [{"code": "basis_polarity_invalid", "detail": {"cause": "polarity_conflict"}}]
    row = CC.assess("지수 백만달러", {}, [d])[0]
    assert row["catalog_evidence"] == [] and d["reasons"][0]["code"] == "basis_polarity_invalid"


def test_merged_recovery_rechecks_the_adopted_catalog_snapshot():
    first = record(reasons=[{"code": CC.MISSING}])
    first["question"] += " 2017=100"
    first["decisions"][0]["quote"] = first["model_output"]["statistics"][0]["quote"]
    first["condition_coverage"] = {"anchors": CC.assess(first["question"], first["model_output"], first["decisions"])}
    second = copy.deepcopy(first)
    d = second["decisions"][0]
    d["final_tables"] = [{"table_id": "s1-table"}]
    with_catalog(d, units=["2017=100"])
    result = adopted(first, second)
    row = result["condition_coverage"]["anchors"][0]
    assert row["status"] == "accounted" and row["support_basis"] == "catalog_guaranteed"
    assert first["condition_coverage"]["anchors"][0]["status"] == "missing"


def test_new_missing_anchor_does_not_license_changing_valid_scope_calculation_or_basis():
    first = record(reasons=[{"code": CC.MISSING}])
    for key in ("focuses", "calculations"):
        second = copy.deepcopy(first)
        second["model_output"][key] = []
        assert adopted(first, second)["rejudge"]["rejected"]
    second = copy.deepcopy(first)
    basis_change(second)
    assert adopted(first, second)["rejudge"]["rejected"]


def test_independent_other_values_are_not_one_basis_choice_set():
    class Cards:
        card_of = {}

    d = {"asks": [], "model_kind": "choice"}
    rows = [splitter("백만달러"), splitter("2017=100", polarity="any_of")]
    assert CH.polarity_problems("s1", rows, d, Cards()) == []
    rows.append(splitter("백만달러", polarity="excluded"))
    assert CH.polarity_problems("s1", rows, d, Cards())[0]["cause"] == "polarity_conflict"


QUESTION = "2024년 월별 예금은행 가계대출 금리를 신규취급액 기준으로 보여줘. 2017=100은 기준으로 쓰지 마."


def current_output(layer):
    return output(QUESTION, layer, [P("2024년", start="2024", end="same")], [F("월별", "M")],
                  contract=K.CONTRACT_VERSION)


def test_existing_contract_output_is_checked_without_upgrading_saved_fields(layer):
    out = current_output(layer)
    before = copy.deepcopy(out)
    r = checked(QUESTION, layer, out, K.CONTRACT_VERSION)
    assert r["decisions"][0]["outcome"] == "failed"
    assert CC.MISSING in {x["code"] for x in r["decisions"][0]["reasons"]}
    assert out == before and r["parse"]["answer_contract"].endswith(".10")


@pytest.mark.parametrize("repair,budget,calls", [(True, True, 2), (False, True, 2), (True, False, 1)])
def test_graph_uses_only_one_recovery_and_keeps_normal_scope(layer, repair, budget, calls):
    first = current_output(layer)
    second = copy.deepcopy(first)
    if repair:
        second["splitters"].append(splitter("2017=100", quote="2017=100은 기준으로 쓰지 마", polarity="excluded"))
    judge = ScopeJudge(first, second)
    judge.answer_contract = K.CONTRACT_VERSION
    state = G.run(QUESTION, layer, judge, K.Settings("fake"), AS_OF, research=True,
                  research_budget=lambda: budget)
    assert len(judge.payloads) == calls
    assert state["record"]["model_output"]["focuses"] == first["focuses"]
    assert state["record"]["route"]["action"] == ("handoff" if repair and budget else "fail")
    assert state["record"]["condition_coverage"]["anchors"][0]["status"] == \
        ("accounted" if repair and budget else "missing")
    if calls == 2:
        facts = judge.payloads[1]["targets"][0]["facts"]
        assert any(f.get("code") == CC.MISSING for f in facts)
