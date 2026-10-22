"""claview_v3 final state -> B request (plan 03 r2, section 1.1), on stored claview_v3 outputs.

Expectations are read from each stored state itself (which requirement got which tables), never
from case or table IDs written into the test, and never from the adapter's own constants.
"""

from __future__ import annotations

import copy

import pytest
from b_v2_support import V3_FIXTURES, load_v3

from claview.ab_pipeline import B_TABLE_FIELDS
from claview.b.adapter_v3 import to_b_request, to_legacy_a_result
from claview.b.contract import check_request

STATES = [pytest.param(load_v3(p), id=p.stem) for p in V3_FIXTURES]
SENT = {"auto", "confirm"}  # selection statuses that reach B (plan 03 r2, 1.1)


def requirements(state: dict) -> list[dict]:
    return state["analysis"]["interpretation"]["requirements"]


def selection_of(state: dict, rid: str) -> dict | None:
    return next((s for s in state["selections"] if s["requirement_id"] == rid), None)


def sent_ids(state: dict) -> list[str]:
    out = []
    for requirement in requirements(state):
        selection = selection_of(state, requirement["id"])
        if selection and selection["status"] in SENT and selection["tables"]:
            out.append(requirement["id"])
    return out


def expected_mentions(selection: dict) -> list[dict]:
    """Every exact occurrence with a value id, in table then source order, in contract names."""
    facts = selection.get("signals", {}).get("facts", {}).get("tables", {})
    out, seen = [], set()
    for table in selection["tables"]:
        occurrences = facts.get(table["table_id"], {}).get("indicator", {}).get("occurrences", [])
        for occ in occurrences:
            key = (table["table_id"], occ.get("obj_id"), occ.get("value_id"))
            if not occ.get("value_id") or not occ["method"].startswith("exact") or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "table_id": table["table_id"],
                    "obj_id": occ.get("obj_id"),
                    "value_id": occ["value_id"],
                    "name": occ.get("name"),
                    "method": occ["method"],
                    "role": occ.get("value_role"),
                    "relation": occ.get("relation"),
                    "parents": occ.get("parents", []),
                }
            )
    return out


def test_fixtures_are_present():
    assert len(V3_FIXTURES) >= 5


@pytest.mark.parametrize("state", STATES)
def test_request_holds_the_contract(state):
    assert check_request(to_b_request(state)) == []


@pytest.mark.parametrize("state", STATES)
def test_sent_and_skipped_requirements_follow_the_selection_status(state):
    request = to_b_request(state)
    assert [s["scope_id"] for s in request["scopes"]] == sent_ids(state)
    order = [r["id"] for r in requirements(state)]
    assert [s["scope_id"] for s in request["skipped"]] == [
        rid for rid in order if rid not in sent_ids(state)
    ]


@pytest.mark.parametrize("state", STATES)
def test_scope_tables_decision_and_covers_follow_the_selection(state):
    for scope in to_b_request(state)["scopes"]:
        selection = selection_of(state, scope["scope_id"])
        assert scope["decision"] == selection["status"]
        assert scope["tables"] == [
            {"org_id": t["org_id"], "table_id": t["table_id"], "table_name": t["table_name"]}
            for t in selection["tables"]
        ]
        assert scope["hints"]["covers"] == {t["table_id"]: t["covers"] for t in selection["tables"]}


@pytest.mark.parametrize("state", STATES)
def test_conditions_are_the_whole_own_and_shared_ones_and_ambiguous_ids_are_kept(state):
    by_id = {c["id"]: c for c in state["analysis"]["conditions"]}
    reqs = {r["id"]: r for r in requirements(state)}
    for scope in to_b_request(state)["scopes"]:
        requirement = reqs[scope["scope_id"]]
        ids = list(requirement["condition_ids"]) + [
            i for i in requirement["shared_condition_ids"] if i not in requirement["condition_ids"]
        ]
        expected = [
            {k: by_id[i][k] for k in ("id", "kind", "quote", "value", "attrs")} for i in ids
        ]
        assert scope["hints"]["conditions"] == expected
        assert scope["hints"]["ambiguous_condition_ids"] == requirement["ambiguous_condition_ids"]


@pytest.mark.parametrize("state", STATES)
def test_requirement_words_are_carried(state):
    reqs = {r["id"]: r for r in requirements(state)}
    for scope in to_b_request(state)["scopes"]:
        requirement, hints = reqs[scope["scope_id"]], scope["hints"]
        assert hints["text"] == requirement["quote"]
        assert hints["indicator"] == (requirement["indicator"] or {}).get("name")
        assert hints["targets"] == [t["name"] for t in requirement["targets"] if t["name"]]
        assert hints["breakdown"] == requirement["breakdown"]
        assert hints["negated"] == state["analysis"]["judgement"].get("negated", [])


def test_some_stored_state_has_a_period_and_a_frequency_condition():
    kinds = set()
    for path in V3_FIXTURES:
        for scope in to_b_request(load_v3(path))["scopes"]:
            kinds.update(c["kind"] for c in scope["hints"]["conditions"])
    assert {"period", "frequency"} <= kinds


@pytest.mark.parametrize("state", STATES)
def test_value_mentions_are_every_exact_occurrence(state):
    for scope in to_b_request(state)["scopes"]:
        expected = expected_mentions(selection_of(state, scope["scope_id"]))
        assert scope["hints"]["value_mentions"] == expected


@pytest.mark.parametrize("state", STATES)
def test_value_mentions_are_codes_of_their_axis_in_the_b_metadata(state, store):
    """A mention's value_id is a B code, and the raw metadata puts it on the mention's obj_id."""
    for scope in to_b_request(state)["scopes"]:
        orgs = {t["table_id"]: t["org_id"] for t in scope["tables"]}
        for mention in scope["hints"]["value_mentions"]:
            key = f"{orgs[mention['table_id']]}:{mention['table_id']}"
            raw_rows = store.records[key]["meta"]["ITM"]
            objs = {r["OBJ_ID"] for r in raw_rows if r["ITM_ID"] == mention["value_id"]}
            assert mention["obj_id"] in objs, mention
            table = store.get(orgs[mention["table_id"]], mention["table_id"])
            if mention["obj_id"] == "ITEM":
                assert mention["value_id"] in table["items"]
            else:
                assert any(mention["value_id"] in d["values"] for d in table["dimensions"].values())


def test_some_stored_state_gives_value_mentions():
    assert any(
        scope["hints"]["value_mentions"]
        for path in V3_FIXTURES
        for scope in to_b_request(load_v3(path))["scopes"]
    )


@pytest.mark.parametrize("state", STATES)
def test_route_is_recorded_not_enforced(state):
    request = to_b_request(state)
    route = state["selection_route"]
    assert request["route"] == {
        k: route[k] for k in ("kind", "confirm", "partial", "reasons", "requirement_ids")
    }
    assert len(request["scopes"]) == len(sent_ids(state))


def test_a_state_whose_requirements_all_have_no_table_sends_no_scope():
    found = False
    for path in V3_FIXTURES:
        state = load_v3(path)
        if {s["status"] for s in state["selections"]} == {"no_table"}:
            found = True
            request = to_b_request(state)
            assert request["scopes"] == []
            assert {s["reason"] for s in request["skipped"]} == {"no_table"}
    assert found


def test_missing_signals_and_route_give_empty_hints_not_an_error():
    state = copy.deepcopy(load_v3(V3_FIXTURES[0]))
    for selection in state["selections"]:
        selection.pop("signals", None)
    state["selection_route"] = None
    request = to_b_request(state)
    assert check_request(request) == []
    assert request["route"] is None
    for scope in request["scopes"]:
        assert scope["hints"]["value_mentions"] == []
        assert scope["hints"]["a_period_request"] is None


def _rich_state() -> dict:
    """A stored state edited to reach the branches the model-off outputs do not: an auto selection
    with two tables, targets, breakdown, a shared and an ambiguous condition."""
    state = copy.deepcopy(next(load_v3(p) for p in V3_FIXTURES if sent_ids(load_v3(p))))
    rid = sent_ids(state)[0]
    requirement = next(r for r in requirements(state) if r["id"] == rid)
    selection = selection_of(state, rid)
    extra = {
        "id": "X9",
        "kind": "exclude",
        "quote": "q",
        "span": [0, 1],
        "value": "v",
        "attrs": {"text": "v", "complement_hints": []},
        "status": "candidate",
    }
    ambiguous = {**extra, "id": "U9", "kind": "unit", "attrs": {"value": "%", "currency": None}}
    state["analysis"]["conditions"] += [extra, ambiguous]
    requirement["shared_condition_ids"] = ["X9"]
    requirement["ambiguous_condition_ids"] = ["U9"]
    requirement["targets"] = [{"quote": "t", "span": [0, 1], "fragment_id": None, "name": "t"}]
    requirement["breakdown"] = ["b"]
    selection["status"] = "auto"
    selection["tables"].append({**copy.deepcopy(selection["tables"][0]), "table_id": "SECOND"})
    return state


def test_rich_state_carries_auto_two_tables_shared_and_ambiguous_conditions():
    request = to_b_request(_rich_state())
    scope = request["scopes"][0]
    assert scope["decision"] == "auto"
    assert scope["tables"][-1]["table_id"] == "SECOND"
    assert scope["hints"]["conditions"][-1]["id"] == "X9"
    assert scope["hints"]["ambiguous_condition_ids"] == ["U9"]
    assert scope["hints"]["targets"] == ["t"] and scope["hints"]["breakdown"] == ["b"]
    assert check_request(request) == []
    assert to_legacy_a_result(request)["tables"][-1]["table_id"] == "SECOND"


def test_an_ambiguous_condition_that_is_also_applied_is_reported_not_hidden():
    state = _rich_state()
    requirement = next(r for r in requirements(state) if r["id"] == sent_ids(state)[0])
    requirement["ambiguous_condition_ids"] = ["X9"]
    assert any("ambiguous" in p for p in check_request(to_b_request(state)))


@pytest.mark.parametrize("state", STATES)
def test_legacy_a_result_is_every_sent_table_once_in_order_and_loads(state, store):
    expected = []
    for rid in sent_ids(state):
        for t in selection_of(state, rid)["tables"]:
            row = {"org_id": t["org_id"], "table_id": t["table_id"], "table_name": t["table_name"]}
            if row not in expected:
                expected.append(row)
    a_result = to_legacy_a_result(to_b_request(state))
    assert a_result == {"tables": expected}
    for table in a_result["tables"]:
        assert tuple(table) == B_TABLE_FIELDS
        assert store.get(table["org_id"], table["table_id"])["items"]


def test_some_stored_state_sends_tables_to_b():
    assert any(to_legacy_a_result(to_b_request(load_v3(p)))["tables"] for p in V3_FIXTURES)


def test_some_stored_state_carries_a_negated_word():
    assert any(
        scope["hints"]["negated"]
        for path in V3_FIXTURES
        for scope in to_b_request(load_v3(path))["scopes"]
    )


def test_parents_of_an_occurrence_are_carried():
    """Stored states give no parents for the sent tables; an edited one checks they are kept."""
    path = next(
        p
        for p in V3_FIXTURES
        if any(s["hints"]["value_mentions"] for s in to_b_request(load_v3(p))["scopes"])
    )
    state = copy.deepcopy(load_v3(path))
    scope = next(s for s in to_b_request(state)["scopes"] if s["hints"]["value_mentions"])
    mention = scope["hints"]["value_mentions"][0]
    facts = selection_of(state, scope["scope_id"])["signals"]["facts"]["tables"]
    for occ in facts[mention["table_id"]]["indicator"]["occurrences"]:
        if occ.get("value_id") == mention["value_id"]:
            occ["parents"] = ["upper", "top"]
    carried = next(s for s in to_b_request(state)["scopes"] if s["scope_id"] == scope["scope_id"])
    assert carried["hints"]["value_mentions"][0]["parents"] == ["upper", "top"]
