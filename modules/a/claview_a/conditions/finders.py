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
from typing import TYPE_CHECKING
from ..shapes import ID_PREFIX, Condition, PeriodExpr, TextView
from . import q_rules
from .q_nodes import frequencies
from . import words as rules


if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer


# Any reference date: the period rules are asked once with it only to learn whether a period could be resolved once a
# reference date is known (``attrs.extract_status``); the dates they compute are not used.
_PROBE_AS_OF = "2000-01-01"


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
    """Explicit frequencies (reused Q3 rules; bare 월말·연말 added in plan 36) in the new shape. IDs F1, F2, ... in
    question order."""
    rows = frequencies(text["raw"], text["norm"], text["offsets"])
    return [frequency_condition(row) for row in rows]
