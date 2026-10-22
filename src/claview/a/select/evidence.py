"""check_evidence (detail 3.4, plan 04 4-1): evidence of each requirement x candidate table, in the 1-stretch shape.

Order of the checks (3.4): confirmed answer -> basis axis -> frequency and period -> region -> indicator evidence;
``matched_items`` (classification values matched by the question words) comes first because the basis and exclude
checks read it. Each check returns ``Check`` rows (contract.shapes): the old judgement status stays in
``source_status``, the new decision is ``verdict``. ``confirmed`` (no confirmed answers yet) and ``measure_kind``
(the interpretation leaves ``measure_kind`` None) are not emitted.

Rules are ported from the old A pipeline (commit 55fd41b), structure kept, word lists in ``data/evidence_words.json``:
- matched_items: src/claview/aflow/j_nodes.py ``_match_method``, ``_j2_terms``, ``j2`` (:46-157)
- period, frequency: j_nodes.py ``_envelope``, ``j3`` (:160-243) with rules.py ``compare_period`` (:586-611)
- basis, unit, exclude: j_nodes.py ``_byeol_linked``, ``_fit_basis``, ``_fit_currency_item``, ``_fit_handoff_only``,
  ``_fit_unit``, ``j4`` (:250-526) with rules.py ``basis_forms``, ``unit_currency``, ``compare_unit`` (:641-779)
- cross_axis: j_nodes.py ``cross_axis_support`` (:338-384)
- indicator: src/claview/aflow/measure.py ``rule_phrases``, ``assess`` (:78-185)
New here: ``region`` (the requirement's target values among the table's values or name), the verdicts, the period
resolution from the written period attributes, and the per-requirement scope below.

Scope. Period, frequency, cross_axis, region and indicator are judged per requirement with its own and shared
conditions. The per-condition checks (basis, unit, exclude) are made for every condition of the question so the
record shows how each table stands on each condition; only the requirement's own and shared conditions decide
(``verdict`` pass/fail), the others and every ambiguous one get verdict n/a. ``matched_items`` holds the value
matches of every condition and of the requirement's own words.

Verdicts: positive evidence -> pass, evidence against -> fail, no evidence either way (unknown) -> n/a. The verdicts
are recorded; which of them filter is the record-mode decision (plan 04, 4-2; ``data/evidence_filters.json``).
``prior`` is the list of checks already made for the same requirement x table (basis and exclude read the
matched_items check, cross_axis the basis checks). Each check function returns ``(checks, evidence_rows)``;
``check_evidence`` merges them into one ``EvidenceDoc``.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..catalog.relations import relations_of
from ..catalog.text import index_key, strip_parens
from ..contract.shapes import Analysis, Check, EvidenceDoc, EvidenceRow
from ..interpret.condition_rules import BASIS_AXES
from ..reused import q_rules
from .tables import WORDS, TableView, measure_norm, norm_v1, nospace, selection_catalog

if TYPE_CHECKING:
    from ..catalog.layer import CatalogLayer


# What one check function returns: its checks and the evidence rows they point at (merged into EvidenceDoc).
CheckResult = tuple[list[Check], dict[str, EvidenceRow]]

TITLE_ONLY_AXES = frozenset(WORDS["title_only_axes"])
HANDOFF_ONLY_AXES = frozenset(WORDS["handoff_only_axes"])
_TAIL_RE = WORDS["currency_tail"]
CURRENCY_ITEM = {v: re.compile(p.replace("{tail}", _TAIL_RE)) for v, p in WORDS["currency_item"].items()}
FOREIGN_ITEM = re.compile(WORDS["foreign_item"].replace("{tail}", _TAIL_RE))
DEFAULT_CURRENCY = WORDS["default_currency"]
_MONEY_UNIT = re.compile(WORDS["money_unit"])
UNIT_CURRENCY = WORDS["unit_currency"]
_UNIT_SCALE = WORDS["unit_scale"]
_UNIT_PARTS = re.compile(WORDS["unit_parts"])
FREQ_MONTHS = WORDS["freq_months"]
LAG_PERIODS = WORDS["lag_periods"]
STOP_TERMS = frozenset(WORDS["stop_terms"])
_STOP_KEYS = frozenset(measure_norm(w) for w in STOP_TERMS)
_TAIL = re.compile(WORDS["token_tail"])
_TRIM = re.compile(WORDS["token_trim"])
_MARKS = re.compile(WORDS["token_marks"])
BASIS_SUFFIX = WORDS["basis_suffix"]
_BYEOL_MARK = re.compile(WORDS["byeol_mark"])
_BYEOL_JOIN = re.compile(WORDS["byeol_join"])
_BYEOL_JOIN_INNER = re.compile(WORDS["byeol_join_inner"])
_BYEOL_NAME = re.compile(WORDS["byeol_byname"])
_SEPS = WORDS["list_separators"]
_COMBINED_VALUE = re.compile(WORDS["combined_value"])
_WORD = re.compile(r"[가-힣A-Za-z0-9]+")
TOKEN_TAIL, WORD_RE = _TAIL, _WORD  # shared with the query builder (candidates.py)
_PERIOD_BEST = ("covered", "unknown", "partial", "outside")
ROLE = {"select": "splits_table", "handoff": "in_table"}
STANCE = {"match": "support", "conflict": "against"}


@dataclass(frozen=True)
class EvidenceInput:
    analysis: Analysis
    layer: CatalogLayer | None
    as_of: str | None = None  # reference date for relative periods; only the period check resolves dates


# ---------------------------------------------------------------- small helpers


def _view(inp: EvidenceInput, table_id: str) -> TableView | None:
    if inp.layer is None:
        return None
    return selection_catalog(inp.layer).views.get(table_id)


def requirement_of(analysis: dict, rid: str) -> dict:
    for r in (analysis.get("interpretation") or {}).get("requirements") or []:
        if r.get("id") == rid:
            return r
    return {"id": rid, "condition_ids": []}


def scope(analysis: dict, rid: str) -> tuple[set[str], set[str]]:
    """(counted, ambiguous) condition IDs of a requirement: own and shared minus ambiguous; ambiguous."""
    req = requirement_of(analysis, rid)
    amb = set(req.get("ambiguous_condition_ids") or [])
    counted = (set(req.get("condition_ids") or []) | set(req.get("shared_condition_ids") or [])) - amb
    return counted, amb


def conditions_of(analysis: dict, *kinds: str) -> list[dict]:
    return [c for c in analysis.get("conditions") or [] if c.get("kind") in kinds]


def _attrs(c: dict) -> dict:
    return c.get("attrs") or {}


def _verdict(cid: str, counted: set[str], status: str, good=("match",), bad=("conflict",)) -> str:
    if cid not in counted:
        return "n/a"
    if status in good:
        return "pass"
    if status in bad:
        return "fail"
    return "n/a"


def _check(name: str, state: str, source_status, verdict: str, evidence_ids: list[str], detail: dict) -> Check:
    if state != "computed":
        verdict = "n/a"
    return {"name": name, "state": state, "source_status": source_status, "verdict": verdict,
            "evidence_ids": evidence_ids, "detail": detail}


def _row(kind: str, stance: str, table_id: str, text: str) -> EvidenceRow:
    return {"kind": kind, "stance": stance, "table_id": table_id, "text": text}


def _state(view: TableView | None) -> str:
    return "computed" if view is not None and view.has_meta else "missing_meta"


def _has_any(text: str, forms) -> bool:
    return any(nospace(f) in text for f in forms)


# Source: src/claview/aflow/rules.py:641-642 (commit 55fd41b).
def basis_forms(axis: str, value: str) -> tuple[str, ...]:
    return tuple(BASIS_AXES.get(axis, {}).get(value, (value,)))


def _others(axis: str, value: str) -> list[str]:
    return [f for v, fs in BASIS_AXES.get(axis, {}).items() if v != value for f in fs]


def axis_of(c: dict) -> str:
    return _attrs(c).get("axis") or ""


def value_of(c: dict) -> str:
    return _attrs(c).get("value") or c.get("value") or ""


def polarity_of(c: dict) -> str:
    return _attrs(c).get("polarity") or "+"


def is_byeol(c: dict) -> bool:
    """A classification "X별" condition (not a dictionary classification value), old ``_is_byeol``."""
    return axis_of(c) == "classification" and value_of(c) not in BASIS_AXES.get("classification", {})


def breakdown_axes(c: dict, view: TableView) -> set[str]:
    """Existing X-byeol stem matching, shared with requirement-local ranking."""
    stem = value_of(c)[:-1]
    return {obj for obj, name in view.dims if stem in nospace(name)} | {
        it.obj_id for it in view.items if it.name_nospace == stem or it.name_nospace.startswith(stem)}


# ---------------------------------------------------------------- question text positions


def _text(analysis: dict) -> dict | None:
    return analysis.get("text")


def _norm_range(text: dict, span: list[int] | None) -> tuple[int, int] | None:
    """Raw span -> [i, j) of the normalised question."""
    if not span or text is None:
        return None
    idx = [k for k, o in enumerate(text["offsets"]) if span[0] <= o < span[1]]
    return (idx[0], idx[-1] + 1) if idx else None


def _raw_span(text: dict, i: int, j: int) -> list[int] | None:
    offsets = text["offsets"]
    if not offsets or i >= j or j > len(offsets):
        return None
    return [offsets[i], offsets[j - 1] + 1]


def _taken(analysis: dict, text: dict) -> list[tuple[int, int]]:
    """Normalised positions of every condition (old J2 leaves the condition words out of the question words)."""
    out = []
    for c in analysis.get("conditions") or []:
        r = _norm_range(text, c.get("span"))
        if r:
            out.append(r)
    return out


# ---------------------------------------------------------------- matched_items (old J2)


def _match_method(it, term: str) -> str | None:
    """Source: src/claview/aflow/j_nodes.py:46-53 (commit 55fd41b)."""
    if it.name_raw == term:
        return "exact"
    if it.name_nospace == nospace(term):
        return "nospace"
    if it.name_norm and it.name_norm == norm_v1(term):
        return "normalized"
    return None


def items_for(view: TableView, text: str) -> list:
    """Values of the table named ``text`` (raw, nospace or search-normalised name), in table order."""
    found = {it.index: it for it in view.by_raw.get(text, [])}
    for it in view.by_nospace.get(nospace(text), []):
        found.setdefault(it.index, it)
    n = norm_v1(text)
    for it in view.by_norm.get(n, []) if n else []:
        found.setdefault(it.index, it)
    return [found[k] for k in sorted(found)]


def _word_groups(analysis: dict, rid: str) -> list[str]:
    """Question words outside every condition, joined in runs of 1-3 neighbours (old ``_j2_terms``), limited to the
    requirement's own words when its span is known. Words are cut at spaces, commas, middle dots, slashes and
    brackets ("기업규모별(대기업, 중견기업)로" gives 대기업, 중견기업)."""
    text = _text(analysis)
    if text is None:
        return []
    query = text["norm"]
    taken = _taken(analysis, text)
    bound = _norm_range(text, requirement_of(analysis, rid).get("span"))
    from . import model_refs
    ranges = model_refs.spans(analysis, rid)
    bounds = [_norm_range(text, r) for r in ranges] if ranges is not None else None
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[^\s,·/()\[\]]+", query)]
    toks = [t for t in toks if all(t[2] <= a or t[1] >= b for a, b in taken)
            and (any(b and b[0] <= t[1] and t[2] <= b[1] for b in bounds) if bounds is not None else
                 (bound is None or (bound[0] <= t[1] and t[2] <= bound[1])))]
    out = []
    for n in (3, 2, 1):
        for k in range(len(toks) - n + 1):
            group = toks[k:k + n]
            if any(query[a[2]:b[1]].strip() for a, b in zip(group, group[1:])):
                continue  # neighbours only
            i, j = group[0][1], group[-1][2]
            word = _MARKS.sub("", _TAIL.sub("", _TRIM.sub("", query[i:j])))
            if len(nospace(word)) < 2 or word in STOP_TERMS:
                continue
            out.append(word)
    return out


def _requirement_names(analysis: dict, rid: str) -> list[str]:
    """Catalog names the interpretation gave the requirement: indicator with an exact or dictionary path, targets."""
    req = requirement_of(analysis, rid)
    out = []
    ind = req.get("indicator") or {}
    if ind.get("name") and ind.get("path") in ("exact", "dictionary"):
        out.append(ind["name"])
    out += [t["name"] for t in req.get("targets") or [] if t.get("name")]
    return out


def matched_terms(analysis: dict, rid: str) -> list[dict]:
    """What matched_items looks for: conditions first (exclude -> breakdown -> basis words), then the question
    words and the requirement's catalog names. {texts, condition_id, kind, polarity, fallback}."""
    terms = []
    for c in conditions_of(analysis, "exclude"):
        hints = _attrs(c).get("complement_hints") or []
        terms.append({"texts": list(hints), "condition_id": c["id"], "kind": "complement",
                      "fallback": _attrs(c).get("text") or c.get("value") or "", "polarity": "+"})
    for c in conditions_of(analysis, "breakdown"):
        terms.append({"texts": [_attrs(c).get("text") or c.get("value") or ""], "condition_id": c["id"],
                      "kind": "term", "polarity": "+"})
    for c in conditions_of(analysis, "basis", "calc"):
        texts = [f for form in basis_forms(axis_of(c), value_of(c)) for f in (form, form + BASIS_SUFFIX)]
        terms.append({"texts": texts, "condition_id": c["id"], "kind": "term", "polarity": polarity_of(c)})
    for word in [*_word_groups(analysis, rid), *_requirement_names(analysis, rid)]:
        terms.append({"texts": [word], "condition_id": None, "kind": "term", "polarity": "+"})
    from . import model_refs
    if model_refs.adopted(analysis):
        counted, ambiguous = scope(analysis, rid)
        terms = [t for t in terms if t["condition_id"] is None or t["condition_id"] in counted | ambiguous]
    return terms


def check_matched_items(inp: EvidenceInput, requirement_id: str, table_id: str,
                        prior: list[Check] | None = None) -> CheckResult:
    """Classification values of the table matched by the question words and conditions (old J2). Same words only
    (exact, nospace, normalized); an exclusion looks for its complement value first. A record (verdict n/a)."""
    view = _view(inp, table_id)
    if view is None:
        return [_check("matched_items", "missing_meta", None, "n/a", [], {"rows": []})], {}
    rows, evidence, seen = [], {}, set()

    def add(it, term, t, method, polarity):
        key = (it.obj_id, it.item_id, term, t["condition_id"])
        if key in seen:
            return
        seen.add(key)
        path = view.ancestors(it)
        rows.append({"condition_id": t["condition_id"], "obj_id": it.obj_id, "item_id": it.item_id,
                     "name": it.name_raw, "term": term, "parent_path": path, "depth": len(path),
                     "match_method": method, "polarity": polarity})
        eid = f"M:{requirement_id}:{table_id}:{len(rows)}"
        note = "(제외 조건의 보완)" if method == "complement" else "(뺄 값)" if polarity == "-" else "일치"
        evidence[eid] = _row("itm", "against" if polarity == "-" else "support", table_id,
                             f"분류값 '{it.name_raw}' {note}" + (f" (상위: {path[0]['name']})" if path else ""))

    for t in matched_terms(inp.analysis, requirement_id):
        hit = False
        for text in t["texts"]:
            if not text:
                continue
            for it in items_for(view, text):
                method = _match_method(it, text)
                if method:
                    hit = True
                    add(it, text, t, "complement" if t["kind"] == "complement" else method, t["polarity"])
        if not hit and t["kind"] == "complement" and t["fallback"]:
            for it in items_for(view, t["fallback"]):
                method = _match_method(it, t["fallback"])
                if method:
                    add(it, t["fallback"], t, method, "-")
    return [_check("matched_items", _state(view), None, "n/a", list(evidence), {"rows": rows})], evidence


# ---------------------------------------------------------------- period and frequency (old J3)


def _expr_year(expr: dict | None, as_of: str | None) -> str | None:
    if not expr:
        return None
    if expr.get("year"):
        return expr["year"]
    alias = expr.get("alias")
    if alias and as_of:
        if alias in q_rules._ALIAS_YEARS:
            return str(int(as_of[:4]) + q_rules._ALIAS_YEARS[alias])
        if alias.startswith("올"):  # "올 들어"
            return as_of[:4]
    return None


def _expr_unit(expr: dict | None) -> tuple[str, str | None] | None:
    if not expr or not expr.get("sub_unit"):
        return ("Y", None)
    unit, value = expr["sub_unit"], expr.get("sub_value")
    if value is None:
        return None  # a month/quarter relative ("지난달") needs the reference month
    if unit == "Q":
        return ("Q", value) if value.isdigit() and 1 <= int(value) <= 4 else None
    if unit == "M":
        return ("M", value) if value.isdigit() and 1 <= int(value) <= 12 else None
    return ("H", value)


def resolve_period(attrs: dict, as_of: str | None) -> dict:
    """{status explicit|unresolved, start, end, form} of one period condition. The interpretation keeps the period
    as written; it is resolved here with the old Q2 date rules (q_rules ``_bounds``, ``_shift_year``)."""
    form = attrs.get("form")
    out = {"status": "unresolved", "start": None, "end": None, "form": form}
    if attrs.get("extract_status") == "unresolved" or attrs.get("invalid"):
        return out
    if form == "recent":
        if as_of and attrs.get("unit") == "year" and attrs.get("amount"):
            out.update(status="explicit", start=q_rules._shift_year(as_of, attrs["amount"]), end=as_of[:4])
        return out
    start_expr = attrs.get("start_expr")
    y1, u1 = _expr_year(start_expr, as_of), _expr_unit(start_expr)
    if not y1 or u1 is None:
        return out
    if form == "range":
        end_expr = attrs.get("end_expr")
        y2 = _expr_year(end_expr, as_of) or (y1 if end_expr and not end_expr.get("alias") else None)
        u2 = _expr_unit(end_expr)
        if not y2 or u2 is None:
            return out
        start, end = q_rules._bounds(y1, u1)[0], q_rules._bounds(y2, u2)[1]
        if q_rules.stamp_months(start)[0] > q_rules.stamp_months(end)[1]:
            return out
        out.update(status="explicit", start=start, end=end)
    elif form == "since":
        out.update(status="explicit", start=q_rules._bounds(y1, u1)[0], end=None)
    elif (start_expr or {}).get("end_mark") and u1 == ("Y", None):
        out.update(status="explicit", start=f"{y1}-12", end=f"{y1}-12")
    else:
        start, end = q_rules._bounds(y1, u1)
        out.update(status="explicit", start=start, end=end)
    return out


def compare_period(req: dict, cov_start: str | None, cov_end: str | None, freq: str) -> tuple[str, str | None]:
    """Source: src/claview/aflow/rules.py:586-611 (commit 55fd41b), unchanged."""
    if req["status"] != "explicit" or req["start"] is None:
        return "unknown", "period_unresolved"
    a, b = q_rules.stamp_months(cov_start), q_rules.stamp_months(cov_end)
    rs = q_rules.stamp_months(req["start"])
    re_ = q_rules.stamp_months(req["end"]) if req["end"] else None
    if not (a and b and rs) or (req["end"] and not re_):
        return "unknown", "stamp_unparsed"
    lo, hi = a[0], b[1]
    want_lo, want_hi = rs[0], (re_[1] if re_ else None)
    lag = FREQ_MONTHS[freq] * LAG_PERIODS
    if want_hi is None:
        if want_lo > hi:
            return ("unknown", "snapshot_lag") if want_lo - hi <= lag else ("outside", "ended_before_request")
        return ("covered", "open_end") if want_lo >= lo else ("partial", "starts_after_request")
    if want_hi < lo:
        return "outside", "starts_after_request"
    if want_lo > hi:
        return ("unknown", "snapshot_lag") if want_lo - hi <= lag else ("outside", "ended_before_request")
    if want_lo >= lo and want_hi <= hi:
        return "covered", None
    if want_hi > hi and want_lo >= lo and want_hi - hi <= lag:
        return "unknown", "snapshot_lag"
    return "partial", "starts_after_request" if want_lo < lo else "ended_before_request"


def envelope(periods: list[dict]) -> dict | None:
    """The requested period of a requirement: one period as it is, several bound first..last (old ``_envelope``)."""
    if not periods:
        return None
    if len(periods) == 1:
        return dict(periods[0])
    if any(p["status"] != "explicit" for p in periods):
        return {"status": "unresolved", "start": None, "end": None, "form": "range"}
    spans = [(q_rules.stamp_months(p["start"]), p) for p in periods]
    first = min(spans, key=lambda x: x[0][0])[1]["start"]
    if any(p["end"] is None and p["form"] == "since" for _, p in spans):
        return {"status": "explicit", "start": first, "end": None, "form": "since"}
    ends = [(q_rules.stamp_months(p["end"] or p["start"]), p["end"] or p["start"]) for _, p in spans]
    return {"status": "explicit", "start": first, "end": max(ends, key=lambda x: x[0][1])[1], "form": "range"}


def requested(analysis: dict, rid: str, as_of: str | None) -> tuple[list[dict], list[str], dict | None]:
    """(period conditions, requested frequencies, resolved envelope) of a requirement (ambiguous ones left out)."""
    counted, _ = scope(analysis, rid)
    periods = [c for c in conditions_of(analysis, "period") if c["id"] in counted]
    freqs = list(dict.fromkeys(c.get("value") or _attrs(c).get("value") for c in conditions_of(analysis, "frequency")
                               if c["id"] in counted))
    resolved = [resolve_period(_attrs(c), as_of) for c in periods]
    return periods, [f for f in freqs if f], envelope(resolved)


def check_period(inp: EvidenceInput, requirement_id: str, table_id: str,
                 prior: list[Check] | None = None) -> CheckResult:
    """Requested period against the table coverage of every frequency (old J3 period). covered and partial pass,
    outside fails, unknown (snapshot lag, unresolved period) is n/a; joining year ranges is cover_cells' work."""
    view = _view(inp, table_id)
    periods, wanted, env = requested(inp.analysis, requirement_id, inp.as_of)
    request = None
    if periods:
        request = {"condition_ids": [c["id"] for c in periods], "form": env["form"], "start": env["start"],
                   "end": env["end"]}
    evidence, rows = {}, []
    available = view.periods if view else []
    for f, start, end in available:
        if not periods:
            status, reason = "covered", "not_requested"
        else:
            status, reason = compare_period(env, start, end, f)
        judged = not wanted or f in wanted
        rows.append({"frequency": f, "requested": judged, "status": status, "coverage": [start, end],
                     "reason": reason})
        if periods and judged:
            eid = f"P:{requirement_id}:{table_id}:{f}"
            evidence[eid] = _row("prd", {"covered": "support", "unknown": "unknown"}.get(status, "against"),
                                 table_id, f"{f} {start}~{end}")
    judged_rows = [r for r in rows if r["requested"]]
    ended = bool(periods) and bool(judged_rows) and all(
        r["reason"] == "ended_before_request" and r["status"] == "outside" for r in judged_rows)
    source_status, verdict = None, "n/a"
    if periods and judged_rows:
        source_status = min((r["status"] for r in judged_rows), key=_PERIOD_BEST.index)
        verdict = {"covered": "pass", "partial": "pass", "outside": "fail"}.get(source_status, "n/a")
    state = _state(view) if available else "missing_meta"
    detail = {"request": request, "by_frequency": rows, "coverage_status": "ended_before_request" if ended else "n/a"}
    return [_check("period", state, source_status, verdict, list(evidence), detail)], evidence


def check_frequency(inp: EvidenceInput, requirement_id: str, table_id: str,
                    prior: list[Check] | None = None) -> CheckResult:
    """Requested frequencies against the table frequencies (old J3 frequency)."""
    view = _view(inp, table_id)
    _, wanted, _ = requested(inp.analysis, requirement_id, inp.as_of)
    available = view.frequencies() if view else []
    if not available:
        status = "unknown"
    elif not wanted:
        status = "match"  # nothing asked, nothing to miss
    else:
        status = "match" if set(wanted) & set(available) else "conflict"
    evidence = {}
    if wanted or not available:
        eid = f"F:{requirement_id}:{table_id}"
        evidence[eid] = _row("prd", {"match": "support", "conflict": "against"}.get(status, "unknown"), table_id,
                             f"요청 주기 {'·'.join(wanted) or '없음'}, 수록 주기 {'·'.join(available) or '없음'}")
    verdict = "n/a" if not wanted else {"match": "pass", "conflict": "fail"}.get(status, "n/a")
    detail = {"requested": wanted, "available": available, "status": status}
    return [_check("frequency", _state(view), status, verdict, list(evidence), detail)], evidence


# ---------------------------------------------------------------- basis, unit, exclude (old J4)


def _prior_rows(prior: list | None, name: str = "matched_items") -> list[dict]:
    for c in prior or []:
        if c.get("name") == name:
            return list((c.get("detail") or {}).get("rows") or [])
    return []



def linked_members(analysis: dict, c: dict, matches: list[dict]) -> list[str]:
    """Values linked by grammar to an "X별" condition and matched to one axis of the table (old ``_byeol_linked``
    and ``_linked_members``, j_nodes.py:254-335). Two forms only: "A, B(, C) 등 X별" and "X별(A, B)"; a list needs
    two items joined by a comma, middle dot, slash or 와/과/및/이나/또는. Not counted: words of other conditions,
    the excluded value, complement values, negated matches, the same word twice; matches of list conditions
    (breakdown, subkind list) are the list itself and count."""
    text = _text(analysis)
    span = c.get("span")
    if text is None or not span:
        return []
    query, offsets = text["norm"], text["offsets"]
    pos = {o: i for i, o in enumerate(offsets)}
    start = pos.get(span[0])
    if start is None:
        return []
    items = []
    head = query[:start]
    mark = _BYEOL_MARK.search(head)
    if mark:
        toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(rf"[^\s{_SEPS}()]+", head[:mark.start()])]
        chain: list[tuple[str, int, int]] = []
        for k in range(len(toks) - 1, -1, -1):
            word, i, j = toks[k]
            if chain:
                gap = head[j:chain[-1][1]]
                joined = re.search(rf"[{_SEPS}]", gap) or _BYEOL_JOIN.search(word)
                if not joined:
                    break
            if chain and _BYEOL_JOIN.search(word):
                j -= len(_BYEOL_JOIN.search(word).group(0))
            chain.append((word, i, j))
        items = [(i, j) for _, i, j in chain] if len(chain) >= 2 else []
    else:
        value_end = start + len(value_of(c))
        paren = re.match(r"\s*\(([^)]*)\)", query[value_end:])
        if paren:  # inside the bracket too, only real separators split (a space alone does not)
            base = value_end + paren.start(1)
            parts = []
            for seg in re.finditer(rf"[^{_SEPS}]+", paren.group(1)):
                cut = seg.start()
                for jm in _BYEOL_JOIN_INNER.finditer(seg.group(0)):
                    parts.append((cut, seg.start() + jm.start()))
                    cut = seg.start() + jm.end()
                parts.append((cut, seg.end()))
            parts = [(i + len(t) - len(t.lstrip()), j - (len(t) - len(t.rstrip())))
                     for i, j in parts for t in [paren.group(1)[i:j]] if t.strip()]
            items = [(i + base, j + base) for i, j in parts] if len(parts) >= 2 else []
    items = sorted(items)
    tokens = [(k, nospace(t.group(0)), _raw_span(text, i + t.start(), i + t.end()))
              for k, (i, j) in enumerate(items) for t in re.finditer(r"\S+", query[i:j])]
    lists = {o["id"] for o in conditions_of(analysis, "breakdown") if _attrs(o).get("subkind") == "list"}
    others = [o["span"] for o in analysis.get("conditions") or []
              if o.get("kind") != "breakdown" and o.get("span") and o["id"] != c["id"]]
    excluded = {nospace(_attrs(o).get("text") or o.get("quote") or o.get("value") or "")
                for o in conditions_of(analysis, "exclude")} - {""}
    by_obj: dict[str, dict] = {}
    for m in matches:
        if (m.get("polarity") != "+" or m.get("obj_id") == "ITEM" or m.get("match_method") == "complement"
                or (m.get("condition_id") is not None and m["condition_id"] not in lists)):
            continue
        word = nospace(m.get("term") or "")
        # the row stands for a whole written word of the list (not a part of one), at its first place in the list
        place = next(((k, ts) for k, tok, ts in tokens if tok == word), None)
        if not word or place is None or word in excluded:
            continue
        item, ts = place
        if not ts or any(a <= ts[0] and ts[1] <= b for a, b in others):
            continue
        got = by_obj.setdefault(m["obj_id"], {})
        if item not in got and word not in {w for w, _ in got.values()}:
            got[item] = (word, m["name"])
    best = max(by_obj.values(), key=len, default={})
    return [name for _, name in best.values()] if len(best) >= 2 else []


def _unit_currency(view: TableView) -> tuple[str | None, str]:
    """(currency, unit text) from money units (rules.py:733-747): the table unit first, else value units of one
    currency; mixed or no money unit -> (None, "")."""
    def money(units):
        found = {}
        for u in units:
            m = _MONEY_UNIT.fullmatch(re.sub(r"\s+", "", u or ""))
            if m:
                found.setdefault(UNIT_CURRENCY[m.group(1)], u)
        return found
    found = money([*view.table_units[:1], *(u for _, u in view.itm_rows[:1])]) or money([it.unit for it in view.items])
    if len(found) != 1:
        return None, ""
    (cur, unit), = found.items()
    return cur, unit


def _fit_currency_item(value: str, view: TableView) -> tuple[str, str, str, str]:
    """Source: src/claview/aflow/j_nodes.py:427-444 (commit 55fd41b)."""
    raw = [it.name_raw for it in view.items]
    hits = [n for n in raw if CURRENCY_ITEM[value].search(n)]
    if hits:
        return "match", "handoff", "item", f"분류값 '{hits[0]}'"
    if value in UNIT_CURRENCY.values() and any(FOREIGN_ITEM.search(n) for n in raw):
        return "unknown", "handoff", "item", "분류값에 외화만 있음(통화별 값은 B가 확인)"
    if any(p.search(n) for v, p in CURRENCY_ITEM.items() if v != value for n in raw):
        return "conflict", "select", "item", "분류값에 반대 기준만 있음"
    cur, unit = _unit_currency(view)
    if cur == value and value != DEFAULT_CURRENCY:
        return "match", "select", "unit", f"단위 '{unit}'"
    if cur and cur != value and value in UNIT_CURRENCY.values():
        return "unknown", "select", "unit", f"단위 '{unit}'(다른 통화)"
    return "unknown", "select", "none", ""


def fit_basis(c: dict, view: TableView, members: list | None = None) -> tuple[str, str, str, str]:
    """One basis word -> (status, role, source, text): title > path > values (old ``_fit_basis``,
    j_nodes.py:387-422). ``members``: values linked to an "X별" condition."""
    axis, value = axis_of(c), value_of(c)
    title, paths = view.title_ns, view.paths_ns
    item_names = view.item_names_ns
    if is_byeol(c):
        stem = value[:-1]
        if value in title:
            return "match", "select", "title", f"표명에 '{value}'"
        if breakdown_axes(c, view):
            return "match", "handoff", "item", f"분류축·분류값에 '{stem}'"
        if _BYEOL_NAME.search(re.sub(r"^[^(]*", "", title)) and members:
            return "match", "handoff", "item", f"분류값 '{'·'.join(members[:4])}'"
        return "unknown", "select", "none", ""
    from .basis import values as basis_values

    title_values = basis_values(title, axis, value)
    if value in title_values:
        return "match", "select", "title", f"표명에 '{value}'"
    if title_values - {value}:
        return "conflict", "select", "title", f"표명이 반대 기준({view.title})"
    if value in basis_values(paths, axis, value):
        return "match", "select", "path", f"경로에 '{value}'"
    if axis in TITLE_ONLY_AXES:
        return "unknown", "select", "none", ""
    if axis == "currency" and value in CURRENCY_ITEM:
        return _fit_currency_item(value, view)
    in_items = [n for n in item_names if value in basis_values(n, axis, value)]
    if in_items:
        return "match", "handoff", "item", f"분류값 '{in_items[0]}'"
    if any(basis_values(n, axis, value) - {value} for n in item_names):
        return "conflict", "select", "item", "분류값에 반대 기준만 있음"
    return "unknown", "select", "none", ""


def _fit_handoff_only(c: dict, view: TableView) -> tuple[str, str, str, str] | None:
    """Comparison bases and calculation requests: a value naming it is the evidence, else no judgement (None).
    A Hangul letter right after the word makes another word ("전기비데"). Source: j_nodes.py:447-452."""
    pats = [re.escape(nospace(f)) + r"(?![가-힣])" for f in basis_forms(axis_of(c), value_of(c))]
    hits = [n for n in view.item_names_ns if any(re.search(p, n) for p in pats)]
    return ("match", "handoff", "item", f"분류값 '{hits[0]}'") if hits else None


def basis_status(c: dict, view: TableView, analysis: dict, matches: list[dict]) -> tuple[str, str, str, str] | None:
    """Status of one basis or calc condition on one table (old J4, polarity included); None = not judged."""
    if axis_of(c) in HANDOFF_ONLY_AXES:
        return _fit_handoff_only(c, view)
    members = linked_members(analysis, c, matches) if is_byeol(c) else None
    status, role, source, text = fit_basis(c, view, members)
    if polarity_of(c) == "-" and role == "select" and status in ("match", "conflict"):
        status = "conflict" if status == "match" else "match"  # "X 제외" conflicts with an X-only table
    elif polarity_of(c) == "-" and role == "handoff" and status == "match":
        from .basis import values

        if any(values(n, axis_of(c), value_of(c)) - {value_of(c)} for n in view.item_names_ns):
            text = f"{text}은 B에서 빼고 반대 기준 분류값을 고른다"
        else:
            status, role, text = "conflict", "select", f"{text}만 있어 뺄 수 없음"
    return status, role, source, text


_SOURCE_KIND = {"title": "title", "path": "path", "item": "itm", "unit": "unit"}


def check_basis(inp: EvidenceInput, requirement_id: str, table_id: str,
                prior: list[Check] | None = None) -> CheckResult:
    """One check per basis (and calc) condition: title > path > values > unit (old J4)."""
    view = _view(inp, table_id)
    if view is None:
        return [], {}
    counted, _ = scope(inp.analysis, requirement_id)
    matches = _prior_rows(prior)
    checks, evidence = [], {}
    for c in conditions_of(inp.analysis, "basis", "calc"):
        got = basis_status(c, view, inp.analysis, matches)
        if got is None:
            continue
        status, role, source, text = got
        eids = []
        if source != "none":
            eid = f"B:{requirement_id}:{table_id}:{c['id']}"
            evidence[eid] = _row(_SOURCE_KIND[source], STANCE.get(status, "unknown"), table_id, text)
            eids.append(eid)
        detail = {"condition_id": c["id"], "status": status, "role": ROLE[role], "source": source}
        checks.append(_check("basis", _state(view), status, _verdict(c["id"], counted, status), eids, detail))
    return checks, evidence


def unit_parts(unit: str) -> tuple[int, str] | None:
    """Source: src/claview/aflow/rules.py:762-765 (commit 55fd41b)."""
    text = re.sub(r"\s+", "", unit or "")
    for word in WORDS["percent_words"]:
        text = text.replace(word, "%")
    m = _UNIT_PARTS.fullmatch(text)
    return (_UNIT_SCALE[m.group(1) or ""], m.group(2)) if m else None


def compare_unit(wanted: str, available: list[str]) -> str:
    """same / rescale (same base, other scale) / differ / unknown (rules.py:768-779)."""
    w = unit_parts(wanted)
    have = [p for p in (unit_parts(u) for u in available) if p]
    if w is None or not available:
        return "unknown"
    if w in have:
        return "same"
    if any(b == w[1] for _, b in have):
        return "rescale"
    return "differ"


def check_unit(inp: EvidenceInput, requirement_id: str, table_id: str,
               prior: list[Check] | None = None) -> CheckResult:
    """One check per unit condition: scale and base compared (old J4 unit, compare_unit). same and rescale pass
    (B converts the scale), differ fails."""
    view = _view(inp, table_id)
    if view is None:
        return [], {}
    counted, _ = scope(inp.analysis, requirement_id)
    units = sorted(view.units())
    checks, evidence = [], {}
    for c in conditions_of(inp.analysis, "unit"):
        verdict = compare_unit(value_of(c), units)
        old = {"same": "match", "differ": "conflict"}.get(verdict, "unknown")
        eids = []
        if verdict != "unknown":
            eid = f"U:{requirement_id}:{table_id}:{c['id']}"
            evidence[eid] = _row("unit", STANCE.get(old, "unknown"), table_id, f"단위 {units[:3]}")
            eids.append(eid)
        decided = _verdict(c["id"], counted, verdict, good=("same", "rescale"), bad=("differ",))
        checks.append(_check("unit", _state(view), old, decided, eids, {"condition_id": c["id"], "status": verdict}))
    return checks, evidence


def check_exclude(inp: EvidenceInput, requirement_id: str, table_id: str,
                  prior: list[Check] | None = None) -> CheckResult:
    """One check per exclude condition: complement value first (old J2/J4 exclude)."""
    view = _view(inp, table_id)
    if view is None:
        return [], {}
    counted, _ = scope(inp.analysis, requirement_id)
    matches = _prior_rows(prior)
    checks, evidence = [], {}
    for c in conditions_of(inp.analysis, "exclude"):
        comp = [m for m in matches if m.get("condition_id") == c["id"] and m.get("match_method") == "complement"]
        name = nospace(_attrs(c).get("text") or c.get("value") or "")
        if comp:
            status, source, text = "match", "item", f"보완 분류값 '{comp[0]['name']}'"
        elif name and name in view.title_ns:
            status, source, text = "conflict", "title", f"표명이 '{name}' 전용"
        elif name and any(name == n for n in view.item_names_ns):
            status, source, text = "match", "item", f"분류값 '{name}'을 B에서 뺀다"
        else:
            status, source, text = "unknown", "none", ""
        eids = []
        if source != "none":
            eid = f"X:{requirement_id}:{table_id}:{c['id']}"
            evidence[eid] = _row(_SOURCE_KIND[source], STANCE.get(status, "unknown"), table_id, text)
            eids.append(eid)
        detail = {"condition_id": c["id"], "complement_found": bool(comp), "items": [m["name"] for m in comp]}
        checks.append(_check("exclude", _state(view), status, _verdict(c["id"], counted, status), eids, detail))
    return checks, evidence


# ---------------------------------------------------------------- cross axis


def check_cross_axis(inp: EvidenceInput, requirement_id: str, table_id: str,
                     prior: list[Check] | None = None) -> CheckResult:
    """Two or more classification conditions given by different axes of the table (old cross_axis_support). A
    record: it says whether the axis structure can give the classifications apart, never whether the question asks
    for a cross table, so its verdict is n/a."""
    view = _view(inp, table_id)
    if view is None:
        return [], {}
    counted, _ = scope(inp.analysis, requirement_id)
    conds, seen = [], set()
    for c in conditions_of(inp.analysis, "basis"):
        if c["id"] in counted and is_byeol(c) and value_of(c) not in seen:
            seen.add(value_of(c))
            conds.append(c)
    if len(conds) < 2:
        return [], {}
    statuses = {(p.get("detail") or {}).get("condition_id"): (p.get("detail") or {}).get("status")
                for p in prior or [] if p.get("name") == "basis"}
    matches = _prior_rows(prior)
    axes: dict[str, list[str]] = {}
    for c in conds:
        got = breakdown_axes(c, view)
        members = linked_members(inp.analysis, c, matches)
        got |= {it.obj_id for it in view.items if it.name_raw in members}
        axes[c["id"]] = sorted(got)
    cids = [c["id"] for c in conds]
    if any(statuses.get(cid) != "match" for cid in cids):
        support, reason = "unknown", "not_matched"
    elif any(not axes[cid] for cid in cids):
        support, reason = "unknown", "no_axis_evidence"
    elif any(len(set(pick)) == len(pick) for pick in itertools.product(*(axes[cid] for cid in cids))):
        support, reason = "supported", "distinct_axes"
    elif any(_COMBINED_VALUE.search(it.name_raw or "") for it in view.items
             if it.obj_id in {a for cid in cids for a in axes[cid]}):
        support, reason = "unknown", "combined_values"
    else:
        support, reason = "single_axis", "shared_axis"
    detail = {"support": support, "reason": reason, "axes": axes}
    return [_check("cross_axis", _state(view), support, "n/a", [], detail)], {}


# ---------------------------------------------------------------- region (targets) and indicator


def target_names(analysis: dict, rid: str) -> list[str]:
    """Target values of a requirement as written for matching: catalog name, else the quote."""
    req = requirement_of(analysis, rid)
    out = []
    for t in req.get("targets") or []:
        name = t.get("name") or t.get("quote")
        if name and name not in out:
            out.append(name)
    return out


def target_forms(analysis: dict, rid: str, name: str) -> list[str]:
    """The name, the quote it came from and the name without brackets ("국내(사용)" -> 국내)."""
    req = requirement_of(analysis, rid)
    forms = [name]
    for t in req.get("targets") or []:
        if (t.get("name") or t.get("quote")) == name and t.get("quote"):
            forms.append(t["quote"])
    forms.append(strip_parens(name))
    return [f for f in dict.fromkeys(forms) if f]


def table_has_name(layer, view: TableView, forms: list[str]) -> bool:
    """One of the forms among the table's values (any name variant of the catalog index: footnotes, list numbers,
    industry codes and brackets removed) or inside its title."""
    rel = relations_of(layer)
    for name in forms:
        k = index_key(name)
        if len(nospace(name)) >= 2 and (view.table_id in rel.tables_of(k) or nospace(name) in view.title_ns):
            return True
        if items_for(view, name):
            return True
    return False


def check_region(inp: EvidenceInput, requirement_id: str, table_id: str,
                 prior: list[Check] | None = None) -> CheckResult:
    """The requirement's target values (2.4 target terms: regions, countries, sectors ...) among the table's values
    or in its name. No targets -> no check."""
    view = _view(inp, table_id)
    names = target_names(inp.analysis, requirement_id)
    if view is None or not names:
        return [], {}
    found = [n for n in names if table_has_name(inp.layer, view, target_forms(inp.analysis, requirement_id, n))]
    status = "match" if len(found) == len(names) else "partial" if found else "none"
    eid = f"G:{requirement_id}:{table_id}"
    stance = {"match": "support", "partial": "unknown", "none": "against"}[status]
    evidence = {eid: _row("itm", stance, table_id, f"대상 {', '.join(found) or '없음'} / 요청 {', '.join(names)}")}
    verdict = {"match": "pass", "partial": "pass", "none": "fail"}[status]
    return [_check("region", _state(view), status, verdict, [eid], {"status": status})], evidence


def indicator_phrase(analysis: dict, rid: str, measure) -> dict | None:
    """Indicator words of a requirement, catalog-known words only (old ``rule_phrases``, measure.py:85-108, with
    the requirement in place of the old Q7 core): the words of its quote and of its indicator, minus the conditions
    and the targets (the region check reads those). A condition over the indicator words themselves (a listed
    indicator is a breakdown condition) does not take them out."""
    text = _text(analysis)
    if text is None:
        return None
    raw = text["raw"]
    req = requirement_of(analysis, rid)
    lo, hi = req.get("span") or [0, len(raw)]
    ind = (req.get("indicator") or {}).get("span")
    from . import model_refs
    refs = model_refs.references(analysis, rid)
    ranges = [r["span"] for r in refs if r["place"] in ("indicator", "context")] if refs is not None else None
    removed = [t["span"] for t in req.get("targets") or [] if t.get("span")]
    removed += [c["span"] for c in analysis.get("conditions") or [] if c.get("span")
                and not (ind and c["span"][0] < ind[1] and ind[0] < c["span"][1])]

    def kept(k: int) -> bool:
        inside = (any(a <= k < b for a, b in ranges) if ranges is not None else
                  lo <= k < hi or (ind is not None and ind[0] <= k < ind[1]))
        return inside and not any(a <= k < b for a, b in removed)

    keep = "".join(ch if kept(k) else " " for k, ch in enumerate(raw))
    terms = []
    for m in _WORD.finditer(unicodedata.normalize("NFKC", keep)):
        word = m.group(0)
        if not measure.recognizes(word):
            word = _TAIL.sub("", word)
        if measure_norm(word) in _STOP_KEYS or not measure.recognizes(word):
            continue
        terms.append(word)
    if not terms:
        # plan 10 (user 10/20 02:28): no catalog-known word in the written words (a name glued to other words), but
        # the requirement carries a catalog indicator name: the check reads that name
        name = (req.get("indicator") or {}).get("name")
        if not name:
            return None
        words = [w for w in (m.group(0) for m in _WORD.finditer(unicodedata.normalize("NFKC", name)))
                 if measure.recognizes(w) and measure_norm(w) not in _STOP_KEYS]
        if not words:
            return None
        return {"text": " ".join(words), "terms": list(dict.fromkeys(words)), "origin": "catalog_name"}
    return {"text": " ".join(terms), "terms": list(dict.fromkeys(terms)),
            "origin": "model" if refs is not None else "rule"}


def assess(phrase: dict | None, view: TableView, measure) -> dict:
    """Source: src/claview/aflow/measure.py:144-185 (commit 55fd41b). A path segment counts when one word equals the
    whole segment name, or the last segment (just above the table) equals the whole phrase; a field holding only part
    of the words is partial."""
    entries = measure.table_fields.get(view.table_id) or []
    leaf = (view.path or "").split(">")[-1].strip()
    by_field = {"title": 0.0, "item": 0.0, "catalog_path": 0.0}
    best = 0.0
    if phrase:
        terms = phrase["terms"]
        for text, f in entries:
            forms_used = set()
            for term in terms:
                form = next((form for form, _ in measure.forms(term) if form and form in text), None)
                if form:
                    forms_used.add(form)
                    forms_used.add(f"#{term}")
            coverage = sum(1 for term in terms if f"#{term}" in forms_used) / len(terms)
            if not coverage:
                continue
            path_stem = measure_norm(re.sub(r"\([^)]*\)", "", f["text"]))
            used = f["field"] != "catalog_path" or path_stem in forms_used or (
                f["text"] == leaf and coverage == 1 and measure_norm(phrase["text"]) == path_stem)
            if used:
                best = max(best, coverage)
                by_field[f["field"]] = max(by_field[f["field"]], coverage)
    status = "match" if best == 1 else "partial" if best else "none"
    return {"status": status, "coverage": best,
            "by_field": {k: "match" if v == 1 else "partial" if v else "none" for k, v in by_field.items()}}


def check_indicator(inp: EvidenceInput, requirement_id: str, table_id: str,
                    prior: list[Check] | None = None) -> CheckResult:
    """Indicator phrase against title, values and catalog path (old measure.assess). match and partial pass, none
    fails; no indicator words (available false) is n/a."""
    view = _view(inp, table_id)
    if view is None:
        return [], {}
    measure = selection_catalog(inp.layer).measure()
    phrase = indicator_phrase(inp.analysis, requirement_id, measure)
    got = assess(phrase, view, measure)
    eid = f"I:{requirement_id}:{table_id}"
    if phrase is None:
        detail = {"result": None, "available": False, "by_field": got["by_field"], "phrase": None, "coverage": None,
                  "origin": None}
        evidence = {eid: _row("measure", "unknown", table_id, "지표 문구 없음")}
        return [_check("indicator", _state(view), None, "n/a", [eid], detail)], evidence
    status = got["status"]
    detail = {"result": status, "available": True, "by_field": got["by_field"], "phrase": phrase["text"],
              "coverage": got["coverage"], "origin": phrase["origin"]}
    stance = {"match": "support", "partial": "unknown", "none": "against"}[status]
    evidence = {eid: _row("measure", stance, table_id, f"지표 '{phrase['text']}' {status}")}
    verdict = {"match": "pass", "partial": "pass", "none": "fail"}[status]
    return [_check("indicator", _state(view), status, verdict, [eid], detail)], evidence


# ---------------------------------------------------------------- breakdown (plan 07 S1, record mode)

_BYEOL_END = "별"
UNDER_READINGS = frozenset({"below", "in_series"})  # catalog readings of a lower item against its indicator


def breakdown_word(text: str) -> str:
    """A breakdown entry without marks and its particle ("경제활동과" -> 경제활동, "업종과" -> 업종)."""
    word = _MARKS.sub("", _TRIM.sub("", nospace(text)))
    return _TAIL.sub("", word) if len(_TAIL.sub("", word)) >= 2 else word


def _entry_span(raw: str, text: str, req: dict) -> list[int] | None:
    """Raw span of a breakdown entry: its first place inside the requirement's span, else in the question."""
    lo, hi = req.get("span") or [0, len(raw)]
    at = raw.find(text, lo)
    if at < 0 or at + len(text) > max(hi, lo):
        at = raw.find(text)
    return [at, at + len(text)] if at >= 0 else None


def named_lower_items(analysis: dict, rid: str) -> list[dict]:
    """Named lower items of a requirement's breakdown (plan 07 r4 9-4): for every breakdown entry that is not an "X별"
    axis, the one fragment holding its words and that fragment's exact-match catalog term. Several fragments, no
    exact term, or several exact names (none written as the entry) -> no name. [{text, word, span, name}] in
    breakdown order; ``name`` None for an unnamed entry. The interpretation output is read as it is."""
    text = _text(analysis)
    if text is None:
        return []
    raw = text["raw"]
    from . import model_refs
    refs = model_refs.references(analysis, rid)
    if refs is not None:
        return [{"text": r["quote"], "word": r["name"] or r["quote"], "span": r["span"], "name": r["name"]}
                for r in refs if r["place"] == "breakdown_value"]
    req = requirement_of(analysis, rid)
    out = []
    for entry in req.get("breakdown") or []:
        word = breakdown_word(entry or "")
        if not word or (word.endswith(_BYEOL_END) and len(word) > len(_BYEOL_END)):
            continue  # "X별" axes are judged by the basis and cross_axis checks
        span = _entry_span(raw, entry, req)
        name = None
        if span is not None:
            frags = [f for f in analysis.get("fragments") or []
                     if f["span"][0] < span[1] and span[0] < f["span"][1]]
            if len(frags) == 1:
                names = list(dict.fromkeys(t["name"] for t in analysis.get("terms") or []
                                           if t.get("fragment_id") == frags[0]["id"] and t.get("path") == "exact"
                                           and t.get("type") != "out_of_scope"))
                same = [n for n in names if index_key(n) == index_key(word)]
                name = names[0] if len(names) == 1 else same[0] if len(same) == 1 else None
        out.append({"text": entry, "word": word, "span": span, "name": name})
    return out


def check_breakdown(inp: EvidenceInput, requirement_id: str, table_id: str,
                    prior: list[Check] | None = None) -> CheckResult:
    """Named lower items of the requirement's breakdown (``named_lower_items``) in the table (plan 07 r4 S1): a value
    of the table (any name variant, ``table_has_name``) and, when the indicator has a catalog name, under it in this
    table (catalog reading ``below`` / ``in_series``). "X별" axes are left to the basis and cross_axis checks;
    unnamed entries are not judged. match: every named item found and none read elsewhere than under the indicator;
    partial: some found; none: none found (verdict fail). No named item -> no check. Record mode (plan 07 decision
    1): recorded only, not a filter and not in the rank score (cover.RECORD_ONLY)."""
    view = _view(inp, table_id)
    if view is None:
        return [], {}
    items = [x for x in named_lower_items(inp.analysis, requirement_id) if x["name"]]
    if not items:
        return [], {}
    req = requirement_of(inp.analysis, requirement_id)
    rel = relations_of(inp.layer)
    ind_name = (req.get("indicator") or {}).get("name")
    rows = []
    for x in items:
        found = table_has_name(inp.layer, view, [x["name"]])
        under = None
        if found and ind_name:
            got = rel.readings(index_key(x["name"]), index_key(ind_name)).get(table_id, set())
            under = bool(got & UNDER_READINGS) if got else None
        rows.append({"text": x["text"], "word": x["name"], "kind": "lower", "found": found, "under": under})
    hits = [r for r in rows if r["found"]]
    if hits and len(hits) == len(rows) and all(r["under"] is not False for r in rows):
        status = "match"
    else:
        status = "partial" if hits else "none"
    eid = f"R:{requirement_id}:{table_id}"
    stance = {"match": "support", "partial": "unknown", "none": "against"}[status]
    shown = ", ".join(r["word"] for r in hits) or "없음"
    evidence = {eid: _row("itm", stance, table_id, f"분해 {shown} / 요청 {', '.join(r['word'] for r in rows)}")}
    verdict = {"match": "pass", "partial": "pass", "none": "fail"}[status]
    detail = {"status": status, "rows": rows}
    return [_check("breakdown", _state(view), status, verdict, [eid], detail)], evidence


# ---------------------------------------------------------------- all checks


CHECK_ORDER = ("matched_items", "basis", "unit", "exclude", "cross_axis", "frequency", "period", "region",
               "indicator", "breakdown")
_FUNCS = {"matched_items": check_matched_items, "basis": check_basis, "unit": check_unit,
          "exclude": check_exclude, "cross_axis": check_cross_axis, "frequency": check_frequency,
          "period": check_period, "region": check_region, "indicator": check_indicator,
          "breakdown": check_breakdown}


def check_pair(inp: EvidenceInput, requirement_id: str, table_id: str) -> CheckResult:
    """Every check of one requirement x table, each reading the ones before it as ``prior``."""
    checks: list[Check] = []
    rows: dict[str, EvidenceRow] = {}
    for name in CHECK_ORDER:
        got, found = _FUNCS[name](inp, requirement_id, table_id, checks)
        checks += got
        rows.update(found)
    return checks, rows


def check_evidence(analysis: Analysis, table_ids: list[str], layer: CatalogLayer | None,
                   as_of: str | None = None, requirement_ids: list[str] | None = None) -> EvidenceDoc:
    """Every requirement (or the given ones) x every table: the checks above, merged into one document."""
    inp = EvidenceInput(analysis, layer, as_of)
    reqs = [r["id"] for r in (analysis.get("interpretation") or {}).get("requirements") or []]
    if requirement_ids is not None:
        reqs = [r for r in reqs if r in requirement_ids]
    doc: EvidenceDoc = {"evidence": [], "evidence_rows": {}}
    for rid in reqs:
        for tid in dict.fromkeys(table_ids):
            checks, rows = check_pair(inp, rid, tid)
            doc["evidence"].append({"requirement_id": rid, "table_id": tid, "checks": checks})
            doc["evidence_rows"].update(rows)
    return doc
