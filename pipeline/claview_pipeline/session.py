"""Session entry points: A's interactive session with the B port passed every turn.

A owns the outer graph (interpret -> select -> verify -> question_route -> wait/resume)
and the call into B (`claview_a.agent.b_connection.connect`). A never stores the
port, so each turn hands it over again. Without a B model no port is built and A
reports `b_port_not_configured`; B selections are never invented here.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

from claview_a.agent import session as a_session
from claview_a.agent.b_connection import AdapterPort
from claview_a.catalog.layer import load_layer
from claview_b.fetch import DataClient
from claview_b.metadata import MetadataStore
from claview_b.models import SelectionModel
from claview_common.settings import PATHS

from .b_port import make_b_port

ROOT = PATHS.root
# The 349-table catalog in data/ (A reads the same files, plan 27).
B_CATALOG = PATHS.catalog_file
B_METADATA = PATHS.meta_raw_file


@dataclass
class FlowSession:
    """Loaded A layer and B metadata, plus the injected B clients (default: none)."""

    layer: Any
    store: MetadataStore
    b_model: SelectionModel | None = None
    fetch_client: DataClient | None = None
    search: Any = None
    _port: AdapterPort | None = field(default=None, init=False, repr=False)

    @classmethod
    def load(
        cls,
        *,
        b_model: SelectionModel | None = None,
        fetch_client: DataClient | None = None,
        search: Any = None,
        layer: Any = None,
        store: MetadataStore | None = None,
    ) -> FlowSession:
        """Load the real catalog once (A layer without external synonyms, B store)."""
        if layer is None:
            layer = _default_layer()
        if store is None:
            store = _default_store()
        return cls(layer, store, b_model, fetch_client, search)

    def port(self) -> AdapterPort | None:
        if self.b_model is None:
            return None
        if self._port is None:
            self._port = make_b_port(self.store, model=self.b_model, fetch_client=self.fetch_client, search=self.search)
        return self._port

    def run_question(
        self,
        question: str,
        *,
        as_of: str | None = None,
        decomposition: dict | None = None,
        request_id: str | None = None,
    ) -> dict:
        return a_session.run_session(
            question,
            layer=self.layer,
            as_of=as_of,
            request_id=request_id,
            decomposition=decomposition,
            b_port=self.port(),
        )

    def answer(self, state: dict, answer: dict) -> dict:
        """Resume a waiting state with an ID-only answer (see `make_answer`)."""
        return a_session.resume_session(state, answer, layer=self.layer, b_port=self.port())

    def new_question(self, state: dict, text: str, *, decomposition: dict | None = None) -> dict:
        return a_session.new_question(state, text, layer=self.layer, decomposition=decomposition, b_port=self.port())


def make_answer(state: dict, option_id: str | None = None, *, index: int = 0) -> dict:
    """The ID-only answer A accepts for the pending public question."""
    question = state["response"].get("question")
    if not question:
        raise ValueError("no pending question")
    options = question["options"]
    chosen = option_id if option_id is not None else options[index]["option_id"]
    return {
        "request_id": state["request_id"],
        "question_version": state["question_version"],
        "question_id": question["question_id"],
        "option_id": chosen,
    }


@functools.cache
def _default_layer() -> Any:
    return load_layer(include_external_synonyms=False)


@functools.cache
def _default_store() -> MetadataStore:
    return MetadataStore(B_CATALOG, B_METADATA)


def _session(b_model: SelectionModel | None, fetch_client: DataClient | None) -> FlowSession:
    return FlowSession.load(b_model=b_model, fetch_client=fetch_client)


def run_question(
    question: str,
    *,
    as_of: str | None = None,
    decomposition: dict | None = None,
    b_model: SelectionModel | None = None,
    fetch_client: DataClient | None = None,
) -> dict:
    """One question through A and, when a B model is given, B and fetch."""
    session = _session(b_model, fetch_client)
    return session.run_question(question, as_of=as_of, decomposition=decomposition)


def answer(
    state: dict,
    answer: dict,
    *,
    b_model: SelectionModel | None = None,
    fetch_client: DataClient | None = None,
) -> dict:
    return _session(b_model, fetch_client).answer(state, answer)


def new_question(
    state: dict,
    text: str,
    *,
    decomposition: dict | None = None,
    b_model: SelectionModel | None = None,
    fetch_client: DataClient | None = None,
) -> dict:
    return _session(b_model, fetch_client).new_question(state, text, decomposition=decomposition)
