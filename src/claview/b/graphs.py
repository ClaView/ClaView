"""B subgraph and shared pipeline adapters; A graph aliases stay compatible."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .a_discovery import TableDiscovery, run_b
from .a_graphs import AState as AState
from .a_graphs import DiscoveryState as DiscoveryState
from .a_graphs import build_a_graph as build_a_graph
from .a_graphs import build_discovery_graph as build_discovery_graph
from .candidate_store import CandidateStore, validate_context
from .contracts import AWAITING_USER, Json, StageFailure, Trace, outcome
from .fetch import DataClient, fetch_data
from .services import Services
from .stages import BSteps


class BState(TypedDict, total=False):
    query: str
    a_result: Json
    table_keys: list[str]
    table_index: int
    table_metadata: Json
    active_table: str
    selection_context: Json
    resolved_period: Json
    period_notices: Json
    model_schema: Json
    expand_enabled: bool
    model_output: Json
    retry_feedback: Json
    retries: int
    expand_notices: Json
    frequency_alternatives: Json
    selections_by_table: Json
    stage_outcome: Json


class PipelineState(TypedDict, total=False):
    clarification_answer: str | None
    a_context: Json
    query: str
    request_id: str
    as_of: str
    policy_version: str
    metadata_snapshot: str
    a_result: Json
    b_result: Json
    data_result: Json
    chart_result: Json
    active_stage: str
    completed_stage: str
    stage_outcomes: Json
    response: Json
    session_id: str
    query_revision: int
    candidate_set_id: str
    selection: list
    interaction: Json
    retry_b: bool
    b_execution: Json


def _notices(state) -> Json:
    """조회 가능 기간 안내(2026-10-06 flow 변경): B가 저장한 period_notices를 응답에 노출함.
    안내만 하며 진행 여부를 묻지 않음(조회 단계는 그대로 진행됨)."""
    notices = (state.get("b_result") or {}).get("period_notices")
    return {"notices": notices} if notices else {}


def build_b_graph(services: Services):
    steps = BSteps(services)
    g = StateGraph(BState)
    for name in ("load", "context", "select", "validate"):
        g.add_node(name, getattr(steps, name))
    g.add_edge(START, "load")
    for current, next_node in [
        ("load", "context"),
        ("context", "select"),
        ("select", "validate"),
    ]:
        g.add_conditional_edges(
            current,
            lambda s, n=next_node: END if s.get("stage_outcome") else n,
            [END, next_node],
        )
    g.add_conditional_edges(
        "validate",
        lambda s: (
            END if s.get("stage_outcome") else "select" if s.get("retry_feedback") else "context"
        ),
        [END, "select", "context"],
    )
    return g.compile(name="B")


def build_legacy_pipeline(
    services: Services,
    data_client: DataClient | None = None,
    renderer: Any = None,
    through: str = "B",
):
    if through not in {"A", "B", "fetch", "C"}:
        raise ValueError("Invalid last stage")
    a_graph, b_graph = build_a_graph(services), build_b_graph(services)

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
        result = a_graph.invoke(
            {"query": state["query"]},
            {"recursion_limit": 20 + services.config.max_route_rounds * 3},
        )["stage_outcome"]
        return save(state, "A", result, "a_result")

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
                **_notices(state),
            }
        }

    graph = StateGraph(PipelineState)
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
    return graph.compile(name="ClaView")


def invoke_pipeline(query: str, services: Services, **kwargs) -> Json:
    if kwargs.get("selection_mode", "user") != "legacy":
        run_id = Trace().run_id
        context = kwargs.pop("context", None) or {
            "session_id": run_id,
            "request_id": run_id,
            "query_revision": 1,
        }
        initial = {"query": query, **validate_context(context), "stage_outcomes": {}}
        for field in ("candidate_set_id", "selection", "retry_b", "clarification_answer"):
            if field in kwargs:
                initial[field] = kwargs.pop(field)
        return build_pipeline(services, **kwargs).invoke(initial)
    services.trace.add(
        "run_config",
        config=vars(services.config),
        metadata_snapshot=services.store.snapshot,
        catalog_snapshot=services.store.catalog_snapshot,
    )
    initial = {
        "query": query,
        "request_id": services.trace.run_id,
        "as_of": services.config.as_of,
        "policy_version": services.config.period_policy,
        "metadata_snapshot": services.store.snapshot,
        "stage_outcomes": {},
    }
    return build_pipeline(services, **kwargs).invoke(initial)


def build_pipeline(
    services,
    data_client=None,
    renderer=None,
    through="B",
    *,
    selection_mode="user",
    repository=None,
    agent=None,
    a_selection_mode="shadow",
):
    if selection_mode == "legacy":
        return build_legacy_pipeline(services, data_client, renderer, through)
    if selection_mode != "user" or through not in {"A", "B", "fetch", "C"}:
        raise ValueError("Invalid pipeline mode or last stage")
    discovery = TableDiscovery(
        services.search,
        repository or CandidateStore(Path("runs/candidates.sqlite3")),
        services.config,
        agent=agent,
        selection_mode=a_selection_mode,
    )
    a_graph = build_discovery_graph(discovery)

    def a_node(state):
        context = {k: state[k] for k in ("session_id", "request_id", "query_revision")}
        initial = {"query": state["query"], "context": context}
        for key in ("candidate_set_id", "selection", "clarification_answer"):
            if key in state:
                initial[key] = state[key]
        child = a_graph.invoke(initial)
        result = child["stage_outcome"]
        update = {"active_stage": "A", "stage_outcomes": {"A": result}}
        if child.get("context"):
            update["query_revision"] = child["context"]["query_revision"]
        if child.get("candidate_set_id"):
            update["candidate_set_id"] = child["candidate_set_id"]
        if result["status"] == "ok":
            update.update(a_result=result["result"], completed_stage="A")
        elif result["status"] == AWAITING_USER:
            update["interaction"] = result["interaction"]
        return update

    def b_node(state):
        context = {k: state[k] for k in ("session_id", "request_id", "query_revision")}
        result = run_b(
            discovery,
            state["candidate_set_id"],
            context,
            services,
            retry=state.get("retry_b", False),
        )
        return {
            **result,
            "active_stage": "B",
            "completed_stage": result["response"]["completed_stage"],
        }

    def fetch_node(state):
        result = (
            fetch_data(state["b_result"], services.store, data_client, Trace())
            if data_client
            else outcome("fetch", failure=StageFailure("data_client_missing", "unresolved"))
        )
        update = {
            "active_stage": "fetch",
            "stage_outcomes": {**state["stage_outcomes"], "fetch": result},
        }
        if result["status"] == "ok":
            update.update(data_result=result["result"], completed_stage="fetch")
        return update

    def c_node(state):
        result = (
            renderer(state["query"], state["data_result"])
            if renderer
            else outcome("C", failure=StageFailure("renderer_not_implemented", "unsupported"))
        )
        update = {"active_stage": "C", "stage_outcomes": {**state["stage_outcomes"], "C": result}}
        if result["status"] == "ok":
            update.update(chart_result=result["result"], completed_stage="C")
        return update

    def respond(state):
        result = state["stage_outcomes"][state["active_stage"]]
        return {
            "response": {
                "status": result["status"],
                "stopped_stage": state["active_stage"],
                "completed_stage": state.get("completed_stage"),
                "issues": result["issues"],
                "trace_ref": result["trace_ref"],
                **({"interaction": result["interaction"]} if "interaction" in result else {}),
                **(
                    {"confirmation": result["result"]}
                    if result["status"] == "needs_confirmation"
                    else {}
                ),
                **_notices(state),
            }
        }

    g = StateGraph(PipelineState)
    for name, fn in (
        ("A", a_node),
        ("B", b_node),
        ("fetch", fetch_node),
        ("C", c_node),
        ("respond", respond),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "A")
    for stage, nxt in (("A", "B"), ("B", "fetch"), ("fetch", "C"), ("C", "respond")):
        g.add_conditional_edges(
            stage,
            lambda s, current=stage, following=nxt: (
                following
                if s["stage_outcomes"][current]["status"] == "ok" and through != current
                else "respond"
            ),
            list(dict.fromkeys([nxt, "respond"])),
        )
    g.add_edge("respond", END)
    return g.compile(name="ClaView_user_selection")
