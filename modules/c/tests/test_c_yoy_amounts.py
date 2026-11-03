"""C-1: 금액·수량 단위 계열의 전년 동기 대비 증감률을 C가 직접 계산한다 (지수 계열 계산과 별개).

계산식은 (해당 시점 값 ÷ 1년 앞선 같은 시점 값 − 1) × 100. 값은 시험용 합성 값이다(실제 KOSIS 값이 아니다).
계산하지 못한 경우(기준값 없음·0 이하, 요청 기간 불명, 이미 %인 값, 단위 미제공)는 0%로 채우지 않고 원자료만 남긴다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from claview_c.c_analysis import YOY_NOT_CALCULATED, calculate_rates, decorate_cards, renderer, yoy_amounts

QUERY = "2024년 M2 전년 동월 대비 증감률"


def months(year):
    return [f"{year}{m:02d}" for m in range(1, 13)]


def series(frequency="M", unit="십억원", values=None, sid="S001", label="M2 기관별 구성내역 / M2(말잔)"):
    """values: {period: value or None}. None -> 값 없음(점 자체가 없다)."""
    points = [
        {"period": p, "raw_value": str(v), "value": str(v), "value_status": "observed"}
        for p, v in values.items()
        if v is not None
    ]
    return {
        "series_id": sid, "frequency": frequency, "unit": unit, "points": points,
        "selection": {"series_id": sid, "label": label, "org_id": "301", "table_id": "DT_161Y014", "item_id": "i",
                      "dimensions": {}, "unit": unit,
                      "period": {"frequency": frequency, "start": min(values), "end": max(values)}},
        "coverage": {"expected": len(points), "returned": len(points), "missing_periods": []},
        "provenance": {"params": {}, "fetched_at": "2026-11-03T00:00:00+00:00"},
    }


def monthly_values():
    """2023년 m월 = 1000 + m, 2024년 m월 = 2023년 값 × (1 + m/100) → 증감률이 정확히 m%."""
    base = {p: Decimal(1000 + n) for n, p in enumerate(months(2023), start=1)}
    now = {p: base[f"2023{n:02d}"] * (1 + Decimal(n) / 100) for n, p in enumerate(months(2024), start=1)}
    return {**base, **now}


def data(values=None, **kw):
    out = {"series": [series(values=values or monthly_values(), **kw)],
           "requested_period": {"frequency": "M", "start": "202401", "end": "202412"}}
    return out


def test_monthly_amount_series_gets_a_year_over_year_rate_with_exact_values():
    calc, note = yoy_amounts(QUERY, data())
    assert note is None and calc["kind"] == "yoy_amount" and calc["unit"] == "%"
    assert calc["periods"] == months(2024) and calc["frequency"] == "M"
    assert [Decimal(v).quantize(Decimal("0.0001")) for v in calc["series"][0]["values"]] == [
        Decimal(n).quantize(Decimal("0.0001")) for n in range(1, 13)
    ]
    first = calc["rows"][0]
    assert first["period"] == "202401" and first["baseline_period"] == "202301" and first["issue"] is None
    assert Decimal(first["current_value"]) == Decimal("1001") * Decimal("1.01")
    assert Decimal(first["baseline_value"]) == Decimal(1001)
    assert calc["complete"] is True and calc["source_unit"] == "십억원" and calc["source_name"] == "원자료"
    assert "전년 동월" in calc["title"] and "한국은행이 공표한 증감률이 아닙니다" in calc["policy"]
    assert "(해당 시점 값 ÷ 전년 동월 값 − 1) × 100" == calc["formula"]


def test_calculate_rates_routes_amount_series_here_and_index_series_stay_as_before():
    assert calculate_rates(QUERY, data())["kind"] == "yoy_amount"
    index = data(unit="2020=100")  # 지수(기준연도=100)는 기존 경로: 특정 표만 지원하므로 그대로 계산하지 않는다
    assert calculate_rates(QUERY, index) is None


@pytest.mark.parametrize("unit", ["%", "퍼센트", "% 포인트", "지수", "2020=100", "", None])
def test_published_rates_indexes_and_unknown_units_are_not_calculated(unit):
    calc, note = yoy_amounts(QUERY, data(unit=unit))
    assert calc is None and note is None


def test_a_question_without_year_over_year_wording_is_not_calculated():
    assert yoy_amounts("2024년 M2 월별 추이", data()) == (None, None)


def test_a_missing_base_value_is_left_empty_and_never_zero():
    values = monthly_values()
    del values["202303"]  # 2024년 3월의 기준값
    values["202405"] = None  # 2024년 5월의 현재값
    calc, _ = yoy_amounts(QUERY, data({k: v for k, v in values.items() if v is not None}))
    got = calc["series"][0]["values"]
    assert got[2] is None and got[4] is None and calc["complete"] is False
    assert sum(v is not None for v in got) == 10 and "0" not in {v for v in got if v is not None and Decimal(v) == 0}
    issues = {r["period"]: r["issue"] for r in calc["rows"] if r["issue"]}
    assert issues == {"202403": "현재값 또는 기준값 없음", "202405": "현재값 또는 기준값 없음"}


@pytest.mark.parametrize("base", [0, -5])
def test_a_base_value_of_zero_or_below_is_not_calculated(base):
    values = monthly_values()
    values["202302"] = Decimal(base)
    calc, _ = yoy_amounts(QUERY, data(values))
    assert calc["series"][0]["values"][1] is None
    assert [r["issue"] for r in calc["rows"] if r["period"] == "202402"] == ["기준값이 0 이하"]


def test_nothing_valid_means_no_calculation_and_a_reason():
    only_now = {p: v for p, v in monthly_values().items() if p.startswith("2024")}
    calc, note = yoy_amounts(QUERY, data(only_now))
    assert calc is None and "계산하지 않았습니다" in note


def test_the_target_periods_come_from_the_requested_range_then_the_users_range_then_the_one_stated_year():
    d = data()
    d["requested_period"] = {"frequency": "M", "start": "202406", "end": "202409"}
    assert yoy_amounts(QUERY, d)[0]["periods"] == ["202406", "202407", "202408", "202409"]
    d = data()
    del d["requested_period"]
    d["user_periods"] = [{"requested": {"start": "202410", "end": "202412"}}]
    assert yoy_amounts(QUERY, d)[0]["periods"] == ["202410", "202411", "202412"]
    d = data()
    del d["requested_period"]
    assert yoy_amounts(QUERY, d)[0]["periods"] == months(2024)


def test_without_any_basis_for_the_period_nothing_is_guessed():
    d = data()
    del d["requested_period"]
    calc, note = yoy_amounts("M2 전년 동월 대비 증감률", d)
    assert calc is None and "요청 기간을 확정하지 못해" in note
    calc, note = yoy_amounts("2023년과 2024년 M2 전년 동월 대비 증감률", d)  # 연도가 둘이면 고르지 않는다
    assert calc is None and note


@pytest.mark.parametrize(
    ("frequency", "query", "start", "end", "base_start", "name"),
    [
        ("Q", "2024년 분기별 전년 동분기 대비", "202401", "202404", "202301", "전년 동분기"),
        ("H", "2024년 반기별 전년 동기 대비", "202401", "202402", "202301", "전년 동반기"),
        ("Y", "2024년 연간 전년 대비", "2024", "2024", "2023", "전년"),
    ],
)
def test_other_frequencies_compare_with_the_same_period_a_year_earlier(frequency, query, start, end, base_start, name):
    from claview_c.c_renderer import periods_between

    values = {p: Decimal(100) for p in periods_between(base_start, end, frequency)}
    for p in periods_between(start, end, frequency):
        values[p] = Decimal(110)
    d = {"series": [series(frequency=frequency, values=values)],
         "requested_period": {"frequency": frequency, "start": start, "end": end}}
    calc, _ = yoy_amounts(query, d)
    assert all(Decimal(v).quantize(Decimal("0.01")) == Decimal("10.00") for v in calc["series"][0]["values"])
    assert calc["rows"][0]["baseline_period"] == base_start and name in calc["title"]


def test_several_series_are_calculated_one_by_one_in_their_own_units():
    two = {p: Decimal(200) if p < "2024" else Decimal(220) for p in monthly_values()}
    other = series(unit="백만달러", sid="S002", label="x / 수출", values=two)
    d = data()
    d["series"].append(other)
    calc, _ = yoy_amounts(QUERY, d)
    assert [s["id"] for s in calc["series"]] == ["S001", "S002"]
    assert all(Decimal(v).quantize(Decimal("0.01")) == Decimal("10.00") for v in calc["series"][1]["values"])
    assert calc["source_unit"] == "백만달러, 십억원"


def test_the_renderer_unit_recovered_from_the_dimension_label_counts_as_a_unit():
    d = data(unit=None)
    d["series"][0]["selection"]["dimensions"] = {"objL1": {"code": "c", "name": "금액(십억원)"}}
    chart = renderer(QUERY, d)["result"]
    assert chart["calculation"]["kind"] == "yoy_amount" and chart["calculation"]["source_unit"] == "십억원"


def test_the_renderer_reports_why_a_year_over_year_question_was_not_calculated():
    d = data()
    del d["requested_period"]
    out = renderer("M2 전년 동월 대비 증감률", d)["result"]
    assert out["calculation"] is None
    assert [w["code"] for w in out["warnings"] if w.get("code") == YOY_NOT_CALCULATED] == [YOY_NOT_CALCULATED]
    # an already published rate (unit %) is just shown: no calculation and no "not calculated" warning
    published = renderer(QUERY, data(unit="%"))["result"]
    codes = [w.get("code") for w in published["warnings"]]
    assert published["calculation"] is None and YOY_NOT_CALCULATED not in codes


def test_the_derived_card_is_separate_from_the_raw_card_and_carries_no_amount_baseline():
    chart = renderer(QUERY, data())["result"]
    raw = {
        "id": "run-g1", "tableName": "M2 기관별 구성내역(말잔)", "freq": "M", "notices": [], "warnings": [],
        "periods": months(2024), "labels": [f"2024년 {n}월" for n in range(1, 13)],
        "series": [{"id": "S001", "name": "M2", "values": ["1"] * 12}], "params": {}, "recommend": "",
        "baseline": {"periods": months(2023), "labels": [], "values": {"S001": ["1"] * 12}},
    }
    cards = decorate_cards(chart, [raw])
    derived, original = cards
    assert derived["derived"] is True and derived["unit"] == "%" and "전년 동월 대비 증감률" in derived["tableName"]
    assert derived["periods"] == months(2024) and len(derived["series"][0]["values"]) == 12
    assert "baseline" not in derived  # 원자료(십억원) 기준 구간이 %차트에 흐리게 붙으면 안 된다
    assert original["baseline"]["values"] == {"S001": ["1"] * 12} and "derived" not in original
    assert derived["params"]["원자료 단위"] == "십억원"
    assert "원자료" in original["recommend"] and any("원자료" in n for n in derived["notices"])
    assert "한국은행이 별도로 공표한 증감률이 아니며" in derived["notices"][1]
