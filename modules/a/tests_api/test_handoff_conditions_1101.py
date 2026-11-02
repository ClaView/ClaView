"""Plan draft 57 step 1 (owner 11-01 13:39 / 14:08): the C2 conditions of a ready request are assembled in view.py from
the same AView statistics the screen shows. A table whose period source the C2 delivery does not allow, or a
statistic not selected, gets no condition (the request keeps today's path). Scripted judges only."""

from __future__ import annotations

import copy

from claview_a_api import screen as S
from claview_a_api import view as V
from claview_a_api.synthetic_judges import ScriptJudge, by_question

AS_OF = "2026-10-29"
QUOTED = "M2 통화량을 2023년 연간으로 보여줘"
ASKED = "M2 통화량을 연간으로 보여줘"
SCRIPT = {
    QUOTED: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
             "periods": [{"quote": "2023년", "for": ["s1"]}], "frequencies": [{"quote": "연간", "for": ["s1"]}],
             "choices": [{"for": ["s1"], "table": "DT_161Y014"}]},
    ASKED: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
            "frequencies": [{"quote": "연간", "for": ["s1"]}], "choices": [{"for": ["s1"], "table": "DT_161Y014"}]},
}


def select(question):
    j = ScriptJudge(by_question(SCRIPT))
    return S.select(question, AS_OF, S.Judges(j, j, "script"))


def test_a_quoted_request_gets_one_condition_with_the_screens_values():
    view = select(QUOTED)["view"]
    (request,) = view["handoff"]["ready"]
    got = V.handoff_conditions(view, request)
    (table,) = view["statistics"][0]["tables"]  # what the screen shows
    assert got["withheld"] is None and got["conditions"] == [{
        "table_id": "DT_161Y014", "statistics": ["s1"], "frequency": "Y",
        "period": [{"start": "2023-01", "end": "2023-12"}], "period_source": "quoted", "frequency_source": "question",
        "period_phrase": "2023년"}]
    cond = got["conditions"][0]
    assert (cond["frequency"], cond["period"], cond["period_source"], cond["frequency_source"]) == (
        table["frequency"], table["period"], table["period_source"], table["frequency_source"])
    assert got["period_hints"] == [{"table_id": "DT_161Y014", **table["period_hint"]}]  # the hint apart


def test_an_answered_period_is_user_answer_with_the_answer_as_its_phrase():
    out = select(ASKED)
    assert not out["view"]["handoff"]["ready"]  # waiting for the period: nothing to send
    (q,) = [x for x in out["view"]["questions"] if x["kind"] == "period"]
    option = next(o for o in q["options"] if o["value"] == "최근 5년")
    after = S.answer(out["checkpoint"], out["checkpoint"]["session"]["session_id"],
                     [{"question_id": q["question_id"], "question_version": q["question_version"],
                       "option_id": option["option_id"]}])
    view = after["view"]
    (request,) = view["handoff"]["ready"]
    (cond,) = V.handoff_conditions(view, request)["conditions"]
    assert (cond["period_source"], cond["period_phrase"], cond["frequency"]) == ("user_answer", "최근 5년", "Y")
    # The answer stays user-authored; its recent range now ends at the table's latest yearly data.
    assert cond["period"] == [{"start": "2021-01", "end": "2025-12"}]


def test_a_default_source_or_an_unselected_statistic_gets_no_condition():
    view = select(QUOTED)["view"]
    kept = copy.deepcopy(view)
    (request,) = view["handoff"]["ready"]
    V.handoff_conditions(view, request)
    assert view == kept  # the assembly changes nothing in the view
    bad = copy.deepcopy(view)
    bad["statistics"][0]["tables"][0]["period_source"] = "default"
    assert V.handoff_conditions(bad, request) == {"conditions": [], "period_hints": [],
                                                  "withheld": "period_source_not_allowed"}
    asking = copy.deepcopy(view)
    asking["statistics"][0]["outcome"] = "ask_condition"
    assert V.handoff_conditions(asking, request)["withheld"] == "statistic_not_selected"
