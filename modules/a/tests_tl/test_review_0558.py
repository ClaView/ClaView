"""The four majors of the 2026-10-28 05:58 UTC review, each with its right-side pair. Made-up model outputs and, for
the scorer, records changed by hand to stand for a code check that missed or over-reported an error. No model, no
network; expectations by hand."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from conftest import fill_option_evidence  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from claview_a.cards import build_cards, table_numbers  # noqa: E402
from claview_a.contract import Settings  # noqa: E402
from claview_a.graph import run  # noqa: E402
from claview_a.pool import build_pool  # noqa: E402
from claview_a.search.tables import selection_catalog  # noqa: E402
from tl_eval import score as S  # noqa: E402

AS_OF = "2026-10-22"
M2_OPEN = "2024년 1~3월 M2 총액을 월별로 보여줘"
M2 = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
M1_2004 = "2004년 1~3월 M1 총액을 평잔·원계열 기준으로 월별로 보여줘"
M2_NEW, M1_OLD, M1_NEW = "DT_161Y006", "DT_101Y019", "DT_161Y002"  # catalog facts


@pytest.fixture(scope="module")
def titles(layer):
    return {t: v.title for t, v in selection_catalog(layer).views.items()}


def empty():
    return {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                            "missing", "alternatives")}


def ref_of(cards, table):
    for c in cards:
        num = table_numbers(c)
        if table in num:
            return f"{c['card_id']}.{num[table]}"
    raise KeyError(table)


def go(layer, question, table, evidence, quote, splitters=()):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": quote, "name": quote}]
        o["splitters"] = [{"quote": q, "axis": a, "for": ["s1"]} for q, a in splitters]
        o["periods"] = [{"quote": question.split(" ")[0] + " " + question.split(" ")[1], "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, table), "evidence": evidence}]
        return o
    cards = build_cards(question, build_pool(question, layer), layer)
    state = run(question, layer, lambda p, r: json.dumps(fill_option_evidence(build(cards), cards), ensure_ascii=False), Settings("fake"), AS_OF)
    return state, state["record"]["decisions"][0]


def std(groups, period, basis):
    return {"case_id": "x", "part": "new24", "question": "", "statistics": {"count": None, "status": "unconfirmed"},
            "groups": groups, "representative": {"confirmed_groups": groups, "confirmed_equivalents": {},
                                                 "proposed_equivalents": {}, "status": "proposed"},
            "ask": {"expected": "not_expected"}, "new_policy": {"status": "same_as_source", "differs": {}},
            "final": {"status": "confirmed", "period": period, "periods": None, "frequency": "M", "basis": basis,
                      "groups": groups}}


M2_STD = std([[M2_NEW]], {"from": "2024-01", "to": "2024-03"},
             [{"axis": "stock", "value": "평잔"}, {"axis": "seasonal", "value": "원계열"}])


# ---------------------------------------------------------------- 1. not confirmed is no mismatch: the axis is held


def test_an_open_question_with_a_valid_title_citation_keeps_the_models_choice(layer):
    """07:19 UTC: no ask made up and no hold; the open axes take the chosen table's values as the model's choice."""
    _, d = go(layer, M2_OPEN, M2_NEW, [{"kind": "title", "text": "M2 상품별 구성내역"}], "M2 총액")
    assert d["outcome"] == "selected" and not d["asks"] and d["basis"]["stock"]["source"] == "model_choice"


def test_an_invalid_citation_never_makes_the_open_basis_the_users(layer):
    """The citation error stays (unverified); the open axes are the model's choice, never stated by the user."""
    _, d = go(layer, M2_OPEN, M2_NEW, [{"kind": "item", "text": "M2, 평잔, 원계열"}], "M2 총액")
    assert d["outcome"] == "selected_unverified" and d["asks"] == []
    assert {d["basis"][a]["source"] for a in ("seasonal", "stock")} == {"model_choice"}
    assert [p["code"] for p in d["citation"]["problems"]] == ["evidence_not_on_card"]


# ---------------------------------------------------------------- 2. one retention verdict, the original table kept


def test_an_unconfirmed_switch_is_kept_out_and_the_original_table_stays(layer):
    _, d = go(layer, M1_2004, M1_OLD, [{"kind": "item", "text": "M1, 평잔, 원계열"}], "M1 총액",
              [("평잔", "stock"), ("원계열", "seasonal")])
    assert d["outcome"] == "selected_unverified" and d["reasons"] == []
    assert [(r["table_id"], r["period"]) for r in d["final_tables"]] == [(M1_OLD, [{"start": "2004-01",
                                                                                     "end": "2004-03"}])]
    assert d["period"]["not_usable"].get(M1_NEW) == "retention_unverified"
    assert M1_NEW not in d["period"]["usable"]


def test_without_cited_values_the_existing_names_check_still_decides(layer):
    """The right-side pair: no item cited (nothing invalid), so the existing same-names rule is unchanged."""
    _, d = go(layer, M1_2004, M1_OLD, [{"kind": "title", "text": "M1 상품별 구성내역"}], "M1 총액",
              [("평잔", "stock"), ("원계열", "seasonal")])
    assert d["outcome"] == "selected" and d["citation"]["status"] == "verified"
    assert d["period"]["usable"].get(M1_NEW) == "same_value_names"


# ---------------------------------------------------------------- 3. completed checked against the scorer


def test_completed_is_false_when_the_scorer_finds_an_evidence_error_code_let_through(layer, titles):
    state, _ = go(layer, M2, M2_NEW, [{"kind": "item", "text": "없는 항목"}], "M2 총액",
                  [("평잔", "stock"), ("원계열", "seasonal")])
    rec = S.adapt(state)
    rec["final"]["s1"]["outcome"], rec["route"], rec["checks"]["s1"]["citation"] = "selected", "handoff", []  # a miss
    res = S.score_case(rec, M2_STD, titles)
    assert res["evidence_contract"]["ok"] is False and res["contract"]["checks"] == {"s1": "code_check_missed"}
    assert res["final_full"]["right"] is True and res["completed"]["right"] is False
    assert "scorer_evidence_errors" in res["completed"]["blocked_by"]


def test_completed_is_true_only_when_everything_agrees(layer, titles):
    state, _ = go(layer, M2, M2_NEW, [{"kind": "item", "text": "M2(평잔 원계열)"}], "M2 총액",
                  [("평잔", "stock"), ("원계열", "seasonal")])
    res = S.score_case(S.adapt(state), M2_STD, titles)
    assert (res["final_full"]["right"], res["completed"]["right"], res["code_checks"]["right"]) == (True, True, True)


# ---------------------------------------------------------------- 4. code handling matched error by error


def _two_errors(layer):
    """Evidence on no card and a statistic quote not in the question: code records both (the quote stops it)."""
    state, d = go(layer, M2, M2_NEW, [{"kind": "item", "text": "없는 항목"}, {"kind": "item", "text": "또 없는 항목"}],
                  "M2 총량", [("평잔", "stock"), ("원계열", "seasonal")])
    return S.adapt(state)


def test_catching_every_error_is_right(layer, titles):
    res = S.score_case(_two_errors(layer), M2_STD, titles)
    assert res["contract"]["checks"] == {"s1": "code_check_success"} and res["code_checks"]["right"] is True
    assert res["contract"]["check_items"]["missed"] == []


def test_catching_only_the_evidence_error_is_not_right(layer, titles):
    rec = _two_errors(layer)
    rec["checks"]["s1"]["reasons"] = [r for r in rec["checks"]["s1"]["reasons"] if r["code"] != "quote_not_in_question"]
    res = S.score_case(rec, M2_STD, titles)
    assert res["contract"]["checks"] == {"s1": "code_check_missed"} and res["code_checks"]["right"] is False
    assert res["contract"]["check_items"]["missed"] == [["s1", "quote_not_in_question", "M2 총량"]]


def test_one_of_two_errors_of_the_same_kind_missed_is_found(layer, titles):
    rec = _two_errors(layer)
    rec["checks"]["s1"]["citation"] = [p for p in rec["checks"]["s1"]["citation"] if p["detail"]["text"] != "또 없는 항목"]
    res = S.score_case(rec, M2_STD, titles)
    assert res["contract"]["check_items"]["missed"] == [["s1", "evidence", "또 없는 항목"]]
    assert res["code_checks"]["right"] is False


def test_an_error_code_reports_that_the_scorer_finds_absent(layer, titles):
    state, _ = go(layer, M2, M2_NEW, [{"kind": "item", "text": "M2(평잔 원계열)"}], "M2 총액",
                  [("평잔", "stock"), ("원계열", "seasonal")])
    rec = S.adapt(state)
    report = copy.deepcopy(rec)
    report["checks"]["s1"]["citation"] = [{"code": "evidence_not_on_card", "detail": {"text": "M2(평잔 원계열)"}}]
    res = S.score_case(report, M2_STD, titles)
    assert res["contract"]["checks"] == {"s1": "code_false_report"} and res["code_checks"]["right"] is False
    reject = copy.deepcopy(rec)
    reject["checks"]["s1"]["reasons"] = [{"code": "quote_not_in_question", "detail": "M2 총액"}]
    res = S.score_case(reject, M2_STD, titles)
    assert res["contract"]["checks"] == {"s1": "code_false_reject"} and res["code_checks"]["right"] is False


# ---------------------------------------------------------------- 06:08 UTC: ask facts kept per statistic and axis


def _two_axis_ask(layer, seasonal_bad: bool, stock_bad: bool):
    """M2 with both axes open, the model asks on each; a bad option value is written on the chosen axes only."""
    def build(cards):
        card = next(c for c in cards if any(r["table_id"] == M2_NEW for r in c["tables"]))
        num = table_numbers(card)
        r = {t: f"{card['card_id']}.{num[t]}" for t in ("DT_161Y005", "DT_161Y006", "DT_161Y008")}
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["asks"] = [
            {"for": "s1", "axis": "seasonal", "options": [{"value": "계절조정", "table": r["DT_161Y005"]},
                                                          {"value": "없는값" if seasonal_bad else "원계열",
                                                           "table": r["DT_161Y006"]}]},
            {"for": "s1", "axis": "stock", "options": [{"value": "없는값" if stock_bad else "평잔",
                                                       "table": r["DT_161Y006"]},
                                                      {"value": "말잔", "table": r["DT_161Y008"]}]}]
        return o
    cards = build_cards(M2_OPEN, build_pool(M2_OPEN, layer), layer)
    out = build(cards)
    state = run(M2_OPEN, layer, lambda p, r: json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), Settings("fake"), AS_OF)
    return S.adapt(state)


@pytest.mark.parametrize("seasonal_bad,stock_bad", [(False, False), (True, False), (False, True), (True, True)])
def test_ask_errors_stay_on_their_own_axis(layer, titles, seasonal_bad, stock_bad):
    rec = _two_axis_ask(layer, seasonal_bad, stock_bad)
    code = sorted(r["detail"]["axis"] for r in rec["checks"]["s1"]["reasons"] if r["code"] == "ask_invalid")
    bad = sorted(a for a, b in (("seasonal", seasonal_bad), ("stock", stock_bad)) if b)
    assert code == bad  # code catches exactly the bad axes
    res = S.score_case(rec, M2_STD, titles)
    items = res["contract"]["check_items"]
    assert items["caught"] == [["s1", "ask_invalid", a] for a in bad]
    assert items["missed"] == [] and items["false_reject"] == [] and items["false_report"] == []
    assert res["code_checks"]["right"] is True
    assert res["contract"]["checks"] == ({"s1": "code_check_success"} if bad else {})


# ---------------------------------------------------------------- 06:27 UTC: the standard's loan_basis value


@pytest.mark.parametrize("axis,value,table,ok", [
    ("loan_basis", "잔액", "DT_121Y015", True),  # the balance table
    ("loan_basis", "잔액", "DT_121Y006", False),  # the new-loan table
    ("loan_basis", "신규취급액", "DT_121Y006", True),
    ("stock", "잔액", "DT_161Y008", False)])  # another axis: no form, no match
def test_the_standard_basis_value_is_read_with_the_shared_forms(titles, axis, value, table, ok):
    have = S._basis_values(S._brackets(titles[table]), axis)
    assert bool(have & S._standard_value(axis, value)) is ok
