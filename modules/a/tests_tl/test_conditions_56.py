"""Plan 56 order 5 (bundle 4, policy ②④): a missing period or frequency is asked, never defaulted.

- The frequency first: a quoted one; else the one offered frequency that fits the requested periods (only_valid);
  none fits: ``frequency_not_fit``; several fit: a frequency question, the period kept and never asked.
- No period: a period question (cause ``user_omitted``) with the presets' actual table windows; '최근 3년'
  and '2020년 이후' are not asked.
- A period or frequency word outside the quotes is restored when the question has one statistic; with several its
  statistic is not known (``extraction_missing``, nothing asked).
- Answers (an option or a typed period) are read as quotes and the period checks run again; an unreadable text is
  refused before anything changes.
- The condition rule ``resolver_default`` gives the rule before (comparison only).

Pairs through the straight graph on the real catalog (fake model, no network) and on synthetic tables. Tables are
found by card name and title basis, never by ID.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from types import SimpleNamespace

import pytest
from conftest import fill_option_evidence  # noqa: E402

from claview_a import answers as AN
from claview_a import check as CH
from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.periods import resolver as R
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

AS_OF = "2026-10-22"
M2 = "M2 상품별 구성내역"


def question(period: str = "", word: str = "", second: bool = False) -> str:
    head = " ".join(x for x in (period, word) if x)
    tail = "과 M2 평균 잔액" if second else ""
    return f"{head + ' ' if head else ''}M2 총액{tail}을 평잔·원계열 기준으로 보여줘"


def out_for(cards, layer, periods=(), frequencies=(), stats=("s1",)) -> dict:
    card = next(c for c in cards if c["name"] == M2)
    views = selection_catalog(layer).views
    rows = [r for r in card["tables"]
            if r["basis"].get("stock") == ["평잔"] and r["basis"].get("seasonal") == ["원계열"]]
    rows.sort(key=lambda r: PR._end_key(views[r["table_id"]]), reverse=True)
    table = f"{card['card_id']}.{table_numbers(card)[rows[0]['table_id']]}"
    out = {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                           "missing", "alternatives")}
    out["statistics"] = [{"id": sid, "quote": "M2 총액", "name": f"M2 총액 {sid}"} for sid in stats]
    out["splitters"] = [{"quote": "평잔", "axis": "stock", "for": list(stats)},
                        {"quote": "원계열", "axis": "seasonal", "for": list(stats)}]
    out["periods"] = [{"quote": p, "for": list(stats)} for p in periods]
    out["frequencies"] = [{"quote": f, "for": list(stats)} for f in frequencies]
    out["choices"] = [{"for": [sid], "table": table, "evidence": [{"kind": "item", "text": "M2(평잔 원계열)"}]}
                      for sid in stats]
    return out


def go(q: str, layer, **kw) -> tuple[dict, dict]:
    cards = build_cards(q, build_pool(q, layer), layer)
    out = fill_option_evidence(out_for(cards, layer, **kw), cards)
    state = run(q, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}


def reply(state, q, option=1, text=None):
    r = {"question_id": q["question_id"], "question_version": AN.question_version(state)}
    return {**r, "text": text} if text is not None else {**r, "option_id": q["options"][option - 1]["option_id"]}


# ---------------------------------------------------------------- no period: asked, never a default window


def test_no_period_is_asked_with_the_catalog_windows_and_nothing_is_handed_on(layer):
    state, d = go(question(word="연간으로"), layer, frequencies=["연간"])
    s = d["s1"]
    assert s["outcome"] == "ask_condition" and state["action"] == "ask" and s["final_tables"] == []
    assert s["period"]["default_applied"] is False and s["period"]["cause"] == "user_omitted"
    (ask,) = s["condition_asks"]
    assert ask["kind"] == "period" and ask["statistics"] == ["s1"] and ask["input"] == "choice_or_text"
    presets = {o["quote"]: o["range"] for o in PR.period_options(AS_OF)}
    # A recent preset has no dates before its table/frequency is known (11-02 policy); the offered option does.
    assert presets["최근 3년"] is None
    assert ask["options"] and all(o["range"] == o["ranges"]["Y"] for o in ask["options"])
    assert next(o for o in ask["options"] if o["value"] == "최근 3년")["range"] == {
        "start": "2023-01", "end": "2025-12"}


@pytest.mark.parametrize("period", ["최근 3년", "2020년 이후"])
def test_a_period_with_its_meaning_is_not_asked(layer, period):
    _, d = go(question(period, "연간으로"), layer, periods=[period], frequencies=["연간"])
    s = d["s1"]
    assert s["outcome"] == "selected" and not s["condition_asks"] and s["period"]["default_applied"] is False
    assert s["frequency"]["reason"] == "confirmed_quote" and [r["frequency"] for r in s["final_tables"]] == ["Y"]


# ---------------------------------------------------------------- the frequency


def test_several_valid_frequencies_ask_the_frequency_only_and_keep_the_period(layer):
    _, d = go(question("2023년"), layer, periods=["2023년"])
    s = d["s1"]
    assert s["outcome"] == "ask_condition" and [a["kind"] for a in s["condition_asks"]] == ["frequency"]
    assert s["frequency"]["reason"] == "needs_answer" and s["frequency"]["value"] is None
    assert [o["value"] for o in s["condition_asks"][0]["options"]] == s["frequency"]["valid"]
    assert len(s["frequency"]["valid"]) > 1 and "cause" not in s["period"]


def test_a_month_span_leaves_one_valid_frequency_and_is_not_asked(layer):
    _, d = go(question("2024년 1~5월"), layer, periods=["2024년 1~5월"])
    s = d["s1"]
    assert s["outcome"] == "selected" and s["frequency"] == {**s["frequency"], "value": "M", "reason": "only_valid"}
    assert [r["frequency"] for r in s["final_tables"]] == ["M"]


def _decision(views: dict, quote: str) -> dict:
    reading = PR.read_quotes([quote], [])
    return CH._frequency_decision(reading, list(views), SimpleNamespace(views=views), AS_OF)


def test_a_quarter_on_a_yearly_table_does_not_fit_and_is_never_switched():
    got = _decision({"T": SimpleNamespace(table_id="T", periods=[("Y", "2000", "2025")])}, "2024년 3분기")
    assert (got["reason"], got["value"], got["valid"]) == ("not_fit", None, [])
    got = _decision({"T": SimpleNamespace(table_id="T", periods=[("Y", "2000", "2025"), ("Q", "2000Q1", "2026Q2")])},
                    "2024년 3분기")
    assert (got["reason"], got["value"]) == ("only_valid", "Q")


@pytest.mark.parametrize("quote,fits", [("2024년 1~5월", ["M"]), ("2024년 1분기", ["Q", "M"]),
                                        ("최근 6개월", ["H", "Q", "M"]), ("2020년 이후", ["Y", "H", "Q", "M"])])
def test_frequency_fit_reads_the_start_and_a_closed_end(quote, fits):
    # Six data-relative months fit one full half-year or two full quarters ending at that frequency's latest point.
    assert PR.valid_frequencies(PR.read_quotes([quote], [])["conditions"], ["Y", "H", "Q", "M"], AS_OF) == fits


# ---------------------------------------------------------------- quotes the model left out


def test_a_period_and_frequency_outside_the_quotes_are_restored_for_one_statistic(layer):
    _, d = go(question("2024년 1~3월", "월별로"), layer)
    s = d["s1"]
    assert s["outcome"] == "selected", s["reasons"]
    assert s["period_quote_missing"]["restored"] is True and s["frequency_quote_missing"]["restored"] is True
    assert [r["frequency"] for r in s["final_tables"]] == ["M"]
    assert [q["source"] for q in s["frequency_quotes"]] == ["code_restored"]


def test_a_period_outside_the_quotes_of_two_statistics_is_extraction_missing_and_not_asked(layer):
    state, d = go(question("2024년", "연간으로", second=True), layer, frequencies=["연간"], stats=("s1", "s2"))
    for s in d.values():
        assert s["outcome"] == "failed" and s["period"]["cause"] == "extraction_missing"
        assert [r["code"] for r in s["reasons"]] == ["period_quote_missing"] and not s["condition_asks"]
    assert AN.questions(state) == []


def test_a_period_quoted_for_one_statistic_only_leaves_the_other_unknown(layer):
    q = question("2024년", "연간으로", second=True)
    cards = build_cards(q, build_pool(q, layer), layer)
    out = fill_option_evidence(out_for(cards, layer, frequencies=["연간"], stats=("s1", "s2")), cards)
    out["periods"] = [{"quote": "2024년", "for": ["s1"]}]
    state = run(q, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    d = {x["statistic"]: x for x in state["record"]["decisions"]}
    assert d["s1"]["outcome"] == "selected", d["s1"]["reasons"]
    assert d["s2"]["period"]["cause"] == "extraction_missing" and not d["s2"]["condition_asks"]
    assert d["s2"]["period_quote_missing"]["quoted_for_other_statistics"] is True


def test_statistics_sharing_a_quoted_frequency_are_asked_their_period_once(layer):
    q = "M2 총액과 M2 평균 잔액을 평잔·원계열 기준으로 연간으로 보여줘"
    cards = build_cards(q, build_pool(q, layer), layer)
    out = fill_option_evidence(out_for(cards, layer, frequencies=["연간"], stats=("s1", "s2")), cards)
    state = run(q, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    qs = AN.questions(state)
    assert [(x["kind"], x["statistics"]) for x in qs] == [("period", ["s1", "s2"])]


# ---------------------------------------------------------------- answers


def test_a_period_answer_decides_the_statistic_with_the_option_range(layer):
    state, _ = go(question(word="연간으로"), layer, frequencies=["연간"])
    (q,) = AN.questions(state)
    before = copy.deepcopy(state)
    new = AN.apply(state, [reply(state, q, 1)], layer)
    assert state == before
    s = new["record"]["decisions"][0]
    assert s["outcome"] == "selected" and new["action"] == "handoff", s["reasons"]
    assert s["condition_answers"][0]["quote"] == q["options"][0]["quote"]
    assert s["period"]["default_applied"] is False and [r["frequency"] for r in s["final_tables"]] == ["Y"]
    assert AN.questions(new) == []


def test_a_frequency_answer_keeps_the_period(layer):
    state, _ = go(question("2023년"), layer, periods=["2023년"])
    (q,) = AN.questions(state)
    pick = next(i for i, o in enumerate(q["options"], start=1) if o["value"] == "Q")
    new = AN.apply(state, [reply(state, q, pick)], layer)
    s = new["record"]["decisions"][0]
    assert s["outcome"] == "selected" and [r["frequency"] for r in s["final_tables"]] == ["Q"]
    assert all("2023" in json.dumps(r["period"]) for r in s["final_tables"])


def test_a_typed_period_is_read_and_an_unreadable_one_is_refused(layer):
    state, _ = go(question(word="연간으로"), layer, frequencies=["연간"])
    (q,) = AN.questions(state)
    before = copy.deepcopy(state)
    with pytest.raises(AN.AnswerError) as e:
        AN.apply(state, [reply(state, q, text="팬데믹 이전")], layer)
    assert e.value.code == "answer_unreadable" and state == before
    new = AN.apply(state, [reply(state, q, text="2020년부터 2022년까지")], layer)
    s = new["record"]["decisions"][0]
    assert s["outcome"] == "selected" and all("2020" in json.dumps(r["period"]) for r in s["final_tables"])
    with pytest.raises(AN.AnswerError) as e:  # the same answer again: no longer pending
        AN.apply(new, [reply(new, q, text="2020년부터 2022년까지")], layer)
    assert e.value.code in ("question_not_pending", "stale_question")


# ---------------------------------------------------------------- the rule before, for comparison only


def test_the_resolver_default_rule_gives_the_rule_before(layer, monkeypatch):
    monkeypatch.setattr(R, "_POLICY", dataclasses.replace(R.default_policy(), condition_rule="resolver_default"))
    _, d = go(question(word="연간으로"), layer, frequencies=["연간"])
    s = d["s1"]
    assert s["outcome"] == "selected" and s["period"]["default_applied"] is True and "frequency" not in s


def test_the_packaged_policy_asks():
    assert R.default_policy().condition_rule == "ask_condition"
