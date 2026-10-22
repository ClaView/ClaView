"""Augmented, invariant-based testing of B's period-resolution rules across
the FULL real 349-table catalog, not just the 22 golden_set_v1 cases.

golden_set_v1.json gives exactly one real example per rule this file cares
about: one quarterly "최근 N년" case (CV-AB-12), and two MoM/YoY
baseline-extension cases (CV-AB-18/20, both frequency M). That is not much
data to trust a rule on -- CV-AB-12 itself was once a single-data-point rule
(a Q-only calendar-year exception) that disagreed with the other 7; the team
dropped it on 2026-10-06 and corrected the golden (see test_b_golden.py).
Nobody has hand-verified an expected
answer for any OTHER table/date/N combination, so inventing "golden"
numbers for them would just be guessing dressed up as a test -- exactly
the kind of fabrication this project has avoided elsewhere.

Instead, these tests vary (table, frequency, N, as_of) across real tables
pulled straight from the catalog and check properties that must hold by
definition, independent of any specific expected number:
  - a resolved period never claims data the table doesn't actually have
    (end == the table's own latest recorded period; start never predates
    the table's earliest)
  - a larger "최근 N" never resolves to a *later* start than a smaller one
    (monotonicity)
  - resolution never silently skips confirmation when as_of and the
    table's actual coverage disagree
  - "최근 N년" on a quarterly table is exactly the plain N*4-quarter sliding
    window on every quarterly table tried, not just the one golden table --
    one rule for every frequency, no Q-specific exception (2026-10-06)
  - MoM/YoY baseline extension's exact per-frequency offset
    ({Y:1,H:2,Q:4,M:12}, claview.stages.detect_baseline_extension) is
    exercised against real Q/Y/H tables too, not just M -- golden only
    ever covers M (CV-AB-18/20)

This complements, not replaces, the golden-set exact-match tests in
test_b_golden.py: those prove a handful of specific inputs match
human-verified answers; this file proves the *rules themselves* don't
misbehave across the ~340 real tables nobody has hand-checked -- so a
future change that breaks on some other table's period shape is caught
here instead of shipping unnoticed.
"""

from __future__ import annotations

import pytest

from claview.contracts import Config, StageFailure
from claview.metadata import period_number
from claview.stages import _year_start, resolve_relative_period, validate_period

# Deterministic, small samples per frequency (sorted catalog order), not an
# exhaustive sweep of all ~340 tables -- keeps the suite fast and
# reproducible while still covering far more real period shapes (different
# coverage start/end combinations) than the golden set's single example per
# rule.
_SAMPLE_SIZE = 6
_WORDING = {"Y": "연도별", "M": "월별", "Q": "분기별", "H": "반기별"}
_WIDTH = {"H": 2, "Q": 4, "M": 12}
_EXTENSION = {"Y": 1, "H": 2, "Q": 4, "M": 12}


@pytest.fixture(scope="module")
def tables_by_freq(store) -> dict[str, list[dict]]:
    sampled: dict[str, list[dict]] = {"Y": [], "M": [], "Q": [], "H": []}
    for key in sorted(store.records):
        if all(len(v) >= _SAMPLE_SIZE for v in sampled.values()):
            break
        org_id, table_id = key.split(":", 1)
        try:
            table = store.get(org_id, table_id)
        except StageFailure:
            continue
        for freq in table["periods"]:
            if len(sampled[freq]) < _SAMPLE_SIZE:
                sampled[freq].append(table)
    return sampled


def _resolved_period(result: dict) -> dict:
    return result["result"]["resolved_period"] if "result" in result else result["period"]


@pytest.mark.parametrize("freq", ["Y", "M", "Q", "H"])
@pytest.mark.parametrize("n", [1, 2, 5, 10])
def test_relative_period_respects_table_coverage(tables_by_freq, config, freq, n):
    """ "최근 N년" must never resolve to an end beyond the table's own latest
    recorded period, and never to a start before its earliest -- on every
    sampled real table of every frequency, not just the golden one."""
    for table in tables_by_freq[freq]:
        bounds = table["periods"][freq]
        result = resolve_relative_period(f"최근 {n}년간 {_WORDING[freq]}", table, config)
        period = _resolved_period(result)
        assert period["end"] == bounds["end"], (freq, table["table_id"], n)
        assert period_number(period["start"], freq) >= period_number(bounds["start"], freq), (
            freq,
            table["table_id"],
            n,
        )


@pytest.mark.parametrize("freq", ["Y", "M", "Q", "H"])
def test_relative_period_monotonic_in_n(tables_by_freq, config, freq):
    """A bigger "최근 N" must never resolve to a *later* start than a
    smaller N on the same table -- true for every sampled table."""
    for table in tables_by_freq[freq]:
        starts = []
        for n in (1, 2, 5, 10, 20):
            result = resolve_relative_period(f"최근 {n}년간 {_WORDING[freq]}", table, config)
            starts.append(period_number(_resolved_period(result)["start"], freq))
        assert starts == sorted(starts, reverse=True), (freq, table["table_id"], starts)


@pytest.mark.parametrize("freq", ["Y", "M", "Q", "H"])
def test_relative_period_confirms_when_as_of_outruns_coverage(tables_by_freq, freq):
    """When as_of implies a later period than the table's actual latest
    recorded data, resolution reports needs_confirmation (B records it as a
    notice and carries on, 2026-10-06) -- it must never silently treat stale
    data as current, on any sampled table."""
    for table in tables_by_freq[freq]:
        bounds = table["periods"][freq]
        end_year = int(bounds["end"][:4])
        # Comfortably past the table's own coverage on every frequency.
        config = Config(as_of=f"{end_year + 2}-06-15")
        result = resolve_relative_period(f"최근 3년간 {_WORDING[freq]}", table, config)
        assert result["status"] == "needs_confirmation", (freq, table["table_id"])
        assert result["result"]["resolved_period"]["end"] == bounds["end"]


def test_quarterly_recent_years_is_always_the_plain_sliding_window(tables_by_freq, config):
    """ "최근 5년간" on every sampled quarterly table must resolve to exactly
    20 quarters ending at the table's latest period -- the same count-back
    rule as every other frequency (team decision 2026-10-06; the earlier
    Q-only calendar-year exception, which gave 21-24 quarters depending on
    the anchor quarter, is gone). Checked against real coverage ends rather
    than one golden table.

    Tables with less than N years of recorded history are skipped: the window
    would clamp to the table's own coverage start, which is already covered
    by test_relative_period_respects_table_coverage above."""
    n = 5
    checked = 0
    for table in tables_by_freq["Q"]:
        bounds = table["periods"]["Q"]
        anchor_idx = period_number(bounds["end"], "Q")
        coverage_start_idx = period_number(bounds["start"], "Q")
        sliding_start_idx = anchor_idx - (n * 4 - 1)
        if sliding_start_idx < coverage_start_idx:
            continue  # not enough history on this table to test unclamped
        result = resolve_relative_period(f"최근 {n}년간 분기별", table, config)
        resolved = _resolved_period(result)
        assert period_number(resolved["start"], "Q") == sliding_start_idx, (
            table["table_id"],
            bounds,
        )
        assert resolved["end"] == bounds["end"], (table["table_id"], bounds)
        assert period_number(resolved["end"], "Q") - period_number(resolved["start"], "Q") + 1 == 20
        checked += 1
    assert checked >= 2, "no sampled Q table had enough history to run this check unclamped"


@pytest.mark.parametrize("freq", ["Y", "M", "Q", "H"])
def test_baseline_extension_accepted_only_with_correct_offset(tables_by_freq, freq):
    """MoM/YoY baseline extension (claview.stages.detect_baseline_extension
    + validate_period's baseline_extension param) is golden-verified only
    for frequency M (CV-AB-18/20). This reproduces the same accept/reject
    shape -- a YoY-extended period is accepted with the right per-frequency
    offset and rejected without one -- against real Y/Q/H tables too, since
    those are otherwise completely unverified."""
    extension = _EXTENSION[freq]
    width = 1 if freq == "Y" else _WIDTH[freq]
    checked = 0
    for table in tables_by_freq[freq]:
        bounds = table["periods"][freq]
        start_idx = period_number(bounds["start"], freq)
        end_idx = period_number(bounds["end"], freq)
        end_year = int(bounds["end"][:4])
        start_year = int(bounds["start"][:4])
        if end_year - start_year < 3:
            continue  # not enough headroom for a clean, unclamped example
        year = end_year - 1
        year_start_idx = period_number(_year_start(freq, year), freq)
        year_end_idx = year_start_idx + (width - 1)
        if year_end_idx > end_idx or year_start_idx - extension < start_idx:
            continue

        def _idx_to_period(idx: int) -> str:
            if freq == "Y":
                return str(idx)
            yr, sub = divmod(idx, width)
            return f"{yr:04d}{sub + 1:02d}"

        period = {
            "frequency": freq,
            "start": _idx_to_period(year_start_idx - extension),
            "end": _idx_to_period(year_end_idx),
        }
        query = f"{year}년 가격이 전년보다 얼마나 올랐는지 비교해줘"

        validate_period(period, table, query=query, baseline_extension=extension)  # no raise

        with pytest.raises(StageFailure) as exc:
            validate_period(period, table, query=query, baseline_extension=0)
        assert exc.value.code == "explicit_period_mismatch", (freq, table["table_id"])
        checked += 1

    assert checked >= 2, f"no sampled {freq} table had enough coverage headroom to run this check"


def test_unparseable_relative_wording_stays_unresolved_across_frequencies(tables_by_freq, config):
    """Bare "지금 상황을" (no attached count, no "올해"/"작년"/"YYYY년 이후")
    must stay a deterministic refusal on every sampled table of every
    frequency -- never silently resolved, never a crash."""
    for freq, tables in tables_by_freq.items():
        for table in tables:
            with pytest.raises(StageFailure) as exc:
                resolve_relative_period(f"지금 상황을 {_WORDING[freq]}로 보여줘", table, config)
            assert exc.value.code == "conditions_incomplete"
            assert exc.value.status == "unresolved"
