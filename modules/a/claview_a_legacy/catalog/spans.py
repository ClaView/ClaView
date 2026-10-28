"""Catalog-name spans that respect word boundaries (plan 04, 2-5; question interpretation 2.2 name protection).

``TermIndex.find_spans`` (stretch 1) takes every whitespace-free match of a catalog name, so a name can start or end
inside a written word ("월말 잔액" holds "말잔"). ``NameSpanIndex.find`` keeps only the matches whose ends fall on word
boundaries:

- matching ignores whitespace, case and NFKC differences (the same flat text as ``find_spans``) and, plan 14 I8, the
  list middle dots (lexicon ``punctuation.dots``): "가상A·가상B 금리" is read as "가상A가상B금리"; a name whose key
  holds middle dots is also found through its dot-free form, and a match written with the name's own dots keeps
  that key (positions and boundaries stay those of the original text);
- a match that crosses whitespace covers whole words: it starts where a word starts (or right after punctuation) and
  ends where a word ends, before punctuation, or before a particle that closes the word;
- a match inside one word starts at the word start, after punctuation or where another kept name ends, and ends at
  the word end, before punctuation or a particle, or where another kept name starts;
- only chosen names are boundary evidence (a candidate that is not chosen props up no other): the chosen set is the
  boundary-valid set of non-overlapping candidates that covers most characters, then has the longer names (dynamic
  programming over positions; M1-1 review: a longest-first choice could drop a valid split altogether).

Keys are the term-index span keys plus the year-range-free form of every catalog name ("금융거래표(2009~)" ->
"금융거래표", decision 6); those extra keys and their candidates live here, the term index is not changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..reused.q_nodes import normalize
from .index import MIN_SPAN_CHARS, TermIndex
from .text import index_key, name_variants, strip_year_range

# Punctuation that ends or starts a name like whitespace does (middle dots after NFKC included).
PUNCT = ".,?!;:()[]{}<>\"'~/·ᆞ・ㆍ-"
YEAR_RANGE_VARIANT = "no_year_range"


def read_particles(path: Path | None = None) -> tuple[str, ...]:
    """Word endings that may follow a catalog name inside one written word: the lexicon's name-boundary particles
    (data/lexicon.json, plan 08), or the "particles" of a given file."""
    if path is None:
        from ..interpret.lexicon import NAME_BOUNDARY_PARTICLES  # the one word dictionary (no catalog import there)

        words = NAME_BOUNDARY_PARTICLES
    else:
        words = json.loads(Path(path).read_text(encoding="utf-8"))["particles"]
    return tuple(sorted({p for p in words if p}, key=len, reverse=True))


def _span_key(k: str) -> bool:
    return len(k) >= MIN_SPAN_CHARS and any(ch.isalpha() for ch in k)


def _same(a: dict, b: dict) -> bool:
    """Same catalog object: the same tables, or the same value of the same axis."""
    return (a.get("kind"), a.get("table_ids"), a.get("obj_id"), a.get("item_id")) == \
        (b.get("kind"), b.get("table_ids"), b.get("obj_id"), b.get("item_id"))


def year_range_entries(index: TermIndex) -> dict[str, list[dict]]:
    """Extra candidates: catalog names under their year-range-free forms ("금융거래표(2009~)" -> 금융거래표).

    Each is a copy of the catalog candidate with ``variant`` "no_year_range" and ``path`` "exact" (decision 6: the
    year-range marks are presentation, not a different name). A form whose key already holds the same catalog object
    as an exact candidate adds nothing (a table name before its first bracket is exact in the term index already);
    the term index itself is not changed (a value's bracket-free form stays a lexical candidate there).
    """
    out: dict[str, list[dict]] = {}
    for cands in index.entries.values():
        for c in cands:
            if c.get("kind") not in ("table", "value") or c.get("path") == "dictionary":
                continue
            stripped = strip_year_range(c.get("name"))
            if stripped is None:
                continue
            # the bracket-free form is not exact (decision 6: a bracket qualifier changes the name)
            forms = [k for v, k in name_variants(stripped).items() if v != "no_paren"]
            for k in dict.fromkeys(forms):
                new = dict(c, key=k, table_ids=list(c["table_ids"]), variant=YEAR_RANGE_VARIANT, path="exact")
                if any(_same(e, new) and e.get("path") == "exact" for e in index.entries.get(k, [])):
                    continue
                rows = out.setdefault(k, [])
                if not any(_same(r, new) for r in rows):
                    rows.append(new)
    return out


@dataclass
class NameSpanIndex:
    index: TermIndex
    particles: tuple[str, ...]
    extra: dict[str, list[dict]] = field(default_factory=dict)  # year-range-free keys -> candidates
    keys: frozenset[str] = frozenset()
    lengths: tuple[int, ...] = ()

    def __post_init__(self):
        if not self.keys:
            self.keys = frozenset(self.index.span_keys) | frozenset(k for k in self.extra if _span_key(k))
        if not self.lengths:
            self.lengths = tuple(sorted({len(k) for k in self.keys}, reverse=True))
        from ..interpret.lexicon import DOTS  # the one word dictionary (no catalog import there)

        self._dots = frozenset(DOTS)
        # plan 14 I8: the dot-free form of a key with middle dots -> that key (the flat text has no dots)
        self._undotted: dict[str, str] = {}
        for k in sorted(self.keys):
            u = "".join(ch for ch in k if ch not in self._dots)
            if u and u != k and u not in self.keys:
                self._undotted.setdefault(u, k)
        self._lengths = tuple(sorted(set(self.lengths) | {len(u) for u in self._undotted}, reverse=True))
        alt = "|".join(re.escape(p) for p in self.particles)
        punct = re.escape(PUNCT)
        self._tail = re.compile(rf"(?:{alt})(?:[{punct}].*)?" if alt else rf"[{punct}].*")

    def lookup(self, text: str) -> list[dict]:
        """Term-index candidates plus year-range-free candidates of ``text`` (copies)."""
        k = index_key(text)
        return self.index.lookup(text) + [dict(c, table_ids=list(c["table_ids"])) for c in self.extra.get(k, [])]

    def candidates(self, k: str) -> int:
        return len(self.index.entries.get(k, [])) + len(self.extra.get(k, []))

    # ------------------------------------------------------------ boundaries

    def _tail_ok(self, rest: str) -> bool:
        """The rest of a written word after a name is punctuation, or a particle (then punctuation or nothing)."""
        return rest == "" or rest[0] in PUNCT or bool(self._tail.fullmatch(rest))

    def _bounds(self, q: str, c: tuple[int, int, str], word: dict[int, tuple[int, int]]) -> tuple[bool, bool, bool]:
        """(crosses whitespace, left boundary of its own, right boundary of its own) of a candidate."""
        a, b, _ = c
        wa, wb = word[a], word[b - 1]
        left = a == wa[0] or (a > wa[0] and q[a - 1] in PUNCT)
        right = b == wb[1] or self._tail_ok(q[b:wb[1]])
        return wa != wb, left, right

    def _select(self, q: str, cands: set, word: dict[int, tuple[int, int]]) -> list[tuple[int, int, str]]:
        """Non-overlapping candidates whose boundaries all hold within the chosen set.

        A candidate inside one word may lean on a chosen neighbour for a missing boundary: its start where the chosen
        name before it ends, its end where the chosen name after it starts. A candidate that crosses whitespace needs
        its own boundaries on both sides. Only chosen names count as boundary evidence, so a dropped candidate never
        props up another. Among the valid sets the one covering most characters wins, then the one whose names are
        longer (sum of squared lengths), then the one with the longer name further left (dynamic programming over
        positions, so a valid split is found even when the longest names do not fit together).
        """
        n = len(q)
        by_start: dict[int, list] = {}
        info = {}
        for c in cands:
            crosses, left, right = self._bounds(q, c, word)
            if crosses and not (left and right):
                continue
            info[c] = (crosses, left, right)
            by_start.setdefault(c[0], []).append(c)
        for row in by_start.values():
            row.sort(key=lambda c: (-len(c[2]), c[1]))
        # best[p][linked][must]: (score, chosen) from position p on; linked = a chosen name ends at p,
        # must = a name has to start at p (the previous name has no right boundary of its own)
        nothing = None
        best: list[list[list]] = [[[nothing, nothing], [nothing, nothing]] for _ in range(n + 1)]
        best[n] = [[((0, 0), ()), nothing], [((0, 0), ()), nothing]]
        for p in range(n - 1, -1, -1):
            for linked in (0, 1):
                for must in (0, 1):
                    top = None if must else best[p + 1][0][0]
                    for c in by_start.get(p, []):
                        crosses, left, right = info[c]
                        if not (left or (linked and not crosses)):
                            continue
                        nxt = best[c[1]][1][0 if right else 1]
                        if nxt is None:
                            continue
                        size = len(c[2])
                        score = (nxt[0][0] + size, nxt[0][1] + size * size)
                        if top is None or score > top[0]:
                            top = (score, (c,) + nxt[1])
                    best[p][linked][must] = top
        result = best[0][0][0]
        return list(result[1]) if result else []

    def _key_of(self, written: str, flat: str) -> str:
        """The key of a match (plan 14 I8): the written form with its middle dots when that is a key (a name written
        as the catalog writes it keeps its own key), else the dot-free flat form, else the key it is the dot-free
        form of."""
        full = "".join(low for ch in written if not ch.isspace() for low in ch.lower())
        if full in self.keys:
            return full
        return flat if flat in self.keys else self._undotted[flat]

    def find(self, text: str, min_chars: int = MIN_SPAN_CHARS) -> list[dict]:
        """Name spans of ``text`` that respect word boundaries; same row shape as ``TermIndex.find_spans``:
        {start, end, quote, key, candidates} with positions in the original text, in text order."""
        norm = normalize(text)
        q, offsets = norm["query"], norm["offsets"]
        chars: list[str] = []
        pos: list[int] = []  # flat index -> index in q
        for k, ch in enumerate(q):
            if ch.isspace() or ch in self._dots:  # plan 14 I8: middle dots are skipped like whitespace
                continue
            for low in ch.lower():
                chars.append(low)
                pos.append(k)
        flat = "".join(chars)
        word: dict[int, tuple[int, int]] = {}
        for m in re.finditer(r"\S+", q):
            for p in range(m.start(), m.end()):
                word[p] = (m.start(), m.end())
        lengths = [n for n in self._lengths if n >= min_chars]
        cands: set[tuple[int, int, str]] = set()
        for i in range(len(flat)):
            for n in lengths:
                sub = flat[i:i + n]
                if i + n <= len(flat) and (sub in self.keys or sub in self._undotted):
                    cands.add((pos[i], pos[i + n - 1] + 1, sub))  # the flat form: sizes count no dots

        chosen = self._select(q, cands, word)
        out = []
        for a, b, sub in sorted(chosen):
            k = self._key_of(q[a:b], sub)
            start, end = offsets[a], offsets[b - 1] + 1
            out.append({"start": start, "end": end, "quote": text[start:end], "key": k,
                        "candidates": self.candidates(k)})
        return out


def build_name_span_index(index: TermIndex, particles: tuple[str, ...] | None = None) -> NameSpanIndex:
    return NameSpanIndex(index=index, particles=read_particles() if particles is None else tuple(particles),
                         extra=year_range_entries(index))
