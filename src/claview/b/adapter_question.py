"""Question-level table selection -> B request (user decision 8, 2026-10-20).

A producer that decides tables for the whole question, not per requirement (the current A v1
`a_result = {"tables": [...]}`, or a later question-level table selection), becomes one scope that
holds every table. B then runs the same per-scope path as for the requirement-level producer.
"""

from __future__ import annotations

import copy

from .contract import B_CONTRACT_VERSION, BRequest, Json

QUESTION_SCOPE_ID = "q1"


def to_b_request_question_level(
    question: str,
    a_result: Json,
    *,
    as_of: str | None = None,
    decision: str = "auto",
    hints: Json | None = None,
) -> BRequest:
    """One scope `q1` with the tables of `a_result` in order, each table once.

    `hints` are optional scope hints (contract.ScopeHints) when the producer has any; none means
    B reads the question as today. A result without tables gives no scope and one skipped entry.
    """
    tables: list[Json] = []
    seen = set()
    for entry in a_result.get("tables") or []:
        key = (entry.get("org_id"), entry.get("table_id"))
        if key in seen:
            continue
        seen.add(key)
        tables.append(
            {
                "org_id": entry.get("org_id") or "",
                "table_id": entry.get("table_id") or "",
                "table_name": entry.get("table_name"),
            }
        )
    scopes = []
    skipped = []
    if tables:
        scopes.append(
            {
                "scope_id": QUESTION_SCOPE_ID,
                "tables": tables,
                "decision": decision,
                "hints": copy.deepcopy(hints or {}),
            }
        )
    else:
        skipped.append({"scope_id": QUESTION_SCOPE_ID, "reason": "no_table"})
    return {
        "question": question,
        "as_of": as_of,
        "scopes": scopes,
        "skipped": skipped,
        "route": None,
        "relations": [],
        "table_group_hint": [],
        "source": "question_level",
        "contract_version": B_CONTRACT_VERSION,
    }
