"""A's AdapterPort bound to B's table-list entry (plan 25 step 2).

B takes what its own A -> B pipeline gives it: the original question and the tables A
chose, `{"tables": [{org_id, table_id, table_name}]}` (`B_TABLE_FIELDS`, as B's own A -> B input).
The port runs B's graph (`build_b_graph`) on that, then `fetch_data`. A's port shape is kept:
the request and result echo A's assumptions and carry one scope per A requirement; B itself
does not read them. The B selection model and the KOSIS client are injected.
"""

from __future__ import annotations

import copy
from typing import Any

from claview.a.agent.assumptions import VERSION as A_ASSUMPTIONS_VERSION
from claview.a.agent.b_connection import AdapterPort
from claview.b.contracts import Config, StageFailure, Trace, outcome
from claview.b.fetch import DataClient, build_api_request, fetch_data
from claview.b.graphs import build_b_graph
from claview.b.metadata import MetadataStore
from claview.b.models import SelectionModel
from claview.b.services import Services

# The table fields B reads from A (B's own A -> B input, `claview.b.ab_pipeline.B_TABLE_FIELDS`).
B_TABLE_FIELDS = ("org_id", "table_id", "table_name")
# The port's own request/result shape (not a B contract version).
TABLE_LIST_CONTRACT = "flow-table-list/2026-10-23.1"
NOT_RUN_FETCH_REASON = "no_fetch_client"


def to_table_request(payload: dict, *, a_assumptions: dict) -> dict:
    """A's payload -> B's input: the original question and A's tables, in order, once each."""
    tables: list[dict] = []
    for selection in payload.get("selections") or []:
        for entry in selection.get("tables") or []:
            table = {k: entry.get(k) for k in B_TABLE_FIELDS}
            key = (table["org_id"], table["table_id"])
            if all((t["org_id"], t["table_id"]) != key for t in tables):
                tables.append(table)
    return {
        "contract_version": TABLE_LIST_CONTRACT,
        "query": payload.get("question") or payload.get("query"),
        "as_of": payload.get("as_of"),
        "a_result": {"tables": tables},
        "a_assumptions": a_assumptions,
        "scopes": [
            {"scope_id": k["requirement_id"]} for k in payload.get("requirement_keys") or []
        ],
    }


def check_table_request(request: dict) -> list[str]:
    """Problems of a table-list request ([] when B may run)."""
    problems = []
    if not isinstance(request.get("query"), str) or not request["query"].strip():
        problems.append("query_missing")
    tables = (request.get("a_result") or {}).get("tables")
    if not tables:
        problems.append("tables_missing")
    for table in tables or []:
        if set(table) != set(B_TABLE_FIELDS) or not table["org_id"] or not table["table_id"]:
            problems.append("table_fields")
    ids = [s.get("scope_id") for s in request.get("scopes") or []]
    if not ids or len(ids) != len(set(ids)) or not all(isinstance(i, str) and i for i in ids):
        problems.append("scope_ids")
    return problems


def check_scope_result(scope: dict) -> list[str]:
    if not isinstance(scope.get("scope_id"), str) or not isinstance(scope.get("status"), str):
        return ["scope_fields"]
    return []


def check_table_result(result: dict, request: dict) -> list[str]:
    """Problems of a result against its request: same scopes, B's stage envelope present."""
    problems = []
    if [s.get("scope_id") for s in result.get("scopes") or []] != [
        s["scope_id"] for s in request["scopes"]
    ]:
        problems.append("scope_mismatch")
    if (result.get("b_outcome") or {}).get("stage") != "B":
        problems.append("b_outcome_missing")
    if not isinstance(result.get("selections"), list):
        problems.append("selections_missing")
    return problems


def make_b_port(
    store: MetadataStore,
    *,
    model: SelectionModel,
    fetch_client: DataClient | None = None,
    search: Any = None,
    as_of: str | None = None,
) -> AdapterPort:
    """Build the port A calls after its outer graph returns.

    `model` answers B's selection prompts. `fetch_client` reads values; when it is None
    the fetch step does not run and reports a labelled not-run plan, so A's
    `execution_status` cannot become `completed`. `as_of` is used only when a request
    carries none (A always sets one). The port keeps the services of its latest request
    in `port.services["b"]` (one request at a time; not thread-safe).
    """
    services: dict[str, Services] = {}

    def run_b(request: dict, holder: dict) -> dict:
        when = request.get("as_of") or as_of
        config = Config(as_of=when) if when else Config()
        holder["b"] = Services(store, search, model, config, Trace())
        tables = request["a_result"]["tables"]
        try:
            b_outcome = build_b_graph(holder["b"]).invoke(
                {"query": request["query"], "a_result": copy.deepcopy(request["a_result"])},
                {"recursion_limit": 10 + 4 * len(tables)},
            )["stage_outcome"]
        except StageFailure as exc:
            b_outcome = outcome("B", failure=exc, trace_ref=holder["b"].trace.run_id)
        ok = b_outcome["status"] == "ok"
        selections = ((b_outcome["result"] or {}).get("selections") or []) if ok else []
        return {
            "contract_version": TABLE_LIST_CONTRACT,
            "a_assumptions": copy.deepcopy(request["a_assumptions"]),
            "a_result": copy.deepcopy(request["a_result"]),
            "b_outcome": b_outcome,
            # One B run answers the whole request: every A requirement in it shares B's status.
            "scopes": [
                {
                    "scope_id": s["scope_id"],
                    "status": b_outcome["status"],
                    "issues": b_outcome["issues"],
                }
                for s in request["scopes"]
            ],
            "selections": copy.deepcopy(selections),
        }

    def fetch(result: dict, holder: dict) -> dict:
        if fetch_client is None:
            return {
                "status": "not_run",
                "reason": NOT_RUN_FETCH_REASON,
                "requests": [build_api_request(s) for s in result["selections"]],
            }
        return fetch_data(result["b_outcome"]["result"], store, fetch_client, holder["b"].trace)

    return AdapterPort(
        adapter=to_table_request,
        check_request=check_table_request,
        check_scope_result=check_scope_result,
        check_result=check_table_result,
        run_b=run_b,
        fetch=fetch,
        services=services,
        contract_version=TABLE_LIST_CONTRACT,
        a_assumptions_version=A_ASSUMPTIONS_VERSION,
    )
