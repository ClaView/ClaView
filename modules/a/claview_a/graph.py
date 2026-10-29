"""LangGraph of the table-level path (plan 37): pool -> cards -> judge -> check -> route, with one optional
re-search (user 2026-10-29 07:51 UTC; off unless the run asks for it).

The re-search runs at most once, only when every statistic of the first round was left missing by the model
(``not_in_pool``): the model's search words are searched with the same settings, the new tables are added after the
first round's cards (which stay as they were, in order), and the model judges the whole question once more under
the same checks. Code never selects a table by itself, and a statistic the first round decided is never redone (a
first round with any decided or failed statistic is not re-judged). It ends, with the reason recorded, when there is
no search word, no new table, the input limit is exceeded or the run's re-judge budget is used up. The first
round's pool, cards, answer and record are kept. The re-judgment may use its own judge and settings
(``configurable.research_judge`` / ``research_settings``, user 2026-10-29 08:18 UTC: another model); its input adds
the unresolved statistics and their condition quotes (``contract.build_payload``, ``unresolved``) and keeps the same
output contract. A missing answer is a re-search signal, not a confirmed absence.

The model is a callable handed in through the run config
(``configurable.judge``: payload, request -> response text), so tests and replays give a fake one and a live run
gives the provider; this module never reaches the network itself. The judge's expected failures arrive as
``JudgeError`` and are recorded (``record.error``, route ``fail``) with no retry. The router reads the record's outcomes and picks one
action (``check.route``); the edges after a check and after a re-search are separate pure functions of the state
(``after_check``, ``after_research``). Limits and safety stay outside them (the payload limit in
``contract.build_payload``, the extra-hit cap in ``pool``, the re-judge budget the runner hands in).

Nothing here names a table, case or question.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .search.tables import selection_catalog

from . import check as CH
from . import contract as K
from .cards import build_cards
from .pool import EXTRA_CAP, build_pool, pool_tables

GRAPH_VERSION = "tl-graph-2026-10-29.4"  # .4: one optional re-search after a wholly missing first round
RESEARCH_VERSION = "tl-research-2026-10-29.2"  # .2: own judge and settings; unresolved statistics in the input
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
    research_mode: bool
    round: int
    research: dict
    first_round: dict
    unresolved: list


def _layer(config) -> Any:
    return config["configurable"]["layer"]


def node_pool(state: TLState, config) -> dict:
    return {"pool": build_pool(state["question"], _layer(config))}


def node_cards(state: TLState, config) -> dict:
    return {"cards": build_cards(state["question"], state["pool"], _layer(config))}


def node_judge(state: TLState, config) -> dict:
    try:
        payload = K.build_payload(state["question"], state["cards"], state.get("unresolved"))
    except K.ContractError as e:
        return {"error": {"stage": "input", "code": e.code, "detail": e.detail}}
    conf = config["configurable"]
    second = state.get("round", 1) == 2
    settings: K.Settings = (conf.get("research_settings") if second else None) or conf["settings"]
    judge = (conf.get("research_judge") if second else None) or conf["judge"]
    try:
        text = judge(payload, settings.request(payload))
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


def unresolved_of(record: dict) -> list[dict]:
    """The statistics the first judgment left missing, with the condition quotes it attached to each (as written by
    the model) and its search words: the re-judgment's ``unresolved`` input."""
    out = record["model_output"]
    rows = []
    for d in record["decisions"]:
        sid = d["statistic"]
        stat = next(s for s in out["statistics"] if s["id"] == sid)
        rows.append({"id": sid, "quote": stat["quote"], "name": stat["name"],
                     "search_words": list(d.get("search_words") or []),
                     **{k: [r["quote"] for r in out[k] if sid in r["for"]]
                        for k in ("splitters", "periods", "frequencies", "calculations")}})
    return rows


def research_words(record: dict) -> tuple[list[str], str | None]:
    """The model's search words when every statistic of the first round was left missing, else why nothing is
    re-searched: ``first_round_error``, ``no_missing_statistic``, ``other_statistics_decided`` (a decided or failed
    statistic would be judged again), ``no_search_words``."""
    if record.get("error"):
        return [], "first_round_error"
    decisions = record.get("decisions") or []
    missing = [d for d in decisions if d["outcome"] == "not_in_pool"]
    if not missing:
        return [], "no_missing_statistic"
    if len(missing) != len(decisions):
        return [], "other_statistics_decided"
    words: list[str] = []
    for d in missing:
        for w in d.get("search_words") or []:
            w = (w or "").strip()
            if w and w not in words:
                words.append(w)
    return (words, None) if words else ([], "no_search_words")


def node_research(state: TLState, config) -> dict:
    """The one re-search: the new pool and cards with their counts and input size, or the recorded reason it ends.
    Nothing is trimmed to fit: an input over the limit ends it."""
    words, why = research_words(state["record"])
    row = {"version": RESEARCH_VERSION, "extra_cap": EXTRA_CAP, "payload_limit": K.PAYLOAD_LIMIT,
           "search_words": words, "status": "not_run", "end": why,
           "missing_read_as": "a re-search signal, not a confirmed absence"}
    if why:
        return {"research": row}
    layer, question = _layer(config), state["question"]
    pool = build_pool(question, layer, extra=words)
    cards = build_cards(question, pool, layer)
    before, after = pool_tables(state["pool"]), pool_tables(pool)
    n = len(state["cards"])
    row.update({"extra_hits": pool["extra"],
                "families_added": [f["family"] for f in pool["families"][len(state["pool"]["families"]):]],
                "tables_before": len(before), "tables_after": len(after),
                "tables_added": [t for t in after if t not in set(before)], "cards_before": n,
                "cards_after": len(cards), "payload_chars_before": len(K.canonical(state["payload"]))})
    if cards[:n] != state["cards"] or after[:len(before)] != before:
        row["end"] = "original_candidates_changed"
        return {"research": row}
    if not row["tables_added"]:
        row["end"] = "no_new_candidates"
        return {"research": row}
    unresolved = unresolved_of(state["record"])
    try:
        payload = K.build_payload(question, cards, unresolved)
    except K.ContractError as e:
        row.update(end=e.code, detail=e.detail)
        return {"research": row}
    row["payload_chars_after"] = len(K.canonical(payload))
    budget = config["configurable"].get("research_budget")
    if budget is not None and not budget():
        row["end"] = "rejudge_call_limit"
        return {"research": row}
    row.update(status="rejudging", end=None)
    first = {k: state.get(k) for k in ("pool", "cards", "payload", "response_text", "parsed", "record")}
    return {"research": row, "first_round": first, "round": 2, "pool": pool, "cards": cards, "payload": None,
            "response_text": None, "parsed": None, "error": None, "record": None, "unresolved": unresolved}


def node_research_end(state: TLState) -> dict:
    """How the re-judged round ended: ``still_missing`` (every statistic missing again), ``second_round_error``
    or ``rejudged`` (the outcomes are the second round's, checked as the first round's are)."""
    record = state["record"]
    outcomes = [d["outcome"] for d in record.get("decisions") or []]
    end = ("second_round_error" if record.get("error") else
           "still_missing" if outcomes and all(o == "not_in_pool" for o in outcomes) else "rejudged")
    return {"research": {**state["research"], "status": "rejudged", "end": end, "second_round_outcomes": outcomes}}


def after_check(state: TLState) -> str:
    """The next node after a check (pure): the second round's end, the re-search when the run asks for one, or
    the route."""
    if state.get("round", 1) == 2:
        return "research_end"
    return "research" if state.get("research_mode") else "route"


def after_research(state: TLState) -> str:
    """Judge again only when the re-search is ready; any recorded end goes to the route (pure)."""
    return "judge" if state["research"]["status"] == "rejudging" else "route"


def node_route(state: TLState) -> dict:
    return {"action": state["record"]["route"]["action"]}


def build_graph():
    g = StateGraph(TLState)
    g.add_node("pool", node_pool)
    g.add_node("cards", node_cards)
    g.add_node("judge", node_judge)
    g.add_node("check", node_check)
    g.add_node("research", node_research)
    g.add_node("research_end", node_research_end)
    g.add_node("route", node_route)
    g.add_edge(START, "pool")
    g.add_edge("pool", "cards")
    g.add_edge("cards", "judge")
    g.add_edge("judge", "check")
    g.add_conditional_edges("check", after_check,
                            {"research": "research", "research_end": "research_end", "route": "route"})
    g.add_conditional_edges("research", after_research, {"judge": "judge", "route": "route"})
    g.add_edge("research_end", "route")
    g.add_edge("route", END)
    return g.compile()


def research_summary(state: TLState) -> dict:
    """The re-search row and the first round in short, for a run record (empty when the run did not ask for it)."""
    if not state.get("research_mode"):
        return {}
    from .cards import refs

    out = {"research": state.get("research")}
    first = state.get("first_round")
    out["card_refs"] = {"round_1": refs((first or state)["cards"]), **({"round_2": refs(state["cards"])} if first
                                                                       else {})}
    if first:
        rec = first["record"] or {}
        out["first_round"] = {"model": rec.get("model_output"), "route": (rec.get("route") or {}).get("action"),
                              "outcomes": {d["statistic"]: d["outcome"] for d in rec.get("decisions") or []},
                              "cards": [c["name"] for c in first["cards"]],
                              "pool": [r["table_id"] for c in first["cards"] for r in c["tables"]],
                              "payload_chars": len(K.canonical(first["payload"])),
                              "response_text": first.get("response_text")}
        out["unresolved"] = state.get("unresolved")
    return out


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
                         "ask_axes": cards.ASK["version"], "research": RESEARCH_VERSION,
                         "prompt_sha256": hashlib.sha256(K.prompt_text().encode("utf-8")).hexdigest()},
            "as_of": as_of, "catalog": dict(_CATALOG_SHA),
            "period_policy": {"version": policy.version, "sha256": policy.sha256},
            "model": {"model": settings.model, "effort": settings.effort,
                      "max_output_tokens": settings.max_output_tokens}}


def run(question: str, layer, judge, settings: K.Settings, as_of: str, research: bool = False,
        research_budget=None, research_judge=None, research_settings: K.Settings | None = None) -> TLState:
    """One question through the graph. ``judge(payload, request) -> text`` is the only model access. ``research``:
    allow the one re-search (default off: one call at most); ``research_budget() -> bool``: the runner's re-judge
    budget, asked once before a second call (None: no budget of its own); ``research_judge`` /
    ``research_settings``: the re-judgment's own judge and settings (None: the first ones). The state carries
    ``run_info`` taken at run time."""
    graph = build_graph()
    state = graph.invoke({"question": question, "as_of": as_of, "research_mode": bool(research), "round": 1},
                         config={"configurable": {"layer": layer, "judge": judge, "settings": settings,
                                                  "research_budget": research_budget,
                                                  "research_judge": research_judge,
                                                  "research_settings": research_settings}})
    info = run_info(as_of, settings)
    if state.get("first_round"):
        info["research_model"] = run_info(as_of, research_settings or settings)["model"]
    return {**state, "run_info": info}
