"""Owner 11-01 12:26 (live question A:CX-Q077, 13-6): a shared period stated as same_as, and a re-judgment that refers to
a fixed statistic.

- Statistics a same_as row links with no concrete period between them are asked one period question; one answer gives
  every linked statistic the same period, and nothing else changes. A chain or a cycle is read one step and never
  loops.
- A re-judgment may name a fixed statistic in a period's refers_to (read, never judged again): the re-judged statistic
  takes the fixed one's period; an ID that is neither its own nor fixed is still refused.
- The stored answers of the live question (``data/q077_stored_answers_1101.json``) run again offline: before the fix
  the first statistic failed and the re-judgment was refused (statistic_ref); now one period question is asked and the
  answer gives both statistics the same period with no re-judgment. Made-up answers otherwise (contract .6); the
  tables are catalog facts the cards show."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_rejudge_fixes_1101 import AS_OF, FIXTURE_CONTRACT, Judge, answer, card_of, PICKS, run

from claview_a import answers as AN
from claview_a import contract as K
from claview_a import graph as G
from claview_a.contract import Settings

SAME = "M2 통화량과 예금은행 대출금리를 같은 기간으로 연간 보여줘"
FIXED = "예금은행 대출금리는 2023년, M2 통화량도 같은 기간으로 연간 보여줘"
STORED = json.loads((Path(__file__).parent / "data" / "q077_stored_answers_1101.json").read_text(encoding="utf-8"))
LINK = {"quote": "같은 기간으로", "meaning": "same_as"}


def by_sid(state):
    return {d["statistic"]: d for d in state["record"]["decisions"]}


def answer_period(state, layer, value):
    (q,) = [x for x in AN.questions(state) if x["kind"] == "period"]
    option = next(o for o in q["options"] if o["value"] == value)
    after = AN.apply(state, [{"question_id": q["question_id"], "question_version": AN.question_version(state),
                              "option_id": option["option_id"]}], layer)
    return q, after


def periods(d):
    return [[t["frequency"], t["period"]] for t in d["final_tables"]]


# ---------------------------------------------------------------- same_as links with no concrete period


@pytest.mark.parametrize("rows", [
    [{**LINK, "for": ["s2"], "refers_to": ["s1"]}],  # one direction: s2's period is s1's
    [{**LINK, "for": ["s1"], "refers_to": ["s2"]}, {**LINK, "for": ["s2"], "refers_to": ["s1"]}],  # a cycle
], ids=["one_way", "cycle"])
def test_linked_statistics_with_no_period_are_asked_once_and_answered_together(layer, rows):
    judge = Judge(answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"}, rows))
    state = run(SAME, judge, layer)
    assert judge.calls == 1  # nothing to recover: the period is the user's
    d = by_sid(state)
    assert d["s1"]["outcome"] == d["s2"]["outcome"] == "ask_condition"
    assert not d["s1"]["reasons"] and not d["s2"]["reasons"]
    q, after = answer_period(state, layer, "최근 5년")
    assert q["statistics"] == ["s1", "s2"] and q["question_id"] == "q:s1+s2:period"
    got = by_sid(after)
    assert got["s1"]["outcome"] == got["s2"]["outcome"] == "selected"
    assert periods(got["s1"]) == periods(got["s2"]) == [["Y", [{"start": "2022-01", "end": "2026-12"}]]]
    # the answer changed the period only: the model output, the tables and the frequency rows stay
    assert after["record"]["model_output"] == state["record"]["model_output"]
    assert [t["table_id"] for t in got["s1"]["final_tables"]] == [PICKS["M2 통화량"]]
    assert [t["table_id"] for t in got["s2"]["final_tables"]] == [PICKS["예금은행 대출금리"]]


def test_a_link_to_a_statistic_with_its_own_period_takes_that_period_and_asks_nothing(layer):
    rows = [{"quote": "2023년", "for": ["s1"], "meaning": "range", "refers_to": []},
            {**LINK, "for": ["s2"], "refers_to": ["s1"]}]
    q = "M2 통화량은 2023년, 예금은행 대출금리도 같은 기간으로 연간 보여줘"
    state = run(q, Judge(answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"}, rows)), layer)
    d = by_sid(state)
    assert not AN.questions(state)
    assert periods(d["s1"]) == periods(d["s2"]) == [["Y", [{"start": "2023-01", "end": "2023-12"}]]]


# ---------------------------------------------------------------- a re-judgment refers to a fixed statistic


def test_the_parser_reads_a_fixed_statistic_in_refers_to_only(layer):
    state = run(FIXED, Judge(answer({"s1": "M2 통화량"}, [])), layer)
    cards = state["cards"]

    def text(refers, for_=("s1",)):
        ref, name = card_of(state["payload"]["cards"], PICKS["M2 통화량"])
        return json.dumps({"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2 통화량"}],
                           "choices": [{"for": list(for_), "table": ref, "evidence": [{"kind": "title", "text": name}]}],
                           "splitters": [], "periods": [{**LINK, "for": ["s1"], "refers_to": list(refers)}],
                           "frequencies": [], "calculations": [], "asks": [], "missing": [], "alternatives": [],
                           "aggregations": [], "changes": []}, ensure_ascii=False)

    with pytest.raises(K.ContractError) as e:  # without the fixed IDs: refused as before
        K.parse_output(text(["s2"]), cards, FIXTURE_CONTRACT)
    assert e.value.code == "statistic_ref"
    got = K.parse_output(text(["s2"]), cards, FIXTURE_CONTRACT, fixed_ids=["s2"])
    assert got["output"]["periods"][0]["refers_to"] == ["s2"]
    for bad in (text(["s9"]), text(["s2"], for_=("s1", "s2"))):  # an unknown ID; a fixed one judged again
        with pytest.raises(K.ContractError) as e:
            K.parse_output(bad, cards, FIXTURE_CONTRACT, fixed_ids=["s2"])
        assert e.value.code == "statistic_ref"


def test_a_re_judged_statistic_takes_the_fixed_statistics_period_and_leaves_it_unchanged(layer):
    first = answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"},
                   [{"quote": "2023년", "for": ["s2"], "meaning": "range", "refers_to": []}])
    second = answer({"s1": "M2 통화량"}, [{**LINK, "for": ["s1"], "refers_to": ["s2"]}])
    judge = Judge(first, second)
    state = run(FIXED, judge, layer)
    rejudge = state["record"]["rejudge"]
    assert judge.calls == 2 and rejudge["merged"] == [
        {"statistic": "s1", "second_round_statistic": "s1", "outcome": "selected"}]
    d = by_sid(state)
    assert periods(d["s1"]) == periods(d["s2"]) == [["Y", [{"start": "2023-01", "end": "2023-12"}]]]
    assert "rejudged_from" not in d["s2"]  # the fixed statistic is the first round's, untouched
    out = state["record"]["model_output"]
    assert [r for r in out["periods"] if "s2" in r["for"]] == [
        {"quote": "2023년", "for": ["s2"], "meaning": "range", "refers_to": []}]


def test_a_re_judgment_naming_an_unknown_statistic_is_refused_and_the_first_round_stays(layer):
    first = answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"},
                   [{"quote": "2023년", "for": ["s2"], "meaning": "range", "refers_to": []}])
    second = answer({"s1": "M2 통화량"}, [{**LINK, "for": ["s1"], "refers_to": ["s9"]}])
    judge = Judge(first, second)
    state = run(FIXED, judge, layer)
    assert judge.calls == 2 and state["research"]["end"] == "second_round_error"
    d = by_sid(state)
    assert d["s1"]["outcome"] == "failed" and d["s2"]["outcome"] == "selected"


# ---------------------------------------------------------------- the stored answers of the live question


def stored_run(layer):
    texts = [a["text"] for a in STORED["answers"]]
    calls = []

    def judge_for(n):
        def judge(payload, request):
            calls.append(request["model"])
            return texts[n]

        judge.answer_contract = STORED["contract"]
        return judge

    state = G.run(STORED["question"], layer, judge_for(0), Settings("gpt-6.1-terra", "low", 4096), STORED["as_of"],
                  research=True, research_judge=judge_for(1), research_settings=Settings("gpt-6.1-sol", "low", 4096))
    return state, calls


def test_the_stored_live_answers_ask_one_shared_period_and_answer_both(layer):
    state, calls = stored_run(layer)
    assert calls == ["gpt-6.1-terra"]  # before the fix: s1 failed (period_quote_missing) and the Sol answer was refused
    q, after = answer_period(state, layer, "최근 3년")
    assert q["statistics"] == ["s1", "s2"]
    got = by_sid(after)
    assert got["s1"]["outcome"] == got["s2"]["outcome"] == "selected"
    assert periods(got["s1"]) == periods(got["s2"]) == [["M", [{"start": "2024-01", "end": "2026-10"}]]]
    assert after["record"]["model_output"] == state["record"]["model_output"]


def test_the_stored_re_judgment_reads_under_the_fixed_statistic(layer):
    # the stored Sol answer names the fixed s2 in refers_to: refused before (statistic_ref), read now
    state, _ = stored_run(layer)
    with pytest.raises(K.ContractError):
        K.parse_output(STORED["answers"][1]["text"], state["cards"], STORED["contract"])
    got = K.parse_output(STORED["answers"][1]["text"], state["cards"], STORED["contract"], fixed_ids=["s2"])
    assert got["output"]["periods"][0]["refers_to"] == ["s2"]


# ---------------------------------------------------------------- owner 11-01 13:17: an unlinked statistic's period

THREE = "M2 통화량과 예금은행 대출금리는 같은 기간으로 비교하고, 예금은행 수신금리는 2024년을 보여줘. 모두 연간으로 보여줘."
PICKS3 = {**PICKS, "예금은행 수신금리": "DT_121Y002"}


def answer3(stats: dict, periods: list[dict]):
    def build(payload):
        out = answer({}, periods)(payload)
        for sid, quote in stats.items():
            ref, name = card_of(payload["cards"], PICKS3[quote])
            out["statistics"].append({"id": sid, "quote": quote, "name": quote})
            out["choices"].append({"for": [sid], "table": ref, "evidence": [{"kind": "title", "text": name}]})
        out["frequencies"] = [{"quote": "연간", "for": list(stats)}]
        return out

    return build


def test_the_reported_reading_keeps_a_linked_statistic_out_of_period_quote_missing():
    # the owner's case as reported: s2 same_as s1, s3 a range of its own; read by the period check directly
    from claview_a import check as CH

    q = "기대인플레이션율과 경제심리지수는 같은 기간으로 비교하고, M2 통화량은 2024년을 보여줘. 모두 월별로 보여줘."
    out = {"statistics": [{"id": "s1", "quote": "기대인플레이션율", "name": "기대인플레이션율"},
                          {"id": "s2", "quote": "경제심리지수", "name": "경제심리지수"},
                          {"id": "s3", "quote": "M2 통화량", "name": "M2 통화량"}],
           "periods": [{"quote": "같은 기간으로", "for": ["s2"], "meaning": "same_as", "refers_to": ["s1"]},
                       {"quote": "2024년", "for": ["s3"], "meaning": "range", "refers_to": []}],
           "frequencies": [{"quote": "월별", "for": ["s1", "s2", "s3"]}], "splitters": [], "calculations": []}
    got = {}
    for sid in ("s1", "s2", "s3"):
        d = {"statistic": sid}
        reading = CH.condition_reading(d, q, out, polar=True)
        got[sid] = (d.get("period_quote_missing"), any(c["kind"] == "period" for c in reading["conditions"]))
    assert got["s1"] == (None, False)  # before: period_quote_missing (quoted for other statistics) from s3's 2024년
    assert got["s2"] == (None, False)
    assert got["s3"] == (None, True)


def test_linked_statistics_are_asked_together_and_an_unlinked_period_stays(layer):
    rows = [{**LINK, "for": ["s2"], "refers_to": ["s1"]},
            {"quote": "2024년", "for": ["s3"], "meaning": "range", "refers_to": []}]
    judge = Judge(answer3({"s1": "M2 통화량", "s2": "예금은행 대출금리", "s3": "예금은행 수신금리"}, rows))
    state = run(THREE, judge, layer)
    assert judge.calls == 1
    d = by_sid(state)
    assert d["s1"]["outcome"] == d["s2"]["outcome"] == "ask_condition" and not d["s1"]["reasons"]
    assert d["s3"]["outcome"] == "selected" and periods(d["s3"]) == [["Y", [{"start": "2024-01", "end": "2024-12"}]]]
    q, after = answer_period(state, layer, "최근 5년")
    assert q["statistics"] == ["s1", "s2"]  # s3 is not asked
    got = by_sid(after)
    assert periods(got["s1"]) == periods(got["s2"]) == [["Y", [{"start": "2022-01", "end": "2026-12"}]]]
    assert periods(got["s3"]) == [["Y", [{"start": "2024-01", "end": "2024-12"}]]]  # unchanged by the answer
    assert got["s3"].get("condition_answers") in (None, [])
    assert after["record"]["model_output"] == state["record"]["model_output"]


def test_an_unlinked_statistic_without_its_own_period_still_stops_as_before(layer):
    # no same_as anywhere: a period quoted for another statistic only is still not this one's (rule unchanged)
    rows = [{"quote": "2024년", "for": ["s3"], "meaning": "range", "refers_to": []}]
    q = "M2 통화량과 예금은행 대출금리, 예금은행 수신금리는 2024년을 보여줘. 모두 연간으로 보여줘."
    state = run(q, Judge(answer3({"s1": "M2 통화량", "s2": "예금은행 대출금리", "s3": "예금은행 수신금리"}, rows)),
                layer, budget=lambda: False)  # the first round only
    d = by_sid(state)
    assert [r["code"] for r in d["s1"]["reasons"]] == ["period_quote_missing"]
