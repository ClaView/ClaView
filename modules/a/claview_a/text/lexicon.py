"""The grammar-word dictionary of question interpretation (plan 08 T2): ``data/lexicon.json`` read once, and every
set the rules derive from it, built here and nowhere else.

The file holds the word classes (request, filler, coordinator, punctuation, particle, marker, negation, exclusion,
relation, tail-noun, X-byeol, unit words) with their properties; this module turns them into the sets, maps and
regular-expression parts the 2.2 rules, the phrase reading (2.3) and the later stages use. Code outside this module
holds no Hangul word: it names a class here. Condition vocabulary (basis axes, calc patterns, period words) stays in
``data/condition_words.json``, model-step texts in ``data/model_step_words.json``.
"""

from __future__ import annotations
import json
import re
from .. import paths
from ..catalog.text import index_key


LEXICON_FILE = paths.LEXICON
LEX: dict = json.loads(LEXICON_FILE.read_text(encoding="utf-8"))


def _key_set(words) -> frozenset:
    return frozenset(index_key(w) for w in words)


def alt(words) -> str:
    """Regular-expression alternation of the words, in the given order (the order decides overlaps)."""
    return "|".join(re.escape(w) for w in words)


def alt_spaced(words) -> str:
    """Alternation of words that may hold a space ("하지 않"): any whitespace there."""
    return "|".join(r"\s*".join(re.escape(p) for p in w.split()) for w in words)


_SCRIPT = LEX["script"]
HANGUL = _SCRIPT["hangul_syllables"]  # class body of the Hangul syllables
HANGUL_ANY = _SCRIPT["hangul_jamo"] + _SCRIPT["hangul_compat_jamo"] + _SCRIPT["hangul_syllables"]
WORD_CHAR = f"[{HANGUL}{_SCRIPT['word_other']}]"  # one letter of a word (old _WORD)
HANGUL_WORD = re.compile(f"[{HANGUL}]+")
NOT_HANGUL = re.compile(f"[^{HANGUL}]")
HANGUL_CHAR = re.compile(f"[{HANGUL_ANY}]")
HANGUL_SYLLABLE = re.compile(f"[{HANGUL}]")


_P = LEX["punctuation"]
SENTENCE_END = frozenset(_P["sentence_end"])
SEPARATOR_PUNCT = frozenset(_P["separators"])
DOTS = frozenset(_P["dots"])
OPEN, CLOSE = _P["open"], _P["close"]
PUNCT = SENTENCE_END | SEPARATOR_PUNCT | frozenset(OPEN + CLOSE + _P["other"])
COMMA = _P["comma"]
PERIOD = _P["period"]
AXIS_LIST_PUNCT = SEPARATOR_PUNCT | frozenset(_P["axis_list"])
TRIM_CHARS = _P["trim"]
NAME_SPLIT = re.compile(r"[\s" + re.escape(_P["name_split"]) + r"]+")  # between the words of a table name
NAME_SPLIT_FRONT = re.compile(r"[\s" + re.escape(_P["name_split"].replace(OPEN[0], "").replace(CLOSE[0], "")) + r"]+")
LIST_DOTS_NORM = _P["list_dots_norm"]
# marks that end the phrase of a relation word: every list mark (comma, middle dots, semicolon) and sentence end
# (review p08 r1-3: a middle dot between two relation phrases ends the first one's operands too)
RELATION_SEPARATORS = SEPARATOR_PUNCT | SENTENCE_END
NUMBER_MARKS = frozenset(_P["number_marks"])  # marks inside a number ("2.5", "1,000", "10%", "3~5")


_PA = LEX["particles"]
ENDING_WORDS = tuple(_PA["endings"])
ENDINGS = tuple(sorted({index_key(e) for e in _PA["endings"]}, key=len, reverse=True))
NAME_BOUNDARY_PARTICLES = tuple(_PA["name_boundary"])
TOPIC = frozenset(_PA["topic"])
OBJECT = frozenset(_PA["object"])
GENITIVE = frozenset(_PA["genitive"])
GENITIVE_OF = _PA["genitive_of"]  # the genitive particle itself ("의")
COMPLEMENT = frozenset(_PA["complement"])
DESIGNATE = frozenset(_PA["designate"])
RESTRICTIVE = tuple(_PA["restrictive"])
SUBJECT = frozenset(_PA["subject"])
SUBJECT_LIST = tuple(_PA["subject"])
AUX = tuple(_PA["aux"])
PHRASE_END = TOPIC | OBJECT | SUBJECT | frozenset(_PA["phrase_end_endings"])
LIST_TAIL = tuple(_PA["list_tail"])
LIST_SUBJECT = tuple(_PA["list_subject"])
TOKEN_TAIL = tuple(_PA["token_tail"])
MARKER_PARTICLES = tuple(_PA["marker"])
PHRASE_CLOSING = tuple(_PA["phrase_closing"])

_VE = LEX["verb_endings"]
MODIFIER_ENDINGS = frozenset(_VE["modifier"])
ADNOMINAL_FINAL = tuple(_VE["adnominal_final"])


_CO = LEX["coordinator"]
LIST_WORDS = tuple(_CO["words"])
LIST_WORD_KEYS = _key_set(_CO["words"])
JOINER_PARTICLES = tuple(_CO["particles"])
JOINERS = _key_set(_CO["particles"])
JOINER_SUFFIXES = JOINERS | {j + GENITIVE_OF for j in JOINERS}
BARE_JOINERS = tuple(p for p in _CO["particles"] if p not in _CO["after_name_only"])  # 와/과: also on a bare word
AND_WORD = _CO["and_word"]
LIST_JOINER_PARTICLES = tuple(_CO["list_joiners"])  # plan 14 I8: written for the and-word or a list mark in a name
PROTECT_CHARS = "".join(BARE_JOINERS) + GENITIVE_OF  # a joiner or genitive letter inside a name protects its run
# a coordinated list of target names before the genitive ("A와 B의 X"): joiner particles, list words, separators
TARGET_LIST_JOINERS = tuple(dict.fromkeys(list(_CO["particles"]) + [AND_WORD] + [COMMA]))


_RQ = LEX["request"]
REQUEST_WORDS = tuple(_RQ["words"])
REQUEST_FINAL = _key_set(_RQ["forms"]["final"])  # complete asking predicates (the lexicon's form)
REQUEST_EXIST_NEGATIVE = _key_set(LEX["exclusion"]["existence"]["forms"])  # "없어": the existence negation
REQUEST_POLITE = tuple(_RQ["polite_endings"])  # "보여줘요": a final form with a polite ending is final too
CLAUSE_END_REQUESTS = tuple(LEX["clause_end_request"]["words"])
# a request word glued to the name before it ("가상A를보여줘", "가상A보여줄래요"): from the right, a polite ending
# (only after a request form), one final or connective request form (required), one name-boundary particle
_GLUED_REQUESTS = tuple(sorted(_key_set(list(_RQ["forms"]["final"]) + list(CLAUSE_END_REQUESTS)), key=len,
                               reverse=True))
_GLUED_PARTICLES = tuple(sorted(_key_set(LEX["particles"]["name_boundary"]), key=len, reverse=True))


# the end of a word, or a request word glued to it ("지역별로보여줘", "비율을보여줘"): the right boundary of the 2.2
# condition and marker words (a lookahead body), as the spaced form "지역별로 보여줘" reads
GLUED_REQUEST_TAIL = rf"(?:{alt(_GLUED_REQUESTS)})(?:{alt(REQUEST_POLITE)})?"
WORD_END = rf"(?:(?!{WORD_CHAR})|(?={GLUED_REQUEST_TAIL}(?!{WORD_CHAR})))"


def glued_request_splits(word: str, is_known) -> set[int]:
    """Where a request word glued to a known word starts ("기준으로보여줘" -> 4, "없이보여줘", "따로보여줄래요"): from the
    right, [a polite ending] and one request form are taken off; the front (with its particle, as written apart) must
    be a word ``is_known`` accepts. Several places: a guess (the caller takes none)."""
    w = word.lower()
    ends = {len(w)} | {len(w) - len(e) for e in REQUEST_POLITE if w.endswith(e) and len(w) > len(e)}
    return {e - len(r) for e in ends for r in _GLUED_REQUESTS
            if len(r) < e and w[:e].endswith(r) and is_known(word[:e - len(r)])}


def glued_name_cuts(word: str, is_name) -> set[int]:
    """Ends of the names ``is_name`` accepts that a written word holds before a glued request word, taken off one
    layer at a time from the right: [polite ending], one request form, [one particle]. Several ends: the reading
    would be a guess (the caller takes none)."""
    w = word.lower()
    ends = {len(w)} | {len(w) - len(e) for e in REQUEST_POLITE if w.endswith(e) and len(w) > len(e)}
    out = set()
    for e in ends:
        for r in _GLUED_REQUESTS:
            if len(r) < e and w[:e].endswith(r):
                c = e - len(r)
                for cut in [c] + [c - len(p) for p in _GLUED_PARTICLES if len(p) < c and w[:c].endswith(p)]:
                    if cut >= 2 and is_name(word[:cut]):
                        out.add(cut)
    return out
CONNECTIVE_ENDINGS = tuple(LEX["connective"]["endings"])
CONNECTIVE = tuple(sorted(LEX["connective"]["endings"], key=len, reverse=True))
CONNECTIVE_STEMS = _key_set(LEX["connective"]["stems"])

_FI = LEX["filler"]
FILLER_WORDS = tuple(_FI["words"])
BASE_WORD_TEXT = _FI["base_word"]
BASE_WORD = index_key(_FI["base_word"])
MODEL_IGNORABLE = _key_set(_FI["model_ignorable"])
KNOWN_WORDS = _key_set(list(_RQ["words"]) + list(_FI["words"]))  # request and filler words

APPOSITIVE_WORDS = tuple(LEX["appositive"]["words"])


_MK = LEX["markers"]
DISTRIBUTIVE_WORDS = tuple(_MK["distributive"])
SIMULTANEOUS_WORDS = tuple(_MK["simultaneous"])
DISTRIBUTIVE = _key_set(_MK["distributive"])
SIMULTANEOUS = _key_set(_MK["simultaneous"])
COMMON_WORDS = tuple(_MK["common"])
MEASURE_SKIP = tuple(_MK["measure_skip"])

_DV = LEX["divide"]
DIVIDE = _key_set(_DV["words"])
DIVIDE_ADNOMINAL = _key_set(_DV["adnominal"])
DIVIDE_ADVERBS = _key_set(_DV["adverbs"])
SOURCE_ENDINGS = frozenset(_DV["source_endings"])


_NG = LEX["negation"]
NEGATION_WORDS = tuple(_NG["words"])
BASIS_NEGATION_PARTICLES = tuple(_NG["basis_particles"])
BASIS_NEGATION_WORDS = tuple(_NG["basis_words"])
DESIGNATION_NEGATION = tuple(_NG["designation"])

EXCLUSION = LEX["exclusion"]
EXCLUDE_VERBS = tuple(EXCLUSION["verbs"])
EXCLUDE_PARTICLES = tuple(EXCLUSION["particles"])
EXCLUDE_NEGATED_ENDING = EXCLUSION["negated_ending"]
EXCLUDE_NEGATED_BY = tuple(EXCLUSION["negated_by"])
EXCLUDE_SUFFIX = EXCLUSION["suffix"]
DEFINITION_SOURCE = EXCLUSION["definition_source"]
DEFINITION_TAILS = tuple(EXCLUSION["definition_tails"])
EXCLUDE_HINTS = tuple(EXCLUSION["hints"])
_EXIST = EXCLUSION["existence"]
# the existence-negation predicate of an exclusion ("필요 없", "필요가 없", "필요는 없"): one pattern, read by the 2.2
# exclusion rule; its written forms are request words that are never a complete request (REQUEST_EXIST_NEGATIVE)
EXISTENCE_NEGATION = (rf"(?:{alt(_EXIST['nouns'])})(?:{alt(_EXIST['particles'])})?\s*" + re.escape(_EXIST["negative"]))


_RL = LEX["relation"]
MARKER_ENTRIES = {"relation": list(_RL["patterns"]), "function": list(LEX["function"]["patterns"]),
                  "display": list(LEX["display"]["patterns"])}
COMBINE = {index_key(k): v for k, v in _RL["combine"].items()}
COMBINE_WORDS = tuple(_RL["combine"])
SHARE_WORDS = tuple(_RL["share"])
RELATION_LIST_SEPARATORS = _key_set(_RL["list_separators"])


_TN = LEX["tail_nouns"]
BASIS_TAIL_NOUNS = _key_set(_TN["basis"])
TOPIC_LEAD_TAILS = _key_set(_TN["topic_lead"])
NEGATION_TAIL_WORDS = tuple(_TN["negation"])
LIST_BOUNDARY = frozenset(_TN["list_boundary"])

BYEOL_CLASS = LEX["byeol"]
BYEOL = BYEOL_CLASS["marker"]
BYEOL_PARTICLES = tuple(BYEOL_CLASS["particles"])
FREQUENCY_HEADS = frozenset(BYEOL_CLASS["frequency_heads"])

UNITS = LEX["units"]

DERIVED_TAIL_WORDS = tuple(LEX["derived_names"]["tail_words"])  # plan 09 P1: table-name tails a question leaves out

# plan 19 stage 4 (segments only; the rules path does not read it): request words that are also words of another
# class (a request noun, a marker word, a divide adverb, a modifier ending: "한", "함께", "수", ...). The segment
# layer reads such a word as a request only where it stands in a request position (interpret/segments.py).
REQUEST_SHARED = _key_set(REQUEST_WORDS) & (_key_set(_RQ["forms"]["noun"]) | DISTRIBUTIVE | SIMULTANEOUS
                                             | _key_set(COMMON_WORDS) | _key_set(MEASURE_SKIP) | DIVIDE_ADVERBS
                                             | _key_set(MODIFIER_ENDINGS))


LABELS: dict[str, str] = {}
for _label, _words in (("request", REQUEST_WORDS), ("ignore", FILLER_WORDS), ("list_words", LIST_WORDS),
                       ("negation", NEGATION_WORDS), ("appositive", APPOSITIVE_WORDS), ("share", SHARE_WORDS),
                       ("clause_ends", CLAUSE_END_REQUESTS)):
    for _w in _words:
        LABELS.setdefault(index_key(_w), _label)
for _w in COMBINE_WORDS:
    LABELS.setdefault(index_key(_w), "combine")
for _w in EXCLUDE_VERBS + tuple(n + _EXIST["negative"] for n in _EXIST["nouns"]) + (EXCLUDE_SUFFIX,):
    LABELS.setdefault(index_key(_w), "exclude_verb")

# word classes that must not share a word (tests_v3/test_lexicon.py)
CONFLICTS = (("request", "exclusion"), ("request", "negation"), ("filler", "exclusion"), ("filler", "negation"),
             ("coordinator", "request"))


def class_words(name: str) -> frozenset:
    """Index keys of the words of one class, for the conflict check."""
    if name == "request":
        return _key_set(REQUEST_WORDS)
    if name == "exclusion":
        return _key_set(EXCLUDE_VERBS)
    if name == "negation":
        return _key_set(NEGATION_WORDS)
    if name == "filler":
        return _key_set(FILLER_WORDS)
    if name == "coordinator":
        return _key_set(LIST_WORDS + JOINER_PARTICLES)
    raise KeyError(name)
