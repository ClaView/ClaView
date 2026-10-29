"""The one re-search after a wholly missing first round (user 2026-10-29 07:51 UTC), with scripted fake judges only
(no model, no network). The first round's search words are the ones a live answer gave for these questions; the
second round's answers are written here to exercise the path, so nothing here says what a model would answer.

- recovery: the re-search adds the card the search word names; the second answer goes through the same checks.
- control: the added candidates are unrelated; an answer still missing stays missing, nothing is selected by code.
- ends with the reason recorded: off by default, decided statistics present, no search words, no new tables, input
  over the limit, no re-judge budget, a second-round error.
Cards are found by name, never by table ID."""

from __future__ import annotations

import json
import re

import pytest

from claview_a import contract as K
from claview_a.cards import build_cards, table_numbers
from claview_a.contract import Settings
from claview_a.graph import JudgeError, research_summary, research_words, run
from claview_a.pool import EXTRA_CAP, build_pool, pool_tables

AS_OF = "2026-10-22"
CV22 = "2024년에 국내에서 생산해 국내에 팔거나 수출한 것들의 전체 물가 흐름을 월별로 보여줘"
Q093 = "우리나라가 해외에 갚아야 할 빚의 규모를 보여줘."
Q082 = "원/달러 환율을 2022년부터 월평균으로 그려 주세요."


def blank() -> dict:
    return {k: [] for k in ("statistics", "splitters", "periods", "frequencies", "calculations", "choices", "asks",
                            "missing", "alternatives")}


def missing(quote, words, period=None, frequency=None) -> dict:
    out = blank()
    out["statistics"] = [{"id": "s1", "quote": quote, "name": quote}]
    out["missing"] = [{"for": ["s1"], "search_words": list(words)}]
    out["periods"] = [{"quote": period, "for": ["s1"]}] if period else []
    out["frequencies"] = [{"quote": frequency, "for": ["s1"]}] if frequency else []
    return out


def card_ref(payload: dict, name: str) -> str | None:
    """``gN.t1`` of the card called ``name`` in the payload's card text (None: no such card)."""
    m = re.search(rf"^\[(g\d+)\] {re.escape(name)}$", payload["cards"], re.M)
    return f"{m.group(1)}.t1" if m else None


def choose(name, quote, period=None, frequency=None):
    def answer(payload):
        out = missing(quote, ["unused"], period, frequency)
        out["missing"] = []
        out["choices"] = [{"for": ["s1"], "table": card_ref(payload, name), "evidence": [{"kind": "title",
                                                                                         "text": name}]}]
        return out
    return answer


class Script:
    """Answers in order, one per call; a callable answer gets the payload. Counts calls and keeps the payloads and
    requests."""

    def __init__(self, *answers):
        self.answers, self.calls, self.payloads, self.requests = list(answers), 0, [], []

    def __call__(self, payload, request):
        self.calls += 1
        self.payloads.append(payload)
        self.requests.append(request)
        a = self.answers[self.calls - 1]
        if isinstance(a, Exception):
            raise a
        return json.dumps(a(payload) if callable(a) else a, ensure_ascii=False)


def go(question, judge, layer, research=True, budget=None):
    return run(question, layer, judge, Settings("fake"), AS_OF, research=research, research_budget=budget)


def decision(state):
    return state["record"]["decisions"][0]


# ---------------------------------------------------------------- recovery and control


def test_recovery_the_added_card_is_judged_again_under_the_same_checks(layer):
    first = missing("전체 물가 흐름", ["총산출물가지수"], "2024년", "월별")
    judge = Script(first, choose("총산출물가지수", "전체 물가 흐름", "2024년", "월별"))
    state = go(CV22, judge, layer)
    r = state["research"]
    assert judge.calls == 2 and r["status"] == "rejudged" and r["end"] == "rejudged"
    assert r["search_words"] == ["총산출물가지수"] and r["extra_cap"] == EXTRA_CAP
    assert r["cards_after"] > r["cards_before"] and r["tables_after"] == r["tables_before"] + len(r["tables_added"])
    assert len(r["extra_hits"]["added"]) <= EXTRA_CAP and r["extra_hits"]["cut"] == r["extra_hits"]["found"][EXTRA_CAP:]
    assert r["payload_chars_after"] <= K.PAYLOAD_LIMIT and r["payload_chars_after"] > r["payload_chars_before"]
    # the first round is kept, and its cards are the head of the second round's cards, in the same order
    first_round = state["first_round"]
    assert [d["outcome"] for d in first_round["record"]["decisions"]] == ["not_in_pool"]
    assert state["cards"][:len(first_round["cards"])] == first_round["cards"]
    assert pool_tables(state["pool"])[:r["tables_before"]] == pool_tables(first_round["pool"])
    # the second answer is the model's own choice and is checked like any first answer
    d = decision(state)
    chosen = d["representative"]
    assert chosen in r["tables_added"] and d["model_kind"] == "choice"
    assert {"basis", "periods", "final_recheck"} <= set(d["checks_done"])
    assert d["outcome"] in ("selected", "selected_unverified") and d["final_tables"]
    assert judge.payloads[1] == K.build_payload(CV22, state["cards"], state["unresolved"])


def test_recovery_external_debt_card_is_added_after_the_question_cards(layer):
    first = missing("해외에 갚아야 할 빚의 규모", ["대외채무", "총대외채무", "외채 잔액"])
    state = go(Q093, Script(first, choose("대외채무", "해외에 갚아야 할 빚의 규모")), layer)
    r = state["research"]
    names = [c["name"] for c in state["cards"]]
    assert r["end"] == "rejudged" and "대외채무" in names[r["cards_before"]:]
    assert "대외채무" not in [c["name"] for c in state["first_round"]["cards"]]
    d = decision(state)
    assert d["representative"] in r["tables_added"] and d["model_kind"] == "choice"


def test_control_unrelated_candidates_never_force_a_selection(layer):
    first = missing("원/달러 환율", ["원/달러 환율", "원/달러 평균환율"], "2022년부터", "월평균")
    judge = Script(first, missing("원/달러 환율", ["원/달러 환율"], "2022년부터", "월평균"))
    state = go(Q082, judge, layer)
    r = state["research"]
    assert judge.calls == 2 and r["end"] == "still_missing" and r["second_round_outcomes"] == ["not_in_pool"]
    assert r["tables_added"] and not any("환율" in c["name"] for c in state["cards"][r["cards_before"]:])
    d = decision(state)
    assert d["outcome"] == "not_in_pool" and d["final_tables"] == [] and d["representative"] is None
    assert state["action"] == "fail"


# ---------------------------------------------------------------- recorded ends


def test_off_by_default_one_call_and_no_research_record(layer):
    judge = Script(missing("전체 물가 흐름", ["총산출물가지수"]))
    state = run(CV22, layer, judge, Settings("fake"), AS_OF)
    assert judge.calls == 1 and "research" not in state and research_summary(state) == {}
    assert decision(state)["outcome"] == "not_in_pool"


def test_a_decided_statistic_is_never_redone(layer):
    q = "2024년 1~3월 M2 총액과 원/달러 환율을 월별로 보여줘"
    out = blank()
    out["statistics"] = [{"id": "s1", "quote": "M2 총액", "name": "M2 총액"},
                         {"id": "s2", "quote": "원/달러 환율", "name": "원/달러 환율"}]
    out["missing"] = [{"for": ["s2"], "search_words": ["원/달러 환율"]}]
    cards = build_cards(q, build_pool(q, layer), layer)
    m2 = next(c for c in cards if c["name"] == "M2 상품별 구성내역")
    out["choices"] = [{"for": ["s1"], "table": f"{m2['card_id']}.{table_numbers(m2)[m2['tables'][0]['table_id']]}",
                       "evidence": [{"kind": "title", "text": m2["name"]}]}]
    judge = Script(out)
    state = go(q, judge, layer)
    assert judge.calls == 1 and state["research"]["end"] == "other_statistics_decided"
    assert state["research"]["status"] == "not_run" and "first_round" not in state


def test_nothing_missing_or_no_search_word_ends_without_a_call(layer):
    # the contract refuses an empty search word list; blank words are still never searched
    assert research_words({"decisions": [{"outcome": "not_in_pool", "search_words": ["  "]}]}) == \
        ([], "no_search_words")
    assert research_words({"decisions": [{"outcome": "selected"}]}) == ([], "no_missing_statistic")
    assert research_words({"decisions": [{"outcome": "not_in_pool", "search_words": ["가", "가 ", "나"]},
                                         {"outcome": "not_in_pool", "search_words": ["나", "다"]}]}) == \
        (["가", "나", "다"], None)
    judge = Script(JudgeError("timeout"))
    state = go(CV22, judge, layer)
    assert judge.calls == 1 and state["research"]["end"] == "first_round_error"


def test_no_new_table_ends_with_the_reason(layer):
    judge = Script(missing("전체 물가 흐름", ["zzqqxx"]))  # a word that hits no table
    state = go(CV22, judge, layer)
    r = state["research"]
    assert judge.calls == 1 and r["end"] == "no_new_candidates" and r["tables_added"] == []
    assert r["extra_hits"] == {"cap": EXTRA_CAP, "found": [], "added": [], "cut": []}


def test_hits_over_the_cap_are_recorded_as_cut(layer):
    judge = Script(missing("전체 물가 흐름", ["국내"]), missing("전체 물가 흐름", ["국내"]))
    state = go(CV22, judge, layer)
    hits = state["research"]["extra_hits"]
    assert len(hits["found"]) > EXTRA_CAP and hits["added"] == hits["found"][:EXTRA_CAP]
    assert hits["cut"] == hits["found"][EXTRA_CAP:] and judge.calls == 2
    first = {r["table_id"] for c in state["first_round"]["cards"] for r in c["tables"]}
    assert not set(hits["found"]) & first  # only tables the first cards did not show count toward the cap


def test_an_input_over_the_limit_ends_without_dropping_anything(layer, monkeypatch):
    first_size = len(K.canonical(K.build_payload(CV22, build_cards(CV22, build_pool(CV22, layer), layer))))
    monkeypatch.setattr(K, "PAYLOAD_LIMIT", first_size + 10)  # the first round fits; the re-searched input does not
    judge = Script(missing("전체 물가 흐름", ["총산출물가지수"]))
    state = go(CV22, judge, layer)
    r = state["research"]
    assert judge.calls == 1 and r["end"] == "input_too_large" and r["payload_limit"] == first_size + 10
    assert r["cards_after"] > r["cards_before"] and decision(state)["outcome"] == "not_in_pool"


def test_no_re_judge_budget_ends_before_a_second_call(layer):
    judge = Script(missing("전체 물가 흐름", ["총산출물가지수"]))
    asked = []
    state = go(CV22, judge, layer, budget=lambda: asked.append(1) or False)
    assert judge.calls == 1 and asked == [1] and state["research"]["end"] == "rejudge_call_limit"


@pytest.mark.parametrize("second", ["judge_error", "unreadable"])
def test_a_second_round_error_is_recorded_and_the_first_round_kept(layer, second):
    first = json.dumps(missing("전체 물가 흐름", ["총산출물가지수"]), ensure_ascii=False)
    calls = []

    def judge(payload, request):
        calls.append(1)
        if len(calls) == 1:
            return first
        if second == "judge_error":
            raise JudgeError("timeout")
        return "not json"

    state = go(CV22, judge, layer)
    assert len(calls) == 2 and state["research"]["end"] == "second_round_error" and state["action"] == "fail"
    assert [d["outcome"] for d in state["first_round"]["record"]["decisions"]] == ["not_in_pool"]


def test_the_research_summary_keeps_the_first_round(layer):
    judge = Script(missing("전체 물가 흐름", ["총산출물가지수"], "2024년", "월별"),
                   choose("총산출물가지수", "전체 물가 흐름", "2024년", "월별"))
    state = go(CV22, judge, layer)
    got = research_summary(state)
    assert got["research"]["end"] == "rejudged"
    assert got["first_round"]["outcomes"] == {"s1": "not_in_pool"} and got["first_round"]["route"] == "fail"
    assert got["first_round"]["model"]["missing"][0]["search_words"] == ["총산출물가지수"]
    assert got["first_round"]["pool"] == [r["table_id"] for c in state["first_round"]["cards"] for r in c["tables"]]


# ---------------------------------------------------------------- the re-judgment by another model (08:18 UTC)


def test_the_re_judgment_goes_to_its_own_judge_with_the_unresolved_statistics(layer):
    from claview_a.cards import refs

    first = Script(missing("전체 물가 흐름", ["총산출물가지수"], "2024년", "월별"))
    second = Script(choose("총산출물가지수", "전체 물가 흐름", "2024년", "월별"))
    state = run(CV22, layer, first, Settings("first-model"), AS_OF, research=True, research_judge=second,
                research_settings=Settings("second-model"))
    assert first.calls == 1 and second.calls == 1
    assert first.requests[0]["model"] == "first-model" and second.requests[0]["model"] == "second-model"
    assert "unresolved" not in first.payloads[0]
    assert second.payloads[0]["unresolved"] == [{"id": "s1", "quote": "전체 물가 흐름", "name": "전체 물가 흐름",
                                                 "search_words": ["총산출물가지수"], "splitters": [],
                                                 "periods": ["2024년"], "frequencies": ["월별"],
                                                 "calculations": []}]
    assert second.payloads[0] == K.build_payload(CV22, state["cards"], state["unresolved"])
    assert second.requests[0]["text"] == first.requests[0]["text"]  # the same output contract
    assert state["run_info"]["model"]["model"] == "first-model"
    assert state["run_info"]["research_model"]["model"] == "second-model"
    got = research_summary(state)
    r1, r2 = got["card_refs"]["round_1"], got["card_refs"]["round_2"]
    assert r1 == refs(state["first_round"]["cards"]) and r2 == refs(state["cards"])
    assert all(r2[k] == v for k, v in r1.items()) and len(r2) > len(r1)  # first-round refs keep their tables
    assert got["research"]["missing_read_as"] == "a re-search signal, not a confirmed absence"
    d = decision(state)
    assert d["outcome"] == "selected" and d["representative"] in state["research"]["tables_added"]


def test_the_other_model_missing_again_ends_with_no_third_judgment(layer):
    first = Script(missing("원/달러 환율", ["원/달러 환율", "원/달러 평균환율"], "2022년부터", "월평균"))
    second = Script(missing("원/달러 환율", ["원/달러 환율"], "2022년부터", "월평균"))
    state = run(Q082, layer, first, Settings("first-model"), AS_OF, research=True, research_judge=second,
                research_settings=Settings("second-model"))
    assert first.calls == 1 and second.calls == 1 and state["research"]["end"] == "still_missing"
    assert decision(state)["final_tables"] == [] and state["action"] == "fail"


def test_the_unresolved_input_counts_toward_the_input_limit(layer, monkeypatch):
    from claview_a.graph import unresolved_of

    first = Script(missing("전체 물가 흐름", ["총산출물가지수"], "2024년", "월별"))
    probe = run(CV22, layer, Script(missing("전체 물가 흐름", ["총산출물가지수"], "2024년", "월별")),
                Settings("fake"), AS_OF)
    pool = build_pool(CV22, layer, extra=["총산출물가지수"])
    cards = build_cards(CV22, pool, layer)
    without = len(K.canonical(K.build_payload(CV22, cards)))
    monkeypatch.setattr(K, "PAYLOAD_LIMIT", without)  # fits without the unresolved part, not with it
    second = Script(choose("총산출물가지수", "전체 물가 흐름"))
    state = run(CV22, layer, first, Settings("fake"), AS_OF, research=True, research_judge=second)
    assert second.calls == 0 and state["research"]["end"] == "input_too_large"
    assert unresolved_of(probe["record"])[0]["quote"] == "전체 물가 흐름"
