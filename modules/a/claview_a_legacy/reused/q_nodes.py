"""Q1 (normalisation) and Q3 (explicit frequency) copied from the old A pipeline (commit 55fd41b).

The node bodies ``q1``/``q3`` and their helpers ``_to_span``/``_condition`` are unchanged; only the imports point at
the copies in this package and the ``runtime`` annotation no longer names the old LangGraph context type (neither
node reads ``runtime``). ``normalize`` and ``frequencies`` are the pure entry points for the new package.
"""

from __future__ import annotations

import unicodedata

from . import q_rules as rules
from .q_rules import nospace


# Source: modules/a/claview_aflow/node_common.py:23-26 (commit 55fd41b), copied verbatim.
def _to_span(offsets: list, start: int, end: int) -> list | None:
    if not offsets or start >= end or end > len(offsets):
        return None
    return [offsets[start], offsets[end - 1] + 1]


# Source: modules/a/claview_aflow/q_nodes.py:17-36 (commit 55fd41b), copied verbatim (runtime annotation dropped).
def q1(state, runtime=None) -> dict:
    """원문 보존, 정리본(NFKC·공백 하나), 공백 제거본, 정리본 → 원문 위치표."""
    question = state["question"]
    chars, offsets = [], []
    prev_space = True
    for i, ch in enumerate(question):
        for out in unicodedata.normalize("NFKC", ch):
            if out.isspace():
                if prev_space:
                    continue
                out, prev_space = " ", True
            else:
                prev_space = False
            chars.append(out)
            offsets.append(i)
    while chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    query = "".join(chars)
    return {"query": query, "query_nospace": nospace(query), "offsets": offsets}


# Source: modules/a/claview_aflow/q_nodes.py:46-49 (commit 55fd41b), copied verbatim.
def _condition(prefix: str, n: int, state, i: int, j: int, status: str, **fields) -> dict:
    span = _to_span(state["offsets"], i, j)
    quote = state["question"][span[0]:span[1]] if span else state["query"][i:j]
    return {"condition_id": f"{prefix}{n}", "status": status, "quote": quote, "span": span, **fields}


# Source: modules/a/claview_aflow/q_nodes.py:63-73 (commit 55fd41b), copied verbatim (runtime annotation dropped).
def q3(state, runtime=None) -> dict:
    """명시된 주기만 (v1.8 R5 사전, rules.FREQUENCY_PATTERNS). 반복어(매달·분기마다)는 넣고 기간 안의 말은 뺀다."""
    view = {"question": state["question"], "query": state["query"], "offsets": state["offsets"]}
    state["query_nospace"]
    rows = [
        # 기간 끝 반복(분기말마다)은 끝 시점을 남긴다. B가 말일 값을 고를 때 쓴다 (Q3·Q4 공통 규칙)
        _condition("F", n, view, i, j, "explicit", value=value,
                   **({"anchor": "end"} if rules.frequency_anchor(state["query"], i, j) else {}))
        for n, (value, i, j) in enumerate(rules.find_frequencies(state["query"]), 1)
    ]
    return {"cond_frequency": rows}


# ---------------------------------------------------------------- pure entry points (new in claview_a_legacy)


def normalize(question: str) -> dict:
    """Q1 as a pure function: question -> {query, query_nospace, offsets}."""
    return q1({"question": question})


def frequencies(question: str, query: str, offsets: list[int]) -> list[dict]:
    """Q3 as a pure function: Q1 output for one question -> frequency condition rows."""
    state = {"question": question, "query": query, "offsets": offsets, "query_nospace": nospace(query)}
    return q3(state)["cond_frequency"]
