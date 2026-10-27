"""Module A's service boundary: question -> tables (and the pending question when A must ask).

A still decides inside its own session which requirements are ready for B and builds the
payload it would hand to a B port (claview_a.agent.b_connection.connect). This layer does not
change that: it runs the session without a port, then replays A's connect step with a
recording port that only captures those payloads. The pipeline then calls B with them.

A's state is returned whole as the checkpoint; /a/answer takes it back with the user's
option_id (A validates it again).
"""

from __future__ import annotations

import copy
import functools
from typing import Any

from claview_a.agent import session as a_session
from claview_a.agent.assumptions import VERSION as A_ASSUMPTIONS_VERSION
from claview_a.agent.b_connection import connect
from claview_a.catalog.layer import load_layer

TABLE_FIELDS = ("org_id", "table_id", "table_name")


@functools.cache
def layer() -> Any:
    return load_layer(include_external_synonyms=False)


class _RecordingPort:
    """Looks like a B port to A's connect step; records each payload and answers nothing."""

    a_assumptions_version = A_ASSUMPTIONS_VERSION
    contract_version = "a-api-recorder/1"

    def __init__(self):
        self.payloads: list[dict] = []

    def __call__(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        raise RuntimeError("recording only")


def _tables(selections: list[dict]) -> list[dict]:
    """Selected tables in A's order, once each (the pipeline's A -> B request rule)."""
    tables: list[dict] = []
    for selection in selections:
        for entry in selection.get("tables") or []:
            table = {k: entry.get(k) for k in TABLE_FIELDS}
            if all((t["org_id"], t["table_id"]) != (table["org_id"], table["table_id"]) for t in tables):
                tables.append(table)
    return tables


def b_requests(state: dict) -> list[dict]:
    """What A would send to B, one entry per requirement group: requirement IDs and tables."""
    if state["response"]["status"] == "awaiting_answer":
        return []
    recorder = _RecordingPort()
    connect(copy.deepcopy(state), recorder)
    return [
        {
            "requirement_ids": [k["requirement_id"] for k in payload["requirement_keys"]],
            "tables": _tables(payload.get("selections") or []),
        }
        for payload in recorder.payloads
    ]


def summary(state: dict) -> dict:
    response = state["response"]
    question = response.get("question")
    requests = b_requests(state)
    return {
        "decision": response["status"],
        "tables": _tables([{"tables": r["tables"]} for r in requests]),
        "b_requests": requests,
        "pending_question": copy.deepcopy(question) if question else None,
        "response": copy.deepcopy(response),
        "state": state,
    }


def select(query: str, as_of: str | None = None) -> dict:
    return summary(a_session.run_session(query, layer=layer(), as_of=as_of, b_port=None))


def answer(state: dict, option_id: str) -> dict:
    question = state["response"].get("question")
    if not question:
        raise ValueError("no pending question")
    reply = {
        "request_id": state["request_id"],
        "question_version": state["question_version"],
        "question_id": question["question_id"],
        "option_id": option_id,
    }
    return summary(a_session.resume_session(state, reply, layer=layer(), b_port=None))
