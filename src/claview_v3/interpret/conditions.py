"""find_conditions (detail 2.2): condition candidates of a question.

Kinds (shapes.ConditionKind): period, frequency, basis, unit, breakdown, exclude, calc, relation, function, display.
Each finder returns candidates of its kind in the new ``conditions`` shape, IDs numbered per kind in question order;
``find_conditions`` takes them all and settles overlaps between kinds.

- period: the reused Q2 rules (``reused/q_rules.find_periods``, regular expressions unchanged) wrapped into the new
  shape. A period keeps its structure as written and is not resolved to dates (2.2, plan 04 decision 13).
- frequency: the reused Q3 rules (stretch 1).
- basis, calc, unit, breakdown, exclude: the old Q4/Q5 rules ported in ``condition_rules`` (condition vocabulary in
  ``data/condition_words.json``, grammar words in ``data/lexicon.json``); comparison bases and calculation requests
  are kind calc (2.2 "계산(전년 대비 등)").
- relation, function, display: new word patterns (2.2).
Ambiguous words ("흐름", a lone "기준") are not condition words (2.2, recommended).
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from ..catalog.names import CatalogNames
from ..catalog.text import index_key
from ..catalog.relations import relations_of
from ..contract.shapes import ID_PREFIX, Condition, PeriodExpr, TextView
from ..reused import q_rules
from ..reused.q_nodes import frequencies
from . import condition_rules as rules
from .catalog_words import catalog_words

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

# ---------------------------------------------------------------- shared helpers

# Any reference date: the period rules are asked once with it only to learn whether a period could be resolved once a
# reference date is known (``attrs.extract_status``); the dates they compute are not used.
_PROBE_AS_OF = "2000-01-01"
VALUE_KINDS = ("period", "frequency", "basis", "calc", "unit", "exclude", "breakdown")
CLASSIFICATION_AXIS = "classification"  # the basis axis of "X-byeol" words
MARKER_KINDS = ("relation", "function", "display")
KIND_ORDER = VALUE_KINDS + MARKER_KINDS
_EMPTY_NAMES = CatalogNames(None)


def _raw_span(text: TextView, i: int, j: int) -> list[int]:
    offsets = text["offsets"]
    return [offsets[i], offsets[j - 1] + 1]


def _condition(text: TextView, kind: str, n: int, i: int, j: int, value, attrs: dict) -> Condition:
    """A condition over norm[i:j]; whitespace a rule took at either end ("2020~2023년 " of the reused range rule)
    is not part of the quote."""
    norm = text["norm"]
    while i < j - 1 and norm[i].isspace():
        i += 1
    while j - 1 > i and norm[j - 1].isspace():
        j -= 1
    a, b = _raw_span(text, i, j)
    return {"id": f"{ID_PREFIX[kind]}{n}", "kind": kind, "quote": text["raw"][a:b], "span": [a, b], "value": value,
            "attrs": attrs, "status": "candidate"}


def _names(layer: CatalogLayer | None) -> CatalogNames:
    return layer.list_names() if layer is not None else _EMPTY_NAMES


def name_spans_of(text: TextView, layer: CatalogLayer | None) -> list[dict] | None:
    """Word-boundary catalog-name spans of the question (raw positions), None without a catalog layer.

    A trimmed and-name ("가상갑 및 가상을대출 비중" of the table "가상기관 가상갑 및 가상을대출 비중") that holds names
    found inside it replaces them, so the list word inside it never cuts a list (plan 04, 2.2: name protection first,
    list cuts only outside name spans). Its row carries ``lookup`` (the full table name; never exact)."""
    if layer is None:
        return None
    derived = layer.index.derived_keys
    spans = _with_tails(layer, text, layer.name_spans().find(text["raw"]))
    # plan 08 (user 10/19): one name written against a request word ("가상A를보여줘") reads as the spaced form
    stripped = catalog_words(layer).stripped_name_spans(text["raw"], spans, layer.name_spans().candidates)
    spaced = _spaced_names_before_glue(layer, text["raw"], spans + stripped)
    if spaced:  # the shorter names inside a spaced name go (plan 09 P1)
        spans = [x for x in spans if not any(s["start"] <= x["start"] and x["end"] <= s["end"] for s in spaced)]
        stripped = [x for x in stripped if not any(s["start"] <= x["start"] and x["end"] <= s["end"] for s in spaced)]
    stripped += spaced
    if stripped:
        spans = sorted(spans + stripped, key=lambda s: s["start"])
    # plan 10 (user 10/20 02:28): a table name with a value of that table glued after it reads as the spaced form
    pairs = _table_then_value(layer, text["raw"], spans)
    if pairs:
        lo_hi = [(p[0]["start"], p[1]["end"]) for p in pairs]
        spans = sorted([s for s in spans if not any(a <= s["start"] and s["end"] <= b for a, b in lo_hi)]
                       + [x for p in pairs for x in p], key=lambda s: s["start"])
    genitive = _genitive_names(layer, text["raw"], spans)
    if genitive:  # plan 09 P2c: the shorter names inside go
        spans = sorted([s for s in spans if not any(g["start"] <= s["start"] and s["end"] <= g["end"]
                                                    for g in genitive)] + genitive, key=lambda s: s["start"])
    # plan 05 G: a long word written without spaces that holds no name span is read again without word
    # boundaries ("가상경상수지와가상상품수지를보여줘"); such names are never exact (``lookup``)
    glued = catalog_words(layer).glued_name_spans(text["raw"], spans, _condition_bounds(text))
    # plan 05 H: head nouns of table names ("가상기관 가상지역별 가상수신" -> 가상수신) as family aliases. Plan 09 P1:
    # a derived name (or a mark-free key) is read on top of them: where an alias reads the same words, the alias
    # stays as it was and the derived name goes
    plain = [s for s in spans if s["key"] not in derived]
    alias = catalog_words(layer).alias_name_spans(text["raw"], plain + glued)
    if alias:
        spans = [s for s in spans if s["key"] not in derived
                 or not any(s["start"] < x["end"] and x["start"] < s["end"] for x in alias)]
    extra = glued + alias
    return sorted(spans + extra, key=lambda s: s["start"]) if extra else spans


GENITIVE_WINDOW = (3, 2)  # written words of a table name split by a genitive ("가상수출 금액의 지수"), longest first


def _genitive_names(layer: CatalogLayer, raw: str, spans: list[dict]) -> list[dict]:
    """Plan 09 P2c: a table name written over two or three words with a genitive particle inside ("가상수출 금액의
    지수" for "가상수출금액지수"): the words without that particle and the spaces are the key of a table name (its
    full name or the name before its bracket), so they are read as that name (``lookup``: never exact) in place of
    the shorter names inside. Not when the words after the genitive start a list (a joiner particle or list word
    follows), when the words before it are a target value of that table or a swap partner of the words after it
    (``CatalogWords.swappable``), or when both sides are names already (the genitive then joins two names)."""
    from .lexicon import (GENITIVE_OF, JOINERS, LIST_WORD_KEYS, NAME_BOUNDARY_PARTICLES, PUNCT, SEPARATOR_PUNCT,
                          glued_request_splits)

    words = catalog_words(layer)
    rel = relations_of(layer)
    derived = layer.index.derived_keys
    runs = []
    for m in re.finditer(r"\S+", raw):
        a, b = m.span()
        while b > a and unicodedata.normalize("NFKC", raw[b - 1]) in PUNCT:
            b -= 1
        runs.append((a, b, m.end()))
    particles = sorted({p for p in NAME_BOUNDARY_PARTICLES if p}, key=len, reverse=True)

    def named(lo: int, hi: int) -> bool:
        return any(s["start"] == lo and s["end"] == hi and s["key"] not in derived for s in spans)

    out: list[dict] = []
    for i in range(len(runs)):
        for size in GENITIVE_WINDOW:
            win = runs[i:i + size]
            if len(win) < size or any(a >= b for a, b, _e in win):
                continue
            inner = win[:-1]
            gen = [n for n, (a, b, _e) in enumerate(inner) if b - a > len(GENITIVE_OF) and raw[a:b].endswith(GENITIVE_OF)]
            if len(gen) != 1 or any(raw[b:e].strip() for a, b, e in inner):  # one genitive, no mark inside
                continue
            g = gen[0]
            a_last, b_last, e_last = win[-1]
            # a request word glued to the last word ("…지수를보여줘") is taken off first, as the spaced form reads
            cuts = glued_request_splits(raw[a_last:b_last], lambda w: bool(w))
            if cuts:
                b_last = a_last + min(cuts)
            last = raw[a_last:b_last]
            for p in [""] + [p for p in particles if last.endswith(p) and len(last) > len(p)]:
                stem_end = b_last - len(p)
                left_lo, left_hi = win[0][0], inner[g][1] - len(GENITIVE_OF)
                right_lo = win[g + 1][0]
                left = raw[left_lo:left_hi]
                right = raw[right_lo:stem_end]
                key = index_key(left + right)
                name = words.title_names.get(key)
                if name is None:
                    continue
                nxt = runs[i + size][0] if i + size < len(runs) else None
                listed = index_key(p) in JOINERS or \
                    any(unicodedata.normalize("NFKC", ch) in SEPARATOR_PUNCT for ch in raw[b_last:e_last]) or \
                    (nxt is not None and index_key(raw[nxt:runs[i + size][1]]) in LIST_WORD_KEYS)
                tid = {t for t, obj, _item in rel.occurrences(key) if obj is None}  # every table of the name
                target = any(t in tid and rel.axis_roles.get((t, obj)) == "target"
                             for t, obj, _item in rel.occurrences(index_key(left)) if obj is not None)
                if listed or target or words.swappable(left, right) or (named(left_lo, left_hi)
                                                                       and named(right_lo, stem_end)):
                    break
                if any(s["start"] < stem_end and left_lo < s["end"]
                       and not (left_lo <= s["start"] and s["end"] <= stem_end) for s in spans + out):
                    break  # a name crosses the window's edge
                out.append({"start": left_lo, "end": stem_end, "quote": raw[left_lo:stem_end], "key": key,
                            "candidates": 0, "lookup": name})
                break
            if out and out[-1]["start"] == win[0][0]:
                break
    return out


def _spaced_names_before_glue(layer: CatalogLayer, raw: str, spans: list[dict]) -> list[dict]:
    """A catalog name written over a space whose last word has a request word glued to it ("가상 금리를보여줘",
    "가상연령별 가상지역별로보여줘"): the question read with that request word as spaces finds it as the spaced form
    does (plan 08). Only names that cross whitespace and end in such a word, over no other name span."""
    from .lexicon import PUNCT, glued_request_splits

    chars, ends = list(raw), []
    for m in re.finditer(r"\S+", raw):
        a, b = m.span()
        if any(s["start"] < b and a < s["end"] for s in spans):
            continue
        while b > a and unicodedata.normalize("NFKC", raw[b - 1]) in PUNCT:  # marks after the request word
            b -= 1
        cuts = glued_request_splits(raw[a:b], lambda w: bool(w))
        if cuts:
            cut = min(cuts)  # the longest request form glued at the end ("보여줘", not its "줘")
            chars[a + cut:b] = " " * (b - a - cut)
            ends.append((a, a + cut))
    if not ends:
        return []
    out = []
    for s in layer.name_spans().find("".join(chars)):
        # a shorter name inside it does not keep it out (plan 09 P1: "가상A 가상B 지표를보여줘" reads its mark-free
        # name as the spaced form does; the inner names go, as in the spaced form)
        if not any(a <= s["end"] <= b and s["start"] < a for a, b in ends) \
                or any(s["start"] < x["end"] and x["start"] < s["end"]
                       and not (s["start"] <= x["start"] and x["end"] <= s["end"]
                                and x["end"] - x["start"] < s["end"] - s["start"]) for x in spans):
            continue
        out.append(dict(s, quote=raw[s["start"]:s["end"]]))
    return out


def _table_then_value(layer: CatalogLayer, raw: str, spans: list[dict]) -> list[tuple[dict, dict]]:
    """A written word that starts with a table name (its full name or the name before its bracket, no spaces) with a
    value of that table glued right after it ("가상A표가상갑값 및 가상을값보여줘"): read as the spaced form, the table
    name and then the value name (found with the table name and the glued request words read as spaces; it may run
    over spaces). Only when the value is a value (an axis item) of a table of that name, it starts right at the cut
    and no name span crosses the word's edge. Pairs (table-name span, value span); the table name is exact when its
    key is a catalog name span key, else read through the name (``lookup``)."""
    from .lexicon import PUNCT, glued_request_splits

    words = catalog_words(layer)
    rel = relations_of(layer)
    index = layer.name_spans()
    blank = list(raw)
    for m in re.finditer(r"\S+", raw):  # glued request words as spaces, as ``_spaced_names_before_glue``
        a, b = m.span()
        while b > a and unicodedata.normalize("NFKC", raw[b - 1]) in PUNCT:
            b -= 1
        cuts = glued_request_splits(raw[a:b], lambda w: bool(w))
        if cuts:
            blank[a + min(cuts):b] = " " * (b - a - min(cuts))
    out: list[tuple[dict, dict]] = []
    for m in re.finditer(r"\S+", raw):
        a, b = m.span()
        low = raw[a:b].lower()
        if any(s["start"] < a < s["end"] for s in spans):
            continue  # a name crosses the word's start
        for n in range(len(low) - 1, GLUED_PREFIX_MIN - 1, -1):
            key = index_key(low[:n])
            if key not in words.title_names:
                continue
            tables = {t for t, obj, _item in rel.occurrences(key) if obj is None}
            chars = list(blank)
            chars[a:a + n] = " " * n
            found = [s for s in index.find("".join(chars)) if s["start"] == a + n]
            value = max(found, key=lambda s: s["end"], default=None)
            if value is None or not any(t in tables and obj is not None for t, obj, _item in
                                        rel.occurrences(value["key"])):
                continue
            if any(s["start"] < value["end"] < s["end"] for s in spans):
                continue  # a name crosses the value's end
            head = {"start": a, "end": a + n, "quote": raw[a:a + n], "key": key, "candidates": index.candidates(key)}
            if key not in index.keys:
                head["lookup"] = words.title_names[key]
            out.append((head, dict(value, quote=raw[value["start"]:value["end"]])))
            break
    return out


GLUED_PREFIX_MIN = 3  # letters of a table name read at the start of a glued word


def _in_bracket_part(raw: str, a: int, names: list[dict] | None) -> bool:
    """raw[a] lies after the first opening bracket of a catalog name holding it (the bracket words of a name stay
    qualifiers of its requirement, as the basis words there do: review of plan 08, held-out 9393)."""
    for s in names or []:
        if s["start"] <= a < s["end"]:
            at = next((s["start"] + n for n, ch in enumerate(raw[s["start"]:s["end"]])
                       if unicodedata.normalize("NFKC", ch) == "("), None)
            if at is not None and a > at:
                return True
    return False


def qualifier_spans_of(text: TextView, layer: CatalogLayer | None) -> list[dict]:
    """Word-boundary spans of the family qualifiers of the question, and of one qualifier written against a glued
    request word ("분기지표로보여줘", read as the spaced form: plan 08)."""
    if layer is None:
        return []
    rel = relations_of(layer)
    found = rel.find_qualifiers(text["raw"])
    if rel.qualifier_spans is None:
        return found
    q = rel.qualifier_spans
    glued = catalog_words(layer).stripped_name_spans(text["raw"], found, q.candidates, q.keys)
    return sorted(found + glued, key=lambda s: s["start"]) if glued else found


def _condition_bounds(text: TextView) -> dict[int, int]:
    """Raw start -> end of the period, frequency, basis and calc words of the question (the 2.2 rules), as the
    boundaries a name written against them may end or start at (plan 05 G)."""
    query = text["norm"]
    spans = [(r["i"], r["j"]) for r in _period_rows(query)]
    spans += [(i, j) for _, i, j in q_rules.find_frequencies(query)]
    spans += [(r["i"], r["j"]) for r in rules.q4_rows(query)]
    out = {}
    for i, j in spans:
        a, b = _raw_span(text, i, j)
        out[a] = max(out.get(a, b), b)
    return out


def _with_tails(layer: CatalogLayer, text: TextView, spans: list[dict]) -> list[dict]:
    tails = catalog_words(layer).and_name_spans(text["raw"])
    if not tails:
        return spans
    derived = layer.index.derived_keys  # plan 09 P1: a derived name never keeps an and-name tail out
    kept_tails = []
    for t in sorted(tails, key=lambda s: (-(s["end"] - s["start"]), s["start"])):
        if any(t["start"] < k["end"] and k["start"] < t["end"] for k in kept_tails):
            continue
        clash = [s for s in spans if s["start"] < t["end"] and t["start"] < s["end"] and s["key"] not in derived]
        if all(t["start"] <= s["start"] and s["end"] <= t["end"] and (s["end"] - s["start"]) < (t["end"] - t["start"])
               for s in clash):
            kept_tails.append(t)
    if not kept_tails:
        return spans
    out = [s for s in spans if not any(t["start"] <= s["start"] and s["end"] <= t["end"] for t in kept_tails)
           and not (s["key"] in derived and any(s["start"] < t["end"] and t["start"] < s["end"] for t in kept_tails))]
    return sorted(out + kept_tails, key=lambda s: s["start"])


def inside_name(a: int, b: int, names: list[dict] | None) -> bool:
    """raw[a:b] lies strictly inside a catalog-name span (the name is longer than the words)."""
    return bool(names) and any(s["start"] <= a and b <= s["end"] and s["end"] - s["start"] > b - a for s in names)


# A frequency or period word glued to a general noun ("연간지표", "분기자료") is part of a compound noun (data file).
_COMPOUND = rules.WORDS["time_word_compounds"]
_COMPOUND_NOUNS = tuple(sorted(_COMPOUND["nouns"], key=len, reverse=True))
_COMPOUND_KEEP = tuple(_COMPOUND["keep_after"])


def compound_time_word(words: str, after: str, before: str = "") -> bool:
    """``words`` (a frequency or period quote) is directly followed by a general noun of the data list (no space
    between) and does not end with a repetition mark (별, 마다). Inside an open bracket ("X(연간지표)") the word is a
    bracket qualifier of the name in front, which keeps its frequency (plan 04, 2-5 brackets), so it is not dropped."""
    if before.count("(") > before.count(")"):
        return False
    return not words.endswith(_COMPOUND_KEEP) and after.startswith(_COMPOUND_NOUNS)


# ---------------------------------------------------------------- period (reused Q2, new shape)

# The reused Q2 fragments, compiled alone to read where one end of a matched period stops.
_YEAR_PART = re.compile(q_rules._yr(9))  # a four-digit year with an optional 년, or a year alias
_SUB_PART = re.compile(q_rules._sub(9))  # quarter, month or half below the year, optional end mark
_SUB_GROUPS = (("q", "Q"), ("m", "M"), ("h", "H"))
_PERIOD_PATTERNS = {  # which reused pattern a row of each form came from, in the order find_periods tries them
    "range": (q_rules._RANGE,), "since": (q_rules._SINCE, q_rules._REL), "recent": (q_rules._RECENT,),
    "point": (q_rules._REL, q_rules._REL_MQ, q_rules._POINT),
}
# Unit words of the month/quarter relatives (지난달, 이번 분기) and the end mark (분기말, 작년 말): data file.
_RELATIVE_UNITS: dict[str, str] = rules.WORDS["period_relative_units"]
_END_MARK: str = rules.WORDS["period_end_mark"]


def _period_match(query: str, row: dict):
    for pattern in _PERIOD_PATTERNS[row["kind"]]:
        m = pattern.match(query, row["i"])
        if m and m.end() == row["j"]:
            return m
    raise ValueError(f"period row without its pattern: {row}")  # find_periods only returns these matches


def _expr(text: TextView, query: str, a: int, b: int, *, year=None, alias=None, sub=None, end_mark=None) -> PeriodExpr:
    while b > a and query[b - 1].isspace():
        b -= 1
    while a < b and query[a].isspace():
        a += 1
    ra, rb = _raw_span(text, a, b)
    written = text["raw"][ra:rb]
    unit, value = sub if sub else (None, None)
    return {"text": written, "year": year, "alias": alias, "sub_unit": unit, "sub_value": value,
            "end_mark": written.endswith(_END_MARK) if end_mark is None else end_mark}


def _end(text: TextView, query: str, m, n: int) -> PeriodExpr:
    """End n of a reused period match: its year (or alias) through its sub unit, as written."""
    g = m.groupdict()
    year, alias = g.get(f"y{n}"), g.get(f"a{n}")
    sub = next(((unit, g[f"{k}{n}"]) for k, unit in _SUB_GROUPS if g.get(f"{k}{n}")), None)
    if year or alias:
        a = m.start(f"y{n}") if year else m.start(f"a{n}")
        head = _YEAR_PART.match(query, a)
        b = head.end() if head else m.end(f"y{n}" if year else f"a{n}")
    else:  # range end without a year ("6월" of "2026년 1월부터 6월까지")
        a = b = m.start("omit")
    if sub:
        k = next(k for k, unit in _SUB_GROUPS if unit == sub[0])
        part = _SUB_PART.match(query, b)
        same = part and part.group(f"{k}9") == sub[1] and part.start(f"{k}9") == m.start(f"{k}{n}")
        b = part.end() if same else m.end(f"{k}{n}")
    return _expr(text, query, a, b, year=year, alias=alias, sub=sub)


def _period_attrs(text: TextView, query: str, row: dict) -> dict:
    m = _period_match(query, row)
    g = m.groupdict()
    form = row["kind"]
    start = end = None
    ends = []
    if m.re is q_rules._RANGE:
        start = _end(text, query, m, 1)
        end = _end(text, query, m, 3 if g.get("omit") else 2)
        ends = [1, 3 if g.get("omit") else 2]
    elif m.re is q_rules._SINCE or m.re is q_rules._POINT:
        start = _end(text, query, m, 1)
        ends = [1]
    elif m.re is q_rules._REL:
        if g.get("olinto"):
            word = g["olinto"].split()[0]
            a = m.start("olinto")
            start = _expr(text, query, a, a + len(word), alias=word, end_mark=False)
        else:
            start = _end(text, query, m, 1)
            ends = [1]
            if g.get("eoy"):
                start = _expr(text, query, m.start("a1"), m.end("eoy"), alias=g["a1"],
                              sub=(start["sub_unit"], start["sub_value"]) if start["sub_unit"] else None, end_mark=True)
    elif m.re is q_rules._REL_MQ:
        written = m.group(0)
        unit_word = next(w for w in sorted(_RELATIVE_UNITS, key=len, reverse=True) if written.endswith(w))
        alias = written[:-len(unit_word)].strip()
        start = _expr(text, query, m.start(), m.end(), alias=alias, sub=(_RELATIVE_UNITS[unit_word], None),
                      end_mark=False)
    invalid = any(q_rules._unit(m, n) is None for n in ends)
    recent = form == "recent"
    return {"form": form, "start_expr": start, "end_expr": end, "open_end": form == "since",
            "amount": row.get("amount") if recent else None, "unit": row.get("unit") if recent else None,
            "relative": bool(row.get("relative")), "invalid": invalid, "extract_status": row["status"]}


def _period_rows(query: str) -> list[dict]:
    return q_rules.find_periods(query, _PROBE_AS_OF)


def find_period_conditions(text: TextView) -> list[Condition]:
    """Periods with form, start/end expressions as written, relative/invalid marks (not resolved to dates).

    ``extract_status`` keeps the old status as if a reference date were known: "unresolved" only when the old rules
    could not resolve the period even with one (month/quarter relatives, recent months, invalid units, year wrap).
    """
    query = text["norm"]
    return [_condition(text, "period", n, row["i"], row["j"], None, _period_attrs(text, query, row))
            for n, row in enumerate(_period_rows(query), 1)]


# ---------------------------------------------------------------- frequency (reused Q3, stretch 1)


def frequency_condition(row: dict) -> Condition:
    """One reused-Q3 row ({condition_id, status, quote, span, value[, anchor]}) -> a frequency condition.

    The old ``status`` is always "explicit" for frequencies and is not carried (see tests_v3/attr_map.csv).
    """
    span = row["span"]
    if span is None:  # Q3 rows always cover at least one character; never expected
        raise ValueError("frequency row without a span")
    return {
        "id": row["condition_id"],
        "kind": "frequency",
        "quote": row["quote"],
        "span": [span[0], span[1]],
        "value": row["value"],
        "attrs": {"value": row["value"], "anchor": row.get("anchor")},
        "status": "candidate",
    }


def find_frequency_conditions(text: TextView) -> list[Condition]:
    """Explicit frequencies (reused Q3 rules, unchanged) in the new shape. IDs F1, F2, ... in question order."""
    rows = frequencies(text["raw"], text["norm"], text["offsets"])
    return [frequency_condition(row) for row in rows]


# ---------------------------------------------------------------- basis, calc, unit (old Q4)


def _q4_conditions(text: TextView, kind: str) -> list[Condition]:
    rows = [r for r in rules.q4_rows(text["norm"]) if r["kind"] == kind]
    return [_condition(text, kind, n, r["i"], r["j"], r["value"],
                       {"axis": r["axis"], "value": r["value"], "polarity": r["polarity"], "origin": r["origin"]})
            for n, r in enumerate(rows, 1)]


def find_basis_conditions(text: TextView) -> list[Condition]:
    """Basis words (명목/실질, 원계열/계절조정, 신규취급액/잔액 기준, 말잔/평잔, currency, survey view, valuation,
    survey method, classification "X별") with axis, value and polarity."""
    return _q4_conditions(text, "basis")


def find_unit_conditions(text: TextView) -> list[Condition]:
    """Requested units ("십억 원 단위", "%로"); a percentage value or "몇 %" is not a unit."""
    return [_condition(text, "unit", n, i, j, value, {"value": value, "currency": rules.unit_currency(value)})
            for n, (value, i, j) in enumerate(rules.find_units(text["norm"]), 1)]


def find_calc_conditions(text: TextView) -> list[Condition]:
    """Comparison bases (전년동기비, 전기비, "1년 전보다", ...) and calculation requests ("몇 %")."""
    return _q4_conditions(text, "calc")


# ---------------------------------------------------------------- breakdown, exclude (old Q5)


def _taken(text: TextView, names: list[dict] | None = None) -> list[tuple[int, int]]:
    """Positions of the period, frequency, basis/calc and unit conditions (the old Q5 input).

    Name protection comes first (M1-1 review): a period or frequency strictly inside a catalog-name span is part of
    the name (``settle`` drops it later), so it does not block an exclusion or list name over the same words.
    """
    query = text["norm"]

    def kept(i: int, j: int) -> bool:
        a, b = _raw_span(text, i, j)
        return not inside_name(a, b, names) and not compound_time_word(query[i:j].rstrip(), query[j:], query[:i])

    out = [(p["i"], p["j"]) for p in _period_rows(query) if kept(p["i"], p["j"])]
    out += [(i, j) for _, i, j in q_rules.find_frequencies(query) if kept(i, j)]
    # a basis or calc word strictly inside a catalog name is part of it too (review of plan 08, variant FX1n:
    # "가상수출 전망은 빼줘" excludes the name "가상수출 전망")
    out += [(r["i"], r["j"]) for r in rules.q4_rows(query) if not inside_name(*_raw_span(text, r["i"], r["j"]), names)]
    out += [(i, j) for _, i, j in rules.find_units(query)]
    return out


def _name_norm_spans(text: TextView, names: list[dict] | None) -> list[tuple[int, int]]:
    """The catalog-name spans in positions of the normalised question."""
    offsets = text["offsets"]
    out = []
    for s in names or []:
        idx = [n for n, o in enumerate(offsets) if s["start"] <= o < s["end"]]
        if idx:
            out.append((idx[0], idx[-1] + 1))
    return out


def _q5(text: TextView, layer: CatalogLayer | None,
        names: list[dict] | None = None) -> tuple[list[Condition], list[Condition]]:
    query = text["norm"]
    if names is None:
        names = name_spans_of(text, layer)
    excludes, breakdowns = rules.q5_rows(query, _taken(text, names), _names(layer), _name_norm_spans(text, names))
    xs = [_condition(text, "exclude", n, r["i"], r["j"], r["text"],
                     {"text": r["text"], "complement_hints": list(r["complement_hints"])})
          for n, r in enumerate(excludes, 1)]
    rs = [_condition(text, "breakdown", n, r["i"], r["j"], r["text"],
                     {"subkind": "list", "text": r["text"], "source": r["source"], "measure_pair": r["measure_pair"]})
          for n, r in enumerate(breakdowns, 1)]
    return xs, rs


def find_breakdown_conditions(text: TextView, layer: CatalogLayer | None = None) -> list[Condition]:
    """Listed names ("A·B와 C") corrected with catalog names of the same table; quotes stay as written."""
    return _q5(text, layer)[1]


def find_exclude_conditions(text: TextView, layer: CatalogLayer | None = None) -> list[Condition]:
    """Exclusions ("A를 뺀", "A 제외") with complement hints; a calculation definition in brackets is not one."""
    return _q5(text, layer)[0]


# ---------------------------------------------------------------- relation, function, display (new)


def _markers(text: TextView, kind: str) -> list[Condition]:
    """Relation, function and display conditions; the attribute is named after the kind ({"relation": "ratio"})."""
    return [_condition(text, kind, n, i, j, None, {kind: value})
            for n, (value, i, j) in enumerate(rules.find_markers(text["norm"], kind), 1)]


def find_relation_conditions(text: TextView) -> list[Condition]:
    """Relation words between requirements: "의 몇 %", "로 나눈", "대비", "차이", "비중"."""
    return _markers(text, "relation")


def find_function_conditions(text: TextView) -> list[Condition]:
    """Function words: "로 나눠서", "를 뺀", "중", "가운데", "각각"."""
    return _markers(text, "function")


def find_display_conditions(text: TextView) -> list[Condition]:
    """Display words: "추이", "비교", "구성", "나란히"."""
    return _markers(text, "display")


# ---------------------------------------------------------------- family qualifiers (plan 04, A-7)


def find_family_conditions(text: TextView, layer: CatalogLayer | None = None,
                           names: list[dict] | None = None) -> list[Condition]:
    """Bracket qualifiers of table families written in the question ("가상용도별" of "가상대출금(가상용도별)"), as basis
    conditions (axis "family", origin "catalog_family"), so that a requirement a family qualifier tells apart keeps
    it as a condition ID (plan 04, 5-1: the conditions are the one source). Word-boundary spans as for names; a
    qualifier inside a catalog name is part of the name."""
    if layer is None:
        return []
    raw, out = text["raw"], []
    for n, s in enumerate(qualifier_spans_of(text, layer), 1):
        a, b = s["start"], s["end"]
        if inside_name(a, b, names) and not _in_bracket_part(raw, a, names):
            continue  # inside a name, a qualifier is part of it, except in its bracket part ("X(대출태도)")
        polarity = "-" if rules._NEGATION.match(raw[b:b + rules.NEG_WINDOW]) else "+"
        value = s["key"]
        out.append({"id": f"{ID_PREFIX['basis']}{n}", "kind": "basis", "quote": raw[a:b], "span": [a, b],
                    "value": value, "attrs": {"axis": "family", "value": value, "polarity": polarity,
                                              "origin": "catalog_family"}, "status": "candidate"})
    return out


# ---------------------------------------------------------------- calc hints (not conditions; user decision 10/18 b)

_CALC_HINTS = [(re.compile(p["regex"]), p["kind"]) for p in rules.WORDS["calc_hints"]["patterns"]]


def find_calc_hints(text: TextView, names: list[dict] | None = None) -> list[dict]:
    """Change expressions ("늘었", "변했", "증가율") as internal marks {quote, span, kind}: not conditions, outside
    catalog names, found before predicate stripping so they are neither stripped nor counted as leftover words."""
    norm, found, taken = text["norm"], [], []
    for pattern, kind in _CALC_HINTS:
        for m in pattern.finditer(norm):
            i, j = m.span()
            if i >= j or any(i < b and a < j for a, b in taken):
                continue
            a, b = _raw_span(text, i, j)
            if names and any(s["start"] < b and a < s["end"] for s in names):
                continue
            taken.append((i, j))
            found.append({"quote": text["raw"][a:b], "span": [a, b], "kind": kind})
    return sorted(found, key=lambda h: h["span"])


# ---------------------------------------------------------------- all kinds


def _overlap(a: Condition, b: Condition) -> bool:
    return a["span"][0] < b["span"][1] and b["span"][0] < a["span"][1]


def _contains(a: Condition, b: Condition) -> bool:
    return a["span"][0] <= b["span"][0] and b["span"][1] <= a["span"][1] and a["span"] != b["span"]


def _inside_name(c: Condition, names: list[dict]) -> bool:
    """Strictly inside a catalog-name span (the name is longer than the condition words)."""
    return inside_name(c["span"][0], c["span"][1], names)


def settle(conditions: list[Condition], name_spans: list[dict] | None = None) -> list[Condition]:
    """Overlaps between kinds, then IDs renumbered per kind in question order.

    - periods and frequencies strictly inside a catalog name are dropped (plan 04, 2-5: they are part of the name);
    - value kinds win in the order period, frequency, basis, calc, unit, exclude, breakdown;
    - a relation, function or display word that overlaps a kept condition is dropped unless it holds the whole
      condition ("의 몇 %" holds the calc "몇 %"; "대비" inside "전년 대비" and "를 뺀" inside "에너지를 뺀" go).
    """
    cands = [c for c in conditions
             if not (name_spans and c["kind"] in ("period", "frequency") and _inside_name(c, name_spans))
             and not (c["kind"] in ("period", "frequency") and c.get("compound"))]
    cands = [{k: v for k, v in c.items() if k != "compound"} for c in cands]
    kept: list[Condition] = []
    for kind in KIND_ORDER:
        for c in sorted((c for c in cands if c["kind"] == kind), key=lambda c: c["span"]):
            if kind in VALUE_KINDS:
                # a word strictly inside a catalog name is part of the name: it blocks no exclusion over that name
                # ("가상수출 전망은 빼줘": review of plan 08, variant FX1n)
                clash = any(_overlap(c, k) and not (kind == "exclude" and _inside_name(k, name_spans or []))
                            for k in kept)
            else:
                clash = any(_overlap(c, k) and not _contains(c, k) for k in kept)
            if not clash:
                kept.append(c)
    kept.sort(key=lambda c: (c["span"][0], c["span"][1], KIND_ORDER.index(c["kind"])))
    count: dict[str, int] = {}
    out = []
    for c in kept:
        count[c["kind"]] = count.get(c["kind"], 0) + 1
        out.append(dict(c, id=f"{ID_PREFIX[c['kind']]}{count[c['kind']]}"))
    return out


def _drop_name_byeol(found: list[Condition], names: list[dict], raw: str, layer: CatalogLayer) -> list[Condition]:
    """10/19 21:45 (variant B2): a word ending in the X-byeol mark that is a word of a catalog name written with a
    space ("가상특별" of the name "가상특별 자금": a space and more words of the name follow it) is part of the name,
    not a basis, when it names no axis of any table (no axis name, no X-byeol word of a table name). Classification
    words in brackets ("수입물가지수(기본분류)", "가상대출금(가상용도별)") stay conditions."""
    from .lexicon import BYEOL

    words = catalog_words(layer)
    axes = set().union(*words.axis_words.values()) if words.axis_words else set()

    def name_part(c: Condition) -> bool:
        if c["kind"] != "basis" or (c.get("attrs") or {}).get("axis") != CLASSIFICATION_AXIS:
            return False
        end = c["span"][1]
        if not c["quote"].endswith(BYEOL) or not raw[end:end + 1].isspace():
            return False
        key = words.axis_key(c["quote"])
        return _inside_name(c, names) and not any(key in x for x in axes)

    return [c for c in found if not name_part(c)]


def find_conditions(text: TextView, layer: CatalogLayer | None = None) -> list[Condition]:
    """All condition candidates in question order: period, frequency, basis, unit, breakdown, exclude, relation,
    calc, function, display. Overlaps between kinds are settled here (``settle``); with a catalog layer, periods and
    frequencies inside catalog names (word-boundary name spans, year-range-free names included) are dropped."""
    names = name_spans_of(text, layer)
    excludes, breakdowns = _q5(text, layer, names)
    found = (find_period_conditions(text) + find_frequency_conditions(text) + find_basis_conditions(text)
             + find_calc_conditions(text) + find_unit_conditions(text) + excludes + breakdowns
             + find_relation_conditions(text) + find_function_conditions(text) + find_display_conditions(text))
    raw = text["raw"]
    for c in found:  # a frequency or period word glued to a general noun ("연간지표") is a compound noun
        if c["kind"] in ("period", "frequency") and compound_time_word(c["quote"], raw[c["span"][1]:],
                                                                       raw[:c["span"][0]]):
            c["compound"] = True
    # family qualifiers where no period, frequency, basis, calc or unit condition is written over the same words
    # (a listed name over them gives way: the qualifier is the basis of that list member)
    spans = [c["span"] for c in found if c["kind"] in ("period", "frequency", "basis", "calc", "unit")
             and not c.get("compound")]
    found += [f for f in find_family_conditions(text, layer, names)
              if not any(f["span"][0] < b and a < f["span"][1] for a, b in spans)]
    if layer is not None and names:
        found = _drop_name_byeol(found, names, text["raw"], layer)
    found = settle(found, names)
    extra = clause_exclusions(text, found, layer)
    return settle(found + extra, names) if extra else found


def clause_exclusions(text: TextView, conditions: list[Condition], layer: CatalogLayer | None) -> list[Condition]:
    """Plan 05 E: exclusion is the polarity of a clause. A clause (cut as the phrase parser 2.3 cuts it) whose
    exclusion condition runs to its predicate ("…는 빼줘", "…는 필요 없어") excludes every catalog name of the
    clause, whatever joins them (와/과, 및, comma, middle dot); each name not yet excluded gets its own exclusion
    condition. A negated exclusion verb ("제외하지 말고") made no exclusion condition (2.2), so it excludes nothing.

    Plan 08 (finding 3): the object of every exclusion is read on the token layer. An exclusion that starts inside a
    catalog name ("가상상품 수지 없이") excludes the whole name; the catalog values coordinated with the excluded name
    right before it ("A, B 및 C 없이": joiner particles, commas, middle dots, conjunctive list words) are excluded
    with it, unless a bare word before the list modifies it (the old Q5 list rule on the token layer's joiners)."""
    if layer is None or not any(c["kind"] == "exclude" for c in conditions):
        return []
    from .phrases import clause_tokens  # the parser's clause cut over the token layer (phrases imports this)

    raw, out = text["raw"], []
    stream = []
    for clause, body_end in clause_tokens(text, conditions, layer):
        stream += clause
        a, b = clause[0].start, clause[-1].send
        excl = [c for c in conditions if c["kind"] == "exclude" and a <= c["span"][0] < b]
        if not excl or max(c["span"][1] for c in excl) < body_end:
            continue  # no exclusion reaching the predicate: an exclusion inside the clause stays local ("A를 뺀 B")
        x_end = max(c["span"][1] for c in excl)
        if any(t.start >= x_end and ((t.kind == "word" and t.label in ("request", "clause_ends"))
                                     or (t.kind == "cond" and t.cond_kind == "display")) for t in clause):
            continue  # the exclusion verb is no predicate ("B 없이 보여줘", "B를 빼고 보여줘"): local to B
        taken = [c["span"] for c in conditions if c["kind"] in ("exclude", "period", "frequency", "basis", "calc",
                                                                 "unit")]
        x_start = min(c["span"][0] for c in excl)
        for t in clause:
            if t.kind != "name" or t.negated or any(s[0] <= t.start and t.end <= s[1] for s in taken):
                continue
            if any(t.end <= w.start < x_start and w.request_form == "final" for w in clause):
                continue  # a name the question requests before the exclusion ("A를 보여줘 B는 빼줘") stays asked
            out.append(_exclusion(raw, t.start, t.end))
    return out + _exclusion_objects(raw, stream, conditions, out, layer)


def _exclusion(raw: str, a: int, b: int, name_end: int | None = None) -> Condition:
    """An exclusion over raw[a:b] whose excluded name is raw[a:name_end] (the whole span by default)."""
    name = raw[a:b if name_end is None else name_end]
    return {"id": "X0", "kind": "exclude", "quote": raw[a:b], "span": [a, b], "value": name,
            "attrs": {"text": name, "complement_hints": rules.complement_hints(name)}, "status": "candidate"}


def _coordinated_before(toks: list, k: int) -> list:
    """Name tokens coordinated with ``toks[k]`` right before it: a joiner particle written against the earlier name
    ("A와 B", "A하고 B"), or a comma, a middle dot or a conjunctive list word between them ("A, B", "A 및 B")."""
    from .lexicon import DOTS, JOINERS, LEX, COMMA

    conj = {index_key(w) for w in LEX["coordinator"]["conjunctive"]}
    out, n = [], k
    while n > 0:
        prev = toks[n - 1]
        if prev.kind == "name" and prev.suffix in JOINERS:
            j = n - 1
        elif (prev.kind == "punct" and (prev.label == COMMA or prev.label in DOTS)) \
                or (prev.kind == "word" and prev.norm in conj):
            j = n - 2
            if j < 0 or toks[j].kind != "name" or toks[j].suffix:
                break
        else:
            break
        out.insert(0, toks[j])
        n = j
    return out


def _modified(toks: list, first) -> bool:
    """A bare word right before a list modifies it ("수입 A와 B"): no particle on it, no condition, no boundary word."""
    from .lexicon import CLOSE, LIST_BOUNDARY

    k = next((n for n, t in enumerate(toks) if t is first), 0)
    if k == 0:
        return False
    prev = toks[k - 1]
    if prev.kind == "punct":
        return prev.label in CLOSE
    if prev.kind == "cond" or prev.suffix or (prev.kind == "word" and prev.ending):
        return False
    return prev.kind in ("word", "name", "qual") and prev.norm not in LIST_BOUNDARY


def _exclusion_objects(raw: str, toks: list, conditions: list[Condition], made: list[Condition],
                       layer: CatalogLayer) -> list[Condition]:
    names = layer.list_names()
    covered = [list(c["span"]) for c in conditions if c["kind"] == "exclude"] + [list(c["span"]) for c in made]
    taken = [c["span"] for c in conditions if c["kind"] in ("period", "frequency", "basis", "calc", "unit")]
    out = []
    for x in [c for c in conditions if c["kind"] == "exclude"]:
        xs, xe = x["span"]
        idx = [n for n, t in enumerate(toks) if t.kind == "name" and not t.negated]
        host = next((n for n in idx if toks[n].start < xs < toks[n].end), None)
        if host is not None:  # the exclusion starts inside a name: the whole name is its object
            out.append(_exclusion(raw, toks[host].start, xe, toks[host].end))
            covered.append([toks[host].start, xe])
            first = host
        else:
            first = next((n for n in idx if xs <= toks[n].start and toks[n].end <= xe), None)
        if first is None:
            continue
        members = _coordinated_before(toks, first)
        if not members or _modified(toks, members[0]) or not all(names.tables(t.quote) for t in members):
            continue
        for t in members:
            if t.negated or any(s[0] <= t.start and t.end <= s[1] for s in covered + taken):
                continue
            out.append(_exclusion(raw, t.start, t.end))
            covered.append([t.start, t.end])
    return out
