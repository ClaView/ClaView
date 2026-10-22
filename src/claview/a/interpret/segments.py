"""build_segments (plan 15 r3, 3.1): the raw question cut into physical chunks that cover it exactly once.

Every character of ``text.raw`` lies in exactly one segment and the segment quotes joined in order are the raw
question (spaces, newlines, punctuation, brackets and particles included). Positions are raw positions, as the
condition spans and tokens have them (full-width forms are read through NFKC to classify a character, never
rewritten).

Material: the token layer (``tokens.tokenize``): its token spans and the particle or ending written against a token
(``send``). Its reading marks (negated, excluded, stripped) are not read. Kinds:

- ``name``: a catalog-name token (2.2 name protection: kept whole, inner spaces included), or a family qualifier
  that carries no condition;
- ``condition``: a condition token (a qualifier carrying a condition too);
- ``marker``: a list, divide, negation, exclusion or relation mark of the lexicon (``marker_of_words``): a word of
  those classes, a list middle dot, or a relation or function condition token whose words are of those classes;
- ``request``: a request word of the lexicon (a complete request form included). Plan 19 stage 4: a request word
  that is also a word of another lexicon class (``lexicon.REQUEST_SHARED``: a request noun, a marker word, a divide
  adverb, a modifier ending) is a request only in a request position: the next token is a request segment (read the
  same way), a complete request form, a punctuation mark, or nothing. Elsewhere ("한 X당", "함께 나눠") it is a
  ``word`` (a quantity, simultaneity or content word the reading must place). The lexicon and the rules path are
  unchanged;
- ``grammar``: the particle or ending written against a name, condition, qualifier or hint token, and a particle the
  tokenizer reads after a bracket;
- ``space``: a run of whitespace; ``punct``: one punctuation mark;
- ``word``: every other piece (an unknown word, a filler word, a calc hint: ambiguous pieces stay words).

Segment IDs are s1, s2, ... in text order (stable for the same question, conditions and catalog layer).
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from ..catalog.text import index_key
from ..contract.shapes import Condition, Segment, TextView
from . import lexicon as LX
from .tokens import Tok, _char_class, split_ending, tokenize, word_label

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

# word labels of the lexicon (lexicon.LABELS) that are marks, and the class they mark
LABEL_MARKER = {"negation": "negation", "exclude_verb": "exclusion", "list_words": "list", "combine": "relation",
                "share": "relation"}
DIVIDE_KEYS = LX.DIVIDE | LX.DIVIDE_ADNOMINAL
REQUEST_LABELS = ("request", "clause_ends")
_LEADING_PARTICLES = tuple(sorted({index_key(p) for p in LX.MARKER_PARTICLES if p}, key=len, reverse=True))


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def word_marker(word: str, leading_particle: bool = False) -> str | None:
    """The marker class of one written word (lexicon classes), None when it is no marker word. The word is read as
    written and without its ending; with ``leading_particle`` (the words of a condition quote) also without one
    leading marker particle ("를뺀")."""
    k = index_key(word)
    if not k:
        return None
    forms = [k]
    if leading_particle:
        forms += [k[len(p):] for p in _LEADING_PARTICLES if k.startswith(p) and len(k) > len(p)]
    for f in forms:
        if f in DIVIDE_KEYS or split_ending(f)[0] in DIVIDE_KEYS:
            return "divide"
        label = word_label(f)
        if label in LABEL_MARKER:
            return LABEL_MARKER[label]
    return None


def marker_of_words(quote: str) -> str | None:
    """The first marker class among the whitespace-separated words of a condition quote."""
    for w in quote.split():
        m = word_marker(w, leading_particle=True)
        if m is not None:
            return m
    return None


def marker_of_condition(cond: Condition | None) -> str | None:
    """A relation condition marks a relation; a function condition marks the class of its words (dividing, excluding
    words); any other condition marks nothing."""
    if cond is None:
        return None
    if cond["kind"] == "relation":
        return "relation"
    if cond["kind"] == "function":
        return marker_of_words(cond["quote"])
    return None


def is_list_dot(ch: str) -> bool:
    return _nfkc(ch) in LX.DOTS and bool(ch)


def is_request_word(t: Tok) -> bool:
    return t.kind == "word" and (word_label(t.quote) in REQUEST_LABELS or t.request_form == "final")


def shared_request_word(t: Tok) -> bool:
    """A request word token (not a complete request form) that is also a word of another lexicon class
    (``lexicon.REQUEST_SHARED``), read as written or without its ending as ``word_label`` reads it."""
    if t.kind != "word" or t.request_form == "final":
        return False
    k = index_key(t.quote)
    return k in LX.REQUEST_SHARED or (k not in LX.LABELS and split_ending(k)[0] in LX.REQUEST_SHARED)


def request_position(nxt: Tok | None, nxt_kind: str | None) -> bool:
    """The place after a word is a request position: nothing or a punctuation mark follows, or a request segment, or
    a complete request form (a display word with its ending, "X해")."""
    if nxt is None or nxt.kind == "punct" or nxt_kind == "request":
        return True
    return nxt.request_form == "final"


def token_kinds(toks: list[Tok]) -> list[str]:
    """The segment kinds of tokens in text order (``token_kind``), a shared request word out of a request position
    read as a word (plan 19 stage 4). Read from the end, so a run of request words is judged by its last word."""
    out: list[str] = [""] * len(toks)
    for i in range(len(toks) - 1, -1, -1):
        kind = token_kind(toks[i])
        if kind == "request" and shared_request_word(toks[i]):
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if not request_position(nxt, out[i + 1] if nxt is not None else None):
                kind = "word"
        out[i] = kind
    return out


def token_kind(t: Tok) -> str:
    """The segment kind of a token body (alone; ``token_kinds`` adds the context rule of shared request words)."""
    if t.kind == "name":
        return "name"
    if t.kind == "qual":
        return "condition" if t.cond is not None else "name"
    if t.kind == "cond":
        return "marker" if marker_of_condition(t.cond) is not None else "condition"
    if t.kind == "punct":
        return "marker" if is_list_dot(t.quote) else "punct"
    if t.kind == "hint":
        return "word"
    if t.label == "particle":  # a particle the tokenizer reads after a bracket ("(...)와")
        return "grammar"
    if word_marker(t.quote) is not None:
        return "marker"
    if is_request_word(t):
        return "request"
    return "word"


def _gap(raw: str, a: int, b: int, out: list[tuple[int, int, str]]) -> None:
    """Pieces of raw[a:b] no token covers: whitespace runs, single marks, text runs (words)."""
    i = a
    while i < b:
        cls = _char_class(raw[i])
        j = i + 1
        if cls == "space":
            while j < b and _char_class(raw[j]) == "space":
                j += 1
            out.append((i, j, "space"))
        elif cls == "punct":
            out.append((i, j, "marker" if is_list_dot(raw[i]) else "punct"))
        else:
            while j < b and _char_class(raw[j]) == "text":
                j += 1
            out.append((i, j, "word"))
        i = j


def segment_pieces(raw: str, toks: list[Tok]) -> list[tuple[int, int, str]]:
    """(start, end, kind) pieces covering raw exactly once, from the token spans and suffixes."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    order = sorted(toks, key=lambda t: (t.start, t.end))
    for t, kind in zip(order, token_kinds(order)):
        a, b = max(t.start, pos), min(t.end, len(raw))
        if a > pos:
            _gap(raw, pos, a, out)
            pos = a
        if b > pos:
            out.append((pos, b, kind))
            pos = b
        s = min(max(t.send, t.end), len(raw))
        if s > pos:  # the particle or ending written against the token
            out.append((pos, s, "grammar"))
            pos = s
    _gap(raw, pos, len(raw), out)
    return out


def build_segments(text: TextView, conditions: list[Condition], layer: CatalogLayer | None = None) -> list[Segment]:
    raw = text["raw"]
    toks, _ = tokenize(text, conditions, layer)
    return [{"segment_id": f"s{n}", "span": [a, b], "quote": raw[a:b], "kind": kind}
            for n, (a, b, kind) in enumerate(segment_pieces(raw, toks), start=1)]
