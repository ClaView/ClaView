"""The target-table principle limited to adding or switching tables (user 2026-10-28 07:19 UTC): the six right/wrong
pairs the user listed. The standard is read only by the scorer; the path names no case or table. Made-up outputs,
no model, no network; expectations by hand."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from conftest import fill_option_evidence  # noqa: E402
from claview_a.cards import build_cards, table_numbers  # noqa: E402
from claview_a.contract import Settings  # noqa: E402
from claview_a.graph import run  # noqa: E402
from claview_a.pool import build_pool  # noqa: E402
from claview_a.search.tables import selection_catalog  # noqa: E402
from tl_eval import score as S  # noqa: E402
from tl_eval import standard as ST  # noqa: E402

AS_OF = "2026-10-22"
STANDARD = {c["case_id"]: c for c in json.loads(ST.OUT_V2.read_text(encoding="utf-8"))["cases"]}


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


def go(layer, question, build):
    cards = build_cards(question, build_pool(question, layer), layer)
    out = fill_option_evidence(build(cards), cards)
    state = run(question, layer, lambda p, r: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}


def choice(sid_quote, table, evidence, period, frequency="월별", splitters=()):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": sid_quote, "name": sid_quote}]
        o["splitters"] = [{"quote": q, "axis": a, "for": ["s1"]} for q, a in splitters]
        o["periods"] = [{"quote": period, "for": ["s1"]}]
        o["frequencies"] = [{"quote": frequency, "for": ["s1"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, table), "evidence": evidence}]
        return o
    return build


# ---------------------------------------------------------------- 1. a right choice beside other-classification siblings


@pytest.mark.parametrize("cid,quote,table,evidence", [
    ("CV-AB-18", "산업용 전력 가격", "DT_404Y016", [{"kind": "item", "text": "산업용전력"}]),
    ("CV-AB-19", "수입 원재료와 중간재의 물가", "DT_401Y018",
     [{"kind": "item", "text": "원재료"}, {"kind": "item", "text": "중간재"}])])
def test_a_right_choice_is_not_held_for_lack_of_sibling_evidence(layer, titles, cid, quote, table, evidence):
    case = STANDARD[cid]
    period = "2024년에" if "2024년에" in case["question"] else "2024년"
    state, d = go(layer, case["question"], choice(quote, table, evidence, period))
    s1 = d["s1"]
    assert s1["outcome"] == "selected" and [r["table_id"] for r in s1["final_tables"]] == [table]
    assert s1["basis"].get("classification", {}).get("source") in (None, "model_choice")  # never the user's
    res = S.score_case(S.adapt(state), case, titles)
    assert res["model_meaning"]["right"] is True and res["final_full"]["right"] is True
    assert res["ask_measures"]["ask_unneeded"] == [] and res["ask_measures"]["held_statistics"] == []


# ---------------------------------------------------------------- 2. a needed ask left out: scored, not forced by code


def test_a_missing_ask_passes_the_fact_checks_and_is_a_meaning_error(layer, titles):
    case = STANDARD["SB:CQ1-02"]
    state, d = go(layer, case["question"], choice(
        "M2 상품별 구성내역의 M2 총액", "DT_161Y005", [{"kind": "title", "text": "M2 상품별 구성내역"}], "2024년 1~3월",
        splitters=[("계절조정계열", "seasonal")]))
    assert d["s1"]["outcome"] == "selected" and d["s1"]["asks"] == []  # no hold, no ask made by code
    res = S.score_case(S.adapt(state), case, titles)
    assert res["ask_measures"]["ask_missing"] == [["question", "stock"]]
    assert res["model_meaning"]["right"] is False and res["final_full"]["right"] is False
    assert res["completed"]["right"] is False  # a handoff is no proof of meaning


# ---------------------------------------------------------------- 3. the needed ask with option evidence


def test_the_needed_ask_with_evidence_per_option_is_an_ask_and_right(layer, titles):
    case = STANDARD["SB:CQ1-02"]
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "M2 상품별 구성내역의 M2 총액", "name": "M2"}]
        o["splitters"] = [{"quote": "계절조정계열", "axis": "seasonal", "for": ["s1"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["asks"] = [{"for": "s1", "axis": "stock", "options": [
            {"value": "평잔", "table": ref_of(cards, "DT_161Y005"),
             "evidence": [{"kind": "item", "text": "M2(평잔계절조정계열)"}]},
            {"value": "말잔", "table": ref_of(cards, "DT_161Y007"),
             "evidence": [{"kind": "item", "text": "M2(말잔 계절조정계열)"}]}]}]
        return o
    state, d = go(layer, case["question"], build)
    assert d["s1"]["outcome"] == "ask" and d["s1"]["citation"]["status"] == "verified"
    res = S.score_case(S.adapt(state), case, titles)
    assert res["final_ask"] == "right" and res["ask_measures"]["ask_missing"] == []
    assert res["ask_measures"]["option_meaning_errors"] == [] and res["ask_measures"]["option_evidence_errors"] == []


# ---------------------------------------------------------------- 4. an unneeded ask


def test_an_unneeded_ask_is_a_meaning_error_whatever_its_facts(layer, titles):
    case = STANDARD["CV-AB-18"]
    def build(cards):
        card = next(c for c in cards if any(r["table_id"] == "DT_404Y016" for r in c["tables"]))
        other = next(r["table_id"] for r in card["tables"] if r["table_id"] != "DT_404Y016"
                     and r["basis"].get("classification"))
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "산업용 전력 가격", "name": "전력"}]
        o["periods"] = [{"quote": "2024년에", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        values = {t: next(r["basis"]["classification"][0] for r in card["tables"] if r["table_id"] == t)
                  for t in ("DT_404Y016", other)}
        o["asks"] = [{"for": "s1", "axis": "classification",
                      "options": [{"value": v, "table": ref_of(cards, t)} for t, v in values.items()]}]
        return o
    state, d = go(layer, case["question"], build)
    assert d["s1"]["outcome"] == "ask"  # the facts of the ask hold
    res = S.score_case(S.adapt(state), case, titles)
    assert res["ask_measures"]["ask_unneeded"] == [["s1", "classification"]]
    assert res["model_meaning"]["right"] is False and res["final_full"]["right"] is False


# ---------------------------------------------------------------- 5. a stated basis against the chosen table


def test_a_choice_against_the_stated_basis_stays_an_error(layer):
    q = "2024년 1~3월 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
    state, d = go(layer, q, choice("예금은행 가계대출 금리", "DT_121Y015", [{"kind": "bracket", "text": "잔액 기준"}],
                                   "2024년 1~3월", splitters=[("신규취급액 기준", "loan_basis")]))
    assert d["s1"]["outcome"] == "failed" and d["s1"]["reasons"][0]["code"] == "stated_basis_conflict"
    assert d["s1"]["corrections"] == [] and state["record"]["route"]["action"] == "fail"  # never switched or passed


# ---------------------------------------------------------------- 6. edition choice within one basis stays code's


def test_an_old_edition_representative_still_gets_the_editions_by_period(layer):
    q = "2000년 1월부터 2010년 12월까지 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
    state, d = go(layer, q, choice("M2 총액", "DT_161Y006", [{"kind": "item", "text": "M2(평잔 원계열)"}],
                                   "2000년 1월부터 2010년 12월까지", splitters=[("평잔", "stock"), ("원계열", "seasonal")]))
    s1 = d["s1"]
    assert s1["outcome"] == "selected" and {r["table_id"] for r in s1["final_tables"]} == {"DT_101Y004", "DT_161Y006"}
    assert s1["basis"]["stock"]["source"] == "splitter"


def test_cv_ab_17_shape_two_statistics_on_one_classification_table_are_selected(layer):
    case = STANDARD["CV-AB-17"]
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "신선식품 생산자물가", "name": "a"},
                           {"id": "s2", "quote": "신선식품을 뺀 물가", "name": "b"}]
        o["periods"] = [{"quote": "2024년", "for": ["s1", "s2"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, "DT_404Y015"),
                         "evidence": [{"kind": "item", "text": "신선식품"}]},
                        {"for": ["s2"], "table": ref_of(cards, "DT_404Y015"),
                         "evidence": [{"kind": "item", "text": "신선식품이외"}]}]
        return o
    state, d = go(layer, case["question"], build)
    assert d["s1"]["outcome"] == d["s2"]["outcome"] == "selected" and state["record"]["route"]["action"] == "handoff"
