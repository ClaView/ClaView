"""Contract .3 and the target-table principle (users 2026-10-28 06:42, 06:43, 06:47 and 07:03 UTC): option evidence
required in the current contract and read as an absence for a contract .2 answer; a target table confirmed only by
the model's own evidence for it; same-name values told apart by the shown text; the fixed ask measures. Made-up
outputs, no model, no network; expectations by hand."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from claview_a import check as CH  # noqa: E402
from claview_a import contract as K  # noqa: E402
from claview_a.cards import build_cards, shown_value, table_numbers  # noqa: E402
from claview_a.graph import run  # noqa: E402
from claview_a.pool import build_pool  # noqa: E402
from claview_a.search.tables import selection_catalog  # noqa: E402
from tl_eval import score as S  # noqa: E402

AS_OF = "2026-10-22"
LOAN_OPEN = "2024년 1~3월 예금은행 가계대출 금리를 월별로 보여줘"
RATE_NEW, RATE_BAL = "DT_121Y006", "DT_121Y015"
GFCF = "2016~2025년 연간 실질 총고정자본형성 증감률 추이를 그래프로 보여줘."


@pytest.fixture(scope="module")
def views(layer):
    return selection_catalog(layer).views


def cards_of(q, layer):
    return build_cards(q, build_pool(q, layer), layer)


def ref_of(cards, table):
    for c in cards:
        num = table_numbers(c)
        if table in num:
            return f"{c['card_id']}.{num[table]}"
    raise KeyError(table)


def loan_ask(cards, evidence=None, values=("신규취급액", "잔액")):
    o = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                         "missing", "alternatives")}
    o["statistics"] = [{"id": "s1", "quote": "예금은행 가계대출 금리", "name": "금리"}]
    o["periods"] = [{"quote": "2024년 1~3월", "for": ["s1"]}]
    o["frequencies"] = [{"quote": "월별", "for": ["s1"]}]
    opts = [{"value": v, "table": ref_of(cards, t)} for v, t in zip(values, (RATE_NEW, RATE_BAL))]
    if evidence is not None:
        for opt, ev in zip(opts, evidence):
            opt["evidence"] = ev
    o["asks"] = [{"for": "s1", "axis": "loan_basis", "options": opts}]
    return o


# the fixed inputs here are of contract .3 (plan 56 order 6-3b: contract .4 is the current one)
OLD = "tl-contract-2026-10-28.3"
GOOD = [[{"kind": "bracket", "text": "신규취급액 기준"}, {"kind": "item", "text": "가계대출"}],
        [{"kind": "bracket", "text": "잔액 기준"}, {"kind": "item", "text": "가계대출 2)"}]]


# ---------------------------------------------------------------- contract .3 and the legacy reading


def test_option_evidence_is_required_in_the_current_contract(layer):
    cards = cards_of(LOAN_OPEN, layer)
    with pytest.raises(K.ContractError) as e:  # the key missing: the schema refuses
        K.parse_output(json.dumps(loan_ask(cards), ensure_ascii=False), cards, OLD)
    assert e.value.code == "schema"
    with pytest.raises(K.ContractError) as e:  # an empty list: a violation of contract .3
        K.parse_output(json.dumps(loan_ask(cards, [[], GOOD[1]]), ensure_ascii=False), cards, OLD)
    assert e.value.code == "empty_option_evidence"
    got = K.parse_output(json.dumps(loan_ask(cards, GOOD), ensure_ascii=False), cards, OLD)
    assert got["problems"] == [] and got["answer_contract"] == OLD  # read with the contract it names


def test_a_contract_2_answer_is_read_with_its_absence_recorded_not_as_a_violation(layer, views):
    cards = cards_of(LOAN_OPEN, layer)
    text = json.dumps(loan_ask(cards), ensure_ascii=False)
    got = K.parse_output(text, cards, answer_contract="tl-contract-2026-10-28.2")
    assert got["problems"][0]["code"] == "option_evidence_field_absent" and "not a violation" in got["problems"][0]["note"]
    rec = CH.check(LOAN_OPEN, cards, got, views, AS_OF)
    d = rec["decisions"][0]
    assert d["outcome"] == "failed" and d["reasons"][0]["code"] == "target_table_unconfirmed"
    assert d["reasons"][0]["detail"]["why"] == "evidence field absent in the answer's contract"
    assert d["citation"]["problems"] == []  # an absence is no citation error
    with pytest.raises(ValueError):
        K.parse_output(text, cards, answer_contract="tl-contract-2026-10-28.1")


# ---------------------------------------------------------------- the target table: the model's own evidence


def test_an_ask_with_evidence_on_each_option_table_passes(layer, views):
    cards = cards_of(LOAN_OPEN, layer)
    got = K.parse_output(json.dumps(loan_ask(cards, GOOD), ensure_ascii=False), cards, OLD)
    d = CH.check(LOAN_OPEN, cards, got, views, AS_OF)["decisions"][0]
    assert d["outcome"] == "ask" and d["citation"]["status"] == "verified"
    assert d["option_evidence"][RATE_BAL]["items"] == ["가계대출 2)"]


def test_the_representatives_evidence_copied_to_another_option_is_an_error(layer, views):
    """Evidence copied from the new-loan table to the balance option is not on the balance table's card."""
    cards = cards_of(LOAN_OPEN, layer)
    copied = [GOOD[0], [{"kind": "bracket", "text": "신규취급액 기준"}]]
    got = K.parse_output(json.dumps(loan_ask(cards, copied), ensure_ascii=False), cards, OLD)
    d = CH.check(LOAN_OPEN, cards, got, views, AS_OF)["decisions"][0]
    assert d["outcome"] == "failed" and d["reasons"][0]["code"] == "target_table_unconfirmed"
    assert [p["code"] for p in d["citation"]["problems"]] == ["evidence_of_other_table"]


# ---------------------------------------------------------------- same-name values


def test_a_name_held_by_several_values_is_not_specific_and_the_shown_text_is(layer, views):
    cards = cards_of(GFCF, layer)
    lk = CH.Cards(cards, views)
    table = "DT_200Y101"
    entries = [it for c in cards for m in c["matched"] for it in m["items"]
               if table in it["tables"] and it["name"] == "총고정자본형성"]
    assert len({(it["parent"], it["unit"]) for it in entries}) >= 2  # two values of one name on that table
    by_name = lk.resolve_item(table, "총고정자본형성")
    assert by_name["resolution"] == "name_only_several_values" and len(by_name["item_ids"]) >= 2
    growth = next(it for it in entries if it["parent"] == "지출항목별 증감률(실질)")
    by_shown = lk.resolve_item(table, shown_value(growth))
    assert by_shown["resolution"] == "shown_text" and len(by_shown["item_ids"]) == 1
    assert shown_value(growth) == "총고정자본형성 ‹상위 지출항목별 증감률(실질) / 단위 %›"


# ---------------------------------------------------------------- the fixed ask measures


def std_ask():
    return {"case_id": "x", "part": "new24", "question": "", "statistics": {"count": None, "status": "unconfirmed"},
            "groups": [[RATE_NEW]], "representative": {"confirmed_groups": [[RATE_NEW]], "confirmed_equivalents": {},
                                                        "proposed_equivalents": {}, "status": "proposed"},
            "ask": {"expected": "required", "axis": "loan_basis", "options": None, "tables": [RATE_NEW, RATE_BAL]},
            "new_policy": {"status": "same_as_source", "differs": {}},
            "final": {"status": "unconfirmed", "period": None, "periods": None, "frequency": None, "basis": [],
                      "groups": [[RATE_NEW]]}}


def measured(layer, views, out, contract=OLD):
    cards = cards_of(LOAN_OPEN, layer)
    text = json.dumps(out(cards), ensure_ascii=False)
    if contract == OLD:
        state = run(LOAN_OPEN, layer, lambda p, r: text, K.Settings("fake"), AS_OF)
        rec = S.adapt(state)
    else:
        parsed = K.parse_output(text, cards, answer_contract=contract)
        rec = S.adapt({"question": LOAN_OPEN, "as_of": AS_OF, "cards": cards, "run_info": {"versions": {}},
                       "record": CH.check(LOAN_OPEN, cards, parsed, views, AS_OF)})
    return S.ask_measures(rec, std_ask(), {t: v.title for t, v in views.items()})


def test_ask_measures_right_ask(layer, views):
    m = measured(layer, views, lambda c: loan_ask(c, GOOD))
    assert (m["ask_missing"], m["ask_unneeded"], m["option_meaning_errors"], m["option_evidence_errors"],
            m["held_statistics"]) == ([], [], [], [], [])


def test_ask_measures_missing_ask_not_held(layer, views):
    def choice(cards):
        o = loan_ask(cards, GOOD)
        o["asks"] = []
        o["choices"] = [{"for": ["s1"], "table": ref_of(cards, RATE_NEW),
                         "evidence": [{"kind": "title", "text": "예금은행 대출금리"}]}]
        return o
    m = measured(layer, views, choice)
    # 07:19 UTC: the choice passes its own checks; the missing ask shows in the measure, not as a hold
    assert m["ask_missing"] == [["question", "loan_basis"]] and m["held_statistics"] == []


def test_ask_measures_wrong_table_for_a_value_and_bad_evidence(layer, views):
    def swapped(cards):
        o = loan_ask(cards, [GOOD[0], [{"kind": "item", "text": "없는 항목"}]], values=("잔액", "신규취급액"))
        return o
    m = measured(layer, views, swapped)
    assert sorted(e[2] for e in m["option_meaning_errors"]) == ["table_not_allowed_for_value"] * 2
    assert m["option_evidence_errors"] == [["s1", "loan_basis", "신규취급액", ["없는 항목"]]]


def test_ask_measures_not_measurable_for_a_contract_2_answer(layer, views):
    m = measured(layer, views, lambda c: loan_ask(c), contract="tl-contract-2026-10-28.2")
    assert m["option_evidence_errors"] == "not_measurable" and m["ask_missing"] == []
