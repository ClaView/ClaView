"""Replays eval/cases/golden_set_v1_period_augmented.json -- 13 new cases
added 2026-10-02 to probe claview.stages.resolve_relative_period on
combinations golden_set_v1's original 22 cases never exercised with real
data: H(반기) frequency, "올해"/"작년" on every frequency, and direct
same-unit "최근 N<분기|반기>" counts. See scripts/build_period_augmented_cases.py
for how each case's table/item/period was derived (real catalog data, hand
-- never model -- authored) and README_기간증강.md for the full writeup.

Each case carries `policy_status`:
  - "implemented": an unambiguous rule the code gets right -- tested as a
    normal exact-match case. This includes the four "올해" cases
    (CV-AUG-05/08/11/13) and the four "작년" cases (CV-AUG-04/07/10/12):
    both used to be ambiguous or outright broken (see below), but the
    2026-10-02 policy decision settled them, so they are no longer
    open questions.
  - "assumption_untested": code resolves to *a* period without erroring,
    but the convention isn't team-decided yet -- tested as an exact match
    against *current* behavior, so a change is noticed, not silently
    accepted as correctness. No case carries this status any more: CV-AUG-01
    (H "최근 N년") was the last one, settled 2026-10-06 when the team decided
    반기 is the sliding window like 월/연 ("5년치 반기 데이터"는 10개가 상식),
    after the axis-3 evidence sets (golden_set_v1_axis3_*.json) showed real
    data exists for both readings. The category is kept for future gaps.

History (both now resolved, 2026-10-02): "올해" used to make
resolve_relative_period raise StageFailure("metadata_refresh_required")
outright whenever a table's data hadn't reached the current calendar year
yet (every annual table in the catalog, since annual data for the current
year cannot exist until the year ends) -- unlike every other relative-
period gap, which degraded gracefully. "작년"/"올해" also used to anchor to
config.as_of's calendar year rather than the table's own actual latest
year, which is what produced that hard failure in the first place (asking
for a year no table could ever have). The team decided to apply the same
"anchor to the table's own data, confirm gracefully instead of hard-
failing" policy already used for "최근 N년"/"...이후" to these two as well
(see claview/stages.py's resolve_relative_period docstring) -- which also
surfaced and fixed a separate, previously-unexercised bug: "작년" names one
closed, already-finished year, so its own end must stop at that year's own
year-end rather than running on to the table's current latest period
(fixed alongside, same commit).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from claview.contracts import Trace
from claview.evaluation import scripted_answers
from claview.graphs import build_b_graph
from claview.models import ScriptedModel
from claview.stages import Services, resolve_relative_period, selection_key

# Read once at import time (pytest needs parametrize ids at collection
# time, before fixtures run) so the two id lists below can never drift
# from the actual file -- they're filtered from this same list, not
# hand-typed separately.
_CASES_PATH = (
    Path(__file__).resolve().parents[2]
    / "eval"
    / "cases"
    / "b_period"
    / "golden_set_v1_period_augmented.json"
)
_CASES = json.loads(_CASES_PATH.read_text(encoding="utf-8-sig"))["cases"]
_RESOLVED_CASE_IDS = [
    c["case_id"] for c in _CASES if c["policy_status"] in ("implemented", "assumption_untested")
]
_OPEN_BUG_CASE_IDS = [c["case_id"] for c in _CASES if c["policy_status"] == "open_bug"]


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


def test_period_augmented_set_matches_its_own_schema(
    period_augmented_schema, period_augmented_cases
):
    payload = {
        "schema_version": "claview-ab/0.4-draft-period-augmented",
        "dataset_id": "claview-golden-set-v1-period-augmented-13",
        "cases": period_augmented_cases,
    }
    errors = list(Draft202012Validator(period_augmented_schema).iter_errors(payload))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


def test_period_augmented_tables_are_all_in_the_catalog(period_augmented_cases, store):
    missing = sorted(
        {
            f"{t['org_id']}:{t['table_id']}"
            for case in period_augmented_cases
            for t in case["expected"]["A"]["tables"]
        }
        - set(store.catalog)
    )
    assert not missing, f"period-augmented tables missing from the catalog: {missing}"


@pytest.mark.parametrize("case_id", _RESOLVED_CASE_IDS)
def test_resolved_or_confirmed_cases_match_expected_period(
    case_id, period_augmented_cases, store, config
):
    """ "implemented" and "assumption_untested" cases must resolve the
    relative-period wording to exactly the case's recorded expected period.

    Since the 2026-10-06 flow change every case answers with status="ok" and
    the expected period. When the table's actual latest data lags what the
    literal wording would ask for (the common case) the result also carries
    a period notice (status "needs_confirmation", informing only); a "작년"
    case whose closed year the table fully has carries none. Whether a notice
    is present must agree with resolve_relative_period itself."""
    case = next(c for c in period_augmented_cases if c["case_id"] == case_id)
    result = _run_b(case, store, config)
    expected_period = case["expected"]["B"]["selections"][0]["period"]
    assert result["status"] == "ok", (case_id, result["status"], result.get("issues"))
    assert result["result"]["selections"][0]["period"] == expected_period, case_id
    table_ref = case["expected"]["A"]["tables"][0]
    resolution = resolve_relative_period(
        case["query"], store.get(table_ref["org_id"], table_ref["table_id"]), config
    )
    notices = result["result"].get("period_notices", [])
    if resolution["status"] == "needs_confirmation":
        assert len(notices) == 1, case_id
        assert notices[0]["status"] == "needs_confirmation", case_id
        assert notices[0]["code"] == "period_requires_confirmation", case_id
        assert notices[0]["resolved_period"] == expected_period, case_id
        assert "?" not in notices[0]["message"], case_id
    else:
        assert not notices, case_id


@pytest.mark.parametrize("case_id", _OPEN_BUG_CASE_IDS)
def test_open_bug_cases_still_reproduce_the_hard_refusal(
    case_id, period_augmented_cases, store, config
):
    """No case is currently tagged "open_bug" (the 2026-10-02 fix resolved
    the only one this file ever had -- see the module docstring), so this
    parametrizes over an empty list and is reported as skipped, the same
    way tests/test_b_golden.py's own empty-set guard is. Kept so that if a
    *new* hard-refusal gap is found and tagged "open_bug" in the future,
    it gets this same regression coverage for free without anyone having
    to remember to re-add this test."""
    case = next(c for c in period_augmented_cases if c["case_id"] == case_id)
    result = _run_b(case, store, config)
    assert result["status"] == "unresolved", (case_id, result["status"])
    assert result["issues"][0]["code"] == "metadata_refresh_required", case_id


def test_period_augmented_status_counts_are_stable(period_augmented_cases):
    """Guards the shape of the set itself: catches a case silently being
    re-tagged (or a new case added without updating the two module-level
    id lists above, which are filtered from the same file so they can't
    independently go stale -- this test is the one that would notice a
    new case nobody wired a status-specific assertion for)."""
    # Counter only includes keys it actually observed, so "open_bug" (0
    # cases since 2026-10-02) and "assumption_untested" (0 since the
    # 2026-10-06 반기 decision) are correctly absent here, not listed as 0.
    counts = Counter(c["policy_status"] for c in period_augmented_cases)
    assert counts == {"implemented": 13}
    assert len(_RESOLVED_CASE_IDS) + len(_OPEN_BUG_CASE_IDS) == len(period_augmented_cases)


def test_period_augmented_selection_keys_are_internally_consistent(period_augmented_cases, store):
    """Every case's expected selection must reference real item/dimension
    codes on its own stated table (schema validation checks shape, not
    that the codes actually exist in that table's metadata)."""
    for case in period_augmented_cases:
        t = case["expected"]["A"]["tables"][0]
        table = store.get(t["org_id"], t["table_id"])
        for s in case["expected"]["B"]["selections"]:
            assert s["item_id"] in table["items"], (case["case_id"], s["item_id"])
            for axis, dim in s["dimensions"].items():
                assert axis in table["dimensions"], (case["case_id"], axis)
                assert dim["code"] in table["dimensions"][axis]["values"], (
                    case["case_id"],
                    dim["code"],
                )
            assert selection_key(s)  # smoke: doesn't raise
