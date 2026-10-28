"""Period and frequency of a statistic, and the final tables among a representative's candidates (plan 37, step 3).

The model only quotes period and frequency words. Code reads them with the current path's condition rules
(``interpret.conditions.find_period_conditions`` / ``find_frequency_conditions``) and resolves them per table with
the shared resolver (``claview_period`` through ``select.periods``). Nothing here copies period rules.

Reading a quote (``read_quotes``; step-3 review, user 2026-10-27 21:36 UTC):
- base-year marks are not periods: "YYYY=100" and a year followed by "기준년"/"기준연도" or "기준" (the current
  path's lexicon reads "2015년 기준" as a base year: ``lexicon.json`` filler.base_word) are taken out, and the removal
  is recorded; a quote left empty by that is recorded as no period;
- a closed month or quarter range that names its year once ("2024년 1~3월", "2024년 1월~3월", "2024년 1~4분기") is
  rewritten to name the year at both ends (x20: 10 sessions dropped on it); the quote, the rewrite and the reading are
  recorded;
- a quote that gives no condition is ``period_unresolved`` / ``frequency_unresolved``, and a quote with a word left
  outside every condition read from it that can change the period (``leftover``: a number, a length written in
  Korean words such as "석 달" or "두 분기", or a period modifier such as "제외", "이전까지", "개월") is ``period_partially_read`` / ``frequency_partially_read`` (for example
  "2023, 2024년" read as 2024 only, "2024년 1월 이후 3개월" read without "3개월"): never a default instead. Other
  leftover words are recorded only (``ignored``). The reading row keeps the quote, the rewrite, the conditions and
  the words left.
- the end word of a frequency read with its end point ("분기말" read as the quarterly frequency with
  ``anchor="end"``) is read with it, so it is not a word left (plan 36); a "말" with no end point read stays a leftover
  word.
Only when no period (or no frequency) is quoted does the resolver's own default apply (``claview_period`` policy:
the longest frequency the table offers, the latest 10 years for Y, 5 for H and Q, 3 for M), and that is recorded.

Final selection (``final_tables``) among the candidates code may switch to; ``usable`` names the ones whose content is
verified by the caller (``check``), the others are only reported. Every candidate is resolved against the same
request: a request that counts from an anchor (no period, recent, since) uses one common anchor, the latest end of
the usable candidates in the chosen frequency. Then:
- one usable table that covers the request: that table (the newest of several that do);
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

from ..catalog.text import index_key, nfkc, strip_footnote
from ..conditions.finders import find_frequency_conditions, find_period_conditions
from ..conditions.q_rules import FREQ_END_AFTER
from ..text.normalize import normalize_text
from . import table_rules as P
from ..search.families import FREQUENCY_WORDS as _FREQUENCY_WORDS


PERIODS_VERSION = "tl-periods-2026-10-29.7"  # .7: the end word of an end-point frequency is read (plan 36)
MAX_STITCH = 3  # tables joined across edition boundaries for one statistic

_MONTH_RANGE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월?\s*[~\-–]\s*(\d{1,2})\s*월")
_QUARTER_RANGE = re.compile(r"(\d{4})\s*년\s*([1-4])\s*(?:분기)?\s*[~\-–]\s*([1-4])\s*분기")
_BASE_YEAR = re.compile(r"\(?\s*\d{4}\s*=\s*100\s*\)?|\d{4}\s*년?\s*(?:기준\s*(?:년도|연도|년)|기준)(?![가-힣])"
                        r"|기준\s*(?:년도|연도|년)\s*\d{4}\s*년?")
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


def normalize_quote(quote: str) -> str:
    """Name the year at both ends of a closed month or quarter range that names it once."""
    text = _MONTH_RANGE.sub(lambda m: f"{m.group(1)}년 {int(m.group(2))}월~{m.group(1)}년 {int(m.group(3))}월", quote)
    return _QUARTER_RANGE.sub(lambda m: f"{m.group(1)}년 {m.group(2)}분기~{m.group(1)}년 {m.group(3)}분기", text)


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


def _frequency_word(word: str) -> bool:
    return word in _FREQUENCY_WORDS or (word.endswith("별") and word[:-1] in _FREQUENCY_WORDS)


def read_quotes(period_quotes: list[str], frequency_quotes: list[str]) -> dict:
    """{conditions, readings, problems}: the conditions read from the quotes (IDs made unique), one reading row per
    quote {kind, quote, base_year_removed, read_as, conditions, uncovered}, and the problems
    (``period_unresolved``, ``period_partially_read``, ``frequency_unresolved``) with their quotes."""
    conditions: list[dict] = []
    readings: list[dict] = []
    problems: list[dict] = []
    for kind, quotes, finder in (("period", period_quotes, find_period_conditions),
                                 ("frequency", frequency_quotes, find_frequency_conditions)):
        for quote in quotes:
            removed: list[str] = []
            text = quote
            if kind == "period":
                text, removed = strip_base_year(quote)
                text = normalize_quote(text) if text else text
            row = {"kind": kind, "quote": quote, "base_year_removed": removed, "read_as": text, "conditions": [],
                   "uncovered": []}
            readings.append(row)
            if not text:
                continue  # only a base-year mark: no period was said
            view = normalize_text(text)
            found = finder(view)
            if not found:
                problems.append({"code": f"{kind}_unresolved", "quote": quote, "read_as": text})
                continue
            read = [_with_end_word(view, c) for c in found] if kind == "frequency" else found
            row["uncovered"], row["ignored"] = leftover(view, read, kind)
            if row["uncovered"]:
                problems.append({"code": f"{kind}_partially_read", "quote": quote, "read_as": text,
                                 "uncovered": row["uncovered"]})
                continue
            for c in found:
                c = copy.deepcopy(c)
                c["id"] = f"{c['id'][0]}{len(conditions) + 1}"
                conditions.append(c)
                row["conditions"].append(c["id"])
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


def _cover_set(requested: list[tuple[int, int]], covered: dict[str, list[tuple[int, int]]]) -> list[str] | None:
    """Fewest tables (at most MAX_STITCH) whose covered months together hold every requested month."""
    need = _need(requested)
    tables = [t for t in covered if covered[t]]
    for size in range(1, MAX_STITCH + 1):
        for combo in combinations(tables, size):
            if need <= {m for t in combo for m in _have(covered[t])}:
                return list(combo)
    return None


def _assign(requested: list[tuple[int, int]], chosen: list[str], covered: dict) -> dict[str, list[tuple[int, int]]]:
    """Each requested month to the first chosen table (newest first) that covers it."""
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


def _full(result: dict) -> bool:
    return result["status"] == "ok" and not (result.get("periods") or {}).get("missing")


def final_tables(rep: str, candidates: list[str], usable: dict[str, str], views: dict, conditions: list[dict],
                 as_of: str) -> dict:
    """{status, tables: [{table_id, period, frequency}], results, reason, detail, anchor}."""
    results = {t: resolve(conditions, views[t], as_of) for t in candidates}
    anchor = None
    request = {"conditions": conditions}
    if P.anchored_request(request):
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
    full = [t for t in order if t in usable and _full(results[t])]
    if full:
        t = full[0]
        return {"status": "selected", "tables": [{"table_id": t, "period": results[t]["periods"].get("requested_months"),
                                                  "frequency": results[t].get("frequency")}],
                "reason": None, "detail": None, **base}
    requested = next((_months((r.get("periods") or {}).get("requested_months")) for r in results.values()
                      if (r.get("periods") or {}).get("requested_months")), [])
    frequencies = {results[t].get("frequency") for t in order if t in usable and results[t].get("frequency")}
    covered = {t: _months((results[t].get("periods") or {}).get("covered_months")) for t in order
               if t in usable and results[t]["status"] in ("ok", "no_data_in_range")}
    if requested and len(frequencies) == 1:
        chosen = _cover_set(requested, covered)
        if chosen:
            spans = _assign(requested, chosen, covered)
            shares = [m for t in chosen for m in sorted(_have(spans[t]))]
            if sorted(shares) != sorted(_need(requested)) or len(shares) != len(set(shares)):
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
                                context=({"bundle_id": f"tl:{rep}", "anchor_source": "bundle_common",
                                          "anchor": anchor} if anchor else None))
                if not _full(again) or again.get("frequency") not in frequencies:
                    return {"status": "failed", "tables": [], "reason": "stitch_check_failed",
                            "detail": f"{t} does not cover its share", **base}
                rows.append({"table_id": t, "period": assignment, "frequency": again.get("frequency")})
            return {"status": "selected", "tables": rows, "reason": None, "detail": "stitched", **base}
        if requested and _need(requested) <= {m for spans in covered.values() for m in _have(spans)}:
            return {"status": "failed", "tables": [], "reason": "stitch_limit_exceeded",
                    "detail": f"more than {MAX_STITCH} tables needed", **base}
    if any(_full(results[t]) for t in candidates if t not in usable):
        return {"status": "failed", "tables": [], "reason": "content_unverified",
                "detail": "only a candidate whose content is not verified covers the request", **base}
    if all(results[t]["status"] == "table_unsuitable" for t in candidates):
        return {"status": "failed", "tables": [], "reason": "frequency_not_offered", "detail": None, **base}
    return {"status": "failed", "tables": [], "reason": "period_not_covered", "detail": None, **base}


def item_key(name: str) -> str:
    """A value name compared without spaces and trailing footnote marks ("가계대출 2)" = "가계대출"), by the
    catalog's own name forms (``catalog.text``)."""
    return index_key(strip_footnote(nfkc(name)))


def item_names(view) -> set[str]:
    return {item_key(it.name_raw) for it in view.items if it.name_raw}
