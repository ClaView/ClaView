"""Check the scope size of statistics that the existing B handoff can merge.

Only sendable groups share a focus limit. Different conditions, pending answers and edition splits keep their
existing handoff restrictions; this check neither merges them nor changes their conditions or scope text.
"""

from __future__ import annotations

import copy

from .focus import FOCUS_JOIN, MAX_FOCUS_CHARS


def group_overflows(decisions: list[dict]) -> list[dict]:
    """The same-table groups whose unchanged, deduplicated scopes do not fit B's focus field.

Match the handoff boundary: group by table first, reject the whole group if its assignments or answers differ,
then reject all candidates if their covered months differ. Calculation differences are notes, not blockers.
"""
    groups: dict[str, list[dict]] = {}
    for d in decisions:
        tables = d.get("final_tables") or []
        if d.get("outcome") != "selected" or d.get("asks") or d.get("condition_asks") or len(tables) != 1:
            continue
        if not isinstance(tables[0], dict) or len(tables[0].get("period") or []) != 1:
            continue
        groups.setdefault(tables[0]["table_id"], []).append(d)
    candidates = []
    for table, members in groups.items():
        assignments = set()
        for d in members:
            t = d["final_tables"][0]
            span = t["period"][0]
            answers = tuple(sorted((a.get("option_id") or "", a.get("value") or "", a.get("text") or "")
                                   for a in d.get("condition_answers") or []))
            assignments.add((t.get("frequency") or "", span["start"], span["end"], answers))
        if len(assignments) == 1:
            candidates.append((table, members))
    windows = {(members[0]["final_tables"][0]["period"][0]["start"],
                members[0]["final_tables"][0]["period"][0]["end"]) for _, members in candidates}
    if len(windows) > 1:
        return []
    overflows = []
    for table, members in candidates:
        if len(members) < 2:
            continue
        texts = [(d.get("focus") or {}).get("text", d["quote"]).strip() for d in members]
        joined = FOCUS_JOIN.join(dict.fromkeys(text for text in texts if text))
        if len(joined) > MAX_FOCUS_CHARS:
            overflows.append({"scope": "handoff_group", "table_id": table,
                              "statistics": [d["statistic"] for d in members],
                              "members": [{"statistic": d["statistic"], "text": text}
                                          for d, text in zip(members, texts)],
                              "chars": len(joined), "limit": MAX_FOCUS_CHARS, "separator": FOCUS_JOIN})
    return overflows


def check_groups(decisions: list[dict], mutable: set[str] | None = None) -> list[dict]:
    """Fail only the allowed members of overflowing groups; fixed decisions of a recovery never change."""
    by_sid = {d["statistic"]: d for d in decisions}
    changed = []
    for detail in group_overflows(decisions):
        for sid in detail["statistics"]:
            if mutable is not None and sid not in mutable:
                continue
            d = by_sid[sid]
            d["reasons"].append({"code": "focus_too_long", "detail": copy.deepcopy(detail)})
            d["outcome"] = "failed"
            changed.append(d)
    return changed
