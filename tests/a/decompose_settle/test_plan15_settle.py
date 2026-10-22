"""Plan 15 r3 step 4: requirement settling, condition fields and hints of the decomposition (7, 8, appendix A 1-3).

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposals handed in as
the proposal source (no network, no model). IDs are found by their words, never fixed.
"""

from __future__ import annotations

import copy
import json

import pytest
from catalog_synth import write_inputs, write_raw

from claview.a.catalog.layer import load_layer
from claview.a.contract.check import check_analysis, check_decomposition
from claview.a.interpret import decompose_payload as DP
from claview.a.interpret.condition_fields import condition_fields, inferred_hints, period_diagnosis
from claview.a.interpret.decompose_run import DecompositionConfig
from claview.a.interpret.decompose_settle import compare_structures
from claview.a.interpret.pipeline import interpret_question
from claview.a.select.facts import requirement_identity
from claview.a.select.needs import all_needs, unsettled

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None),
                                    ("ra2", "가상부산", None, "r0", None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "가상가계대출", None, None, None)]),
    ]),
    ("T_B", "가상예금(말잔)", "Synthetic Deposits", [
        ("REG", "지역코드별", "REGION", [("rb1", "가상서울", None, None, None)]),
        ("ACC", "계정항목별", "BSI", [("ab1", "가상예금액", None, None, None)]),
    ]),
]
SPLIT_Q = "가상대출금을 가상서울·가상부산으로 나눠서 분기별로 보여줘"
RATIO_Q = "가상대출금 대비 가상예금액 비율"
TWO_Q = "가상대출금과 가상예금액 분기별로 보여줘"
WORD_Q = "가상대출금 모르는말 보여줘"


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_settle")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class P:
    """The payload the proposal source is given, with helpers that find IDs by their words."""

    def __init__(self, payload: dict):
        self.payload = payload

    def s(self, text: str, nth: int = 0) -> str:
        return [x["id"] for x in self.payload["segments"] if x["text"] == text][nth]

    def z(self, name: str, role: str) -> str:
        (z,) = {r["sense_id"] for r in self.payload["candidate_summary"] if r["name"] == name and r["role"] == role}
        return z

    def c(self, kind: str, nth: int = 0) -> str:
        return [x["id"] for x in self.payload["conditions"] if x["kind"] == kind][nth]


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None, targets=(), basis=(), context=None, breakdowns=()) -> dict:
    return {"id": rid, "indicator": indicator, "targets": list(targets), "basis_refs": list(basis),
            "context": context, "breakdowns": list(breakdowns)}


def bd(values=(), markers=(), kind="values", axis=None) -> dict:
    return {"kind": kind, "axis": axis, "values": list(values), "marker_refs": list(markers)}


def bind(cid, scope="question", rids=(), names=()) -> dict:
    return {"condition_id": cid, "scope": scope, "requirement_ids": list(rids), "name_refs": list(names)}


def reading(reqs, bindings=(), relations=(), links=(), request=(), unused=()) -> dict:
    return {"requirements": list(reqs), "condition_bindings": list(bindings), "relations": list(relations),
            "polarity_links": list(links), "request_refs": list(request), "unused_refs": list(unused)}


def proposal(primary, alternative=None, ambiguities=()) -> dict:
    return {"primary": primary, "alternative": alternative, "ambiguities": list(ambiguities)}


def run(question: str, layer, build, as_json: bool = False) -> dict:
    """The gpt mode with a fake proposal source (called exactly once); the result passes the contract check."""
    calls: list[dict] = []

    def source(payload, schema):
        calls.append(payload)
        out = build(P(payload))
        return json.dumps(out, ensure_ascii=False) if as_json else out

    a = interpret_question(question, layer, decomposition={"mode": "gpt", "proposal_source": source})
    assert len(calls) == 1
    assert check_analysis(a) == []
    assert json.loads(json.dumps(a, ensure_ascii=False)) == a  # serialisable as it is
    return a


def without_record(a: dict) -> dict:
    return {k: v for k, v in a.items() if k != "decomposition"}


def ind(p: P, name: str, role: str = "indicator") -> dict:
    return R(p.s(name), z=p.z(name, role))


def split_reading(p: P) -> dict:
    """5.3 on the synthetic catalog: one requirement broken down by two values."""
    return reading(
        [req(indicator=ind(p, "가상대출금"), breakdowns=[bd(
            [R(p.s("가상서울"), z=p.z("가상서울", "target")), R(p.s("가상부산"), z=p.z("가상부산", "target"))],
            [R(p.s("·")), R(p.s("으로 나눠서"))])])],
        [bind(p.c("frequency")), bind(p.c("function"), "requirements", ["r1"]),
         bind(p.c("breakdown", 0), "requirements", ["r1"]), bind(p.c("breakdown", 1), "requirements", ["r1"])],
        request=[R(p.s("보여줘"))])


def ratio_reading(p: P) -> dict:
    """The rule structure of RATIO_Q: two requirements, both relation words over r1 and r2."""
    l1, l2 = p.c("relation", 0), p.c("relation", 1)
    return reading([req("r1", ind(p, "가상대출금")), req("r2", ind(p, "가상예금액"))],
                   [bind(l1, "requirements", ["r1", "r2"]), bind(l2, "requirements", ["r1", "r2"])],
                   [{"condition_id": l1, "requirement_ids": ["r1", "r2"]},
                    {"condition_id": l2, "requirement_ids": ["r1", "r2"]}])


def two_reading(p: P, freq: dict) -> dict:
    return reading([req("r1", ind(p, "가상대출금")), req("r2", ind(p, "가상예금액"))],
                   [freq, bind(p.c("breakdown", 0), "requirements", ["r1"]),
                    bind(p.c("breakdown", 1), "requirements", ["r2"])],
                   request=[R(p.s("보여줘"))])


# ---------------------------------------------------------------- default and rules mode


@pytest.mark.parametrize("question", [SPLIT_Q, RATIO_Q, TWO_Q, WORD_Q, "가상대출금 최근 3개월"])
def test_the_default_call_is_the_rule_path_and_the_rules_mode_only_adds_the_record(layer, question):
    def never(payload, schema):
        raise AssertionError("the rules mode calls nothing")

    default = interpret_question(question, layer)
    assert "decomposition" not in default
    for setting in ({"mode": "rules"}, {"mode": "rules", "execution_mode": "live", "proposal_source": never},
                    DecompositionConfig()):
        a = interpret_question(question, layer, decomposition=setting)
        assert without_record(a) == default
        d = a["decomposition"]
        assert (d["requested_mode"], d["applied_source"], d["execution_mode"], d["fallback_reason"]) == \
            ("rules", "rules", None, None)
        assert d["verify"] is None and d["certainty"] is None and d["alternative"] is None
        assert d["rule_diagnostics"]["certain"] == default["judgement"]["certain"]
        assert d["rule_diagnostics"]["failed_sources"] == default["judgement"]["failed_sources"]
        assert check_analysis(a) == []


def test_setting_errors(layer):
    src = lambda payload, schema: None  # noqa: E731
    for bad in ({"mode": "other"}, {"mode": "gpt"}, {"mode": "gpt", "execution_mode": "replay", "proposal_source": src},
                {"mode": "gpt", "execution_mode": "live", "proposal_source": src}, {"mode": "rules", "unknown": 1}):
        with pytest.raises(ValueError):
            interpret_question(RATIO_Q, layer, decomposition=bad)
    with pytest.raises(ValueError):  # the old 2.6 model step and the gpt mode never run together
        interpret_question(RATIO_Q, layer, model=object(), decomposition={"mode": "gpt", "proposal_source": src})


# ---------------------------------------------------------------- fallback keeps the pure rule results


def _fallback(layer, question, source, reason, monkeypatch=None):
    rules = interpret_question(question, layer)
    a = interpret_question(question, layer, decomposition={"mode": "gpt", "proposal_source": source})
    assert check_analysis(a) == []
    assert without_record(a) == rules  # the whole question goes back; nothing of the proposal is mixed in
    d = a["decomposition"]
    assert d["applied_source"] == "rules_fallback" and d["fallback_reason"] == reason
    assert d["bindings"] == [] and d["assumption_seeds"] == [] and d["certainty"] is None
    assert d["rule_diagnostics"]["failed_conditions"] == rules["judgement"]["failed_conditions"]
    return d


def test_transport_failures_fall_back_and_keep_only_the_error_class(layer):
    def boom(payload, schema):
        raise RuntimeError("secret-token-text")

    d = _fallback(layer, RATIO_Q, boom, "transport")
    assert d["error_class"] == "RuntimeError" and d["verify"] is None
    assert "secret-token-text" not in json.dumps(d, ensure_ascii=False)
    d = _fallback(layer, RATIO_Q, lambda payload, schema: None, "transport")
    assert d["error_class"] is None


def test_format_limit_and_meaning_failures_fall_back_with_the_verification(layer):
    d = _fallback(layer, RATIO_Q, lambda payload, schema: "not json {", "format")
    assert d["verify"]["outcome"] == "fallback" and {r["code"] for r in d["verify"]["fallback_reasons"]} == {"format"}

    def too_many(payload, schema):
        p = P(payload)
        reqs = [req(f"r{n}", ind(p, "가상대출금")) for n in range(1, DP.LIMITS["requirements"] + 2)]
        return proposal(reading(reqs))

    assert _fallback(layer, RATIO_Q, too_many, "limit")["verify"]["outcome"] == "fallback"

    def bad_subquote(payload, schema):
        p = P(payload)
        out = ratio_reading(p)
        out["requirements"][0]["indicator"]["subquote"] = "없는말"
        return proposal(out)

    d = _fallback(layer, RATIO_Q, bad_subquote, "semantic")
    assert {r["code"] for r in d["verify"]["fallback_reasons"]} == {"subquote_not_found"}


def test_an_input_limit_falls_back_without_calling(layer, monkeypatch):
    monkeypatch.setitem(DP.LIMITS, "segments", 3)

    def never(payload, schema):
        raise AssertionError("an input limit sends nothing")

    d = _fallback(layer, SPLIT_Q, never, "input_limit")
    assert d["verify"] is None


# ---------------------------------------------------------------- projection of the condition bindings (7.1)


def test_a_question_binding_is_common_and_shared(layer):
    a = run(TWO_Q, layer, lambda p: proposal(two_reading(p, bind(p.c("frequency")))))
    f = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")
    it = a["interpretation"]
    assert it["common_condition_ids"] == [f]
    assert all(r["shared_condition_ids"] == [f] and f not in r["condition_ids"] for r in it["requirements"])
    assert a["judgement"]["attribution"][f] == "common"
    assert a["decomposition"]["bindings"][0]["scope"] == "question"


def test_partial_sharing_goes_to_each_named_requirement(layer):
    a = run(TWO_Q, layer, lambda p: proposal(two_reading(p, bind(p.c("frequency"), "requirements", ["r1", "r2"]))))
    f = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")
    it = a["interpretation"]
    assert it["common_condition_ids"] == []
    assert all(f in r["condition_ids"] and r["shared_condition_ids"] == [] for r in it["requirements"])
    assert f not in a["judgement"]["attribution"]  # not representable in the old shape: the bindings are the source
    assert any(b["condition_id"] == f and b["requirement_ids"] == ["r1", "r2"] for b in a["decomposition"]["bindings"])
    one = run(TWO_Q, layer, lambda p: proposal(two_reading(p, bind(p.c("frequency"), "requirements", ["r2"]))))
    assert one["judgement"]["attribution"][f] == "d2"
    assert f in one["interpretation"]["requirements"][1]["condition_ids"]
    assert f not in one["interpretation"]["requirements"][0]["condition_ids"]


def test_an_unresolved_binding_is_ambiguous_scoped_and_seeded(layer):
    a = run(TWO_Q, layer, lambda p: proposal(two_reading(p, bind(p.c("frequency"), "unresolved", ["r1", "r2"]))))
    f = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")
    it, j, d = a["interpretation"], a["judgement"], a["decomposition"]
    assert all(r["ambiguous_condition_ids"] == [f] and f not in r["condition_ids"] for r in it["requirements"])
    assert j["attribution"][f] == "ambiguous"
    (row,) = [x for x in j["failed_sources"] if x["condition"] == "ambiguous_attribution"]
    assert row["condition_ids"] == [f] and row["requirement_ids"] == ["r1", "r2"]
    assert j["certain"] is False and it["status"] == "ask"
    (seed,) = [s for s in d["assumption_seeds"] if s["reason_code"] == "condition_unresolved"]
    assert seed["refs"]["condition_ids"] == [f] and seed["source"] == "question" and seed["stage"] == "A"
    assert seed["requirement_keys"] == [{"reading_id": "primary", "requirement_id": "r1"},
                                        {"reading_id": "primary", "requirement_id": "r2"}]
    assert d["condition_fields"][f]["confirmed"] is False
    for rid in ("r1", "r2"):  # the selection reads it as an unsettled condition of each requirement
        assert f in unsettled(a, rid)["condition_ids"]
    # the structure differs from the rules (they give F to r2): that failure is the whole requirement's
    (diff,) = [x for x in j["failed_sources"] if x["condition"] == "unverified_structure"]
    assert diff["condition_ids"] == [] and diff["fragment_ids"] == [] and diff["requirement_ids"]
    only = copy.deepcopy(a)  # the binding's own scope, read alone: those conditions only, never the whole requirement
    only["judgement"]["failed_sources"] = [row]
    for rid in ("r1", "r2"):
        got = unsettled(only, rid)
        assert got["all"] is False and got["condition_ids"] == [f] and got["fragment_ids"] == []


# ---------------------------------------------------------------- the settled requirements


def test_requirements_carry_refs_reading_and_a_compatible_span(layer):
    a = run(SPLIT_Q, layer, lambda p: proposal(split_reading(p)))
    (r,) = a["interpretation"]["requirements"]
    assert r["reading_id"] == "primary" and r["quote"] == a["text"]["raw"][r["span"][0]:r["span"][1]]
    assert r["indicator"]["name"] == "가상대출금" and r["indicator"]["path"] == "exact"
    assert r["breakdown"] == ["가상서울", "가상부산"] and r["targets"] == []
    segs = {s["segment_id"]: s["quote"] for s in a["segments"]}
    assert [segs[s] for s in r["refs"]["segment_ids"]] == ["가상대출금", "가상서울", "·", "가상부산", "으로 나눠서"]
    assert set(r["refs"]["mention_ids"]) <= {m["mention_id"] for m in a["mentions"]}
    j = a["judgement"]
    assert j["pairs"] == [] and len(j["requirement_draft"]) == 1  # no judgement-table row is made up
    assert {f["quote"] for f in a["fragments"]} == {"가상대출금", "가상서울", "가상부산"}
    assert a["decomposition"]["requirement_ids"] == {"r1": "r1"}
    # the selection reads the settled structure unchanged
    assert requirement_identity(a, "r1")["fragment_ids"] == j["requirement_draft"][0]["fragment_ids"]
    needs = all_needs(a)
    assert needs["r1"]["evidence"]["indicator"]["mapped"] is True


def test_proposal_requirement_ids_are_renumbered_and_kept(layer):
    def build(p):
        out = ratio_reading(p)
        out["requirements"][0]["id"], out["requirements"][1]["id"] = "r5", "r2"
        for b in out["condition_bindings"]:
            b["requirement_ids"] = ["r5", "r2"]
        for rel in out["relations"]:
            rel["requirement_ids"] = ["r5", "r2"]
        return proposal(out)

    a = run(RATIO_Q, layer, build)
    assert [r["id"] for r in a["interpretation"]["requirements"]] == ["r1", "r2"]
    assert a["decomposition"]["requirement_ids"] == {"r1": "r5", "r2": "r2"}
    assert all(rel["requirement_ids"] == ["r1", "r2"] for rel in a["interpretation"]["relations"])


# ---------------------------------------------------------------- certainty (7.2)


def test_same_structure_with_certain_rules_is_certain(layer):
    rules = interpret_question(RATIO_Q, layer)
    assert rules["judgement"]["certain"] is True
    a = run(RATIO_Q, layer, lambda p: proposal(ratio_reading(p)))
    d = a["decomposition"]
    assert d["applied_source"] == "model" and d["certainty"]["same_structure"] is True
    assert a["judgement"]["certain"] is True and a["judgement"]["failed_conditions"] == []
    assert a["interpretation"]["status"] == "ok" and d["assumption_seeds"] == []
    assert [x["kind"] for x in a["interpretation"]["relations"]] == ["ratio", "ratio"]


def test_same_structure_with_uncertain_rules_is_not_certain(layer):
    rules = interpret_question(WORD_Q, layer)
    assert rules["judgement"]["certain"] is False

    def build(p):
        return proposal(reading([req(indicator=ind(p, "가상대출금"))], request=[R(p.s("모르는말")), R(p.s("보여줘"))]))

    a = run(WORD_Q, layer, build)
    basis = a["decomposition"]["certainty"]
    # user approval 10/20 22:48: the unconfirmed word put in the request refs stays unresolved (fix 5), and the rule
    # ground over it is re-checked and still valid (never copied, never dropped)
    assert basis == {"rules_certain": False, "same_structure": True, "verified": True, "seeds_left": True,
                     "failures_left": True, "differences": []}
    assert a["judgement"]["certain"] is False and a["interpretation"]["status"] == "ask"
    assert a["judgement"]["failed_conditions"] == ["leftover_word"]
    word = next(s["segment_id"] for s in a["segments"] if s["quote"] == "모르는말")
    (seed,) = [s for s in a["decomposition"]["assumption_seeds"] if s["reason_code"] == "unused_unresolved"]
    assert seed["refs"]["segment_ids"] == [word]  # the unresolved scope is kept
    assert "unused_unresolved" in a["decomposition"]["verify"]["scopes"]["segments"][word]
    assert a["decomposition"]["rule_diagnostics"]["failed_conditions"] == rules["judgement"]["failed_conditions"]


def test_a_different_structure_over_the_same_tables_is_not_certain(layer):
    rules = interpret_question(SPLIT_Q, layer)
    assert rules["judgement"]["certain"] is True
    a = run(SPLIT_Q, layer, lambda p: proposal(split_reading(p)))
    tables = lambda x: {t for term in x["terms"] if term["type"] != "out_of_scope" for t in term["table_ids"]}  # noqa
    assert tables(a) == tables(rules)  # the same tables are no ground for the same meaning
    d = a["decomposition"]
    assert d["certainty"]["same_structure"] is False and d["certainty"]["differences"] == ["requirements"]
    assert a["judgement"]["certain"] is False and a["judgement"]["failed_conditions"] == ["unverified_structure"]
    (row,) = a["judgement"]["failed_sources"]
    assert row["requirement_ids"] == ["r1"] and row["fragment_ids"] == [] and row["condition_ids"] == []
    assert unsettled(a, "r1")["all"] is True  # the whole differing requirement is open
    (seed,) = d["assumption_seeds"]
    assert seed["reason_code"] == "unverified_structure" and seed["requirement_keys"][0]["requirement_id"] == "r1"


def test_compare_structures_reads_meaning_not_ids():
    def interp(*reqs, rels=()):
        return {"requirements": list(reqs), "relations": list(rels)}

    def r(rid, name, targets=(), conds=()):
        return {"id": rid, "indicator": {"name": name, "quote": name}, "targets": [{"name": t} for t in targets],
                "breakdown": [], "context": None, "condition_ids": list(conds), "shared_condition_ids": [],
                "ambiguous_condition_ids": [], "status": "ok"}

    j = {"negated": []}
    a = interp(r("r1", "가상가"), r("r2", "가상나"), rels=[{"kind": "ratio", "requirement_ids": ["r1", "r2"],
                                                        "condition_id": "L1"}])
    b = interp(r("r1", "가상나"), r("r2", "가상가"), rels=[{"kind": "ratio", "requirement_ids": ["r2", "r1"],
                                                        "condition_id": "L1"}])
    assert compare_structures(a, j, b, j)["same"] is True  # order of requirements is no meaning, operand order is
    c = interp(r("r1", "가상나"), r("r2", "가상가"), rels=[{"kind": "ratio", "requirement_ids": ["r1", "r2"],
                                                        "condition_id": "L1"}])
    out = compare_structures(a, j, c, j)
    assert out["differences"] == ["relations"] and out["requirement_ids"] == ["r1", "r2"]
    d = interp(r("r1", "가상가", conds=["F1"]), r("r2", "가상나"), rels=a["relations"])
    out = compare_structures(a, j, d, j)
    assert out["differences"] == ["requirements", "relations"] and out["requirement_ids"] == ["r1"]
    assert compare_structures(a, j, a, {"negated": ["가상다"]})["differences"] == ["polarity"]


# ---------------------------------------------------------------- failures scoped for the selection (7.2)


def test_a_finding_on_a_target_is_a_name_only_fragment_scope(layer):
    def build(p):
        return proposal(reading([req(indicator=ind(p, "가상대출금"), targets=[R(p.s("모르는말"))])],
                                request=[R(p.s("보여줘"))]))

    a = run(WORD_Q, layer, build)
    (row,) = [x for x in a["judgement"]["failed_sources"] if x["condition"] == "inexact_term"]
    frag = next(f["id"] for f in a["fragments"] if f["quote"] == "모르는말")
    assert row["requirement_ids"] == ["r1"] and row["fragment_ids"] == [frag] and row["condition_ids"] == []
    assert row["detail"] == "unmapped_concept:target"
    only = copy.deepcopy(a)  # read alone (the structure difference from the rules is a failure of its own)
    only["judgement"]["failed_sources"] = [row]
    got = unsettled(only, "r1")
    assert got["all"] is False and got["fragment_ids"] == [frag] and got["condition_ids"] == []
    assert unsettled(a, "r1")["all"] is True  # with it: the rules read no target here
    assert a["interpretation"]["requirements"][0]["status"] == "ok"
    (seed,) = [s for s in a["decomposition"]["assumption_seeds"] if s["reason_code"] == "unmapped_concept"]
    assert seed["source"] == "catalog" and seed["refs"]["segment_ids"] == [
        s["segment_id"] for s in a["segments"] if s["quote"] == "모르는말"]


def test_an_unmapped_indicator_makes_the_requirement_unresolved(layer):
    def build(p):
        return proposal(reading([req(indicator=R(p.s("모르는말")))], request=[R(p.s("보여줘"))],
                                unused=[{"ref": ind(p, "가상대출금"), "reason": "unresolved"}]))

    a = run(WORD_Q, layer, build)
    (r,) = a["interpretation"]["requirements"]
    assert r["status"] == "unresolved" and r["indicator"]["name"] is None
    assert unsettled(a, "r1")["all"] is True
    assert a["judgement"]["leftover"] == ["가상대출금"]


# ---------------------------------------------------------------- the alternative reading (appendix A 3)


def test_a_kept_alternative_is_settled_and_recorded_with_its_own_reading(layer):
    def build(p):
        alt = split_reading(p)
        alt["requirements"][0]["targets"] = [R(p.s("가상서울"), z=p.z("가상서울", "target")),
                                             R(p.s("가상부산"), z=p.z("가상부산", "target"))]
        alt["requirements"][0]["breakdowns"] = [bd([], [R(p.s("·")), R(p.s("으로 나눠서"))])]
        return proposal(split_reading(p), alt)

    a = run(SPLIT_Q, layer, build)
    d = a["decomposition"]
    assert d["verify"]["alternative"] == "kept"
    alt = d["alternative"]
    assert alt["reading_id"] == "alternative"
    (r_alt,) = alt["interpretation"]["requirements"]
    (r_pri,) = a["interpretation"]["requirements"]
    assert r_alt["id"] == r_pri["id"] == "r1"  # the same requirement ID in two readings: told apart by the reading
    assert r_alt["reading_id"] == "alternative" and r_pri["reading_id"] == "primary"
    assert [t["name"] for t in r_alt["targets"]] == ["가상서울", "가상부산"] and r_pri["targets"] == []
    assert set(alt["condition_fields"]) == {c["id"] for c in a["conditions"]} and set(alt["inferred"]) == {"r1"}
    seeds = {s["reason_code"]: s for s in d["assumption_seeds"]}
    assert seeds["alternative_reading"]["requirement_keys"] == [{"reading_id": "primary", "requirement_id": "r1"}]
    assert "mixed_reading" in a["judgement"]["failed_conditions"] and a["judgement"]["certain"] is False
    assert all(k["reading_id"] == "alternative" for s in alt["assumption_seeds"] for k in s["requirement_keys"])


def test_a_dropped_alternative_is_not_recorded(layer):
    def build(p):
        alt = split_reading(p)
        alt["requirements"][0]["indicator"]["subquote"] = "없는말"
        return proposal(split_reading(p), alt)

    d = run(SPLIT_Q, layer, build)["decomposition"]
    assert d["verify"]["alternative"] == "dropped" and d["alternative"] is None


# ---------------------------------------------------------------- condition fields (7.4) and period diagnosis


def _rules_record(layer, question: str) -> dict:
    a = interpret_question(question, layer, decomposition={"mode": "rules"})
    return a, a["decomposition"]


@pytest.mark.parametrize("question,kind,confirmed", [
    ("가상대출금 2024년 1월부터 분기별로 보여줘", "period", True),
    ("가상대출금 2024년 1월부터 분기별로 보여줘", "frequency", True),
    ("가상대출금 지난달", "period", True),  # shared resolver supports it; attribution is settled
    ("가상대출금 2024년 13월", "period", False),
    (TWO_Q, "breakdown", True),  # over a catalog name
    (RATIO_Q, "relation", False),  # never by its position
    (SPLIT_Q, "function", False),
])
def test_condition_fields_by_kind(layer, question, kind, confirmed):
    a, d = _rules_record(layer, question)
    rows = [d["condition_fields"][c["id"]] for c in a["conditions"] if c["kind"] == kind]
    assert rows and all(r["confirmed"] is confirmed and r["source"] == "question" and r["applied"] is None
                        for r in rows)
    assert all(("period_diagnosis" in d["condition_fields"][c["id"]]) == (c["kind"] == "period")
               for c in a["conditions"])
    assert all(set(c) == {"id", "kind", "quote", "span", "value", "attrs", "status"} for c in a["conditions"])


def test_a_condition_named_by_a_failure_or_unattributed_is_not_confirmed():
    cond = {"id": "F1", "kind": "frequency", "quote": "월별", "span": [0, 2], "value": "M",
            "attrs": {"value": "M", "anchor": None}, "status": "candidate"}
    interp = {"common_condition_ids": [], "requirements": [{"id": "r1", "condition_ids": ["F1"],
                                                            "shared_condition_ids": [],
                                                            "ambiguous_condition_ids": []}]}
    assert condition_fields([cond], interp, {"failed_sources": []})["F1"]["confirmed"] is True
    failed = {"failed_sources": [{"condition": "ambiguous_word", "requirement_ids": [], "detail": "x",
                                  "condition_ids": ["F1"], "fragment_ids": []}]}
    assert condition_fields([cond], interp, failed)["F1"]["confirmed"] is False
    nowhere = {"common_condition_ids": [], "requirements": [{"id": "r1", "condition_ids": [],
                                                             "shared_condition_ids": [],
                                                             "ambiguous_condition_ids": []}]}
    assert condition_fields([cond], nowhere, {})["F1"]["confirmed"] is False


@pytest.mark.parametrize("question,diagnosis", [
    ("가상대출금 2024년 1월부터", "ok"),
    ("가상대출금 2024년", "ok"),
    ("가상대출금 최근 3개월", "ok"),  # the shared resolver, not the old extract_status, decides support
    ("가상대출금 지난달", "ok"),
    ("가상대출금 2024년 13월", "malformed"),  # an invalid unit
    ("가상대출금 2024년 6월부터 2024년 1월까지", "malformed"),  # a backwards range
])
def test_period_diagnosis_three_values(layer, question, diagnosis):
    a, d = _rules_record(layer, question)
    (p,) = [c for c in a["conditions"] if c["kind"] == "period"]
    assert d["condition_fields"][p["id"]]["period_diagnosis"] == diagnosis
    assert period_diagnosis(p["attrs"], d["as_of"]) == diagnosis


def test_period_confirmation_uses_the_given_reference_date(layer):
    attrs = {"form": "point", "start_expr": {"text": "올해", "year": None, "alias": "올해", "sub_unit": None,
                                             "sub_value": None, "end_mark": False},
             "end_expr": None, "open_end": False, "amount": None, "unit": None, "relative": True, "invalid": False,
             "extract_status": "explicit"}
    assert period_diagnosis(attrs, "2026-10-15") == "ok"
    a = interpret_question("가상대출금 2024년", layer, decomposition={"mode": "rules", "as_of": "2026-10-15"})
    assert a["decomposition"]["as_of"] == "2026-10-15"


# ---------------------------------------------------------------- inferred frequency hint (7.5)


@pytest.mark.parametrize("question,want", [
    ("가상대출금 최근 3개월", "M"),
    ("가상대출금 최근 4분기", "Q"),
    ("가상대출금 지난달", "M"),
    ("가상대출금 최근 3개월 월별로", None),  # an explicit frequency wins
    ("가상대출금 2024년 1월부터 2024년 3분기까지", None),  # mixed units
    ("가상대출금 2024년 13월", None),  # an invalid unit
    ("가상대출금 2024년 6월부터 2024년 1월까지", None),  # a malformed period
    ("가상대출금 2024년", None),  # a year is no month or quarter unit
    (RATIO_Q, None),  # no period
])
def test_inferred_hint_cases(layer, question, want):
    a, d = _rules_record(layer, question)
    assert set(d["inferred"]) == {r["id"] for r in a["interpretation"]["requirements"]}
    if want is None:
        assert all(h == [] for h in d["inferred"].values())
    else:
        (hints,) = d["inferred"].values()
        (p,) = [c["id"] for c in a["conditions"] if c["kind"] == "period"]
        assert hints == [{"kind": "frequency", "value": want, "from_condition_id": p, "rule": "period_unit"}]
    # never a condition, never on the requirements the selection reads
    assert a["conditions"] == interpret_question(question, layer)["conditions"]
    assert all("inferred" not in r for r in a["interpretation"]["requirements"])


def test_an_ambiguous_frequency_or_period_gives_no_hint():
    period = {"id": "P1", "kind": "period", "quote": "최근 3개월", "span": [0, 6], "value": None, "status": "candidate",
              "attrs": {"form": "recent", "start_expr": None, "end_expr": None, "open_end": False, "amount": 3,
                        "unit": "month", "relative": True, "invalid": False, "extract_status": "unresolved"}}
    freq = {"id": "F1", "kind": "frequency", "quote": "분기별", "span": [7, 10], "value": "Q", "status": "candidate",
            "attrs": {"value": "Q", "anchor": None}}
    own = {"id": "r1", "condition_ids": ["P1"], "shared_condition_ids": [], "ambiguous_condition_ids": []}
    assert inferred_hints(own, [period], {"requirements": [own]})[0]["value"] == "M"
    amb = dict(own, ambiguous_condition_ids=["F1"])
    assert inferred_hints(amb, [period, freq], {"requirements": [amb]}) == []
    loose = dict(own)  # a frequency attributed to no requirement may be this one's
    assert inferred_hints(loose, [period, freq], {"requirements": [loose]}) == []
    amb_period = dict(own, condition_ids=[], ambiguous_condition_ids=["P1"])
    assert inferred_hints(amb_period, [period], {"requirements": [amb_period]}) == []


# ---------------------------------------------------------------- contract of the record


def test_check_decomposition_catches_broken_records(layer):
    a = run(TWO_Q, layer, lambda p: proposal(two_reading(p, bind(p.c("frequency"), "unresolved", ["r1", "r2"]))))
    f = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")

    def broken(how):
        b = copy.deepcopy(a)
        d = b["decomposition"]
        if how == "applied":
            d["condition_fields"][f]["applied"] = True
        elif how == "row_missing":
            del d["condition_fields"][f]
        elif how == "diagnosis_on_frequency":
            d["condition_fields"][f]["period_diagnosis"] = "ok"
        elif how == "seed_segment":
            d["assumption_seeds"][0]["refs"]["segment_ids"] = ["s999"]
        elif how == "seed_requirement":
            d["assumption_seeds"][0]["requirement_keys"] = [{"reading_id": "primary", "requirement_id": "r9"}]
        elif how == "certain":
            b["judgement"]["certain"] = True
            b["judgement"]["failed_conditions"] = []
            b["judgement"]["failed_sources"] = []
        elif how == "fallback_reason":
            d["fallback_reason"] = "transport"
        elif how == "reading":
            b["interpretation"]["requirements"][0]["reading_id"] = "alternative"
        elif how == "ids":
            d["requirement_ids"] = {"r1": "r1"}
        elif how == "source_fragment":
            b["judgement"]["failed_sources"][0]["fragment_ids"] = ["g999"]
        elif how == "binding":
            d["bindings"][0]["condition_id"] = "Z9"
        elif how == "inferred":
            d["inferred"]["r1"] = [{"kind": "frequency", "value": "M", "from_condition_id": f, "rule": "period_unit"}]
        return b

    for how in ("applied", "row_missing", "diagnosis_on_frequency", "seed_segment", "seed_requirement", "certain",
                "fallback_reason", "reading", "ids", "source_fragment", "binding", "inferred"):
        assert check_analysis(broken(how)) != [], how
    rules = interpret_question(TWO_Q, layer, decomposition={"mode": "rules"})
    bad = copy.deepcopy(rules)
    bad["decomposition"]["applied_source"] = "model"
    assert check_decomposition(bad["decomposition"], bad) != []
    bad = copy.deepcopy(rules)
    bad["decomposition"]["bindings"] = [{"condition_id": f, "scope": "question", "requirement_ids": [],
                                         "name_refs": []}]
    assert check_decomposition(bad["decomposition"], bad) != []


# ---------------------------------------------------------------- statuses (7.3): out of scope and unmapped apart


@pytest.fixture(scope="module")
def scoped_layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_settle_scope")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    oos = folder / "out_of_scope.jsonl"
    oos.write_text(json.dumps({"term": "가상환율", "reason": "synthetic", "source": "test", "status": "approved"},
                              ensure_ascii=False) + "\n", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=oos, **inputs)


def test_an_out_of_scope_indicator_is_unsupported_and_an_unmapped_one_unresolved(scoped_layer):
    question = "가상환율 보여줘"
    rules = interpret_question(question, scoped_layer)
    assert [r["status"] for r in rules["interpretation"]["requirements"]] == ["unsupported"]
    a = run(question, scoped_layer, lambda p: proposal(reading([req(indicator=R(p.s("가상환율")))],
                                                               request=[R(p.s("보여줘"))])))
    (r,) = a["interpretation"]["requirements"]
    assert r["status"] == "unsupported" and r["reasons"] == ["out_of_scope"]
    assert a["interpretation"]["status"] == "unsupported"
    assert a["interpretation"]["unsupported_sources"]["r1"]["all_out_of_scope"] is True
    b = run(WORD_Q, scoped_layer, lambda p: proposal(reading(
        [req(indicator=R(p.s("모르는말")))], request=[R(p.s("보여줘"))],
        unused=[{"ref": ind(p, "가상대출금"), "reason": "unresolved"}])))
    assert b["interpretation"]["requirements"][0]["status"] == "unresolved"


def test_a_replacement_link_is_projected_as_negated_words_and_fragment(layer):
    question = "가상서울 말고 가상부산 가상대출금"
    rules = interpret_question(question, layer)
    assert rules["judgement"]["negated"] == ["가상서울"] and rules["judgement"]["certain"] is True

    def build(p):
        seoul, busan = R(p.s("가상서울"), z=p.z("가상서울", "target")), R(p.s("가상부산"), z=p.z("가상부산", "target"))
        link = {"kind": "replace", "marker_refs": [R(p.s("말고"))], "negative_refs": [seoul],
                "positive_refs": [busan], "requirement_ids": ["r1"]}
        return proposal(reading([req(indicator=ind(p, "가상대출금"), targets=[busan])], links=[link]))

    a = run(question, layer, build)
    assert a["judgement"]["negated"] == ["가상서울"]
    (neg,) = [f for f in a["fragments"] if f["phrase"] == "negated"]
    assert neg["quote"] == "가상서울"
    assert neg["id"] not in a["judgement"]["requirement_draft"][0]["fragment_ids"]
    assert a["decomposition"]["certainty"]["same_structure"] is True and a["judgement"]["certain"] is True
    assert all_needs(a)["r1"]["filter"]["negated_fragment_ids"] == [neg["id"]]
