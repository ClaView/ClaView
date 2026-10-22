"""A2 official evidence and optional table-set decision; persistence owns confirmation."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .a2_selection import failure_decision


class A2State(TypedDict, total=False):
    question: dict
    candidate_ids: list[str]
    assessment: dict
    selection_decision: dict


def build_a2_graph(evidence, policy=None):
    graph = StateGraph(A2State)
    graph.add_node(
        "compare_official_evidence",
        lambda state: {"assessment": evidence.compare(state["question"], state["candidate_ids"])},
    )
    graph.add_edge(START, "compare_official_evidence")
    if policy:

        def decide(state):
            try:
                decision = policy.decide(state["question"], state["assessment"])
            except Exception:
                # Never persist raw exception text, which may contain private data.
                decision = failure_decision()
            return {"selection_decision": decision}

        graph.add_node("propose_and_verify_table_set", decide)
        graph.add_edge("compare_official_evidence", "propose_and_verify_table_set")
        graph.add_edge("propose_and_verify_table_set", END)
    else:
        graph.add_edge("compare_official_evidence", END)
    return graph.compile(name="A2")
