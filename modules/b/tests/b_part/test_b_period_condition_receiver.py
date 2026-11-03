"""B's receiver for A's `period_condition` (docs/contracts/A_B_기간조건_공용모델과_A송신부.md, B 회신 v2).

Pinned here:
  * the common example table (`eval/cases/b/period/period_examples_b.json`, 45 cases, expected values fixed
    by rule before the code ran): requested / fetched / missing / calculation-extra ranges, applied sources,
    substitution, notices with their visible flag, frequency alternatives, and the period_condition_* stops
  * invalid input refused by the contract's codes (`invalid_requests` of the samples file)
  * the end-to-end path (BSteps with a synthetic model): the model never sees the condition, the answered
    period is applied in code, `period_resolutions` is returned and matches the shared result model
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.b_part.test_b_unit import make_table

from claview_b.contracts import Config, StageFailure
from claview_b.period_condition import parse_condition, resolution_row
from claview_b.stages import plan_table_period
from claview_common.contracts.period_condition import PeriodCondition, PeriodResolution

EVAL = Path(__file__).resolve().parents[4] / "eval/cases/b/period"
EXAMPLES = json.loads((EVAL / "period_examples_b.json").read_text(encoding="utf-8"))
SAMPLES = json.loads((EVAL / "period_condition_samples_b.json").read_text(encoding="utf-8"))
CASES = [(c, t) for c in EXAMPLES["cases"] for t in c["tables"]]


def build_condition(raw):
    """The examples write the answers as a list of kinds; B only checks that they are linked."""
    if raw is None:
        return None
    condition = {k: v for k, v in raw.items() if k != "answers"}
    kinds = raw.get("answers", [])
    if kinds:
        condition["statistics"] = ["s1"]
        condition["answers"] = [
            {
                "kind": kind,
                "question_id": f"q-{kind}",
                "question_version": 1,
                "statistics": ["s1"],
                "option_id": None,
                "value": "x",
                "text": None,
                "source": "user_answer",
            }
            for kind in kinds
        ]
    return condition


def spans(rows):
    return [(r["frequency"], r["start"], r["end"]) for r in rows]


@pytest.mark.parametrize("case,item", CASES, ids=[f"{c['case_id']}-{t['table_id']}" for c, t in CASES])
def test_common_examples(case, item):
    table = make_table(periods=item["coverage"])
    expected = item["expected"]
    raw = build_condition(item.get("period_condition"))
    config = Config(as_of=case["as_of"])
    if expected["status"] == "error":
        with pytest.raises(StageFailure) as stop:
            plan_table_period(case["query"], table, config, raw)
        assert stop.value.code == expected["error_code"]
        return
    plan = plan_table_period(case["query"], table, config, raw)
    condition = None if raw is None else parse_condition(raw)
    row = PeriodResolution.model_validate(resolution_row(table["table_id"], condition, plan))
    assert row.frequency == expected["frequency"]
    assert spans([r.model_dump() for r in row.requested]) == spans(expected["requested"])
    assert spans([r.model_dump() for r in row.fetched]) == spans(expected["fetched"])
    assert spans([r.model_dump() for r in row.missing]) == spans(expected["missing"])
    assert spans([r.model_dump() for r in row.calculation_extra]) == spans(expected["calculation_extra"])
    assert [r.purpose for r in row.calculation_extra] == [r["purpose"] for r in expected["calculation_extra"]]
    assert row.applied_sources.model_dump() == expected["applied_sources"]
    assert row.substituted is expected["substituted"]
    assert [(n.code, n.visible) for n in row.notices] == [(n["code"], n["visible"]) for n in expected["notices"]]
    assert row.frequency_alternatives == expected.get("frequency_alternatives", [])
    if "reference_differs" in expected:
        assert row.reference.differs.model_dump() == expected["reference_differs"]
    if raw is None:
        assert row.reference is None
    # every visible notice carries its words; the period actually selected is the fetched range
    assert all(n.message for n in row.notices if n.visible)
    assert plan["period"] == {
        "frequency": expected["frequency"],
        "start": expected["fetched"][0]["start"],
        "end": expected["fetched"][-1]["end"],
    }


@pytest.mark.parametrize("name", sorted(SAMPLES["invalid_requests"]))
def test_invalid_conditions_are_refused_by_the_contract_codes(name):
    sample = SAMPLES["invalid_requests"][name]
    raw = sample["request"]["tables"][0]["period_condition"]
    with pytest.raises(StageFailure) as stop:
        parse_condition(raw)
    assert stop.value.code == sample["expected_error"]
    assert stop.value.status == "error"


def test_parse_condition_accepts_the_model_and_its_dump():
    raw = build_condition(next(t for _c, t in CASES if t.get("period_condition"))["period_condition"])
    model = parse_condition(raw)
    assert isinstance(model, PeriodCondition)
    assert parse_condition(model) is model
    assert parse_condition(model.model_dump()) == model


def condition(**overrides):
    base = {
        "frequency": "M",
        "frequency_source": "question",
        "period": [{"start": "2024-01", "end": "2024-12"}],
        "period_source": "quoted",
    }
    base.update(overrides)
    return base


def test_no_condition_keeps_the_existing_behavior():
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    plan = plan_table_period("2024년 연도별 수출", table, Config(as_of="2026-10-29"))
    assert plan["period"] == {"frequency": "Y", "start": "2024", "end": "2024"}
    assert plan["legacy"] is None and plan["notices"] == []
    # wording with no period at all: the model's own period is validated later, nothing is decided here
    silent = plan_table_period("수출을 보여줘", table, Config(as_of="2026-10-29"))
    assert silent["period"] is None and silent["requested"] is None


def test_quoted_fallback_still_stops_when_a_period_does_not_fit_exactly_or_the_table():
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    config = Config(as_of="2026-10-29")
    for period in ([{"start": "2025-02", "end": "2025-12"}], [{"start": "2026-01", "end": "2026-12"}]):
        raw = condition(frequency="Y", period=period)
        with pytest.raises(StageFailure) as stop:
            plan_table_period("지금 상황을 연도별로 보여줘", table, config, raw)
        assert stop.value.code == "conditions_incomplete"


def test_user_answer_never_falls_back_to_other_periods_or_frequencies():
    table = make_table(periods={"Q": {"start": "200001", "end": "202602"}, "M": {"start": "200001", "end": "202608"}})
    config = Config(as_of="2026-10-29")
    answered = condition(
        frequency_source="user_answer",
        period_source="user_answer",
        statistics=["s1"],
        answers=[
            {"kind": kind, "question_id": f"q-{kind}", "question_version": 1, "statistics": ["s1"], "value": "x"}
            for kind in ("period", "frequency")
        ],
    )
    # monthly was chosen: monthly it is, even though the question says quarterly
    plan = plan_table_period("분기별로 보여줘", table, config, answered)
    assert plan["frequency"] == "M" and plan["applied"]["frequency"] == "a_user_answer"


def test_legacy_notice_is_kept_for_confirmed_partial_coverage():
    table = make_table(periods={"M": {"start": "200001", "end": "202606"}})
    raw = build_condition(
        {
            "frequency": "M",
            "frequency_source": "user_answer",
            "period": [{"start": "2023-09", "end": "2026-08"}],
            "period_source": "user_answer",
            "answers": ["period", "frequency"],
        }
    )
    plan = plan_table_period("통화량을 보여줘", table, Config(as_of="2026-10-29"), raw)
    legacy = plan["legacy"]
    assert legacy["code"] == "period_requires_confirmation"
    assert legacy["result"]["resolved_period"] == {"frequency": "M", "start": "202309", "end": "202606"}
    assert "202607~202608" in legacy["result"]["message"]
    assert plan["show"] is False  # the model is never told a confirmed period


