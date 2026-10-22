"""B per scope (plan 04): the resolver path against the text path, scope results, statuses.

The golden B answers are replayed (ScriptedModel); this checks code paths, not model accuracy.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest
from b_v2_support import ROOT, V3_FIXTURES, load_v3

from claview.b.adapter_question import to_b_request_question_level
from claview.b.adapter_v3 import to_b_request
from claview.b.contract import check_scope_result
from claview.b.run import run_b_request
from claview.contracts import Config, Trace
from claview.evaluation import scripted_answers
from claview.fetch import validate_b_result
from claview.graphs import build_b_graph
from claview.models import ScriptedModel
from claview.services import Services
from claview.stages import selection_key

GOLDEN = json.loads((ROOT / "eval" / "cases" / "golden_set_v1.json").read_text(encoding="utf-8"))
GOLDEN_CONDITIONS = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_conditions" / "golden_conditions.json").read_text(
        encoding="utf-8"
    )
)
AS_OF = "2026-10-01"  # the date the golden periods and the suite are pinned to


def services(store, responses, **config) -> Services:
    return Services(
        store,
        search=None,
        model=ScriptedModel(responses),
        config=Config(as_of=AS_OF, **config),
        trace=Trace(),
    )


def text_path(case, store) -> dict:
    """The B graph as it was: the question text gives the period."""
    graph = build_b_graph(services(store, scripted_answers(case)))
    tables = case["expected"]["A"]["tables"]
    return graph.invoke(
        {"query": case["query"], "a_result": case["expected"]["A"]},
        {"recursion_limit": 10 + 4 * len(tables)},
    )["stage_outcome"]


def resolver_path(case, store) -> dict:
    """The same case with the conditions handed to B (period not read from the text)."""
    stored = GOLDEN_CONDITIONS[case["case_id"]]
    hints = {"conditions": stored["conditions"], "calc_hints": stored["calc_hints"]}
    request = to_b_request_question_level(
        case["query"], case["expected"]["A"], as_of=AS_OF, hints=hints
    )
    return run_b_request(request, services(store, scripted_answers(case)))


def keys(rows) -> Counter:
    return Counter(selection_key(r) for r in rows)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["case_id"])
def test_resolver_path_gives_the_text_paths_series_on_every_golden_case(case, store):
    before = text_path(case, store)
    after = resolver_path(case, store)
    [scope] = after["scopes"]
    assert check_scope_result(scope) == []
    if before["status"] == "ok":
        assert scope["status"] == "ok", scope["issues"]
        assert keys(after["selections"]) == keys(before["result"]["selections"])
        validate_b_result({"selections": after["selections"]}, store)
    else:
        assert scope["status"] != "ok"


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["case_id"])
def test_resolver_path_matches_the_golden_periods(case, store):
    after = resolver_path(case, store)
    expected = {
        (r["table_id"], tuple(r["period"].values())) for r in case["expected"]["B"]["selections"]
    }
    got = {(r["table_id"], tuple(r["period"].values())) for r in after["selections"]}
    if after["scopes"][0]["status"] == "ok":
        assert got == expected


def test_every_golden_case_has_stored_conditions():
    assert set(GOLDEN_CONDITIONS) == {c["case_id"] for c in GOLDEN["cases"]}


def _case_with_default_period():
    """A golden case whose stored conditions carry no period: B assumes and records it."""
    for case in GOLDEN["cases"]:
        stored = GOLDEN_CONDITIONS[case["case_id"]]
        if not any(c["kind"] == "period" for c in stored["conditions"]):
            return case
    return None


def test_scope_records_where_period_and_frequency_came_from(store):
    case = next(
        c
        for c in GOLDEN["cases"]
        if any(x["kind"] == "frequency" for x in GOLDEN_CONDITIONS[c["case_id"]]["conditions"])
    )
    [scope] = resolver_path(case, store)["scopes"]
    assert scope["frequency"]["source"] == "question"
    assert scope["frequency"]["rule"] == "question_word"
    assert scope["period"]["applied"]["frequency"] == scope["frequency"]["chosen"]


def test_series_ids_carry_the_scope_and_map_back(store):
    case = GOLDEN["cases"][0]
    result = resolver_path(case, store)
    assert all(r["series_id"].startswith("q1:") for r in result["selections"])
    assert all(scopes == ["q1"] for scopes in result["series_scopes"].values())
    assert set(result["fetched_as"]) == {r["series_id"] for r in result["selections"]}


def test_empty_answer_with_a_settled_period_is_table_unsuitable(store):
    case = next(c for c in GOLDEN["cases"] if len(c["expected"]["A"]["tables"]) == 1)
    responses = {k: [{"selections": []}] for k in scripted_answers(case) if k.startswith("B:")}
    stored = GOLDEN_CONDITIONS[case["case_id"]]
    request = to_b_request_question_level(
        case["query"],
        case["expected"]["A"],
        as_of=AS_OF,
        hints={"conditions": stored["conditions"]},
    )
    [scope] = run_b_request(request, services(store, responses))["scopes"]
    assert scope["status"] == "table_unsuitable"
    assert scope["issues"][0]["resume_at"] == "A_reselect"
    assert check_scope_result(scope) == []


def test_a_frequency_the_table_lacks_stops_before_the_model(store):
    case = next(c for c in GOLDEN["cases"] if len(c["expected"]["A"]["tables"]) == 1)
    table = case["expected"]["A"]["tables"][0]
    meta = store.get(table["org_id"], table["table_id"])
    missing = next(f for f in ("Y", "H", "Q", "M") if f not in meta["periods"])
    conditions = [
        {
            "id": "F1",
            "kind": "frequency",
            "quote": "q",
            "value": missing,
            "attrs": {"value": missing},
        }
    ]
    request = to_b_request_question_level(
        case["query"], case["expected"]["A"], as_of=AS_OF, hints={"conditions": conditions}
    )
    model = services(store, {})  # no scripted answer: a model call would fail differently
    [scope] = run_b_request(request, model)["scopes"]
    assert scope["status"] == "table_unsuitable"
    assert scope["issues"][0]["code"] == "table_unsuitable"
    assert not [e for e in model.trace.events if e["event"] == "model_request"]


def test_series_over_the_cap_asks_per_scope(store):
    case = next(c for c in GOLDEN["cases"] if len(c["expected"]["B"]["selections"]) >= 2)
    stored = GOLDEN_CONDITIONS[case["case_id"]]
    request = to_b_request_question_level(
        case["query"],
        case["expected"]["A"],
        as_of=AS_OF,
        hints={"conditions": stored["conditions"], "calc_hints": stored["calc_hints"]},
    )
    result = run_b_request(
        request, services(store, scripted_answers(case), max_expanded_selections=1)
    )
    [scope] = result["scopes"]
    assert scope["status"] == "ask" and scope["ask"]["kind"] == "series_count"
    assert scope["issues"][0]["code"] == "selection_count_requires_confirmation"
    assert result["selections"] == []
    assert check_scope_result(scope) == []


def test_two_scopes_count_the_cap_separately(store):
    """Each scope runs B on its own, so two scopes of two series each pass a cap of 2."""
    case = next(
        c
        for c in GOLDEN["cases"]
        if len(c["expected"]["B"]["selections"]) == 2 and len(c["expected"]["A"]["tables"]) == 1
    )
    stored = GOLDEN_CONDITIONS[case["case_id"]]
    hints = {"conditions": stored["conditions"], "calc_hints": stored["calc_hints"]}
    request = to_b_request_question_level(
        case["query"], case["expected"]["A"], as_of=AS_OF, hints=hints
    )
    second = copy.deepcopy(request["scopes"][0])
    second["scope_id"] = "q2"
    request["scopes"].append(second)
    answers = scripted_answers(case)
    doubled = {k: v * 2 for k, v in answers.items()}
    result = run_b_request(request, services(store, doubled, max_expanded_selections=2))
    assert [s["status"] for s in result["scopes"]] == ["ok", "ok"]
    assert [len(s["series"]) for s in result["scopes"]] == [2, 2]
    # the same two coordinates: fetched once, both scopes served
    assert len(result["selections"]) == 2
    validate_b_result({"selections": result["selections"]}, store)
    assert all(scopes == ["q1", "q2"] for scopes in result["series_scopes"].values())
    assert {result["fetched_as"][s["series_id"]] for s in result["scopes"][1]["series"]} == {
        r["series_id"] for r in result["selections"]
    }


def test_no_conditions_keeps_the_text_path(store):
    case = GOLDEN["cases"][0]
    request = to_b_request_question_level(case["query"], case["expected"]["A"], as_of=AS_OF)
    result = run_b_request(request, services(store, scripted_answers(case)))
    before = text_path(case, store)
    assert keys(result["selections"]) == keys(before["result"]["selections"])
    assert result["scopes"][0]["period"]["rule"] == "question_text"


def test_default_period_is_assumed_and_recorded(store):
    case = _case_with_default_period()
    if case is None:
        pytest.skip("every stored golden case names a period")
    stored = GOLDEN_CONDITIONS[case["case_id"]]
    request = to_b_request_question_level(
        case["query"],
        case["expected"]["A"],
        as_of=AS_OF,
        hints={"conditions": stored["conditions"]},
    )
    [scope] = run_b_request(request, services(store, scripted_answers(case)))["scopes"]
    if scope["status"] == "ok":
        assert scope["period"]["source"] == "default"
        assert any(a["dimension"] == "period" for a in scope["assumptions"])


@pytest.mark.parametrize("path", V3_FIXTURES, ids=lambda p: p.stem)
def test_stored_claview_v3_outputs_run_through_b(path, store):
    """New A output -> adapter -> B, answers built from the metadata (first item and values)."""
    request = to_b_request(load_v3(path))
    responses = {}
    for scope in request["scopes"]:
        for table in scope["tables"]:
            meta = store.get(table["org_id"], table["table_id"])
            row = {
                "item_id": next(iter(meta["items"])),
                "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
                "expand": {a: "none" for a in meta["dimensions"]},
            }
            responses.setdefault(f"B:{table['org_id']}:{table['table_id']}", []).append(
                {"selections": [row]}
            )
    result = run_b_request(request, services(store, responses))
    for scope in result["scopes"]:
        assert check_scope_result(scope) == [], (scope["scope_id"], check_scope_result(scope))
        # one valid answer per table and the period settled by the resolver: every sent scope is ok
        assert scope["status"] == "ok", (scope["scope_id"], scope["issues"])
        assert scope["period"]["source"] in {"question", "default"}
    if result["selections"]:
        validate_b_result({"selections": result["selections"]}, store)


def _one_table_case():
    return next(c for c in GOLDEN["cases"] if len(c["expected"]["A"]["tables"]) == 1)


def test_the_requests_as_of_reaches_the_models_context_and_the_notices(store):
    """The request's date is the run's date: resolver, notices and the model's context agree."""
    case = _one_table_case()
    stored = GOLDEN_CONDITIONS[case["case_id"]]
    request = to_b_request_question_level(
        case["query"],
        case["expected"]["A"],
        as_of="2026-08-03",
        hints={"conditions": stored["conditions"], "calc_hints": stored["calc_hints"]},
    )
    model = services(store, scripted_answers(case))  # its config says 2026-10-01
    run_b_request(request, model)
    contexts = [e["context"] for e in model.trace.events if e["event"] == "model_request"]
    assert contexts and all(c["as_of"] == "2026-08-03" for c in contexts)


def test_open_periods_are_shown_to_the_model_like_the_text_path(store):
    """ "2020년 이후" (since): the text path showed the computed period, the resolver path too."""
    for case in GOLDEN["cases"]:
        forms = {
            c["attrs"].get("form")
            for c in GOLDEN_CONDITIONS[case["case_id"]]["conditions"]
            if c["kind"] == "period"
        }
        if forms != {"since"} or len(case["expected"]["A"]["tables"]) != 1:
            continue
        text_model = services(store, scripted_answers(case))
        build_b_graph(text_model).invoke(
            {"query": case["query"], "a_result": case["expected"]["A"]}, {"recursion_limit": 20}
        )
        plan_model = services(store, scripted_answers(case))
        stored = GOLDEN_CONDITIONS[case["case_id"]]
        request = to_b_request_question_level(
            case["query"],
            case["expected"]["A"],
            as_of=AS_OF,
            hints={"conditions": stored["conditions"], "calc_hints": stored["calc_hints"]},
        )
        run_b_request(request, plan_model)

        def shown(m):
            return [
                e["context"]["resolved_period"]
                for e in m.trace.events
                if e["event"] == "model_request"
            ]

        assert shown(plan_model) == shown(text_model) and shown(text_model)[0] is not None
        return
    pytest.fail("no golden case with a single 'since' period and one table")


def test_text_path_records_an_assumed_longest_frequency(store):
    """No conditions, no frequency word, several frequencies: B's choice is an assumption."""
    case = next(
        c
        for c in GOLDEN["cases"]
        if len(c["expected"]["A"]["tables"]) == 1
        and len(
            store.get(
                c["expected"]["A"]["tables"][0]["org_id"],
                c["expected"]["A"]["tables"][0]["table_id"],
            )["periods"]
        )
        > 1
        and not any(w in c["query"] for w in ("월", "분기", "반기", "연간", "연도별", "달"))
    )
    request = to_b_request_question_level(case["query"], case["expected"]["A"], as_of=AS_OF)
    [scope] = run_b_request(request, services(store, scripted_answers(case)))["scopes"]
    assert scope["status"] == "ok"
    assert scope["frequency"]["source"] == "default"
    assert {
        "dimension": "frequency",
        "value": scope["frequency"]["chosen"],
        "rule": "longest_frequency",
    } in scope["assumptions"]
    assert check_scope_result(scope) == []


def test_alternative_series_ids_carry_the_scope(store):
    case = next(
        c
        for c in GOLDEN["cases"]
        if len(c["expected"]["A"]["tables"]) == 1
        and not any(x["kind"] == "frequency" for x in GOLDEN_CONDITIONS[c["case_id"]]["conditions"])
        and len(
            store.get(
                c["expected"]["A"]["tables"][0]["org_id"],
                c["expected"]["A"]["tables"][0]["table_id"],
            )["periods"]
        )
        > 1
    )
    [scope] = resolver_path(case, store)["scopes"]
    alternatives = [n for n in scope["notices"] if n["kind"] == "frequency_alternative"]
    assert alternatives
    for notice in alternatives:
        for row in notice["selections"]:
            assert row["series_id"].startswith("q1:alt-") and row["scope_id"] == "q1"
