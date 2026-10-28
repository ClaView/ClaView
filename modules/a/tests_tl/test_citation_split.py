"""Card evidence errors kept apart from the rest of a statistic's checks (users 2026-10-28 05:37 and 05:42 UTC):
the six right/wrong pairs the user listed. Made-up model outputs, no model, no network; expectations by hand."""

from __future__ import annotations

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
M2 = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
M2_LONG = "2000년 1월부터 2010년 12월까지 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
TWO = "2024년 1~3월 예금은행 가계대출 금리와 예금은행 수신금리를 신규취급액 기준으로 월별로 보여줘"
NEW, OLD, M1_NEW = "DT_161Y006", "DT_101Y004", "DT_161Y002"  # M2 평잔·원계열 new / old edition, M1 평잔·원계열
RATE_NEW, RECEIPT_NEW = "DT_121Y006", "DT_121Y002"


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
    state = run(question, layer, lambda p, r: json.dumps(fill_option_evidence(build(cards), cards), ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"].get("decisions", [])}


def m2_out(table, evidence, period="2024년 1~3월"):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"}]
        o["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                          {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
        o["periods"] = [{"quote": period, "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, table) if table.startswith("DT_") else table,
                         "evidence": evidence}]
        return o
    return build


def std(groups, period, frequency="M"):
    return {"case_id": "x", "part": "new24", "question": "", "statistics": {"count": None, "status": "unconfirmed"},
            "groups": groups, "representative": {"confirmed_groups": groups, "confirmed_equivalents": {},
                                                 "proposed_equivalents": {}, "status": "proposed"},
            "ask": {"expected": "not_expected"}, "new_policy": {"status": "same_as_source", "differs": {}},
            "final": {"status": "confirmed", "period": period, "periods": None, "frequency": frequency,
                      "basis": [{"axis": "stock", "value": "평잔"}, {"axis": "seasonal", "value": "원계열"}],
                      "groups": groups}}


M2_STD = std([[NEW]], {"from": "2024-01", "to": "2024-03"})


def test_right_table_wrong_citation_goes_on_and_keeps_the_error(layer, titles):
    state, d = go(layer, M2, m2_out(NEW, [{"kind": "item", "text": "서비스수지, M2, 평잔"}]))
    s1 = d["s1"]
    assert s1["outcome"] == "selected_unverified" and s1["reasons"] == []
    assert [p["code"] for p in s1["citation"]["problems"]] == ["evidence_not_on_card"]
    assert [r["table_id"] for r in s1["final_tables"]] == [NEW] and s1["checks_not_done"] == []
    assert state["record"]["route"]["action"] == "partial"  # never the verified handoff
    assert state["record"]["model_output"]["choices"][0]["evidence"][0]["text"] == "서비스수지, M2, 평잔"  # untouched
    res = S.score_case(S.adapt(state), M2_STD, titles)
    assert res["model_meaning"]["right"] is True and res["evidence_contract"]["ok"] is False
    assert res["final_full"]["right"] is True and res["completed"]["right"] is False
    assert res["code_checks"]["right"] is True and "selected_evidence_unverified" in res["labels"]


def test_right_table_right_citation_completes(layer, titles):
    state, d = go(layer, M2, m2_out(NEW, [{"kind": "item", "text": "M2(평잔 원계열)"}]))
    assert d["s1"]["outcome"] == "selected" and state["record"]["route"]["action"] == "handoff"
    res = S.score_case(S.adapt(state), M2_STD, titles)
    assert (res["final_full"]["right"], res["completed"]["right"], res["evidence_contract"]["ok"]) == (True, True, True)


def test_wrong_table_with_a_true_citation_passes_the_facts_but_not_the_meaning(layer, titles):
    state, d = go(layer, M2, m2_out(M1_NEW, [{"kind": "title", "text": "M1 상품별 구성내역"}]))
    assert d["s1"]["citation"]["status"] == "verified" and d["s1"]["reasons"] == []
    res = S.score_case(S.adapt(state), M2_STD, titles)
    assert res["contract"]["ok"] is True and res["evidence_contract"]["ok"] is True
    assert res["model_meaning"]["right"] is False and res["final_full"]["right"] is False


def test_a_reference_outside_the_pool_allows_no_final_selection(layer, titles):
    state, d = go(layer, M2, m2_out("g1.t99", [{"kind": "title", "text": "없는 표"}]))
    assert d["s1"]["outcome"] == "failed" and [r["code"] for r in d["s1"]["reasons"]] == ["table_not_in_pool"]
    assert d["s1"]["final_tables"] == [] and state["record"]["route"]["action"] == "fail"


def test_an_edition_switch_whose_kept_values_cannot_be_confirmed_is_not_made(layer):
    _, ok = go(layer, M2_LONG, m2_out(NEW, [{"kind": "item", "text": "M2(평잔 원계열)"}], "2000년 1월부터 2010년 12월까지"))
    assert {r["table_id"] for r in ok["s1"]["final_tables"]} == {OLD, NEW}  # a valid cited value is kept: stitched
    _, bad = go(layer, M2_LONG, m2_out(NEW, [{"kind": "item", "text": "M2 상품별 구성내역의 M2 총액"}],
                                       "2000년 1월부터 2010년 12월까지"))
    s1 = bad["s1"]
    assert s1["outcome"] == "failed" and s1["final_tables"] == []  # never stitched without the kept-value check
    assert s1["period"]["not_usable"].get(OLD) == "retention_unverified"
    assert [p["code"] for p in s1["citation"]["problems"]] == ["evidence_not_on_card"]


def test_one_statistics_citation_error_keeps_the_others_result(layer, titles):
    def build(cards):
        o = empty()
        o["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "a"},
                           {"id": "s2", "quote": "예금은행 수신금리", "name": "b"}]
        o["splitters"] = [{"quote": "신규취급액 기준", "axis": "loan_basis", "for": ["s1", "s2"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, RATE_NEW),
                         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]},
                        {"for": ["s2"], "table": ref_of(cards, RECEIPT_NEW),
                         "evidence": [{"kind": "item", "text": "없는 항목"}]}]
        return o
    state, d = go(layer, TWO, build)
    assert d["s1"]["outcome"] == "selected" and d["s1"]["citation"]["status"] == "verified"
    assert d["s2"]["outcome"] == "selected_unverified" and [r["table_id"] for r in d["s2"]["final_tables"]] == [
        RECEIPT_NEW]
    assert state["record"]["route"]["action"] == "partial"
