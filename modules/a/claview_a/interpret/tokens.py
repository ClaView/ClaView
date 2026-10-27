"""The token layer of question interpretation (plan 08 T1): the question read once, after the conditions of 2.2.

``tokenize`` turns the question and its conditions into one token stream: catalog names (2.2 name protection: kept
whole), conditions, family qualifiers, calc hints, words and punctuation, each with its span, the particle or ending
written against it and the flags the later stages read (word label, stem and ending, request form, bracket inside a
name, ...). Marks are read after NFKC (full-width brackets and commas are the half-width ones). The words of every
class come from ``lexicon``. Stages after 2.2 read these tokens, not the question text again.

- Where a name and a condition cover the same words, the longer one wins; with the same span a condition wins
  ("명목" is a basis word even when it is also a value name), and the name counts as used. A family qualifier
  ("계절조정계열" of "가상통화량(계절조정계열)") covers the condition inside it. Calc hints (change words, user decision
  10/18) are tokens too, so predicate stripping never removes them. The text between tokens is cut at whitespace and
  punctuation; a particle or ending written against a token is its suffix.
- The base reading of one question and condition set is computed once (cached); every call gets its own copies of the
  tokens, since the phrase reading marks them (negated, stripped, excluded).
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from ..catalog.relations import relations_of
from ..catalog.text import index_key
from ..catalog.text import key as role_key
from ..contract.shapes import Condition, TextView
from . import lexicon as LX
from .condition_rules import _marker_pattern
from .conditions import find_calc_hints, name_spans_of, qualifier_spans_of
from .lexicon import (BASE_WORD, CLOSE, ENDINGS, HANGUL_SYLLABLE, LABELS, NUMBER_MARKS, OPEN, PROTECT_CHARS, PUNCT,
                      REQUEST_FINAL, REQUEST_POLITE, glued_request_splits)

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

VALUE_KINDS = ("period", "frequency", "basis", "calc", "unit")
SENTENCE_END_MARKS = LX.SENTENCE_END
MARKER_KINDS = ("relation", "function", "display")
MAX_ENTRY_WORDS = 3  # most words of one out-of-scope or dictionary entry looked up over adjacent unknown words
_TIE = {"marker": 0, "cond": 1, "qual": 2, "name": 3, "hint": 4}


def key_of(text: str) -> str:
    return index_key(text)


def split_ending(k: str) -> tuple[str, str]:
    """(stem, ending): the longest ending of the data list that leaves a stem."""
    for e in ENDINGS:
        if len(k) > len(e) and k.endswith(e):
            return k[:-len(e)], e
    return k, ""


def word_label(word: str) -> str:
    k = key_of(word)
    if k in LABELS:
        return LABELS[k]
    stem, _ = split_ending(k)
    return LABELS.get(stem, "unknown")


def _char_class(ch: str) -> str:
    c = unicodedata.normalize("NFKC", ch)
    if not c or c.isspace():
        return "space"
    if all(x in PUNCT for x in c):
        return "punct"
    return "text"


# ---------------------------------------------------------------- tokens


@dataclass(eq=False)
class Tok:
    kind: str  # name, cond, qual, hint, word, punct
    start: int  # raw span of the token body
    end: int
    quote: str
    send: int = -1  # end including the glued suffix
    suffix: str = ""  # particle or ending written against the token (index key form)
    label: str = ""  # word label, condition kind, hint kind or the punctuation mark
    cond: dict | None = None  # the condition of a cond token, or the one a qualifier covers
    inner: list = field(default_factory=list)  # conditions inside a name or qualifier token
    key: str = ""  # name or qualifier key
    qual: str | None = None  # qualifier key that coincides with a condition token
    used_names: list = field(default_factory=list)  # name spans this token took over
    excluded: bool = False  # a name inside an exclusion
    negated: bool = False
    stripped: bool = False  # part of the clause predicate
    hint: dict | None = None
    lookup: str | None = None  # catalog name a list member was completed to (never exact)
    members: list = field(default_factory=list)  # list-member (breakdown) conditions inside the token
    alias: list | None = None  # plan 05 H: catalog candidates of a table head alias (never exact)
    glued: bool = False  # a name read inside a word written without spaces (plan 05 G)
    cut: bool = False  # a list mark that always cuts (a middle dot right after a relation phrase)

    def __post_init__(self):
        if self.send < 0:
            self.send = self.end

    @property
    def cond_kind(self) -> str | None:
        return self.cond["kind"] if self.kind == "cond" and self.cond else None

    # ---- what the token layer says of a token (plan 08 T1): read from its own words, once, by these properties

    @property
    def norm(self) -> str:
        """The token's words as an index key (NFKC, lower case, no spaces)."""
        return index_key(self.quote)

    @property
    def stem(self) -> str:
        """A word token without its ending ("가상A를" -> 가상a); other tokens: their key form."""
        return split_ending(self.norm)[0]

    @property
    def ending(self) -> str:
        """The ending of a word token ("가상A를" -> 를); other tokens have their particle in ``suffix``."""
        return split_ending(self.norm)[1]

    @property
    def stem_end(self) -> int:
        """Raw end of a word token without the ending written in it ("가상A를" -> the end of 가상A)."""
        e = self.ending
        return self.end - len(e) if e and self.quote.endswith(e) else self.end

    def ends_in(self, particles) -> bool:
        """The particle written against the token is one of ``particles``: its suffix, or a word's own ending."""
        if self.kind == "punct":
            return False
        return self.suffix in particles or (self.kind == "word" and self.ending in particles)

    @property
    def request_form(self) -> str | None:
        """"final" for a complete request word: a final form of the lexicon ("보여줘", "보여줄래", "보자"), also with
        a polite ending ("보여줘요"); None otherwise (a stem, a noun, the "없어" of an exclusion: findings 1, 2). A
        display or function word with the ending written against it is read as one word ("비교" + "해" = "비교해",
        review p08 r1-1)."""
        if self.kind == "word":
            words = self.norm
        elif self.kind == "cond" and self.cond_kind in ("display", "function") and self.suffix:
            words = self.norm + self.suffix
        else:
            return None
        if words in REQUEST_FINAL:
            return "final"
        polite = next((e for e in REQUEST_POLITE if words.endswith(e) and len(words) > len(e)), None)
        if polite is not None and words[:-len(polite)] in REQUEST_FINAL:
            return "final"
        return None

    @property
    def bracket_at(self) -> int:
        """Raw position of the first opening bracket inside the token (full-width forms included), -1 without one."""
        return next((self.start + n for n, ch in enumerate(self.quote)
                     if unicodedata.normalize("NFKC", ch) == OPEN[0]), -1)

    @property
    def hangul_len(self) -> int:
        """Hangul syllables of the token's words."""
        return len(HANGUL_SYLLABLE.findall(self.quote))

    @property
    def numeric(self) -> bool:
        """The stem is a number ("2.5", "10%")."""
        s = self.stem
        return bool(s) and all("0" <= ch <= "9" or ch in NUMBER_MARKS for ch in s)

    @property
    def spaced_words(self) -> list[str]:
        """The token's words as written, cut at spaces ("연령별 지역별")."""
        return self.quote.split()

    @property
    def protected(self) -> bool:
        """A name holding a mark, a joiner or a genitive letter inside it (its fragment is protected, 2.3)."""
        return self.kind == "name" and any(_char_class(ch) != "text" or ch in PROTECT_CHARS for ch in self.quote)


def _intervals(text: TextView, conditions: list[Condition], names: list[dict], quals: list[dict],
               hints: list[dict]) -> list[tuple]:
    out = []
    for c in conditions:
        if c["kind"] == "breakdown" and c["attrs"].get("source", "").startswith("catalog_") \
                and index_key(c["value"]) != index_key(c["quote"]):
            # a list member the 2.2 list rules completed with a catalog name ("상품·서비스수지": 상품 -> 상품수지);
            # read as that name, never as an exact one
            out.append((c["span"][0], c["span"][1], "name",
                        {"start": c["span"][0], "end": c["span"][1], "key": index_key(c["value"]),
                         "lookup": c["value"]}))
            continue
        if c["kind"] in ("breakdown", "exclude"):
            continue
        kind = "marker" if c["kind"] in MARKER_KINDS else "cond"
        out.append((c["span"][0], c["span"][1], kind, c))
    for s in names:
        out.append((s["start"], s["end"], "name", s))
    for s in quals:
        out.append((s["start"], s["end"], "qual", s))
    for h in hints:
        out.append((h["span"][0], h["span"][1], "hint", h))
    return out


_BASE: dict = {}


def tokenize(text: TextView, conditions: list[Condition], layer: CatalogLayer | None) -> tuple[list[Tok], list]:
    """Tokens of the question in order, and the name spans found (for the unused-name check). The base reading of
    one question and condition set (exclusions aside: they only mark names) is made once; each call gets fresh
    copies with the conditions of `conditions` and the names they exclude marked."""
    key = (text["raw"], tuple((c["id"], c["kind"], c["span"][0], c["span"][1]) for c in conditions
                              if c["kind"] != "exclude"), id(layer))
    hit = _BASE.get(key)
    if hit is None or hit[2] is not layer:
        hit = (*_tokenize(text, conditions, layer), layer)
        if len(_BASE) > 512:
            _BASE.clear()
        _BASE[key] = hit
    by_id = {c["id"]: c for c in conditions}
    excludes = [c for c in conditions if c["kind"] == "exclude"]
    toks = [_fresh(t, by_id) for t in hit[0]]
    for t in toks:
        if t.kind == "name" and any(x["span"][0] <= t.start and t.end <= x["span"][1] for x in excludes):
            t.excluded = True
    return toks, hit[1]


def _fresh(t: Tok, by_id: dict) -> Tok:
    """A copy of a base token, bound to the given conditions (same IDs and spans) and with no reading marks."""
    n = copy.copy(t)
    n.__dict__.pop("_term", None)
    n.inner = [by_id.get(c.get("id"), c) for c in t.inner]
    n.members = [by_id.get(c.get("id"), c) for c in t.members]
    n.used_names = list(t.used_names)
    if t.cond is not None:
        n.cond = by_id.get(t.cond.get("id"), t.cond)
    return n


def _tokenize(text: TextView, conditions: list[Condition], layer: CatalogLayer | None) -> tuple[list[Tok], list]:
    raw = text["raw"]
    names = name_spans_of(text, layer) or []
    rel = relations_of(layer) if layer is not None else None
    quals = qualifier_spans_of(text, layer) if rel else []
    hints = find_calc_hints(text, names)
    placed: list[Tok] = []
    order = sorted(_intervals(text, conditions, names, quals, hints),
                   key=lambda x: (-(x[1] - x[0]), _TIE[x[2]], x[0]))
    for a, b, kind, obj in order:
        hit = [t for t in placed if t.start < b and a < t.end]
        if hit:
            if kind in ("cond", "marker"):
                for host in hit:  # a condition inside a name or qualifier stays a condition of that phrase
                    if host.kind in ("name", "qual") and host.start <= a and b <= host.end:
                        host.inner.append(obj)
            elif kind == "name":
                for host in hit:  # the name lies under a longer (or equal) condition, qualifier or marker
                    if host.kind in ("cond", "qual") and host.start <= a and b <= host.end:
                        host.used_names.append(obj)
            elif kind == "qual":
                for host in hit:  # the same words as a condition: the condition is also a family qualifier
                    if host.kind == "cond" and host.start == a and host.end == b:
                        host.qual = obj["key"]
            continue
        if kind in ("cond", "marker"):
            placed.append(Tok("cond", a, b, raw[a:b], label=obj["kind"], cond=obj))
        elif kind == "name":
            placed.append(Tok("name", a, b, raw[a:b], key=obj["key"], lookup=obj.get("lookup"),
                              alias=obj.get("alias"), glued=bool(obj.get("glued"))))
        elif kind == "qual":
            placed.append(Tok("qual", a, b, raw[a:b], key=obj["key"]))
        else:
            placed.append(Tok("hint", a, b, raw[a:b], label=obj["kind"], hint=obj))
    # a qualifier that covers a condition carries it
    for t in placed:
        if t.kind == "qual":
            inside = [c for c in conditions if c["kind"] in VALUE_KINDS
                      and t.start <= c["span"][0] and c["span"][1] <= t.end]
            if inside:
                t.cond = inside[0]
                t.inner.extend(inside[1:])
    placed.sort(key=lambda t: t.start)
    toks: list[Tok] = []
    pos = 0
    for t in placed + [None]:
        stop = t.start if t is not None else len(raw)
        _fill(raw, pos, stop, toks)
        if t is not None:
            toks.append(t)
            pos = t.end
    breakdowns = [c for c in conditions if c["kind"] == "breakdown"]
    for t in toks:
        t.members = [c for c in breakdowns if t.start <= c["span"][0] and c["span"][1] <= t.end]
    if layer is not None:  # 2.4 first paths (out-of-scope list, approved dictionary) reach the items (R-10)
        toks = _first_path_names(raw, toks, layer)
        _completed_names(toks, layer)
    vocab = set(layer.roles.basis_vocab) - {BASE_WORD} if layer is not None else set()
    for prev, t in zip([None] + toks, toks):
        # "2015년 기준": a base year, not a period with a filler word; left as an unknown word (check 5)
        if t.kind == "word" and split_ending(key_of(t.quote))[0] == BASE_WORD and prev is not None \
                and prev.kind == "cond" and prev.cond_kind == "period":
            t.label = "unknown"
    for t in toks:
        if t.kind == "word" and t.label == "unknown" and role_key(split_ending(key_of(t.quote))[0]) in vocab:
            t.label = "basis_word"  # a basis word of the catalog layer ("잔액", "금액"): a view of its phrase
    return toks, names


def _first_path_hit(stem: str, layer) -> bool:
    return any(c.get("type") == "out_of_scope" or (c.get("path") == "dictionary" and c.get("status") == "approved")
               for c in layer.index.lookup(stem))


def _first_path_names(raw: str, toks: list[Tok], layer) -> list[Tok]:
    """Unknown words that are an out-of-scope or approved dictionary entry become names (2.4 first paths). Entries of
    several words ("가상 실업률") are looked up over runs of adjacent unknown words (longest run first), since the
    entry keys have no spaces."""
    out: list[Tok] = []
    n = 0
    while n < len(toks):
        t = toks[n]
        if not (t.kind == "word" and t.label == "unknown"):
            out.append(t)
            n += 1
            continue
        run = [t]
        while len(run) < MAX_ENTRY_WORDS and n + len(run) < len(toks):
            nxt = toks[n + len(run)]
            if not (nxt.kind == "word" and nxt.label == "unknown") or raw[run[-1].send:nxt.start].strip() \
                    or run[-1].suffix:
                break
            run.append(nxt)
        for size in range(len(run), 0, -1):
            part = run[:size]
            joined = "".join(key_of(x.quote) for x in part)
            stem, ending = split_ending(joined)
            if not stem or not _first_path_hit(stem, layer):
                continue
            if size == 1:  # one word: the token itself becomes the name (its ending a suffix)
                t.kind, t.key, t.lookup = "name", stem, stem
                if len(key_of(t.quote)) > len(stem) and key_of(t.quote).startswith(stem):
                    cut = raw_len(t.quote, stem)  # the written length of the stem (review p08 r1-8)
                    t.suffix = key_of(t.quote)[len(stem):] + t.suffix
                    t.quote, t.end = t.quote[:cut], t.start + cut
                out.append(t)
            else:
                last = part[-1]
                lk = key_of(last.quote)
                end = last.start + max(raw_len(last.quote, lk[:len(lk) - len(ending)]), 1)
                out.append(Tok("name", t.start, end, raw[t.start:end], send=last.send, key=stem, lookup=stem,
                               label=t.label, suffix=(ending if end < last.end else "") + last.suffix))
            n += size
            break
        else:
            out.append(t)
            n += 1
    return out


def _completed_names(toks: list[Tok], layer) -> None:
    """Plan 09 P2c: a table name split by a negation ("가상수단은 가상직불이 아니라 가상선불로"): in one clause, a name
    token H with the topic particle, then a negation word, and unknown words w after H whose keys put together (w + H)
    are a table name (its full name or the name before its bracket). w becomes a name token of that table
    (``lookup``: never exact); H is taken into it: it is no name of its own any more (no indicator, no unused name,
    check 6) and reads as a filler word. The negation of the phrase reading then runs as before. A name H that is not
    the topic the words after it qualify ("가상선불 말고 가상수단을": H is the name asked) is left as it is (review of
    plan 09). Clauses end at a sentence mark, a list mark or a clause-ending request word."""
    from .catalog_words import catalog_words  # catalog readings (no circular import at module load)

    titles = catalog_words(layer).title_names
    glued: list[tuple[Tok, Tok]] = []
    clause: list[Tok] = []
    for t in toks + [None]:
        if t is not None and not (t.kind == "punct" and t.label in SENTENCE_END_MARKS | LX.SEPARATOR_PUNCT) \
                and not (t.kind == "word" and t.label == "clause_ends"):
            clause.append(t)
            continue
        sentence, clause = clause, []
        heads = [h for h in sentence if h.kind == "name" and h.lookup is None and not h.alias and h.suffix in LX.TOPIC
                 and any(x.kind == "word" and x.label == "negation" and x.start > h.start for x in sentence)]
        taken: set[int] = set()
        for w in sentence:
            if not (w.kind == "word" and w.label == "unknown" and w.stem):
                continue
            # the word as written, or without a request word glued to it ("가상선불로보여줘": as the spaced form)
            cuts = glued_request_splits(w.quote, lambda x: bool(x))
            front = w.quote[:min(cuts)] if cuts else w.quote
            stem = split_ending(key_of(front))[0]
            hit = next(((h, stem + h.key) for h in heads if w.start > h.start and (stem + h.key) in titles), None)
            if hit is None:
                continue
            h, k = hit
            if front != w.quote:  # the glued request word becomes a word token of its own
                rest = w.quote[len(front):]
                glued.append((w, Tok("word", w.start + len(front), w.end, rest, label=word_label(rest))))
                w.quote, w.end = front, w.start + len(front)
                w.send, w.suffix = w.end, ""
            cut = raw_len(w.quote, stem)
            w.suffix = key_of(w.quote)[len(stem):] + w.suffix
            w.kind, w.key, w.lookup, w.label = "name", k, titles[k], ""
            w.quote, w.end = w.quote[:cut], w.start + cut
            taken.add(id(h))
        for h in heads:
            if id(h) in taken:
                h.kind, h.label, h.lookup = "word", "ignore", None
    for w, req in glued:
        toks.insert(toks.index(w) + 1, req)


def raw_len(written: str, key: str) -> int:
    """The length of the shortest prefix of ``written`` whose index key is ``key`` (a normalised particle or stem
    written in another form, decomposed jamo say, keeps its own written length); len(key) when none is."""
    for m in range(len(written) + 1):
        if key_of(written[:m]) == key:
            return m
    return len(key)


def _glued_splits(piece: str) -> set[int]:
    return glued_request_splits(piece, lambda w: word_label(w) != "unknown")


def _glued_ending(k: str) -> str | None:
    """The ending a glued piece starts with when the rest is a known word ("를보여줘" -> 를 + 보여줘)."""
    for e in ENDINGS:
        if len(k) > len(e) and k.startswith(e) and word_label(k[len(e):]) not in ("unknown",):
            return e
    return None


def _fill(raw: str, a: int, b: int, toks: list[Tok]) -> None:
    """Words and punctuation of raw[a:b]; a piece glued to the previous token that is an ending is its suffix."""
    i = a
    while i < b:
        cls = _char_class(raw[i])
        if cls == "space":
            i += 1
            continue
        if cls == "punct":
            toks.append(Tok("punct", i, i + 1, raw[i], label=unicodedata.normalize("NFKC", raw[i])))
            i += 1
            continue
        j = i
        while j < b and _char_class(raw[j]) == "text":
            j += 1
        piece = raw[i:j]
        prev = toks[-1] if toks else None
        k = key_of(piece)
        glued = prev is not None and prev.send == i and prev.kind != "punct"
        head = _glued_ending(k) if glued and not (k in ENDINGS or (prev.suffix + k) in ENDINGS) else None
        if glued and (k in ENDINGS or (prev.suffix + k) in ENDINGS):
            prev.suffix += k
            prev.send = j
        elif head:  # plan 05 G: "를보여줘" glued to a name: its particle, then a known word
            cut = raw_len(piece, head)  # the written length of the particle (review p08 r1-8)
            prev.suffix += head
            prev.send = i + cut
            rest = piece[cut:]
            toks.append(Tok("word", i + cut, j, rest, label=word_label(rest)))
        elif prev is not None and prev.kind == "punct" and prev.label in CLOSE and prev.end == i \
                and k not in ENDINGS and _glued_ending(k):
            # a particle after a bracket with a known word glued to it ("(평잔)을보여줘"): the particle, then the
            # word, as written apart (plan 08, glued request words)
            head = _glued_ending(k)
            cut = raw_len(piece, head)
            toks.append(Tok("word", i, i + cut, piece[:cut], label="particle"))
            toks.append(Tok("word", i + cut, j, piece[cut:], label=word_label(piece[cut:])))
        elif k not in ENDINGS and word_label(piece) == "unknown" and len(_glued_splits(piece)) == 1:
            # a known word with a request word glued to it ("기준으로보여줘", "없이보여줘"): the two words, as written
            # apart (plan 08, glued request words)
            (cut,) = _glued_splits(piece)
            toks.append(Tok("word", i, i + cut, piece[:cut], label=word_label(piece[:cut])))
            toks.append(Tok("word", i + cut, j, piece[cut:], label=word_label(piece[cut:])))
        else:
            # a particle after a bracket is grammar ("(잔액 기준)와"); a word written apart that is also a request or
            # filler word of the lexicon ("… 같이 해") is that word (review of plan 08, variant M1)
            after_mark = prev is not None and prev.kind == "punct" and prev.end == i
            label = "particle" if k in ENDINGS and (after_mark or k not in LABELS) else word_label(piece)
            toks.append(Tok("word", i, j, piece, label=label))
        i = j


# ---------------------------------------------------------------- written words (the attribution view, plan 08 T1)


def word_conditions(toks: list[Tok], kind: str) -> set[str]:
    """IDs of the conditions of ``kind`` that are tokens of their own: a relation word inside a catalog name
    ("비율" of "가상부채 비율") is part of the name (2.2 name protection), no word of the question (finding 7)."""
    return {t.cond["id"] for t in toks if t.kind == "cond" and t.cond is not None and t.cond["kind"] == kind}


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _rstrip_marks(s: str, marks) -> str:
    """``s`` without the marks of ``marks`` at its end (a full-width form counts as its NFKC mark)."""
    n = len(s)
    while n > 0 and nfkc(s[n - 1]) in marks:
        n -= 1
    return s[:n]


_TRAIL_ALL = frozenset(LX.SENTENCE_END | {LX.COMMA})  # ".,?!"
_TRAIL_COMMA = frozenset(LX.COMMA)
_TRAIL_CLAUSE = frozenset({LX.COMMA, LX.PERIOD})
_PHRASE_END_PARTICLES = tuple(sorted(LX.OBJECT | LX.TOPIC, key=len, reverse=True))
_DESIGNATE_PARTICLES = tuple(sorted(LX.DESIGNATE, key=len, reverse=True))
_LIST_TAIL = tuple(sorted(LX.LIST_TAIL, key=len, reverse=True))
_RELATION_ALT = "|".join(f"(?:{_marker_pattern(e).pattern})" for e in LX.MARKER_ENTRIES["relation"]
                         if e.get("start") == "word")
_COMPARE_ALT = "|".join(f"(?:{_marker_pattern(e).pattern})" for e in LX.MARKER_ENTRIES["display"]
                        if e.get("value") == "compare")
_COMPARE = re.compile(f"{_RELATION_ALT}|{_COMPARE_ALT}")
_RELATION_WORD = re.compile(f"^(?:{_RELATION_ALT})")
_COMMON = re.compile(LX.alt_spaced(LX.COMMON_WORDS + LX.DISTRIBUTIVE_WORDS + LX.SIMULTANEOUS_WORDS))
_NEGATE = re.compile(LX.alt_spaced(LX.DESIGNATION_NEGATION))


def _longest_suffix(s: str, words) -> str | None:
    return next((w for w in sorted(words, key=len, reverse=True) if s.endswith(w)), None)


def strip_trailing_marks(w: str) -> str:
    """A written word without the sentence marks and commas after it."""
    return _rstrip_marks(w, _TRAIL_ALL)


def ends_with_phrase_end(w: str) -> bool:
    """The word closes a noun phrase with an object or topic particle ("X를", "X는")."""
    return strip_trailing_marks(w).endswith(_PHRASE_END_PARTICLES)


def ends_with_subject(w: str) -> bool:
    return strip_trailing_marks(w).endswith(LX.SUBJECT_LIST)


def designation_stem(w: str) -> str | None:
    """The words before a designation particle ("X는", "X만", "X에만"; commas after it aside), None without one."""
    r = _rstrip_marks(w, _TRAIL_COMMA)
    p = _longest_suffix(r, _DESIGNATE_PARTICLES)
    return nfkc(r[:len(r) - len(p)]) if p is not None else None


def restricts(w: str) -> bool:
    """A restrictive designation ("X만", "X에만")."""
    return _rstrip_marks(w, _TRAIL_COMMA).endswith(LX.RESTRICTIVE)


def ends_with_topic(w: str) -> bool:
    return _rstrip_marks(w, _TRAIL_COMMA).endswith(tuple(LX.TOPIC))


def ends_with_bare_joiner(w: str) -> bool:
    """The word ends in a joiner particle that also joins a bare word ("A와")."""
    return w.endswith(LX.BARE_JOINERS)


def ends_clause(w: str) -> bool:
    """A connective ending closes the clause of the word ("보여주고", "비교하되")."""
    r = _rstrip_marks(w, _TRAIL_CLAUSE)
    return any(r.endswith(e) and len(r) > len(e) for e in LX.CONNECTIVE_ENDINGS)


def ends_adnominal(w: str) -> bool:
    """An adnominal verb form ("늘어난", "가진"): the noun phrase may continue after it."""
    return len(w) >= 2 and w[-1] in LX.ADNOMINAL_FINAL


def is_basis_tail_noun(w: str) -> bool:
    """A general noun written after a basis value ("명목 금액"): the lexicon's basis tail nouns."""
    return index_key(w) in LX.BASIS_TAIL_NOUNS


def strip_list_particle(w: str) -> str:
    p = _longest_suffix(w, _LIST_TAIL)
    return w[:len(w) - len(p)] if p is not None else w


def closes_phrase_before_head(w: str) -> bool:
    """A particle closes the word, so it is no shared head of a list after it ("X A와 B")."""
    return _rstrip_marks(w, _TRAIL_CLAUSE).endswith(LX.PHRASE_CLOSING)


def strip_genitive(w: str) -> str:
    return w.removesuffix(LX.GENITIVE_OF)


def starts_with_base_word(s: str) -> bool:
    return s.startswith(LX.BASE_WORD_TEXT)


@lru_cache(maxsize=4096)
def starts_with_relation(s: str) -> bool:
    return bool(_RELATION_WORD.search(s))


@lru_cache(maxsize=4096)
def compare_word(s: str) -> bool:
    """A relation or comparison word with the word boundaries of the 2.2 relation and display words."""
    return bool(_COMPARE.search(s))


@lru_cache(maxsize=4096)
def has_common_marker(s: str) -> bool:
    """A word that makes a tail condition common ("모두", "각각", "둘 다", "함께")."""
    return bool(_COMMON.search(s))


@lru_cache(maxsize=4096)
def has_designation_negation(s: str) -> bool:
    """A negation or exclusion word that makes a designation ambiguous ("말고", "빼고", "제외")."""
    return bool(_NEGATE.search(s))


@dataclass(frozen=True)
class Word:
    start: int
    end: int
    text: str


class Words:
    """The written words of the question (runs without whitespace, as spans), read once with the tokens; a view of a
    part of the question (``clip``) keeps the positions of the whole. ``blank_notes``: bracket notes ("(...)") read
    as spaces."""

    def __init__(self, raw: str, lo: int = 0, hi: int | None = None, blank_notes: bool = False,
                 names: tuple = (), base: list | None = None, cuts: tuple = ()):
        self.raw = raw
        self.names = tuple(names)  # (start, end) of the catalog-name tokens (2.2 name protection)
        self.lo = max(0, lo)
        self.hi = len(raw) if hi is None else min(hi, len(raw))
        self.size = len(raw)
        # the written words of the whole question, read once; a view cuts them (review p08 r1-9)
        self.base = base if base is not None else _runs(raw, 0, len(raw), cuts)
        cut = [Word(max(w.start, self.lo), min(w.end, self.hi), raw[max(w.start, self.lo):min(w.end, self.hi)])
               for w in self.base if max(w.start, self.lo) < min(w.end, self.hi)]
        if blank_notes:  # bracket notes inside the view read as spaces: the words around them stay
            notes = _note_spans(raw, self.lo, self.hi)
            cut = [x for w in cut for x in _minus_spans(raw, w, notes)]
        self.items: list[Word] = cut

    def __iter__(self):
        return iter(self.items)

    def clip(self, a: int | None, b: int | None, blank_notes: bool = False) -> Words:
        lo = self.lo if a is None else max(self.lo, a)
        hi = self.hi if b is None else min(self.hi, b)
        return Words(self.raw, lo, max(lo, hi), blank_notes=blank_notes, names=self.names, base=self.base)

    def text(self, a: int, b: int) -> str:
        return self.raw[a:b]

    def in_name(self, start: int | None, length: int) -> bool:
        """The written word at ``start`` lies inside a longer catalog-name token ("비율을" of "가상부채 비율을")."""
        if start is None:
            return False
        return any(a <= start and start + length <= b and b - a > length for a, b in self.names) \
            or any(a <= start < b and b - a > length and start + length > b for a, b in self.names)

    def compare_word(self, s: str, start: int | None = None) -> bool:
        """A relation or comparison word (``compare_word``) that is not part of a catalog name (finding 7)."""
        if self.in_name(start, len(strip_trailing_marks(s))):
            return False
        return compare_word(s)

    def relation_start(self, s: str, start: int | None = None, length: int = 0) -> bool:
        """A designation stem that starts with a relation word ("차이는"), not part of a catalog name."""
        if self.in_name(start, length or len(s)):
            return False
        return starts_with_relation(s)

    def next_start(self, b: int) -> int:
        """The start of the first word at or after ``b`` in the view, else the end of the question."""
        return next((w.start for w in self.items if w.start >= b), self.size)

    def word_at(self, p: int) -> Word | None:
        return next((w for w in self.items if w.start == p), None)

    def any_between(self, a: int, b: int) -> bool:
        lo, hi = max(a, self.lo), min(b, self.hi)
        return any(not self.raw[k].isspace() for k in range(lo, hi))

    def last_before(self, end: int) -> Word | None:
        words = list(self.clip(None, end))
        return words[-1] if words else None

    def _break_at(self, j: int, stop: int) -> bool:
        ch = nfkc(self.raw[j])
        if ch in LX.SENTENCE_END - {LX.PERIOD}:
            return True
        if ch == LX.PERIOD:  # a decimal point ("2.5%") is no sentence end
            return not (j + 1 < stop and self.raw[j + 1].isdigit())
        return False

    def break_ends(self, a: int, b: int) -> list[int]:
        """Ends of the sentence marks that start in [a, b) of the view."""
        lo, hi = max(a, self.lo), min(b, self.hi)
        return [j + 1 for j in range(lo, hi) if self._break_at(j, hi)]

    def first_break(self, i: int) -> int:
        """The start of the first sentence mark at or after ``i`` in the view, else the end of the question."""
        return next((j for j in range(max(i, self.lo), self.hi) if self._break_at(j, self.hi)), self.size)


def _runs(raw: str, lo: int, hi: int, cuts=()) -> list[Word]:
    """Runs without whitespace of raw[lo:hi]; a run is also cut where a request word is glued to the word before it
    (``cuts``: "가상A를보여줘" reads as "가상A를" and "보여줘", like the spaced form)."""
    out, i, cuts = [], lo, set(cuts)
    while i < hi:
        if raw[i].isspace():
            i += 1
            continue
        j = i + 1
        while j < hi and not raw[j].isspace() and j not in cuts:
            j += 1
        out.append(Word(i, j, raw[i:j]))
        i = j
    return out


def glued_request_starts(toks: list[Tok]) -> tuple:
    """Starts of request words written against the token before them ("…를보여줘"): cuts of the written words."""
    return tuple(t.start for prev, t in zip(toks, toks[1:]) if t.kind == "word"
                 and t.label in ("request", "clause_ends") and prev.kind != "punct" and prev.send == t.start)


def _note_spans(raw: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Bracket notes inside [lo, hi): "(...)" with no closing bracket inside (full-width brackets too)."""
    out, j = [], lo
    while j < hi:
        if nfkc(raw[j]) == LX.OPEN[0]:
            k = j + 1
            while k < hi and nfkc(raw[k]) != LX.CLOSE[0]:
                k += 1
            if k < hi:
                out.append((j, k + 1))
                j = k + 1
                continue
        j += 1
    return out


def _minus_spans(raw: str, w: Word, spans: list[tuple[int, int]]) -> list[Word]:
    """The parts of a written word outside the given spans."""
    parts, a = [], w.start
    for s, e in spans:
        if e <= a or s >= w.end:
            continue
        if s > a:
            parts.append(Word(a, s, raw[a:s]))
        a = max(a, e)
    if a < w.end:
        parts.append(Word(a, w.end, raw[a:w.end]))
    return parts


def _trim_char(ch: str) -> bool:
    return ch in LX.TRIM_CHARS or nfkc(ch) in LX.TRIM_CHARS


def trim_span(raw: str, a: int, b: int, joiner: bool = False) -> list[int]:
    """A quote span [a, b) without the spaces and commas at its ends (a full-width form read as its NFKC mark) and,
    with ``joiner``, without a joiner particle written against its last word ("A와" -> A)."""
    while a < b and _trim_char(raw[a]):
        a += 1
    while b > a and _trim_char(raw[b - 1]):
        b -= 1
    if joiner and b - a >= 2 and raw[b - 1] in LX.BARE_JOINERS and not raw[b - 2].isspace():
        b -= 1
        while b > a and raw[b - 1].isspace():
            b -= 1
    return [a, b]
