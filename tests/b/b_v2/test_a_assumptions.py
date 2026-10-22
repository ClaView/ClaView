"""A's public assumptions through B (plan 07, user's B brief "완료 2" of 2026-10-21).

States are the current-A fixtures with an envelope built by the test (synthetic). Model answers
are synthetic as well; nothing here is a claim about model accuracy or a real A output.
"""

from __future__ import annotations

import copy

import pytest
from b_v2_support import V3CUR_FIXTURES, load_v3

from claview.b.adapter_v3 import to_b_request
from claview.b.contract import (
    A_ASSUMPTION_VERSIONS,
    B_CONTRACT_VERSION,
    check_a_assumptions,
    check_b_result,
    check_request,
    check_scope_result,
    issue_of,
)
from claview.b.run import run_b_request
from claview.contracts import Config, Trace
from claview.models import ScriptedModel
from claview.services import Services

AS_OF = "2026-10-20"
VERSION = "a-assumptions/2026-10-21.1"


def item(assumption_id="a:0f3c", requirement_id="r1", **change):
    out = {
        "assumption_id": assumption_id,
        "stage": "A",
        "requirement_ids": [requirement_id],
        "requirement_keys": [{"reading_id": "primary", "requirement_id": requirement_id}],
        "refs": {"segment_ids": [], "mention_ids": [], "condition_ids": []},
        "reason_code": "first_assignment",
        "adopted": {"value": None, "interpretation": "실제로 채택한 내용"},
        "source": "default",
        "confirmed": False,
    }
    out.update(change)
    return out


def envelope(*items):
    return {"version": VERSION, "items": list(items)}


def state(stem="v3cur_fx5"):
    """One requirement `r1` (a rules record: the old path). fx5 states its frequency and
    period; fx6 ("기업대출 금액을 보여줘") leaves both to B's defaults (stage-B assumptions)."""
    out = copy.deepcopy(next(load_v3(p) for p in V3CUR_FIXTURES if p.stem == stem))
    assert [r["id"] for r in out["analysis"]["interpretation"]["requirements"]] == ["r1"]
    return out


def services(store, responses):
    return Services(store, None, ScriptedModel(responses), Config(as_of=AS_OF), Trace())


def model_calls(svc):
    return [e for e in svc.trace.events if e["event"] == "model_request"]


def answers(store, request):
    out = {}
    for sc in request["scopes"]:
        for t in sc["tables"]:
            meta = store.get(t["org_id"], t["table_id"])
            row = {
                "item_id": next(iter(meta["items"])),
                "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
                "expand": {a: "none" for a in meta["dimensions"]},
            }
            out[f"B:{t['org_id']}:{t['table_id']}"] = [{"selections": [row]}]
    return out


def run(store, request):
    svc = services(store, answers(store, request))
    return run_b_request(request, svc), svc


def test_the_versions():
    assert A_ASSUMPTION_VERSIONS == (VERSION,)
    assert B_CONTRACT_VERSION == "b-contract/2026-10-21.3"


# ---------------------------------------------------------------- round trips


def test_without_assumptions_nothing_changes(store):
    request = to_b_request(state("v3cur_fx6"))  # fx6: B assumes, so entries exist
    assert "a_assumptions" not in request and check_request(request) == []
    result, _svc = run(store, request)
    assert "a_assumptions" not in result
    [sc] = result["scopes"]
    assert sc["shared_assumptions"]
    assert all("supersedes" not in e for e in sc["shared_assumptions"])  # the old shape
    assert check_b_result(result, request) == []


@pytest.mark.parametrize(
    "given",
    [envelope(), envelope(item(), item("a:77aa", adopted={"value": {"k": [1, 2.5, True, None]},
                                                         "interpretation": "다른 채택"}))],
    ids=["empty", "two_items"],
)  # fmt: skip
def test_an_envelope_comes_back_unchanged_and_apart_from_stage_b(store, given):
    before = copy.deepcopy(given)
    request = to_b_request(state("v3cur_fx6"), a_assumptions=given)
    assert check_request(request) == [] and "input_errors" not in request
    assert request["a_assumptions"] == before
    result, _svc = run(store, request)
    [out] = result["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert result["a_assumptions"] == before and given == before
    assert result["a_assumptions"] is not request["a_assumptions"]  # a copy, never shared
    a_ids = {i["assumption_id"] for i in before["items"]}
    assert out["shared_assumptions"]  # fx6: B assumes the frequency and the period
    for entry in out.get("shared_assumptions") or []:
        assert entry["stage"] == "B" and entry["assumption_id"] not in a_ids  # never merged
        assert entry["supersedes"] is None  # no ground to replace an A assumption: not guessed
    assert check_scope_result(out) == []
    assert check_b_result(result, request) == []


def test_the_same_ids_come_back_the_same_after_a_resume(store):
    """A makes an ID from the request, question version, reading, references, reason and the
    adopted value (no turn number): two runs with the same envelope return the same IDs."""
    given = envelope(item(), item("a:77aa"))
    first, _ = run(store, to_b_request(state(), a_assumptions=copy.deepcopy(given)))
    again, _ = run(store, to_b_request(state(), a_assumptions=copy.deepcopy(given)))
    assert first["a_assumptions"] == again["a_assumptions"] == given


def test_b_never_changes_the_original_values_and_keeps_applied_apart(store):
    st = state()
    conditions = copy.deepcopy(st["analysis"]["conditions"])
    fields = copy.deepcopy(st["analysis"]["decomposition"]["condition_fields"])
    given = envelope(item(adopted={"value": "202401", "interpretation": "가정한 기간"}))
    request = to_b_request(st, a_assumptions=given)
    result, _svc = run(store, request)
    [out] = result["scopes"]
    assert st["analysis"]["conditions"] == conditions  # condition.value untouched
    assert st["analysis"]["decomposition"]["condition_fields"] == fields  # A's applied untouched
    assert result["a_assumptions"]["items"][0]["adopted"]["value"] == "202401"
    assert all(
        "applied" not in f for f in request["scopes"][0]["hints"]["condition_fields"].values()
    )
    assert {f["applied"] for f in out["condition_fields"].values()} <= {True, False, None}


# ---------------------------------------------------------------- broken envelopes


BROKEN = {
    "version": (
        {"version": "a-assumptions/2099-01-01.1", "items": []},
        "a_assumptions.version: unsupported version 'a-assumptions/2099-01-01.1'",
    ),
    "envelope_extra": (
        {**envelope(), "extra": 1},
        "a_assumptions: unknown field 'extra'",
    ),
    "envelope_missing": ({"version": VERSION}, "a_assumptions: missing field 'items'"),
    "item_missing": (
        envelope({k: v for k, v in item().items() if k != "confirmed"}),
        "a_assumptions.items[0]: missing field 'confirmed'",
    ),
    "item_extra": (envelope(item(fetch=False)), "a_assumptions.items[0]: unknown field 'fetch'"),
    "key_extra": (
        envelope(item(requirement_keys=[{"reading_id": "p", "requirement_id": "r1", "x": 1}])),
        "a_assumptions.items[0].requirement_keys[0]: unknown field 'x'",
    ),
    "refs_missing": (
        envelope(item(refs={"segment_ids": [], "mention_ids": []})),
        "a_assumptions.items[0].refs: missing field 'condition_ids'",
    ),
    "adopted_extra": (
        envelope(item(adopted={"value": 1, "interpretation": "x", "more": 2})),
        "a_assumptions.items[0].adopted: unknown field 'more'",
    ),
    "duplicate_id": (
        envelope(item(), item()),
        "a_assumptions.items[1].assumption_id: used twice in the envelope",
    ),
    "empty_id": (envelope(item("")), "a_assumptions.items[0].assumption_id: not text"),
    "stage": (envelope(item(stage="B")), "a_assumptions.items[0].stage: must be 'A'"),
    "source": (envelope(item(source="model")), "a_assumptions.items[0].source: unknown"),
    "confirmed": (
        envelope(item(confirmed="no")), "a_assumptions.items[0].confirmed: not a boolean"
    ),
    "reason": (envelope(item(reason_code="")), "a_assumptions.items[0].reason_code: not text"),
    "interpretation": (
        envelope(item(adopted={"value": 1, "interpretation": ""})),
        "a_assumptions.items[0].adopted.interpretation: not text",
    ),
    "value": (
        envelope(item(adopted={"value": float("nan"), "interpretation": "x"})),
        "a_assumptions.items[0].adopted.value: not a JSON value",
    ),
    "duplicate_ref": (
        envelope(item(refs={"segment_ids": ["s1", "s1"], "mention_ids": [], "condition_ids": []})),
        "a_assumptions.items[0].refs.segment_ids: not a list of distinct text",
    ),
    "ids_vs_keys": (
        envelope(item(requirement_ids=["r1", "r2"])),
        "a_assumptions.items[0].requirement_ids: differ from the keys' requirement_id",
    ),
    "unknown_requirement": (
        envelope(item(requirement_id="r9")),
        "a_assumptions.items[0].requirement_ids: 'r9' is no requirement of A",
    ),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_broken_envelope_stops_before_the_model_and_the_fetch(store, case):
    given, expected = BROKEN[case]
    request = to_b_request(state(), a_assumptions=copy.deepcopy(given))
    assert {"code": "input_contract_error", "detail": expected} in request["input_errors"]
    result, svc = run(store, request)
    [out] = result["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error")
    assert not model_calls(svc) and result["selections"] == []
    assert "a_assumptions" in result  # still returned as received (the error is reported)
    if case != "unknown_requirement":  # the request check sees the shape (not A's list)
        assert any(p.startswith("request.a_assumptions") for p in check_request(request))


@pytest.mark.parametrize("case", ["version", "duplicate_id", "item_extra"])
def test_the_runner_stops_a_broken_envelope_built_outside_the_adapter(store, case):
    given, expected = BROKEN[case]
    request = to_b_request(state())
    request["a_assumptions"] = copy.deepcopy(given)
    result, svc = run(store, request)
    [out] = result["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error")
    assert {"kind": "input", "code": "input_contract_error", "detail": expected} in out["notices"]
    assert not model_calls(svc)


def test_the_check_never_raises_on_wrong_types():
    for bad in (None, 1, [], {"version": VERSION, "items": [1, None, {}]}, envelope(item(
            requirement_keys=[{"reading_id": [], "requirement_id": {}}], requirement_ids=[[]],
            refs={"segment_ids": [[]], "mention_ids": 1, "condition_ids": None}))):  # fmt: skip
        assert check_a_assumptions(bad)


# ---------------------------------------------------------------- supersedes


Z_REFS = {"segment_ids": [], "mention_ids": [], "condition_ids": ["Z1"]}


def _with_supersedes(store, target, items=None, b_conditions=("Z1",)):
    """A run with an envelope, its first stage-B entry then made to name `target` (B itself
    never links; the checker is what is tested). The default A items stand for condition Z1."""
    given = envelope(
        *(items or [item(refs=Z_REFS), item("a:other", requirement_id="r1", refs=Z_REFS)])
    )
    request = to_b_request(state("v3cur_fx6"), a_assumptions=given)
    result, _svc = run(store, request)
    [out] = result["scopes"]
    assert out["shared_assumptions"], "the fixture needs a stage-B assumption"
    entry = out["shared_assumptions"][0]
    entry["supersedes"] = target
    entry["refs"] = {**entry["refs"], "condition_ids": list(b_conditions)}
    return result, request


def test_a_supersedes_naming_an_a_assumption_of_the_same_requirement_is_valid(store):
    result, request = _with_supersedes(store, "a:0f3c")
    assert check_b_result(result, request) == []
    assert check_scope_result(result["scopes"][0]) == []


def test_a_supersedes_naming_no_assumption_of_the_request_is_an_error(store):
    result, request = _with_supersedes(store, "a:missing")
    assert check_b_result(result, request) == [
        "result.scopes[0].shared_assumptions[0].supersedes: "
        "'a:missing' is not in the request's A assumptions"
    ]


def test_a_supersedes_naming_another_requirements_assumption_is_an_error(store):
    st = state("v3cur_fx6")
    st["analysis"]["interpretation"]["requirements"].append(
        {**copy.deepcopy(st["analysis"]["interpretation"]["requirements"][0]), "id": "r2"}
    )  # A's requirement list holds r2 (not selected: no scope)
    given = envelope(item(), item("a:r2", requirement_id="r2"))
    request = to_b_request(st, a_assumptions=given)
    assert "input_errors" not in request
    result, _svc = run(store, request)
    result["scopes"][0]["shared_assumptions"][0]["supersedes"] = "a:r2"
    assert check_b_result(result, request) == [
        "result.scopes[0].shared_assumptions[0].supersedes: "
        "'a:r2' is another requirement's assumption"
    ]


@pytest.mark.parametrize("target", [["a:0f3c"], 7, ""])
def test_a_supersedes_is_one_id_or_none(store, target):
    result, request = _with_supersedes(store, target)
    assert any("supersedes" in p for p in check_scope_result(result["scopes"][0])) or any(
        "supersedes" in p for p in check_b_result(result, request)
    )


def test_a_returned_envelope_must_be_the_one_sent(store):
    request = to_b_request(state(), a_assumptions=envelope(item()))
    result, _svc = run(store, request)
    result["a_assumptions"]["items"][0]["confirmed"] = True
    assert check_b_result(result, request) == [
        "result.a_assumptions: not the request's envelope unchanged"
    ]
    plain = to_b_request(state())
    result, _svc = run(store, plain)
    result["a_assumptions"] = envelope()
    assert check_b_result(result, plain) == [
        "result.a_assumptions: given without one in the request"
    ]


# ---------------------------------------------------------------- review fixes (plan 07)


@pytest.mark.parametrize(
    ("b_conditions", "why"),
    [(("Z2",), "other condition"), ((), "no condition on either side")],
)
def test_a_supersedes_must_stand_for_the_same_conditions(store, b_conditions, why):
    result, request = _with_supersedes(store, "a:0f3c", b_conditions=b_conditions)
    assert check_b_result(result, request) == [
        "result.scopes[0].shared_assumptions[0].supersedes: "
        "'a:0f3c' does not stand for the same conditions"
    ], why


def test_the_returned_envelope_is_compared_as_json(store):
    given = envelope(item(adopted={"value": False, "interpretation": "꺼짐"}))
    request = to_b_request(state(), a_assumptions=given)
    result, _svc = run(store, request)
    assert check_b_result(result, request) == []
    result["a_assumptions"]["items"][0]["adopted"]["value"] = 0  # == False in Python
    assert check_b_result(result, request) == [
        "result.a_assumptions: not the request's envelope unchanged"
    ]


def test_a_value_containing_itself_is_no_json_value(store):
    loop: list = []
    loop.append(loop)
    given = envelope(item(adopted={"value": loop, "interpretation": "x"}))
    assert check_a_assumptions(given) == ["a_assumptions.items[0].adopted.value: not a JSON value"]
    request = to_b_request(state())
    request["a_assumptions"] = given
    svc = services(store, answers(store, request))
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


def test_the_result_check_never_raises_on_wrong_types(store):
    request = to_b_request(state("v3cur_fx6"), a_assumptions=envelope(item()))
    result, _svc = run(store, request)
    for change in (
        lambda req, res: req["scopes"][0].update(requirement_key="bad"),
        lambda req, res: res["scopes"][0].update(shared_assumptions=1),
        lambda req, res: req.update(scopes=7),
        lambda req, res: res.update(scopes={"x": 1}),
        lambda req, res: res["scopes"][0]["shared_assumptions"][0].update(
            supersedes="a:0f3c", refs=5
        ),
    ):
        req, res = copy.deepcopy(request), copy.deepcopy(result)
        change(req, res)
        assert isinstance(check_b_result(res, req), list)


def test_the_result_check_survives_a_wrong_scope_id_and_a_cyclic_envelope(store):
    result, request = _with_supersedes(store, "a:0f3c")
    for bad in ([], {}, 3):
        res = copy.deepcopy(result)
        res["scopes"][0]["scope_id"] = bad
        assert check_b_result(res, request) == [
            "result.scopes[0].shared_assumptions[0].supersedes: "
            "'a:0f3c' is another requirement's assumption"
        ]
    loop: list = []
    loop.append(loop)
    given = envelope(item(adopted={"value": loop, "interpretation": "x"}))
    request = to_b_request(state())
    request["a_assumptions"] = given
    svc = services(store, answers(store, request))
    result = run_b_request(request, svc)  # the envelope comes back as received (an input error)
    assert result["a_assumptions"] is not given
    assert check_b_result(result, request) == []  # compared without recursing forever
