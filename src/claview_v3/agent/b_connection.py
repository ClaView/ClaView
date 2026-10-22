"""Explicit A -> B port, after A's existing outer graph returns.

The port owns to_b_request -> check_request -> run_b_request -> fetch. It must
advertise the A assumption contract and is never created implicitly. A/B package
isolation belongs to the port (the offline integration uses a separate process).
"""
from __future__ import annotations

import copy

from .assumptions import VERSION, check, digest, reading_id


class AdapterPort:
    """Bind real B functions in an isolated B worker or embedding application.

    Callers supply services and fetch explicitly. No SDK, key or live service is
    constructed here. B itself must acknowledge and echo the assumption contract.
    """

    def __init__(self, *, adapter, check_request, check_scope_result, run_b, fetch,
                 services, contract_version, check_result, a_assumptions_version=None):
        if not callable(check_result):
            raise TypeError("B result/request checker required")
        self.adapter = adapter
        self.check_request = check_request
        self.check_scope_result = check_scope_result
        self.run_b = run_b
        self.fetch = fetch
        self.services = services
        self.contract_version = contract_version
        self.a_assumptions_version = a_assumptions_version
        self.check_result = check_result

    def __call__(self, payload):
        request = self.adapter(copy.deepcopy(payload), a_assumptions=copy.deepcopy(payload["a_assumptions"]))
        if request.get("a_assumptions") != payload["a_assumptions"] or self.check_request(request) or request.get("input_errors"):
            raise ValueError("B request contract")
        expected = sorted(k["requirement_id"] for k in payload["requirement_keys"])
        if sorted(s["scope_id"] for s in request.get("scopes") or []) != expected:
            raise ValueError("B adapter requirement mismatch")
        result = self.run_b(request, self.services)
        _validate_result(result, payload["a_assumptions"], expected)
        if self.check_result(result, request):
            raise ValueError("B result/request contract")
        if any(self.check_scope_result(s) for s in result["scopes"]):
            raise ValueError("B scope result contract")
        # Validation precedes fetch. B's existing selection list contains only ok
        # scopes. Its original status/coverage/notices remain visible in result.
        result = copy.deepcopy(result)
        try:
            result["fetch"] = self.fetch(result, self.services) if result.get("selections") else None
        except Exception as exc:  # noqa: BLE001 - retain validated B results even when fetch fails
            result["fetch"] = {"status": "error", "error_type": type(exc).__name__}
        return result


def connect(state, port=None):
    from .session import session_data, with_session
    out = copy.deepcopy(state)
    response = out["response"]
    route = out.get("selection_route") or {}
    envelope = response["assumptions"]
    errors = check(envelope)
    if errors:
        raise ValueError("invalid A assumptions")
    eligible = set(route.get("eligible") or [])
    result = {"status": "not_run", "requirements": [], "skipped": copy.deepcopy(route.get("skipped") or []),
              "a_assumptions": envelope, "superseded": []}
    response["b"] = result
    response["execution_status"] = "not_executed"
    if response["status"] == "awaiting_answer":
        response["execution_status"] = "awaiting_answer"
        result["reason"] = "awaiting_answer"
        return out
    if not eligible:
        result["reason"] = "no_executable_requirements"
        return out
    if port is None:
        result["reason"] = "b_port_not_configured"
        return out
    if getattr(port, "a_assumptions_version", None) != VERSION:
        result.update(status="contract_pending", reason="a_assumptions_version")
        return out
    data = copy.deepcopy(session_data(out))
    cache = data.setdefault("b_cache", {})
    analysis = out["analysis"]
    reading = reading_id(analysis)
    selections = {s["requirement_id"]: s for s in out.get("selections") or []}
    groups = [{rid} for rid in sorted(eligible)]
    rejected = []
    relations = (analysis.get("interpretation") or {}).get("relations") or []
    for relation in relations:
        ids = set(relation.get("requirement_ids") or [])
        touching = [g for g in groups if g & ids]
        if touching:
            merged = set.union(ids, *touching)
            groups = [g for g in groups if g not in touching] + [merged]
    for group in groups:
        rids = sorted(group)
        if not group <= eligible:
            result["skipped"] += [{"requirement_id": rid, "reason": "relation_operand_unavailable"} for rid in rids]
            continue
        if any(not selections[rid].get("bundle") or not
               (selections[rid].get("signals", {}).get("plan17") or {}).get("b_handoff_ready") for rid in rids):
            result["skipped"] += [{"requirement_id": rid, "reason": "bundle_not_ready"} for rid in rids]
            continue
        # Pass the full analysis for condition/reference validation, but only the
        # eligible selection. No completed or skipped requirement is re-executed.
        relevant = {"version": VERSION, "items": [r for r in envelope["items"]
                    if group & set(r["requirement_ids"])]}
        payload = {"query": out["question"], "question": out["question"], "request_id": out["request_id"],
                   "question_version": out["question_version"], "as_of": out.get("as_of"),
                   "analysis": analysis, "selections": [selections[rid] for rid in rids],
                   "selection_route": {"kind": "proceed"}, "a_assumptions": relevant,
                   "requirement_keys": [{"reading_id": reading, "requirement_id": rid} for rid in rids]}
        cache_key = digest([payload, getattr(port, "contract_version", None)])
        if cache_key not in cache:
            try:
                got = port(copy.deepcopy(payload))
                _validate_result(got, relevant, rids)
                cache[cache_key] = {"status": "returned", "result": copy.deepcopy(got)}
            except Exception as exc:  # noqa: BLE001 - boundary; never log transport/secret-bearing text
                cache[cache_key] = {"status": "connection_error", "error_type": type(exc).__name__}
        entry = {"requirement_keys": payload["requirement_keys"], **copy.deepcopy(cache[cache_key])}
        result["requirements"].append(entry)
        if entry["status"] != "returned":
            result["skipped"] += [{"requirement_id": rid, "reason": "connection_error"} for rid in rids]
            rejected.append((payload, cache_key))
        for scope in entry.get("result", {}).get("scopes", []):
            for row in scope.get("shared_assumptions") or []:
                if row.get("supersedes"):
                    result["superseded"].append({"a_assumption_id": row["supersedes"],
                                                  "b_assumption_id": row["assumption_id"]})
    result["status"] = "returned" if result["requirements"] and not result["skipped"] and all(
        r["status"] == "returned" for r in result["requirements"]) else "partial"
    complete = result["status"] == "returned" and all(
        all(s["status"] == "ok" for s in r["result"]["scopes"]) and
        (r["result"].get("fetch") or {}).get("status") == "ok" for r in result["requirements"])
    returned = any(r["status"] == "returned" for r in result["requirements"])
    response["execution_status"] = "completed" if complete else "partial" if returned else "not_executed"
    # A relation may become non-executable after routing when an operand is
    # missing. Do not leave a table-progress claim for its remaining operands.
    # Their unresolved interpretation assumptions and any actual B results stay.
    excluded = {r["requirement_id"] for r in result["skipped"]}
    public = {**envelope, "items": [r for r in envelope["items"] if not (
        r["reason_code"] in ("first_assignment", "user_selection") and excluded & set(r["requirement_ids"]))]}
    response["assumptions"] = public
    result["a_assumptions"] = public
    # Publishing a rejection removes progress claims, not the identity of the
    # failed execution. Alias only that published payload to its existing error;
    # changed analysis, selections, version or port still produces a new key.
    for payload, key in rejected:
        published = {**payload, "a_assumptions": {**public, "items": [r for r in public["items"]
                     if set(r["requirement_ids"]) & {k["requirement_id"] for k in payload["requirement_keys"]}]}}
        cache.setdefault(digest([published, getattr(port, "contract_version", None)]), copy.deepcopy(cache[key]))
    # B success never changes analysis.certain, A route, failed sources or values.
    out["a_outcome"] = with_session(out, data)
    return out


def _validate_result(result, assumptions, rids):
    if not isinstance(result, dict) or result.get("a_assumptions") != assumptions or not isinstance(result.get("scopes"), list):
        raise ValueError("B result contract")
    if len(result["scopes"]) != len(rids) or {s.get("scope_id") for s in result["scopes"] if isinstance(s, dict)} != set(rids):
        raise ValueError("B requirement mismatch")
    known = {r["assumption_id"]: r for r in assumptions["items"]}
    for scope in result["scopes"]:
        if not isinstance(scope, dict) or not isinstance(scope.get("scope_id"), str) or not isinstance(scope.get("status"), str):
            raise TypeError("B scope contract")
        rid = scope["scope_id"]
        for row in scope.get("shared_assumptions") or []:
            if row.get("stage") != "B":
                raise ValueError("B assumption stage")
            target = row.get("supersedes")
            if target is not None and (not isinstance(target, str) or target not in known or
                    rid not in known[target]["requirement_ids"] or rid not in row.get("requirement_ids", [])):
                raise ValueError("B supersedes reference")
            if target is not None:
                a_conditions = set(known[target]["refs"]["condition_ids"])
                b_conditions = set((row.get("refs") or {}).get("condition_ids") or [])
                if not a_conditions or a_conditions != b_conditions:
                    raise ValueError("B supersedes condition mismatch")
