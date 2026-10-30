"""패널 나누기 요청("그래프를 따로 배치", "지표별로 그래프를 나눠")의 인식과 묶음 기준(panel_groups)."""

import pytest

from claview_c.chart_policy import panel_groups, requests


def one(query):
    found = requests(query)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize(
    ("query", "by"),
    [
        ("지수와 %를 구분해 그래프를 따로 배치해줘", "auto"),
        ("지표별로 그래프를 나눠줘", "indicator"),
        ("항목별로 차트를 분리해줘", "indicator"),
        ("단위별로 그래프를 분리해줘", "unit"),
        ("통계표별로 그래프를 나눠줘", "table"),
        ("표별 그래프를 나눠줘", "table"),
        ("잔액과 금리는 범위와 단위가 다르므로 차트 영역을 나눠줘.", "unit"),
        ("원재료 공급과 공산품 출하의 차트 영역을 나눠 보여줘", "auto"),
    ],
)
def test_panel_wording_is_a_small_multiples_request_with_its_grouping_hint(query, by):
    item = one(query)
    assert item["kind"] == "small_multiples" and item["panel_by"] == by


def test_jipyo_is_not_read_as_pyo():
    # "지표별"의 끝이 "표별"이어도 통계표별이 아니다
    assert one("지표별로 그래프를 나눠줘")["panel_by"] == "indicator"


@pytest.mark.parametrize(
    "query",
    ["항목별 나눠 보기로 보여줘", "각각 나눠 선그래프로 보여줘", "분할 그래프로 보여줘"],
)
def test_existing_per_series_wording_has_no_grouping_hint(query):
    assert "panel_by" not in one(query)


@pytest.mark.parametrize(
    "query",
    ["2024년 대기업과 중소기업을 구분해서 보여줘", "선그래프로 보여줘", "월별로 비교해줘"],
)
def test_wording_without_a_layout_request_is_not_a_panel_request(query):
    assert all(item["kind"] != "small_multiples" for item in requests(query))


def series(sid, unit, table="T1", item="I1", dims=None, label=None):
    return {
        "series_id": sid,
        "unit": unit,
        "label": label or f"지표{item} / {sid}",
        "selection": {"table_id": table, "item_id": item, "dimensions": dims or {}},
    }


def titles(groups):
    return [(g["title"], g["series_ids"]) for g in groups]


def test_groups_by_unit_keep_series_order_and_name_missing_units():
    items = [series("S1", "지수"), series("S2", "%"), series("S3", "지수"), series("S4", None)]
    assert titles(panel_groups(items, "unit")) == [
        ("지수", ["S1", "S3"]),
        ("%", ["S2"]),
        ("단위 미제공", ["S4"]),
    ]


def test_groups_by_table():
    items = [series("S1", "%", "TA"), series("S2", "%", "TB"), series("S3", "%", "TA")]
    assert titles(panel_groups(items, "table")) == [("TA", ["S1", "S3"]), ("TB", ["S2"])]


def dim(axis, code, name):
    return {axis: {"code": code, "name": name}}


def test_indicator_grouping_uses_the_axis_whose_values_are_measures_not_the_entity_axis():
    items = [
        series("S1", "%", dims={**dim("objL1", "L", "대기업"), **dim("objL2", "D", "부채비율")}),
        series("S2", "%", dims={**dim("objL1", "S", "중소기업"), **dim("objL2", "D", "부채비율")}),
        series("S3", "%", dims={**dim("objL1", "L", "대기업"), **dim("objL2", "B", "차입금의존도")}),
        series("S4", "%", dims={**dim("objL1", "S", "중소기업"), **dim("objL2", "B", "차입금의존도")}),
    ]
    assert titles(panel_groups(items, "indicator")) == [
        ("부채비율", ["S1", "S2"]),
        ("차입금의존도", ["S3", "S4"]),
    ]


def test_indicator_grouping_falls_back_to_the_item_when_dimensions_are_all_entities():
    items = [
        series("S1", "%", item="A", dims=dim("objL1", "L", "대기업"), label="매출액 / 대기업"),
        series("S2", "%", item="B", dims=dim("objL1", "L", "대기업"), label="영업이익 / 대기업"),
    ]
    assert titles(panel_groups(items, "indicator")) == [("매출액", ["S1"]), ("영업이익", ["S2"])]


def test_unresolvable_grouping_returns_none_instead_of_guessing():
    same_item = [
        series("S1", "%", dims=dim("objL1", "L", "대기업")),
        series("S2", "%", dims=dim("objL1", "S", "중소기업")),
    ]
    assert panel_groups(same_item, "indicator") is None  # 분류가 모두 구성원 이름이고 항목도 같다
    assert panel_groups(same_item, "unit") is None  # 단위가 하나뿐이다
    assert panel_groups(same_item, "table") is None  # 표가 하나뿐이다
    assert panel_groups(same_item, "auto") is None


def test_auto_prefers_unit_then_table_then_indicator():
    mixed = [series("S1", "지수", "TA"), series("S2", "%", "TB")]
    assert [g["title"] for g in panel_groups(mixed, "auto")] == ["지수", "%"]
    same_unit = [series("S1", "%", "TA"), series("S2", "%", "TB")]
    assert [g["title"] for g in panel_groups(same_unit, "auto")] == ["TA", "TB"]
