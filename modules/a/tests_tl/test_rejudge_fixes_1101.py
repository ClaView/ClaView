"""Codex review 11-01 (candidate 39f0c28), fixes ② and ③ of the one self-recovery.

② A second decision taken under the first round's ID keeps no reference to its second-round ID: its condition
question is asked for the first ID and an answer applies (it ended in KeyError before), and the record's model output
reads the taken decision's rows. ③ A first answer that broke the output contract is judged once more as a whole and a
valid second answer is taken; a transport failure still opens nothing and there is no third judgment. Made-up answers
in contract .6; the tables are catalog facts the cards show."""

from __future__ import annotations

import re

from test_research_46 import Script, blank

from claview_a import answers as AN
from claview_a import graph as G
from claview_a.contract import Settings

# These fixed responses predate the focus contract; keep their original schema.
FIXTURE_CONTRACT = "tl-contract-2026-10-28.6"

AS_OF = "2026-10-29"
TWO = "M2 통화량과 예금은행 대출금리 2023년 연간으로 보여줘"
ONE = "M2 통화량 2023년 연간으로 보여줘"
PICKS = {"M2 통화량": "DT_161Y014", "예금은행 대출금리": "DT_121Y006"}


def card_of(cards: str, table: str) -> tuple[str, str]:
    card = name = None
    for line in cards.splitlines():
        if m := re.match(r"^\[(g\d+)\] (.+)$", line):
            card, name = m.groups()
        elif card and (m := re.match(r"^(t\d+) (\S+) \|", line)) and m.group(2) == table:
            return f"{card}.{m.group(1)}", name
    raise LookupError(table)


def answer(stats: dict, periods: list[dict]):
    """``stats``: id -> quote; ``periods``: the period rows as written (contract .6)."""

    def build(payload):
        out = {**blank(), "aggregations": [], "changes": []}
        for sid, quote in stats.items():
            ref, name = card_of(payload["cards"], PICKS[quote])
            out["statistics"].append({"id": sid, "quote": quote, "name": quote})
            out["choices"].append({"for": [sid], "table": ref, "evidence": [{"kind": "title", "text": name}]})
        out["periods"] = periods
        out["frequencies"] = [{"quote": "연간", "for": list(stats)}]
        return out

    return build


class Judge(Script):
    answer_contract = FIXTURE_CONTRACT


def run(question, judge, layer, budget=None):
    return G.run(question, layer, judge, Settings("fake"), AS_OF, research=True, research_budget=budget)


def test_a_renumbered_second_decision_asks_and_answers_under_the_first_id(layer):
    first = answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"},
                   [{"quote": "2023년", "for": ["s2"], "meaning": "range", "refers_to": []}])
    # the second round renumbers the target (s7) and says the user did not decide its period
    second = answer({"s7": "M2 통화량", "s2": "예금은행 대출금리"},
                    [{"quote": "2023년", "for": ["s2"], "meaning": "range", "refers_to": []},
                     {"quote": "M2 통화량", "for": ["s7"], "meaning": "user_undecided", "refers_to": []}])
    judge = Judge(first, second)
    state = run(TWO, judge, layer)
    rejudge = state["record"]["rejudge"]
    assert judge.calls == 2 and rejudge["merged"] == [
        {"statistic": "s1", "second_round_statistic": "s7", "outcome": "ask_condition"}]
    d = {x["statistic"]: x for x in state["record"]["decisions"]}
    assert "s7" not in str(d["s1"].get("condition_asks"))  # no reference left to the second-round ID
    out = state["record"]["model_output"]
    assert [r["for"] for r in out["periods"] if "s1" in r["for"]] == [["s1"]]  # the taken decision's row
    assert [r["for"] for r in out["periods"] if "s2" in r["for"]] == [["s2"]]  # the other statistic's row stays
    (q,) = [x for x in AN.questions(state) if x["kind"] == "period"]
    assert q["question_id"] == "q:s1:period" and q["statistics"] == ["s1"]
    option = next(o for o in q["options"] if o["value"] == "최근 5년")
    after = AN.apply(state, [{"question_id": q["question_id"], "question_version": AN.question_version(state),
                              "option_id": option["option_id"]}], layer)
    got = {x["statistic"]: x for x in after["record"]["decisions"]}
    assert got["s1"]["outcome"] == "selected" and got["s2"]["outcome"] == "selected"
    assert [r["period"] for r in got["s1"]["final_tables"]] == [[{"start": "2022-01", "end": "2026-12"}]]
    assert [r["period"] for r in got["s2"]["final_tables"]] == [[{"start": "2023-01", "end": "2023-12"}]]


def test_a_first_answer_that_broke_the_contract_is_judged_once_more_and_taken(layer):
    good = answer({"s1": "M2 통화량"}, [{"quote": "2023년", "for": ["s1"], "meaning": "range", "refers_to": []}])
    judge = Judge({"statistics": "not a list"}, good)  # the first answer breaks the output contract
    state = run(ONE, judge, layer)
    assert judge.calls == 2 and state["research"]["end"] == "rejudged"
    assert state["record"]["rejudge"]["whole"] is True and not state["record"].get("error")
    (d,) = state["record"]["decisions"]
    assert d["outcome"] == "selected" and [r["table_id"] for r in d["final_tables"]] == ["DT_161Y014"]


def test_a_second_broken_answer_leaves_the_first_error_and_no_third_judgment(layer):
    judge = Judge({"statistics": "not a list"}, {"statistics": "still not a list"})
    state = run(ONE, judge, layer)
    assert judge.calls == 2 and state["record"].get("error")
    assert state["record"]["rejudge"]["rejected"] == [{"statistic": None, "why": "second_round_error"}]


def test_a_transport_failure_of_the_first_round_still_opens_nothing(layer):
    from claview_a.graph import JudgeError

    judge = Judge(JudgeError("timeout"))
    state = run(ONE, judge, layer)
    assert judge.calls == 1 and state["research"]["status"] == "not_run"
    assert state["research"]["end"] == "first_round_error"


def test_a_used_slot_keeps_the_first_contract_error_without_a_call(layer):
    judge = Judge({"statistics": "not a list"})
    state = run(ONE, judge, layer, budget=lambda: False)
    assert judge.calls == 1 and state["research"]["end"] == "rejudge_call_limit" and state["record"].get("error")


# ---------------------------------------------------------------- Codex review 11-01 v3: the whole ID map at once


def _shared_period_run(layer, second_ids):
    # the first round leaves out the period of both statistics, so both are targets; the second renumbers them and
    # asks one shared period for the two
    first = answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"}, [])
    a, b = second_ids
    second = answer({a: "M2 통화량", b: "예금은행 대출금리"},
                    [{"quote": "M2 통화량과 예금은행 대출금리", "for": [a, b], "meaning": "user_undecided",
                      "refers_to": []}])
    judge = Judge(first, second)
    return judge, run(TWO, judge, layer)


def _answer_shared_period(state, layer):
    (q,) = [x for x in AN.questions(state) if x["kind"] == "period"]
    option = next(o for o in q["options"] if o["value"] == "최근 5년")
    after = AN.apply(state, [{"question_id": q["question_id"], "question_version": AN.question_version(state),
                              "option_id": option["option_id"]}], layer)
    return q, {x["statistic"]: x for x in after["record"]["decisions"]}


def test_two_renumbered_statistics_share_one_question_under_the_first_ids(layer):
    judge, state = _shared_period_run(layer, ("s7", "s8"))
    rejudge = state["record"]["rejudge"]
    assert judge.calls == 2 and {(m["statistic"], m["second_round_statistic"]) for m in rejudge["merged"]} == {
        ("s1", "s7"), ("s2", "s8")}
    refs = str([{k: v for k, v in d.items() if k != "rejudged_from"} for d in state["record"]["decisions"]])
    assert "s7" not in refs and "s8" not in refs  # only rejudged_from keeps the second-round ID
    out = state["record"]["model_output"]
    assert [r["for"] for r in out["periods"]] == [["s1", "s2"]]  # the shared row once, under both first IDs
    q, got = _answer_shared_period(state, layer)  # KeyError 's8' before
    assert sorted(q["statistics"]) == ["s1", "s2"]
    assert got["s1"]["outcome"] == "selected" and got["s2"]["outcome"] == "selected"


def test_two_swapped_ids_are_read_at_once_without_collision(layer):
    # the second round gives the first round's s2 the ID s1 and s1 the ID s2
    first = answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"}, [])
    second = answer({"s2": "M2 통화량", "s1": "예금은행 대출금리"},
                    [{"quote": "M2 통화량과 예금은행 대출금리", "for": ["s2", "s1"], "meaning": "user_undecided",
                      "refers_to": []}])
    state = run(TWO, Judge(first, second), layer)
    d = {x["statistic"]: x for x in state["record"]["decisions"]}
    assert d["s1"]["rejudged_from"] == "s2" and d["s2"]["rejudged_from"] == "s1"
    q, got = _answer_shared_period(state, layer)
    assert sorted(q["statistics"]) == ["s1", "s2"] and got["s1"]["outcome"] == got["s2"]["outcome"] == "selected"


def test_a_decision_naming_a_statistic_not_taken_is_not_taken(layer):
    # only s1 is a target (s2's period is given); the second round's s1 shares its question with a new statistic s9
    # the merged record would not hold, so the second decision is refused and the first stays
    first = answer({"s1": "M2 통화량", "s2": "예금은행 대출금리"},
                   [{"quote": "2023년", "for": ["s2"], "meaning": "range", "refers_to": []}])
    second = answer({"s1": "M2 통화량", "s9": "예금은행 대출금리"},
                    [{"quote": "M2 통화량과 예금은행 대출금리", "for": ["s1", "s9"], "meaning": "user_undecided",
                      "refers_to": []}])
    state = run(TWO, Judge(first, second), layer)
    rejudge = state["record"]["rejudge"]
    assert rejudge["merged"] == [] and rejudge["rejected"] == [
        {"statistic": "s1", "why": "reference_not_matched", "refs": ["s9"]}]
    assert "s9" not in str(state["record"]["decisions"]) and "s9" not in str(state["record"]["model_output"])
