"""B subgraph: load -> context -> select -> validate for the tables A chose.

The A -> B -> fetch flow is `claview_pipeline` (the old A graphs and pipeline adapters were removed
in plan 26).
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .contracts import Json
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
    period_conditions: Json
    period_plans: Json
    model_schema: Json
    expand_enabled: bool
    model_output: Json
    retry_feedback: Json
    retries: int
    expand_notices: Json
    frequency_alternatives: Json
    selections_by_table: Json
    stage_outcome: Json


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
