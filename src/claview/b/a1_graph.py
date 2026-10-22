"""A1 subgraph reuses retrieval and emits a source-bound internal A2 handoff."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .a1_query import auxiliary_queries, prepare_question


class A1State(TypedDict, total=False):
    original_query: str
    effective_query: str
    answers: list
    question: dict
    auxiliaries: list
    retrieval_rows: list
    diagnostics: dict
    handoff: dict


def build_a1_graph(retrieve, dictionary, interpreter=None):
    def prepare(state):
        question = prepare_question(
            state["original_query"],
            effective_query=state.get("effective_query"),
            answers=state.get("answers", ()),
        )
        auxiliaries = auxiliary_queries(question)
        if interpreter is not None:
            interpretation = interpreter(question)
            question["interpretation"] = interpretation
            auxiliaries.extend(interpretation["auxiliaries"])
        return {"question": question, "auxiliaries": auxiliaries}

    def search(state):
        question = state["question"]
        resolution = dictionary.resolve(question["original_query"])
        # A global condition must not authorize a mapping in another request clause.
        if len([s for s in question["scopes"] if s["source_id"] == "original"]) > 1:
            resolution["expansions"] = []
            for row in resolution["trace"]:
                if row["action"] == "applied":
                    row.update(action="deferred", reason="compound_mapping_scope_unresolved")
        rows, diagnostics = retrieve(
            question["original_query"],
            [r["expression"] for r in state["auxiliaries"]],
            mapping_resolution=resolution,
        )
        return {"retrieval_rows": rows, "diagnostics": diagnostics}

    def handoff(state):
        rows, diagnostics = state["retrieval_rows"], state["diagnostics"]
        queries = [
            {"expression": state["original_query"], "kind": "original", "source_ids": ["original"]},
            *state["auxiliaries"],
        ]
        for expansion in diagnostics["mapping_resolution"]["expansions"]:
            queries.append({**expansion, "kind": "mapping", "source_ids": ["original"]})
        original_ids = {
            r["doc_id"] for hits in diagnostics["runs"][0]["channels"].values() for r in hits
        }
        auxiliary_ids = {
            r["doc_id"]
            for run in diagnostics["runs"][1:]
            for hits in run["channels"].values()
            for r in hits
        }
        payload = {
            **state["question"],
            "search_queries": queries,
            "retrieval_rows": rows,
            "candidate_sources": {
                row["doc_id"]: {
                    "original": row["doc_id"] in original_ids,
                    "auxiliary": row["doc_id"] in auxiliary_ids,
                }
                for row in rows
            },
            "contribution": {
                "original_count": len(original_ids),
                "auxiliary_hit_count": len(auxiliary_ids),
                "auxiliary_added_ids": sorted(
                    ({r["doc_id"] for r in rows} & auxiliary_ids) - original_ids
                ),
                "other_channel_added_ids": sorted(
                    {r["doc_id"] for r in rows} - original_ids - auxiliary_ids
                ),
            },
            "decision": "pending_a2",
            "model_calls": state["question"]
            .get("interpretation", {})
            .get("runtime", {})
            .get("external_calls", 0),
        }
        return {"handoff": payload, "diagnostics": {**diagnostics, "a1": payload}}

    graph = StateGraph(A1State)
    for name, node in (
        ("prepare_question", prepare),
        ("retrieve_candidates", search),
        ("handoff_to_a2", handoff),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "prepare_question")
    graph.add_edge("prepare_question", "retrieve_candidates")
    graph.add_edge("retrieve_candidates", "handoff_to_a2")
    graph.add_edge("handoff_to_a2", END)
    return graph.compile(name="A1")
