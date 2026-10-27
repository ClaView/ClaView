"""Catalog readings question interpretation needs beyond the term index (plan 04, 2-2 and 2-5), built once per layer.

- And-names: a table name that joins words with the list word "및" ("가상기관 가상갑 및 가상을대출 비중") is also
  found from the word before "및" on ("가상갑 및 가상을대출 비중"), so that a question which writes the leading words
  elsewhere does not cut the name at "및" into a list (2.2 name protection: list cuts only outside name spans). Such
  a span is a trimmed name: it is read through the full name and is never exact.
- Axis words: the classification axis names of a table and the X-byeol words of its name ("가상활동별", "가상규모별"),
  as keys without "별"; "N에서 A와 B를 나누어" and "A와 B를 나눈 N" read A and B as breakdown axes only when a table
  of N has axes named by them (or by part of them).
- Swap pairs: two words are values of one basis slot when replacing one by the other in a table name gives another
  table name ("가상대출 신규취급액" / "가상대출 잔액"; "가상표(갑, 명목)" / "가상표(갑, 실질)"): copies of one indicator
  differ by such words (row 6), while words that never stand in for each other qualify one requirement together.

Nothing here names a table, case or question; the list word and the grammar words come from data/lexicon.json.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import TYPE_CHECKING

from .. import paths
from ..catalog.index import TermIndex
from ..catalog.spans import NameSpanIndex, read_particles
from ..catalog.text import collapse, index_key, paren_front
from .lexicon import AND_WORD, BYEOL, HANGUL_WORD, KNOWN_WORDS, MARKER_ENTRIES, NAME_SPLIT, PUNCT, glued_name_cuts

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

MIN_AXIS_CHARS = 2
_RULES = json.loads(paths.FRAGMENT_WORDS.read_text(encoding="utf-8"))  # phrase-rule parameters (no words)
GLUED_MIN = _RULES["glued_word_min_chars"]
# words a glued name may be followed by (plan 05 G): request and filler words, and the plain words of the relation,
# function and display word patterns ("비교", "각각")
GRAMMAR_WORDS = tuple(sorted(KNOWN_WORDS | {w for kind in ("relation", "function", "display") for e in MARKER_ENTRIES[kind]
                                            for w in e["regex"].split("|") if HANGUL_WORD.fullmatch(w)}
                             - {w for w in KNOWN_WORDS if len(w) < 2}, key=len, reverse=True))
_CACHE_KEY = "interpret_catalog_words"


class _NoRole:
    role = None


class CatalogWords:
    def __init__(self, layer: CatalogLayer):
        tables = list(layer.catalog.tables.values())
        spans = layer.name_spans()
        self.span_keys = spans.keys
        self.span_lengths = tuple(n for n in spans.lengths if n >= 2)
        self.particles = read_particles()
        self.table_keys = {index_key(t.name): t.table_id for t in tables}
        self.names = {t.table_id: t.name for t in tables}
        # plan 09 P2c: table-name keys -> the name to read them by: the full name for its own key, the name before
        # the bracket for that key (it finds every table of the family, as the spaced form does: review of plan 09)
        self.title_names: dict[str, str] = {}
        for t in tables:
            self.title_names.setdefault(index_key(t.name), t.name)
        for t in tables:
            self.title_names.setdefault(index_key(paren_front(t.name)), collapse(paren_front(t.name)))
        # ---- and-names: tails of table names from the word before "및" on
        entries: dict[str, list[dict]] = {}
        known = set(layer.index.entries) - layer.index.derived_keys  # plan 09 P1: the catalog's own name keys only
        # user decision 10/19 21:45: the two sides of "A 및 B" count as names when derived names (a value's
        # abbreviation) are (DN5b: "가상GDP 및 VGNI" reads like "가상GDP와 VGNI")
        names = set(layer.index.entries)
        for t in tables:
            for name in dict.fromkeys(n for n in (t.name_front, paren_front(t.name)) if n):
                words = collapse(name).split(" ")
                for i, w in enumerate(words):
                    if w != AND_WORD or i < 2 or i == len(words) - 1:
                        continue
                    if index_key(words[i - 1]) in names and index_key(words[i + 1]) in names:
                        continue  # "A 및 B" of two catalog names: a list reading stays possible

                    for s in range(1, i):
                        tail = " ".join(words[s:])
                        k = index_key(tail)
                        row = entries.setdefault(k, [])
                        if not any(r["full"] == collapse(name) for r in row):
                            row.append({"key": k, "name": tail, "full": collapse(name), "type": "series_name",
                                        "table_ids": [t.table_id], "obj_id": None, "obj_name": None,
                                        "item_id": None, "path": "lexical", "variant": "and_tail",
                                        "kind": "table", "status": "draft"})
                        else:
                            r = next(r for r in row if r["full"] == collapse(name))
                            if t.table_id not in r["table_ids"]:
                                r["table_ids"].append(t.table_id)
        entries = {k: v for k, v in entries.items() if k not in known}  # a tail that is a name itself stays that name
        self.and_tails = entries
        self.and_spans = NameSpanIndex(index=TermIndex(entries=entries, span_keys=frozenset(entries)),
                                       particles=read_particles()) if entries else None
        # ---- head aliases (plan 05 H): a table name without its X-byeol words and its own target values
        alias: dict[str, list[dict]] = {}
        for t in tables:
            front = collapse(paren_front(t.name))
            words = front.split(" ")
            targets = {index_key(it.name or "") for ax in t.axes
                       if (layer.roles.axis(t.table_id, ax.obj_id) or _NoRole).role == "target" for it in ax.items}
            head = [w for w in words if not w.endswith(BYEOL) and index_key(w) not in targets]
            if not head or len(head) == len(words):
                continue
            k = index_key(" ".join(head))
            if len(k) < MIN_AXIS_CHARS or k in known or k in KNOWN_WORDS:
                continue  # a name already, or a known filler or request word ("통계"): no alias
            rows = alias.setdefault(k, [])
            rows.append({"key": k, "name": t.name, "type": "series_name", "table_ids": [t.table_id], "obj_id": None,
                         "obj_name": None, "item_id": None, "path": "lexical", "variant": "head_alias",
                         "kind": "table", "status": "draft"})
        self.aliases = alias
        self.alias_spans = NameSpanIndex(index=TermIndex(entries=alias, span_keys=frozenset(alias)),
                                         particles=read_particles()) if alias else None
        # ---- axis words of each table
        self.axis_words: dict[str, set[str]] = {}
        for t in tables:
            words = {self.axis_key(ax.name) for ax in t.axes if ax.name}
            words |= {self.axis_key(w) for w in NAME_SPLIT.split(t.name) if w.endswith(BYEOL)}
            self.axis_words[t.table_id] = {w for w in words if len(w) >= MIN_AXIS_CHARS}

    @staticmethod
    def axis_key(text: str) -> str:
        return index_key(text).removesuffix(BYEOL)

    # ------------------------------------------------------------ and-names

    def and_name_spans(self, raw: str) -> list[dict]:
        """Word-boundary spans of the and-name tails in ``raw``: {start, end, quote, key, lookup} where ``lookup`` is
        the full table name the tail stands for (the first one when several tables share the tail)."""
        if self.and_spans is None:
            return []
        out = []
        for s in self.and_spans.find(raw):
            rows = self.and_tails.get(s["key"], [])
            if rows:
                out.append(dict(s, lookup=rows[0]["full"]))
        return out

    # ------------------------------------------------------------ head aliases (plan 05 H)

    def alias_name_spans(self, raw: str, spans: list[dict]) -> list[dict]:
        """Word-boundary spans of table head aliases ("가상수신" of "가상기관 가상지역별 가상수신") over words no
        name span covers. Rows carry ``alias`` (one candidate per table; never exact)."""
        if self.alias_spans is None:
            return []
        out = []
        for s in self.alias_spans.find(raw):
            if any(s["start"] < x["end"] and x["start"] < s["end"] for x in spans):
                continue
            out.append(dict(s, alias=[dict(r, table_ids=list(r["table_ids"])) for r in self.aliases[s["key"]]]))
        return out

    # ------------------------------------------------------------ glued request words (plan 08, user 10/19)

    def stripped_name_spans(self, raw: str, spans: list[dict], counts, keys=None) -> list[dict]:
        """Names written against a request word without a space ("가상경상수지를보여줘", "…를보여줄래요."): from the
        right end of a written word (its marks aside, or one piece of it between marks) that holds no name span, the
        lexicon's layers are taken off one at a time (``lexicon.glued_name_cuts``: a polite ending, one final or
        connective request form, one particle). When exactly one remainder of the word front is one catalog name, it is
        read like the spaced form: an exact name span (rows as ``NameSpanIndex.find``, ``counts`` its candidate count);
        a name with marks in it ("가상상품수지(평잔)를보여줘") holds the shorter spans found inside it. Two names glued
        together, a remainder that is no catalog name, or two possible names (guessing) give none: the glued reading of
        ``glued_name_spans`` stays (never exact). ``keys``: the name keys read (default: the catalog-name span keys;
        the family qualifiers' keys for qualifiers)."""
        keys = self.span_keys if keys is None else keys
        out = []
        for m in re.finditer(r"\S+", raw):  # the written words, cut at the lexicon's marks ("가상A·가상B를보여줘")
            lo, hi = m.start(), m.end()
            while lo < hi and unicodedata.normalize("NFKC", raw[lo]) in PUNCT:
                lo += 1
            while hi > lo and unicodedata.normalize("NFKC", raw[hi - 1]) in PUNCT:
                hi -= 1
            whole = self._stripped(raw, lo, hi, counts, keys) if hi > lo else []
            inside = [s for s in spans if s["start"] < m.end() and m.start() < s["end"]]
            if whole and all(whole[0]["start"] <= s["start"] and s["end"] <= whole[0]["end"] for s in inside):
                out += whole  # one name with marks in it ("가상상품수지(평잔)를보여줘"): it holds the shorter spans
                continue
            pieces, start = [], m.start()
            for k in range(m.start(), m.end() + 1):
                if k == m.end() or unicodedata.normalize("NFKC", raw[k]) in PUNCT:
                    if k > start:
                        pieces.append((start, k))
                    start = k + 1
            for a, b in pieces:
                if not any(s["start"] < b and a < s["end"] for s in spans):
                    out += self._stripped(raw, a, b, counts, keys)
        return out

    def _stripped(self, raw: str, a: int, b: int, counts, keys) -> list[dict]:
        """The one exact name span of a piece raw[a:b] (a written word between marks), or none."""
        body = raw[a:b].lower()
        found = glued_name_cuts(body, keys.__contains__)
        if len(found) != 1:
            return []
        (cut,) = found
        return [{"start": a, "end": a + cut, "quote": raw[a:a + cut], "key": body[:cut], "candidates": counts(body[:cut])}]

    # ------------------------------------------------------------ glued words (plan 05 G)

    def glued_name_spans(self, raw: str, spans: list[dict], conds: dict[int, int] | None = None) -> list[dict]:
        """Names inside long words written without spaces ("가상갑수지와가상을수지를보여줘") that hold no name span.
        Read from the word start: a condition (``conds``: raw start -> end) is passed over, then the longest catalog
        name whose end is a boundary: the word end, a condition or another name, or a particle followed by the word
        end, a condition, a name or a known word (request, filler, display word). A letter where no condition or
        bounded name starts ends the reading of the word (no name is taken from inside an unknown compound, plan 04
        2-5). Rows as ``NameSpanIndex.find`` with ``lookup`` (the name key: never exact) and ``glued`` True."""
        conds = conds or {}
        out = []
        for m in re.finditer(r"\S+", raw):
            a, b = m.span()
            if b - a < GLUED_MIN or any(s["start"] < b and a < s["end"] for s in spans):
                continue
            low = "".join(ch.lower() for ch in raw[a:b])
            if len(low) != b - a:
                continue

            memo: dict[int, bool] = {}

            def names_at(q: int) -> list[int]:
                return [n for n in self.span_lengths if q + n <= len(low) and low[q:q + n] in self.span_keys]

            def boundary(q: int) -> bool:
                """A boundary that holds by itself: the word end, a mark of the lexicon's punctuation ("보여줘.":
                review p08 r1-5), a condition, a known word that ends at a boundary itself, or a name that is itself
                bounded (only kept names are boundary evidence: a fixed point, plan 04 2-5)."""
                if q in memo:
                    return memo[q]
                memo[q] = False  # no cycle: names have positive length
                ok = q >= len(low) or unicodedata.normalize("NFKC", low[q]) in PUNCT or (a + q) in conds \
                    or any(low.startswith(w, q) and bounded(q + len(w)) for w in GRAMMAR_WORDS) \
                    or any(bounded(q + n) for n in names_at(q))
                memo[q] = ok
                return ok

            def bounded(q: int) -> bool:
                if boundary(q):
                    return True
                return any(low.startswith(x, q) and boundary(q + len(x)) for x in self.particles)

            p = 0
            while p < len(low):
                if (a + p) in conds:  # a condition written against the names ("2020년", "월별")
                    p = conds[a + p] - a
                    p += len(next((x for x in self.particles if low.startswith(x, p)), ""))
                    continue
                hit = next((n for n in self.span_lengths if p + n <= len(low) and low[p:p + n] in self.span_keys
                            and bounded(p + n)), None)
                if hit is None:
                    break
                key = low[p:p + hit]
                out.append({"start": a + p, "end": a + p + hit, "quote": raw[a + p:a + p + hit], "key": key,
                            "candidates": 0, "lookup": key, "glued": True})
                p += hit
                if not boundary(p):
                    p += len(next((x for x in self.particles if low.startswith(x, p) and boundary(p + len(x))), ""))
        return out

    # ------------------------------------------------------------ axis words

    def axis_tables(self, words: list[str]) -> set[str]:
        """Tables with an axis named by (part of) each word ("가상규모" in "가상규모별", "가상업" in "가상업코드별")."""
        keys = [self.axis_key(w) for w in words]
        if not keys or any(len(k) < MIN_AXIS_CHARS for k in keys):
            return set()
        return {t for t, axes in self.axis_words.items() if all(any(k in a for a in axes) for k in keys)}

    # ------------------------------------------------------------ swap pairs

    def swappable(self, a: str, b: str) -> bool:
        """Replacing word ``a`` by word ``b`` (or ``b`` by ``a``) in some table name gives another table name."""
        ka, kb = index_key(a), index_key(b)
        if len(ka) < MIN_AXIS_CHARS or len(kb) < MIN_AXIS_CHARS or ka == kb:
            return False
        for k in self.table_keys:
            if ka in k and k.replace(ka, kb) in self.table_keys and k.replace(ka, kb) != k:
                return True
            if kb in k and k.replace(kb, ka) in self.table_keys and k.replace(kb, ka) != k:
                return True
        return False


def catalog_words(layer: CatalogLayer) -> CatalogWords:
    if _CACHE_KEY not in layer._derived:
        layer._derived[_CACHE_KEY] = CatalogWords(layer)
    return layer._derived[_CACHE_KEY]
