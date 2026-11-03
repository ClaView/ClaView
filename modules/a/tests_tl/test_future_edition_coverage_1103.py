"""Future dates already held by compatible editions are covered, never silently shortened."""

from types import SimpleNamespace

import pytest
from test_period_semantics_1102 import F, P

from claview_a import periods as PR
from claview_a.periods import semantic as S

AS_OF = "2026-10-29"


def annual(table_id, start, end, unit="원"):
    return SimpleNamespace(table_id=table_id, periods=[("Y", start, end)], table_units=[unit])


def select(views, *, rep="OLD", usable=None, relations=(), form="absolute"):
    row = P("requested period", form=form, start="2015" if form == "absolute" else "none",
            end="2027", count="13" if form == "relative" else "none",
            unit="Y" if form == "relative" else "none")
    reading = S.read_rows([row], [F("annual", "Y")], AS_OF)
    assert reading["problems"] == []
    return PR.final_tables(rep, list(views), dict.fromkeys(views, "ok") if usable is None else usable,
                           views, reading["conditions"], AS_OF, relations=list(relations))


@pytest.mark.parametrize("rep", ["OLD", "NEW"])
@pytest.mark.parametrize("form", ["absolute", "relative"])
def test_compatible_actual_coverage_of_future_request_can_be_stitched(rep, form):
    views = {"OLD": annual("OLD", "2015", "2020"), "NEW": annual("NEW", "2021", "2027")}
    got = select(views, rep=rep, form=form)
    assert got["status"] == "selected", got
    assert got["detail"] == "stitched"
    by_table = {row["table_id"]: row for row in got["tables"]}
    assert by_table["OLD"]["period"] == [{"start": "2015-01", "end": "2020-12"}]
    assert by_table["NEW"]["period"] == [{"start": "2021-01", "end": "2027-12"}]
    assert all(not row.get("latest_unpublished") for row in got["tables"])
    shares = [month for row in got["tables"] for month in PR._need(PR._months(row["period"]))]
    assert len(shares) == len(set(shares)) == 13 * 12
    assert min(shares) == PR.P.month("2015-01") and max(shares) == PR.P.month("2027-12")


@pytest.mark.parametrize("future_end", ["2025", "2026"])
def test_an_actual_missing_future_tail_still_fails_without_shortening(future_end):
    views = {"OLD": annual("OLD", "2015", "2020"), "NEW": annual("NEW", "2021", future_end)}
    got = select(views)
    assert got["status"] == "failed" and got["tables"] == []
    assert got["reason"] == "period_not_covered" and got["detail"] == "after_as_of"


@pytest.mark.parametrize("blocked_by", ["unit", "retention", "relation"])
def test_future_coverage_from_an_incompatible_or_unverified_candidate_is_not_used(blocked_by):
    views = {"OLD": annual("OLD", "2015", "2020"),
             "NEW": annual("NEW", "2021", "2027", unit="달러" if blocked_by == "unit" else "원")}
    usable = {"OLD": "ok"} if blocked_by == "retention" else None
    relations = [{"tables": ["OLD", "NEW"], "allows": False}] if blocked_by == "relation" else []
    got = select(views, usable=usable, relations=relations)
    assert got["status"] == "failed" and got["tables"] == []


def test_a_gap_between_actual_editions_does_not_become_complete():
    views = {"OLD": annual("OLD", "2015", "2020"), "NEW": annual("NEW", "2022", "2027")}
    got = select(views)
    assert got["status"] == "failed" and got["tables"] == []


def test_actual_future_coverage_does_not_bypass_the_existing_stitch_limit():
    views = {"OLD": annual("OLD", "2015", "2017"), "T2": annual("T2", "2018", "2020"),
             "T3": annual("T3", "2021", "2023"), "T4": annual("T4", "2024", "2027")}
    got = select(views)
    assert got["status"] == "failed" and got["tables"] == []
    assert got["reason"] == "stitch_limit_exceeded"
