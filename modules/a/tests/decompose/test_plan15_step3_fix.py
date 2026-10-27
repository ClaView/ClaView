"""Plan 15 r3 step 3 fixes (instruction of 2026-10-20 22:01 and user decisions of 22:10): top-level format errors apart
from errors inside an alternative (fix 1), roles checked by place without the model's own role ambiguity as ground
(fix 2), clear and certain polarity objects and their effect (fix 3, P2), subquotes read by what they quote and the
meaningful rest of a segment (fix 4), meaningful words that may not be set aside unchecked (fix 5), and the question
structures of section 6.

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposals, each put through
``verify`` (and its contract check). No network, no model.
"""

from __future__ import annotations

import copy

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a.catalog.layer import load_layer
from claview_a.contract.check import check_verify_result
from claview_a.interpret.conditions import find_conditions
from claview_a.interpret.decompose_payload import build_payload
from claview_a.interpret.decompose_schema import build_schema, validate
from claview_a.interpret.decompose_verify import verify
from claview_a.interpret.mentions import build_mentions, mention_spans
from claview_a.interpret.pipeline import interpret_question
from claview_a.interpret.segments import build_segments
from claview_a.interpret.text import normalize_text

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None),
                                    ("ra2", "가상부산", None, "r0", None), ("ra3", "가상대구", None, "r0", None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "가상가계대출", None, None, None),
                                  ("a2", "가상예·적금담보대출", None, None, None)]),
        ("BASE", "기준년도구분별", "BASE YEAR", [("y1", "가상기준값", None, None, None)]),
        ("FUND", "자금구분별", "FUND", [("f1", "가상미분류값", None, None, None)]),
        ("IND", "업종코드별", "INDUSTRY", [("i1", "가상도매 및 소매업", None, None, None)]),
    ]),
    ("T_B", "가상예금(말잔)", "Synthetic Deposits", [
        ("REG", "지역코드별", "REGION", [("rb1", "가상서울", None, None, None), ("rb2", "가상겸용", None, None, None)]),
        ("ACC", "계정항목별", "BSI", [("ab1", "가상예금액", None, None, None)]),
    ]),
    ("T_C", "가상겸용", "Synthetic Dual", [("ACC", "계정항목별", "BSI", [("c1", "가상씨값", None, None, None)])]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_step3_fix")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class Q:
    """One question read up to the payload, with helpers that find IDs by their words (no fixed IDs)."""

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

    def c(self, kind: str, nth: int = 0) -> str:
        return [x["id"] for x in self.conditions if x["kind"] == kind][nth]

    def all_bound(self, rids=("r1",)) -> list[dict]:
        return [bind(c["id"], "requirements", rids) for c in self.conditions]

    def verify(self, proposal) -> dict:
        out = verify(proposal, self.payload, self.local, self.text, self.conditions, self.segments, self.mentions)
        seg_ids = {x["segment_id"] for x in self.segments}
        cond_ids = {x["id"] for x in self.conditions}
        req_ids = {r["id"] for r in proposal["primary"]["requirements"]} \
            if isinstance(proposal, dict) and isinstance(proposal.get("primary"), dict) else None
        assert check_verify_result(out, seg_ids, cond_ids, req_ids) == []
        return out


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None, targets=(), basis=(), context=None, breakdowns=()) -> dict:
    return {"id": rid, "indicator": indicator, "targets": list(targets), "basis_refs": list(basis),
            "context": context, "breakdowns": list(breakdowns)}


def bd(values=(), markers=(), kind="values", axis=None) -> dict:
    return {"kind": kind, "axis": axis, "values": list(values), "marker_refs": list(markers)}


def bind(cid, scope="question", rids=(), names=()) -> dict:
    return {"condition_id": cid, "scope": scope, "requirement_ids": list(rids), "name_refs": list(names)}


def link(kind="exclude", markers=(), negative=(), positive=(), rids=()) -> dict:
    return {"kind": kind, "marker_refs": list(markers), "negative_refs": list(negative),
            "positive_refs": list(positive), "requirement_ids": list(rids)}


def reading(reqs, bindings=(), relations=(), links=(), request=(), unused=()) -> dict:
    return {"requirements": list(reqs), "condition_bindings": list(bindings), "relations": list(relations),
            "polarity_links": list(links), "request_refs": list(request), "unused_refs": list(unused)}


def proposal(primary, alternative=None, ambiguities=()) -> dict:
    return {"primary": primary, "alternative": alternative, "ambiguities": list(ambiguities)}


def amb(refs, kind, reason) -> dict:
    return {"refs": list(refs), "kind": kind, "condition_ids": [], "reason_code": reason}


def U(ref, reason="unattached") -> dict:
    return {"ref": ref, "reason": reason}


def codes(result: dict, field: str = "fallback_reasons") -> set[str]:
    return {it["code"] for it in result[field]}


def issues_on(result: dict, sid: str, field: str = "issues") -> set[str]:
    return {it["code"] for it in result[field] if sid in it["segment_ids"]}


SPLIT_Q = "가상대출금을 가상서울·가상부산으로 나눠서 분기별로 보여줘"


def split_reading(q: Q) -> dict:
    return reading(
        [req(indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")), breakdowns=[bd(
            [R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))],
            [R(q.s("·")), R(q.s("으로 나눠서"))])])],
        [bind(q.c("frequency")), bind(q.c("function"), "requirements", ["r1"]),
         bind(q.c("breakdown", 0), "requirements", ["r1"]), bind(q.c("breakdown", 1), "requirements", ["r1"])],
        request=[R(q.s("보여줘"))])


# ---------------------------------------------------------------- fix 1: top-level errors and alternative errors


def test_the_split_example_is_still_accepted(layer):
    q = Q(SPLIT_Q, layer)
    out = q.verify(proposal(split_reading(q)))
    assert out["outcome"] == "accept" and out["issues"] == []


@pytest.mark.parametrize("how", ["alternative_missing", "alternative_extra_with_null", "alternative_extra_with_reading",
                                 "alternative_not_a_reading", "primary_missing"])
def test_top_level_contract_errors_fall_back_without_an_exception(layer, how):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    if how == "alternative_missing":
        del p["alternative"]
    elif how == "alternative_extra_with_null":
        p["alternative_extra"] = None
    elif how == "alternative_extra_with_reading":
        p["alternative"] = split_reading(q)
        p["alternative_extra"] = split_reading(q)
    elif how == "alternative_not_a_reading":
        p["alternative"] = "another reading"
    elif how == "primary_missing":
        p["alternative"] = split_reading(q)
        del p["primary"]
    assert validate(p, build_schema(q.payload)) != []
    out = q.verify(p)
    assert out["outcome"] == "fallback" and codes(out) == {"format"}
    assert out["issues"] == [] and out["alternative_issues"] == []


@pytest.mark.parametrize("how, code", [("unknown_segment", "unknown_id"), ("unknown_sense", "unknown_id"),
                                       ("extra_field", "format"), ("wrong_type", "format"),
                                       ("missing_field", "format")])
def test_an_error_inside_an_existing_alternative_drops_only_it(layer, how, code):
    q = Q(SPLIT_Q, layer)
    alt = split_reading(q)
    r1 = alt["requirements"][0]
    if how == "unknown_segment":
        r1["indicator"]["segment_ids"] = ["s999"]
    elif how == "unknown_sense":
        r1["indicator"]["sense_id"] = "z999"
    elif how == "extra_field":
        r1["confidence"] = "high"
    elif how == "wrong_type":
        r1["targets"] = "가상서울"
    elif how == "missing_field":
        del r1["context"]
    out = q.verify(proposal(split_reading(q), alt))
    assert out["outcome"] == "accept" and out["alternative"] == "dropped"
    assert codes(out, "alternative_issues") == {code}
    assert out["issues"] == [] and out["assumption_seed"] is False


def test_a_broken_primary_with_a_valid_alternative_falls_back_whole(layer):
    q = Q(SPLIT_Q, layer)
    bad = split_reading(q)
    bad["requirements"][0]["note"] = "x"
    out = q.verify(proposal(bad, split_reading(q)))
    assert out["outcome"] == "fallback" and codes(out) == {"format"} and out["alternative"] == "dropped"
    bad = split_reading(q)
    bad["polarity_links"] = [link(markers=[R(q.s("·"))], negative=[R(q.s("가상서울"))])]  # no polarity marker
    out = q.verify(proposal(bad, split_reading(q)))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_no_marker"}


# ---------------------------------------------------------------- fix 2: roles by place


def test_a_sure_target_as_indicator_fails_even_with_a_model_role_ambiguity(layer):
    q = Q("가상서울 보여줘", layer)
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    plain = proposal(reading([req(indicator=seoul)], request=[R(q.s("보여줘"))]))
    assert codes(q.verify(plain)) == {"target_as_indicator"}
    declared = copy.deepcopy(plain)
    declared["ambiguities"] = [amb([R(q.s("가상서울"))], "role", "role_unclear")]
    out = q.verify(declared)
    assert out["outcome"] == "fallback" and codes(out) == {"target_as_indicator"}
    no_sense = proposal(reading([req(indicator=dict(seoul, sense_id=None))], request=[R(q.s("보여줘"))]),
                        ambiguities=[amb([R(q.s("가상서울"))], "role", "role_unclear")])
    assert codes(q.verify(no_sense)) == {"target_as_indicator"}


def test_a_name_with_several_roles_is_kept_open_by_the_code(layer):
    q = Q("가상겸용 보여줘", layer)
    s1 = q.s("가상겸용")
    roles = {r["role"] for r in q.payload["candidate_summary"] if r["name"] == "가상겸용"}
    assert {"indicator", "target"} <= roles
    out = q.verify(proposal(reading([req(indicator=R(s1))], request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"sense_unresolved"}
    ok = q.verify(proposal(reading([req(indicator=R(s1, z=q.z("가상겸용", "indicator")))], request=[R(q.s("보여줘"))])))
    assert ok["outcome"] == "accept" and ok["issues"] == []
    # the target sense chosen on purpose: a sure target-only sense as the indicator
    bad = q.verify(proposal(reading([req(indicator=R(s1, z=q.z("가상겸용", "target")))], request=[R(q.s("보여줘"))])))
    assert bad["outcome"] == "fallback" and codes(bad) == {"target_as_indicator"}


@pytest.mark.parametrize("question, name, role, path_kind", [
    ("가상기준값 보여줘", "가상기준값", "basis", "sense"),
    ("가상기준값 보여줘", "가상기준값", "basis", "no_sense"),
    ("가상도매·소매업 가상대출금", "가상도매 및 소매업", "target", "sense"),  # a lexical target: not sure
    ("가상도매·소매업 가상대출금", "가상도매 및 소매업", "target", "no_sense"),
])
def test_a_basis_or_lexical_target_as_indicator_is_kept_with_a_role_finding(layer, question, name, role, path_kind):
    q = Q(question, layer)
    (sid,) = {r["segment_ids"][0] for r in q.payload["candidate_summary"] if r["name"] == name}
    ind = R(sid, z=q.z(name, role) if path_kind == "sense" else None)
    rest = [U(R(x["segment_id"])) for x in q.segments if x["kind"] == "name" and x["segment_id"] != sid]
    request = [R(x["segment_id"]) for x in q.segments if x["kind"] == "request"]
    out = q.verify(proposal(reading([req(indicator=ind)], q.all_bound(), request=request, unused=rest)))
    assert out["outcome"] == "accept" and "role_ambiguous" in codes(out, "issues")  # never a silent pass
    assert "role_ambiguous" in out["scopes"]["requirements"]["r1"]


def test_an_axis_name_as_indicator_is_kept_with_a_role_finding(layer):
    q = Q("가상대출금 지역별로 보여줘", layer)
    axis = [r for r in q.payload["candidate_summary"] if r["role"] == "breakdown"]
    assert axis
    ind = R(axis[0]["segment_ids"][0], z=axis[0]["sense_id"])
    out = q.verify(proposal(reading([req(indicator=ind)], [bind(c["id"]) for c in q.conditions],
                                    request=[R(q.s("보여줘"))], unused=[U(R(q.s("가상대출금")))])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"role_ambiguous"}


def test_an_item_or_table_name_indicator_passes_and_unclassified_stays_open(layer):
    q = Q("가상대출금 보여줘", layer)
    for role in ("indicator", "table_name"):
        out = q.verify(proposal(reading([req(indicator=R(q.s("가상대출금"), z=q.z("가상대출금", role)))],
                                        request=[R(q.s("보여줘"))])))
        assert out["outcome"] == "accept" and out["issues"] == []
    q = Q("가상미분류값 보여줘", layer)
    for z in (q.z("가상미분류값", "unclassified"), None):
        out = q.verify(proposal(reading([req(indicator=R(q.s("가상미분류값"), z=z))], request=[R(q.s("보여줘"))])))
        assert out["outcome"] == "accept" and codes(out, "issues") == {"indicator_role_unclassified"}


def test_a_sense_of_a_role_its_place_does_not_take_is_a_finding(layer):
    q = Q("가상기준값 가상대출금", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    basis_as_target = R(q.s("가상기준값"), z=q.z("가상기준값", "basis"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[basis_as_target])])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"role_mismatch"}
    ok = q.verify(proposal(reading([req(indicator=ind, basis=[basis_as_target])])))
    assert ok["outcome"] == "accept" and ok["issues"] == []
    ctx = q.verify(proposal(reading([req(indicator=ind, context=R(q.s("가상대출금"), z=q.z("가상대출금", "table_name")))])
                            | {"unused_refs": [U(basis_as_target)]}))
    assert ctx["outcome"] == "accept" and "role_mismatch" not in codes(ctx, "issues")


# ---------------------------------------------------------------- fix 3: clear polarity objects and their effect

NEG_Q = "가상서울 말고 가상부산 가상대출금"


def neg_case(q: Q, how: str) -> tuple[dict, str | None]:
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul, busan = R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))
    mk = R(q.s("말고"))
    if how == "normal":
        return proposal(reading([req(indicator=ind, targets=[busan])],
                                links=[link("replace", [mk], [seoul], [busan])])), None
    if how == "wrong_object_no_positive":
        return proposal(reading([req(indicator=ind, targets=[busan])], links=[link("exclude", [mk], [busan], [])],
                                unused=[U(seoul)])), "polarity_target_mismatch"
    if how == "other_name_negated_too":
        return proposal(reading([req(indicator=ind)], links=[link("exclude", [mk], [seoul, busan], [])])), \
            "polarity_target_mismatch"
    if how == "empty_negative":
        return proposal(reading([req(indicator=ind, targets=[busan])], links=[link("replace", [mk], [], [busan])],
                                unused=[U(seoul)])), "polarity_effect_lost"
    if how == "marker_request_only":
        return proposal(reading([req(indicator=ind, targets=[busan])], request=[mk], unused=[U(seoul)])), \
            "polarity_marker_request_only"
    if how == "marker_unused_only":
        return proposal(reading([req(indicator=ind, targets=[busan])], unused=[U(mk), U(seoul)])), \
            "polarity_effect_lost"
    if how == "marker_ambiguity_only":
        return proposal(reading([req(indicator=ind, targets=[seoul, busan])]),
                        ambiguities=[amb([mk], "polarity", "polarity_scope")]), "polarity_effect_lost"
    if how == "negated_also_required":
        return proposal(reading([req(indicator=ind, targets=[seoul, busan])],
                                links=[link("replace", [mk], [seoul], [busan])])), "polarity_excluded_required"
    raise AssertionError(how)


@pytest.mark.parametrize("how", ["normal", "wrong_object_no_positive", "other_name_negated_too", "empty_negative",
                                 "marker_request_only", "marker_unused_only", "marker_ambiguity_only",
                                 "negated_also_required"])
def test_a_clear_negation_object_and_effect(layer, how):
    q = Q(NEG_Q, layer)
    p, code = neg_case(q, how)
    out = q.verify(p)
    if code is None:
        assert out["outcome"] == "accept" and out["issues"] == []
    else:
        assert out["outcome"] == "fallback" and code in codes(out), codes(out)


def test_a_code_sure_not_excluded_applied_as_exclusion_falls_back(layer):
    q = Q("가상대출금에서 가상서울은 제외하지 말고 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    words = [U(R(q.s("제외하지")))]
    out = q.verify(proposal(reading([req(indicator=ind)], links=[link("exclude", [R(q.s("말고"))], [seoul])],
                                    request=[R(q.s("보여줘"))], unused=words)))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_negated_exclusion"}
    ok = q.verify(proposal(reading([req(indicator=ind, targets=[seoul])], request=[R(q.s("보여줘"))],
                                   unused=words + [U(R(q.s("말고")))])))
    assert ok["outcome"] == "accept" and ok["issues"] == []  # the not-excluded words need no meaning


EXCLUDE_Q = "가상서울을 뺀 가상대출금 보여줘"


def test_a_clear_exclusion_bound_unresolved_with_its_object_required_falls_back(layer):
    q = Q(EXCLUDE_Q, layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[seoul])], [bind(q.c("exclude"), "unresolved", ["r1"])],
                                    request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_excluded_required"}
    kept = q.verify(proposal(reading([req(indicator=ind)], [bind(q.c("exclude"), "unresolved", ["r1"])],
                                     request=[R(q.s("보여줘"))])))
    assert kept["outcome"] == "accept" and codes(kept, "issues") == {"condition_unresolved"}


# ---------------------------------------------------------------- P2 (user decision 10/20 22:10): the certain part

LIST_Q = "가상서울과 가상부산을 뺀 가상대출금 보여줘"


def list_parts(q: Q):
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul, busan = R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))
    return ind, seoul, busan, R(q.s("뺀"))


def test_p2_a_both_excluded_is_kept_with_an_unclear_scope(layer):
    q = Q(LIST_Q, layer)
    ind, seoul, busan, mk = list_parts(q)
    out = q.verify(proposal(reading([req(indicator=ind)], q.all_bound(), links=[link("exclude", [mk], [seoul, busan])],
                                    request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"unclear_scope"}


def test_p2_b_the_certain_part_excluded_and_the_rest_open_is_kept(layer):
    q = Q(LIST_Q, layer)
    ind, seoul, busan, mk = list_parts(q)
    x_seoul, x_busan = q.c("exclude", 0), q.c("exclude", 1)
    out = q.verify(proposal(reading([req(indicator=ind, targets=[seoul])],
                                    [bind(x_seoul, "unresolved", ["r1"]), bind(x_busan, "requirements", ["r1"])],
                                    links=[link("exclude", [mk], [busan])], request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and {"unclear_scope", "condition_unresolved"} <= codes(out, "issues")


@pytest.mark.parametrize("how", ["required_with_link", "required_by_binding", "required_by_unresolved_binding",
                                 "positive_side"])
def test_p2_c_the_certain_part_required_or_included_falls_back(layer, how):
    q = Q(LIST_Q, layer)
    ind, seoul, busan, mk = list_parts(q)
    bound, unresolved = q.all_bound(), [bind(c["id"], "unresolved", ["r1"]) for c in q.conditions]
    if how == "required_with_link":
        p = reading([req(indicator=ind, targets=[busan])], bound, links=[link("exclude", [mk], [seoul, busan])])
        code = "polarity_excluded_required"
    elif how == "required_by_binding":
        p, code = reading([req(indicator=ind, targets=[seoul, busan])], bound), "polarity_excluded_required"
    elif how == "required_by_unresolved_binding":
        p, code = reading([req(indicator=ind, targets=[seoul, busan])], unresolved), "polarity_excluded_required"
    else:
        p = reading([req(indicator=ind, targets=[seoul, busan])], bound,
                    links=[link("exclude", [mk], [], [seoul, busan])])
        code = "polarity_inverted"
    p["request_refs"] = [R(q.s("보여줘"))]
    out = q.verify(proposal(p))
    assert out["outcome"] == "fallback" and code in codes(out), codes(out)


@pytest.mark.parametrize("how", ["link_without_it", "empty_negative", "marker_set_aside"])
def test_p2_d_the_certain_part_effect_missing_falls_back(layer, how):
    q = Q(LIST_Q, layer)
    ind, seoul, busan, mk = list_parts(q)
    x_seoul, x_busan = q.c("exclude", 0), q.c("exclude", 1)
    if how == "link_without_it":
        p, code = reading([req(indicator=ind)], q.all_bound(), links=[link("exclude", [mk], [seoul])]), \
            "polarity_target_mismatch"
    elif how == "empty_negative":
        p = reading([req(indicator=ind)], q.all_bound(), links=[link("exclude", [mk], [])])
        code = "polarity_effect_lost"
    else:  # the marker and its condition set aside (the condition is then unbound as well)
        p = reading([req(indicator=ind)], [bind(x_seoul, "requirements", ["r1"])],
                    unused=[U(R(q.s("가상부산"))), U(mk)])
        code = "polarity_effect_lost"
    p["request_refs"] = [R(q.s("보여줘"))]
    out = q.verify(proposal(p))
    assert out["outcome"] == "fallback" and code in codes(out), codes(out)


def test_p2_no_certain_part_without_a_list(layer):
    """A name not ending a list leaves the scope unclear with no certain part (only the list rule of the data file)."""
    q = Q("가상서울의 가상부산을 뺀 가상대출금 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    busan = R(q.s("가상부산"), z=q.z("가상부산", "target"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[busan])], q.all_bound(),
                                    request=[R(q.s("보여줘"))], unused=[U(R(q.s("가상서울")))])))
    assert out["outcome"] == "accept" and "unclear_scope" in codes(out, "issues")


# ---------------------------------------------------------------- fix 4: subquotes


def test_mention_spans_follow_the_mention_numbering(layer):
    q = Q("가상서울별 가상대출금", layer)
    spans = mention_spans(q.text, q.conditions, q.segments, layer)
    assert set(spans) == {m["mention_id"] for m in q.mentions} and q.local["mention_spans"] == spans
    inner = [m for m in q.mentions if m["kind"] == "name" and m["segment_ids"] == [q.s("가상서울별")]]
    assert [q.text["raw"][slice(*spans[m["mention_id"]])] for m in inner] == ["가상서울"]


@pytest.mark.parametrize("subquote, sense, want", [
    ("가상서울", True, None),  # once, and the catalog name written exactly there
    ("서울", True, "wrong_sense"),  # in the segment, but the sense is not what it quotes
    ("서울", False, "unmapped_concept"),  # no catalog name there: kept unmapped
    ("없는말", True, "subquote_not_found"),
    ("", True, "subquote_not_found"),
])
def test_a_subquote_is_read_by_what_it_quotes(layer, subquote, sense, want):
    q = Q("가상서울별 가상대출금", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    tgt = R(q.s("가상서울별"), z=q.z("가상서울", "target") if sense else None, sq=subquote)
    out = q.verify(proposal(reading([req(indicator=ind, targets=[tgt])], q.all_bound())))
    if want is None:
        assert out["outcome"] == "accept" and out["issues"] == []
    elif want == "unmapped_concept":
        assert out["outcome"] == "accept" and codes(out, "issues") == {want}
    else:
        assert out["outcome"] == "fallback" and codes(out) == {want}


def test_a_repeated_or_multi_segment_subquote_falls_back(layer):
    q = Q("가상가계대출 보여줘", layer)
    ind = R(q.s("가상가계대출"), z=q.z("가상가계대출", "indicator"), sq="가")
    assert codes(q.verify(proposal(reading([req(indicator=ind)], request=[R(q.s("보여줘"))])))) == {"subquote_repeated"}
    two = R(q.s("가상가계대출"), q.s(" "), sq="대출")
    out = q.verify(proposal(reading([req(indicator=two)], request=[R(q.s("보여줘"))])))
    assert codes(out) == {"subquote_multi_segment"}


def test_the_whole_name_sense_is_not_handed_to_a_part(layer):
    q = Q("가상예금(말잔) 보여줘", layer)
    s1, b1 = q.s("가상예금(말잔)"), q.c("basis")
    name = R(s1, z=q.z("가상예금(말잔)", "indicator"))
    basis = [R(s1, sq="말잔")]
    content = [bind(b1, "name_content", names=[R(s1)])]
    both = q.verify(proposal(reading([req(indicator=name, basis=basis)], content, request=[R(q.s("보여줘"))])))
    assert both["outcome"] == "accept" and both["issues"] == []  # two concepts of one segment, each quoted
    part = q.verify(proposal(reading([req(indicator=R(s1, z=name["sense_id"], sq="가상예금"), basis=basis)], content,
                                     request=[R(q.s("보여줘"))])))
    assert part["outcome"] == "fallback" and codes(part) == {"wrong_sense"}
    unmapped = q.verify(proposal(reading([req(indicator=R(s1, sq="가상예금"), basis=basis)], content,
                                         request=[R(q.s("보여줘"))])))
    assert unmapped["outcome"] == "accept" and codes(unmapped, "issues") == {"unmapped_concept"}
    one_missing = q.verify(proposal(reading([req(indicator=None, basis=basis)], content, request=[R(q.s("보여줘"))])))
    assert one_missing["outcome"] == "fallback" and codes(one_missing) == {"segment_unused"}


def test_a_part_quote_does_not_keep_the_rest_of_its_segment(layer):
    q = Q("가상대출금 모르는말 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[R(q.s("모르는말"), sq="모르")])],
                                    request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "fallback" and codes(out) == {"segment_unused"}
    rest = q.verify(proposal(reading([req(indicator=ind, targets=[R(q.s("모르는말"), sq="모르")])],
                                     request=[R(q.s("보여줘"))], unused=[U(R(q.s("모르는말"), sq="는말"), "unmapped")])))
    assert rest["outcome"] == "accept"


# ---------------------------------------------------------------- fix 5: meaningful words set aside

WORD_Q = "가상대출금 모르는말 보여줘"


@pytest.mark.parametrize("where", ["unmapped", "unresolved", "unattached", "request"])
def test_an_unmapped_word_keeps_an_unresolved_finding_whatever_it_is_called(layer, where):
    q = Q(WORD_Q, layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    word = R(q.s("모르는말"))
    if where == "request":
        p = reading([req(indicator=ind)], request=[word, R(q.s("보여줘"))])
    else:
        p = reading([req(indicator=ind)], request=[R(q.s("보여줘"))], unused=[U(word, where)])
    out = q.verify(proposal(p))
    assert out["outcome"] == "accept"
    assert issues_on(out, q.s("모르는말")) & {"unmapped_concept", "unused_unresolved"}
    assert q.s("모르는말") in out["scopes"]["segments"]


def test_request_and_grammar_words_need_no_finding(layer):
    q = Q("가상대출금에 대해 좀 알려줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    for p in (reading([req(indicator=ind)], request=[R(q.s("대해")), R(q.s("알려줘"))]),
              reading([req(indicator=ind)], request=[R(q.s("알려줘"))], unused=[U(R(q.s("대해")))])):
        out = q.verify(proposal(p))
        assert out["outcome"] == "accept" and out["issues"] == []


# ---------------------------------------------------------------- section 6: question structures


def test_a_and_b_of_x_is_one_requirement_and_a_never_becomes_the_indicator(layer):
    q = Q("가상서울과 가상부산의 가상대출금 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul, busan = R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[seoul, busan])], q.all_bound(),
                                    request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and out["issues"] == []
    promoted = q.verify(proposal(reading([req(indicator=seoul, targets=[busan])], q.all_bound(),
                                         request=[R(q.s("보여줘"))], unused=[U(ind)])))
    assert promoted["outcome"] == "fallback" and codes(promoted) == {"target_as_indicator"}


THREE_Q = "가상대출금을 가상서울·가상부산·가상대구로 나눠서 보여줘"


@pytest.mark.parametrize("how", ["all_three", "one_dropped", "one_repeated", "one_as_indicator", "one_unconfirmed"])
def test_x_divided_by_three_values(layer, how):
    q = Q(THREE_Q, layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    vals = [R(q.s(n), z=q.z(n, "target")) for n in ("가상서울", "가상부산", "가상대구")]
    marks = [R(q.s("·", 0)), R(q.s("·", 1)), R(q.s("로 나눠서"))]
    bindings = q.all_bound()
    indicator, values, unused = ind, vals, []
    if how == "one_dropped":
        values = vals[:2]
        bindings = [b for b in bindings if b["condition_id"] != q.c("breakdown", 2)]
    elif how == "one_repeated":
        values = vals + [vals[0]]
    elif how == "one_as_indicator":
        indicator, values, unused = vals[2], vals[:2], [U(ind)]
    elif how == "one_unconfirmed":
        values = vals[:2] + [dict(vals[2], sense_id=None)]
    out = q.verify(proposal(reading([req(indicator=indicator, breakdowns=[bd(values, marks)])], bindings,
                                    request=[R(q.s("보여줘"))], unused=unused)))
    if how == "all_three":
        assert out["outcome"] == "accept" and out["issues"] == []
    elif how == "one_dropped":
        assert out["outcome"] == "fallback" and {"segment_unused", "condition_missing"} <= codes(out)
    elif how == "one_repeated":
        assert out["outcome"] == "accept" and codes(out, "issues") == {"breakdown_incomplete"}
    elif how == "one_as_indicator":
        assert out["outcome"] == "fallback" and codes(out) == {"target_as_indicator"}
    else:
        assert out["outcome"] == "accept" and codes(out, "issues") == {"breakdown_value_unconfirmed"}


def test_a_middle_dot_inside_a_catalog_name_is_no_list(layer):
    q = Q("가상예·적금담보대출 보여줘", layer)
    (s1,) = [x["segment_id"] for x in q.segments if x["kind"] == "name"]
    assert all(s["marker"] is None for s in q.payload["segments"])  # the name is one protected segment
    name = R(s1, z=q.z("가상예·적금담보대출", "indicator"))
    bound = [bind(c["id"], "requirements", ["r1"]) for c in q.conditions]
    out = q.verify(proposal(reading([req(indicator=name)], bound, request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and out["issues"] == []
    split = q.verify(proposal(reading(
        [req(indicator=name, breakdowns=[bd([R(s1, sq="가상예"), R(s1, sq="적금담보대출")], [R(s1, sq="·")])])],
        bound, request=[R(q.s("보여줘"))])))
    assert split["outcome"] == "accept"
    assert {"breakdown_incomplete", "breakdown_value_unconfirmed"} <= codes(split, "issues")
    two = q.verify(proposal(reading([req("r1", indicator=R(s1, sq="가상예")), req("r2", indicator=R(s1, sq="적금담보대출"))],
                                    [bind(c["id"], "requirements", ["r1", "r2"]) for c in q.conditions],
                                    request=[R(q.s("보여줘"))])))
    assert two["outcome"] == "accept" and codes(two, "issues") == {"unmapped_concept"}  # never a clean split
    real = Q(THREE_Q, layer)
    assert [s["marker"] for s in real.payload["segments"] if s["text"] == "·"] == ["list", "list"]


def test_one_requirement_over_many_tables_is_kept_as_one(layer):
    q = Q("가상서울 가상대출금 가상예금액 보여줘", layer)
    seoul = q.z("가상서울", "target")
    assert len({r["table_id"] for r in q.local["senses"][seoul]["rows"]}) == 2  # every table candidate kept locally
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    dep = R(q.s("가상예금액"), z=q.z("가상예금액", "indicator"))
    common = [bind(c["id"]) for c in q.conditions]
    one = reading([req("r1", indicator=ind, targets=[R(q.s("가상서울"), z=seoul)]),
                   req("r2", indicator=dep, targets=[R(q.s("가상서울"), z=seoul)])], common, request=[R(q.s("보여줘"))])
    out = q.verify(proposal(one))
    assert out["outcome"] == "accept" and out["issues"] == []
    shared = q.verify(proposal(reading([req(indicator=ind, targets=[R(q.s("가상서울"), z=seoul)])], common,
                                       request=[R(q.s("보여줘"))], unused=[U(dep, "unresolved")])))
    assert shared["outcome"] == "accept"  # a requirement is not multiplied by the tables of its candidates


def test_a_kept_and_a_dropped_alternative_with_the_new_checks(layer):
    q = Q(NEG_Q, layer)
    good, _ = neg_case(q, "normal")
    bad, _ = neg_case(q, "marker_unused_only")
    out = q.verify(proposal(good["primary"], bad["primary"]))
    assert out["outcome"] == "accept" and out["alternative"] == "dropped"
    assert codes(out, "alternative_issues") == {"polarity_effect_lost"} and out["issues"] == []
    out = q.verify(proposal(bad["primary"], good["primary"]))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_effect_lost"}


def test_the_rules_path_reads_the_same_mentions(layer):
    """The mention spans come from the same count as the mentions (build_mentions is unchanged)."""
    for question in (SPLIT_Q, LIST_Q, "가상서울별 가상대출금"):
        a = interpret_question(question, layer)
        q = Q(question, layer)
        assert a["mentions"] == q.mentions and q.local["mention_spans"]
