"""Plan 37 stage-1 fixes (user 2026-10-28 05:01 UTC): spacing of quoted card texts, explicit ask value forms, the
order of matched values on a card, the record of another table's evidence, and the scorer's reading of an ask-only
statistic. Each allowance is paired with a counterexample that must still fail. Made-up model outputs, no model, no
network; expectations written by hand."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from conftest import fill_option_evidence  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from claview_a import check as CH  # noqa: E402
from claview_a.cards import build_cards, card_facts, render_card, table_numbers  # noqa: E402
from claview_a.contract import Settings, parse_output  # noqa: E402
from claview_a.graph import run  # noqa: E402
from claview_a.pool import build_pool  # noqa: E402
from claview_a.search.tables import selection_catalog  # noqa: E402
from tl_eval import score as S  # noqa: E402

AS_OF = "2026-10-22"
M2 = "2024년 1~3월 M2 총액을 평잔·원계열 기준으로 월별로 보여줘"
LOAN = "2024년 1~3월 예금은행 가계대출 금리를 신규취급액 기준으로 월별로 보여줘"
LOAN_OPEN = "2024년 1~3월 예금은행 가계대출 금리를 월별로 보여줘"
SERVICE = "2005~2025년 연간 서비스수지와 가공·운송·여행·건설수지 추이를 그래프로 비교해줘."
M2_NEW, RATE_NEW, RATE_BAL, BOP = "DT_161Y006", "DT_121Y006", "DT_121Y015", "DT_301Y013"  # catalog facts


@pytest.fixture(scope="module")
def views(layer):
    return selection_catalog(layer).views


def empty():
    return {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                            "missing", "alternatives")}


def cards_of(question, layer):
    return build_cards(question, build_pool(question, layer), layer)


def ref_of(cards, table):
    for c in cards:
        num = table_numbers(c)
        if table in num:
            return f"{c['card_id']}.{num[table]}"
    raise KeyError(table)


def decide(question, cards, out, views):
    parsed = parse_output(json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), cards)
    rec = CH.check(question, cards, parsed, views, AS_OF)
    return {d["statistic"]: d for d in rec["decisions"]}


def m2_choice(cards, evidence):
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"}]
    o["splitters"] = [{"quote": "평잔", "axis": "stock", "for": ["s1"]},
                      {"quote": "원계열", "axis": "seasonal", "for": ["s1"]}]
    o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    o["choices"] = [{"for": ["s1"], "table": ref_of(cards, M2_NEW), "evidence": evidence}]
    return o


def loan_ask(cards, values, tables=(RATE_NEW, RATE_BAL)):
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "예금은행 가계대출 금리"}]
    o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    o["asks"] = [{"for": "s1", "axis": "loan_basis",
                  "options": [{"value": v, "table": ref_of(cards, t)} for v, t in zip(values, tables)]}]
    return o


def codes(d):
    """Blocking reasons and citation errors together (05:37 UTC keeps them apart in the record)."""
    return [r["code"] for r in d["reasons"] + d["citation"]["problems"]]


# ---------------------------------------------------------------- 1. spacing of a quoted card text


def test_a_spacing_difference_of_an_item_is_allowed_and_recorded(layer, views):
    cards = cards_of(M2, layer)
    d = decide(M2, cards, m2_choice(cards, [{"kind": "item", "text": "M2 (평잔 원계열)"}]), views)["s1"]
    assert codes(d) == [] and d["outcome"] == "selected"
    ev = d["model_evidence"][0]
    assert ev["normalized"] == {"rule": "whitespace", "card_text": "M2(평잔 원계열)"} and ev["text"] == "M2 (평잔 원계열)"
    assert d["item_evidence"] == ["M2(평잔 원계열)"]  # the card's text is carried on, the model's kept in the record


@pytest.mark.parametrize("text", ["M2 평잔 원계열", "M1(평잔 원계열)", "M2(평잔 계절조정계열)", "M2(말잔 원계열)"])
def test_anything_beyond_spacing_still_fails(layer, views, text):
    """Brackets dropped, another number, another value: never one text by spacing."""
    cards = cards_of(M2, layer)
    d = decide(M2, cards, m2_choice(cards, [{"kind": "item", "text": text}]), views)["s1"]
    assert d["outcome"] == "selected_unverified" and codes(d)[0] in ("evidence_not_on_card",
                                                                     "evidence_of_other_table")


def test_spacing_is_allowed_only_within_the_cited_kind(layer, views):
    cards = cards_of(M2, layer)
    d = decide(M2, cards, m2_choice(cards, [{"kind": "title", "text": "M2 (평잔 원계열)"}]), views)["s1"]
    assert codes(d) == ["evidence_kind_mismatch"] and d["citation"]["problems"][0]["detail"]["fact_of_table"] == [
        "item"]


def test_two_card_texts_equal_but_for_spaces_are_ambiguous(layer, views):
    cards = copy.deepcopy(cards_of(M2, layer))
    card = next(c for c in cards if any(r["table_id"] == M2_NEW for r in c["tables"]))
    card["matched"][0]["items"].append({"name": "M2 ( 평잔 원계열)", "tables": [M2_NEW]})  # made-up second spelling
    d = decide(M2, cards, m2_choice(cards, [{"kind": "item", "text": "M2 (평잔 원계열)"}]), views)["s1"]
    assert codes(d) == ["evidence_normalization_ambiguous"]
    assert d["citation"]["problems"][0]["detail"]["card_texts"] == ["M2 ( 평잔 원계열)", "M2(평잔 원계열)"]


# ---------------------------------------------------------------- 2. explicit ask value forms


@pytest.mark.parametrize("values", [("신규취급액 기준", "잔액 기준"), ("신규취급액", "잔액기준"), ("신규취급액기준", "잔액 기준"),
                                    ("신규취급액", "잔액")])  # '잔액' added 05:28 UTC
def test_loan_basis_forms_map_to_the_axis_value_and_the_value_is_used_after(layer, views, values):
    cards = cards_of(LOAN_OPEN, layer)
    d = decide(LOAN_OPEN, cards, loan_ask(cards, values), views)["s1"]
    assert codes(d) == [] and d["outcome"] == "ask"
    (a,) = d["asks"]
    assert [o["value"] for o in a["options"]] == ["신규취급액", "잔액기준"]
    for o, w in zip(a["options"], values):
        assert (o.get("written"), o.get("rule")) == ((w, "value_forms") if w != o["value"] else (None, None))


def test_a_mapped_value_is_compared_with_the_stated_basis(layer, views):
    """The question states 신규취급액: the ask written with '신규취급액 기준' becomes the stated table."""
    cards = cards_of(LOAN, layer)
    d = decide(LOAN, cards, loan_ask(cards, ("신규취급액 기준", "잔액 기준")), views)["s1"]
    assert [c["code"] for c in d["corrections"]] == ["unneeded_ask_to_choice"] and d["representative"] == RATE_NEW


@pytest.mark.parametrize("values,problem", [
    (("잔액 기준", "신규취급액 기준"), "option_value_not_on_table:잔액기준"),  # values on the wrong tables
    (("신규 기준", "잔액 기준"), "option_value_not_on_table:신규 기준"),  # not a listed form
    (("신규취급액", "잔액 금리"), "option_value_not_on_table:잔액 금리"),  # not a listed form either
    (("잔액", "신규취급액"), "option_value_not_on_table:잔액기준")])  # '잔액' on the new-loan table (05:28 UTC)
def test_values_outside_the_forms_or_on_the_wrong_table_still_fail(layer, views, values, problem):
    cards = cards_of(LOAN_OPEN, layer)
    d = decide(LOAN_OPEN, cards, loan_ask(cards, values), views)["s1"]
    assert codes(d) == ["ask_invalid"] and problem in d["reasons"][0]["detail"]["problems"]


def test_no_general_suffix_removal_on_other_axes():
    assert CH.option_value("seasonal", "원계열 기준") == "원계열 기준"
    assert CH.option_value("loan_basis", "잔액 기준") == "잔액기준"
    assert CH.option_value("loan_basis", "잔액") == "잔액기준"  # 05:28 UTC, loan_basis option values only
    assert CH.option_value("stock", "잔액") == "잔액" and CH.option_value("seasonal", "잔액") == "잔액"


# ---------------------------------------------------------------- 3. the card's matched values


def test_a_value_whose_added_words_are_in_the_question_is_shown_first(layer):
    cards = cards_of(SERVICE, layer)
    card = next(c for c in cards if any(r["table_id"] == BOP for r in c["tables"]))
    shown = {m["word"]: [it["name"] for it in m["items"]] for m in card["matched"]}
    assert "운송수지" in shown["운송"] and "여행수지" in shown["여행"]
    assert "더 있음" not in render_card(card)  # every matched value is shown (05:05 UTC), none left as a count
    assert {"운송수지", "여행수지"} <= card_facts(card)[BOP]  # what is shown is what may be cited
    assert "운송수지" in render_card(card) and "여행수지" in render_card(card)


def test_a_narrower_value_never_comes_before_one_the_question_spells_out():
    from claview_a.cards import _rest_rank

    q = "가공·운송·여행·건설수지"
    ranks = {n: _rest_rank(n, "운송", q.replace(" ", "")) for n in ("운송", "운송수지", "운송수입", "기타운송수입")}
    assert ranks == {"운송": 0, "운송수지": 1, "운송수입": 2, "기타운송수입": 2}


def test_a_value_hidden_by_the_card_is_not_citable_even_when_the_catalog_has_it(layer, views):
    cards = copy.deepcopy(cards_of(SERVICE, layer))
    card = next(c for c in cards if any(r["table_id"] == BOP for r in c["tables"]))
    for m in card["matched"]:  # the stage-1 card: 운송수지 matched but not shown
        m["items"] = [it for it in m["items"] if it["name"] != "운송수지"]
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": "서비스수지와 가공·운송·여행·건설수지", "name": "서비스수지"}]
    o["periods"] = [{"quote": "2005~2025년", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "연간", "for": ["s1"]}]
    o["choices"] = [{"for": ["s1"], "table": ref_of(cards, BOP), "evidence": [{"kind": "item", "text": "운송수지"}]}]
    d = decide(SERVICE, cards, o, views)["s1"]
    shown_elsewhere = any("운송수지" in f for t, f in CH.Cards(cards, views).facts.items() if t != BOP)
    assert d["outcome"] == "selected_unverified"
    problem = d["citation"]["problems"][0]
    assert problem["detail"]["chosen_table_in_catalog"] is True  # a fact of the catalog, kept apart from the card
    if shown_elsewhere:
        assert problem["code"] == "evidence_of_other_table"
        assert problem["detail"]["observed"] == "not_on_chosen_table_card_shown_for_owners"
    else:
        assert problem["code"] == "evidence_not_on_card"


def test_text_on_no_card_and_not_in_the_catalog_says_so(layer, views):
    cards = cards_of(M2, layer)
    d = decide(M2, cards, m2_choice(cards, [{"kind": "item", "text": "M3(평잔 원계열)"}]), views)["s1"]
    assert codes(d) == ["evidence_not_on_card"]
    assert d["citation"]["problems"][0]["detail"]["chosen_table_in_catalog"] is False


# ---------------------------------------------------------------- 4. the scorer on an ask-only statistic


def std_ask(cid="ask"):
    return {"case_id": cid, "part": "golden16", "question": "", "statistics": {"count": None, "status": "unconfirmed"},
            "groups": [[RATE_NEW]],
            "representative": {"confirmed_groups": [[RATE_NEW]], "confirmed_equivalents": {},
                               "proposed_equivalents": {}, "status": "proposed"},
            "ask": {"expected": "required", "axis": "loan_basis", "options": None, "tables": [RATE_NEW, RATE_BAL]},
            "new_policy": {"status": "same_as_source", "differs": {}},
            "final": {"status": "unconfirmed", "period": None, "periods": None, "frequency": None, "basis": [],
                      "groups": [[RATE_NEW]]}}


def scored(layer, views, values, tables=(RATE_NEW, RATE_BAL)):
    cards = cards_of(LOAN_OPEN, layer)
    out = loan_ask(cards, values, tables)
    state = run(LOAN_OPEN, layer, lambda p, r: json.dumps(fill_option_evidence(out, cards), ensure_ascii=False), Settings("fake"), AS_OF)
    return S.score_case(S.adapt(state), std_ask(), {t: v.title for t, v in views.items()})


def test_an_ask_matching_the_expected_ask_is_right_meaning(layer, views):
    res = scored(layer, views, ("신규취급액 기준", "잔액 기준"))
    assert res["model_meaning"]["groups"] == ["right"] and res["model_meaning"]["right"] is True
    assert res["final_ask"] == "right"


def test_one_right_option_with_a_wrong_table_is_not_right(layer, views):
    """The right table plus an unrelated one (the receipt-rate table of the same card family is not on this card, so
    the same table twice stands for 'a table that is not the value's')."""
    res = scored(layer, views, ("신규취급액 기준", "잔액 기준"), (RATE_NEW, RATE_NEW))
    assert res["model_meaning"]["groups"] == ["wrong"] and res["model_meaning"]["right"] is False


def test_a_value_missing_or_written_outside_the_forms_is_not_right(layer, views):
    assert scored(layer, views, ("신규취급액 기준", "잔액 금리"))["model_meaning"]["groups"] == ["wrong"]  # not a form
    assert scored(layer, views, ("신규취급액 기준",), (RATE_NEW,))["model_meaning"]["right"] is False  # one option


def test_the_scorer_finds_spacing_and_ambiguity_itself(layer, views):
    cards = cards_of(M2, layer)
    out = m2_choice(cards, [{"kind": "item", "text": "M2 (평잔 원계열)"}])
    state = run(M2, layer, lambda p, r: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    rec = S.adapt(state)
    assert S.model_facts(rec) == {"s1": set()}
    card = next(c for c in rec["cards"] if any(r["table_id"] == M2_NEW for r in c["tables"]))
    card["matched"][0]["items"].append({"name": "M2 ( 평잔 원계열)", "tables": [M2_NEW]})
    assert S.model_facts(rec) == {"s1": {"evidence_normalization_ambiguous"}}


def test_with_every_value_shown_another_tables_value_is_still_refused(layer, views):
    """All matched values shown (05:05 UTC): 운송수지·여행수지 are citable for DT_301Y013, and a value shown only for
    other tables (not held by DT_301Y013 in the catalog either) is still refused."""
    cards = cards_of(SERVICE, layer)
    lk = CH.Cards(cards, views)
    assert {"운송수지", "여행수지"} <= lk.facts[BOP]
    other = sorted({it["name"] for c in cards for m in c["matched"] for it in m["items"]  # value names (cards .10)
                    if BOP not in it["tables"] and not lk.in_catalog(BOP, "item", it["name"]) and "수지" in it["name"]})
    assert other  # there is such a value on the cards of this question
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": "서비스수지와 가공·운송·여행·건설수지", "name": "서비스수지"}]
    o["periods"] = [{"quote": "2005~2025년", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "연간", "for": ["s1"]}]
    ok = copy.deepcopy(o)
    ok["choices"] = [{"for": ["s1"], "table": ref_of(cards, BOP),
                      "evidence": [{"kind": "item", "text": "운송수지"}, {"kind": "item", "text": "여행수지"}]}]
    assert codes(decide(SERVICE, cards, ok, views)["s1"]) == []
    o["choices"] = [{"for": ["s1"], "table": ref_of(cards, BOP), "evidence": [{"kind": "item", "text": other[0]}]}]
    d = decide(SERVICE, cards, o, views)["s1"]
    assert codes(d) == ["evidence_of_other_table"]
    assert d["citation"]["problems"][0]["detail"]["chosen_table_in_catalog"] is False


# ---------------------------------------------------------------- 05:28 UTC: loan_basis notation, legend, scorer


def test_the_loan_basis_label_sent_to_the_model_matches_the_cards_split_values(layer):
    from claview_a.cards import ASK_AXES, CARD_LEGEND
    from claview_a.contract import build_payload

    assert ASK_AXES["loan_basis"] == "신규취급액/잔액기준"
    cards = cards_of(LOAN_OPEN, layer)
    card = next(c for c in cards if any(r["table_id"] == RATE_NEW for r in c["tables"]))
    assert "갈림 loan_basis: 신규취급액=t1 잔액기준=t2" in render_card(card)
    assert build_payload(LOAN_OPEN, cards)["ask_axes"]["loan_basis"] == "신규취급액/잔액기준"
    assert "인용 근거가 아니다" in CARD_LEGEND and "항목 이름 하나" in CARD_LEGEND


def test_balance_in_the_question_states_no_loan_basis(layer, views):
    """'잔액' maps only an ask option's value: a question that says 잔액 is not read as the balance-based rate."""
    q = "2024년 1~3월 예금은행 가계대출 금리 잔액 추이를 월별로 보여줘"
    cards = cards_of(q, layer)
    d = decide(q, cards, loan_ask(cards, ("신규취급액", "잔액")), views)["s1"]
    assert d["outcome"] == "ask" and d["corrections"] == []


def test_balance_on_another_axis_is_refused(layer, views):
    cards = cards_of(M2, layer)
    o = m2_choice(cards, [])
    o["choices"] = []
    o["splitters"] = []
    card = next(c for c in cards if any(r["table_id"] == M2_NEW for r in c["tables"]))
    num = table_numbers(card)
    a, b = [f"{card['card_id']}.{num[t]}" for t in ("DT_161Y005", M2_NEW)]
    o["asks"] = [{"for": "s1", "axis": "seasonal", "options": [{"value": "잔액", "table": a},
                                                               {"value": "원계열", "table": b}]}]
    d = decide(M2, cards, o, views)["s1"]
    assert codes(d) == ["ask_invalid"] and "option_value_not_on_table:잔액" in d["reasons"][0]["detail"]["problems"]


def test_the_scorer_checks_ask_options_like_the_ask_score(layer, views):
    titles = {t: v.title for t, v in views.items()}
    ok = scored(layer, views, ("신규취급액", "잔액"))
    assert ok["final_ask"] == "right" and ok["contract"]["checks"] == {} and ok["model_meaning"]["right"] is True
    bad = scored(layer, views, ("잔액", "신규취급액"))  # values on the wrong tables: code refuses, the scorer agrees
    assert bad["contract"]["checks"] == {"s1": "code_check_success"}
    assert bad["contract"]["model_fact_errors"] == {"s1": ["ask_invalid"]} and "code_false_reject" not in bad["labels"]


def test_a_code_rejection_the_scorer_cannot_decide_is_review_not_false_reject(layer, views):
    """loan_basis options on tables whose titles carry no loan_basis value: code refuses (value not on the table);
    the scorer cannot read the tables' value, so it holds the case instead of calling code wrong."""
    cards = cards_of(M2, layer)
    card = next(c for c in cards if any(r["table_id"] == M2_NEW for r in c["tables"]))
    num = table_numbers(card)
    o = m2_choice(cards, [])
    o["choices"], o["splitters"] = [], []
    o["asks"] = [{"for": "s1", "axis": "loan_basis", "options": [
        {"value": "신규취급액", "table": f"{card['card_id']}.{num['DT_161Y005']}"},
        {"value": "잔액", "table": f"{card['card_id']}.{num[M2_NEW]}"}]}]
    state = run(M2, layer, lambda p, r: json.dumps(fill_option_evidence(o, cards), ensure_ascii=False), Settings("fake"), AS_OF)
    rec = S.adapt(state)
    assert [r["code"] for r in rec["checks"]["s1"]["reasons"]] == ["ask_invalid"]
    res = S.score_case(rec, std_ask(), {t: v.title for t, v in views.items()})
    assert res["contract"]["checks"] == {"s1": "code_check_review"} and "code_false_reject" not in res["labels"]
    assert res["review"][0]["what"] == "ask rejection not decidable"


def test_a_search_word_list_as_item_evidence_still_fails(layer, views):
    """CV-AB-05 (fixed-version live run): the matched-values header copied as one item is not split into items."""
    cards = cards_of(SERVICE, layer)
    o = empty()
    o["statistics"] = [{"id": "s1", "quote": "서비스수지와 가공·운송·여행·건설수지", "name": "서비스수지"}]
    o["periods"] = [{"quote": "2005~2025년", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "연간", "for": ["s1"]}]
    o["choices"] = [{"for": ["s1"], "table": ref_of(cards, BOP),
                     "evidence": [{"kind": "item", "text": "서비스수지, 가공, 운송, 여행, 건설수지"}]}]
    d = decide(SERVICE, cards, o, views)["s1"]
    assert codes(d) == ["evidence_not_on_card"] and d["outcome"] == "selected_unverified"  # never split, never verified
