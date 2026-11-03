"""Period and frequency of a statistic, and the final tables among a representative's candidates (plan 37, step 3).

The current model structures period and frequency meaning; older answers and typed user answers use the quote
reader. Code resolves either condition shape per table with the period resolver. Nothing here copies its arithmetic.

Reading a quote (``read_quotes``; step-3 review, user 2026-10-27 21:36 UTC):
- explicit base-year marks are not periods: "YYYY=100", a year followed by "기준년"/"기준연도"/"기준년도", and
  "기준연도 YYYY년" are taken out, and the removal is recorded; a quote left with only such marks is recorded as no
  period (user 2026-10-29 07:51 UTC: until then "YYYY년 기준" was removed too). A year followed by a lone "기준"
  ("2024년 기준") is read as the year the model quoted as a period (``plain_standard`` recorded), also next to other
  periods ("2023년 기준과 2024년 기준": two points; the same year twice: that year; separate quotes: each as the
  model quoted it). Only a quoted expression in which a lone-기준 year stands directly before another period
  ("2015년 기준 2024년", "2015년 기준으로 2024년") cannot be told apart (base year or observation year):
  ``period_base_year_ambiguous`` with the expression, never a removal, never a default (users 08:24 and 08:42 UTC:
  judged on the statistic's own quoted expression, never on two quotes standing next to each other);
- a closed month or quarter range that names its year once ("2024년 1~3월", "2024년 1월~3월", "2024년 1~4분기") is
  rewritten to name the year at both ends (x20: 10 sessions dropped on it); the quote, the rewrite and the reading are
  recorded;
- a quote that gives no condition is ``period_unresolved`` / ``frequency_unresolved``, and a quote with a word left
  outside every condition read from it that can change the period (``leftover``: a number, a length written in
  Korean words such as "석 달" or "두 분기", or a period modifier such as "제외", "이전까지", "개월") is ``period_partially_read`` / ``frequency_partially_read`` (for example
  "2023, 2024년" read as 2024 only, "2024년 1월 이후 3개월" read without "3개월"): never a default instead. Other
  leftover words are recorded only (``ignored``). The reading row keeps the quote, the rewrite, the conditions and
  the words left.
- decades and open ends (plan 53, user 2026-10-30 02:07 UTC): "N0년대 이후/부터/이래" is rewritten to "N0년 이후"
  (the open request from N0, ended by the existing anchor rule) and "N0년대" alone to "N0년부터 N0+9년까지", each
  rewrite recorded (``rewrites``); a decade with any other period word after it ("2000년대 초반", "2000년대까지")
  stays unread. "…부터 최근까지" / "현재까지" / "지금까지" right after a since is read with it (``open_end_words``).
  Qualifiers with no agreed range ("초반", "중반", "후반", "들어"), and a decade left unread, are words that change
  the period: ``period_partially_read`` with ``qualifier_undefined``, never a default or a single year;
- the end word of a frequency read with its end point ("분기말" read as the quarterly frequency with
  ``anchor="end"``) is read with it, so it is not a word left (plan 36); a "말" with no end point read stays a leftover
  word.
Only when no period (or no frequency) is quoted does the resolver's own default apply (``claview_period`` policy:
the longest frequency the table offers, the latest 10 years for Y, 5 for H and Q, 3 for M), and that is recorded.

Final selection (``final_tables``) among the candidates code may switch to; ``usable`` names the ones whose content is
verified by the caller (``check``), the others are only reported. Every candidate is resolved against the same
request: a request that counts from an anchor uses one common anchor. With the policy's ``anchor_rule``
``as_of_calendar`` a calendar-relative or explicit open period counts from the period as_of falls in;
the part after a candidate's latest period is ``latest_unpublished`` (the candidate still covers the request when no
other candidate covers that part, and the period handed on ends at as_of's period); a request that starts before every
usable candidate fails ``period_not_covered`` with detail ``past_shortfall``. A request with no period keeps the latest end of the usable candidates in the chosen frequency
(the rule before, also for ``data_latest``). The separate ``recent_anchor_rule: data_latest`` uses a candidate
group's latest reference for recent N units; it does not move calendar aliases or explicit dates. Then:
- one usable table that covers the request: that table (the newest of several that do; plan 53, user 2026-10-30
  02:07 UTC: among those tied on the newest end the representative stays when it is usable and covers the request,
  since the candidates' order is no priority; a tie is recorded as ``tie``. A newer candidate still wins, and the
  representative failing a check or the request keeps no priority);
- else the fewest usable tables, at most ``MAX_STITCH``, whose covered months together cover the request, each with
  its own share; the shares are checked to cover the request with no gap and no overlap, and each table is resolved
  again on its share;
- else the reason: ``stitch_limit_exceeded`` (the usable tables together cover it, but not within ``MAX_STITCH``),
  ``content_unverified`` (only a candidate whose content is not verified covers it), ``frequency_not_offered``,
  ``period_not_covered``, ``period_unresolved``.
The representative failing to cover is never a failure before its candidates are tried.

Nothing here names a table, case or question.
"""

from __future__ import annotations

import copy
import json
import re
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

from ..catalog.text import index_key, nfkc, strip_footnote
from ..conditions.finders import find_frequency_conditions, find_period_conditions
from ..conditions.q_rules import FREQ_END_AFTER
from ..text.normalize import normalize_text
from . import table_rules as P
from ..search.families import FREQUENCY_WORDS as _FREQUENCY_WORDS


# .8: only explicit base-year marks are removed; "YYYY년 기준" is the quoted year or ambiguous (user 2026-10-29
# 07:51 UTC); "최근 N개 분기" reads as "최근 N분기"
# .9: ambiguous only for a lone-기준 year directly before another period, not for every second period (08:24 UTC)
# .10: within one quoted expression only, never from two quotes next to each other (08:42 UTC)
# .11: decades, the open end "…부터 최근까지", undefined qualifiers never dropped (plan 53, user 2026-10-30 02:07 UTC)
# .12: a representative tied on the newest end stays (plan 53, B)
# .13: the as_of calendar anchor, latest_unpublished and past_shortfall (policy ①, plan 56 order 4)
# .14: frequency_fit, valid_frequencies, the condition rule and the presets of the condition questions (plan 56 order 5)
# .15: the representative first in the choice and in the stitch assignment (plan 56 order 7)
# .16: the three edition judgments, the relation status and the relation file (plan 56 order 7)
# .17: the edition reasons (edition_relation_conflict, edition_evidence_insufficient) and the judgments trace
# .18: the latest month held elsewhere on the card bounds the latest-unpublished months (plan 56 order 8)
PERIODS_VERSION = "tl-periods-2026-11-03.23"  # .23: allow future ranges actually covered by compatible editions
MAX_STITCH = 3  # tables joined across edition boundaries for one statistic

_MONTH_RANGE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월?\s*[~\-–]\s*(\d{1,2})\s*월")
_QUARTER_RANGE = re.compile(r"(\d{4})\s*년\s*([1-4])\s*(?:분기)?\s*[~\-–]\s*([1-4])\s*분기")
_BASE_YEAR = re.compile(r"\(?\s*\d{4}\s*=\s*100\s*\)?|\d{4}\s*년?\s*기준\s*(?:년도|연도|년)(?![가-힣])"
                        r"|기준\s*(?:년도|연도|년)\s*\d{4}\s*년?")
# a year with a lone "기준" after it: a point the model quoted as a period
_PLAIN_STANDARD = re.compile(r"\d{4}\s*년?\s*기준(?!\s*(?:년도|연도|년))(?![가-힣])")
# ... directly before another period: base year or observation year cannot be told apart
_STANDARD_BEFORE_PERIOD = re.compile(r"\d{4}\s*년?\s*기준(?:\s*(?:으로|의))?\s*(?=\d{4})")
_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
_LEXICON = json.loads((Path(__file__).resolve().parents[1] / "data" / "lexicon.json").read_text(encoding="utf-8"))
_ENDINGS = sorted(_LEXICON["particles"]["endings"], key=len, reverse=True)
# a length written in Korean words: a native or Sino-Korean quantity and a period unit, spaced or not ("석 달",
# "세 개월", "두 분기", "한 해", "삼년"); user 2026-10-27 22:20 UTC
_KOREAN_LENGTH = re.compile(r"(?:한|두|세|석|네|넉|다섯|여섯|일곱|여덟|아홉|열|몇|일|이|삼|사|오|육|칠|팔|구|십)\s*"
                            r"(?:개월|달|분기|해|년|반기|주|주일|일)")
# words that change a requested period when written next to it (user 2026-10-27 21:57 UTC); vocabulary only
PERIOD_MODIFIERS = frozenset(["제외", "빼고", "제외하고", "이전", "이전까지", "전까지", "이후", "부터", "까지", "직전",
                              "동안", "개월", "년간", "분기간", "최근", "지난", "말", "초", "중순", "상순", "하순"])
# qualifiers with no agreed range: never dropped, even glued to another word (plan 53, user 2026-10-30 02:07 UTC)
_UNDEFINED_QUALIFIER = re.compile(r"초반|중반|후반|들어")
# decades (user 2026-10-30 02:07 UTC): "N0년대 이후/부터/이래" is the open request from N0 (the existing anchor rule
# ends it); "N0년대" alone is N0 to N0+9; a decade with any other period word after it is left unread
_DECADE_SINCE = re.compile(r"(\d{3}0)\s*년대\s*(이후|부터|이래)")
_DECADE = re.compile(r"(\d{3}0)\s*년대(?!\s*(?:이후|부터|이래|까지|이전|전|후|초|중|말|들어|무렵|경|쯤))")
_DECADE_LEFT = re.compile(r"\d{4}\s*년대\S*")
# the open end written after a since ("2020년부터 최근까지" = "2020년부터 현재까지"): read with it
_OPEN_END = re.compile(r"\s*(?:최근|현재|지금)\s*까지")


def normalize_quote(quote: str) -> str:
    """Name the year at both ends of a closed month or quarter range that names it once."""
    text = _MONTH_RANGE.sub(lambda m: f"{m.group(1)}년 {int(m.group(2))}월~{m.group(1)}년 {int(m.group(3))}월", quote)
    return _QUARTER_RANGE.sub(lambda m: f"{m.group(1)}년 {m.group(2)}분기~{m.group(1)}년 {m.group(3)}분기", text)


def decade_quote(text: str) -> tuple[str, list[dict]]:
    """(text, rewrites): a decade written as an agreed reading (``_DECADE_SINCE``, ``_DECADE``) in the words the
    period rules read ("2000년대 이후" -> "2000년 이후", "2000년대" -> "2000년부터 2009년까지"), each rewrite
    recorded {from, to, rule}. Any other decade is left as written (``_DECADE_LEFT``: unread)."""
    rewrites: list[dict] = []

    def since(m) -> str:
        to = f"{m.group(1)}년 {m.group(2)}"
        rewrites.append({"from": m.group(0), "to": to, "rule": "decade_since"})
        return to

    def span(m) -> str:
        to = f"{m.group(1)}년부터 {int(m.group(1)) + 9}년까지"
        rewrites.append({"from": m.group(0), "to": to, "rule": "decade"})
        return to

    return _DECADE.sub(span, _DECADE_SINCE.sub(since, text)), rewrites


def strip_base_year(quote: str) -> tuple[str, list[str]]:
    removed = [m.group(0).strip() for m in _BASE_YEAR.finditer(quote)]
    return re.sub(r"\s+", " ", _BASE_YEAR.sub(" ", quote)).strip(" ,"), removed


def leftover(view: dict, found: list[dict], kind: str) -> tuple[list[str], list[str]]:
    """(changing, ignored): words of the quote outside every condition read from it. ``changing``: words that can
    change the requested period or frequency and were not read: a number (a year, "3개월") or a period modifier of
    ``PERIOD_MODIFIERS`` ("제외", "이전까지"); ``ignored``: the other leftover words, recorded only (user 2026-10-27
    21:57 UTC: not every leftover letter is an error)."""
    norm = view["norm"]
    spans = [c.get("span") or [0, 0] for c in found]
    rest = "".join(ch if not any(a <= i < b for a, b in spans) else " " for i, ch in enumerate(norm))
    changing, ignored = [], []
    lengths = [(m.start(), m.end()) for m in _KOREAN_LENGTH.finditer(rest)]
    lengths += [(m.start(), m.end()) for m in _UNDEFINED_QUALIFIER.finditer(rest)]
    changing += [rest[a:b] for a, b in lengths]
    rest = "".join(" " if any(a <= i < b for a, b in lengths) else ch for i, ch in enumerate(rest))
    for m in _WORD.finditer(rest):
        word = m.group(0)
        stem = next((word[:-len(e)] for e in _ENDINGS if word.endswith(e) and len(word) > len(e)), word)
        if re.search(r"\d", word) or word in PERIOD_MODIFIERS or stem in PERIOD_MODIFIERS:
            changing.append(word)
        else:
            ignored.append(word)
    return changing, ignored


def _with_end_word(view: dict, c: dict) -> dict:
    """The condition with its span stretched over the end word right after it ("분기" + "말") when it was read with
    the end point (``anchor="end"``), for ``leftover`` only; any other condition is returned as it is."""
    if (c.get("attrs") or {}).get("anchor") != "end" or not c.get("span"):
        return c
    m = FREQ_END_AFTER.match(view["raw"], c["span"][1])
    return {**c, "span": [c["span"][0], m.end()]} if m else c


def _with_open_end(view: dict, c: dict) -> tuple[dict, str | None]:
    """(condition, open-end words): a since condition with its span stretched over the open end right after it
    ("2020년부터" + " 최근까지"), for ``leftover`` only; the open end is the since's own (the resolver's anchor rule
    ends it, unchanged). Any other condition is returned as it is."""
    if (c.get("attrs") or {}).get("form") != "since" or not c.get("span"):
        return c, None
    m = _OPEN_END.match(view["raw"], c["span"][1])
    return ({**c, "span": [c["span"][0], m.end()]}, m.group(0).strip()) if m else (c, None)


def _frequency_word(word: str) -> bool:
    return word in _FREQUENCY_WORDS or (word.endswith("별") and word[:-1] in _FREQUENCY_WORDS)


def _standard_before_period(texts: list[str]) -> list[dict]:
    """The lone-기준 years directly before another period within one quoted period text."""
    return [{"quote": t, "expression": m.group(0).strip() + " " + t[m.end():].strip()}
            for t in texts for m in _STANDARD_BEFORE_PERIOD.finditer(t)]


def read_quotes(period_quotes: list[str], frequency_quotes: list[str]) -> dict:
    """{conditions, readings, problems}: the conditions read from the quotes (IDs made unique), one reading row per
    quote {kind, quote, base_year_removed, read_as, conditions, uncovered}, and the problems
    (``period_unresolved``, ``period_partially_read``, ``frequency_unresolved``, ``period_base_year_ambiguous``)
    with their quotes."""
    conditions: list[dict] = []
    readings: list[dict] = []
    problems: list[dict] = []
    for kind, quotes, finder in (("period", period_quotes, find_period_conditions),
                                 ("frequency", frequency_quotes, find_frequency_conditions)):
        for quote in quotes:
            removed: list[str] = []
            rewrites: list[dict] = []
            text = quote
            if kind == "period":
                text, removed = strip_base_year(quote)
                text = normalize_quote(text) if text else text
                text, rewrites = decade_quote(text) if text else (text, [])
            row = {"kind": kind, "quote": quote, "base_year_removed": removed, "read_as": text, "conditions": [],
                   "uncovered": []}
            if kind == "period":
                row["plain_standard"] = [m.group(0).strip() for m in _PLAIN_STANDARD.finditer(text or "")]
                row["rewrites"] = rewrites
            readings.append(row)
            if not text:
                continue  # only a base-year mark: no period was said
            view = normalize_text(text)
            found = finder(view)
            if not found:
                problems.append({"code": f"{kind}_unresolved", "quote": quote, "read_as": text})
                continue
            if kind == "frequency":
                read = [_with_end_word(view, c) for c in found]
            else:
                pairs = [_with_open_end(view, c) for c in found]
                read = [c for c, _ in pairs]
                row["open_end_words"] = [w for _, w in pairs if w]
            row["uncovered"], row["ignored"] = leftover(view, read, kind)
            decades = [m.group(0) for m in _DECADE_LEFT.finditer(text)] if kind == "period" else []
            undefined = decades + [w for w in row["uncovered"] if _UNDEFINED_QUALIFIER.fullmatch(w)]
            if undefined:  # never dropped, never a default or a single year (plan 53)
                row["qualifier_undefined"] = undefined
                row["uncovered"] = decades + [w for w in row["uncovered"] if w not in decades]
            if row["uncovered"]:
                problems.append({"code": f"{kind}_partially_read", "quote": quote, "read_as": text,
                                 "uncovered": row["uncovered"],
                                 **({"qualifier_undefined": undefined} if undefined else {})})
                continue
            for c in found:
                c = copy.deepcopy(c)
                c["id"] = f"{c['id'][0]}{len(conditions) + 1}"
                conditions.append(c)
                row["conditions"].append(c["id"])
    for row in _standard_before_period([r["read_as"] for r in readings if r["kind"] == "period" and r["read_as"]]):
        problems.append({"code": "period_base_year_ambiguous", **row,
                         "why": "a year with a lone 기준 directly before another period: base year or observation "
                                "year cannot be told apart"})
    return {"conditions": conditions, "readings": readings, "problems": problems}


def frequency_points(reading: dict, rows: list[dict]) -> list[dict]:
    """One row per frequency read from the quoted frequency words ``rows`` (the model's ``frequencies`` entries, in the
    order given to ``read_quotes``): {quote, for, frequency, point_in_period}. ``point_in_period`` is the point of each
    period the words name ("end" for 월말, 분기말, 연말; None when not said). A quote that read nothing gives one row with
    ``frequency`` None and its problem code. Record only: it chooses no basis (말잔/평잔), and it is not the resolver's
    ``anchor`` (the latest data point a request counts back from)."""
    by_id = {c["id"]: c for c in reading["conditions"]}
    problems = {p["quote"]: p["code"] for p in reading["problems"] if p["code"].startswith("frequency_")}
    reads = [r for r in reading["readings"] if r["kind"] == "frequency"]
    out = []
    for row, read in zip(rows, reads):
        base = {"quote": row["quote"], "for": list(row["for"])}
        found = [by_id[i] for i in read["conditions"]]
        if not found:
            out.append({**base, "frequency": None, "point_in_period": None,
                        "problem": problems.get(row["quote"])})
        for c in found:
            out.append({**base, "frequency": c["value"], "point_in_period": (c.get("attrs") or {}).get("anchor")})
    return out


def conditions_of(period_quotes: list[str], frequency_quotes: list[str]) -> tuple[list[dict], list[dict]]:
    """(conditions, readings) of ``read_quotes`` (problems left out: callers that need them use ``read_quotes``)."""
    got = read_quotes(period_quotes, frequency_quotes)
    return got["conditions"], got["readings"]


def resolve(conditions: list[dict], view, as_of: str, assignment: list | None = None,
            context: dict | None = None) -> dict:
    inp = {"conditions": conditions, "condition_fields": {}, "inferred": None, "as_of": as_of}
    return P.resolve(inp, view, assignment=assignment, context=context)


def _months(rows: list[dict] | None) -> list[tuple[int, int]]:
    return [(P.month(r["start"]), P.month(r["end"])) for r in rows or []]


def _need(requested: list[tuple[int, int]]) -> set[int]:
    return {m for a, b in requested for m in range(a, b + 1)}


def _have(spans: list[tuple[int, int]]) -> set[int]:
    return {m for a, b in spans for m in range(a, b + 1)}


def _cover_set(requested: list[tuple[int, int]], covered: dict[str, list[tuple[int, int]]],
               rep: str | None = None) -> list[str] | None:
    """Fewest tables (at most MAX_STITCH) whose covered months together hold every requested month; of one size, a
    combination with the representative ``rep`` first (plan 56 order 7)."""
    need = _need(requested)
    tables = [t for t in covered if covered[t]]
    for size in range(1, MAX_STITCH + 1):
        combos = sorted(combinations(tables, size), key=lambda c: rep not in c)  # stable: the order is kept otherwise
        for combo in combos:
            if need <= {m for t in combo for m in _have(covered[t])}:
                return list(combo)
    return None


def _assign(requested: list[tuple[int, int]], chosen: list[str], covered: dict) -> dict[str, list[tuple[int, int]]]:
    """Each requested month to the first chosen table that covers it (the caller puts the representative first,
    then newest first)."""
    out: dict[str, list[int]] = {t: [] for t in chosen}
    for m in sorted(_need(requested)):
        for t in chosen:
            if any(x <= m <= y for x, y in covered[t]):
                out[t].append(m)
                break
    spans: dict[str, list[tuple[int, int]]] = {}
    for t, months in out.items():
        rows: list[list[int]] = []
        for m in months:
            if rows and m == rows[-1][1] + 1:
                rows[-1][1] = m
            else:
                rows.append([m, m])
        spans[t] = [(a, b) for a, b in rows]
    return spans


def _end_key(view) -> tuple[int, int]:
    best = (0, 0)
    for f, _, end in view.periods:
        got = P.metadata_months(f, end)
        if got:
            best = max(best, divmod(got[1], 12))
    return best


def _anchor(views: list, frequency: str) -> str | None:
    """The latest end, as YYYY-MM, of the tables in ``frequency``."""
    ends = []
    for view in views:
        for f, _, end in view.periods:
            got = P.metadata_months(f, end) if f == frequency else None
            if got:
                ends.append(got[1])
    return P.month_text(max(ends)) if ends else None


def _report(results: dict, usable: dict) -> dict:
    return {t: {"status": r["status"], "issue": (r.get("issue") or {}).get("code"), "frequency": r.get("frequency"),
                "source": r.get("source"), "assumptions": r.get("assumptions"),
                "requested": (r.get("periods") or {}).get("requested_months"),
                "covered": (r.get("periods") or {}).get("covered_months"), "usable": usable.get(t)}
            for t, r in results.items()}


def _full(result: dict, top: int | None = None) -> bool:
    """The result covers the request: nothing missing, or (as_of calendar) only its latest_unpublished part, after
    ``top`` (the latest month any usable candidate covers) when given."""
    if result["status"] != "ok":
        return False
    periods = result.get("periods") or {}
    kinds = periods.get("missing_kinds") or []
    if not kinds:
        return not periods.get("missing")
    if any(k["kind"] != "latest_unpublished" for k in kinds):
        return False
    return top is None or all(P.month(k["start"]) > top for k in kinds)


def _unpublished(result: dict) -> list[dict]:
    return [{"start": k["start"], "end": k["end"]} for k in (result.get("periods") or {}).get("missing_kinds") or []
            if k["kind"] == "latest_unpublished"]


def _calendar_context(rep: str, conditions: list[dict], as_of: str) -> dict | None:
    """The as_of calendar context of a request with a quoted period, when the policy's anchor rule is the calendar."""
    if not any(c["kind"] == "period" for c in conditions) or not as_of:
        return None
    from .resolver import default_policy

    if default_policy().anchor_rule != "as_of_calendar":
        return None
    return {"bundle_id": f"tl:{rep}", "anchor_source": "as_of_calendar", "anchor": as_of[:7]}


def data_recent(conditions: list[dict]) -> bool:
    """At least one recent range uses the data anchor, not a calendar alias or a stated date."""
    from .resolver import default_policy

    return default_policy().recent_anchor_rule == "data_latest" and any(
        c.get("kind") == "period" and (c.get("attrs") or {}).get("form") == "recent" for c in conditions)


EDITION_RELATIONS = json.loads((Path(__file__).resolve().parents[1] / "data" / "edition_relations.json")
                               .read_text(encoding="utf-8"))


def latest_month(views: list) -> dict[str, int]:
    """Frequency -> the latest month any of ``views`` holds in it, by its metadata (a month number)."""
    out: dict[str, int] = {}
    for v in views:
        for f, _, end in v.periods:
            if got := P.metadata_months(f, end):
                out[f] = max(out.get(f, got[1]), got[1])
    return out


def _relation(rep: str, t: str, relations: list[dict]) -> dict | None:
    return next((r for r in relations if set(r.get("tables") or []) == {rep, t}), None)


def _units(view) -> set[str]:
    """The table's units, or every unit it shows (ITEM rows, values) when the table names none."""
    got = set(getattr(view, "table_units", None) or ())
    return got or (view.units() if hasattr(view, "units") else set())


def edition_judgment(rep: str, t: str, usable: dict, views: dict, relations: list[dict] | None = None,
                     unknown: tuple = ()) -> dict:
    """The three judgments before ``t`` replaces or is stitched to the representative ``rep`` (plan 56 order 7, ⑦),
    recorded apart: a relation entry (``data/edition_relations.json``); the preservation evidence (the same candidate
    group, which shares the title basis and other bracket words; usable, so the cited values held or the same value
    names; the same known table unit; no axis the card splits left without a value, ``unknown``); the compatibility
    (both units known and different: a confirmed incompatibility). relation_status: verified (an entry that allows it),
    unverified (no entry, the evidence sufficient), conflict (an incompatibility or an entry that forbids it), None
    (the evidence insufficient: no automatic replacement or stitch)."""
    relation = _relation(rep, t, EDITION_RELATIONS["relations"] if relations is None else relations)
    ua, ub = (_units(views[x]) for x in (rep, t))
    why = []
    if t not in usable:  # usable: the cited values held or the same value names (``check._kept``)
        why.append("content_unverified")
    if not ua or not ub:
        why.append("unit_unknown")
    elif ua != ub:
        why.append("unit_differs")
    if t in unknown or rep in unknown:
        why.append("basis_unknown")
    conflict = bool(ua and ub and ua != ub) or (relation is not None and relation.get("allows") is False)
    evidence = "insufficient" if why else "sufficient"
    status = "conflict" if conflict else "verified" if relation else "unverified" if evidence == "sufficient" else None
    return {"table": t, "relation": relation, "evidence": evidence, "evidence_why": why,
            "compatibility": "conflict" if conflict else "no_conflict_found", "relation_status": status,
            "units": {"representative": sorted(ua), "table": sorted(ub)}}


def final_tables(rep: str, candidates: list[str], usable: dict[str, str], views: dict, conditions: list[dict],
                 as_of: str, relations: list[dict] | None = None, unknown: tuple = (),
                 held_elsewhere: dict[str, int] | None = None) -> dict:
    """{status, tables: [{table_id, period, frequency}], results, reason, detail, anchor, edition_judgments}. The
    representative first (plan 56 order 7, ⑦): a usable representative covering the request is the final table; a
    replacement or a stitch only for what it does not cover, and only with a table whose edition judgment allows it
    (``edition_judgment``)."""
    calendar = _calendar_context(rep, conditions, as_of)  # any quoted period: its unpublished end is told apart
    results = {t: resolve(conditions, views[t], as_of, context=calendar) for t in candidates}
    anchor = calendar["anchor"] if calendar else None
    contexts = {t: calendar for t in candidates}
    if calendar and data_recent(conditions):
        # Editions of one statistic must compete for the same recent window. An old edition must not win by
        # moving its own window backwards. A known later edition still bounds this window when its content
        # cannot yet be confirmed; that case stays unresolved rather than selecting stale data.
        latest = latest_month([views[t] for t in candidates])
        for f, end in (held_elsewhere or {}).items():
            latest[f] = max(latest.get(f, end), end)
        for t, result in results.items():
            f = result.get("frequency")
            if f in latest:
                contexts[t] = {**calendar, "recent_anchor": P.month_text(latest[f])}
        results = {t: resolve(conditions, views[t], as_of, context=contexts[t]) for t in candidates}
        only_recent = all(c.get("kind") != "period" or c["attrs"]["form"] == "recent" for c in conditions)
        anchors = {c["recent_anchor"] for c in contexts.values() if c and c.get("recent_anchor")}
        if only_recent:
            anchor = next(iter(anchors)) if len(anchors) == 1 else None
    request = {"conditions": conditions}
    if P.anchored_request(request) and not calendar:
        freqs = {results[t].get("frequency") for t in candidates if t in usable and results[t].get("frequency")}
        if len(freqs) == 1:
            anchor = _anchor([views[t] for t in candidates if t in usable], next(iter(freqs)))
        if anchor:
            context = {"bundle_id": f"tl:{rep}", "anchor_source": "bundle_common", "anchor": anchor}
            results = {t: resolve(conditions, views[t], as_of, context=context) for t in candidates}
    report = _report(results, usable)
    base = {"results": report, "anchor": anchor}
    bad = [r for r in results.values() if r["status"] in ("invalid_input", "unresolved")]
    if bad and len(bad) == len(results):
        code = (bad[0].get("issue") or {}).get("code") or bad[0]["status"]
        return {"status": "failed", "tables": [], "reason": "period_unresolved", "detail": code, **base}
    order = sorted(candidates, key=lambda t: (_end_key(views[t]), candidates.index(t)), reverse=True)  # newest first
    # the latest month any candidate covers (a usable one or not): a newer table holds what an older one lacks
    tops = [m[1] for t in order if results[t]["status"] == "ok"
            for m in _months((results[t].get("periods") or {}).get("covered_months"))]
    top = max(tops) if calendar and tops else None
    freq = next((results[t].get("frequency") for t in order if t in usable and results[t].get("frequency")), None)
    if calendar and held_elsewhere and freq in held_elsewhere:
        # a later month another table holds in the same frequency is published, not unpublished (policy ①)
        top = held_elsewhere[freq] if top is None else max(top, held_elsewhere[freq])
    judged = {t: edition_judgment(rep, t, usable, views, relations, unknown) for t in candidates if t != rep}
    allowed = {rep} | {t for t, j in judged.items() if j["relation_status"] in ("verified", "unverified")}
    base["edition_judgments"] = judged
    full_any = [t for t in order if t in usable and _full(results[t], top)]
    full = [t for t in full_any if t in allowed]
    if rep in full:  # policy ⑦: the representative covering the request stays, a later sibling included
        full = [rep] + [t for t in full if t != rep]
    if full:
        t = full[0]
        tied = [x for x in full if _end_key(views[x]) == _end_key(views[t])]
        tie = {"tie": {"tables": tied, "kept": "representative" if t == rep else "candidate_order"}} \
            if len(tied) > 1 else {}
        unpublished = _unpublished(results[t])
        status = {"relation_status": judged[t]["relation_status"]} if t != rep else {}
        return {"status": "selected", "tables": [{"table_id": t, "period": results[t]["periods"].get("requested_months"),
                                                  "frequency": results[t].get("frequency"), **status,
                                                  **({"latest_unpublished": unpublished} if unpublished else {})}],
                "reason": None, "detail": None, **tie, **base}
    requested = next((_months((r.get("periods") or {}).get("requested_months")) for r in results.values()
                      if (r.get("periods") or {}).get("requested_months")), [])
    full_request = list(requested)
    frequencies = {results[t].get("frequency") for t in order if t in usable and results[t].get("frequency")}
    covered_any = {t: _months((results[t].get("periods") or {}).get("covered_months")) for t in order
                   if t in usable and results[t]["status"] in ("ok", "no_data_in_range")}
    # the representative first, then newest first; only tables the edition judgment allows
    covered = {t: covered_any[t] for t in ([rep] if rep in covered_any else []) + [x for x in covered_any if x != rep]
               if t in allowed}
    if any(k.get("kind") == "after_as_of" for r in results.values()
           for k in (r.get("periods") or {}).get("missing_kinds") or []) and not (
            requested and len(frequencies) == 1
            and _need(requested) <= {m for spans in covered.values() for m in _have(spans)}):
        # A candidate's missing future range may already be held by a compatible edition. Only actual
        # coverage of the whole request can proceed to stitching; an unheld future tail cannot be clipped.
        return {"status": "failed", "tables": [], "reason": "period_not_covered",
                "detail": "after_as_of", **base}
    unpublished_rows = []
    if top is not None and requested and max(b for _, b in requested) > top:
        # the months after every usable candidate's latest one are latest_unpublished: no table is needed for them
        unpublished_rows = [{"start": P.month_text(max(a, top + 1)), "end": P.month_text(b)} for a, b in requested
                            if b > top]
        requested = [(a, min(b, top)) for a, b in requested if a <= top]
    if requested and len(frequencies) == 1:
        chosen = _cover_set(requested, covered, rep)
        if chosen:
            spans = _assign(requested, chosen, covered)
            if unpublished_rows:
                # Selection needs published coverage, but emitted assignments must retain the user's entire
                # request. Its unpublished tail belongs to the edition holding the preceding covered month.
                tails = _months(unpublished_rows)
                for start, end in tails:
                    owner = next((t for t in chosen if spans[t] and spans[t][-1][1] == start - 1), None)
                    if owner is None:
                        return {"status": "failed", "tables": [], "reason": "stitch_check_failed",
                                "detail": "unpublished request has no adjacent edition share", **base}
                    spans[owner][-1] = (spans[owner][-1][0], end)
            shares = [m for t in chosen for m in sorted(_have(spans[t]))]
            if sorted(shares) != sorted(_need(full_request)) or len(shares) != len(set(shares)):
                return {"status": "failed", "tables": [], "reason": "stitch_check_failed",
                        "detail": "shares leave a gap or overlap", **base}
            rows = []
            for t in chosen:
                if not spans[t]:
                    continue
                try:
                    assignment = P.continuous_assignment(spans[t])
                except ValueError:  # the shared contract takes one continuous share per table
                    return {"status": "failed", "tables": [], "reason": "stitch_check_failed",
                            "detail": f"{t}: its share is not one continuous range", **base}
                again = resolve(conditions, views[t], as_of, assignment=assignment,
                                context=contexts[t] or ({"bundle_id": f"tl:{rep}", "anchor_source": "bundle_common",
                                                      "anchor": anchor} if anchor else None))
                if not _full(again) or again.get("frequency") not in frequencies:
                    return {"status": "failed", "tables": [], "reason": "stitch_check_failed",
                            "detail": f"{t} does not cover its share", **base}
                unpublished = _unpublished(again)
                rows.append({"table_id": t, "period": assignment, "frequency": again.get("frequency"),
                             **({"latest_unpublished": unpublished} if unpublished else {}),
                             **({"relation_status": judged[t]["relation_status"]} if t != rep else {})})
            rows.sort(key=lambda r: order.index(r["table_id"]))  # shown newest first, as before; assigned rep first
            return {"status": "selected", "tables": rows, "reason": None, "detail": "stitched",
                    **({"latest_unpublished": unpublished_rows} if unpublished_rows else {}), **base}
        if requested and _need(requested) <= {m for spans in covered.values() for m in _have(spans)}:
            return {"status": "failed", "tables": [], "reason": "stitch_limit_exceeded",
                    "detail": f"more than {MAX_STITCH} tables needed", **base}
    blocked = [t for t in order if t not in allowed and (t in full_any or t in covered_any)]
    if blocked and (full_any or (requested and _need(requested) <= {m for x in covered_any.values()
                                                                    for m in _have(x)})):
        # another edition would cover the request, its judgment does not allow it: never replaced or stitched
        conflict = [t for t in blocked if judged[t]["relation_status"] == "conflict"]
        return {"status": "failed", "tables": [], "reason": "edition_relation_conflict" if conflict
                else "edition_evidence_insufficient",
                "detail": {t: judged[t]["evidence_why"] or [judged[t]["compatibility"]] for t in blocked}, **base}
    if any(_full(results[t], top) for t in candidates if t not in usable):
        return {"status": "failed", "tables": [], "reason": "content_unverified",
                "detail": "only a candidate whose content is not verified covers the request", **base}
    if all(results[t]["status"] == "table_unsuitable" for t in candidates):
        return {"status": "failed", "tables": [], "reason": "frequency_not_offered", "detail": None, **base}
    if calendar and requested and covered and min(a for a, _ in requested) < min(
            (a for spans in covered.values() for a, _ in spans), default=min(a for a, _ in requested)):
        # the reason keeps its name (period_not_covered); which shortfall it is goes in the detail (plan 56 order 4)
        return {"status": "failed", "tables": [], "reason": "period_not_covered", "detail": "past_shortfall", **base}
    return {"status": "failed", "tables": [], "reason": "period_not_covered", "detail": None, **base}


# ---------------------------------------------------------------- condition questions (policy ②④, plan 56 order 5)

PRESETS = json.loads((Path(__file__).resolve().parents[1] / "data" / "period_presets.json").read_text(encoding="utf-8"))
_MONTHS_PER = {"Y": 12, "H": 6, "Q": 3, "M": 1}
_FULL_M = SimpleNamespace(table_id=None, periods=[("M", "190001", "209912")])  # a monthly table over every year


def condition_rule() -> str:
    """The policy's rule for a statistic with no period or no frequency to go on: ``ask_condition`` (policy ②④, plan
    56 order 5: ask, never a default) or ``resolver_default`` (the rule before: the resolver's default window, the
    frequency of the period unit or the longest one; kept for comparison only)."""
    from .resolver import default_policy

    return default_policy().condition_rule


def _calendar(as_of: str) -> dict:
    return {"bundle_id": "tl:fit", "anchor_source": "as_of_calendar", "anchor": as_of[:7]}


def requested_span(condition: dict, as_of: str, *, recent_anchor: str | None = None) -> dict | None:
    """One period condition's monthly range. Recent ranges require their actual catalog reference; without it
    None is returned, never an as_of preview presented as a resolved date. Other expressions retain the calendar
    interpretation. open_end marks a since or recent span rather than a closed endpoint the user named."""
    context = _calendar(as_of)
    if data_recent([condition]):
        if recent_anchor is None and not condition["attrs"].get("end_expr"):
            return None  # a data-relative window has no actual dates until its catalog reference is known
        if recent_anchor is not None:
            context["recent_anchor"] = recent_anchor
    # This is a monthly projection, not a user-stated monthly frequency. The single-frequency
    # card lets the resolver project calendar units without rejecting a quoted quarter as a conflict.
    got = resolve([condition], _FULL_M, as_of, context=context)
    rows = (got.get("periods") or {}).get("requested_months") or []
    if got["status"] != "ok" or len(rows) != 1:
        return None
    attrs = condition.get("attrs") or {}
    return {"start": rows[0]["start"], "end": rows[0]["end"],
            "open_end": bool(attrs.get("open_end")) or (attrs.get("form") == "recent" and not attrs.get("end_expr"))}


def frequency_fit(conditions: list[dict], frequency: str, as_of: str) -> bool:
    """Whether frequency can express the requested window without rounding. Recent-data lengths must contain an
    integral number of its periods; actual coverage is checked later. Calendar ranges must start on its boundary
    and a closed end must finish on one. No period fits every frequency and is asked for separately."""
    step = _MONTHS_PER[frequency]
    for c in conditions:
        if c.get("kind") != "period":
            continue
        if data_recent([c]):
            attrs = c["attrs"]
            width = {"year": 12, "half": 6, "quarter": 3, "month": 1}.get(attrs.get("unit"))
            amount = attrs.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1 or width is None:
                return False
            if amount * width % step:
                return False
            end = attrs.get("end_expr")
            if end:
                span = requested_span(c, as_of)
                if span is None or (P.month(span["end"]) % 12 + 1) % step:
                    return False
            continue
        span = requested_span(c, as_of)
        if span is None:
            return False
        if (P.month(span["start"]) % 12) % step:
            return False
        if not span["open_end"] and (P.month(span["end"]) % 12 + 1) % step:
            return False
    return True


def frequency_order() -> list[str]:
    """The policy's frequencies, longest first."""
    from .resolver import default_policy

    return list(default_policy().frequency_order)


def offered_frequencies(views: list) -> list[str]:
    """The frequencies the tables offer, longest first (the policy's order)."""
    have = {f for view in views for f, _, _ in view.periods}
    return [f for f in frequency_order() if f in have]


def valid_frequencies(conditions: list[dict], offered: list[str], as_of: str) -> list[str]:
    """The offered frequencies that fit the requested periods (``frequency_fit``), in the order given."""
    return [f for f in offered if frequency_fit(conditions, f, as_of)]


def unquoted_conditions(question: str, quoted: list[str]) -> dict[str, list[str]]:
    """The period and frequency words the reader finds in the question outside every quoted period and frequency
    (``quoted``): {"period": [quote], "frequency": [quote]}, each as the reader read it (base-year marks taken out,
    one-year month ranges and decades rewritten as ``read_quotes`` does). The same reader as the quotes; no wording
    of its own."""
    text = question
    for q in sorted({q for q in quoted if q}, key=len, reverse=True):
        text = text.replace(q, " ")
    text, _ = strip_base_year(text)
    text = normalize_quote(text) if text else text
    text, _ = decade_quote(text) if text else (text, [])
    view = normalize_text(text or "")
    return {"period": [c["quote"] for c in find_period_conditions(view)],
            "frequency": [c["quote"] for c in find_frequency_conditions(view)]}


def period_options(as_of: str) -> list[dict]:
    """The preset intents. A recent option has no dates until period_ask resolves its actual catalog/frequency
    reference. Other preset expressions retain their calendar dates. No option is an implicit answer."""
    out = []
    for w in PRESETS["period"]["windows"]:
        got = read_quotes([w["quote"]], [])
        span = requested_span(got["conditions"][0], as_of) if len(got["conditions"]) == 1 else None
        if span is None and data_recent(got["conditions"]):
            out.append({"key": w["key"], "label": w["quote"], "quote": w["quote"], "range": None})
            continue
        if span is None:
            raise ValueError(f"period preset {w['key']} does not read as one period")
        out.append({"key": w["key"], "label": f"{w['quote']} ({span['start']}~{span['end']})", "quote": w["quote"],
                    "range": {"start": span["start"], "end": span["end"]}})
    return out


def latest_quote(views: list, frequency: str) -> str | None:
    """A quote of the latest period ``views`` hold in ``frequency`` (the meaning latest_available, plan 56 order 6-3),
    read by the period reader like any quote; None when none of them holds the frequency."""
    end = _anchor(views, frequency)
    if end is None:
        return None
    year, month = int(end[:4]), int(end[5:7])
    if frequency == "Y":
        return f"{year}년"
    if frequency == "Q":
        return f"{year}년 {(month - 1) // 3 + 1}분기"
    if frequency == "H":
        return f"{year}년 1~6월" if month <= 6 else f"{year}년 7~12월"
    return f"{year}년 {month}월"


def frequency_options(frequencies: list[str]) -> list[dict]:
    """The options of a frequency question: {value, label, quote}; the quote is the word the reader reads as it."""
    labels = PRESETS["frequency"]["labels"]
    return [{"value": f, "label": labels[f], "quote": labels[f]} for f in frequencies]


def item_key(name: str) -> str:
    """A value name compared without spaces and trailing footnote marks ("가계대출 2)" = "가계대출"), by the
    catalog's own name forms (``catalog.text``)."""
    return index_key(strip_footnote(nfkc(name)))


def item_names(view) -> set[str]:
    return {item_key(it.name_raw) for it in view.items if it.name_raw}
