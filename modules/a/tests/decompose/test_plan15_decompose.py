"""Plan 15 r3 step 3: model input, per-question schema and code verification of the decomposition proposal (4-6).

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposals. No network,
no model, nothing wired into the rules path.
"""

from __future__ import annotations

import copy
import json

import pytest
from catalog_synth import write_inputs, write_raw

from claview_a.catalog.layer import load_layer
from claview_a.contract import shapes
from claview_a.contract.check import check_verify_result
from claview_a.interpret import decompose_payload as DP
from claview_a.interpret.conditions import find_conditions
from claview_a.interpret.decompose_payload import build_payload, is_input_limit, prompt_text
from claview_a.interpret.decompose_schema import build_schema, lint_schema, requirement_slots, validate
from claview_a.interpret.decompose_verify import verify
from claview_a.interpret.mentions import build_mentions
from claview_a.interpret.pipeline import interpret_question
from claview_a.interpret.segments import build_segments
from claview_a.interpret.text import normalize_text

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


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_decompose")
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

    def verify(self, proposal) -> dict:
        out = verify(proposal, self.payload, self.local, self.text, self.conditions, self.segments, self.mentions)
        seg_ids = {x["segment_id"] for x in self.segments}
        cond_ids = {x["id"] for x in self.conditions}
        req_ids = {r["id"] for r in proposal["primary"]["requirements"]} if isinstance(proposal, dict) else None
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


def codes(result: dict, field: str = "fallback_reasons") -> set[str]:
    return {it["code"] for it in result[field]}


# ---------------------------------------------------------------- the 5.3 example on the synthetic catalog

SPLIT_Q = "가상대출금을 가상서울·가상부산으로 나눠서 분기별로 보여줘"


def split_reading(q: Q) -> dict:
    """X = 가상대출금, A·B = 가상서울·가상부산 (5.3: one requirement broken down by two values, the frequency on the
    question, the divide condition on r1; the listed names are breakdown conditions of r1)."""
    return reading(
        [req(indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")), breakdowns=[bd(
            [R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))],
            [R(q.s("·")), R(q.s("으로 나눠서"))])])],
        [bind(q.c("frequency")), bind(q.c("function"), "requirements", ["r1"]),
         bind(q.c("breakdown", 0), "requirements", ["r1"]), bind(q.c("breakdown", 1), "requirements", ["r1"])],
        request=[R(q.s("보여줘"))])


def test_the_example_proposal_validates_and_is_accepted(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    assert validate(p, build_schema(q.payload)) == []
    out = q.verify(p)
    assert out["outcome"] == "accept" and out["issues"] == [] and out["assumption_seed"] is False
    assert out["alternative"] == "absent"


# ---------------------------------------------------------------- payload (4)

FORBIDDEN_FIELDS = {"table_id", "obj_id", "value_id", "certain", "judgement", "requirement_draft", "pairs",
                    "fragments", "terms", "interpretation", "failed_conditions", "case_id", "answer"}


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


@pytest.mark.parametrize("question", [SPLIT_Q, "가상서울을 뺀 가상대출금 보여줘", "가상예금(말잔) 보여줘",
                                      "가상대출금 대비 가상예금액 비율", "가상대출금 지역별로 2024년 1월부터"])
def test_payload_holds_no_codes_and_no_rule_results(layer, question):
    q = Q(question, layer)
    codes_ = {x for s in q.local["senses"].values() for r in s["rows"] for x in (r["table_id"], r["obj_id"],
                                                                                  r["value_id"]) if x}
    assert codes_
    found = set(_strings(q.payload))
    assert not found & codes_
    assert not found & FORBIDDEN_FIELDS
    rules = interpret_question(question, layer)
    assert rules["judgement"] is not None  # the rule path still runs as before (nothing of it is sent)
    assert q.payload["raw"] == question
    assert [s["text"] for s in q.payload["segments"]] == [s["quote"] for s in q.segments]


def test_local_keeps_every_raw_candidate_once(layer):
    q = Q("가상서울 가상대출금과 가상서울 가상가계대출", layer)
    rows = [r for s in q.local["senses"].values() for r in s["rows"]]
    for m in q.mentions:
        for c in m["candidates"]:
            assert c in rows
    keys = [(r["table_id"], r["obj_id"], r["value_id"], r["role"]) for r in rows]
    assert len(keys) == len(set(keys))
    seoul = [r for r in q.payload["candidate_summary"] if r["name"] == "가상서울"]
    assert len({r["mention_id"] for r in seoul}) == 2 and len({r["sense_id"] for r in seoul}) == 1
    assert len(q.local["senses"][seoul[0]["sense_id"]]["rows"]) == 2  # two tables, one sense


def test_a_table_name_and_its_item_row_are_two_senses(layer):
    q = Q("가상대출금 보여줘", layer)
    roles = {r["role"] for r in q.payload["candidate_summary"] if r["name"] == "가상대출금"}
    assert roles == {"indicator", "table_name"}
    assert q.z("가상대출금", "indicator") != q.z("가상대출금", "table_name")


def test_relations_among_senses(layer):
    q = Q("가상전국 가상서울 가상부산 가상대출금", layer)
    rel = {(r["kind"], tuple(r["sense_ids"])) for r in q.payload["relations"]}
    whole, seoul, busan = q.z("가상전국", "target"), q.z("가상서울", "target"), q.z("가상부산", "target")
    table = q.z("가상대출금", "table_name")
    assert ("parent_child", (whole, seoul)) in rel and ("parent_child", (whole, busan)) in rel
    assert ("sibling", tuple(sorted((seoul, busan), key=lambda z: int(z[1:])))) in rel
    assert ("table_value", (table, seoul)) in rel
    assert all(k in DP.RELATION_KINDS for k, _ in rel)


def test_condition_rows(layer):
    q = Q("가상대출금 2024년 1월부터 분기별로 보여줘", layer)
    rows = {c["kind"]: c for c in q.payload["conditions"]}
    assert rows["period"]["extract_status"] == "explicit" and rows["frequency"]["extract_status"] is None
    assert set(rows["period"]["attrs"]) <= set(DP.POLICY["condition_attrs"]["period"])
    assert "extract_status" not in rows["period"]["attrs"]
    assert rows["frequency"]["attrs"] == {"value": "Q", "anchor": None}
    assert rows["frequency"]["segment_ids"] == [q.s("분기별")]


def test_marker_classes_on_segments(layer):
    q = Q("가상서울 말고 가상부산 가상대출금", layer)
    marks = {s["text"]: s["marker"] for s in q.payload["segments"]}
    assert marks["말고"] == "negation" and marks["가상서울"] is None


def test_senses_over_the_limit_are_flagged_and_kept_locally(layer, monkeypatch):
    monkeypatch.setitem(DP.LIMITS, "senses_per_mention", 1)
    q = Q("가상대출금 보여줘", layer)
    assert q.payload["truncation"]["senses_omitted"] is True
    (row,) = [r for r in q.payload["candidate_summary"] if r["name"] == "가상대출금"]
    assert row["role"] == "indicator"  # the policy order keeps the indicator sense first
    (mid,) = q.local["mention_senses"]
    assert len(q.local["mention_senses"][mid]) == 2 and len(q.local["mention_kept"][mid]) == 1


def test_too_many_segments_give_input_limit(layer, monkeypatch):
    monkeypatch.setitem(DP.LIMITS, "segments", 3)
    q = Q(SPLIT_Q, layer)
    assert is_input_limit(q.payload) and q.payload["input_limit"]["reason"] == "segments"
    assert q.local["senses"]  # the local table is still built
    with pytest.raises(ValueError):
        build_schema(q.payload)
    out = q.verify(proposal(reading([req()])))
    assert out["outcome"] == "fallback" and codes(out) == {"limit_exceeded"}


def test_descriptions_shrink_before_input_limit(layer, monkeypatch):
    full = Q(SPLIT_Q, layer).payload
    size = len(json.dumps(full, ensure_ascii=False))
    monkeypatch.setitem(DP.LIMITS, "payload_chars", size - 1)
    q = Q(SPLIT_Q, layer)
    assert not is_input_limit(q.payload)
    # plan 19 stage 5 (shrink order of decompose_policy.json): the match paths go first, the condition attributes last
    shrunk = q.payload["truncation"]["shrunk"]
    assert shrunk[0] == "candidate_paths" and "condition_attrs" not in shrunk
    assert all(r["path"] is None for r in q.payload["candidate_summary"])
    assert [c["attrs"] for c in q.payload["conditions"]] == [c["attrs"] for c in full["conditions"]]
    assert q.payload["raw"] == SPLIT_Q and len(q.payload["segments"]) == len(full["segments"])
    monkeypatch.setitem(DP.LIMITS, "payload_chars", 50)
    q = Q(SPLIT_Q, layer)
    assert is_input_limit(q.payload) and q.payload["input_limit"]["reason"] == "payload_chars"


def test_an_empty_question_is_an_input_limit(layer):
    for question in ("", "   "):
        assert is_input_limit(Q(question, layer).payload)


def test_prompt_states_the_task_with_placeholders_only():
    text = prompt_text()
    assert "X" in text and "A" in text and "B" in text
    assert "가상" not in text
    for word in ("지표", "대상", "조건", "부정", "제외", "요구"):
        assert word in text


# ---------------------------------------------------------------- schema (5)


def test_schema_follows_the_rules_of_5_1(layer):
    for question in (SPLIT_Q, "가상서울 보여줘", "모르는말 보여줘", "가상대출금 대비 가상예금액 비율"):
        schema = build_schema(Q(question, layer).payload)
        assert lint_schema(schema) == []
        text = json.dumps(schema)
        for word in ("table_id", "obj_id", "value_id", "confidence", "span", "start", "end"):
            assert f'"{word}"' not in text


def test_schema_enumerations_are_per_question(layer):
    q = Q(SPLIT_Q, layer)
    d = build_schema(q.payload)["$defs"]
    assert d["Ref"]["properties"]["segment_ids"]["items"]["enum"] == [s["segment_id"] for s in q.segments]
    assert d["ConditionBinding"]["properties"]["condition_id"]["enum"] == [c["id"] for c in q.conditions]
    assert d["Requirement"]["properties"]["id"]["enum"] == requirement_slots() == [f"r{n}" for n in range(1, 13)]
    senses = d["Ref"]["properties"]["sense_id"]["anyOf"][0]["enum"]
    assert set(senses) == {r["sense_id"] for r in q.payload["candidate_summary"]}


def test_empty_id_sets_give_no_empty_enum(layer):
    q = Q("모르는말 보여줘", layer)  # no condition, no sense, no relation condition
    d = build_schema(q.payload)["$defs"]
    assert d["Ref"]["properties"]["sense_id"] == {"type": "null"}
    assert d["ConditionBinding"]["properties"]["condition_id"] == {"type": "null"}
    assert d["Reading"]["properties"]["condition_bindings"]["maxItems"] == 0
    assert d["Reading"]["properties"]["relations"]["maxItems"] == 0
    assert d["Ambiguity"]["properties"]["condition_ids"]["maxItems"] == 0
    assert lint_schema(build_schema(q.payload)) == []
    ok = proposal(reading([req(indicator=None)], unused=[{"ref": R(q.s("모르는말")), "reason": "unmapped"}],
                          request=[R(q.s("보여줘"))]))
    assert validate(ok, build_schema(q.payload)) == []
    bad = copy.deepcopy(ok)
    bad["primary"]["condition_bindings"] = [bind("F1")]
    assert validate(bad, build_schema(q.payload))


def test_schema_fields_are_the_contract_shapes(layer):
    d = build_schema(Q(SPLIT_Q, layer).payload)["$defs"]
    pairs = {"Ref": shapes.ProposalRef, "Breakdown": shapes.ProposalBreakdown, "Requirement": shapes.RequirementProposal,
             "ConditionBinding": shapes.ConditionBinding, "Relation": shapes.RelationProposal,
             "PolarityLink": shapes.PolarityLink, "UnusedRef": shapes.UnusedRef, "Ambiguity": shapes.Ambiguity,
             "Reading": shapes.Reading}
    for name, tp in pairs.items():
        assert d[name]["required"] == list(tp.__annotations__), name


@pytest.mark.parametrize("how", ["extra_top", "extra_ref", "missing", "unknown_segment", "unknown_sense",
                                 "unknown_condition", "unknown_slot", "bad_scope", "too_many", "no_requirement"])
def test_schema_rejects(layer, how):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    r1 = p["primary"]["requirements"][0]
    if how == "extra_top":
        p["confidence"] = 1
    elif how == "extra_ref":
        r1["indicator"]["start"] = 0
    elif how == "missing":
        del r1["context"]
    elif how == "unknown_segment":
        r1["indicator"]["segment_ids"] = ["s999"]
    elif how == "unknown_sense":
        r1["indicator"]["sense_id"] = "z999"
    elif how == "unknown_condition":
        p["primary"]["condition_bindings"][0]["condition_id"] = "F9"
    elif how == "unknown_slot":
        r1["id"] = "r13"
    elif how == "bad_scope":
        p["primary"]["condition_bindings"][0]["scope"] = "common"
    elif how == "too_many":
        p["primary"]["requirements"] = [dict(r1, id=f"r{n}") for n in range(1, 14)]
    elif how == "no_requirement":
        p["primary"]["requirements"] = []
    assert validate(p, build_schema(q.payload)) != []


# ---------------------------------------------------------------- verification: fallback rows (6.1)


def test_not_json_and_format_violations_fall_back(layer):
    q = Q(SPLIT_Q, layer)
    out = verify("{not json", q.payload, q.local, q.text, q.conditions, q.segments, q.mentions)
    assert out["outcome"] == "fallback" and codes(out) == {"format"}
    p = proposal(split_reading(q))
    p["primary"]["requirements"][0]["note"] = "x"
    assert codes(q.verify(p)) == {"format"}
    p = proposal(split_reading(q))
    out = verify(json.dumps(p, ensure_ascii=False), q.payload, q.local, q.text, q.conditions, q.segments, q.mentions)
    assert out["outcome"] == "accept"


def test_unknown_ids_fall_back(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    p["primary"]["requirements"][0]["indicator"]["segment_ids"] = ["s999"]
    assert codes(q.verify(p)) == {"unknown_id"}
    p = proposal(split_reading(q))
    p["primary"]["condition_bindings"][1]["requirement_ids"] = ["r2"]  # a slot no requirement of the reading has
    out = q.verify(p)
    assert out["outcome"] == "fallback" and "unknown_id" in codes(out)


def test_a_sense_of_another_segment_falls_back(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    p["primary"]["requirements"][0]["indicator"]["sense_id"] = q.z("가상서울", "target")
    out = q.verify(p)
    assert out["outcome"] == "fallback" and codes(out) == {"wrong_sense"}
    assert out["fallback_reasons"][0]["requirement_ids"] == ["r1"]


@pytest.mark.parametrize("subquote, code", [("대출", "wrong_sense"), ("없는말", "subquote_not_found"),
                                            ("가", "subquote_repeated"), ("", "subquote_not_found")])
def test_subquote_once_zero_twice(layer, subquote, code):
    # "가" twice in the name, "대출" once; the whole name's sense is no sense of the part "대출" (user approval
    # 10/20 22:48: a subquote is read by what it quotes, plan 3.4)
    q = Q("가상가계대출 보여줘", layer)
    p = proposal(reading([req(indicator=R(q.s("가상가계대출"), z=q.z("가상가계대출", "indicator"), sq=subquote))],
                         request=[R(q.s("보여줘"))]))
    out = q.verify(p)
    if code is None:
        assert out["outcome"] == "accept"
    else:
        assert out["outcome"] == "fallback" and codes(out) == {code}


def test_a_subquote_over_two_segments_and_a_gap_fall_back(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    p["primary"]["request_refs"] = [R(q.s(" ", 2), q.s("보여줘"), sq="보여")]
    assert codes(q.verify(p)) == {"subquote_multi_segment"}
    p = proposal(split_reading(q))
    p["primary"]["request_refs"] = [R(q.s("분기별"), q.s("보여줘"))]
    assert "ref_not_contiguous" in codes(q.verify(p))


def test_an_unused_meaningful_segment_falls_back(layer):
    q = Q("가상대출금 모르는말 좀 보여줘", layer)
    base = [req(indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")))]
    out = q.verify(proposal(reading(base, request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "fallback" and codes(out) == {"segment_unused"}
    assert out["fallback_reasons"][0]["segment_ids"] == [q.s("모르는말")]  # the filler word needs no use
    ok = q.verify(proposal(reading(base, request=[R(q.s("보여줘"))],
                                   unused=[{"ref": R(q.s("모르는말")), "reason": "unmapped"}])))
    assert ok["outcome"] == "accept" and codes(ok, "issues") == {"unmapped_concept"}


def test_a_missing_condition_falls_back(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    p["primary"]["condition_bindings"] = p["primary"]["condition_bindings"][1:]
    out = q.verify(p)
    assert codes(out) == {"condition_missing", "segment_unused"}  # its words are used nowhere either
    (missing,) = [it for it in out["fallback_reasons"] if it["code"] == "condition_missing"]
    assert missing["condition_ids"] == [q.c("frequency")] and missing["segment_ids"] == [q.s("분기별")]
    p["primary"]["unused_refs"] = [{"ref": R(q.s("분기별")), "reason": "unattached"}]
    assert codes(q.verify(p)) == {"condition_missing"}


def test_a_conflicting_or_invalid_binding_falls_back(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    p["primary"]["condition_bindings"].append(bind(q.c("frequency"), "requirements", ["r1"]))
    assert codes(q.verify(p)) == {"condition_conflict"}
    p = proposal(split_reading(q))
    p["primary"]["condition_bindings"][0] = bind(q.c("frequency"), "requirements", [])
    assert codes(q.verify(p)) == {"condition_binding_invalid"}
    p = proposal(split_reading(q))
    p["primary"]["condition_bindings"][0] = bind(q.c("frequency"), "name_content",
                                                 names=[R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))])
    assert codes(q.verify(p)) == {"condition_binding_invalid"}
    p = proposal(split_reading(q))
    p["primary"]["condition_bindings"].append(dict(p["primary"]["condition_bindings"][0]))  # the same binding twice
    assert q.verify(p)["outcome"] == "accept"


def test_a_condition_inside_a_name_bound_as_name_content(layer):
    q = Q("가상예금(말잔) 보여줘", layer)
    name = R(q.s("가상예금(말잔)"), z=q.z("가상예금(말잔)", "indicator"))
    out = q.verify(proposal(reading([req(indicator=name)], [bind(q.c("basis"), "name_content", names=[name])],
                                    request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and out["issues"] == []


def test_a_sure_target_used_as_indicator_falls_back(layer):
    q = Q("가상서울 보여줘", layer)
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    out = q.verify(proposal(reading([req(indicator=seoul)], request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "fallback" and codes(out) == {"target_as_indicator"}
    out = q.verify(proposal(reading([req(indicator=dict(seoul, sense_id=None))], request=[R(q.s("보여줘"))])))
    assert codes(out) == {"target_as_indicator"}
    # a role ambiguity the model names is no ground (user approval 10/20 22:48, plan 6.1 role row): still a fallback
    amb = {"refs": [R(q.s("가상서울"))], "kind": "role", "condition_ids": [], "reason_code": "role_unclear"}
    out = q.verify(proposal(reading([req(indicator=seoul)], request=[R(q.s("보여줘"))]), ambiguities=[amb]))
    assert out["outcome"] == "fallback" and codes(out) == {"target_as_indicator"}


def test_request_refs_may_not_swallow_names_or_conditions(layer):
    q = Q("가상대출금 가상서울 분기별로 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    out = q.verify(proposal(reading([req(indicator=ind)], [bind(q.c("frequency"))],
                                    request=[R(q.s("가상서울")), R(q.s("보여줘"))])))
    assert out["outcome"] == "fallback" and codes(out) == {"request_swallow"}
    out = q.verify(proposal(reading([req(indicator=ind, targets=[R(q.s("가상서울"), z=q.z("가상서울", "target"))])],
                                    request=[R(q.s("분기별"), q.s("로")), R(q.s("보여줘"))])))
    assert codes(out) == {"request_swallow", "condition_missing"}
    ok = q.verify(proposal(reading([req(indicator=ind, targets=[R(q.s("가상서울"), z=q.z("가상서울", "target"))])],
                                   [bind(q.c("frequency"))], request=[R(q.s("분기별"), q.s("로")), R(q.s("보여줘"))])))
    assert ok["outcome"] == "accept"  # a bound condition written with the request keeps its effect


def test_too_many_requirements_fall_back(layer):
    q = Q(SPLIT_Q, layer)
    p = proposal(split_reading(q))
    r1 = p["primary"]["requirements"][0]
    p["primary"]["requirements"] = [dict(r1, id=f"r{n}") for n in range(1, 13)] + [dict(r1, id="r12")]
    assert "limit_exceeded" in codes(q.verify(p))


# ---------------------------------------------------------------- verification: polarity (6.2)

EXCLUDE_Q = "가상서울을 뺀 가상대출금 보여줘"


def exclude_base(q: Q, targets=(), links=(), x_scope="requirements") -> dict:
    return proposal(reading([req(indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")), targets=targets)],
                            [bind(q.c("exclude"), x_scope, ["r1"] if x_scope == "requirements" else [])],
                            links=links, request=[R(q.s("보여줘"))]))


def test_an_exclusion_with_its_marker_is_accepted(layer):
    q = Q(EXCLUDE_Q, layer)
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    out = q.verify(exclude_base(q, links=[link(markers=[R(q.s("뺀"))], negative=[seoul], rids=["r1"])]))
    assert out["outcome"] == "accept" and out["issues"] == []


def test_polarity_case_1_no_marker(layer):
    q = Q(EXCLUDE_Q, layer)
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    out = q.verify(exclude_base(q, links=[link(markers=[], negative=[seoul])]))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_no_marker"}
    out = q.verify(exclude_base(q, links=[link(markers=[R(q.s("가상대출금"))], negative=[seoul])]))
    assert codes(out) == {"polarity_no_marker"}


@pytest.mark.parametrize("question, marker", [("가상대출금에서 가상서울을 제외하지 않고 보여줘", "제외하지"),
                                              ("가상대출금에서 가상서울을 빼지 말고 보여줘", "말고")])
def test_polarity_case_2_a_sure_not_excluded_applied_as_exclusion(layer, question, marker):
    q = Q(question, layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    rest = [R(x["segment_id"]) for x in q.segments if x["kind"] == "word"]
    out = q.verify(proposal(reading([req(indicator=ind)], links=[link(markers=[R(q.s(marker))], negative=[seoul])],
                                    request=[R(q.s("보여줘"))], unused=[{"ref": r, "reason": "unattached"}
                                                                        for r in rest])))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_negated_exclusion"}
    # the not-excluded name kept as a target: accepted
    ok = q.verify(proposal(reading([req(indicator=ind, targets=[seoul])], request=[R(q.s("보여줘"))],
                                   unused=[{"ref": r, "reason": "unattached"} for r in rest]
                                   + [{"ref": R(x["segment_id"]), "reason": "unattached"} for x in q.segments
                                      if x["kind"] == "marker"])))
    assert ok["outcome"] == "accept"


def test_polarity_case_2_the_not_excluded_object_excluded_by_another_marker(layer):
    q = Q("가상대출금에서 가상서울을 빼지 말고 가상부산은 빼줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    marker = [x["segment_id"] for x in q.segments if x["kind"] == "marker" and x["quote"] != "말고"][-1]
    bindings = [bind(c["id"], "requirements", ["r1"]) for c in q.conditions]
    unused = [{"ref": R(x["segment_id"]), "reason": "unattached"} for x in q.segments if x["kind"] in ("word", "marker")]
    out = q.verify(proposal(reading([req(indicator=ind)], bindings, links=[link(markers=[R(marker)], negative=[seoul])],
                                    unused=unused)))
    assert out["outcome"] == "fallback" and "polarity_negated_exclusion" in codes(out)


def test_polarity_case_3_a_clearly_excluded_name_also_required(layer):
    q = Q(EXCLUDE_Q, layer)
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    out = q.verify(exclude_base(q, targets=[seoul],
                                links=[link(markers=[R(q.s("뺀"))], negative=[seoul], rids=["r1"])]))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_excluded_required"}
    # through the bound exclusion condition alone (no link)
    out = q.verify(exclude_base(q, targets=[seoul]))
    assert codes(out) == {"polarity_excluded_required"}
    out = q.verify(exclude_base(q, targets=[seoul], x_scope="question"))
    assert codes(out) == {"polarity_excluded_required"}


def test_polarity_explicit_exclusion_applied_inversely(layer):
    q = Q(EXCLUDE_Q, layer)
    seoul = R(q.s("가상서울"), z=q.z("가상서울", "target"))
    out = q.verify(exclude_base(q, links=[link(markers=[R(q.s("뺀"))], negative=[], positive=[seoul])]))
    assert out["outcome"] == "fallback" and "polarity_inverted" in codes(out)


def test_polarity_case_4_a_marker_only_as_request(layer):
    q = Q("가상서울 말고 가상부산 가상대출금", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul, busan = R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[busan])], request=[R(q.s("말고"))],
                                    unused=[{"ref": seoul, "reason": "unattached"}])))
    assert out["outcome"] == "fallback" and codes(out) == {"polarity_marker_request_only"}
    ok = q.verify(proposal(reading([req(indicator=ind, targets=[busan])],
                                   links=[link("replace", [R(q.s("말고"))], [seoul], [busan], ["r1"])])))
    assert ok["outcome"] == "accept" and ok["issues"] == []
    swapped = q.verify(proposal(reading([req(indicator=ind, targets=[busan])],
                                        links=[link("replace", [R(q.s("말고"))], [busan], [seoul], ["r1"])])))
    assert swapped["outcome"] == "fallback" and codes(swapped) == {"polarity_inverted"}


def test_a_marker_kept_by_its_bound_condition_is_no_request_only(layer):
    q = Q(EXCLUDE_Q, layer)
    p = exclude_base(q)
    p["primary"]["request_refs"].append(R(q.s("뺀")))
    assert q.verify(p)["outcome"] == "accept"


def test_polarity_case_5_unclear_scope_is_kept_with_an_issue(layer):
    q = Q("가상서울과 가상부산을 뺀 가상대출금 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    seoul, busan = R(q.s("가상서울"), z=q.z("가상서울", "target")), R(q.s("가상부산"), z=q.z("가상부산", "target"))
    both = reading([req(indicator=ind)], [bind(c["id"], "requirements", ["r1"]) for c in q.conditions],
                   links=[link(markers=[R(q.s("뺀"))], negative=[seoul, busan], rids=["r1"])],
                   request=[R(q.s("보여줘"))])
    only_b = reading([req(indicator=ind, targets=[seoul])],
                     [bind(q.c("exclude", 0), "unresolved", ["r1"]), bind(q.c("exclude", 1), "requirements", ["r1"])],
                     links=[link(markers=[R(q.s("뺀"))], negative=[busan], rids=["r1"])], request=[R(q.s("보여줘"))])
    amb = {"refs": [R(q.s("가상서울"), q.s("과"), q.s(" ", 0), q.s("가상부산"))], "kind": "polarity",
           "condition_ids": [], "reason_code": "polarity_scope"}
    out = q.verify(proposal(both, only_b, [amb]))
    assert out["outcome"] == "accept" and "unclear_scope" in codes(out, "issues")
    assert out["alternative"] == "kept" and "unclear_scope" in codes(out, "alternative_issues")
    assert out["assumption_seed"] is True
    marker = q.s("뺀")
    assert "unclear_scope" in out["scopes"]["segments"][marker]
    assert "unclear_scope" in out["scopes"]["requirements"]["r1"]
    # a clear scope gives no such issue
    clear = Q(EXCLUDE_Q, layer)
    assert clear.verify(exclude_base(clear))["issues"] == []


# ---------------------------------------------------------------- verification: accepted with findings


def test_unmapped_concepts_and_unconfirmed_values_are_kept(layer):
    q = Q("가상대출금 모르는말 보여줘", layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    out = q.verify(proposal(reading([req(indicator=ind, targets=[R(q.s("모르는말"))])], request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"unmapped_concept"}
    assert out["scopes"]["requirements"] == {"r1": ["unmapped_concept"]}
    out = q.verify(proposal(reading([req(indicator=ind, breakdowns=[bd([R(q.s("모르는말"))])])],
                                    request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"breakdown_value_unconfirmed"}
    out = q.verify(proposal(reading([req(indicator=R(q.s("모르는말")))], request=[R(q.s("보여줘"))],
                                    unused=[{"ref": ind, "reason": "unresolved"}])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"unmapped_concept", "unused_unresolved"}
    out = q.verify(proposal(reading([req(indicator=None)], request=[R(q.s("보여줘"))],
                                    unused=[{"ref": ind, "reason": "unresolved"},
                                            {"ref": R(q.s("모르는말")), "reason": "unmapped"}])))
    assert out["outcome"] == "accept" and "indicator_unresolved" in codes(out, "issues")


def test_a_condition_shared_by_two_requirements_is_allowed(layer):
    q = Q("가상대출금과 가상예금액 분기별로 보여줘", layer)
    r1 = req("r1", indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")))
    r2 = req("r2", indicator=R(q.s("가상예금액"), z=q.z("가상예금액", "indicator")))
    bindings = [bind(q.c("frequency"), "requirements", ["r1", "r2"]), bind(q.c("breakdown", 0), "requirements", ["r1"]),
                bind(q.c("breakdown", 1), "requirements", ["r2"])]
    out = q.verify(proposal(reading([r1, r2], bindings, request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and out["issues"] == []
    bindings[0] = bind(q.c("frequency"), "question")
    assert q.verify(proposal(reading([r1, r2], bindings, request=[R(q.s("보여줘"))])))["issues"] == []
    bindings[0] = bind(q.c("frequency"), "unresolved", ["r1", "r2"])
    out = q.verify(proposal(reading([r1, r2], bindings, request=[R(q.s("보여줘"))])))
    assert out["outcome"] == "accept" and codes(out, "issues") == {"condition_unresolved"}
    assert out["scopes"]["conditions"][q.c("frequency")] == ["condition_unresolved"]


def test_relations_are_checked_against_their_bindings(layer):
    q = Q("가상대출금 대비 가상예금액 비율", layer)
    r1 = req("r1", indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")))
    r2 = req("r2", indicator=R(q.s("가상예금액"), z=q.z("가상예금액", "indicator")))
    bindings = [bind(c, "requirements", ["r1", "r2"]) for c in (q.c("relation", 0), q.c("relation", 1))]
    rel = [{"condition_id": q.c("relation", 0), "requirement_ids": ["r2", "r1"]}]
    assert q.verify(proposal(reading([r1, r2], bindings, rel)))["outcome"] == "accept"
    out = q.verify(proposal(reading([r1, r2], bindings, rel + rel)))
    assert codes(out) == {"condition_conflict"}
    narrow = [bind(q.c("relation", 0), "requirements", ["r1"]), bindings[1]]
    assert codes(q.verify(proposal(reading([r1, r2], narrow, rel)))) == {"condition_conflict"}


# ---------------------------------------------------------------- alternatives (6.1 last rows)


def test_a_failing_alternative_is_dropped_and_the_primary_kept(layer):
    q = Q(SPLIT_Q, layer)
    alt = split_reading(q)
    alt["requirements"][0]["indicator"]["subquote"] = "없는말"
    out = q.verify(proposal(split_reading(q), alt))
    assert out["outcome"] == "accept" and out["alternative"] == "dropped"
    assert codes(out, "alternative_issues") == {"subquote_not_found"}
    assert out["issues"] == [] and out["assumption_seed"] is False
    alt = split_reading(q)
    alt["requirements"][0]["indicator"]["segment_ids"] = ["s999"]  # a schema error inside the alternative only
    out = q.verify(proposal(split_reading(q), alt))
    assert out["outcome"] == "accept" and out["alternative"] == "dropped"
    assert codes(out, "alternative_issues") == {"unknown_id"}


def test_a_valid_alternative_is_kept(layer):
    q = Q(SPLIT_Q, layer)
    alt = split_reading(q)
    alt["requirements"][0]["indicator"]["sense_id"] = q.z("가상대출금", "table_name")
    out = q.verify(proposal(split_reading(q), alt))
    assert out["outcome"] == "accept" and out["alternative"] == "kept" and out["assumption_seed"] is True


def test_a_failing_primary_falls_back_even_with_a_valid_alternative(layer):
    q = Q(SPLIT_Q, layer)
    bad = split_reading(q)
    bad["requirements"][0]["indicator"]["subquote"] = "없는말"
    out = q.verify(proposal(bad, split_reading(q)))
    assert out["outcome"] == "fallback" and codes(out) == {"subquote_not_found"}
    assert out["issues"] == [] and out["assumption_seed"] is False


# ---------------------------------------------------------------- contract of the result


def test_check_verify_result_catches_broken_results(layer):
    q = Q(SPLIT_Q, layer)
    good = q.verify(proposal(split_reading(q)))
    for how in ("seed", "fatal_issue", "fallback_without_reason", "scopes", "unknown_segment", "fatal_flag"):
        bad = copy.deepcopy(good)
        issue = {"code": "unmapped_concept", "reading": "primary", "fatal": False, "requirement_ids": ["r1"],
                 "segment_ids": [q.s("가상대출금")], "condition_ids": [], "detail": ""}
        if how == "seed":
            bad["assumption_seed"] = True
        elif how == "fatal_issue":
            bad["issues"] = [dict(issue, code="format", fatal=True)]
        elif how == "fallback_without_reason":
            bad["outcome"] = "fallback"
        elif how == "scopes":
            bad["issues"], bad["assumption_seed"] = [issue], True
        elif how == "unknown_segment":
            bad["issues"], bad["assumption_seed"] = [dict(issue, segment_ids=["s999"])], True
            from claview_a.contract.check import scope_index
            bad["scopes"] = scope_index(bad["issues"])
        elif how == "fatal_flag":
            bad["issues"], bad["assumption_seed"] = [dict(issue, fatal=True)], True
        assert check_verify_result(bad, {s["segment_id"] for s in q.segments}) != [], how


def test_the_rules_path_is_unchanged_by_this_step(layer):
    """Nothing of step 3 is wired in: the analysis has the same fields as before."""
    a = interpret_question(SPLIT_Q, layer)
    assert set(a) == {"text", "conditions", "fragments", "terms", "judgement", "interpretation", "segments",
                      "mentions", "ref_map"}
