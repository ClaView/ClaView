"""Fact checks for the model's table-specific scope, distinct from an original statistic quote.

Source quotes establish provenance, not the semantic correctness of the model's summary. Nothing here chooses
items, infers a scope from names, or turns card evidence into the list of series B should select.
"""

from __future__ import annotations

from .quotes import quote_in_question

MAX_FOCUS_CHARS = 100
FOCUS_JOIN = " / "


def check_scope(question: str, row: dict) -> tuple[dict, list[dict]]:
    """Keep the model's text and source excerpts; reject unverifiable sources or an unsendable scope."""
    text, quotes = row["text"], list(row["quotes"])
    reasons = []
    absent = [quote for quote in quotes if not quote_in_question(quote, question)]
    if absent:
        reasons.append({"code": "focus_quote_not_in_question", "detail": {"quotes": absent}})
    if len(text) > MAX_FOCUS_CHARS:
        reasons.append({"code": "focus_too_long", "detail": {"chars": len(text), "limit": MAX_FOCUS_CHARS}})
    return {"text": text, "quotes": quotes, "source": "model_scope",
            "status": "invalid" if reasons else "grounded"}, reasons


def legacy_scope(quote: str) -> dict:
    """An old answer has no separate scope judgement; preserve its original quote without inventing one."""
    return {"text": quote, "quotes": [quote], "source": "legacy_quote", "status": "verified_quote_only"}
