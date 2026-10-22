"""Plan 19: core-meaning and table-bundle agreement (supplementary to the strict metric, which is unchanged).

Hand-written payloads and proposals with made-up "가상" names; no catalog, model or network.
"""

import copy

from test_plan15_settle import R, bd, bind, proposal, reading, req
from claview.a import development_metrics as M

SEGMENTS = [("s1", "가상대출금", "name"), ("s2", "과", "grammar"), ("s3", "가상예금액", "name"), ("s4", " ", "space"),
            ("s5", "가상서울", "name"), ("s6", "·", "punct"), ("s7", "가상부산", "name"), ("s8", " ", "space"),
            ("s9", "분기별", "condition"), ("s10", " ", "space"), ("s11", "대비", "marker"), ("s12", " ", "space"),
            ("s13", "모르는말", "word"), ("s14", " ", "space"), ("s15", "좀", "word"), ("s16", " ", "space"),
            ("s17", "보여줘", "request")]
SENSES = {"z1": ("가상대출금", "indicator", "exact", "m1", "s1"), "z2": ("가상예금액", "indicator", "exact", "m2", "s3"),
          "z3": ("가상서울", "target", "exact", "m3", "s5"), "z4": ("가상부산", "target", "exact", "m4", "s7")}


def payload(senses=None) -> dict:
    senses = SENSES if senses is None else senses
    return {"raw": "".join(t for _, t, _ in SEGMENTS),
            "segments": [{"id": i, "text": t, "kind": k, "marker": None} for i, t, k in SEGMENTS],
            "conditions": [{"id": "c1", "kind": "frequency", "quote": "분기별", "value": "Q", "segment_ids": ["s9"],
                            "extract_status": None, "attrs": {}},
                           {"id": "c2", "kind": "relation", "quote": "대비", "value": "대비", "segment_ids": ["s11"],
                            "extract_status": None, "attrs": {}}],
            "candidate_summary": [{"mention_id": m, "segment_ids": [s], "sense_id": z, "name": n, "role": r, "path": p}
                                  for z, (n, r, p, m, s) in senses.items()],
            "relations": [], "truncation": {}, "limits": {}}


def two(order=("r1", "r2")) -> dict:
    """Two requirements (indicator z1 bound to c1; indicator z2 with a target), a relation over both."""
    a, b = order
    first = req(a, R("s1", z="z1"))
    second = req(b, R("s3", z="z2"), targets=[R("s5", z="z3")])
    return proposal(reading([first, second], [bind("c1", "requirements", [a])],
                            [{"condition_id": "c2", "requirement_ids": [a, b]}], request=[R("s17")]))


def core(p, pay=None):
    return M.core_signature(p, pay or payload())


def test_requirement_order_and_labels_do_not_count():
    one = two()
    swapped = two(order=("r2", "r1"))
    swapped["primary"]["requirements"].reverse()
    assert core(one) == core(swapped) is not None


def test_duplicate_targets_and_breakdown_values_are_kept_as_a_difference():
    one = two()
    dup = copy.deepcopy(one)
    dup["primary"]["requirements"][1]["targets"].append(R("s5", z="z3"))
    assert core(one) != core(dup)
    split = proposal(reading([req("r1", R("s1", z="z1"), breakdowns=[bd([R("s5", z="z3"), R("s7", z="z4")])])]))
    doubled = copy.deepcopy(split)
    doubled["primary"]["requirements"][0]["breakdowns"][0]["values"].append(R("s5", z="z3"))
    assert core(split) != core(doubled)
    filtered = proposal(reading([req("r1", R("s1", z="z1"), targets=[R("s5", z="z3"), R("s7", z="z4")])]))
    assert core(split) != core(filtered)  # a breakdown is not a filter


def test_senses_compare_by_name_role_and_path_not_by_code_made_id():
    one = two()
    renamed = dict(SENSES)
    renamed["z9"] = renamed.pop("z1")
    other = copy.deepcopy(one)
    other["primary"]["requirements"][0]["indicator"]["sense_id"] = "z9"
    assert core(one) == core(other, payload(renamed))
    for name in ("가상 대출금", "가상대출금 1)"):  # whitespace and footnote variants of one catalog name
        variant = dict(SENSES)
        variant["z1"] = (name,) + SENSES["z1"][1:]
        assert core(one) == core(one, payload(variant))
    for field, value in ((1, "table_name"), (2, "lexical"), (0, "가상대출금(평잔)")):
        changed = dict(SENSES)
        changed["z1"] = tuple(value if n == field else x for n, x in enumerate(SENSES["z1"]))
        assert core(one) != core(one, payload(changed))
    words = copy.deepcopy(one)
    words["primary"]["requirements"][0]["indicator"]["sense_id"] = None
    assert core(one) != core(words)  # a chosen sense is not the bare words


def test_bracket_numbers_and_years_stay_apart_in_the_core_meaning():
    """Plan 19 review F4: the core name key is the sense table's spelling equivalence (``spelling_key``): spacing and a
    footnote mark written apart are one name, the contents of a bracket never are."""
    one = two()

    def named(name):
        senses = dict(SENSES)
        senses["z1"] = (name,) + SENSES["z1"][1:]
        return core(one, payload(senses))

    for a, b in (("가상항목(갑)", "가상항목 (갑)"), ("가상항목(갑)", "가상항목(갑) 1)"), ("가상항목(1)", "가상항목 (1)")):
        assert named(a) == named(b), (a, b)
    for a, b in (("가상항목(1)", "가상항목(2)"), ("가상항목(2010)", "가상항목(2015)"),
                 ("가상항목(평잔)", "가상항목(전년동기비)")):
        assert named(a) != named(b), (a, b)
    assert M._name_key("가상항목(1)") != M._name_key("가상항목(2)")


def test_missing_indicator_is_never_merged_with_a_table_context():
    ind = proposal(reading([req("r1", R("s1", z="z1"))]))
    ctx = proposal(reading([req("r1", None, context=R("s1", z="z1"))]))
    none = proposal(reading([req("r1", None)]))
    assert len({core(ind), core(ctx), core(none)}) == 3


def test_old_contract_relations_are_unordered_and_direction_is_not_comparable():
    one = two()
    reversed_ = copy.deepcopy(one)
    reversed_["primary"]["relations"][0]["requirement_ids"].reverse()
    assert core(one) == core(reversed_)
    assert M.proposal_signature(one) != M.proposal_signature(reversed_)  # the strict metric keeps the order
    assert M.core_view(one, payload())["relation_direction"] == M.RELATION_DIRECTION_OLD_CONTRACT == "not_comparable"
    no_relation = copy.deepcopy(one)
    no_relation["primary"]["relations"] = []
    assert core(one) != core(no_relation)


def test_bindings_follow_the_requirement_content():
    one = two()
    moved = copy.deepcopy(one)
    moved["primary"]["condition_bindings"][0]["requirement_ids"] = ["r2"]
    question = copy.deepcopy(one)
    question["primary"]["condition_bindings"][0].update(scope="question", requirement_ids=[])
    assert len({core(one), core(moved), core(question)}) == 3


def test_alternative_ambiguities_requests_markers_and_grammar_are_left_out():
    one = two()
    extra = copy.deepcopy(one)
    extra["alternative"] = copy.deepcopy(one["primary"])
    extra["alternative"]["requirements"].pop()
    extra["ambiguities"] = [{"refs": [R("s1")], "kind": "meaning", "condition_ids": [],
                             "reason_code": "several_senses"}]
    extra["primary"]["request_refs"] = []
    extra["primary"]["unused_refs"] = [{"ref": R("s2"), "reason": "unattached"},
                                       {"ref": R("s15"), "reason": "unattached"}]  # grammar and a filler word
    assert core(one) == core(extra)
    marked = proposal(reading([req("r1", R("s1", z="z1"), breakdowns=[bd([R("s5", z="z3"), R("s7", z="z4")],
                                                                         [R("s6")])])]))
    plain = copy.deepcopy(marked)
    plain["primary"]["requirements"][0]["breakdowns"][0]["marker_refs"] = []
    assert core(marked) == core(plain)


def test_unused_content_words_count():
    one = two()
    unused = copy.deepcopy(one)
    unused["primary"]["unused_refs"] = [{"ref": R("s13"), "reason": "unresolved"}]
    assert core(one) != core(unused)
    assert M.core_view(unused, payload())["unused_content"] == ["모르는말"]


def test_unreadable_reading_is_not_comparable():
    assert core({"primary": {"requirements": "broken"}}) is None
    same_id = proposal(reading([req("r1", R("s1", z="z1")), req("r1", R("s3", z="z2"))]))
    assert core(same_id) is None


def groups(signatures, counts, expected, adopted=(True, True, True), bundles=("b", "b", "b")):
    tasks = [{"profile": {"id": "p"}, "case_id": "x", "repeat": n} for n in (1, 2, 3)]
    rows = [{"profile_id": "p", "case_id": "x", "repeat": n, "model_adopted": a, "core_signature": s,
             "requirement_count": c, "relation_count": 0, "bundle_signature": b}
            for n, s, c, a, b in zip((1, 2, 3), signatures, counts, adopted, bundles)]
    return M.core_repeat_report(tasks, rows, expected)["overall"]


def test_the_same_answer_repeated_with_another_requirement_count_is_reported_apart():
    agree_wrong = groups(["s", "s", "s"], [2, 2, 2], {"x": 1})
    assert agree_wrong["core"] == {"agree": 1, "eligible": 1, "rate": 1.0}
    assert agree_wrong["same_count_mismatch_repeated"] == [["p", "x"]]
    assert groups(["s", "s", "s"], [2, 2, 2], {"x": 2})["same_count_mismatch_repeated"] == []
    assert groups(["s", "t", "s"], [2, 2, 2], {"x": 1})["same_count_mismatch_repeated"] == []
    assert groups(["s", "s", "s"], [2, 2, 2], {})["same_count_mismatch_repeated"] == []


def test_supplementary_denominators_follow_the_strict_groups():
    mixed = groups(["s", "s", "s"], [1, 1, 1], {}, adopted=(True, False, True))
    assert mixed["core"]["eligible"] == 0  # core: all-model groups only, as the strict structure metric
    assert mixed["core_all_proposals"] == {"agree": 1, "eligible": 1, "rate": 1.0}
    assert mixed["bundle"]["eligible"] == 1 and mixed["all_model_bundle"]["eligible"] == 0
    unreadable = groups(["s", None, "s"], [1, None, 1], {})
    assert unreadable["core"]["eligible"] == unreadable["core_all_proposals"]["eligible"] == 0
    rules = groups(["s", "s", "s"], [1, 1, 1], {}, adopted=(False, False, False))
    assert rules["bundle"]["eligible"] == rules["core"]["eligible"] == 0


def test_table_bundles_ignore_requirement_identity_and_status_but_not_the_tables():
    analysis = {"interpretation": {"requirements": [{"id": "r1", "context": "first"}, {"id": "r2", "context": "second"}]}}
    rows = [{"requirement_id": rid, "status": "selected", "tables": [{"org_id": "o", "table_id": tid}]}
            for rid, tid in (("r1", "t1"), ("r2", "t2"))]
    swapped = copy.deepcopy(rows)
    swapped[0]["tables"], swapped[1]["tables"] = swapped[1]["tables"], swapped[0]["tables"]
    swapped[0]["status"] = "confirm"
    assert M.bundle_signature(rows) == M.bundle_signature(swapped)
    assert M.selection_signature(analysis, rows) != M.selection_signature(analysis, swapped)
    merged = [{"requirement_id": "r1", "status": "selected", "tables": rows[0]["tables"] + rows[1]["tables"]}]
    assert M.bundle_signature(rows) != M.bundle_signature(merged)
    other = copy.deepcopy(rows)
    other[1]["tables"][0]["table_id"] = "t3"
    assert M.bundle_signature(rows) != M.bundle_signature(other)
