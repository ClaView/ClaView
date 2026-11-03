"""User decision 2026-11-03: hand B an unchanged explicit request with a future tail."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_conditions_56 import go, question, reply
from test_period_semantics_1102 import F, P
from test_reselection_guard_1103 import checked, period_fixture

from claview_a import answers as AN
from claview_a import periods as PR
from claview_a.periods import semantic as S

AS_OF = "2026-10-29"


def table(tid="T", start="2015", end="2025", frequency="Y", unit="원"):
    return SimpleNamespace(table_id=tid, periods=[(frequency, start, end)], table_units=[unit])


def selected(views, *, start="2015", end="2027", frequency="Y", rep=None, usable=None, relations=(),
             form="absolute", extra_periods=()):
    row = P("명시 기간", start=start, end=end, form=form)
    reading = S.read_rows([row, *extra_periods], [F("주기", frequency)], AS_OF)
    assert not reading["problems"]
    return PR.final_tables(rep or next(iter(views)), list(views), dict.fromkeys(views, "ok") if usable is None else usable,
                           views, reading["conditions"], AS_OF, relations=list(relations))


@pytest.mark.parametrize("frequency,start,end,covered_end,unpublished_end,future_start", [
    ("Y", "2015", "2025", "2025-12", "2026-12", "2027-01"),
    ("H", "2015 1/2", "2026 1/2", "2026-06", "2026-12", "2027-01"),
    ("Q", "2015 1/4", "2026 2/4", "2026-06", "2026-12", "2027-01"),
    ("M", "2015.01", "2026.07", "2026-07", "2026-10", "2026-11"),
])
def test_explicit_request_is_kept_and_missing_types_are_separate(
        frequency, start, end, covered_end, unpublished_end, future_start):
    got = selected({"T": table(start=start, end=end, frequency=frequency)}, frequency=frequency)
    assert got["status"] == "selected"
    assert got["tables"][0]["period"] == [{"start": "2015-01", "end": "2027-12"}]
    detail = got["coverage_notice"]
    assert detail["requested"] == got["tables"][0]["period"]
    assert detail["covered"] == [{"start": "2015-01", "end": covered_end}]
    assert detail["missing_kinds"][-1] == {"start": future_start, "end": "2027-12", "kind": "after_as_of"}
    assert detail["missing_kinds"][0]["kind"] == "latest_unpublished"
    assert detail["missing_kinds"][0]["end"] == unpublished_end
    assert got["tables"][0]["latest_unpublished"] == [
        {k: v for k, v in detail["missing_kinds"][0].items() if k != "kind"}]


@pytest.mark.parametrize("start,end", [("2027", "same"), ("2027", "2028"), ("2010", "2027")])
def test_no_overlap_or_past_start_shortfall_still_fails(start, end):
    got = selected({"T": table()}, start=start, end=end)
    assert got["status"] == "failed" and got["tables"] == []
    assert "coverage_notice" not in got


def test_fully_covered_table_still_precedes_a_partial_table():
    got = selected({"OLD": table("OLD"), "NEW": table("NEW", end="2027")})
    assert got["tables"][0]["table_id"] == "NEW"
    assert not got.get("coverage_notice")


@pytest.mark.parametrize("blocked_by", ["unit", "retention", "relation"])
def test_an_incompatible_later_table_cannot_be_hidden_by_partial_selection(blocked_by):
    views = {"OLD": table("OLD"), "NEW": table("NEW", end="2026", unit="달러" if blocked_by == "unit" else "원")}
    usable = {"OLD": "ok"} if blocked_by == "retention" else None
    relations = [{"tables": ["OLD", "NEW"], "allows": False}] if blocked_by == "relation" else []
    got = selected(views, usable=usable, relations=relations)
    assert got["status"] == "failed" and got["tables"] == []


def test_newer_compatible_partial_table_is_used_without_changing_the_request():
    got = selected({"OLD": table("OLD"), "NEW": table("NEW", end="2026")})
    assert got["tables"][0]["table_id"] == "NEW"
    assert got["tables"][0]["period"] == [{"start": "2015-01", "end": "2027-12"}]
    assert got["coverage_notice"]["missing_kinds"] == [
        {"start": "2027-01", "end": "2027-12", "kind": "after_as_of"}]
    assert not got["tables"][0].get("latest_unpublished")


def test_disjoint_requests_do_not_gain_a_new_partial_selection_route():
    got = selected({"T": table()}, start="2015", end="2020", extra_periods=[P("다른 기간", start="2027", end="2028")])
    assert got["status"] == "failed"
    assert not got.get("coverage_notice")


def test_normal_check_keeps_period_and_records_available_and_future_ranges():
    q, out, cards, views = period_fixture()
    q = q.replace("2025", "2027")
    out["periods"][0].update(quote="2010~2027년", end="2027")
    out["choices"][0]["table"] = "g2.t1"
    first = deepcopy(out)
    record = checked(q, out, cards, views)
    d = record["decisions"][0]
    assert d["outcome"] == "selected"
    assert d["final_tables"][0]["period"] == [{"start": "2010-01", "end": "2027-12"}]
    warning = next(w for w in d["warnings"] if w["code"] == "period_partial_coverage")
    assert warning["detail"]["requested"] == d["final_tables"][0]["period"]
    assert warning["detail"]["missing_kinds"][-1]["kind"] == "after_as_of"
    assert out == first
    assert not record["rejudge"]["targets"]


def test_typed_user_answer_keeps_exact_future_end_and_user_source(layer):
    state, _ = go(question(word="월별로"), layer, frequencies=["월별"])
    ask = next(q for q in AN.questions(state) if q["kind"] == "period")
    result = AN.apply(state, [reply(state, ask, text="2020년부터 2027년까지")], layer)
    d = result["record"]["decisions"][0]
    assert d["outcome"] == "selected"
    assert d["final_tables"][0]["period"] == [{"start": "2020-01", "end": "2027-12"}]
    assert d["final_tables"][0]["period_phrase"]["source"] == "user_answer"
    assert d["condition_answers"][0]["text"] == "2020년부터 2027년까지"
