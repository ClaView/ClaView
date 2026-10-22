"""Shared period and frequency resolver (plan 04): rules in both directions, on synthetic tables."""

from __future__ import annotations

import copy

import pytest

from claview.b.period_resolver import DEFAULT_RECENT_YEARS, resolve_period_for_table

AS_OF = "2026-10-20"
TABLE = {
    "periods": {
        "Y": {"start": "2000", "end": "2025"},
        "Q": {"start": "200001", "end": "202602"},
        "M": {"start": "200001", "end": "202608"},
    }
}
HALF_TABLE = {"periods": {"H": {"start": "201001", "end": "202601"}}}
SINGLE_M = {"periods": {"M": {"start": "201501", "end": "202608"}}}


def expr(year=None, alias=None, sub_unit=None, sub_value=None, end_mark=False):
    return {
        "text": "t",
        "year": year,
        "alias": alias,
        "sub_unit": sub_unit,
        "sub_value": sub_value,
        "end_mark": end_mark,
    }


def period(form, start=None, end=None, amount=None, unit=None, relative=False, **extra):
    attrs = {
        "form": form,
        "start_expr": start,
        "end_expr": end,
        "open_end": form == "since",
        "amount": amount,
        "unit": unit,
        "relative": relative,
        "invalid": False,
        "extract_status": "explicit",
        **extra,
    }
    return {"id": "P1", "kind": "period", "quote": "q", "value": None, "attrs": attrs}


def freq(value):
    return {
        "id": "F1",
        "kind": "frequency",
        "quote": "q",
        "value": value,
        "attrs": {"value": value},
    }


def calc(value):
    return {"id": "K1", "kind": "calc", "quote": "q", "value": value, "attrs": {"value": value}}


def run(conditions, table=TABLE, as_of=AS_OF):
    return resolve_period_for_table(conditions, table, as_of)


def applied(result):
    a = result["applied"]
    return a["frequency"], a["start"], a["end"]


# ---------------------------------------------------------------- frequency


def test_a_stated_frequency_is_used():
    result = run([freq("Q"), period("range", expr("2020"), expr("2023"))])
    assert result["status"] == "ok"
    assert applied(result) == ("Q", "202001", "202304")
    assert result["slots"]["frequency"] == {
        "value": "Q",
        "source": "question",
        "confirmed": True,
        "applied": "Q",
        "rule": "question_word",
        "alternatives": [],
        "hint_state": "absent",
    }
    assert result["assumptions"] == []


def test_a_stated_frequency_the_table_lacks_makes_the_table_unsuitable():
    result = run([freq("H")])
    assert result["status"] == "table_unsuitable"
    assert result["issue"] == {"code": "table_unsuitable", "detail": "frequency_not_in_table"}
    assert result["applied"] is None


def test_two_different_stated_frequencies_are_unresolved():
    result = run([freq("Q"), {**freq("M"), "id": "F2"}])
    assert result["status"] == "unresolved"
    assert result["issue"]["code"] == "conditions_incomplete"


@pytest.mark.parametrize(
    "condition, expected",
    [
        (period("recent", amount=3, unit="month", relative=True), "M"),
        (period("point", expr("2024", sub_unit="Q", sub_value="3")), "Q"),
    ],
)
def test_the_unit_of_the_period_gives_the_frequency(condition, expected):
    result = run([condition])
    assert result["frequency"] == expected
    assert result["slots"]["frequency"]["rule"] == "period_unit"
    assert result["slots"]["frequency"]["source"] == "question"


def test_a_period_unit_the_table_lacks_does_not_drop_the_table():
    """Stage 3 (user-approved change of the 118 expectation): a frequency read from the period's
    unit is an assumption only; the table lacks H, so the longest frequency is used and noticed."""
    result = run([period("point", expr("2024", sub_unit="H", sub_value="상"))])
    assert result["status"] == "ok"
    assert applied(result) == ("Y", "2024", "2024")
    assert result["slots"]["frequency"]["rule"] == "longest_frequency"
    assert {"dimension": "frequency", "value": "Y", "rule": "longest_frequency"} in result[
        "assumptions"
    ]
    assert {
        "code": "inferred_frequency_unavailable",
        "wanted": ["H"],
        "used": "Y",
        "hint_state": "absent",
    } in result["notices"]


def test_a_year_unit_does_not_choose_the_frequency():
    result = run([period("recent", amount=3, unit="year", relative=True)])
    assert result["slots"]["frequency"]["rule"] == "longest_frequency"


def test_the_only_frequency_of_the_table_is_catalog_and_confirmed():
    result = run([period("range", expr("2020"), expr("2021"))], SINGLE_M)
    assert result["slots"]["frequency"]["source"] == "catalog"
    assert result["slots"]["frequency"]["confirmed"] is True
    assert applied(result) == ("M", "202001", "202112")
    assert result["assumptions"] == []


def test_no_frequency_word_takes_the_longest_and_offers_the_others():
    result = run([period("range", expr("2020"), expr("2021"))])
    assert result["frequency"] == "Y"
    slot = result["slots"]["frequency"]
    assert slot["source"] == "default" and slot["confirmed"] is False
    assert slot["rule"] == "longest_frequency"
    assert result["assumptions"] == [
        {"dimension": "frequency", "value": "Y", "rule": "longest_frequency"}
    ]
    assert result["alternatives"] == [
        {"frequency": "Q", "applied": {"frequency": "Q", "start": "202001", "end": "202104"}},
        {"frequency": "M", "applied": {"frequency": "M", "start": "202001", "end": "202112"}},
    ]
    assert slot["alternatives"] == ["Q", "M"]


# ---------------------------------------------------------------- period


@pytest.mark.parametrize(
    "f, expected",
    [
        ("Y", ("Y", "2016", "2025")),
        ("Q", ("Q", "202103", "202602")),
        ("M", ("M", "202309", "202608")),
    ],
)
def test_no_period_assumes_the_recent_window_of_the_frequency(f, expected):
    result = run([freq(f)])
    assert applied(result) == expected
    assert result["source"] == "default"
    assert result["slots"]["period"]["confirmed"] is False
    rule = f"recent_years:{f}={DEFAULT_RECENT_YEARS[f]}"
    assert {"dimension": "period", "value": result["applied"], "rule": rule} in result[
        "assumptions"
    ]
    assert result["notice"] is not None or f == "Y"  # the table lags behind as_of for Q and M


def test_default_window_numbers_are_the_reported_ones():
    assert DEFAULT_RECENT_YEARS == {"Y": 10, "H": 5, "Q": 5, "M": 3}


@pytest.mark.parametrize("f, count", [("Y", 5), ("Q", 20), ("M", 60)])
def test_recent_years_count_back_from_the_tables_latest_period(f, count):
    result = run([freq(f), period("recent", amount=5, unit="year", relative=True)])
    first = {"Y": "2021", "Q": "202103", "M": "202109"}[f]
    assert applied(result)[1] == first
    assert result["relative"] is True
    assert result["source"] == "question"


def test_recent_quarters_on_a_monthly_question_count_months():
    result = run([freq("M"), period("recent", amount=2, unit="quarter", relative=True)])
    assert applied(result) == ("M", "202603", "202608")


def test_recent_months_cannot_be_counted_on_a_quarterly_question():
    result = run([freq("Q"), period("recent", amount=6, unit="month", relative=True)])
    assert result["issue"]["code"] == "conditions_incomplete"


def test_since_runs_to_the_latest_period_and_notices_the_lag():
    result = run([freq("M"), period("since", expr("2015"))])
    assert applied(result) == ("M", "201501", "202608")
    assert result["notice"]["requested_as_of_period"] == "202610"
    assert result["notice"]["resolved_period"] == result["applied"]


def test_no_lag_notice_without_as_of_or_when_the_table_is_current():
    assert run([freq("M"), period("since", expr("2015"))], as_of=None)["notice"] is None
    assert run([freq("M"), period("since", expr("2015"))], as_of="2026-08-03")["notice"] is None


def test_a_range_without_the_end_year_uses_the_start_year():
    result = run(
        [
            period(
                "range",
                expr("2026", sub_unit="M", sub_value="1"),
                expr(None, sub_unit="M", sub_value="6"),
            )
        ]
    )
    assert applied(result) == ("M", "202601", "202606")


def test_a_closed_range_has_no_lag_notice():
    assert run([freq("Y"), period("range", expr("2020"), expr("2023"))])["notice"] is None


def test_an_explicit_range_past_the_latest_period_keeps_the_missing_end():
    """Stage 3 (approved change of 228): query the covered part, keep the missing one."""
    result = run([freq("Y"), period("range", expr("2020"), expr("2026"))])
    assert result["status"] == "ok"
    assert applied(result) == ("Y", "2020", "2025")
    assert result["periods"]["requested"] == [{"start": "2020", "end": "2026"}]
    assert result["periods"]["missing"] == [{"start": "2026", "end": "2026"}]


def test_an_explicit_start_before_the_coverage_keeps_the_missing_start():
    """Stage 3 (approved change of 236): never metadata_refresh_required for coverage."""
    result = run([freq("Y"), period("range", expr("1990"), expr("2001"))])
    assert result["status"] == "ok"
    assert applied(result) == ("Y", "2000", "2001")
    assert result["periods"]["missing"] == [{"start": "1990", "end": "1999"}]


def test_a_relative_window_before_the_coverage_is_clamped_with_a_notice():
    result = run([freq("Y"), period("recent", amount=40, unit="year", relative=True)])
    assert applied(result) == ("Y", "2000", "2025")
    assert result["notice"]["code"] == "period_requires_confirmation"


def test_this_year_and_last_year_follow_the_tables_latest_year():
    this_year = run([freq("M"), period("since", expr(alias="올해"), relative=True)])
    assert applied(this_year) == ("M", "202601", "202608")
    last_year = run([freq("M"), period("point", expr(alias="작년"), relative=True)])
    assert applied(last_year) == ("M", "202501", "202512")
    assert last_year["notice"] is None  # a closed past year is not a lag
    two_years = run([freq("Y"), period("point", expr(alias="재작년"), relative=True)])
    assert applied(two_years) == ("Y", "2023", "2023")


def test_last_year_end_mark_is_the_last_period_of_that_year():
    result = run([freq("M"), period("point", expr(alias="작년", end_mark=True), relative=True)])
    assert applied(result) == ("M", "202512", "202512")


def test_last_month_counts_back_from_the_latest_period():
    result = run([period("point", expr(alias="지난", sub_unit="M"), relative=True)])
    assert applied(result) == ("M", "202607", "202607")


def test_last_month_on_a_quarterly_question_is_unresolved():
    result = run([freq("Q"), period("point", expr(alias="지난", sub_unit="M"), relative=True)])
    assert result["issue"]["code"] in {"conditions_incomplete", "table_unsuitable"}


def test_half_years_read_upper_and_lower_halves():
    upper = run([period("point", expr("2024", sub_unit="H", sub_value="상"))], HALF_TABLE)
    assert applied(upper) == ("H", "202401", "202401")
    lower = run([period("point", expr("2024", sub_unit="H", sub_value="하"))], HALF_TABLE)
    assert applied(lower) == ("H", "202402", "202402")


def test_a_month_on_a_quarterly_question_is_its_quarter():
    result = run([freq("Q"), period("point", expr("2024", sub_unit="M", sub_value="5"))])
    assert applied(result) == ("Q", "202402", "202402")


@pytest.mark.parametrize(
    "value, f, start",
    [("전년동기비", "M", "202301"), ("전기비", "M", "202312"), ("전년동기비", "Y", "2023")],
)
def test_comparison_calcs_start_earlier_by_their_base(value, f, start):
    result = run([freq(f), period("point", expr("2024")), calc(value)])
    assert applied(result)[1] == start
    assert result["slots"]["period"]["rule"].endswith("_base")


def test_other_calcs_do_not_move_the_start():
    result = run([freq("M"), period("point", expr("2024")), calc("최댓값")])
    assert applied(result) == ("M", "202401", "202412")


def test_several_periods_are_enveloped():
    second = {**period("point", expr("2022")), "id": "P2"}
    result = run([freq("Y"), period("point", expr("2018")), second])
    assert applied(result) == ("Y", "2018", "2022")
    assert result["slots"]["period"]["value"] == {"condition_ids": ["P1", "P2"]}


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"invalid": True}, "invalid_input"),
        ({"extract_status": "unresolved"}, "ok"),
        ({"form": "weird"}, "invalid_input"),
    ],
)
def test_periods_the_interpretation_marked(change, status):
    """Stage 3 (user items 4 and the 10/21 follow-up): `invalid` and an unknown form are input
    contract errors (malformed, as the interpretation diagnoses `invalid`); the old
    `extract_status` alone decides nothing, so a period the resolver reads is resolved."""
    condition = period("point", expr("2024"))
    condition["attrs"].update(change)
    result = run([freq("Y"), condition])
    assert result["status"] == status
    if status == "ok":
        assert applied(result) == ("Y", "2024", "2024")
    else:
        assert result["issue"]["code"] == "malformed_condition"
        assert result["applied"] is None


def test_an_unknown_alias_or_sub_value_is_unresolved():
    assert run([freq("Y"), period("point", expr(alias="언젠가"))])["status"] == "unresolved"
    assert (
        run([freq("M"), period("point", expr("2024", sub_unit="M", sub_value="13"))])["status"]
        == "unresolved"
    )


def test_a_table_without_periods_is_unsuitable():
    result = run([], {"periods": {}})
    assert result["status"] == "table_unsuitable"
    assert result["issue"]["detail"] == "table_without_periods"


def test_malformed_conditions_never_raise():
    for bad in ([None, 1, "x"], [{"kind": "period", "attrs": None}], [{"kind": "frequency"}]):
        result = run(bad)
        assert result["status"] == "invalid_input"  # stage 3: an input contract error


def test_inputs_are_not_changed():
    conditions = [freq("M"), period("since", expr("2015")), calc("전년동기비")]
    before = (copy.deepcopy(conditions), copy.deepcopy(TABLE))
    run(conditions)
    assert (conditions, TABLE) == before


def test_ambiguous_kinds_are_ignored():
    """Only period, frequency and calc conditions count; basis, unit and others are not read."""
    basis = {"id": "B1", "kind": "basis", "quote": "q", "value": "원계열", "attrs": {}}
    assert applied(run([freq("Y"), basis])) == applied(run([freq("Y")]))


def test_open_or_relative_periods_do_not_start_earlier_for_a_comparison():
    """ "2015년 이후 ... 성장률(전년동기비)": the rate is a series of the table, the start stays."""
    since = run([freq("Q"), period("since", expr("2015")), calc("전년동기비")])
    assert applied(since)[1] == "201501"
    recent = run(
        [freq("M"), period("recent", amount=1, unit="year", relative=True), calc("전기비")]
    )
    assert applied(recent)[1] == "202509"


def test_an_extreme_over_a_change_starts_one_period_earlier():
    """ "2024년에 ... 어느 달에 가장 크게 변했는지": 최댓값 with a change hint needs 2023-12."""
    conditions = [freq("M"), period("point", expr("2024")), calc("최댓값")]
    result = resolve_period_for_table(
        conditions, TABLE, AS_OF, [{"quote": "변했", "kind": "change"}]
    )
    assert applied(result) == ("M", "202312", "202412")
    assert result["slots"]["period"]["rule"].endswith("largest_change_base")
    rate_only = resolve_period_for_table(
        conditions, TABLE, AS_OF, [{"quote": "증가율", "kind": "rate"}]
    )
    assert applied(rate_only) == ("M", "202401", "202412")


SHORT_M = {"periods": {"M": {"start": "202601", "end": "202608"}}}


def test_a_relative_period_outside_the_coverage_is_not_turned_inside_out():
    """작년 = 2025 on a table recorded from 202601: no overlap, not a reversed 202601~202512."""
    result = run([period("point", expr(alias="작년"), relative=True)], SHORT_M)
    assert result["status"] == "no_data_in_range"  # stage 3, approved change of 382
    assert result["issue"] == {"code": "no_data_in_range", "detail": "requested_outside_coverage"}
    assert result["coverage"] == {"frequency": "M", "start": "202601", "end": "202608"}
    assert result["applied"] is None


def test_a_stated_period_outside_the_coverage_is_kept_missing_next_to_a_relative_one():
    """Stage 3 (approved change of 389): each period is judged on its own."""
    stated = period("point", expr("1990"))
    recent = {**period("recent", amount=3, unit="year", relative=True), "id": "P2"}
    result = run([freq("Y"), stated, recent])
    assert result["status"] == "ok"
    assert applied(result) == ("Y", "2023", "2025")
    assert result["periods"]["missing"] == [{"start": "1990", "end": "1990"}]
    assert result["condition_fields"]["P1"]["applied"] is False
    assert result["condition_fields"]["P2"]["applied"] is True


def test_a_relative_and_a_stated_period_inside_the_coverage_are_enveloped():
    stated = period("point", expr("2018"))
    recent = {**period("recent", amount=3, unit="year", relative=True), "id": "P2"}
    assert applied(run([freq("Y"), stated, recent])) == ("Y", "2018", "2025")


def test_this_year_as_a_point_cut_at_the_latest_period_tells_the_lag():
    result = run([period("point", expr(alias="올해"), relative=True)], SINGLE_M)
    assert applied(result) == ("M", "202601", "202608")
    assert result["notice"]["requested_as_of_period"] == "202610"


def test_since_and_relative_periods_are_shown_to_the_model_stated_closed_ones_are_not():
    assert run([freq("M"), period("since", expr("2015"))])["show_period"] is True
    assert run([freq("Y"), period("recent", amount=3, unit="year", relative=True)])["show_period"]
    assert run([freq("Y"), period("range", expr("2020"), expr("2023"))])["show_period"] is False
    assert run([freq("Y")])["show_period"] is False  # the assumed window


def test_attrs_of_the_wrong_type_never_raise():
    # stage 3: an input contract error, found by the attrs check itself (valid id and kind)
    for bad in (
        [{"id": "P1", "kind": "period", "attrs": [1]}],
        [{"id": "F1", "kind": "frequency", "attrs": "M"}],
    ):
        result = run(bad)
        assert result["status"] == "invalid_input"
        assert result["issue"]["problems"] == ["conditions[0].attrs: not an object"]


def test_a_period_unit_frequency_is_recorded_as_an_assumption():
    result = run([period("recent", amount=3, unit="month", relative=True)])
    slot = result["slots"]["frequency"]
    assert (slot["source"], slot["rule"], slot["confirmed"]) == ("question", "period_unit", False)
    assert {"dimension": "frequency", "value": "M", "rule": "period_unit"} in result["assumptions"]


def test_inference_hints_replace_bs_own_reading_of_the_units():
    """With the interpretation's inference hints given, B uses them and does not re-read units."""
    conditions = [period("point", expr("2024", sub_unit="Q", sub_value="3"))]
    hint = [{"kind": "frequency", "value": "M", "from_condition_id": "P1", "rule": "period_unit"}]
    with_hint = resolve_period_for_table(conditions, TABLE, AS_OF, None, hint)
    assert (
        with_hint["frequency"] == "M" and with_hint["slots"]["frequency"]["rule"] == "period_unit"
    )
    no_hint_given = resolve_period_for_table(conditions, TABLE, AS_OF, None, [])
    # stage 3 (user item 3): [] and the absent field both re-read the units; hint_state differs
    assert no_hint_given["frequency"] == "Q"
    assert no_hint_given["slots"]["frequency"]["hint_state"] == "empty"
    assert no_hint_given["slots"]["frequency"]["confirmed"] is False
    hint_field_absent = resolve_period_for_table(conditions, TABLE, AS_OF)
    assert hint_field_absent["frequency"] == "Q"


def test_a_stated_frequency_wins_over_an_inference_hint():
    hint = [{"kind": "frequency", "value": "M", "from_condition_id": "P1", "rule": "period_unit"}]
    result = resolve_period_for_table([freq("Q")], TABLE, AS_OF, None, hint)
    assert result["slots"]["frequency"]["rule"] == "question_word"
