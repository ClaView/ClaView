"""One flow over A (claview.a) and B (claview): question -> A session -> B port -> fetch.

This package only wires existing functions together. Table selection, waiting and
resuming are A's (claview.a.agent.session); series/period selection and the
request/result contracts are B's (claview.b). Nothing here calls a model or KOSIS
unless the caller passes clients built with `live_b_clients(allow_external=True)`.
"""

from __future__ import annotations

from .b_port import NOT_RUN_FETCH_REASON, make_b_port
from .live import live_b_clients
from .session import FlowSession, answer, make_answer, new_question, run_question

__all__ = [
    "NOT_RUN_FETCH_REASON",
    "FlowSession",
    "answer",
    "live_b_clients",
    "make_answer",
    "make_b_port",
    "new_question",
    "run_question",
]
