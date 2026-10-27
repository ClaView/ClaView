"""Step 5: fake responses through the real outer graph, selection and V1/V2. Network guard is session-wide."""

import copy
import json

import pytest

from test_plan15_settle import P, R, bind, ind, layer, proposal, reading, req, split_reading, SPLIT_Q
from test_plan15_fix2 import layer as scoped_layer
from claview_a.agent import nodes, actions
from claview_a.agent.decomposition import CallLedger
from claview_a.agent.graph import run_outer
from claview_a.contract.check import check_analysis
from claview_a.interpret.pipeline import interpret_question
from claview_a.select.candidates import build_queries, term_tables
from claview_a.select.facts import requirement_identity
from claview_a.select.readings import reading_candidates
from claview_a.select.needs import all_needs
from claview_a.select.cover import question_words
from claview_a import _guard


QUESTION = "가상대출금 보여줘"


def simple(p):
    return proposal(reading([req(indicator=ind(p, "가상대출금"))],
                            [bind(c["id"]) for c in p.payload["conditions"]],
                            request=[R(p.s("보여줘"))]))


def setting(build=simple):
    calls = []

    def source(payload, schema):
        calls.append(payload)
        return build(P(payload))

    return {"mode": "gpt", "execution_mode": "fake", "proposal_source": source}, calls


def run(layer, cfg, question=QUESTION, **kw):
    state = run_outer(question, kw.pop("variant", "router"), layer=layer, decomposition=cfg,
                      request_id=kw.pop("request_id", "request"), as_of="2026-10-20", **kw)
    assert check_analysis(state["analysis"]) == []
    assert state["control"]["budget"]["llm_calls"] == 0
    assert _guard.NETWORK_ATTEMPTS == []
    json.dumps(state, ensure_ascii=False)  # the graph state, including its local ledger, is serialisable
    return state


@pytest.mark.parametrize("variant", ["straight", "parts", "central", "router"])
def test_full_graph_uses_model_structure_and_checks(layer, variant):
    cfg, calls = setting()
    state = run(layer, cfg, variant=variant)
    assert len(calls) == 1
    assert state["analysis"]["decomposition"]["applied_source"] == "model"
    assert state["a_outcome"]["parts"]["select"]["status"] == "done"
    assert state["verification"]["interpretation"]["check"] == "V1"
    assert state["verification"]["selection"]["check"] == "V2"
    assert next(x for x in state["verification"]["interpretation"]["signals"]
                if x["code"] == "decomposition_contract")["status"] == "clear"
    assert state["response"]["decomposition"]["fake_calls"] == 1
    assert state["response"]["decomposition"]["model_calls"] == 0
    assert state["jev_record"]["mode"] == "blocked_by_decomposition"
    assert state["analysis"]["decomposition"]["as_of"] == state["as_of"]


def test_rules_do_not_use_provider_or_ledger(layer):
    cfg, calls = setting()
    cfg["mode"] = "rules"
    default = run_outer(QUESTION, "router", layer=layer, as_of="2026-10-20")
    explicit = run(layer, cfg)
    assert not calls and not explicit["decomposition_runs"]
    assert {k: v for k, v in explicit["analysis"].items() if k != "decomposition"} == default["analysis"]
    assert explicit["selections"] == default["selections"]


@pytest.mark.parametrize("question", [QUESTION, "가상대출금 최근 3개월"])
def test_explicit_rules_never_inject_a_selection_reference_date(layer, question):
    default = run_outer(question, "router", layer=layer)
    explicit = run_outer(question, "router", layer=layer, decomposition={"mode": "rules"})
    assert default["as_of"] is None and explicit["as_of"] is None
    assert explicit["selections"] == default["selections"]
    assert explicit["selection_route"] == default["selection_route"]
    assert {k: v for k, v in explicit["analysis"].items() if k != "decomposition"} == default["analysis"]


@pytest.mark.parametrize("how,reason", [("json", "format"), ("meaning", "semantic"),
                                       ("exception", "transport"), ("timeout", "transport"), ("empty", "transport")])
def test_failures_fall_back_whole_and_are_cached(layer, how, reason):
    def build(p):
        if how == "exception":
            raise RuntimeError("private message must not be logged")
        if how == "timeout":
            raise TimeoutError("private message must not be logged")
        if how == "empty":
            return None
        if how == "json":
            return "{"
        out = simple(p)
        out["primary"]["request_refs"].append(out["primary"]["requirements"].pop()["indicator"])
        out["primary"]["requirements"] = [req()]  # swallows the catalog indicator
        return out

    cfg, calls = setting(build)
    ledger = CallLedger()
    first = run(layer, cfg, decomposition_ledger=ledger)
    second = run(layer, cfg, decomposition_ledger=ledger)
    assert len(calls) == 1
    assert first["analysis"]["decomposition"]["fallback_reason"] == reason
    rules = interpret_question(QUESTION, layer)
    assert {k: v for k, v in first["analysis"].items() if k != "decomposition"} == rules
    assert second["analysis"] == first["analysis"]
    assert second["response"]["decomposition"]["fake_calls"] == 0
    assert "private message" not in json.dumps(ledger.snapshot())


def test_reentry_and_existing_jev_cannot_send_again(layer, monkeypatch):
    cfg, calls = setting()
    ledger = CallLedger()
    monkeypatch.setattr(nodes.jev_mod, "record_jev", lambda *a, **kw: pytest.fail("JEV called"))
    original = nodes.route

    def retry(view):
        if view.stage == "verify_select" and view.used("reinterpret") == 0:
            return actions.reinterpret()
        return original(view)

    monkeypatch.setattr(nodes, "route", retry)
    state = run(layer, cfg, decomposition_ledger=ledger, jev={"mode": "live"})
    assert len(calls) == 1
    assert state["control"]["budget"]["reinterpret"] == 1
    assert [x["visit"] for x in state["decomposition_runs"]] == ["owner", "reused"]


def test_shared_ledger_isolation_and_default_ledger_boundary(layer):
    cfg, calls = setting()
    ledger = CallLedger()
    first = run(layer, cfg, decomposition_ledger=ledger)
    second = run(layer, cfg, decomposition_ledger=ledger)
    assert len(calls) == 1 and first["analysis"] == second["analysis"]
    second["analysis"]["judgement"]["certain"] = "mutated"
    again = run(layer, cfg, decomposition_ledger=ledger)
    assert again["analysis"] == first["analysis"]
    run(layer, cfg, decomposition_ledger=ledger, question_version=2)
    run(layer, cfg, decomposition_ledger=ledger, request_id="other")
    assert len(calls) == 3
    run(layer, cfg)
    run(layer, cfg)
    assert len(calls) == 5  # separate default ledgers are intentionally independent


@pytest.mark.parametrize("change", ["question", "provider", "date"])
def test_conflicts_do_not_overwrite_or_reuse_old_results(layer, change):
    cfg, calls = setting()
    ledger = CallLedger()
    run(layer, cfg, decomposition_ledger=ledger)
    before = ledger.snapshot()
    other = dict(cfg)
    q = "가상예금액 보여줘" if change == "question" else QUESTION
    if change == "provider":
        other["proposal_source"] = lambda *a: pytest.fail("conflict called")
    if change == "date":
        state = run_outer(q, "router", layer=layer, decomposition=other, decomposition_ledger=ledger,
                          request_id="request", as_of="2025-01-01")
    else:
        state = run(layer, other, q, decomposition_ledger=ledger)
    assert state["analysis"]["decomposition"]["fallback_reason"] == "ledger_conflict"
    assert state["analysis"]["text"]["raw"] == q
    assert state["analysis"]["judgement"] == interpret_question(q, layer)["judgement"]
    assert ledger.snapshot() == before and len(calls) == 1


def test_in_progress_does_not_destroy_owners_completion(layer):
    ledger = CallLedger()
    nested = []

    def build(p):
        nested.append(run(layer, cfg, decomposition_ledger=ledger))
        return simple(p)

    cfg, calls = setting(build)
    state = run(layer, cfg, decomposition_ledger=ledger)
    assert len(calls) == 1
    assert nested[0]["analysis"]["decomposition"]["fallback_reason"] == "ledger_in_progress"
    assert state["analysis"]["decomposition"]["applied_source"] == "model"
    assert next(iter(ledger.entries.values()))["status"] == "completed"
    assert run(layer, cfg, decomposition_ledger=ledger)["analysis"] == state["analysis"]


def test_model_references_keep_queries_and_table_codes_with_their_requirement(layer):
    question = "가상대출금 가상예금액 가상서울 보여줘"

    def build(p):
        return proposal(reading([req("r1", ind(p, "가상대출금"),
                                     [R(p.s("가상서울"), z=p.z("가상서울", "target"))]),
                                 req("r2", ind(p, "가상예금액"))],
                                [bind(c["id"]) for c in p.payload["conditions"]],
                                request=[R(p.s("보여줘"))]))

    cfg, calls = setting(build)
    state = run(layer, cfg, question)
    a = state["analysis"]
    assert a["decomposition"]["applied_source"] == "model"
    assert a["interpretation"]["requirements"][0]["span"][1] > a["interpretation"]["requirements"][1]["span"][0]
    assert all("가상예금액" not in q["text"] for q in build_queries(a, "r1"))
    assert "가상예금액" not in question_words(a, "r1")["text"]
    assert term_tables(a, "r1")[0] == ["T_A"]
    rows = a["decomposition"]["selection_refs"]["r1"]
    target = next(r for r in rows if r["place"] == "target")
    assert {c["table_id"] for c in target["candidates"]} == {"T_A", "T_B"}
    assert {c["value_id"] for c in target["candidates"]} == {"ra1", "rb1"}
    assert requirement_identity(a, "r1")["key"] != requirement_identity(a, "r2")["key"]
    assert [s["requirement_id"] for s in state["selections"]] == ["r1", "r2"]
    assert "T_A" in state["evidence"]["pool"]["r1"]
    assert a["judgement"]["certain"] is False


@pytest.mark.parametrize("bad", [False, True])
def test_only_preserved_model_alternative_is_used(layer, monkeypatch, bad):
    def build(p):
        out = simple(p)
        alt = copy.deepcopy(out["primary"])
        alt["requirements"][0]["indicator"]["sense_id"] = p.z("가상대출금", "table_name")
        if bad:
            alt["requirements"][0]["indicator"]["segment_ids"] = ["unknown"]
        out["alternative"] = alt
        return out

    cfg, calls = setting(build)
    from claview_a.interpret import model_step
    monkeypatch.setattr(model_step, "prepare", lambda *a, **kw: pytest.fail("old 2.6 reading called"))
    state = run(layer, cfg)
    alternatives = reading_candidates(state["analysis"], layer)
    assert len(alternatives) == (0 if bad else 1)
    for alt in alternatives:
        assert alt["analysis"]["decomposition"]["reading_id"] == "alternative"
        assert check_analysis(alt["analysis"]) == []
        assert reading_candidates(alt["analysis"], layer) == []
    assert len(calls) == 1


def test_split_structure_reaches_selection(layer):
    cfg, _ = setting(lambda p: proposal(split_reading(p)))
    state = run(layer, cfg, SPLIT_Q)
    assert len(state["analysis"]["interpretation"]["requirements"]) == 1
    assert state["analysis"]["decomposition"]["condition_fields"]


def test_inferred_hint_never_becomes_a_condition_or_changes_cover(layer):
    def build(p):
        return proposal(reading([req(indicator=ind(p, "가상대출금"))],
                                [bind(c["id"]) for c in p.payload["conditions"]]))

    cfg, _ = setting(build)
    state = run(layer, cfg, "가상대출금 최근 3개월")
    a = state["analysis"]
    assert a["decomposition"]["inferred"]["r1"]
    assert not [c for c in a["conditions"] if c["kind"] == "frequency"]
    assert not [c for c in all_needs(a)["r1"]["set"] if c["kind"] == "frequency"]
    without = copy.deepcopy(a)
    without["decomposition"]["inferred"]["r1"] = []
    from claview_a.select.graph import select_tables
    assert select_tables(a, layer, state["as_of"]) == select_tables(without, layer, state["as_of"])


def test_record_only_uncertainty_through_outer_graph(layer):
    def build(p):
        return proposal(reading([req(indicator=ind(p, "가상대출금"))],
                                request=[R(p.s("대해")), R(p.s("알려줘"))]))

    cfg, _ = setting(build)
    a = run(layer, cfg, "가상대출금에 대해 알려줘")["analysis"]
    assert a["decomposition"]["certainty"]["reason"] == "rules_uncertain_only"
    assert a["judgement"]["certain"] is False and not a["judgement"]["failed_sources"]
    assert all_needs(a)["r1"]["certainty"]["all"] is False


def test_input_limit_has_no_provider_attempt_and_is_reused(layer, monkeypatch):
    from claview_a.interpret import decompose_payload
    monkeypatch.setitem(decompose_payload.LIMITS, "segments", 0)
    cfg, calls = setting()
    ledger = CallLedger()
    a = run(layer, cfg, decomposition_ledger=ledger)
    b = run(layer, cfg, decomposition_ledger=ledger)
    assert not calls
    assert a["analysis"]["decomposition"]["fallback_reason"] == "input_limit"
    assert b["decomposition_runs"][0]["visit"] == "reused"


def test_forced_legacy_retry_model_is_blocked(layer, monkeypatch):
    cfg, calls = setting()
    original = nodes.route

    def retry(view):
        if view.stage == "verify_interpret" and view.used("retry_model") == 0:
            return actions.retry_model()
        return original(view)

    monkeypatch.setattr(nodes, "route", retry)
    state = run(layer, cfg)
    assert len(calls) == 1
    assert state["control"]["budget"]["retry_model"] == 0
    assert len(state["decomposition_runs"]) == 1


def test_contract_rejects_forged_selection_references(layer):
    cfg, _ = setting()
    a = run(layer, cfg)["analysis"]
    bad = copy.deepcopy(a)
    bad["decomposition"]["selection_refs"]["r1"][0]["candidates"][0]["value_id"] = "forged"
    assert any("raw mention candidate" in x for x in check_analysis(bad))
    bad = copy.deepcopy(a)
    bad["decomposition"]["selection_refs"]["r1"][0]["span"] = [0, 1]
    assert any("quote or bounds" in x for x in check_analysis(bad))


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_unsupported_execution_fails_before_claim(layer, mode):
    cfg, calls = setting()
    cfg["execution_mode"] = mode
    ledger = CallLedger()
    with pytest.raises(ValueError):
        run(layer, cfg, decomposition_ledger=ledger)
    assert not calls and not ledger.entries


def test_role_failure_stays_with_its_requirement_through_outer(scoped_layer):
    question = "가상기준값 가상대출금과 가상예금액"

    def build(p):
        return proposal(reading(
            [req("r1", ind(p, "가상대출금"), [R(p.s("가상기준값"), z=p.z("가상기준값", "basis"))]),
             req("r2", ind(p, "가상예금액"))],
            [bind(c["id"], "requirements", ["r1" if c["quote"] == "가상대출금" else "r2"])
             for c in p.payload["conditions"]]))

    cfg, _ = setting(build)
    state = run(scoped_layer, cfg, question)
    a = state["analysis"]
    assert a["decomposition"]["applied_source"] == "model"
    row = next(x for x in a["judgement"]["failed_sources"] if x["detail"] == "role_mismatch:target")
    assert row["requirement_ids"] == ["r1"]
    needs = all_needs(a)
    assert needs["r1"]["evidence"]["targets"][0]["settled"] is False
    assert needs["r2"]["certainty"]["sources"] == []
    assert needs["r2"]["evidence"]["indicator"]["settled"] is True
    assert state["verification"]["selection"]["check"] == "V2"


def test_condition_failure_stays_with_its_condition_through_outer(scoped_layer):
    from test_plan15_fix2 import P as ScopedP, TWO_Q, two_reading

    def build(p):
        return proposal(two_reading(ScopedP(p.payload)), ambiguities=[
            {"refs": [R(p.s("분기별"))], "kind": "condition", "condition_ids": [p.c("frequency")],
             "reason_code": "condition_unattached"}])

    cfg, _ = setting(build)
    a = run(scoped_layer, cfg, TWO_Q)["analysis"]
    needs = all_needs(a)
    freq = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")
    assert needs["r2"]["certainty"]["condition_ids"] == [freq]
    assert needs["r2"]["certainty"]["all"] is False
    assert needs["r1"]["certainty"]["sources"] == []
    from claview_a.select.evidence import matched_terms, scope
    for rid in ("r1", "r2"):
        own, ambiguous = scope(a, rid)
        assert all(t["condition_id"] is None or t["condition_id"] in own | ambiguous
                   for t in matched_terms(a, rid))


def test_alternative_keeps_its_own_condition_bindings(scoped_layer):
    from test_plan15_fix2 import P as ScopedP, TWO_Q, two_reading
    from claview_a.select.evidence import scope

    def build(p):
        p = ScopedP(p.payload)
        return proposal(two_reading(p, "requirements", ["r2"]),
                        alternative=two_reading(p, "requirements", ["r1"]))

    cfg, _ = setting(build)
    state = run(scoped_layer, cfg, TWO_Q)
    a = state["analysis"]
    (other,) = reading_candidates(a, scoped_layer)
    alt = other["analysis"]
    assert check_analysis(alt) == []
    freq = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")
    assert freq in scope(a, "r2")[0] and freq not in scope(a, "r1")[0]
    assert freq in scope(alt, "r1")[0] and freq not in scope(alt, "r2")[0]
    assert alt["decomposition"]["bindings"] != a["decomposition"]["bindings"]
    assert all(r["reading_id"] == "alternative" for r in alt["interpretation"]["requirements"])
    assert alt["decomposition"]["condition_fields"] is not a["decomposition"]["condition_fields"]


def test_one_indicator_preserves_multiple_raw_table_candidates(tmp_path):
    from catalog_synth import write_inputs, write_raw
    from claview_a.catalog.layer import load_layer
    from test_plan15_settle import TABLES

    tables = copy.deepcopy(TABLES)
    tables.append(("T_C", tables[0][1], tables[0][2], tables[0][3]))
    search, meta = write_raw(tmp_path / "raw", tables)
    inputs = write_inputs(tmp_path / "inputs", overrides=[])
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf8")
    catalog = load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                         synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)
    cfg, _ = setting()
    state = run(catalog, cfg)
    a = state["analysis"]
    assert len(a["interpretation"]["requirements"]) == 1
    assert set(term_tables(a, "r1")[0]) == {"T_A", "T_C"}
    assert {"T_A", "T_C"} <= set(state["evidence"]["pool"]["r1"])


def test_measurement_keeps_fake_and_reused_calls_separate(layer):
    cfg, calls = setting()
    ledger = CallLedger()
    first = run(layer, cfg, decomposition_ledger=ledger)["response"]["decomposition"]
    reused = run(layer, cfg, decomposition_ledger=ledger)["response"]["decomposition"]
    entry = next(iter(ledger.snapshot().values()))
    assert first["fake_calls"] == entry["fake_calls"] == len(calls) == 1
    assert first["model_calls"] == entry["model_calls"] == 0
    assert first["usage"] is None
    assert first["latency_ms"] == entry["latency_ms"] and first["latency_ms"] >= 0
    assert reused["fake_calls"] == reused["model_calls"] == 0
    assert reused["latency_ms"] is None
