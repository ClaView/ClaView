"""B per scope (plans 04-05): one B run per requirement, results in the B contract shape.

For every scope of a `BRequest`:
1. A scope named by the request's `input_errors` (or every scope, for a request-level error) ends
   with the input contract error; nothing runs for it.
2. When the scope's hints carry `conditions`, each table's frequency and period come from the
   shared resolver (`claview_period`, through `claview.b.period_resolver`) with the scope's
   condition slots, inference hints, the bundle's anchor context and the table's assigned share;
   they are handed to the B graph as `period_plans`. Without `conditions` the B graph reads the
   question text as before.
3. A table whose share lies wholly outside its coverage (`no_data_in_range`) is left out when
   other tables of the scope remain; it stays reported. If none remains, the scope is unresolved.
4. The existing B graph runs once per scope (the series cap counts per scope).
5. The outcome becomes a `BScopeResult`: ok, ask (series over the cap), table_unsuitable or
   unresolved, series ids unique across scopes (`<scope_id>:S001`), the month coverage of the scope
   (requested, covered by the union of its tables, unmet), the condition slots with `applied` filled
   by B, and stage-B shared assumptions.

The request's `as_of` is the reference date of the whole run. The question text sent to the model
is the whole question for every scope; hints are not put in the model's context (user decision 5).
"""

from __future__ import annotations

import copy
import dataclasses

from ..contracts import Json, StageFailure
from ..graphs import build_b_graph
from ..services import Services
from ..stages import decide_frequency
from .contract import (
    BRequest,
    BScopeResult,
    bundle_segment_problems,
    check_a_assumptions,
    check_bundle_shape,
    fetch_selections,
    issue_of,
    series_fetched_as,
    series_scopes,
)
from .period_resolver import resolve_period_for_table

SERIES_COUNT_OPTIONS = [
    {"value": "proceed", "label": "그대로 조회"},
    {"value": "narrow", "label": "범위를 줄여 다시 묻기"},
]
INTERPRETATIONS = {
    "longest_frequency": "질문이 주기를 정하지 않아 표의 가장 긴 주기로 가정",
    "period_unit": "질문 기간의 단위로 주기를 가정",
    "period_unit_finest": "질문 기간의 단위들 중 표가 지원하는 가장 세밀한 주기로 가정",
    "recent_years": "질문이 기간을 정하지 않아 기본 최근 구간으로 가정",
    "recent_rounded_up": "최근 N 단위를 표 주기로 올림해 가정",
}


def _key(table: Json) -> str:
    return f"{table['org_id']}:{table['table_id']}"


def _bundle_stop(scope: Json) -> tuple[str, list[str]] | None:
    """The input contract error of the scope's bundle, checked as `check_request` checks it and
    whether or not the hints carry conditions: a broken bundle never reaches the model. Its shape
    first (input_contract_error), then each table's segments (invalid_assignment)."""
    if "bundle" not in scope:
        return None
    bundle = scope["bundle"]
    own = [t.get("table_id") for t in scope.get("tables") or [] if isinstance(t, dict)]
    shape = check_bundle_shape(bundle, own)
    if shape:
        return "input_contract_error", shape
    segments = bundle_segment_problems(bundle)
    return ("invalid_assignment", segments) if segments else None


def period_plans(scope: Json, services: Services, as_of: str | None) -> dict[str, Json] | None:
    """table key -> resolver result per table; None when the hints give no conditions."""
    hints = scope.get("hints") or {}
    if "conditions" not in hints:
        return None
    bundle = scope.get("bundle") or {}
    context = bundle.get("context")
    assignments = bundle.get("assignments") or {}
    plans = {}
    for table in scope["tables"]:
        try:
            meta = services.store.get(table["org_id"], table["table_id"])
        except StageFailure:
            continue  # the B graph's load step reports the metadata problem itself
        plans[_key(table)] = resolve_period_for_table(
            hints["conditions"],
            meta,
            as_of,
            hints.get("calc_hints"),
            hints["inferred"] if "inferred" in hints else None,
            condition_fields=hints.get("condition_fields"),
            context=context,
            assignment=assignments.get(table["table_id"]),
        )
    return plans


# ---------------------------------------------------------------- month coverage


def _month(text: str) -> int:
    return int(text[:4]) * 12 + int(text[5:7]) - 1


def _month_text(month: int) -> str:
    year, rest = divmod(month, 12)
    return f"{year:04d}-{rest + 1:02d}"


def _merge(pieces: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for first, last in sorted(pieces):
        if out and first <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], last)
        else:
            out.append([first, last])
    return [(a, b) for a, b in out]


def _minus(pieces: list[tuple[int, int]], cut: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for first, last in pieces:
        start = first
        for a, b in cut:
            if b < start or a > last:
                continue
            if a > start:
                out.append((start, a - 1))
            start = max(start, b + 1)
        if start <= last:
            out.append((start, last))
    return out


def _coverage_months(plans: list[Json]) -> Json | None:
    """The scope's requested months (the union over its tables: each table counts a relative
    period back from its own anchor), the union its tables cover, and what none covers."""
    plans = [p for p in plans if isinstance(p.get("periods"), dict)]
    if not plans:
        return None
    requested = _merge(
        [
            (_month(s["start"]), _month(s["end"]))
            for plan in plans
            for s in plan["periods"]["requested_months"]
        ]
    )
    covered = _merge(
        [
            (_month(s["start"]), _month(s["end"]))
            for plan in plans
            for s in plan["periods"]["covered_months"]
        ]
    )
    covered_in_request = _merge(
        [(max(a, c), min(b, d)) for a, b in requested for c, d in covered if max(a, c) <= min(b, d)]
    )

    def text(pieces):
        return [{"start": _month_text(a), "end": _month_text(b)} for a, b in pieces]

    return {
        "requested": text(requested),
        "covered": text(covered_in_request),
        "unmet": text(_minus(requested, covered)),
    }


# ---------------------------------------------------------------- records


def _by_table_notice(applied: dict[str, Json]) -> list[Json]:
    if len({(a["frequency"], a["start"], a["end"]) for a in applied.values()}) > 1:
        return [{"kind": "period", "code": "period_differs_by_table", "by_table": applied}]
    return []


def _plan_records(plans: dict[str, Json]) -> tuple:
    ok = [p for p in plans.values() if p["status"] == "ok"]
    if not ok:
        return None, None, [], []
    first = ok[0]
    period = {
        "source": first["slots"]["period"]["source"],
        "requested": first["slots"]["period"]["value"],
        "applied": dict(first["applied"]),
        "rule": first["slots"]["period"]["rule"],
        "coverage_months": _coverage_months(ok),
    }
    frequency = {
        "source": first["slots"]["frequency"]["source"],
        "chosen": first["frequency"],
        "alternatives": list(first["slots"]["frequency"]["alternatives"]),
        "rule": first["slots"]["frequency"]["rule"],
    }
    assumptions = [a for p in ok for a in p["assumptions"]]
    assumptions = [a for i, a in enumerate(assumptions) if a not in assumptions[:i]]
    applied = {key: p["applied"] for key, p in plans.items() if p["status"] == "ok"}
    notices = _by_table_notice(applied)
    for key, plan in plans.items():
        for notice in plan["notices"]:
            notices.append({"kind": "period", "table": key, **notice})
    return period, frequency, assumptions, notices


def _text_records(question: str, rows: list[Json], services: Services) -> tuple:
    """Records of the text path: the period B read from the question; the frequency's source told
    the way B chose it (stated in the text, the table's only one, or the longest = assumed)."""
    if not rows:
        return None, None, [], []
    applied = dict(rows[0]["period"])
    meta = services.store.get(rows[0]["org_id"], rows[0]["table_id"])
    chosen, others = decide_frequency(question, meta)
    if len(meta["periods"]) == 1:
        source, rule = "catalog", "table_single"
    elif others:
        source, rule = "default", "longest_frequency"
    else:
        source, rule = "question", "question_text"
    period = {"source": "question", "requested": None, "applied": applied, "rule": "question_text"}
    frequency = {
        "source": source,
        "chosen": applied["frequency"],
        "alternatives": [f for f in others if f != applied["frequency"]],
        "rule": rule,
    }
    assumptions = []
    if source == "default" and chosen == applied["frequency"]:
        assumptions.append({"dimension": "frequency", "value": chosen, "rule": rule})
    by_table = {f"{r['org_id']}:{r['table_id']}": dict(r["period"]) for r in rows}
    return period, frequency, assumptions, _by_table_notice(by_table)


def _condition_fields(scope: Json, plans: dict[str, Json] | None) -> dict:
    """The scope's condition slots: A's source/confirmed/diagnosis kept, `applied` filled by B
    (true when any table used the condition, false when the tables all left it unused, None for
    conditions B does not apply)."""
    hints = scope.get("hints") or {}
    given = hints.get("condition_fields")
    given = given if isinstance(given, dict) else {}
    conditions = hints.get("conditions")
    out = {}
    for condition in conditions if isinstance(conditions, list) else []:
        cid = condition.get("id") if isinstance(condition, dict) else None
        if not isinstance(cid, str):
            continue  # a malformed condition: the resolver reported it as an input error
        flags = [
            p["condition_fields"].get(cid, {}).get("applied")
            for p in (plans or {}).values()
            if p["status"] == "ok"
        ]
        flags = [f for f in flags if f is not None]
        mine = given.get(cid) if isinstance(given.get(cid), dict) else {}
        entry = {
            "source": mine.get("source", "question"),
            "confirmed": mine.get("confirmed"),
            "applied": (any(flags) if flags else None),
        }
        if "period_diagnosis" in mine:
            entry["period_diagnosis"] = mine["period_diagnosis"]
        out[cid] = entry
    return out


def _shared_assumptions(scope: Json, assumptions: list[Json]) -> list[Json]:
    """Stage-B entries of the shared assumption contract for this scope."""
    hints = scope.get("hints") or {}
    conditions = hints.get("conditions") if isinstance(hints.get("conditions"), list) else []
    period_ids = [
        c["id"]
        for c in conditions
        if isinstance(c, dict) and c.get("kind") == "period" and isinstance(c.get("id"), str)
    ]
    inferred = hints.get("inferred") if isinstance(hints.get("inferred"), list) else []
    hint_ids = [
        h["from_condition_id"]
        for h in inferred
        if isinstance(h, dict) and isinstance(h.get("from_condition_id"), str)
    ]
    out = []
    used_ids: dict[str, int] = {}
    for entry in assumptions:
        rule = entry["rule"]
        base_rule = rule.split(":")[0].split("+")[0]
        if entry["dimension"] == "period":
            condition_ids = period_ids
        elif base_rule.startswith("period_unit"):
            condition_ids = [i for i in hint_ids if i] or period_ids
        else:
            condition_ids = []
        default = base_rule in {"longest_frequency", "recent_years"}
        # tables of one scope may assume different values by one rule: each keeps its own id
        assumption_id = f"B:{scope['scope_id']}:{entry['dimension']}:{rule}"
        used_ids[assumption_id] = used_ids.get(assumption_id, 0) + 1
        if used_ids[assumption_id] > 1:
            assumption_id += f"#{used_ids[assumption_id]}"
        out.append(
            {
                "assumption_id": assumption_id,
                "requirement_ids": [scope["scope_id"]],
                "refs": {"segment_ids": [], "mention_ids": [], "condition_ids": condition_ids},
                "reason_code": rule,
                "adopted": {
                    "value": entry["value"],
                    "interpretation": INTERPRETATIONS.get(base_rule, rule),
                },
                "source": "default" if default else "question",
                "confirmed": False,
                "stage": "B",
            }
        )
    return out


def _series(scope_id: str, rows: list[Json]) -> list[Json]:
    return [
        {**row, "series_id": f"{scope_id}:{row['series_id']}", "scope_id": scope_id} for row in rows
    ]


def _notices(scope_id: str, b_result: Json) -> list[Json]:
    out = []
    for notice in b_result.get("period_notices") or []:
        out.append({"kind": "period", **notice})
    for notice in b_result.get("expand_notices") or []:
        out.append({"kind": "expand", **notice})
    for alt in b_result.get("frequency_alternatives") or []:
        # alternative series are not fetched; their ids are kept apart from the scope's own series
        rows = [
            {
                **row,
                "series_id": f"{scope_id}:alt-{alt['frequency']}:{row['series_id']}",
                "scope_id": scope_id,
            }
            for row in alt.get("selections") or []
        ]
        out.append({"kind": "frequency_alternative", **alt, "selections": rows})
    return out


def _empty_result(scope_id: str, code: str, notices: list[Json]) -> BScopeResult:
    status = "table_unsuitable" if code == "table_unsuitable" else "unresolved"
    return {
        "scope_id": scope_id,
        "status": status,
        "series": [],
        "period": None,
        "frequency": None,
        "assumptions": [],
        "decided": {},
        "notices": notices,
        "issues": [issue_of(code)],
    }


def run_scope(scope: Json, question: str, services: Services) -> BScopeResult:
    """One scope through the B graph (services.config.as_of is the run's reference date)."""
    scope_id = scope["scope_id"]
    stop = _bundle_stop(scope)
    if stop is not None:
        code, problems = stop
        notices = [{"kind": "input", "code": code, "detail": p} for p in problems]
        result = _empty_result(scope_id, code, notices)
        result["condition_fields"] = _condition_fields(scope, None)
        return result
    plans = period_plans(scope, services, services.config.as_of)
    tables = [dict(t) for t in scope["tables"]]
    dropped = []
    left_out: list[Json] = []
    if plans is not None:
        invalid = [p for p in plans.values() if p["status"] == "invalid_input"]
        if invalid:
            # an input contract error (malformed condition, anchor after a table's latest period):
            # the scope ends before any model call
            issue = invalid[0]["issue"]
            notices = [{"kind": "input", "code": issue["code"], "detail": issue.get("detail")}]
            result = _empty_result(scope_id, issue["code"], notices)
            result["condition_fields"] = _condition_fields(scope, None)
            return result
        no_data = [k for k, p in plans.items() if p["status"] == "no_data_in_range"]
        keep = [t for t in tables if _key(t) not in no_data]
        if no_data and keep:
            dropped = [
                {
                    "kind": "period",
                    "code": "no_data_in_range",
                    "table": k,
                    "coverage": plans[k]["coverage"],
                    "requested": plans[k]["periods"],
                }
                for k in no_data
            ]
            tables = keep
            left_out = [plans[k] for k in no_data]  # their requests still count as unmet
            plans = {k: p for k, p in plans.items() if k not in no_data}
        elif no_data:
            notices = [
                {
                    "kind": "period",
                    "code": "no_data_in_range",
                    "table": k,
                    "coverage": plans[k]["coverage"],
                }
                for k in no_data
            ]
            coverage = _coverage_months(list(plans.values()))
            if coverage:
                notices.append({"kind": "period", "code": "unmet_months", **coverage})
            result = _empty_result(scope_id, "no_data_in_range", notices)
            result["condition_fields"] = _condition_fields(scope, plans)
            return result
    state: Json = {"query": question, "a_result": {"tables": tables}}
    if plans is not None:
        state["period_plans"] = plans
    if "structure" in (scope.get("hints") or {}):
        # the interpretation's own structure: no question-word exclusion rule (decision 10/21)
        state["structured"] = True
    outcome = build_b_graph(services).invoke(state, {"recursion_limit": 10 + 4 * len(tables)})[
        "stage_outcome"
    ]
    b_result = outcome["result"] or {}
    rows = (b_result.get("selections") or []) if outcome["status"] == "ok" else []
    if plans is not None:
        period, frequency, assumptions, extra = _plan_records(plans)
        if period is not None and left_out:
            ok = [p for p in plans.values() if p["status"] == "ok"]
            period["coverage_months"] = _coverage_months(ok + left_out)
    else:
        period, frequency, assumptions, extra = _text_records(question, rows, services)
    result: BScopeResult = {
        "scope_id": scope_id,
        "status": "ok",
        "series": _series(scope_id, rows),
        "period": period,
        "frequency": frequency,
        "assumptions": assumptions,
        "decided": {axis: "model" for row in rows for axis in row["dimensions"]},
        "notices": dropped + extra + _notices(scope_id, b_result),
        "issues": [],
        "condition_fields": _condition_fields(scope, plans),
        "shared_assumptions": _shared_assumptions(scope, assumptions),
    }
    if outcome["status"] == "ok":
        return result
    code = outcome["issues"][0]["code"] if outcome["issues"] else "b_failed"
    result.update(series=[], decided={}, issues=[issue_of(code)])
    if outcome["status"] == "needs_confirmation":
        result["status"] = "ask"
        result["ask"] = {
            "kind": "series_count",
            "message": b_result.get("message") or "선택된 계열 수가 상한을 넘습니다.",
            "options": [dict(o) for o in SERIES_COUNT_OPTIONS],
        }
        result["notices"].append(
            {"kind": "series_count", "candidate_labels": b_result.get("candidate_labels", [])}
        )
    else:
        result["status"] = "table_unsuitable" if code == "table_unsuitable" else "unresolved"
    return result


def run_b_request(request: BRequest, services: Services) -> Json:
    """Every scope of the request through B.

    Returns {scopes: [BScopeResult], skipped, selections (what fetch reads: ok scopes, each
    coordinate once), series_scopes (fetched series_id -> scope ids), fetched_as (series_id ->
    the fetched series_id holding the same coordinate)}.
    """
    as_of = request.get("as_of")
    if as_of and as_of != services.config.as_of:
        services = dataclasses.replace(
            services, config=dataclasses.replace(services.config, as_of=as_of)
        )
    errors = request.get("input_errors") or []
    request_level = [e for e in errors if not e.get("scope_id")]
    given = "a_assumptions" in request
    if given:
        # A's public assumptions are checked again here (a request built outside the adapter):
        # a broken envelope ends every scope before the model and the fetch
        request_level += [
            {"code": "input_contract_error", "detail": p}
            for p in check_a_assumptions(request["a_assumptions"])
            if {"code": "input_contract_error", "detail": p} not in request_level
        ]
    results = []
    for scope in request["scopes"]:
        mine = request_level + [e for e in errors if e.get("scope_id") == scope["scope_id"]]
        if mine:
            notices = [{"kind": "input", **e} for e in mine]
            results.append(_empty_result(scope["scope_id"], "input_contract_error", notices))
        else:
            results.append(run_scope(scope, request["question"], services))
        # what the adapter noticed reading the selection comes first in the scope's notices
        adapter = [{"kind": "adapter", **n} for n in scope.get("adapter_notices") or []]
        results[-1]["notices"] = adapter + results[-1]["notices"]
        if given:
            # no ground to say a stage-B assumption replaces one of A's: never guessed
            for entry in results[-1].get("shared_assumptions") or []:
                entry.setdefault("supersedes", None)
    out = {
        "scopes": results,
        "skipped": [dict(s) for s in request["skipped"]],
        "selections": fetch_selections(results),
        "series_scopes": series_scopes(results),
        "fetched_as": series_fetched_as(results),
    }
    if given:
        out["a_assumptions"] = copy.deepcopy(request["a_assumptions"])  # unchanged, not merged
    return out
