"""Golden end-to-end evaluation over the new A (plan 26 step 2), offline.

Each golden case runs question -> A session (rules, no model call) -> the B port -> B, whose
selection model REPLAYS the stored golden B answer for each golden table (B's `ScriptedModel`,
keyed by B's task names) -> a SYNTHETIC fetch client (value "1"). A table the new A chooses
outside the golden tables has no stored answer, so B reports `fixture_response_missing` for it.

What this measures: whether the new A's tables match the golden tables, and whether B, given
the new A's tables and the stored answers, reproduces the golden selections end to end. It is a
development baseline: the 22 golden cases were used repeatedly during development, the B answers
are replayed (not a model run) and the values are synthetic.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from claview.b.evaluation import b_success, condition_counts, scripted_answers
from claview.b.models import ScriptedModel

from .session import FlowSession, make_answer
from .synthetic import SyntheticFetchClient

SCOPE = (
    "development baseline: new A (rules) + stored golden B answers replayed + synthetic fetch; "
    "the 22 golden cases are not an independent test set; NOT model or value accuracy"
)
ANSWER_POLICIES = ("none", "first_option")
MAX_ANSWER_TURNS = 5


def load_cases(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))["cases"]


def _tables(state: dict) -> list[list[str]]:
    seen: list[list[str]] = []
    for selection in state.get("selections") or []:
        for t in selection.get("tables") or []:
            key = [t.get("org_id"), t.get("table_id")]
            if key not in seen:
                seen.append(key)
    return seen


def _b_envelope(requirements: list[dict]) -> dict | None:
    """One B envelope over all returned requirements (selections concatenated), for B's metrics."""
    outcomes = [
        (r.get("result") or {}).get("b_outcome")
        for r in requirements
        if r.get("status") == "returned"
    ]
    outcomes = [o for o in outcomes if o]
    if not outcomes:
        return None
    status = next((o["status"] for o in outcomes if o["status"] != "ok"), "ok")
    selections = [s for o in outcomes for s in ((o.get("result") or {}).get("selections") or [])]
    resolved = next(
        ((o.get("result") or {}).get("resolved_period") for o in outcomes if o.get("result")), None
    )
    return {"status": status, "result": {"selections": selections, "resolved_period": resolved}}


def evaluate_case(base: FlowSession, case: dict, *, as_of: str | None, answer_policy: str) -> dict:
    expected = case["expected"]
    model = ScriptedModel(scripted_answers(case))
    client = SyntheticFetchClient()
    session = FlowSession.load(
        b_model=model, fetch_client=client, layer=base.layer, store=base.store
    )
    state = session.run_question(case["query"], as_of=as_of)
    first_status = state["response"]["status"]
    turns = 0
    while (
        answer_policy == "first_option"
        and state["response"]["status"] == "awaiting_answer"
        and turns < MAX_ANSWER_TURNS
    ):
        state = session.answer(state, make_answer(state))
        turns += 1
    response = state["response"]
    b = response.get("b") or {}
    requirements = b.get("requirements") or []
    chosen = _tables(state)
    golden = [[t["org_id"], t["table_id"]] for t in expected["A"]["tables"]]
    envelope = _b_envelope(requirements)
    fetches = [((r.get("result") or {}).get("fetch") or {}).get("status") for r in requirements]
    return {
        "case_id": case["case_id"],
        "a_first_status": first_status,
        "a_status": response["status"],
        "answer_turns": turns,
        "execution_status": response.get("execution_status"),
        "b_reason": b.get("reason"),
        "a_tables": chosen,
        "golden_tables": golden,
        "table_exact": bool(chosen) and sorted(chosen) == sorted(golden),
        "table_recall": len({tuple(t) for t in chosen} & {tuple(t) for t in golden}) / len(golden),
        "b_status": envelope["status"] if envelope else "not_run",
        "b_success": bool(envelope) and b_success(envelope, expected["B"]),
        "b_exact": bool(envelope)
        and envelope["status"] == "ok"
        and condition_counts(envelope["result"]) == condition_counts(expected["B"]),
        "fetch_statuses": fetches,
        "fetch_calls": len(client.calls),
    }


def evaluate(
    cases: list[dict],
    *,
    as_of: str | None = None,
    answer_policy: str = "first_option",
    base: FlowSession | None = None,
) -> dict[str, Any]:
    if answer_policy not in ANSWER_POLICIES:
        raise ValueError(answer_policy)
    base = base or FlowSession.load()
    rows = [evaluate_case(base, c, as_of=as_of, answer_policy=answer_policy) for c in cases]
    count = Counter()
    for r in rows:
        count["table_exact"] += r["table_exact"]
        count["b_success"] += r["b_success"]
        count["b_exact"] += r["b_exact"]
        count["completed"] += r["execution_status"] == "completed"
        count["asked"] += r["a_first_status"] == "awaiting_answer"
    summary = {
        "cases": len(rows),
        "table_exact_count": count["table_exact"],
        "table_recall_mean": sum(r["table_recall"] for r in rows) / len(rows) if rows else 0.0,
        "b_success_count": count["b_success"],
        "b_exact_count": count["b_exact"],
        "completed_count": count["completed"],
        "asked_count": count["asked"],
        "a_status": dict(Counter(r["a_status"] for r in rows)),
        "execution_status": dict(Counter(r["execution_status"] for r in rows)),
        "b_status": dict(Counter(r["b_status"] for r in rows)),
    }
    return {
        "scope": SCOPE,
        "as_of": as_of,
        "answer_policy": answer_policy,
        "metadata_snapshot": base.store.snapshot,
        "summary": summary,
        "cases": rows,
    }
