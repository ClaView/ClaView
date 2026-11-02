"""Literal catalog selectors are evidence, not a natural-language intent parser.

Numbered labels on an enumerated axis can name a classification rather than a query date. Expose exact question
matches even when a large axis is not listed on the card. Never select a value or invent its polarity here.
"""

from __future__ import annotations

import re


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def contains(text: str, value: str) -> bool:
    """Whitespace is immaterial, but a label's number must not match inside a different number."""
    value = compact(value)
    left = r"(?<!\d)" if value[:1].isdigit() else ""
    right = r"(?!\d)" if value[-1:].isdigit() else ""
    return bool(value and re.search(left + re.escape(value) + right, compact(text)))


def matches(question: str, view) -> list[dict]:
    """Known numeric label families only; bare years, numbers and unlisted values prove no selector."""
    rows = []
    for obj, axis in getattr(view, "dims", None) or []:
        groups: dict[str, list] = {}
        for item in getattr(view, "items", []):
            label = compact(item.name_raw or "")
            skeleton = re.sub(r"\d+", "#", label)
            # Require an actual catalog family, not a year-like word inferred from the question.
            if getattr(item, "obj_id", None) == obj and re.search(r"\d", label) and \
                    len(re.sub(r"[\W\d_]", "", label)) >= 2:
                groups.setdefault(skeleton, []).append(item)
        for items in groups.values():
            if len({compact(it.name_raw) for it in items}) < 2:
                continue
            for item in items:
                if contains(question, item.name_raw):
                    rows.append({"table_id": view.table_id, "axis": axis, "value": item.name_raw,
                                 "item_id": item.item_id})
    return rows


def missing_scope(decision: dict, candidates: list[dict], source: str) -> list[dict]:
    """A matched selector must remain in the scope; its meaning/polarity is still the model's responsibility.

For several statistics only the statistic's own quote establishes ownership. A one-statistic request may use the
whole question. This deliberately does not guess ownership of qualifiers omitted by a multi-statistic answer.
"""
    focus = decision.get("focus") or {}
    if focus.get("source") != "model_scope":
        return []
    tables = {row["table_id"] for row in decision.get("final_tables") or []}
    return [row for row in candidates if row["table_id"] in tables and contains(source, row["value"])
            and not contains(focus.get("text", ""), row["value"])]
