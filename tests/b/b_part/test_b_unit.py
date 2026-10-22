"""Direct unit tests for B's deterministic validation logic (claview.b.stages),
using a small hand-built table instead of the full 349-table catalog.

golden_set_v1.json only exercises the "happy path" plus two documented
policy gaps (see test_b_golden.py); it never triggers most of B's guard
rails (duplicate selections, unit conflicts, out-of-range periods, axis
pruning). Those are exactly the failure modes B exists to catch before bad
data reaches fetch.py, so they are covered here with synthetic fixtures.
"""

from __future__ import annotations

import pytest

from claview.b.contracts import Config, StageFailure
from claview.b.metadata import normalize_period, period_number, periods_between
from claview.b.stages import (
    build_b_context,
    build_b_schema,
    canonicalize_selections,
    detect_baseline_extension,
    infer_frequency,
    resolve_relative_period,
    validate_period,
)


def make_table(**overrides) -> dict:
    table = {
        "org_id": "301",
        "table_id": "DT_TEST001",
        "table_name": "테스트 표",
        "items": {"ITEM1": {"name": "테스트 지표", "unit": None, "parent_code": None}},
        "dimensions": {
            "objL1": {
                "name": "구분",
                "values": {
                    "A": {
                        "name": "원화기준",
                        "unit": "%",
                        "parent_code": None,
                        "path": [{"code": "A", "name": "원화기준"}],
                        "is_leaf": True,
                    },
                    "B": {
                        "name": "달러기준",
                        "unit": "달러",
                        "parent_code": None,
                        "path": [{"code": "B", "name": "달러기준"}],
                        "is_leaf": True,
                    },
                },
            }
        },
        "periods": {
            "Y": {"start": "2010", "end": "2025"},
            "M": {"start": "201001", "end": "202512"},
        },
        "notes": [],
        "units": ["%", "달러"],
        "snapshot_id": "test",
        "warnings": [],
    }
    table.update(overrides)
    return table


def make_row(period, axis_code="A"):
    return {"item_id": "ITEM1", "dimensions": {"objL1": axis_code}, "period": period}


def test_canonicalize_selections_happy_path():
    table = make_table()
    raw = {"selections": [make_row({"frequency": "Y", "start": "2020", "end": "2024"})]}
    rows = canonicalize_selections(raw, table, Config())
    assert len(rows) == 1
    assert rows[0]["unit"] == "%"
    assert rows[0]["dimensions"]["objL1"]["name"] == "원화기준"


def test_canonicalize_selections_rejects_empty_selections():
    table = make_table()
    with pytest.raises(StageFailure) as exc:
        canonicalize_selections({"selections": []}, table, Config())
    assert exc.value.code == "conditions_incomplete"
    assert exc.value.status == "unresolved"


def test_canonicalize_selections_rejects_duplicates():
    table = make_table()
    period = {"frequency": "Y", "start": "2020", "end": "2024"}
    raw = {"selections": [make_row(period), make_row(period)]}
    with pytest.raises(StageFailure) as exc:
        canonicalize_selections(raw, table, Config())
    assert exc.value.code == "duplicate_selection"


def test_validate_period_rejects_relative_wording_even_with_a_year():
    table = make_table()
    period = {"frequency": "Y", "start": "2024", "end": "2024"}
    with pytest.raises(StageFailure) as exc:
        validate_period(period, table, query="2024년 최근 동향을 보여줘")
    assert exc.value.code == "conditions_incomplete"
    assert exc.value.status == "unresolved"


def test_validate_period_rejects_out_of_table_range():
    table = make_table()
    period = {"frequency": "Y", "start": "2005", "end": "2024"}
    with pytest.raises(StageFailure) as exc:
        validate_period(period, table, query=None)
    assert exc.value.code == "metadata_refresh_required"
    assert exc.value.status == "unresolved"


def test_validate_period_rejects_mismatched_explicit_years():
    table = make_table()
    period = {"frequency": "Y", "start": "2020", "end": "2024"}
    with pytest.raises(StageFailure) as exc:
        validate_period(period, table, query="2015~2019년 추이를 보여줘")
    assert exc.value.code == "explicit_period_mismatch"


def test_validate_period_accepts_explicit_matching_years():
    table = make_table()
    period = {"frequency": "Y", "start": "2020", "end": "2024"}
    validate_period(period, table, query="2020년부터 2024년까지 추이를 보여줘")  # no raise


def test_selected_unit_conflict_rejected_through_canonicalize():
    """Two axis codes whose units disagree (%, 달러) with no table-level
    single unit must raise unit_conflict, not silently pick one."""
    table = make_table()
    table["items"]["ITEM1"]["unit"] = None
    raw = {
        "selections": [
            {
                "item_id": "ITEM1",
                "dimensions": {"objL1": "A"},
                "period": {"frequency": "Y", "start": "2020", "end": "2024"},
            }
        ]
    }
    # Sanity: single-axis selection resolves to one unit fine.
    rows = canonicalize_selections(raw, table, Config())
    assert rows[0]["unit"] == "%"

    # Force a genuine conflict: item declares a unit that disagrees with
    # the chosen axis value's unit.
    conflicting = make_table()
    conflicting["items"]["ITEM1"]["unit"] = "달러"
    with pytest.raises(StageFailure) as exc:
        canonicalize_selections(raw, conflicting, Config())
    assert exc.value.code == "unit_conflict"
    assert exc.value.status == "unresolved"


def test_build_b_schema_enumerates_only_table_codes():
    table = make_table()
    schema = build_b_schema(table, Config())
    item_prop = schema["properties"]["selections"]["items"]["properties"]["item_id"]
    axis_prop = schema["properties"]["selections"]["items"]["properties"]["dimensions"][
        "properties"
    ]["objL1"]
    assert item_prop["enum"] == ["ITEM1"]
    assert sorted(axis_prop["enum"]) == ["A", "B"]


def test_build_b_context_prunes_oversized_axis_but_keeps_ancestors():
    values = {}
    for i in range(500):
        code = f"leaf{i}"
        values[code] = {
            "name": f"항목{i} 반도체" if i == 0 else f"항목{i}",
            "unit": None,
            "parent_code": "root",
            "path": [
                {"code": "root", "name": "전체"},
                {"code": code, "name": f"항목{i}"},
            ],
            "is_leaf": True,
        }
    values["root"] = {
        "name": "전체",
        "unit": None,
        "parent_code": None,
        "path": [{"code": "root", "name": "전체"}],
        "is_leaf": False,
    }
    table = make_table(dimensions={"objL1": {"name": "업종", "values": values}})
    config = Config(max_context_chars=4000, max_axis_candidates=20)
    context = build_b_context("반도체 관련 수치 보여줘", table, config)
    assert "objL1" in context["pruned_axes"]
    kept = context["dimensions"]["objL1"]["values"]
    assert len(kept) <= 21  # candidates + root ancestor
    assert "root" in kept  # ancestor of every kept leaf retained
    assert any("반도체" in v["name"] for v in kept.values())  # query-relevant leaf survives pruning


def test_build_b_context_refuses_broad_scope_pruning():
    values = {
        f"leaf{i}": {
            "name": f"항목{i}",
            "unit": None,
            "parent_code": None,
            "path": [{"code": f"leaf{i}", "name": f"항목{i}"}],
            "is_leaf": True,
        }
        for i in range(500)
    }
    table = make_table(dimensions={"objL1": {"name": "업종", "values": values}})
    config = Config(max_context_chars=2000, max_axis_candidates=20)
    with pytest.raises(StageFailure) as exc:
        build_b_context("업종별 전체 비교해줘", table, config)
    assert exc.value.code == "broad_scope_context_incomplete"
    assert exc.value.status == "unresolved"


# --- 반기(half-year) metadata support -----------------------------------
# Added for the team's 2026-10 decision: 14 of 349 BOK tables report on a
# half-year cadence, and one (DT_105Y001) reports *only* on that cadence --
# it failed to load at all before this support existed.


def test_half_year_frequency_round_trips():
    assert period_number("202501", "H") < period_number("202502", "H")
    assert periods_between("202401", "202502", "H") == [
        "202401",
        "202402",
        "202501",
        "202502",
    ]
    assert normalize_period("2002 1/2", "H") == "200201"
    assert normalize_period("2025 2/2", "H") == "202502"


def test_validate_period_accepts_half_year_table():
    table = make_table(periods={"H": {"start": "202001", "end": "202502"}})
    period = {"frequency": "H", "start": "202002", "end": "202502"}
    validate_period(period, table, query=None)  # no raise


# --- infer_frequency ------------------------------------------------------


def test_infer_frequency_from_explicit_wording():
    table = make_table()
    assert infer_frequency("분기별로 보여줘", table) == "Q"
    assert infer_frequency("월별로 보여줘", table) == "M"
    assert infer_frequency("연도별로 정리해줘", table) == "Y"


def test_infer_frequency_falls_back_to_the_tables_only_frequency():
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    assert infer_frequency("지역별로 비교해줘", table) == "Y"  # table has only Y, no other reading


def test_infer_frequency_ambiguous_with_multiple_table_frequencies():
    table = make_table()  # has both Y and M, query names neither
    assert infer_frequency("비교해줘", table) is None


# --- resolve_relative_period ----------------------------------------------
# Verified against golden_set_v1.json's own 8 relative-period cases in
# test_b_golden.py; these synthetic cases isolate the edge behavior that
# golden set doesn't happen to exercise (no-gap/resolved path, clamping to
# the table's earliest data, unparseable wording).


def test_resolve_relative_period_no_gap_resolves_without_confirmation():
    """When the table's latest data already covers what as_of implies,
    there is nothing to confirm -- proceed with the resolved period."""
    table = make_table()  # M coverage ends 202512
    config = Config(as_of="2025-12-15")
    result = resolve_relative_period("최근 3년간 월별", table, config)
    assert result == {
        "status": "resolved",
        "period": {"frequency": "M", "start": "202301", "end": "202512"},
    }


def test_resolve_relative_period_gap_needs_confirmation():
    table = make_table()
    config = Config(as_of="2026-03-01")  # table's M data still ends 202512
    result = resolve_relative_period("최근 3년간 월별", table, config)
    assert result["status"] == "needs_confirmation"
    assert result["code"] == "period_requires_confirmation"
    assert result["result"]["resolved_period"] == {
        "frequency": "M",
        "start": "202301",
        "end": "202512",
    }
    message = result["result"]["message"]
    assert "조회 기간: 202301 ~ 202512" in message
    assert "202603까지 필요" in message and "202512" in message
    assert "?" not in message  # informs; never asks (2026-10-06 flow change)


def test_resolve_relative_period_since_year():
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    result = resolve_relative_period("2020년 이후 연도별", table, Config(as_of="2025-12-15"))
    assert result == {
        "status": "resolved",
        "period": {"frequency": "Y", "start": "2020", "end": "2025"},
    }


def test_resolve_relative_period_clamps_to_table_coverage():
    """ "최근 100년간" on a table that only goes back to 2010 must clamp to
    the table's own earliest period, not invent data -- and clamping on
    its own forces a confirmation even when as_of lines up with the
    table's latest period, since the extractable range is still narrower
    than literally requested."""
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    result = resolve_relative_period("최근 100년간 연도별", table, Config(as_of="2025-12-15"))
    assert result["status"] == "needs_confirmation"
    assert result["result"]["resolved_period"]["start"] == "2010"
    # as_of lines up with the latest period, so the notice must say the *start* is
    # what the table lacks -- not claim the table is behind as_of.
    assert "수록되어 있지 않아" in result["result"]["message"]
    assert "?" not in result["result"]["message"]


def test_resolve_relative_period_last_year_notice_informs_without_asking():
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    # "작년" on a table that starts after last year -> clamped, notice for last year
    short = make_table(periods={"Y": {"start": "2025", "end": "2025"}})
    result = resolve_relative_period("작년 연도별", short, Config(as_of="2026-10-01"))
    assert result["status"] == "needs_confirmation"
    assert "작년(2024년)" in result["result"]["message"]
    assert "?" not in result["result"]["message"]
    # fully covered last year -> nothing to notice
    assert resolve_relative_period("작년 연도별", table, Config(as_of="2026-10-01"))["status"] == (
        "resolved"
    )


def test_resolve_relative_period_unparseable_wording_stays_unresolved():
    table = make_table(periods={"Y": {"start": "2010", "end": "2025"}})
    with pytest.raises(StageFailure) as exc:
        resolve_relative_period("지금 상황을 연도별로 보여줘", table, Config())
    assert exc.value.code == "conditions_incomplete"
    assert exc.value.status == "unresolved"


# --- "최근 N년/분기/반기/개월": one count-back rule for every frequency -----
# Team decisions 2026-10-06: 반기 and then 분기 both follow the same sliding
# window as 연/월 -- "N years" = N * periods-per-year periods counted back from
# the table's own latest recorded period ("5년치 분기"는 20개, "5년치 반기"는
# 10개). The earlier Q-only calendar-year-anchored exception (golden CV-AB-12,
# 202101) was removed and CV-AB-12's expected period corrected to 202103. These
# synthetic cases pin that there is no frequency-specific branch left, so one
# can't silently reappear.


def test_resolve_relative_period_quarterly_recent_years_is_a_sliding_window():
    """ "최근 5년간" on a Q table anchored at 202602 (same shape as golden
    CV-AB-12): exactly 20 quarters, 202103 ~ 202602."""
    table = make_table(periods={"Q": {"start": "199404", "end": "202602"}})
    result = resolve_relative_period("최근 5년간 분기별", table, Config(as_of="2026-10-01"))
    resolved = result["result"]["resolved_period"]
    assert resolved == {"frequency": "Q", "start": "202103", "end": "202602"}
    assert period_number(resolved["end"], "Q") - period_number(resolved["start"], "Q") + 1 == 20


def test_resolve_relative_period_quarterly_recent_years_counts_back_from_the_table_anchor():
    """The window must be counted back from the table's own latest recorded
    period (bounds['end']), not config.as_of -- anchor here is 2025Q4 while
    as_of is in 2026. At a Q4 anchor sliding == N full calendar years."""
    table = make_table(periods={"Q": {"start": "199404", "end": "202504"}})
    result = resolve_relative_period("최근 5년간 분기별", table, Config(as_of="2026-10-01"))
    resolved = result["result"]["resolved_period"]
    assert resolved == {"frequency": "Q", "start": "202101", "end": "202504"}


def test_resolve_relative_period_quarterly_recent_quarters_is_the_same_rule():
    table = make_table(periods={"Q": {"start": "199404", "end": "202602"}})
    result = resolve_relative_period("최근 8분기 분기별", table, Config(as_of="2026-10-01"))
    resolved = result["result"]["resolved_period"]
    assert resolved == {"frequency": "Q", "start": "202403", "end": "202602"}


def test_resolve_relative_period_monthly_recent_years_sliding_window():
    table = make_table(periods={"M": {"start": "199601", "end": "202607"}})
    result = resolve_relative_period("최근 5년간 월별", table, Config(as_of="2026-10-01"))
    resolved = result["result"]["resolved_period"]
    assert resolved == {"frequency": "M", "start": "202108", "end": "202607"}


def test_resolve_relative_period_semiannual_recent_years_sliding_window():
    """반기: sliding window, team decision 2026-10-06 ("5년치 반기 = 10개")."""
    table = make_table(periods={"H": {"start": "200201", "end": "202602"}})
    result = resolve_relative_period("최근 5년간 반기별", table, Config(as_of="2026-10-01"))
    # as_of (Oct 2026, H2) lines up with this table's own latest period, so
    # this one resolves outright instead of needing confirmation.
    assert result == {
        "status": "resolved",
        "period": {"frequency": "H", "start": "202201", "end": "202602"},
    }


@pytest.mark.parametrize(
    ("freq", "end", "per_year"),
    [("Y", "2025", 1), ("H", "202601", 2), ("Q", "202602", 4), ("M", "202607", 12)],
)
@pytest.mark.parametrize("n", [1, 2, 5])
def test_recent_n_years_is_n_times_periods_per_year_for_every_frequency(freq, end, per_year, n):
    """The unified rule as one invariant: "최근 N년" always returns exactly
    N * periods-per-year periods ending at the table's latest period."""
    table = make_table(periods={freq: {"start": "199001" if freq != "Y" else "1990", "end": end}})
    result = resolve_relative_period(f"최근 {n}년간", table, Config(as_of="2026-10-01"))
    resolved = (
        result["period"] if result["status"] == "resolved" else result["result"]["resolved_period"]
    )
    assert resolved["end"] == end
    assert (
        period_number(resolved["end"], freq) - period_number(resolved["start"], freq) + 1
        == n * per_year
    )


# --- MoM/YoY baseline extension --------------------------------------------


def test_detect_baseline_extension_by_calc_type():
    assert detect_baseline_extension("전년보다 얼마나 올랐는지", "M") == 12
    assert detect_baseline_extension("전년동기 대비 증가", "Q") == 4
    assert detect_baseline_extension("전월대비 상승폭", "M") == 1
    assert detect_baseline_extension("가장 크게 변한 달", "M") == 1
    assert detect_baseline_extension("그냥 추이를 보여줘", "M") == 0


def test_canonicalize_selections_extends_baseline_for_mom_query():
    """ "가장 크게 변했는지" (MoM) over a stated 2024년 needs 2023년 12월 as a
    baseline point -- validate_period must accept the extra leading month
    via detect_baseline_extension, without the model or caller computing
    it by hand."""
    table = make_table(periods={"M": {"start": "202001", "end": "202412"}})
    raw = {"selections": [make_row({"frequency": "M", "start": "202312", "end": "202412"})]}
    rows = canonicalize_selections(
        raw, table, Config(), query="2024년에 어느 달에 가장 크게 변했는지 보여줘"
    )
    assert rows[0]["period"] == {"frequency": "M", "start": "202312", "end": "202412"}


def test_canonicalize_selections_extends_baseline_for_yoy_query():
    table = make_table(periods={"M": {"start": "202001", "end": "202412"}})
    raw = {"selections": [make_row({"frequency": "M", "start": "202301", "end": "202412"})]}
    rows = canonicalize_selections(
        raw, table, Config(), query="2024년 가격이 전년보다 얼마나 올랐는지 비교해줘"
    )
    assert rows[0]["period"] == {"frequency": "M", "start": "202301", "end": "202412"}


def test_canonicalize_selections_still_rejects_unextended_mismatch():
    """Same shifted-start period as above, but with no comparison keyword
    in the query -- must still be rejected: the extension is earned by the
    query's own wording, never a standing allowance."""
    table = make_table(periods={"M": {"start": "202001", "end": "202412"}})
    raw = {"selections": [make_row({"frequency": "M", "start": "202312", "end": "202412"})]}
    with pytest.raises(StageFailure) as exc:
        canonicalize_selections(raw, table, Config(), query="2024년 추이를 보여줘")
    assert exc.value.code == "explicit_period_mismatch"
