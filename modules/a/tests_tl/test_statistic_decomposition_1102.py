"""The existing contract can keep separate comparison results separate without weakening required conditions.

These are made-up judgments against real catalog cards, not model accuracy tests. The fixed answers name contract
.6 explicitly so replay of their original shape remains meaningful if a later contract adds output fields.
"""

from __future__ import annotations

import copy
import json

import pytest

from claview_a import check as CH
from claview_a import contract as K
from claview_a.cards import build_cards
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

ANSWER_CONTRACT = "tl-contract-2026-10-28.6"
AS_OF = "2026-10-29"
LOAN = "예금은행 대출금리"
DEPOSIT = "예금은행 수신금리"
SEPARATE = (
    "2023년 월별 예금은행 대출금리에서 가계대출을 신규취급액 기준과 잔액 기준으로 각각 비교하고, "
    "예금은행 수신금리도 보여줘."
)
BASIS_QUOTES = {"신규취급액": "신규취급액 기준", "잔액기준": "잔액 기준"}
YEAR = [{"start": "2023-01", "end": "2023-12"}]


@pytest.fixture(scope="module")
def catalog(layer):
    cards = build_cards(SEPARATE, build_pool(SEPARATE, layer), layer)
    return cards, selection_catalog(layer).views


def table(cards, name, basis):
    card = next(c for c in cards if c["name"] == name)
    return next(r["table_id"] for r in card["tables"] if r["basis"].get("loan_basis") == [basis])


def empty():
    return {k: [] for k in (
        "statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks", "missing",
        "alternatives", "aggregations", "changes",
    )}


def add_stat(out, cards, sid, quote, name, card_name, basis):
    out["statistics"].append({"id": sid, "quote": quote, "name": name})
    out["choices"].append({"for": [sid], "table": table(cards, card_name, basis),
                           "evidence": [{"kind": "title", "text": card_name}]})


def basis_row(sid, value, polarity="required"):
    return {"for": [sid], "quote": BASIS_QUOTES[value], "axis": "loan_basis", "value": value,
            "polarity": polarity}


def time_rows(out):
    ids = [s["id"] for s in out["statistics"]]
    out["periods"] = [{"for": ids, "quote": "2023년", "meaning": "range", "refers_to": []}]
    out["frequencies"] = [{"for": ids, "quote": "월별"}]
    return out


def check(catalog, question, out):
    cards, views = catalog
    parsed = K.parse_output(json.dumps(out, ensure_ascii=False), cards, ANSWER_CONTRACT)
    record = CH.check(question, cards, parsed, views, AS_OF)
    return {d["statistic"]: d for d in record["decisions"]}


def separate(cards, with_deposit=False):
    out = empty()
    for sid, value in (("s1", "신규취급액"), ("s2", "잔액기준")):
        add_stat(out, cards, sid, LOAN, f"가계대출 금리 {value}", LOAN, value)
        out["splitters"].append(basis_row(sid, value))
    if with_deposit:
        add_stat(out, cards, "s3", DEPOSIT, DEPOSIT, DEPOSIT, "신규취급액")
    return time_rows(out)


def test_separate_basis_results_keep_two_statistics_and_the_shared_time(catalog):
    cards, _ = catalog
    out = separate(cards)
    got = check(catalog, SEPARATE, out)
    assert set(got) == {"s1", "s2"}
    for sid, value in (("s1", "신규취급액"), ("s2", "잔액기준")):
        d = got[sid]
        assert d["outcome"] == "selected" and not d["reasons"] and not d["asks"]
        assert d["basis"]["loan_basis"]["value"] == [value]
        assert [(r["table_id"], r["frequency"], r["period"]) for r in d["final_tables"]] == [
            (table(cards, LOAN, value), "M", YEAR),
        ]
    # The same contiguous indicator quote is legal; its attached conditions distinguish the two results.
    assert out["statistics"][0]["quote"] == out["statistics"][1]["quote"] == LOAN


def test_multiple_items_on_one_table_need_no_artificial_table_split(catalog):
    cards, _ = catalog
    question = "2023년 월별 예금은행 대출금리를 신규취급액 기준으로 가계대출과 기업대출로 나눠 보여줘."
    out = empty()
    out["statistics"] = [{"id": sid, "quote": word, "name": f"{word} 금리"}
                         for sid, word in (("s1", "가계대출"), ("s2", "기업대출"))]
    out["choices"] = [{"for": ["s1", "s2"], "table": table(cards, LOAN, "신규취급액"),
                       "evidence": [{"kind": "title", "text": LOAN}]}]
    out["splitters"] = [basis_row(sid, "신규취급액") for sid in ("s1", "s2")]
    got = check(catalog, question, time_rows(out))
    assert all(d["outcome"] == "selected" and not d["asks"] for d in got.values())
    assert {r["table_id"] for d in got.values() for r in d["final_tables"]} == {
        table(cards, LOAN, "신규취급액"),
    }


def combined(cards, polarity="required"):
    out = empty()
    add_stat(out, cards, "s1", LOAN, LOAN, LOAN, "신규취급액")
    out["splitters"] = [basis_row("s1", value, polarity) for value in BASIS_QUOTES]
    return time_rows(out)


def test_two_required_values_on_one_result_are_not_silently_split_or_reduced(catalog):
    cards, _ = catalog
    question = "2023년 월별 예금은행 대출금리 한 결과에 신규취급액 기준과 잔액 기준을 모두 적용해줘."
    out = combined(cards)
    before = copy.deepcopy(out)
    got = check(catalog, question, out)
    assert out == before  # The checker does not reinterpret the model's semantic decision.
    assert set(got) == {"s1"} and got["s1"]["outcome"] == "failed"
    assert got["s1"]["final_tables"] == []
    assert [r["code"] for r in got["s1"]["reasons"]] == ["basis_combination_unsupported"]
    assert got["s1"]["reasons"][0]["detail"]["required"] == ["신규취급액", "잔액기준"]


@pytest.mark.parametrize("selected", ["신규취급액", "잔액기준"])
def test_either_basis_is_still_one_result_not_two_required_results(catalog, selected):
    cards, _ = catalog
    question = "2023년 월별 예금은행 대출금리는 신규취급액 기준과 잔액 기준 중 어느 하나면 돼."
    out = combined(cards, "any_of")
    out["choices"][0]["table"] = table(cards, LOAN, selected)
    got = check(catalog, question, out)
    assert set(got) == {"s1"} and got["s1"]["outcome"] == "selected"
    assert got["s1"]["basis"]["loan_basis"]["value"] == [selected]
    assert not got["s1"]["asks"] and not got["s1"]["reasons"]


def test_a_badly_combined_result_does_not_change_an_unrelated_statistic(catalog):
    cards, _ = catalog
    correct = separate(cards, with_deposit=True)
    expected = check(catalog, SEPARATE, correct)["s3"]
    bad = combined(cards)
    add_stat(bad, cards, "s3", DEPOSIT, DEPOSIT, DEPOSIT, "신규취급액")
    got = check(catalog, SEPARATE, time_rows(bad))
    assert got["s1"]["outcome"] == "failed"
    assert got["s3"]["outcome"] == "selected"
    for key in ("quote", "name", "basis", "model_evidence", "final_tables", "asks", "reasons"):
        assert got["s3"][key] == expected[key], key
