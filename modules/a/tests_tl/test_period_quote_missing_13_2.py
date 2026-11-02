"""Plan 56 order 13-2 review (bundle 10, policy ②, 21:54): a period in the question that the model left out of a
statistic's quotes is never asked. (a) Whose it is is clear (one statistic): the code restores it and nothing is judged
again. (b) It is not (the period is quoted for another statistic only): the statistic is a correction target of the
one re-judgment. (c) The re-judgment does not fix it, or its slot is used: the statistic ends with
``period_quote_missing`` and its recovery status (``attempted_failed``, ``budget_exhausted``). Made-up answers in the
contract .6; the tables are catalog facts the cards show."""

from __future__ import annotations

import re

from test_research_46 import Script, blank

from claview_a import graph as G
from claview_a.contract import Settings

# Preserve the original responses while exercising today's period/recovery code.
FIXTURE_CONTRACT = "tl-contract-2026-10-28.6"

AS_OF = "2026-10-29"
ONE = "M2 통화량 2023년 연간으로 보여줘"
TWO = "M2 통화량과 예금은행 대출금리 2023년 연간으로 보여줘"
PICKS = {"s1": ("M2 통화량", "DT_161Y014"), "s2": ("예금은행 대출금리", "DT_121Y006")}


def card_of(cards: str, table: str) -> tuple[str, str]:
    """(card reference gN.tN, card name) of ``table`` as the payload's cards show it."""
    card = name = None
    for line in cards.splitlines():
        if m := re.match(r"^\[(g\d+)\] (.+)$", line):
            card, name = m.groups()
        elif card and (m := re.match(r"^(t\d+) (\S+) \|", line)) and m.group(2) == table:
            return f"{card}.{m.group(1)}", name
    raise LookupError(table)


def answer(stats, period_for):
    """The made-up answer: ``stats`` with their tables, the question's '2023년' quoted for ``period_for`` only."""

    def build(payload):
        out = {**blank(), "aggregations": [], "changes": []}  # contract .6 keys
        for sid in stats:
            quote, table = PICKS[sid]
            ref, name = card_of(payload["cards"], table)
            out["statistics"].append({"id": sid, "quote": quote, "name": quote})
            out["choices"].append({"for": [sid], "table": ref, "evidence": [{"kind": "title", "text": name}]})
        if period_for:
            out["periods"] = [{"quote": "2023년", "for": list(period_for), "meaning": "range", "refers_to": []}]
        out["frequencies"] = [{"quote": "연간", "for": list(stats)}]
        return out

    return build


class Judge(Script):
    answer_contract = FIXTURE_CONTRACT


def run(question, judge, layer, budget=None):
    state = G.run(question, layer, judge, Settings("fake"), AS_OF, research=True, research_budget=budget)
    return state, {d["statistic"]: d for d in state["record"]["decisions"]}


YEAR_2023 = [{"start": "2023-01", "end": "2023-12"}]


def test_a_clear_owner_is_restored_by_the_code_and_not_judged_again(layer):
    judge = Judge(answer(["s1"], None))
    state, d = run(ONE, judge, layer)
    assert judge.calls == 1 and state["record"]["rejudge"]["targets"] == []
    assert d["s1"]["outcome"] == "selected" and d["s1"]["period_quote_missing"]["restored"] is True
    assert [r["period"] for r in d["s1"]["final_tables"]] == [YEAR_2023]


def test_an_unclear_owner_is_a_correction_target_fixed_by_the_one_re_judgment(layer):
    judge = Judge(answer(["s1", "s2"], ["s2"]), answer(["s1", "s2"], ["s1", "s2"]))
    state, d = run(TWO, judge, layer)
    rejudge = state["record"]["rejudge"]
    assert judge.calls == 2 and not d["s1"].get("condition_asks")  # never asked
    assert [(t["statistic"], t["kind"], t["code"]) for t in rejudge["targets"]] == [
        ("s1", "correction", "period_quote_missing")]
    assert rejudge["sent"] == ["s1"] and rejudge["merged"][0]["outcome"] == "selected"
    assert [r["period"] for r in d["s1"]["final_tables"]] == [YEAR_2023]
    assert d["s2"]["outcome"] == "selected"  # no target: its first decision kept


def test_a_re_judgment_that_does_not_fix_it_ends_with_attempted_failed(layer):
    judge = Judge(answer(["s1", "s2"], ["s2"]), answer(["s1", "s2"], ["s2"]))
    _, d = run(TWO, judge, layer)
    assert judge.calls == 2  # no third judgment
    (r,) = d["s1"]["reasons"]
    assert d["s1"]["outcome"] == "failed" and r["code"] == "period_quote_missing"
    assert r["detail"]["cause"] == "extraction_missing" and r["detail"]["recovery"]["status"] == "attempted_failed"
    assert d["s2"]["outcome"] == "selected" and "recovery" not in str(d["s2"]["reasons"])


def test_a_used_slot_ends_with_budget_exhausted_and_no_call(layer):
    judge = Judge(answer(["s1", "s2"], ["s2"]))
    state, d = run(TWO, judge, layer, budget=lambda: False)
    assert judge.calls == 1 and state["research"]["end"] == "rejudge_call_limit"
    (r,) = d["s1"]["reasons"]
    assert r["code"] == "period_quote_missing" and r["detail"]["recovery"]["status"] == "budget_exhausted"
