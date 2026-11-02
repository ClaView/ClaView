"""Question-source checks shared by A decisions, scopes and saved-record presentation."""

from __future__ import annotations

from .search.tables import nospace


def quote_in_question(quote: str, question: str) -> bool:
    """Ignore whitespace only when checking provenance; leave the supplied quote unchanged."""
    return bool(quote.strip()) and nospace(quote) in nospace(question)
