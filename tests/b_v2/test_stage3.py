"""B stage 3 (plan 05): adapter on the current A output, bundles, editions, isolation, slots.

Model answers are synthetic (ScriptedModel built from the metadata): these check code paths and
contracts, not model accuracy. Tables are chosen from the catalog by their coverage, never by ID.
"""

from __future__ import annotations

import copy

import pytest
from b_v2_support import A_PERIOD_SHAPES, V3_FIXTURES, V3CUR_FIXTURES, load_v3

from claview.b.adapter_question import to_b_request_question_level
from claview.b.adapter_v3 import to_b_request, to_legacy_a_result
from claview.b.contract import check_request, check_scope_result, issue_of
from claview.b.run import run_b_request
from claview.contracts import Config, Trace
from claview.fetch import validate_b_result
from claview.models import ScriptedModel
from claview.services import Services

AS_OF = "2026-10-20"
STATES = [pytest.param(load_v3(p), id=p.stem) for p in V3CUR_FIXTURES]


def expr(year=None, alias=None, sub_unit=None, sub_value=None):
    return {
        "text": "t",
        "year": year,
        "alias": alias,
        "sub_unit": sub_unit,
        "sub_value": sub_value,
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


def pick_tables(store, frequency, start, end, n, exclude=()):
    """The first `n` catalog tables (catalog order) recording `frequency` over [start, end]."""
    out = []
    for key in store.catalog:
        org, table_id = key.split(":")
        if table_id in exclude:
            continue
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


def answers(store, tables, frequency, repeat=1):
    """One valid selection per table: its first item and first value of every axis."""
    out = {}
    for table in tables:
        meta = store.get(table["org_id"], table["table_id"])
        row = {
            "item_id": next(iter(meta["items"])),
            "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
            "period": {"frequency": frequency, "start": "x", "end": "x"},
            "expand": {a: "none" for a in meta["dimensions"]},
        }
        out[f"B:{table['org_id']}:{table['table_id']}"] = [{"selections": [row]}] * repeat
    return out


def services(store, responses):
    return Services(store, None, ScriptedModel(responses), Config(as_of=AS_OF), Trace())


def scope(scope_id, tables, conditions, **extra):
    return {
        "scope_id": scope_id,
        "tables": tables,
        "decision": "auto",
        "hints": {"conditions": conditions, **extra.pop("hints", {})},
        **extra,
    }


def request(*scopes, errors=None):
    out = {
        "question": "q",
        "as_of": AS_OF,
        "scopes": list(scopes),
        "skipped": [],
        "route": None,
        "relations": [],
        "table_group_hint": [],
        "source": "test",
    }
    if errors:
        out["input_errors"] = errors
    return out


# ---------------------------------------------------------------- adapter on the current A output


@pytest.mark.parametrize("state", STATES)
def test_current_a_output_becomes_a_valid_request(state):
    req = to_b_request(state)
    assert check_request(req) == []
    assert "input_errors" not in req


@pytest.mark.parametrize("state", STATES)
def test_requirement_key_and_condition_slots_follow_the_decomposition(state):
    decomposition = state["analysis"]["decomposition"]
    reqs = {r["id"]: r for r in state["analysis"]["interpretation"]["requirements"]}
    for sc in to_b_request(state)["scopes"]:
        rid = sc["scope_id"]
        assert sc["requirement_key"] == {
            "reading_id": decomposition["reading_id"],
            "requirement_id": rid,
        }
        ids = reqs[rid]["condition_ids"] + [
            i for i in reqs[rid]["shared_condition_ids"] if i not in reqs[rid]["condition_ids"]
        ]
        expected = {
            cid: {k: v for k, v in decomposition["condition_fields"][cid].items() if k != "applied"}
            for cid in ids
            if cid in decomposition["condition_fields"]
        }
        assert sc["hints"]["condition_fields"] == expected
        assert all("applied" not in f for f in sc["hints"]["condition_fields"].values())
        assert sc["hints"]["inferred"] == decomposition["inferred"][rid]  # [] kept as given


def test_an_older_a_output_has_no_inference_field():
    for path in V3_FIXTURES:
        for sc in to_b_request(load_v3(path))["scopes"]:
            assert "inferred" not in sc["hints"] and "condition_fields" not in sc["hints"]


@pytest.mark.parametrize("state", STATES)
def test_value_rows_are_codes_of_their_table_and_axis(state, store):
    for sc in to_b_request(state)["scopes"]:
        orgs = {t["table_id"]: t["org_id"] for t in sc["tables"]}
        for row in sc["hints"]["value_rows"]:
            assert row["table_id"] in orgs
            if row["value_id"] is None:
                continue
            raw = store.records[f"{orgs[row['table_id']]}:{row['table_id']}"]["meta"]["ITM"]
            assert row["obj_id"] in {r["OBJ_ID"] for r in raw if r["ITM_ID"] == row["value_id"]}, (
                row
            )


def test_target_and_item_rows_reach_b():
    roles = {
        (row["role"], row["obj_id"] == "ITEM")
        for path in V3CUR_FIXTURES
        for sc in to_b_request(load_v3(path))["scopes"]
        for row in sc["hints"]["value_rows"]
    }
    assert ("target", False) in roles
    assert ("indicator", True) in roles


def _confirm_state():
    return copy.deepcopy(
        next(
            load_v3(p)
            for p in V3CUR_FIXTURES
            if any(
                s["status"] == "confirm" and s.get("alternatives") for s in load_v3(p)["selections"]
            )
        )
    )


def test_basis_rows_and_negation_are_carried_for_the_requirement():
    state = _confirm_state()
    sel = next(s for s in state["selections"] if s["status"] == "confirm")
    req = next(
        r
        for r in state["analysis"]["interpretation"]["requirements"]
        if r["id"] == sel["requirement_id"]
    )
    table = sel["tables"][0]
    frag = req["indicator"]["fragment_id"]
    state["analysis"]["segments"].append(
        {"segment_id": "sX", "span": [0, 1], "quote": "뺀말", "kind": "word"}
    )
    state["analysis"]["mentions"].append(
        {
            "mention_id": "mX",
            "kind": "name",
            "segment_ids": ["sX"],
            "condition_ids": [],
            "marker": None,
            "candidates": [
                {
                    "table_id": table["table_id"],
                    "obj_id": "AX",
                    "value_id": "AX.1",
                    "value_name": "뺀말",
                    "role": "basis",
                    "path": "exact",
                    "type": "basis",
                }
            ],
            "overlaps": [],
        }
    )
    state["analysis"]["ref_map"]["mention_fragments"]["mX"] = [frag]
    state["analysis"]["judgement"]["negated"] = ["뺀말"]
    [sc] = [s for s in to_b_request(state)["scopes"] if s["scope_id"] == req["id"]]
    row = next(r for r in sc["hints"]["value_rows"] if r["mention_id"] == "mX")
    assert (row["role"], row["value_id"], row["negated"], row["bound"]) == (
        "basis",
        "AX.1",
        True,
        True,
    )


def test_first_alternative_bundle_and_reference_candidates_stay_apart():
    state = _confirm_state()
    sel = next(s for s in state["selections"] if s["status"] == "confirm" and s.get("alternatives"))
    table_id = sel["tables"][0]["table_id"]
    candidate = {
        "table_id": table_id,
        "obj_id": "ITEM",
        "value_id": "X1",
        "value_name": "참고",
        "role": "indicator",
        "path": "lexical",
        "type": "indicator",
    }
    sel["signals"]["facts"]["tables"][table_id]["indicator"]["reference_candidates"] = [candidate]
    req = to_b_request(state)
    [sc] = [s for s in req["scopes"] if s["scope_id"] == sel["requirement_id"]]
    assert sc["alternatives"] == [
        {"table_ids": sel["alternatives"][0], "fetch": False, "source": "alternatives[0]"}
    ]
    assert sc["hints"]["reference_candidates"] == [{**candidate, "fetch": False}]
    fetched = {t["table_id"] for t in to_legacy_a_result(req)["tables"]}
    assert fetched == {t["table_id"] for t in sel["tables"]}  # neither alternatives nor candidates
    assert check_request(req) == []


def test_another_readings_alternative_is_never_read():
    state = _confirm_state()
    state["analysis"]["decomposition"]["alternative"] = {
        "reading_id": "R2",
        "interpretation": {"requirements": [{"id": "r1", "condition_ids": ["ZZ"]}]},
        "condition_fields": {"ZZ": {}},
    }
    before = to_b_request(_confirm_state())
    assert to_b_request(state) == before


def test_three_table_bundle_is_passed_whole():
    state = _confirm_state()
    sel = next(s for s in state["selections"] if s["status"] == "confirm")
    base = sel["tables"][0]
    sel["tables"] = [base, {**base, "table_id": "T_SECOND"}, {**base, "table_id": "T_THIRD"}]
    [sc] = [s for s in to_b_request(state)["scopes"] if s["scope_id"] == sel["requirement_id"]]
    assert [t["table_id"] for t in sc["tables"]] == [base["table_id"], "T_SECOND", "T_THIRD"]


def test_unknown_condition_slot_ids_are_input_errors_that_end_the_run(store):
    state = _confirm_state()
    state["analysis"]["decomposition"]["condition_fields"]["ZZ9"] = {
        "source": "question",
        "confirmed": True,
        "applied": None,
    }
    req = to_b_request(state)
    assert req["input_errors"] == [
        {"code": "input_contract_error", "detail": "condition_fields_unknown_id:ZZ9"}
    ]
    result = run_b_request(req, services(store, {}))
    for sc in result["scopes"]:
        assert sc["status"] == "unresolved"
        assert sc["issues"][0] == issue_of("input_contract_error")
        assert sc["issues"][0]["resume_at"] == "none"
        assert check_scope_result(sc) == []


def test_a_requirement_naming_an_unknown_condition_is_an_input_error():
    state = _confirm_state()
    req0 = state["analysis"]["interpretation"]["requirements"][0]
    req0["condition_ids"] = req0["condition_ids"] + ["NOPE"]
    errors = to_b_request(state)["input_errors"]
    assert {
        "code": "input_contract_error",
        "detail": "requirement_unknown_condition:NOPE",
        "scope_id": req0["id"],
    } in errors


def test_condition_slots_must_not_carry_applied_from_a():
    sc = scope(
        "r1",
        [{"org_id": "301", "table_id": "T1", "table_name": None}],
        [freq("M")],
        hints={
            "condition_fields": {"F1": {"source": "question", "confirmed": True, "applied": True}}
        },
    )
    assert any("applied is B's" in p for p in check_request(request(sc)))


# ---------------------------------------------------------------- run: bundles, editions, isolation


def test_three_tables_with_assigned_shares_report_union_and_unmet(store):
    tables = pick_tables(store, "M", "200501", "202412", 3)
    shares = [("2005-01", "2009-12"), ("2010-01", "2016-12"), ("2017-01", "2022-12")]
    bundle = {
        "bundle_id": "b1",
        "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
        "assignments": {
            t["table_id"]: [{"start": a, "end": b}] for t, (a, b) in zip(tables, shares)
        },
    }
    sc = scope(
        "r1", tables, [freq("M"), period("range", expr("2005"), expr("2024"))], bundle=bundle
    )
    assert check_request(request(sc)) == []
    result = run_b_request(request(sc), services(store, answers(store, tables, "M")))
    [out] = result["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert check_scope_result(out) == []
    by_table = {r["table_id"]: (r["period"]["start"], r["period"]["end"]) for r in out["series"]}
    assert by_table == {
        t["table_id"]: (a.replace("-", ""), b.replace("-", "")) for t, (a, b) in zip(tables, shares)
    }
    assert out["period"]["coverage_months"] == {
        "requested": [{"start": "2005-01", "end": "2024-12"}],
        "covered": [{"start": "2005-01", "end": "2022-12"}],
        "unmet": [{"start": "2023-01", "end": "2024-12"}],
    }
    validate_b_result({"selections": result["selections"]}, store)


def test_a_bundle_common_anchor_reaches_the_series(store):
    tables = pick_tables(store, "M", "201001", "202406", 2)
    meta_end = min(store.get(t["org_id"], t["table_id"])["periods"]["M"]["end"] for t in tables)
    anchor = f"{meta_end[:4]}-{meta_end[4:]}"
    bundle = {
        "bundle_id": "b2",
        "context": {
            "bundle_id": "b2",
            "anchor_source": "bundle_common",
            "anchor": anchor,
            "simultaneous": True,
        },
        "assignments": {},
    }
    sc = scope(
        "r1",
        tables,
        [freq("M"), period("recent", amount=1, unit="year", relative=True)],
        bundle=bundle,
    )
    [out] = run_b_request(request(sc), services(store, answers(store, tables, "M")))["scopes"]
    assert out["status"] == "ok", out["issues"]
    ends = {r["period"]["end"] for r in out["series"]}
    assert ends == {meta_end}  # both tables end at the bundle's common latest period


def test_two_requirements_on_one_table_keep_their_own_conditions(store):
    [table] = pick_tables(store, "M", "201501", "202412", 1)
    if "Y" not in store.get(table["org_id"], table["table_id"])["periods"]:
        [table] = [
            t
            for t in pick_tables(store, "M", "201501", "202412", 40)
            if "Y" in store.get(t["org_id"], t["table_id"])["periods"]
        ][:1]
    first = scope(
        "r1", [table], [freq("M"), period("recent", amount=1, unit="year", relative=True)]
    )
    second = scope("r2", [table], [freq("Y", cid="F2"), period("point", expr("2020"), cid="P2")])
    responses = answers(store, [table], "M")
    key = next(iter(responses))
    responses[key] = responses[key] + answers(store, [table], "Y")[key]
    result = run_b_request(request(first, second), services(store, responses))
    r1, r2 = result["scopes"]
    assert (r1["status"], r2["status"]) == ("ok", "ok"), (r1["issues"], r2["issues"])
    assert {r["period"]["frequency"] for r in r1["series"]} == {"M"}
    assert {(r["period"]["frequency"], r["period"]["start"]) for r in r2["series"]} == {
        ("Y", "2020")
    }
    assert set(r1["condition_fields"]) == {"F1", "P1"} and set(r2["condition_fields"]) == {
        "F2",
        "P2",
    }


def test_value_rows_of_two_requirements_stay_apart():
    state = _confirm_state()
    reqs = state["analysis"]["interpretation"]["requirements"]
    sel = next(s for s in state["selections"] if s["status"] == "confirm")
    table_id = sel["tables"][0]["table_id"]
    other = copy.deepcopy(reqs[0])
    other["id"], other["indicator"] = "r9", {**(other["indicator"] or {}), "fragment_id": "g99"}
    reqs.append(other)
    state["selections"].append({**copy.deepcopy(sel), "requirement_id": "r9"})
    state["analysis"]["mentions"].append(
        {
            "mention_id": "m99",
            "kind": "name",
            "segment_ids": [],
            "condition_ids": [],
            "marker": None,
            "candidates": [
                {
                    "table_id": table_id,
                    "obj_id": "AX",
                    "value_id": "AX.9",
                    "value_name": "남의값",
                    "role": "target",
                    "path": "exact",
                    "type": "target",
                }
            ],
            "overlaps": [],
        }
    )
    state["analysis"]["ref_map"]["mention_fragments"]["m99"] = ["g99"]
    scopes = {s["scope_id"]: s for s in to_b_request(state)["scopes"]}
    assert any(r["mention_id"] == "m99" for r in scopes["r9"]["hints"]["value_rows"])
    assert not any(
        r["mention_id"] == "m99" for r in scopes[sel["requirement_id"]]["hints"]["value_rows"]
    )


def test_b_fills_applied_from_its_own_use(store):
    [table] = pick_tables(store, "Y", "2000", "2024", 1)
    fields = {
        "P1": {"source": "user", "confirmed": True},
        "P2": {"source": "question", "confirmed": False},
    }
    sc = scope(
        "r1",
        [table],
        [
            freq("Y"),
            period("point", expr("1800"), cid="P1"),
            period("point", expr("2020"), cid="P2"),
        ],
        hints={"condition_fields": fields},
    )
    [out] = run_b_request(request(sc), services(store, answers(store, [table], "Y")))["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert out["condition_fields"]["P1"] == {"source": "user", "confirmed": True, "applied": False}
    assert out["condition_fields"]["P2"]["applied"] is True
    assert out["condition_fields"]["F1"]["applied"] is True


def test_period_unsupported_ends_the_scope_without_a_default(store):
    [table] = pick_tables(store, "Y", "2000", "2024", 1)
    sc = scope(
        "r1",
        [table],
        [freq("Y"), period("point", expr("2020"))],
        hints={
            "condition_fields": {
                "P1": {"source": "question", "confirmed": False, "period_diagnosis": "unsupported"}
            }
        },
    )
    svc = services(store, {})
    [out] = run_b_request(request(sc), svc)["scopes"]
    assert out["status"] == "unresolved"
    assert out["issues"][0]["code"] == "period_unsupported"
    assert out["series"] == []
    assert not [e for e in svc.trace.events if e["event"] == "model_request"]


def test_a_malformed_condition_is_an_input_contract_error(store):
    [table] = pick_tables(store, "Y", "2000", "2024", 1)
    broken = period("point", expr("2020"))
    broken["attrs"]["form"] = "sometime"
    [out] = run_b_request(request(scope("r1", [table], [freq("Y"), broken])), services(store, {}))[
        "scopes"
    ]
    assert out["issues"][0]["code"] == "malformed_condition"
    assert out["issues"][0]["resume_at"] == "none"


def test_a_table_with_no_data_in_range_is_left_out_when_others_remain(store):
    [old] = [
        t
        for t in pick_tables(store, "Y", "2000", "2024", 60)
        if store.get(t["org_id"], t["table_id"])["periods"]["Y"]["start"] >= "1990"
    ][:1]
    conditions = [freq("Y"), period("range", expr("1950"), expr("1960"))]
    [alone] = run_b_request(request(scope("r1", [old], conditions)), services(store, {}))["scopes"]
    assert alone["status"] == "unresolved" and alone["issues"][0]["code"] == "no_data_in_range"
    assert alone["issues"][0]["resume_at"] == "A_reselect"
    assert alone["series"] == []

    [deep] = [t for t in pick_tables(store, "Y", "1955", "1960", 1, exclude={old["table_id"]})]
    svc = services(store, answers(store, [deep], "Y"))
    [both] = run_b_request(request(scope("r1", [old, deep], conditions)), svc)["scopes"]
    assert both["status"] == "ok", both["issues"]
    assert {r["table_id"] for r in both["series"]} == {deep["table_id"]}
    dropped = [n for n in both["notices"] if n.get("code") == "no_data_in_range"]
    assert [n["table"] for n in dropped] == [f"{old['org_id']}:{old['table_id']}"]


# ---------------------------------------------------------------- review fixes (stage 3)


def two_latest(store, frequency):
    """Two catalog tables recording `frequency` up to different latest periods (later one first)."""
    seen = {}
    for key in store.catalog:
        org, table_id = key.split(":")
        try:
            meta = store.get(org, table_id)
        except Exception:  # noqa: BLE001 - a table with broken metadata is just not picked
            continue
        bounds = meta["periods"].get(frequency)
        if bounds and bounds["end"] not in seen and bounds["start"] <= "201501":
            seen[bounds["end"]] = {
                "org_id": org,
                "table_id": table_id,
                "table_name": meta["table_name"],
            }
            if len(seen) == 2:
                return [seen[e] for e in sorted(seen, reverse=True)]
    raise AssertionError("no two tables with different latest periods")


@pytest.mark.parametrize("conditions", [[None], [{"kind": "period"}], [{"id": 3, "kind": "x"}]])
def test_a_broken_condition_list_ends_the_scope_without_a_model_call(store, conditions):
    [table] = pick_tables(store, "Y", "2000", "2024", 1)
    svc = services(store, {})
    [out] = run_b_request(request(scope("r1", [table], conditions)), svc)["scopes"]
    assert out["status"] == "unresolved"
    assert out["issues"][0]["code"] == "malformed_condition"
    assert out["issues"][0]["resume_at"] == "none"
    assert not [e for e in svc.trace.events if e["event"] == "model_request"]


@pytest.mark.parametrize(
    "hints",
    [
        {"conditions": [freq("Y")], "condition_fields": {"F1": 1}},
        {"conditions": 1},
        {"conditions": [freq("Y")], "condition_fields": [1]},
    ],
)
def test_broken_hint_shapes_end_as_input_errors_without_raising(store, hints):
    [table] = pick_tables(store, "Y", "2000", "2024", 1)
    sc = {"scope_id": "r1", "tables": [table], "decision": "auto", "hints": hints}
    svc = services(store, {})
    [out] = run_b_request(request(sc), svc)["scopes"]
    assert out["status"] == "unresolved"
    assert out["issues"][0]["code"] == "malformed_condition"
    assert not [e for e in svc.trace.events if e["event"] == "model_request"]


def test_an_inference_hint_without_a_text_condition_id_keeps_the_result_valid(store):
    [table] = pick_tables(store, "M", "201501", "202412", 1)
    hint = {"kind": "frequency", "value": "M", "from_condition_id": 7, "rule": "period_unit"}
    sc = scope("r1", [table], [period("point", expr("2024"))], hints={"inferred": [hint]})
    assert check_request(request(sc)) == []
    [out] = run_b_request(request(sc), services(store, answers(store, [table], "M")))["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert check_scope_result(out) == []
    [unit] = [a for a in out["shared_assumptions"] if a["reason_code"] == "period_unit"]
    assert unit["refs"]["condition_ids"] == ["P1"]  # the period it was read from


def test_the_scope_request_is_the_union_of_its_tables_in_any_order(store):
    tables = two_latest(store, "M")
    conditions = [freq("M"), period("recent", amount=3, unit="month", relative=True)]
    coverages = []
    for order in (tables, tables[::-1]):
        svc = services(store, answers(store, order, "M"))
        [out] = run_b_request(request(scope("r1", order, conditions)), svc)["scopes"]
        assert out["status"] == "ok", out["issues"]
        coverages.append(out["period"]["coverage_months"])
    assert coverages[0] == coverages[1]
    ends = sorted(store.get(t["org_id"], t["table_id"])["periods"]["M"]["end"] for t in tables)
    assert coverages[0]["requested"][-1]["end"] == f"{ends[-1][:4]}-{ends[-1][4:]}"
    assert coverages[0]["unmet"] == []


def test_when_every_table_is_left_out_the_unmet_months_are_reported(store):
    [old] = pick_tables(store, "Y", "2000", "2024", 1)
    conditions = [freq("Y"), period("range", expr("1700"), expr("1701"))]
    [out] = run_b_request(request(scope("r1", [old], conditions)), services(store, {}))["scopes"]
    [unmet] = [n for n in out["notices"] if n.get("code") == "unmet_months"]
    assert unmet["requested"] == unmet["unmet"] == [{"start": "1700-01", "end": "1701-12"}]
    assert unmet["covered"] == []


def test_different_defaults_of_two_tables_keep_their_own_assumption_ids(store):
    tables = two_latest(store, "M")
    [out] = run_b_request(
        request(scope("r1", tables, [freq("M")])), services(store, answers(store, tables, "M"))
    )["scopes"]
    assert out["status"] == "ok", out["issues"]
    period_ids = [
        a["assumption_id"]
        for a in out["shared_assumptions"]
        if a["reason_code"].startswith("recent")
    ]
    assert len(period_ids) == 2 and len(set(period_ids)) == 2
    assert check_scope_result(out) == []
    duplicated = copy.deepcopy(out)
    duplicated["shared_assumptions"][1]["assumption_id"] = period_ids[0]
    assert any("used twice" in p for p in check_scope_result(duplicated))


def test_a_mention_another_requirement_names_stays_with_it():
    state = _confirm_state()
    reqs = state["analysis"]["interpretation"]["requirements"]
    sel = next(s for s in state["selections"] if s["status"] == "confirm")
    other = copy.deepcopy(reqs[0])
    other["id"], other["indicator"] = "r9", {**(other["indicator"] or {}), "fragment_id": "g99"}
    other["refs"] = {**(other.get("refs") or {}), "mention_ids": ["m98"]}
    reqs.append(other)
    state["selections"].append({**copy.deepcopy(sel), "requirement_id": "r9"})
    candidate = {
        "table_id": sel["tables"][0]["table_id"],
        "obj_id": "AX",
        "value_id": "AX.8",
        "value_name": "이름만",
        "role": "target",
        "path": "exact",
        "type": "target",
    }
    state["analysis"]["mentions"].append(
        {
            "mention_id": "m98",
            "kind": "name",
            "segment_ids": [],
            "condition_ids": [],
            "marker": None,
            "candidates": [candidate],
            "overlaps": [],
        }
    )
    state["analysis"]["ref_map"]["mention_fragments"]["m98"] = []
    scopes = {s["scope_id"]: s for s in to_b_request(state)["scopes"]}
    [row] = [r for r in scopes["r9"]["hints"]["value_rows"] if r["mention_id"] == "m98"]
    assert row["bound"] is True
    rows = scopes[sel["requirement_id"]]["hints"]["value_rows"]
    assert not any(r["mention_id"] == "m98" for r in rows)


POISON = [None, 1, "x", [], {}, [None], {"k": None}]


def _positions(node, path=()):
    yield path
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _positions(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _positions(value, (*path, i))


def _replaced(node, path, value):
    if not path:
        return value
    node = copy.deepcopy(node)
    target = node
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return node


def _stage3_request():
    state = _confirm_state()
    sel = next(s for s in state["selections"] if s["status"] == "confirm")
    table_id = sel["tables"][0]["table_id"]
    sel["signals"]["facts"]["tables"][table_id]["indicator"]["reference_candidates"] = [
        {"table_id": table_id, "obj_id": "ITEM", "value_id": "X1", "role": "indicator"}
    ]
    sel["bundle"] = {
        "bundle_id": "b1",
        "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
        "assignments": {table_id: [{"start": "2020-01", "end": "2024-12"}]},
    }
    req = to_b_request(state)
    assert check_request(req) == []
    assert any(s["hints"]["value_rows"] and s.get("alternatives") for s in req["scopes"])
    return req


def _stage3_result(store):
    tables = two_latest(store, "M")
    sc = scope(
        "r1",
        tables,
        [freq("M")],
        hints={"condition_fields": {"F1": {"source": "question", "confirmed": True}}},
    )
    [out] = run_b_request(request(sc), services(store, answers(store, tables, "M")))["scopes"]
    assert check_scope_result(out) == [] and out["shared_assumptions"]
    return out


def test_new_contract_fields_never_make_the_checks_raise(store):
    for valid, check in (
        (_stage3_request(), check_request),
        (_stage3_result(store), check_scope_result),
    ):
        for path in _positions(valid):
            for value in POISON:
                check(_replaced(valid, path, value))  # must return, never raise


# ---------------------------------------------------------------- follow-up 10/21


def _model_calls(svc):
    return [e for e in svc.trace.events if e["event"] == "model_request"]


@pytest.mark.parametrize("entry", load_v3(A_PERIOD_SHAPES), ids=lambda e: e["question"][:8])
def test_periods_as_a_writes_them_are_read_without_its_old_diagnosis(store, entry):
    """A's conditions carry the old `extract_status: unresolved`; B reads them itself. A's own
    diagnosis still says `unsupported` (old resolver): passed on, it is honoured as agreed."""
    [table] = pick_tables(store, "M", "201501", "202412", 1)
    conditions = entry["conditions"]
    assert all(
        c["attrs"]["extract_status"] == "unresolved" for c in conditions if c["id"][0] == "P"
    )
    svc = services(store, answers(store, [table], "M"))
    [out] = run_b_request(request(scope("r1", [table], conditions)), svc)["scopes"]
    assert out["status"] == "ok", out["issues"]
    [attrs] = [c["attrs"] for c in conditions if c["kind"] == "period"]
    unit = "M" if attrs["unit"] == "month" else attrs["start_expr"]["sub_unit"]
    assert out["period"]["applied"]["frequency"] == unit  # the unit the question counts in
    diagnosed = {
        cid: {"source": "question", "confirmed": True, "period_diagnosis": d}
        for cid, d in entry["a_diagnosis"].items()
    }
    sc = scope("r1", [table], conditions, hints={"condition_fields": diagnosed})
    svc = services(store, {})
    [out] = run_b_request(request(sc), svc)["scopes"]
    assert set(entry["a_diagnosis"].values()) == {"unsupported"}
    assert out["status"] == "unresolved" and out["issues"][0]["code"] == "period_unsupported"
    assert out["series"] == [] and not _model_calls(svc)


@pytest.mark.parametrize("with_slot", [False, True])
def test_a_period_marked_invalid_ends_before_any_model_call(store, with_slot):
    [table] = pick_tables(store, "Y", "2000", "2024", 1)
    bad = period("point", expr("2024", sub_unit="Q", sub_value="5"))
    bad["attrs"]["invalid"] = True
    hints = {"condition_fields": {"P1": {"source": "question", "period_diagnosis": "ok"}}}
    sc = scope("r1", [table], [freq("Y"), bad], **({"hints": hints} if with_slot else {}))
    svc = services(store, {})
    result = run_b_request(request(sc), svc)
    [out] = result["scopes"]
    assert out["status"] == "unresolved"
    assert out["issues"][0] == issue_of("malformed_condition")
    assert out["period"] is None and result["selections"] == [] and not _model_calls(svc)


def _bundle(assignments):
    return {
        "bundle_id": "b1",
        "context": {"bundle_id": "b1", "anchor_source": "table_latest", "anchor": None},
        "assignments": assignments,
    }


def test_two_assigned_segments_of_one_table_are_rejected_before_any_query(store):
    tables = pick_tables(store, "M", "201001", "202412", 2)
    split = [{"start": "2024-01", "end": "2024-03"}, {"start": "2024-10", "end": "2024-12"}]
    assignments = {tables[0]["table_id"]: split, tables[1]["table_id"]: [split[0]]}
    sc = scope(
        "r1",
        tables,
        [freq("M"), period("range", expr("2024"), expr("2024"))],
        bundle=_bundle(assignments),
    )
    req = request(sc)
    given = copy.deepcopy(req)
    expected = "assignment: one continuous segment per table expected, got 2"
    assert any(p.endswith(expected) for p in check_request(req))  # the B entry check agrees
    svc = services(store, answers(store, tables, "M"))
    result = run_b_request(req, svc)
    [out] = result["scopes"]
    assert out["status"] == "unresolved"
    assert out["issues"][0] == issue_of("invalid_assignment")
    assert out["issues"][0]["resume_at"] == "none"
    detail = f"bundle.assignments[{tables[0]['table_id']}].{expected}"
    assert {"kind": "input", "code": "invalid_assignment", "detail": detail} in out["notices"]
    assert out["series"] == [] and result["selections"] == [] and not _model_calls(svc)
    assert req == given  # nothing filled, nothing cut to the first segment
    assert check_scope_result(out) == []


def test_two_assigned_segments_are_rejected_even_without_conditions(store):
    """The question-text path (no `conditions` in the hints) checks the shares too."""
    [table] = pick_tables(store, "M", "201001", "202412", 1)
    split = [{"start": "2024-01", "end": "2024-03"}, {"start": "2024-10", "end": "2024-12"}]
    sc = {
        "scope_id": "r1",
        "tables": [table],
        "decision": "auto",
        "hints": {},
        "bundle": _bundle({table["table_id"]: split}),
    }
    req = {**request(sc), "question": "2024년 월별 자료"}
    svc = services(store, answers(store, [table], "M"))
    [out] = run_b_request(req, svc)["scopes"]
    assert out["issues"][0] == issue_of("invalid_assignment")
    assert not _model_calls(svc)


def test_two_requirements_on_one_table_keep_their_own_shares(store):
    [table] = pick_tables(store, "M", "201001", "202412", 1)
    request_period = [freq("M"), period("range", expr("2015"), expr("2024"))]
    first = scope(
        "r1",
        [table],
        request_period,
        bundle=_bundle({table["table_id"]: [{"start": "2015-01", "end": "2017-12"}]}),
    )
    second = scope(
        "r2",
        [table],
        copy.deepcopy(request_period),
        bundle=_bundle({table["table_id"]: [{"start": "2020-01", "end": "2022-06"}]}),
    )
    responses = answers(store, [table], "M", repeat=2)
    r1, r2 = run_b_request(request(first, second), services(store, responses))["scopes"]
    assert (r1["status"], r2["status"]) == ("ok", "ok"), (r1["issues"], r2["issues"])
    assert {(s["period"]["start"], s["period"]["end"]) for s in r1["series"]} == {
        ("201501", "201712")
    }
    assert {(s["period"]["start"], s["period"]["end"]) for s in r2["series"]} == {
        ("202001", "202206")
    }
    assert r1["period"]["coverage_months"]["covered"] == [{"start": "2015-01", "end": "2017-12"}]
    assert r2["period"]["coverage_months"]["covered"] == [{"start": "2020-01", "end": "2022-06"}]


def test_catalog_integrity_errors_end_the_run():
    for code in ("duplicate_catalog_id", "duplicate_metadata_id", "invalid_catalog_path"):
        assert issue_of(code)["resume_at"] == "none"


def test_question_level_request_still_runs_without_hints(store):
    tables = pick_tables(store, "Y", "2000", "2024", 1)
    req = to_b_request_question_level("2020년 연간 자료", {"tables": tables}, as_of=AS_OF)
    [out] = run_b_request(req, services(store, answers(store, tables, "Y")))["scopes"]
    assert out["status"] == "ok", out["issues"]
    assert out["period"]["rule"] == "question_text"
