"""A graph entry points, kept separate from B selection and parent routing."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .a_discovery import TableDiscovery, page_view
from .a_stages import ASteps, a_search_scope
from .contracts import AWAITING_USER, Json, StageFailure, outcome
from .services import Services


class AState(TypedDict, total=False):
    query: str
    query_plan: Json
    global_candidates: list[Json]
    scoped_candidates: list[Json]
    merged_candidates: list[Json]
    selection_candidates: list[Json]
    condition_assessments: Json
    retrieval_diagnostics: Json
    route_frontier: list[str]
    finished_scopes: list[str]
    retained_direct_doc_ids: list[str]
    selected_node_ids: list[str]
    route_round: int
    route_failed: bool
    model_output: Json
    model_schema: Json
    stage_outcome: Json


def build_a_graph(services: Services):
    steps = ASteps(services)
    g = StateGraph(AState)
    for name in ("prepare", "search", "route", "advance", "scoped", "review", "select", "validate"):
        g.add_node(name, getattr(steps, name))
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "search")

    def after_search(state):
        if state.get("stage_outcome"):
            return END
        return (
            "route"
            if a_search_scope(services.config) != "global" and services.config.max_route_rounds
            else "review"
        )

    g.add_conditional_edges("search", after_search, ["route", "review", END])
    g.add_conditional_edges(
        "route", lambda s: "scoped" if s.get("route_failed") else "advance", ["scoped", "advance"]
    )
    g.add_conditional_edges(
        "advance", lambda s: "route" if s["route_frontier"] else "scoped", ["route", "scoped"]
    )
    g.add_conditional_edges(
        "scoped", lambda s: END if s.get("stage_outcome") else "review", [END, "review"]
    )
    g.add_conditional_edges(
        "review", lambda s: END if s.get("stage_outcome") else "select", [END, "select"]
    )
    g.add_conditional_edges(
        "select", lambda s: END if s.get("stage_outcome") else "validate", [END, "validate"]
    )
    g.add_edge("validate", END)
    return g.compile(name="A")


class DiscoveryState(TypedDict, total=False):
    clarification_answer: str | None
    query: str
    context: Json
    candidate_set_id: str
    selection: list
    rows: list[Json]
    candidates: list[Json]
    diagnostics: Json
    stage_outcome: Json


def build_discovery_graph(discovery: TableDiscovery, checkpointer=None):
    """Checkpoints hold a cursor; SQLite owns all candidate lifecycle transitions."""

    def guarded(fn):
        def node(state):
            try:
                return fn(state)
            except StageFailure as exc:
                return {"stage_outcome": outcome("A", failure=exc)}

        return node

    @guarded
    def retrieve(state):
        result = discovery.search_tables(state["query"], state["context"])
        if result["status"] == "ok":
            return {"candidate_set_id": result["candidate_set_id"], "stage_outcome": result}
        if result["status"] != AWAITING_USER:
            return {
                "candidate_set_id": result["interaction"]["candidate_set_id"],
                "stage_outcome": result,
            }
        return {"candidate_set_id": result["interaction"]["candidate_set_id"]}

    @guarded
    def clarify(state):
        result = discovery.clarify_tables(
            state["candidate_set_id"], state["clarification_answer"], state["context"]
        )
        if result["status"] == "ok":
            return {
                "candidate_set_id": result["candidate_set_id"],
                "context": result["context"],
                "stage_outcome": result,
                "clarification_answer": None,
                "selection": None,
            }
        return {
            "candidate_set_id": result["interaction"]["candidate_set_id"],
            "context": result["interaction"]["context"],
            "clarification_answer": None,
            "selection": None,
            "stage_outcome": None if result["status"] == AWAITING_USER else result,
        }

    @guarded
    def await_selection(state):
        bundle = discovery.repository.get(
            state["candidate_set_id"], state["context"], discovery.snapshots, active=True
        )
        if state.get("query") and state["query"] != bundle["original_query"]:
            raise StageFailure("query_mismatch")
        page = page_view(bundle)
        waiting = outcome("A", interaction=page, trace_ref=bundle["trace"]["run_id"])
        if state.get("selection") is not None or state.get("clarification_answer") is not None:
            return {"query": bundle["original_query"], "stage_outcome": waiting}
        if bundle["status"] == "confirmed":
            return {"query": bundle["original_query"], "stage_outcome": bundle["a_outcome"]}
        if discovery.selection_mode == "auto":
            resumed = discovery.decision_outcome(bundle)
            if resumed["status"] != AWAITING_USER:
                return {"query": bundle["original_query"], "stage_outcome": resumed}
        if checkpointer is not None:
            chosen = interrupt(waiting)
            if page.get("kind") == "clarification":
                return {
                    "clarification_answer": chosen,
                    "query": bundle["original_query"],
                    "stage_outcome": waiting,
                }
            return {
                "selection": chosen,
                "query": bundle["original_query"],
                "stage_outcome": waiting,
            }
        return {"query": bundle["original_query"], "stage_outcome": waiting}

    @guarded
    def finalize_selection(state):
        return {
            "stage_outcome": discovery.confirm_tables(
                state["candidate_set_id"], state["selection"], state["context"]
            )
        }

    g = StateGraph(DiscoveryState)
    for name, fn in (
        ("retrieve", retrieve),
        ("clarify", clarify),
        ("await_selection", await_selection),
        ("finalize_selection", finalize_selection),
    ):
        g.add_node(name, fn)
    g.add_conditional_edges(
        START,
        lambda s: (
            "clarify"
            if s.get("clarification_answer") is not None
            else "await_selection"
            if s.get("candidate_set_id")
            else "retrieve"
        ),
        ["await_selection", "retrieve", "clarify"],
    )
    for current, following in (
        ("retrieve", "await_selection"),
        ("clarify", "await_selection"),
    ):
        g.add_conditional_edges(
            current, lambda s, n=following: END if s.get("stage_outcome") else n, [END, following]
        )
    g.add_conditional_edges(
        "await_selection",
        lambda s: (
            "clarify"
            if s.get("stage_outcome", {}).get("status") == AWAITING_USER
            and s.get("clarification_answer") is not None
            else "finalize_selection"
            if s.get("stage_outcome", {}).get("status") == AWAITING_USER
            and s.get("selection") is not None
            else END
        ),
        ["finalize_selection", "clarify", END],
    )
    g.add_edge("finalize_selection", END)
    return g.compile(checkpointer=checkpointer, name="A_user_selection")
