"""Plan 15 r3 step 3/4 fix round 2 (user decisions and review of 2026-10-20 22:48).

B: when the only reason a model reading is not certain is 7.2's "the rules are certain" (the structure verified and
the same, every rule failure ground re-checked and resolved, nothing open of its own), no scope-less row goes into
``failed_sources`` (``select/needs.py`` would read it as question-wide uncertainty); ``decomposition.certainty``
records it. In every other case the active rows stay.
C: each rule failure source is re-checked by its kind and detail (``data/decompose_policy.json``
``rule_failures.checks``) and recorded as resolved_by_code, still_valid or not_rechecked.
D: a non-fatal ``role_mismatch`` is a scoped ``inexact_term`` source, never settled evidence.

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposal sources and
some rule sources are injected into a copy of the rule analysis to reach each class. No network, no model.
"""

from __future__ import annotations

import copy
import json

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a.catalog.layer import load_layer
from claview_a.contract.check import check_analysis
from claview_a.interpret.decompose_run import DecompositionConfig, decompose
from claview_a.interpret.decompose_settle import finish_certainty
from claview_a.interpret.pipeline import interpret_question
from claview_a.select.needs import all_needs, unsettled

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None),
                                    ("ra2", "가상부산", None, "r0", None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "가상가계대출", None, None, None)]),
        ("BASE", "기준년도구분별", "BASE YEAR", [("y1", "가상기준값", None, None, None)]),
    ]),
    ("T_B", "가상예금(말잔)", "Synthetic Deposits", [
        ("REG", "지역코드별", "REGION", [("rb1", "가상서울", None, None, None)]),
        ("ACC", "계정항목별", "BSI", [("ab1", "가상예금액", None, None, None)]),
    ]),
]
TWO_Q = "가상대출금과 가상예금액 분기별로 보여줘"
RATIO_Q = "가상대출금 대비 가상예금액 비율"


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_fix2")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class P:
    def __init__(self, payload: dict):
        self.payload = payload

    def s(self, text: str, nth: int = 0) -> str:
        return [x["id"] for x in self.payload["segments"] if x["text"] == text][nth]

    def z(self, name: str, role: str) -> str:
        (z,) = {r["sense_id"] for r in self.payload["candidate_summary"] if r["name"] == name and r["role"] == role}
        return z

    def c(self, kind: str, nth: int = 0) -> str:
        return [x["id"] for x in self.payload["conditions"] if x["kind"] == kind][nth]

    def quote_c(self, quote: str) -> str:
        return next(x["id"] for x in self.payload["conditions"] if x["quote"] == quote)


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None, targets=(), basis=()) -> dict:
    return {"id": rid, "indicator": indicator, "targets": list(targets), "basis_refs": list(basis), "context": None,
            "breakdowns": []}


def bind(cid, scope="question", rids=()) -> dict:
    return {"condition_id": cid, "scope": scope, "requirement_ids": list(rids), "name_refs": []}


def reading(reqs, bindings=(), relations=(), request=(), unused=()) -> dict:
    return {"requirements": list(reqs), "condition_bindings": list(bindings), "relations": list(relations),
            "polarity_links": [], "request_refs": list(request), "unused_refs": list(unused)}


def proposal(primary, alternative=None, ambiguities=()) -> dict:
    return {"primary": primary, "alternative": alternative, "ambiguities": list(ambiguities)}


def ind(p: P, name: str) -> dict:
    return R(p.s(name), z=p.z(name, "indicator"))


def adopt(rules: dict, layer, build) -> dict:
    """The gpt mode over the given rule analysis with a fake proposal source; the model structure is adopted."""
    cfg = DecompositionConfig(mode="gpt", proposal_source=lambda payload, schema: build(P(payload)))
    a = decompose(cfg, rules, layer)
    assert check_analysis(a) == []
    assert json.loads(json.dumps(a, ensure_ascii=False)) == a
    assert a["decomposition"]["applied_source"] == "model"
    return a


def with_rule_rows(rules: dict, *rows: dict, failed: list[str] | None = None) -> dict:
    """A copy of the rule analysis made uncertain by the given failure sources (injected to reach each class)."""
    out = copy.deepcopy(rules)
    j = out["judgement"]
    j["failed_sources"] = [{"requirement_ids": [], "condition_ids": [], "fragment_ids": [], **r} for r in rows]
    j["failed_conditions"] = failed if failed is not None else list(dict.fromkeys(r["condition"] for r in rows))
    j["certain"] = False
    return out


LEFTOVER_KEY = ("leftover_word", "leftover")
LEFTOVER_RESOLVED = ("resolved_by_code", "leftover_placed")


def resolvable(rules: dict, *rows: dict, failed: list[str] | None = None) -> dict:
    """The rule analysis made uncertain by a leftover word the reading places (re-checked and resolved by code)."""
    out = with_rule_rows(rules, {"condition": "leftover_word", "detail": "leftover"}, *rows, failed=failed)
    out["judgement"]["leftover"] = ["보여줘"]
    return out


def checks(a: dict) -> dict[tuple[str, str], tuple[str, str]]:
    return {(c["condition"], c["detail"]): (c["outcome"], c["check"]) for c in a["decomposition"]["rule_checks"]}


def rule_rows(a: dict) -> list[dict]:
    return [x for x in a["judgement"]["failed_sources"] if x["detail"].startswith("rule:")]


def frag_of(rules: dict, quote: str) -> str:
    return next(f["id"] for f in rules["fragments"] if f["quote"].startswith(quote))


def two_reading(p: P, freq_scope="requirements", freq_rids=("r2",)) -> dict:
    """The rule structure of TWO_Q: the frequency on r2."""
    return reading([req("r1", ind(p, "가상대출금")), req("r2", ind(p, "가상예금액"))],
                   [bind(p.c("frequency"), freq_scope, freq_rids), bind(p.quote_c("가상대출금"), "requirements", ["r1"]),
                    bind(p.quote_c("가상예금액"), "requirements", ["r2"])],
                   request=[R(p.s("보여줘"))])


def ratio_reading(p: P, relations=True) -> dict:
    l1, l2 = p.c("relation", 0), p.c("relation", 1)
    rels = [{"condition_id": l1, "requirement_ids": ["r1", "r2"]},
            {"condition_id": l2, "requirement_ids": ["r1", "r2"]}] if relations else []
    return reading([req("r1", ind(p, "가상대출금")), req("r2", ind(p, "가상예금액"))],
                   [bind(l1, "requirements", ["r1", "r2"]), bind(l2, "requirements", ["r1", "r2"])], rels)


# ---------------------------------------------------------------- B: rules uncertain is the one reason left


def test_b_a_resolved_rule_ground_leaves_no_scopeless_row_and_records_the_reason(layer):
    rules = interpret_question(TWO_Q, layer)
    assert rules["judgement"]["certain"] is True
    uncertain = resolvable(rules)
    a = adopt(uncertain, layer, lambda p: proposal(two_reading(p)))
    j, d = a["judgement"], a["decomposition"]
    assert checks(a) == {LEFTOVER_KEY: LEFTOVER_RESOLVED}
    assert j["failed_sources"] == [] and j["failed_conditions"] == []
    assert j["certain"] is False  # 7.2 is unchanged: the rules were not certain
    assert d["certainty"] == {"rules_certain": False, "same_structure": True, "verified": True, "seeds_left": False,
                              "failures_left": False, "differences": [], "certain": False,
                              "reason": "rules_uncertain_only"}
    assert a["interpretation"]["status"] == "ask"  # the status logic is unchanged (question_route decides asking)
    for rid in ("r1", "r2"):  # never read as question-wide uncertainty
        got = unsettled(a, rid)
        assert got["all"] is False and got["condition_ids"] == [] and got["fragment_ids"] == []


def test_b_the_real_leftover_case_records_the_reason_instead_of_a_row(layer):
    question = "가상대출금에 대해 알려줘"
    rules = interpret_question(question, layer)
    assert [x["condition"] for x in rules["judgement"]["failed_sources"]] == ["leftover_word"]
    a = adopt(rules, layer, lambda p: proposal(reading([req(indicator=ind(p, "가상대출금"))],
                                                       request=[R(p.s("대해")), R(p.s("알려줘"))])))
    assert checks(a) == {("leftover_word", "leftover"): ("resolved_by_code", "leftover_placed")}
    assert a["judgement"]["failed_sources"] == [] and a["judgement"]["certain"] is False
    assert a["decomposition"]["certainty"]["reason"] == "rules_uncertain_only"
    assert unsettled(a, "r1")["all"] is False


@pytest.mark.parametrize("how", ["not_rechecked_kept", "uncovered_rule_failure", "structure_differs",
                                 "own_finding", "alternative_kept"])
def test_b_every_other_case_keeps_the_active_rows(layer, how):
    rules = interpret_question(TWO_Q, layer)
    build = lambda p: proposal(two_reading(p))  # noqa: E731
    if how == "not_rechecked_kept":
        uncertain = resolvable(rules, {"condition": "row_2_or_7", "detail": "appositive"})
    elif how == "uncovered_rule_failure":  # a failed condition of the rules with no source to re-check
        uncertain = resolvable(rules, failed=["leftover_word", "ambiguous_word"])
    elif how == "structure_differs":
        uncertain = resolvable(rules)
        build = lambda p: proposal(two_reading(p, "question", ()))  # noqa: E731
    elif how == "own_finding":
        uncertain = resolvable(rules)
        build = lambda p: proposal(two_reading(p), ambiguities=[  # noqa: E731
            {"refs": [R(p.s("가상대출금"))], "kind": "meaning", "condition_ids": [], "reason_code": "several_senses"}])
    else:
        uncertain = resolvable(rules)
        build = lambda p: proposal(two_reading(p), two_reading(p, "question", ()))  # noqa: E731
    a = adopt(uncertain, layer, build)
    j, basis = a["judgement"], a["decomposition"]["certainty"]
    assert j["certain"] is False and j["failed_sources"] and j["failed_conditions"]
    assert "reason" not in basis and "certain" not in basis
    if how == "not_rechecked_kept":
        assert checks(a)[("row_2_or_7", "appositive")] == ("not_rechecked", "")
        assert [x["detail"] for x in rule_rows(a)] == ["rule:appositive"]
    elif how == "uncovered_rule_failure":
        assert j["failed_sources"] == [{"condition": "unverified_structure", "requirement_ids": [],
                                        "detail": "rules_uncertain", "condition_ids": [], "fragment_ids": []}]
    elif how == "structure_differs":
        assert "unverified_structure" in j["failed_conditions"]
    elif how == "own_finding":
        assert "ambiguous_word" in j["failed_conditions"]
    else:
        assert "mixed_reading" in j["failed_conditions"]
        alt = a["decomposition"]["alternative"]
        assert alt["judgement"]["failed_sources"] and alt["rule_checks"]


def test_b_the_contract_check_refuses_a_reason_with_anything_left(layer):
    rules = interpret_question(TWO_Q, layer)
    uncertain = resolvable(rules)
    a = adopt(uncertain, layer, lambda p: proposal(two_reading(p)))
    assert checks(a) == {LEFTOVER_KEY: LEFTOVER_RESOLVED}
    bad = copy.deepcopy(a)
    bad["decomposition"]["rule_checks"][0]["outcome"] = "not_rechecked"
    assert check_analysis(bad) != []
    bad = copy.deepcopy(a)
    del bad["decomposition"]["certainty"]["certain"]
    assert check_analysis(bad) != []
    bad = copy.deepcopy(a)
    bad["judgement"]["failed_sources"] = [{"condition": "unverified_structure", "requirement_ids": [],
                                           "detail": "rules_uncertain", "condition_ids": [], "fragment_ids": []}]
    bad["judgement"]["failed_conditions"] = ["unverified_structure"]
    bad["decomposition"]["certainty"]["failures_left"] = True
    assert check_analysis(bad) != []


# ---------------------------------------------------------------- C: re-check classes by kind


def test_c_condition_bound_three_classes(layer):
    rules = interpret_question(TWO_Q, layer)
    f = next(c["id"] for c in rules["conditions"] if c["kind"] == "frequency")
    row = {"condition": "ambiguous_attribution", "detail": "place_ambiguous", "condition_ids": [f]}
    key = ("ambiguous_attribution", "place_ambiguous")
    # bound to concrete requirements with no finding: not re-checked (the binding is the model's choice and no
    # independent attribution check exists yet), so the row is kept
    a = adopt(with_rule_rows(rules, row), layer, lambda p: proposal(two_reading(p)))
    assert checks(a)[key] == ("not_rechecked", "condition_bound")
    (kept,) = [x for x in rule_rows(a) if x["condition"] == "ambiguous_attribution"]
    assert kept["condition_ids"] == [f] and kept["detail"] == "rule:place_ambiguous"
    # an unresolved binding: still valid
    a = adopt(with_rule_rows(rules, row), layer, lambda p: proposal(two_reading(p, "unresolved", ["r1", "r2"])))
    assert checks(a)[key] == ("still_valid", "condition_bound")
    (kept,) = [x for x in rule_rows(a) if x["condition"] == "ambiguous_attribution"]
    assert kept["condition_ids"] == [f] and kept["detail"] == "rule:place_ambiguous"
    # bound to a requirement, but a finding (a condition ambiguity the model listed) lies on it: still valid
    a = adopt(with_rule_rows(rules, row), layer, lambda p: proposal(two_reading(p), ambiguities=[
        {"refs": [R(p.s("분기별"))], "kind": "condition", "condition_ids": [p.c("frequency")],
         "reason_code": "condition_unattached"}]))
    assert checks(a)[key] == ("still_valid", "condition_bound")
    # a source with no condition to re-check (another detail of the kind): not re-checked, kept
    other = {"condition": "ambiguous_attribution", "detail": "designation_conflict"}
    a = adopt(with_rule_rows(rules, other), layer, lambda p: proposal(two_reading(p)))
    assert checks(a)[("ambiguous_attribution", "designation_conflict")] == ("not_rechecked", "")
    assert [x["detail"] for x in rule_rows(a)] == ["rule:designation_conflict"]


@pytest.mark.parametrize("scope, rids", [("question", ()), ("requirements", ("r1",))])
def test_c_a_concrete_binding_without_independent_ground_is_not_resolved(layer, scope, rids):
    # the frequency bound for the whole question, or to r1: a valid existing requirement that may be the wrong one;
    # nothing checks the attribution independently, so the model's binding never resolves the rule failure
    rules = interpret_question(TWO_Q, layer)
    f = next(c["id"] for c in rules["conditions"] if c["kind"] == "frequency")
    row = {"condition": "ambiguous_attribution", "detail": "place_ambiguous", "condition_ids": [f]}
    a = adopt(with_rule_rows(rules, row), layer, lambda p: proposal(two_reading(p, scope, rids)))
    got = checks(a)
    assert got[("ambiguous_attribution", "place_ambiguous")] == ("not_rechecked", "condition_bound")
    assert all(outcome != "resolved_by_code" for outcome, _ in got.values())
    (kept,) = [x for x in rule_rows(a) if x["condition"] == "ambiguous_attribution"]
    assert kept["condition_ids"] == [f] and kept["detail"] == "rule:place_ambiguous"
    assert "reason" not in a["decomposition"]["certainty"]
    assert a["judgement"]["certain"] is False


@pytest.mark.parametrize("name, attach, want", [
    # the catalog check is a compatibility check only: compatible is never textual proof that the scope is unique,
    # so it stays not re-checked and the row is kept
    ("가상부산", ["r1"], "not_rechecked"),  # held by the first indicator's table only, attached there only
    ("가상부산", ["r1", "r2"], "still_valid"),  # attached where the catalog does not hold it
    # held by both indicators' tables and attached to both, but the text leaves the scope ambiguous: not resolved
    ("가상서울", ["r1", "r2"], "not_rechecked"),
    ("가상서울", ["r2"], "still_valid"),  # the catalog leaves the other scope open: the model only chose one
    ("가상서울", [], "still_valid"),  # set aside
])
def test_c_group_scope_held_by_the_catalog(layer, name, attach, want):
    question = f"가상대출금과 가상예금액의 {name}"
    rules = interpret_question(question, layer)
    assert ("scope_ambiguous", "group_scope") in {(x["condition"], x["detail"])
                                                  for x in rules["judgement"]["failed_sources"]}

    def build(p):
        tgt = R(p.s(name), z=p.z(name, "target"))
        reqs = [req("r1", ind(p, "가상대출금"), [tgt] if "r1" in attach else []),
                req("r2", ind(p, "가상예금액"), [tgt] if "r2" in attach else [])]
        return proposal(reading(reqs, [bind(p.quote_c("가상대출금"), "requirements", ["r1"]),
                                       bind(p.quote_c("가상예금액"), "requirements", ["r2"])],
                                unused=[] if attach else [{"ref": tgt, "reason": "unresolved"}]))

    a = adopt(rules, layer, build)
    assert checks(a)[("scope_ambiguous", "group_scope")] == (want, "group_scope_held")
    kept = [x for x in rule_rows(a) if x["condition"] == "scope_ambiguous"]
    assert want != "resolved_by_code" and kept  # the catalog never resolves it: the row is always kept
    assert checks(a)[("unused_name", "unused")] == ("resolved_by_code", "name_used")  # both indicators are used


@pytest.mark.parametrize("how, want", [
    ("relation", "resolved_by_code"),  # two indicators joined by a relation condition of the question
    ("no_relation", "not_rechecked"),  # the model merely reads two requirements: no re-check
    ("finding", "still_valid"),  # a finding lies on the pair
])
def test_c_pair_relation(layer, how, want):
    rules = interpret_question(RATIO_Q, layer)
    pair = {"condition": "mixed_reading", "detail": "pair_row_3",
            "fragment_ids": [frag_of(rules, "가상대출금"), frag_of(rules, "가상예금액")]}

    def build(p):
        out = proposal(ratio_reading(p, relations=how != "no_relation"))
        if how == "finding":
            out["ambiguities"] = [{"refs": [R(p.s("가상예금액"))], "kind": "role", "condition_ids": [],
                                   "reason_code": "role_unclear"}]
        return out

    a = adopt(with_rule_rows(rules, pair), layer, build)
    assert checks(a)[("mixed_reading", "pair_row_3")] == (want, "pair_relation")
    kept = [x for x in rule_rows(a) if x["condition"] == "mixed_reading"]
    assert bool(kept) == (want != "resolved_by_code")
    if want == "resolved_by_code" and how == "relation":
        assert a["decomposition"]["certainty"].get("reason") == "rules_uncertain_only"


def test_c_leftover_and_unused_classes(layer):
    question = "가상대출금 모르는말 보여줘"
    rules = interpret_question(question, layer)
    a = adopt(rules, layer, lambda p: proposal(reading([req(indicator=ind(p, "가상대출금"))], request=[R(p.s("보여줘"))],
                                                       unused=[{"ref": R(p.s("모르는말")), "reason": "unresolved"}])))
    assert checks(a)[("leftover_word", "leftover")] == ("still_valid", "leftover_placed")
    question = "가상대출금 가상예금액 분기별"
    rules = interpret_question(question, layer)
    assert [x["condition"] for x in rules["judgement"]["failed_sources"]] == ["unused_name"]

    def two(p):
        return proposal(reading([req("r1", ind(p, "가상대출금")), req("r2", ind(p, "가상예금액"))],
                                [bind(p.c("frequency"))]))

    a = adopt(rules, layer, two)
    assert checks(a)[("unused_name", "unused")] == ("resolved_by_code", "name_used")
    assert not [x for x in a["judgement"]["failed_sources"] if x["condition"] == "unused_name"]


def test_c_a_kind_with_no_recheck_is_kept_and_recorded_apart(layer):
    question = "가상기준값 보여줘"
    rules = interpret_question(question, layer)
    a = adopt(rules, layer, lambda p: proposal(reading([req(indicator=R(p.s("가상기준값")))],
                                                       request=[R(p.s("보여줘"))])))
    got = checks(a)
    assert got[("row_2_or_7", "unresolved")] == ("not_rechecked", "")
    assert got[("inexact_term", "catalog_basis")] == ("not_rechecked", "")
    assert all(outcome != "resolved_by_code" for outcome, _ in got.values())
    assert {x["detail"] for x in rule_rows(a)} >= {"rule:unresolved", "rule:catalog_basis"}


def test_c_the_rules_path_records_no_rule_checks(layer):
    a = interpret_question(TWO_Q, layer, decomposition={"mode": "rules"})
    assert a["decomposition"]["rule_checks"] == [] and check_analysis(a) == []
    bad = copy.deepcopy(a)
    bad["decomposition"]["rule_checks"] = [{"condition": "leftover_word", "detail": "leftover",
                                            "outcome": "still_valid", "check": "leftover_placed"}]
    assert check_analysis(bad) != []


# ---------------------------------------------------------------- D: role_mismatch is never settled evidence


def test_d_a_role_mismatch_is_a_scoped_inexact_term_source(layer):
    question = "가상기준값 가상대출금"

    def build(p):
        basis_as_target = R(p.s("가상기준값"), z=p.z("가상기준값", "basis"))
        return proposal(reading([req(indicator=ind(p, "가상대출금"), targets=[basis_as_target])]))

    a = interpret_question(question, layer, decomposition={"mode": "gpt", "proposal_source":
                                                           lambda payload, schema: build(P(payload))})
    assert check_analysis(a) == [] and a["decomposition"]["applied_source"] == "model"
    assert {it["code"] for it in a["decomposition"]["verify"]["issues"]} == {"role_mismatch"}
    frag = next(f["id"] for f in a["fragments"] if f["quote"] == "가상기준값")
    (row,) = [x for x in a["judgement"]["failed_sources"] if x["detail"].startswith("role_mismatch")]
    assert row["condition"] == "inexact_term" and row["requirement_ids"] == ["r1"]
    assert row["fragment_ids"] == [frag] and row["detail"] == "role_mismatch:target"
    assert a["judgement"]["certain"] is False
    only = copy.deepcopy(a)  # the finding read alone: that name's fragment is unsettled, not the whole requirement
    only["judgement"]["failed_sources"] = [row]
    assert unsettled(only, "r1")["fragment_ids"] == [frag] and unsettled(only, "r1")["all"] is False
    (target,) = all_needs(only)["r1"]["evidence"]["targets"]
    assert target["fragment_id"] == frag and target["settled"] is False
    (seed,) = [s for s in a["decomposition"]["assumption_seeds"] if s["reason_code"] == "role_mismatch"]
    assert seed["source"] == "catalog"


# ---------------------------------------------------------------- B: each of the five conditions failing alone


def _settled_stub(**kw) -> dict:
    """A settled reading that meets every condition of B (one rule source re-checked and resolved)."""
    out = {"reading_id": "primary",
           "judgement": {"failed_sources": [], "leftover": []},
           "interpretation": {"requirements": [{"id": "r1", "status": "ok", "ambiguous_condition_ids": []}]},
           "assumption_seeds": [],
           "rule_checks": [{"condition": "leftover_word", "detail": "leftover", "outcome": "resolved_by_code",
                            "check": "leftover_placed"}]}
    for k, v in kw.items():
        if k in ("failed_sources", "leftover"):
            out["judgement"][k] = v
        elif k == "requirements":
            out["interpretation"]["requirements"] = v
        else:
            out[k] = v
    return out


SAME = {"same": True, "differences": [], "requirement_ids": []}
RESIDUAL = {"condition": "unverified_structure", "requirement_ids": [], "detail": "rules_uncertain",
            "condition_ids": [], "fragment_ids": []}
OWN_ROW = {"condition": "inexact_term", "requirement_ids": ["r1"], "detail": "role_mismatch:target",
           "condition_ids": [], "fragment_ids": []}


def test_b_all_five_conditions_met_writes_no_row():
    s = _settled_stub()
    basis = finish_certainty(s, False, SAME, True, ["leftover_word"])
    assert s["judgement"]["failed_sources"] == [] and s["judgement"]["failed_conditions"] == []
    assert s["judgement"]["certain"] is False and s["interpretation"]["status"] == "ask"
    assert basis["certain"] is False and basis["reason"] == "rules_uncertain_only"


@pytest.mark.parametrize("which, stub, rules_certain, cmp, verified, rules_failed, alt", [
    ("1_not_verified", {}, False, SAME, False, ["leftover_word"], False),
    ("2_structure_differs", {}, False, {"same": False, "differences": ["requirements"], "requirement_ids": []},
     True, ["leftover_word"], False),
    ("3_a_ground_still_valid", {"rule_checks": [{"condition": "leftover_word", "detail": "leftover",
                                                 "outcome": "still_valid", "check": "leftover_placed"}]},
     False, SAME, True, ["leftover_word"], False),
    ("3_a_ground_not_rechecked", {"rule_checks": [{"condition": "row_2_or_7", "detail": "appositive",
                                                   "outcome": "not_rechecked", "check": ""}]},
     False, SAME, True, ["row_2_or_7"], False),
    ("3_a_rule_failure_without_source", {}, False, SAME, True, ["leftover_word", "ambiguous_word"], False),
    ("3_no_rule_source_at_all", {"rule_checks": []}, False, SAME, True, ["leftover_word"], False),
    ("4_own_failure", {"failed_sources": [OWN_ROW]}, False, SAME, True, ["leftover_word"], False),
    ("4_own_seed", {"assumption_seeds": [{"seed_id": "seed-x"}]}, False, SAME, True, ["leftover_word"], False),
    ("4_own_leftover", {"leftover": ["모르는말"]}, False, SAME, True, ["leftover_word"], False),
    ("4_unresolved_requirement", {"requirements": [{"id": "r1", "status": "unresolved",
                                                    "ambiguous_condition_ids": []}]},
     False, SAME, True, ["leftover_word"], False),
    ("4_ambiguous_condition", {"requirements": [{"id": "r1", "status": "ok", "ambiguous_condition_ids": ["F1"]}]},
     False, SAME, True, ["leftover_word"], False),
    ("4_alternative_kept", {}, False, SAME, True, ["leftover_word"], True),
    ("5_rules_certain", {}, True, SAME, True, [], False),
])
def test_b_one_condition_failing_alone_keeps_the_active_rows(which, stub, rules_certain, cmp, verified,
                                                             rules_failed, alt):
    s = _settled_stub(**copy.deepcopy(stub))
    own = [dict(x) for x in s["judgement"]["failed_sources"]]
    basis = finish_certainty(s, rules_certain, cmp, verified, rules_failed, alt)
    assert "reason" not in basis and "certain" not in basis
    j = s["judgement"]
    if which == "5_rules_certain":  # 7.2 met: certain, nothing written
        assert j["certain"] is True and j["failed_sources"] == []
    elif own:  # the reading's own row stays as it is; no residual row is added beside it
        assert j["certain"] is False and j["failed_sources"] == own
    else:  # the active question-level row
        assert j["certain"] is False and j["failed_sources"] == [RESIDUAL]
        assert j["failed_conditions"] == ["unverified_structure"]


def test_b_never_drops_unverified_structure_rows_as_a_whole(layer):
    rules = interpret_question(TWO_Q, layer)
    # a rule failure of the unverified_structure code (check 8 of the rules) has no re-check: kept
    a = adopt(resolvable(rules, {"condition": "unverified_structure", "detail": "structure"}), layer,
              lambda p: proposal(two_reading(p)))
    assert checks(a)[LEFTOVER_KEY] == LEFTOVER_RESOLVED
    assert checks(a)[("unverified_structure", "structure")] == ("not_rechecked", "")
    assert [x["detail"] for x in rule_rows(a)] == ["rule:structure"]
    assert "reason" not in a["decomposition"]["certainty"]
    # a structure that differs from the rules keeps its own unverified_structure row
    a = adopt(resolvable(rules), layer, lambda p: proposal(two_reading(p, "question", ())))
    assert [x["detail"] for x in a["judgement"]["failed_sources"]
            if x["condition"] == "unverified_structure"] == ["unverified_structure:requirements"]


# ---------------------------------------------------------------- C: per scope, and a scope that cannot be mapped


def test_c_two_sources_of_one_kind_are_rechecked_one_by_one(layer):
    rules = interpret_question(RATIO_Q, layer)
    loan, deposit = frag_of(rules, "가상대출금"), frag_of(rules, "가상예금액")
    rows = ({"condition": "mixed_reading", "detail": "pair_row_3", "fragment_ids": [loan, deposit]},
            {"condition": "mixed_reading", "detail": "pair_row_3", "fragment_ids": [deposit]})
    # the real pair is joined by a relation condition (resolved); the same kind over one fragment only has no pair
    # to re-check (not re-checked) and is kept with its own fragment scope
    a = adopt(with_rule_rows(rules, *rows), layer, lambda p: proposal(ratio_reading(p)))
    got = [(c["outcome"], c["check"]) for c in a["decomposition"]["rule_checks"]]
    assert got == [("resolved_by_code", "pair_relation"), ("not_rechecked", "pair_relation")]
    deposit_now = [f["id"] for f in a["fragments"] if f["quote"].startswith("가상예금액")]
    (kept,) = [x for x in rule_rows(a) if x["condition"] == "mixed_reading"]
    assert kept == {"condition": "mixed_reading", "requirement_ids": [], "detail": "rule:pair_row_3",
                    "condition_ids": [], "fragment_ids": deposit_now}
    assert "reason" not in a["decomposition"]["certainty"]

    # two condition sources of one kind: one bound concretely (not re-checked), one unresolved (still valid); both
    # kept with their own conditions
    rules = interpret_question(TWO_Q, layer)
    f = next(c["id"] for c in rules["conditions"] if c["kind"] == "frequency")
    r1_cond = next(c["id"] for c in rules["conditions"] if c["quote"] == "가상대출금")
    rows = ({"condition": "ambiguous_attribution", "detail": "place_ambiguous", "condition_ids": [f]},
            {"condition": "ambiguous_attribution", "detail": "place_ambiguous", "requirement_ids": ["r1"],
             "condition_ids": [r1_cond]})

    def build(p):  # the frequency bound to r2, the r1 condition left unresolved
        out = two_reading(p)
        out["condition_bindings"][1] = bind(p.quote_c("가상대출금"), "unresolved", ["r1"])
        return proposal(out)

    a = adopt(with_rule_rows(rules, *rows), layer, build)
    got = [(c["outcome"], c["check"]) for c in a["decomposition"]["rule_checks"]]
    assert got == [("not_rechecked", "condition_bound"), ("still_valid", "condition_bound")]
    kept = [x for x in rule_rows(a) if x["condition"] == "ambiguous_attribution"]
    assert [x["condition_ids"] for x in kept] == [[f], [r1_cond]]
    assert kept[1]["requirement_ids"] == ["r1"]


def test_c_a_still_valid_source_whose_scope_cannot_be_mapped_is_kept_for_the_question(layer):
    rules = interpret_question(TWO_Q, layer)
    f = next(c["id"] for c in rules["conditions"] if c["kind"] == "frequency")
    row = {"condition": "ambiguous_attribution", "detail": "place_ambiguous", "requirement_ids": ["r9"],
           "condition_ids": [f]}  # a rule requirement the reading has nothing for
    a = adopt(with_rule_rows(rules, row), layer, lambda p: proposal(two_reading(p, "unresolved", ["r2"])))
    assert checks(a)[("ambiguous_attribution", "place_ambiguous")] == ("still_valid", "condition_bound")
    (kept,) = rule_rows(a)
    assert kept == {"condition": "ambiguous_attribution", "requirement_ids": [],
                    "detail": "rule:scope_unmapped:place_ambiguous", "condition_ids": [], "fragment_ids": []}
    assert all(unsettled(a, rid)["all"] for rid in ("r1", "r2"))  # never dropped quietly: the whole question


# ---------------------------------------------------------------- what select/needs.py reads (integration)


def _gpt(question: str, layer, build) -> dict:
    a = interpret_question(question, layer, decomposition={"mode": "gpt", "proposal_source":
                                                           lambda payload, schema: build(P(payload))})
    assert check_analysis(a) == [] and a["decomposition"]["applied_source"] == "model"
    return a


NOTHING = {"all": False, "condition_ids": [], "fragment_ids": [], "sources": []}


def test_needs_a_role_mismatch_unsettles_that_name_and_not_the_other_requirement(layer):
    question = "가상기준값 가상대출금과 가상예금액"

    def build(p):
        basis_as_target = R(p.s("가상기준값"), z=p.z("가상기준값", "basis"))
        return proposal(reading([req("r1", ind(p, "가상대출금"), [basis_as_target]), req("r2", ind(p, "가상예금액"))],
                                [bind(p.quote_c("가상대출금"), "requirements", ["r1"]),
                                 bind(p.quote_c("가상예금액"), "requirements", ["r2"])]))

    a = _gpt(question, layer, build)
    frag = next(f["id"] for f in a["fragments"] if f["quote"] == "가상기준값")
    (row,) = [x for x in a["judgement"]["failed_sources"] if x["detail"].startswith("role_mismatch")]
    assert (row["condition"], row["requirement_ids"], row["fragment_ids"]) == ("inexact_term", ["r1"], [frag])
    needs = all_needs(a)
    (target,) = needs["r1"]["evidence"]["targets"]
    assert target["fragment_id"] == frag and target["settled"] is False
    assert any(c["kind"] == "failed_source" and c["detail"] == "role_mismatch:target" for c in needs["r1"]["causes"])
    # the other requirement is not touched: its indicator and conditions are settled evidence
    r2 = needs["r2"]
    assert r2["certainty"] == NOTHING
    assert r2["evidence"]["indicator"]["settled"] is True
    assert r2["evidence"]["conditions"] and all(c["settled"] for c in r2["evidence"]["conditions"])


def test_needs_a_condition_attribution_failure_reaches_only_its_condition_and_requirement(layer):
    def build(p):
        return proposal(two_reading(p), ambiguities=[
            {"refs": [R(p.s("분기별"))], "kind": "condition", "condition_ids": [p.c("frequency")],
             "reason_code": "condition_unattached"}])

    a = _gpt(TWO_Q, layer, build)
    f = next(c["id"] for c in a["conditions"] if c["kind"] == "frequency")
    assert a["decomposition"]["certainty"]["same_structure"] is True
    (row,) = a["judgement"]["failed_sources"]
    assert row["condition"] == "ambiguous_attribution" and row["condition_ids"] == [f]
    assert row["requirement_ids"] == ["r2"]
    needs = all_needs(a)
    r2 = needs["r2"]
    assert r2["certainty"]["all"] is False and r2["certainty"]["condition_ids"] == [f]
    assert r2["certainty"]["fragment_ids"] == []
    by_id = {c["condition_id"]: c["settled"] for c in r2["evidence"]["conditions"]}
    assert by_id[f] is False and all(v for k, v in by_id.items() if k != f)  # only that condition
    assert r2["evidence"]["indicator"]["settled"] is True
    assert all(x["condition_id"] != f for x in r2["set"])  # an unsettled frequency sets nothing
    r1 = needs["r1"]  # the other requirement: nothing spreads
    assert r1["certainty"] == NOTHING
    assert all(c["settled"] for c in r1["evidence"]["conditions"])


def test_needs_the_record_only_reason_does_not_weaken_the_evidence(layer):
    rules = interpret_question(TWO_Q, layer)
    certain = adopt(rules, layer, lambda p: proposal(two_reading(p)))
    assert certain["judgement"]["certain"] is True
    only = adopt(resolvable(rules), layer, lambda p: proposal(two_reading(p)))
    assert checks(only) == {LEFTOVER_KEY: LEFTOVER_RESOLVED}
    assert only["judgement"]["certain"] is False and only["decomposition"]["certainty"]["reason"]
    assert all_needs(only) == all_needs(certain)  # the same settled evidence, causes and sets
    # with a rule ground kept active (not re-checked), the same reading is read as unsettled
    kept = adopt(with_rule_rows(rules, {"condition": "row_2_or_7", "detail": "appositive"}), layer,
                 lambda p: proposal(two_reading(p)))
    assert all(n["certainty"]["all"] for n in all_needs(kept).values())
