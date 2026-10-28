"""Outer graph of the A measurement (plan 04, 1-1 and 1-2): the same nodes in four wirings.

START -> intake -> router; router -> Command(goto = interpret | select | verify | ask_user | respond); every part
node -> router (straight and parts: through the part-end check node); respond -> END.

| variant | part ends | central verify | ask_user |
|---|---|---|---|
| straight (2) | interpret -> check_interpretation, select -> check_selection, then router (record only) | no | no |
| router (3) | interpret, select -> router | yes | yes |
| central (4-a) | interpret, select -> router | yes | no |
| parts (4-b) | as straight, but each check -> part_retry (the part's own retry, else router) | no | no |

Only the wiring and the router's rule table differ; the node functions are the objects of ``nodes.NODES`` in every
variant (a test checks it).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes import NODES, RESPOND
from .state import VARIANTS, OuterState

# Longest path: guard.LIMITS["max_steps"] dispatches x (router + node + check + part_retry) + intake + router +
# respond.
RECURSION_LIMIT = 100

_PART_END_EDGES = (("interpret", "check_interpretation"), ("check_interpretation", "router"),
                   ("select", "check_selection"), ("check_selection", "router"))
_PART_RETRY_EDGES = (("interpret", "check_interpretation"), ("check_interpretation", "part_retry"),
                     ("select", "check_selection"), ("check_selection", "part_retry"))
_CENTRAL_EDGES = (("interpret", "router"), ("select", "router"), ("verify", "router"))

WIRING: dict[str, dict[str, tuple]] = {
    "straight": {"nodes": ("interpret", "check_interpretation", "select", "check_selection"),
                 "edges": _PART_END_EDGES},
    "parts": {"nodes": ("interpret", "check_interpretation", "select", "check_selection", "part_retry"),
              "edges": _PART_RETRY_EDGES},
    "central": {"nodes": ("interpret", "select", "verify"), "edges": _CENTRAL_EDGES},
    "router": {"nodes": ("interpret", "select", "verify", "ask_user"),
               "edges": (*_CENTRAL_EDGES, ("ask_user", "router"))},
}

# Nodes the router may send to, per variant (the guard keeps the off actions from reaching the others).
_DISPATCH = ("interpret", "select", "verify", "ask_user")


def node_names(variant: str) -> tuple[str, ...]:
    return ("intake", "router", *WIRING[variant]["nodes"], RESPOND)


def build_outer_graph(variant: str):
    """Compiled graph of one variant, bound to its variant name and recursion limit."""
    if variant not in VARIANTS:
        raise ValueError("unknown outer-graph variant")
    wiring = WIRING[variant]
    graph = StateGraph(OuterState)
    destinations = tuple(n for n in _DISPATCH if n in wiring["nodes"]) + (RESPOND,)
    for name in node_names(variant):
        if name == "router":
            graph.add_node(name, NODES[name], destinations=destinations)
        elif name == "part_retry":
            graph.add_node(name, NODES[name], destinations=("interpret", "select", "router"))
        else:
            graph.add_node(name, NODES[name])
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "router")
    for start, end in wiring["edges"]:
        graph.add_edge(start, end)
    graph.add_edge(RESPOND, END)
    compiled = graph.compile(name=f"claview_v3_outer_{variant}")
    return compiled.with_config(configurable={"variant": variant}, recursion_limit=RECURSION_LIMIT)


def run_outer(question: str, variant: str, *, layer: Any = None, jev: Any = None, as_of: str | None = None,
              request_id: str | None = None, question_version: int = 1, decomposition=None,
              decomposition_ledger=None, candidate_policy: str = "legacy", selection_mode: str = "legacy") -> dict:
    """One question through the outer graph of ``variant``. Returns the final state."""
    if candidate_policy not in ("legacy", "union"):
        raise ValueError("unknown candidate policy")
    if selection_mode not in ("legacy", "refutation"):
        raise ValueError("unknown selection mode")
    configurable = {"layer": layer, "jev": jev, "decomposition": decomposition,
                    "decomposition_ledger": decomposition_ledger, "candidate_policy": candidate_policy,
                    "selection_mode": selection_mode}
    return build_outer_graph(variant).invoke({"question": question, "as_of": as_of, "request_id": request_id,
                                             "question_version": question_version},
                                             {"configurable": configurable})
