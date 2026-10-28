"""Append-only call reservations and response bodies for development replay.

Reservations survive crashes and count against the batch cap even without a response.
A short exclusive lock serialises reservations; an abandoned lock requires human review.
There is no implicit retry, overwrite, or live fallback from an archive miss.

Plan 20 (Q2): rows are written in version 2 with ``input_binding`` (the digest of the raw mapping behind the payload:
sense rows, scope rows and signatures, mention scopes, relation evidence and rows, omitted scopes). Only such a row
with a matching binding is replayed; a version 1 row is kept as it is and only read by the offline re-verification
(``development_reverify``), never replayed.
"""

from __future__ import annotations
import json


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
