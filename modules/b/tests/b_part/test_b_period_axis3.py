"""Replays the three "axis 3" evidence sets (2026-10-06) -- 30 cases that widen
the period axis around golden_set_v1's five human-labeled "최근 N년" cases:

  eval/cases/b/period/golden_set_v1_axis3_h_recent_years.json   (8)  반기, N sweep, H1 vs H2 anchor
  eval/cases/b/period/golden_set_v1_axis3_cross_frequency.json  (12) same table + same N, frequency varies
  eval/cases/b/period/golden_set_v1_axis3_qm_controls.json      (10) 분기/월 re-asked with other N/anchors

See modules/b/scripts/b_part/build_axis3_period_sets.py for how each case was derived and
modules/b/scripts/b_part/axis3_evidence_report.py for the evidence matrix. Every case's
`expected` period is what the code does, pinned by the team's decisions of
2026-10-06: "최근 N년" is one sliding-window rule for every frequency (N years =
N * periods-per-year periods counted back from the table's own latest period;
반기 -> 10 for "5년치", 분기 -> 20). The Q-only calendar-year exception that
used to make 분기 reach back an extra year at Q4 anchors was removed that day
(golden CV-AB-12's expected period corrected to 202103~202602). The tests here
are therefore both regression guards and the discriminating invariants of the
decision: `test_every_frequency_is_n_times_periods_per_year_periods` and
`test_cross_frequency_start_year_agrees` (same table + same N => same start
year whichever frequency is asked; holds for every group, including the
year-end-anchored DT_303Y001 groups that used to be strict xfails).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from claview_b.contracts import Trace
from claview_b.evaluation import scripted_answers
from claview_b.graphs import build_b_graph
from claview_b.metadata import period_number
from claview_b.models import ScriptedModel
from claview_b.stages import (
    Services,
    _as_of_period,
    _index_to_period,
    _year_start,
    resolve_relative_period,
    selection_key,
)

_CASES_DIR = Path(__file__).resolve().parents[4] / "eval" / "cases" / "b" / "period"
_SET_STEMS = [
    "golden_set_v1_axis3_h_recent_years",
    "golden_set_v1_axis3_cross_frequency",
    "golden_set_v1_axis3_qm_controls",
]
_PAYLOADS = {
    stem: json.loads((_CASES_DIR / f"{stem}.json").read_text(encoding="utf-8-sig"))
    for stem in _SET_STEMS
}
_ALL = [(stem, case) for stem in _SET_STEMS for case in _PAYLOADS[stem]["cases"]]
_ALL_IDS = [case["case_id"] for _, case in _ALL]
_PER_YEAR = {"Y": 1, "H": 2, "Q": 4, "M": 12}


def _n_years(case: dict) -> int:
    return int(re.search(r"최근\s*(\d+)년", case["query"]).group(1))


def _period(case: dict) -> dict:
    return case["expected"]["B"]["selections"][0]["period"]


def _is_final_anchor(period: dict) -> bool:
    """The case's period end is the table's own latest period (none of these
    cases is clamped or closed), so it doubles as the anchor."""
    return period["frequency"] == "Y" or int(period["end"][4:]) == _PER_YEAR[period["frequency"]]


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


@pytest.mark.parametrize("stem", _SET_STEMS)
def test_axis3_set_matches_the_period_augmented_schema(stem, period_augmented_schema):
    errors = list(Draft202012Validator(period_augmented_schema).iter_errors(_PAYLOADS[stem]))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


def test_axis3_sets_have_the_planned_shape():
    assert {stem: len(p["cases"]) for stem, p in _PAYLOADS.items()} == {
        "golden_set_v1_axis3_h_recent_years": 8,
        "golden_set_v1_axis3_cross_frequency": 12,
        "golden_set_v1_axis3_qm_controls": 10,
    }
    assert len(set(_ALL_IDS)) == len(_ALL_IDS), "case_ids must be unique across the three sets"


def test_axis3_selections_reference_real_catalog_items_and_dimensions(store):
    for _, case in _ALL:
        t = case["expected"]["A"]["tables"][0]
        table = store.get(t["org_id"], t["table_id"])
        assert _period(case)["frequency"] in table["periods"], case["case_id"]
        for s in case["expected"]["B"]["selections"]:
            assert s["item_id"] in table["items"], (case["case_id"], s["item_id"])
            for axis, dim in s["dimensions"].items():
                assert axis in table["dimensions"], (case["case_id"], axis)
                assert dim["code"] in table["dimensions"][axis]["values"], (
                    case["case_id"],
                    dim["code"],
                )
            assert selection_key(s)  # smoke: doesn't raise


@pytest.mark.parametrize("case_id", _ALL_IDS)
def test_axis3_case_replays_to_its_recorded_period(case_id, store, config):
    """B must resolve each case to exactly its recorded period and answer with
    status=ok. When the table lags as_of (the usual case) the result also
    carries a period notice (status "needs_confirmation") with the same period;
    since the 2026-10-06 flow change B no longer stops there."""
    case = next(c for _, c in _ALL if c["case_id"] == case_id)
    result = _run_b(case, store, config)
    assert result["status"] == "ok", (case_id, result["status"], result.get("issues"))
    assert result["result"]["selections"][0]["period"] == _period(case), case_id
    table_ref = case["expected"]["A"]["tables"][0]
    resolution = resolve_relative_period(
        case["query"], store.get(table_ref["org_id"], table_ref["table_id"]), config
    )
    notices = result["result"].get("period_notices", [])
    assert bool(notices) == (resolution["status"] == "needs_confirmation"), case_id
    for notice in notices:
        assert notice["status"] == "needs_confirmation", case_id
        assert notice["code"] == "period_requires_confirmation", case_id
        assert notice["resolved_period"] == _period(case), case_id


@pytest.mark.parametrize("case_id", _ALL_IDS)
def test_axis3_candidates_follow_their_definitions(case_id, store):
    """Recomputes the three candidate periods independently of the generator
    (and of resolve_relative_period) from the table's own coverage bounds, and
    checks the file's recorded candidates equal them -- and that the recorded
    expected period is one of them."""
    case = next(c for _, c in _ALL if c["case_id"] == case_id)
    t = case["expected"]["A"]["tables"][0]
    table = store.get(t["org_id"], t["table_id"])
    freq, n = _period(case)["frequency"], _n_years(case)
    bounds = table["periods"][freq]
    anchor_idx = period_number(bounds["end"], freq)
    cov_start = period_number(bounds["start"], freq)
    sliding_idx = anchor_idx - (n * _PER_YEAR[freq] - 1)
    sliding_year = int(_index_to_period(sliding_idx, freq)[:4])
    expected_starts = {
        "sliding_window": sliding_idx,
        "calendar_anchor": period_number(_year_start(freq, int(bounds["end"][:4]) - n), freq),
        "floor_to_year": period_number(_year_start(freq, sliding_year), freq),
    }
    recorded = case["period_candidates"]
    assert set(recorded) == set(expected_starts), case_id
    for name, idx in expected_starts.items():
        assert recorded[name] == {
            "frequency": freq,
            "start": _index_to_period(max(idx, cov_start), freq),
            "end": bounds["end"],
        }, (case_id, name)
    assert _period(case) in recorded.values(), case_id


@pytest.mark.parametrize("stem", ["golden_set_v1_axis3_h_recent_years"])
def test_h_follows_the_sliding_window_at_both_anchor_halves(stem):
    """Team decision 2026-10-06: every 반기 "최근 N년" is the sliding window
    (exactly N years of data counted back from the table's own latest period),
    at H2 anchors (where that equals floor_to_year) and at H1 anchors (where it
    starts a half-year later than both calendar_anchor and floor_to_year).
    Rationale recorded with the decision: "5년치 반기 데이터"는 10개가 상식이고
    11~12개는 이용자 기대에서 벗어난다. Changing this means reopening that
    decision, not just editing a number here."""
    for case in _PAYLOADS[stem]["cases"]:
        assert _period(case) == case["period_candidates"]["sliding_window"], case["case_id"]
    h1 = [c for c in _PAYLOADS[stem]["cases"] if not _is_final_anchor(_period(c))]
    assert h1, "set must keep H1-anchored cases -- they are the only ones that separate S from F"
    for case in h1:
        cands = case["period_candidates"]
        assert cands["sliding_window"] != cands["floor_to_year"], case["case_id"]


_H_IDS = [case["case_id"] for _, case in _ALL if _period(case)["frequency"] == "H"]


@pytest.mark.parametrize("case_id", _H_IDS)
def test_h_recent_n_years_is_2n_half_years_and_says_when_the_expected_period_has_no_data(
    case_id, store, config
):
    """The two halves of the 2026-10-06 reasoning, as executable checks:
    (1) "최근 N년" in 반기 is N*2 half-years -- 5년치 = 10개, never 11 or 12;
    (2) the user's expected period (the one containing as_of) usually has no
    data yet, and the answer must say so rather than silently shifting the
    window: B records a notice (status "needs_confirmation") reporting both the
    period the request called for and the table's actual latest, the window
    ends at that latest period, and B carries on to fetch (no question asked,
    2026-10-06 flow change)."""
    case = next(c for _, c in _ALL if c["case_id"] == case_id)
    p = _period(case)
    assert period_number(p["end"], "H") - period_number(p["start"], "H") + 1 == 2 * _n_years(case)

    result = _run_b(case, store, config)
    assert result["status"] == "ok", (case_id, result["status"])
    assert result["result"]["selections"][0]["period"] == p
    info = result["result"]["period_notices"][0]
    wanted = _as_of_period(config.as_of, "H")
    assert wanted != p["end"], "case must be one where the expected period really has no data"
    assert info["requested_as_of_period"] == wanted
    assert info["resolved_period"] == p
    assert wanted in info["message"] and p["end"] in info["message"], info["message"]
    assert "?" not in info["message"]


def test_axis3_policy_status_claims_only_what_is_pinned():
    """All 30 cases are "implemented": the one count-back rule is pinned by the
    existing human goldens (연 CV-AB-10, 월 CV-AB-07/13/15, 분기 CV-AB-12 after
    its 2026-10-06 correction) and by the team's 2026-10-06 decisions for 반기
    and 분기. Nothing is left "assumption_untested"."""
    assert Counter(c["policy_status"] for _, c in _ALL) == {"implemented": 30}


@pytest.mark.parametrize("case_id", _ALL_IDS)
def test_every_frequency_is_n_times_periods_per_year_periods(case_id):
    """The unified rule as an invariant on every case: "최근 N년" is exactly
    N * periods-per-year periods (Y 5, H 10, Q 20, M 60 for N=5) and always the
    sliding-window candidate -- never the calendar-anchored or floored one
    when they differ."""
    case = next(c for _, c in _ALL if c["case_id"] == case_id)
    p = _period(case)
    freq = p["frequency"]
    assert period_number(p["end"], freq) - period_number(p["start"], freq) + 1 == (
        _n_years(case) * _PER_YEAR[freq]
    ), case_id
    assert p == case["period_candidates"]["sliding_window"], case_id


def _cross_frequency_groups() -> list:
    groups: dict[tuple, list] = defaultdict(list)
    for case in _PAYLOADS["golden_set_v1_axis3_cross_frequency"]["cases"]:
        key = (case["expected"]["A"]["tables"][0]["table_id"], _n_years(case))
        groups[key].append(case)
    return [pytest.param(cases, id=f"{table_id}-N{n}") for (table_id, n), cases in groups.items()]


@pytest.mark.parametrize("cases", _cross_frequency_groups())
def test_cross_frequency_start_year_agrees(cases):
    """Same table, same "최근 N년": the resolved start falls in the same
    calendar year whichever frequency the query asks for. Held at mid-year
    anchors from the start; since the 2026-10-06 unification it also holds at
    the year-end-anchored table (DT_303Y001), where 분기 used to reach back an
    extra year."""
    start_years = {_period(c)["frequency"]: _period(c)["start"][:4] for c in cases}
    assert len(set(start_years.values())) == 1, start_years
