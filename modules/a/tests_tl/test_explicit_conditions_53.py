"""Plan 53, change A (user 2026-10-30 02:07 UTC): explicit periods and negated basis values are read as written.

Periods: "N0년대 이후/부터/이래" is the open request from N0, "N0년대" alone is N0 to N0+9, "…부터 최근까지" is
the same open end as "…부터 현재까지"; qualifiers with no agreed range (초반, 중반, 후반, 들어) and any other decade
are never dropped (``period_partially_read`` with ``qualifier_undefined``). Basis: a negated value is excluded,
never stated, for splitters and the question alike; a table of it conflicts and code never picks the other value.
Normal/error
pairs, the reading functions and the straight graph on the real catalog (fake model, no network). Questions and model
outputs are the forms under test, never rewritten to a supported form; tables are found by card name and title basis.
"""

from __future__ import annotations

import json

import pytest
from conftest import fill_option_evidence  # noqa: E402

from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.basis import polarity
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"


def read(quote: str) -> dict:
    got = PR.read_quotes([quote], [])
    rows = [(c["attrs"]["form"], (c["attrs"]["start_expr"] or {}).get("text"),
             (c["attrs"]["end_expr"] or {}).get("text")) for c in got["conditions"]]
    return {"conditions": rows, "codes": [p["code"] for p in got["problems"]], "problems": got["problems"],
            "reading": got["readings"][0]}


# ---------------------------------------------------------------- periods: decades


@pytest.mark.parametrize("quote, start", [("2000년대 이후", "2000년"), ("2000년대부터", "2000년"),
                                          ("1990년대 이래", "1990년")])
def test_a_decade_since_is_the_open_request_from_its_first_year(quote, start):
    got = read(quote)
    assert got["codes"] == [] and got["conditions"] == [("since", start, None)]
    (rewrite,) = got["reading"]["rewrites"]
    assert rewrite["from"] == quote and rewrite["rule"] == "decade_since"


@pytest.mark.parametrize("quote, start", [("2000년 이후", "2000년"), ("2000년부터", "2000년")])
def test_a_year_since_reads_as_before(quote, start):
    got = read(quote)
    assert got["codes"] == [] and got["conditions"] == [("since", start, None)] and got["reading"]["rewrites"] == []


def test_a_decade_alone_is_its_ten_years():
    got = read("2000년대")
    assert got["codes"] == [] and got["conditions"] == [("range", "2000년", "2009년")]
    assert got["reading"]["rewrites"] == [{"from": "2000년대", "to": "2000년부터 2009년까지", "rule": "decade"}]


@pytest.mark.parametrize("quote, undefined", [
    ("2000년대 초반", ["2000년대", "초반"]), ("2000년대 중반", ["2000년대", "중반"]),
    ("1990년대 후반", ["1990년대", "후반"]), ("2000년대 초반 이후", ["2000년대", "초반"]),
    ("2000년대까지", ["2000년대까지"]), ("2000년대말", ["2000년대말"]), ("2005년대 이후", ["2005년대"]),
    ("2025년 들어", ["들어"])])
def test_an_undefined_qualifier_is_never_dropped(quote, undefined):
    """Before plan 53 "2000년대 초반", "1990년대 후반" and "2025년 들어" were read as one year with no problem."""
    got = read(quote)
    assert got["codes"] == ["period_partially_read"] and got["conditions"] == []
    assert got["problems"][0]["qualifier_undefined"] == undefined
    assert set(undefined) <= set(got["problems"][0]["uncovered"])


def test_a_decade_start_with_an_undefined_decade_end_is_not_read():
    got = read("1990년대부터 2000년대까지")
    assert got["codes"] == ["period_partially_read"] and got["problems"][0]["qualifier_undefined"] == ["2000년대까지"]


def test_an_alias_into_stays_a_since():
    assert read("올해 들어")["conditions"] == [("since", "올해", None)]


# ---------------------------------------------------------------- periods: open ends and the other forms


@pytest.mark.parametrize("quote, word", [("2020년부터 최근까지", "최근까지"), ("2020년부터 현재까지", "현재까지"),
                                         ("2020년부터 지금까지", "지금까지")])
def test_an_open_end_after_a_since_is_read_with_it(quote, word):
    got = read(quote)
    assert got["codes"] == [] and got["conditions"] == [("since", "2020년", None)]
    assert got["reading"]["open_end_words"] == [word]


@pytest.mark.parametrize("quote", ["최근", "최근까지", "같은 기간"])
def test_a_period_with_no_number_stays_unresolved(quote):
    assert read(quote)["codes"] == ["period_unresolved"]


@pytest.mark.parametrize("quote, rows", [
    ("2000년부터 2010년까지", [("range", "2000년", "2010년")]), ("최근 5년", [("recent", None, None)]),
    ("2024년 1~3월", [("range", "2024년 1월", "2024년 3월")]), ("2024년 기준", [("point", "2024년", None)])])
def test_explicit_ranges_recent_years_and_a_plain_standard_read_as_before(quote, rows):
    got = read(quote)
    assert got["codes"] == [] and got["conditions"] == rows


def test_base_year_marks_are_still_removed_or_ambiguous():
    assert read("2015년 기준연도")["reading"]["base_year_removed"] == ["2015년 기준연도"]
    assert read("2010=100 기준 2020년 이후")["conditions"] == [("since", "2020년", None)]
    assert read("2015년 기준 2024년")["codes"] == ["period_base_year_ambiguous"]


@pytest.mark.parametrize("quote, frequency", [("월말", "M"), ("분기말", "Q"), ("반기말", "H"), ("연말", "Y")])
def test_end_point_frequencies_keep_their_end(quote, frequency):
    got = PR.read_quotes([], [quote])
    assert got["problems"] == [] and [(c["value"], c["attrs"]["anchor"]) for c in got["conditions"]] == [
        (frequency, "end")]


# ---------------------------------------------------------------- basis polarity (reading)


@pytest.mark.parametrize("text, axis, required, excluded", [
    ("계절조정은 하지 않은 값", "seasonal", [], ["계절조정"]),
    ("계절조정이 아니라 원계열", "seasonal", ["원계열"], ["계절조정"]),
    ("원계열이 아니라 계절조정", "seasonal", ["계절조정"], ["원계열"]),
    ("명목이 아니라 실질", "price", ["실질"], ["명목"]),
    ("신규취급액이 아니라 잔액 기준", "loan_basis", ["잔액기준"], ["신규취급액"]),
    ("원계열", "seasonal", ["원계열"], []),
    ("계절조정한 계열", "seasonal", ["계절조정"], []),
    ("계절조정계열", "seasonal", ["계절조정"], []),
])
def test_polarity_keeps_required_and_excluded_apart(text, axis, required, excluded):
    got = polarity(text, axis)
    assert sorted(got["required"]) == required and sorted(got["excluded"]) == excluded and got["unresolved"] == []


@pytest.mark.parametrize("text, why", [("원계열이 아니라 원계열", "required_and_excluded"),
                                       ("계절조정 값은 보고 싶지 않은", "negation_scope_unread")])
def test_a_negation_whose_scope_is_not_read_is_unresolved(text, why):
    got = polarity(text, "seasonal")
    assert got["required"] == set() and got["excluded"] == set() and [u["why"] for u in got["unresolved"]] == [why]


# ---------------------------------------------------------------- basis polarity through the graph


M2 = "M2 상품별 구성내역"
Q013 = "월말 잔액 기준의 광의통화를 상품별로 보고 싶어. 계절조정은 하지 않은 값으로."
NEGATED = "계절조정은 하지 않은 값"


def ref(cards, name, layer, **basis):
    card = next(c for c in cards if c["name"] == name)
    views = selection_catalog(layer).views
    rows = [r for r in card["tables"] if all((r["basis"].get(a) or None) == ([v] if v else None)
                                             for a, v in basis.items())]
    rows.sort(key=lambda r: PR._end_key(views[r["table_id"]]), reverse=True)
    return f"{card['card_id']}.{table_numbers(card)[rows[0]['table_id']]}", rows[0]["table_id"], card


def output(stats, splitters, choices, periods=(), asks=(), frequencies=()) -> dict:
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    ids = [sid for sid, _ in stats]
    out["statistics"] = [{"id": sid, "quote": q, "name": q} for sid, q in stats]
    out["splitters"] = list(splitters)
    out["periods"] = [{"quote": p, "for": ids} for p in periods]
    out["frequencies"] = [{"quote": f, "for": ids} for f in frequencies]
    out["choices"] = [{"for": [sid], "table": t, "evidence": [{"kind": "title", "text": name}]}
                      for sid, t, name in choices]
    out["asks"] = list(asks)
    return out


def go(question, layer, build):
    cards = build_cards(question, build_pool(question, layer), layer)
    out = fill_option_evidence(build(cards), cards)
    state = run(question, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}


def codes(d):
    return [r["code"] for r in d["reasons"]]


# plan 56 order 5 (policies ②④, user 2026-10-31 17:53 item 1): the graph tests of the stated-basis polarity check
# polarity, not periods. Their made-up question and answer quote a period and a frequency together (``dated``); before,
# none was quoted and the default window and the longest frequency were assumed. The assertions are unchanged; the
# question is otherwise as written (A:CX-Q013's own text is not changed anywhere else).
PERIOD, FREQUENCY = "2024년", "월별"


def dated(question: str) -> str:
    return f"{PERIOD} {FREQUENCY} {question}"


def m2(layer, seasonal, splitters, question=Q013):
    def build(cards):
        r, _, card = ref(cards, M2, layer, seasonal=seasonal, stock="말잔")
        return output([("s1", "광의통화")], splitters, [("s1", r, card["name"])], periods=[PERIOD],
                      frequencies=[FREQUENCY])
    return go(dated(question), layer, build)


STOCK = {"quote": "월말 잔액 기준", "axis": "stock", "for": ["s1"]}


def test_a_negated_value_lets_the_other_values_table_pass(layer):
    """The plan 52 case A:CX-Q013 as written: 원계열 chosen, '계절조정은 하지 않은' quoted (it failed before)."""
    state, d = m2(layer, "원계열", [STOCK, {"quote": NEGATED, "axis": "seasonal", "for": ["s1"]}])
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []
    assert d["s1"]["basis"]["seasonal"]["source"] == "model_choice"
    assert d["s1"]["basis"]["seasonal"]["excluded"] == ["계절조정"]
    assert not any(c["code"] == "basis_switched" for c in d["s1"]["corrections"])


def test_a_table_of_the_negated_value_conflicts_and_is_never_switched(layer):
    """Before plan 53 a 계절조정 table passed this question (the negated word read as a requirement)."""
    state, d = m2(layer, "계절조정", [STOCK, {"quote": NEGATED, "axis": "seasonal", "for": ["s1"]}])
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
    assert d["s1"]["reasons"][0]["detail"]["excluded"] == ["계절조정"]
    assert d["s1"]["corrections"] == [] and d["s1"]["final_tables"] == []


@pytest.mark.parametrize("seasonal, ok", [("원계열", True), ("계절조정", False)])
def test_the_question_path_reads_the_same_polarity(layer, seasonal, ok):
    """No seasonal splitter: the one statistic's question is read (the same polarity as the splitter path)."""
    state, d = m2(layer, seasonal, [STOCK])
    assert (d["s1"]["outcome"] == "selected") is ok
    assert codes(d["s1"]) == ([] if ok else ["stated_basis_conflict"])


@pytest.mark.parametrize("quote, seasonal, outcome", [
    ("계절조정이 아니라 원계열", "원계열", "selected"), ("원계열이 아니라 계절조정", "계절조정", "selected"),
    ("원계열", "원계열", "selected"), ("계절조정", "계절조정", "selected")])
def test_a_contrast_states_its_second_value(layer, quote, seasonal, outcome):
    q = f"월말 잔액 기준의 광의통화를 상품별로 {quote}로 보여줘"
    state, d = m2(layer, seasonal, [STOCK, {"quote": quote, "axis": "seasonal", "for": ["s1"]}], question=q)
    assert d["s1"]["outcome"] == outcome and codes(d["s1"]) == []
    assert d["s1"]["basis"]["seasonal"]["source"] == "splitter"


@pytest.mark.parametrize("quote", ["계절조정이 아니라 원계열", "원계열"])
def test_a_contrast_against_the_chosen_value_is_not_passed(layer, quote):
    """The stated value is 원계열 and the model chose a 계절조정 table without evidence for another: a conflict."""
    q = f"월말 잔액 기준의 광의통화를 상품별로 {quote}로 보여줘"
    state, d = m2(layer, "계절조정", [STOCK, {"quote": quote, "axis": "seasonal", "for": ["s1"]}], question=q)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]


def test_an_unread_negation_scope_stops_the_statistic(layer):
    q = "월말 잔액 기준의 광의통화를 상품별로 보고 싶어. 계절조정 값은 보고 싶지 않은데"
    splitter = {"quote": "계절조정 값은 보고 싶지 않은", "axis": "seasonal", "for": ["s1"]}
    state, d = m2(layer, "원계열", [STOCK, splitter], question=q)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["basis_polarity_unresolved"]


def test_an_ask_on_a_negated_axis_is_never_turned_into_the_other_option(layer):
    def build(cards):
        r1, _, card = ref(cards, M2, layer, seasonal="원계열", stock="말잔")
        r2, _, _ = ref(cards, M2, layer, seasonal="계절조정", stock="말잔")
        ask = {"for": "s1", "axis": "seasonal", "options": [{"value": "원계열", "table": r1},
                                                               {"value": "계절조정", "table": r2}]}
        return output([("s1", "광의통화")], [STOCK], [], asks=[ask])
    state, d = go(Q013, layer, build)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["stated_basis_conflict"]
    assert d["s1"]["reasons"][0]["detail"]["stage"] == "ask_option_excluded"
    assert not any(c["code"] == "unneeded_ask_to_choice" for c in d["s1"]["corrections"])


def test_an_ask_on_a_contrast_axis_becomes_the_stated_option_as_before(layer):
    q = "월말 잔액 기준의 광의통화를 상품별로 계절조정이 아니라 원계열로 보여줘"

    def build(cards):
        r1, _, card = ref(cards, M2, layer, seasonal="원계열", stock="말잔")
        r2, _, _ = ref(cards, M2, layer, seasonal="계절조정", stock="말잔")
        ask = {"for": "s1", "axis": "seasonal", "options": [{"value": "원계열", "table": r1},
                                                               {"value": "계절조정", "table": r2}]}
        return output([("s1", "광의통화")], [STOCK, {"quote": "계절조정이 아니라 원계열", "axis": "seasonal",
                                                    "for": ["s1"]}], [], asks=[ask])
    state, d = go(q, layer, build)
    assert codes(d["s1"]) == [] and [c["code"] for c in d["s1"]["corrections"]][:1] == ["unneeded_ask_to_choice"]


def test_a_negation_of_another_statistic_does_not_apply(layer):
    q = "월말 잔액 기준의 광의통화를 상품별로, 협의통화는 계절조정은 하지 않은 값으로 보여줘"

    def build(cards):
        r, _, card = ref(cards, M2, layer, seasonal="계절조정", stock="말잔")
        out = output([("s1", "광의통화"), ("s2", "협의통화")],
                     [STOCK, {"quote": NEGATED, "axis": "seasonal", "for": ["s2"]}], [("s1", r, card["name"])])
        out["missing"] = [{"for": ["s2"], "search_words": ["협의통화"]}]
        return out
    state, d = go(q, layer, build)
    assert "stated_basis_conflict" not in codes(d["s1"]) and "basis_polarity_unresolved" not in codes(d["s1"])


# ---------------------------------------------------------------- periods through the graph


CA = "경상수지"


def current_account(layer, period, word="연간으로", frequency=None):
    def build(cards):
        r, _, card = ref(cards, CA, layer, seasonal="계절조정")
        return output([("s1", "경상수지")], [], [("s1", r, card["name"])], periods=[period],
                      frequencies=[frequency] if frequency else [])
    return go(f"{period} 계절조정 경상수지를 {word} 보여줘", layer, build)


# plan 56 order 5 (policy ④, user 2026-10-31 17:53 item 4). The test checks that a decade reaches the final
# table's period. Its question asked 연간으로 while the 계절조정 경상수지 tables are monthly only, and the made-up
# answer quoted no frequency, so the resolver took the only frequency (M) and the stated 연간 was lost. Now the
# question's 연간 is restored by code and no table gives it: that is the counterexample below. The decade check
# itself asks 월별로 and quotes it, with its start and end assertions unchanged.
@pytest.mark.parametrize("period, start, end", [("2000년대 이후", "2000", None), ("2000년대", "2000", "2009")])
def test_a_decade_reaches_the_final_table(layer, period, start, end):
    state, d = current_account(layer, period, word="월별로", frequency="월별")
    assert d["s1"]["outcome"] == "selected" and codes(d["s1"]) == []
    (row,) = d["s1"]["final_tables"]
    assert row["period"][0]["start"].startswith(start)
    if end:
        assert row["period"][-1]["end"].startswith(end)


@pytest.mark.parametrize("frequency", [None, "연간"])
@pytest.mark.parametrize("period", ["2000년대 이후", "2000년대"])
def test_a_yearly_request_on_monthly_tables_is_refused_never_the_monthly_table(layer, period, frequency):
    """Quoted or restored, 연간 is the user's frequency: no candidate gives it (each is table_unsuitable), the statistic
    stops with frequency_not_offered and nothing is handed on; the monthly table is never taken instead."""
    state, d = current_account(layer, period, frequency=frequency)
    s = d["s1"]
    assert s["outcome"] == "failed" and codes(s) == ["frequency_not_offered"] and s["final_tables"] == []
    assert state["action"] == "fail" and s["frequency"]["value"] == "Y" and "Y" not in s["frequency"]["offered"]
    assert {r["status"] for r in s["period"]["results"].values()} == {"table_unsuitable"}
    restored = (s.get("frequency_quote_missing") or {}).get("restored")
    assert restored is (None if frequency else True)


@pytest.mark.parametrize("period", ["2000년대 초반", "2025년 들어"])
def test_an_undefined_qualifier_stops_before_a_final_table(layer, period):
    state, d = current_account(layer, period)
    assert d["s1"]["outcome"] == "failed" and codes(d["s1"]) == ["period_partially_read"]
    assert d["s1"]["final_tables"] == []
