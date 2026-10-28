"""Plan 19 stage 3: one source-range rule for verification, settling and selection refs; basis merges; ratio operand
and order checks; lexical senses that add a measurement meaning (handling table of plan 19).

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposals put through
``verify`` (with its contract check) or through the gpt mode with a fake proposal source. IDs are found by their words,
never fixed. No network, no model.
"""

from __future__ import annotations

import copy
import json
from typing import get_args

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a_legacy.catalog.layer import load_layer
from claview_a_legacy.contract import shapes
from claview_a_legacy.contract.check import check_analysis, check_verify_result
from claview_a_legacy.interpret import decompose_payload as DP
from claview_a_legacy.interpret import decompose_settle as DS
from claview_a_legacy.interpret import reading as RD
from claview_a_legacy.interpret.conditions import find_conditions
from claview_a_legacy.interpret.decompose_payload import basis_exclusive_pairs, build_payload
from claview_a_legacy.interpret.decompose_settle import requirement_refs
from claview_a_legacy.interpret.decompose_verify import verify
from claview_a_legacy.interpret.mentions import build_mentions
from claview_a_legacy.interpret.pipeline import interpret_question
from claview_a_legacy.interpret.segments import build_segments
from claview_a_legacy.interpret.settle import RELATION_KINDS
from claview_a_legacy.interpret.text import normalize_text
from claview_a_legacy.select.needs import unsettled

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "가상가계대출", None, None, None)]),
        ("IND", "업종코드별", "INDUSTRY", [("i1", "가상도매 및 소매업", None, None, None)]),
    ]),
    ("T_B", "가상예금(말잔)", "Synthetic Deposits", [
        ("REG", "지역코드별", "REGION", [("rb1", "가상서울", None, None, None)]),
        ("ACC", "계정항목별", "BSI", [("ab1", "가상예금액", None, None, None)]),
    ]),
]

MERGE_Q = "가상대출금 원계열과 계절조정 보여줘"  # two values of one basis slot (seasonal)
NEGATED_Q = "가상대출금 원계열이 아니라 계절조정 보여줘"
CHOICE_Q = "가상대출금 원계열 또는 계절조정 보여줘"
SLOTS_Q = "가상대출금 명목과 시가 보여줘"  # a price word and a valuation word: two slots (unresolved scope)
CLASS_Q = "가상대출금 기본분류와 특수분류 보여줘"  # classification values: no slot of their own
AXES_Q = "가상대출금 지역별 업종별 보여줘"  # X-byeol words: breakdown axes, never basis values
NAME_Q = "가상예금(말잔) 평잔 보여줘"  # one stock word inside a name, one outside
RATIO_Q = "가상대출금 대비 가상예금액 비율"
PART_Q = "가상서울별 가상대출금"  # an inner name mention ("가상서울") inside a condition segment
WHOLE_Q = "가상가계대출 보여줘"
SPACED_Q = "가상도매 및 소매업 가상대출금"


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan19_stage3")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


# ---------------------------------------------------------------- helpers


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None, targets=(), basis=(), context=None, breakdowns=()) -> dict:
    return {"id": rid, "indicator": indicator, "targets": list(targets), "basis_refs": list(basis),
            "context": context, "breakdowns": list(breakdowns)}


def bind(cid, scope="question", rids=(), names=()) -> dict:
    return {"condition_id": cid, "scope": scope, "requirement_ids": list(rids), "name_refs": list(names)}


def link(kind="exclude", markers=(), negative=(), positive=(), rids=()) -> dict:
    return {"kind": kind, "marker_refs": list(markers), "negative_refs": list(negative),
            "positive_refs": list(positive), "requirement_ids": list(rids)}


def U(ref, reason="unattached") -> dict:
    return {"ref": ref, "reason": reason}


def proposal(primary, alternative=None, ambiguities=()) -> dict:
    return {"primary": primary, "alternative": alternative, "ambiguities": list(ambiguities)}


def complete(segs: list[tuple[str, str]], cond_segs: set[str], reqs, bindings, relations=(), links=(),
             unused=()) -> dict:
    """A reading whose request words are request refs and whose other meaningful segments no ref or bound condition
    uses are set aside (unattached), so that only the checked structure decides the findings."""
    used = {s for r in reqs for _, ref in requirement_refs(r) for s in ref["segment_ids"]}
    used |= {s for p in links for f in ("marker_refs", "negative_refs", "positive_refs") for ref in p[f]
             for s in ref["segment_ids"]}
    used |= {s for u in unused for s in u["ref"]["segment_ids"]}
    request = [R(sid) for sid, kind in segs if kind == "request"]
    rest = [U(R(sid)) for sid, kind in segs if kind in ("name", "word", "marker") and sid not in used | cond_segs]
    return {"requirements": list(reqs), "condition_bindings": list(bindings), "relations": list(relations),
            "polarity_links": list(links), "request_refs": request, "unused_refs": list(unused) + rest}


class Q:
    """One question read up to the payload, with helpers that find IDs by their words."""

    def __init__(self, question: str, layer):
        self.text = normalize_text(question)
        self.conditions = find_conditions(self.text, layer)
        self.segments = build_segments(self.text, self.conditions, layer)
        self.mentions = build_mentions(self.text, self.conditions, self.segments, layer)
        self.payload, self.local = build_payload(self.text, self.conditions, self.segments, self.mentions, layer)

    def s(self, quote: str, nth: int = 0) -> str:
        return [x["segment_id"] for x in self.segments if x["quote"] == quote][nth]

    def z(self, name: str, role: str) -> str:
        (z,) = {r["sense_id"] for r in self.payload["candidate_summary"] if r["name"] == name and r["role"] == role}
        return z

    def cq(self, quote: str) -> str:
        (cid,) = [c["id"] for c in self.conditions if c["quote"] == quote]
        return cid

    def kinds(self, kind: str) -> list[str]:
        return [c["id"] for c in self.conditions if c["kind"] == kind]

    def bindings(self, **over) -> list[dict]:
        """Every condition bound to the question, except the given ones (condition ID -> binding)."""
        return [over.get(c["id"]) or bind(c["id"]) for c in self.conditions]

    def reading(self, reqs, bindings, relations=(), links=(), unused=()) -> dict:
        segs = [(x["segment_id"], x["kind"]) for x in self.segments]
        cond = {s for v in self.local["condition_segments"].values() for s in v}
        return complete(segs, cond, reqs, bindings, relations, links, unused)

    def verify(self, p) -> dict:
        out = verify(p, self.payload, self.local, self.text, self.conditions, self.segments, self.mentions)
        assert check_verify_result(out, {x["segment_id"] for x in self.segments}, {x["id"] for x in self.conditions},
                                   {r["id"] for r in p["primary"]["requirements"]}) == []
        return out


class P:
    """The payload a fake proposal source is given (gpt mode), with the same helpers."""

    def __init__(self, payload: dict):
        self.payload = payload

    def s(self, text: str, nth: int = 0) -> str:
        return [x["id"] for x in self.payload["segments"] if x["text"] == text][nth]

    def z(self, name: str, role: str) -> str:
        (z,) = {r["sense_id"] for r in self.payload["candidate_summary"] if r["name"] == name and r["role"] == role}
        return z

    def cq(self, quote: str) -> str:
        (cid,) = [c["id"] for c in self.payload["conditions"] if c["quote"] == quote]
        return cid

    def kinds(self, kind: str) -> list[str]:
        return [c["id"] for c in self.payload["conditions"] if c["kind"] == kind]

    def bindings(self, **over) -> list[dict]:
        return [over.get(c["id"]) or bind(c["id"]) for c in self.payload["conditions"]]

    def reading(self, reqs, bindings, relations=(), links=(), unused=()) -> dict:
        segs = [(x["id"], x["kind"]) for x in self.payload["segments"]]
        cond = {s for c in self.payload["conditions"] for s in c["segment_ids"]}
        return complete(segs, cond, reqs, bindings, relations, links, unused)


def run(question: str, layer, build) -> dict:
    """The gpt mode with a fake proposal source called exactly once; the result passes the contract check."""
    calls: list[dict] = []

    def source(payload, schema):
        calls.append(payload)
        return build(P(payload))

    a = interpret_question(question, layer, decomposition={"mode": "gpt", "proposal_source": source})
    assert len(calls) == 1
    assert check_analysis(a) == []
    assert json.loads(json.dumps(a, ensure_ascii=False)) == a
    return a


def codes(result: dict, field: str = "fallback_reasons") -> set[str]:
    return {it["code"] for it in result[field]}


def found(result: dict, code: str, field: str = "issues") -> list[dict]:
    return [it for it in result[field] if it["code"] == code]


def loans(q) -> dict:
    return R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))


# ---------------------------------------------------------------- contract and policy rows


def test_the_new_codes_are_in_the_contract_and_the_policy():
    accept, fallback = set(get_args(shapes.AcceptCode)), set(get_args(shapes.FallbackCode))
    assert {"basis_merged", "ratio_operands", "ratio_order_conflict", "added_measure"} <= accept
    assert "basis_merged_fatal" in fallback and "basis_merged_fatal" not in accept
    assert DP.POLICY["basis_merge"]["effect"] == "non_fatal"  # user decision 10/21
    assert DS.FAIL_CODES["basis_merged"] == "ambiguous_attribution"
    assert DS.FAIL_CODES["ratio_operands"] == DS.FAIL_CODES["ratio_order_conflict"] == \
        DS.FAIL_CODES["relation_incomplete"]
    assert DS.FAIL_CODES["added_measure"] == "inexact_term" and "added_measure" in DS.NAME_CODES
    assert DS.SEED_SOURCES["added_measure"] == "catalog" and DS.SEED_SOURCES["basis_merged"] == "question"
    assert set(DS.FAIL_CODES.values()) <= set(get_args(shapes.CertaintyFailure))


def test_the_basis_facts_are_local_and_the_payload_is_unchanged(layer):
    q = Q(MERGE_Q, layer)
    raw, season = q.cq("원계열"), q.cq("계절조정")
    assert q.local["basis_exclusive"] == [[raw, season]]
    assert set(q.payload) == {"raw", "segments", "conditions", "candidate_summary", "relations", "truncation",
                              "limits"}
    assert "basis_exclusive" not in json.dumps(q.payload, ensure_ascii=False)


# ---------------------------------------------------------------- basis merge: the existing slot and swap rule


def test_exclusive_pairs_reuse_the_slot_rule_and_the_catalog_swap_pairs(layer, monkeypatch):
    assert basis_exclusive_pairs(Q(SLOTS_Q, layer).conditions, layer) == []  # price vs valuation: other slots
    cls = Q(CLASS_Q, layer)
    assert len([c for c in cls.conditions if c["kind"] == "basis"]) == 2
    assert basis_exclusive_pairs(cls.conditions, layer) == []  # classification values name no slot
    axes = Q(AXES_Q, layer)
    assert {c["value"] for c in axes.conditions if c["kind"] == "basis"} >= {"지역별", "업종별"}
    # the swap pairs of the catalog are read through the rules' own Relations.swappable (no list of its own)
    monkeypatch.setattr(RD.Relations, "swappable", lambda self, a, b: True)
    a, b = cls.kinds("basis")
    assert basis_exclusive_pairs(cls.conditions, layer) == [[a, b]]
    assert basis_exclusive_pairs(axes.conditions, layer) == []  # X-byeol words are axes, never values


def test_a_basis_merge_is_a_non_fatal_finding_scoped_to_the_requirement(layer):
    q = Q(MERGE_Q, layer)
    raw, season = q.cq("원계열"), q.cq("계절조정")
    merged = q.reading([req("r1", loans(q))], q.bindings(**{raw: bind(raw, "requirements", ["r1"]),
                                                            season: bind(season, "requirements", ["r1"])}))
    out = q.verify(proposal(merged))
    assert out["outcome"] == "accept"
    (it,) = found(out, "basis_merged")
    assert it["fatal"] is False and it["requirement_ids"] == ["r1"] and it["condition_ids"] == [raw, season]
    assert set(it["segment_ids"]) == {q.s("원계열"), q.s("계절조정")}
    assert "basis_merged" in out["scopes"]["requirements"]["r1"]
    # each requirement its own basis: no merge
    split = q.reading([req("r1", loans(q)), req("r2", loans(q))],
                      q.bindings(**{raw: bind(raw, "requirements", ["r1"]),
                                    season: bind(season, "requirements", ["r2"])}))
    assert not found(q.verify(proposal(split)), "basis_merged")
    # both bound to the whole question: every requirement holds both
    shared = q.reading([req("r1", loans(q)), req("r2", loans(q))], q.bindings())
    assert sorted(it["requirement_ids"][0] for it in found(q.verify(proposal(shared)), "basis_merged")) == ["r1", "r2"]


def test_the_contract_check_reads_the_scope_of_the_new_findings(layer):
    q = Q(MERGE_Q, layer)
    raw, season = q.cq("원계열"), q.cq("계절조정")
    merged = q.reading([req("r1", loans(q))], q.bindings(**{raw: bind(raw, "requirements", ["r1"]),
                                                            season: bind(season, "requirements", ["r1"])}))
    out = q.verify(proposal(merged))
    seg_ids, cond_ids = {x["segment_id"] for x in q.segments}, {x["id"] for x in q.conditions}
    broken = copy.deepcopy(out)
    next(it for it in broken["issues"] if it["code"] == "basis_merged")["condition_ids"] = [raw]
    assert any("does not name its scope" in x for x in check_verify_result(broken, seg_ids, cond_ids, {"r1"}))


def test_unresolved_name_content_negated_choice_and_other_slots_are_not_flagged(layer):
    q = Q(MERGE_Q, layer)
    raw, season = q.cq("원계열"), q.cq("계절조정")
    open_one = q.reading([req("r1", loans(q))], q.bindings(**{raw: bind(raw, "unresolved", ["r1"]),
                                                              season: bind(season, "requirements", ["r1"])}))
    out = q.verify(proposal(open_one))
    assert out["outcome"] == "accept" and "condition_unresolved" in codes(out, "issues")
    assert not found(out, "basis_merged")

    neg = Q(NEGATED_Q, layer)
    raw_n, season_n = neg.cq("원계열"), neg.cq("계절조정")
    assert next(c for c in neg.conditions if c["id"] == raw_n)["attrs"]["polarity"] == "-"
    replaced = neg.reading([req("r1", loans(neg))],
                           neg.bindings(**{raw_n: bind(raw_n, "requirements", ["r1"]),
                                           season_n: bind(season_n, "requirements", ["r1"])}),
                           links=[link("replace", [R(neg.s("아니라"))], [R(neg.s("원계열"))], [R(neg.s("계절조정"))])])
    out = neg.verify(proposal(replaced))
    assert out["outcome"] == "accept" and not found(out, "basis_merged")

    for question in (CHOICE_Q, SLOTS_Q, CLASS_Q):
        x = Q(question, layer)
        one = x.reading([req("r1", loans(x))], x.bindings())
        out = x.verify(proposal(one))
        assert out["outcome"] == "accept" and not found(out, "basis_merged"), question

    name = Q(NAME_Q, layer)
    deposit = R(name.s("가상예금(말잔)"), z=name.z("가상예금(말잔)", "indicator"))
    inside, outside = name.cq("말잔"), name.cq("평잔")
    content = name.reading([req("r1", deposit)], name.bindings(**{
        inside: bind(inside, "name_content", names=[R(name.s("가상예금(말잔)"))]),
        outside: bind(outside, "requirements", ["r1"])}))
    out = name.verify(proposal(content))
    assert out["outcome"] == "accept" and not found(out, "basis_merged")
    applied = name.reading([req("r1", deposit)], name.bindings(**{inside: bind(inside, "requirements", ["r1"]),
                                                                  outside: bind(outside, "requirements", ["r1"])}))
    assert found(name.verify(proposal(applied)), "basis_merged")  # the same pair applied to the requirement


def test_primary_and_alternative_share_the_rule_and_the_fatal_setting(layer, monkeypatch):
    q = Q(MERGE_Q, layer)
    raw, season = q.cq("원계열"), q.cq("계절조정")
    merged = q.reading([req("r1", loans(q))], q.bindings(**{raw: bind(raw, "requirements", ["r1"]),
                                                            season: bind(season, "requirements", ["r1"])}))
    split = q.reading([req("r1", loans(q)), req("r2", loans(q))],
                      q.bindings(**{raw: bind(raw, "requirements", ["r1"]),
                                    season: bind(season, "requirements", ["r2"])}))
    out = q.verify(proposal(split, merged))
    assert out["outcome"] == "accept" and out["alternative"] == "kept"
    assert found(out, "basis_merged", "alternative_issues") and not found(out, "basis_merged")
    monkeypatch.setitem(DP.POLICY["basis_merge"], "effect", "fatal")
    out = q.verify(proposal(merged))
    assert out["outcome"] == "fallback" and codes(out) == {"basis_merged_fatal"}
    out = q.verify(proposal(split, merged))
    assert out["outcome"] == "accept" and out["alternative"] == "dropped"
    assert codes(out, "alternative_issues") == {"basis_merged_fatal"}


def test_a_settled_basis_merge_leaves_its_conditions_open_for_that_requirement(layer):
    def build(p):
        raw, season = p.cq("원계열"), p.cq("계절조정")
        return proposal(p.reading([req("r1", R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")))],
                                  p.bindings(**{raw: bind(raw, "requirements", ["r1"]),
                                                season: bind(season, "requirements", ["r1"])})))

    a = run(MERGE_Q, layer, build)
    raw = next(c["id"] for c in a["conditions"] if c["quote"] == "원계열")
    season = next(c["id"] for c in a["conditions"] if c["quote"] == "계절조정")
    j, d = a["judgement"], a["decomposition"]
    assert d["applied_source"] == "model" and j["certain"] is False
    (row,) = [x for x in j["failed_sources"] if x["detail"] == "basis_merged"]
    assert row["condition"] == "ambiguous_attribution" and row["requirement_ids"] == ["r1"]
    assert row["condition_ids"] == [raw, season]
    assert d["condition_fields"][raw]["confirmed"] is False and d["condition_fields"][season]["confirmed"] is False
    only = copy.deepcopy(a)  # read alone: those conditions of that requirement, never the whole requirement
    only["judgement"]["failed_sources"] = [row]
    got = unsettled(only, "r1")
    assert got["all"] is False and got["condition_ids"] == sorted([raw, season])
    assert any(s["reason_code"] == "basis_merged" for s in d["assumption_seeds"])


# ---------------------------------------------------------------- ratio operands and order


def _ratio(q):
    l1, l2 = q.kinds("relation")
    assert all(RELATION_KINDS.get(c["attrs"]["relation"]) == "ratio" for c in q.conditions if c["id"] in (l1, l2))
    dep = R(q.s("가상예금액"), z=q.z("가상예금액", "indicator"))
    return l1, l2, dep


@pytest.mark.parametrize("how", ["same_order", "conflict", "three", "duplicate", "one"])
def test_ratio_operands_and_order(layer, how):
    q = Q(RATIO_Q, layer)
    l1, l2, dep = _ratio(q)
    reqs = [req("r1", loans(q)), req("r2", dep)]
    rels = {"same_order": [(l1, ["r1", "r2"]), (l2, ["r1", "r2"])],
            "conflict": [(l1, ["r1", "r2"]), (l2, ["r2", "r1"])],
            "three": [(l1, ["r1", "r2", "r3"])],
            "duplicate": [(l1, ["r1", "r2", "r1"])],
            "one": [(l1, ["r1", "r1"])]}[how]
    if how == "three":
        reqs.append(req("r3", loans(q)))
    out = q.verify(proposal(q.reading(reqs, q.bindings(), [{"condition_id": c, "requirement_ids": rr}
                                                           for c, rr in rels])))
    assert out["outcome"] == "accept"
    got = codes(out, "issues") & {"ratio_operands", "ratio_order_conflict", "relation_incomplete"}
    if how == "same_order":
        assert got == set()
    elif how == "conflict":
        assert got == {"ratio_order_conflict"}
        (it,) = found(out, "ratio_order_conflict")
        assert it["requirement_ids"] == ["r1", "r2"] and it["condition_ids"] == [l1, l2] and it["fatal"] is False
    elif how in ("three", "duplicate"):
        assert got == {"ratio_operands"}
        assert found(out, "ratio_operands")[0]["condition_ids"] == [l1]
    else:
        assert got == {"relation_incomplete"}  # fewer than two distinct operands: the existing finding only


def test_a_ratio_order_conflict_is_settled_as_direction_unknown_without_reading_the_order(layer):
    def build(p):
        l1, l2 = p.kinds("relation")
        reqs = [req("r1", R(p.s("가상대출금"), z=p.z("가상대출금", "indicator"))),
                req("r2", R(p.s("가상예금액"), z=p.z("가상예금액", "indicator")))]
        return proposal(p.reading(reqs, p.bindings(), [{"condition_id": l1, "requirement_ids": ["r1", "r2"]},
                                                       {"condition_id": l2, "requirement_ids": ["r2", "r1"]}]))

    a = run(RATIO_Q, layer, build)
    l1, l2 = [c["id"] for c in a["conditions"] if c["kind"] == "relation"]
    (row,) = [x for x in a["judgement"]["failed_sources"] if x["detail"] == "ratio_order_conflict"]
    assert row["condition"] == "scope_ambiguous" and row["requirement_ids"] == ["r1", "r2"]
    assert row["condition_ids"] == [l1, l2]
    # the relations are kept as written (operand order kept, never turned into a direction)
    assert [x["requirement_ids"] for x in a["interpretation"]["relations"] if x["kind"] == "ratio"] == \
        [["r1", "r2"], ["r2", "r1"]]
    assert a["judgement"]["certain"] is False


# ---------------------------------------------------------------- whole and partial refs: one rule everywhere


def _inner(q, sid: str) -> list[dict]:
    lo, hi = next(x["span"] for x in q.segments if x["segment_id"] == sid)
    return [m for m in q.mentions if m["kind"] == "name" and m["segment_ids"] == [sid]
            and tuple(q.local["mention_spans"][m["mention_id"]]) != (lo, hi)]


@pytest.mark.parametrize("how, want", [
    ("part_own_sense", None),  # the part written exactly as a catalog name, with its own sense
    ("whole_inner_sense", "wrong_sense"),  # new: a whole ref may not carry a sense only its part has
    ("whole_no_sense", "unmapped_concept"),  # new: a whole ref reads no inner candidate
])
def test_a_whole_ref_never_carries_an_inner_sense(layer, how, want):
    q = Q(PART_Q, layer)
    sid = q.s("가상서울별")
    assert _inner(q, sid)  # precondition: an inner name mention lies strictly inside the segment
    seoul = q.z("가상서울", "target")
    target = {"part_own_sense": R(sid, z=seoul, sq="가상서울"), "whole_inner_sense": R(sid, z=seoul),
              "whole_no_sense": R(sid)}[how]
    bound = [bind(c["id"], "requirements", ["r1"]) for c in q.conditions]
    out = q.verify(proposal(q.reading([req("r1", loans(q), targets=[target])], bound)))
    if want is None:
        assert out["outcome"] == "accept" and out["issues"] == []
    elif want == "wrong_sense":
        assert out["outcome"] == "fallback" and codes(out) == {"wrong_sense"}
    else:
        assert out["outcome"] == "accept" and want in codes(out, "issues")
        assert "sense_unresolved" not in codes(out, "issues")


def test_a_part_never_carries_the_whole_names_sense_and_the_alternative_reads_the_same_rule(layer):
    q = Q(WHOLE_Q, layer)
    sid, whole = q.s("가상가계대출"), q.z("가상가계대출", "indicator")
    rest = [U(R(sid, sq="가상가계"), "unmapped")]
    part = q.reading([req("r1", R(sid, z=whole, sq="대출"))], q.bindings(), unused=rest)
    assert codes(q.verify(proposal(part))) == {"wrong_sense"}
    ok = q.reading([req("r1", R(sid, z=whole))], q.bindings())
    assert q.verify(proposal(ok))["issues"] == []
    p = Q(PART_Q, layer)
    psid = p.s("가상서울별")
    bound = [bind(c["id"], "requirements", ["r1"]) for c in p.conditions]
    good = p.reading([req("r1", loans(p), targets=[R(psid, z=p.z("가상서울", "target"), sq="가상서울")])], bound)
    bad = p.reading([req("r1", loans(p), targets=[R(psid, z=p.z("가상서울", "target"))])], bound)
    out = p.verify(proposal(good, bad))
    assert out["outcome"] == "accept" and out["alternative"] == "dropped"
    assert codes(out, "alternative_issues") == {"wrong_sense"}


def test_settling_and_selection_refs_read_the_range_the_verification_read(layer):
    def parts(p):
        sid = p.s("가상가계대출")
        return proposal(p.reading([req("r1", R(sid, sq="가상가계")), req("r2", R(sid, sq="대출"))],
                                  [bind(c["id"], "name_content", names=[R(sid)])
                                   for c in p.payload["conditions"]]))

    a = run(WHOLE_Q, layer, parts)
    assert a["decomposition"]["applied_source"] == "model"
    # the parts quote no catalog name: the whole name's rows are not handed down to them
    assert not [t for t in a["terms"] if t["name"] == "가상가계대출"]
    refs = a["decomposition"]["selection_refs"]
    assert [x["quote"] for rid in ("r1", "r2") for x in refs[rid]] == ["가상가계", "대출"]
    assert all(x["candidates"] == [] and x["name"] is None for rid in ("r1", "r2") for x in refs[rid])

    def own(p):
        sid = p.s("가상서울별")
        return proposal(p.reading(
            [req("r1", R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")),
                 targets=[R(sid, z=p.z("가상서울", "target"), sq="가상서울")])],
            [bind(c["id"], "requirements", ["r1"]) for c in p.payload["conditions"]]))

    b = run(PART_Q, layer, own)
    (target,) = [x for x in b["decomposition"]["selection_refs"]["r1"] if x["place"] == "target"]
    assert target["quote"] == "가상서울" and target["name"] == "가상서울" and target["candidates"]
    inner = [m for m in b["mentions"] if m["kind"] == "name" and m["segment_ids"] == target["segment_ids"]]
    assert all(any(c in m["candidates"] for m in inner) for c in target["candidates"])
    (t,) = b["interpretation"]["requirements"][0]["targets"]
    assert t["quote"] == "가상서울" and t["span"] == target["span"]

    def whole(p):
        sid = p.s("가상서울별")
        return proposal(p.reading([req("r1", R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")),
                                       targets=[R(sid)])],
                                  [bind(c["id"], "requirements", ["r1"]) for c in p.payload["conditions"]]))

    c = run(PART_Q, layer, whole)
    assert c["decomposition"]["applied_source"] == "model"
    assert not [x for x in c["terms"] if x["name"] == "가상서울"]  # the inner name's rows stay with the part


def test_a_subquote_span_leaves_its_spaces_out_everywhere(layer):
    q = Q(SPACED_Q, layer)
    name = [x for x in q.segments if x["kind"] == "name" and x["quote"] == "가상도매 및 소매업"]
    assert name  # precondition: the name is one protected segment with its inner spaces

    def build(p):
        sid = p.s("가상도매 및 소매업")
        return proposal(p.reading([req("r1", R(p.s("가상대출금"), z=p.z("가상대출금", "indicator")),
                                       targets=[R(sid, sq="가상도매 ")])],
                                  [bind(c["id"], "requirements", ["r1"]) for c in p.payload["conditions"]],
                                  unused=[U(R(sid, sq="소매업"), "unmapped")]))

    a = run(SPACED_Q, layer, build)
    assert a["decomposition"]["applied_source"] == "model"
    (t,) = a["interpretation"]["requirements"][0]["targets"]
    lo = name[0]["span"][0]
    assert t["quote"] == "가상도매" and t["span"] == [lo, lo + len("가상도매")]
    (ref,) = [x for x in a["decomposition"]["selection_refs"]["r1"] if x["place"] == "target"]
    assert ref["quote"] == "가상도매" and ref["span"] == t["span"]


# ---------------------------------------------------------------- lexical senses that add a measurement meaning


def _renamed(q, name: str, path: str) -> dict:
    z = q.z("가상대출금", "indicator")
    q.local["senses"][z] = dict(q.local["senses"][z], name=name, path=path)
    return loans(q)


@pytest.mark.parametrize("question, name, path, added", [
    ("가상대출금 보여줘", "가상대출금(전년동기비)", "lexical", "comparison:전년동기비"),
    ("가상대출금 보여줘", "가상대출금(평잔)", "lexical", "stock:평잔"),
    ("가상대출금 보여줘", "가상대출금 증가율", "lexical", "rate"),
    ("가상대출금 보여줘", "가상 대출금", "lexical", None),  # a spacing variant adds nothing
    ("가상대출금 보여줘", "가상대출금(전년동기비)", "exact", None),  # only a lexical choice is checked
    ("가상대출금 전년동기비 보여줘", "가상대출금(전년동기비)", "lexical", None),  # the question asks for it
    ("가상대출금 전년 같은 달 대비 보여줘", "가상대출금 증가율", "lexical", None),  # a comparison base is a rate
])
def test_a_lexical_sense_that_adds_a_measure_is_a_scoped_finding(layer, question, name, path, added):
    q = Q(question, layer)
    ind = _renamed(q, name, path)
    out = q.verify(proposal(q.reading([req("r1", ind)], q.bindings())))
    assert out["outcome"] == "accept"
    got = found(out, "added_measure")
    if added is None:
        assert got == []
    else:
        (it,) = got
        assert it["fatal"] is False and it["requirement_ids"] == ["r1"] and it["segment_ids"] == ind["segment_ids"]
        assert it["detail"].endswith(f"adds {added}") and it["detail"].startswith("indicator:")
