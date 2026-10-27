"""Condition attribution over the list items of a question (plan 04, 2-4; detail 2.5 temporary, 2.7 final).

Each condition (period, frequency, basis, calc, unit, breakdown, exclude) gets an owner item, or is common to every
requirement, or is ambiguous. The specification (modules/a/tests/spec/test_spec_requirements.py, lists ATTRIBUTION,
TAIL_BASIS, DESIGNATION and COUNTER) is the reference; the rules below are written to meet it, first rule first:

- Positions. The items of the question (from 2.3) are its segments; the first starts at the question start, the last
  ends at its phrase end (the first word closing a noun phrase with 을/를/은/는, or 이/가 on an unknown word of three
  or more letters, or the start of a request; a noun phrase continues over an adnominal verb when a catalog word
  follows). Before the first non-condition word is the head; after the phrase end, and in later clauses, the tail.
- Inside an item (and in front of every item but the first): that item's (rule 2). In front of the first item: its
  own when the same kind also sits in another item (the parallel form), else periods and frequencies are common and
  the others ambiguous (rules 2, 5, 6).
- Tail (rules 1, 3, 4, 5, 7, 8; the clause of the condition is the part between sentence ends and connective endings):
  1. a designation in the condition's clause ("X는·X만·X에만", the nearest one before the condition, else after)
     that names one item gives it to that item. A designation naming several items ("A와 B에만", "금리만"), naming
     none ("전자에만", an item not in the list), or a clause that negates or excludes ("에만 적용하지 말고",
     "A만 빼고") makes it ambiguous. The lone "기준은" names nothing; "차이는·비율은" (relation words) are not
     designations;
  2. without a designation, a condition in another sentence is ambiguous (periods and frequencies common);
  3. when another item holds a basis of the same axis, the condition is the last item's when it follows the last
     phrase directly, else ambiguous;
  4. a topic in the clause that is not an item ("설명은", "색상은") makes it ambiguous: its "모두·각각·비교" belong to
     that topic;
  5. "모두·각각·둘 다·함께" in the clause, or a comparison word (차이·격차·비교·견주) at the end of the last phrase,
     in the clause or in the first clause, makes it common;
  6. otherwise periods and frequencies are common and the rest ambiguous.
Ported from the rules of the old pipeline the specification was moved from (read only): the measure-scope reading of
modules/a/claview_aflow/rules.py (measure_scopes, _tail_owner, _designee, _measure_names), on the new items and tokens.

The rules read the written words of the token layer (``tokens.Words``: runs without whitespace, their spans and what
the lexicon says of them), never the question text with patterns of their own (plan 08 T1). A part of the question
read alone (an item clause, ``Words.clip``) keeps its positions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import tokens as W

KIND_CHAR = {"period": "P", "frequency": "F", "basis": "B", "unit": "U", "breakdown": "R", "exclude": "X",
             "calc": "K"}
REPEATABLE = frozenset("PF")


def nospace(s: str) -> str:
    return "".join(s.split())


@dataclass
class Plan:
    segments: list[tuple[int, int]]
    end: int
    own: list[list[str]] = field(default_factory=list)
    common: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    shared: dict[str, list[int]] = field(default_factory=dict)  # condition ID -> items of the clause it is common to


def phrase_end(words: W.Words, start: int, request_at, known, in_condition=None) -> int | None:
    """End of the last noun phrase that starts at ``start`` (old measure_scopes 'end'). The phrase continues over an
    adnominal verb only to a catalog word that is no condition word (``in_condition``: positions of the condition
    words): "A와 B를 가상조정한 실질 분기로" ends at "B를", its conditions stay tail conditions (plan 09, user decision
    10/19: rules 5-7 make them common or ambiguous, never the last item's alone)."""
    end = None
    pending = False
    found_name = False
    prev_cond = False
    view = words.clip(start, None)
    for m in view:
        b = m.end
        nxt = view.next_start(b)
        word = W.strip_trailing_marks(m.text)
        here_cond = bool(in_condition and in_condition(m.start))
        if pending:
            bare = W.strip_list_particle(word)
            # a general noun after a basis value ("실질 금액으로") is part of that value, no head noun
            tail_noun = prev_cond and W.is_basis_tail_noun(bare)
            found_name = found_name or bool(known and len(bare) >= 2 and known(bare) and not here_cond
                                            and not tail_noun)
        prev_cond = here_cond
        subject = W.ends_with_subject(word) and len(word) >= 3 and not (known and known(word))
        if W.ends_with_phrase_end(word) or subject or request_at(nxt):
            if end is None:
                end = b
                after = view.word_at(nxt)
                if not request_at(nxt) and after is not None \
                        and W.ends_adnominal(W.strip_trailing_marks(after.text)) \
                        and not (known and known(after.text)):
                    pending = True
                    continue
                break
            if found_name:
                end = b
            break
    return end


def measure_names(words: W.Words, segments: list, spans: list[tuple[int, int]], end: int) -> list[str]:
    """Names of the items for designation matching (spaces removed): bracket notes, condition words and the "기준~"
    word after them, and the comparison noun closing the last phrase are not part of a name."""
    names = []
    for k, (a, b) in enumerate(segments):
        out, after_cond = [], False
        for m in words.clip(a, b, blank_notes=True):
            p = m.start
            in_cond = any(i <= p < j for i, j in spans)
            if in_cond or (after_cond and W.starts_with_base_word(m.text)):
                after_cond = in_cond
                continue
            after_cond = False
            out.append(m)
        if k == len(segments) - 1 and out and words.compare_word(out[-1].text, out[-1].start) and len(out) > 1:
            out = out[:-1]
        names.append("".join(W.nfkc(x.text) for x in out))
    return names


def _designee(names: list[str], words: list, k: int, skip, view: W.Words):
    p, word = words[k]
    stem = W.designation_stem(word)
    if stem is None or len(stem) < 2 or skip(p) or W.starts_with_base_word(stem):
        return None
    if view.relation_start(stem, p, len(stem)):
        return "common"  # "차이는 신규취급액 기준으로": the relation, not one item
    hits = None
    for n in range(1, min(k + 1, 4) + 1):
        phrase = "".join(W.nfkc(w) for _, w in words[k - n + 1:k]) + stem
        found = [x for x, name in enumerate(names) if phrase in name]
        if not found:
            break
        hits = found
        if len(found) == 1:
            break
    if hits is None:
        return "unsure" if W.restricts(word) else None
    if k > 0 and W.ends_with_bare_joiner(words[k - 1][1]):
        prev = W.nfkc(words[k - 1][1])[:-1]
        if len(prev) >= 2 and any(prev in name for name in names):
            return "unsure"
    return hits[0] if len(hits) == 1 else "unsure"


def _tail_owner(words: W.Words, segments: list, names: list, end: int, i: int, other_basis: bool, skip):
    breaks = words.break_ends(end, i)
    start = breaks[-1] if breaks else end
    stop = words.first_break(i)
    seq = [(m.start, m.text) for m in words.clip(None, stop) if m.start >= start]
    clause_of, n = [], 0
    for _, w in seq:
        clause_of.append(n)
        if W.ends_clause(w):
            n += 1
    here = next((clause_of[k] for k, (p, w) in enumerate(seq) if p <= i < p + len(w)), None)
    if here is None:
        here = max((clause_of[k] for k, (p, _) in enumerate(seq) if p < i), default=0)
    marks = [(k, _designee(names, seq, k, skip, words)) for k, (p, w) in enumerate(seq)
             if W.designation_stem(w) is not None]
    marks = [(k, d) for k, d in marks if d is not None]
    for c in {clause_of[k] for k, _ in marks}:
        text = " ".join(w for k, (_, w) in enumerate(seq) if clause_of[k] == c)
        if W.has_designation_negation(text):
            return "unsure"
    mine = [(seq[k][0], d) for k, d in marks if clause_of[k] == here]
    before = [d for p, d in mine if p < i]
    after = [d for p, d in mine if p >= i]
    if before or after:
        chosen = before[-1] if before else after[0]
        if chosen != "common" or not other_basis:
            return chosen
        # "차이는 신규취급액 기준으로" while an item holds its own basis of that axis: the conflict rule decides
        return len(segments) - 1 if not words.any_between(end, i) else None
    if breaks:
        return None
    if other_basis:
        return len(segments) - 1 if not words.any_between(end, i) else None
    clause = [(p, w) for k, (p, w) in enumerate(seq) if clause_of[k] == here]
    foreign = any(W.ends_with_topic(w) and len(w) >= 3 and not words.compare_word(w, p)
                  and not W.starts_with_base_word(w) and not skip(p) for k, (p, w) in enumerate(seq) if clause_of[k] == here)
    if foreign:
        return None
    if any(W.has_common_marker(w) for _p, w in clause):
        return "common"
    last = words.last_before(end)
    first = [(p, w) for k, (p, w) in enumerate(seq) if clause_of[k] == 0]
    if (last is not None and words.compare_word(last.text, last.start)) \
            or any(words.compare_word(w, p) for p, w in clause + first):
        return "common"
    return None


def scopes(words: W.Words, starts: list[int], end: int, conds: list[tuple]) -> Plan:
    """Owner of each condition over the items starting at ``starts`` (the first item from the question start) with
    the last phrase ending at ``end``. ``conds``: (condition ID, kind letter, start, end, basis axis or None)."""
    bounds = [0, *starts[1:], end]
    segments = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    ranges = [(c[2], c[3]) for c in conds if c[2] is not None and c[1] != "R"]
    head_end = 0
    for m in words.clip(None, segments[0][1]):
        if any(i <= m.start < j for i, j in ranges):
            continue
        head_end = m.start
        break

    def place(pos):
        if pos is None or pos >= end:
            return "tail"
        if pos < head_end:
            return "head"
        return next((k for k, (a, b) in enumerate(segments) if a <= pos < b), "tail")

    places = {c[0]: place(c[2]) for c in conds}
    kinds: dict[str, set] = {}
    for c in conds:
        where = places[c[0]]
        if where != "tail":
            kinds.setdefault(c[1], set()).add(0 if where == "head" else where)
    names = measure_names(words, segments, ranges, end)
    # a designation word inside an exclusion ("가상A금리만 빼고") is still read: its clause negates (rule 1)
    skips = [(c[2], c[3]) for c in conds if c[2] is not None and c[1] not in ("R", "X")]

    def skip(p):
        return any(a <= p < b for a, b in skips)

    owned_groups = {(c[1], c[4]) for c in conds if c[4] is not None and isinstance(places[c[0]], int)}
    plan = Plan(segments=segments, end=end, own=[[] for _ in segments])
    for c in conds:
        cid, kind, i = c[0], c[1], c[2]
        where = places[cid]
        repeated = len(kinds.get(kind, ())) >= 2
        if isinstance(where, int):
            plan.own[where].append(cid)
        elif where == "head" and repeated:
            plan.own[0].append(cid)
        elif where == "tail" and i is not None:
            group = c[4]
            owner = _tail_owner(words, segments, names, end, i, (kind, group) in owned_groups and group is not None,
                                skip)
            if isinstance(owner, int):
                plan.own[owner].append(cid)
            elif owner == "unsure":
                plan.ambiguous.append(cid)
            elif owner == "common" or kind in REPEATABLE:
                plan.common.append(cid)
            else:
                plan.ambiguous.append(cid)
        elif kind in REPEATABLE:
            plan.common.append(cid)
        else:
            plan.ambiguous.append(cid)
    return plan


def period_scopes(words: W.Words, starts: list[int], conds: list[tuple]) -> Plan:
    """Owners when the list is cut by its own periods ("2014년 A 기준과 2015년 B 기준 ..."): every part runs to the
    next part (the last to the end of the question). Periods and basis words are their part's; any other condition is
    its part's in a middle part and common in the last part (the shared tail: "… 가상매출액 증가율과 가상영업이익률을")."""
    size = words.size
    bounds = [0, *starts[1:], size]
    segments = [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]
    plan = Plan(segments=segments, end=size, own=[[] for _ in segments])
    last = len(segments) - 1
    for c in conds:
        cid, kind, i = c[0], c[1], c[2]
        k = next((n for n, (a, b) in enumerate(segments) if i is not None and a <= i < b), None)
        if k is None:
            (plan.common if kind in REPEATABLE else plan.ambiguous).append(cid)
        elif kind in ("P", "B", "K") or k < last:
            plan.own[k].append(cid)
        else:
            plan.common.append(cid)
    return plan


def shared_head(words: W.Words, prev_name: tuple[int, int], name: tuple[int, int], rest: tuple[int, int],
                cond_ranges: list, together) -> tuple[int, int] | None:
    """"X A와 B": the words X before A (at most two, not conditions, not ending in a particle or an adnominal verb)
    also modify B when B is alone in its item and X and B (or B's own part) are found together in one table. ``rest``:
    the span after B to the end of its requirement (only comparison words may stand there)."""
    if any(not words.compare_word(m.text, m.start) for m in words.clip(*rest)):
        return None
    found = []
    for m in reversed(list(words.clip(None, prev_name[0]))):
        w = m.text
        if any(x <= m.start < y or x < m.end <= y for x, y in cond_ranges) \
                or W.closes_phrase_before_head(w) or W.ends_adnominal(w) or len(found) == 2:
            break
        bare = W.strip_genitive(w)
        found.insert(0, (m.start, m.start + len(bare), bare))
    if not found:
        return None
    head = " ".join(w for _, _, w in found)
    left, right = nospace(words.text(*prev_name)), nospace(words.text(*name))
    pre = len(os.path.commonprefix([left, right]))
    suf = len(os.path.commonprefix([left[::-1], right[::-1]]))
    core = right[pre:len(right) - suf] if len(right) - pre - suf >= 2 else right
    if not (together(head, right) or together(head, core)):
        return None
    return found[0][0], found[-1][1]
