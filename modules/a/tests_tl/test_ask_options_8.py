"""Plan 56 order 8 (bundle 7, policy ⑥-가·나): each ask option's status from its own card, and the ask that follows.

supported / unsupported / unconfirmed with why (period, frequency, items, basis); only supported options are offered
and selectable; an unconfirmed option is never turned into unsupported nor offered as executable; one supported option
is chosen without another call only under the five conditions (``auto_decided``, support_only, never the user's
choice); none executable: no question is sent, a re-judgment is pending when a table of the card outside the options
covers the request (code never chooses it), else the ask is invalid. Real catalog, made-up answers, tables found by
card name and title basis; the helper-level cases use a small made-up record.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from test_check import cards_of, empty, go, stat

from claview_a import check as CH
from claview_a import periods as PR
from claview_a.cards import table_numbers
from claview_a.search.tables import selection_catalog

LOSS = "손익 지표"


def ref(cards, layer, name, **basis):
    """The card reference of the newest table of ``name`` whose bracket basis holds every given value."""
    card = next(c for c in cards if c["name"] == name)
    views = selection_catalog(layer).views
    rows = [r for r in card["tables"] if all(v in r["basis"].get(a, []) for a, v in basis.items())]
    rows.sort(key=lambda r: PR._end_key(views[r["table_id"]]), reverse=True)
    return f"{card['card_id']}.{table_numbers(card)[rows[0]['table_id']]}"


def size_law_ask(q, layer, period="2020년 이후"):
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", LOSS)]
    out["periods"] = [{"quote": period, "for": ["s1"]}] if period else []
    out["frequencies"] = [{"quote": "연간", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "size_law", "options": [
        {"value": v, "table": ref(cards, layer, LOSS, size_law=v)} for v in ("중소기업기본법", "조세특례제한법")]}]
    return go(q, out, layer)


def test_an_ask_with_no_executable_option_is_not_sent_and_a_table_outside_is_pending(layer):
    state, d = size_law_ask("2020년 이후 손익 지표를 연간으로 보여줘", layer)
    s = d["s1"]
    assert s["outcome"] == "failed" and [r["code"] for r in s["reasons"]] == ["rejudge_pending"]
    assert s["asks"] == [] and s["final_tables"] == [] and state["action"] != "ask"
    (target,) = [t for t in s["rejudge_targets"] if t["code"] == "ask_not_executable"]
    assert [o["status"] for o in target["options"]] == ["unsupported", "unsupported"]
    assert all(o["why"] == ["period"] for o in target["options"])
    offered = {o["table"] for o in target["options"]}
    assert target["candidates"] and not offered & set(target["candidates"])  # outside the options, never chosen
    assert not any(c["code"] == "auto_decided" for c in s["corrections"])


def test_an_ask_with_no_executable_option_and_nothing_outside_is_invalid(layer):
    state, d = size_law_ask("1990년 손익 지표를 연간으로 보여줘", layer, period="1990년")
    s = d["s1"]
    assert [r["code"] for r in s["reasons"]] == ["ask_invalid"]
    assert s["reasons"][0]["detail"]["problems"] == ["no_executable_option"] and s["asks"] == []


def test_options_the_period_supports_are_asked_with_their_status(layer):
    state, d = size_law_ask("2015년 손익 지표를 연간으로 보여줘", layer, period="2015년")
    s = d["s1"]
    if s["outcome"] == "ask":  # both cover 2015 only if each card table does; then both are offered
        assert all(o["status"] == "supported" and o["selectable"] for a in s["asks"] for o in a["options"])
    else:  # one covers it: chosen by ⑥-나 with its words, never as the user's choice
        (auto,) = [c for c in s["corrections"] if c["code"] == "auto_decided"]
        assert auto["reason"] == "support_only" and "하나여서 선택했습니다" in auto["message"]
        assert s["basis"]["size_law"]["source"] == "auto_decided"


# ---------------------------------------------------------------- the rules on a made-up record


def record(statuses, evidence=True):
    """One ask (axis seasonal) whose options have the given (frequency status, feasibility status)."""
    opts = [{"value": f"v{i}", "table_id": f"T{i}"} for i in range(len(statuses))]
    d = {"asks": [{"axis": "seasonal", "options": opts}], "corrections": [], "basis": {},
         "option_evidence": {o["table_id"]: {"valid": evidence} for o in opts}, "retention": None,
         "ask_frequency_checks": [{"axis": "seasonal", "value": o["value"], "status": f}
                                  for o, (f, _) in zip(opts, statuses) if f],
         "ask_feasibility": [{"axis": "seasonal", "value": o["value"], "status": s, "remaining_axes": [],
                              "groups": [{"reason": None if s == "covered" else "period_not_covered"}]}
                             for o, (_, s) in zip(opts, statuses)]}
    return d


READING = {"conditions": [{"kind": "period"}, {"kind": "frequency"}], "problems": [], "readings": []}
LK = SimpleNamespace(views={"T0": SimpleNamespace(periods=[("Y", "2000", "2025")])})


def test_one_supported_and_one_unsupported_is_chosen_by_the_five_conditions():
    d = record([("supported", "covered"), ("not_offered", "not_covered")])
    assert CH.settle_asks(d, "q", READING, LK, "2026-10-22") is None
    assert d["asks"] == [] and d["model_kind"] == "ask_corrected" and d["representative"] == "T0"
    (auto,) = d["corrections"]
    assert (auto["code"], auto["reason"], auto["value"]) == ("auto_decided", "support_only", "v0")
    assert [o["status"] for o in auto["options"]] == ["supported", "unsupported"]
    assert auto["message"] == CH.AUTO_DECIDED_MESSAGE.format(value="v0")


def test_one_supported_and_one_unconfirmed_is_held_never_chosen():
    d = record([("supported", "covered"), ("items_kept_unconfirmed", "covered")])
    code, detail = CH.settle_asks(d, "q", READING, LK, "2026-10-22")
    assert code == "target_table_unconfirmed" and not d["corrections"]
    assert [o["status"] for o in detail["options"]] == ["supported", "unconfirmed"]


def test_no_choice_when_the_option_has_no_valid_evidence_or_an_answer_is_pending():
    d = record([("supported", "covered"), ("not_offered", "not_covered")], evidence=False)
    assert CH.settle_asks(d, "q", READING, LK, "2026-10-22")[0] == "target_table_unconfirmed"
    d = record([("supported", "covered"), ("not_offered", "not_covered")])
    d["ask_feasibility"][0]["status"] = "conditional"  # its status depends on another open axis
    d["ask_feasibility"][0]["remaining_axes"] = ["price"]
    assert CH.settle_asks(d, "q", READING, LK, "2026-10-22")[0] == "target_table_unconfirmed"
    d = record([("supported", "covered"), ("not_offered", "not_covered")])
    no_frequency = {"conditions": [{"kind": "period"}], "problems": [], "readings": []}
    lk = SimpleNamespace(views={"T0": SimpleNamespace(periods=[("Y", "2000", "2025"), ("Q", "2000 1/4", "2025 4/4")])})
    assert CH.settle_asks(d, "q", no_frequency, lk, "2026-10-22")[0] == "target_table_unconfirmed"


def test_two_supported_options_are_asked_and_an_unconfirmed_one_is_shown_unselectable():
    d = record([("supported", "covered"), ("supported", "covered"), ("items_kept_unconfirmed", "covered")])
    assert CH.settle_asks(d, "q", READING, LK, "2026-10-22") is None
    assert [(o["status"], o["selectable"]) for o in d["asks"][0]["options"]] == [
        ("supported", True), ("supported", True), ("unconfirmed", False)]


def test_an_unread_period_check_is_unconfirmed_never_unsupported():
    d = record([(None, "covered"), (None, "not_covered")])
    d["ask_feasibility"][1]["groups"] = [{"reason": "period_unresolved"}]
    assert CH.settle_asks(d, "q", READING, LK, "2026-10-22")[0] == "target_table_unconfirmed"
    assert d["asks"] == []


def test_without_a_quoted_period_the_statuses_are_recorded_and_the_ask_goes_on():
    d = record([("supported", "covered"), ("not_offered", "not_covered")])
    assert CH.settle_asks(d, "q", {"conditions": [], "problems": [], "readings": []}, LK, "2026-10-22") is None
    opts = d["asks"][0]["options"]
    assert [o["status"] for o in opts] == ["supported", "unsupported"] and opts[1]["depends_on"] == ["period"]
    assert all(o["selectable"] for o in opts) and not d["corrections"]


# ---------------------------------------------------------------- options of several cards (⑨, order 8-2)

M2 = "M2 상품별 구성내역"
M2H = "M2 경제주체별 보유현황"


def two_card_ask(layer, values=(("말잔", M2, "말잔"), ("평잔", M2H, "평잔"))):
    q = "2024년 월별 M2를 원계열로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2")]
    out["periods"] = [{"quote": "2024년", "for": ["s1"]}]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "stock", "options": [
        {"value": v, "table": ref(cards, layer, name, stock=on, seasonal="원계열")} for v, name, on in values]}]
    return go(q, out, layer)


def test_options_of_two_cards_are_no_problem_any_more(layer):
    _, d = two_card_ask(layer)
    s = d["s1"]
    problems = [p for r in s["reasons"] for p in (r.get("detail") or {}).get("problems", [])]
    assert "options_in_several_cards" not in problems
    assert s["outcome"] == "ask", s["reasons"]
    assert {o["status"] for a in s["asks"] for o in a["options"]} == {"supported"}


def test_an_option_value_not_on_its_table_is_still_invalid(layer):
    _, d = two_card_ask(layer, values=(("말잔", M2, "말잔"), ("평잔", M2H, "말잔")))  # 평잔 on a 말잔 table
    s = d["s1"]
    problems = [p for r in s["reasons"] for p in (r.get("detail") or {}).get("problems", [])]
    assert [r["code"] for r in s["reasons"]] == ["ask_invalid"] and "options_in_several_cards" not in problems
    assert "option_value_not_on_table:평잔" in problems


def test_the_scorer_allows_several_cards_only_for_records_of_the_policy_that_does():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from tl_eval import score as S

    new = {"run_info": {"versions": {"check": "tl-check-2026-10-31.29"}}}
    old = {"run_info": {"versions": {"check": "tl-check-2026-10-31.27"}}}
    assert S.several_cards_allowed(new) and not S.several_cards_allowed(old) and not S.several_cards_allowed({})


# ---------------------------------------------------------------- combinations, question order, display (8-3, 8-4)


def test_combinations_are_marked_executable_needs_confirmation_or_unsupported():
    d = {"asks": [{"axis": "stock", "options": [{"value": "말잔", "status": "supported"},
                                                  {"value": "평잔", "status": "supported"}]},
                  {"axis": "seasonal", "options": [{"value": "원계열", "status": "supported"},
                                                     {"value": "계절조정", "status": "unconfirmed"}]}]}

    def row(axis, value, groups):
        other = "seasonal" if axis == "stock" else "stock"
        return {"axis": axis, "value": value, "remaining_axes": [other],
                "groups": [{"other_axes": {other: k}, "status": s, "reason": r} for k, s, r in groups]}
    d["ask_feasibility"] = [
        row("stock", "말잔", [("원계열", "selected", None), ("계절조정", "selected", None)]),
        row("stock", "평잔", [("원계열", "failed", "period_not_covered")]),  # no 평잔 계절조정 table: no group
        row("seasonal", "원계열", [("말잔", "selected", None), ("평잔", "failed", "period_not_covered")]),
        row("seasonal", "계절조정", [("말잔", "selected", None)])]
    got = {(c["values"]["stock"], c["values"]["seasonal"]): c["status"] for c in CH.ask_combinations(d)}
    assert got == {("말잔", "원계열"): "executable", ("말잔", "계절조정"): "needs_confirmation",
                   ("평잔", "원계열"): "unsupported", ("평잔", "계절조정"): "unsupported"}
    assert CH.ask_combinations({"asks": d["asks"][:1]}) == []


def test_without_a_period_the_period_is_asked_first_when_the_options_depend_on_it(layer):
    import test_period_frequency_46 as F

    from claview_a import answers as A

    q = "실질 국내총생산에 대한 지출을 연간으로 보여줘"
    state, s = F.go(q, F.GDP, lambda card: F.output(F.GDP, [("실질", "price")], None, "연간",
                                                    asks=[F.seasonal_ask(card, price="실질")]), layer)
    assert s["outcome"] == "ask_condition" and [a["kind"] for a in s["condition_asks"]] == ["period"]
    assert all(a["after"] == ["period"] for a in s["asks"])
    assert [x["kind"] for x in A.questions(state)] == ["period"]  # the basis question waits for the answer
    (period,) = A.questions(state)
    option = next(o for o in period["options"] if "Y" in (o.get("frequencies") or ["Y"]))
    new = A.apply(state, [{"question_id": period["question_id"], "question_version": 0,
                           "option_id": option["option_id"]}], layer)
    d = new["record"]["decisions"][0]
    assert not d["condition_asks"] and d["ask_feasibility"]  # the statuses judged again on the answer
    # 계절조정 has only finer tables (an unconfirmed aggregation): never unsupported, so 원계열 is not chosen for it
    assert not any(c["code"] == "auto_decided" for c in d["corrections"])
    assert [r["code"] for r in d["reasons"]] == ["target_table_unconfirmed"]


def test_without_a_period_independent_options_are_asked_on_the_same_screen(layer):
    from claview_a import answers as A

    q = "월별 M2를 원계열로 보여줘"
    cards = cards_of(q, layer)
    out = empty()
    out["statistics"] = [stat("s1", "M2")]
    out["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    out["asks"] = [{"for": "s1", "axis": "stock", "options": [
        {"value": v, "table": ref(cards, layer, M2, stock=v, seasonal="원계열")} for v in ("말잔", "평잔")]}]
    state, d = go(q, out, layer)
    s = d["s1"]
    if s["outcome"] == "ask_condition":
        assert all(a["after"] == [] for a in s["asks"])
        assert sorted(x["kind"] for x in A.questions(state)) == ["basis_axis", "period"]


def test_an_unconfirmed_option_is_shown_but_never_accepted():
    from claview_a import answers as A

    d = {"statistic": "s1", "outcome": "ask", "asks": [{"axis": "seasonal", "source": "model", "options": [
        {"value": "원계열", "table_id": "T0", "status": "supported", "selectable": True},
        {"value": "계절조정", "table_id": "T1", "status": "unconfirmed", "selectable": False},
        {"value": "x", "table_id": "T2", "status": "unsupported", "selectable": False}]}]}
    state = {"record": {"decisions": [d]}}
    (q,) = A.questions(state)
    assert [(o["value"], o["status"], o["selectable"]) for o in q["options"]] == [
        ("원계열", "supported", True), ("계절조정", "unconfirmed", False)]  # the unsupported one is not shown
    import pytest

    with pytest.raises(A.AnswerError) as e:
        A.apply(state, [{"question_id": q["question_id"], "question_version": 0,
                         "option_id": q["options"][1]["option_id"]}], None)
    assert str(e.value) == "option_not_offered" or e.value.args[0] == "option_not_offered"


# ---------------------------------------------------------------- the policy boundaries (users 2026-10-31 21:54)


def lone(table_id, *periods):
    return SimpleNamespace(table_id=table_id, periods=list(periods), table_units=["%"])


def test_a_lone_table_is_not_dropped_because_the_request_ends_after_its_catalog_end():
    views = {"T": lone("T", ("Y", "2000", "2025"))}
    c = PR.read_quotes(["2020년 이후"], ["연간"])["conditions"]
    got = PR.final_tables("T", ["T"], {"T": "representative_checked"}, views, c, "2026-10-22",
                          held_elsewhere=PR.latest_month(list(views.values())))
    assert got["status"] == "selected" and got["tables"][0]["latest_unpublished"] == [
        {"start": "2026-01", "end": "2026-12"}]


def test_an_option_missing_only_its_latest_unpublished_months_stays_supported():
    views = {"T": lone("T", ("M", "2015.01", "2026.06")), "U": lone("U", ("M", "2010.01", "2026.06"))}
    c = PR.read_quotes(["2024년부터"], ["월별"])["conditions"]
    got = PR.final_tables("T", ["T"], {"T": "representative_checked"}, views, c, "2026-10-22",
                          held_elsewhere=PR.latest_month(list(views.values())))
    assert got["status"] == "selected" and got["tables"][0]["latest_unpublished"] == [
        {"start": "2026-07", "end": "2026-10"}]


def test_a_value_the_user_stated_is_never_replaced_by_another_option(layer):
    import test_period_frequency_46 as F

    q = "2024년 연간 계절조정 실질 국내총생산에 대한 지출을 보여줘"
    _, d = F.go(q, F.GDP, lambda card: F.output(F.GDP, [("실질", "price"), ("계절조정", "seasonal")], "2024년",
                                                "연간", asks=[F.seasonal_ask(card, price="실질")]), layer)
    assert not any(c["code"] == "auto_decided" for c in d["corrections"])
    assert "원계열" not in json.dumps(d["basis"], ensure_ascii=False)  # the stated 계절조정 is kept, unmet or not


def test_an_unread_condition_is_a_recovery_target_not_a_plain_question(layer):
    import test_period_frequency_46 as F

    from claview_a import graph as G

    q = "2024년 실질 국내총생산에 대한 지출을 어쩌다 한 번씩 보여줘"
    state, d = F.go(q, F.GDP, lambda card: F.output(F.GDP, [("실질", "price")], "2024년", "어쩌다",
                                                    asks=[F.seasonal_ask(card, price="실질")]), layer)
    assert [t["code"] for t in d["rejudge_targets"]] == ["condition_unreadable"]
    assert [t["kind"] for t in G.rejudge_targets(state["record"])["targets"]] == ["correction"]
    assert not d.get("condition_asks")  # the user is not asked to repeat what the code could not read


def test_the_period_is_not_asked_first_for_a_computable_range_or_the_latest_value(layer):
    from test_meaning_aggregation_63 import judged, layer_of, m2, period, polar

    layer_of[:] = [layer]
    q = "최신 월별 M2를 보여줘"

    def build(cards):
        out = m2([("s1", "M2", "말잔")])(cards)
        choice, out["choices"] = out["choices"][0], []
        out["asks"] = [{"for": "s1", "axis": "stock", "options": [
            {"value": v, "table": ref(cards, layer, "M2 상품별 구성내역", stock=v, seasonal="원계열"),
             "evidence": [{"kind": "title", "text": "M2 상품별 구성내역"}]} for v in ("말잔", "평잔")]}]
        assert choice
        return out
    _, d = judged(layer, q, build, periods=[period("최신", ["s1"], "latest_available")],
                  splitters=[polar("원계열", "seasonal", "원계열", "s1")])
    assert not d["s1"].get("condition_asks")  # 'the latest value' has its meaning: no period question
    _, d = size_law_ask("최근 3년 손익 지표를 연간으로 보여줘", layer, period="최근 3년")
    assert not d["s1"].get("condition_asks")  # a computable range is read, never asked


def test_an_option_whose_aggregation_method_is_verified_stays_supported_and_unconfirmed_otherwise():
    """⑥ with the ④ supplement (coordinator 22:02 note on the 21:54 boundary): an option that gives the requested
    frequency only by aggregation is supported when the method check of 6-3c verifies it (the aggregation still to
    do), unconfirmed when it does not; never unsupported."""
    from test_aggregation_evidence_63c import Card

    lk = Card({"F": {"title": "수지", "items": ["상품수지"], "units": ["백만달러"]}})
    lk.views["F"].periods = [("Q", "2000 1/4", "2026 2/4")]
    d = {"item_evidence": ["상품수지"],
         "ask_frequency_checks": [{"axis": "seasonal", "value": "v", "status": "not_offered", "requested": "Y",
                                   "candidates": [{"table": "F"}]}]}
    row = {"for": ["s1"], "requested_frequency": "Y", "source_frequency": "Q", "method": "sum", "source_basis": "flow",
           "evidence": [{"kind": "item", "text": "상품수지"}], "weights": []}
    assert CH.option_status(d, "seasonal", "v", lk, [row]) == ("supported", [], [])
    assert CH.option_status(d, "seasonal", "v", lk, [{**row, "evidence": [{"kind": "item", "text": "백만달러"}]}]) == (
        "unconfirmed", ["frequency"], [])  # a unit only: the method is not verified
    assert CH.option_status(d, "seasonal", "v", lk, []) == ("unconfirmed", ["frequency"], [])  # no method proposed
