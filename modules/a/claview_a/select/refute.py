"""S4 refutation (plan 11 r4, 4-1 as changed by 13 and 14; R1b observation path): the only place that decides
which candidate tables and which of their cells are removed.

A removal is made only from a table-side fact (``select/facts.py``) against a settled interpretation value
(``select/needs.py``), and every removal carries a certificate:

    {code, requirement_key, condition_id, fragment_id, cell, opposite_fact, catalog_version, scope, table_id}

``scope`` is ``table`` (the table leaves every assignment) or ``cell`` (only that cell of the table). Codes switched
on in R1b (``data/decision_policy.json`` ``refutations``):

- ``indicator_absent`` (table): the indicator name, mapped for sure (C2) and settled, occurs in no way on the
  table (K1: no exact, qualified, derived or partial occurrence, no word of it found) and the table surface is
  complete for the indicator (C2, K14).
- ``target_absent`` (cell): a named target, mapped for sure and settled, is held neither as a value nor in the
  table name, and no part of it occurs on the table (the K1 rule for every name), the surface being complete for
  targets.
- ``lower_item_absent`` (cell): a named lower item (breakdown), settled, is not found on the table (the breakdown
  check) and no part of it occurs, the surface being complete for the indicator side.
- ``condition_conflict`` (table, or cell for a basis axis split into cells, a frequency or a period segment): a
  settled own or shared positive condition whose opposite alone the table holds: opposite basis (title or values,
  currency included), unit of another base, none of the requested frequencies, the calendar period outside the
  table's coverage (a segment past the table end within the snapshot lag is no refutation: a gap).
- ``forbidden_only`` (table): a settled negative basis word or exclusion whose forbidden value is all the table
  holds on a value axis (C6: never from the table name alone).
- ``negated_name_only`` (table): the indicator stands on the table only through words the judgement negated
  (no exact, qualified or derived occurrence; every partial occurrence holds a negated word), the negation and the
  indicator being settled.
- ``cells_impossible`` (cell): two requested parts of a cell sit on two branches of one physical axis (K4), every
  part settled.
Plan 11 R1c: a basis conflict read from the title (bracket qualifiers included) is a catalog fact and refutes
only when the requested value occurs nowhere on the table (``basis_conflict``; both values present, or no basis word
in the title, is no refutation); ``indicator_mention_only`` (table): the indicator name, mapped for sure and
settled, stands on the table only inside other, longer names (no exact, qualified or derived occurrence, no word of
it missing: ``facts`` ``mention_only``), the surface being complete for the indicator (implemented, switched off
until decided). The indicator relation
(subject / component) is no refutation: it is a decision dimension (``decision.py``).
Not refutations (they stay as gaps): partial indicator, undetermined relation, unknown verdicts, a title-only
negation (forbidden_present), an unnamed target, undetermined cell combination, unknown period or lag.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .. import paths
from ..catalog.text import index_key
from ..reused import q_rules
from .evidence import FREQ_MONTHS, LAG_PERIODS
from .facts import STRONG_METHODS

CODES = ("indicator_absent", "target_absent", "lower_item_absent", "condition_conflict", "forbidden_only",
         "negated_name_only", "cells_impossible", "indicator_mention_only")
LATEST = "latest"


@lru_cache(maxsize=1)
def switches() -> dict[str, bool]:
    policy = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))
    return {k: bool(v) for k, v in (policy.get("refutations") or {}).items() if not k.startswith("_")}


def on(code: str) -> bool:
    return switches().get(code, False)


def cell_key(cell: tuple) -> str:
    """A requested cell (target, frequency, period label, basis IDs) as one string."""
    target, freq, period, basis = cell
    return json.dumps([target, freq, period, list(basis)], ensure_ascii=False)


def certificate(code: str, needs_r: dict, table_id: str, scope: str, opposite_fact: dict, version: str,
                condition_id: str | None = None, fragment_id: str | None = None, cell: str | None = None) -> dict:
    return {"code": code, "requirement_key": needs_r["identity"]["key"], "condition_id": condition_id,
            "fragment_id": fragment_id, "cell": cell, "opposite_fact": opposite_fact, "catalog_version": version,
            "scope": scope, "table_id": table_id}


def is_certificate(c: dict) -> bool:
    """A certificate has every field, a code of this module and a table, cell scope with its cell."""
    keys = ("code", "requirement_key", "condition_id", "fragment_id", "cell", "opposite_fact", "catalog_version",
            "scope", "table_id")
    return (isinstance(c, dict) and all(k in c for k in keys) and c["code"] in CODES and bool(c["table_id"])
            and c["scope"] in ("table", "cell") and (c["scope"] == "table" or bool(c["cell"]))
            and isinstance(c["opposite_fact"], dict) and bool(c["requirement_key"]))


# ---------------------------------------------------------------- table scope


def _cond_row(f: dict, check: str, cid: str) -> dict | None:
    return next((r for r in f.get("conditions") or [] if r["check"] == check and r["condition_id"] == cid), None)


def basis_conflict(row: dict | None) -> dict | None:
    """The opposite fact of a positive basis word judged ``conflict`` (R1c), or None when it does not refute: a
    title (bracket qualifier) conflict refutes only when the requested value occurs nowhere on the table
    (``facts.basis_value_facts``: both values present is no refutation); a value-axis conflict as the check gave
    it. A record made before R1c (no ``basis`` field) is read as before."""
    if not row or row["status"] != "conflict":
        return None
    fact = {"check": "basis", "status": "conflict", "source": row["source"]}
    if "basis" not in row:
        return fact
    b = row.get("basis") or {}
    if row["source"] == "title":
        if not b.get("title_conflict"):
            return None
        return {**fact, "opposite_in_title": b.get("opposite_in_title") or []}
    return None if b.get("requested_in") else fact


def split_basis(needs_r: dict) -> set[str]:
    """Positive basis conditions on an axis with two or more values: split into cells (``requested_cells``)."""
    by_axis: dict[str, list[str]] = {}
    for c in needs_r["evidence"]["conditions"]:
        if c["kind"] == "basis" and c["polarity"] == "+" and not c["byeol"]:
            by_axis.setdefault(c["axis"] or "", []).append(c["condition_id"])
    return {cid for ids in by_axis.values() if len(ids) >= 2 for cid in ids}


def _negated_only(f: dict, view, negated: list[str]) -> dict | None:
    keys = [index_key(w) for w in negated if len(index_key(w)) >= 2]
    ind = f.get("indicator") or {}
    if not keys or not ind.get("name") or view is None:
        return None
    occ = ind.get("occurrences") or []
    if any(o["method"] in STRONG_METHODS for o in occ) or (ind.get("check") or {}).get("result") == "match":
        return None
    title = index_key(view.title)
    texts = [index_key(o.get("name") or "") if o["field"] == "value" else title for o in occ]
    if occ and not all(any(k in t for k in keys) for t in texts):
        return None
    found = [w for w, k in zip(negated, keys) if k in title or any(k in index_key(it.name_raw or "")
                                                                   for it in view.items)]
    if not found:
        return None
    return {"negated_words_on_table": found, "indicator_occurrences": len(occ)}


def refute_tables(needs_r: dict, facts_tables: dict[str, dict], views: dict, version: str) -> dict[str, list[dict]]:
    """Table-scope certificates: table ID -> certificates (only refuted tables are listed)."""
    out: dict[str, list[dict]] = {}
    ev = needs_r["evidence"]
    ind = ev["indicator"]
    split = split_basis(needs_r)
    conds = {c["condition_id"]: c for c in ev["conditions"]}
    periods = [c for c in ev["conditions"] if c["kind"] == "period"]
    for t, f in facts_tables.items():
        certs: list[dict] = []

        def add(code, fact, **kw):
            if on(code):
                certs.append(certificate(code, needs_r, t, "table", fact, version, **kw))

        surface = f.get("surface") or {}
        fi = f.get("indicator") or {}
        if ind and ind["settled"] and ind["mapped"] and fi.get("absent") is True \
                and (surface.get("indicator") or {}).get("complete"):
            add("indicator_absent", {"indicator": fi.get("name"), "occurrences": 0, "partial": 0,
                                     "word_check": (fi.get("check") or {}).get("result")},
                fragment_id=ind["fragment_id"])
        elif ind and ind["settled"] and ind["mapped"] and fi.get("mention_only") is True \
                and (surface.get("indicator") or {}).get("complete"):
            add("indicator_mention_only", {"indicator": fi.get("name"), "strong_occurrences": 0,
                                           "inside_other_names": fi.get("partial_count")},
                fragment_id=ind["fragment_id"])
        for cid, c in conds.items():
            if not c["settled"]:
                continue
            if c["kind"] == "basis" and c["polarity"] == "+" and not c["byeol"] and cid not in split:
                fact = basis_conflict(_cond_row(f, "basis", cid))
                if fact is not None:
                    add("condition_conflict", fact, condition_id=cid)
            elif c["kind"] == "unit":
                row = _cond_row(f, "unit", cid)
                if row and row["status"] == "conflict":
                    add("condition_conflict", {"check": "unit", "status": "differ"}, condition_id=cid)
            elif c["kind"] == "frequency" and (f.get("frequency") or {}).get("status") == "conflict":
                add("condition_conflict", {"check": "frequency", "status": "conflict"}, condition_id=cid)
        if periods and all(c["settled"] for c in periods) \
                and ((f.get("period") or {}).get("table") or {}).get("status") == "outside":
            add("condition_conflict", {"check": "period", "status": "outside",
                                       "by_frequency": f["period"]["table"]["by_frequency"]},
                condition_id=periods[0]["condition_id"])
        for row in f.get("conditions") or []:
            cid = row["condition_id"]
            c = conds.get(cid)
            if c is not None and c["settled"] and row["negation"] == "forbidden_only" \
                    and (c["kind"] == "exclude" or c["polarity"] == "-"):
                add("forbidden_only", {"check": row["check"], "negation": "forbidden_only"}, condition_id=cid)
        flt = needs_r["filter"]
        if ind and ind["settled"] and flt["negated"] and flt["negated_settled"] \
                and (surface.get("indicator") or {}).get("complete"):
            fact = _negated_only(f, views.get(t), flt["negated"])
            if fact is not None:
                add("negated_name_only", fact,
                    fragment_id=(flt["negated_fragment_ids"] or [None])[0])
        if certs:
            out[t] = certs
    return out


# ---------------------------------------------------------------- cell scope


def _rows(view, f: str | None) -> list[tuple[int, int]]:
    out = []
    for g, s, e in view.periods:
        if f is not None and g != f:
            continue
        lo, hi = q_rules.stamp_months(s), q_rules.stamp_months(e)
        if lo and hi:
            out.append((lo[0], hi[1], g))
    return out


def _segment_refuted(view, f: str | None, lo: int, hi: int) -> dict | None:
    """The table holds no data for the period segment [lo, hi] (frequency ``f``, all when None), and the segment
    does not start within the snapshot lag after the table's end (that is unknown, a gap)."""
    rows = _rows(view, f)
    if not rows:
        return None  # the frequency is judged on its own
    if any(a <= lo and hi <= b for a, b, _ in rows):
        return None
    for a, b, g in rows:
        if lo > b and lo - b <= FREQ_MONTHS.get(g, 12) * LAG_PERIODS:
            return None
    return {"check": "period", "segment": [lo, hi], "coverage": [[a, b, g] for a, b, g in rows]}


def _part_matches(part: str, cell: tuple, split: set[str]) -> bool:
    target, _f, _p, basis = cell
    if part == "indicator":
        return True
    kind, _, name = part.partition(":")
    if kind == "target":
        return target == name
    if kind == "basis":
        return name not in split or name in basis
    return False


def _part_settled(part: str, needs_r: dict) -> bool:
    ev = needs_r["evidence"]
    kind, _, name = part.partition(":")
    if part == "indicator":
        return bool(ev["indicator"] and ev["indicator"]["settled"])
    if kind == "target":
        t = next((x for x in ev["targets"] if x["name"] == name), None)
        if t is not None:
            return t["settled"]
        low = next((x for x in ev["lower_items"] if name in (x["text"], x["word"], x["name"])), None)
        return bool(low and low["settled"])
    if kind == "basis":
        c = next((x for x in ev["conditions"] if x["condition_id"] == name), None)
        return bool(c and c["settled"])
    return False


def refute_cells(needs_r: dict, facts_tables: dict[str, dict], views: dict, req: dict, cells: set,
                 tables: list[str], version: str, breakdown_keys: dict[str, str] | None = None
                 ) -> dict[str, dict[str, list[dict]]]:
    """Cell-scope certificates of the tables left after the table scope: table ID -> cell key -> certificates.
    ``req``: the requested-cell dimensions (``cover.requested_cells``); ``breakdown_keys``: breakdown condition ID
    -> the cell target key of its listed value."""
    out: dict[str, dict[str, list[dict]]] = {}
    ev = needs_r["evidence"]
    split = split_basis(needs_r)
    conds = {c["condition_id"]: c for c in ev["conditions"]}
    freq_settled = all(c["settled"] for c in ev["conditions"] if c["kind"] == "frequency")
    periods = [c for c in ev["conditions"] if c["kind"] == "period"]
    period_settled = bool(periods) and all(c["settled"] for c in periods)
    bounds = {label: (lo, hi) for label, lo, hi in req["periods"]}
    targets = {t["name"]: t for t in ev["targets"]}
    lowers = {}
    for x in ev["lower_items"]:
        key = (breakdown_keys or {}).get(x["condition_id"]) if x["condition_id"] else None
        if key:
            lowers[key] = x
    for t in tables:
        f = facts_tables.get(t) or {}
        view = views.get(t)
        if view is None:
            continue
        surface = f.get("surface") or {}
        # absent: neither held (exact) nor occurring in part (the absence rule of K1 for every name)
        present = {x["name"]: x["present"] or x.get("partial", True) for x in f.get("targets") or []}
        found = {x.get("text"): x for x in f.get("lower_items") or []}
        have_freqs = {g for g, _s, _e in view.periods}
        got: dict[str, list[dict]] = {}

        def add(cell, code, fact, **kw):
            if on(code):
                k = cell_key(cell)
                got.setdefault(k, []).append(certificate(code, needs_r, t, "cell", fact, version, cell=k, **kw))

        impossible = [p["parts"] for p in (f.get("cells") or {}).get("pairs") or [] if p["verdict"] == "impossible"]
        for cell in sorted(cells, key=cell_key):
            target, freq, period, basis = cell
            if target is not None:
                tg = targets.get(target)
                low = lowers.get(target)
                if tg and tg["named"] and tg["settled"] and tg["mapped"] and present.get(target) is False \
                        and (surface.get("target") or {}).get("complete"):
                    add(cell, "target_absent", {"target": target, "present": False}, fragment_id=tg["fragment_id"])
                if low and low["named"] and low["settled"] and (surface.get("indicator") or {}).get("complete"):
                    row = found.get(low["text"])
                    if row is not None and row.get("found") is False and row.get("partial") is False:
                        add(cell, "lower_item_absent", {"lower_item": low["name"], "found": False},
                            condition_id=low["condition_id"], fragment_id=low["fragment_id"])
            for cid in basis:
                c = conds.get(cid)
                fact = basis_conflict(_cond_row(f, "basis", cid))
                if c and c["settled"] and fact is not None:
                    add(cell, "condition_conflict", fact, condition_id=cid)
            if freq is not None and freq_settled and freq not in have_freqs:
                add(cell, "condition_conflict", {"check": "frequency", "requested": freq,
                                                 "available": sorted(have_freqs)},
                    condition_id=next((c["condition_id"] for c in ev["conditions"] if c["kind"] == "frequency"), None))
            if period != LATEST and period_settled and (freq is None or freq in have_freqs):
                lo, hi = bounds[period]
                fact = _segment_refuted(view, freq, lo, hi)
                if fact is not None:
                    add(cell, "condition_conflict", fact, condition_id=periods[0]["condition_id"])
            for x, y in impossible:
                if _part_matches(x, cell, split) and _part_matches(y, cell, split) \
                        and _part_settled(x, needs_r) and _part_settled(y, needs_r):
                    add(cell, "cells_impossible", {"parts": [x, y], "verdict": "impossible"})
        if got:
            out[t] = got
    return out
