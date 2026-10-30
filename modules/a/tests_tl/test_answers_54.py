"""Plan 54: answers to the new A's basis-axis questions (``claview_a.answers``) and the entry's re-search arguments.

The judge is a made-up answer (no model); the table IDs below are catalog facts the cards show for these questions.
What is checked: an answer is applied only to its statistic and axis, the code checks of a decided table run again
on the answered option table, nothing else changes, no model is called, and bad replies are refused unchanged.
"""

from __future__ import annotations

import copy
import json

import pytest

from claview_a import answers as AN
from claview_a import entry
from claview_a.contract import Settings

from conftest import fill_option_evidence

AS_OF = "2026-10-22"
M2 = "M2 통화량 2023년 보여줘"
GDP = "GDP 2023년 보여줘"
TWO = "M2 통화량과 예금은행 대출금리 2024년"


def ask(sid, axis, *options):
    return {"for": sid, "axis": axis, "options": [{"value": v, "table": t} for v, t in options]}


OUTPUTS = {
    M2: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"}],
         "periods": [{"quote": "2023년", "for": ["s1"]}],
         "asks": [ask("s1", "stock", ("평잔", "DT_161Y013"), ("말잔", "DT_161Y014"))]},
    GDP: {"statistics": [{"id": "s1", "quote": "GDP", "name": "GDP"}],
          "periods": [{"quote": "2023년", "for": ["s1"]}],
          "asks": [ask("s1", "seasonal", ("계절조정", "DT_200Y104"), ("원계열", "DT_200Y106")),
                   ask("s1", "price", ("명목", "DT_200Y105"), ("실질", "DT_200Y106"))]},
    TWO: {"statistics": [{"id": "s1", "quote": "M2 통화량", "name": "M2"},
                         {"id": "s2", "quote": "예금은행 대출금리", "name": "rate"}],
          "periods": [{"quote": "2024년", "for": ["s1", "s2"]}],
          "asks": [ask("s1", "stock", ("평잔", "DT_161Y013"), ("말잔", "DT_161Y014")),
                   ask("s2", "loan_basis", ("신규취급액", "DT_121Y006"), ("잔액기준", "DT_121Y015"))]},
}
KEYS = ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks", "missing",
        "alternatives")


class Judge:
    def __init__(self):
        self.calls = 0

    def __call__(self, payload, request):
        self.calls += 1
        out = {k: copy.deepcopy(OUTPUTS[payload["question"]].get(k, [])) for k in KEYS}
        return json.dumps(out, ensure_ascii=False)


@pytest.fixture()
def run(layer, monkeypatch):
    """One question through the entry; the made-up asks get their option tables' card names as evidence."""
    import claview_a.contract as K

    real = K.parse_output

    def parse(text, cards, answer_contract=K.CONTRACT_VERSION):
        return real(json.dumps(fill_option_evidence(json.loads(text), cards), ensure_ascii=False), cards,
                    answer_contract)

    monkeypatch.setattr(K, "parse_output", parse)

    def go(question):
        judge = Judge()
        state = entry.run(question, judge, as_of=AS_OF, settings=Settings("fake"), layer=layer)
        return state, judge

    return go


def reply(state, q, n):
    return {"question_id": q["question_id"], "question_version": AN.question_version(state),
            "option_id": q["options"][n]["option_id"]}


def decision(state, sid):
    return next(d for d in state["record"]["decisions"] if d["statistic"] == sid)


def test_one_axis_answer_decides_the_statistic_by_the_checks_of_a_decided_table(run, layer):
    state, judge = run(M2)
    assert state["action"] == "ask"
    qs = AN.questions(state)
    assert [(q["question_id"], [o["value"] for o in q["options"]]) for q in qs] == [("q:s1:stock", ["평잔", "말잔"])]
    before = copy.deepcopy(state)
    new = AN.apply(state, [reply(state, qs[0], 1)], layer)
    assert state == before  # the given state is not changed
    assert judge.calls == 1  # no model call for the answer
    d = decision(new, "s1")
    assert d["outcome"] == "selected" and [r["table_id"] for r in d["final_tables"]] == ["DT_161Y014"]
    assert d["basis"]["stock"]["value"] == ["말잔"] and d["basis"]["stock"]["source"] == "user_answer"
    assert {"answer_table", "periods", "final_recheck"} <= set(d["checks_done"])
    assert d["answers"][0]["question_id"] == "q:s1:stock" and d["answers"][0]["source"] == "user_answer"
    assert new["action"] == "handoff" and new["record"]["route"]["action"] == "handoff"
    # what the answer must not change: the question, the date, the cards, the model's output, the run info
    for key in ("question", "as_of", "pool", "cards", "payload", "response_text", "run_info"):
        assert new[key] == state[key]
    assert new["record"]["model_output"] == state["record"]["model_output"]
    assert new["record_before_answers"] == state["record"]
    assert AN.question_version(new) == 1 and AN.questions(new) == []


def test_answers_belong_to_their_statistic_and_keep_the_other_question(run, layer):
    state, _ = run(TWO)
    qs = {q["statistic"]: q for q in AN.questions(state)}
    assert set(qs) == {"s1", "s2"}
    one = AN.apply(state, [reply(state, qs["s1"], 1)], layer)
    assert decision(one, "s1")["outcome"] == "selected" and decision(one, "s2")["outcome"] == "ask"
    assert decision(one, "s2") == decision(state, "s2")  # untouched
    assert [q["question_id"] for q in AN.questions(one)] == ["q:s2:loan_basis"]
    assert one["action"] == "ask"
    q2 = AN.questions(one)[0]
    two = AN.apply(one, [reply(one, q2, 0)], layer)
    assert decision(two, "s1") == decision(one, "s1")  # the settled statistic is kept as it was
    assert [r["table_id"] for r in decision(two, "s2")["final_tables"]] == ["DT_121Y006"]
    assert two["action"] == "handoff"


def test_two_axes_take_the_option_table_holding_both_values_or_fail(run, layer):
    state, _ = run(GDP)
    seasonal, price = AN.questions(state)
    one = AN.apply(state, [reply(state, seasonal, 1)], layer)  # 원계열
    assert decision(one, "s1")["outcome"] == "ask" and [q["axis"] for q in AN.questions(one)] == ["price"]
    two = AN.apply(one, [reply(one, AN.questions(one)[0], 1)], layer)  # 실질
    d = decision(two, "s1")
    assert d["outcome"] == "selected" and d["representative"] == "DT_200Y106"
    assert {b: d["basis"][b]["value"] for b in ("seasonal", "price")} == {"seasonal": ["원계열"], "price": ["실질"]}
    # both answered in one batch, to a pair no option table holds: never a sibling made up by code
    both = AN.apply(state, [reply(state, seasonal, 0), reply(state, price, 0)], layer)  # 계절조정 + 명목
    d = decision(both, "s1")
    assert d["outcome"] == "failed" and d["reasons"][0]["code"] == "target_table_unconfirmed"
    assert d["reasons"][0]["detail"]["stage"] == "answer" and both["action"] == "fail"


@pytest.mark.parametrize("bad, code", [
    (lambda s, q: [], "no_reply"),
    (lambda s, q: [{**reply(s, q, 0), "question_version": 7}], "stale_question"),
    (lambda s, q: [{**reply(s, q, 0), "question_id": "q:s9:stock"}], "question_not_pending"),
    (lambda s, q: [{**reply(s, q, 0), "option_id": "q:s1:stock:o9"}], "option_not_offered"),
    (lambda s, q: [reply(s, q, 0), reply(s, q, 1)], "duplicate_reply"),
    (lambda s, q: [{**reply(s, q, 0), "text": "말잔"}], "reply_shape"),
    (lambda s, q: [{**reply(s, q, 0), "question_version": True}], "stale_question"),
])
def test_bad_replies_are_refused_and_change_nothing(run, layer, bad, code):
    state, _ = run(M2)
    before = copy.deepcopy(state)
    with pytest.raises(AN.AnswerError) as e:
        AN.apply(state, bad(state, AN.questions(state)[0]), layer)
    assert e.value.code == code and state == before


def test_an_answered_question_cannot_be_answered_again(run, layer):
    state, _ = run(M2)
    q = AN.questions(state)[0]
    new = AN.apply(state, [reply(state, q, 0)], layer)
    with pytest.raises(AN.AnswerError) as e:  # the old version
        AN.apply(new, [reply(state, q, 1)], layer)
    assert e.value.code == "stale_question"
    with pytest.raises(AN.AnswerError) as e:  # the current version, no longer pending
        AN.apply(new, [reply(new, q, 1)], layer)
    assert e.value.code == "question_not_pending"


def test_entry_hands_the_re_search_arguments_to_the_graph(monkeypatch, layer):
    seen = {}

    def graph_run(question, layer_, judge, settings, as_of, **kw):
        seen.update(kw, settings=settings)
        return {"action": "fail"}

    monkeypatch.setattr(entry, "_graph_run", graph_run)
    first, second = Settings("first-model"), Settings("second-model")
    budget, rejudge = (lambda: True), (lambda payload, request: "{}")
    entry.run("q", None, as_of=AS_OF, settings=first, layer=layer, research=True, research_budget=budget,
              research_judge=rejudge, research_settings=second)
    assert seen == {"research": True, "research_budget": budget, "research_judge": rejudge,
                    "research_settings": second, "settings": first}
    seen.clear()
    entry.run("q", None, as_of=AS_OF, settings=first, layer=layer)  # unchanged default: no re-search
    assert seen["research"] is False and seen["research_judge"] is None and seen["research_settings"] is None
