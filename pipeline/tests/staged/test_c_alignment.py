# ruff: noqa: E501
"""C part: aligned series, the dual-axis view, and the frequency question (pause/resume).

The fetched data is replaced by a crafted `data_result` (A, B and the fetch client are the
synthetic ones), so these check wiring, not statistics. No HCX/KOSIS call is made.
"""

from __future__ import annotations

import pytest

from claview_b.metadata import periods_between

AS_OF = "2026-10-22"
PROCEED_Q = "GDP 2023년 보여줘"


def make_series(
    sid, unit, freq="M", start="202401", end="202406", values=None, label=None, table="DT_TEST"
):
    periods = periods_between(start, end, freq)
    values = values if values is not None else [str(i + 1) for i in range(len(periods))]
    points = [
        {
            "period": p,
            "raw_value": v,
            "value": v,
            "value_status": "observed" if v is not None else "missing",
        }
        for p, v in zip(periods, values, strict=True)
    ]
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
        "coverage": {"expected": len(periods), "returned": len(points), "missing_periods": []},
        "provenance": {"fetched_at": "2026-10-22T00:00:00+00:00"},
    }


def crafted(monkeypatch, pipeline, *series, **extra):
    data = {"series": list(series), "relations": [], **extra}
    monkeypatch.setattr(pipeline, "_render_data", lambda result, fetched, b: data)
    return data


def answer(paused, option_id):
    group = paused["confirmation"]["groups"][0]
    return {
        "choices": [{"requirement_id": group["requirement_id"], "option_id": option_id}],
        "interpretation_confirmed": False,
    }


def stage_status(state):
    return {s["id"]: s["status"] for s in state["stages"]}


# ---- dual axis ----------------------------------------------------------------------
def test_amount_and_rate_become_one_dual_axis_card(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(
        monkeypatch,
        pipeline,
        make_series(
            "S1", "십억원", label="GDP / 금액", values=["100", "110", "120", "130", "140", "150"]
        ),
        make_series(
            "S2",
            "%",
            label="GDP / 증감률",
            values=["1.5", "2.0", None, "2.5", "3.0", "3.5"],
            table="DT_RATE",
        ),
    )
    state = pipeline.run(PROCEED_Q, as_of=AS_OF)
    assert state["status"] == "partial"  # one missing value
    assert len(state["ui_cases"]) == 1
    card = state["ui_cases"][0]
    assert card["defaultView"] == "dual_axis"
    assert (
        card["kinds"][0] == "dual_axis"
        and "line" not in card["kinds"]
        and "bar" not in card["kinds"]
    )
    assert [a["side"] for a in card["axes"]] == ["left", "right"]
    assert [a["unit"] for a in card["axes"]] == ["십억원", "%"]
    assert [a["draw"] for a in card["axes"]] == ["column", "line"]
    assert [(s["unit"], s["axis"]) for s in card["series"]] == [("십억원", 0), ("%", 1)]
    assert card["unit"] == "십억원 · %"
    assert any(b["kind"] == "line" for b in card["blocked"])
    assert any("왼쪽·오른쪽 축" in n for n in card["notices"])
    assert card["series"][1]["values"][2] is None  # a gap stays a gap
    assert set(stage_status(state).values()) <= {"completed", "warning"}


def test_same_unit_stays_a_normal_chart(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(
        monkeypatch,
        pipeline,
        make_series("S1", "십억원"),
        make_series("S2", "십억원", table="DT_B"),
    )
    state = pipeline.run(PROCEED_Q, as_of=AS_OF)
    card = state["ui_cases"][0]
    assert "axes" not in card and "dual_axis" not in card["kinds"]
    assert card["defaultView"] == "line"


def test_bar_request_draws_the_left_axis_as_columns_and_line_request_draws_lines(
    make_pipeline, monkeypatch
):
    for query, draws in (
        ("GDP 2023년 막대그래프로 보여줘", ["column", "line"]),
        ("GDP 2023년 꺾은선으로 보여줘", ["line", "line"]),
    ):
        pipeline = make_pipeline()
        crafted(
            monkeypatch, pipeline, make_series("S1", "%"), make_series("S2", "십억원", table="DT_B")
        )
        card = pipeline.run(query, as_of=AS_OF)["ui_cases"][0]
        assert [a["draw"] for a in card["axes"]] == draws
        assert card["defaultView"] == "dual_axis" or card["defaultView"] == "table"


def test_dual_axis_request_is_honoured_or_explained():
    from claview_c.chart_policy import choose

    possible = choose("이중 축으로 보여줘", ["dual_axis", "table"], periods=6, series_count=2)
    assert possible["selected"] == "dual_axis"
    impossible = choose("이중 축으로 보여줘", ["line", "bar", "table"], periods=6, series_count=2)
    assert impossible["selected"] == "line"
    assert any("이중 축은 단위가 서로 다른" in n["message"] for n in impossible["notices"])
    # without a request, a dual-axis chart is the default when it is available
    assert (
        choose("보여줘", ["dual_axis", "table"], periods=6, series_count=2)["selected"]
        == "dual_axis"
    )
    # an explicit request for another view still wins
    assert (
        choose("수치표로 보여줘", ["dual_axis", "table"], periods=6, series_count=2)["selected"]
        == "table"
    )


def test_three_units_keep_one_card_per_unit(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(
        monkeypatch,
        pipeline,
        make_series("S1", "십억원"),
        make_series("S2", "%", table="DT_B"),
        make_series("S3", "건", table="DT_C"),
    )
    state = pipeline.run(PROCEED_Q, as_of=AS_OF)
    assert len(state["ui_cases"]) == 3
    assert all("axes" not in c for c in state["ui_cases"])


def test_currency_difference_is_not_converted(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(
        monkeypatch,
        pipeline,
        make_series("S1", "십억원", values=["10"] * 6),
        make_series("S2", "백만달러", values=["7"] * 6, table="DT_USD"),
    )
    card = pipeline.run(PROCEED_Q, as_of=AS_OF)["ui_cases"][0]
    assert [s["unit"] for s in card["series"]] == ["십억원", "백만달러"]
    assert card["series"][1]["values"] == ["7"] * 6
    assert any("환율로 환산하지 않았습니다" in n for n in card["notices"])


def test_scale_difference_is_converted_in_the_card(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(
        monkeypatch,
        pipeline,
        make_series("S1", "십억원", values=["1", "2", "3", "4", "5", "6"]),
        make_series("S2", "백만원", values=["1500"] * 6, table="DT_B"),
    )
    card = pipeline.run(PROCEED_Q, as_of=AS_OF)["ui_cases"][0]
    assert card["unit"] == "십억원"
    assert card["series"][1]["values"] == ["1.5"] * 6
    assert any("단위 배율을 십억원로 맞췄습니다" in n for n in card["notices"])


# ---- frequency question -------------------------------------------------------------
def monthly_quarterly():
    return (
        make_series(
            "S1",
            "십억원",
            freq="M",
            start="202401",
            end="202406",
            label="월 지표",
            values=["1", "2", "3", "4", "5", "6"],
        ),
        make_series(
            "S2",
            "십억원",
            freq="Q",
            start="202401",
            end="202402",
            label="분기 지표",
            values=["10", "20"],
            table="DT_Q",
        ),
    )


def test_frequency_difference_pauses_with_a_choice_and_keeps_the_fetched_data(
    make_pipeline, monkeypatch
):
    pipeline = make_pipeline()
    crafted(monkeypatch, pipeline, *monthly_quarterly())
    waiting = pipeline.run(PROCEED_Q, as_of=AS_OF)
    assert waiting["status"] == "needs_confirmation"
    assert waiting["error_code"] == "frequency_choice_required"
    status = stage_status(waiting)
    assert status["fetch"] == "completed" and status["chart"] == "needs_confirmation"
    assert waiting["ui_cases"] == []
    confirmation = waiting["confirmation"]
    assert confirmation["kind"] == "alignment"
    options = confirmation["groups"][0]["options"]
    assert [o["option_id"] for o in options] == ["freq_mean", "freq_last", "freq_sum", "freq_none"]
    assert all(o["tables"][0]["table_name"] == o["label"] for o in options)
    assert waiting["render_jobs"] and waiting["calls"]["KOSIS"] >= 1


def test_choosing_a_method_resumes_without_any_new_call(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(monkeypatch, pipeline, *monthly_quarterly())
    waiting = pipeline.run(PROCEED_Q, as_of=AS_OF)
    hcx_before, kosis_before = len(pipeline.model.calls), len(pipeline.client.calls)
    resumed = pipeline.resume(waiting, answer(waiting, "freq_mean"))
    assert (len(pipeline.model.calls), len(pipeline.client.calls)) == (hcx_before, kosis_before)
    assert resumed["calls"] == {"HCX": 0, "KOSIS": 0}
    assert resumed["status"] == "ok" and resumed["resume_kind"] == "alignment"
    assert resumed["parent_run_id"] == waiting["run_id"]
    assert resumed["user_confirmation"]["option_id"] == "freq_mean"
    assert set(stage_status(resumed).values()) == {"completed"}
    card = resumed["ui_cases"][0]
    assert card["freq"] == "Q"
    assert [s["values"] for s in card["series"]] == [["2", "5"], ["10", "20"]]
    assert any("주기를 분기로 맞췄습니다" in n for n in card["notices"])


def test_choosing_to_keep_frequencies_separate_gives_one_card_per_frequency(
    make_pipeline, monkeypatch
):
    pipeline = make_pipeline()
    crafted(monkeypatch, pipeline, *monthly_quarterly())
    waiting = pipeline.run(PROCEED_Q, as_of=AS_OF)
    resumed = pipeline.resume(waiting, answer(waiting, "freq_none"))
    assert resumed["status"] == "ok"
    assert sorted(c["freq"] for c in resumed["ui_cases"]) == ["M", "Q"]


def test_frequency_choice_rejects_options_not_offered(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    crafted(monkeypatch, pipeline, *monthly_quarterly())
    waiting = pipeline.run(PROCEED_Q, as_of=AS_OF)
    for bad in (
        answer(waiting, "freq_magic"),
        {**answer(waiting, "freq_mean"), "choices": []},
        {
            "choices": [{"requirement_id": "other", "option_id": "freq_mean"}],
            "interpretation_confirmed": False,
        },
    ):
        with pytest.raises(ValueError):
            pipeline.validate_resume(waiting, bad)


def test_sum_is_not_offered_for_a_rate_and_a_forged_sum_is_refused(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    monthly = make_series("S1", "%", freq="M", start="202401", end="202406", label="월 증감률")
    quarterly = make_series(
        "S2", "십억원", freq="Q", start="202401", end="202402", values=["10", "20"], table="DT_Q"
    )
    crafted(monkeypatch, pipeline, monthly, quarterly)
    waiting = pipeline.run(PROCEED_Q, as_of=AS_OF)
    offered = [o["option_id"] for o in waiting["confirmation"]["groups"][0]["options"]]
    assert "freq_sum" not in offered
    with pytest.raises(ValueError):
        pipeline.validate_resume(waiting, answer(waiting, "freq_sum"))


# ---- combining requirements ---------------------------------------------------------
def test_requirements_are_combined_only_when_one_chart_is_requested(make_pipeline):
    pipeline = make_pipeline()
    one = {
        "data": {"series": [make_series("S1", "십억원")], "relations": []},
        "tables": [{"table_id": "A"}],
        "run_id": "r-1",
    }
    two = {
        "data": {"series": [make_series("S1", "%", table="DT_B")], "relations": []},
        "tables": [{"table_id": "B"}],
        "run_id": "r-2",
    }
    assert pipeline._combine_jobs("GDP와 증감률 보여줘", [one, two]) == [one, two]
    merged = pipeline._combine_jobs("GDP와 증감률을 한 그래프에서 보여줘", [one, two])
    assert len(merged) == 1
    assert [s["series_id"] for s in merged[0]["data"]["series"]] == ["R1-S1", "R2-S1"]
    assert [t["table_id"] for t in merged[0]["tables"]] == ["A", "B"]
    assert merged[0]["run_id"] == "r-1"


def test_combined_requirements_render_as_one_dual_axis_card(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    state = pipeline.run(PROCEED_Q, as_of=AS_OF, through="B")
    assert state["status"] == "b_verified"
    jobs = pipeline._combine_jobs(
        "GDP와 증감률을 한 그래프에서 보여줘",
        [
            {
                "data": {
                    "series": [make_series("S1", "십억원", label="GDP / 금액")],
                    "relations": [],
                },
                "tables": [{"table_id": "DT_TEST", "table_name": "가"}],
                "run_id": "r-1",
            },
            {
                "data": {
                    "series": [make_series("S1", "%", label="GDP / 증감률", table="DT_B")],
                    "relations": [],
                },
                "tables": [{"table_id": "DT_B", "table_name": "나"}],
                "run_id": "r-2",
            },
        ],
    )
    out_state = {
        "query": "GDP와 증감률을 한 그래프에서 보여줘",
        "stage_outcomes": {},
        "ui_cases": [],
    }
    stages = [
        {"id": s, "status": "pending", "summary": ""}
        for s in ("question", "table", "conditions", "fetch", "chart")
    ]
    out_state["stages"] = stages

    def update(stage, status, summary, rows=None, listing=None, note=""):
        out_state["stages"][["question", "table", "conditions", "fetch", "chart"].index(stage)] = {
            "id": stage,
            "status": status,
            "summary": summary,
        }

    pipeline._render_stage(out_state, jobs, None, update, [], [], False)
    assert len(out_state["ui_cases"]) == 1
    card = out_state["ui_cases"][0]
    assert card["defaultView"] == "dual_axis"
    assert [s["id"] for s in card["series"]] == ["R1-S1", "R2-S1"]
    assert card["tableName"] == "가 · 나"
