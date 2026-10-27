"""B-part verification against golden_set_v1.json (22 cases).

Each case's `expected.A` is fed straight into the B subgraph (bypassing
real A/search), paired with a ScriptedModel preloaded with the case's own
`expected.B.selections` (via claview_b.evaluation.scripted_answers). This is
"replay" mode exactly as claview_b.evaluation.evaluate() defines it: it
proves B's own deterministic code (schema building, axis pruning, period
resolution/validation, canonicalization, unit resolution, series_id
assignment) reproduces the golden answer when the model gives the right
raw answer. It is NOT a test of model/search accuracy — see test_b_unit.py
for direct tests of the validation logic, and evaluate(..., mode="live")
for actual HCX accuracy, which needs CLOVASTUDIO_API_KEY and
--allow-external and is out of scope here.

This file reflects the team's 2026-10 policy decision (see the
"ClaView B파트 실패 10건 분석" doc) that replaced the original "refuse any
relative-period or comparison-baseline query" behavior:
  - relative-period wording ("최근/이후/올해/작년") is now resolved in code
    against the table's own metadata coverage, then surfaced to the user as
    a confirmation step (status=needs_confirmation) rather than refused
    outright -- see claview_b.stages.resolve_relative_period.
  - MoM/YoY comparison wording ("전월대비", "전년보다", "가장 크게 변") now
    auto-extends the selection's start before the literally stated period
    -- see claview_b.stages.detect_baseline_extension.
  - "기여도"/"연쇄가중" (contribution / chain-weighted figures) remain
    refused: those are methodology questions, not a baseline period the
    code can mechanically extend.
"""

from __future__ import annotations

from collections import Counter

import pytest
from jsonschema import Draft202012Validator

from claview_b.contracts import Trace
from claview_b.evaluation import scripted_answers
from claview_b.graphs import build_b_graph
from claview_b.models import ScriptedModel
from claview_b.stages import Services, selection_key

# The 8 cases with "최근/이후/올해/작년" wording. All resolve deterministically
# (claview_b.stages.resolve_relative_period) and, as of 2026-10-01, all of them
# have a table whose latest recorded period lags behind what "오늘" would
# literally imply. Until 2026-10-06 that stopped B at needs_confirmation; since
# the 2026-10-06 flow change B records the notice (period_notices, status
# "needs_confirmation") and keeps going, so all 8 come back status=ok.
RELATIVE_PERIOD_CASES = {
    "CV-AB-07",
    "CV-AB-08",
    "CV-AB-10",
    "CV-AB-11",
    "CV-AB-12",
    "CV-AB-13",
    "CV-AB-14",
    "CV-AB-15",
}
# All 8 resolve to exactly the golden answer's period. CV-AB-12 ("최근 5년간
# ... 분기별") used to be the one exception: the golden answer was 202101 ("Q1
# of anchor_year - 5", calendar-year-anchored) while a plain N-unit sliding
# window gives 202103, and the team special-cased 분기 (2026-10). On
# 2026-10-06 the team reversed that: every frequency counts back from the
# table's latest period ("5년치 분기"는 20개), and CV-AB-12's expected period
# was corrected to 202103~202602 -- no frequency-specific rule remains. See
# tests/test_b_unit.py for the synthetic invariant tests, and
# RESOLVED_PERIOD_DISCREPANCIES below -- empty, kept as a named guard so a
# future reintroduced mismatch fails loudly here too.
RELATIVE_PERIOD_EXACT_CASES = RELATIVE_PERIOD_CASES
RESOLVED_PERIOD_DISCREPANCIES: dict[str, dict] = {}
# CV-AB-18 (전월대비, "가장 크게 변") and CV-AB-20 (전년보다) -- B now
# auto-extends the baseline period and reproduces the golden selections
# exactly, same bar as the 12 originally-passing cases.
BASELINE_EXTENSION_CASES = {"CV-AB-18", "CV-AB-20"}

ALWAYS_OK_CASES = [
    "CV-AB-01",
    "CV-AB-02",
    "CV-AB-03",
    "CV-AB-04",
    "CV-AB-05",
    "CV-AB-06",
    "CV-AB-09",
    "CV-AB-16",
    "CV-AB-17",
    "CV-AB-19",
    "CV-AB-21",
    "CV-AB-22",
    *sorted(BASELINE_EXTENSION_CASES),
]  # 14 cases total


def _run_b(case: dict, store, config) -> dict:
    trace = Trace()
    model = ScriptedModel(scripted_answers(case))
    services = Services(store, search=None, model=model, config=config, trace=trace)
    graph = build_b_graph(services)
    state = graph.invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]},
        {"recursion_limit": 10 + 4 * len(case["expected"]["A"]["tables"])},
    )
    return state["stage_outcome"]


def _condition_counts(result: dict) -> Counter:
    return Counter(selection_key(s) for s in result["selections"])


def _unit_counts(result: dict) -> Counter:
    return Counter((selection_key(s), s["unit"]) for s in result["selections"])


def test_golden_set_matches_its_own_schema(golden_schema, golden_cases):
    payload = {
        "schema_version": "claview-ab/0.4-draft",
        "dataset_id": "claview-golden-set-v1-ab-22",
        "cases": golden_cases,
    }
    errors = list(Draft202012Validator(golden_schema).iter_errors(payload))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


def test_golden_tables_are_all_in_the_catalog(golden_cases, store):
    missing = sorted(
        {
            f"{t['org_id']}:{t['table_id']}"
            for case in golden_cases
            for t in case["expected"]["A"]["tables"]
        }
        - set(store.catalog)
    )
    assert not missing, f"golden-set tables missing from the catalog: {missing}"


@pytest.mark.parametrize("case_id", ALWAYS_OK_CASES)
def test_b_reproduces_golden_selections(case_id, golden_cases, store, config):
    """14 cases (12 with an explicit period, plus the 2 MoM/YoY baseline-
    extension cases) must reproduce the golden selections -- same item/axis/
    period tuples and the same unit -- exactly, with status 'ok'."""
    case = next(c for c in golden_cases if c["case_id"] == case_id)
    result = _run_b(case, store, config)
    assert result["status"] == "ok", (case_id, result["status"], result["issues"])
    expected = case["expected"]["B"]
    assert _condition_counts(result["result"]) == _condition_counts(expected), case_id
    assert _unit_counts(result["result"]) == _unit_counts(expected), case_id


@pytest.mark.parametrize("case_id", sorted(RELATIVE_PERIOD_EXACT_CASES))
def test_b_resolves_relative_period_notifies_and_proceeds(case_id, golden_cases, store, config):
    """All 8 relative-period cases (CV-AB-12 included, with its expected
    period corrected to the unified sliding window on 2026-10-06): B resolves
    the "최근/이후" wording to exactly the golden answer's period. Because the
    table's latest data lags behind what "오늘"(as_of) would imply, B records a
    notice (status "needs_confirmation") with the periods involved -- and then,
    per the 2026-10-06 flow change, carries on to a normal status=ok result so
    the pipeline goes on to fetch. The notice only informs; it asks nothing."""
    case = next(c for c in golden_cases if c["case_id"] == case_id)
    result = _run_b(case, store, config)
    assert result["status"] == "ok", (case_id, result["status"], result["issues"])
    expected = case["expected"]["B"]
    assert _condition_counts(result["result"]) == _condition_counts(expected), case_id
    notices = result["result"]["period_notices"]
    assert len(notices) == 1, case_id
    notice = notices[0]
    assert notice["status"] == "needs_confirmation", case_id
    assert notice["code"] == "period_requires_confirmation", case_id
    assert (
        notice["table"]
        == f"{case['expected']['A']['tables'][0]['org_id']}:"
        + (case["expected"]["A"]["tables"][0]["table_id"])
    )
    assert notice["resolved_period"] == expected["selections"][0]["period"], case_id
    assert notice["resolved_period"]["end"] in notice["message"], case_id
    assert "?" not in notice["message"], "the notice informs; it must not ask a question"


@pytest.mark.parametrize("case_id", sorted(RESOLVED_PERIOD_DISCREPANCIES))
def test_b_resolved_period_known_discrepancies(case_id, golden_cases, store, config):
    """Empty since CV-AB-12's expected period was corrected (2026-10-06) --
    it lives in RELATIVE_PERIOD_EXACT_CASES above. Kept as a parametrized, data-driven
    guard (rather than deleted outright) so that if a *future* case is ever
    added to RESOLVED_PERIOD_DISCREPANCIES, it is exercised and tracked the
    same way CV-AB-12 was, instead of silently passing inside the exact-match
    test above."""
    case = next(c for c in golden_cases if c["case_id"] == case_id)
    result = _run_b(case, store, config)
    assert result["status"] == "ok"
    golden_period = case["expected"]["B"]["selections"][0]["period"]
    resolved_period = result["result"]["period_notices"][0]["resolved_period"]
    assert resolved_period == RESOLVED_PERIOD_DISCREPANCIES[case_id], (
        "resolve_relative_period's output changed -- re-check against the golden answer "
        f"({golden_period}) and update this test either way"
    )
    assert resolved_period != golden_period, (
        f"{case_id} now matches the golden answer -- the discrepancy this test tracks is "
        "resolved; move it out of RESOLVED_PERIOD_DISCREPANCIES into a normal exact-match case"
    )


def test_known_outcome_shape_is_stable(golden_cases, store, config):
    """Guards against silent regressions: since the 2026-10-06 flow change every
    one of the 22 cases comes back status=ok; exactly the 8 relative-period
    cases carry a period notice, and the other 14 carry none."""
    noticed, plain, other = set(), set(), {}
    for case in golden_cases:
        result = _run_b(case, store, config)
        if result["status"] != "ok":
            other[case["case_id"]] = result["status"]
        elif result["result"].get("period_notices"):
            noticed.add(case["case_id"])
        else:
            plain.add(case["case_id"])
    assert not other, f"unexpected non-ok statuses: {other}"
    assert plain == set(ALWAYS_OK_CASES)
    assert noticed == RELATIVE_PERIOD_CASES
