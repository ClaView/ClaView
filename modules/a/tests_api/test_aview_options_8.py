"""Plan 56 order 8 (bundle 7, ⑥): AView carries an ask option's status and whether it may be chosen, and the basis A
chose by ⑥-나 in its own words (never as the user's choice)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from claview_common.contracts.a_view import A_VIEW_VERSION, QuestionOption, StatisticView


def test_an_option_carries_its_status_and_an_unconfirmed_one_is_not_selectable():
    o = QuestionOption(option_id="q:s1:seasonal:o2", label="계절조정", value="계절조정", status="unconfirmed",
                       selectable=False)
    assert o.model_dump()["status"] == "unconfirmed" and o.selectable is False
    assert QuestionOption(option_id="x", label="원계열", value="원계열").selectable is True
    with pytest.raises(ValidationError):  # an unsupported option is never offered
        QuestionOption(option_id="x", label="v", value="v", status="unsupported")


def test_a_basis_chosen_by_the_five_conditions_is_shown_in_its_own_words():
    msg = "제시된 선택지 중 요청 기간·주기를 지원하는 것으로 확인된 기준이 원계열 하나여서 선택했습니다"
    row = StatisticView(request_id="x:s1", statistic="s1", quote="GDP", name="GDP", outcome="selected",
                        state="selected", verified=True,
                        auto_decided=[{"axis": "seasonal", "value": "원계열", "message": msg}])
    assert row.model_dump()["auto_decided"][0]["message"] == msg
    assert A_VIEW_VERSION == "a-view/2026-11-02.11"  # the current version (a wrong or rolled-back one fails)
