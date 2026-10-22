"""A v1 (claview.aflow, N5) -> B v6 connection over the existing A.tables schema.

B receives only what its current contract defines: the original query and
`a_result = {"tables": [{"org_id", "table_id", "table_name"}, ...]}` in A's order.
A's own fields (requirements, conditions, matched items, confidence, scopes) stay in
`a_v1` for diagnostics and are not passed to B. Richer A-to-B fields are follow-up work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .aflow import ACatalog, ARunConfig, run_a
from .aflow.catalog import build_catalog
from .contracts import Json, StageFailure, outcome
from .fetch import DataClient, fetch_data
from .graphs import build_b_graph
from .services import Services

B_TABLE_FIELDS = ("org_id", "table_id", "table_name")


class ABState(TypedDict, total=False):
    query: str
    as_of: str
    a_v1: Json
    a_result: Json
    b_result: Json
    data_result: Json
    chart_result: Json
    active_stage: str
    completed_stage: str
    stage_outcomes: Json
    response: Json


def load_a_catalog(root: Path) -> ACatalog:
    """A catalog built from the same stored files B reads (data/catalog, data/raw)."""
    return build_catalog(
        root / "data" / "catalog" / "search_catalog.json",
        root / "data" / "raw" / "boK_meta_raw.jsonl",
    )


def to_b_a_result(b_handoff: Json) -> Json:
    """Keep only B's existing A.tables schema, in A's order."""
    return {"tables": [{k: t[k] for k in B_TABLE_FIELDS} for t in b_handoff["tables"]]}


def run_a_stage(query: str, catalog: ACatalog, config: ARunConfig, client: Any = None) -> Json:
    """Run A v1 and wrap it as a B-style stage outcome; empty selection stops before B."""
    a_result, b_handoff, report = run_a(query, config, catalog, client=client)
    a_v1 = {
        "config": config.snapshot(),
        "a_result": a_result,
        "b_handoff": b_handoff,
        "issues": report["issues"],
    }
    if not b_handoff["tables"]:
        code = (a_result.get("issue_codes") or ["a_no_tables"])[-1]
        return {"a_v1": a_v1, "outcome": outcome("A", failure=StageFailure(code, "unresolved"))}
    return {"a_v1": a_v1, "outcome": outcome("A", result=to_b_a_result(b_handoff))}


def build_ab_pipeline(
    services: Services,
    a_catalog: ACatalog,
    *,
    a_config: ARunConfig | None = None,
    a_client: Any = None,
    data_client: DataClient | None = None,
    renderer: Any = None,
    through: str = "B",
):
    """A v1 -> B -> fetch -> C. Stops at `through` or at the first stage that is not ok.

    Default A config: model "on" only when a client is given, as_of shared with B's config.
    """
    if through not in {"A", "B", "fetch", "C"}:
        raise ValueError("Invalid last stage")
    config = a_config or ARunConfig(
        model="on" if a_client is not None else "off", as_of=services.config.as_of
    )
    b_graph = build_b_graph(services)

    def save(state, stage, result, key):
        update = {
            "active_stage": stage,
            "stage_outcomes": {**state.get("stage_outcomes", {}), stage: result},
        }
        if result["result"] is not None:
            update[key] = result["result"]
        if result["status"] == "ok":
            update["completed_stage"] = stage
        services.trace.add("stage_result", stage=stage, outcome=result)
        return update

    def a_node(state):
        ran = run_a_stage(state["query"], a_catalog, config, a_client)
        return {**save(state, "A", ran["outcome"], "a_result"), "a_v1": ran["a_v1"]}

    def b_node(state):
        result = b_graph.invoke(
            {"query": state["query"], "a_result": state["a_result"]},
            {"recursion_limit": 10 + 4 * len(state["a_result"]["tables"])},
        )["stage_outcome"]
        return save(state, "B", result, "b_result")

    def fetch_node(state):
        result = (
            fetch_data(state["b_result"], services.store, data_client, services.trace)
            if data_client
            else outcome("fetch", failure=StageFailure("data_client_missing", "unresolved"))
        )
        return save(state, "fetch", result, "data_result")

    def c_node(state):
        if renderer is None:
            result = outcome("C", failure=StageFailure("renderer_not_implemented", "unsupported"))
        else:
            result = renderer(state["query"], state["data_result"])
        return save(state, "C", result, "chart_result")

    def respond(state):
        stage = state["active_stage"]
        last = state["stage_outcomes"][stage]
        notices = (state.get("b_result") or {}).get("period_notices")
        return {
            "response": {
                "status": last["status"],
                "completed_stage": state.get("completed_stage"),
                "stopped_stage": stage,
                "issues": last["issues"],
                "trace_ref": services.trace.run_id,
                **(
                    {"confirmation": last["result"]}
                    if last["status"] == "needs_confirmation"
                    else {}
                ),
                **({"notices": notices} if notices else {}),
            }
        }

    graph = StateGraph(ABState)
    for name, fn in [
        ("A", a_node),
        ("B", b_node),
        ("fetch", fetch_node),
        ("C", c_node),
        ("respond", respond),
    ]:
        graph.add_node(name, fn)
    graph.add_edge(START, "A")
    for stage, nxt in [("A", "B"), ("B", "fetch"), ("fetch", "C"), ("C", "respond")]:

        def route(state, current=stage, next_stage=nxt):
            return (
                next_stage
                if state["stage_outcomes"][current]["status"] == "ok" and through != current
                else "respond"
            )

        graph.add_conditional_edges(stage, route, list(dict.fromkeys([nxt, "respond"])))
    graph.add_edge("respond", END)
    return graph.compile(name="ClaView_AB")


def run_ab(query: str, services: Services, a_catalog: ACatalog, **kwargs) -> Json:
    """One question through A v1 and B (and fetch/C when given). Returns the final state."""
    return build_ab_pipeline(services, a_catalog, **kwargs).invoke(
        {"query": query, "as_of": services.config.as_of, "stage_outcomes": {}}
    )
