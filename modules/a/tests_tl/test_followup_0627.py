"""Follow-ups of the 40-question run (user 2026-10-28 06:27 UTC): display requests are not statistics (prompt only,
no word rule in code), and a cited value name that carries an axis value never fixes that axis for the user.
Made-up model outputs, no model, no network; expectations by hand."""

from __future__ import annotations

import json

import pytest

from conftest import fill_option_evidence  # noqa: E402

from claview_a import check as CH
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings, prompt_text
from claview_a.graph import run
from claview_a.pool import build_pool

AS_OF = "2026-10-22"
SA_AVG, ORIG_AVG, ORIG_END, SA_END = "DT_161Y005", "DT_161Y006", "DT_161Y008", "DT_161Y007"  # M2 by product
M1_ORIG_AVG = "DT_161Y002"


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
    out = build(cards)
    state = run(question, layer, lambda p, r: json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"].get("decisions", [])}


def stat(sid, quote):
    return {"id": sid, "quote": quote, "name": quote}


# ---------------------------------------------------------------- 2. display requests (prompt, not code)


def test_the_prompt_says_display_requests_are_no_statistics_and_keeps_named_indicators():
    text = prompt_text()
    assert "부가 표시·형식 요청은 별도 통계로 만들지 않는다" in text and "단위노동비용" in text


@pytest.mark.parametrize("quote", ["원자료 단위", "단위노동비용"])
def test_code_never_drops_a_statistic_by_a_word(layer, quote):
    """Whatever the model writes as a statistic stays one: no word rule removes it in code."""
    q = "2024년 1~3월 수출물량지수 총지수를 월별로 보여줘. 원자료 단위도 함께 표시해줘. 단위노동비용도"
    def build(cards):
        o = empty()
        o["statistics"] = [stat("s1", "수출물량지수 총지수"), stat("s2", quote)]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, "DT_403Y002"),
                         "evidence": [{"kind": "title", "text": "수출물량지수"}]}]
        o["missing"] = [{"for": ["s2"], "search_words": [quote]}]
        return o
    state, d = go(layer, q, build)
    assert sorted(d) == ["s1", "s2"] and d["s2"]["outcome"] == "not_in_pool"
    assert state["record"]["question"] == q  # the display request stays in the handed-on question


# ---------------------------------------------------------------- 3. a cited name never fixes an unstated axis


def test_an_unstated_axis_is_the_models_choice_never_the_users(layer):
    """SB:CQ1-02 shape: seasonal stated, stock not; the model chose the 평잔 table. 07:19 UTC: that choice is checked
    as it is (no hold, no ask by code); 평잔 is recorded as the model's choice. The missing ask is scored."""
    q = "2024년 1~3월 M2 상품별 구성내역의 M2 총액을 계절조정계열로 월별로 보여줘."
    def build(cards):
        o = empty()
        o["statistics"] = [stat("s1", "M2 상품별 구성내역의 M2 총액")]
        o["splitters"] = [{"quote": "계절조정계열", "axis": "seasonal", "for": ["s1"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, SA_AVG),
                         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"},
                                      {"kind": "item", "text": "M2(평잔계절조정계열)"}]}]
        return o
    _, d = go(layer, q, build)
    s1 = d["s1"]
    assert s1["outcome"] == "selected" and s1["asks"] == []
    assert s1["basis"]["stock"]["source"] == "model_choice" and s1["basis"]["seasonal"]["source"] == "splitter"


def test_a_stated_axis_switch_without_target_evidence_is_held(layer):
    q = "2024년 1~3월 M2 총액을 말잔·원계열 기준으로 월별로 보여줘"
    def build(cards):
        o = empty()
        o["statistics"] = [stat("s1", "M2 총액")]
        o["splitters"] = [{"quote": "말잔", "axis": "stock", "for": ["s1"]},
                          {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, ORIG_AVG),
                         "evidence": [{"kind": "item", "text": "M2(평잔 원계열)"}]}]
        return o
    _, d = go(layer, q, build)
    s1 = d["s1"]
    # 07:19 UTC: the stated 말잔 against the chosen 평잔 table: a conflict, not switched
    assert s1["outcome"] == "failed" and [r["code"] for r in s1["reasons"]] == ["stated_basis_conflict"]
    assert s1["corrections"] == [] and s1["final_tables"] == []


def test_a_value_stated_for_another_statistic_is_never_taken_from_this_ones_citation(layer):
    """평잔 belongs to s1 only; s2 cites 'M1(평잔 원계열)' and is asked on stock, never given 평잔."""
    q = "2024년 1~3월 평잔 M2 총액과 M1 총액을 원계열로 월별로 보여줘"
    def build(cards):
        o = empty()
        o["statistics"] = [stat("s1", "M2 총액"), stat("s2", "M1 총액")]
        o["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                          {"quote": "원계열", "axis": "seasonal", "for": ["s1", "s2"]}]
        o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
        o["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, ORIG_AVG),
                         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]},
                        {"for": ["s2"], "table": ref_of(cards, M1_ORIG_AVG),
                         "evidence": [{"kind": "item", "text": "M1(평잔 원계열)"}]}]
        return o
    _, d = go(layer, q, build)
    assert d["s1"]["outcome"] == "selected" and d["s1"]["basis"]["stock"]["value"] == ["평잔"]
    # s2's 평잔 is its own chosen table's value, recorded as the model's choice, never s1's stated basis
    assert d["s2"]["outcome"] == "selected" and d["s2"]["basis"]["stock"]["source"] == "model_choice"


def test_a_name_without_the_axis_value_is_still_required_across_the_axis():
    kept, bound = CH.axis_items(["현금통화", "M2(평잔 원계열)"], ["stock"])
    assert kept == ["현금통화"] and bound == ["M2(평잔 원계열)"]
    assert CH.axis_items(["M2(평잔 원계열)"], ["valuation"]) == (["M2(평잔 원계열)"], [])
