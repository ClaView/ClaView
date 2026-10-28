"""build_mentions and build_ref_map (plan 15 r3, 3.2-3.3 and appendix A 3).

A mention is one semantic occurrence over contiguous segments; mentions may overlap and every one is kept:

- ``name``: every catalog-name token, every family qualifier, and every name a longer condition or qualifier took
  over (the token layer keeps those in ``used_names``: copying the top-level tokens alone would lose them); an X-byeol
  word (a condition or a word) that names classification axes. Candidates come from the occurrence layer
  (``catalog.occurrences``): one row per table, axis and value, never collapsed (``terms`` is not read: it drops the
  item IDs); a name the span layer found through its dot-free form is looked up by that key (plan 19 stage 4,
  ``written_key``: "A·B" gets the candidates of "A B");
- ``condition``: every condition of 2.2 with its existing ID (conditions inside a name included); a relation or
  function condition also carries the marker class of its words;
- ``marker``: a list, divide, negation, exclusion or relation word of the lexicon, a list middle dot, a joiner
  particle written against a name or condition between two nominals (plan 19 stage 4, ``joiner_list_marks``: not
  after a predicate stem nor before a verb, request word or marker). Only that a marker is written is recorded: no
  scope;
- ``request``: a run of request words (with the spaces between them).

``overlaps`` lists the other mentions that share a segment. IDs are m1, m2, ... in text order (start, then the longer
first, then the kind order name, condition, marker, request). ``span`` (plan 19, 2026-10-21) is the mention's raw
[start, end), the same value ``mention_spans`` gives; it is record only (the payload does not send it).

``build_ref_map``: segment -> mentions, segment <-> 2.3 fragments by span (a fragment is a compatibility view), and
mention -> fragments over its segments.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from ..catalog.occurrences import merge, occurrences_of
from ..catalog.text import index_key
from ..contract.shapes import Condition, Fragment, Mention, RefMap, Segment, TextView
from . import lexicon as LX
from .segments import is_list_dot, marker_of_condition, word_marker
from .tokens import MARKER_KINDS, split_ending, tokenize

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

KIND_ORDER = {"name": 0, "condition": 1, "marker": 2, "request": 3}


def _overlap(a: int, b: int, x: int, y: int) -> bool:
    return x < b and a < y


class _Rows:
    """Mention rows before numbering: (start, end, kind) -> condition IDs, marker, candidates."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    def add(self, a: int, b: int, kind: str, condition_ids=(), marker=None, candidates=()) -> None:
        if b <= a:
            return
        key = (a, b, kind, tuple(condition_ids), marker)
        row = self.rows.setdefault(key, {"start": a, "end": b, "kind": kind, "condition_ids": list(condition_ids),
                                         "marker": marker, "candidates": []})
        row["candidates"] = merge(row["candidates"] + [dict(c) for c in candidates])


_DOT_CHARS = frozenset(LX.DOTS) | frozenset(unicodedata.normalize("NFKC", d) for d in LX.DOTS)


def _undotted(k: str) -> str:
    return "".join(ch for ch in k if ch not in _DOT_CHARS)


def written_key(quote: str, key: str | None) -> str:
    """The text a written name is looked up by. Plan 19 stage 4: the name-span layer finds a name skipping list
    middle dots (plan 14 I8: "A·B" is read as the catalog's "A B", and the other way round), so when the written
    words differ from the span's key only by middle dots (and spaces) the key is looked up: the same candidates as
    the name written the catalog's way. Otherwise the written words, as before."""
    if key and index_key(quote) != key and _undotted(index_key(quote)) == _undotted(key):
        if any(ch in _DOT_CHARS for ch in quote):
            # each written middle dot read as a space: the catalog's own spelling of the name, so the match keeps the
            # path the spaced form gets (the bare key reaches it only as a looser lexical match)
            return " ".join("".join(" " if ch in _DOT_CHARS else ch for ch in quote).split())
        return key
    return quote


def _name_candidates(occ, quote: str, lookup: str | None, alias, key: str | None = None) -> list[dict]:
    """Candidates of a name as the token layer read it: an alias carries its own (a guess), a completed list member
    or an and-name tail is read through its catalog name (a guess unless it is the written words); a name found
    through its dot-free form is read through the key it was found by (``written_key``)."""
    if occ is None:
        return []
    if alias:
        return merge([dict(r, path="lexical") for r in occ.expand(alias)])
    # a lookup that differs from the written words only by list middle dots is the same name, not a guess
    guess = lookup is not None and _undotted(index_key(lookup)) != _undotted(index_key(quote))
    return occ.lookup(lookup or written_key(quote, key), guess=guess)


def _nominal(t) -> bool:
    """A token that can be a list member after a joiner particle: a name, a qualifier, a condition that is no relation,
    function or display word, or an unknown (or catalog basis) word that is no marker or request word."""
    if t is None:
        return False
    if t.kind in ("name", "qual"):
        return True
    if t.kind == "cond":
        return t.cond_kind not in MARKER_KINDS
    if t.kind == "word":
        return t.label in ("unknown", "basis_word") and word_marker(t.quote) is None and t.request_form is None
    return False


def _predicate_joiner(t, joiner: str) -> bool:
    """The joiner written against a token is a verb ending there: the token's words with the joiner's front part make
    a predicate stem of the lexicon (connective stems, ``"<display word>" + "하" + "고"``)."""
    body = index_key(t.quote)
    return any(joiner.endswith(e) and len(joiner) > len(e) and body + joiner[:-len(e)] in LX.CONNECTIVE_STEMS
               for e in LX.CONNECTIVE_ENDINGS)


def joiner_list_marks(toks: list, raw: str) -> list[tuple[int, int]]:
    """[start, end) of the joiner particles that mark a list: written against a name, condition or qualifier token
    that is no predicate (``_predicate_joiner``), with a nominal token right after it (``_nominal``). Plan 19 stage 4:
    a joiner after a predicate stem or before a verb, a request word, a marker or nothing ("비교하고 싶어", "X를
    비교하고 Y도 보여줘") marks no list; the lexicon and the rules path are unchanged."""
    out = []
    for i, t in enumerate(toks):
        if t.kind not in ("name", "cond", "qual") or t.send <= t.end:
            continue
        joiner = index_key(raw[t.end:t.send])
        if joiner not in LX.JOINERS or _predicate_joiner(t, joiner):
            continue
        if _nominal(toks[i + 1] if i + 1 < len(toks) else None):
            out.append((t.end, t.send))
    return out


def _axis_word(text: str) -> str:
    """An X-byeol word without the ending written against it ("가상규모별로" -> 가상규모별)."""
    return split_ending(index_key(text))[0]


def build_mentions(text: TextView, conditions: list[Condition], segments: list[Segment],
                   layer: CatalogLayer | None = None) -> list[Mention]:
    return _number(_collect(text, conditions, segments, layer), segments)


def mention_spans(text: TextView, conditions: list[Condition], segments: list[Segment],
                  layer: CatalogLayer | None = None) -> dict[str, list[int]]:
    """Mention ID -> its [start, end) in the raw question, numbered as ``build_mentions`` numbers them (the same
    inputs give the same IDs). A mention inside one segment (a name a longer token took over) shares the segment ID
    with the segment's own mention; only the span tells them apart (plan 15 r3 3.4: subquote positions)."""
    return {f"m{n}": [r["start"], r["end"]] for n, r in enumerate(_ordered(_collect(text, conditions, segments,
                                                                                     layer)), start=1)}


def _collect(text: TextView, conditions: list[Condition], segments: list[Segment],
             layer: CatalogLayer | None) -> _Rows:
    raw = text["raw"]
    toks, _ = tokenize(text, conditions, layer)
    occ = occurrences_of(layer) if layer is not None else None
    rows = _Rows()
    # ---- names (top-level tokens, qualifiers, names under a longer condition or qualifier)
    for t in toks:
        if t.kind == "name":
            rows.add(t.start, t.end, "name", candidates=_name_candidates(occ, t.quote, t.lookup, t.alias, t.key))
        elif t.kind == "qual":
            cands = (occ.qualifier(t.key) + occ.lookup(t.quote)) if occ is not None else []
            rows.add(t.start, t.end, "name", candidates=cands)
        for s in t.used_names:
            rows.add(s["start"], s["end"], "name",
                     candidates=_name_candidates(occ, raw[s["start"]:s["end"]], s.get("lookup"), s.get("alias"),
                                                 s.get("key")))
    # ---- X-byeol words naming classification axes (a condition or a word segment)
    if occ is not None:
        # a name found through its dot-free form names its axes by that key too (plan 19 stage 4, ``written_key``)
        keys = {(t.start, t.end): t.key for t in toks if t.kind == "name" and t.lookup is None and not t.alias}
        spots = [(c["span"][0], c["span"][1], c["quote"]) for c in conditions]
        for s in segments:
            if s["kind"] in ("word", "name"):
                quote = written_key(s["quote"], keys.get(tuple(s["span"]))) if s["kind"] == "name" else s["quote"]
                spots.append((s["span"][0], s["span"][1], quote))
        for a, b, quote in spots:
            axes = occ.axis(_axis_word(quote))
            if axes:
                rows.add(a, b, "name", candidates=axes)
    # ---- conditions (existing IDs)
    for c in conditions:
        rows.add(c["span"][0], c["span"][1], "condition", condition_ids=[c["id"]], marker=marker_of_condition(c))
    # ---- markers written as words, list dots and joiner particles between two nominals (no scope)
    for t in toks:
        if t.kind == "word" and t.label != "particle":
            m = word_marker(t.quote)
            if m is not None:
                rows.add(t.start, t.end, "marker", marker=m)
        elif t.kind == "punct" and is_list_dot(t.quote):
            rows.add(t.start, t.end, "marker", marker="list")
    for a, b in joiner_list_marks(toks, raw):
        rows.add(a, b, "marker", marker="list")
    # ---- request phrases: request words with only spaces between them
    run: list[Segment] = []
    for s in segments + [None]:
        if s is not None and s["kind"] == "request":
            run.append(s)
        elif s is not None and s["kind"] == "space" and run:
            continue  # a space between two request words; trailing spaces are left out
        elif run:
            rows.add(run[0]["span"][0], run[-1]["span"][1], "request")
            run = []
    return rows


def _ordered(rows: _Rows) -> list[dict]:
    return sorted(rows.rows.values(), key=lambda r: (r["start"], -r["end"], KIND_ORDER[r["kind"]],
                                                     r["condition_ids"], r["marker"] or ""))


def _number(rows: _Rows, segments: list[Segment]) -> list[Mention]:
    out: list[Mention] = []
    for n, r in enumerate(_ordered(rows), start=1):
        sids = [s["segment_id"] for s in segments if _overlap(r["start"], r["end"], *s["span"])]
        # plan 19 (decision of 2026-10-21): the raw [start, end) kept on the mention, for the final reference check
        # (``Mention.span``); the model payload never copies it
        out.append({"mention_id": f"m{n}", "kind": r["kind"], "segment_ids": sids, "span": [r["start"], r["end"]],
                    "condition_ids": list(r["condition_ids"]), "marker": r["marker"],
                    "candidates": r["candidates"], "overlaps": []})
    for m in out:
        own = set(m["segment_ids"])
        m["overlaps"] = [x["mention_id"] for x in out if x is not m and own & set(x["segment_ids"])]
    return out


def build_ref_map(segments: list[Segment], mentions: list[Mention], fragments: list[Fragment]) -> RefMap:
    seg_mentions: dict[str, list[str]] = {s["segment_id"]: [] for s in segments}
    for m in mentions:
        for sid in m["segment_ids"]:
            seg_mentions.setdefault(sid, []).append(m["mention_id"])
    frag_segments = {f["id"]: [s["segment_id"] for s in segments if _overlap(*f["span"], *s["span"])]
                     for f in fragments}
    seg_fragments: dict[str, list[str]] = {s["segment_id"]: [] for s in segments}
    for fid, sids in frag_segments.items():
        for sid in sids:
            seg_fragments.setdefault(sid, []).append(fid)
    mention_fragments = {}
    for m in mentions:
        own = set(m["segment_ids"])
        mention_fragments[m["mention_id"]] = [fid for fid, sids in frag_segments.items() if own & set(sids)]
    return {"segment_mentions": seg_mentions, "segment_fragments": seg_fragments,
            "fragment_segments": frag_segments, "mention_fragments": mention_fragments}
