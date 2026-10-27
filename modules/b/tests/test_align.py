# ruff: noqa: E501
"""claview_b.align: units, frequency, common period, axes, combining. Offline, no model."""

import copy

import pytest

from claview_b.align import (
    AlignmentError,
    align,
    combine_results,
    frequency_choice,
    parse_unit,
)
from claview_b.metadata import periods_between


def make_series(
    sid, unit, freq="M", start="202401", end="202406", values=None, label=None, table="T1"
):
    periods = periods_between(start, end, freq)
    values = values if values is not None else [str(i + 1) for i in range(len(periods))]
    points = []
    for period, value in zip(periods, values, strict=True):
        status = "observed" if value is not None else "missing"
        points.append(
            {"period": period, "raw_value": value, "value": value, "value_status": status}
        )
    returned = len(points)
    return {
        "series_id": sid,
        "selection": {
            "series_id": sid,
            "org_id": "301",
            "table_id": table,
            "item_id": f"I{sid}",
            "dimensions": {},
            "period": {"frequency": freq, "start": start, "end": end},
            "unit": unit,
            "label": label or sid,
        },
        "frequency": freq,
        "unit": unit,
        "points": points,
        "coverage": {"expected": len(periods), "returned": returned, "missing_periods": []},
        "provenance": {},
    }


def data(*series, **extra):
    return {"series": list(series), "relations": [], **extra}


def values(series):
    return [p["value"] for p in series["points"]]


# ---- units -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "kind", "currency", "scale"),
    [
        ("십억원", "money", "KRW", 10**9),
        ("백만원", "money", "KRW", 10**6),
        ("조원", "money", "KRW", 10**12),
        ("백만달러", "money", "USD", 10**6),
        ("%", "rate", None, None),
        ("연리%", "rate", None, None),
        ("연%", "rate", None, None),
        ("2020＝100", "index", None, None),
        ("건", "other", None, None),
        (None, "unknown", None, None),
    ],
)
def test_parse_unit(text, kind, currency, scale):
    info = parse_unit(text)
    assert (info["kind"], info["currency"], info["scale"]) == (kind, currency, scale)


def test_same_currency_scale_is_converted_exactly_and_raw_value_kept():
    big = make_series("S1", "십억원", values=["1", "2", "3", "4", "5", "6"])
    small = make_series("S2", "백만원", values=["1500", "2500", None, "100", "0", "7"])
    original = data(big, small)
    snapshot = copy.deepcopy(original)
    out = align(original)
    assert original == snapshot  # input untouched
    s1, s2 = out["series"]
    assert s1["unit"] == s2["unit"] == "십억원" or s1["unit"] == s2["unit"]
    # the unit most series use wins; a tie goes to the first series (십억원)
    assert s2["unit"] == "십억원"
    assert values(s2) == ["1.5", "2.5", None, "0.1", "0", "0.007"]
    assert [p["raw_value"] for p in s2["points"]] == ["1500", "2500", None, "100", "0", "7"]
    assert s2["source_unit"] == "백만원"
    assert s2["selection"]["unit"] == "백만원"  # B's record is untouched
    assert out["alignment"]["unit_conversions"] == [
        {"series_id": "S2", "from_unit": "백만원", "to_unit": "십억원", "factor": "0.001"}
    ]
    assert [a["unit"] for a in out["alignment"]["axes"]] == ["십억원"]
    assert out["alignment"]["status"] == "aligned"


def test_majority_scale_is_the_target():
    out = align(
        data(
            make_series("S1", "백만원", values=["1"] * 6),
            make_series("S2", "십억원", values=["1"] * 6),
            make_series("S3", "십억원", values=["2"] * 6),
        )
    )
    assert {s["unit"] for s in out["series"]} == {"십억원"}
    assert out["series"][0]["unit"] == "십억원"
    assert values(out["series"][0]) == ["0.001"] * 6


def test_currency_is_never_converted_and_goes_to_two_axes():
    out = align(
        data(
            make_series("S1", "십억원", label="원화 지표"),
            make_series("S2", "백만달러", label="달러 지표"),
        )
    )
    s1, s2 = out["series"]
    assert (s1["unit"], s2["unit"]) == ("십억원", "백만달러")
    assert values(s2) == ["1", "2", "3", "4", "5", "6"]
    assert out["alignment"]["unit_conversions"] == []
    assert len(out["alignment"]["axes"]) == 2
    codes = [n["code"] for n in out["alignment"]["notices"]]
    assert "currency_not_converted" in codes
    assert "dual_axis" in codes


def test_amount_and_percent_use_two_axes_amount_left_as_columns():
    out = align(data(make_series("S1", "%"), make_series("S2", "십억원")))
    left, right = out["alignment"]["axes"]
    assert (left["unit"], left["side"], left["draw"]) == ("십억원", "left", "column")
    assert (right["unit"], right["side"], right["draw"]) == ("%", "right", "line")
    assert left["series_ids"] == ["S2"] and right["series_ids"] == ["S1"]


def test_three_units_offer_no_axes():
    out = align(data(make_series("S1", "십억원"), make_series("S2", "%"), make_series("S3", "건")))
    assert out["alignment"]["axes"] is None
    assert out["alignment"]["axes_reason"] == "too_many_units"


def test_single_series_is_unchanged():
    out = align(data(make_series("S1", "%")))
    assert out["alignment"]["status"] == "unchanged"
    assert out["alignment"]["axes"] is None


# ---- frequency ----------------------------------------------------------------------
def test_different_frequencies_need_a_choice_and_nothing_changes():
    monthly = make_series("S1", "십억원", freq="M", start="202401", end="202412", label="월 지표")
    quarterly = make_series(
        "S2", "십억원", freq="Q", start="202401", end="202404", label="분기 지표"
    )
    out = align(data(monthly, quarterly))
    alignment = out["alignment"]
    assert alignment["status"] == "needs_choice"
    choice = alignment["choice"]
    assert choice["question_id"] == "frequency_alignment"
    assert choice["target_frequency"] == "Q"
    assert [o["option_id"] for o in choice["options"]] == [
        "freq_mean",
        "freq_last",
        "freq_sum",
        "freq_none",
    ]
    assert out["series"] == data(monthly, quarterly)["series"]


def test_sum_is_not_offered_for_rates():
    choice = frequency_choice(
        [
            make_series("S1", "%", freq="M"),
            make_series("S2", "십억원", freq="Q", start="202401", end="202404"),
        ]
    )
    assert "freq_sum" not in [o["option_id"] for o in choice["options"]]
    with pytest.raises(AlignmentError) as caught:
        align(
            data(
                make_series("S1", "%", freq="M", start="202401", end="202412"),
                make_series("S2", "십억원", freq="Q", start="202401", end="202404"),
            ),
            frequency_policy="freq_sum",
        )
    assert caught.value.code == "alignment_policy_not_allowed"


def monthly_and_quarterly(**monthly_extra):
    monthly = make_series(
        "S1",
        "십억원",
        freq="M",
        start="202401",
        end="202406",
        values=["1", "2", "3", "4", "5", "6"],
        label="월 지표",
    )
    quarterly = make_series(
        "S2",
        "십억원",
        freq="Q",
        start="202401",
        end="202402",
        values=["10", "20"],
        label="분기 지표",
    )
    return data(monthly, quarterly, **monthly_extra)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("freq_mean", ["2", "5"]), ("freq_last", ["3", "6"]), ("freq_sum", ["6", "15"])],
)
def test_month_to_quarter_policies(policy, expected):
    out = align(monthly_and_quarterly(), frequency_policy=policy)
    monthly, quarterly = out["series"]
    assert monthly["frequency"] == quarterly["frequency"] == "Q"
    assert [p["period"] for p in monthly["points"]] == ["202401", "202402"]
    assert values(monthly) == expected
    assert monthly["aligned_period"] == {"frequency": "Q", "start": "202401", "end": "202402"}
    assert monthly["source_frequency"] == "M"
    assert [p["value"] for p in monthly["source_points"]] == ["1", "2", "3", "4", "5", "6"]
    assert monthly["selection"]["period"]["frequency"] == "M"  # B's record untouched
    assert monthly["coverage"] == {"expected": 2, "returned": 2, "missing_periods": []}
    assert out["alignment"]["status"] == "aligned"
    assert out["alignment"]["frequency"] == "Q"
    assert len(out["alignment"]["axes"]) == 1
    assert out["alignment"]["frequency_conversions"][0]["method"] == policy.removeprefix("freq_")


def test_mean_is_rounded_to_six_decimals():
    monthly = make_series(
        "S1", "십억원", freq="M", start="202401", end="202403", values=["1", "1", "2"]
    )
    quarterly = make_series("S2", "십억원", freq="Q", start="202401", end="202401", values=["1"])
    out = align(data(monthly, quarterly), frequency_policy="freq_mean")
    assert values(out["series"][0]) == ["1.333333"]


def test_incomplete_and_partial_buckets():
    monthly = make_series(
        "S1",
        "십억원",
        freq="M",
        start="202402",
        end="202409",
        values=["2", "3", "4", None, "6", "7", "8", "9"],
    )
    quarterly = make_series(
        "S2", "십억원", freq="Q", start="202401", end="202403", values=["1", "2", "3"]
    )
    out = align(data(monthly, quarterly), frequency_policy="freq_mean")
    converted = out["series"][0]
    # 2024-02 starts mid-quarter: Q1 (and nothing after Q3) is not fully covered
    assert [p["period"] for p in converted["points"]] == ["202402", "202403"]
    assert values(converted) == [None, "8"]  # Q2 has a gap (May); Q3 = mean(7, 8, 9)
    assert converted["points"][0]["value_status"] == "missing"
    report = out["alignment"]["frequency_conversions"][0]
    assert report["dropped_buckets"] == ["202401"]
    assert report["incomplete_buckets"] == ["202402"]
    codes = [n["code"] for n in out["alignment"]["notices"]]
    assert "frequency_converted" in codes and "frequency_bucket_incomplete" in codes
    # the quarterly series is cut to the common span (Q2..Q3)
    assert out["alignment"]["common_period"] == {
        "frequency": "Q",
        "start": "202402",
        "end": "202403",
    }


def test_requested_period_follows_the_chosen_frequency():
    monthly = make_series(
        "S1", "십억원", freq="M", start="202401", end="202412", values=[str(i) for i in range(12)]
    )
    quarterly = make_series(
        "S2", "십억원", freq="Q", start="202401", end="202404", values=["1", "2", "3", "4"]
    )
    out = align(
        data(
            monthly,
            quarterly,
            requested_period={"frequency": "M", "start": "202404", "end": "202409"},
        ),
        frequency_policy="freq_last",
    )
    assert out["requested_period"] == {"frequency": "Q", "start": "202402", "end": "202403"}


def test_keep_frequencies_separate():
    out = align(
        monthly_and_quarterly(
            requested_period={"frequency": "M", "start": "202401", "end": "202406"}
        ),
        frequency_policy="freq_none",
    )
    assert [s["frequency"] for s in out["series"]] == ["M", "Q"]
    assert out["requested_period"] is None
    assert out["alignment"]["axes"] is None
    assert out["alignment"]["axes_reason"] == "mixed_frequency"
    assert out["alignment"]["frequency"] is None
    assert [n["code"] for n in out["alignment"]["notices"]] == ["frequency_not_aligned"]


def test_invalid_policy_is_refused():
    with pytest.raises(AlignmentError) as caught:
        align(monthly_and_quarterly(), frequency_policy="freq_magic")
    assert caught.value.code == "alignment_policy_invalid"


def test_policy_is_ignored_when_frequencies_already_agree():
    out = align(
        data(make_series("S1", "십억원"), make_series("S2", "%")), frequency_policy="freq_mean"
    )
    assert out["alignment"]["frequency_policy"] is None
    assert out["series"][0]["frequency"] == "M"


# ---- common period -------------------------------------------------------------------
def test_common_period_cuts_each_series_and_clamps_requested_period():
    first = make_series(
        "S1", "십억원", start="202401", end="202412", values=[str(i) for i in range(12)]
    )
    second = make_series("S2", "%", start="202403", end="202409", values=[str(i) for i in range(7)])
    out = align(
        data(first, second, requested_period={"frequency": "M", "start": "202401", "end": "202412"})
    )
    cut, kept = out["series"]
    assert [p["period"] for p in cut["points"]] == periods_between("202403", "202409", "M")
    assert cut["coverage"] == {"expected": 7, "returned": 7, "missing_periods": []}
    assert cut["aligned_period"] == {"frequency": "M", "start": "202403", "end": "202409"}
    assert "aligned_period" not in kept
    assert out["requested_period"] == {"frequency": "M", "start": "202403", "end": "202409"}
    assert out["alignment"]["common_period"] == {
        "frequency": "M",
        "start": "202403",
        "end": "202409",
    }
    assert "common_period_applied" in [n["code"] for n in out["alignment"]["notices"]]


def test_disjoint_periods_are_left_alone():
    out = align(
        data(
            make_series("S1", "십억원", start="202401", end="202402"),
            make_series("S2", "%", start="202406", end="202407"),
        )
    )
    assert [len(s["points"]) for s in out["series"]] == [2, 2]
    assert out["alignment"]["common_period"] is None
    assert "no_common_period" in [n["code"] for n in out["alignment"]["notices"]]


def test_notices_reach_period_notices_without_dropping_b_notices():
    out = align(
        data(
            make_series("S1", "십억원"),
            make_series("S2", "%"),
            period_notices=[{"code": "x", "message": "B 안내"}],
        )
    )
    messages = [n["message"] for n in out["period_notices"]]
    assert messages[0] == "B 안내"
    assert any("왼쪽·오른쪽 축" in m for m in messages)


# ---- combining requirements ----------------------------------------------------------
def test_combine_prefixes_ids_dedups_and_renames_relations():
    one = data(
        make_series("S001", "십억원", label="전체"),
        make_series("S002", "십억원", label="하위"),
        requested_period={"frequency": "M", "start": "202401", "end": "202406"},
    )
    one["relations"] = [{"ancestor_series_id": "S001", "descendant_series_id": "S002"}]
    two = data(
        make_series("S001", "십억원", label="전체", table="T1"),  # same selection as one's S001
        make_series("S003", "%", label="증감률", table="T2"),
        requested_period={"frequency": "M", "start": "202401", "end": "202406"},
    )
    two["series"][0]["selection"]["item_id"] = "IS001"
    merged = combine_results([one, two])
    ids = [s["series_id"] for s in merged["series"]]
    assert ids == ["R1-S001", "R1-S002", "R2-S003"]
    assert all(s["selection"]["series_id"] == s["series_id"] for s in merged["series"])
    assert merged["relations"] == [
        {"ancestor_series_id": "R1-S001", "descendant_series_id": "R1-S002"}
    ]
    assert merged["requested_period"] == {"frequency": "M", "start": "202401", "end": "202406"}


def test_combine_drops_requested_period_when_parts_disagree():
    one = data(
        make_series("S1", "십억원"),
        requested_period={"frequency": "M", "start": "202401", "end": "202406"},
    )
    two = data(
        make_series("S1", "%", table="T2"),
        requested_period={"frequency": "M", "start": "202402", "end": "202406"},
    )
    merged = combine_results([one, two])
    assert "requested_period" not in merged
    assert merged["period_notices"][-1]["code"] == "requested_period_not_shared"


def test_combine_single_part_is_a_copy_and_empty_is_refused():
    one = data(make_series("S1", "십억원"))
    assert combine_results([one]) == one
    with pytest.raises(AlignmentError):
        combine_results([])
