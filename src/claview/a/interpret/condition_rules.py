"""Rules of find_conditions (detail 2.2) other than periods and frequencies (those are the reused Q2/Q3 copies).

Ported from the old A pipeline (commit 55fd41b). The rule structure is kept; the condition vocabulary moved to
``data/condition_words.json`` and the grammar words (X-byeol mark, negation, units, list, exclusion and marker words)
to ``data/lexicon.json`` (``lexicon``, plan 08), in the same order (the order decides overlaps):
- basis words and "X별" classification words: src/claview/aflow/rules.py find_basis (:641-667) with BASIS_AXES,
  _NOT_BEFORE, _AXIS, _FREQ_AXIS; polarity from src/claview/aflow/q_nodes.py q4 (:83-96)
- comparison bases and calculation requests (the old hand-off conditions, kind calc in the new shape):
  rules.py find_handoff (:682-707) with _THAN and HANDOFF_PATTERNS
- units: rules.py find_units (:750-755)
- lists, list refinement and exclusions: rules.py find_lists, _clean_chain, _token_before, _phrase_after,
  list_modified, exclude_list_members, refine_lists, find_excludes (:319-530) and the condition part of q_nodes.py
  q5 (:316-348)
New here: relation, function and display words (detail 2.2), as plain word patterns.

Positions are indices of the normalised question (``text["norm"]``); the finders turn them into raw spans.
One change to the old output: an "X별" classification word stops after "별" (a following "로" is a particle, not part
of the word), as the evidence specification writes these conditions ("기업규모별", "지역별").
"""

from __future__ import annotations

import json
import re

from .. import paths
from ..catalog.names import CatalogNames
from . import lexicon as LX

WORDS = json.loads(paths.CONDITION_WORDS.read_text(encoding="utf-8"))  # condition vocabulary (basis axes, calc, period)
_WORDS = WORDS
_WORD = LX.WORD_CHAR
LIST_DOTS = LX.LIST_DOTS_NORM
NEG_WINDOW = 12  # characters after a basis word read for a negation (old q4)
MIN_NAME_CHARS = 2  # shortest exclusion or list name (old find_excludes / q5)
MIN_JOINED_WORD = 3  # a word that ends in a list joiner joins only when it is this long (old find_lists)


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in words)


def _spaced(word: str) -> str:
    """The characters of a word with optional whitespace between them ("월말 잔액" for 월말잔액)."""
    return r"\s*".join(map(re.escape, word))


def _phrase(words: str) -> str:
    """A word list entry that may hold a space ("하지 않"): any whitespace there; after the negated verb ending
    ("지") an auxiliary particle of the lexicon may stand ("하지는 않", "하지도 않"; finding 4)."""
    parts = [re.escape(p) + (_AUX if p.endswith(LX.EXCLUDE_NEGATED_ENDING) else "") for p in words.split()]
    return r"\s*".join(parts)


_AUX = rf"(?:{'|'.join(re.escape(a) for a in LX.AUX)})?"


# ---------------------------------------------------------------- basis words and "X별" (old Q4 find_basis)

BASIS_AXES: dict[str, dict[str, list[str]]] = _WORDS["basis_axes"]
CALC_AXES = frozenset(_WORDS["calc_axes"])
_NOT_BEFORE = {form: rf"(?!\s*(?:{_alt(words)}))" for form, words in _WORDS["basis_not_followed_by"].items()}
_BYEOL_MARK = LX.BYEOL
_AXIS = re.compile(rf"(?<!{_WORD})(?P<word>(?P<head>{_WORD}{{2,}}?){re.escape(_BYEOL_MARK)})"
                   rf"(?:{_alt(LX.BYEOL_PARTICLES)})?{LX.WORD_END}")
_FREQ_AXIS = LX.FREQUENCY_HEADS
_BASIS_PATTERNS = [
    (axis, value, re.compile(rf"(?<!{_WORD})" + _spaced(form) + _NOT_BEFORE.get(form, "")))
    for axis, values in BASIS_AXES.items() for value, forms in values.items()
    for form in sorted(forms, key=len, reverse=True)
]
_NEGATION = re.compile(rf"\s*(?:(?:{_alt(LX.NEGATION_TAIL_WORDS)})\s*)?"  # a tail word between ("기준이 아니라")
                       rf"(?:{_alt(LX.BASIS_NEGATION_PARTICLES)})?\s*(?:{'|'.join(_phrase(w) for w in LX.BASIS_NEGATION_WORDS)})")


def _free(i: int, j: int, taken) -> bool:
    return all(j <= a or i >= b for a, b in taken)


def find_basis(query: str) -> list[tuple[str, str, int, int]]:
    """(axis, value, start, end). Dictionary words first (from a word start; spaces between letters allowed), then
    the remaining "X별" as classification ``X별``; frequency words ("연도별", "월별") are left to frequencies."""
    found, taken = [], []
    for axis, value, pattern in _BASIS_PATTERNS:
        for m in pattern.finditer(query):
            i, j = m.span()
            if _free(i, j, taken):
                taken.append((i, j))
                found.append((axis, value, i, j))
    for m in _AXIS.finditer(query):
        if m.group("head") in _FREQ_AXIS or not _free(m.start(), m.end(), taken):
            continue
        i, j = m.span("word")
        taken.append((i, j))
        found.append(("classification", m.group("word"), i, j))
    return sorted(found, key=lambda x: x[2])


# ---------------------------------------------------------------- comparison bases and calculation (old find_handoff)

_CALC_PATTERNS = [(re.compile(p["regex"].replace("{than}", _WORDS["calc_than"])), p["axis"], p["value"])
                  for p in _WORDS["calc_patterns"]]


def find_handoff(query: str, taken) -> list[tuple[str, str, int, int]]:
    """(axis, value, start, end) of comparison phrases and "몇 %"; spans in ``taken`` (basis words) are skipped. At
    one place the earlier pattern wins."""
    found, spans = [], list(taken)
    for pattern, axis, value in _CALC_PATTERNS:
        for m in pattern.finditer(query):
            i, j = m.span()
            if _free(i, j, spans):
                spans.append((i, j))
                found.append((axis, value, i, j))
    return sorted(found, key=lambda x: x[2])


def q4_rows(query: str) -> list[dict]:
    """Basis and calc rows in question order, as the old Q4 found them:
    {axis, value, i, j, polarity ("+"/"-"), origin ("dictionary"/"handoff_rule"), kind ("basis"/"calc")}."""
    found = find_basis(query)
    handoff = find_handoff(query, [(i, j) for _, _, i, j in found])
    rows = []
    tagged = [(*f, "dictionary") for f in found] + [(*h, "handoff_rule") for h in handoff]
    for axis, value, i, j, origin in sorted(tagged, key=lambda x: x[2]):
        polarity = "-" if _NEGATION.match(query[j:j + NEG_WINDOW]) else "+"
        rows.append({"axis": axis, "value": value, "i": i, "j": j, "polarity": polarity, "origin": origin,
                     "kind": "calc" if axis in CALC_AXES else "basis"})
    return rows


# ---------------------------------------------------------------- units (old find_units)

_UNITS = LX.UNITS
_COUNT = re.escape(_UNITS["count_word"])
_UNIT = re.compile(rf"(?:(?:{_alt(_UNITS['scales'])})\s*)?(?:{_alt(_UNITS['money'])})\s*"
                   rf"{re.escape(_UNITS['unit_word'])}"
                   rf"|(?<![0-9{_COUNT}.])(?<![0-9{_COUNT}]\s)(?:{_alt(_UNITS['percent'])})")
_MONEY = re.compile(rf"(?:{_alt(_UNITS['scales'])})?(?P<base>{_alt(_UNITS['money'])})")


def find_units(query: str) -> list[tuple[str, int, int]]:
    """(unit, start, end): "X 단위" (the X, spaces removed) or a percent sign/word; a percent value ("5%") or a
    percent request ("몇 %") is not a unit."""
    out = []
    for m in _UNIT.finditer(query):
        value = re.sub(r"\s+", "", m.group(0)).removesuffix(_UNITS["unit_word"])
        for word in _UNITS["percent"]:
            value = value.replace(word, _UNITS["percent_value"])
        out.append((value, m.start(), m.end()))
    return out


def unit_currency(value: str) -> str | None:
    """Currency named by a money unit ("십억원" -> 원화, "백만달러" -> 달러); None for other units."""
    m = _MONEY.fullmatch(value or "")
    return _UNITS["money_currency"].get(m["base"]) if m else None


# ---------------------------------------------------------------- lists and exclusions (old Q5)

_JOINERS = LX.BARE_JOINERS
_AND = LX.AND_WORD
_SUBJECT = LX.LIST_SUBJECT
_GENITIVE = LX.GENITIVE_OF
_PARTICLE = re.compile(rf"(?:{_alt(LX.LIST_TAIL)})$")
_LIST_BOUNDARY = LX.LIST_BOUNDARY
_TOKEN_TAIL = re.compile(rf"(?:{_alt(LX.TOKEN_TAIL)})$")
_EX = LX.EXCLUSION
# an exclusion verb followed by its own negation ("제외하지 말고") excludes nothing
_EXCLUDE = re.compile(rf"({_WORD}+?)(?:{_alt(_EX['particles'])})?\s*(?:{_alt(_EX['verbs'])}|{LX.EXISTENCE_NEGATION})"
                      rf"(?!{_WORD}*{re.escape(_EX['negated_ending'])}{_AUX}\s*(?:{_alt(_EX['negated_by'])}))"
                      rf"|({_WORD}+?){re.escape(_EX['suffix'])}")
_DEFINITION_SOURCE = re.compile(rf"{re.escape(_EX['definition_source'])}\s*$")
_DEFINITION_TAIL = re.compile(rf"\s*(?:{_alt(_EX['definition_tails'])})\s*$")


def nospace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def complement_hints(name: str) -> list[str]:
    return [h.replace("{name}", name) for h in _EX["hints"]]


def find_lists(query: str, known=None) -> list[list[tuple[str, int, int]]]:
    """Name chains joined by a middle dot or "와/과 + space"; per chain [(name, start, end)] with the joiner cut off.

    ``known(name)`` true keeps a final "이/가" of the last name (a catalog name such as "생산자물가").
    """
    out = []
    for m in re.finditer(rf"{_WORD}+(?:(?:\s*[{LIST_DOTS}]\s*|\s+){_WORD}+)*", query):
        chunk, base = m.group(0), m.start()
        parts = [(p.group(0), base + p.start(), base + p.end()) for p in re.finditer(rf"{_WORD}+", chunk)]
        links = []
        for (a, _ai, aj), (_b, bi, _bj) in zip(parts, parts[1:]):
            gap = query[aj:bi]
            links.append(any(d in gap for d in LIST_DOTS) or (a[-1] in _JOINERS and len(a) >= MIN_JOINED_WORD))
        chain: list[tuple[str, int, int]] = []
        for k, part in enumerate(parts):
            if chain and links[k - 1]:
                chain.append(part)
            else:
                if len(chain) >= 2:
                    out.append(_clean_chain(chain, query, known))
                chain = [part]
        if len(chain) >= 2:
            out.append(_clean_chain(chain, query, known))
    return out


def _clean_chain(chain, query, known=None):
    rows = []
    for k, (word, i, j) in enumerate(chain):
        if k < len(chain) - 1 and word[-1] in _JOINERS and not any(d in query[j:chain[k + 1][1]] for d in LIST_DOTS):
            word, j = word[:-1], j - 1
        if k == len(chain) - 1:
            stripped = _PARTICLE.sub("", word)
            if word[-1] in _SUBJECT and known and known(word):
                stripped = word
            elif known and not known(stripped):  # plan 08: a glued request word ("가상B를보여줘") is peeled off
                stripped = peel_glued(word, known) or stripped
            j -= len(word) - len(stripped)
            word = stripped
        rows.append((word, i, j))
    return rows


def peel_glued(word: str, known) -> str | None:
    """The one known name a written word holds before the lexicon's request forms and particles glued to it, taken
    off from the right one layer at a time ("가상B를보여줘" -> 가상B); None when no layer leads to a known name or
    two different ones do (no guessing)."""
    found = LX.glued_name_cuts(word, lambda w: len(w) >= MIN_NAME_CHARS and known(w))
    return word[:found.pop()] if len(found) == 1 else None


def _token_before(query: str, i: int):
    """The word right before i (one space between): (word, start, end) or None."""
    m = re.search(rf"({_WORD}+) $", query[:i])
    return (m.group(1), m.start(1), m.end(1)) if m else None


def _phrase_after(query: str, j: int) -> list[tuple[str, int, int]]:
    """Words after j, one space apart, up to and including the first word with a particle or ending."""
    out, k = [], j
    while (m := re.match(rf" ({_WORD}+)", query[k:])):
        word, a, b = m.group(1), k + 1, k + m.end()
        out.append((word, a, b))
        k = b
        if _TOKEN_TAIL.search(word) or not re.match(rf" {_WORD}", query[k:]):
            break
    return out


def list_modified(query: str, i: int, taken: list, names: CatalogNames) -> bool:
    """The word right before the first listed name is a bare noun that modifies it (not a condition, boundary word or
    a word with a particle). A closing bracket right before ("금리(잔액 기준) 흐름과") counts as a modifier."""
    if query[:i].endswith(") "):
        return True
    before = _token_before(query, i)
    return bool(before and _free(before[1], before[2], taken) and before[0] not in _LIST_BOUNDARY
                and (names.known(before[0]) or not _TOKEN_TAIL.search(before[0])))


def exclude_list_members(query: str, chains: list, excludes: list, taken: list,
                         names: CatalogNames) -> list[tuple[str, int, int]]:
    """When an excluded name ends a list ("식료품과 에너지를 뺀"), the earlier names are excluded too, if the list is
    not modified and every earlier name is a catalog value."""
    starts = {i for _, i, _, _ in excludes}
    out = []
    for chain in chains:
        if chain[-1][1] in starts and not list_modified(query, chain[0][1], taken, names) \
                and all(names.tables(w) for w, _, _ in chain[:-1]):
            out.extend(chain[:-1])
    return out


def refine_lists(query: str, chains: list, taken: list, names: CatalogNames) -> list[list[dict]]:
    """Chains refined with the catalog: [{text, i, j, source[, measure_pair]}] groups (old Q5 improvements).

    1. a chain that splits one value name is joined again (catalog_merge);
    2. the tail of the last name is given to earlier names when that makes values of the same table (catalog_tail);
    3. after "와/과" the two name phrases are compared: a shared head word that makes values of one table is given to
       both (catalog_head); two indicator phrases are marked ``measure_pair`` (marked, never removed).
    """
    out = []
    for chain in chains:
        rows = [{"text": w, "i": i, "j": j, "source": "list"} for w, i, j in chain]
        joined = "".join(r["text"] for r in rows)
        whole = names.item(joined) or names.item("".join(r["text"] for r in rows[:-1]) + _AND + rows[-1]["text"])
        if whole:
            out.append([{"text": whole, "i": rows[0]["i"], "j": rows[-1]["j"], "source": "catalog_merge"}])
            continue
        for r in rows:  # "W 및 X" is one value name
            m = re.search(rf"({_WORD}+) {re.escape(_AND)} $", query[:r["i"]])
            if m and (name := names.item(m.group(1) + _AND + r["text"])):
                r.update(text=name, i=m.start(1), source="catalog_merge")
        last = rows[-1]["text"].removesuffix(_BYEOL_MARK)
        for n in range(len(last) - 1, 0, -1):
            tail, hit = last[-n:], False
            tables = names.tables(last)
            for r in rows[:-1]:
                if r["source"] == "list" and (cand := names.item(r["text"] + tail, tables)):
                    r.update(text=cand, source="catalog_tail")
                    hit = True
            if hit:
                break
        parts, cur = [], [rows[0]]
        before = _token_before(query, rows[0]["i"])
        modified = list_modified(query, rows[0]["i"], taken, names)
        for a, b in zip(rows, rows[1:]):
            gap = query[a["j"]:b["i"]]
            keep = True
            if not any(d in gap for d in LIST_DOTS):
                raw = re.match(rf"{_WORD}+", query[b["i"]:]).group(0)
                after = _phrase_after(query, b["i"] + len(raw)) if raw == b["text"] else []
                genitive = raw == b["text"] + _GENITIVE and bool(_phrase_after(query, b["i"] + len(raw)))
                if genitive and modified and not (names.known(a["text"]) and names.known(b["text"])):
                    keep = False
                elif after:
                    head = _PARTICLE.sub("", after[-1][0])
                    h = _PARTICLE.sub("", after[0][0])
                    shared = len(after) == 1 and names.item(a["text"] + h) and names.item(b["text"] + h)
                    if shared and names.tables(a["text"] + h) & names.tables(b["text"] + h):
                        a.update(text=names.item(a["text"] + h), source="catalog_head")
                        b.update(text=names.item(b["text"] + h), j=after[0][1] + len(h), source="catalog_head")
                    elif nospace(head) == nospace(a["text"]):
                        left = f"{before[0]} {a['text']}" if modified else a["text"]
                        right_j = after[-1][1] + len(head)
                        right = query[b["i"]:right_j]
                        lt, rt = names.tables(left), names.tables(right)
                        if lt & rt:
                            a.update(text=names.item(left, lt & rt), i=before[1] if modified else a["i"],
                                     source="catalog_head")
                            b.update(text=names.item(right, lt & rt), j=right_j, source="catalog_head")
                        else:
                            keep = False
                    elif modified and not (names.known(a["text"]) and names.known(b["text"])):
                        keep = False
            if not keep:
                a["measure_pair"] = b["measure_pair"] = True
            cur.append(b)
        if modified and before is not None and rows[0]["source"] == "list" and not names.known(before[0]) \
                and before[1] < rows[0]["i"]:
            # plan 04 (2.7): the first listed name is quoted with the bare noun that modifies it ("가상매출액
            # 증가율과 가상영업이익률"), so the words of the question it stands on are inside one condition; the
            # value stays the listed name
            rows[0]["i"] = before[1]
        parts.append(cur)
        out.extend(p for p in parts if len(p) >= 2)
    return out


_MODIFIER_END = tuple(LX.MODIFIER_ENDINGS)
_OBJECT_END = tuple(LX.OBJECT)
# verbs that make the list something else: exclusion ("뺀", "제외한"), combining ("합친"), dividing ("나눈")
_NOT_MODIFIER = frozenset(nospace(w) for w in LX.EXCLUDE_VERBS + LX.COMBINE_WORDS) | LX.DIVIDE_ADNOMINAL | LX.DIVIDE


def modifies_head(query: str, part: list[dict], names: CatalogNames) -> bool:
    """Plan 09 P2b: "A와 B C를 <adnominal verb> N" ("가상갑과 가상을 변동을 제거한 가상지수"): the last item ends in an
    object particle, an unknown verb with a modifier ending follows and a catalog name N comes right after it. The
    list then names what the verb acts on, a modifier of N, not values to break N down by: it makes no breakdown list
    (the words stay leftover words). Not when a listed word is a catalog name or an X-byeol word, a relation word
    stands in the phrase, or the verb excludes, combines or divides."""
    last = part[-1]
    m = re.match(rf"{_WORD}+", query[last["i"]:])
    if m is None:
        return False
    words = [(m.group(0), last["i"], last["i"] + m.end())]
    if not _TOKEN_TAIL.search(m.group(0)):
        words += _phrase_after(query, words[-1][2])
    end_word, _a, end = words[-1]
    if not end_word.endswith(_OBJECT_END):
        return False
    verb = re.match(rf" ({_WORD}+)", query[end:])
    if verb is None or not verb.group(1).endswith(_MODIFIER_END) or verb.group(1) in _NOT_MODIFIER:
        return False
    head = _phrase_after(query, end + verb.end())
    if not head or not any(names.known(_PARTICLE.sub("", "".join(w for w, _i, _j in head[:n])))
                           for n in range(1, len(head) + 1)):
        return False
    listed = [r["text"] for r in part] + [w for w, _i, _j in words[1:]]
    for w in listed:
        stem = _PARTICLE.sub("", w)
        if names.known(stem) or stem.endswith(_BYEOL_MARK):
            return False
    lo, hi = part[0]["i"], end + verb.end()
    return not any(lo <= i and j <= hi for _v, i, j in find_markers(query, "relation"))


def find_excludes(query: str) -> list[tuple[str, int, int, list[str]]]:
    """(name, start, end, complement_hints); the span covers the exclusion words. "A에서 B를 뺀 것" in brackets is a
    calculation definition, not an exclusion; "(직접투자 제외 증권투자분)" stays one."""
    out = []
    for m in _EXCLUDE.finditer(query):
        name = m.group(1) or m.group(2)
        if len(name) < MIN_NAME_CHARS:
            continue
        lo, hi = query.rfind("(", 0, m.start()), query.find(")", m.end())
        if (lo >= 0 and hi >= 0 and ")" not in query[lo:m.start()]
                and _DEFINITION_SOURCE.search(query[lo + 1:m.start()])
                and _DEFINITION_TAIL.fullmatch(query[m.end():hi])):
            continue
        out.append((name, m.start(), m.end(), complement_hints(name)))
    return out


def _to_name_ends(chain: list, query: str, protected, known) -> list:
    """Name protection first (10/19, variant B2): a middle-dot list member that is no catalog name by itself and whose
    end falls inside a catalog-name span ("가상A·가상B 가상C" with the name "가상B 가상C" written with a space) runs to
    the end of that name, unless that reaches the next member. A member that is a name by itself keeps its words: the
    words after it may be the list's shared head ("가상A·가상B 잔액")."""
    out = []
    for k, (word, i, j) in enumerate(chain):
        nxt = chain[k + 1][1] if k + 1 < len(chain) else len(query)
        dotted = k > 0 and any(d in query[chain[k - 1][2]:i] for d in LIST_DOTS)  # a middle-dot member only
        for a, b in (protected or ()) if dotted and not known(word) else ():
            if a <= i < b and i < j < b and b <= nxt:
                word, j = query[i:b], b
                break
        out.append((word, i, j))
    return out


def q5_rows(query: str, taken: list[tuple[int, int]], names: CatalogNames,
            protected: list[tuple[int, int]] = ()) -> tuple[list[dict], list[dict]]:
    """(excludes, breakdowns) as the old Q5 found its conditions.

    ``taken``: positions of the period, frequency, basis/calc and unit conditions; exclusions and list names there are
    dropped. Excludes {text, i, j, complement_hints}; breakdowns {text, i, j, source, measure_pair}.
    """
    taken = list(taken)
    cond_taken = list(taken)

    def known(w):
        return names.known(w) or names.in_titles(w)

    chains = [_to_name_ends(c, query, protected, known) for c in find_lists(query, known)]
    # an exclusion written inside a catalog name ("에너지이외" as a value name) is part of the name (name protection
    # first: review of plan 08, held-out 9393/9494)
    found = [x for x in find_excludes(query) if _free(x[1], x[2], taken)
             and not any(a <= x[1] and x[2] <= b for a, b in protected)]
    members = [(n, i, j, complement_hints(n))
               for n, i, j in exclude_list_members(query, chains, found, cond_taken, names) if _free(i, j, taken)]
    excludes = []
    for name, i, j, hints in sorted(found + members, key=lambda x: x[1]):
        taken.append((i, j))
        excludes.append({"text": name, "i": i, "j": j, "complement_hints": hints})
    breakdowns = []
    for part in refine_lists(query, chains, cond_taken, names):
        if modifies_head(query, part, names):
            continue  # plan 09 P2b: the listed words modify the head name after them; no breakdown list

        # names that overlap another condition or are one letter are dropped; a list needs two names left, except a
        # single value name joined from a split chain
        kept = [r for r in part if len(nospace(r["text"])) >= MIN_NAME_CHARS and _free(r["i"], r["j"], taken)]
        if len(kept) < 2 and not (kept and kept[0]["source"] == "catalog_merge"):
            continue
        breakdowns.extend({"text": r["text"], "i": r["i"], "j": r["j"], "source": r["source"],
                           "measure_pair": bool(r.get("measure_pair"))} for r in kept)
    return excludes, breakdowns


# ---------------------------------------------------------------- relation, function and display words (new)

_MARKER_PARTICLES = _alt(sorted(LX.MARKER_PARTICLES, key=len, reverse=True))


def _marker_pattern(entry: dict) -> re.Pattern:
    start = rf"(?<!{_WORD})" if entry["start"] == "word" else ""
    end = rf"(?=(?:{_MARKER_PARTICLES})?{LX.WORD_END})" if entry["end"] == "word" else ""
    return re.compile(f"{start}(?:{entry['regex']}){end}")


MARKERS = {kind: [(_marker_pattern(e), e["value"]) for e in LX.MARKER_ENTRIES[kind]]
           for kind in ("relation", "function", "display")}


def find_markers(query: str, kind: str) -> list[tuple[str, int, int]]:
    """(value, start, end) of the relation, function or display words of ``kind``; at one place the earlier entry
    of the word list wins."""
    found, taken = [], []
    for pattern, value in MARKERS[kind]:
        for m in pattern.finditer(query):
            i, j = m.span()
            if i < j and _free(i, j, taken):
                taken.append((i, j))
                found.append((value, i, j))
    return sorted(found, key=lambda x: x[1])
