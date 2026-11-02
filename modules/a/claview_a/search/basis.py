"""Basis occurrences shared by evidence, facts and table signatures.

Vocabulary belongs to the existing basis dictionary. Offsets refer to the supplied text.
Within an axis, a longer occurrence protects its span from a shorter, embedded value.
Independent occurrences, including an opposite value elsewhere, remain visible.

``polarity`` (plan 53, user 2026-10-30 02:07 UTC) keeps what a stated text requires apart from what it excludes:
a value directly followed by a negation ("계절조정은 하지 않은", "신규취급액이 아니라") is excluded, never a
requirement, unless that exclusion is negated or conceded again in its clause ("계절조정을 제외하고 싶지 않아",
"계절조정 없이는 안 돼", "계절조정 없이도 돼", "원계열 말고는"): then it is unresolved; a value with a negation later
in its clause but not right after it is unresolved (its scope is not read). The negation words are the lexicon's
(``negation``, and ``negation.basis_scope.other_negations`` read only as a negation in the clause); no opposite value
is ever inferred.

Whose negation it is (plan 55, user 2026-10-31 12:36 UTC; words in ``negation.basis_scope``): "N뿐/N만 아니라",
"N 계열뿐 아니라" and "N 말고도" are not a negation of N; "N을 상품별로 표가 아니라" and "N 계열을 표 대신" negate
표, not N (N's phrase, the value word or up to two words after it, is closed by its particle, only "-로" words
between, and the negated word is a word of its own: not a verb in -지 right after N ("N을 적용하지 말고"), a clause
nominalized on a verb ("N 계열을 고른게 아니라"), a modifier before 대신 ("N을 보는 대신") or a phrase closed by a
particle that is also a modifier ending ("N 보는 게 아니라"), and not right before another value of the axis ("N 계열을
이번에는 말고 M으로")); "N 표가 아니라" and "N표가 아니라" negate the noun phrase N is in, which excludes N only when
the word right after the negation is another value of the axis ("N 표가 아니라 M 표로"; "N 표가 아니라 그래프로" and
"N 표 말고 M 표도" are not read). A negated exclusion (D1, user 2026-10-31 12:47 UTC): the prohibitive "N은 제외하지
말고" / "N은 빼지(도) 말고" / "N은 제외하지 마세요" (an exclusion stem, -지, an auxiliary particle or none, 말- or its
form 마-) requires N, and nothing about the other value; the permissive "N은 제외하지 않아도 돼" leaves N free
(neither required nor excluded); any other ("N을 제외하지 않고", "N을 제외하고 싶지 않아", "N을 제외할 필요는 없어",
"N은 제외하면 안 돼", "N은 제외해도 돼") is not read here: unresolved. The lexicon's exclusion predicates ("N은 빼줘",
"N 없이") and existence negation ("N은 필요 없어") right after N or its particle exclude it ("N밖에 필요 없어" is not
read). A value joined only by a coordinator, a joining particle ("원화나 달러화", "원화도 달러화도") or a list mark
("원화, 달러화 말고") to a next value that is excluded, unresolved or free is not read either. Any other negation in
the clause stays unresolved.
"""

from __future__ import annotations
import json
import re
from functools import lru_cache
from .. import paths
from ..conditions.words import BASIS_AXES
from ..text.lexicon import (AUX, BASIS_NEGATION_PARTICLES, BASIS_NEGATION_WORDS, BASIS_SCOPE, DESIGNATION_NEGATION,
                            ENDING_WORDS, EXCLUDE_NEGATED_BY, EXCLUDE_NEGATED_ENDING, EXCLUDE_VERBS,
                            EXISTENCE_NEGATION, JOINER_PARTICLES, LIST_WORDS, MODIFIER_ENDINGS, NEGATION_WORDS,
                            SEPARATOR_PUNCT, alt, alt_spaced)

_NEGATION = alt_spaced(BASIS_NEGATION_WORDS + NEGATION_WORDS)
_EXCLUDE = alt_spaced(EXCLUDE_VERBS)
_OTHER_NEGATION = alt_spaced(BASIS_SCOPE["other_negations"])
_PARTICLE = rf"(?:{alt(BASIS_NEGATION_PARTICLES)})?"
# right after the value: the rest of its word (no space), a particle, then a negation word
_NEGATED_AFTER = re.compile(rf"[가-힣]{{0,4}}?\s*{_PARTICLE}\s*(?P<neg>{_NEGATION})")
# the lexicon's exclusion predicates ("빼줘", "없이") and existence negation ("필요 없어") right after the value, its
# particle or an auxiliary particle ("계절조정도 필요 없어"); never after the rest of a word ("계절조정밖에 필요 없어",
# "원계열외에는 빼줘", "원계열빠짐없이")
_EXCLUDED_BY = re.compile(rf"\s*(?:{alt(BASIS_NEGATION_PARTICLES + AUX)})?\s*"
                          rf"(?:(?P<verb>{_EXCLUDE})|{EXISTENCE_NEGATION})")
# any negation in the clause; an exclusion predicate only as a word of its own ("빠짐없이" is none)
_NEGATION_ANY = re.compile(rf"{alt_spaced(BASIS_NEGATION_WORDS + NEGATION_WORDS + DESIGNATION_NEGATION)}"
                           rf"|{EXISTENCE_NEGATION}|(?<![가-힣])(?:{_EXCLUDE})|{_OTHER_NEGATION}")
# a negation of an exclusion written before it: any but a lone exclusion predicate of another word ("설명 없이")
_AGAIN = re.compile(rf"{alt_spaced(BASIS_NEGATION_WORDS + NEGATION_WORDS + DESIGNATION_NEGATION)}"
                    rf"|{EXISTENCE_NEGATION}|{_OTHER_NEGATION}")
# an exclusion conceded on its own word: "제외해도 돼", "빼줘도 돼", "필요 없어도 돼"
_CONCESSIVE = re.compile(rf"[가-힣]{{0,3}}?(?:{alt_spaced(BASIS_SCOPE['concessive'])})")
# a whole negation word or exclusion predicate, after which 는/도 is an auxiliary particle ("말고는", "없이는",
# "제외는"; in "않는", "필요 없는" it is a modifier ending)
_WHOLE = re.compile(rf"(?:{alt_spaced(NEGATION_WORDS)}|{_EXCLUDE})")
_CLAUSE_END = re.compile(r"[.,;!?\n]")
# "계절조정, 그건 빼고": a word after a clause mark that takes up the value; its clause goes on
_ANAPHOR = re.compile(rf"\s*(?:{alt_spaced(BASIS_SCOPE['anaphors'])}){_PARTICLE}(?:{alt(AUX)})?(?![가-힣])")
_NOUN_NEGATION = alt(BASIS_SCOPE["noun_negations"])
_IS_NOUN_NEGATION = re.compile(rf"(?:{_NOUN_NEGATION})")
_CLOSING = alt(sorted(BASIS_SCOPE["closing_particles"], key=len, reverse=True))
_BETWEEN = alt(BASIS_SCOPE["between_endings"])
_NOT_ONLY_FORM = rf"(?:{alt(BASIS_SCOPE['not_only'])})+{_PARTICLE}\s*(?:{alt(BASIS_SCOPE['not_only_negations'])})"
# "원계열뿐 아니라", "원계열만이 아니라", "원계열뿐만 아니라": not only the value; "원계열 말고도": besides it
_NOT_ONLY = re.compile(rf"[가-힣]{{0,4}}?(?:{_NOT_ONLY_FORM}|\s*(?:{alt_spaced(BASIS_SCOPE['additive'])}))")
# "계절조정 계열뿐 아니라": the noun phrase of the value, not only
_NOT_ONLY_TAIL = re.compile(rf"{_NOT_ONLY_FORM}$")
# "계절조정은 제외하지 말고", "평잔은 제외하지는 말고", "계절조정은 제외하지 마세요", "계절조정을 빼지 않아도 돼": the
# exclusion of the value is itself negated (``by``: the lexicon's negating verbs and the prohibitive's forms); an
# auxiliary particle (``aux``) may stand between -지 and the negating verb
_BY = alt(EXCLUDE_NEGATED_BY + tuple(w for w in BASIS_SCOPE["prohibitive"] if w not in EXCLUDE_NEGATED_BY))
_DOUBLE = re.compile(rf"[가-힣]{{0,4}}?\s*{_PARTICLE}\s*(?:{alt(BASIS_SCOPE['double_stems'])})\s*"
                     rf"{re.escape(EXCLUDE_NEGATED_ENDING)}\s*(?P<aux>{alt(AUX)})?\s*(?P<by>{_BY})")
_PROHIBITIVE = frozenset(BASIS_SCOPE["prohibitive"])
_PERMISSIVE = re.compile(rf"(?:{alt_spaced(BASIS_SCOPE['permissive'])})")
_WORD_REST = re.compile(r"[가-힣]*")
_WORD = r"[가-힣A-Za-z0-9]+?"
# "원계열을 상품별로 표가 아니라", "계절조정 계열을 표 대신", "신규취급액 기준 대출금리를 표 대신": the value's phrase
# (the value word, or up to two words after it) is closed by its particle, words between end in "-로", and the
# negation follows another word W (W is what is negated, when it is a word of its own: see ``_own_phrase``)
_OTHER_WORD = re.compile(rf"(?P<rest>[가-힣]{{0,4}}?)(?P<phrase>(?:\s+{_WORD}){{0,2}}?)(?P<close>{_CLOSING})\s+"
                         rf"(?P<between>(?:\S+(?:{_BETWEEN})\s+)*)(?P<word>(?P<w>{_WORD}){_PARTICLE})\s*"
                         rf"(?P<neg>{_NOUN_NEGATION})")
# "계절조정 표가 아니라", "계절조정한 표가 아니라": the value modifies the negated noun (no particle of its own)
_IN_NEGATED_NOUN = re.compile(rf"(?P<rest>[가-힣]{{0,4}}?)(?P<between>\s+[가-힣]{{1,6}}?)??\s+[가-힣]{{1,6}}?"
                              rf"{_PARTICLE}\s*(?:{_NOUN_NEGATION})")
# "원계열표가 아니라": the rest of the value word before a noun negation
_NOUN_REST = re.compile(rf"(?P<rest>[가-힣]{{1,4}}?)\s*{_PARTICLE}\s*(?:{_NOUN_NEGATION})")
_ENDS_CLOSED = re.compile(rf"(?:{_CLOSING}|{_BETWEEN})$")
_VERB_IN_JI = re.compile(rf"{re.escape(EXCLUDE_NEGATED_ENDING)}(?:{alt(AUX)})?$")
# "고른게", "달라는게", "원한걸": a clause nominalized by a bound noun written onto its verb
_NOMINAL = re.compile(rf".(?:{alt(BASIS_SCOPE['nominal_endings'])})$")
_MODIFIER_CLOSINGS = frozenset(BASIS_SCOPE["modifier_closings"])
_MODIFIER_NEGATIONS = frozenset(BASIS_SCOPE["modifier_negations"])
# "원계열 말고 계절조정도", "원계열 그래프 말고 계절조정 그래프도": the next word, or the one after it, ends in 도
_ALSO = re.compile(rf"\s*(?:[가-힣A-Za-z0-9]+\s+)?[가-힣A-Za-z0-9]*?(?:{alt(BASIS_SCOPE['also'])})(?![가-힣])")
_ENDINGS = frozenset(ENDING_WORDS)
_VERB_RESTS = tuple(sorted(MODIFIER_ENDINGS))
# only a coordinator between two values ("원화나 달러화", "원계열과 계절조정", "원계열 및 계절조정", "원화·달러화")
_JOINERS = sorted(LIST_WORDS + JOINER_PARTICLES + BASIS_SCOPE["value_joiners"], key=len, reverse=True)
_JOINED = re.compile(rf"\s*(?:{alt(_JOINERS)}|[{re.escape(''.join(sorted(SEPARATOR_PUNCT)))}])\s*")


@lru_cache(maxsize=1)
def _aliases() -> dict:
    return json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8")).get("basis_aliases", {})


def occurrences(text: str, axis: str, extra: str | None = None) -> list[dict]:
    positions = [i for i, char in enumerate(text or "") if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    vocabulary = dict(BASIS_AXES.get(axis, {}))
    for value, forms in _aliases().get(axis, {}).items():
        vocabulary[value] = (*vocabulary.get(value, ()), *forms)
    if extra and extra not in vocabulary:
        vocabulary[extra] = (extra,)
    found = []
    for value, forms in vocabulary.items():
        for form in forms:
            word = "".join(form.split())
            if not word:
                continue
            start = compact.find(word)
            while start >= 0:
                end = start + len(word)
                found.append({"value": value, "form": form,
                              "span": [positions[start], positions[end - 1] + 1],
                              "_compact": (start, end)})
                start = compact.find(word, start + 1)
    kept, seen = [], set()
    for row in sorted(found, key=lambda r: (r["_compact"][0], -r["_compact"][1], r["value"])):
        lo, hi = row["_compact"]
        if any(a <= lo and hi <= b and b - a > hi - lo
               for a, b in (other["_compact"] for other in found)):
            continue
        key = (row["value"], *row["span"])
        if key not in seen:
            kept.append({k: v for k, v in row.items() if k != "_compact"})
            seen.add(key)
    return kept


def values(text: str, axis: str, extra: str | None = None) -> set[str]:
    return {row["value"] for row in occurrences(text, axis, extra)}


def _closed(words: str) -> bool:
    """A word ends in a closing particle or a between ending ("수출액을", "지역별로"): it closes the value's phrase."""
    return bool(_ENDS_CLOSED.search(words.strip()))


def _final_n(word: str) -> bool:
    """The last syllable of ``word`` ends in ㄴ ("본", "보는", "고른"): Hangul syllables are composed in blocks of
    28 final consonants, ㄴ is the fourth."""
    last = word[-1:]
    return "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 == 4


def _own_phrase(m: re.Match) -> bool:
    """The words before W (``_OTHER_WORD``) are the value's own phrase and W is a word of its own: the value
    word closed by its particle, or the value word with up to two words after it, only the last one closed
    ("계절조정 계열을") by a particle that is no modifier ending ("원계열 보는 게", "원계열 담은 표"). W is no
    clause nominalized on its verb ("고른게"), no modifier before a negation that also follows a verb ("보는 대신",
    "본 대신"), and no verb in -지 after more than the value word ("수치를 표로 보여주지 말고") or right after it
    ("원계열을 보여주지 말고"; after a "-로" word, "원계열을 표로 보여주지 말고", the verb's manner is negated, as
    before)."""
    words = m["phrase"].split()
    if _NOMINAL.search(m["w"]) or (m["neg"] in _MODIFIER_NEGATIONS and _final_n(m["word"])):
        return False
    if not words:
        return not (_VERB_IN_JI.search(m["w"]) and not m["between"])
    return (m["close"] not in _MODIFIER_CLOSINGS and not _closed(m["rest"])
            and not any(_closed(w) for w in words[:-1]) and not _VERB_IN_JI.search(m["w"]))


def _noun_rest(rest: str) -> bool:
    """The rest of the value word is a noun ("원계열표"), not a particle ("만"), a verb form ("된거", "한것") or a verb
    in -지 ("계절조정하지 말고")."""
    return (bool(rest) and rest not in _ENDINGS and not rest.startswith(_VERB_RESTS)
            and not _VERB_IN_JI.search(rest))


def _reading(text: str, rows: list[dict], i: int) -> tuple[str | None, str | None]:
    """How the value of ``rows[i]`` is written: (required | excluded | unresolved | None, why); None leaves it free
    (the permissive "제외하지 않아도 돼")."""
    row, end = rows[i], rows[i]["span"][1]
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    stop = nxt["span"][0] if nxt else len(text)
    clause = _CLAUSE_END.search(text, end, stop)
    while clause and _ANAPHOR.match(text, clause.end()):  # "계절조정, 그건 빼고": the clause goes on
        clause = _CLAUSE_END.search(text, clause.end(), stop)
    limit = clause.start() if clause else stop

    def within(pattern):
        m = pattern.match(text, end)
        return m if m and m.end() <= limit else None

    def negated_after(at):
        return bool(_NEGATION_ANY.search(text, at, limit))

    def asked(at, why="negation_scope_unread"):
        """The value is not negated up to ``at``: required, unless another negation follows in its clause."""
        return ("unresolved", why) if negated_after(at) else ("required", None)

    def contrast(at):
        """The value is in the negated noun phrase: excluded when the word right after the negation (``at``) is
        another value of the axis ("계절조정 표가 아니라 원계열 표로"); otherwise ("원계열 표가 아니라 그래프로",
        "계절조정 표가 아니라 계절조정 그래프로", "원계열 그래프 말고 계절조정 그래프도") the scope is not
        read; "계절조정 계열뿐 아니라" is no negation of it."""
        if _NOT_ONLY_TAIL.search(text, end, at):
            return asked(at)
        if nxt and nxt["value"] != row["value"] and at <= stop and not text[at:stop].strip() \
                and not _ALSO.match(text, nxt["span"][1]):
            return "excluded", None
        return "unresolved", "negation_scope_unread"

    def negated_again(at, whole):
        """The exclusion ending at ``at`` is conceded on its word ("제외해도 돼", "없이도 돼"), followed by an
        auxiliary particle after a whole negation word (``whole``: "없이는", "말고는", "제외는") or negated later in
        its clause ("제외하고 싶지 않아", "제외하면 안 돼"), unless that negation is of another word after a phrase of
        its own ("하지 않은 값을 표 말고": 표)."""
        if (c := _CONCESSIVE.match(text, at)) and c.end() <= limit:
            return True
        if whole and text.startswith(AUX, at):
            return True
        at = _WORD_REST.match(text, at).end()
        if (w := _OTHER_WORD.match(text, at)) and w.end() <= limit and _own_phrase(w):
            at = w.end()
        return bool(_AGAIN.search(text, at, limit))

    if m := within(_NOT_ONLY):
        return asked(m.end())
    if m := within(_DOUBLE):
        if m["by"] in _PROHIBITIVE:  # "제외하지(는) 말고", "제외하지 마세요": N is required
            return asked(_WORD_REST.match(text, m.end()).end(), "double_negation_unread")
        permissive = None if m["aux"] else _PERMISSIVE.match(text, m.end())
        if permissive and permissive.end() <= limit:  # "제외하지 않아도 돼": no condition on N
            return ("unresolved", "double_negation_unread") if negated_after(permissive.end()) else (None, None)
        return "unresolved", "double_negation_unread"  # "제외하지 않고": not read
    if m := _NEGATED_AFTER.match(text, end):
        if (n := _NOUN_REST.match(text, end)) and _noun_rest(n["rest"]):  # "원계열표가 아니라"
            return contrast(n.end())
        if negated_again(m.end(), bool(_WHOLE.fullmatch(m["neg"]))):
            return "unresolved", "double_negation_unread"
        if _IS_NOUN_NEGATION.fullmatch(m["neg"]) and _ALSO.match(text, m.end()):  # "원계열 말고 계절조정도"
            return "unresolved", "negation_scope_unread"
        return "excluded", None
    if m := _EXCLUDED_BY.match(text, end):  # "원계열은 빼줘", "계절조정 없이", "계절조정은 필요 없어"
        if negated_again(m.end(), bool(m["verb"])):
            return "unresolved", "double_negation_unread"
        return "excluded", None
    if (m := within(_OTHER_WORD)) and _own_phrase(m):
        if nxt and not text[m.end():stop].strip():  # "계절조정 계열을 이번에는 말고 원계열로": N may be contrasted
            return "unresolved", "negation_scope_unread"
        return asked(m.end())
    if (m := within(_IN_NEGATED_NOUN)) and not _closed(m["rest"]) and not _closed(m["between"] or ""):
        return contrast(m.end())
    return asked(end)


def polarity(text: str, axis: str) -> dict:
    """{required, excluded, unresolved} of the values of ``axis`` written in ``text``: a value directly followed by
    a negation word is excluded, unless that exclusion is negated or conceded again in its clause ("제외하고 싶지
    않아", "없이는 안 돼", "없이도 돼": unresolved); one with a negation word later in the same clause (before the next
    value of the axis or a clause mark) is unresolved ({value, form, why}); the others are required. A value both
    required and excluded is unresolved too. Words only, never a meaning: nothing here picks the other value. Whose
    negation a word is (plan 55) is read before that, and the prohibitive "제외하지 말고" / "제외하지 마세요" requires
    the value: see the module text. A required value joined only by a coordinator, a joining particle ("원화나",
    "원화도") or a list mark ("원화, 달러화 말고") to a next value that is excluded, unresolved or free is unresolved:
    the negation may cover both."""
    text = text or ""
    rows = sorted(occurrences(text, axis), key=lambda r: r["span"][0])
    reads = [_reading(text, rows, i) for i in range(len(rows))]
    for i in reversed(range(len(rows) - 1)):
        if reads[i][0] == "required" and reads[i + 1][0] in ("excluded", "unresolved", None) \
                and _JOINED.fullmatch(text, rows[i]["span"][1], rows[i + 1]["span"][0]):
            reads[i] = ("unresolved", "negation_scope_unread")
    required, excluded, unresolved = set(), set(), []
    for row, (kind, why) in zip(rows, reads):
        if kind == "required":
            required.add(row["value"])
        elif kind == "excluded":
            excluded.add(row["value"])
        elif kind == "unresolved":
            unresolved.append({"value": row["value"], "form": row["form"], "why": why})
    for value in sorted(required & excluded):
        unresolved.append({"value": value, "form": None, "why": "required_and_excluded"})
    both = required & excluded
    return {"required": required - both, "excluded": excluded - both, "unresolved": unresolved}
