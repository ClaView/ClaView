"""Scripted judges for wiring tests and the screen demo: no model is called.

A script names tables by their real IDs and leaves the card evidence out; ``ScriptJudge`` fills each choice and ask
option with title evidence (the card name the payload shows for that table) and the empty keys of the output
contract. What it returns says nothing about a model: it only drives the path (selection, asks, missing, failure).
``{"judge_error": code}`` in a script raises the judge's expected failure (``JudgeError``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from claview_a.contract import output_schema
from claview_a.graph import JudgeError

# the contract of the scripts written before contract .4 (plan 56 order 6-3)
LEGACY_SCRIPT_CONTRACT = "tl-contract-2026-10-28.3"

KEYS = (
    "statistics",
    "splitters",
    "periods",
    "frequencies",
    "calculations",
    "choices",
    "asks",
    "missing",
    "alternatives",
)
_CARD = re.compile(r"^\[(g\d+)\] (.+)$")
_ROW = re.compile(r"^(t\d+) (\S+) \|")


def card_index(cards_text: str) -> dict[str, tuple[str, str]]:
    """Table ID -> (card reference ``gN.tN``, card name) as the payload's cards show them."""
    out, card, name = {}, None, None
    for line in cards_text.splitlines():
        if m := _CARD.match(line):
            card, name = m.group(1), m.group(2)
        elif card and (m := _ROW.match(line)):
            out.setdefault(m.group(2), (f"{card}.{m.group(1)}", name))
    return out


def complete(out: dict, payload: dict, contract: str = LEGACY_SCRIPT_CONTRACT) -> dict:
    """The script's output with every key of ``contract`` and title evidence where none was written."""
    index = card_index(payload["cards"])
    keys = output_schema(contract)["properties"]
    full = {k: json.loads(json.dumps(out.get(k, []), ensure_ascii=False)) for k in keys}

    def evidence(table: str) -> list[dict]:
        return [{"kind": "title", "text": index[table][1]}] if table in index else [{"kind": "title", "text": table}]

    for c in full["choices"]:
        c.setdefault("evidence", evidence(c["table"]))
    for a in full["asks"]:
        for o in a["options"]:
            o.setdefault("evidence", evidence(o["table"]))
    for a in full["alternatives"]:
        a.setdefault("why", "script")
    return full


class ScriptJudge:
    """``script(payload) -> output | None``: the output for this payload (table IDs, no evidence needed), or None
    when the script has none (then ``LookupError``, never a guess)."""

    def __init__(self, script: Callable[[dict], dict | None], answer_contract: str = LEGACY_SCRIPT_CONTRACT):
        # the contract its scripted answers are written to: the old shape (no polarity, period meaning or
        # aggregation fields) unless a script of the current contract says so (plan 56 order 6-3)
        self.script, self.calls, self.models = script, 0, []
        self.answer_contract = answer_contract

    def __call__(self, payload: dict, request: dict) -> str:
        self.calls += 1
        self.models.append(request["model"])
        out = self.script(payload)
        if out is None:
            raise LookupError("no scripted answer for this question")
        if "judge_error" in out:
            raise JudgeError(out["judge_error"])
        return json.dumps(complete(out, payload, self.answer_contract), ensure_ascii=False)


def by_question(answers: dict[str, dict], fallback: Callable[[dict], dict | None] | None = None):
    """A script from question text -> output (the demo's scenario file); ``fallback`` for other questions."""

    def script(payload: dict) -> dict | None:
        got = answers.get(payload["question"])
        return got if got is not None else (fallback(payload) if fallback else None)

    return script


def first_card(payload: dict) -> dict:
    """The fake judge's answer as a script: the first table of the first card, or missing when there is none."""
    index = card_index(payload["cards"])
    out = {"statistics": [{"id": "s1", "quote": payload["question"], "name": "synthetic"}]}
    if index:
        table = next(iter(index))
        out["choices"] = [{"for": ["s1"], "table": table}]
    else:
        out["missing"] = [{"for": ["s1"], "search_words": ["synthetic"]}]
    return out
