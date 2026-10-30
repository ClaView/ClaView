# ruff: noqa: E501
"""C part: "그래프를 따로 배치"류 요청이 묶음 패널(card["panels"])이 되는 흐름.

C의 결과를 카드 모델로 옮기는 단계(`ui_cases`)까지 직접 부른다. A·B는 거치지 않는다: 시험용 A의
규칙 해석은 "~별로"·"따로 배치" 같은 말을 되묻기(`a_selection_unsettled`)로 보내므로 이 흐름의 시험에 맞지 않는다.
조회 결과는 만든 data_result이며 외부 호출은 없다.
"""

from __future__ import annotations

from claview_c_api import service as c_service
from claview_pipeline.staged import ui_cases
from test_c_alignment import make_series


def dim(axis, code, name):
    return {axis: {"code": code, "name": name}}


def with_dims(item, dims, item_id="I0"):
    item["selection"]["dimensions"] = dims
    item["selection"]["item_id"] = item_id
    return item


def cards(wording, *series, tables=(), axes=None, notices=()):
    data = {"series": list(series), "relations": [], "period_notices": list(notices)}
    if axes:
        data["alignment"] = {"status": "ok", "axes": axes}
    out = c_service.present(f"GDP 2023년 보여줘. {wording}", data)
    assert out["status"] in {"ok", "partial"}, out
    return ui_cases(
        out["result"], data, "run1", [{"table_id": t, "table_name": n} for t, n in tables]
    )


def panels(card):
    return [(p["title"], p["series"]) for p in card["panels"]]


def test_unit_wording_gives_one_card_with_a_panel_per_unit():
    out = cards(
        "지수와 %는 단위가 달라 그래프를 따로 배치해줘",
        make_series("S1", "지수", label="소비자심리지수"),
        make_series("S2", "%", label="기대인플레이션율", table="DT_RATE"),
        axes=[
            {"side": "left", "unit": "지수", "kind": "level", "draw": "line", "series_ids": ["S1"]},
            {"side": "right", "unit": "%", "kind": "rate", "draw": "line", "series_ids": ["S2"]},
        ],
    )
    assert len(out) == 1
    card = out[0]
    assert card["defaultView"] == "small_multiples"
    assert panels(card) == [("지수", ["S1"]), ("%", ["S2"])]


def test_indicator_wording_groups_series_by_the_measure_axis():
    def s(sid, entity, measure):
        return with_dims(
            make_series(sid, "%", label=f"{entity} / {measure}"),
            {**dim("objL1", entity, entity), **dim("objL2", measure, measure)},
        )

    out = cards(
        "지표별로 그래프를 나눠줘",
        s("S1", "대기업", "부채비율"),
        s("S2", "중소기업", "부채비율"),
        s("S3", "대기업", "차입금의존도"),
        s("S4", "중소기업", "차입금의존도"),
    )
    assert len(out) == 1 and out[0]["defaultView"] == "small_multiples"
    assert panels(out[0]) == [("부채비율", ["S1", "S2"]), ("차입금의존도", ["S3", "S4"])]


def test_table_wording_groups_by_table_and_shows_table_names():
    out = cards(
        "통계표별로 그래프를 나눠줘",
        make_series("S1", "%", label="가", table="DT_A"),
        make_series("S2", "%", label="나", table="DT_B"),
        make_series("S3", "%", label="다", table="DT_A"),
        tables=[("DT_A", "국내공급물가지수"), ("DT_B", "총산출물가지수")],
    )
    assert panels(out[0]) == [("국내공급물가지수", ["S1", "S3"]), ("총산출물가지수", ["S2"])]


def test_unresolvable_grouping_keeps_one_panel_per_series_and_says_so():
    def s(sid, entity):
        return with_dims(make_series(sid, "%", label=entity), dim("objL1", entity, entity))

    out = cards("지표별로 그래프를 나눠줘", s("S1", "대기업"), s("S2", "중소기업"))
    card = out[0]
    assert card["defaultView"] == "small_multiples" and "panels" not in card
    assert any("항목마다 패널" in n for n in card["notices"])


def test_panels_follow_the_cards_series_when_the_grouping_covers_several_cards():
    # 단위가 달라도 이중 축 정렬이 없으면 카드가 나뉜다: 카드마다 자기 계열만 가진 패널이 하나뿐이면 panels를 싣지 않는다
    out = cards(
        "단위별로 그래프를 나눠줘",
        make_series("S1", "지수", label="가"),
        make_series("S2", "%", label="나", table="DT_B"),
    )
    assert len(out) == 2
    assert all("panels" not in card for card in out)


def test_per_series_wording_and_plain_questions_have_no_panels():
    two = (make_series("S1", "%", label="가"), make_series("S2", "%", label="나", table="DT_B"))
    each = cards("각각 나눠 보여줘", *two)[0]
    assert each["defaultView"] == "small_multiples" and "panels" not in each
    assert "panels" not in cards("선그래프로 보여줘", *two)[0]


DUAL_NOTICE = "단위가 서로 다른 지표(지수 · %)를 왼쪽·오른쪽 축에 나눠 그렸습니다. 축 눈금이 달라 선·막대의 높이를 직접 비교할 수 없습니다."
AXES = [
    {"side": "left", "unit": "지수", "kind": "level", "draw": "line", "series_ids": ["S1"]},
    {"side": "right", "unit": "%", "kind": "rate", "draw": "line", "series_ids": ["S2"]},
]


def test_dual_axis_notice_is_reworded_when_panels_are_the_first_view():
    two = (make_series("S1", "지수", label="가"), make_series("S2", "%", label="나", table="DT_B"))
    panel = cards("단위가 달라 그래프를 나눠줘", *two, axes=AXES, notices=[DUAL_NOTICE])[0]
    assert panel["notices"] == [
        "단위가 서로 다른 지표(지수 · %)를 패널로 나눠 그렸습니다. 패널마다 축 눈금이 달라 높이를 직접 비교할 수 없습니다."
    ]
    # 패널을 요청하지 않으면 이중 축 안내가 그대로다
    dual = cards("보여줘", *two, axes=AXES, notices=[DUAL_NOTICE])[0]
    assert dual["defaultView"] == "dual_axis" and dual["notices"] == [DUAL_NOTICE]
