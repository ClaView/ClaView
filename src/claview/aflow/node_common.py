"""Helpers shared by several A node stages (span mapping, query tokens, search channels).

Moved verbatim from nodes.py (node-structure N5); nodes.py assembles NODES."""

from __future__ import annotations

import re

from langgraph.runtime import Runtime

from .contract import ARunContext


Ctx = Runtime[ARunContext]
CHANNEL_CAP = 10


def _tokens(query: str) -> list[tuple[str, int, int]]:
    """정리본에서 공백으로 나눈 낱말과 정리본 위치."""
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", query)]


def _to_span(offsets: list, start: int, end: int) -> list | None:
    if not offsets or start >= end or end > len(offsets):
        return None
    return [offsets[start], offsets[end - 1] + 1]


def _channel(per_query: dict) -> dict:
    """검색식별 결과를 채널 하나로 모은다. 채널 안 순위는 다시 매긴다 (v1.8 S1·S2)."""
    best: dict[str, float] = {}
    qids: dict[str, list] = {}
    for qid, rows in per_query.items():
        for row in rows:
            tid = row["table_id"]
            best[tid] = max(best.get(tid, 0.0), row["raw_score"])
            qids.setdefault(tid, []).append(qid)
    order = sorted(best, key=lambda t: (-best[t], t))
    ranked_full = [
        {"table_id": t, "score": best[t], "rank": i + 1, "query_ids": qids[t]} for i, t in enumerate(order)
    ]
    return {"per_query": per_query, "ranked_full": ranked_full, "merged": ranked_full[:CHANNEL_CAP]}


def _rank_rows(scores: dict[str, float]) -> list:
    order = sorted((t for t, s in scores.items() if s > 0), key=lambda t: (-scores[t], t))
    return [{"table_id": t, "raw_score": scores[t], "rank": i + 1} for i, t in enumerate(order)]
