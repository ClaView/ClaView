"""Outer state of the A measurement graph (plan 04, 1-1 and 5-3) and its initial value.

The shared fields (``question``, ``as_of``, ``request_id``, ``requirements``, ``unresolved``, ``selection``,
``a_outcome``, ``control``, ``response``) are a subset of the shared ``AgentState`` of the working folder (plan 02,
``src/claview/agent/state.py``); their names and the key tuples below must stay equal to that file (a test compares
them as text). This copy never defines their meaning anew: it fills them from the interpretation and selection
outputs through ``common.to_common``.

New fields of this copy (plan 04, 1-1 and 5-2):
- ``variant``: wiring of the outer graph (``straight``, ``router``, ``central``, ``parts``; plan 04, 1-2).
- ``analysis``: the whole output of the interpretation sub-graph (``contract.shapes.Analysis``). Conditions and the
  judgement live only here and are never copied elsewhere.
- ``selections``: per-requirement table groups (plan 04, 5-2 ``Selection``) from ``select.graph.select_tables``;
  ``selection_groups``: requirements that share tables (one fetch group each, ``SelectionGroup``).
- ``evidence``: a summary of the selection's evidence document, not the document itself (``EVIDENCE_SUMMARY_KEYS``):
  the full document holds every check of every requirement x candidate pair and its rows, it is read by nobody
  after ``select_tables`` (V2 runs the evidence checks again through ``select.cover.verify_choice``), and copying it
  into every state update of the run would multiply its size by the number of steps. The summary keeps the counts
  and the candidate pool per requirement (for the candidate-inclusion measure).
- ``verification``: ``{interpretation, selection}`` results of V1 and V2 (``verify.checks``).
- ``jev_record``: the jev requirement-count record (``verify.jev``), None when jev is off. The router never reads it.
- ``selection_route``: the question-level way after the selection recheck (plan 07 r4 S5,
  ``question_route.route_of_state``; shape ``contract.shapes.SelectionRoute``), None until V2 ran (or respond fills
  it when the run ended earlier). The router does not read it yet (user decision 10/19).

``control`` keeps the shared keys only. Their use in this graph:
- ``stage``: step that just ended (None after intake, then a ``STEPS`` name).
- ``attempts``: visits per step name; ``failure_counts``: V1/V2 failure kinds seen.
- ``budget``: ``steps`` and ``llm_calls`` (shared) plus the use of every limited action (``reinterpret``,
  ``retry_model``, ``next_alternative``) and of real asks (``ask``); the guard reads the limits from ``guard.LIMITS``.
- ``resume_point``: step whose rules apply again after ``ask_user``; ``pending_question``: an ask waiting for a user
  answer (always None in automatic runs).
- ``changes``: defaults applied in place of an answer; ``path``: nodes run; ``decisions``: router records
  (``type`` "route") and ask records (``type`` "ask").
"""

from __future__ import annotations

from typing import Any, TypedDict

Json = dict[str, Any]

# Shared key tuples: equal to the working folder's src/claview/agent/state.py (checked by a text test).
CONTROL_KEYS = (
    "stage",
    "last_failure",
    "attempts",
    "failure_counts",
    "budget",
    "resume_point",
    "pending_question",
    "changes",
    "path",
    "decisions",
)
SELECTION_KEYS = ("tables", "backups")
REQUIREMENT_KEYS = ("scope_id", "text", "a_status", "gaps", "reason_codes", "source")
UNRESOLVED_KEYS = ("scope_id", "kind", "detail", "from_node")

VARIANTS = ("straight", "router", "central", "parts")

# Steps the router can see as "just ended" (control.stage). verify runs as one node for both stages.
INTERPRET = "interpret"
SELECT = "select"
VERIFY_INTERPRET = "verify_interpret"
VERIFY_SELECT = "verify_select"
ASK_USER = "ask_user"
STEPS = (INTERPRET, SELECT, VERIFY_INTERPRET, VERIFY_SELECT, ASK_USER)

# Budget counters besides the shared steps / llm_calls.
BUDGET_COUNTERS = ("reinterpret", "retry_model", "next_alternative", "ask")
# Keys of the ``evidence`` summary.
EVIDENCE_SUMMARY_KEYS = ("kept", "pairs", "checks", "rows", "pool")


class OuterState(TypedDict, total=False):
    # request
    question: str
    as_of: str | None
    request_id: str | None
    question_version: int
    decomposition_ledger: Json
    decomposition_runs: list[Json]
    variant: str
    # interpretation / selection outputs (single source of their data)
    analysis: Json | None
    selections: list[Json]
    selection_groups: list[Json]
    evidence: Json | None
    verification: Json
    jev_record: Json | None
    selection_route: Json | None  # plan 07 r4 S5
    # shared fields filled by common.to_common
    requirements: list[Json]
    unresolved: list[Json]
    selection: Json
    # per-part status envelope of A (the a_outcome place of plan 02)
    a_outcome: Json | None
    # control (router node; ask_user appends its ask record)
    control: Json
    # response (respond only)
    response: Json | None


def initial_control() -> Json:
    return {
        "stage": None,
        "last_failure": None,
        "attempts": {},
        "failure_counts": {},
        "budget": {"steps": 0, "llm_calls": 0, **{name: 0 for name in BUDGET_COUNTERS}},
        "resume_point": None,
        "pending_question": None,
        "changes": [],
        "path": ["intake"],
        "decisions": [],
    }


def initial_outcome() -> Json:
    """The A envelope: ``parts`` holds one ``{status, reasons}`` per node that ran (intake, interpret, select)."""
    return {"stage": "A", "status": None, "parts": {}}


def make_initial_state(question: Any, variant: str, as_of: str | None = None,
                       request_id: str | None = None) -> Json:
    """Every OuterState field with its empty value (a test checks nothing is missing)."""
    return {
        "question": question,
        "as_of": as_of,
        "request_id": request_id,
        "question_version": 1,
        "decomposition_ledger": {},
        "decomposition_runs": [],
        "variant": variant,
        "analysis": None,
        "selections": [],
        "selection_groups": [],
        "evidence": None,
        "verification": {"interpretation": None, "selection": None},
        "jev_record": None,
        "selection_route": None,
        "requirements": [],
        "unresolved": [],
        "selection": {"tables": [], "backups": []},
        "a_outcome": initial_outcome(),
        "control": initial_control(),
        "response": None,
    }
