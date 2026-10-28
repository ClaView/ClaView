"""One flow over A (claview_a_legacy) and B (claview_b): question -> A session -> B port -> fetch.

This package only wires existing functions together. Table selection, waiting and
resuming are A's (claview_a_legacy.agent.session); series/period selection is B's graph,
which receives the original question and A's tables (`b_port.to_table_request`).
Nothing here calls a model or KOSIS unless the caller passes clients built with
`live_b_clients(allow_external=True)`.

The names below are loaded on first use, so importing the package (for example the
command line, which blocks the network first) loads no A or B module by itself.
"""

from __future__ import annotations

import importlib

_EXPORTS = {
    "NOT_RUN_FETCH_REASON": "b_port",
    "TABLE_LIST_CONTRACT": "b_port",
    "make_b_port": "b_port",
    "to_table_request": "b_port",
    "live_b_clients": "live",
    "FlowSession": "session",
    "answer": "session",
    "make_answer": "session",
    "new_question": "session",
    "run_question": "session",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    if name in _EXPORTS:
        return getattr(importlib.import_module(f".{_EXPORTS[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
