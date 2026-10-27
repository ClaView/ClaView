"""Completion-1 independent review: request date, scoped evidence, reference contracts and V1 roles."""
import copy

import pytest

from test_plan15_settle import layer, P, R, req, ind, bind, reading, proposal, bd
from test_plan15_step5 import run, setting, simple
from claview_a.agent.graph import run_outer
from claview_a.contract.check import check_analysis
from claview_a.select import cover, decision, evidence
from claview_a.select.candidates import build_queries
from claview_a.select.tables import selection_catalog


def test_no_request_date_is_injected_into_gpt_fallback(layer):
    q = "가상대출금 최근 3년"
    baseline = run_outer(q, "router", layer=layer)
    cfg = {"mode": "gpt", "proposal_source": lambda *args: "{"}
    got = run_outer(q, "router", layer=layer, decomposition=cfg)
    assert got["as_of"] is baseline["as_of"] is None
    assert got["selections"] == baseline["selections"]
    assert got["selection_route"] == baseline["selection_route"]
    accepted = {**cfg, "proposal_source": lambda payload, schema: proposal(reading(
        [req(indicator=ind(P(payload), "가상대출금"))], [bind(c["id"]) for c in payload["conditions"]]))}
    adopted = run_outer(q, "router", layer=layer, decomposition=accepted)
    assert adopted["analysis"]["decomposition"]["applied_source"] == "model"
    assert adopted["as_of"] is None
    cfg["as_of"] = "2026-10-20"
    assert run_outer(q, "router", layer=layer, decomposition=cfg)["as_of"] == "2026-10-20"


def test_missing_date_and_explicit_probe_date_are_different_ledger_settings(layer):
    from claview_a.agent.decomposition import CallLedger
    from claview_a.interpret.condition_fields import DEFAULT_AS_OF

    cfg, calls = setting()
    ledger = CallLedger()
    first = run_outer("가상대출금 보여줘", "router", layer=layer, decomposition=cfg,
                      decomposition_ledger=ledger, request_id="same")
    before = ledger.snapshot()
    other = run_outer("가상대출금 보여줘", "router", layer=layer, decomposition=cfg,
                      decomposition_ledger=ledger, request_id="same", as_of=DEFAULT_AS_OF)
    assert first["as_of"] is None and other["as_of"] == DEFAULT_AS_OF
    assert other["analysis"]["decomposition"]["fallback_reason"] == "ledger_conflict"
    assert len(calls) == 1 and ledger.snapshot() == before


def overlapping(layer):
    def build(p):
        return proposal(reading(
            [req("r1", ind(p, "가상대출금"), breakdowns=[bd(values=[R(p.s("가상서울"),
                                                                          z=p.z("가상서울", "target"))])]),
             req("r2", ind(p, "가상예금액"))],
            [bind(c["id"]) for c in p.payload["conditions"]], request=[R(p.s("보여줘"))]))
    cfg, _ = setting(build)
    return run(layer, cfg, "가상대출금 가상예금액 가상서울 보여줘")["analysis"]


def test_overlapping_refs_do_not_leak_into_evidence(layer):
    a = overlapping(layer)
    phrase = evidence.indicator_phrase(a, "r1", selection_catalog(layer).measure())
    assert phrase is not None and "가상예금액" not in phrase["text"]
    assert all("가상예금액" not in x for x in evidence._word_groups(a, "r1"))
    assert all("가상예금액" not in x for t in evidence.matched_terms(a, "r1") for x in t["texts"])
    lowers = evidence.named_lower_items(a, "r1")
    assert [x["name"] for x in lowers] == ["가상서울"]
    assert evidence.named_lower_items(a, "r2") == []


def test_both_rank_paths_use_own_title_words(layer, monkeypatch):
    a = overlapping(layer)
    catalog = selection_catalog(layer)
    # The other requirement's title stem is present in the question, but absent from r1's references.
    assert cover.TITLE_BONUS == cover.rank_score(
        {}, [], "가상대출금가상예금액가상서울", catalog.views["T_B"])
    assert cover.rank_score({}, [], cover.title_bonus_words(a, "r1"), catalog.views["T_B"]) == 0
    calls = []
    original = cover.rank_score

    def capture(candidate, checks, words, view):
        calls.append(words)
        return original(candidate, checks, words, view)

    monkeypatch.setattr(cover, "rank_score", capture)
    monkeypatch.setattr(decision, "rank_score", capture)
    from claview_a.select.graph import select_tables
    select_tables(a, layer, "2026-10-20")
    # Also exercise the observation path's newly restored candidate (no prior score).
    decision._scores(a, "r1", [{"table_id": "T_B"}], {}, {"T_B": catalog.views["T_B"]}, {})
    assert calls[-1] == cover.title_bonus_words(a, "r1")
    assert set(calls) <= {cover.title_bonus_words(a, rid) for rid in ("r1", "r2")}


@pytest.mark.parametrize("alternative", [False, True])
@pytest.mark.parametrize("damage", ["missing", "empty", "foreign"])
def test_primary_and_alternative_ref_contracts(layer, alternative, damage):
    def build(p):
        primary = reading([req("r1", ind(p, "가상대출금")), req("r2", ind(p, "가상예금액"))],
                          [bind(c["id"]) for c in p.payload["conditions"]], request=[R(p.s("보여줘"))])
        return proposal(primary, alternative=copy.deepcopy(primary))

    cfg, _ = setting(build)
    a = run(layer, cfg, "가상대출금 가상예금액 보여줘")["analysis"]
    record = a["decomposition"]["alternative"] if alternative else a["decomposition"]
    assert record is not None
    if damage == "missing":
        del record["selection_refs"]
    elif damage == "empty":
        record["selection_refs"]["r1"] = []
    else:
        ref = record["selection_refs"]["r1"][0]
        other = record["selection_refs"]["r2"][0]
        ref.update(name=other["name"], candidates=copy.deepcopy(other["candidates"]))
    assert any("selection_refs" in p for p in check_analysis(a))


def test_context_of_same_table_does_not_fire_sibling_alarm(layer):
    def build(p):
        return proposal(reading(
            [req(indicator=ind(p, "가상가계대출"),
                 context=R(p.s("가상대출금"), z=p.z("가상대출금", "table_name")))],
            [bind(c["id"]) for c in p.payload["conditions"]], request=[R(p.s("보여줘"))]))
    cfg, _ = setting(build)
    state = run(layer, cfg, "가상대출금 가상가계대출 보여줘")
    assert state["analysis"]["decomposition"]["applied_source"] == "model"
    signal = next(s for s in state["verification"]["interpretation"]["signals"]
                  if s["code"] == "same_table_siblings_merged")
    assert signal["status"] == "clear"


def test_breakdown_role_does_not_depend_on_draft_quote_spelling(layer):
    from claview_a.verify.checks import check_interpretation_state

    def build(p):
        return proposal(reading(
            [req(indicator=ind(p, "가상대출금"), breakdowns=[
                bd(values=[R(p.s("가상가계대출"), z=p.z("가상가계대출", "indicator"))])])],
            [bind(c["id"]) for c in p.payload["conditions"]], request=[R(p.s("보여줘"))]))

    cfg, _ = setting(build)
    state = run(layer, cfg, "가상대출금 가상가계대출 보여줘")
    a = copy.deepcopy(state["analysis"])
    assert a["decomposition"]["applied_source"] == "model"
    # V1 must consume the validated role, not the legacy draft's list of raw quote strings.
    a["judgement"]["requirement_draft"][0]["breakdown"] = ["별칭으로 쓴 하위 항목"]
    signal = next(s for s in check_interpretation_state({**state, "analysis": a})["signals"]
                  if s["code"] == "same_table_siblings_merged")
    assert signal["status"] == "clear"


def test_two_subquotes_in_one_segment_keep_separate_search_and_evidence(layer):
    q = "가상가계대출 보여줘"

    def build(p):
        sid = p.s("가상가계대출")
        return proposal(reading(
            [req("r1", R(sid, sq="가상가계")), req("r2", R(sid, sq="대출"))],
            [bind(c["id"], "name_content", names=[R(sid)]) for c in p.payload["conditions"]],
            request=[R(p.s("보여줘"))]))

    cfg, _ = setting(build)
    a = run(layer, cfg, q)["analysis"]
    assert a["decomposition"]["applied_source"] == "model"
    assert [r["indicator"]["quote"] for r in a["interpretation"]["requirements"]] == ["가상가계", "대출"]
    assert all("대출" not in x["text"] for x in build_queries(a, "r1"))
    assert all("가상가계" not in x["text"] for x in build_queries(a, "r2"))
    for rid, other in (("r1", "대출"), ("r2", "가상가계")):
        phrase = evidence.indicator_phrase(a, rid, selection_catalog(layer).measure())
        assert phrase is None or other not in phrase["text"]
        assert all(other not in x for x in evidence._word_groups(a, rid))
