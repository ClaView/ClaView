"""Plan 15 r3 step 3 fix round 2 (user decisions and review of 2026-10-20 22:48), verification side.

A1: a subquote is read by what it quotes: the whole name's sense on a part is a wrong sense (fallback), a part with
its own catalog name written there passes, a part with no catalog name is kept unmapped.
E: a confirmed request word of the data file is set aside only as part of a request phrase; the same word inside a
compound concept, or with no request word after it, stays unresolved.

Synthetic catalog (made-up "가상" names and table IDs); model answers are hand-written fake proposals, each put through
``verify`` and its contract check. No network, no model.
"""

from __future__ import annotations

import pytest
from catalog_synth import write_inputs, write_raw

from claview.a.catalog.layer import load_layer
from claview.a.contract.check import check_verify_result
from claview.a.interpret.conditions import find_conditions
from claview.a.interpret.decompose_payload import build_payload
from claview.a.interpret.decompose_verify import verify
from claview.a.interpret.mentions import build_mentions
from claview.a.interpret.segments import build_segments
from claview.a.interpret.text import normalize_text

TABLES = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None)]),
        ("ACC", "계정항목별", "ACC", [("a1", "가상가계대출", None, None, None)]),
    ]),
]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_step3_fix2")
    search, meta = write_raw(folder / "raw", TABLES)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


class Q:
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

    def verify(self, proposal) -> dict:
        out = verify(proposal, self.payload, self.local, self.text, self.conditions, self.segments, self.mentions)
        assert check_verify_result(out, {x["segment_id"] for x in self.segments}, {x["id"] for x in self.conditions},
                                   {r["id"] for r in proposal["primary"]["requirements"]}) == []
        return out


def R(*sids, z=None, sq=None) -> dict:
    return {"segment_ids": list(sids), "subquote": sq, "sense_id": z}


def req(rid="r1", indicator=None, targets=()) -> dict:
    return {"id": rid, "indicator": indicator, "targets": list(targets), "basis_refs": [], "context": None,
            "breakdowns": []}


def reading(reqs, request=(), unused=()) -> dict:
    return {"requirements": list(reqs), "condition_bindings": [], "relations": [], "polarity_links": [],
            "request_refs": list(request), "unused_refs": list(unused)}


def proposal(primary) -> dict:
    return {"primary": primary, "alternative": None, "ambiguities": []}


def codes(result: dict, field: str = "fallback_reasons") -> set[str]:
    return {it["code"] for it in result[field]}


def issues_on(result: dict, sid: str) -> set[str]:
    return {it["code"] for it in result["issues"] if sid in it["segment_ids"]}


# ---------------------------------------------------------------- A1: subquotes by what they quote


def test_a_part_of_a_name_with_the_whole_names_sense_or_its_own_or_none(layer):
    q = Q("가상가계대출 보여줘", layer)
    s1, whole = q.s("가상가계대출"), q.z("가상가계대출", "indicator")
    show = [R(q.s("보여줘"))]
    wrong = q.verify(proposal(reading([req(indicator=R(s1, z=whole, sq="대출"))], show,
                                      [{"ref": R(s1, sq="가상가계"), "reason": "unmapped"}])))
    assert wrong["outcome"] == "fallback" and codes(wrong) == {"wrong_sense"}
    kept = q.verify(proposal(reading([req(indicator=R(s1, sq="대출"))], show,
                                     [{"ref": R(s1, sq="가상가계"), "reason": "unmapped"}])))
    assert kept["outcome"] == "accept" and codes(kept, "issues") == {"unmapped_concept"}
    assert all(s1 in it["segment_ids"] for it in kept["issues"])
    q = Q("가상서울별 가상대출금", layer)  # the part written exactly as a catalog name, with its own sense
    own = q.verify(proposal(reading([req(indicator=R(q.s("가상대출금"), z=q.z("가상대출금", "indicator")),
                                         targets=[R(q.s("가상서울별"), z=q.z("가상서울", "target"), sq="가상서울")])])
                            | {"condition_bindings": [{"condition_id": c["id"], "scope": "requirements",
                                                       "requirement_ids": ["r1"], "name_refs": []}
                                                      for c in q.conditions]}))
    assert own["outcome"] == "accept" and own["issues"] == []


# ---------------------------------------------------------------- E: confirmed words only in a request phrase


@pytest.mark.parametrize("question, aside", [
    ("가상대출금 자료 보여줘", ["자료"]),
    ("가상대출금 관련 자료를 보여줘", ["관련", "자료를"]),  # a particle after the word, a chain of confirmed words
    ("가상대출금에 대한 자료 좀 보여줘", ["대한", "자료"]),  # a filler word inside the phrase
])
@pytest.mark.parametrize("how", ["request", "unattached"])
def test_a_confirmed_word_in_a_request_phrase_needs_no_finding(layer, question, aside, how):
    q = Q(question, layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    words = [R(q.s(w)) for w in aside]
    show = [R(x["segment_id"]) for x in q.segments if x["kind"] == "request"]
    if how == "request":
        p = reading([req(indicator=ind)], words + show)
    else:
        p = reading([req(indicator=ind)], show, [{"ref": w, "reason": "unattached"} for w in words])
    out = q.verify(proposal(p))
    assert out["outcome"] == "accept" and out["issues"] == []


@pytest.mark.parametrize("question, word", [
    ("가상대출금 자료 수집 보여줘", "자료"),  # inside a compound concept ("자료 수집"): it carries meaning
    ("가상대출금 데이터", "데이터"),  # no request phrase after it: not confirmed by the code
    ("자료 가상대출금 보여줘", "자료"),  # a name between it and the request word
])
@pytest.mark.parametrize("how", ["request", "unattached"])
def test_the_same_word_outside_a_request_phrase_stays_unresolved(layer, question, word, how):
    q = Q(question, layer)
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    show = [R(x["segment_id"]) for x in q.segments if x["kind"] == "request"]
    aside = [R(x["segment_id"]) for x in q.segments if x["kind"] == "word"]
    if how == "request":
        p = reading([req(indicator=ind)], aside + show)
    else:
        p = reading([req(indicator=ind)], show, [{"ref": w, "reason": "unattached"} for w in aside])
    out = q.verify(proposal(p))
    assert out["outcome"] == "accept"
    assert "unused_unresolved" in issues_on(out, q.s(word))
    assert q.s(word) in out["scopes"]["segments"]


NAMED = [
    ("T_A", "가상대출금", "Synthetic Loans", [
        ("REG", "지역코드별", "REGION", [("r0", "가상전국", None, None, None), ("ra1", "가상서울", None, "r0", None)]),
        ("SRC", "자료구분별", "SOURCE", [("d1", "자료", None, None, None)]),  # a catalog value written as the word
    ]),
]


@pytest.fixture(scope="module")
def named_layer(tmp_path_factory):
    folder = tmp_path_factory.mktemp("plan15_step3_fix2_named")
    search, meta = write_raw(folder / "raw", NAMED)
    inputs = write_inputs(folder / "inputs", overrides=[])
    empty = folder / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    return load_layer(search_catalog=search, meta_raw=meta, overrides=False, include_external_synonyms=False,
                      synonyms_approved_path=empty, out_of_scope_path=empty, **inputs)


def test_a_confirmed_word_that_is_a_protected_catalog_name_is_never_set_aside(named_layer):
    q = Q("가상대출금 자료 보여줘", named_layer)
    word = q.s("자료")
    assert next(s["kind"] for s in q.segments if s["segment_id"] == word) == "name"  # protected, no plain word
    ind = R(q.s("가상대출금"), z=q.z("가상대출금", "indicator"))
    bound = [{"condition_id": c["id"], "scope": "requirements", "requirement_ids": ["r1"], "name_refs": []}
             for c in q.conditions]
    off_word = [b for b in bound if word not in q.local["condition_segments"].get(b["condition_id"], [])]
    swallowed = q.verify(proposal(reading([req(indicator=ind)], [R(word), R(q.s("보여줘"))])
                                  | {"condition_bindings": off_word}))
    assert swallowed["outcome"] == "fallback" and "request_swallow" in codes(swallowed)
    # used as a name it is kept (its role is unclassified in this catalog: a role finding, never confirmed)
    used = q.verify(proposal(reading([req(indicator=ind, targets=[R(word, z=q.z("자료", "unclassified"))])],
                                     [R(q.s("보여줘"))]) | {"condition_bindings": bound}))
    assert used["outcome"] == "accept" and codes(used, "issues") == {"role_mismatch"}
