"""Contract .8's required rows on the screen service (owner 11-02 02:47 UTC ⑤): a period nobody stated is asked once
for the statistics that share it, never once per row; an answer applies to the statistics of its question only,
keeps their other conditions and calls no model. Scripted answers (made up)."""

from __future__ import annotations

from claview_a import contract as K
from claview_a_api import screen as S
from claview_a_api.run_record import RunStore
from claview_a_api.synthetic_judges import ScriptJudge, by_question

AS_OF = "2026-10-22"
V7, V8 = "tl-contract-2026-10-28.7", K.CONTRACT_VERSION
BOTH = "M2 통화량과 예금은행 대출금리를 연간으로 보여줘"
ONE = "M2 통화량 2023년과 예금은행 대출금리를 연간으로 보여줘"
STATS = [{"id": "s1", "quote": "M2 통화량", "name": "M2"},
         {"id": "s2", "quote": "예금은행 대출금리", "name": "대출금리"}]
CHOICES = [{"for": ["s1"], "table": "DT_161Y014"}, {"for": ["s2"], "table": "DT_121Y006"}]
FOCUSES = [{"for": ["s1"], "text": "M2 통화량", "quotes": ["M2 통화량"]},
           {"for": ["s2"], "text": "예금은행 대출금리", "quotes": ["예금은행 대출금리"]}]


def P(quote, sid, meaning="not_stated", **values):
    row = {"quote": quote, "for": [sid], "meaning": meaning, "refers_to": [], "form": "none", "start": "none",
           "end": "none", "count": "none", "unit": "none", "read": "ok"}
    return {**row, **values}


def v8(periods):
    return {"statistics": STATS, "choices": CHOICES, "focuses": FOCUSES, "periods": periods,
            "frequencies": [{"quote": "연간", "for": ["s1", "s2"], "value": "Y", "anchor": "none"}]}


def run(question, out, contract, store):
    j = ScriptJudge(by_question({question: out}), answer_contract=contract)
    return S.select(question, AS_OF, S.Judges(j, j, "script"), store=store), j


def period_questions(view):
    return [q for q in view["questions"] if q["kind"] == "period"]


def test_a_period_nobody_stated_is_asked_once_as_without_rows(tmp_path):
    old = {"statistics": STATS, "choices": CHOICES, "focuses": FOCUSES, "periods": [],
           "frequencies": [{"quote": "연간", "for": ["s1", "s2"]}]}
    got7, _ = run(BOTH, old, V7, RunStore(tmp_path / "a"))
    got8, _ = run(BOTH, v8([P("M2 통화량", "s1"), P("예금은행 대출금리", "s2")]), V8, RunStore(tmp_path / "b"))
    asked7 = [sorted(q["statistics"]) for q in period_questions(got7["view"])]
    asked8 = [sorted(q["statistics"]) for q in period_questions(got8["view"])]
    assert asked8 == asked7 and len(asked8) == 1  # the separate not_stated rows ask no more than no rows did


def test_an_answer_applies_to_its_statistics_only_and_calls_no_model(tmp_path):
    store = RunStore(tmp_path / "runs")
    out = v8([P("2023년", "s1", meaning="range", form="absolute", start="2023", end="same"),
              P("예금은행 대출금리", "s2")])
    got, judge = run(ONE, out, V8, store)
    (q,) = period_questions(got["view"])
    assert q["statistics"] == ["s2"]
    before = {s["statistic"]: s for s in got["view"]["statistics"]}
    calls = judge.calls
    sid = got["checkpoint"]["session"]["session_id"]
    reply = [{"question_id": q["question_id"], "question_version": q["question_version"], "text": "2024년"}]
    done = S.answer(got["checkpoint"], sid, reply, store)
    assert judge.calls == calls  # applying the answer called no model
    after = {s["statistic"]: s for s in done["view"]["statistics"]}
    assert after["s1"]["tables"] == before["s1"]["tables"]  # the statistic the question did not name is unchanged
    assert [t["period"] for t in after["s2"]["tables"]] == [[{"start": "2024-01", "end": "2024-12"}]]
    assert [t["period"] for t in after["s1"]["tables"]] == [[{"start": "2023-01", "end": "2023-12"}]]
