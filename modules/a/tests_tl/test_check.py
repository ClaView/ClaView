"""Plan 37 step 3: checks, corrections, final tables and the route, on made-up model outputs through the straight graph
(no model, no network). The cases are the counterexamples the user listed on 2026-10-27 21:20 UTC."""

from __future__ import annotations

import json

import pytest

from conftest import fill_option_evidence  # noqa: E402

from claview_a import check as CH
from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings, parse_output
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"
# the fixed inputs here are of contract .3 (plan 56 order 6-3b: contract .4 is the current one)
OLD = "tl-contract-2026-10-28.3"
LOAN = "2024년 1~3월 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
LOAN_OPEN = "2024년 1~3월 예금은행 가계대출 금리를 월별로 보여줘"
M2_OPEN = "2024년 1~3월 M2 총액을 월별로 보여줘"
M2_SET = "M2 총액을 평잔·원계열 기준으로 월별로 보여줘"


def empty() -> dict:
    return {"statistics": [], "splitters": [], "periods": [], "frequencies": [], "calculations": [], "choices": [],
            "asks": [], "missing": [], "alternatives": []}


def stat(sid: str, quote: str) -> dict:
    return {"id": sid, "quote": quote, "name": quote}


def cards_of(question: str, layer) -> list[dict]:
    return build_cards(question, build_pool(question, layer), layer)


def ref(cards: list[dict], card_name: str, **basis) -> str:
    """The card reference of the table of ``card_name`` whose bracket basis holds every given value."""
    card = next(c for c in cards if c["name"] == card_name)
    num = table_numbers(card)
    rows = [r for r in card["tables"] if all(v in r["basis"].get(a, []) for a, v in basis.items())]
    rows.sort(key=lambda r: PR._end_key(selection_catalog_view(r["table_id"])), reverse=True)
    return f"{card['card_id']}.{num[rows[0]['table_id']]}"


_VIEWS = {}


def selection_catalog_view(table_id: str):
    return _VIEWS[table_id]


@pytest.fixture(scope="module", autouse=True)
def _views(layer):
    _VIEWS.update(selection_catalog(layer).views)


def go(question: str, out: dict, layer):
    state = run(question, layer, lambda payload, request: json.dumps(fill_option_evidence(out, cards_of(question, layer)), ensure_ascii=False), Settings("fake"),
                AS_OF)
    return state, {d["statistic"]: d for d in state["record"].get("decisions", [])}


def loan_choice(cards, table_ref, evidence, quote="예금은행 가계대출 금리") -> dict:
    out = empty()
    out["statistics"] = [stat("s1", quote)]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["choices"] = [{"for": ["s1"], "table": table_ref, "evidence": evidence}]
    return out


# ---------------------------------------------------------------- quotes and evidence


def test_a_quote_not_in_the_question_fails_the_statistic(layer):
    cards = cards_of(LOAN, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "bracket", "text": "신규취급액 기준"}], quote="예금은행 가계대출 이자율")
    state, d = go(LOAN, out, layer)
    assert d["s1"]["outcome"] == "failed" and "quote_not_in_question" in [r["code"] for r in d["s1"]["reasons"]]
    assert state["action"] == "fail"


def test_a_period_quote_not_in_the_question_fails_but_a_calculation_quote_only_warns(layer):
    cards = cards_of(LOAN, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "bracket", "text": "신규취급액 기준"}])
    out["calculations"] = [{"quote": "전년동월비", "for": ["s1"]}]
    _, d = go(LOAN, out, layer)
    assert d["s1"]["outcome"] == "selected"
    assert [w["code"] for w in d["s1"]["warnings"]] == ["calculation_quote_not_in_question"]
    out["periods"] = [{"quote": "2023년", "for": ["s1"]}]
    _, d = go(LOAN, out, layer)
    assert d["s1"]["outcome"] == "failed"


def test_evidence_must_be_a_fact_of_that_table_of_that_kind(layer):
    cards = cards_of(LOAN, layer)
    new = ref(cards, "예금은행 대출금리", loan_basis="신규취급액")
    # another table's bracket
    # (05:37 UTC: recorded under citation, outcome selected_unverified, never selected)
    _, d = go(LOAN, loan_choice(cards, new, [{"kind": "bracket", "text": "잔액 기준"}]), layer)
    assert [r["code"] for r in d["s1"]["citation"]["problems"]] == ["evidence_of_other_table"]
    assert d["s1"]["outcome"] == "selected_unverified"
    # a bracket cited as a title
    _, d = go(LOAN, loan_choice(cards, new, [{"kind": "title", "text": "신규취급액 기준"}]), layer)
    assert [r["code"] for r in d["s1"]["citation"]["problems"]] == ["evidence_kind_mismatch"]
    # words on no card
    _, d = go(LOAN, loan_choice(cards, new, [{"kind": "item", "text": "없는 항목"}]), layer)
    assert [r["code"] for r in d["s1"]["citation"]["problems"]] == ["evidence_not_on_card"]
    assert d["s1"]["outcome"] == "selected_unverified"


def test_a_reference_outside_the_pool_fails_and_alternatives_never_replace_it(layer):
    cards = cards_of(LOAN, layer)
    out = loan_choice(cards, "g99.t1", [{"kind": "title", "text": "예금은행 대출금리"}])
    out["alternatives"] = [{"for": ["s1"], "table": ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                            "why": "x"}]
    state, d = go(LOAN, out, layer)
    assert d["s1"]["outcome"] == "failed" and d["s1"]["reasons"][0]["code"] == "table_not_in_pool"
    assert state["record"]["alternatives"] == out["alternatives"] and not d["s1"]["final_tables"]






# ---------------------------------------------------------------- basis and asks


def test_a_stated_basis_conflict_is_kept_as_an_error_not_switched(layer):
    """Was: switched to the stated table and selected. 07:19 UTC: the model chose another value's table and gave no
    evidence for the stated value's table: no switch, no pass, an error."""
    cards = cards_of(LOAN, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="잔액기준"),
                      [{"kind": "bracket", "text": "잔액 기준"}])
    state, d = go(LOAN, out, layer)
    s = d["s1"]
    assert s["outcome"] == "failed" and state["action"] == "fail" and s["final_tables"] == []
    assert [r["code"] for r in s["reasons"]] == ["stated_basis_conflict"] and s["corrections"] == []
    assert any("신규취급액" in selection_catalog_view(t).title for t in s["reasons"][0]["detail"]["stated_value_tables"])
    assert state["record"]["model_output"]["choices"][0]["table"] == out["choices"][0]["table"]  # original kept


def test_an_ask_on_a_stated_axis_is_corrected_to_the_stated_table(layer):
    cards = cards_of(LOAN, layer)
    out = empty()
    out["statistics"] = [stat("s1", "예금은행 가계대출 금리")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "loan_basis", "options": [
        {"value": "신규취급액", "table": ref(cards, "예금은행 대출금리", loan_basis="신규취급액")},
        {"value": "잔액기준", "table": ref(cards, "예금은행 대출금리", loan_basis="잔액기준")}]}]
    _, d = go(LOAN, out, layer)
    s = d["s1"]
    assert s["outcome"] == "selected" and s["corrections"][0]["code"] == "unneeded_ask_to_choice"
    assert "신규취급액" in selection_catalog_view(s["final_tables"][0]["table_id"]).title


def test_an_open_approved_axis_on_a_choice_keeps_the_models_choice(layer):
    """Was: code turned the choice into an ask (then, 06:47, held it). 07:19 UTC: the model's choice is checked as
    it is; the open axis takes the chosen table's value, recorded as the model's choice, never as the user's."""
    cards = cards_of(LOAN_OPEN, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "item", "text": "가계대출"}])
    state, d = go(LOAN_OPEN, out, layer)
    s = d["s1"]
    assert s["outcome"] == "selected" and state["action"] == "handoff" and s["asks"] == []
    assert s["basis"]["loan_basis"]["source"] == "model_choice" and s["basis"]["loan_basis"]["value"] == ["신규취급액"]


def test_m2_open_on_two_axes_asks_per_axis_and_fixes_neither(layer):
    cards = cards_of(M2_OPEN, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["choices"] = [{"for": ["s1"], "table": ref(cards, "M2 상품별 구성내역", stock="평잔", seasonal="원계열"),
                       "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]}]
    state, d = go(M2_OPEN, out, layer)
    s = d["s1"]
    # 07:19 UTC: the model chose; code checks that table, neither asks nor holds; both axes are the model's choice
    assert s["outcome"] == "selected" and s["asks"] == []
    assert {a: s["basis"][a]["source"] for a in ("stock", "seasonal")} == {"stock": "model_choice",
                                                                            "seasonal": "model_choice"}
    # the model asking on both axes itself (with evidence per option) is kept as two asks
    out["choices"] = []
    out["asks"] = [{"for": "s1", "axis": axis, "options": [
        {"value": v, "table": ref(cards, "M2 상품별 구성내역", **{axis: v})} for v in vals]}
        for axis, vals in (("stock", ("평잔", "말잔")), ("seasonal", ("원계열", "계절조정")))]
    _, d = go(M2_OPEN, out, layer)
    assert d["s1"]["outcome"] == "ask" and len(d["s1"]["asks"]) == 2


# ---------------------------------------------------------------- periods and final tables


def m2_choice(cards, table_ref, period, evidence) -> dict:
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액")]
    out["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
    if period:
        out["periods"] = [{"quote": period, "for": ["s1"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["choices"] = [{"for": ["s1"], "table": table_ref, "evidence": evidence}]
    return out


def _old_edition_ref(cards) -> tuple[str, str, str]:
    """(reference of the old 평잔·원계열 M2 table, its ID, the newer one's ID)."""
    card = next(c for c in cards if c["name"] == "M2 상품별 구성내역")
    rows = [r for r in card["tables"] if r["basis"].get("stock") == ["평잔"] and r["basis"].get("seasonal") == ["원계열"]]
    rows.sort(key=lambda r: PR._end_key(selection_catalog_view(r["table_id"])))
    old, new = rows[0]["table_id"], rows[-1]["table_id"]
    return f"{card['card_id']}.{table_numbers(card)[old]}", old, new


def test_a_representative_that_misses_the_period_gives_way_to_a_sibling_that_covers_it(layer):
    q = "2024년 1~3월 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, "2024년 1~3월", [{"kind": "item", "text": "M2(평잔 원계열)"}])
    _, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "selected", s["reasons"]
    assert [t["table_id"] for t in s["final_tables"]] == [new]
    assert s["final_tables"][0]["period"] == [{"start": "2024-01", "end": "2024-03"}]
    assert s["corrections"][-1]["code"] == "final_tables_by_period"
    assert s["period"]["readings"][0]["read_as"] == "2024년 1월~2024년 3월"


def test_a_request_across_the_edition_boundary_gets_several_tables_with_their_shares(layer):
    q = "2000년 1월부터 2010년 12월까지 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, "2000년 1월부터 2010년 12월까지", [{"kind": "item", "text": "M2(평잔 원계열)"}])
    _, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "selected", s["reasons"]
    got = {t["table_id"]: t["period"] for t in s["final_tables"]}
    assert set(got) == {old, new}
    months = sorted((p["start"], p["end"]) for ps in got.values() for p in ps)
    assert months[0][0] == "2000-01" and months[-1][1] == "2010-12"


def test_a_candidate_whose_content_is_not_verified_is_never_switched_to(layer):
    q = "2024년 1~3월 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    if PR.item_names(selection_catalog_view(old)) == PR.item_names(selection_catalog_view(new)):
        pytest.skip("the two editions hold the same value names")
    out = m2_choice(cards, old_ref, "2024년 1~3월", [{"kind": "title", "text": "M2 상품별 구성내역"}])  # no item
    _, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "failed" and s["reasons"][0]["code"] == "content_unverified"
    assert s["period"]["usable"] == {old: "representative_checked"}


def test_an_edition_switch_never_changes_the_meaning_basis(layer):
    q = "2024년 1~3월 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, "2024년 1~3월", [{"kind": "item", "text": "M2(평잔 원계열)"}])
    _, d = go(q, out, layer)
    rows = {r["table_id"]: r for c in cards for r in c["tables"]}
    for t in d["s1"]["period"]["candidates"]:
        assert rows[t]["basis"] == rows[old]["basis"]  # no 계절조정 or 말잔 table among the candidates


def test_final_tables_report_reasons_instead_of_guessing(layer):
    views = selection_catalog(layer).views
    conditions, _ = PR.conditions_of(["2024년 1~3월"], ["월별"])
    group = ["DT_101Y004", "DT_161Y006"]
    got = PR.final_tables("DT_101Y004", group, {"DT_101Y004": "representative"}, views, conditions, AS_OF)
    assert got["status"] == "failed" and got["reason"] == "content_unverified"
    quarterly, _ = PR.conditions_of(["2024년 1~3월"], ["분기별"])
    got = PR.final_tables("DT_513Y001", ["DT_513Y001"], {"DT_513Y001": "representative"}, views, quarterly, AS_OF)
    assert got["status"] == "failed" and got["reason"] in ("frequency_not_offered", "period_not_covered")


def test_a_closed_month_or_quarter_range_names_its_year_at_both_ends():
    assert PR.normalize_quote("2024년 1~3월") == "2024년 1월~2024년 3월"
    assert PR.normalize_quote("2024년 1월~3월") == "2024년 1월~2024년 3월"
    assert PR.normalize_quote("2024년 1~4분기") == "2024년 1분기~2024년 4분기"
    assert PR.normalize_quote("2015~2025년") == "2015~2025년" and PR.normalize_quote("최근 3년간") == "최근 3년간"
    conditions, readings = PR.conditions_of(["2024년 1~3월"], ["월별"])
    assert [c["kind"] for c in conditions] == ["period", "frequency"]
    assert conditions[0]["attrs"]["form"] == "range" and readings[0]["quote"] == "2024년 1~3월"


# ---------------------------------------------------------------- several statistics and the route


def test_one_statistic_fails_while_the_other_is_selected_partial_route(layer):
    q = "2024년 1~3월 예금은행 가계대출 금리와 예금은행 수신금리를 신규취급액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "예금은행 가계대출 금리"), stat("s2", "예금은행 수신금리")]
    out["splitters"] = [{"quote": "신규취급액 기준", "axis": "loan_basis", "for": ["s1", "s2"]}]  # explicit common
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}, {"quote": "2023년 1~3월", "for": ["s2"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
    out["choices"] = [
        {"for": ["s1"], "table": ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]},
        {"for": ["s2"], "table": ref(cards, "예금은행 수신금리", loan_basis="신규취급액"),
         "evidence": [{"kind": "bracket", "text": "신규취급액 기준"}]}]
    state, d = go(q, out, layer)  # s2: a period quote not in the question, a condition error that still stops it
    assert d["s1"]["outcome"] == "selected" and d["s2"]["outcome"] == "failed"
    assert state["record"]["route"] == {"action": "partial", "complete": False,
                                        "counts": {"selected": 1, "selected_unverified": 0, "ask": 0,
                                                   "ask_unverified": 0, "not_in_pool": 0, "failed": 1}}


def test_not_in_pool_is_recorded_and_routes_without_a_table(layer):
    cards = cards_of(LOAN, layer)
    out = empty()
    out["statistics"] = [stat("s1", "예금은행 가계대출 금리")]
    out["missing"] = [{"for": ["s1"], "search_words": ["가계대출금리"]}]
    state, d = go(LOAN, out, layer)
    assert d["s1"]["outcome"] == "not_in_pool" and d["s1"]["search_words"] == ["가계대출금리"]
    assert state["action"] == "fail"


def test_a_broken_answer_stops_at_the_judge_with_its_code(layer):
    state = run(LOAN, layer, lambda p, r: "not json", Settings("fake"), AS_OF)
    assert state["action"] == "fail" and state["record"]["error"] == {"stage": "output", "code": "not_json",
                                                                     "detail": ""}
    assert "parsed" not in state


def test_the_route_is_a_pure_function_of_the_outcomes():
    def rec(*outcomes):
        return {"decisions": [{"outcome": o} for o in outcomes]}

    assert CH.route(rec("selected", "selected"))["action"] == "handoff"
    assert CH.route(rec("ask", "selected"))["action"] == "ask"
    assert CH.route(rec("ask", "failed"))["action"] == "partial"
    assert CH.route(rec("not_in_pool"))["action"] == "fail"
    assert CH.route(rec())["action"] == "fail"


def test_the_graph_is_straight_but_for_one_limited_re_judgment(layer):
    # user 2026-10-29 08:27 UTC: this test (was: the six straight edges only) now checks the one allowed branch:
    # a re-judgment only under its conditions, at most once, no third call, no retry, within the call limit. What
    # the straight test stopped, an unbounded re-call, stays stopped.
    import json as _json

    from claview_a.graph import JudgeError, build_graph, run

    g = build_graph().get_graph()
    edges = sorted((e.source, e.target) for e in g.edges)
    straight = [("__start__", "pool"), ("pool", "cards"), ("cards", "judge"), ("judge", "check"),
                ("check", "route"), ("route", "__end__")]
    branch = [("check", "research"), ("research", "judge"), ("research", "route"), ("check", "research_end"),
              ("research_end", "route")]
    assert edges == sorted(straight + branch)

    q = "2024년에 국내에서 생산해 국내에 팔거나 수출한 것들의 전체 물가 흐름을 월별로 보여줘"
    lost = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                            "missing", "alternatives")}
    lost["statistics"] = [{"id": "s1", "quote": "전체 물가 흐름", "name": "물가"}]
    lost["missing"] = [{"for": ["s1"], "search_words": ["총산출물가지수"]}]

    def judge_of(*answers):
        calls = []

        def judge(payload, request):
            calls.append(1)
            a = answers[min(len(calls), len(answers)) - 1]
            if isinstance(a, Exception):
                raise a
            return _json.dumps(a, ensure_ascii=False)
        return judge, calls

    def go(judge, **kw):
        return run(q, layer, judge, Settings("fake"), AS_OF, **kw)

    judge, calls = judge_of(lost)  # off by default: the straight path, one call
    assert go(judge)["action"] == "fail" and len(calls) == 1
    judge, calls = judge_of(lost)  # on, missing again: two calls and the end, never a third
    state = go(judge, research=True)
    assert len(calls) == 2 and state["research"]["end"] == "still_missing" and state["action"] == "fail"
    judge, calls = judge_of(JudgeError("timeout"))  # a failed first call: no retry, no re-search
    assert go(judge, research=True)["research"]["end"] == "first_round_error" and len(calls) == 1
    judge, calls = judge_of(lost, JudgeError("timeout"))  # a failed re-judgment: no retry
    assert go(judge, research=True)["research"]["end"] == "second_round_error" and len(calls) == 2
    judge, calls = judge_of(lost)  # the run's limit refuses the re-judgment: no second call
    assert go(judge, research=True, research_budget=lambda: False)["research"]["end"] == "rejudge_call_limit"
    assert len(calls) == 1


def test_parse_output_is_not_acceptance(layer):
    cards = cards_of(LOAN, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "item", "text": "없는 항목"}])
    parsed = parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards, OLD)
    assert parsed["problems"] == []
    record = CH.check(LOAN, cards, parsed, selection_catalog(layer).views, AS_OF)
    assert record["decisions"][0]["outcome"] == "selected_unverified"  # 05:37 UTC: never "selected"


# ---------------------------------------------------------------- step-3 review counterexamples (user 2026-10-27 21:36 UTC)




def test_a_total_word_is_met_by_the_top_value_and_recorded(layer):
    views = selection_catalog(layer).views
    got = CH.support("M2 총액", "DT_161Y006", views)
    assert got["verdict"] == "supported"
    total = next(w for w in got["words"] if w["word"] == "총액")
    assert total["rule"] == "total_word" and total["place"].startswith("top value:")
    assert CH.support("예금은행 가계대출 금리", "DT_121Y006", views)["verdict"] == "supported"




def test_a_basis_switch_to_a_table_without_the_cited_value_fails(layer):
    q = "2024년 1~3월 예금은행 기업일반자금대출 금리를 잔액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    new = ref(cards, "예금은행 대출금리", loan_basis="신규취급액")
    out = loan_choice(cards, new, [{"kind": "item", "text": "기업일반자금대출"}], quote="예금은행 기업일반자금대출 금리")
    state, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "failed" and state["action"] == "fail"
    # 07:19 UTC: the question states 잔액 and the model chose the new-loan table: kept as a conflict, not switched
    assert s["reasons"][0]["code"] == "stated_basis_conflict" and s["corrections"] == []
    assert s["model_evidence"][0]["text"] == "기업일반자금대출"  # the model's evidence kept apart from code's
    assert all(e["kind"] == "bracket" for e in s["code_evidence"])


def test_a_model_ask_on_one_axis_leaves_the_other_open_axis_held(layer):
    cards = cards_of(M2_OPEN, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "stock", "options": [
        {"value": v, "table": ref(cards, "M2 상품별 구성내역", stock=v, seasonal="원계열")} for v in ("평잔", "말잔")]}]
    _, d = go(M2_OPEN, out, layer)
    s = d["s1"]
    # was: code added the seasonal ask. 07:19 UTC: the model's ask stands as it is; the axis it did not ask stays
    # open (whether an ask was needed there is scored, not decided by code)
    assert s["outcome"] == "ask" and [(a["axis"], a["source"]) for a in s["asks"]] == [("stock", "model")]
    assert s["unasked_axes"] == ["seasonal"] and "seasonal" not in s["basis"]


def test_a_basis_stated_for_another_statistic_is_never_taken_as_this_ones(layer):
    q = "2024년 1~3월 M2 총액은 평잔 원계열, M1 총액은 말잔 원계열로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액"), stat("s2", "M1 총액")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
    out["choices"] = [
        {"for": ["s1"], "table": ref(cards, "M2 상품별 구성내역", stock="말잔", seasonal="원계열"),
         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]},
        {"for": ["s2"], "table": ref(cards, "M1 상품별 구성내역", stock="말잔", seasonal="원계열"),
         "evidence": [{"kind": "title", "text": "M1 상품별 구성내역"}]}]
    state, d = go(q, out, layer)  # no splitters: the question's 평잔/말잔 cannot be attributed
    assert d["s1"]["outcome"] == "failed" and d["s1"]["reasons"][0]["code"] == "basis_attribution_unresolved"
    assert "stock" not in d["s1"]["basis"]
    out["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                        {"quote": "말잔", "axis": "stock", "for": ["s2"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s1"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s2"]}]
    state, d = go(q, out, layer)
    assert d["s2"]["outcome"] == "selected", d["s2"]["reasons"]
    assert "stock" not in d["s1"]["basis"]  # stated 평잔 is s1's, and s1's 말잔 table conflicts with it
    # 07:19 UTC: s1 chose the 말잔 table against its stated 평잔: a conflict, not switched
    assert d["s1"]["outcome"] == "failed" and d["s1"]["reasons"][0]["code"] == "stated_basis_conflict"
    assert state["action"] == "partial"


def test_a_period_quote_that_cannot_be_read_fails_and_never_falls_back_to_a_default(layer):
    q = "팬데믹 이전 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "bracket", "text": "신규취급액 기준"}])
    out["periods"] = [{"quote": "팬데믹 이전", "for": ["s1"]}]
    state, d = go(q, out, layer)
    assert d["s1"]["outcome"] == "failed" and d["s1"]["reasons"][0]["code"] == "period_unresolved"
    assert not d["s1"]["final_tables"] and state["action"] == "fail"


def test_a_period_the_user_did_not_decide_is_asked_in_contract_4(layer):
    """Plan 56 order 6-3b (user 2026-10-31 17:53 UTC): the same question answered to contract .4, the model saying the
    user did not decide the range (meaning user_undecided): the statistic's period is asked, no default is applied,
    nothing is handed on. The answer above (contract .3) still reads as period_unresolved. A synthetic judgment: the
    code path, not the model's reading."""
    q = "팬데믹 이전 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "bracket", "text": "신규취급액 기준"}])
    out["periods"] = [{"quote": "팬데믹 이전", "for": ["s1"], "meaning": "user_undecided", "refers_to": []}]
    out["aggregations"] = []
    text = json.dumps(fill_option_evidence(out, cards), ensure_ascii=False)

    def judge(payload, request):
        return text

    judge.answer_contract = "tl-contract-2026-10-28.4"  # an answer written to contract .4 (.6 is current)
    state = run(q, layer, judge, Settings("fake"), AS_OF)
    s = state["record"]["decisions"][0]
    assert s["outcome"] == "ask_condition" and s["reasons"] == [] and not s["final_tables"]
    assert [(a["kind"], a["cause"]) for a in s["condition_asks"]] == [("period", "unreadable_range")]
    assert s["period"]["default_applied"] is False and state["action"] == "ask"


def test_a_partly_read_period_and_base_year_marks():
    got = PR.read_quotes(["2023, 2024년"], [])
    assert [p["code"] for p in got["problems"]] == ["period_partially_read"]
    assert got["problems"][0]["uncovered"] == ["2023"]
    got = PR.read_quotes(["2020=100"], [])
    assert got["problems"] == [] and got["conditions"] == []
    assert got["readings"][0]["base_year_removed"] == ["2020=100"]
    # the base-year policy changed (users 2026-10-29 07:51 and 08:42 UTC). Before: "2015년 기준" taken out as a base
    # year, one condition (2024) left. Now: how the two years relate cannot be told, so the statistic stops with
    # period_base_year_ambiguous; 2015 is never dropped to keep 2024 alone
    got = PR.read_quotes(["2015년 기준 2024년"], [])
    assert got["readings"][0]["base_year_removed"] == []
    assert [p["code"] for p in got["problems"]] == ["period_base_year_ambiguous"]
    got = PR.read_quotes([], ["어쩌다"])
    assert [p["code"] for p in got["problems"]] == ["frequency_unresolved"]


def test_no_period_applies_the_resolvers_default_and_records_it(layer):
    # plan 56 order 5 (policy ②, user 2026-10-31 17:02). Before: selected, default_applied True, the newest edition on
    # the resolver's default window (results[new].source "default"). Now a question whose period cannot be decided is
    # asked: default_applied False, cause user_omitted, one period question, no default period decided or handed on.
    q = M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, None, [{"kind": "item", "text": "M2(평잔 원계열)"}])
    state, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "ask_condition" and s["period"]["default_applied"] is False
    assert s["period"]["cause"] == "user_omitted" and s["final_tables"] == []
    assert "period" in [a["kind"] for a in s["condition_asks"]] and state["action"] == "ask"


def test_too_many_tables_and_missing_data_are_told_apart(layer, monkeypatch):
    views = selection_catalog(layer).views
    group = ["DT_101Y004", "DT_161Y006"]
    usable = {t: "cited_values_held" for t in group}
    conditions, _ = PR.conditions_of(["2000년 1월부터 2010년 12월까지"], ["월별"])
    monkeypatch.setattr(PR, "MAX_STITCH", 1)
    got = PR.final_tables("DT_161Y006", group, usable, views, conditions, AS_OF)
    assert got["status"] == "failed" and got["reason"] == "stitch_limit_exceeded"
    monkeypatch.setattr(PR, "MAX_STITCH", 3)
    early, _ = PR.conditions_of(["1950년 1월부터 2010년 12월까지"], ["월별"])
    got = PR.final_tables("DT_161Y006", group, usable, views, early, AS_OF)
    assert got["status"] == "failed" and got["reason"] == "period_not_covered"
    got = PR.final_tables("DT_161Y006", group, usable, views, conditions, AS_OF)
    shares = sorted((p["start"], p["end"]) for t in got["tables"] for p in t["period"])
    assert shares[0][0] == "2000-01" and shares[-1][1] == "2010-12"
    assert all(a[1] < b[0] for a, b in zip(shares, shares[1:]))  # no overlap


def test_an_expected_judge_failure_is_recorded_and_a_program_fault_is_not_hidden(layer):
    from claview_a.graph import JudgeError

    def broken(payload, request):
        raise JudgeError("transport")

    state = run(LOAN, layer, broken, Settings("fake"), AS_OF)
    assert state["action"] == "fail" and state["record"]["error"] == {"stage": "judge", "code": "transport",
                                                                     "detail": ""}
    assert state["record"]["route"]["complete"] is False

    def faulty(payload, request):
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        run(LOAN, layer, faulty, Settings("fake"), AS_OF)
    with pytest.raises(ValueError):
        JudgeError("anything")


def test_two_point_years_stay_two_points_and_a_split_share_fails_explicitly(layer):
    # user 2026-10-27 21:38 UTC: "2020년, 2024년" is two points, never one stretched range nor one year
    views = selection_catalog(layer).views
    got = PR.read_quotes(["2020년, 2024년"], [])
    assert got["problems"] == [] and [c["attrs"]["form"] for c in got["conditions"]] == ["point", "point"]
    result = PR.resolve(got["conditions"], views["DT_161Y006"], AS_OF)
    assert result["periods"]["requested_months"] == [{"start": "2020-01", "end": "2020-12"},
                                                     {"start": "2024-01", "end": "2024-12"}]
    assert [p["code"] for p in PR.read_quotes(["2020, 2024년"], [])["problems"]] == ["period_partially_read"]
    # a base year is not turned into a period
    # the base-year policy changed (users 2026-10-29 07:51 and 08:42 UTC). Before: "2020년 기준" taken out as a base
    # year, no condition. Now: quoted as a period by the model, it is the observation year 2020, neither removed nor
    # replaced by the default period. Explicit base-year marks are still taken out (the 2020=100 case above)
    base = PR.read_quotes(["2020년 기준"], [])
    assert base["readings"][0]["base_year_removed"] == [] and base["problems"] == []
    assert [(c["attrs"]["form"], c["attrs"]["start_expr"]["year"]) for c in base["conditions"]] == [("point", "2020")]
    explicit = PR.read_quotes(["기준연도 2020년"], [])
    assert explicit["conditions"] == [] and explicit["readings"][0]["base_year_removed"] == ["기준연도 2020년"]
    # points that need one edition twice around another edition cannot be one continuous share
    group = ["DT_101Y004", "DT_161Y006"]
    usable = {t: "cited_values_held" for t in group}
    conditions = PR.read_quotes(["1990년과 1995년과 2024년"], [])["conditions"]
    got = PR.final_tables("DT_161Y006", group, usable, views, conditions, AS_OF)
    assert got["status"] == "failed" and got["reason"] == "stitch_check_failed"  # it used to raise ValueError


# ---------------------------------------------------------------- step-3 re-review counterexamples (user 2026-10-27 21:54 UTC)




def test_expression_rules_are_recorded_and_a_measure_word_needs_its_form_on_the_table(layer):
    views = selection_catalog(layer).views
    rules = {w["word"]: (w["rule"], w["place"]) for w in CH.support("산업용 전력 가격", "DT_404Y016", views)["words"]}
    assert rules["가격"] == ("measure_word", "form on table: 물가")
    rules = {w["word"]: (w["rule"], w["place"]) for w in CH.support("M2 통화량", "DT_161Y008", views)["words"]}
    assert rules["통화량"][0] == "quantity_stem"
    rules = {w["word"]: (w["rule"], w["place"]) for w in CH.support("국내에 공급된 원재료", "DT_405Y006", views)["words"]}
    assert rules["공급된"][0] == "ending_cut"
    got = CH.support("가계신용 잔액", "DT_151Y001", views)  # no balance form on that table: not assumed
    assert got["verdict"] == "insufficient" and got["missing"] == [] and got["unverified"] == ["잔액"]


def test_a_basis_given_to_one_statistic_is_never_spread_to_the_others(layer):
    q = "2024년 1~3월 M2 총액은 기준을 정하지 않고, M1 총액만 평잔 원계열로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액"), stat("s2", "M1 총액")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
    out["choices"] = [
        {"for": ["s1"], "table": ref(cards, "M2 상품별 구성내역", stock="평잔", seasonal="원계열"),
         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]},
        {"for": ["s2"], "table": ref(cards, "M1 상품별 구성내역", stock="평잔", seasonal="원계열"),
         "evidence": [{"kind": "title", "text": "M1 상품별 구성내역"}]}]
    state, d = go(q, out, layer)  # no splitters
    assert d["s1"]["outcome"] == "failed" and d["s1"]["reasons"][0]["code"] == "basis_attribution_unresolved"
    assert state["action"] != "handoff"
    out["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s2"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s2"]}]
    state, d = go(q, out, layer)  # attributed to M1 only
    assert d["s2"]["outcome"] == "selected"
    # s1 is open on both axes and never takes s2's basis: its own chosen table's values, as the model's choice
    assert d["s1"]["outcome"] == "selected" and d["s1"]["basis"]["stock"]["source"] == "model_choice"
    assert state["action"] == "handoff"


def test_a_basis_the_question_states_for_every_statistic_applies_to_each(layer):
    q = "2024년 1~3월 M2와 M1 총액 모두 평잔 원계열로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2"), stat("s2", "M1 총액")]
    out["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1", "s2"]},
                        {"quote": "원계열", "axis": "seasonal", "for": ["s1", "s2"]}]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1", "s2"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1", "s2"]}]
    out["choices"] = [
        {"for": ["s1"], "table": ref(cards, "M2 상품별 구성내역", stock="평잔", seasonal="원계열"),
         "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]},
        {"for": ["s2"], "table": ref(cards, "M1 상품별 구성내역", stock="평잔", seasonal="원계열"),
         "evidence": [{"kind": "title", "text": "M1 상품별 구성내역"}]}]
    state, d = go(q, out, layer)
    assert d["s1"]["outcome"] == "selected" and d["s2"]["outcome"] == "selected" and state["action"] == "handoff"
    assert d["s1"]["basis"]["stock"] == {"value": ["평잔"], "source": "splitter"}


def test_a_period_length_left_unread_stops_the_statistic(layer):
    q = "2024년 1월 이후 3개월 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, "2024년 1월 이후 3개월", [{"kind": "item", "text": "M2(평잔 원계열)"}])
    state, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "failed" and state["action"] == "fail"
    assert s["reasons"][0]["code"] == "period_partially_read" and s["reasons"][0]["detail"]["uncovered"] == ["3개월"]
    assert s["period"]["readings"][0]["read_as"] == "2024년 1월 이후 3개월" and not s["final_tables"]




def test_ask_options_record_their_period_cover_without_deciding_anything(layer):
    q = "2024년 1~3월 M2 총액을 월별로 보여줘"
    cards = cards_of(q, layer)
    card = next(c for c in cards if c["name"] == "M2 상품별 구성내역")
    num = table_numbers(card)
    old_of = {}
    for r in card["tables"]:
        if r["basis"].get("seasonal") == ["원계열"]:
            v = r["basis"]["stock"][0]
            if v not in old_of or PR._end_key(selection_catalog_view(r["table_id"])) < PR._end_key(
                    selection_catalog_view(old_of[v])):
                old_of[v] = r["table_id"]
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "stock", "options": [  # option tables are the OLD editions (end in 2004)
        {"value": v, "table": f"{card['card_id']}.{num[t]}"} for v, t in sorted(old_of.items())]}]
    # 06:47 UTC: code makes no seasonal ask, so the model asks it too (old editions again)
    old_sa = min((r["table_id"] for r in card["tables"] if r["basis"].get("seasonal") == ["계절조정"]
                  and r["basis"].get("stock") == ["평잔"]), key=lambda t: PR._end_key(selection_catalog_view(t)))
    out["asks"].append({"for": "s1", "axis": "seasonal", "options": [
        {"value": "원계열", "table": f"{card['card_id']}.{num[old_of['평잔']]}"},
        {"value": "계절조정", "table": f"{card['card_id']}.{num[old_sa]}"}]})
    state, d = go(q, out, layer)
    s = d["s1"]
    assert s["outcome"] == "ask" and state["action"] == "ask"  # the record never turns the ask into a choice
    assert sorted(a["axis"] for a in s["asks"]) == ["seasonal", "stock"]
    stock = [f for f in s["ask_feasibility"] if f["axis"] == "stock"]
    assert len(stock) == 2 and all(f["remaining_axes"] == ["seasonal"] for f in stock)
    for f in stock:
        assert f["status"] in ("covered", "conditional")  # a newer edition of the option's value covers 2024
        assert all(set(g["other_axes"]) == {"seasonal"} for g in f["groups"])
        assert any(g["status"] == "selected" and g["tables"][0]["table_id"] != f["option_table"] for g in f["groups"])
    assert not s["final_tables"] and "stock" not in s["basis"] and "seasonal" not in s["basis"]


def test_ask_feasibility_marks_an_unreadable_period(layer):
    q = "팬데믹 이전 M2 총액을 월별로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2 총액")]
    out["periods"] = [{"quote": "팬데믹 이전", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": axis, "options": [  # 06:47 UTC: the model asks (code makes no ask)
        {"value": v, "table": ref(cards, "M2 상품별 구성내역", **{axis: v})} for v in vals]}
        for axis, vals in (("stock", ("평잔", "말잔")), ("seasonal", ("원계열", "계절조정")))]
    state, d = go(q, out, layer)
    s = d["s1"]
    # plan 56 order 8 (bundle 7, ⑥, registered policy change): an unreadable period leaves every option
    # unconfirmed: held, never asked as executable. Before: ask
    assert s["outcome"] == "failed" and [r["code"] for r in s["reasons"]] == ["target_table_unconfirmed"]
    assert {f["status"] for f in s["ask_feasibility"]} == {"unreadable"}
    assert s["ask_feasibility"][0]["problems"][0]["code"] == "period_unresolved"


# ---------------------------------------------------------------- statistic support, narrowed (user 2026-10-27 21:57 UTC)


@pytest.mark.parametrize("quote, table, verdict", [
    ("예금은행 가계대출 대출이율", "DT_121Y006", "supported"),   # synonym rule, recorded
    ("예금은행 가계대출 대출이율", "DT_151Y003", "mismatch"),    # the loan-amount table: the indicator differs
    ("예금은행 가계대출금리", "DT_121Y006", "supported"),        # an attached compound, both parts on the table
    ("예금은행 가계대출 금리", "DT_121Y007", "mismatch"),        # another institution's table: the scope differs
    ("예금은행 가계대출 이자부담", "DT_121Y006", "insufficient"),  # scope words met, the indicator unregistered
    ("M2 총액", "DT_161Y006", "supported"),
    ("가계대출 금리", "DT_104Y016", "mismatch"),
])
def test_support_pairs_right_and_wrong_tables(layer, quote, table, verdict):
    got = CH.support(quote, table, selection_catalog(layer).views)
    assert got["verdict"] == verdict, got["words"]
    if verdict == "supported":
        assert any(w.get("indicator") and w.get("met") for w in got["words"])


def test_an_unregistered_word_decides_nothing_by_itself(layer):
    views = selection_catalog(layer).views
    got = CH.support("우리나라 서비스수지 같은것", "DT_301Y013", views)
    assert got["verdict"] in ("supported", "insufficient") and got["missing"] == []
    rule = {w["word"]: w["rule"] for w in got["words"]}
    assert rule["서비스수지"] == "on_table"
    synonym = next(w for w in CH.support("예금은행 가계대출 대출이율", "DT_121Y006", views)["words"]
                   if w["word"] == "대출이율")
    assert synonym["rule"] == "synonym:이율->금리" and synonym["normalized"] == "대출금리" and synonym["place"]




def test_only_period_changing_leftovers_stop_a_period():
    got = PR.read_quotes(["2024년 제외 최근 3년"], [])
    assert got["problems"] and got["problems"][0]["code"] == "period_partially_read"
    assert "제외" in got["problems"][0]["uncovered"]
    got = PR.read_quotes(["2024년 1~3월 사이"], [])
    assert got["problems"] == [] and got["readings"][0]["ignored"] == ["사이"]



# ---------------------------------------------------------------- code fact checks vs model meaning (user 2026-10-27 22:20 UTC)
# Model meaning errors that pass every fact check are evaluation cases: the record keeps them visible as diagnostics,
# and code never blocks them. Code checks that still fail are kept above (references, quotes, evidence on the card,
# stated basis, ask structure, periods, edition shares, switches that lose a cited value).


def test_model_meaning_case_loan_rate_on_the_loan_amount_table(layer):
    cards = cards_of(LOAN_OPEN, layer)
    card = next(c for c in cards if c["name"] == "예금은행 대출금")
    out = loan_choice(cards, f"{card['card_id']}.t1", [{"kind": "title", "text": "예금은행 대출금"}])
    state, d = go(LOAN_OPEN, out, layer)
    s = d["s1"]
    assert s["outcome"] == "selected"  # facts hold: a model meaning error, counted in evaluation
    diag = next(x for x in s["diagnostics"] if x["stage"] == "model_choice")
    assert diag["support"] == "mismatch" and "금리" in diag["missing_words"]


def test_model_meaning_case_interest_burden_on_the_rate_table_and_word_order(layer):
    q = "2024년 1~3월 예금은행 가계대출 이자부담을 신규취급액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    table = ref(cards, "예금은행 대출금리", loan_basis="신규취급액")
    verdicts = set()
    for quote in ("예금은행 가계대출 이자부담", "가계대출 이자부담(예금은행)", "예금은행 가계대출(이자부담)"):
        if quote not in q:
            q2 = q.replace("예금은행 가계대출 이자부담", quote)
        else:
            q2 = q
        cards2 = cards_of(q2, layer)
        out = loan_choice(cards2, ref(cards2, "예금은행 대출금리", loan_basis="신규취급액"),
                          [{"kind": "bracket", "text": "신규취급액 기준"}], quote=quote)
        _, d = go(q2, out, layer)
        diag = next(x for x in d["s1"]["diagnostics"] if x["stage"] == "model_choice")
        verdicts.add(diag["support"])
        assert d["s1"]["outcome"] == "selected"  # not blocked by code
    assert table  # the same rate table each time
    assert verdicts <= {"insufficient", "supported"}  # recorded, never a fact failure


def test_model_meaning_case_another_institution_in_brackets(layer):
    q = "2024년 1~3월 가계대출 금리(비은행예금취급기관)를 신규취급액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    if not any(c["name"] == "예금은행 대출금리" for c in cards):
        pytest.skip("the bank rate card is not in this pool")
    out = loan_choice(cards, ref(cards, "예금은행 대출금리", loan_basis="신규취급액"),
                      [{"kind": "bracket", "text": "신규취급액 기준"}], quote="가계대출 금리(비은행예금취급기관)")
    _, d = go(q, out, layer)
    diag = next(x for x in d["s1"]["diagnostics"] if x["stage"] == "model_choice")
    assert diag["support"] in ("mismatch", "insufficient")  # the bracket is read, not set aside as an alias
    assert d["s1"]["outcome"] == "selected"  # a model meaning case: recorded, not blocked


def test_model_meaning_case_receipt_rate_asked_with_lending_rate_options(layer):
    q = "2024년 1~3월 예금은행 수신금리를 신규취급액 기준으로 월별로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "예금은행 수신금리")]
    out["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "loan_basis", "options": [
        {"value": "신규취급액", "table": ref(cards, "예금은행 대출금리", loan_basis="신규취급액")},
        {"value": "잔액기준", "table": ref(cards, "예금은행 대출금리", loan_basis="잔액기준")}]}]
    _, d = go(q, out, layer)
    s = d["s1"]
    assert s["corrections"][0]["code"] == "unneeded_ask_to_choice"  # the stated value: a code fact correction
    assert {x["support"] for x in s["diagnostics"] if x["stage"].startswith("model_ask_option")} == {"mismatch"}


def test_model_meaning_case_named_card_and_catalog_words(layer):
    q = "2024년 1~3월 예금은행 대출금리 중 가계대출을 월별로 보여줘"
    cards = cards_of(q, layer)
    other = next(c for c in cards if c["name"] == "예금은행 수신금리")
    out = empty()
    out["statistics"] = [stat("s1", "예금은행 대출금리 중 가계대출")]
    out["choices"] = [{"for": ["s1"], "table": f"{other['card_id']}.t1",
                       "evidence": [{"kind": "title", "text": "예금은행 수신금리"}]}]
    _, d = go(q, out, layer)
    assert any("named_table_mismatch" in x for x in d["s1"]["diagnostics"])
    assert not any(r["code"] == "named_table_mismatch" for r in d["s1"]["reasons"])


def test_diagnostics_never_change_candidates_corrections_or_the_final_table(layer, monkeypatch):
    q = "2024년 1~3월 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, "2024년 1~3월", [{"kind": "item", "text": "M2(평잔 원계열)"}])
    _, base = go(q, out, layer)

    def always_mismatch(quote, table, views):
        return {"verdict": "mismatch", "words": [], "missing": ["x"], "unverified": [], "indicator": None}

    monkeypatch.setattr(CH, "support", always_mismatch)
    _, forced = go(q, out, layer)
    for key in ("outcome", "final_tables", "corrections", "reasons", "basis", "asks"):
        assert forced["s1"][key] == base["s1"][key], key
    assert forced["s1"]["period"]["usable"] == base["s1"]["period"]["usable"]
    assert {x["support"] for x in forced["s1"]["diagnostics"]} == {"mismatch"}


def test_candidates_and_final_tables_are_traced_with_their_diagnosis(layer):
    q = "2024년 1~3월 " + M2_SET
    cards = cards_of(q, layer)
    old_ref, old, new = _old_edition_ref(cards)
    out = m2_choice(cards, old_ref, "2024년 1~3월", [{"kind": "item", "text": "M2(평잔 원계열)"}])
    _, d = go(q, out, layer)
    v = d["s1"]["verification"]
    stages = [x["for"] for x in v]
    assert "model_choice" in stages and "choice:period_candidate" in stages and stages[-1] == "final_table"
    final = v[-1]
    assert final["table"] == new and final["ok"] and final["cited_values_held"] == ["M2(평잔 원계열)"]
    assert final["diagnosis"]["verdict"] == "supported" and final["period"] == [{"start": "2024-01", "end": "2024-03"}]


def test_korean_written_lengths_stop_a_period():
    for quote in ("2024년 1월 이후 3개월", "2024년 1월 이후 세 개월", "2024년 1월 이후 석 달", "2024년 1월 이후 석달",
                  "2024년 1월부터 두 분기", "2024년부터 한 해"):
        got = PR.read_quotes([quote], [])
        assert [p["code"] for p in got["problems"]] == ["period_partially_read"], quote
    for quote in ("2024년 1~3월 사이", "2023년과 2024년", "2024년 1~3월", "최근 3년간", "작년 한 해"):
        assert PR.read_quotes([quote], [])["problems"] == [], quote
