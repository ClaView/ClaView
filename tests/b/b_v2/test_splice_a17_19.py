"""B contract splice with A plans 17 and 19 (plan 06).

States are built from a current-A fixture with the selection's tables, conditions and bundle set
by the test (tables are picked from the catalog by coverage, never by ID). Model answers are
synthetic: these check code paths and contracts, not model accuracy.
"""

from __future__ import annotations

import copy

import pytest
from b_v2_support import V3_FIXTURES, V3CUR_FIXTURES, load_v3

from claview.b.adapter_v3 import to_b_request, to_legacy_a_result
from claview.b.contract import B_CONTRACT_VERSION, check_request, check_scope_result, issue_of
from claview.b.run import run_b_request
from claview.contracts import Config, Trace
from claview.models import ScriptedModel
from claview.services import Services

AS_OF = "2026-10-20"
BASE = next(p for p in V3CUR_FIXTURES if p.stem == "v3cur_fx4")  # one requirement, one period


def expr(year=None, alias=None):
    return {
        "text": "t",
        "year": year,
        "alias": alias,
        "sub_unit": None,
        "sub_value": None,
        "end_mark": False,
    }


def period(form, start=None, end=None, amount=None, unit=None, relative=False, cid="P1"):
    return {
        "id": cid,
        "kind": "period",
        "quote": "q",
        "value": None,
        "attrs": {
            "form": form,
            "start_expr": start,
            "end_expr": end,
            "open_end": form == "since",
            "amount": amount,
            "unit": unit,
            "relative": relative,
            "invalid": False,
            "extract_status": "explicit",
        },
    }


def freq(value, cid="F1"):
    return {"id": cid, "kind": "frequency", "quote": "q", "value": value, "attrs": {"value": value}}


def pick_tables(store, frequency, start, end, n):
    out = []
    for key in store.catalog:
        org, table_id = key.split(":")
        try:
            meta = store.get(org, table_id)
        except Exception:  # noqa: BLE001 - a table with broken metadata is just not picked
            continue
        bounds = meta["periods"].get(frequency)
        if bounds and bounds["start"] <= start and bounds["end"] >= end:
            out.append({"org_id": org, "table_id": table_id, "table_name": meta["table_name"]})
            if len(out) == n:
                return out
    raise AssertionError("not enough tables for the test")


def answers(store, tables, frequency):
    out = {}
    for table in tables:
        meta = store.get(table["org_id"], table["table_id"])
        row = {
            "item_id": next(iter(meta["items"])),
            "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
            "period": {"frequency": frequency, "start": "x", "end": "x"},
            "expand": {a: "none" for a in meta["dimensions"]},
        }
        out[f"B:{table['org_id']}:{table['table_id']}"] = [{"selections": [row]}]
    return out


def services(store, responses):
    return Services(store, None, ScriptedModel(responses), Config(as_of=AS_OF), Trace())


def model_calls(svc):
    return [e for e in svc.trace.events if e["event"] == "model_request"]


def state_with(tables, conditions, *, fields=None, alternatives=None, **selection_extra):
    """A current-A final state of one requirement on `tables` with `conditions`."""
    state = copy.deepcopy(load_v3(BASE))
    analysis = state["analysis"]
    analysis["conditions"] = copy.deepcopy(conditions)
    [req] = analysis["interpretation"]["requirements"]
    req["condition_ids"] = [c["id"] for c in conditions]
    analysis["decomposition"]["condition_fields"] = copy.deepcopy(
        fields if fields is not None else {c["id"]: {"source": "question", "confirmed": True}
                                           for c in conditions}
    )  # fmt: skip
    [sel] = state["selections"]
    sel["status"] = "auto"
    sel["tables"] = [{**t, "covers": {}} for t in tables]
    sel["alternatives"] = alternatives or []
    sel["signals"] = {"facts": {"tables": {t["table_id"]: {} for t in tables}}}
    sel.update(selection_extra)
    return state


def bundle(bundle_id, tables, anchor_source="table_latest", anchor=None, assignments=None):
    return {
        "bundle_id": bundle_id,
        "context": {
            "bundle_id": bundle_id,
            "anchor_source": anchor_source,
            "anchor": anchor,
            "simultaneous": anchor_source == "bundle_common",
        },
        "assignments": assignments or {},
    }


# ---------------------------------------------------------------- 1. old outputs


@pytest.mark.parametrize("path", V3CUR_FIXTURES + V3_FIXTURES, ids=lambda p: p.stem)
def test_rules_and_older_outputs_keep_their_old_hints(path):
    for sc in to_b_request(load_v3(path))["scopes"]:
        assert "structure" not in sc["hints"]
        assert all(isinstance(r["negated"], bool) for r in sc["hints"].get("value_rows", []))


# ---------------------------------------------------------------- 2. identity key


def test_an_a_meaning_hash_never_replaces_the_b_requirement_key():
    state = copy.deepcopy(next(load_v3(p) for p in V3CUR_FIXTURES if p.stem == "v3cur_fx5"))
    [req] = state["analysis"]["interpretation"]["requirements"]
    rid = req["id"]
    req["requirement_key"] = "f3a9c2e1"  # A's internal meaning hash (plan 11 R0)
    req["reading_id"] = "R1"
    [sc] = [s for s in to_b_request(state)["scopes"] if s["scope_id"] == rid]
    assert sc["requirement_key"] == {"reading_id": "R1", "requirement_id": rid}


# ---------------------------------------------------------------- 3. several stated frequencies


def test_several_stated_frequencies_of_one_requirement_end_unresolved_without_a_pick(store):
    tables = pick_tables(store, "M", "201501", "202412", 1)
    conditions = [freq("M"), freq("Q", cid="F2"), period("point", expr("2024"))]
    request = to_b_request(state_with(tables, conditions))
    svc = services(store, answers(store, tables, "M"))
    [out] = run_b_request(request, svc)["scopes"]
    assert out["status"] == "unresolved"
    assert out["issues"][0] == issue_of("conditions_incomplete")
    assert out["issues"][0]["resume_at"] == "ask_user"
    assert out["series"] == [] and not model_calls(svc)


# ---------------------------------------------------------------- 5. bundle reading order


def _plan17(b, ready):
    return {"plan17": {"mode": "refutation", "b_handoff_ready": ready, "bundle": b}}


def _scope(request):
    [sc] = request["scopes"]
    return sc


@pytest.mark.parametrize(
    ("public", "staged", "ready", "used", "codes"),
    [
        ("pub", None, None, "pub", []),
        (None, "stg", True, "stg", []),
        (None, "stg", False, None, ["plan17_bundle_not_ready"]),
        (None, "stg", None, None, ["plan17_bundle_not_ready"]),
        ("pub", "stg", True, "pub", ["bundle_sources_differ"]),
        ("pub", "pub", True, "pub", []),
        ("pub", "stg", False, "pub", ["bundle_sources_differ"]),  # readiness aside
        (None, None, None, None, []),
    ],
)
def test_the_bundle_is_read_public_first_then_a_ready_plan17_record(
    store, public, staged, ready, used, codes
):
    tables = pick_tables(store, "M", "201501", "202412", 2)
    made = {name: bundle(name, tables) for name in ("pub", "stg")}
    state = state_with(tables, [freq("M")])
    [sel] = state["selections"]
    if public:
        sel["bundle"] = made[public]
    if staged:
        signals = _plan17(made[staged], ready)
        if ready is None:
            del signals["plan17"]["b_handoff_ready"]
        sel["signals"].update(signals)
    request = to_b_request(state)
    assert check_request(request) == []
    sc = _scope(request)
    assert sc.get("bundle") == (made[used] if used else None)
    assert [n["code"] for n in sc.get("adapter_notices", [])] == codes
    if codes:
        [out] = run_b_request(request, services(store, answers(store, tables, "M")))["scopes"]
        assert [n["code"] for n in out["notices"] if n["kind"] == "adapter"] == codes
    assert request["contract_version"] == B_CONTRACT_VERSION


def test_a_bundle_needs_no_requirement_or_reading_ids(store):
    tables = pick_tables(store, "M", "201501", "202412", 2)
    b = bundle("b1", tables)
    assert "requirement_ids" not in b and "reading_id" not in b
    request = to_b_request(state_with(tables, [freq("M")], bundle=b))
    assert check_request(request) == [] and "input_errors" not in request


@pytest.mark.parametrize("broken", ["bundle_id", "table"])
def test_a_bundle_that_does_not_fit_its_selection_is_a_contract_error(store, broken):
    tables = pick_tables(store, "M", "201501", "202412", 3)
    chosen, outside = tables[:2], tables[2]
    b = bundle("b1", chosen)
    if broken == "bundle_id":
        b["context"]["bundle_id"] = "b2"
        detail = "bundle.context.bundle_id: differs from the bundle"
    else:
        b["assignments"] = {outside["table_id"]: [{"start": "2020-01", "end": "2020-12"}]}
        detail = f"bundle.assignments[{outside['table_id']}]: table not in the scope"
    request = to_b_request(state_with(chosen, [freq("M")], bundle=b))
    [error] = request["input_errors"]
    assert error["code"] == "input_contract_error" and error["detail"] == detail
    assert check_request(request)  # the B entry check sees it too
    svc = services(store, answers(store, chosen, "M"))
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error")
    assert not model_calls(svc)
    # a request built elsewhere (no input_errors) is stopped by the runner itself
    del request["input_errors"]
    svc = services(store, answers(store, chosen, "M"))
    [out] = run_b_request(request, svc)["scopes"]
    assert out["issues"][0] == issue_of("input_contract_error") and not model_calls(svc)


def test_reference_candidates_are_read_public_first_and_never_fetched(store):
    tables = pick_tables(store, "M", "201501", "202412", 1)
    tid = tables[0]["table_id"]
    signal = {"table_id": tid, "obj_id": "ITEM", "value_id": "S1", "role": "indicator"}
    public = {  # A's MentionCandidate (plan 19 contract document 2)
        "table_id": tid,
        "obj_id": "ITEM",
        "value_id": "P1",
        "value_name": "참고",
        "role": "indicator",
        "path": "lexical",
        "type": "item_row",
    }
    state = state_with(tables, [freq("M")])
    [sel] = state["selections"]
    sel["signals"]["facts"]["tables"][tid] = {"indicator": {"reference_candidates": [signal]}}
    sel["signals"]["plan17"] = {"reference_candidates": ["DT_SOME_TABLE"], "b_handoff_ready": True}
    fallback = _scope(to_b_request(state))["hints"]["reference_candidates"]
    assert fallback == [{**signal, "fetch": False}]
    sel["reference_candidates"] = [public]
    request = to_b_request(state)
    assert _scope(request)["hints"]["reference_candidates"] == [{**public, "fetch": False}]
    assert {t["table_id"] for t in to_legacy_a_result(request)["tables"]} == {tid}


# ---------------------------------------------------------------- 5. bundles through to_b_request


def test_a_three_table_edition_bundle_keeps_every_table_and_its_one_share(store):
    tables = pick_tables(store, "M", "200501", "202412", 3)
    shares = [("2005-01", "2009-12"), ("2010-01", "2016-12"), ("2017-01", "2024-12")]
    b = bundle(
        "b1",
        tables,
        assignments={t["table_id"]: [{"start": a, "end": e}] for t, (a, e) in zip(tables, shares)},
    )
    conditions = [freq("M"), period("range", expr("2005"), expr("2024"))]
    alternative = [pick_tables(store, "Y", "2000", "2024", 1)[0]["table_id"]]
    request = to_b_request(state_with(tables, conditions, alternatives=[alternative], bundle=b))
    assert check_request(request) == []
    sc = _scope(request)
    assert [t["table_id"] for t in sc["tables"]] == [t["table_id"] for t in tables]
    assert sc["bundle"] == b
    assert sc["alternatives"] == [
        {"table_ids": alternative, "fetch": False, "source": "alternatives[0]"}
    ]
    result = run_b_request(request, services(store, answers(store, tables, "M")))
    [out] = result["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert {r["table_id"]: (r["period"]["start"], r["period"]["end"]) for r in out["series"]} == {
        t["table_id"]: (a.replace("-", ""), e.replace("-", "")) for t, (a, e) in zip(tables, shares)
    }
    assert out["period"]["coverage_months"]["covered"] == [{"start": "2005-01", "end": "2024-12"}]
    assert out["period"]["coverage_months"]["unmet"] == []
    fetched = {s["table_id"] for s in result["selections"]}
    assert fetched == {t["table_id"] for t in tables} and not set(alternative) & fetched
    assert check_scope_result(out) == []


def test_a_relative_period_counts_from_the_bundle_common_anchor(store):
    tables = pick_tables(store, "M", "201001", "202406", 2)
    common = min(store.get(t["org_id"], t["table_id"])["periods"]["M"]["end"] for t in tables)
    anchor = f"{common[:4]}-{common[4:]}"
    b = bundle("b1", tables, anchor_source="bundle_common", anchor=anchor)
    conditions = [freq("M"), period("recent", amount=1, unit="year", relative=True)]
    request = to_b_request(state_with(tables, conditions, bundle=b))
    assert _scope(request)["bundle"]["context"]["anchor"] == anchor
    [out] = run_b_request(request, services(store, answers(store, tables, "M")))["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert {r["period"]["end"] for r in out["series"]} == {common}


def test_two_segments_from_the_selection_are_rejected_before_any_query(store):
    tables = pick_tables(store, "M", "201001", "202412", 1)
    split = [{"start": "2024-01", "end": "2024-03"}, {"start": "2024-10", "end": "2024-12"}]
    b = bundle("b1", tables, assignments={tables[0]["table_id"]: split})
    request = to_b_request(state_with(tables, [freq("M"), period("point", expr("2024"))], bundle=b))
    assert _scope(request)["bundle"]["assignments"][tables[0]["table_id"]] == split  # kept as given
    svc = services(store, answers(store, tables, "M"))
    result = run_b_request(request, svc)
    [out] = result["scopes"]
    assert out["issues"][0] == issue_of("invalid_assignment")
    assert result["selections"] == [] and not model_calls(svc)


def test_only_b_writes_applied(store):
    tables = pick_tables(store, "Y", "2000", "2024", 1)
    conditions = [freq("Y"), period("point", expr("1800")), period("point", expr("2020"), cid="P2")]
    fields = {
        "F1": {"source": "question", "confirmed": True, "applied": False},
        "P1": {"source": "user", "confirmed": True, "applied": True},
        "P2": {"source": "question", "confirmed": False, "applied": None},
    }
    request = to_b_request(state_with(tables, conditions, fields=fields))
    assert all("applied" not in f for f in _scope(request)["hints"]["condition_fields"].values())
    [out] = run_b_request(request, services(store, answers(store, tables, "Y")))["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert {cid: f["applied"] for cid, f in out["condition_fields"].items()} == {
        "F1": True,
        "P1": False,
        "P2": True,
    }
    assert out["condition_fields"]["P1"]["source"] == "user"


# ---------------------------------------------------------------- review fixes (plan 06)


@pytest.mark.parametrize("bad", ["bad", ["bad"], True, {}, {"bundle_id": "b", "assignments": []}])
def test_a_broken_bundle_stops_before_the_model_wherever_the_request_comes_from(store, bad):
    tables = pick_tables(store, "M", "201501", "202412", 1)
    request = to_b_request(state_with(tables, [freq("M")], bundle=bad))
    assert [e["code"] for e in request["input_errors"]] == ["input_contract_error"] * len(
        request["input_errors"]
    ) and request["input_errors"]
    assert check_request(request)
    for errors in (request["input_errors"], None):  # the adapter's errors, then a bare request
        req = copy.deepcopy(request)
        if errors is None:
            del req["input_errors"]
        svc = services(store, answers(store, tables, "M"))
        [out] = run_b_request(req, svc)["scopes"]
        assert out["issues"][0] == issue_of("input_contract_error")
        assert not model_calls(svc) and out["series"] == []
