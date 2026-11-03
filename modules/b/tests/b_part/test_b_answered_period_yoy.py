"""B-1 (an answered period reaches the model) and B-2 ("전년 동월 대비" reads the year before as a calculation range).

B-1: the period a user answered (or A quoted) is not in the question, so the model was shown a question without any
period and returned no selection (FN-PERIOD-ASK). `resolved_period` is now shown for such a period; the years a
question states stay hidden (CV-AB-04), and the code still applies and validates the period after the selection.
B-2: "전년 동월 대비 / 전년동월비 / 전년 같은 달보다" were not read as year-over-year wording, so only the requested
year was fetched. The requested range stays the shown range; the periods before it are `calculation_extra`
(purpose `yoy_baseline`), read in one continuous range.

Cases: `eval/cases/b/period/answered_period_yoy_b.json` (expected values fixed by the rules before the code was
changed). The real BService runs over the synthetic model (wrapped to record what it is sent) and the synthetic
fetch client: wiring and period rules only, no external call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.b_part.test_b_table_period_context import request_tables

from claview_b.stages import YEAR_OVER_YEAR, detect_baseline_extension, period_not_in_question
from claview_b.synthetic import SyntheticFetchClient, SyntheticSelectionModel
from claview_b_api.service import BService, store

DATA = Path(__file__).resolve().parents[4] / "eval/cases/b/period/answered_period_yoy_b.json"
CASES = json.loads(DATA.read_text(encoding="utf-8"))["cases"]
MODEL_SHOWN = [c for c in CASES if "model_shown" in c]


class RecordingModel(SyntheticSelectionModel):
    """The synthetic model; keeps the `resolved_period` of every request it is sent, per table."""

    def __init__(self, meta):
        super().__init__(meta)
        self.shown: dict[str, dict | None] = {}

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.shown[task.rpartition(":")[2]] = context["resolved_period"]
        return super().complete(task, system, context, schema, max_tokens, trace)


def run(case: dict) -> tuple[dict, RecordingModel, SyntheticFetchClient]:
    model, client = RecordingModel(store()), SyntheticFetchClient()
    service = BService(model, client, "synthetic")
    outcome, _ = service.resolve(case["query"], request_tables(case), case["as_of"])
    assert outcome["status"] == "ok", outcome.get("issues")
    service.fetch(outcome["result"])
    return outcome, model, client


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_every_table_reads_the_expected_period(case):
    outcome, _, client = run(case)
    rows = {row["table_id"]: row for row in outcome["result"]["period_resolutions"]}
    fetched = {p["tblId"]: [p["prdSe"], p["startPrdDe"], p["endPrdDe"]] for p in client.calls}
    for table_id, expected in case["expect"].items():
        row = rows[table_id]
        got = {
            "requested": [row["frequency"], row["requested"][0]["start"], row["requested"][0]["end"]],
            "fetched": [row["frequency"], row["fetched"][0]["start"], row["fetched"][0]["end"]],
            "extra": [row["frequency"], row["calculation_extra"][0]["start"], row["calculation_extra"][0]["end"]]
            if row["calculation_extra"]
            else None,
            "sources": row["applied_sources"],
            "fetch": fetched[table_id],
        }
        for field, value in expected.items():
            assert got[field] == value, f"{case['id']} {table_id} {field}: {got[field]} != {value}"


@pytest.mark.parametrize("case", MODEL_SHOWN, ids=[c["id"] for c in MODEL_SHOWN])
def test_the_model_is_shown_an_answered_period_but_never_the_years_the_question_states(case):
    _, model, _ = run(case)
    assert model.shown == case["model_shown"]


@pytest.mark.parametrize("case", [c for c in CASES if c["id"].startswith("YY-0") and "extra" in str(c["expect"])
                                  and c["expect"]["DT_161Y014"]["extra"]], ids=lambda c: c["id"])
def test_the_calculation_range_says_why_it_is_read(case):
    outcome, _, _ = run(case)
    extra = outcome["result"]["period_resolutions"][0]["calculation_extra"]
    assert [e["purpose"] for e in extra] == ["yoy_baseline"]


def test_the_user_answer_is_still_applied_by_the_code_not_by_the_model():
    # whatever period the synthetic model writes ("x"), the answered one is what is selected and fetched
    case = next(c for c in CASES if c["id"] == "AP-01")
    outcome, _, client = run(case)
    assert {s["period"]["start"] for s in outcome["result"]["selections"]} == {"202401"}
    assert [(p["startPrdDe"], p["endPrdDe"]) for p in client.calls] == [("202401", "202412")]


def test_period_not_in_question_only_for_an_answered_or_quoted_fallback_period():
    shown = {"period": {"frequency": "M", "start": "202401", "end": "202412"}}
    assert period_not_in_question({**shown, "applied": {"period": "a_user_answer", "frequency": "b_computed"}})
    assert period_not_in_question({**shown, "applied": {"period": "a_quoted_fallback", "frequency": "b_computed"}})
    assert not period_not_in_question({**shown, "applied": {"period": "b_computed", "frequency": "a_user_answer"}})
    assert not period_not_in_question({"period": None, "applied": {"period": "a_user_answer"}})
    assert not period_not_in_question({**shown})


@pytest.mark.parametrize(
    ("query", "months"),
    [
        ("2024년 전년 동월 대비 증감률", 12), ("2024년 전년동월대비", 12), ("2024년 전년동월비", 12),
        ("2024년 전년도 동월 대비", 12), ("2024년 전년 같은 달보다", 12), ("2024년 작년 동월 대비", 12),
        ("2024년 전년 대비", 12), ("2024년 전년동기 대비", 12), ("2024년 증감률", 0), ("2024년 전년도 말 잔액", 0),
    ],
)
def test_year_over_year_wordings_for_a_monthly_table(query, months):
    assert detect_baseline_extension(query, "M") == months


def test_the_extension_follows_the_frequency():
    assert detect_baseline_extension("2024년 전년 동월 대비", "Q") == 4
    assert detect_baseline_extension("2024년 전년 동월 대비", "H") == 2
    assert detect_baseline_extension("2024년 전년 동월 대비", "Y") == 1


def test_month_over_month_wording_is_not_year_over_year():
    import re

    assert not re.search(YEAR_OVER_YEAR, "2024년 전월 대비 변화")
    assert detect_baseline_extension("2024년 전월 대비 변화", "M") == 1
