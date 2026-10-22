"""Frequency and period decided in code: stated years, default frequency, alternatives.

2026-10-13: B computes the period of a question with stated years itself (like a relative one),
picks the longest frequency when the question names none and the table has several, and offers
the other frequencies as `frequency_alternatives` (same series, other period). The 22-case live
run showed the model writing 202410 for "2024년" (CV-AB-16), skipping the comparison start
(CV-AB-18) and choosing annual vs monthly differently from the golden (CV-AB-20).
"""

from __future__ import annotations

import copy

import pytest

from claview.contracts import StageFailure, Trace
from claview.evaluation import scripted_answers
from claview.graphs import build_b_graph
from claview.models import ScriptedModel
from claview.stages import (
    Services,
    decide_frequency,
    resolve_explicit_period,
    selection_key,
)


def _table(store, table_id):
    return store.get("301", table_id)


# ---- decide_frequency --------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024년 수출 공산품 가격이 전년보다 얼마나 올랐는지 비교해줘", ("Y", ["Q", "M"])),
        ("2024년 수출 공산품 가격을 월별로 보여줘", ("M", [])),
        ("2024년 수출 공산품 가격을 분기별로 보여줘", ("Q", [])),
        ("2024년 수출 공산품 가격을 연간으로 보여줘", ("Y", [])),
        ("2024년 어느 달에 가격이 가장 컸는지 알려줘", ("M", [])),
        ("2024년 달러 기준 수출 가격을 보여줘", ("Y", ["Q", "M"])),  # 달러 is not a month cue
    ],
)
def test_default_frequency_is_the_longest_unless_the_question_states_one(store, query, expected):
    assert decide_frequency(query, _table(store, "DT_402Y014")) == expected


def test_a_table_with_one_frequency_has_no_alternatives(store):
    table = _table(store, "DT_402Y014")
    only_year = {**table, "periods": {"Y": table["periods"]["Y"]}}
    assert decide_frequency("2024년 수출 가격 추이", only_year) == ("Y", [])


def test_quarter_and_year_table_defaults_to_year(store):
    table = _table(store, "DT_151Y001")  # 가계신용: Q and Y
    assert decide_frequency("2020년 가계신용 추이", table) == ("Y", ["Q"])


# ---- resolve_explicit_period --------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "freq", "expected"),
    [
        ("2015~2025년 연간 실질 GDP 성장률", "Y", ("2015", "2025")),
        ("2008년 1분기부터 2024년 4분기까지 분기별 추이", "Q", ("200801", "202404")),
        ("2024년 월별 물가 비교", "M", ("202401", "202412")),
        ("2024년 3월부터 5월까지 월별 물가", "M", ("202403", "202405")),
        ("2024년 가격이 전년보다 얼마나 올랐는지", "Y", ("2023", "2024")),
        ("2024년 가격이 전년보다 얼마나 올랐는지", "M", ("202301", "202412")),
        ("2024년에 어느 달에 가장 크게 변했는지", "M", ("202312", "202412")),
    ],
)
def test_explicit_period_is_computed_from_the_question(store, query, freq, expected):
    period = resolve_explicit_period(query, _table(store, "DT_402Y014"), freq)
    assert (period["start"], period["end"]) == expected and period["frequency"] == freq


@pytest.mark.parametrize(
    "query", ["최근 3년간 물가 추이", "물가 추이를 보여줘", "2024년 수출 물가"]
)
def test_explicit_period_is_none_without_years_or_with_relative_wording(store, query):
    table = _table(store, "DT_402Y014")
    freq = "M"
    if query == "2024년 수출 물가":
        assert resolve_explicit_period(query, table, "H") is None  # H not in the table
    else:
        assert resolve_explicit_period(query, table, freq) is None


def test_explicit_period_outside_the_coverage_is_refused(store):
    with pytest.raises(StageFailure) as exc:
        resolve_explicit_period("2030년 월별 물가", _table(store, "DT_402Y014"), "M")
    assert exc.value.code == "metadata_refresh_required"


# ---- pipeline ---------------------------------------------------------------------


def _run(case, store, config, answers=None):
    services = Services(
        store,
        search=None,
        model=ScriptedModel(answers or scripted_answers(case)),
        config=config,
        trace=Trace(),
    )
    state = build_b_graph(services).invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]}, {"recursion_limit": 20}
    )
    return state["stage_outcome"]


def _case(golden_cases, case_id):
    return next(c for c in golden_cases if c["case_id"] == case_id)


def test_cv_ab_20_answers_annually_and_offers_quarterly_and_monthly(golden_cases, store, config):
    out = _run(_case(golden_cases, "CV-AB-20"), store, config)
    assert out["status"] == "ok", out["issues"]
    result = out["result"]
    assert {
        (s["period"]["frequency"], s["period"]["start"], s["period"]["end"])
        for s in result["selections"]
    } == {("Y", "2023", "2024")}
    alts = {a["frequency"]: a for a in result["frequency_alternatives"]}
    assert set(alts) == {"Q", "M"}
    assert (alts["M"]["period"]["start"], alts["M"]["period"]["end"]) == ("202301", "202412")
    assert (alts["Q"]["period"]["start"], alts["Q"]["period"]["end"]) == ("202301", "202404")
    primary_ids = [s["series_id"] for s in result["selections"]]
    for alt in alts.values():
        assert [s["series_id"] for s in alt["selections"]] == primary_ids
        assert {s["period"]["frequency"] for s in alt["selections"]} == {alt["frequency"]}
        assert {s["dimensions"]["objL1"]["code"] for s in alt["selections"]} == {
            s["dimensions"]["objL1"]["code"] for s in result["selections"]
        }


def test_alternatives_are_not_offered_when_the_question_states_the_frequency(
    golden_cases, store, config
):
    out = _run(_case(golden_cases, "CV-AB-19"), store, config)  # "월별"
    assert out["status"] == "ok" and "frequency_alternatives" not in out["result"]


@pytest.mark.parametrize(
    ("case_id", "model_start", "model_end", "want"),
    [
        ("CV-AB-16", "202401", "202410", ("202401", "202412")),  # live: end 202410
        ("CV-AB-18", "202401", "202412", ("202312", "202412")),  # live: no 202312 start
        ("CV-AB-19", "2024-01", "2024-12", ("202401", "202412")),
    ],
)
def test_the_period_the_model_wrote_does_not_matter_for_stated_years(
    golden_cases, store, config, case_id, model_start, model_end, want
):
    case = _case(golden_cases, case_id)
    answers = copy.deepcopy(scripted_answers(case))
    for batch in answers.values():
        for entry in batch:
            for row in entry.get("selections", []):
                row["period"] = {**row["period"], "start": model_start, "end": model_end}
    out = _run(case, store, config, answers)
    assert out["status"] == "ok", out["issues"]
    assert {(s["period"]["start"], s["period"]["end"]) for s in out["result"]["selections"]} == {
        want
    }


def test_relative_period_with_no_stated_frequency_uses_the_default_instead_of_stopping(
    golden_cases, store, config
):
    case = copy.deepcopy(_case(golden_cases, "CV-AB-20"))
    case["query"] = "최근 5년간 수출 공산품과 농림수산품의 가격을 원화 기준 막대그래프로 비교해줘"
    out = _run(case, store, config)
    assert out["status"] == "ok", out["issues"]
    selections = out["result"]["selections"]
    assert {s["period"]["frequency"] for s in selections} == {"Y"}
    assert {a["frequency"] for a in out["result"]["frequency_alternatives"]} == {"Q", "M"}
    monthly = next(a for a in out["result"]["frequency_alternatives"] if a["frequency"] == "M")
    # 5 years back from the table's latest month = 60 months
    start, end = monthly["period"]["start"], monthly["period"]["end"]
    assert (int(end[:4]) * 12 + int(end[4:])) - (int(start[:4]) * 12 + int(start[4:])) == 59


def test_every_golden_case_still_matches_its_golden_series(golden_cases, store, config):
    for case in golden_cases:
        out = _run(case, store, config)
        assert out["status"] == "ok", (case["case_id"], out["issues"])
        got = sorted(selection_key(s) for s in out["result"]["selections"])
        want = sorted(
            selection_key(
                {**s, "dimensions": {a: {"code": v["code"]} for a, v in s["dimensions"].items()}}
            )
            for s in case["expected"]["B"]["selections"]
        )
        assert got == want, case["case_id"]


# ---- what the model is shown ----------------------------------------------------------


def _model_context(case, store, config):
    """The context B hands to the model for the first table of a golden case."""
    from claview.stages import BSteps

    steps = BSteps(
        Services(store, search=None, model=ScriptedModel({}), config=config, trace=Trace())
    )
    state = {"query": case["query"], "a_result": case["expected"]["A"]}
    state.update(steps.load(state))
    state.update(steps.context(state))
    return state["selection_context"], state["resolved_period"]


def test_a_period_stated_in_years_is_not_sent_to_the_model(golden_cases, store, config):
    """CV-AB-04: with `resolved_period` in the context HCX picked 14 values instead of 5 (3/3)."""
    for case_id in ("CV-AB-04", "CV-AB-16", "CV-AB-19"):
        context, resolved = _model_context(_case(golden_cases, case_id), store, config)
        assert context["resolved_period"] is None, case_id
        assert resolved is not None, case_id  # the code still fixes it for the answer


def test_a_relative_period_is_still_sent_to_the_model(golden_cases, store, config):
    for case_id in ("CV-AB-12", "CV-AB-15"):
        context, resolved = _model_context(_case(golden_cases, case_id), store, config)
        assert context["resolved_period"] == resolved and resolved is not None, case_id
