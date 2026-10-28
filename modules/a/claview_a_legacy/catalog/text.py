"""Name normalisation for the catalog layer.

Two families of keys:

- index keys (term index, name-preserving spans): NFKC, lower case, every whitespace removed. Punctuation is kept,
  so "M2(말잔 원계열)" and "M2(말잔원계열)" share a key but "M2" does not. Derived variants drop the trailing
  footnote mark ("1)"), leading list numbering / industry code prefixes, or every parenthesised part.
- role keys (axis-role table, target dictionary): NFKC, lower case, spaces and punctuation removed, after the
  display cleanup; ``bkey`` also drops parenthesised parts ("비금융법인(사용)" -> 비금융법인).

The role-key helpers are copied from modules/a/scripts/eda_v3/common.py (commit b118a64) so that the four-layer rules give the
same numbers as the EDA recount; the scripts are outside the package boundary and are not imported.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- copied from modules/a/scripts/eda_v3/common.py:32-36 (commit b118a64)

_PUNCT = re.compile(r"[\s·ㆍ・,()\[\]{}<>/\-_.:;'\"~]+")
_FOOTNOTE = re.compile(r"(?:\s*\d{1,2}\))+\s*$")
_LEAD_NUM = re.compile(r"^\s*(?:\(\d+\)|\d+\)|[①-⑳]|\[\d+\]|(?:[A-Z]\s*-\s*)?(?:(?:\d+|[가-하])\.)+(?!\d))\s*")
_LEAD_CODE = re.compile(r"^(?:[A-Z](?:\d+(?:-\d+)?)?\s+)+(?=[가-힣])")
_PAREN = re.compile(r"\s*[(\[][^()\[\]]*[)\]]")
_SPACE = re.compile(r"\s+")


# Source: modules/a/scripts/eda_v3/common.py:39-40 (commit b118a64).
def nfkc(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "")


# Source: modules/a/scripts/eda_v3/common.py:43-44 (commit b118a64).
def nospace(text: str) -> str:
    return _SPACE.sub("", text)


# Source: modules/a/scripts/eda_v3/common.py:47-49 (commit b118a64).
def key(text: str | None) -> str:
    """Role key: NFKC, lower case, spaces and punctuation removed."""
    return _PUNCT.sub("", nfkc(text).lower())


# Source: modules/a/scripts/eda_v3/common.py:52-61 (commit b118a64).
def clean_value(text: str | None) -> str:
    """Display-level cleanup of a value name: footnote marks, list numbering, industry code prefixes."""
    s = nfkc(text).strip()
    s = _FOOTNOTE.sub("", s)
    prev = None
    while prev != s:
        prev = s
        s = _LEAD_NUM.sub("", s)
    s = _LEAD_CODE.sub("", s)
    return s.strip()


# Source: modules/a/scripts/eda_v3/common.py:68-70 (commit b118a64).
def bkey(text: str | None) -> str:
    """Base key: cleaned value with every parenthesised part removed ("비금융법인(사용)" -> "비금융법인")."""
    return key(strip_parens(clean_value(text)))


# Source: modules/a/scripts/eda_v3/common.py:77-83 (commit b118a64).
def strip_parens(text: str) -> str:
    prev = None
    s = text
    while prev != s:
        prev = s
        s = _PAREN.sub("", s)
    return s.strip()


# Source: modules/a/scripts/eda_v3/common.py:86-90 (commit b118a64).
def paren_front(title: str) -> str:
    """Table name before its first parenthesis (the trailing qualifier)."""
    t = nfkc(title)
    i = t.find("(")
    return t[:i].strip() if i > 0 else t.strip()


# ---------------------------------------------------------------- new in claview_a_legacy


def collapse(text: str | None) -> str:
    """NFKC, every whitespace run as one space, trimmed (display form)."""
    return _SPACE.sub(" ", nfkc(text)).strip()


def strip_footnote(text: str | None) -> str:
    """Trailing footnote marks removed ("M2(말잔) 1)" -> "M2(말잔)")."""
    return _FOOTNOTE.sub("", nfkc(text)).strip()


def index_key(text: str | None) -> str:
    """Index key: NFKC, lower case, every whitespace removed (punctuation kept)."""
    return nospace(nfkc(text).lower())


# A bracket that holds only a year range ("(2009~)", "(~2006)", "(1990~1997)", "(1975-2005)") or one year ("(2003)").
_YEAR_RANGE_PAREN = re.compile(r"\s*\(\s*(?:\d{4})?\s*~\s*(?:\d{2,4})?\s*\)|\s*\(\d{4}\s*-\s*\d{4}\)|\s*\(\d{4}\)")


def strip_year_range(name: str | None) -> str | None:
    """The name without its year-range brackets ("금융거래표(2009~)" -> "금융거래표"); None when it has none.

    Plan 04, 2-5 and decision 6: such a name is the same name for exact matching. A bracket that also holds other words
    ("(2007~2010, 표본조사)") is a qualifier and is kept. Not part of ``name_variants`` (its keys stay as they were).
    """
    text = nfkc(name)
    if not _YEAR_RANGE_PAREN.search(text):
        return None
    out = _YEAR_RANGE_PAREN.sub("", text).strip()
    return out or None


def name_variants(name: str | None) -> dict[str, str]:
    """Index-key variants of one catalog name: {variant: key}. Empty keys and repeats of an earlier key are dropped.

    - ``name``: the name itself (NFKC, spaces removed)
    - ``no_footnote``: trailing footnote marks removed
    - ``no_marks``: footnotes, leading list numbering and industry code prefixes removed (``clean_value``)
    - ``no_paren``: the cleaned name with every parenthesised part removed
    """
    raw = nfkc(name)
    cleaned = clean_value(raw)
    forms = (
        ("name", raw),
        ("no_footnote", strip_footnote(raw)),
        ("no_marks", cleaned),
        ("no_paren", strip_parens(cleaned)),
    )
    out: dict[str, str] = {}
    seen: set[str] = set()
    for variant, form in forms:
        k = index_key(form)
        if k and k not in seen:
            out[variant] = k
            seen.add(k)
    return out
