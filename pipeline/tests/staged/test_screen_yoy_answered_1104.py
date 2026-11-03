"""B-1·B-2·C-1 through the real screen path (scripted A, real BService and C, SYNTHETIC model and fetch).

* "전년 동월 대비" on a monthly M2 request reads the year before as a calculation range (B-2) and C calculates the
  year-over-year rate of the amount series itself (C-1): the derived % card comes first, the raw card keeps the
  requested year and the faded base year, and the derived card does not carry the amount base year.
* A period the user answered is shown to the selection model (B-1) while the stated years of a question are not.

Values are synthetic ("1" per period), so every rate is 0%; exact values are checked in modules/c/tests. This is a
wiring check, not a check of the real model or of KOSIS numbers.
"""

from __future__ import annotations

from decimal import Decimal

from claview_a_api import screen as a_screen
from claview_a_api.synthetic_judges import ScriptJudge, by_question
from claview_pipeline.screen import ScreenPipeline
from claview_pipeline.synthetic import SyntheticFetchClient, SyntheticSelectionModel

STATISTIC = [{"id": "s1", "quote": "M2 통화량", "name": "M2"}]
TABLE = "DT_161Y014"
AS_OF = "2026-11-03"


class RecordingModel(SyntheticSelectionModel):
    """The synthetic selection model; keeps the `resolved_period` it was shown."""

    def __init__(self, store):
        super().__init__(store)
        self.shown = []

    def complete(self, task, system, context, schema, max_tokens, trace):
        self.shown.append(context["resolved_period"])
        return super().complete(task, system, context, schema, max_tokens, trace)


def pipeline(base, query, script, model, client):
    judge = ScriptJudge(by_question({query: script}))
    return ScreenPipeline(
        model=model, client=client, runs_dir=None, session=base,
        judges_factory=lambda: a_screen.Judges(judge, judge, "script"),
    )


def script(period=None, frequency="월별"):
    return {
        "statistics": STATISTIC,
        "choices": [{"for": ["s1"], "table": TABLE}],
        **({"periods": [{"quote": period, "for": ["s1"]}]} if period else {}),
        **({"frequencies": [{"quote": frequency, "for": ["s1"]}]} if frequency else {}),
    }


def test_a_year_over_year_request_reads_the_base_year_and_c_calculates_the_rate(base):
    query = "M2 통화량 2024년 월별 전년 동월 대비 증감률 보여줘"
    client = SyntheticFetchClient()
    state = pipeline(base, query, script("2024년"), SyntheticSelectionModel(base.store), client).run(query, as_of=AS_OF)
    assert state["status"] == "ok", state.get("message")
    assert [(p["prdSe"], p["startPrdDe"], p["endPrdDe"]) for p in client.calls] == [("M", "202301", "202412")]
    derived, raw = state["ui_cases"]
    assert derived["derived"] is True and derived["unit"] == "%" and "전년 동월 대비 증감률" in derived["tableName"]
    assert derived["periods"] == [f"2024{m:02d}" for m in range(1, 13)]
    assert [Decimal(v) for v in derived["series"][0]["values"]] == [Decimal(0)] * 12
    assert "baseline" not in derived  # the amount base year must not be drawn faded under a % chart
    assert not raw.get("derived") and raw["periods"] == derived["periods"]
    assert raw["requestedPeriod"] == {"frequency": "M", "start": "202401", "end": "202412"}
    assert raw["baseline"]["periods"] == [f"2023{m:02d}" for m in range(1, 13)]
    # the calculation was made: the generic "C does not calculate" warning is not on the raw card
    assert not [w for w in raw["warnings"] if "별도 증감 계산" in w]
    assert any("C에서 계산한 값" in n for n in derived["notices"])


def test_a_year_over_year_request_without_a_basis_for_its_period_shows_only_the_raw_data(base):
    query = "M2 통화량 월별 전년 동월 대비 증감률 보여줘"  # no period at all: nothing says which months to compare
    state = pipeline(base, query, script(), SyntheticSelectionModel(base.store), SyntheticFetchClient()).run(
        query, as_of=AS_OF
    )
    # A asks for the period first; the request never reaches a calculation without one (no guess)
    assert state["status"] == "needs_confirmation"
    assert not state.get("ui_cases")


def test_an_answered_period_is_shown_to_the_model_and_a_stated_year_is_not(base):
    # 1) the user answers the period: the model is told the answered range
    query = "M2 통화량을 월별로 보여줘"
    model, client = RecordingModel(base.store), SyntheticFetchClient()
    pipe = pipeline(base, query, script(), model, client)
    state = pipe.run(query, as_of=AS_OF)
    assert state["status"] == "needs_confirmation"
    card = state["confirmation"]
    (question,) = card["groups"]
    state = pipe.resume(
        state,
        {
            "session_id": card["session_id"],
            "answers": [{"question_id": question["requirement_id"],
                         "question_version": question["question_version"], "text": "2024년 월별"}],
        },
    )
    assert state["status"] == "ok", state.get("message")
    assert model.shown == [{"frequency": "M", "start": "202401", "end": "202412"}]
    assert [(p["prdSe"], p["startPrdDe"], p["endPrdDe"]) for p in client.calls] == [("M", "202401", "202412")]
    (info,) = state["ui_cases"][0]["periodInfo"]
    assert info["sources"]["period"] == "사용자가 고른 값"
    # 2) a year the question itself states stays hidden from the model
    query = "M2 통화량 2024년 월별로 보여줘"
    model = RecordingModel(base.store)
    state = pipeline(base, query, script("2024년"), model, SyntheticFetchClient()).run(query, as_of=AS_OF)
    assert state["status"] == "ok" and model.shown == [None]
