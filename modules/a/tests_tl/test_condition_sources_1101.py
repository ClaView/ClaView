"""Owner 11-01 13:39 (A안): where each condition of a final table came from.

- A period word of the question the check restored for its one statistic is the question's: period_source quoted,
  the phrase is the restored words, and the restoring stays recorded (period_quote_missing). Nothing without such
  words becomes quoted.
- A frequency the user answered is user_answer; a period answer alone leaves the frequency's source as it was; a
  statistic that answered nothing keeps its sources.
- A shared same_as period answer gives every linked statistic user_answer for the period only.
Made-up answers in contract .6; the tables are catalog facts the cards show."""

from __future__ import annotations

from test_rejudge_fixes_1101 import Judge, answer, run
from test_same_as_period_1101 import LINK, THREE, answer3, answer_period, by_sid

from claview_a import answers as AN
from claview_a import check as CH


def tables_of(state, layer):
    """statistic -> [(table_id, frequency, period, period_source, frequency_source, phrase)] as the record keeps them:
    the AView's period_source is the final table's period_phrase.source and its frequency_source the decision's
    frequency.source (claview_a_api.view copies them unchanged)."""
    return {d["statistic"]: [(t["table_id"], t["frequency"], t["period"], (t.get("period_phrase") or {}).get("source"),
                              (d.get("frequency") or {}).get("source"), (t.get("period_phrase") or {}).get("text"))
                             for t in d["final_tables"]]
            for d in state["record"]["decisions"]}


def no_frequency(build):
    def wrapped(payload):
        out = build(payload)
        out["frequencies"] = []
        return out

    return wrapped


def answer_all(state, layer, values: dict):
    """Answer every pending condition question by kind: {kind: option value}."""
    replies = []
    for q in AN.questions(state):
        option = next(o for o in q["options"] if o["value"] == values[q["kind"]])
        replies.append({"question_id": q["question_id"], "question_version": AN.question_version(state),
                        "option_id": option["option_id"]})
    return AN.apply(state, replies, layer)


def test_a_restored_question_period_is_quoted_with_its_words_and_the_restoring_is_kept(layer):
    # one statistic; the model quotes no period, the check restores '2023년' from the question
    state = run("M2 통화량 2023년 연간으로 보여줘", Judge(answer({"s1": "M2 통화량"}, [])), layer)
    (d,) = state["record"]["decisions"]
    assert d["period_quote_missing"]["restored"] is True and d["period_quote_missing"]["quotes"] == ["2023년"]
    ((table, freq, period, psource, fsource, phrase),) = tables_of(state, layer)["s1"]
    assert period == [{"start": "2023-01", "end": "2023-12"}]
    assert (psource, phrase) == ("quoted", "2023년")  # before: ('default', None)
    assert fsource == "question"


def test_a_table_with_no_words_and_no_answer_stays_default():
    # read by annotate directly: a final table with no period row, no restored words and no answer is never quoted
    d = {"statistic": "s1", "outcome": "selected", "reasons": [], "warnings": [], "condition_answers": [],
         "final_tables": [{"table_id": "T", "frequency": "Y", "period": [{"start": "2023-01", "end": "2023-12"}]}]}
    CH.annotate(d, {"periods": [], "frequencies": []})
    assert d["final_tables"][0]["period_phrase"] == {"text": None, "source": "default"}
    d2 = {**d, "period_quote_missing": {"quotes": ["2023년"], "quoted_for_other_statistics": True, "restored": False},
          "final_tables": [dict(d["final_tables"][0])]}
    CH.annotate(d2, {"periods": [], "frequencies": []})  # words found but not this statistic's: not quoted
    assert d2["final_tables"][0]["period_phrase"]["source"] == "default"


def test_a_period_and_a_frequency_the_user_answered_are_user_answer(layer):
    state = run("M2 통화량 추이를 보여줘", Judge(no_frequency(answer({"s1": "M2 통화량"}, []))), layer)
    kinds = sorted(q["kind"] for q in AN.questions(state))
    assert kinds == ["frequency", "period"]
    after = answer_all(state, layer, {"period": "최근 5년", "frequency": "Y"})
    ((table, freq, period, psource, fsource, phrase),) = tables_of(after, layer)["s1"]
    assert (freq, psource, fsource, phrase) == ("Y", "user_answer", "user_answer", "최근 5년")  # before: question


def test_a_period_answer_alone_keeps_the_quoted_frequency_source(layer):
    state = run("M2 통화량을 연간으로 보여줘", Judge(answer({"s1": "M2 통화량"}, [])), layer)
    assert [q["kind"] for q in AN.questions(state)] == ["period"]
    after = answer_all(state, layer, {"period": "최근 5년"})
    ((table, freq, period, psource, fsource, phrase),) = tables_of(after, layer)["s1"]
    assert (freq, psource, fsource) == ("Y", "user_answer", "question")


def test_a_shared_same_as_answer_marks_the_linked_periods_only(layer):
    rows = [{**LINK, "for": ["s2"], "refers_to": ["s1"]},
            {"quote": "2024년", "for": ["s3"], "meaning": "range", "refers_to": []}]
    state = run(THREE, Judge(answer3({"s1": "M2 통화량", "s2": "예금은행 대출금리", "s3": "예금은행 수신금리"}, rows)),
                layer)
    q, after = answer_period(state, layer, "최근 5년")
    got = tables_of(after, layer)
    for sid in ("s1", "s2"):  # the linked statistics: the period answered, the frequency as quoted
        ((_, freq, period, psource, fsource, phrase),) = got[sid]
        assert (psource, fsource, phrase) == ("user_answer", "question", "최근 5년")
    ((_, freq, period, psource, fsource, phrase),) = got["s3"]  # unlinked: its own quote
    assert (psource, fsource, phrase) == ("quoted", "question", "2024년")
