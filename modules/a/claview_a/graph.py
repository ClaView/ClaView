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
``JudgeError`` and are recorded (``record.error``, route ``fail``) with no retry. The router reads the record's
outcomes and picks one action (``check.route``); the edges after a check and after a re-search are pure state functions
(``after_check``, ``after_research``). Limits and safety stay outside them (the payload limit in
``contract.build_payload``, the extra-hit cap in ``pool``, the re-judge budget the runner hands in).

Nothing here names a table, case or question.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import check as CH
from . import contract as K
from . import reselection as RS
from .cards import build_cards
from .catalog_scope import contains as scope_contains
from .pool import EXTRA_CAP, build_pool, pool_tables
from .search.tables import selection_catalog

# .6: the rejudge targets, a re-search when some statistics are missing, the per-statistic merge (plan 56 order 9)
GRAPH_VERSION = "tl-graph-2026-10-31.6"  # .4: one optional re-search after a wholly missing first round; .5: ledger
# (plan 56 order 3): the re-search reads ``ledger.available("a_self_rejudge")`` and a failed first round is split
RESEARCH_VERSION = "tl-research-2026-10-29.2"  # .2: own judge and settings; unresolved statistics in the input
# the one limited self-recovery (plan 56 order 9, bundle 10, ⑧): the targets of a re-judgment and why others are not,
# the merge of a second round per statistic under the condition preservation check
# .3: every correction target left unfixed records its recovery status, not only a basis polarity error (13-2 review)
# .4: an accepted second decision keeps no reference to its second-round ID and the record's model output takes its
# rows; a first answer that broke the contract is judged once more as a whole (Codex review 11-01)
# .10: typed row preservation, source-span corrections, absence rechecks and normalized same-period references
# .9: contract .8 conditions are kept by their structured values (meaning, form, calendar values, count and unit,
# frequency and anchor, same-period references); only the rows the first round failed to structure (by the code's
# check, never the model's word) may be corrected, recorded in ``condition_corrections``; a statistic with applied
# user answers is never taken from a second round (BOK20 live check 11-02, owner 02:37 and 02:47 UTC)
REJUDGE_VERSION = "tl-rejudge-2026-11-03.15"  # .15: item identity and diagnosed calculation quote repairs
# .14: copy valid scope before checking a partial recovery
# .8: a merged focus limit is checked before recovery and after adoption; shared-limit facts accompany targets
# .7: scope fact errors share the existing recovery slot; .6: fixed statistics referable; .5: the whole ID map at
# once; .2: corrections sent (contract .6), changes with reasons
EXCLUDE_WHYS = ("policy_hold", "fact_gap", "unsupported_expression", "user_only", "rule_mismatch")
_FACT_GAP = ("target_table_unconfirmed", "content_unverified", "retention_unverified", "value_names_differ",
             "table_not_in_pool", "frequency_not_offered", "period_not_covered")
# a fact on the card the model misread, misquoted or did not attribute: the model reviews it (⑥ table)
_CORRECTABLE = ("basis_polarity_invalid", "rejudge_pending", "period_rows_inconsistent", "basis_attribution_unresolved",
                "period_unresolved", "focus_quote_not_in_question", "focus_too_long",
                "focus_catalog_condition_missing", *CH.SEMANTIC_INTERNAL)
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
    second_round_record: dict
    rejudge_fixed: dict
    rejudge_input: list


def _layer(config) -> Any:
    return config["configurable"]["layer"]


def node_pool(state: TLState, config) -> dict:
    return {"pool": build_pool(state["question"], _layer(config))}


def node_cards(state: TLState, config) -> dict:
    return {"cards": build_cards(state["question"], state["pool"], _layer(config))}


# the contract of a judge that names none: the current one (a stored answer is read with its record's contract,
# which the replay names on its judges; never guessed from an answer's content)
UNDECLARED_CONTRACT = K.CONTRACT_VERSION


def node_judge(state: TLState, config) -> dict:
    try:
        payload = K.build_payload(state["question"], state["cards"], state.get("unresolved"),
                                  fixed=state.get("rejudge_fixed"), targets=state.get("rejudge_input"))
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
        # the contract the judge's answers are written to (a replay names the record's; plan 56 order 6-3)
        fixed_ids = [x["id"] for x in (state.get("rejudge_fixed") or {}).get("decided") or []] if second else []
        parsed = K.parse_output(text, state["cards"], getattr(judge, "answer_contract", None) or UNDECLARED_CONTRACT,
                                **({"fixed_ids": fixed_ids} if fixed_ids else {}))
    except K.ContractError as e:
        return {"payload": payload, "response_text": text,
                "error": {"stage": "output", "code": e.code, "detail": e.detail}}
    return {"payload": payload, "response_text": text, "parsed": parsed}


def node_check(state: TLState, config) -> dict:
    if state.get("error"):
        record = {"version": CH.CHECK_VERSION, "question": state["question"], "as_of": state["as_of"],
                  "model_output": None, "error": state["error"], "decisions": []}
        record["route"] = {"action": "fail", "complete": False, "counts": {o: 0 for o in CH.OUTCOMES}}
        if state.get("round", 1) == 1:
            record["rejudge"] = {"version": REJUDGE_VERSION, **rejudge_targets(record), "merged": [],
                                 "rejected": [], "ledger_slot": "a_self_rejudge"}
        return {"record": record}
    views = selection_catalog(_layer(config)).views
    parsed = state["parsed"]
    if state.get("round", 1) == 2 and state.get("first_round"):
        # a re-judged statistic's same_as may name a fixed statistic: its first-round period rows are what it refers
        # to (read only; the fixed statistic is not judged again)
        first_out = (state["first_round"].get("record") or {}).get("model_output") or {}
        own = {s["id"] for s in parsed["output"]["statistics"]}
        named = {x for p in parsed["output"]["periods"] for x in p.get("refers_to") or []} - own
        parsed = {**parsed, "scope_statistic_count": len(first_out.get("statistics") or []),
                  "reference_periods": {x: [r for r in first_out.get("periods") or [] if x in r["for"]]
                                                  for x in sorted(named)}}
    patch = []
    raw_output = None
    if state.get("round", 1) == 2 and state.get("first_round"):
        from copy import deepcopy

        raw_output = deepcopy(parsed["output"])
        parsed, patch = prepare_recovery_output(state["first_round"]["record"], parsed)
    record = CH.check(state["question"], state["cards"], parsed, views, state["as_of"])
    if raw_output is not None:
        record["recovery_raw_output"] = raw_output
        record["recovery_patch"] = patch
    if state.get("round", 1) == 1:  # the one self-recovery's targets, recorded on every first round (⑧-가)
        RS.identities(record, state["cards"], views)
        RS.prepare(record, state["cards"], views)
        record["rejudge"] = {"version": REJUDGE_VERSION, **rejudge_targets(record), "merged": [], "rejected": [],
                             "ledger_slot": "a_self_rejudge"}
    return {"record": record}


def rejudge_targets(record: dict) -> dict:
    """{targets, excluded} of the one self-recovery (plan 56 order 9, ⑧-가). A target: a statistic the model left
    missing with search words (``research``; some missing is enough), or one a correction may fix (``correction``):
    a correctable output error, an ask with no executable option and a fitting table outside it, a stated basis in
    conflict or unverified with an alternative on the card, a period quote the code could not attribute, a first
    round whose answer broke the contract. Excluded, with why (``EXCLUDE_WHYS``): what only the user can give
    (user_only), a fact the catalog lacks or the code cannot confirm (fact_gap), an expression the first stage does
    not support (unsupported_expression), a check rule the answer cannot meet (rule_mismatch). A transport error of
    the first round opens nothing. Nothing is decided here."""
    if record.get("error"):
        kind = first_round_kind(record)
        if kind["correctable"]:
            return {"targets": [{"statistic": None, "kind": "correction", "code": kind["kind"]}], "excluded": []}
        return {"targets": [], "excluded": [{"statistic": None, "why": "rule_mismatch", "code": kind["kind"]}]}
    targets, excluded = [], []
    for d in record.get("decisions") or []:
        sid, codes = d["statistic"], [r["code"] for r in d.get("reasons") or []]
        if d["outcome"] == "not_in_pool":
            words = [w.strip() for w in d.get("search_words") or [] if (w or "").strip()]
            if words:
                targets.append({"statistic": sid, "kind": "research", "code": "missing", "search_words": words})
            else:
                excluded.append({"statistic": sid, "why": "fact_gap", "code": "no_search_words"})
            continue
        if d["outcome"] == CH.CONDITION_OUTCOME:
            excluded.append({"statistic": sid, "why": "user_only", "code": "condition_ask"})
            continue
        if d["outcome"] != "failed":
            continue
        fixes = [t for t in d.get("rejudge_targets") or []]
        cause = (d.get("period") or {}).get("cause")
        if d.get("period_reselection"):
            targets.append({"statistic": sid, "kind": "correction", "code": "period_table_reselection",
                            "reasons": codes, "fixes": [d["period_reselection"]]})
        elif cause == "unsupported_expression":
            excluded.append({"statistic": sid, "why": "unsupported_expression", "code": codes[0] if codes else None})
        elif fixes or any(c in _CORRECTABLE for c in codes) or "period_quote_missing" in codes and \
                cause == "extraction_missing":
            targets.append({"statistic": sid, "kind": "correction", "code": codes[0] if codes else fixes[0]["code"],
                            "fixes": fixes, "reasons": codes})
        elif any(c in _FACT_GAP for c in codes):
            excluded.append({"statistic": sid, "why": "fact_gap", "code": codes[0]})
        else:
            excluded.append({"statistic": sid, "why": "rule_mismatch", "code": codes[0] if codes else None})
    return {"targets": targets, "excluded": excluded}


def _quotes(out: dict, sid: str, key: str) -> list[str]:
    return [r["quote"] for r in (out or {}).get(key) or [] if sid in r["for"]]


def _match(first: dict, second: dict, sid: str) -> str | None:
    """The second round's statistic for the first round's ``sid``: the same ID with the same quote, else the one
    statistic with that quote (an ID the second round renumbered); None when there is none or several."""
    quote = next(s["quote"] for s in first["model_output"]["statistics"] if s["id"] == sid)
    stats = (second.get("model_output") or {}).get("statistics") or []
    same = [s["id"] for s in stats if s["id"] == sid and s["quote"] == quote]
    if same:
        return same[0]
    by_quote = [s["id"] for s in stats if s["quote"] == quote]
    if len(by_quote) == 1:
        return by_quote[0]
    from .search.tables import nospace

    normalized = nospace(quote)
    own = [s for s in first["model_output"]["statistics"] if nospace(s["quote"]) == normalized]
    matches = [s["id"] for s in stats if nospace(s["quote"]) == normalized]
    return matches[0] if len(own) == len(matches) == 1 else None


def prepare_recovery_output(first: dict, parsed: dict) -> tuple[dict, list[dict]]:
    """Copy valid non-target scope before the ordinary check; never infer synonymous intent.

    The raw model response stays separate. A replacement table is checked against the original scope and
    calculation requirements, rather than losing a valid repair because the model rephrased those fields.
    Focuses diagnosed as invalid are left to the existing recovery guard. Joining an old valid scope to an
    arbitrary model repair would not prove that the new wording preserves that scope.
    """
    from copy import deepcopy

    from .quotes import quote_in_question

    if not first.get("model_output"):
        return parsed, []
    result = deepcopy(parsed)
    output = result["output"]
    raw = {"model_output": parsed["output"]}
    wanted = {t["statistic"] for t in (first.get("rejudge") or {}).get("targets") or []}
    patches = []

    def replace(kind, sid, rows, why):
        supplied = [deepcopy(r) for r in output.get(kind) or [] if sid in r["for"]]
        replacement = [{**deepcopy(r), "for": [sid]} for r in rows]
        if supplied == replacement:
            return
        kept = []
        for r in output.get(kind) or []:
            rest = [s for s in r["for"] if s != sid]
            if rest:
                kept.append({**r, "for": rest})
        output[kind] = kept + replacement
        patches.append({"statistic": sid, "kind": kind, "why": why,
                        "supplied": supplied, "applied": deepcopy(replacement)})

    for d in first.get("decisions") or []:
        sid = d["statistic"]
        if sid not in wanted or d.get("condition_answers") or d.get("answered_asks"):
            continue
        other = _match(first, raw, sid)
        if other is None:
            continue
        original = first["model_output"]
        quote = next(s["quote"] for s in original["statistics"] if s["id"] == sid)
        stat = next(s for s in output["statistics"] if s["id"] == other)
        if stat["quote"] != quote:
            patches.append({"statistic": other, "kind": "statistic_quote", "why": "same_source_whitespace",
                            "supplied": stat["quote"], "applied": quote})
            stat["quote"] = quote
        focus_errors = {r["code"] for r in d.get("reasons") or []} & {
            "focus_quote_not_in_question", "focus_too_long", "focus_catalog_condition_missing"}
        focus_rows = [r for r in original.get("focuses") or [] if sid in r["for"]]
        if focus_rows and not focus_errors and (d.get("focus") or {}).get("status") == "grounded":
            replace("focuses", other, focus_rows, "valid_scope_kept")
        calculations = [r for r in original.get("calculations") or [] if sid in r["for"]]
        if all(quote_in_question(r["quote"], first["question"]) for r in calculations):
            replace("calculations", other, calculations, "valid_calculations_kept")
    return result, patches


def _changes(first: dict, second: dict, sid: str, other: str) -> dict:
    """(axis, value) -> the second round's change row for ``other`` with a quote of the question and a reason."""
    question = first.get("question") or ""
    return {(c["axis"], c["value"]): c for c in (second.get("model_output") or {}).get("changes") or []
            if other in c["for"] and c.get("reason", "").strip() and c.get("quote") and c["quote"] in question}


def _semantic(record: dict) -> bool:
    return (record.get("parse") or {}).get("answer_contract") in K.SEMANTIC_PERIOD_CONTRACTS


def _row_problems(out: dict, sid: str, as_of: str | None = None,
                  answer_contract: str = K.CONTRACT_VERSION) -> list[dict]:
    """The code's internal errors for this statistic, independent of an earlier table or scope failure."""
    from .periods import semantic as SP

    periods = [r for r in out["periods"] if sid in r["for"]]
    got = SP.read_rows([r for r in periods if r.get("meaning") != "same_as"] or periods,
                       [r for r in out["frequencies"] if sid in r["for"]], answer_contract=answer_contract,
                       **({"as_of": as_of} if as_of else {}))
    if not [r for r in periods if r.get("meaning") != "same_as"] and periods:
        got["problems"] = [p for p in got["problems"] if p["code"] != "period_row_missing"]
    return [p for p in got["problems"] if p["code"] in CH.SEMANTIC_INTERNAL]


def _row_errors(row: dict, kind: str, as_of: str | None,
                answer_contract: str = K.CONTRACT_VERSION) -> list[str]:
    """Validate one typed row. A sibling's failure or a group conflict never invalidates this row."""
    from .periods import semantic as SP

    got = SP.read_rows([row] if kind == "periods" else [], [row] if kind == "frequencies" else [],
                       answer_contract=answer_contract, **({"as_of": as_of} if as_of else {}))
    prefix = "period_" if kind == "periods" else "frequency_"
    return sorted({p["code"] for p in got["problems"] if p["code"].startswith(prefix)
                   and p["code"] in CH.SEMANTIC_INTERNAL})


def _absence(row: dict, kind: str) -> bool:
    return row.get("meaning" if kind == "periods" else "value") == "not_stated"


def _corresponding_quote(question: str, before: str, after: str) -> bool:
    """Whitespace-insensitive source spans may narrow or widen a failed excerpt, not jump to another clause.

    This verifies provenance only. It cannot prove that a model interpreted the period's meaning correctly.
    A changed excerpt must be contained in the other and the longer excerpt must occur only once in the question.
    """
    from .search.tables import nospace

    question, before, after = map(nospace, (question, before or "", after or ""))
    if not before or not after or before not in question or after not in question:
        return False
    if before == after:
        return True
    longer, shorter = (before, after) if len(before) >= len(after) else (after, before)
    return (shorter in longer and question.count(before) == 1 and question.count(longer) == 1
            and longer.count(shorter) == 1)


def _semantic_plan(first: dict, second: dict, sid: str, other: str) -> tuple[list[str], list[dict]]:
    """Match each typed condition before adopting a recovery. Valid values are immutable; an invalid explicit row
    needs a corresponding explicit replacement. No aggregate failure exempts a whole kind from preservation.

    Contract .9 may correct a bare not-stated declaration during an internal recovery. A not-stated declaration
    that contradicts an explicit condition may be removed; it never excuses removing that explicit condition.
    """
    from .periods import semantic as SP
    from .quotes import quote_in_question

    names = {matched: s["id"] for s in first["model_output"]["statistics"]
             if (matched := _match(first, second, s["id"])) is not None}
    normalized = _rename_refs(second["model_output"], names)
    normalized_sid = names.get(other, other)
    version = (first.get("parse") or {}).get("answer_contract")
    second_version = (second.get("parse") or {}).get("answer_contract")
    problems = _row_problems(first["model_output"], sid, first.get("as_of"), version)
    internal = bool(problems)
    repair_absence = version in K.CALENDAR_PERIOD_CONTRACTS and internal
    lost, changes = [], []
    for kind in ("periods", "frequencies"):
        mine = [r for r in first["model_output"][kind] if sid in r["for"]]
        theirs = [r for r in normalized[kind] if normalized_sid in r["for"]]
        unused = set(range(len(theirs)))
        errors = [_row_errors(r, kind, first.get("as_of"), version) for r in mine]
        explicit = [r for r in mine if not _absence(r, kind)]
        declarations = [r for r, err in zip(mine, errors) if not err and _absence(r, kind)]
        deferred = []
        # First reserve every independently valid, explicit row. This prevents a failed sibling from consuming it.
        for row, err in zip(mine, errors):
            if err or _absence(row, kind):
                deferred.append((row, err))
                continue
            matches = [i for i in unused if SP.structured_key(theirs[i], kind) == SP.structured_key(row, kind)
                       and not _row_errors(theirs[i], kind, second.get("as_of"), second_version)]
            if matches:
                unused.remove(matches[0])
            else:
                lost.append(f"{kind}:{row['quote']}")
        for row, err in deferred:
            same = [i for i in unused if SP.structured_key(theirs[i], kind) == SP.structured_key(row, kind)
                    and theirs[i].get("read") == row.get("read")
                    and _corresponding_quote(first.get("question") or "", row["quote"], theirs[i]["quote"])]
            if same:
                unused.remove(same[0])
                continue
            why = err
            if not err and _absence(row, kind):
                if explicit:
                    why = ["period_meaning_conflict" if kind == "periods" else "frequency_conflict"]
                    changes.append({"statistic": sid, "kind": kind, "scope": "row", "quote": row["quote"],
                                    "first": [row], "second": [], "why": why,
                                    "action": "removed_contradictory_absence"})
                    continue
                if repair_absence and len(declarations) == len(mine):
                    replacements = [i for i in unused if not _absence(theirs[i], kind)
                                    and not _row_errors(theirs[i], kind, second.get("as_of"), second_version)
                                    and quote_in_question(theirs[i]["quote"], first.get("question") or "")]
                    if replacements:
                        changes.append({"statistic": sid, "kind": kind, "scope": "row", "quote": row["quote"],
                                        "first": [row], "second": [theirs[i] for i in sorted(replacements)],
                                        "why": ["absence_declaration_rechecked"]})
                        unused.difference_update(replacements)
                        continue
                lost.append(f"{kind}:{row['quote']}")
                continue
            # A failed reading is not permission to drop the requirement or change its kind of intent.
            candidates = []
            for i in unused:
                new = theirs[i]
                if kind == "periods":
                    compatible = new.get("meaning") == row.get("meaning")
                else:
                    compatible = new.get("value") in SP.UNITS
                if compatible and not _row_errors(new, kind, second.get("as_of"), second_version) and \
                        _corresponding_quote(first.get("question") or "", row["quote"], new["quote"]):
                    candidates.append(i)
            exact = [i for i in candidates if theirs[i]["quote"] == row["quote"]]
            candidates = exact or candidates
            if len(candidates) != 1:
                lost.append(f"{kind}:{row['quote']}")
                continue
            index = candidates[0]
            unused.remove(index)
            changes.append({"statistic": sid, "kind": kind, "scope": "row", "quote": row["quote"],
                            "first": [row], "second": [theirs[index]], "why": why})
        if not mine:
            # A required row omitted entirely can be restored, with source and code checks.
            accepted = [i for i in unused if quote_in_question(theirs[i]["quote"], first.get("question") or "")
                        and not _row_errors(theirs[i], kind, second.get("as_of"), second_version)]
            if accepted:
                changes.append({"statistic": sid, "kind": kind, "scope": "missing_rows", "first": [],
                                "second": [theirs[i] for i in sorted(accepted)],
                                "why": ["period_row_missing" if kind == "periods" else "frequency_row_missing"]})
                unused.difference_update(accepted)
        lost += [f"{kind}:added:{theirs[i]['quote']}" for i in sorted(unused)]
    return lost, changes


def _semantic_lost(first: dict, second: dict, sid: str, other: str) -> list[str]:
    return _semantic_plan(first, second, sid, other)[0]


def condition_corrections(first: dict, second: dict, sid: str, other: str) -> list[dict]:
    """Accepted row corrections keep the original excerpt, replacement excerpt, values and typed reason."""
    return _semantic_plan(first, second, sid, other)[1]


def _preserved_rows(out: dict, sid: str, kind: str) -> set[str]:
    """Compare a statistic's intent, independent of ID, row order and quote whitespace.

    Aggregation evidence belongs to the selected card and may change with that card; the requested method,
    frequencies, source basis and weights must not change during an unrelated repair.
    """
    from .search.tables import nospace

    def normalized(value):
        if isinstance(value, str):
            return nospace(value)
        if isinstance(value, dict):
            return {k: normalized(v) for k, v in value.items()}
        if isinstance(value, list):
            return sorted((normalized(v) for v in value), key=K.canonical)
        return value

    omitted = {"for"} | ({"evidence", "average_evidence"} if kind == "aggregations" else set())
    return {K.canonical(normalized({k: v for k, v in row.items() if k not in omitted}))
            for row in out.get(kind) or [] if sid in row.get("for", [])}


def _basis_change_allowed(decision: dict, row: dict) -> bool:
    """A reason for another component, or a table/basis mismatch, never licenses changing valid polarity.

    Narrow row diagnostics retain their value/quote scope. An axis-level polarity conflict is the only broad
    scope here; its repair still needs the existing change provenance and must pass the second-round check.
    """
    from .search.tables import nospace

    for reason in decision.get("reasons") or []:
        if reason.get("code") != "basis_polarity_invalid" or not isinstance(reason.get("detail"), dict):
            continue
        detail = reason["detail"]
        for problem in detail.get("problems") or [detail]:
            if problem.get("cause") not in {"quote_not_in_question", "unreadable_row", "value_not_in_axis",
                                             "polarity_conflict"}:
                continue
            scope = problem.get("detail") or {}
            if scope.get("axis") != row["axis"]:
                continue
            if "value" in scope and scope["value"] != row.get("value"):
                continue
            if "quote" in scope and nospace(scope["quote"]) != nospace(row.get("quote", "")):
                continue
            return True
    return False


def _focus_repair_losses(first: dict, second: dict, sid: str, other: str, errors: set[str]) -> list[str]:
    """An erroneous focus still owns its valid source quotes; missing selectors only permit additions."""
    from .quotes import quote_in_question
    from .search.tables import nospace

    mine = [r for r in first["model_output"].get("focuses") or [] if sid in r["for"]]
    theirs = [r for r in second["model_output"].get("focuses") or [] if other in r["for"]]
    quotes = [nospace(q) for r in theirs for q in r["quotes"]]
    lost = [f"focus_quote:{q}" for r in mine for q in r["quotes"]
            if quote_in_question(q, first.get("question") or "") and not any(nospace(q) in new for new in quotes)]
    if errors == {"focus_catalog_condition_missing"}:
        texts = [nospace(r["text"]) for r in theirs]
        lost += ["focuses:scope_changed" for r in mine if not any(nospace(r["text"]) in new for new in texts)]
    return lost


def _calculation_repair_losses(first: dict, second: dict, sid: str, other: str, decision: dict) -> list[str]:
    """Only a diagnosed source-quote error may change during another allowed recovery.

    Normal calculation rows and the number of rows remain fixed. The contract has
    no structured calculation meaning: a new source quote is evidence, not a code
    proof that the model interpreted the requested calculation correctly.
    """
    from collections import Counter

    from .quotes import quote_in_question
    from .search.tables import nospace

    question = first.get("question") or ""
    diagnosed = {nospace(w["detail"]) for w in decision.get("warnings") or []
                 if w.get("code") == "calculation_quote_not_in_question" and isinstance(w.get("detail"), str)}
    mine = [r for r in first["model_output"].get("calculations") or [] if sid in r["for"]]
    theirs = [r for r in second["model_output"].get("calculations") or [] if other in r["for"]]
    broken = Counter(nospace(r["quote"]) for r in mine if nospace(r["quote"]) in diagnosed and
                     not quote_in_question(r["quote"], question))
    if not broken:
        return ([] if _preserved_rows(first["model_output"], sid, "calculations") ==
                _preserved_rows(second["model_output"], other, "calculations") else ["calculations:changed"])
    fixed = Counter(nospace(r["quote"]) for r in mine) - broken
    supplied = Counter(nospace(r["quote"]) for r in theirs)
    if len(mine) != len(theirs) or fixed - supplied:
        return ["calculations:changed"]
    # An unchanged invalid row retains the existing warning; every actual edit must
    # be grounded in the question. No row may disappear or create an extra slot.
    edits = (supplied - fixed) - broken
    return ["calculations:changed"] if any(not quote_in_question(q, question) for q in edits) else []


def condition_preservation(first: dict, second: dict, sid: str, other: str) -> list[str]:
    """What the second round lost of the first round's conditions for statistic ``sid`` (as ``other`` there): each
    period and frequency quote, and each basis row (value and polarity) the first round attached to it (plan 56 order
    9, 10-31 16:28 UTC). A basis change needs a matching code diagnosis plus its quote and reason (contract .6).
    Contract .8: the periods and frequencies by their structured values (``_semantic_lost``), and the user's applied
    answers. Normal focus, calculation intent and aggregation intent also survive unrelated repairs.
    Empty: the conditions are kept."""
    lost = []
    original = next((d for d in first.get("decisions", []) if d["statistic"] == sid), {})
    if _semantic(first):
        lost += _semantic_lost(first, second, sid, other)
        d = next((x for x in first.get("decisions") or [] if x["statistic"] == sid), {})
        if d.get("condition_answers") or d.get("answered_asks"):
            lost.append("user_answers")
    else:
        for key in ("periods", "frequencies"):
            mine, theirs = _quotes(first["model_output"], sid, key), set(_quotes(second["model_output"], other, key))
            lost += [f"{key}:{q}" for q in mine if q not in theirs]
    rows = [r for r in first["model_output"]["splitters"] if sid in r["for"] and r.get("value")]
    again = {(r["axis"], r.get("value"), r.get("polarity")) for r in second["model_output"]["splitters"]
             if other in r["for"]}
    changed = _changes(first, second, sid, other)
    for row in rows:
        a, v, p = row["axis"], row["value"], row.get("polarity")
        if (a, v, p) not in again and not (
                _basis_change_allowed(original, row) and changed.get((a, v)) and changed[(a, v)]["from"] == p
                and (a, v, changed[(a, v)]["to"]) in again):
            lost.append(f"splitters:{a}:{v}:{p}")
    focus_errors = {r.get("code") for r in original.get("reasons") or []} & {
        "focus_quote_not_in_question", "focus_too_long", "focus_catalog_condition_missing"}
    for kind in ("focuses", "calculations", "aggregations"):
        if kind == "focuses" and focus_errors:
            lost += _focus_repair_losses(first, second, sid, other, focus_errors)
            continue
        if kind == "calculations":
            lost += _calculation_repair_losses(first, second, sid, other, original)
            continue
        if _preserved_rows(first["model_output"], sid, kind) != _preserved_rows(second["model_output"], other, kind):
            lost.append(f"{kind}:changed")
    corrected = next((d for d in second.get("decisions", []) if d["statistic"] == other), {})
    for reason in original.get("reasons", []):
        if reason["code"] == "focus_catalog_condition_missing":
            for row in reason["detail"]["selectors"]:
                if not scope_contains((corrected.get("focus") or {}).get("text", ""), row["value"]):
                    lost.append(f"focus_selector:{row['axis']}:{row['value']}")
    return lost


def _recovery(record: dict, rejudge: dict, status: str) -> None:
    """A correctable output error the re-judgment did not fix ends with its recovery's status (``attempted_failed``,
    ``budget_exhausted``), never shown as waiting for another judgment."""
    fixed = {m["statistic"] for m in rejudge.get("merged") or [] if m["outcome"] != "failed"}
    # the reasons each correction target was judged again for (a period quote the model left out included)
    targeted = {t["statistic"]: set(t.get("reasons") or [t.get("code")]) for t in rejudge.get("targets") or []
                if t.get("kind") == "correction"}
    for d in record.get("decisions") or []:
        for r in d.get("reasons") or []:
            if d["statistic"] not in fixed and isinstance(r.get("detail"), dict) and \
                    (r["code"] == "basis_polarity_invalid" or r["code"] in targeted.get(d["statistic"], ())):
                r["detail"]["recovery"] = {**(r["detail"].get("recovery") or {}), "status": status}


_REF_KEYS = ("statistic", "statistics", "for", "refers_to")  # the keys whose values name statistics


def _rename_refs(value, names: dict[str, str]):
    """``value`` with every statistic reference (under the keys that name statistics) read through ``names``
    (second-round ID -> first-round ID), all at once: a swap of two IDs never collides (Codex review 11-01 v3)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _REF_KEYS and isinstance(v, str):
                v = names.get(v, v)
            elif k in _REF_KEYS and isinstance(v, list) and all(isinstance(x, str) for x in v):
                v = [names.get(x, x) for x in v]
            else:
                v = _rename_refs(v, names)
            out[k] = v
        return out
    if isinstance(value, list):
        return [_rename_refs(x, names) for x in value]
    return value


def _refs(value, keys=_REF_KEYS) -> set[str]:
    """Every string under ``keys`` in ``value`` (the statistic IDs it may name)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if k in keys and isinstance(v, str):
                found.add(v)
            elif k in keys and isinstance(v, list):
                found.update(x for x in v if isinstance(x, str))
            else:
                found |= _refs(v, keys)
    elif isinstance(value, list):
        for x in value:
            found |= _refs(x, keys)
    return found


def _names_any(row: dict, ids) -> list[str]:
    f = row.get("for")
    return [x for x in (f if isinstance(f, list) else [f]) if x in ids]


def _adopt_rows(output: dict, second: dict, names: dict[str, str]) -> None:
    """The record's model output after the targets' second decisions are taken (``names``: second-round ID ->
    first-round ID of every taken decision): the first round's rows of those statistics leave and the second round's
    rows come in read through ``names``, a shared row once with all its taken statistics, so a later answer reads the
    conditions of the decisions that were taken (the statistics list and every other statistic's rows stay)."""
    adopted = set(names.values())
    for key, rows in second.items():
        if key == "statistics" or not isinstance(rows, list):
            continue
        kept = []
        for r in output.get(key) or []:
            if not _names_any(r, adopted):
                kept.append(r)
            elif isinstance(r.get("for"), list):
                rest = [x for x in r["for"] if x not in adopted]
                if rest:
                    kept.append({**r, "for": rest})
        for r in rows:
            taken = _names_any(r, names)
            if taken:
                kept.append({**_rename_refs(r, names),
                             "for": [names[x] for x in taken] if isinstance(r["for"], list) else names[taken[0]]})
        output[key] = kept


def _merge_whole(first: dict, second: dict, rejudge: dict) -> dict:
    """A first answer that broke the output contract judged once more as a whole: a valid second round is taken
    whole; a failed one leaves the first round's error as it was (no third judgment)."""
    import copy

    if second.get("error"):
        record = copy.deepcopy(first)
        rejudge["rejected"] = [{"statistic": None, "why": "second_round_error"}]
    else:
        record = copy.deepcopy(second)
        rejudge["merged"] = [{"statistic": d["statistic"], "second_round_statistic": d["statistic"],
                              "outcome": d["outcome"]} for d in record.get("decisions") or []]
    rejudge["second_round_output"] = copy.deepcopy(second.get("model_output"))
    record["rejudge"] = rejudge
    record["route"] = CH.route(record)
    return record


def _keep_referenced(first: dict, second: dict, accepted: dict, wanted: list, by_sid: dict, rejudge: dict) -> None:
    """Drop from ``accepted`` every second decision that names a second-round statistic the merged record will not
    have under any first-round ID (one not taken, and no statistic kept from the first round with its quote), until
    none is left: a shared question or a reference never points to a statistic the record does not hold (Codex review
    11-01 v3). Each is rejected with ``reference_not_matched`` and the IDs it named."""
    second_quote = {s["id"]: s["quote"] for s in (second.get("model_output") or {}).get("statistics") or []}
    first_quote = {s["id"]: s["quote"] for s in (first.get("model_output") or {}).get("statistics") or []}
    kept = {d["statistic"] for d in first.get("decisions") or []} - set(wanted)
    kept_ok = {i for i in kept if i not in second_quote or second_quote[i] == first_quote.get(i)}
    second_ids = set(second_quote) | set(by_sid)
    rows = {k: v for k, v in ((second.get("model_output") or {}).items()) if k != "statistics" and isinstance(v, list)}
    while True:
        taken = set(accepted.values())
        drop = {}
        for sid, other in accepted.items():
            named = _refs(by_sid[other]) | {x for v in rows.values() for r in v if isinstance(r, dict)
                                             and other in _names_any(r, {other}) for x in _refs(r, ("refers_to",))}
            missing = sorted((named & second_ids) - taken - kept_ok)
            if missing:
                drop[sid] = missing
        if not drop:
            return
        for sid, missing in drop.items():
            del accepted[sid]
            rejudge["rejected"].append({"statistic": sid, "why": "reference_not_matched", "refs": missing})


def merge_rounds(first: dict, second: dict, targets: list[dict]) -> dict:
    """The record after the one re-judgment (plan 56 order 9, ⑧-나): every statistic that is no target keeps its first
    decision; a target takes the second round's only when its statistic is matched and its conditions are kept
    (``condition_preservation``), else the first stays and the reason is recorded. A taken decision is read under the
    first round's ID throughout (its asks and rows included) and the model output takes its rows (``_adopt_rows``).
    A second round that failed keeps the first round whole. A first answer that broke the contract is merged whole
    (``_merge_whole``)."""
    import copy

    rejudge = {"version": REJUDGE_VERSION, **copy.deepcopy(first.get("rejudge") or {}), "merged": [], "rejected": []}
    if first.get("error") and any(t.get("code") == "first_round_output_error" for t in targets):
        return _merge_whole(first, second, rejudge)
    record = copy.deepcopy(first)
    wanted = [t["statistic"] for t in targets if t["statistic"]]
    if second.get("error"):
        rejudge["rejected"] = [{"statistic": sid, "why": "second_round_error"} for sid in wanted]
        _recovery(record, rejudge, "attempted_failed")
    else:
        by_sid = {d["statistic"]: d for d in second.get("decisions") or []}
        accepted: dict[str, str] = {}  # first-round ID -> its second-round ID, in decision order
        for d in record["decisions"]:
            sid = d["statistic"]
            if sid not in wanted:
                continue
            other = _match(first, second, sid)
            if other is None or other not in by_sid:
                rejudge["rejected"].append({"statistic": sid, "why": "statistic_not_matched"})
                continue
            lost = (condition_preservation(first, second, sid, other) +
                    RS.identity_losses(first, second, sid, other) + RS.period_losses(first, second, sid, other))
            if lost:
                rejudge["rejected"].append({"statistic": sid, "why": "condition_lost", "lost": lost})
                continue
            accepted[sid] = other
        _keep_referenced(first, second, accepted, wanted, by_sid, rejudge)
        names = {other: sid for sid, other in accepted.items()}  # the whole map, applied at once
        for i, d in enumerate(record["decisions"]):
            sid = d["statistic"]
            if sid not in accepted:
                continue
            other = accepted[sid]
            new = _rename_refs(copy.deepcopy(by_sid[other]), names)
            new["statistic"], new["rejudged_from"] = sid, other
            record["decisions"][i] = new
            rejudge["merged"].append({"statistic": sid, "second_round_statistic": other, "outcome": new["outcome"]})
            if _semantic(first):
                rejudge.setdefault("condition_corrections", []).extend(condition_corrections(first, second, sid,
                                                                                             other))
            for c in _changes(first, second, sid, other).values():  # a change of a target's basis row, as given
                rejudge.setdefault("changes", []).append({"statistic": sid, "axis": c["axis"], "value": c["value"],
                                                          "from": c["from"], "to": c["to"], "quote": c["quote"],
                                                          "reason": c["reason"]})
        if names and record.get("model_output") and second.get("model_output"):
            _adopt_rows(record["model_output"], second["model_output"], names)
            # the merged statistics are asked together again where they share a period row or a same_as link
            CH.group_condition_asks({d["statistic"]: d for d in record["decisions"]}, record["model_output"])
            # A correction may join a fixed statistic's table. Check the composed group, but only the accepted
            # targets may fail: the fixed statistic and its scope stay exactly as they were. No third judgment.
            for d in CH.check_groups(record["decisions"], mutable=set(accepted)):
                CH.annotate(d, record["model_output"])
                for r in d["reasons"]:
                    if r["code"] == "focus_too_long" and r["detail"].get("scope") == "handoff_group":
                        r["detail"]["recovery"] = {"status": "attempted_failed"}
            for merged in rejudge["merged"]:
                merged["outcome"] = next(d["outcome"] for d in record["decisions"]
                                         if d["statistic"] == merged["statistic"])
        _recovery(record, rejudge, "attempted_failed")
        rejudge["second_round_output"] = copy.deepcopy(second.get("model_output"))
        rejudge["second_round_raw_output"] = copy.deepcopy(second.get("recovery_raw_output",
                                                                    second.get("model_output")))
        rejudge["preserved_fields"] = copy.deepcopy(second.get("recovery_patch") or [])
    record["rejudge"] = rejudge
    record["route"] = CH.route(record)
    return record


def rejudge_fixed(record: dict, sent: set) -> dict:
    """The re-judgment's fixed context (contract .6): the question and the statistics it must not change."""
    out = record["model_output"]
    return {"question": record["question"],
            "decided": [{"id": s["id"], "quote": s["quote"]} for s in out["statistics"] if s["id"] not in sent]}


def rejudge_input(record: dict, targets: list[dict]) -> list[dict]:
    """The re-judgment's targets (contract .6): each statistic with its quotes as the first judgment wrote them, why
    it is judged again and the facts the check found (candidates outside an ask, the axes in question)."""
    out = record["model_output"]
    by_sid = {d["statistic"]: d for d in record["decisions"]}
    rows = []
    for t in targets:
        sid = t["statistic"]
        stat = next(s for s in out["statistics"] if s["id"] == sid)
        d = by_sid[sid]
        rows.append({"id": sid, "quote": stat["quote"], "kind": t["kind"], "why": t.get("reasons") or [t["code"]],
                     "quotes": {k: [r["quote"] for r in out[k] if sid in r["for"]]
                                for k in ("splitters", "periods", "frequencies", "calculations")},
                     # contract .8: its structured period and frequency rows, and the code's problems with them
                     **({"conditions": {k: [r for r in out[k] if sid in r["for"]] for k in ("periods", "frequencies")},
                         "condition_problems": _row_problems(out, sid, record.get("as_of"),
                                                             (record.get("parse") or {}).get("answer_contract"))}
                        if _semantic(record) else {}),
                     "facts": ([d["period_reselection"]] if d.get("period_reselection") else []) +
                              ([record["recovery_identity_ambiguities"][sid]]
                               if sid in record.get("recovery_identity_ambiguities", {}) else []) +
                              [{k: v for k, v in f.items() if k in ("code", "axis", "candidates", "problems")}
                               for f in d.get("rejudge_targets") or []] +
                              [{"code": r["code"], **r["detail"]} for r in d.get("reasons") or []
                               if isinstance(r.get("detail"), dict) and
                               (r["code"] == "focus_catalog_condition_missing" or
                                (r["code"] == "focus_too_long" and r["detail"].get("scope") == "handoff_group"))],
                     **({"focus": {**d["focus"], "quotes": list(d["focus"]["quotes"])}}
                        if (d.get("focus") or {}).get("source") == "model_scope" else {}),
                     **({"search_words": t["search_words"]} if t.get("search_words") else {})})
    return rows


def unresolved_of(record: dict, only: list[str] | None = None) -> list[dict]:
    """The statistics the first judgment left missing (``only``: these), with the condition quotes it attached to each
    (as written by the model) and its search words: the re-judgment's ``unresolved`` input."""
    out = record["model_output"]
    rows = []
    for d in record["decisions"]:
        sid = d["statistic"]
        if only is not None and sid not in only:
            continue
        stat = next(s for s in out["statistics"] if s["id"] == sid)
        rows.append({"id": sid, "quote": stat["quote"], "name": stat["name"],
                     "search_words": list(d.get("search_words") or []),
                     **{k: [r["quote"] for r in out[k] if sid in r["for"]]
                        for k in ("splitters", "periods", "frequencies", "calculations")}})
    return rows


def research_words(record: dict) -> tuple[list[str], str | None]:
    """The model's search words of the statistics it left missing (some missing is enough, plan 56 order 9: a decided
    statistic is never redone, ``merge_rounds``), else why nothing is re-searched: ``first_round_error``,
    ``no_missing_statistic``, ``no_search_words``."""
    if record.get("error"):
        return [], "first_round_error"
    decisions = record.get("decisions") or []
    missing = [d for d in decisions if d["outcome"] == "not_in_pool"]
    if not missing:
        return [], "no_missing_statistic"
    words: list[str] = []
    for d in missing:
        for w in d.get("search_words") or []:
            w = (w or "").strip()
            if w and w not in words:
                words.append(w)
    return (words, None) if words else ([], "no_search_words")


def first_round_kind(record: dict) -> dict:
    """How a failed first round failed (plan 56 order 3): ``first_round_transport_error`` (the judge's call failed:
    it never opens the re-judgment), ``first_round_output_error`` (an answer came back but broke the output contract:
    a content error a correction could fix, ``correctable``) or ``first_round_input_error`` (the input was refused
    before any call). The re-search end stays ``first_round_error`` for all three; this says which."""
    error = record.get("error") or {}
    stage = error.get("stage")
    kind = {"judge": "first_round_transport_error", "output": "first_round_output_error"}.get(
        stage, "first_round_input_error")
    return {"kind": kind, "code": error.get("code"), "correctable": stage == "output"}


def node_research(state: TLState, config) -> dict:
    """The one re-search: the new pool and cards with their counts and input size, or the recorded reason it ends.
    Nothing is trimmed to fit: an input over the limit ends it. With a ledger (plan 56 order 3) the re-judgment is
    only read here (``available("a_self_rejudge")``); it is used right before the call is sent."""
    record = state["record"]
    targets = (record.get("rejudge") or {}).get("targets") or []
    corrections = [t for t in targets if t["kind"] == "correction" and t["statistic"]]
    # a first answer that broke the output contract: the whole question once more on the same cards (Codex 11-01)
    whole = [t for t in targets if t["kind"] == "correction" and t["statistic"] is None
             and t["code"] == "first_round_output_error"]
    words, why = research_words(record)
    row = {"version": RESEARCH_VERSION, "extra_cap": EXTRA_CAP, "payload_limit": K.PAYLOAD_LIMIT,
           "search_words": words, "status": "not_run", "end": why, "corrections": [t["statistic"] for t in corrections],
           "missing_read_as": "a re-search signal, not a confirmed absence"}
    if why == "first_round_error":
        row["first_round"] = first_round_kind(record)
    if why and not corrections and not whole:
        return {"research": row}
    layer, question = _layer(config), state["question"]
    pool, cards = state["pool"], state["cards"]
    research = []
    if not why:
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
        if row["tables_added"]:
            research = [t["statistic"] for t in targets if t["kind"] == "research"]
        elif not corrections:
            row["end"] = "no_new_candidates"
            return {"research": row}
        else:  # no new table: the corrections go on the first cards
            pool, cards = state["pool"], state["cards"]
    unresolved = unresolved_of(record, research) if research else None
    sent = set(research) | {t["statistic"] for t in corrections}
    try:
        if whole:  # no decided statistic and no model output to fix in parts: the first input again
            fixed, rows = None, None
            payload = K.build_payload(question, cards, None)
        else:
            fixed = rejudge_fixed(record, sent)
            rows = rejudge_input(record, [t for t in targets if t["statistic"] in sent])
            payload = K.build_payload(question, cards, unresolved, fixed=fixed, targets=rows)
    except K.ContractError as e:
        row.update(end=e.code, detail=e.detail)
        return {"research": row}
    row["payload_chars_after"] = len(K.canonical(payload))
    ledger = config["configurable"].get("ledger")
    budget = config["configurable"].get("research_budget")
    if ledger is not None and not ledger.available("a_self_rejudge") or budget is not None and not budget():
        row.update(end="rejudge_call_limit", **({"refusal": "slot_used"} if ledger is not None else {}))
        import copy

        exhausted = copy.deepcopy(record)
        _recovery(exhausted, exhausted.get("rejudge") or {}, "budget_exhausted")
        return {"research": row, "record": exhausted}
    record = {**record, "rejudge": {**record["rejudge"], "sent": sorted(sent), **({"whole": True} if whole else {})}}
    row.update(status="rejudging", end=None)
    first = {**{k: state.get(k) for k in ("pool", "cards", "payload", "response_text", "parsed")}, "record": record}
    return {"research": row, "first_round": first, "round": 2, "pool": pool, "cards": cards, "payload": None,
            "response_text": None, "parsed": None, "error": None, "record": None, "unresolved": unresolved,
            "rejudge_fixed": fixed, "rejudge_input": rows}


def node_research_end(state: TLState) -> dict:
    """How the re-judged round ended: ``still_missing`` (every target missing again), ``second_round_error`` or
    ``rejudged``; the record merges the second round into the first per statistic (``merge_rounds``). There is no
    third judgment."""
    second = state["record"]
    first = state["first_round"]["record"]
    sent = set(first["rejudge"].get("sent") or [])
    targets = first["rejudge"]["targets"] if first["rejudge"].get("whole") else \
        [t for t in first["rejudge"]["targets"] if t["statistic"] in sent]
    record = merge_rounds(first, second, targets)
    outcomes = [d["outcome"] for d in second.get("decisions") or []]
    wanted = {t["statistic"] for t in targets}
    left = [d["outcome"] for d in record["decisions"] if d["statistic"] in wanted]
    end = ("second_round_error" if second.get("error") else
           "still_missing" if left and all(o == "not_in_pool" for o in left) else "rejudged")
    return {"record": record, "second_round_record": second,
            "research": {**state["research"], "status": "rejudged", "end": end, "second_round_outcomes": outcomes}}


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

    from . import cards, check, contract, paths, periods, pool
    from .periods import core as claview_period

    if not _CATALOG_SHA:
        for name, path in (("search_catalog", paths.CATALOG_SEARCH), ("meta_raw", paths.CATALOG_META_RAW)):
            _CATALOG_SHA[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    policy = claview_period.default_policy()
    return {"versions": {"pool": pool.POOL_VERSION, "cards": cards.CARDS_VERSION,
                         "contract": contract.CONTRACT_VERSION, "prompt": contract.PROMPT_VERSION,
                         "check": check.CHECK_VERSION,
                         "periods": periods.PERIODS_VERSION, "graph": GRAPH_VERSION,
                         "ask_axes": cards.ASK["version"], "research": RESEARCH_VERSION,
                         "prompt_sha256": hashlib.sha256(K.prompt_text().encode("utf-8")).hexdigest()},
            "as_of": as_of, "catalog": dict(_CATALOG_SHA),
            "period_policy": {"version": policy.version, "sha256": policy.sha256},
            "model": {"model": settings.model, "effort": settings.effort,
                      "max_output_tokens": settings.max_output_tokens}}


def run(question: str, layer, judge, settings: K.Settings, as_of: str, research: bool = False,
        research_budget=None, research_judge=None, research_settings: K.Settings | None = None,
        ledger=None) -> TLState:
    """One question through the graph. ``judge(payload, request) -> text`` is the only model access. ``research``:
    allow the one re-search (default off: one call at most); ``research_budget() -> bool``: the runner's re-judge
    budget, asked once before a second call (None: no budget of its own); ``research_judge`` /
    ``research_settings``: the re-judgment's own judge and settings (None: the first ones); ``ledger``: the
    question's call ledger (``budget.Ledger``), read before a re-judgment (None: no ledger). The state carries
    ``run_info`` taken at run time."""
    graph = build_graph()
    state = graph.invoke({"question": question, "as_of": as_of, "research_mode": bool(research), "round": 1},
                         config={"configurable": {"layer": layer, "judge": judge, "settings": settings,
                                                  "research_budget": research_budget,
                                                  "research_judge": research_judge,
                                                  "research_settings": research_settings,
                                                  "ledger": ledger}})
    info = run_info(as_of, settings)
    # the contract the answers were written to and read with (the judge's own, else the current one): what a replay
    # reads them with again
    info["versions"]["contract"] = getattr(judge, "answer_contract", None) or UNDECLARED_CONTRACT
    if state.get("first_round"):
        info["research_model"] = run_info(as_of, research_settings or settings)["model"]
    return {**state, "run_info": info}
