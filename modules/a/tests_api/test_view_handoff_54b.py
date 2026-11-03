"""Plan 54 review fixes: what the AView hands to B is decided on A's structured decisions (tables, requested months,
frequency), never on quoted words, and A's warnings keep their fields. Pure adapter cases plus scripted runs."""

from __future__ import annotations

import copy

import pytest

from claview_a_api import screen as S
from claview_a_api import view as V
from claview_a_api.synthetic_judges import ScriptJudge, by_question

SESSION = {"session_id": "sess"}


def span(start, end):
    return {"start": start, "end": end}


def stat(sid, tables, calcs=(), quote=None, periods=("q",)):
    return {"statistic": sid, "outcome": "selected", "focus": quote or f"지표{sid}", "reasons": [],
            "conditions": {"periods": list(periods), "frequencies": [], "calculations": list(calcs), "splitters": []},
            "tables": [{"org_id": "301", "table_id": t, "table_name": t, "frequency": f, "period": p}
                       for t, f, p in tables]}


def handoff(*stats, answers=None):
    return V._handoff(SESSION, list(stats), set(), answers or {})


Y2023 = [span("2023-01", "2023-12")]
Y2024 = [span("2024-01", "2024-12")]


def test_same_range_in_other_words_is_one_request():
    # "2023년" and "2023년도": the quotes differ, A's months and frequency are the same
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)], periods=["2023년"]),
                             stat("s2", [("T1", "Y", Y2023)], periods=["2023년도"]))
    assert blocked == [] and [r["statistics"] for r in ready] == [["s1", "s2"]]
    assert ready[0]["tables"][0]["focus"] == "지표s1 / 지표s2"
    assert ready[0]["assignments"] == [{"table_id": "T1", "frequency": "Y", "period": Y2023,
                                        "statistics": ["s1", "s2"]}]


def test_really_different_ranges_on_one_table_are_not_merged():
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)]), stat("s2", [("T1", "Y", Y2024)]))
    assert ready == [] and [b["code"] for b in blocked] == ["same_table_conditions_differ"]
    detail = blocked[0]["detail"]
    assert [d["tables"][0]["period"] for d in detail] == [Y2023, Y2024]


def test_same_months_other_frequency_on_one_table_are_not_merged():
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)]), stat("s2", [("T1", "M", Y2023)]))
    assert ready == [] and blocked[0]["code"] == "same_table_conditions_differ"


def test_different_ranges_on_different_tables_are_preserved_for_supported_b():
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)]), stat("s2", [("T2", "Y", Y2024)]))
    # A describes each validated result; the pipeline checks the actual receiver's capabilities.
    assert blocked == [] and [r["statistics"] for r in ready] == [["s1"], ["s2"]]
    assert [r["assignments"][0]["period"] for r in ready] == [Y2023, Y2024]
    assert [r["tables"][0]["focus"] for r in ready] == ["지표s1", "지표s2"]


def test_same_range_on_different_tables_is_sent_as_two_requests():
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)]), stat("s2", [("T2", "M", Y2023)]))
    assert blocked == [] and [r["statistics"] for r in ready] == [["s1"], ["s2"]]


def test_a_gap_is_never_one_range():
    gap = [span("2010-01", "2010-12"), span("2020-01", "2020-12")]
    ready, blocked = handoff(stat("s1", [("T1", "Y", gap)]))
    assert ready == [] and blocked[0]["code"] == "disjoint_period_not_in_b_contract"
    # and two statistics with the same outer range but one with a gap are not the same months
    whole = [span("2010-01", "2020-12")]
    assert V._months(stat("a", [("T1", "Y", gap)])) != V._months(stat("b", [("T1", "Y", whole)]))


def test_editions_sharing_one_statistic_are_not_sent():
    ready, blocked = handoff(stat("s1", [("T_NEW", "Y", [span("2003-01", "2010-12")]),
                                         ("T_OLD", "Y", [span("2001-01", "2002-12")])]))
    assert ready == [] and blocked[0]["code"] == "edition_period_split"
    assert [t["table_id"] for t in blocked[0]["detail"][0]["tables"]] == ["T_NEW", "T_OLD"]


def test_different_calculations_on_one_table_are_sent_with_a_note():
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)], calcs=["비중"]),
                             stat("s2", [("T1", "Y", Y2023)], calcs=["증감률"]))
    assert blocked == [] and ready[0]["statistics"] == ["s1", "s2"]
    assert ready[0]["calculations"] == {"s1": ["비중"], "s2": ["증감률"]}
    assert [n["code"] for n in ready[0]["notes"]] == ["calculation_not_in_b_contract"]


def test_different_period_answers_keep_requests_apart():
    a1 = [{"option_id": None, "value": "작년", "text": "작년"}]
    a2 = [{"option_id": None, "value": "올해", "text": "올해"}]
    ready, blocked = handoff(stat("s1", [("T1", "Y", Y2023)]), stat("s2", [("T1", "Y", Y2023)]),
                             answers={"s1": a1, "s2": a2})
    assert ready == [] and blocked[0]["code"] == "same_table_conditions_differ"


def test_warnings_keep_every_field_and_the_axis_label():
    rows = [{"code": "stated_basis_unverified", "axis": "seasonal", "stated": ["원계열"], "table": "T1",
             "stage": "ask_option"}, {"code": "calculation_quote_not_in_question", "detail": "증감"}]
    before = copy.deepcopy(rows)
    got = V._warnings(rows)
    assert rows == before
    assert got[0] == {"code": "stated_basis_unverified",
                      "detail": {"axis": "seasonal", "stated": ["원계열"], "table": "T1", "stage": "ask_option",
                                 "axis_label": "원계열/계절조정"}}
    assert got[1] == {"code": "calculation_quote_not_in_question", "detail": "증감"}


# plan 56 order 5 (policy ④, user 2026-10-31 17:53 item 1): the warning is checked here, not the frequency: the
# question and the made-up answer quote 연간 with the period (before, the longest frequency was assumed)
Q = "2024년 연간 수출 공산품 가격을 원화 기준으로 보여줘"
# an ask on the classification axis; the model's own splitter states a currency the option tables' titles do not show
# (check step 3a: stated_basis_unverified, a warning)
SCRIPT = {Q: {"statistics": [{"id": "s1", "quote": "수출 공산품 가격", "name": "수출물가"}],
              "splitters": [{"quote": "원화 기준", "axis": "currency", "for": ["s1"]}],
              "periods": [{"quote": "2024년", "for": ["s1"]}], "frequencies": [{"quote": "연간", "for": ["s1"]}],
              "asks": [{"for": "s1", "axis": "classification",
                        "options": [{"value": "기본분류", "table": "DT_402Y014"},
                                    {"value": "품목별", "table": "DT_402Y016"}]}]}}


def test_a_warning_survives_the_answer_and_reaches_the_view():
    j = ScriptJudge(by_question(SCRIPT))
    out = S.select(Q, "2026-10-22", S.Judges(j, j, "script"))
    warnings = out["view"]["statistics"][0]["warnings"]
    assert {w["code"] for w in warnings} == {"stated_basis_unverified"}
    assert {w["detail"]["table"] for w in warnings} == {"DT_402Y014", "DT_402Y016"}
    assert all(w["detail"]["axis"] == "currency" and w["detail"]["stated"] for w in warnings)
    cp, q = out["checkpoint"], out["view"]["questions"][0]
    done = S.answer(cp, cp["session"]["session_id"], [{"question_id": q["question_id"],
                                                       "question_version": q["question_version"],
                                                       "option_id": q["options"][1]["option_id"]}])
    s = done["view"]["statistics"][0]
    assert s["outcome"] == "selected" and done["view"]["status"] == "ready"  # a warning alone changes no status
    assert s["warnings"] == warnings  # kept as A wrote them before the answer
    assert all(w["detail"]["axis_label"] == "통화" for w in s["warnings"])


@pytest.mark.parametrize("frequency", ["Y", "H", "Q", "M"])
def test_months_are_kept_apart_per_statistic(frequency):
    assert V._months(stat("s", [("T", frequency, Y2023)])) == frozenset(range(2023 * 12, 2023 * 12 + 12))
