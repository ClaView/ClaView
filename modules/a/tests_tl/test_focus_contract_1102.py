"""A-authored scope provenance, older answer compatibility, and recovery ownership (no model or network)."""

from __future__ import annotations

import copy
import json

import pytest

from claview_a import check as CH
from claview_a import contract as K
from claview_a import graph as G
from claview_a.cards import build_cards, refs
from claview_a.focus import MAX_FOCUS_CHARS
from claview_a.pool import build_pool
from claview_a.search.tables import selection_catalog

QUESTION = "2024년 월별 예금은행 가계대출 금리를 신규취급액 기준으로 보여줘"
QUOTE = "예금은행 가계대출 금리"
SCOPE = "예금은행 가계대출 금리(신규취급액 기준)"
OLD = "tl-contract-2026-10-28.6"
# Historical .7 fixtures (quote-only period rows): the focus checks stay independent of contract .8's period rows
FOCUS7 = "tl-contract-2026-10-28.7"


@pytest.fixture(scope="module")
def case(layer):
    cards = build_cards(QUESTION, build_pool(QUESTION, layer), layer)
    table = "DT_121Y006"
    assert table in refs(cards).values()
    card = next(c for c in cards if any(t["table_id"] == table for t in c["tables"]))
    return cards, table, card["name"]


def output(case, *, sid="s1"):
    _, table, title = case
    out = {key: [] for key in K.output_schema()["properties"]}
    out.update(
        statistics=[{"id": sid, "quote": QUOTE, "name": SCOPE}],
        choices=[{"for": [sid], "table": table, "evidence": [{"kind": "title", "text": title}]}],
        periods=[{"for": [sid], "quote": "2024년", "meaning": "range", "refers_to": []}],
        frequencies=[{"for": [sid], "quote": "월별"}],
        splitters=[{"for": [sid], "quote": "신규취급액 기준", "axis": "loan_basis", "value": "신규취급액",
                    "polarity": "required"}],
        focuses=[{"for": [sid], "text": SCOPE, "quotes": [QUOTE, "신규취급액 기준"]}],
    )
    return out


def parsed(case, out, contract=FOCUS7):
    return K.parse_output(json.dumps(out, ensure_ascii=False), case[0], contract)


def checked(case, layer, out, contract=FOCUS7):
    return CH.check(QUESTION, case[0], parsed(case, out, contract), selection_catalog(layer).views, "2026-10-29")


def test_new_scope_is_a_summary_with_separate_original_sources(case, layer):
    out = output(case)
    record = checked(case, layer, out)
    d = record["decisions"][0]
    assert d["outcome"] == "selected"
    assert d["quote"] == QUOTE and d["name"] == SCOPE
    assert SCOPE not in QUESTION  # source excerpts, not the summary, must be exact original text
    assert d["focus"] == {"text": SCOPE, "quotes": [QUOTE, "신규취급액 기준"],
                          "source": "model_scope", "status": "grounded"}
    assert record["model_output"] == out


def test_previous_contract_keeps_its_changes_and_does_not_gain_scope(case, layer):
    assert "changes" in K.output_schema(OLD)["properties"]
    assert "focuses" not in K.output_schema(OLD)["properties"]
    out = output(case)
    out.pop("focuses")
    p = parsed(case, out, OLD)
    assert p["answer_contract"] == OLD and "focuses" not in p["output"]
    d = checked(case, layer, out, OLD)["decisions"][0]
    assert d["outcome"] == "selected"
    assert d["focus"] == {"text": QUOTE, "quotes": [QUOTE], "source": "legacy_quote",
                          "status": "verified_quote_only"}
    with pytest.raises(K.ContractError, match="schema"):
        parsed(case, out)  # a .6 answer is never silently read as a .7 answer


@pytest.mark.parametrize("contract,changes", [("tl-contract-2026-10-28.4", False),
                                               ("tl-contract-2026-10-28.5", False), (OLD, True)])
def test_older_schema_fields_are_unchanged(contract, changes):
    props = K.output_schema(contract)["properties"]
    assert ("changes" in props) == changes and "focuses" not in props


@pytest.mark.parametrize("change,code", [
    (lambda out: out.update(focuses=[]), "focus_missing"),
    (lambda out: out["focuses"].append(copy.deepcopy(out["focuses"][0])), "focus_repeated"),
    (lambda out: out["focuses"][0].update(quotes=[]), "empty_focus_quotes"),
    (lambda out: out["focuses"][0].update(**{"for": []}), "empty_for"),
    (lambda out: out["focuses"][0].update(**{"for": ["s99"]}), "statistic_ref"),
    (lambda out: out["focuses"][0].update(text=" "), "empty_string"),
])
def test_scope_structure_cannot_lose_or_misassign_a_statistic(case, change, code):
    out = output(case)
    change(out)
    with pytest.raises(K.ContractError) as exc:
        parsed(case, out)
    assert exc.value.code == code


def test_one_scope_row_cannot_cover_multiple_statistics(case):
    out = output(case)
    out["statistics"].append({"id": "s2", "quote": QUOTE, "name": SCOPE})
    out["choices"][0]["for"].append("s2")
    out["focuses"][0]["for"].append("s2")
    with pytest.raises(K.ContractError) as exc:
        parsed(case, out)
    assert exc.value.code == "focus_statistic_count"


@pytest.mark.parametrize("bad_quote", ["예금은행 금리", "가계대출·금리", "기업대출 금리"])
def test_scope_sources_must_be_original_excerpts_apart_from_whitespace(case, layer, bad_quote):
    out = output(case)
    out["focuses"][0]["quotes"].append(bad_quote)
    d = checked(case, layer, out)["decisions"][0]
    assert d["outcome"] == "failed" and d["focus"]["status"] == "invalid"
    assert "focus_quote_not_in_question" in {r["code"] for r in d["reasons"]}
    assert d["focus"]["quotes"][-1] == bad_quote  # evidence is recorded, not repaired or silently dropped


@pytest.mark.parametrize("length,accepted", [(MAX_FOCUS_CHARS, True), (MAX_FOCUS_CHARS + 1, False)])
def test_focus_length_is_checked_without_truncation(case, layer, length, accepted):
    out = output(case)
    out["focuses"][0]["text"] = "가" * length
    d = checked(case, layer, out)["decisions"][0]
    assert (d["outcome"] == "selected") == accepted
    assert len(d["focus"]["text"]) == length
    assert ("focus_too_long" in {r["code"] for r in d["reasons"]}) == (not accepted)


def test_a_valid_focus_cannot_mask_an_invalid_statistic_quote(case, layer):
    out = output(case)
    out["statistics"][0]["quote"] = "없는 지표"
    d = checked(case, layer, out)["decisions"][0]
    assert d["focus"]["status"] == "grounded" and d["outcome"] == "failed"
    assert "quote_not_in_question" in {r["code"] for r in d["reasons"]}


def test_invalid_legacy_quote_is_not_reported_as_verified_provenance(case, layer):
    out = output(case)
    out.pop("focuses")
    out["statistics"][0]["quote"] = "없는 지표"
    d = checked(case, layer, out, OLD)["decisions"][0]
    assert d["outcome"] == "failed" and d["focus"]["status"] == "invalid"


def test_recovery_adopts_focus_with_renumbered_target_and_preserves_other_statistic(case, layer):
    first_output = output(case)
    first_output["statistics"].append({"id": "s2", "quote": "가계대출 금리", "name": "가계대출 금리"})
    for key in ("choices", "periods", "frequencies", "splitters"):
        first_output[key][0]["for"].append("s2")
    first_output["focuses"].append({"for": ["s2"], "text": "가계대출 금리", "quotes": ["가계대출 금리"]})
    first_output["focuses"][0]["quotes"] = ["잘못 옮긴 인용"]
    first = checked(case, layer, first_output)
    second = checked(case, layer, output(case, sid="s9"))
    before = copy.deepcopy(first)
    merged = G.merge_rounds(first, second, [{"statistic": "s1", "kind": "correction",
                                           "code": "focus_quote_not_in_question"}])
    assert first == before
    assert all(d["outcome"] == "selected" for d in merged["decisions"])
    assert merged["decisions"][1] == first["decisions"][1]
    scopes = {r["for"][0]: r for r in merged["model_output"]["focuses"]}
    assert scopes["s1"] == {"for": ["s1"], "text": SCOPE, "quotes": [QUOTE, "신규취급액 기준"]}
    assert scopes["s2"] == first_output["focuses"][1]
    assert merged["decisions"][0]["focus"]["text"] == SCOPE


class ScopeJudge:
    answer_contract = FOCUS7

    def __init__(self, *responses):
        self.responses = responses
        self.payloads = []

    def __call__(self, payload, request):
        self.payloads.append(copy.deepcopy(payload))
        return json.dumps(self.responses[len(self.payloads) - 1], ensure_ascii=False)


def test_invalid_scope_uses_one_recovery_and_keeps_the_fixed_statistic(case, layer):
    first = output(case)
    first["statistics"].append({"id": "s2", "quote": "가계대출 금리", "name": "가계대출 금리"})
    for key in ("choices", "periods", "frequencies", "splitters"):
        first[key][0]["for"].append("s2")
    first["focuses"].append({"for": ["s2"], "text": "가계대출 금리", "quotes": ["가계대출 금리"]})
    first["focuses"][0]["quotes"] = ["잘못 옮긴 인용"]
    judge = ScopeJudge(first, output(case, sid="s9"))
    state = G.run(QUESTION, layer, judge, K.Settings("fake"), "2026-10-29", research=True)
    assert len(judge.payloads) == 2
    target = judge.payloads[1]["targets"][0]
    assert target["id"] == "s1" and target["focus"]["quotes"] == ["잘못 옮긴 인용"]
    assert judge.payloads[1]["fixed"]["decided"] == [{"id": "s2", "quote": "가계대출 금리"}]
    record = state["record"]
    assert record["route"]["action"] == "handoff"
    assert record["decisions"][1] == state["first_round"]["record"]["decisions"][1]
    assert record["decisions"][0]["focus"]["status"] == "grounded"
    assert record["rejudge"]["merged"] == [{"statistic": "s1", "second_round_statistic": "s9",
                                           "outcome": "selected"}]


@pytest.mark.parametrize("budget,expected_calls", [(True, 2), (False, 1)])
def test_scope_recovery_cannot_retry_twice_or_bypass_the_budget(case, layer, budget, expected_calls):
    bad = output(case)
    bad["focuses"][0]["text"] = "가" * (MAX_FOCUS_CHARS + 1)
    judge = ScopeJudge(bad, copy.deepcopy(bad))
    state = G.run(QUESTION, layer, judge, K.Settings("fake"), "2026-10-29", research=True,
                  research_budget=lambda: budget)
    assert len(judge.payloads) == expected_calls
    assert state["record"]["decisions"][0]["outcome"] == "failed"
    assert state["record"]["decisions"][0]["focus"]["status"] == "invalid"
