"""split_fragments (detail 2.3, plan 04 2-1): the phrase structure of a question and its fragments.

The question is read as the tokens of the token layer (``tokens``: catalog names, conditions, family qualifiers, calc
hints, words, punctuation, read once after 2.2), then each clause is read top-down in the order of plan 04 2.3:
relation -> appositive -> negation -> topic -> complement -> modifier -> list. What is left at the bottom are the
list items the judgement table (2-2) reads. The rules read the tokens and what the token layer says of them (labels,
stems and endings, request forms); the words of every class come from ``lexicon``.

- Clauses: a sentence end (. ? !) or a request verb that ends a clause (보여주고) starts a new clause.
- Predicate: from the end of each clause only known request words (and frequency, period, display and function
  conditions around them) are taken off; the first unknown word, name or content condition stops it (2-5, R3-04).
- Negation: "A가 아니라 B", "A 말고 B": the last name, qualifier or condition before the marker is negated and
  leaves the items. Appositive: "즉", "그러니까", or a bracket holding an indicator or an unknown word after a name.
- Topic: two or more names with the topic particle (은/는) cut the clause into topic segments.
- Modifier: a genitive ("X의 A와 B") or "X 중 A" whose right side holds a catalog name. A genitive that only carries a
  calculation ("A의 전년 대비 증가율과 B") or a relation word ("A와 B의 차이") is no modifier (M2).
- List: items are cut at "와/과/이랑/랑/하고" after a word, at "및/그리고/또는", at "·" and ",", at the ratio word
  "대비" and at "중" outside brackets; an item made only of periods and frequencies joins the next item
  ("2019년과 2021년 가계대출"). A list that ends in "X와 (같이)" takes X as the last item.
- Fragments: every item (and modifier) gives the contiguous runs of its tokens without periods, frequencies, display
  words and stripped request words; runs keep the particles written against their last token.

The reading is a pure function of (text, conditions, layer) and is cached, so attach_terms and judge_fragments read
the same structure. Names inside one fragment are never cut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import paths
from ..catalog.text import index_key
from ..contract.shapes import Condition, Fragment, TextView
from .lexicon import (BARE_JOINERS, BASE_WORD, BASIS_TAIL_NOUNS, BYEOL, CLOSE, COMBINE, COMPLEMENT,  # noqa: F401
                      CONNECTIVE, CONNECTIVE_STEMS, DESIGNATE, DISTRIBUTIVE, DIVIDE,
                      DIVIDE_ADNOMINAL, DIVIDE_ADVERBS, DOTS, ENDINGS, GENITIVE, GENITIVE_OF, JOINER_SUFFIXES,
                      JOINERS, LABELS, MODIFIER_ENDINGS, OBJECT, OPEN, PHRASE_END, PUNCT, RELATION_LIST_SEPARATORS,
                      SENTENCE_END, SEPARATOR_PUNCT, SIMULTANEOUS, SOURCE_ENDINGS, SUBJECT, TOPIC, TOPIC_LEAD_TAILS)
from .tokens import (MARKER_KINDS, VALUE_KINDS, Tok, Words, glued_request_starts, key_of,  # noqa: F401
                     split_ending, tokenize, word_label)

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer

# phrase-rule parameters (no words): the relation kinds a scope phrase reads as one requirement (plan 05 A)
_RULES = json.loads(paths.FRAGMENT_WORDS.read_text(encoding="utf-8"))
SCOPE_RELATIONS = frozenset(_RULES["scope_relations"])  # "X 중 B가 차지하는 비중": one requirement over X (plan 05 A)
REMOVED_KINDS = frozenset({"period", "frequency", "display"})  # not part of fragments (2.3)
MIN_JOINED_WORD = 3  # an unknown word ending in a joiner joins only when it is this long (old find_lists)


def _marker_of(t: Tok, words: frozenset) -> bool:
    """A word or function condition of the clause that is one of ``words`` (its ending off)."""
    if t.kind not in ("word", "cond"):
        return False
    return t.norm in words or t.stem in words


# ---------------------------------------------------------------- reading


@dataclass(eq=False)
class Item:
    toks: list[Tok]
    phrase: str = "item"  # item, modifier, negated, appositive, topic
    fragment_ids: list[str] = field(default_factory=list)
    after_dot: bool = False  # the item follows a middle dot of a list
    before_dot: bool = False  # a middle dot follows the item
    axis_list: bool = False  # a member of the list a dividing verb takes ("나이와 사는 곳을 나눠")
    related: bool = False  # read in a clause with a relation word: no breakdown inside the item

    @property
    def start(self) -> int:
        return self.toks[0].start

    @property
    def end(self) -> int:
        return self.toks[-1].send


@dataclass(eq=False)
class Group:
    """One list of items read together (a clause, or a topic segment of one)."""

    items: list[Item]
    modifier: list[Item] = field(default_factory=list)
    modifier_kind: str | None = None  # genitive, among
    relation: str | None = None  # ratio, diff, share_of, sum, net, percent_of
    appositive: bool = False
    complement: bool = False  # "N을 L로": the first item is N, the others its complement list
    designation: bool = False  # a later clause naming only earlier items ("가상B금리는 신규취급액 기준으로"): no item
    combined: bool = False  # "A와 B를 상계한 순액": the list is the material of one value
    by_period: bool = False  # parts cut by their own periods (the old period split)
    unsure_periods: bool = False  # two periods with words but no joiner between them
    distributive: bool = False  # plan 05 B(1): the clause holds a distributive marker ("각각", "따로")
    simultaneous: bool = False  # plan 05 B(1): the clause holds a simultaneous marker ("동시에", "함께")
    clause_end: int = 0  # end of the clause the group stands in (its predicate included)
    clause: int = 0
    start: int = 0
    end: int = 0


@dataclass
class Reading:
    toks: list[Tok]
    names: list[dict]
    groups: list[Group]
    fragments: list[Fragment]
    negated: list[Tok]
    layer: object = None
    raw: str = ""
    words: Words | None = None  # the written words of the question (token layer), for the attribution rules


def _is_joiner(t: Tok) -> bool:
    if t.kind == "punct":
        return False
    if t.suffix in JOINER_SUFFIXES:
        return True
    if t.kind == "word" and t.label in ("unknown", "basis_word"):
        k = t.norm
        return len(k) >= MIN_JOINED_WORD and k[-1] in BARE_JOINERS
    if t.kind == "word" and t.label == "ignore":  # "2014년 기준과 2015년 기준": a filler word with a joiner
        return bool(t.stem) and t.ending in JOINERS
    if t.kind == "word" and t.label == "particle":  # "가상A금리(잔액 기준)와": the joiner after a bracket
        return t.norm in JOINER_SUFFIXES
    return False


def _is_separator(t: Tok, depth: int) -> bool:
    if depth:
        return False
    if t.kind == "punct" and t.label in SEPARATOR_PUNCT:
        return True
    if t.kind == "word" and t.label == "list_words":
        return True
    if t.kind == "cond" and t.cond_kind == "relation" and t.cond["attrs"].get("relation") == "ratio" \
            and t.norm in RELATION_LIST_SEPARATORS:
        return True
    return t.kind == "cond" and t.cond_kind == "function" and t.cond["attrs"].get("function") == "among"


def _content(t: Tok) -> bool:
    """A token that can make an item by itself."""
    if t.kind in ("name", "qual", "hint"):
        return True
    if t.kind == "cond":
        return t.cond_kind in ("basis", "calc", "unit")
    return t.kind == "word" and t.label in ("unknown", "basis_word")


def _time(t: Tok) -> bool:
    return t.kind == "cond" and t.cond_kind in ("period", "frequency")


def _each(t: Tok) -> bool:
    return t.kind == "cond" and t.cond_kind == "function" and t.cond["attrs"].get("function") == "each"


def _only_time(item: list[Tok]) -> bool:
    """Only periods and frequencies (with "각각" and punctuation): no word of its own."""
    return bool(item) and all(t.kind == "punct" or _each(t)
                              or (t.kind == "cond" and t.cond_kind in ("period", "frequency")) for t in item) \
        and any(t.kind == "cond" and t.cond_kind in ("period", "frequency") for t in item)


def _has_freq(item: list[Tok]) -> bool:
    return any(t.kind == "cond" and t.cond_kind == "frequency" for t in item)


def _dot_lists(seq: list[Tok]) -> set[int]:
    """Indices of middle dots inside "A·B X": a dot list whose last member is followed by more words of the same
    phrase (no particle on it) is one phrase whose head X every member shares ("가계대출·기업대출 잔액"); the dots do
    not cut it. A dot list that ends the phrase ("상품·서비스수지를") is a list of items."""
    def dot(i: int) -> bool:
        return 0 <= i < len(seq) and seq[i].kind == "punct" and seq[i].label in DOTS

    keep: set[int] = set()
    n = 0
    while n < len(seq):
        if not dot(n):
            n += 1
            continue
        dots = [n]
        while dot(dots[-1] + 2):  # member, dot, member, dot, ...
            dots.append(dots[-1] + 2)
        last = dots[-1] + 1
        member = seq[last] if last < len(seq) else None
        nxt = seq[last + 1] if last + 1 < len(seq) else None
        followed = nxt is not None and (nxt.kind in ("name", "word", "qual") or _byeol(nxt)) \
            and nxt.start > member.end and _content(nxt) if member is not None else False
        # "가계대출·기업대출 잔액" where "기업대출 잔액" is one name: the list member ends inside the name
        inside = member is not None and bool(member.members) and member.members[-1]["span"][1] < member.end
        if member is not None and ((followed and not member.suffix) or inside) and not _is_joiner(member) \
                and not any(seq[d].cut for d in dots):
            keep.update(dots)
        n = last + 1
    return keep


def split_list(seq: list[Tok]) -> list[list[Tok]]:
    items: list[list[Tok]] = []
    cur: list[Tok] = []
    depth = 0
    inner_dots = _dot_lists(seq)
    for n, t in enumerate(seq):
        if t.kind == "punct" and t.label in OPEN:
            depth += 1
        elif t.kind == "punct" and t.label in CLOSE:
            depth = max(0, depth - 1)
        if _is_separator(t, depth) and n not in inner_dots:
            if cur:
                items.append(cur)
            cur = []
            continue
        cur.append(t)
        rest = seq[n + 1:]
        if depth == 0 and _is_joiner(t) and any(_content(x) or _time(x) for x in rest):
            items.append(cur)
            cur = []
    if cur:
        items.append(cur)
    # "X와 (같이)": a list ending in a joiner takes its last name as the last item
    if items and items[-1] and _is_joiner(items[-1][-1]) and len(items[-1]) >= 2:
        last = items[-1]
        cut = None
        for k in range(len(last) - 2, -1, -1):
            if last[k].suffix in PHRASE_END or (last[k].kind == "hint" and last[k].suffix):
                cut = k + 1
                break
        if cut and any(_content(x) for x in last[:cut]):
            items[-1:] = [last[:cut], last[cut:]]
    # periods and frequencies alone join the next item ("2019년과 2021년 가계대출"); at the end of the list they are
    # tail conditions, not an item, unless they continue a list of frequencies ("연간과 분기로")
    merged: list[list[Tok]] = []
    carry: list[list[Tok]] = []  # the time-only items waiting for the next item, one list each
    for it in items:
        if _only_time(it):
            carry.append(it)
            continue
        merged.append([t for c in carry for t in c] + it)
        carry = []
    flat = [t for c in carry for t in c]
    if carry and merged and _has_freq(flat) and _has_freq(merged[-1]):
        merged += carry  # "연간과 분기와 월간으로": every listed frequency stays its own item
    elif carry and not merged:
        merged.append(flat)
    return [it for it in merged if it]


def connective(t: Tok) -> bool:
    """A predicate with a connective ending that ends a clause ("비교하되", "보여주고", "적용하고", "하고")."""
    if t.kind == "cond" and t.cond_kind == "display":
        return any(t.suffix.endswith(e) for e in CONNECTIVE) and len(t.suffix) >= 2
    if t.kind != "word" or t.label in ("negation", "exclude_verb"):
        return False
    k = t.norm
    return any(k.endswith(e) and k[:-len(e)] in CONNECTIVE_STEMS for e in CONNECTIVE)


def clause_tokens(text: TextView, conditions: list[Condition], layer) -> list[tuple[list[Tok], int]]:
    """The clauses of the question as this parser cuts them, each with the end of its body (the clause without its
    predicate): for 2.2 rules that work clause by clause (plan 05 E). Tokens of a fresh tokenization."""
    toks, _names = tokenize(text, conditions, layer)
    out = []
    for clause in _clauses(toks):
        body = _strip_predicate(list(clause))
        out.append((clause, body[-1].send if body else clause[0].start))
    return out


def _clauses(toks: list[Tok]) -> list[list[Tok]]:
    out, cur, depth = [], [], 0
    for t in toks:
        if t.kind == "punct" and t.label in OPEN:
            depth += 1
        elif t.kind == "punct" and t.label in CLOSE:
            depth = max(0, depth - 1)
        prev = cur[-1] if cur else None
        cur.append(t)
        # a separator after a complete request ("A를 보여줘, B는 빼줘") ends the clause as a sentence end does
        after_request = t.kind == "punct" and t.label in SEPARATOR_PUNCT and prev is not None \
            and prev.request_form == "final"
        end = (t.kind == "punct" and t.label in SENTENCE_END and depth == 0) or \
              (t.kind == "word" and t.label == "clause_ends") or (depth == 0 and connective(t)) or \
              (depth == 0 and after_request)
        if end:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _strip_predicate(clause: list[Tok]) -> list[Tok]:
    """The clause without its predicate: known request words (and the conditions around them) from the end."""
    i = len(clause) - 1
    while i >= 0:
        t = clause[i]
        if t.kind == "punct" or (t.kind == "word" and t.label in ("request", "ignore", "clause_ends")) \
                or (t.kind == "word" and t.label == "unknown" and i == len(clause) - 1 and connective(t)):
            t.stripped = t.kind == "word"
            i -= 1
            continue
        if t.kind == "cond" and (t.cond_kind == "display" or _split_by(t)):
            i -= 1  # periods, frequencies and "각각" stay: they are conditions of the list (A-5)
            continue
        break
    return clause[:i + 1]


def _split_by(t: Tok) -> bool:
    return t.kind == "cond" and t.cond_kind == "function" and t.cond["attrs"].get("function") == "split_by"


NEGATABLE_KINDS = ("basis", "calc", "period", "frequency")


def _closes_phrase(t: Tok) -> bool:
    """The token ends a case phrase with an object or topic particle ("X를", "X는")."""
    if t.kind == "punct":
        return False
    return t.ends_in(OBJECT | TOPIC)


def _negate(body: list[Tok], negated: list[Tok]) -> list[Tok]:
    out = list(body)
    for t in list(out):
        if not (t.kind == "word" and t.label == "negation") or t not in out:
            continue
        k = out.index(t)
        target = None
        for m in range(k - 1, -1, -1):
            x = out[m]
            if m < k - 1 and _closes_phrase(x):
                break  # plan 05 E: a negation covers its own phrase only, never the phrase before ("X를 A가 아니라")
            if x.kind in ("name", "qual") or (x.kind == "cond" and x.cond_kind in NEGATABLE_KINDS):
                target = m
                break
            if x.kind == "punct" or (x.kind == "word" and x.label == "unknown"):
                break
        if target is None:
            continue
        out[target].negated = True
        negated.append(out[target])
        del out[target:k + 1]
    return out


def _appositive(body: list[Tok], read_kind) -> bool:
    """"즉 / 그러니까", or a bracket after a name that holds an indicator name or an unknown word."""
    if any(t.kind == "word" and t.label == "appositive" for t in body):
        return True
    depth, inside, opened_after_name = 0, [], False
    for n, t in enumerate(body):
        if t.kind == "punct" and t.label in OPEN:
            depth += 1
            opened_after_name = n > 0 and body[n - 1].kind == "name" and body[n - 1].send == t.start
            inside = []
            continue
        if t.kind == "punct" and t.label in CLOSE:
            depth = max(0, depth - 1)
            if opened_after_name and any((x.kind == "word" and x.label == "unknown")
                                         or (x.kind == "name" and read_kind(x) == "indicator") for x in inside):
                return True
            continue
        if depth:
            inside.append(t)
    return False


TOPIC_LEAD_KINDS = ("period", "frequency", "basis", "unit")  # conditions that may lead a topic head


def _topics(body: list[Tok]) -> tuple[list[list[Tok]], bool]:
    """Topic segments of a clause body, and whether a cut is unsure. A segment starts at the conditions written
    right before its head ("A는 월별로, 분기별로 B는": 분기별로 leads B) when a separator ends the segment before them;
    conditions between two heads with no separator (whose segment?) leave the cut unsure (the drafts are undecided)."""
    # the heads are the clause's arguments: names with a topic particle, and with an object particle too when a topic
    # head stands beside them and conditions or markers of its own follow it, or it ends the clause body ("A를 X별로
    # 동시에, B는 따로", "A는 X별로, B를 따로": review of plan 08, variant RW2); an object of a verb inside the clause
    # ("A를 뺀 B는", "A를 보여줘 B는") is no argument head
    def head(n: int, t: Tok) -> bool:
        if t.kind not in ("name", "qual"):
            return False
        if t.suffix in TOPIC:
            return True
        nxt = body[n + 1] if n + 1 < len(body) else None
        if t.suffix not in OBJECT:
            return False
        return nxt is None or (nxt.kind == "cond" and nxt.cond_kind in TOPIC_LEAD_KINDS) \
            or _marker_of(nxt, DISTRIBUTIVE | SIMULTANEOUS)  # the clause's last argument, or one with its own words

    heads = [n for n, t in enumerate(body) if head(n, t)]
    topical = any(body[n].suffix in TOPIC for n in heads)
    marks = heads if topical else []
    if len(marks) < 2:
        return [body], False
    out, pos, unsure = [], 0, False
    for c in marks[1:]:
        lead = c
        while True:  # back over the conditions and their tail words ("분기 기준으로") right before the head
            prev = body[lead - 1] if lead - 1 > pos else None
            if prev is not None and prev.kind == "cond" and prev.cond_kind in TOPIC_LEAD_KINDS:
                lead -= 1
            elif prev is not None and prev.kind == "word" and prev.stem in TOPIC_LEAD_TAILS:
                lead -= 1
            else:
                break
        while lead < c and body[lead].kind == "word":  # tail words alone lead nothing
            lead += 1
        if lead < c:
            if body[lead - 1].kind == "punct" and _is_separator(body[lead - 1], 0):
                c = lead
            else:
                unsure = True
        out.append(body[pos:c])
        pos = c
    out.append(body[pos:])
    return [s for s in out if s], unsure


def _relation(body: list[Tok]) -> str | None:
    for t in body:
        if t.kind == "cond" and t.cond_kind == "relation":
            return t.cond["attrs"]["relation"]
        if t.kind == "word" and t.label == "combine":
            return COMBINE.get(t.norm) or COMBINE.get(t.stem)
    return None


def _modifier(body: list[Tok], read_kind) -> tuple[int, str] | None:
    """Index of the token that ends the modifier X of "X의 L" / "X 중 L", and its kind; None without one."""
    depth = 0
    for n, t in enumerate(body[:-1]):
        if t.kind == "punct" and t.label in OPEN:
            depth += 1
        elif t.kind == "punct" and t.label in CLOSE:
            depth = max(0, depth - 1)
        if depth:
            continue
        among = t.kind == "cond" and t.cond_kind == "function" and t.cond["attrs"].get("function") == "among"
        genitive = t.suffix in GENITIVE and t.kind != "punct"
        if not (among or genitive):
            continue
        rest = body[n + 1:]
        first = []
        for x in rest:  # the right side up to the next list joint
            first.append(x)
            if _is_joiner(x) or _is_separator(x, 0):
                break
        if any(x.kind == "cond" and x.cond_kind == "relation" for x in first[:1]):
            continue  # "A와 B의 차이"
        if not any(x.kind == "name" and read_kind(x) in ("indicator", "target") for x in rest):
            continue  # nothing named on the right: no modifier
        if all(x.kind == "hint" or (x.kind == "cond" and x.cond_kind in ("calc", "period", "frequency", "relation"))
               or x.kind == "punct" for x in first):
            continue  # "A의 전년 대비 증가율과 B": the genitive carries a calculation
        left = body[:n] if among else body[:n + 1]
        if not any(x.kind in ("name", "qual") for x in left):
            continue
        return (n, "among" if among else "genitive")
    return None


def _designates(seg: list[Tok]) -> bool:
    """A designation word in a clause: "X는·X은·X만·X에만·X에는" on a name or a word (2-4 rule 1)."""
    for t in seg:
        if t.kind in ("name", "cond", "qual") and t.suffix in DESIGNATE:
            return True
        if t.kind == "word" and t.ending in DESIGNATE:
            return True
    return False


def _split_related(items: list[Item]) -> list[Item]:
    """"A를 B로 나눈 비율": in a clause with a relation word the object particle closes A (R-1; the topic particle
    alike, "A는 B로 나눈 비율": the argument of the clause whatever its case particle, plan 08), and no item of the
    clause reads a breakdown inside itself."""
    out = []
    for it in items:
        cur: list[Tok] = []
        for n, t in enumerate(it.toks):
            cur.append(t)
            if t.kind in ("name", "word") and t.suffix in OBJECT | TOPIC \
                    and any(x.kind == "name" for x in it.toks[n + 1:]):
                out.append(Item(cur, it.phrase, related=True))
                cur = []
        if cur:
            out.append(Item(cur, it.phrase, after_dot=it.after_dot, axis_list=it.axis_list, related=True))
    return out


def _combined(seg: list[Tok]) -> bool:
    """"A와 B를 상계한 순액": a combining verb after a list, followed by a noun naming the result, makes the list the
    material of one value (one requirement). With a filler noun ("합친 값") the list stays two requirements and a
    relation. "A와 B를 뺀 X" combines only when B repeats the head of A (parallel phrases); otherwise it is an
    exclusion inside the last item ("A와 C를 뺀 물가")."""
    joints = [n for n, t in enumerate(seg) if _is_joiner(t)]
    if not joints:
        return False
    for n, t in enumerate(seg):
        if n <= joints[0]:
            continue
        combine = (t.kind == "word" and t.label == "combine") or \
                  (t.kind == "cond" and t.cond_kind == "relation" and t.cond["attrs"].get("relation") == "net")
        minus = t.kind == "word" and t.label == "exclude_verb"
        if not (combine or minus):
            continue
        rest = seg[n + 1:]
        if not any(x.kind in ("name", "word") and x.label not in ("ignore", "request", "particle") and _content(x)
                   for x in rest):
            return False
        if combine:
            return True
        before = [x for x in seg[:n] if _content(x)]
        if len(before) >= 2:
            last = before[-1]
            head = next((x for x in reversed(before[:-1]) if _is_joiner(x)), None)
            if head is not None and head.stem == last.stem:
                return True
        return False
    return False


def _modifies_head(seg: list[Tok], read_kind) -> bool:
    """Plan 09 P2b: "A와 B C를 <adnominal verb> N" ("가상갑과 가상을 변동을 제거한 가상지수"): a list of words whose
    last item ends in an object particle, then an unknown verb with a modifier ending and an indicator name N right
    after it. The list names what the verb acts on, a modifier of N: the phrase is one item (no requirement per listed
    word; the words stay leftover words, check 5). Not when the words before the verb hold a catalog name, a family
    qualifier, a calc hint, a condition other than a period or frequency, a list member of 2.2, or a combining,
    exclusion, share or negation word (the 2.2 rule ``condition_rules.modifies_head`` reads the same pattern)."""
    for n, t in enumerate(seg):
        if not (t.kind == "word" and t.label == "unknown" and t.stem and t.ending in MODIFIER_ENDINGS) \
                or n < 2 or n + 1 >= len(seg) or t.norm in DIVIDE_ADNOMINAL | DIVIDE:
            continue
        head, obj, before = seg[n + 1], seg[n - 1], seg[:n]
        if head.kind != "name" or read_kind(head) != "indicator" or not obj.ends_in(OBJECT):
            continue
        if not any(_is_joiner(x) or _is_separator(x, 0) for x in before[:-1]):
            continue  # no list before the verb
        if any(x.kind in ("name", "qual", "hint") or x.members
               or (x.kind == "cond" and x.cond_kind not in ("period", "frequency"))
               or (x.kind == "word" and x.label in ("combine", "exclude_verb", "share", "negation")) for x in before):
            return False
        return True
    return False


def _phrase_end_tok(t: Tok) -> bool:
    if t.suffix in PHRASE_END or (t.kind == "hint" and t.suffix):
        return True
    return t.kind == "word" and t.ending in PHRASE_END


def _verb_object(clause: list[Tok], verb: int) -> Tok | None:
    """The object of the dividing verb at ``verb``: the token before it (request, filler and the adverbs of the data
    list skipped) when it carries the object particle."""
    k = verb - 1
    while k >= 0 and clause[k].kind == "word" and (clause[k].label in ("ignore", "request")
                                                   or clause[k].norm in DIVIDE_ADVERBS):
        k -= 1
    if k < 0:
        return None
    obj = clause[k]
    has_object = obj.suffix in OBJECT or (obj.kind == "word" and obj.ending in OBJECT)
    return obj if has_object else None


def _source_end(t: Tok) -> bool:
    """"N에서": the token closes the phrase a divided list is taken from."""
    if t.kind == "punct":
        return False
    return t.suffix in SOURCE_ENDINGS or (t.kind == "word" and t.ending in SOURCE_ENDINGS)


def _member_word(toks: list[Tok]) -> str:
    """The words of one listed axis without particles, request and filler words ("기업규모를" -> 기업규모)."""
    parts = []
    for t in toks:
        if t.kind == "name" or t.kind == "qual":
            parts.append(t.key)
        elif t.kind == "word" and t.label not in ("request", "ignore", "particle", "list_words"):
            parts.append(t.stem)
        elif t.kind == "cond" and t.cond_kind == "basis":
            parts.append(t.norm)
    return "".join(parts)


def _axes_of_phrase(head: list[Tok], members: list[list[Tok]], layer) -> bool:
    """The listed words name axes (or part of axis names) of one table of the phrase N they divide: a table of a name
    in N, or a table whose name holds every name of N ("가상자산 가상자본 지표에서 가상업과 가상규모를 나누어")."""
    if layer is None:
        return False
    from .reading import Relations, term_of  # catalog reading of names (reading imports this module)

    words = [_member_word(m) for m in members]
    if not all(words):
        return False
    rel = Relations(layer)
    found = rel.axis_tables(words)
    if not found:
        return False
    names = [t for t in head if t.kind == "name" and not t.negated]
    if not names:
        return False
    tables = {x for t in names for x in term_of(t, layer).tables}
    keys = [t.key for t in names]
    tables |= {tid for tid, name in rel.words.names.items() if all(k in index_key(name) for k in keys)}
    return bool(found & tables)


def _divided(items: list[Item], clause: list[Tok], layer=None) -> list[Item]:
    """"A를 … B와 C를 (함께) 나눠": the list a dividing verb takes as its object is a list of breakdown axes of the
    phrase before it. The first member is cut off its item after the last word closing a phrase there; after "N에서"
    ("N에서 A와 B를 나누어") only when A and B name axes of a table of N."""
    verb = next((n for n, t in enumerate(clause) if t.kind == "word" and t.norm in DIVIDE), None)
    if verb is None or len(items) < 2:
        return items
    obj = _verb_object(clause, verb)
    m = next((n for n, it in enumerate(items) if any(t is obj for t in it.toks)), None) if obj else None
    if m is None or m == 0:
        return items
    prev = items[m - 1].toks
    cut = next((n for n in range(len(prev) - 2, -1, -1) if _phrase_end_tok(prev[n])), None)
    if cut is None:
        src = next((n for n in range(len(prev) - 2, -1, -1) if _source_end(prev[n])), None)
        if src is not None and _axes_of_phrase(prev[:src + 1], [prev[src + 1:]] + [
                [t for t in items[m].toks if t.start <= obj.start]], layer):
            cut = src
    if cut is None or not any(_content(t) for t in prev[:cut + 1]):
        return items
    head, member = Item(prev[:cut + 1]), Item(prev[cut + 1:], axis_list=True)
    items[m].axis_list = True
    return items[:m - 1] + [head, member] + items[m:]


def _divided_before(items: list[Item], clause: list[Tok], layer) -> list[Item]:
    """"A와 B를 (동시에) 나눈 N": the list before the adnominal dividing verb is a list of breakdown axes of the
    phrase N after it, when A and B name axes (or part of axis names) of a table of N. The verb and the adverbs before
    it belong to no item. Plan 09 (10/19): the connective form "A와 B를 (동시에) 나눠 N" reads the same when no phrase
    before the list was divided ("N에서 A와 B를 나누어", ``_divided``)."""
    verb = next((n for n, t in enumerate(clause) if t.kind == "word" and t.norm in DIVIDE_ADNOMINAL), None)
    if verb is None and not any(it.axis_list for it in items):
        verb = next((n for n, t in enumerate(clause) if t.kind == "word" and t.norm in DIVIDE), None)
    if verb is None or len(items) < 2:
        return items
    obj = _verb_object(clause, verb)
    vtok = clause[verb]
    m = next((n for n, it in enumerate(items) if any(t is obj for t in it.toks)), None) if obj else None
    if m is None or m == 0 or not any(t is vtok for t in items[m].toks):
        return items
    toks = items[m].toks
    o = next(n for n, t in enumerate(toks) if t is obj)
    v = next(n for n, t in enumerate(toks) if t is vtok)
    head = toks[v + 1:]
    if not any(_content(t) for t in head):
        return items
    members = [it.toks for it in items[:m]] + [toks[:o + 1]]
    if not _axes_of_phrase(head, members, layer):
        return items
    out = [Item(it.toks, it.phrase, axis_list=True) for it in items[:m]]
    out += [Item(toks[:o + 1], axis_list=True), Item(head)]
    return out + items[m + 1:]


def period_cuts(seg: list[Tok]) -> tuple[list[int], bool]:
    """Where the list is cut by its periods (the old period split): between two periods with other words between
    them that end in a joiner ("2014년 A 기준과 2015년 B 기준") the second period starts a new part. Periods with only
    a joiner between ("2019년과 2021년") are one part; words between them without a joiner make the reading
    unsure. Returns (token indices that start a part, unsure)."""
    periods = [n for n, t in enumerate(seg) if t.kind == "cond" and t.cond_kind == "period"]
    first_content = next((n for n, t in enumerate(seg) if _content(t)), None)
    if not periods or (first_content is not None and first_content < periods[0]):
        return [], False  # the list does not start with a period: its items are cut as a list
    cuts, unsure = [], False
    for a, b in zip(periods, periods[1:]):
        gap = seg[a + 1:b]
        words = [t for t in gap if t.kind != "punct" and not (t.kind == "word" and t.label == "list_words")]
        if not words:
            continue
        joined = _is_joiner(seg[b - 1]) or (seg[b - 1].kind == "punct" and seg[b - 1].label in SEPARATOR_PUNCT) \
            or (seg[b - 1].kind == "word" and seg[b - 1].label == "list_words")
        if joined:
            cuts.append(b)
        else:
            unsure = True
    return cuts, unsure


def _byeol(t: Tok) -> bool:
    return t.kind == "cond" and t.cond_kind == "basis" and t.cond["attrs"].get("axis") == "classification" \
        and t.cond["value"].endswith(BYEOL)


def _complement(body: list[Tok], after: list[Tok], read_kind) -> int | None:
    """Index of N in "N을 L로 (나눠)" ("N은 L로"): the first indicator name with the object or topic particle, followed
    by content, where the complement ends in "로/으로" or a "로 나눠" function word follows the body."""
    depth = 0
    for n, t in enumerate(body[:-1]):
        if t.kind == "punct" and t.label in OPEN:
            depth += 1
        elif t.kind == "punct" and t.label in CLOSE:
            depth = max(0, depth - 1)
        if depth or t.kind != "name" or t.suffix not in OBJECT | TOPIC or read_kind(t) != "indicator":
            continue  # finding 5: the argument of the clause, object or topic particle alike ("N을/N은 L로")
        rest = [x for x in body[n + 1:] if x.kind != "punct"]
        if not any(_content(x) for x in rest):
            return None
        last = next((x for x in reversed(rest) if _content(x)), None)
        split_by = any(_split_by(x) for x in after[:3]) or any(_split_by(x) for x in rest)
        if not ((last is not None and last.suffix in COMPLEMENT) or split_by):
            return None
        # a complement is a list, names or X-byeol axes; one condition phrase ("신규취급액 기준으로") is not one, nor
        # conditions with a general noun after a basis value ("가상조정한 실질 분기 금액으로": that noun is part of
        # the value, plan 04 2-2 row 6; plan 09, user decision 10/19)
        listed = len(split_list([x for x in body[n + 1:]])) >= 2
        named = [x for k, x in enumerate(rest) if x.kind == "name" and not (
            x.key in BASIS_TAIL_NOUNS and k > 0 and rest[k - 1].kind in ("cond", "qual"))]
        if listed or named or any(_byeol(x) for x in rest):
            return n
        return None
    return None


def read_question(text: TextView, conditions: list[Condition], layer: CatalogLayer | None) -> Reading:
    key = (text["raw"], tuple((c["id"], c["span"][0], c["span"][1]) for c in conditions), id(layer))
    hit = _CACHE.get(key)
    if hit is not None and hit.layer is layer:
        return hit
    reading = _read(text, conditions, layer)
    reading.layer = layer
    if len(_CACHE) > 512:
        _CACHE.clear()
    _CACHE[key] = reading
    return reading


_CACHE: dict = {}


def _read(text: TextView, conditions: list[Condition], layer: CatalogLayer | None) -> Reading:
    from .reading import name_kind  # catalog reading of one name (indicator / target / basis / unknown)

    toks, names = tokenize(text, conditions, layer)

    def read_kind(t: Tok) -> str:
        return name_kind(t, layer)

    groups: list[Group] = []
    negated: list[Tok] = []
    first_clause: int | None = None
    seen: set[str] = set()
    for ci, clause in enumerate(_clauses(toks)):
        body = _strip_predicate(clause)
        after = clause[len(body):]
        if not body:
            continue
        relation = _relation(body)
        appos = _appositive(body, read_kind)
        body = _negate(body, negated)
        segments, topic_unsure = _topics(body)
        for sn, seg in enumerate(segments):
            # plan 05 B·D: markers ("각각", "동시에") count in their own topic segment only; the first segment takes
            # the words before it, the last one the clause predicate after it
            lo = clause[0].start if sn == 0 else seg[0].start
            hi = segments[sn + 1][0].start if sn + 1 < len(segments) else clause[-1].send + 1
            seg_words = [t for t in clause if lo <= t.start < hi]
            for prev, t in zip(seg, seg[1:]):  # a list mark right after a relation word ends that relation phrase
                if t.kind == "punct" and t.label in DOTS and prev.kind == "cond" and prev.cond_kind == "relation":
                    t.cut = True  # review p08 r1-3: "A 대비 B 비율·C 대비 D 비율" is two phrases, no "A·B X" list
            body_items = [t for t in seg if not (t.kind == "cond" and t.cond_kind == "relation"
                                                 and not _is_separator(t, 0))
                          and not (t.kind == "word" and t.label in ("combine", "share"))]
            combined = _combined(seg)
            # plan 09 P2b: a list modifying the head name after an adnominal verb is one item
            modhead = not combined and relation is None and _modifies_head(body_items, read_kind)
            cuts, unsure = period_cuts(body_items)
            by_period = bool(cuts) and not combined and not modhead
            mod = None if combined or by_period or modhead else _modifier(body_items, read_kind)
            modifier, kind, complement = [], None, False
            comp = _complement(body_items, after, read_kind) if mod is None and not (combined or by_period or modhead) \
                else None
            if combined or modhead:
                items = [Item([t for t in body_items])]
            elif by_period:  # "2014년 A 기준과 2015년 B 기준 ...": one part per period, no list cut inside
                bounds = [0, *cuts, len(body_items)]
                items = [Item(body_items[a:b]) for a, b in zip(bounds, bounds[1:]) if body_items[a:b]]
            elif mod is not None:
                n, kind = mod
                left = body_items[:n] if kind == "among" else body_items[:n + 1]
                right = body_items[n + 1:]
                modifier = [Item(it, "modifier") for it in split_list(left)]
                items = [Item(it) for it in split_list(right)]
            elif comp is not None:
                complement = True
                rest = body_items[comp + 1:]
                k = len(rest)
                while k > 0 and (_byeol(rest[k - 1]) or rest[k - 1].kind == "punct" or _split_by(rest[k - 1])
                                 or _time(rest[k - 1]) or _each(rest[k - 1])):
                    k -= 1  # "A·B·C 용도별로 나눠 월별": the X-byeol after the complement list belongs to N
                head_items = split_list(body_items[:comp + 1])
                items = [Item(it) for it in head_items]
                items += [Item(it, "complement") for it in split_list(rest[:k])]
                if any(_byeol(x) for x in rest[k:]):
                    items.append(Item(rest[k:], "complement"))
            else:
                items = _divided([Item(it) for it in split_list(body_items)], clause, layer)
                items = _divided_before(items, clause, layer)
            if relation is not None and not combined:
                items = _split_related(items)
                for it in modifier:
                    it.related = True
            if len(segments) > 1:
                for it in items:
                    it.phrase = "topic"
            for it in items:  # a list member after a middle dot ("가공·운송·여행·건설수지")
                at = next((n for n, t in enumerate(body_items) if t is it.toks[0]), None)
                it.after_dot = at is not None and at > 0 and body_items[at - 1].kind == "punct" \
                    and body_items[at - 1].label in DOTS
                end = next((n for n, t in enumerate(body_items) if t is it.toks[-1]), None)
                it.before_dot = end is not None and end + 1 < len(body_items) \
                    and body_items[end + 1].kind == "punct" and body_items[end + 1].label in DOTS
            if not items and not modifier:
                continue
            names_here = {t.key for it in modifier + items for t in it.toks if t.kind == "name" and not t.negated}
            later = first_clause is not None and ci != first_clause
            # a later clause makes no item when it only designates items of an earlier clause ("가상B금리는 …
            # 기준으로": a designation word is needed, R-2), or when it names nothing at all
            designation = later and ((not names_here) or (names_here <= seen and _designates(seg)))
            if not designation:
                first_clause = ci if first_clause is None else first_clause
                seen |= names_here
            groups.append(Group(items=items, modifier=modifier, modifier_kind=kind, relation=relation,
                                appositive=appos, complement=complement, designation=designation,
                                combined=combined, by_period=by_period, unsure_periods=unsure or topic_unsure,
                                clause=ci,
                                distributive=any(_marker_of(t, DISTRIBUTIVE) for t in seg_words),
                                simultaneous=any(_marker_of(t, SIMULTANEOUS) for t in seg_words),
                                clause_end=clause[-1].send,
                                start=seg[0].start, end=seg[-1].send))
    fragments = _fragments(text, groups, negated)
    return Reading(toks=toks, names=names, groups=groups, fragments=fragments, negated=negated, raw=text["raw"],
                   words=Words(text["raw"], names=[(t.start, t.end) for t in toks if t.kind == "name"],
                               cuts=glued_request_starts(toks)))


def _runs(item: Item) -> list[list[Tok]]:
    runs, cur = [], []
    for t in item.toks:
        removed = (t.kind == "cond" and t.cond_kind in REMOVED_KINDS) or t.stripped \
            or (t.kind == "punct" and t.label in SEPARATOR_PUNCT | SENTENCE_END and not cur)
        if removed:
            if cur:
                runs.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        while run and run[-1].kind == "punct" and run[-1].label not in CLOSE:
            run = run[:-1]
        if run and any(t.kind != "punct" for t in run):
            out.append(run)
    return out


def _protected(run: list[Tok]) -> str | None:
    for t in run:
        if t.protected:
            return t.key
    return None


def _fragments(text: TextView, groups: list[Group], negated: list[Tok]) -> list[Fragment]:
    raw = text["raw"]
    out: list[Fragment] = []
    for g in groups:
        for item in g.modifier + g.items:
            for run in _runs(item):
                a, b = run[0].start, run[-1].send
                fid = f"g{len(out) + 1}"
                frag: Fragment = {"id": fid, "quote": raw[a:b], "span": [a, b], "role": "core",
                                  "protected_by": _protected(run), "phrase": item.phrase}
                if g.appositive:
                    frag["phrase"] = "appositive"
                out.append(frag)
                item.fragment_ids.append(fid)
    return out


def split_fragments(text: TextView, conditions: list[Condition],
                    layer: CatalogLayer | None = None) -> list[Fragment]:
    """Fragments of the question (2.3): the runs of each list item and modifier, in question order."""
    return [dict(f) for f in read_question(text, conditions, layer).fragments]
