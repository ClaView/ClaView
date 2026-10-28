"""Straight LangGraph of the table-level path (plan 37): pool -> cards -> judge -> check -> route.

No loop, no search retry, no second model call. The model is a callable handed in through the run config
(``configurable.judge``: payload, request -> response text), so tests and replays give a fake one and a live run
gives the provider; this module never reaches the network itself. The judge's expected failures arrive as
``JudgeError`` and are recorded (``record.error``, route ``fail``) with no retry. The router reads the record's outcomes and picks one
action (``check.route``); limits and safety stay outside it (the payload limit in ``contract.build_payload``).

Nothing here names a table, case or question.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .search.tables import selection_catalog

from . import check as CH
from . import contract as K
from .cards import build_cards
from .pool import build_pool

GRAPH_VERSION = "tl-graph-2026-10-28.3"
JUDGE_ERRORS = ("transport", "timeout", "incomplete", "refusal", "empty_output", "response_format", "key_unavailable")


class JudgeError(RuntimeError):
    """An expected failure of the judge callable (a provider or a replay): one of ``JUDGE_ERRORS``. The graph records
    the code and ends with ``fail``; it never retries. Any other exception is a fault of the program and propagates.
    No raw message is recorded."""

    def __init__(self, code: str):
        if code not in JUDGE_ERRORS:
            raise ValueError(f"unknown judge error {code!r}")
        super().__init__(code)
        self.code = code


class TLState(TypedDict, total=False):
    question: str
    as_of: str
    pool: dict
    cards: list
    payload: dict
    response_text: str
    parsed: dict
    error: dict
    record: dict
    action: str


def _layer(config) -> Any:
    return config["configurable"]["layer"]


def node_pool(state: TLState, config) -> dict:
    return {"pool": build_pool(state["question"], _layer(config))}


def node_cards(state: TLState, config) -> dict:
    return {"cards": build_cards(state["question"], state["pool"], _layer(config))}


def node_judge(state: TLState, config) -> dict:
    try:
        payload = K.build_payload(state["question"], state["cards"])
    except K.ContractError as e:
        return {"error": {"stage": "input", "code": e.code, "detail": e.detail}}
    settings: K.Settings = config["configurable"]["settings"]
    try:
        text = config["configurable"]["judge"](payload, settings.request(payload))
    except JudgeError as e:
        return {"payload": payload, "error": {"stage": "judge", "code": e.code, "detail": ""}}
    try:
        parsed = K.parse_output(text, state["cards"])
    except K.ContractError as e:
        return {"payload": payload, "response_text": text,
                "error": {"stage": "output", "code": e.code, "detail": e.detail}}
    return {"payload": payload, "response_text": text, "parsed": parsed}


def node_check(state: TLState, config) -> dict:
    if state.get("error"):
        record = {"version": CH.CHECK_VERSION, "question": state["question"], "as_of": state["as_of"],
                  "model_output": None, "error": state["error"], "decisions": []}
        record["route"] = {"action": "fail", "complete": False, "counts": {o: 0 for o in CH.OUTCOMES}}
        return {"record": record}
    views = selection_catalog(_layer(config)).views
    return {"record": CH.check(state["question"], state["cards"], state["parsed"], views, state["as_of"])}


def node_route(state: TLState) -> dict:
    return {"action": state["record"]["route"]["action"]}


def build_graph():
    g = StateGraph(TLState)
    g.add_node("pool", node_pool)
    g.add_node("cards", node_cards)
    g.add_node("judge", node_judge)
    g.add_node("check", node_check)
    g.add_node("route", node_route)
    g.add_edge(START, "pool")
    g.add_edge("pool", "cards")
    g.add_edge("cards", "judge")
    g.add_edge("judge", "check")
    g.add_edge("check", "route")
    g.add_edge("route", END)
    return g.compile()


_CATALOG_SHA: dict[str, str] = {}


def run_info(as_of: str, settings: K.Settings) -> dict:
    """What a run needs to be reproduced, recorded when it runs (not when it is scored): the path versions, the
    request date, the catalog files, the ask axes and the shared period policy, the model settings."""
    import hashlib

    from .periods import core as claview_period
    from . import paths

    from . import cards, check, contract, periods, pool

    if not _CATALOG_SHA:
        for name, path in (("search_catalog", paths.CATALOG_SEARCH), ("meta_raw", paths.CATALOG_META_RAW)):
            _CATALOG_SHA[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    policy = claview_period.default_policy()
    return {"versions": {"pool": pool.POOL_VERSION, "cards": cards.CARDS_VERSION,
                         "contract": contract.CONTRACT_VERSION, "check": check.CHECK_VERSION,
                         "periods": periods.PERIODS_VERSION, "graph": GRAPH_VERSION,
                         "ask_axes": cards.ASK["version"],
                         "prompt_sha256": hashlib.sha256(K.prompt_text().encode("utf-8")).hexdigest()},
            "as_of": as_of, "catalog": dict(_CATALOG_SHA),
            "period_policy": {"version": policy.version, "sha256": policy.sha256},
            "model": {"model": settings.model, "effort": settings.effort,
                      "max_output_tokens": settings.max_output_tokens}}


def run(question: str, layer, judge, settings: K.Settings, as_of: str) -> TLState:
    """One question through the straight graph. ``judge(payload, request) -> text`` is the only model access.
    The state carries ``run_info`` taken at run time."""
    graph = build_graph()
    state = graph.invoke({"question": question, "as_of": as_of},
                         config={"configurable": {"layer": layer, "judge": judge, "settings": settings}})
    return {**state, "run_info": run_info(as_of, settings)}
