"""The adopted model reading's references, shared by search, facts and verification.

None means the legacy/rules path; an empty list means a model requirement has no references of that kind. Never
fall back from empty model references to its broad quote/span.
"""

from __future__ import annotations


def adopted(analysis: dict) -> bool:
    return (analysis.get("decomposition") or {}).get("applied_source") == "model"


def references(analysis: dict, rid: str) -> list[dict] | None:
    if not adopted(analysis):
        return None
    return (analysis["decomposition"].get("selection_refs") or {}).get(rid, [])


def search_qualifiers(analysis: dict, rid: str) -> list[dict]:
    """Positive, locally bound basis/axis references for discovery, never for proof.

    Reuse the settled condition attribution. A condition assigned to another requirement,
    unresolved or name content is not a local search preference. Negative/excluded spans
    and negated fragments veto an overlapping qualifier even if a positive row also overlaps.
    With no extracted condition, the accepted raw reference remains a soft search clue.
    """
    refs = references(analysis, rid)
    if refs is None:
        return []
    req = next(r for r in analysis["interpretation"]["requirements"] if r["id"] == rid)
    local = set(req.get("condition_ids") or []) | set(req.get("shared_condition_ids") or [])
    ambiguous = set(req.get("ambiguous_condition_ids") or [])
    conditions = analysis.get("conditions") or []
    negative_spans = [f["span"] for f in analysis.get("fragments") or []
                      if f.get("phrase") == "negated" and f.get("span")]

    def overlaps(a, b):
        return bool(a and b and a[0] < b[1] and b[0] < a[1])

    out, seen = [], set()
    for ref in refs:
        if ref.get("place") not in ("basis", "breakdown_axis") or not ref.get("quote"):
            continue
        span = ref.get("span")
        if not span or any(overlaps(span, other) for other in negative_spans):
            continue
        matching = [c for c in conditions if overlaps(span, c.get("span"))]
        if any(c.get("kind") == "exclude" or (c.get("attrs") or {}).get("polarity") == "-"
               or c["id"] not in local or c["id"] in ambiguous for c in matching):
            continue
        key = (tuple(span), ref["quote"], ref.get("name"),
               tuple(sorted((c.get("table_id") or "", c.get("obj_id") or "",
                             c.get("value_id") or "", c.get("path") or "")
                            for c in ref.get("candidates") or [])))
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def qualifier_words(analysis: dict, rid: str) -> list[str]:
    """Original wording in source order; overlapping duplicate roles do not add weight."""
    spans = sorted({tuple(r["span"]) for r in search_qualifiers(analysis, rid)})
    merged = []
    for lo, hi in spans:
        if merged and lo < merged[-1][1]:
            merged[-1][1] = max(hi, merged[-1][1])
        else:
            merged.append([lo, hi])
    raw = analysis["text"]["raw"]
    return list(dict.fromkeys(raw[lo:hi] for lo, hi in merged))


def spans(analysis: dict, rid: str) -> list[list[int]] | None:
    refs = references(analysis, rid)
    if refs is None:
        return None
    return [r["span"] for r in refs if r["place"] != "breakdown_marker"]


def fragment_ids(analysis: dict, rid: str) -> list[str] | None:
    ranges = spans(analysis, rid)
    if ranges is None:
        return None
    return [f["id"] for f in analysis.get("fragments") or []
            if any(a < f["span"][1] and f["span"][0] < b for a, b in ranges)]


def words(analysis: dict, rid: str, remove_conditions: bool = False) -> str | None:
    ranges = spans(analysis, rid)
    if ranges is None:
        return None
    raw = analysis["text"]["raw"]
    removed = [c["span"] for c in analysis.get("conditions") or []] if remove_conditions else []
    return " ".join("".join(" " if any(lo <= i < hi for lo, hi in removed) else raw[i]
                           for i in range(a, b)) for a, b in sorted({tuple(x) for x in ranges}))
