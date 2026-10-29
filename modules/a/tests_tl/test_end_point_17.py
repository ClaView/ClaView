"""Plan 36 follow-up (user 2026-10-28 20:24 UTC): an end word quoted in ``frequencies`` keeps its frequency and end
point with the statistics it is for; one quoted only in another field is never moved into a frequency by code (and
the question is never scanned again), it is diagnosed by the evaluator. Normal/error pairs through the straight graph
(fake model, no network) and the golden scorer's condition diagnosis (.17) on made-up records. The parser's own
boundaries (월평균, 월말잔액, 7월말, 2024년 말, 지난 월말) are in ``test_month_end_36``."""

from __future__ import annotations

import copy
import json

import pytest

from conftest import fill_option_evidence  # noqa: E402
from test_indep_38 import final, output, record, row  # noqa: F401 (shared made-up fixtures)
from test_score_split_16 import old_case, score

from claview_a import periods as PR
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import run
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog
from tl_eval import score as S

AS_OF = "2026-10-22"
M2 = "M2 상품별 구성내역"


def m2_question(period: str, word: str = "") -> str:
    return f"{period} {word + ' ' if word else ''}M2 총액을 평잔·원계열 기준으로 보여줘"


def m2_out(cards, layer, periods=(), frequencies=(), splitters=(), stats=("s1",)) -> dict:
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
                        {"quote": "원계열", "axis": "seasonal", "for": list(stats)}, *splitters]
    out["periods"] = [{"quote": p, "for": list(stats)} for p in periods]
    out["frequencies"] = list(frequencies)
    out["choices"] = [{"for": [sid], "table": table, "evidence": [{"kind": "item", "text": "M2(평잔 원계열)"}]}
                      for sid in stats]
    return out


def go(question: str, layer, **kw) -> tuple[dict, dict, dict]:
    cards = build_cards(question, build_pool(question, layer), layer)
    out = fill_option_evidence(m2_out(cards, layer, **kw), cards)
    sent = copy.deepcopy(out)
    state = run(question, layer, lambda payload, request: json.dumps(out, ensure_ascii=False), Settings("fake"), AS_OF)
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}, sent


# ---------------------------------------------------------------- path: an end word quoted as a frequency


@pytest.mark.parametrize("word, period, frequency, outcome", [
    ("월말", "2024년 1~3월", "M", "selected"), ("분기말", "2024년", "Q", "selected"),
    ("연말", "2020~2024년", "Y", "selected"),
    ("반기말", "2024년", "H", "failed"),  # the M2 tables offer no half-year: the statistic stops, the record stays
])
def test_an_end_word_in_frequencies_keeps_its_frequency_and_end_point(layer, word, period, frequency, outcome):
    state, d, _ = go(m2_question(period, word), layer, periods=[period], frequencies=[{"quote": word, "for": ["s1"]}])
    assert d["s1"]["frequency_quotes"] == [{"quote": word, "for": ["s1"], "frequency": frequency,
                                            "point_in_period": "end"}]
    assert d["s1"]["outcome"] == outcome
    if outcome == "selected":
        assert {r["frequency"] for r in d["s1"]["final_tables"]} == {frequency}
    else:
        assert [r["code"] for r in d["s1"]["reasons"]] == ["frequency_not_offered"]
    assert d["s1"]["basis"]["stock"]["value"] == ["평잔"]  # the end point chooses no basis (말잔/평잔)


@pytest.mark.parametrize("where", ["periods", "splitters"])
def test_an_end_word_quoted_only_in_another_field_is_not_moved_into_a_frequency(layer, where):
    q = m2_question("2020~2024년", "연말")
    kw = ({"periods": ["2020~2024년 연말"]} if where == "periods" else
          {"periods": ["2020~2024년"], "splitters": [{"quote": "연말", "axis": "other", "for": ["s1"]}]})
    state, d, sent = go(q, layer, **kw)
    assert d["s1"]["frequency_quotes"] == []  # no end point is made up from another field or from the question
    assert state["record"]["model_output"]["frequencies"] == sent["frequencies"] == []
    assert d["s1"]["basis"]["stock"]["value"] == ["평잔"]
    if where == "periods":  # the period reader leaves the word as ignored, on the record only
        assert [r["ignored"] for r in d["s1"]["period"]["readings"]] == [["연말"]]


def test_an_end_word_for_some_statistics_is_not_spread_to_the_others(layer):
    q = m2_question("2020~2024년", "연말")
    _, d, _ = go(q, layer, periods=["2020~2024년"], frequencies=[{"quote": "연말", "for": ["s1"]}], stats=("s1", "s2"))
    assert d["s1"]["frequency_quotes"] == [{"quote": "연말", "for": ["s1"], "frequency": "Y",
                                            "point_in_period": "end"}]
    assert d["s2"]["frequency_quotes"] == []


def test_a_question_without_a_frequency_records_none_and_does_not_stop(layer):
    _, d, _ = go(m2_question("2020~2024년"), layer, periods=["2020~2024년"])
    assert d["s1"]["frequency_quotes"] == [] and d["s1"]["outcome"] == "selected"


def test_an_end_word_not_in_the_question_still_fails_the_quote_check(layer):
    _, d, _ = go(m2_question("2020~2024년"), layer, periods=["2020~2024년"],
                 frequencies=[{"quote": "연말", "for": ["s1"]}])
    assert d["s1"]["outcome"] == "failed" and "quote_not_in_question" in [r["code"] for r in d["s1"]["reasons"]]


def test_an_unknown_statistic_in_a_frequency_still_breaks_the_contract(layer):
    state, _, _ = go(m2_question("2020~2024년", "연말"), layer, periods=["2020~2024년"],
                     frequencies=[{"quote": "연말", "for": ["s9"]}])
    assert state["record"]["error"]["code"] == "statistic_ref"


# ---------------------------------------------------------------- evaluator: condition diagnosis (.17)

YEAR_END = "2020~2024년 연말 가상지표를 보여 줘"


def end_record(frequencies=(), periods=(), splitters=(), points=(), question=YEAR_END, rows=None):
    m = output([("s1", "가상지표")], [("s1", "DT_X01")])
    m["frequencies"], m["periods"], m["splitters"] = list(frequencies), list(periods), list(splitters)
    return record(m, {"s1": final(tables=rows or [row("DT_X01", "2020-12", "2024-12", "Y")], quotes=points)},
                  question=question)


def f(quote):
    return {"quote": quote, "for": ["s1"]}


KEPT = [{"quote": "연말", "for": ["s1"], "frequency": "Y", "point_in_period": "end"}]


def test_an_end_word_quoted_as_a_frequency_and_kept_is_quoted():
    rec = end_record([f("연말")], [f("2020~2024년")], points=KEPT)
    assert S.condition_quotes(rec) == {"frequency": "quoted", "period": "quoted"}


def test_an_end_word_quoted_as_a_frequency_whose_end_point_was_lost_is_not_kept():
    lost = [{**KEPT[0], "point_in_period": None}]
    assert S.condition_quotes(end_record([f("연말")], [f("2020~2024년")], points=lost))["frequency"] == "not_kept"


@pytest.mark.parametrize("fields", [{"periods": [f("2020~2024년 연말")]},
                                    {"periods": [f("2020~2024년")], "splitters": [{**f("연말"), "axis": "other"}]}])
def test_an_end_word_quoted_only_in_another_field_is_other_field_not_missing(fields):
    assert S.condition_quotes(end_record(**fields))["frequency"] == "other_field"


def test_an_end_word_quoted_nowhere_is_missing():
    assert S.condition_quotes(end_record(periods=[f("2020~2024년")]))["frequency"] == "missing"


def test_a_question_without_a_frequency_is_not_stated_even_with_empty_frequencies():
    rec = end_record(periods=[f("최근 10년간")], question="최근 10년간 가상지표를 지역별로 비교해 줘")
    assert S.condition_quotes(rec) == {"frequency": "not_stated", "period": "quoted"}


@pytest.mark.parametrize("question", ["2024년 가상지표 월평균을 보여 줘", "2024년 가상지표 월말잔액을 보여 줘",
                                      "2024년 7월말 가상지표를 보여 줘", "2024년 말 가상지표를 보여 줘",
                                      "지난 월말 가상지표를 보여 줘"])
def test_a_point_a_calculation_or_a_basis_word_is_no_stated_frequency(question):
    assert S._stated(question)["frequency"] == [] and S._stated(question)["end"] == []


def test_a_base_year_mark_is_no_stated_period():
    assert S._stated("가상지표를 2020=100 기준으로 월별로 보여 줘")["period"] == []


MONTHLY = "2024년 가상지표 월별 추이를 보여 줘"


def short_final(**fields):
    m = output([("s1", "가상지표")], [("s1", "DT_X01")])
    m["periods"] = [f("2024년")]
    for k, v in fields.items():
        m[k] = v
    quotes = [{"quote": "월별", "for": ["s1"], "frequency": "M", "point_in_period": None}] if m["frequencies"] else []
    return record(m, {"s1": final(tables=[row("DT_X01", "2024-01", "2024-06")], quotes=quotes)}, question=MONTHLY)


def test_a_worse_final_after_a_clean_model_is_put_on_the_code():
    res = score(old_case([["DT_X01"]], question=MONTHLY), short_final(frequencies=[f("월별")]))
    assert res["extraction"]["frequency"] == "quoted" and res["final_full"]["right"] is False
    assert "code_correction_error" in res["labels"] and "model_extraction_missing" not in res["labels"]


def test_a_worse_final_after_a_condition_in_another_field_is_not_put_on_the_code():
    res = score(old_case([["DT_X01"]], question=MONTHLY), short_final(splitters=[{**f("월별"), "axis": "other"}]))
    assert res["extraction"]["frequency"] == "other_field" and res["final_full"]["right"] is False
    assert "condition_in_other_field" in res["labels"]
    assert "code_correction_error" not in res["labels"] and "model_extraction_missing" not in res["labels"]


def test_the_prompt_asks_for_an_end_word_in_frequencies_and_no_made_up_frequency():
    from claview_a.contract import prompt_text

    text = prompt_text()
    assert "월말·분기말·반기말·연말처럼 주기와 끝 시점을 함께 나타내는 표현" in text
    assert "그것이 주기 인용을 대신하지는 않는다" in text and "원문에 없는 주기를 만들어 인용하지 않는다" in text


def test_empty_frequencies_on_a_question_without_a_frequency_are_no_extraction_miss():
    """CV-AB-10 kind: the standard has a frequency by the default policy, the question states none."""
    rec = record(output([("s1", "가상지표")], [("s1", "DT_X01")]), {"s1": final(tables=[row("DT_X01", "2024-01",
                                                                                              "2024-12")])},
                 question="2024년 가상지표 추이를 보여 줘")
    rec["model"]["periods"] = [f("2024년")]
    res = score(old_case([["DT_X01"]], question="2024년 가상지표 추이를 보여 줘"), rec)
    assert res["extraction"] == {"frequency": "not_stated", "period": "quoted"}
    assert "model_extraction_missing" not in res["labels"]
