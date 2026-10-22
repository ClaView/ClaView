"""Plan 19 contract joint (instruction of 2026-10-21 13:06, items 1-6): the kept structure of an adopted reading
(``decomposition.structure``: breakdown kinds and groups, polarity links in their proposed scope, confirmed effects held
empty -- user decision of 2026-10-21: always [], a non-empty list is rejected), its contract states (absent, null,
unsupported version, violation, valid), the primary and alternative ID spaces, the settled result left as it was, the
exact range check of the final reference check over ``Mention.span`` (decision of 2026-10-21), and the optional public
``Selection.bundle`` / ``reference_candidates`` checked with the shared period package.

Synthetic catalog (made-up "가상" names and table IDs: the plan 15 step 3 fix catalog); model answers are hand-written
fake proposals run through the gpt mode (``run``: every result passes ``check_analysis``). IDs are found by their words,
never fixed. No network, no model.
"""

from __future__ import annotations

import copy
import json

import pytest
from test_plan15_step3_fix import EXCLUDE_Q, NEG_Q, SPLIT_Q, layer  # noqa: F401  (layer is a fixture)
from test_plan19_stage3 import P, Q, R, bind, link, proposal, req, run

from claview.a import development as BATCH
from claview.a import paths
from claview.a.contract import shapes
from claview.a.contract.check import check_analysis, check_selections, interpretation_fields, structure_status
from claview.a.development_reverify import without_structure
from claview.a.interpret import decompose_run as DR
from claview.a.interpret.decompose_archive import digest
from claview.a.interpret.decompose_payload import build_payload
from claview.a.interpret.decompose_schema import build_schema
from claview.a.interpret.decompose_verify import polarity_grounds
from claview.a.interpret.pipeline import interpret_question
from claview.a.select.graph import select_tables
from claview.a.select.needs import all_needs

AS_OF = "2026-10-21"
AXIS_Q = "가상대출금 지역별로 보여줘"
PART_Q = "가상서울별 가상대출금"
NEG2_Q = "가상서울 말고 가상부산 가상대출금 가상예금액"
UNCLEAR_Q = "가상서울의 가상부산 말고 가상대출금 보여줘"
HELD = "confirmed_effects: held empty"  # the check's finding on any confirmed effect (decision of 2026-10-21)


# ---------------------------------------------------------------- helpers


def bd(values=(), markers=(), kind="values", axis=None) -> dict:
    return {"kind": kind, "axis": axis, "values": list(values), "marker_refs": list(markers)}


def ind(p: P, name: str) -> dict:
    return R(p.s(name), z=p.z(name, "indicator"))


def tgt(p: P, name: str) -> dict:
    return R(p.s(name), z=p.z(name, "target"))


def holder(a: dict, alternative: bool = False) -> dict:
    d = a["decomposition"]
    return d["alternative"] if alternative else d


def structure(a: dict, alternative: bool = False) -> dict:
    return holder(a, alternative)["structure"]


def positive(a: dict, rid: str, alternative: bool = False) -> list[dict]:
    """The selection refs of a requirement without its indicator, in their order."""
    return [x for x in holder(a, alternative)["selection_refs"][rid] if x["place"] != "indicator"]


def flat(rs: dict) -> list[dict]:
    out = list(rs["targets"]) + list(rs["basis"]) + ([rs["context"]] if rs["context"] is not None else [])
    for g in rs["breakdowns"]:
        out += ([g["axis"]] if g["axis"] is not None else []) + list(g["values"]) + list(g["markers"])
    return out


def old_negated(a: dict, refs: list[dict]) -> list[str]:
    """The old flattened projection of negative refs (decompose_settle._negative_spans): outside exclude conditions."""
    excl = [c["span"] for c in a["conditions"] if c["kind"] == "exclude"]
    return [r["quote"] for r in refs if not any(e[0] <= r["span"][0] and r["span"][1] <= e[1] for e in excl)]


def invalid_with(a: dict, text: str, alternative: bool = False) -> bool:
    got = structure_status(a, alternative)
    return got["state"] == "invalid" and any(text in x for x in got["problems"])


def split(p: P, kind: str = "values", with_target: bool = False, rid: str = "r1") -> dict:
    vals = [tgt(p, "가상서울"), tgt(p, "가상부산")]
    targets = [tgt(p, "가상서울")] if with_target else []
    return proposal(p.reading([req(rid, ind(p, "가상대출금"), targets=targets,
                                   breakdowns=[bd(vals, [R(p.s("·")), R(p.s("으로 나눠서"))], kind)])], p.bindings()))


def by_axis(p: P) -> dict:
    rows = [r for r in p.payload["candidate_summary"] if r["role"] == "breakdown" and r["sense_id"]]
    axis = R(*rows[0]["segment_ids"], z=rows[0]["sense_id"])
    return proposal(p.reading([req("r1", ind(p, "가상대출금"), breakdowns=[bd([], [], "axis", axis)])], p.bindings()))


def part(p: P, whole: bool = False) -> dict:
    sid = p.s("가상서울별")
    target = R(sid) if whole else R(sid, z=p.z("가상서울", "target"), sq="가상서울")
    return proposal(p.reading([req("r1", ind(p, "가상대출금"), targets=[target])], p.bindings()))


def neg_one(p: P) -> dict:
    return proposal(p.reading([req("r1", ind(p, "가상대출금"), targets=[tgt(p, "가상부산")])], p.bindings(),
                              links=[link("replace", [R(p.s("말고"))], [tgt(p, "가상서울")], [tgt(p, "가상부산")])]))


def neg_two(p: P, rids=("r3",)) -> dict:
    """Two requirements (proposal IDs r3 and r1); the replacement names the first one only."""
    return proposal(p.reading([req("r3", ind(p, "가상대출금"), targets=[tgt(p, "가상부산")]),
                               req("r1", ind(p, "가상예금액"))], p.bindings(),
                              links=[link("replace", [R(p.s("말고"))], [tgt(p, "가상서울")], [tgt(p, "가상부산")],
                                          list(rids))]))


def unclear(p: P, rids=()) -> dict:
    return proposal(p.reading([req("r1", ind(p, "가상대출금"))], p.bindings(),
                              links=[link("exclude", [R(p.s("말고"))], [tgt(p, "가상부산")], [], list(rids))]))


def excluded(p: P, scope: str) -> dict:
    x = p.kinds("exclude")[0]
    return proposal(p.reading([req("r1", ind(p, "가상대출금"))],
                              p.bindings(**{x: bind(x, scope, [] if scope == "question" else ["r1"])}),
                              links=[link("exclude", [R(p.s("뺀"))], [tgt(p, "가상서울")])]))


def grounds_of(question: str, layer) -> tuple:
    q = Q(question, layer)
    return q, polarity_grounds(q.payload, q.local, q.text, q.conditions, q.segments, q.mentions)["certain"]


# ---------------------------------------------------------------- breakdown kinds, groups, filter and breakdown


@pytest.mark.parametrize("kind", ["values", "axis", "descendants"])
def test_the_three_breakdown_kinds_are_kept_as_proposed(layer, kind):
    a = run(AXIS_Q if kind == "axis" else SPLIT_Q, layer, by_axis if kind == "axis" else (lambda p: split(p, kind)))
    d = a["decomposition"]
    assert d["applied_source"] == "model"
    s = d["structure"]
    assert s["version"] == shapes.STRUCTURE_VERSION and d["structure_reason"] is None
    assert list(s["requirements"]) == ["r1"] and s["polarity"] == [] and s["confirmed_effects"] == []
    rs = s["requirements"]["r1"]
    (g,) = rs["breakdowns"]
    assert g["group_id"] == "b1" and g["kind"] == kind
    assert flat(rs) == positive(a, "r1")  # one projection for the structure and the selection refs
    # the old flattened field is as before: the quotes of the axis and the values
    words = [x["quote"] for x in ([g["axis"]] if g["axis"] is not None else []) + g["values"]]
    assert a["interpretation"]["requirements"][0]["breakdown"] == words
    if kind == "axis":
        assert g["values"] == [] and g["markers"] == [] and g["descendants"] is None  # values not specified
        assert g["axis"]["place"] == "breakdown_axis" and g["axis"]["name"] and g["axis"]["candidates"]
    else:
        assert [v["quote"] for v in g["values"]] == ["가상서울", "가상부산"]
        assert [m["quote"] for m in g["markers"]] == ["·", "으로 나눠서"]
        assert all(v["place"] == "breakdown_value" and v["candidates"] for v in g["values"])
        if kind == "descendants":  # not expanded: the parents as written, with their candidate rows
            assert g["descendants"] == {"status": "unexpanded", "source_refs": g["values"]}
        else:
            assert g["descendants"] is None
    assert structure_status(a) == {"state": "valid", "problems": []}


def test_group_ids_and_descendants_are_checked(layer):
    a = run(SPLIT_Q, layer, lambda p: split(p, "descendants"))
    dup = copy.deepcopy(a)
    groups = structure(dup)["requirements"]["r1"]["breakdowns"]
    groups.append(copy.deepcopy(groups[0]))
    assert invalid_with(dup, "duplicate 'b1'")
    kind = copy.deepcopy(a)
    structure(kind)["requirements"]["r1"]["breakdowns"][0]["kind"] = "values"
    assert invalid_with(kind, "present exactly for the kind 'descendants'")
    source = copy.deepcopy(a)
    structure(source)["requirements"]["r1"]["breakdowns"][0]["descendants"]["source_refs"].pop()
    assert invalid_with(source, "not the group's axis and value refs")
    expanded = copy.deepcopy(a)
    structure(expanded)["requirements"]["r1"]["breakdowns"][0]["descendants"]["status"] = "expanded"
    assert structure_status(expanded)["state"] == "invalid"  # no expander: only the unexpanded state exists
    assert check_analysis(expanded) != []


def test_a_filter_value_and_the_same_value_as_a_breakdown_value_stay_apart(layer):
    a = run(SPLIT_Q, layer, lambda p: split(p, with_target=True))
    rs = structure(a)["requirements"]["r1"]
    (t,) = rs["targets"]
    v = rs["breakdowns"][0]["values"][0]
    assert t["place"] == "target" and v["place"] == "breakdown_value"
    assert {k: x for k, x in t.items() if k != "place"} == {k: x for k, x in v.items() if k != "place"}
    assert flat(rs) == positive(a, "r1")
    r = a["interpretation"]["requirements"][0]
    assert [x["quote"] for x in r["targets"]] == ["가상서울"] and r["breakdown"] == ["가상서울", "가상부산"]
    moved = copy.deepcopy(a)  # the filter value moved into the breakdown group
    mrs = structure(moved)["requirements"]["r1"]
    mrs["breakdowns"][0]["values"].append(mrs["targets"].pop())
    assert invalid_with(moved, "differ from selection_refs")
    relabel = copy.deepcopy(a)  # or labelled as the other place
    structure(relabel)["requirements"]["r1"]["targets"][0]["place"] = "breakdown_value"
    assert invalid_with(relabel, "is not 'target'") and invalid_with(relabel, "differ from selection_refs")
    assert check_analysis(relabel) != []


def test_a_partial_quote_carries_only_its_own_candidate_rows(layer):
    a = run(PART_Q, layer, part)
    (t,) = structure(a)["requirements"]["r1"]["targets"]
    sid = t["segment_ids"][0]
    seg = next(s for s in a["segments"] if s["segment_id"] == sid)
    assert t["quote"] == "가상서울" and t["span"] != seg["span"]  # a part of the segment
    assert t == positive(a, "r1")[0]
    assert t["candidates"] and all(c["value_name"] == "가상서울" for c in t["candidates"])
    other = [c for m in a["mentions"] if m["kind"] == "name" and sid not in m["segment_ids"] for c in m["candidates"]]
    assert other and not [c for c in t["candidates"] if c in other]
    whole = run(PART_Q, layer, lambda p: part(p, whole=True))
    (w,) = structure(whole)["requirements"]["r1"]["targets"]
    assert w["quote"] == seg["quote"] and w["span"] == seg["span"] and w["candidates"] == [] and w["name"] is None
    forged = copy.deepcopy(a)  # a row of another mention put on the part
    structure(forged)["requirements"]["r1"]["targets"][0]["candidates"].append(other[0])
    assert invalid_with(forged, "raw mention candidate") and invalid_with(forged, "differ from selection_refs")
    widened = copy.deepcopy(a)  # the part read as the whole segment
    wt = structure(widened)["requirements"]["r1"]["targets"][0]
    wt["span"], wt["quote"] = list(seg["span"]), seg["quote"]
    assert invalid_with(widened, "differ from selection_refs")


# ---------------------------------------------------------------- the exact range rule of the final reference check

RAW_ROWS = "not a raw mention candidate"


def both_targets(a: dict, change) -> None:
    """The same change to the requirement's target selection ref and to its kept structure target, so that the two
    stay consistent (the reviewer's scenario: only the range rule can tell)."""
    d = a["decomposition"]
    (sel,) = [x for x in d["selection_refs"]["r1"] if x["place"] == "target"]
    (ref,) = d["structure"]["requirements"]["r1"]["targets"]
    for x in (sel, ref):
        change(x)


def without_spans(a: dict) -> dict:
    """The analysis as an older run kept it: no ``Mention.span``."""
    out = copy.deepcopy(a)
    for m in out["mentions"]:
        m.pop("span", None)
    return out


def range_findings(a: dict) -> list[str]:
    return [x for x in check_analysis(a) if RAW_ROWS in x]


def test_every_mention_keeps_its_raw_span(layer):
    a = run(PART_Q, layer, part)
    raw = a["text"]["raw"]
    segs = {s["segment_id"]: s["span"] for s in a["segments"]}
    for m in a["mentions"]:
        lo, hi = m["span"]
        assert 0 <= lo < hi <= len(raw)
        assert [sid for sid, (x, y) in segs.items() if x < hi and lo < y] == m["segment_ids"]
    assert check_analysis(a) == []
    bad = copy.deepcopy(a)  # a span off its segments is a violation
    bad["mentions"][0]["span"] = [len(raw) - 1, len(raw)]
    assert any(x.startswith("mentions[0].span") for x in check_analysis(bad))


def test_a_partial_quote_carrying_the_whole_names_rows_is_a_violation_even_changed_consistently(layer):
    a = run(NEG_Q, layer, neg_one)
    (t,) = structure(a)["requirements"]["r1"]["targets"]
    seg = next(s for s in a["segments"] if s["segment_id"] == t["segment_ids"][0])
    assert t["span"] == seg["span"] and t["name"] and t["candidates"]  # a whole ref with the whole name's rows
    lo = t["span"][0]
    part_quote = a["text"]["raw"][lo:lo + len(t["quote"]) - 1]

    def narrow(x):  # read as a part of the segment, still with the whole name's sense and rows
        x["span"], x["quote"] = [lo, lo + len(part_quote)], part_quote

    tampered = copy.deepcopy(a)
    both_targets(tampered, narrow)
    assert not any(x["span"] == [lo, lo + len(part_quote)] for x in tampered["mentions"] if x["kind"] == "name")
    found = range_findings(tampered)
    assert any(x.startswith("decomposition.selection_refs.r1[") for x in found), found
    assert any(x.startswith("decomposition.structure.requirements.r1.targets[0]") for x in found), found
    assert structure_status(tampered)["state"] == "invalid"
    # an older run without mention spans keeps the segment-level rule: the same change is not caught there
    old = without_spans(tampered)
    assert range_findings(old) == [] and structure_status(old) == {"state": "valid", "problems": []}


def test_a_whole_ref_carrying_an_inner_mentions_rows_is_a_violation_even_changed_consistently(layer):
    a = run(PART_Q, layer, lambda p: part(p, whole=True))
    (w,) = structure(a)["requirements"]["r1"]["targets"]
    sid = w["segment_ids"][0]
    seg = next(s for s in a["segments"] if s["segment_id"] == sid)
    assert w["span"] == seg["span"] and w["candidates"] == [] and w["name"] is None
    inner = [m for m in a["mentions"] if m["kind"] == "name" and m["segment_ids"] == [sid]
             and m["span"] != seg["span"] and m["candidates"]]
    assert inner  # a name strictly inside the segment
    rows = [c for c in inner[0]["candidates"] if c["value_name"] == inner[0]["candidates"][0]["value_name"]]

    def carry(x):
        x["name"], x["candidates"] = rows[0]["value_name"], copy.deepcopy(rows)

    tampered = copy.deepcopy(a)
    both_targets(tampered, carry)
    found = range_findings(tampered)
    assert any(x.startswith("decomposition.selection_refs.r1[") for x in found), found
    assert any(x.startswith("decomposition.structure.requirements.r1.targets[0]") for x in found), found
    old = without_spans(tampered)  # without spans an inner mention cannot be told: the old rule lets it through
    assert range_findings(old) == [] and structure_status(old) == {"state": "valid", "problems": []}


def test_correct_partial_and_whole_refs_pass_the_exact_range_rule(layer):
    partial = run(PART_Q, layer, part)  # ``run`` asserts check_analysis == []
    (t,) = structure(partial)["requirements"]["r1"]["targets"]
    assert t["candidates"] and any(m["kind"] == "name" and m["span"] == t["span"] for m in partial["mentions"])
    whole = run(NEG_Q, layer, neg_one)
    (w,) = structure(whole)["requirements"]["r1"]["targets"]
    assert w["candidates"]
    for a in (partial, whole):
        assert all("span" in m for m in a["mentions"])
        assert structure_status(a) == {"state": "valid", "problems": []}
        # an older run's analysis (no mention spans) is read as before and does not fail
        old = without_spans(a)
        assert check_analysis(old) == [] and structure_status(old) == {"state": "valid", "problems": []}


@pytest.mark.parametrize("question", [PART_Q, NEG_Q, SPLIT_Q])
def test_the_payload_and_request_do_not_carry_the_mention_span(layer, question):
    rules, payload, schema = BATCH.input_of(question, layer)
    assert all("span" in m for m in rules["mentions"])
    before = [{k: v for k, v in m.items() if k != "span"} for m in rules["mentions"]]  # the mentions before
    old_payload, old_local = build_payload(rules["text"], rules["conditions"], rules["segments"], before, layer)
    _, local = build_payload(rules["text"], rules["conditions"], rules["segments"], rules["mentions"], layer)
    assert payload == old_payload and build_schema(old_payload) == schema and local == old_local
    assert local["mention_spans"] == {m["mention_id"]: m["span"] for m in rules["mentions"]}
    policy = BATCH.read(BATCH.MODELS)
    for profile in policy["profiles"]:
        new = BATCH.settings(profile, policy).request(payload, schema)
        old = BATCH.settings(profile, policy).request(old_payload, build_schema(old_payload))
        assert digest(new) == digest(old)


def test_primary_and_alternative_keep_their_own_requirement_ids(layer):
    def build(p):
        out = split(p, rid="r4")
        out["alternative"] = p.reading([req("r2", ind(p, "가상대출금"), targets=[tgt(p, "가상서울")]),
                                        req("r1", ind(p, "가상대출금"), targets=[tgt(p, "가상부산")])], p.bindings())
        return out

    a = run(SPLIT_Q, layer, build)
    d = a["decomposition"]
    assert d["verify"]["alternative"] == "kept"
    assert d["requirement_ids"] == {"r1": "r4"} and d["alternative"]["requirement_ids"] == {"r1": "r2", "r2": "r1"}
    s, alt = d["structure"], d["alternative"]["structure"]
    assert list(s["requirements"]) == ["r1"] and list(alt["requirements"]) == ["r1", "r2"]
    assert [x["quote"] for x in alt["requirements"]["r1"]["targets"]] == ["가상서울"]
    assert [x["quote"] for x in alt["requirements"]["r2"]["targets"]] == ["가상부산"]
    assert alt["requirements"]["r1"]["breakdowns"] == [] and s["requirements"]["r1"]["breakdowns"]
    for rid in ("r1", "r2"):
        assert flat(alt["requirements"][rid]) == positive(a, rid, alternative=True)
    assert structure_status(a) == structure_status(a, alternative=True) == {"state": "valid", "problems": []}
    swapped = copy.deepcopy(a)  # each reading is checked in its own IDs
    swapped["decomposition"]["alternative"]["structure"] = copy.deepcopy(s)
    assert invalid_with(swapped, "keys differ", alternative=True)
    assert any(x.startswith("decomposition.alternative.structure") for x in check_analysis(swapped))


# ---------------------------------------------------------------- polarity: proposed scope, confirmed effects


@pytest.mark.parametrize("rids, want", [(("r3",), ["r1"]), ((), [])])
def test_a_negation_on_one_of_two_requirements_keeps_its_scope_and_confirms_no_effect(layer, rids, want):
    a = run(NEG2_Q, layer, lambda p: neg_two(p, rids))
    d = a["decomposition"]
    assert d["requirement_ids"] == {"r1": "r3", "r2": "r1"}
    s = d["structure"]
    (pl,) = s["polarity"]
    assert pl["link_id"] == "p1" and pl["kind"] == "replace" and pl["requirement_ids"] == want  # [] stays every one
    assert [x["quote"] for x in pl["markers"]] == ["말고"]
    assert [x["quote"] for x in pl["negative"]] == ["가상서울"] and [x["quote"] for x in pl["positive"]] == ["가상부산"]
    assert pl["scope_status"] == "clear"
    # partly certain: the target is certain by the verification's own ground, the attribution among two is not
    q, certain = grounds_of(NEG2_Q, layer)
    assert certain[q.s("말고")] == q.s("가상서울")
    assert s["confirmed_effects"] == []
    assert structure_status(a) == {"state": "valid", "problems": []}
    assert a["judgement"]["negated"] == old_negated(a, pl["negative"])  # the old projection as before
    if want:  # any effect is rejected (held empty); one outside the link's proposed scope is also named
        bad = copy.deepcopy(a)
        structure(bad)["confirmed_effects"] = [{"link_id": "p1", "negative_ref_index": 0, "requirement_ids": ["r2"],
                                                "evidence_refs": [pl["markers"][0], pl["negative"][0]]}]
        assert invalid_with(bad, HELD) and invalid_with(bad, "outside the link's scope")
        unknown = copy.deepcopy(a)
        structure(unknown)["polarity"][0]["requirement_ids"] = ["r9"]
        assert invalid_with(unknown, "unknown ID 'r9'")


def test_a_certain_target_of_the_one_requirement_is_held_as_no_confirmed_effect(layer):
    a = run(NEG_Q, layer, neg_one)
    s = a["decomposition"]["structure"]
    (pl,) = s["polarity"]
    # the link is kept as recorded (kind, markers, negative, positive, scope, scope status)
    assert pl["kind"] == "replace" and pl["requirement_ids"] == [] and pl["scope_status"] == "clear"
    assert [x["quote"] for x in pl["markers"]] == ["말고"]
    assert [x["quote"] for x in pl["negative"]] == ["가상서울"] and [x["quote"] for x in pl["positive"]] == ["가상부산"]
    q, certain = grounds_of(NEG_Q, layer)
    assert certain[q.s("말고")] == q.s("가상서울")  # the verification's own ground is certain ...
    assert s["confirmed_effects"] == []  # ... yet no effect is made: held empty (user decision of 2026-10-21)
    assert structure_status(a) == {"state": "valid", "problems": []}
    # the effect the earlier rule made from these grounds, written by hand: now a violation
    base = {"link_id": "p1", "negative_ref_index": 0, "requirement_ids": ["r1"],
            "evidence_refs": [pl["markers"][0], pl["negative"][0]]}
    held = copy.deepcopy(a)
    structure(held)["confirmed_effects"] = [base]
    got = structure_status(held)
    assert got["state"] == "invalid" and got["problems"] == [
        "decomposition.structure.confirmed_effects: held empty in 'structure-2026-10-21.1' (no confirmed effect is "
        "made this round), got 1"]
    assert got["problems"][0] in check_analysis(held)
    cases = [(dict(base, link_id="p9"), "unknown link"),
             (dict(base, negative_ref_index=1), "is no negative ref"),
             (dict(base, requirement_ids=["r9"]), "unknown ID 'r9'"),
             (dict(base, requirement_ids=[]), "empty or a requirement named twice"),
             (dict(base, evidence_refs=[pl["markers"][0]]), "the negative ref is not among them"),
             (dict(base, evidence_refs=[pl["markers"][0], pl["negative"][0], pl["positive"][0]]),
              "not the link's markers and that negative ref"),
             (dict(base, evidence_refs=[pl["negative"][0]]), "no marker of the link")]
    for effect, text in cases:  # rejected as held, and the effect rule it breaks is still named
        bad = copy.deepcopy(a)
        structure(bad)["confirmed_effects"] = [effect]
        assert invalid_with(bad, HELD) and invalid_with(bad, text), text
    twice = copy.deepcopy(a)
    structure(twice)["confirmed_effects"] = [base, copy.deepcopy(base)]
    assert invalid_with(twice, HELD) and invalid_with(twice, "duplicate effect")


@pytest.mark.parametrize("rids", [(), ("r1",)])
def test_an_uncertain_negation_stays_open_and_takes_no_new_global_path(layer, rids):
    a = run(UNCLEAR_Q, layer, lambda p: unclear(p, rids))
    assert "unclear_scope" in {it["code"] for it in a["decomposition"]["verify"]["issues"]}
    s = a["decomposition"]["structure"]
    (pl,) = s["polarity"]
    assert pl["scope_status"] == "uncertain" and pl["requirement_ids"] == list(rids)
    q, certain = grounds_of(UNCLEAR_Q, layer)
    assert certain[q.s("말고")] is None
    assert s["confirmed_effects"] == []  # the model naming the requirement confirms nothing
    # the old question-level projection is as before, and nothing the selection reads comes from the structure
    assert a["judgement"]["negated"] == old_negated(a, pl["negative"])
    plain = copy.deepcopy(without_structure(a))
    assert all_needs(copy.deepcopy(a)) == all_needs(plain)
    assert select_tables(copy.deepcopy(a), layer, AS_OF) == select_tables(plain, layer, AS_OF)


@pytest.mark.parametrize("scope, clear", [("requirements", True), ("question", True), ("unresolved", False)])
def test_an_exclusion_written_as_a_condition_keeps_its_link_and_confirms_no_effect(layer, scope, clear):
    a = run(EXCLUDE_Q, layer, lambda p: excluded(p, scope))
    s = a["decomposition"]["structure"]
    (pl,) = s["polarity"]
    assert pl["scope_status"] == ("clear" if clear else "uncertain")
    assert s["confirmed_effects"] == []  # held empty, a concrete binding too (user decision of 2026-10-21)
    assert structure_status(a) == {"state": "valid", "problems": []}
    if scope == "requirements":  # an effect written anyway is rejected; the binding rule is still named
        written = copy.deepcopy(a)
        structure(written)["confirmed_effects"] = [{"link_id": "p1", "negative_ref_index": 0,
                                                    "requirement_ids": ["r1"],
                                                    "evidence_refs": [pl["markers"][0], pl["negative"][0]]}]
        assert invalid_with(written, HELD)
        bad = copy.deepcopy(written)
        x = next(c["id"] for c in a["conditions"] if c["kind"] == "exclude")
        for b in bad["decomposition"]["bindings"]:
            if b["condition_id"] == x:
                b["scope"] = "unresolved"
        assert invalid_with(bad, HELD) and invalid_with(bad, "bound unresolved")


# ---------------------------------------------------------------- states: absent, null, unsupported version, violation


def test_null_absent_unsupported_version_and_violation_are_told_apart(layer):
    rules = interpret_question(NEG_Q, layer, decomposition={"mode": "rules"})
    d = rules["decomposition"]
    assert d["structure"] is None and d["structure_reason"] == "rules_structure_unrecorded"
    assert structure_status(rules) == {"state": "null", "problems": []} and check_analysis(rules) == []
    fallback = interpret_question(NEG_Q, layer, decomposition={"mode": "gpt", "proposal_source": lambda p, s: None})
    assert fallback["decomposition"]["applied_source"] == "rules_fallback"
    assert fallback["decomposition"]["structure_reason"] == "rules_structure_unrecorded"
    assert structure_status(fallback) == {"state": "null", "problems": []} and check_analysis(fallback) == []
    no_reason = copy.deepcopy(rules)
    no_reason["decomposition"]["structure_reason"] = None
    assert structure_status(no_reason)["state"] == "null"
    assert any("names its reason" in x for x in check_analysis(no_reason))
    rules_with = copy.deepcopy(rules)
    a = run(NEG_Q, layer, neg_one)
    rules_with["decomposition"].update(structure=copy.deepcopy(a["decomposition"]["structure"]), structure_reason=None)
    assert any("rules adopted keep no model structure" in x for x in check_analysis(rules_with))

    old = copy.deepcopy(a)  # a record of an older run: no structure, not a violation
    del old["decomposition"]["structure"], old["decomposition"]["structure_reason"]
    assert structure_status(old) == {"state": "absent", "problems": []} and check_analysis(old) == []
    null = copy.deepcopy(a)
    null["decomposition"]["structure"] = None
    assert structure_status(null)["state"] == "null" and any("keeps its structure" in x for x in check_analysis(null))

    future = copy.deepcopy(a)  # another version is unsupported, whatever its shape
    future["decomposition"]["structure"]["version"] = "structure-2099-01-01.1"
    future["decomposition"]["structure"]["new_field"] = []
    got = structure_status(future)
    assert got["state"] == "unsupported_version" and len(got["problems"]) == 1
    assert "unsupported version" in got["problems"][0] and check_analysis(future) == got["problems"]
    shape = copy.deepcopy(a)  # the supported version with another shape is a violation
    shape["decomposition"]["structure"]["new_field"] = []
    assert invalid_with(shape, "unknown field 'new_field'")
    keyed = copy.deepcopy(a)
    rs = keyed["decomposition"]["structure"]["requirements"]
    rs["r9"] = copy.deepcopy(rs["r1"])
    assert invalid_with(keyed, "keys differ")


def test_keeping_the_structure_changes_nothing_else(layer, monkeypatch):
    builds = [(SPLIT_Q, lambda p: split(p, "descendants")), (NEG2_Q, neg_two), (UNCLEAR_Q, unclear),
              (EXCLUDE_Q, lambda p: excluded(p, "requirements"))]
    kept = [run(question, layer, build) for question, build in builds]
    monkeypatch.setattr(DR, "build_structure", lambda *args, **kwargs: None)
    for (question, build), a in zip(builds, kept):
        off = interpret_question(question, layer, decomposition={
            "mode": "gpt", "proposal_source": lambda payload, schema, build=build: build(P(payload))})
        assert without_structure(off) == without_structure(a), question


def test_the_new_leaves_have_field_effects_for_the_integrated_selection():
    shapes_of = ("DecompositionStructure", "StructureBreakdown", "BreakdownDescendants", "PolarityStructure",
                 "ConfirmedEffect")
    new = [k for k in interpretation_fields() if k.split(".")[0] in shapes_of] + ["DecompositionRecord.structure_reason"]
    assert len(new) == 12
    effects = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))["field_effects"]
    expected = {k: "0_unused" for k in new}
    # User-approved splice: only the fields now consumed by the refutation path.
    expected.update({"DecompositionStructure.version": "2_evidence",
                     "StructureBreakdown.group_id": "3_question_set",
                     "StructureBreakdown.kind": "3_question_set",
                     "BreakdownDescendants.status": "2_evidence",
                     "PolarityStructure.requirement_ids": "2_evidence"})
    assert {k: effects[k] for k in new} == expected


# ---------------------------------------------------------------- item 6: Selection.bundle and reference_candidates

COVERS = {"targets": [], "periods": [], "frequencies": [], "basis": []}
BUNDLE = {"bundle_id": "bundle-1", "context": {"bundle_id": "bundle-1", "anchor_source": "bundle_common",
                                               "anchor": "2026-09"},
          "assignments": {"T_A": [{"start": "2020-01", "end": "2023-12"}],
                          "T_B": [{"start": "2024-01", "end": "2026-09"}]}}
CANDIDATE = {"table_id": "T_A", "obj_id": "ITEM", "value_id": "a1", "value_name": "가상값", "role": "indicator",
             "path": "exact", "type": "item_row"}


def selection(**kw) -> dict:
    return {"requirement_id": "r1", "tables": [{"table_id": t, "covers": dict(COVERS)} for t in ("T_A", "T_B")],
            "unspecified": [], "status": "auto", "model_step": "skipped", "reasons": [], **kw}


def test_the_bundle_and_reference_candidates_are_optional_public_fields():
    assert check_selections([selection()]) == []  # absent: none
    assert check_selections([selection(bundle=copy.deepcopy(BUNDLE), reference_candidates=[dict(CANDIDATE)])]) == []
    latest = {"bundle_id": "b2", "context": {"bundle_id": "b2", "anchor_source": "table_latest", "anchor": None,
                                             "simultaneous": True}, "assignments": {}}
    assert check_selections([selection(bundle=latest, reference_candidates=[])]) == []


@pytest.mark.parametrize("change, text", [
    ("bundle_id", "differs from context.bundle_id"),
    ("table", "not a table of the selection"),
    ("two_segments", "one continuous segment per table"),
    ("reversed", "start after end"),
    ("empty_assignment", "not a non-empty list"),
    ("anchor_source", "context.anchor_source: unknown"),
    ("anchor", "a bundle_common anchor must be YYYY-MM"),
    ("extra_field", "unknown field 'tables'"),
])
def test_bundle_violations_are_read_by_the_shared_period_package(change, text):
    b = copy.deepcopy(BUNDLE)
    if change == "bundle_id":
        b["bundle_id"] = "bundle-2"
    elif change == "table":
        b["assignments"]["T_Z"] = [{"start": "2020-01", "end": "2020-12"}]
    elif change == "two_segments":
        b["assignments"]["T_A"].append({"start": "2024-01", "end": "2024-12"})
    elif change == "reversed":
        b["assignments"]["T_A"] = [{"start": "2024-01", "end": "2020-12"}]
    elif change == "empty_assignment":
        b["assignments"]["T_A"] = []
    elif change == "anchor_source":
        b["context"]["anchor_source"] = "somewhere"
    elif change == "anchor":
        b["context"]["anchor"] = "2026"
    else:
        b["tables"] = ["T_A"]
    got = check_selections([selection(bundle=b)])
    assert any(text in x for x in got), got


@pytest.mark.parametrize("value, text", [
    ("T_A", "expected a list"),
    ([{"table_id": "T_A"}], "missing field 'obj_id'"),
    ([dict(CANDIDATE, table_id="")], "table_id: empty"),
    ([dict(CANDIDATE, fetch=False)], "unknown field 'fetch'"),  # B marks fetch itself; A keeps the raw shape
])
def test_reference_candidates_keep_the_raw_candidate_shape(value, text):
    got = check_selections([selection(reference_candidates=value)])
    assert any(text in x for x in got), got
