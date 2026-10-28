"""Cell cover of table selection (plan 04, 4-3 to 4-5): 3.5 cover_cells and the 3.7 recheck ``verify_choice``.

Requested cells of a requirement = target values x period segments x frequency x basis values. A dimension the
question does not set is not split: no target -> one cell; no frequency -> frequencies are not split (B picks one);
no period -> "the latest" (decision 2); a basis axis with one value is a condition every cell needs, an axis with two
or more values of the requirement is split.

Cells a table provides: for every (frequency, that frequency's period range) pair of the table, its targets x the
period segments inside the range x the basis values it does not contradict. Frequency and period are never
multiplied apart: a table with yearly data from 2000 and monthly data from 2010 gives no monthly cell before 2010.
Period segments are the resolved requested period cut at the period edges of the candidate tables; the requested
period is never cut back to the data (a relative period running past every candidate within the snapshot lag is
only marked ``period_lag``; its open part stays uncovered). "The latest" (no period) is computed among the tables
that meet the requirement's conditions (no basis or exclusion conflict, a requested target present): within one
family only the tables reaching that family's latest end give it, and only when that end is within the snapshot lag
of the latest end of all such candidates.

Search (4-3): per family the least table group covering every requested cell is searched exactly after dominated
tables are removed (cells a subset of another candidate's and a rank score not higher). More than 12 tables left ->
the 12 best by rank score are searched exactly, the rest is added greedily (most uncovered cells, then rank) for
cells the 12 leave open; marked ``search_truncated``; never searched exponentially. Same size -> higher rank-score
sum. No family covers everything alone -> the same search over every candidate (families mixed). Order of
alternatives (4-3): complete first, more covered cells, fewer tables, rank-score sum, evidence tier.

Unspecified dimensions (4-4, decision 5): tables of one family differ by a dimension when their basis-axis values
(read from values and title: 원계열/계절조정, 명목/실질, 말잔/평잔 ...) or their non-basis bracket words (source,
classification system, agency; year ranges left out) differ. A family member outside the chosen group that gives
any requested cell a chosen table gives, stands on every basis word of the requirement at least as well, and differs
from it by such a dimension, is put in; so are chosen tables that differ among themselves. All such tables of the
requirement form one mark (its dimension names every axis involved) with one cap of 3 tables, most recent start
first; past the cap ``more`` is marked, and a chosen table the cover still needs is kept.

3.7 ``verify_choice`` re-runs the checks on the chosen tables and returns, besides the cover, every own or shared
condition of the requirement a chosen table does not pass (``unproven``): decide_state allows auto only without
them (state.py).

Nothing here names a table, case or question.
"""

from __future__ import annotations

import itertools
import json
import re

from .. import paths
from ..catalog.relations import relations_of
from ..interpret.condition_rules import BASIS_AXES
from ..reused import q_rules
from .candidates import family_key
from ..catalog.text import index_key
from .evidence import (FREQ_MONTHS, HANDOFF_ONLY_AXES, LAG_PERIODS, EvidenceInput, axis_of, check_pair,
                       conditions_of, is_byeol, polarity_of, requested, requirement_of, scope, table_has_name,
                       target_forms, target_names)
from .tables import nospace, selection_catalog

EXACT_MAX = 12  # candidates searched exactly (plan 04, 4-3)
UNSPECIFIED_CAP = 3  # tables put in per unspecified dimension (decision 5)
TITLE_BONUS = 15.0  # the table name written as such in the question (old J5 title bonus)
CHECK_WEIGHT = 10.0  # one passed or failed condition check in the rank score (old J5 condition unit)
INDICATOR_VALUE = {"match": 1.0, "partial": 0.5, "none": 0.0}
# Checks that never move the rank score: matched_items and cross_axis (verdict always n/a) and plan 07's breakdown
# (record mode, decision 1: recorded only until the development record shows it drops no answer table).
RECORD_ONLY = ("matched_items", "cross_axis", "breakdown")
LATEST = "latest"
_BRACKET = re.compile(r"\(([^()]*)\)")
_LETTER = re.compile(r"[가-힣A-Za-z]")
_YEAR_RANGE = re.compile(r"~?\d{4}(?:\.\d{1,2})?(?:~(?:\d{2,4}(?:\.\d{1,2})?)?)?|\d{4}-\d{4}")
SIGNATURE_AXES = [a for a in BASIS_AXES if a not in HANDOFF_ONLY_AXES]
_BASIS_FORMS = {nospace(f) for a in SIGNATURE_AXES for fs in BASIS_AXES[a].values() for f in fs}
POLICY = json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8"))
AXIS_KINDS: dict[str, str] = POLICY["basis_axis_kinds"]
FREQUENCY_WORDS = frozenset(nospace(w) for w in POLICY["frequency_words"])
SOURCE_WORDS = frozenset(nospace(w) for w in POLICY["source_words"])
AGENCY_WORDS = tuple(POLICY["agency_axis_words"])
CONVENTIONS: dict[str, list[str]] = POLICY["conventions"]
CONFIRM_KINDS = frozenset(POLICY["confirm_kinds"])
POLICIES = ("representative", "keep_all")
DEFAULT_POLICY = POLICY["unspecified_policy"]


# ---------------------------------------------------------------- rank score and evidence


def _checks_by_table(evidence: dict, rid: str) -> dict[str, list[dict]]:
    return {e["table_id"]: e["checks"] for e in evidence["evidence"] if e["requirement_id"] == rid}


def indicator_result(checks: list[dict]) -> str | None:
    for c in checks:
        if c["name"] == "indicator":
            return c["detail"]["result"] if c["detail"]["available"] else None
    return None


def rank_score(candidate: dict, checks: list[dict], question_ns: str, view) -> float:
    """Rank score of a candidate (plan 04, 4-2: verdicts that do not filter order the candidates): search score +
    10 x indicator value + 10 x passed - 10 x failed condition checks + 15 when the table name is in the question.
    A check with verdict n/a (ambiguous attribution, no evidence either way) gives nothing; the record-only checks
    (``RECORD_ONLY``: matched_items, cross_axis and plan 07's breakdown until its record is reviewed) give nothing."""
    score = float(candidate.get("fused_score") or 0.0)
    for c in checks:
        if c["name"] == "indicator":
            score += CHECK_WEIGHT * INDICATOR_VALUE.get(c["detail"].get("result") or "none", 0.0)
        elif c["name"] in RECORD_ONLY:
            continue
        elif c["verdict"] == "pass":
            score += CHECK_WEIGHT
        elif c["verdict"] == "fail":
            score -= CHECK_WEIGHT
    stem = nospace(view.title.split("(")[0]) if view else ""
    if len(stem) >= 2 and stem in question_ns:
        score += TITLE_BONUS
    return round(score, 6)


# ---------------------------------------------------------------- plan 07 r5 Q3: recorded score items
# Weights of the score items (rank-score units; a condition check is CHECK_WEIGHT): read from the catalog structure
# only (table name words, bracket words, axes); the question words are compared as written (no spaces).
TITLE_WORD = 4.0  # a word of the table name (front or bracket) the question writes, no condition of the requirement
AMBIGUOUS_WORD = 2.0  # the same for the words of an ambiguous condition (weak plus: the condition is not dropped)
UNASKED_AXIS = 5.0  # minus: a basis axis the question names nowhere that other candidates do not have
_TITLE_SPLIT = re.compile(r"[\s(),·/]+")


def _filler_words() -> frozenset:
    # the lexicon's request and filler words and the general nouns written after a value (no word in this code)
    from ..interpret.lexicon import BASIS_TAIL_NOUNS, KNOWN_WORDS

    return KNOWN_WORDS | BASIS_TAIL_NOUNS


def title_words(title: str) -> list[str]:
    """The words of a table name (front and bracket parts) as nospace keys, at least two letters, no year range or
    number only, no filler word of the lexicon."""
    out = []
    for w in _TITLE_SPLIT.split(title or ""):
        k = nospace(w)
        if len(k) < 2 or not _LETTER.search(k) or _YEAR_RANGE.fullmatch(k) or index_key(k) in _filler_words():
            continue
        if k not in out:
            out.append(k)
    return out


def question_words(analysis: dict, rid: str) -> dict:
    """What the score items compare a table with: {text: the question without spaces and without its negated
    condition words, own: the words of the requirement's own and shared conditions, ambiguous: the words of its
    ambiguous conditions, starts: the written words of the question, cut at spaces and marks}."""
    raw = (analysis.get("text") or {}).get("raw") or ""
    counted, amb = scope(analysis, rid)
    from . import model_refs
    own_text = model_refs.words(analysis, rid)
    if own_text is not None:
        raw = own_text + " " + " ".join(c["quote"] for c in analysis.get("conditions") or []
                                      if c["id"] in counted | amb)
    own, ambiguous = set(), set()
    for c in analysis.get("conditions") or []:
        words = {nospace(c.get("quote") or ""), nospace(str(c.get("value") or ""))} - {""}
        if polarity_of(c) == "-" and c.get("quote"):
            raw = raw.replace(c["quote"], " ")
        if c["id"] in counted:
            own |= words
        elif c["id"] in amb:
            ambiguous |= words
    starts = tuple(w for w in _TITLE_SPLIT.split(raw) if w)
    return {"text": nospace(raw), "own": own, "ambiguous": ambiguous, "starts": starts}


def title_bonus_words(analysis: dict, rid: str) -> str:
    """Only the adopted model requirement supplies its title bonus; retain the legacy question-wide signal."""
    from . import model_refs
    if model_refs.adopted(analysis):
        return question_words(analysis, rid)["text"]
    return nospace((analysis.get("text") or {}).get("raw") or "")


def _asked_axis(view, obj: str, name: str, qtext: str) -> bool:
    from ..interpret.lexicon import BYEOL  # the X-byeol mark of the lexicon

    key = nospace(name).removesuffix(BYEOL)
    if len(key) >= 2 and key in qtext:
        return True
    return any(len(it.name_nospace) >= 2 and it.name_nospace in qtext for it in view.items if it.obj_id == obj)


def score_items(view, words: dict, others: list) -> list[dict]:
    """Plan 07 r5 Q3 score items of one table, each {item, value, reason}: a table-name word the question writes
    (``title_word``; weak for an ambiguous condition's word, none for the requirement's own conditions, which their
    checks score already) and a classification axis the question names nowhere (neither its name nor a value) that
    some other candidate (``others``: the requirement's other candidates) lacks (``unasked_axis``, minus)."""
    if view is None:
        return []
    out = []
    qtext = words["text"]
    starts = words.get("starts")
    for w in title_words(view.title):
        if w not in qtext or any(w in o or o in w for o in words["own"] if len(o) >= 2):
            continue
        if starts is not None and not any(s.lower().startswith(w.lower()) for s in starts):
            continue  # a written word starts with it, never only inside another word ("가상자산" in "가상수정자산률")
        weak = any(w in a or a in w for a in words["ambiguous"] if len(a) >= 2)
        out.append({"item": "title_word", "value": AMBIGUOUS_WORD if weak else TITLE_WORD,
                    "reason": f"{w}{' (ambiguous condition)' if weak else ''}"})
    from ..interpret.lexicon import BYEOL

    roles = getattr(view, "axis_roles", {}) or {}
    # an X-byeol word of the requirement written in the table name names its axes ("가상A별 가상B별" tables)
    named_axes = any(o.endswith(BYEOL) and len(o) >= 3 and o in view.title_ns for o in words["own"])
    for obj, name in view.dims:
        # a basis axis only (a measure kind the table is split by: target axes default to their totals)
        if named_axes or roles.get(obj) != "basis" or _asked_axis(view, obj, name, qtext):
            continue
        key = nospace(name)
        if others and not all(any(nospace(n) == key for _, n in o.dims) for o in others):
            out.append({"item": "unasked_axis", "value": -UNASKED_AXIS, "reason": name})
    return out


def has_evidence(candidate: dict, checks: list[dict]) -> bool:
    """A candidate with indicator evidence (match or partial). With no indicator words: a value matched by the
    question words or a table of the requirement's terms. A table of a no-indicator question holding every target
    (plan 07 r4 S3, entered by target) has its evidence from the targets: the indicator check is skipped."""
    if candidate.get("entered_by") == "target":
        return True
    result = indicator_result(checks)
    if result is not None:
        return result in ("match", "partial")
    if candidate.get("entered_by") == "term":
        return True
    return any(row["condition_id"] is None and row["polarity"] == "+"
               for c in checks if c["name"] == "matched_items" for row in c["detail"]["rows"])


# ---------------------------------------------------------------- requested cells


def _months(stamp: str | None, end: bool = False) -> int | None:
    got = q_rules.stamp_months(stamp)
    return None if got is None else got[1 if end else 0]


def _rows(view, freqs: list[str | None]) -> list[tuple[str, int, int]]:
    """(frequency, first month, last month) of the table rows asked for (all rows when no frequency is asked)."""
    out = []
    for f, start, end in view.periods:
        if freqs != [None] and f not in freqs:
            continue
        lo, hi = _months(start), _months(end, True)
        if lo is not None and hi is not None:
            out.append((f, lo, hi))
    return out


def _label(lo: int, hi: int) -> str:
    return f"{lo // 12:04d}-{lo % 12 + 1:02d}~{hi // 12:04d}-{hi % 12 + 1:02d}"


def requested_cells(analysis: dict, rid: str, views: dict, as_of: str | None) -> dict:
    """Dimensions of the requested cells of one requirement over the candidate ``views``:
    {targets, target_forms, frequencies, periods, basis, constraints, cross, proof, flags}. ``periods``: [(label,
    lo, hi)] or [(LATEST, None, None)]; ``basis``: combinations of condition IDs on split axes; ``constraints``:
    other basis/exclude conditions every cell needs; ``cross``: two or more "X별" breakdowns to give on different
    axes; ``proof``: own and shared condition IDs by check name that a chosen table must pass for auto."""
    counted, _ = scope(analysis, rid)
    forms = {n: target_forms(analysis, rid, n) for n in target_names(analysis, rid)}
    for c in conditions_of(analysis, "breakdown"):  # listed values to show are covered like targets
        value = (c.get("attrs") or {}).get("text") or c.get("value")
        if c["id"] in counted and value and value not in forms:
            forms[value] = [value]
    targets = list(forms) or [None]
    periods_c, wanted, env = requested(analysis, rid, as_of)
    relative = any((c.get("attrs") or {}).get("relative") for c in periods_c)
    freqs: list[str | None] = list(wanted) or [None]
    flags: list[str] = []
    rows = [r for v in views.values() for r in _rows(v, freqs)]
    periods: list[tuple[str, int | None, int | None]]
    if not periods_c or env is None or env["status"] != "explicit":
        if periods_c:
            flags.append("period_unknown")
        periods = [(LATEST, None, None)]
    else:
        lo = _months(env["start"])
        hi = _months(env["end"] or env["start"], True) if env["end"] else None
        max_hi = max((r[2] for r in rows), default=None)
        if hi is None:  # open end: up to the latest data
            hi = max_hi if max_hi is not None and max_hi >= lo else lo
        if relative and max_hi is not None and hi > max_hi:
            lag = max(FREQ_MONTHS[f] for f, _, h in rows if h == max_hi) * LAG_PERIODS
            if hi - max_hi <= lag:
                flags.append("period_lag")  # a mark only: the open part of the request stays a cell
        edges = {lo, hi + 1}
        for _, a, b in rows:
            for x in (a, b + 1):
                if lo < x <= hi:
                    edges.add(x)
        cuts = sorted(edges)
        periods = [(_label(a, b - 1), a, b - 1) for a, b in zip(cuts, cuts[1:])]
    by_axis: dict[str, list[str]] = {}
    constraints: list[str] = []
    cross: list[str] = []
    proof: dict[str, list[str]] = {"period": [c["id"] for c in periods_c],
                                   "frequency": [c["id"] for c in conditions_of(analysis, "frequency")
                                                 if c["id"] in counted],
                                   "basis": [], "unit": [], "exclude": []}
    for c in conditions_of(analysis, "basis"):
        if c["id"] not in counted or axis_of(c) in HANDOFF_ONLY_AXES:
            continue
        proof["basis"].append(c["id"])
        if is_byeol(c):  # "X별" words are axes to break down by, not values
            constraints.append(c["id"])
            cross.append(c["id"])
        elif polarity_of(c) == "+":
            by_axis.setdefault(axis_of(c), []).append(c["id"])
        else:
            constraints.append(c["id"])
    proof["unit"] = [c["id"] for c in conditions_of(analysis, "unit") if c["id"] in counted]
    proof["exclude"] = [c["id"] for c in conditions_of(analysis, "exclude") if c["id"] in counted]
    split = {a: ids for a, ids in by_axis.items() if len(ids) >= 2}
    constraints += [cid for a, ids in by_axis.items() if len(ids) < 2 for cid in ids]
    constraints += proof["exclude"]
    basis = [tuple(p) for p in itertools.product(*split.values())] if split else [()]
    return {"targets": targets, "target_forms": forms, "frequencies": freqs, "periods": periods, "basis": basis,
            "constraints": constraints, "cross": cross if len(cross) >= 2 else [], "proof": proof, "flags": flags}


def all_cells(req: dict) -> set[tuple]:
    return set(itertools.product(req["targets"], req["frequencies"], [p[0] for p in req["periods"]], req["basis"]))


def _status_by_condition(checks: list[dict]) -> dict[str, str]:
    return {c["detail"]["condition_id"]: c["source_status"] for c in checks
            if c["name"] in ("basis", "exclude") and "condition_id" in c["detail"]}


def _targets_of(layer, view, req: dict) -> list:
    return [t for t in req["targets"] if t is None or table_has_name(layer, view, req["target_forms"][t])]


def eligible(layer, view, checks: list[dict], req: dict) -> bool:
    """The table meets the requirement's conditions: no basis or exclusion conflict, a requested target present."""
    status = _status_by_condition(checks)
    if any(status.get(cid) == "conflict" for cid in req["constraints"]):
        return False
    return bool(_targets_of(layer, view, req))


def latest_ends(views: dict, req: dict, families: dict[str, str]) -> dict:
    """{"all": {frequency: last month}, "family": {family: {frequency: last month}}} over ``views`` (the eligible
    candidates)."""
    out: dict = {"all": {}, "family": {}}
    for f in req["frequencies"]:
        for t, v in views.items():
            ends = [h for _, _, h in _rows(v, [f])]
            if not ends:
                continue
            top = max(ends)
            out["all"][f] = max(out["all"].get(f, top), top)
            fam = out["family"].setdefault(families.get(t, t), {})
            fam[f] = max(fam.get(f, top), top)
    return out


def provided_cells(layer, view, checks: list[dict], req: dict, latest: dict, family: str | None = None) -> set[tuple]:
    """Cells one table gives (module docstring)."""
    status = _status_by_condition(checks)
    if any(status.get(cid) == "conflict" for cid in req["constraints"]):
        return set()
    basis = [b for b in req["basis"] if all(status.get(cid) != "conflict" for cid in b)]
    targets = _targets_of(layer, view, req)
    out: set[tuple] = set()
    fam_tops = latest["family"].get(family or view.table_id, {})
    for f in req["frequencies"]:
        rows = _rows(view, [f])
        for label, lo, hi in req["periods"]:
            if label == LATEST:  # the latest of its own group; the snapshot lag against all candidates is _fresh
                fam_top = fam_tops.get(f)
                end = max((h for _, _, h in rows), default=None)
                ok = end is not None and fam_top is not None and end >= fam_top
            else:
                ok = any(a <= lo and hi <= b for _, a, b in rows)
            if ok:
                out |= set(itertools.product(targets, [f], [label], basis))
    return out


def fresh(view, req: dict, latest: dict) -> bool:
    """For "the latest": the table's data reach the latest end of all eligible candidates within the snapshot lag
    (two periods of its frequency). A table of a stale group stays a sibling to compare and mark, never a pick."""
    if all(p[0] != LATEST for p in req["periods"]):
        return True
    for f in req["frequencies"]:
        top = latest["all"].get(f)
        rows = _rows(view, [f])
        end = max((h for _, _, h in rows), default=None)
        if top is not None and end is not None and any(h == end and h + FREQ_MONTHS[g] * LAG_PERIODS >= top
                                                       for g, _, h in rows):
            return True
    return False


def table_unproven(table_id: str, checks: list[dict], req: dict, complete: bool) -> list[dict]:
    """Own and shared conditions this chosen table does not pass (plan 04, 4-6 auto list): period covered (partial
    only as part of a complete join, never unknown), frequency, basis and "X별" words, unit and exclusion pass; two
    or more "X별" given on different axes. {table_id, check, condition_id, status}."""
    out = []

    def miss(check: str, cid, status) -> None:
        out.append({"table_id": table_id, "check": check, "condition_id": cid, "status": status})

    by_cond = {(c["name"], c["detail"].get("condition_id")): c for c in checks}
    proof = req["proof"]
    if proof["period"]:
        period = next((c for c in checks if c["name"] == "period"), None)
        status = period["source_status"] if period else None
        if not (status == "covered" or (status == "partial" and complete)):
            miss("period", None, status)
    if proof["frequency"]:
        freq = next((c for c in checks if c["name"] == "frequency"), None)
        if not freq or freq["verdict"] != "pass":
            miss("frequency", None, freq["source_status"] if freq else None)
    for name in ("basis", "unit", "exclude"):
        for cid in proof[name]:
            c = by_cond.get((name, cid))
            if not c or c["verdict"] != "pass":
                miss(name, cid, c["source_status"] if c else None)
    if req["cross"]:
        cross = next((c for c in checks if c["name"] == "cross_axis"), None)
        if not cross or cross["detail"].get("support") != "supported":
            miss("cross_axis", None, cross["detail"].get("support") if cross else None)
    return out


def condition_supported_cells(table_id: str, checks: list[dict], req: dict, cells: set[tuple]) -> set[tuple]:
    """Cells whose conditions are positively supported, for ordering only.

    A split basis value belongs to its cell, not to every table in a bundle. Unknown
    evidence stays possible. This helper never asserts settled interpretation,
    indicator identity, execution compatibility or a proven allocation.
    """
    split = {cid for ids in req["basis"] for cid in ids}
    out = set()
    for cell in cells:
        local = {**req, "proof": {**req["proof"], "basis": [cid for cid in req["proof"]["basis"]
                  if cid not in split or cid in cell[3]]}}
        if not table_unproven(table_id, checks, local, True):
            out.add(cell)
    return out


# ---------------------------------------------------------------- least cover


def _undominated(tables: list[str], prov: dict[str, set], score: dict[str, float], order: dict[str, int]) -> list[str]:
    keep = []
    for a in tables:
        dominated = False
        for b in tables:
            if a == b:
                continue
            if prov[a] <= prov[b] and score[a] <= score[b]:
                if prov[a] == prov[b] and score[a] == score[b] and order[a] < order[b]:
                    continue  # equal tables: the earlier one stays
                dominated = True
                break
        if not dominated:
            keep.append(a)
    return keep


def _least(tables: list[str], target: set, prov: dict[str, set], score: dict[str, float],
           order: dict[str, int]) -> list[str]:
    """Least group covering ``target`` (exact search over at most EXACT_MAX tables); same size -> higher score
    sum, then candidate order."""
    if not target:
        return []
    assert len(tables) <= EXACT_MAX, "exact search is bounded"
    for k in range(1, len(tables) + 1):
        best = None
        for combo in itertools.combinations(tables, k):
            if target <= set().union(*(prov[t] for t in combo)):
                key = (-sum(score[t] for t in combo), [order[t] for t in combo])
                if best is None or key < best[0]:
                    best = (key, list(combo))
        if best is not None:
            return best[1]
    return []


def best_cover(tables: list[str], cells: set, prov: dict[str, set], score: dict[str, float],
               order: dict[str, int], log: list | None = None) -> dict:
    """{tables, covered, complete, truncated} for ``tables`` (module docstring, 4-3). ``log`` (plan 11 R0, record
    only): receives what the dominance cut and the least-cover search left out, and why."""
    useful = [t for t in tables if prov[t] & cells]
    if not useful:
        return {"tables": [], "covered": set(), "complete": False, "truncated": False}
    kept = _undominated(useful, prov, score, order)
    if log is not None:
        _log_dominated(useful, kept, prov, score, order, log)
    ranked = sorted(kept, key=lambda t: (-score[t], order[t]))
    head, rest = ranked[:EXACT_MAX], ranked[EXACT_MAX:]
    target = cells & set().union(*(prov[t] for t in head))
    chosen = _least(head, target, prov, score, order)
    if log is not None and chosen:
        _log_least(head, target, prov, score, chosen, log)
    covered = cells & set().union(*(prov[t] for t in chosen)) if chosen else set()
    missing = (cells & set().union(*(prov[t] for t in kept))) - covered
    while missing and rest:  # left-out tables, greedily: most uncovered cells, then rank
        pick = max(rest, key=lambda t: (len(prov[t] & missing), score[t], -order[t]))
        if not prov[pick] & missing:
            break
        rest.remove(pick)
        chosen.append(pick)
        covered |= prov[pick] & cells
        missing -= prov[pick]
    return {"tables": chosen, "covered": covered, "complete": covered == cells, "truncated": len(kept) > EXACT_MAX}


# ---------------------------------------------------------------- plan 11 R0: what the cuts leave out (record only)

COVERS_CAP = 10  # complete covers recorded per family (plan 11 D: past it ``cut``)
COVER_COMBOS = 20000  # combinations tried per family before the enumeration is marked cut
PRUNED_CAP = 10  # same-size least covers recorded per search


def _log_dominated(useful: list[str], kept: list[str], prov: dict[str, set], score: dict[str, float],
                   order: dict[str, int], log: list) -> None:
    for a in useful:
        if a in kept:
            continue
        by = next((b for b in useful if b != a and prov[a] <= prov[b] and score[a] <= score[b]
                   and not (prov[a] == prov[b] and score[a] == score[b] and order[a] < order[b])), None)
        log.append({"step": "undominated", "table_ids": [a], "kept": [by] if by else [],
                    "reason": "score" if by is not None and prov[a] == prov[by] else "cells"})


def _log_least(head: list[str], target: set, prov: dict[str, set], score: dict[str, float], chosen: list[str],
               log: list) -> None:
    """The other least covers of the same size the score sum (then the candidate order) left out."""
    n = 0
    for combo in itertools.combinations(head, len(chosen)):
        if set(combo) == set(chosen) or not target <= set().union(*(prov[t] for t in combo)):
            continue
        log.append({"step": "least", "table_ids": list(combo), "kept": list(chosen), "reason": "score",
                    "score_sum": round(sum(score[t] for t in combo), 6)})
        n += 1
        if n >= PRUNED_CAP:
            break


def all_covers(tables: list[str], cells: set, prov: dict[str, set], score: dict[str, float],
               cap: int = COVERS_CAP) -> dict:
    """Every minimal complete cover of ``cells`` over ``tables`` (plan 11 D, R0 record): {covers: [{tables,
    score_sum}], cut}. Smallest first; past ``cap`` covers, past EXACT_MAX tables (the best by score are used) or
    past COVER_COMBOS combinations the list is marked cut."""
    useful = [t for t in tables if prov.get(t, set()) & cells]
    if not cells or not useful or not cells <= set().union(*(prov[t] for t in useful)):
        return {"covers": [], "cut": False}
    cut = len(useful) > EXACT_MAX
    if cut:
        useful = sorted(useful, key=lambda t: -score.get(t, 0.0))[:EXACT_MAX]
    found: list[tuple[str, ...]] = []
    tried = 0
    for k in range(1, len(useful) + 1):
        for combo in itertools.combinations(useful, k):
            tried += 1
            if tried > COVER_COMBOS:
                cut = True
                break
            if any(set(f) <= set(combo) for f in found):
                continue
            if cells <= set().union(*(prov[t] for t in combo)):
                found.append(combo)
                if len(found) > cap:
                    break
        if tried > COVER_COMBOS or len(found) > cap:
            break
    if len(found) > cap:
        cut = True
        found = found[:cap]
    return {"covers": [{"tables": list(c), "score_sum": round(sum(score.get(t, 0.0) for t in c), 6)} for c in found],
            "cut": cut}


OBS_CAPS = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))["observation_caps"]
OBS_COVERS_CAP = OBS_CAPS["OBS_COVERS_CAP"]
OBS_COMBOS = OBS_CAPS["OBS_COMBOS"]


def enumerate_assignments(units: list[str], cells: set, prov: dict[str, set], score: dict[str, float],
                          cap: int | None = None, combos: int | None = None) -> dict:
    """Plan 11 R1b S5 (observation path only; C7, K2): every minimal cover of the requested cells over ``units``
    (tables or edition groups, the refuted ones already left out), and beside them every unit no minimal cover uses
    as a partial assignment of its own. With no complete cover, the minimal covers of the cells the units can reach
    are the (partial) covers. A unit covering the cells alone is a cover and never part of a larger one; a larger
    cover is searched only over the other units, never with more units than cells. Past ``cap`` covers or
    ``combos`` combinations: ``cut``; the search stops there (covers are found smaller first, so the covers kept are
    the smaller ones, then the better-scored among those found). When ``cut``, every unit outside the kept covers
    may belong to an unexplored or dropped cover, so none is a partial assignment: they are listed under
    ``cut_units``. A completed search keeps the partial assignments as above.
    {covers: [{units, complete}], partial: [{units, complete}], cut_units: [units], cut, tried}."""
    cap = OBS_COVERS_CAP if cap is None else cap
    combos = OBS_COMBOS if combos is None else combos
    useful = [u for u in units if prov.get(u, set()) & cells]
    reach = cells & set().union(*(prov[u] for u in useful)) if useful else set()
    if not reach:
        return {"covers": [], "partial": [], "cut_units": [], "cut": False, "tried": 0}
    target = cells if reach == cells else reach
    complete = target == cells
    found: list[tuple[str, ...]] = [(u,) for u in useful if target <= prov[u]]
    rest = [u for u in useful if not target <= prov[u]]
    # Bit masks: a combination covers the target when the union of its cell masks is the target mask; it is not
    # minimal when the unit mask of a cover found earlier (always smaller, as k grows) is inside its own.
    cell_bit = {c: 1 << i for i, c in enumerate(target)}
    full = (1 << len(cell_bit)) - 1
    cell_mask = {u: sum(cell_bit[c] for c in prov[u] & target) for u in rest}
    unit_bit = {u: 1 << i for i, u in enumerate(rest)}
    found_masks: list[int] = []
    tried, cut = 0, len(found) > cap
    for k in range(2, min(len(rest), len(target)) + 1):
        if cut:
            break
        for combo in itertools.combinations(rest, k):
            tried += 1
            if tried > combos:
                cut = True
                break
            umask = 0
            for u in combo:
                umask |= unit_bit[u]
            if any(m & umask == m for m in found_masks):
                continue
            cmask = 0
            for u in combo:
                cmask |= cell_mask[u]
            if cmask == full:
                found.append(combo)
                found_masks.append(umask)
                if len(found) > cap:
                    cut = True
                    break
    found.sort(key=lambda c: (len(c), -round(sum(score.get(u, 0.0) for u in c), 6), [useful.index(u) for u in c]))
    dropped: list[tuple[str, ...]] = []
    if len(found) > cap:
        cut = True
        found, dropped = found[:cap], found[cap:]
    used = {u for c in found for u in c}
    # A stopped search leaves combinations unexplored: any unit outside a kept cover may be in one of them.
    in_cut = (set(useful) if cut else {u for c in dropped for u in c}) - used
    partial = [(u,) for u in useful if u not in used and u not in in_cut]
    return {"covers": [{"units": list(c), "complete": complete} for c in found],
            "partial": [{"units": list(c), "complete": False} for c in partial],
            "cut_units": [u for u in useful if u in in_cut], "cut": cut, "tried": tried}


# ---------------------------------------------------------------- unspecified dimensions


def signature(view) -> dict[str, frozenset]:
    """Basis-axis values a table carries, read from its values and its title (axis -> values)."""
    from .basis import values

    texts = [view.title] + [getattr(it, "name_raw", "") or "" for it in getattr(view, "items", [])]
    out = {}
    for axis in SIGNATURE_AXES:
        got = frozenset(v for text in texts for v in values(text, axis))
        if got:
            out[axis] = got
    return out


def bracket_words(name: str) -> dict[str, str]:
    """Bracket words of a table name, cut at commas, middle dots and spaces, with their class: basis (holds a basis
    word: read by ``signature``), frequency, period (a year range or a year), source, other. Pieces of digits and
    marks only ("2010=100") are kept (other unless they are a year range)."""
    out: dict[str, str] = {}
    for m in _BRACKET.finditer(name or ""):
        for piece in re.split(r"[,·]+", m.group(1)):
            if nospace(piece) in FREQUENCY_WORDS:  # a frequency phrase ("분기 및 연간") is one word
                out[nospace(piece)] = "frequency"
                continue
            for part in piece.split():
                _classify(nospace(part), out)
    return out


def _classify(word: str, out: dict[str, str]) -> None:
    if word:
        if any(f in word for f in _BASIS_FORMS):
            kind = "basis"
        elif word in FREQUENCY_WORDS:
            kind = "frequency"
        elif _YEAR_RANGE.fullmatch(word):
            kind = "period"
        elif word in SOURCE_WORDS:
            kind = "source"
        else:
            kind = "other"
        out[word] = kind


def _qualifiers(name: str) -> set[str]:
    """Non-basis bracket words of a table name, year ranges left out (keep_all's dimension test)."""
    return {w for w, k in bracket_words(name).items() if k not in ("basis", "period")}


def agency_values(view) -> frozenset:
    """Values of every axis whose name holds an agency or source word (출처, 기관, 작성 ...), whatever the axis
    name: the two tables are compared on the union."""
    objs = {obj for obj, name in getattr(view, "dims", []) if any(w in (name or "") for w in AGENCY_WORDS)}
    return frozenset(it.name_raw for it in getattr(view, "items", []) if getattr(it, "obj_id", None) in objs)


def _agency_diff(a, b) -> bool:
    return agency_values(a) != agency_values(b)


def _axis_values(view, indicator: bool) -> frozenset:
    """(axis name, value) pairs of the table: the non-indicator axes (``indicator`` False) or every axis."""
    roles = getattr(view, "axis_roles", {}) or {}
    names = dict(getattr(view, "dims", []))
    return frozenset((names.get(it.obj_id, it.obj_id), it.name_raw) for it in getattr(view, "items", [])
                     if (indicator or roles.get(it.obj_id) != "indicator")
                     and not any(f in nospace(it.name_raw) for f in _BASIS_FORMS))  # basis values: signature


def dimension_of(a, b) -> str | None:
    """What tells two family tables apart (``a``, ``b``: table views): the basis axes whose values differ, and
    "qualifier" when their non-basis bracket words or the values of an agency/source axis differ; None when they
    differ by year range (or nothing) only. (keep_all policy)"""
    sa, sb = signature(a), signature(b)
    axes = {x for x in set(sa) | set(sb) if sa.get(x, frozenset()) != sb.get(x, frozenset())}
    if _qualifiers(a.title) ^ _qualifiers(b.title) or _agency_diff(a, b):
        axes.add("qualifier")
    return "+".join(sorted(axes)) if axes else None


def _frequencies(view) -> frozenset:
    return frozenset(f for f, _, _ in view.periods)


def dimension_kinds(a, b) -> dict[str, list[str]]:
    """Kinds of the dimensions two family tables differ by (decision 5 as changed, research note section 2): kind
    -> what differs. A safe kind (period, source, frequency, survey) is given only when it is shown; whatever is not
    compared or not classified is ``other`` (confirm):
    - basis-axis values (``signature``): mixed_basis / survey / other by ``basis_axis_kinds``;
    - bracket words not on a basis axis, word by word: frequency, period (year ranges), source, else other;
    - agency/source axis values (whatever the axis names) and the values of the non-indicator axes: other;
    - frequency when only the frequencies differ;
    - source when the names, axes and all values are the same (only periods or the source differ);
    - anything else unexplained: other."""
    out: dict[str, list[str]] = {}
    sa, sb = signature(a), signature(b)
    for axis in sorted(set(sa) | set(sb)):
        if sa.get(axis, frozenset()) != sb.get(axis, frozenset()):
            out.setdefault(AXIS_KINDS.get(axis, "other"), []).append(axis)
    wa, wb = bracket_words(a.title), bracket_words(b.title)
    for word in sorted(set(wa) ^ set(wb)):
        kind = (wa.get(word) or wb.get(word))
        if kind != "basis":
            out.setdefault(kind, []).append(word)
    if _agency_diff(a, b):
        out.setdefault("other", []).append("agency_values")
    if _axis_values(a, False) != _axis_values(b, False):
        out.setdefault("other", []).append("axis_values")
    if _frequencies(a) != _frequencies(b):
        out.setdefault("frequency", []).append("frequencies")
    if not out:
        same = a.title == b.title and _axis_values(a, True) == _axis_values(b, True)
        out["source" if same else "other"] = [] if same else ["unexplained"]
    return out


def other_values(a, b) -> list[str]:
    """Basis values, bracket words, agency and non-indicator axis values ``b`` has and ``a`` lacks (the
    unspecified mark's other values)."""
    sa, sb = signature(a), signature(b)
    vals = sorted(v for axis, vs in sb.items() for v in vs - sa.get(axis, frozenset()))
    words = sorted(w for w, k in bracket_words(b.title).items() if k != "basis" and w not in bracket_words(a.title))
    agency = sorted(agency_values(b) - agency_values(a))
    axis = sorted(v for _, v in _axis_values(b, False) - _axis_values(a, False))[:8]
    return list(dict.fromkeys(vals + words + agency + axis))


def latest_group(view, family: str) -> str:
    """Group in which "the latest" is computed: the family and the basis values, bracket words (frequency words
    and year ranges left out), agency values and non-indicator axis values of the table, so that a sibling on
    another unset dimension keeps its own latest table."""
    sig = sorted((k, tuple(sorted(v))) for k, v in signature(view).items())
    words = sorted(w for w, k in bracket_words(view.title).items() if k not in ("frequency", "period"))
    return repr((family, sig, words, sorted(agency_values(view)), sorted(_axis_values(view, False))))


def _start(view) -> int:
    starts = [_months(s) for _, s, _ in view.periods if _months(s) is not None]
    return min(starts) if starts else 0


_STATUS_RANK = {"match": 2, "unknown": 1, None: 1, "conflict": 0}


def as_good(mate: dict[str, str], chosen: dict[str, str]) -> bool:
    """The mate stands on every basis word of the requirement at least as well as the chosen table (match >
    unknown > conflict): a word the chosen table matches and the mate does not means the question set that
    dimension."""
    return all(_STATUS_RANK.get(mate.get(cid), 1) >= _STATUS_RANK.get(s, 1) for cid, s in chosen.items())


def unspecified(chosen: list[str], family_tables: list[str], prov: dict[str, set], usable: set[str],
                views: dict, words: dict[str, dict[str, str]] | None = None) -> tuple[list[str], list[dict]]:
    """(tables, marks) under the keep_all policy: the chosen group with the tables put in for dimensions the
    question did not set, and the marks {dimension, table_ids, more} (module docstring). ``words``: table -> basis
    condition ID -> status over the requirement's own and shared basis words (ambiguous ones are not used)."""
    words = words or {}
    groups: dict[str, list[str]] = {}

    def put(dim: str, *tables: str) -> None:
        group = groups.setdefault(dim, [])
        group += [t for t in tables if t not in group]

    for c in chosen:
        for t in family_tables:
            if t in chosen or t not in usable or not prov.get(t) or not prov[c] & prov[t]:
                continue
            if not as_good(words.get(t, {}), words.get(c, {})):
                continue
            dim = dimension_of(views[c], views[t])
            if dim:
                put(dim, c, t)
    for a, b in itertools.combinations(chosen, 2):
        dim = dimension_of(views[a], views[b])
        if dim:
            put(dim, a, b)
    if not groups:
        return list(chosen), []

    def order_key(t: str) -> tuple:
        return (-_start(views[t]), family_tables.index(t) if t in family_tables else len(family_tables))

    # one cap over the requirement: every table in some unspecified dimension counts once
    dim = "+".join(sorted({a for d in groups for a in d.split("+")}))
    grouped = []
    for g in groups.values():
        grouped += [t for t in g if t not in grouped]
    ordered = sorted(grouped, key=order_key)
    kept = ordered[:UNSPECIFIED_CAP]
    mark = {"dimension": dim, "table_ids": kept, "more": len(ordered) > UNSPECIFIED_CAP}
    tables = [t for t in chosen if t not in grouped] + kept
    need = set().union(*(prov.get(c, set()) for c in chosen))
    for c in chosen:  # a chosen table the cover still needs stays, past the cap
        missing = need - set().union(*(prov.get(t, set()) for t in tables))
        if c not in tables and prov.get(c, set()) & missing:
            tables.append(c)
            mark["table_ids"].append(c)
    return tables, [mark]


def _end(view) -> int:
    ends = [_months(e, True) for _, _, e in view.periods if _months(e, True) is not None]
    return max(ends) if ends else 0


def _mark(dims: dict[str, list[str]], table_ids: list[str], others: list[str], values: list[str]) -> dict:
    kinds = sorted(k for k in dims if k in CONFIRM_KINDS)
    return {"dimension": "+".join(sorted({x for k in kinds for x in (dims[k] or [k])})), "kind": "+".join(kinds),
            "table_ids": list(table_ids), "other_table_ids": list(others), "other_values": sorted(set(values)),
            "more": False}


def _merge(into: dict[str, list[str]], more: dict[str, list[str]]) -> None:
    for k, v in more.items():
        into.setdefault(k, [])
        into[k] += [x for x in v if x not in into[k]]


def _convention(view) -> bool:
    return any(signature(view).get(axis) == frozenset(vals) for axis, vals in CONVENTIONS.items())


def representatives(chosen: list[str], members: dict[str, list[str]], prov: dict[str, set], usable: set[str],
                    views: dict, words: dict[str, dict[str, str]], score: dict[str, float], order: dict[str, int],
                    cells: set, siblings: set[str] | None = None,
                    named: set[str] | None = None,
                    log: list | None = None) -> tuple[list[str], list[dict], list[dict]]:
    """(tables, marks, conventions) under the representative policy (decision 5 as changed; research note 2).

    For each chosen table: its family mates that give some of its cells and stand on every basis word as well
    (``siblings``: also those whose group is stale for "the latest"; they are compared and marked, never picked).
    The dimensions are applied one after another: survey -> only tables with the convention value stay (marked as
    a convention); source -> the latest end (then the wider range); frequency -> the most requested cells; then the
    best by rank among what is left is the representative, and every mate that differs from it by a confirm kind
    (mixed_basis, other: also anything unclassified) is marked with it (confirm). A stand-in must give every cell
    of the table it replaces. A table the question named by its full name (``named``) is never replaced. Chosen
    tables that differ among themselves by a confirm kind are marked too. The result is cut back to a least cover
    of the requested cells. ``log`` (plan 11 R0, record only): receives every option a step left out and why
    (survey, score, source, frequency, cells)."""
    siblings = set(siblings or usable) | set(usable)
    named = named or set()
    tables: list[str] = []
    marks: list[dict] = []
    conventions: list[dict] = []

    def rank(t: str) -> tuple:
        return (score.get(t, 0.0), -order.get(t, 0))

    for c in chosen:
        mates = [t for t in members.get(c, []) if t != c and t not in chosen and t in siblings and prov.get(t)
                 and prov[c] & prov[t] and as_good(words.get(t, {}), words.get(c, {}))]
        if c in named:  # the question named this very table: its bracket words are set by the question
            if c not in tables:
                tables.append(c)
            continue
        pick = c
        options = [c, *[t for t in mates if t in usable and prov[t] >= prov[c]]]

        def kinds(x: str, y: str) -> set[str]:
            return set(dimension_kinds(views[x], views[y]))

        surveyed = list(options)
        if any("survey" in kinds(c, t) for t in options[1:]):
            usual = [t for t in options if _convention(views[t])]
            if usual:
                options = usual
                conventions.append({"table_id": None, "conventions": CONVENTIONS})
        pick = max(options, key=rank)
        same_source = [t for t in options if t == pick or kinds(pick, t) <= {"source"}]
        pick = max(same_source, key=lambda t: (_end(views[t]), _end(views[t]) - _start(views[t]), *rank(t)))
        same_freq = [t for t in options if t == pick or kinds(pick, t) <= {"frequency"}]
        pick = max(same_freq, key=lambda t: (len(prov[t] & cells), *rank(t)))
        if log is not None:
            for t in surveyed:
                if t == pick:
                    continue
                reason = ("survey" if t not in options else "frequency" if t in same_freq
                          else "source" if t in same_source else "score")
                log.append({"step": "representative", "table_ids": [t], "kept": [pick], "for": c,
                            "reason": reason, "marked": bool(set(dimension_kinds(views[pick], views[t]))
                                                             & CONFIRM_KINDS)})
        if conventions and conventions[-1]["table_id"] is None:
            conventions[-1]["table_id"] = pick
        dims: dict[str, list[str]] = {}
        others, values = [], []
        for t in [c, *mates]:
            if t == pick:
                continue
            k = dimension_kinds(views[pick], views[t])
            if set(k) & CONFIRM_KINDS:
                _merge(dims, {x: v for x, v in k.items() if x in CONFIRM_KINDS})
                others.append(t)
                values += other_values(views[pick], views[t])
        if others:
            marks.append(_mark(dims, [pick], others, values))
        if pick not in tables:
            tables.append(pick)
    for a, b in itertools.combinations(tables, 2):
        dims = dimension_kinds(views[a], views[b])
        if set(dims) & CONFIRM_KINDS and not any({a, b} <= set(m["table_ids"]) for m in marks):
            marks.append(_mark(dims, [a, b], [], other_values(views[a], views[b]) + other_values(views[b], views[a])))
    reach = cells & set().union(*(prov.get(t, set()) for t in tables)) if tables else set()
    got = best_cover(tables, reach, prov, {t: score.get(t, 0.0) for t in tables},
                     {t: order.get(t, 0) for t in tables}) if reach else {"tables": []}
    if got["tables"]:
        if log is not None:
            log.extend({"step": "representative_cover", "table_ids": [t], "kept": list(got["tables"]),
                        "reason": "cells"} for t in tables if t not in got["tables"])
        tables = [t for t in tables if t in got["tables"]]
    for m in marks:
        m["table_ids"] = [t for t in m["table_ids"] if t in tables]
    return tables, [m for m in marks if m["table_ids"]], [c for c in conventions if c["table_id"] in tables]


# ---------------------------------------------------------------- 3.5


def _families(layer, tables) -> dict[str, str]:
    return {t: family_key(layer, t) for t in tables}


def cover_cells(analysis: dict, rid: str, pool: list[dict], evidence: dict, layer, as_of: str | None) -> dict:
    """Family alternatives of one requirement (before 3.6 picks between families).

    Returns {requirement, cells, flags, scores, provided, alternatives: [{family, tables, covered, complete,
    truncated, best_score, score_sum, tier}], usable, views, order, words}; ``usable`` = candidates with evidence
    that give at least one cell."""
    sel = selection_catalog(layer)
    checks = _checks_by_table(evidence, rid)
    question_ns = title_bonus_words(analysis, rid)
    order = {p["table_id"]: p["rank"] for p in pool}
    views = {p["table_id"]: sel.views[p["table_id"]] for p in pool if p["table_id"] in sel.views}
    counted, _ = scope(analysis, rid)
    score = {t: rank_score(p, checks.get(t, []), question_ns, views.get(t)) for p in pool
             for t in [p["table_id"]] if t in views}
    words = {t: {c["detail"]["condition_id"]: c["source_status"] for c in checks.get(t, [])
                 if c["name"] == "basis" and c["detail"]["condition_id"] in counted} for t in views}
    # plan 10 D: where each basis word was found on the table (title, item, path ...): the set-dimension filter
    # counts a word as met only when the table name says it
    word_sources = {t: {c["detail"]["condition_id"]: c["detail"].get("source") for c in checks.get(t, [])
                        if c["name"] == "basis" and c["detail"]["condition_id"] in counted} for t in views}
    with_evidence = [p["table_id"] for p in pool
                     if p["table_id"] in views and has_evidence(p, checks.get(p["table_id"], []))]
    # plan 07 r5 Q3: the score items of every candidate, recorded with their reasons and added to its rank score
    qwords = question_words(analysis, rid)
    items = {t: score_items(views[t], qwords, [views[o] for o in with_evidence if o != t]) for t in views}
    for t, rows in items.items():
        if t in score and rows:
            score[t] = round(score[t] + sum(x["value"] for x in rows), 6)
    families = _families(layer, views)
    req = requested_cells(analysis, rid, {t: views[t] for t in with_evidence}, as_of)
    cells = all_cells(req)
    fit = {t: views[t] for t in with_evidence if eligible(layer, views[t], checks.get(t, []), req)}
    lgroups = {t: latest_group(views[t], families[t]) for t in views}
    latest = latest_ends(fit, req, lgroups)
    prov = {t: provided_cells(layer, views[t], checks.get(t, []), req, latest, lgroups[t]) for t in views}
    siblings = [t for t in with_evidence if prov[t]]
    usable = [t for t in siblings if fresh(views[t], req, latest)]
    ind = (requirement_of(analysis, rid).get("indicator") or {})
    same_name = [t for t in views if ind.get("path") == "exact" and ind.get("name")
                 and index_key(views[t].title) == index_key(ind["name"])]
    # the question named one table by its full name, bracket words included: those words are set by the question
    named = set(same_name) if len(same_name) == 1 and bracket_words(views[same_name[0]].title) else set()
    groups: dict[str, list[str]] = {}
    for p in pool:
        if p["table_id"] in usable:
            groups.setdefault(p["family"], []).append(p["table_id"])
    alternatives = []
    pruned: list[dict] = []  # plan 11 R0: what the least-cover search left out per family (record only)
    covers_all: dict[str, dict] = {}  # plan 11 R0: every minimal complete cover per family (record only)
    for fam, tables in groups.items():
        log: list[dict] = []
        got = best_cover(tables, cells, prov, score, order, log)
        pruned += [{**x, "family": fam} for x in log]
        covers_all[fam] = all_covers(tables, cells, prov, score)
        if got["tables"]:
            alternatives.append({"family": fam, **got})
    if alternatives and not any(a["complete"] for a in alternatives):
        mixed = best_cover(usable, cells, prov, score, order)
        covers_all["mixed"] = all_covers(usable, cells, prov, score)
        if mixed["tables"] and len({families[t] for t in mixed["tables"]}) > 1:
            alternatives.append({"family": "mixed", **mixed})
    for a in alternatives:
        a["best_score"] = max(score[t] for t in a["tables"])
        a["score_sum"] = round(sum(score[t] for t in a["tables"]), 6)
        results = [indicator_result(checks.get(t, [])) for t in a["tables"]]
        a["tier"] = max(INDICATOR_VALUE.get(r, 0.5 if r is None else 0.0) for r in results)
    from . import model_refs
    if model_refs.adopted(analysis):
        supported = {t: condition_supported_cells(t, checks.get(t, []), req, prov[t] & cells) for t in views}
        for a in alternatives:
            a["condition_supported"] = set().union(*(supported[t] for t in a["tables"]))
    return {"requirement": req, "cells": cells, "flags": list(req["flags"]), "scores": score, "provided": prov,
            "alternatives": alternatives, "usable": usable, "siblings": siblings, "named": named, "views": views,
            "order": order, "words": words, "word_sources": word_sources, "family_of": families,
            "score_items": items, "covers_all": covers_all, "pruned": pruned}


def order_alternatives(alternatives: list[dict]) -> list[dict]:
    """Complete first, more covered cells, then fewer tables, rank-score sum and evidence tier.

    Adopted model readings first prefer more condition-supported cells before reducing
    table count. This orders possible covers; it does not assert a proven allocation.
    Rules alternatives have no support record and retain their original order.
    """
    return sorted(alternatives, key=lambda a: (not a["complete"], -len(a["covered"]),
                                             -len(a.get("condition_supported", a["covered"])), len(a["tables"]),
                                               -a.get("score_sum", a["best_score"]), -a["tier"], a["family"]))


def family_members(layer, family: str) -> list[str]:
    if family.startswith("table:"):
        return [family.split(":", 1)[1]]
    return list(relations_of(layer).family_tables.get(family, []))


# ---------------------------------------------------------------- 3.7 recheck


def covers_of(cells: set, req: dict) -> dict:
    """covers{targets, periods, frequencies, basis} of a set of cells."""
    return {"targets": sorted({c[0] for c in cells if c[0] is not None}),
            "periods": [p[0] for p in req["periods"] if any(c[2] == p[0] for c in cells)],
            "frequencies": sorted({c[1] for c in cells if c[1] is not None}),
            "basis": sorted({cid for c in cells for cid in c[3]})}


PERIOD_CHECK_FLAGS = ("period_unknown", "period_lag")
_FLOOR = ("no_table", "confirm", "auto")  # worse to better


def period_check(recheck: dict | None) -> bool:
    """Plan 07 r5 Q8: the requested period is unknown, runs into the snapshot lag or is not covered by a chosen table:
    the table stays, and B checks the period (a mark handed on, never a reason to change the table)."""
    if not recheck:
        return False
    return any(f in (recheck.get("flags") or []) for f in PERIOD_CHECK_FLAGS) \
        or any(u["check"] == "period" for u in recheck.get("unproven") or [])


def verify_selection(analysis: dict, selection: dict, layer, as_of: str | None = None) -> dict:
    """Plan 07 r5 Q6: the one recheck of a chosen table group, after the choice (V2) and again right before B looks
    the data up. The tables never change: the state is only lowered to the floor of ``verify_choice``.
    {state, recheck, period_check}."""
    current = selection.get("status")
    tables = [t["table_id"] for t in selection.get("tables") or []]
    if not tables or current == "no_table":
        return {"state": "no_table", "recheck": None, "period_check": False}
    if ((selection.get("signals") or {}).get("plan17") or {}).get("mode") == "refutation":
        from .execution import verify

        return verify(analysis, selection, layer, as_of)
    got = verify_choice(analysis, selection["requirement_id"], tables, layer, as_of)
    state = current if current not in _FLOOR or _FLOOR.index(got["floor"]) >= _FLOOR.index(current) else got["floor"]
    return {"state": state, "recheck": got, "period_check": period_check(got)}


def verify_choice(analysis: dict, rid: str, table_ids: list[str], layer, as_of: str | None,
                  candidates: list[str] | None = None) -> dict:
    """3.7 (plan 04, 4-5) as a pure function: the chosen tables go through the evidence checks again and the
    requested cells are covered again. Tables covering nothing are dropped. ``candidates``: the tables the period
    segments and "the latest" were measured against (default: the chosen ones).

    Returns {tables: [{table_id, covers}], dropped, covered, cells, uncovered, unproven, floor, flags}; ``floor`` is
    the highest state the cover allows: auto (every cell covered), confirm (some), no_table (none); ``unproven``
    lists the own or shared conditions a kept table does not pass (``table_unproven``). V2 (verify/checks.py) can
    call this with the selections to fill its evidence recheck."""
    sel = selection_catalog(layer)
    inp = EvidenceInput(analysis, layer, as_of)
    pool = [t for t in dict.fromkeys(candidates or table_ids) if t in sel.views]
    views = {t: sel.views[t] for t in pool}
    checks = {t: check_pair(inp, rid, t)[0] for t in dict.fromkeys([*table_ids, *pool]) if t in sel.views}
    families = _families(layer, checks)
    req = requested_cells(analysis, rid, views, as_of)
    cells = all_cells(req)
    fit = {t: views[t] for t in pool if eligible(layer, views[t], checks[t], req)}
    lgroups = {t: latest_group(sel.views[t], families[t]) for t in checks}
    latest = latest_ends(fit, req, lgroups)
    stale = {t for t in table_ids if t in sel.views and not fresh(sel.views[t], req, latest)}
    out, dropped, covered = [], [], set()
    for t in table_ids:
        if t not in sel.views:
            dropped.append(t)
            continue
        got = provided_cells(layer, sel.views[t], checks[t], req, latest, lgroups[t]) & cells
        if not got or t in stale:
            dropped.append(t)
            continue
        covered |= got
        out.append({"table_id": t, "covers": covers_of(got, req)})
    complete = covered == cells and bool(cells)
    unproven = [u for t in out for u in table_unproven(t["table_id"], checks[t["table_id"]], req, complete)]
    floor = "auto" if complete else "confirm" if covered else "no_table"
    return {"tables": out, "dropped": dropped, "covered": len(covered), "cells": len(cells),
            "uncovered": len(cells - covered), "unproven": unproven, "floor": floor, "flags": list(req["flags"])}
