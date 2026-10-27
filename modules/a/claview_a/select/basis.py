"""Basis occurrences shared by evidence, facts and table signatures.

Vocabulary belongs to the existing basis dictionary. Offsets refer to the supplied text.
Within an axis, a longer occurrence protects its span from a shorter, embedded value.
Independent occurrences, including an opposite value elsewhere, remain visible.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .. import paths
from ..interpret.condition_rules import BASIS_AXES


@lru_cache(maxsize=1)
def _aliases() -> dict:
    return json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8")).get("basis_aliases", {})


def occurrences(text: str, axis: str, extra: str | None = None) -> list[dict]:
    positions = [i for i, char in enumerate(text or "") if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    vocabulary = dict(BASIS_AXES.get(axis, {}))
    for value, forms in _aliases().get(axis, {}).items():
        vocabulary[value] = (*vocabulary.get(value, ()), *forms)
    if extra and extra not in vocabulary:
        vocabulary[extra] = (extra,)
    found = []
    for value, forms in vocabulary.items():
        for form in forms:
            word = "".join(form.split())
            if not word:
                continue
            start = compact.find(word)
            while start >= 0:
                end = start + len(word)
                found.append({"value": value, "form": form,
                              "span": [positions[start], positions[end - 1] + 1],
                              "_compact": (start, end)})
                start = compact.find(word, start + 1)
    kept, seen = [], set()
    for row in sorted(found, key=lambda r: (r["_compact"][0], -r["_compact"][1], r["value"])):
        lo, hi = row["_compact"]
        if any(a <= lo and hi <= b and b - a > hi - lo
               for a, b in (other["_compact"] for other in found)):
            continue
        key = (row["value"], *row["span"])
        if key not in seen:
            kept.append({k: v for k, v in row.items() if k != "_compact"})
            seen.add(key)
    return kept


def values(text: str, axis: str, extra: str | None = None) -> set[str]:
    return {row["value"] for row in occurrences(text, axis, extra)}
