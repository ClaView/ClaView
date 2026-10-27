"""The one reading of a proposal ref's source range (plan 19 stage 3), shared by the verification
(``decompose_verify``), the settling (``decompose_settle``) and the selection refs (``decompose_run``).

- ``sub_range``: the raw [start, end) of a subquote found exactly once in its one segment, the spaces around it left
  out (the same span everywhere: verification, fragments, quotes and selection refs).
- ``span``: a whole ref spans its segments; a subquote its ``sub_range`` (a subquote that is only spaces, which the
  verification lets through without a name, keeps its raw position).
- ``name_mentions``: the name mentions a ref quotes, and so the senses and catalog rows it may carry:
  - a subquote: only the name mentions written exactly over the subquote (by their raw span, ``local["mention_spans"]``;
    without spans, those whose own catalog name is the subquote). The sense of the whole name is never handed down to
    a part of it, and the rows of another part of the same segment are never taken;
  - a whole ref: the name mentions over its segments, except an inner mention (one that lies strictly inside one
    segment: a name another token took over, an X-byeol word inside a condition). Such a part is quoted by a
    subquote only, so a whole ref never carries a sense that only a part of it has. Without spans no mention can be
    told inner (every mention over the segments is read, as before).
- ``sense_ids``: the chosen sense, else every kept sense of those mentions; ``candidate_rows``: the raw candidate rows
  of those mentions.

Primary and alternative readings are read by the same rule. Nothing here reads the catalog.

The range rule itself is a set of pure functions (``core_range``, ``inner_span``, ``whole_ref_quotes``,
``subquote_quotes``) shared with the final reference check (``contract/check._ref_problems``, plan 19 decision of
2026-10-21: the check reads a mention's own ``Mention.span`` by the same rule the generator reads here).
"""

from __future__ import annotations

from ..catalog.text import index_key
from ..contract.shapes import Mention, Segment


def occurrences(hay: str, needle: str) -> int:
    """Occurrences of ``needle`` in ``hay``, overlapping ones counted."""
    if not needle:
        return 0
    n, i = 0, hay.find(needle)
    while i >= 0:
        n += 1
        i = hay.find(needle, i + 1)
    return n


# ---------------------------------------------------------------- the range rule (pure; also read by the check)


def core_range(start: int, quote: str) -> tuple[int, int] | None:
    """Raw [start, end) of ``quote`` written at raw position ``start``, the spaces around it left out (None: only
    spaces)."""
    lead = len(quote) - len(quote.lstrip())
    core = quote.strip()
    return (start + lead, start + lead + len(core)) if core else None


def span_pair(span) -> tuple[int, int] | None:
    """(start, end) of a raw span written as [start, end) of two integers with 0 <= start < end; None for anything
    else (a malformed span is no range: the functions below never index it)."""
    if isinstance(span, (list, tuple)) and len(span) == 2 \
            and all(isinstance(x, int) and not isinstance(x, bool) for x in span) and 0 <= span[0] < span[1]:
        return span[0], span[1]
    return None


def inner_span(segment_ids: list[str], span, seg_spans: dict) -> bool:
    """A name mention strictly inside one segment: one segment, and its raw ``span`` is not that segment's span
    (``seg_spans``: segment ID -> [start, end)). Without a span, or with a malformed one (``span_pair``): never."""
    pair = span_pair(span)
    if pair is None or len(segment_ids) != 1 or segment_ids[0] not in seg_spans:
        return False
    lo, hi = seg_spans[segment_ids[0]]
    return pair != (lo, hi)


def whole_ref_quotes(segment_ids: list[str], span, ref_segment_ids, seg_spans: dict) -> bool:
    """A whole ref over ``ref_segment_ids`` quotes this name mention: the mention lies over those segments and is not
    an inner mention (``inner_span``). A malformed span is quoted by nothing (never read as no span)."""
    if span is not None and span_pair(span) is None:
        return False
    return bool(segment_ids) and set(segment_ids) <= set(ref_segment_ids) \
        and not inner_span(segment_ids, span, seg_spans)


def subquote_quotes(segment_ids: list[str], span, sid: str, rng) -> bool | None:
    """A subquote over the one segment ``sid`` at raw range ``rng`` quotes this name mention: the mention is over
    that segment only and its raw ``span`` is exactly ``rng``. None: the mention has no span (told otherwise). A
    malformed span (``span_pair``) quotes nothing."""
    if segment_ids != [sid]:
        return False
    if span is None:
        return None
    pair = span_pair(span)
    return pair is not None and pair == (rng[0], rng[1])


class RefReader:
    """Source ranges of refs over one question's segments and name mentions (module docstring)."""

    def __init__(self, segments: list[Segment], mentions: list[Mention], local: dict):
        self.seg = {s["segment_id"]: s for s in segments}
        self.names = [m for m in mentions if m["kind"] == "name"]
        self.spans = local.get("mention_spans") or {}
        self.kept = local["mention_kept"]
        self.senses = local["senses"]

    # -- ranges

    def sub_range(self, ref: dict) -> tuple[int, int] | None:
        """Raw [start, end) of a subquote found exactly once in its one segment (spaces around it left out)."""
        sq = ref["subquote"]
        if sq is None or len(ref["segment_ids"]) != 1 or ref["segment_ids"][0] not in self.seg:
            return None
        seg = self.seg[ref["segment_ids"][0]]
        if occurrences(seg["quote"], sq) != 1:
            return None
        return core_range(seg["span"][0] + seg["quote"].find(sq), sq)

    def span(self, ref: dict) -> list[int]:
        first, last = self.seg[ref["segment_ids"][0]], self.seg[ref["segment_ids"][-1]]
        if ref["subquote"] is None:
            return [first["span"][0], last["span"][1]]
        rng = self.sub_range(ref)
        if rng is not None:
            return [rng[0], rng[1]]
        a = first["span"][0] + max(first["quote"].find(ref["subquote"]), 0)
        return [a, a + len(ref["subquote"])]

    def mention_span(self, m: Mention) -> tuple[int, int]:
        sp = self.spans.get(m["mention_id"])
        if sp is not None:
            return sp[0], sp[1]
        return self.seg[m["segment_ids"][0]]["span"][0], self.seg[m["segment_ids"][-1]]["span"][1]

    def inner(self, m: Mention) -> bool:
        """A name mention strictly inside one segment (by its raw span; without a span: never)."""
        return inner_span(m["segment_ids"], self.spans.get(m["mention_id"]), self._seg_spans())

    def _seg_spans(self) -> dict:
        return {sid: s["span"] for sid, s in self.seg.items()}

    # -- what a ref quotes

    def name_mentions(self, ref: dict) -> list[Mention]:
        if ref["subquote"] is None:
            seg_spans = self._seg_spans()
            return [m for m in self.names
                    if whole_ref_quotes(m["segment_ids"], self.spans.get(m["mention_id"]), ref["segment_ids"],
                                        seg_spans)]
        rng = self.sub_range(ref)
        if rng is None:
            return []
        sid = ref["segment_ids"][0]
        out = []
        for m in self.names:
            exact = subquote_quotes(m["segment_ids"], self.spans.get(m["mention_id"]), sid, rng)
            if exact:
                out.append(m)
            elif exact is None and any(index_key(self.senses[z]["name"]) == index_key(ref["subquote"])
                                       for z in self.kept.get(m["mention_id"], [])):
                out.append(m)
        return out

    def sense_ids(self, ref: dict) -> list[str]:
        if ref["sense_id"] is not None:
            return [ref["sense_id"]]
        return list(dict.fromkeys(z for m in self.name_mentions(ref) for z in self.kept.get(m["mention_id"], [])))

    def candidate_rows(self, ref: dict) -> list[dict]:
        return [c for m in self.name_mentions(ref) for c in m["candidates"]]
