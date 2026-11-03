"""Explicit requests retain their whole range through edition assignment, without calls."""

from types import SimpleNamespace

import pytest
from test_period_semantics_1102 import F, P

from claview_a import periods as PR
from claview_a.periods import semantic as S

AS_OF = "2026-10-29"


def annual(table_id, start, end):
    return SimpleNamespace(table_id=table_id, periods=[("Y", start, end)], table_units=["원"])


def resolve(start, end, views, rep=None, form="absolute", count="none"):
    period = P("requested period", form=form, start=start, end=end, count=count,
               unit="Y" if form == "relative" else "none")
    reading = S.read_rows([period], [F("annual", "Y")], AS_OF)
    assert not reading["problems"]
    return PR.final_tables(rep or next(iter(views)), list(views), dict.fromkeys(views, "ok"),
                           views, reading["conditions"], AS_OF, relations=[])


@pytest.mark.parametrize("start,end", [("2015", "2027"), ("2027", "same")])
def test_missing_future_absolute_range_is_not_shortened_into_success(start, end):
    got = resolve(start, end, {"T": annual("T", "2015", "2025")})
    if start == "2015":
        # 2026-11-03: permit B's partial query, while preserving the full explicit request.
        assert got["status"] == "selected"
        assert got["tables"][0]["period"] == [{"start": "2015-01", "end": "2027-12"}]
        assert got["coverage_notice"]["covered"] == [{"start": "2015-01", "end": "2025-12"}]
        return
    assert got["status"] == "failed"
    assert got["tables"] == []
    assert got["reason"] == "period_not_covered"
    assert got["detail"] == "after_as_of"


@pytest.mark.parametrize("rep", ["OLD", "NEW"])
def test_actual_full_future_coverage_is_not_rejected_by_other_candidate(rep):
    views = {"OLD": annual("OLD", "2015", "2025"), "NEW": annual("NEW", "2015", "2027")}
    got = resolve("2015", "2027", views, rep)
    assert got["status"] == "selected"
    assert got["tables"][0]["table_id"] == "NEW"
    assert got["tables"][0]["period"] == [{"start": "2015-01", "end": "2027-12"}]
    assert not got["tables"][0].get("latest_unpublished")


def test_current_year_unpublished_request_stays_on_single_table():
    got = resolve("2015", "2026", {"T": annual("T", "2015", "2025")})
    assert got["status"] == "selected"
    assert got["tables"][0]["period"] == [{"start": "2015-01", "end": "2026-12"}]
    assert got["tables"][0]["latest_unpublished"] == [{"start": "2026-01", "end": "2026-12"}]


@pytest.mark.parametrize("form", ["absolute", "relative"])
def test_stitch_keeps_unpublished_tail_on_the_responsible_table(form):
    views = {"OLD": annual("OLD", "2010", "2020"), "NEW": annual("NEW", "2021", "2025")}
    got = resolve("2018" if form == "absolute" else "none", "2026", views,
                  form=form, count="9" if form == "relative" else "none")
    assert got["status"] == "selected"
    assert got["detail"] == "stitched"
    by_table = {row["table_id"]: row for row in got["tables"]}
    assert by_table["OLD"]["period"] == [{"start": "2018-01", "end": "2020-12"}]
    assert by_table["NEW"]["period"] == [{"start": "2021-01", "end": "2026-12"}]
    assert not by_table["OLD"].get("latest_unpublished")
    assert by_table["NEW"]["latest_unpublished"] == [{"start": "2026-01", "end": "2026-12"}]
    requested = PR._need([(PR.P.month("2018-01"), PR.P.month("2026-12"))])
    shares = [month for row in got["tables"] for month in PR._need(PR._months(row["period"]))]
    assert set(shares) == requested
    assert len(shares) == len(set(shares))


def test_stitch_does_not_extend_a_fully_published_request():
    views = {"OLD": annual("OLD", "2010", "2020"), "NEW": annual("NEW", "2021", "2025")}
    got = resolve("2018", "2025", views)
    assert got["status"] == "selected"
    by_table = {row["table_id"]: row for row in got["tables"]}
    assert by_table["OLD"]["period"] == [{"start": "2018-01", "end": "2020-12"}]
    assert by_table["NEW"]["period"] == [{"start": "2021-01", "end": "2025-12"}]
    assert all(not row.get("latest_unpublished") for row in got["tables"])
