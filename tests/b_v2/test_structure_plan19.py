"""B reading the interpretation's structure (A plan 19 contract document of 2026-10-21, plan 06).

Inputs are the document's section 5 examples, synthetic, with the example values as written.
Where the document writes a placeholder ("…" for candidate rows, "<markers[0]과 같은 참조>" for
evidence refs, "<가상서울 breakdown_value>" for breakdown refs) the test fills in the object the
placeholder names, in the same reference shape. Model answers are synthetic as well.
"""

from __future__ import annotations

import copy

import pytest
from b_v2_support import V3CUR_FIXTURES, load_v3

from claview.b.adapter_v3 import to_b_request
from claview.b.contract import (
    B_CONTRACT_VERSION,
    STRUCTURE_VERSIONS,
    check_decomposition_structure,
    check_request,
    issue_of,
)
from claview.b.run import run_b_request
from claview.contracts import Config, Trace
from claview.models import ScriptedModel
from claview.services import Services

AS_OF = "2026-10-20"
VERSION = "structure-2026-10-21.1"


def ref(place, segment_ids, span, quote, name, candidates):
    return {
        "place": place,
        "segment_ids": segment_ids,
        "span": span,
        "quote": quote,
        "name": name,
        "candidates": candidates,
    }


# ---- document 5: "가상서울 말고 가상부산 가상대출금", one requirement
BUSAN_ROW = {
    "table_id": "T_A",
    "obj_id": "REG",
    "value_id": "ra2",
    "value_name": "가상부산",
    "role": "target",
    "path": "exact",
    "type": "item_row",
}
# the document's "…" candidate rows: the same row shape for the name each reference quotes
SEOUL_ROW = {**BUSAN_ROW, "value_id": "ra1", "value_name": "가상서울"}
TARGET = ref("target", ["s5"], [8, 12], "가상부산", "가상부산", [BUSAN_ROW])
MARKER = ref("polarity_marker", ["s3"], [5, 7], "말고", None, [])
NEGATIVE = ref("polarity_negative", ["s1"], [0, 4], "가상서울", "가상서울", [SEOUL_ROW])
POSITIVE = ref("polarity_positive", ["s5"], [8, 12], "가상부산", "가상부산", [BUSAN_ROW])
NORMAL = {
    "version": VERSION,
    "requirements": {"r1": {"targets": [TARGET], "breakdowns": [], "basis": [], "context": None}},
    "polarity": [
        {
            "link_id": "p1",
            "kind": "replace",
            "markers": [MARKER],
            "negative": [NEGATIVE],
            "positive": [POSITIVE],
            "requirement_ids": [],
            "scope_status": "clear",
        }
    ],
    # the final document: held empty in this version, though the target is certain
    "confirmed_effects": [],
}
# the first version of the document's effect (now a contract error when listed)
EFFECT = {
    "link_id": "p1",
    "negative_ref_index": 0,
    "requirement_ids": ["r1"],
    "evidence_refs": [MARKER, NEGATIVE],  # "<markers[0]과 같은 참조>", "<negative[0]…>"
}
HELD_EMPTY = (
    "decomposition.structure.confirmed_effects: held empty in 'structure-2026-10-21.1' "
    "(no confirmed effect is made this round), got 1"
)
# ---- document 5: the breakdown group of "가상대출금을 가상서울·가상부산으로 나눠서 분기별로"
SEOUL_VALUE = ref("breakdown_value", ["s3"], [6, 10], "가상서울", "가상서울", [SEOUL_ROW])
BUSAN_VALUE = ref("breakdown_value", ["s5"], [11, 15], "가상부산", "가상부산", [BUSAN_ROW])
GROUP = {
    "group_id": "b1",
    "kind": "descendants",
    "axis": None,
    "values": [SEOUL_VALUE, BUSAN_VALUE],
    "markers": [
        ref("breakdown_marker", ["s4"], [10, 11], "·", None, []),
        ref("breakdown_marker", ["s6"], [15, 21], "으로 나눠서", None, []),
    ],
    "descendants": {"status": "unexpanded", "source_refs": [SEOUL_VALUE, BUSAN_VALUE]},
}


def one_requirement_state(structure=..., **decomposition_extra):
    """The current-A fixture of one requirement `r1` with the given structure record.

    The fixture is a rules record (`applied_source: rules`); a structure other than None is
    written the way A writes it, on an adopted model reading (`applied_source: model`), unless
    the test says otherwise."""
    state = copy.deepcopy(next(load_v3(p) for p in V3CUR_FIXTURES if p.stem == "v3cur_fx5"))
    analysis = state["analysis"]
    [req] = analysis["interpretation"]["requirements"]
    assert req["id"] == "r1"
    req["breakdown"] = ["업권별"]
    analysis["judgement"]["negated"] = ["가계"]  # what the old words would decide
    decomposition = analysis["decomposition"]
    assert decomposition["applied_source"] == "rules"
    if structure is not ...:
        decomposition["structure"] = copy.deepcopy(structure)
        if structure is not None:
            decomposition["applied_source"] = "model"
    decomposition.update(copy.deepcopy(decomposition_extra))
    return state


def stops_before_the_model(store, request, detail):
    """The request carries `detail` as a request-level input contract error, no old words are
    brought back, and every scope ends before the model."""
    assert {"code": "input_contract_error", "detail": detail} in request["input_errors"]
    hints = scope_of(request)["hints"]
    assert "structure" not in hints
    assert hints["breakdown"] == [] and hints["negated"] == []
    assert not any(r["negated"] is True for r in hints["value_rows"])
    svc = services(store, {})
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)
    assert out["series"] == []


def scope_of(request, rid="r1"):
    return next(s for s in request["scopes"] if s["scope_id"] == rid)


def services(store, responses):
    return Services(store, None, ScriptedModel(responses), Config(as_of=AS_OF), Trace())


def model_calls(svc):
    return [e for e in svc.trace.events if e["event"] == "model_request"]


def test_the_supported_versions_are_the_documents():
    assert STRUCTURE_VERSIONS == (VERSION,)
    assert B_CONTRACT_VERSION == "b-contract/2026-10-21.3"  # .3: A assumptions (plan 07)


# ---------------------------------------------------------------- valid structures


def test_the_documents_normal_example_is_carried_for_its_requirement():
    assert check_decomposition_structure(NORMAL, ["r1"]) == []
    request = to_b_request(one_requirement_state(NORMAL))
    assert check_request(request) == [] and "input_errors" not in request
    hints = scope_of(request)["hints"]
    structure = hints["structure"]
    assert structure["version"] == VERSION
    assert structure["requirement"] == NORMAL["requirements"]["r1"]
    assert structure["polarity"] == NORMAL["polarity"]  # empty requirement_ids: every requirement
    assert structure["confirmed_effects"] == NORMAL["confirmed_effects"]
    assert hints["breakdown"] == [] and hints["negated"] == []  # old words not re-applied
    assert all(r["negated"] is None for r in hints["value_rows"])
    assert "structure_reason" not in hints


def test_the_documents_breakdown_group_is_carried_as_given():
    structure = copy.deepcopy(NORMAL)
    structure["requirements"]["r1"]["breakdowns"] = [GROUP]
    request = to_b_request(one_requirement_state(structure))
    assert check_request(request) == []
    assert scope_of(request)["hints"]["structure"]["requirement"]["breakdowns"] == [GROUP]


def test_an_uncertain_link_is_carried_and_never_becomes_an_exclusion():
    structure = copy.deepcopy(NORMAL)
    structure["polarity"][0]["scope_status"] = "uncertain"
    structure["confirmed_effects"] = []  # a negation without a confirmed effect
    request = to_b_request(one_requirement_state(structure))
    hints = scope_of(request)["hints"]
    assert hints["structure"]["polarity"][0]["scope_status"] == "uncertain"
    assert hints["structure"]["confirmed_effects"] == []
    assert not any(r["negated"] is True for r in hints["value_rows"])
    old = scope_of(to_b_request(one_requirement_state()))["hints"]
    assert any(r["negated"] is True for r in old["value_rows"])  # the words alone would


# ---------------------------------------------------------------- the version gate


def test_an_unsupported_version_stops_and_never_brings_back_the_old_words(store):
    """User decision 10/21 05:13 (3): not a notice and the old path, a contract error."""
    structure = {**copy.deepcopy(NORMAL), "version": "structure-2099-01-01.1"}
    structure["polarity"] = "anything"  # not read further
    request = to_b_request(one_requirement_state(structure))
    assert len(request["input_errors"]) == 1
    stops_before_the_model(
        store, request, "structure_unsupported_version: 'structure-2099-01-01.1'"
    )
    assert "adapter_notices" not in scope_of(request)
    # the old words would mark a row negated: the stop is not the old path
    old = scope_of(to_b_request(one_requirement_state()))["hints"]
    assert old["negated"] == ["가계"] and any(r["negated"] is True for r in old["value_rows"])


# ---------------------------------------------------------------- three kinds of null (decision 4)


def test_a_missing_structure_field_is_an_older_output_on_the_old_path():
    """(a) no `structure` field at all: the old fields as before."""
    state = one_requirement_state()
    assert "structure" not in state["analysis"]["decomposition"]
    request = to_b_request(state)
    assert check_request(request) == [] and "input_errors" not in request
    hints = scope_of(request)["hints"]
    assert "structure" not in hints and "structure_reason" not in hints
    assert hints["breakdown"] == ["업권별"] and hints["negated"] == ["가계"]


@pytest.mark.parametrize("source", ["rules", "rules_fallback"])
def test_a_rules_null_with_its_reason_keeps_the_reason_and_the_old_fields(source):
    """(b) structure None with reason rules_structure_unrecorded, the rules adopted or
    fallen back to."""
    state = one_requirement_state(
        None, structure_reason="rules_structure_unrecorded", applied_source=source
    )
    request = to_b_request(state)
    assert check_request(request) == [] and "input_errors" not in request
    hints = scope_of(request)["hints"]
    assert hints["structure_reason"] == "rules_structure_unrecorded"
    assert "structure" not in hints and hints["breakdown"] == ["업권별"]
    assert hints["negated"] == ["가계"]
    assert all(isinstance(r["negated"], bool) for r in hints["value_rows"])


@pytest.mark.parametrize(
    ("record", "detail"),
    [
        (
            {"applied_source": "model", "structure_reason": "rules_structure_unrecorded"},
            "decomposition.structure: null on an adopted reading ('model')",
        ),
        (
            {"applied_source": "model"},
            "decomposition.structure: null on an adopted reading ('model')",
        ),
        ({}, "decomposition.structure_reason: None does not name the null"),
        (
            {"structure_reason": "something_else"},
            "decomposition.structure_reason: 'something_else' does not name the null",
        ),
        (
            {"applied_source": "rules_fallback"},
            "decomposition.structure_reason: None does not name the null",
        ),
    ],
)
def test_a_null_breaking_the_contract_stops_before_the_model(store, record, detail):
    """(c) a null on an adopted model reading, or a null that does not name its reason."""
    request = to_b_request(one_requirement_state(None, **record))
    stops_before_the_model(store, request, detail)


def test_a_lone_reason_without_the_structure_field_is_a_contract_error(store):
    state = one_requirement_state(structure_reason="rules_structure_unrecorded")
    request = to_b_request(state)
    stops_before_the_model(
        store, request, "decomposition.structure_reason: given without a structure field"
    )


@pytest.mark.parametrize("source", ["rules", "rules_fallback"])
def test_a_structure_on_a_rules_record_is_a_contract_error(store, source):
    """Contract document 4 (7): a rules record keeps no structure."""
    request = to_b_request(one_requirement_state(NORMAL, applied_source=source))
    stops_before_the_model(
        store, request, f"decomposition.structure: given on a rules record ({source!r})"
    )


def test_a_reason_next_to_a_structure_is_a_contract_error(store):
    request = to_b_request(
        one_requirement_state(NORMAL, structure_reason="rules_structure_unrecorded")
    )
    stops_before_the_model(
        store, request, "decomposition.structure_reason: given next to a structure"
    )


def test_the_alternative_readings_structure_is_never_read():
    alternative = {"reading_id": "R2", "structure": copy.deepcopy(NORMAL)}
    state = one_requirement_state(None, structure_reason="rules_structure_unrecorded")
    state["analysis"]["decomposition"]["alternative"] = alternative
    hints = scope_of(to_b_request(state))["hints"]
    assert "structure" not in hints and hints["negated"] == ["가계"]


# ---------------------------------------------------------------- contract errors


def _broken(change):
    structure = copy.deepcopy(NORMAL)
    structure["confirmed_effects"] = [copy.deepcopy(EFFECT)]  # read even when listed
    change(structure)
    if structure["confirmed_effects"] == [EFFECT]:
        structure["confirmed_effects"] = []  # not an effect case: the list stays held empty
    return structure


BROKEN = {
    "requirement_keys_differ": (
        lambda s: s["requirements"].update({"r9": s["requirements"]["r1"]}),
        "decomposition.structure.requirements: keys differ from the reading's requirement IDs",
    ),
    "effect_outside_link": (
        lambda s: (
            s["polarity"][0].update(requirement_ids=["r1"])
            or s["confirmed_effects"][0].update(requirement_ids=["r9"])
        ),
        "decomposition.structure.confirmed_effects[0].requirement_ids: outside the link's scope",
    ),
    "unknown_field": (
        lambda s: s["polarity"][0].update(extra=1),
        "decomposition.structure.polarity[0]: unknown field 'extra'",
    ),
    "missing_field": (
        lambda s: s["requirements"]["r1"].pop("basis"),
        "decomposition.structure.requirements.r1: missing field 'basis'",
    ),
    "unknown_link": (
        lambda s: s["confirmed_effects"][0].update(link_id="p9"),
        "decomposition.structure.confirmed_effects[0].link_id: unknown link",
    ),
    "index_outside": (
        lambda s: s["confirmed_effects"][0].update(negative_ref_index=1),
        "decomposition.structure.confirmed_effects[0].negative_ref_index: outside the link",
    ),
    "descendants_on_values": (
        lambda s: s["requirements"]["r1"].update(breakdowns=[{**GROUP, "kind": "values"}]),
        "decomposition.structure.requirements.r1.breakdowns[0].descendants: "
        "only and always for kind descendants",
    ),
    "bad_scope_status": (
        lambda s: s["polarity"][0].update(scope_status="maybe"),
        "decomposition.structure.polarity[0].scope_status: unknown",
    ),
    "version_missing": (
        lambda s: s.pop("version"),
        "decomposition.structure: missing field 'version'",
    ),
    "candidate_empty": (
        lambda s: s["requirements"]["r1"]["targets"][0].update(candidates=[{}]),
        "decomposition.structure.requirements.r1.targets[0].candidates[0]: missing field 'obj_id'",
    ),
    "candidate_extra": (
        lambda s: s["polarity"][0]["negative"][0]["candidates"][0].update(extra=1),
        "decomposition.structure.polarity[0].negative[0].candidates[0]: unknown field 'extra'",
    ),
    "candidate_role": (
        lambda s: s["requirements"]["r1"]["targets"][0]["candidates"][0].update(role="x"),
        "decomposition.structure.requirements.r1.targets[0].candidates[0].role: unknown",
    ),
}


@pytest.mark.parametrize(
    ("bindings", "expected"),
    [
        ("bad", "decomposition.bindings: not a list"),
        ([1], "decomposition.bindings[0]: not an object"),
        ([{"scope": "question"}], "decomposition.bindings[0]: missing field 'condition_id'"),
        (
            [{"condition_id": "P1", "scope": "all", "requirement_ids": [], "name_refs": []}],
            "decomposition.bindings[0].scope: unknown",
        ),
        (
            [{"condition_id": "P1", "scope": "question", "requirement_ids": [], "name_refs": [{}]}],
            "decomposition.bindings[0].name_refs[0]: missing field 'segment_ids'",
        ),
    ],
)
def test_bindings_carried_with_a_structure_are_checked_never_dropped(store, bindings, expected):
    request = to_b_request(one_requirement_state(NORMAL, bindings=bindings))
    assert {"code": "input_contract_error", "detail": expected} in request["input_errors"]
    assert "structure" not in scope_of(request)["hints"]
    svc = services(store, {})
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


def test_bindings_are_not_judged_without_a_structure():
    """The old path keeps its behaviour: a rules record's bindings are not checked here."""
    state = one_requirement_state(None, structure_reason="rules_structure_unrecorded", bindings=[1])
    assert "input_errors" not in to_b_request(state)


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_structure_breaking_the_contract_ends_every_scope_before_the_model(store, case):
    change, expected = BROKEN[case]
    request = to_b_request(one_requirement_state(_broken(change)))
    errors = request["input_errors"]
    assert {"code": "input_contract_error", "detail": expected} in errors
    assert all("scope_id" not in e for e in errors)  # the question's structure: request level
    assert "structure" not in scope_of(request)["hints"]
    svc = services(store, {})
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


def test_a_structure_that_is_no_object_is_a_contract_error():
    request = to_b_request(one_requirement_state("structure"))
    assert request["input_errors"] == [
        {"code": "input_contract_error", "detail": "decomposition.structure: not an object or None"}
    ]


def test_the_slice_check_guards_the_request():
    request = to_b_request(one_requirement_state(NORMAL))
    hints = scope_of(request)["hints"]
    hints["structure_reason"] = "rules_structure_unrecorded"
    assert any("structure_reason" in p for p in check_request(request))
    del hints["structure_reason"]
    hints["structure"]["version"] = "structure-2099-01-01.1"
    assert any("structure.version" in p for p in check_request(request))


# ---------------------------------------------------------------- settled IDs, two requirements


def _two_requirements(structure_for):
    state = copy.deepcopy(next(load_v3(p) for p in V3CUR_FIXTURES if p.stem == "v3cur_fx2"))
    analysis = state["analysis"]
    first, second = [r["id"] for r in analysis["interpretation"]["requirements"]]
    tables = next(s["tables"] for s in state["selections"] if s["tables"])
    for sel in state["selections"]:
        sel["tables"] = sel["tables"] or copy.deepcopy(tables)
        sel["status"] = "auto"
    decomposition = analysis["decomposition"]
    decomposition["requirement_ids"] = {first: second, second: first}  # settled -> proposal
    decomposition["bindings"] = [
        {"condition_id": cid, "scope": scope, "requirement_ids": ids, "name_refs": []}
        for cid, scope, ids in (("Q1", "requirements", [second]), ("Q2", "unresolved", []))
    ]
    decomposition["structure"] = structure_for(first, second)
    decomposition["applied_source"] = "model"
    return state, first, second


def _per_requirement(first, second):
    empty = {"targets": [], "breakdowns": [], "basis": [], "context": None}
    link = copy.deepcopy(NORMAL["polarity"][0])
    whole = {**copy.deepcopy(link), "link_id": "p2", "requirement_ids": []}
    return {
        "version": VERSION,
        "requirements": {first: {**empty, "targets": [TARGET]}, second: copy.deepcopy(empty)},
        "polarity": [{**link, "requirement_ids": [second]}, whole],
        "confirmed_effects": [],  # held empty in this version
    }


def test_structure_ids_are_settled_ids_and_bindings_keep_their_proposal_ids():
    state, first, second = _two_requirements(_per_requirement)
    request = to_b_request(state)
    assert check_request(request) == [] and "input_errors" not in request
    a, b = (scope_of(request, r)["hints"]["structure"] for r in (first, second))
    assert a["requirement"]["targets"] == [TARGET] and b["requirement"]["targets"] == []
    assert [p["link_id"] for p in a["polarity"]] == ["p2"]  # whole-question link only
    assert [p["link_id"] for p in b["polarity"]] == ["p1", "p2"]
    assert a["confirmed_effects"] == b["confirmed_effects"] == []
    # bindings name proposal IDs: proposal `second` is settled `first`
    assert [x["condition_id"] for x in a["condition_bindings"]] == ["Q1", "Q2"]
    assert [x["condition_id"] for x in b["condition_bindings"]] == ["Q2"]


def test_a_reference_candidate_without_a_fetch_field_gets_fetch_false():
    state = one_requirement_state(NORMAL)
    [sel] = state["selections"]
    table_id = sel["tables"][0]["table_id"]
    row = {**BUSAN_ROW, "table_id": table_id}
    sel["reference_candidates"] = [row]
    assert "fetch" not in row
    candidates = scope_of(to_b_request(state))["hints"]["reference_candidates"]
    assert candidates == [{**row, "fetch": False}]


# ---------------------------------------------------------------- bundle review fixes


@pytest.mark.parametrize("source", ["gpt", "", None, False, []])
def test_only_an_adopted_model_reading_keeps_a_structure(store, source):
    request = to_b_request(one_requirement_state(NORMAL, applied_source=source))
    stops_before_the_model(
        store, request, f"decomposition.structure: given on a rules record ({source!r})"
    )


@pytest.mark.parametrize(
    ("public", "expected"),
    [
        ([7], "selection.reference_candidates[0]: not an object"),
        ("x", "selection.reference_candidates: not a list"),
        ([{"table_id": "T"}], "selection.reference_candidates[0]: missing field 'obj_id'"),
        (
            [{**BUSAN_ROW, "fetch": True}],
            "selection.reference_candidates[0]: unknown field 'fetch'",
        ),
    ],
)
def test_public_reference_candidates_are_mention_candidates(store, public, expected):
    state = one_requirement_state(NORMAL)
    [sel] = state["selections"]
    sel["reference_candidates"] = public
    request = to_b_request(state)
    assert {"code": "input_contract_error", "detail": expected, "scope_id": "r1"} in (
        request["input_errors"]
    )
    svc = services(store, {})
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


@pytest.mark.parametrize(
    ("bundle", "expected"),
    [
        ({"bundle_id": "b1"}, "bundle: missing field 'context'"),
        (
            {"bundle_id": "b1", "context": None, "assignments": {}},
            "bundle.context: not an object",
        ),
        (
            {
                "bundle_id": "b1",
                "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
                "assignments": {},
                "extra": 1,
            },
            "bundle: unknown field 'extra'",
        ),
        (
            {
                "bundle_id": "b1",
                "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
            },
            "bundle: missing field 'assignments'",
        ),
    ],
)
def test_a_public_bundle_is_exactly_a_selection_bundle(store, bundle, expected):
    state = one_requirement_state()
    [sel] = state["selections"]
    sel["bundle"] = bundle
    request = to_b_request(state)
    assert {"code": "input_contract_error", "detail": expected, "scope_id": "r1"} in (
        request["input_errors"]
    )
    for errors in (True, False):  # the adapter's errors, then the runner on a bare request
        req = copy.deepcopy(request)
        if not errors:
            del req["input_errors"]
        svc = services(store, {})
        [out] = run_b_request(req, svc)["scopes"]
        assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


def test_a_null_public_bundle_is_a_contract_error_not_a_plan17_fallback(store):
    state = one_requirement_state()
    [sel] = state["selections"]
    table_id = sel["tables"][0]["table_id"]
    staged = {
        "bundle_id": "b1",
        "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
        "assignments": {table_id: [{"start": "2020-01", "end": "2024-12"}]},
    }
    sel["bundle"] = None
    sel["signals"]["plan17"] = {"b_handoff_ready": True, "bundle": staged}
    request = to_b_request(state)
    sc = scope_of(request)
    assert "bundle" in sc and sc["bundle"] is None  # the public field, not the plan-17 record
    assert {
        "code": "input_contract_error",
        "detail": "bundle: needs a bundle_id",
        "scope_id": "r1",
    } in request["input_errors"]
    assert check_request(request)
    for errors in (True, False):
        req = copy.deepcopy(request)
        if not errors:
            del req["input_errors"]
        svc = services(store, {})
        [out] = run_b_request(req, svc)["scopes"]
        assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


# ---------------------------------------------------------------- final document (10/21 14:13)


def test_a_listed_confirmed_effect_is_a_contract_error_and_its_rules_are_still_read(store):
    structure = copy.deepcopy(NORMAL)
    structure["confirmed_effects"] = [copy.deepcopy(EFFECT)]
    assert check_decomposition_structure(structure, ["r1"]) == [HELD_EMPTY]
    request = to_b_request(one_requirement_state(structure))
    stops_before_the_model(store, request, HELD_EMPTY)
    structure["confirmed_effects"][0]["link_id"] = "p9"  # an effect that also breaks a rule
    problems = check_decomposition_structure(structure, ["r1"])
    assert problems == [
        HELD_EMPTY,
        "decomposition.structure.confirmed_effects[0].link_id: unknown link",
    ]


def test_a_mention_span_is_accepted_and_changes_nothing():
    plain = to_b_request(one_requirement_state(NORMAL))
    state = one_requirement_state(NORMAL)
    for i, mention in enumerate(state["analysis"]["mentions"]):
        mention["span"] = [i, i + 1]  # the new optional field of the final document
    assert to_b_request(state) == plain


# ---------------------------------------------------------------- the question-word exclusion rule


def _spy_rule(monkeypatch):
    import claview.stages as stages

    seen = []
    real = stages.correct_to_named_value

    def spy(*args, **kwargs):
        seen.append(kwargs.get("structured", False))
        return real(*args, **kwargs)

    monkeypatch.setattr(stages, "correct_to_named_value", spy)
    return seen


def _answers_for(store, request):
    out = {}
    for sc in request["scopes"]:
        for t in sc["tables"]:
            meta = store.get(t["org_id"], t["table_id"])
            frequency = next(iter(meta["periods"]))
            row = {
                "item_id": next(iter(meta["items"])),
                "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                "period": {"frequency": frequency, "start": "x", "end": "x"},
                "expand": {a: "none" for a in meta["dimensions"]},
            }
            out[f"B:{t['org_id']}:{t['table_id']}"] = [{"selections": [row]}]
    return out


@pytest.mark.parametrize("structured", [True, False])
def test_the_runner_turns_the_exclusion_rule_off_only_under_a_structure(
    store, monkeypatch, structured
):
    seen = _spy_rule(monkeypatch)
    state = (
        one_requirement_state(NORMAL)
        if structured
        else one_requirement_state(None, structure_reason="rules_structure_unrecorded")
    )
    request = to_b_request(state)
    assert ("structure" in scope_of(request)["hints"]) is structured
    [out] = run_b_request(request, services(store, _answers_for(store, request)))["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert seen and set(seen) == {structured}


def test_effect_rules_are_reported_even_next_to_other_problems():
    """Bundle review of 10/21: the slice check and the full check both read each listed
    effect's rules, whatever else is wrong."""
    structure = copy.deepcopy(NORMAL)
    structure["confirmed_effects"] = [{**copy.deepcopy(EFFECT), "link_id": "p9"}]
    del structure["requirements"]["r1"]["basis"]
    problems = check_decomposition_structure(structure, ["r1"])
    assert "decomposition.structure.requirements.r1: missing field 'basis'" in problems
    assert HELD_EMPTY in problems
    assert "decomposition.structure.confirmed_effects[0].link_id: unknown link" in problems
    request = to_b_request(one_requirement_state(NORMAL))
    hints = scope_of(request)["hints"]
    hints["structure"]["confirmed_effects"] = [{**copy.deepcopy(EFFECT), "link_id": "p9"}]
    found = check_request(request)
    assert any(p.endswith("structure.confirmed_effects: held empty in "
                          "'structure-2026-10-21.1' (no confirmed effect is made this round), "
                          "got 1") for p in found)  # fmt: skip
    assert any(p.endswith("structure.confirmed_effects[0].link_id: unknown link") for p in found)


# ---- A's offline tests (B 전달 10/21): through the real B validate node


def _except_case(store, need=2):
    """Catalog tables (found by their values, never by ID) where the question-word exclusion
    rule would change a pick: one question, and per table the axis, the picked `~이외` value
    and the value the rule turns it into."""
    from claview.stages import excluded_named_value

    by_question: dict = {}
    for key in store.catalog:
        org, table_id = key.split(":")
        try:
            meta = store.get(org, table_id)
        except Exception:  # noqa: BLE001 - a table with broken metadata is just not picked
            continue
        for axis, dimension in meta["dimensions"].items():
            values = dimension["values"]
            excepts = [c for c, v in values.items() if v["name"].strip().endswith("이외")]
            for target in excepts:
                base = values[target]["name"].strip()[:-2]
                question = f"{base}를 뺀 물가"
                for chosen in excepts:
                    if (
                        chosen != target
                        and excluded_named_value(question, values, chosen) == target
                    ):
                        table = {
                            "org_id": org,
                            "table_id": table_id,
                            "table_name": meta["table_name"],
                        }
                        cases = by_question.setdefault(question, {})
                        cases.setdefault(table_id, (table, axis, chosen, target))
                        break
        for question, cases in by_question.items():
            if len(cases) >= need:
                return question, list(cases.values())[:need]
    raise AssertionError("no catalog tables where the exclusion rule applies")


def _through_validate(store, structure_record, monkeypatch=None):
    question, cases = _except_case(store)
    state = one_requirement_state(**structure_record)
    state["question"] = question
    [sel] = state["selections"]
    sel["tables"] = [{**table, "covers": {}} for table, _a, _c, _t in cases]
    sel["signals"] = {"facts": {"tables": {}}}
    analysis = state["analysis"]
    analysis["conditions"] = []
    [req] = analysis["interpretation"]["requirements"]
    req["condition_ids"] = []
    req["shared_condition_ids"] = []
    analysis["decomposition"]["condition_fields"] = {}
    responses = {}
    for table, axis, chosen, _target in cases:
        meta = store.get(table["org_id"], table["table_id"])
        dims = {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()}
        dims[axis] = chosen
        row = {
            "item_id": next(iter(meta["items"])),
            "dimensions": dims,
            "period": {"frequency": "Y", "start": "x", "end": "x"},
            "expand": {a: "none" for a in meta["dimensions"]},
        }
        responses[f"B:{table['org_id']}:{table['table_id']}"] = [{"selections": [row]}]
    request = to_b_request(state)
    svc = services(store, responses)
    [out] = run_b_request(request, svc)["scopes"]
    return request, out, cases, svc


def _picked(out, table_id, axis):
    [row] = [r for r in out["series"] if r["table_id"] == table_id]
    value = row["dimensions"][axis]
    return value["code"] if isinstance(value, dict) else value


def _rule_notices(out):
    return [n for n in out["notices"] if n.get("reason") == "query_excludes_named_value"]


def _structure(scope_status=None, links=True):
    structure = copy.deepcopy(NORMAL)
    if not links:
        structure["polarity"] = []
    elif scope_status:
        structure["polarity"][0]["scope_status"] = scope_status
    return {"structure": structure}


@pytest.mark.parametrize(
    "record",
    [_structure("uncertain"), _structure("clear"), _structure(links=False)],
    ids=["uncertain", "clear", "no_links"],
)
def test_under_a_structure_the_question_words_change_no_value(store, record):
    """A's tests 1, 2 and 6: the adapter carries the structure, the real validate node of every
    table keeps the model's pick although the question says `Y를 뺀`."""
    request, out, cases, svc = _through_validate(store, record)
    assert "structure" in scope_of(request)["hints"]
    assert out["status"] == "ok", out["issues"]
    for table, axis, chosen, _target in cases:
        assert _picked(out, table["table_id"], axis) == chosen
    assert _rule_notices(out) == []
    assert len(model_calls(svc)) == len(cases)


@pytest.mark.parametrize(
    "record",
    [
        {},
        {
            "structure": None,
            "structure_reason": "rules_structure_unrecorded",
            "applied_source": "rules_fallback",
        },
    ],
    ids=["older_output", "rules_null"],
)
def test_on_the_old_paths_the_question_words_still_change_the_value(store, record):
    """A's test 3: no structure field, or an allowed rules null: the old correction as before."""
    request, out, cases, _svc = _through_validate(store, record)
    assert "structure" not in scope_of(request)["hints"]
    assert out["status"] == "ok", out["issues"]
    for table, axis, _chosen, target in cases:
        assert _picked(out, table["table_id"], axis) == target
    assert len(_rule_notices(out)) == len(cases)


@pytest.mark.parametrize(
    "record",
    [
        {"structure": {**copy.deepcopy(NORMAL), "version": "structure-2099-01-01.1"}},
        {"structure": None, "applied_source": "model"},
        {"structure": None},
        {"structure": {**copy.deepcopy(NORMAL), "confirmed_effects": [copy.deepcopy(EFFECT)]}},
    ],
    ids=["unsupported_version", "null_on_model", "null_without_reason", "listed_effect"],
)
def test_broken_structure_records_never_reach_the_old_correction(store, record):
    """A's tests 4 and 5: an input contract error, no old correction, no model call, no query."""
    request, out, _cases, svc = _through_validate(store, record)
    assert request["input_errors"]
    assert out["issues"][0] == issue_of("input_contract_error")
    assert out["series"] == [] and _rule_notices(out) == [] and not model_calls(svc)


def test_effect_rules_are_reported_next_to_top_level_field_problems():
    """Bundle re-review of 10/21: an unknown top-level field does not hide the effect rules."""
    structure = copy.deepcopy(NORMAL)
    structure["confirmed_effects"] = [{**copy.deepcopy(EFFECT), "link_id": "p9"}]
    structure["extra"] = True
    problems = check_decomposition_structure(structure, ["r1"])
    assert problems[0] == "decomposition.structure: unknown field 'extra'"
    assert HELD_EMPTY in problems
    assert "decomposition.structure.confirmed_effects[0].link_id: unknown link" in problems
    request = to_b_request(one_requirement_state(NORMAL))
    hints = scope_of(request)["hints"]
    hints["structure"]["confirmed_effects"] = [{**copy.deepcopy(EFFECT), "link_id": "p9"}]
    hints["structure"]["extra"] = True
    found = check_request(request)
    assert any(p.endswith("structure: unknown field 'extra'") for p in found)
    assert any(p.endswith("structure.confirmed_effects[0].link_id: unknown link") for p in found)
    assert any("held empty" in p for p in found)
