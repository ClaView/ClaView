"""The official entry of the new A: one question through the table-level path.

``run(question, judge, as_of=..., settings=None, layer=None)`` is the one execution path; the command line
(``python -m claview_a``) and the evaluator call it. It adds no logic of its own: it supplies the default model
settings and the catalog layer, then runs the straight graph (candidate pool -> cards -> judge -> check -> route).
``judge(payload, request) -> text`` is the only model access (``claview_a.judges``). There is no fallback to another
path: an error of the judge, the contract or the check is recorded in the returned state, and other errors propagate.
"""

from __future__ import annotations

from . import contract as K
from .graph import run as _graph_run

# The model settings the fixed 40- and 26-question runs used (the evaluator's MODEL).
DEFAULT_MODEL = {"model": "gpt-6-luna", "effort": "low", "max_output_tokens": 4096}
_LAYER: list = []


def default_layer():
    """The catalog layer (349 tables), loaded once per process from the repository's data files."""
    if not _LAYER:
        from .catalog.layer import load_layer

        _LAYER.append(load_layer())
    return _LAYER[0]


def default_settings() -> K.Settings:
    return K.Settings(**DEFAULT_MODEL)


def run(question: str, judge, *, as_of: str, settings: K.Settings | None = None, layer=None) -> dict:
    """The state of one question: ``pool``, ``cards``, ``payload``, ``record`` (selection, asks, partial result,
    failure), ``action`` (handoff / ask / fail) and ``run_info``."""
    return _graph_run(question, default_layer() if layer is None else layer, judge,
                      default_settings() if settings is None else settings, as_of)
