"""B contract (plan 03 r2, section 1): failure-code table, issues, request and result checks."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from claview.b.contract import (
    FETCH_SELECTION_KEYS,
    ISSUE_KINDS,
    ISSUE_OF_CODE,
    ISSUE_OF_PREFIX,
    RESUME_POINTS,
    SERIES_KEYS,
    check_request,
    check_scope_result,
    fetch_selections,
    issue_of,
    series_fetched_as,
    series_scopes,
)
from claview.fetch import validate_b_result

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "claview"
B_SOURCES = ("stages.py", "metadata.py", "models.py", "fetch.py")
GOLDEN = ROOT / "eval" / "cases" / "golden_set_v1.json"


def raised_codes() -> set[str]:
    codes = set()
    for name in B_SOURCES:
        text = (SRC / name).read_text(encoding="utf-8")
        codes.update(re.findall(r'StageFailure\(\s*f?"([^"]+)"', text))
    return codes


def fetch_issue_codes() -> set[str]:
    """Issue codes fetch reports without stopping (normalize_rows appends them to `issues`)."""
    text = (SRC / "fetch.py").read_text(encoding="utf-8")
    return set(re.findall(r'issues\.append\("([^"]+)"\)', text))


def test_every_b_and_fetch_failure_code_has_a_kind_and_a_resume_point():
    codes = raised_codes()
    assert len(codes) > 40  # the scan found the codes, not an empty set
    for code in codes:
        if "{" in code:
            assert any(code.startswith(p) for p in ISSUE_OF_PREFIX), code
        else:
            assert code in ISSUE_OF_CODE, code


def test_fetch_issue_codes_are_data_issues_at_fetch():
    codes = fetch_issue_codes()
    assert codes  # the scan found them
    for code in codes:
        assert code in ISSUE_OF_CODE, code
        assert issue_of(code)["kind"] == "data" and issue_of(code)["resume_at"] == "fetch"


def test_the_code_table_uses_only_known_kinds_and_resume_points():
    for code, (kind, resume_at, retryable) in ISSUE_OF_CODE.items():
        assert kind in ISSUE_KINDS and resume_at in RESUME_POINTS, code
        assert isinstance(retryable, bool)
    for prefix, (kind, resume_at) in ISSUE_OF_PREFIX.items():
        assert kind in ISSUE_KINDS and resume_at in RESUME_POINTS, prefix


@pytest.mark.parametrize(
    "code, retryable",
    [
        ("kosis_http_429", True),
        ("kosis_http_503", True),
        ("kosis_http_404", False),
        ("hcx_http_500", True),
    ],
)
def test_http_codes_are_retryable_only_for_rate_limit_and_server_errors(code, retryable):
    issue = issue_of(code)
    assert issue["retryable"] is retryable
    assert issue["resume_at"] == ("fetch" if code.startswith("kosis") else "B_select")


def test_unknown_code_is_kept_as_a_validation_issue():
    issue = issue_of("something_new", table_id="T1")
    assert issue == {
        "code": "something_new",
        "kind": "validation",
        "retryable": False,
        "resume_at": "B_select",
        "table_id": "T1",
    }


def test_table_unsuitable_goes_back_to_table_selection():
    assert issue_of("table_unsuitable")["resume_at"] == "A_reselect"


def _series(scope_id: str = "r1") -> dict:
    return {
        "series_id": "S001",
        "label": "L",
        "org_id": "301",
        "table_id": "T1",
        "item_id": "I1",
        "dimensions": {},
        "period": {"frequency": "M", "start": "202401", "end": "202412"},
        "unit": None,
        "scope_id": scope_id,
    }


def _ok_result() -> dict:
    return {
        "scope_id": "r1",
        "status": "ok",
        "series": [_series()],
        "period": {
            "source": "question",
            "requested": None,
            "applied": {"frequency": "M", "start": "202401", "end": "202412"},
        },
        "frequency": {"source": "question", "chosen": "M", "alternatives": []},
        "assumptions": [],
        "decided": {"objL1": "code"},
        "notices": [],
        "issues": [],
    }


def _assumed_result() -> dict:
    result = _ok_result()
    result["period"]["source"] = "default"
    result["frequency"] = {"source": "default", "chosen": "M", "alternatives": ["Q"]}
    result["assumptions"] = [
        {"dimension": "frequency", "value": "M", "rule": "longest_frequency"},
        {"dimension": "period", "value": result["period"]["applied"], "rule": "recent_years"},
    ]
    return result


def _ask_result() -> dict:
    return {
        **_ok_result(),
        "status": "ask",
        "series": [],
        "period": None,
        "ask": {"kind": "series_count", "message": "m", "options": [{"value": "all"}]},
    }


def test_a_complete_ok_result_holds_the_contract():
    assert set(_series()) == SERIES_KEYS
    assert check_scope_result(_ok_result()) == []


def test_an_assumed_period_and_frequency_recorded_in_assumptions_hold_the_contract():
    assert check_scope_result(_assumed_result()) == []


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda r: r["assumptions"][0].update(value=None), "value: empty"),
        (lambda r: r["assumptions"][0].update(value="D"), "not a frequency"),
        (lambda r: r["assumptions"][1].update(value={"frequency": "M"}), "not a period"),
    ],
)
def test_assumption_values_are_checked(change, problem):
    result = _assumed_result()
    change(result)
    problems = check_scope_result(result)
    assert any(problem in p for p in problems), problems


def test_a_complete_ask_result_holds_the_contract():
    assert check_scope_result(_ask_result()) == []


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda r: r.update(series=[]), "ok without series"),
        (lambda r: r.update(status="unresolved"), "series on a scope that is not ok"),
        (lambda r: r.update(status="ask", series=[]), "ask without a question"),
        (lambda r: r.update(status="table_unsuitable", series=[]), "failure without an issue"),
        (lambda r: r["series"][0].update(scope_id="r2"), "scope_id differs"),
        (lambda r: r["series"][0].pop("label"), "series[0]: keys differ"),
        (lambda r: r.update(series=[{"scope_id": "r1"}]), "series[0]: keys differ"),
        (lambda r: r["series"][0].update(series_id=""), "an id or the label is empty"),
        (lambda r: r["series"][0].update(period="2024"), "period malformed"),
        (lambda r: r["series"][0].update(dimensions={"objL1": []}), "period malformed"),
        (lambda r: r["series"][0]["period"].update(start="x"), "period malformed"),
        (lambda r: r["series"][0]["period"].update(start="202413"), "period malformed"),
        (lambda r: r["series"][0]["period"].update(start="202501"), "period malformed"),
        (lambda r: r["series"].append(copy.deepcopy(r["series"][0])), "duplicate series_id"),
        (lambda r: r["period"].update(source="guess"), "period.source"),
        (lambda r: r.update(period={"source": "user"}), "period: missing fields"),
        (lambda r: r["period"].update(applied=None), "ok without an applied period"),
        (lambda r: r["period"].update(applied="bad"), "period.applied: not a period"),
        (lambda r: r.update(frequency={"source": "user"}), "frequency: missing fields"),
        (lambda r: r["frequency"].update(source="guess"), "frequency.source"),
        (lambda r: r["frequency"].update(alternatives=123), "alternatives"),
        (lambda r: r["frequency"].update(chosen="D"), "frequency.chosen"),
        (lambda r: r["decided"].update(objL2="guess"), "decided[objL2]"),
        (lambda r: r.update(status="done"), "status: unknown"),
        (lambda r: r.update(issues=[{"kind": "data", "resume_at": "fetch"}]), "code missing"),
        (
            lambda r: r.update(issues=[{"code": "x", "kind": "data", "resume_at": "fetch"}]),
            "retryable missing",
        ),
        (lambda r: r.update(issues=None), "wrong type"),
        (lambda r: r.update(series=None), "series: not a list"),
        (lambda r: r.update(assumptions=None), "assumptions: not a list"),
        (lambda r: r.update(assumptions=[{"dimension": "x"}]), "assumptions[0]: keys differ"),
        (
            lambda r: r.update(assumptions=[{"dimension": "unit", "value": 1, "rule": "r"}]),
            "dimension: unknown",
        ),
        (lambda r: r["period"].update(source="default"), "assumed period not recorded"),
        (lambda r: r["frequency"].update(source="default"), "assumed frequency"),
    ],
)
def test_result_check_reports_each_broken_rule(change, problem):
    result = _ok_result()
    change(result)
    problems = check_scope_result(result)
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda r: r.update(ask={"kind": "series_count"}), "message or options missing"),
        (lambda r: r["ask"].update(options=[]), "no option to choose"),
        (lambda r: r["ask"].update(options=[{}]), "no option to choose"),
        (lambda r: r["ask"].update(kind="other"), "ask.kind"),
        # frequency and period are assumed, never asked (user decisions 1 and 2)
        (lambda r: r["ask"].update(kind="frequency"), "ask.kind"),
        (lambda r: r["ask"].update(kind="period"), "ask.kind"),
    ],
)
def test_ask_check_reports_each_broken_rule(change, problem):
    result = _ask_result()
    change(result)
    problems = check_scope_result(result)
    assert any(problem in p for p in problems), problems


def _hints() -> dict:
    return {
        "text": "q",
        "indicator": None,
        "targets": [],
        "breakdown": [],
        "conditions": [{"id": "P1", "kind": "period", "quote": "q", "value": None, "attrs": {}}],
        "ambiguous_condition_ids": [],
        "negated": [],
        "value_mentions": [],
        "covers": {},
        "a_period_request": None,
    }


def _request() -> dict:
    return {
        "question": "q",
        "as_of": None,
        "scopes": [
            {
                "scope_id": "r1",
                "tables": [{"org_id": "301", "table_id": "T1", "table_name": None}],
                "decision": "auto",
                "hints": _hints(),
            }
        ],
        "skipped": [{"scope_id": "r2", "reason": "no_table"}],
        "route": None,
        "relations": [],
        "table_group_hint": [],
        "source": "test",
    }


def test_a_complete_request_holds_the_contract():
    assert check_request(_request()) == []


@pytest.mark.parametrize("keep", [(), ("conditions",), ("text", "value_mentions")])
def test_hints_are_optional(keep):
    request = _request()
    request["scopes"][0]["hints"] = {k: v for k, v in _hints().items() if k in keep}
    assert check_request(request) == []


def _mention(table_id: str) -> dict:
    return {
        "table_id": table_id,
        "obj_id": "ITEM",
        "value_id": "X",
        "name": None,
        "method": "exact",
        "role": None,
        "relation": None,
        "parents": [],
    }


def _hint(r: dict) -> dict:
    return r["scopes"][0]["hints"]


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda r: r.pop("route"), "keys differ"),
        (lambda r: r.update(as_of=123), "as_of: not text"),
        (lambda r: r.update(scopes=None), "scopes: not a list"),
        (lambda r: r.update(table_group_hint=[[1]]), "table_group_hint"),
        (lambda r: r["scopes"][0].update(scope_id=[]), "scope_id: empty or not text"),
        (lambda r: r["scopes"][0].update(decision=[]), "decision: unknown"),
        (lambda r: r["scopes"].append(copy.deepcopy(r["scopes"][0])), "duplicate scope_id"),
        (lambda r: r["scopes"][0].update(decision="no_table"), "decision: unknown"),
        (lambda r: r["scopes"][0].update(tables=[]), "tables: empty"),
        (lambda r: r["scopes"][0]["tables"][0].update(table_id=""), "table_id empty"),
        (lambda r: _hint(r).update(extra=1), "hints: unknown keys"),
        (lambda r: _hint(r).update(conditions=[{}]), "conditions[0]: keys differ"),
        (lambda r: _hint(r)["conditions"][0].update(attrs=None), "attrs: not an object"),
        (lambda r: _hint(r).update(targets="x"), "targets: not a list of text"),
        (lambda r: _hint(r).update(targets=[{}]), "targets: not a list of text"),
        (lambda r: _hint(r).update(ambiguous_condition_ids=[[]]), "ambiguous_condition_ids"),
        (lambda r: _hint(r).update(negated=[1]), "negated"),
        (lambda r: _hint(r).update(covers=[]), "covers: not an object"),
        (lambda r: _hint(r).update(a_period_request="2024"), "a_period_request"),
        (lambda r: _hint(r)["ambiguous_condition_ids"].append("P1"), "ambiguous"),
        (lambda r: _hint(r)["value_mentions"].append(_mention("T9")), "not in the scope"),
        (
            lambda r: _hint(r)["value_mentions"].append({**_mention("T1"), "table_id": []}),
            "not in the scope",
        ),
        (lambda r: _hint(r).update(inferred=[1]), "inferred"),
        (
            lambda r: _hint(r)["value_mentions"].append({**_mention("T1"), "parents": [{}]}),
            "parents: not a list of names",
        ),
        (lambda r: r["skipped"].append({"scope_id": "r1", "reason": "no_table"}), "also sent"),
        (lambda r: r["skipped"].append(dict(r["skipped"][0])), "skipped: duplicate"),
    ],
)
def test_request_check_reports_each_broken_rule(change, problem):
    request = _request()
    change(request)
    problems = check_request(request)
    assert any(problem in p for p in problems), problems


@pytest.mark.parametrize("bad", [None, [], "x", {"question": "q"}])
def test_checks_never_raise_on_wrong_top_level_types(bad):
    assert check_request(bad)
    assert check_scope_result(bad)


def _poison(value):
    """Each nested position of a valid object replaced by values of the wrong type."""
    yield from ([], [[]], {}, None, 1, "x")


def _positions(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path + (key,)
            yield from _positions(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield path + (i,)
            yield from _positions(value, path + (i,))


def _replaced(node, path, value):
    node = copy.deepcopy(node)
    target = node
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return node


def _request_with_everything() -> dict:
    request = _request()
    hints = request["scopes"][0]["hints"]
    hints["value_mentions"].append({**_mention("T1"), "parents": ["upper"]})
    hints["calc_hints"] = [{"quote": "q", "kind": "change"}]
    hints["inferred"] = [{"kind": "frequency", "value": "M", "from_condition_id": "P1"}]
    return request


@pytest.mark.parametrize(
    "build, check",
    [
        (_request_with_everything, check_request),
        (_ok_result, check_scope_result),
        (_ask_result, check_scope_result),
        (_assumed_result, check_scope_result),
    ],
)
def test_checks_never_raise_on_a_wrong_type_at_any_depth(build, check):
    valid = build()
    for path in _positions(valid):
        for value in _poison(None):
            check(_replaced(valid, path, value))  # must return, never raise


def test_fetch_selections_flatten_ok_scopes_into_what_fetch_accepts(store):
    """Golden B selections, wrapped as scope results, flatten back into a list fetch validates."""
    case = next(c for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"])
    rows = case["expected"]["B"]["selections"]
    result = {**_ok_result(), "series": [{**row, "scope_id": "r1"} for row in rows]}
    other = {**_ask_result(), "scope_id": "r2"}
    flat = fetch_selections([result, other])
    assert flat == [{k: row[k] for k in FETCH_SELECTION_KEYS} for row in rows]
    validate_b_result({"selections": flat}, store)  # raises on any shape the fetch rejects
    assert series_scopes([result, other]) == {row["series_id"]: ["r1"] for row in rows}


def test_the_same_series_of_two_scopes_is_fetched_once(store):
    """Two requirements asking for the same coordinate: fetch gets it once, both scopes keep it."""
    case = next(c for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"])
    rows = case["expected"]["B"]["selections"]
    first = {
        **_ok_result(),
        "series": [
            {**row, "series_id": "r1:" + row["series_id"], "scope_id": "r1"} for row in rows
        ],
    }
    second = {
        **_ok_result(),
        "scope_id": "r2",
        "series": [
            {**row, "series_id": "r2:" + row["series_id"], "scope_id": "r2"} for row in rows
        ],
    }
    flat = fetch_selections([first, second])
    assert [r["series_id"] for r in flat] == ["r1:" + r["series_id"] for r in rows]
    validate_b_result({"selections": flat}, store)
    fetched = series_fetched_as([first, second])
    assert all(fetched["r2:" + r["series_id"]] == "r1:" + r["series_id"] for r in rows)
    assert all(series_scopes([first, second])["r1:" + r["series_id"]] == ["r1", "r2"] for r in rows)
