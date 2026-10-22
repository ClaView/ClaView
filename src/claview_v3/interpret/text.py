"""normalize_text (detail 2.1): the reused Q1 turned into the new ``text`` shape. Implemented in stretch 1."""

from __future__ import annotations

from ..contract.shapes import TextView
from ..reused.q_nodes import normalize


def normalize_text(question: str) -> TextView:
    """Raw question kept as given, plus the reused Q1 normalisation (NFKC, one space), no-space text and offsets."""
    q1 = normalize(question)
    return {"raw": question, "norm": q1["query"], "nospace": q1["query_nospace"], "offsets": list(q1["offsets"])}
