"""Standard and scoring of the independent evaluation (plan 38; users 2026-10-28 16:23 and 18:00 UTC).

The independent questions are written apart from the development history, and their answers are confirmed by the
user before any model output is seen. Their standard is kept per requirement target (one requested statistic), not
squeezed into the per-question form of ``standard.py``: a question may hold several statistics, several ask axes,
several tables per ask value, and several complete ways (combinations) of serving a statistic's period.

- ``build`` copies the confirmed answers into the standard without loss. It never decides an equivalence or an
  answer, and never repairs one: a missing or malformed field, a target text not found at its place in the question,
  a combination with overlapping months, two combinations that are the same, a part outside the catalog coverage, or
  a joint combination naming an unknown one refuse the build.
- A target's final selection is a list of **combinations**. Between combinations the rule is "or"; inside one, every
  part is needed. A part is a table, a frequency and either a month range or a list of months (points). A statistic's
  final rows are right only when, after a repeated-month check, their (table, frequency, month) set equals one
  confirmed combination exactly; parts of different combinations are never merged into one allowed list.
- ``score_case`` scores one run record (``score.adapt``) against one case. The scorer's own fact check and code's
  handling are ``score.fact_checks`` as for the other standards. Model statistics are placed on targets by where
  their quotes sit in the question, never by tables. Months are joined only inside one statistic.
- A hold fixed in the confirmed answers makes only that item ``held`` (outside the denominator): a target item
  (``holds``), a held combination, or a representative candidate whose equivalence is unconfirmed
  (``representative_held``). An output that matches only a held alternative is held for that item; any other
  mismatch is wrong. Errors found after a run are never turned into holds.
- Boundary questions (``expected: missing``) are scored apart: right only for a valid output that names the
  statistic as missing and hands off no table and asks nothing; run and contract failures are counted apart.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

from tl_eval import score as S

INDEP_STANDARD_VERSION = "tl-indep-standard-2026-10-29.2"  # .2: combinations, held alternatives
INDEP_SCORE_VERSION = "tl-indep-score-2026-10-29.2"
EXPECTED = ("select", "ask", "missing")
TYPES = ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "B0")
FREQUENCIES = ("Y", "H", "Q", "M")
HOLD_ITEMS = ("representative", "ask", "final", "point_in_period")
COMBINATION_STATUS = ("confirmed", "held")
ROW_KEYS = {"qid", "type", "question", "expected", "targets", "holds_why", "joint_combinations"}
TARGET_KEYS = {"id", "text", "occurrence", "representative", "equivalents", "representative_held", "ask", "final",
               "holds", "out_of_scope"}
FINAL_KEYS = {"frequency", "basis", "combinations", "point_in_period", "relative"}
COMBINATION_KEYS = {"id", "status", "parts", "why"}
PART_KEYS = {"table", "frequency", "from", "to", "points"}
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
TABLE = re.compile(r"DT_\w+")
PERIOD_POLICY = {
    "as_of": "fixed per standard; never moved to the run date",
    "calendar_words": "올해·작년·지난해·재작년: calendar years of as_of",
    "latest_words": "최근 N년·N개월·최신·지금까지: counted back from the table's latest data, stated per target",
    "no_data": "a requested period without data is never replaced by another period",
}
METRICS = {
    "run_verified": "the record carries its run versions and the standard's as_of",
    "pool_hit": "every target has an allowed table (representative, equivalent, held candidate, combination or ask "
                "value table) in the candidate pool; a miss is retrieval_miss, apart from a statistic missing from "
                "the catalog",
    "contract_ok": "score.fact_checks: every fact of the model output holds by the scorer's own check",
    "evidence_contract_ok": "score.fact_checks: every card evidence is a fact of the chosen table's card",
    "code_checks_right": "score.fact_checks: code handling of fact errors only",
    "model_meaning_right": "every target: its covering statistics' representatives lie in the target's "
                           "representative tables or their confirmed equivalents (held when one is only a held "
                           "candidate; an ask target: the model's ask per axis and value); a target no statistic "
                           "covers is missing",
    "ask_right": "every target: asked exactly on the confirmed axes with every value once and each option table "
                 "allowed for its value, or not asked when no ask is confirmed",
    "table_match": "every target: each covering statistic's final table set equals the table set of a confirmed "
                   "combination (held when it equals only a held one)",
    "final_full": "every target: each covering statistic's (table, frequency, month) set equals one confirmed "
                  "combination exactly, with no repeated month, the basis and the catalog coverage; a joint "
                  "combination list, when given, is met; an uncovered statistic handing off a table allowed for no "
                  "target is wrong",
    "completed": "final_full right, no fact error, no card evidence left unverified, routed handoff or ask",
    "point_in_period": "recorded only (not in completed): the confirmed end point against the record's "
                       "frequency_quotes",
    "boundary": "boundary questions only: right / wrong (a table handed off or an ask) / run_or_contract_error / "
                "review (no table, no ask, but no statistic named missing)",
}


class StandardError(ValueError):
    """The confirmed answers cannot be copied into a standard as they are."""


# ---------------------------------------------------------------- combinations


def _part_months(part: dict) -> list[int]:
    if part.get("points") is not None:
        return [S._months(p, p)[0] for p in part["points"]]
    return S._months(part["from"], part["to"])


def combination_cells(combo: dict) -> set[tuple[str, str, int]]:
    """(table, frequency, month) of every part of one combination."""
    return {(p["table"], p["frequency"], m) for p in combo["parts"] for m in _part_months(p)}


def _check_combinations(tid: str, fin: dict, known, coverage: dict | None) -> list[str]:
    bad = []
    combos = fin.get("combinations")
    if not isinstance(combos, list):
        return [f"{tid}: combinations missing"]
    ids = [c.get("id") for c in combos]
    if len(set(ids)) != len(ids) or not all(ids):
        bad.append(f"{tid}: combination ids missing or repeated")
    seen = []
    for c in combos:
        cid = f"{tid}.{c.get('id')}"
        bad += [f"{cid}: unknown field {k}" for k in sorted(set(c) - COMBINATION_KEYS)]
        if c.get("status") not in COMBINATION_STATUS:
            bad.append(f"{cid}: status {c.get('status')!r}")
        if not c.get("parts"):
            bad.append(f"{cid}: no parts")
            continue
        ok = True
        for p in c["parts"]:
            bad += [f"{cid}: unknown part field {k}" for k in sorted(set(p) - PART_KEYS)]
            known(p.get("table"), cid)
            if p.get("frequency") not in FREQUENCIES:
                bad.append(f"{cid}: part frequency {p.get('frequency')!r}")
                ok = False
            has_range, has_points = "from" in p or "to" in p, "points" in p
            if has_range == has_points:
                bad.append(f"{cid}: a part has either from/to or points")
                ok = False
            elif has_points:
                pts = p["points"]
                if not pts or not all(MONTH.fullmatch(str(x)) for x in pts) or pts != sorted(set(pts)):
                    bad.append(f"{cid}: points {pts!r}")
                    ok = False
            elif not (MONTH.fullmatch(str(p.get("from"))) and MONTH.fullmatch(str(p.get("to")))) or p["from"] > p["to"]:
                bad.append(f"{cid}: part range {p.get('from')!r}~{p.get('to')!r}")
                ok = False
        if not ok:
            continue
        cells = [(p["frequency"], m) for p in c["parts"] for m in _part_months(p)]
        if len(cells) != len(set(cells)):
            bad.append(f"{cid}: months overlap inside the combination")
        if coverage is not None:
            for p in c["parts"]:
                cov = (coverage.get(p["table"]) or {}).get(p["frequency"])
                months = _part_months(p)
                if cov and (min(months) < cov[0] or max(months) > cov[1]):
                    bad.append(f"{cid}: {p['table']} {p['frequency']} outside its catalog coverage")
        cell_set = combination_cells(c)
        if cell_set in seen:
            bad.append(f"{cid}: the same as another combination")
        seen.append(cell_set)
    return bad


# ---------------------------------------------------------------- build


def _spans(question: str, text: str) -> list[tuple[int, int]]:
    return S._spans(question, text)


def _check_row(row: dict, tables: set[str] | None, coverage: dict | None) -> list[str]:
    qid = row.get("qid", "?")
    bad = [f"{qid}: unknown field {k}" for k in sorted(set(row) - ROW_KEYS)]
    bad += [f"{qid}: missing field {k}" for k in sorted({"qid", "type", "question", "expected", "targets"} - set(row))]
    if bad:
        return bad
    if row["type"] not in TYPES:
        bad.append(f"{qid}: type {row['type']!r}")
    if row["expected"] not in EXPECTED:
        bad.append(f"{qid}: expected {row['expected']!r}")
    if (row["type"] == "B0") != (row["expected"] == "missing"):
        bad.append(f"{qid}: a boundary question (B0) and only it expects missing")
    if not row["targets"]:
        bad.append(f"{qid}: no targets")
    ids = [t.get("id") for t in row["targets"]]
    if len(set(ids)) != len(ids):
        bad.append(f"{qid}: target ids repeated")

    def known(t, where: str):
        if not isinstance(t, str) or not TABLE.fullmatch(t):
            bad.append(f"{qid} {where}: table {t!r}")
        elif tables is not None and t not in tables:
            bad.append(f"{qid} {where}: {t} not in the catalog")

    combo_ids = {}
    for t in row["targets"]:
        tid = f"{qid}.{t.get('id')}"
        bad += [f"{tid}: unknown field {k}" for k in sorted(set(t) - TARGET_KEYS)]
        missing = {"id", "text", "occurrence", "representative", "equivalents", "ask", "final", "holds"} - set(t)
        bad += [f"{tid}: missing field {k}" for k in sorted(missing)]
        if missing:
            continue
        if not isinstance(t["occurrence"], int) or t["occurrence"] < 1:
            bad.append(f"{tid}: occurrence {t['occurrence']!r}")
        elif not t["text"] or len(_spans(row["question"], t["text"])) < t["occurrence"]:
            bad.append(f"{tid}: text {t['text']!r} not found at occurrence {t['occurrence']} in the question")
        unknown_holds = set(t["holds"]) - set(HOLD_ITEMS)
        if unknown_holds or not all(isinstance(v, bool) for v in t["holds"].values()):
            bad.append(f"{tid}: holds {t['holds']!r}")
        for x in t["representative"]:
            known(x, f"{tid} representative")
        for k, vs in t["equivalents"].items():
            if k not in t["representative"]:
                bad.append(f"{tid}: equivalents key {k} is not a representative table")
            if k in vs:
                bad.append(f"{tid}: {k} listed as its own equivalent")
            for x in vs:
                known(x, f"{tid} equivalents")
        confirmed_reps = set(t["representative"]) | {x for vs in t["equivalents"].values() for x in vs}
        for x in t.get("representative_held") or []:
            known(x, f"{tid} representative_held")
            if x in confirmed_reps:
                bad.append(f"{tid}: {x} is both a confirmed and a held representative")
        fin = t["final"]
        if not isinstance(fin, dict) or set(fin) - FINAL_KEYS or "combinations" not in fin:
            bad.append(f"{tid}: final {sorted(fin) if isinstance(fin, dict) else fin!r}")
            continue
        if fin.get("frequency") not in (None, *FREQUENCIES):
            bad.append(f"{tid}: frequency {fin.get('frequency')!r}")
        bad += _check_combinations(tid, fin, known, coverage)
        combo_ids[t["id"]] = {c.get("id") for c in fin.get("combinations") or []}
        ask = t["ask"]
        if ask is not None:
            axes = ask.get("axes") if isinstance(ask, dict) else None
            if not axes:
                bad.append(f"{tid}: ask without axes")
            for ax in axes or []:
                if not ax.get("axis") or len(ax.get("values") or []) < 2:
                    bad.append(f"{tid}: ask axis {ax.get('axis')!r} needs a name and two values or more")
                for v in ax.get("values") or []:
                    if not v.get("value") or not v.get("tables"):
                        bad.append(f"{tid}: ask value {v.get('value')!r} without tables")
                    for x in v.get("tables") or []:
                        known(x, f"{tid} ask {ax.get('axis')}={v.get('value')}")
        combos = fin.get("combinations") or []
        if row["expected"] == "missing":
            if t["representative"] or t["equivalents"] or t.get("representative_held") or combos or ask is not None:
                bad.append(f"{tid}: a missing target holds no table and no ask")
        elif ask is None:
            if not t["representative"] or not combos:
                bad.append(f"{tid}: a select target needs representative tables and combinations")
            elif not any(c.get("status") == "confirmed" for c in combos) and not t["holds"].get("final"):
                bad.append(f"{tid}: no confirmed combination, and the final item is not held")
    for j in row.get("joint_combinations") or []:
        if not isinstance(j, dict) or set(j) != set(combo_ids) or any(j[k] not in combo_ids[k] for k in j):
            bad.append(f"{qid}: joint combination {j!r} does not name one combination per target")
    if row["expected"] == "ask" and not any(t.get("ask") for t in row["targets"]):
        bad.append(f"{qid}: expected ask but no target has an ask")
    if row["expected"] == "select" and any(t.get("ask") for t in row["targets"]):
        bad.append(f"{qid}: expected select but a target has an ask")
    return bad


def build(rows: list[dict], as_of: str, part: str, source: Path | None = None,
          tables: set[str] | None = None, coverage: dict | None = None) -> dict:
    """The standard of ``part`` ('indep60' or 'boundary') from the confirmed answer rows, copied as they are.
    ``tables``: the catalog's table ids; ``coverage``: table -> {frequency: (first month, last month)}."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of or ""):
        raise StandardError(f"as_of {as_of!r}")
    if part not in ("indep60", "boundary"):
        raise StandardError(f"part {part!r}")
    bad = []
    qids = [r.get("qid") for r in rows]
    if len(set(qids)) != len(qids):
        bad.append("qid repeated")
    questions = [r.get("question") for r in rows]
    if len(set(questions)) != len(questions):
        bad.append("question repeated")
    for r in rows:
        bad += _check_row(r, tables, coverage)
        if (part == "boundary") != (r.get("type") == "B0"):
            bad.append(f"{r.get('qid')}: type {r.get('type')!r} does not belong to part {part}")
    if bad:
        raise StandardError("; ".join(bad))
    cases = [{"case_id": f"IND-{r['qid']}", "part": part, "as_of": as_of, **copy.deepcopy(r)} for r in rows]
    src = {}
    if source is not None:
        src["answers"] = {"path": Path(source).name, "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()}
    return {"version": INDEP_STANDARD_VERSION, "as_of": as_of, "part": part, "sources": src,
            "ask_axes": None, "period_policy": dict(PERIOD_POLICY), "metrics": dict(METRICS), "cases": cases}


def is_indep(standard: dict) -> bool:
    return str(standard.get("version", "")).startswith("tl-indep-standard")


# ---------------------------------------------------------------- scoring


def _placement(rec: dict, case: dict) -> dict:
    """Targets covered by each model statistic, by quote place (as ``score.requirement_targets``)."""
    q, m = rec["question"] or "", rec["model"]
    places = []
    for t in case["targets"]:
        spans = _spans(q, t["text"])
        places.append(spans[t["occurrence"] - 1] if len(spans) >= t["occurrence"] else None)
    unplaced, split, covers = [], [], {}
    for s in m["statistics"]:
        spans = _spans(q, s.get("quote") or "")
        if not spans:
            unplaced.append(s["id"])
            continue
        per = [frozenset(i for i, p in enumerate(places) if p and a <= p[0] and p[1] <= b) for a, b in spans]
        if len({x for x in per if x}) > 1:
            split.append(s["id"])
        covers[s["id"]] = set().union(*per)
    return {"covers": covers, "unplaced": unplaced, "split": split,
            "uncovered": [sid for sid, c in covers.items() if not c]}


def _verdict(marks: list) -> object:
    marks = [x for x in marks if x not in ("na", "held")]
    if not marks:
        return "na"
    if {"wrong", "missing"} & set(marks):
        return False
    if "review" in marks:
        return None
    return True


def _join(marks: list[str]) -> str:
    """One mark of several statistics' marks: any wrong, else any review, else held when one is held."""
    if "wrong" in marks:
        return "wrong"
    if "review" in marks:
        return "review"
    return "held" if "held" in marks else "right"


def _held_or(target: dict, item: str, mark: str) -> str:
    return "held" if target["holds"].get(item) else mark


def _score_ask(asks: list[dict], spec: dict | None, table_of) -> tuple[str, list[str]]:
    """``asks``: the asks made on one target's statistics; ``spec``: the confirmed ask or None. Options are mapped
    to confirmed values by their tables only."""
    problems = []
    if spec is None:
        return ("wrong", [f"unneeded ask on {sorted({a['axis'] for a in asks})}"]) if asks else ("right", [])
    want = {ax["axis"]: ax["values"] for ax in spec["axes"]}
    got_axes = [a["axis"] for a in asks]
    for axis in want:
        if got_axes.count(axis) != 1:
            problems.append(f"asks on axis {axis}: {got_axes.count(axis)}")
    if set(got_axes) - set(want):
        problems.append(f"extra axes {sorted(set(got_axes) - set(want))}")
    for a in asks:
        values = want.get(a["axis"])
        if values is None:
            continue
        seen = []
        for o in a["options"]:
            table = table_of(o)
            hit = [v["value"] for v in values if table in v["tables"]]
            if len(hit) != 1:
                problems.append(f"option {o.get('value')!r} table {table} "
                                f"{'allowed for no value' if not hit else 'in several values'}")
            else:
                seen.append(hit[0])
        for v in values:
            if seen.count(v["value"]) != 1:
                problems.append(f"value {v['value']!r} given {seen.count(v['value'])} times")
    return ("wrong" if problems else "right"), problems


def _fmt(cell: tuple[str, str, int]) -> str:
    t, f, m = cell
    return f"{t} {f} {m // 12}-{m % 12 + 1:02d}"


def _rows_check(rows: list[dict], target: dict, titles: dict, coverage: dict | None) -> dict:
    """One statistic's final rows against one target's combinations: the repeated-month check first, then the
    (table, frequency, month) set against each combination; basis and catalog coverage after. Months are joined
    inside this statistic only, and never across combinations."""
    fin = target["final"]
    listed = [(r["table_id"], r["frequency"], m) for r in rows for p in (r.get("period") or [])
              for m in S._months(p["start"], p["end"])]
    per_month = [(f, m) for _, f, m in listed]
    repeated = sorted({x for x in per_month if per_month.count(x) > 1})
    have = set(listed)
    combos = fin["combinations"]
    out = {"combination": None, "problems": []}
    exact = [c for c in combos if combination_cells(c) == have]
    confirmed = [c for c in exact if c["status"] == "confirmed"]
    same_tables = [c for c in combos if {p["table"] for p in c["parts"]} == {r["table_id"] for r in rows}]
    if repeated:
        out["final"] = "wrong"
        out["problems"].append({"repeated_months": len(repeated)})
    elif confirmed:
        out["final"], out["combination"] = "right", confirmed[0]["id"]
    elif exact:
        out["final"], out["combination"] = "held", exact[0]["id"]
    else:
        out["final"] = "wrong"
        if combos:
            near = max(combos, key=lambda c: len(combination_cells(c) & have))
            cells = combination_cells(near)
            out["problems"].append({"nearest": near["id"], "missing": sorted(_fmt(x) for x in cells - have)[:12],
                                    "extra": sorted(_fmt(x) for x in have - cells)[:12],
                                    "missing_count": len(cells - have), "extra_count": len(have - cells)})
    if any(c["status"] == "confirmed" for c in same_tables):
        out["tables"] = "right"
    elif same_tables:
        out["tables"] = "held"
    else:
        out["tables"] = "wrong"
    if not listed:
        out["final"] = out["tables"] = "wrong"
        out["problems"].append("no months")
    if fin.get("basis"):
        out["basis"] = "right" if all(S._basis_values(S._brackets(titles.get(r["table_id"], "")), b["axis"]) &
                                      S._standard_value(b["axis"], b["value"])
                                      for r in rows for b in fin["basis"]) else "wrong"
    if coverage is not None:
        marks = []
        for r in rows:
            cov = (coverage.get(r["table_id"]) or {}).get(r["frequency"])
            months = [x for p in (r.get("period") or []) for x in S._months(p["start"], p["end"])]
            if not cov:
                marks.append("review")
            else:
                marks.append("right" if months and cov[0] <= min(months) and max(months) <= cov[1] else "wrong")
        out["coverage"] = "wrong" if "wrong" in marks else "review" if "review" in marks else "right"
    return out


def _boundary(rec: dict, res: dict) -> dict:
    if rec["error"]:
        return {"verdict": "run_or_contract_error", "why": f"{rec['error'].get('stage')}:{rec['error'].get('code')}"}
    if not res["contract"]["ok"]:
        return {"verdict": "run_or_contract_error", "why": "model fact errors"}
    m = rec["model"]
    tables = [t["table_id"] for f in rec["final"].values() for t in f.get("tables") or []]
    asks = m["asks"] or [a for f in rec["final"].values() for a in f.get("asks") or []]
    if tables:
        return {"verdict": "wrong", "why": f"tables handed off: {sorted(set(tables))}"}
    if asks:
        return {"verdict": "wrong", "why": "asked"}
    if not m["missing"]:
        return {"verdict": "review", "why": "no table and no ask, but no statistic named missing"}
    return {"verdict": "right", "why": "named missing, no table, no ask"}


def _allowed_tables(t: dict) -> set[str]:
    out = set(t["representative"]) | {x for vs in t["equivalents"].values() for x in vs}
    out |= set(t.get("representative_held") or [])
    out |= {p["table"] for c in t["final"]["combinations"] for p in c["parts"]}
    out |= {x for ax in (t["ask"] or {}).get("axes", []) for v in ax["values"] for x in v["tables"]}
    return out


def score_case(rec: dict, case: dict, titles: dict, coverage: dict | None = None) -> dict:
    res = {"case_id": case["case_id"], "part": case["part"], "type": case["type"], "labels": [], "review": []}
    info = rec.get("run_info") or {}
    res["run"] = {"verified": bool(info.get("versions")) and bool(rec.get("as_of"))
                  and info.get("as_of") == rec.get("as_of") == case["as_of"], "info": info}
    pool = set(rec["pool"])
    hits = [bool(_allowed_tables(t) & pool) if _allowed_tables(t) else None for t in case["targets"]]
    res["pool"] = {"hit": "na" if case["expected"] == "missing" else (False if False in hits else True),
                   "targets": hits}
    if res["pool"]["hit"] is False:
        res["labels"].append("retrieval_miss")
    blank = {k: {"right": False} for k in ("contract", "evidence_contract", "model_meaning", "ask", "table_match",
                                           "final_full", "completed")}
    if rec["error"]:
        res["labels"].append(f"run_error:{rec['error']['stage']}:{rec['error']['code']}")
        res.update(blank, code_checks={"right": None}, targets=[], point_in_period={"right": "na"})
        if case["expected"] == "missing":
            res.update({k: {"right": "na"} for k in blank})
            res["boundary"] = _boundary(rec, res)
        return res
    m = rec["model"]
    S.fact_checks(rec, titles, res)
    if case["expected"] == "missing":
        res.update({k: {"right": "na"} for k in ("model_meaning", "ask", "table_match", "final_full", "completed")})
        res["boundary"] = _boundary(rec, res)
        res["targets"], res["point_in_period"] = [], {"right": "na"}
        if m["missing"] and res["boundary"]["verdict"] != "right":
            res["labels"].append("boundary_" + res["boundary"]["verdict"])
        return res
    if m["missing"] and res["pool"]["hit"] is True:
        res["labels"].append("named_missing_but_in_catalog")
    place = _placement(rec, case)
    resolve = lambda o: S._resolve(rec, o["table"])  # noqa: E731
    final_table = lambda o: o.get("table_id")  # noqa: E731
    outcomes = {sid: f["outcome"].removesuffix("_unverified") for sid, f in rec["final"].items()}
    rows_t, points, matched = [], [], {}
    for ti, t in enumerate(case["targets"]):
        sids = [sid for sid, c in place["covers"].items() if ti in c]
        row = {"target": t["id"], "statistics": sids}
        if not sids:
            mark = "review" if place["unplaced"] or place["split"] else "missing"
            row.update(model=mark, ask=mark, tables=mark, full=mark)
        elif any(sid in place["split"] for sid in sids):
            row.update(model="review", ask="review", tables="review", full="review",
                       why="a quote placed on different targets")
        else:
            model_asks = [a for a in m["asks"] if a["for"] in sids]
            ask_held = bool(t["holds"].get("ask"))
            ask_mark, ask_problems = _score_ask(model_asks, t["ask"], resolve)
            if t["ask"] is not None:
                model_mark = ask_mark
            else:
                ok = set(t["representative"]) | {x for vs in t["equivalents"].values() for x in vs}
                held_reps = set(t.get("representative_held") or [])
                reps = [[resolve(c) for c in m["choices"] if sid in c["for"]] for sid in sids]
                if all(r and set(r) <= ok for r in reps):
                    model_mark = "right"
                elif all(r and set(r) <= ok | held_reps for r in reps):
                    model_mark = "held"  # an unconfirmed candidate: only this meaning item is held
                else:
                    model_mark = "wrong"
                if ask_held and model_asks and not any(reps):  # whether an ask is due is held: so is asking
                    model_mark = "held"
            final_asks = [a for sid in sids for a in (rec["final"].get(sid) or {}).get("asks") or []]
            f_ask, f_problems = _score_ask(final_asks, t["ask"], final_table)
            if t["ask"] is not None:
                tables_mark = full_mark = f_ask
                details = {"ask_problems": f_problems}
            else:
                tab, full, details = [], [], {}
                for sid in sids:
                    rows = (rec["final"].get(sid) or {}).get("tables") or []
                    if not rows and ask_held and outcomes.get(sid) == "ask":  # an ask where its due is held
                        tab.append("held")
                        full.append("held")
                        details[sid] = {"outcome": "ask", "held": "ask"}
                        continue
                    if not rows or outcomes.get(sid) not in ("selected",):
                        tab.append("wrong")
                        full.append("wrong")
                        details[sid] = {"outcome": outcomes.get(sid), "rows": len(rows)}
                        continue
                    c = _rows_check(rows, t, titles, coverage)
                    tab.append(c["tables"])
                    conds = [c.get(k) for k in ("basis", "coverage") if c.get(k)]
                    full.append("wrong" if c["final"] == "wrong" or "wrong" in conds else
                                "review" if "review" in conds else c["final"])
                    details[sid] = c
                    if c["combination"] and c["final"] == "right":
                        matched.setdefault(t["id"], set()).add(c["combination"])
                if final_asks and not ask_held:
                    tab.append("wrong")
                    full.append("wrong")
                    details["unneeded_final_ask"] = [a["axis"] for a in final_asks]
                tables_mark, full_mark = _join(tab), _join(full)
            row.update(model=_held_or(t, "representative", model_mark), ask=_held_or(t, "ask", ask_mark),
                       ask_problems=ask_problems,
                       tables=_held_or(t, "final", tables_mark), full=_held_or(t, "final", full_mark),
                       final_detail=details)
            want_point = t["final"].get("point_in_period")
            if want_point:
                got = {q.get("point_in_period") for sid in sids
                       for q in (rec["final"].get(sid) or {}).get("frequency_quotes") or []}
                points.append(_held_or(t, "point_in_period", "right" if want_point in got else "wrong"))
        rows_t.append(row)
    joint = case.get("joint_combinations")
    joint_mark = []
    if joint and all(r.get("full") == "right" for r in rows_t):
        chosen = {k: v for k, v in matched.items()}
        ok = any(all(j[k] in chosen.get(k, set()) for k in j) and all(len(v) == 1 for v in chosen.values())
                 for j in joint)
        if not ok:
            joint_mark = ["wrong"]
            res["labels"].append("combination_not_jointly_allowed")
    allowed_any = set().union(*(_allowed_tables(t) for t in case["targets"]))
    extra_wrong, extra_review = [], []
    for sid in place["uncovered"]:
        if outcomes.get(sid) not in ("selected", "ask", "failed"):
            continue
        handed = {r["table_id"] for r in (rec["final"].get(sid) or {}).get("tables") or []}
        (extra_wrong if handed - allowed_any else extra_review).append(sid)
    extra_mark = (["wrong"] if extra_wrong else []) + (["review"] if extra_review or place["unplaced"] else [])
    if extra_wrong:
        res["labels"].append("extra_statistic_table")
    res["targets"] = rows_t
    res["placement"] = {"unplaced": place["unplaced"], "split": place["split"], "uncovered": place["uncovered"],
                        "extra_wrong": extra_wrong, "extra_review": extra_review}
    review_mark = ["review"] if extra_review or place["unplaced"] else []
    res["model_meaning"] = {"right": _verdict([r["model"] for r in rows_t] + review_mark)}
    res["ask"] = {"right": _verdict([r["ask"] for r in rows_t])}
    res["table_match"] = {"right": _verdict([r["tables"] for r in rows_t] + extra_mark)}
    res["final_full"] = {"right": _verdict([r["full"] for r in rows_t] + extra_mark + joint_mark)}
    res["held"] = {k: [r["target"] for r in rows_t if r.get(k) == "held"] for k in ("model", "ask", "tables", "full")}
    unverified = sorted(sid for sid, f in rec["final"].items() if f["outcome"].endswith("_unverified"))
    ff = res["final_full"]["right"]
    blocked = [w for w, bad in (("scorer_fact_errors", not res["contract"]["ok"]),
                                ("scorer_evidence_errors", not res["evidence_contract"]["ok"]),
                                ("evidence_unverified", bool(unverified)),
                                ("route_not_verified_handoff", rec.get("route") not in ("handoff", "ask"))) if bad]
    res["completed"] = {"right": ff if ff is not True else False if blocked else
                        None if res["code_checks"]["right"] is None else True, "blocked_by": blocked}
    res["point_in_period"] = {"right": _verdict(points) if points else "na", "marks": points}
    for key, lab in (("model_meaning", "model_meaning_wrong"), ("final_full", "final_wrong")):
        if res[key]["right"] is False:
            res["labels"].append(lab)
    res["diagnostics"] = rec["diagnostics"]
    res["labels"] = list(dict.fromkeys(res["labels"]))
    return res


def aggregate(results: list[dict]) -> dict:
    """Counts per part (indep60, boundary) and per type; each metric with right, wrong, review, held, na and the
    denominator (right + wrong). A held item is never in a denominator."""
    def rate(rows, get):
        vals = [get(r) for r in rows]
        right = sum(1 for v in vals if v is True)
        wrong = sum(1 for v in vals if v is False)
        review = sum(1 for v in vals if v is None)
        return {"right": right, "wrong": wrong, "review": review, "na": len(vals) - right - wrong - review,
                "denominator": right + wrong}

    def held(rows, key):
        return sum(len(r.get("held", {}).get(key, [])) for r in rows)

    def block(rows):
        out = {"questions": len(rows)}
        for name, key in (("run_verified", None), ("pool_hit", "pool"), ("contract_ok", "contract"),
                          ("evidence_contract_ok", "evidence_contract"), ("code_checks_right", "code_checks"),
                          ("model_meaning_right", "model_meaning"), ("ask_right", "ask"),
                          ("table_match", "table_match"), ("final_full", "final_full"), ("completed", "completed"),
                          ("point_in_period", "point_in_period")):
            if name == "run_verified":
                out[name] = rate(rows, lambda r: r["run"]["verified"])
            elif name == "pool_hit":
                out[name] = rate(rows, lambda r: r["pool"]["hit"])
            else:
                out[name] = rate(rows, lambda r, k=key: (r.get(k) or {}).get("right", r.get(k, {}).get("ok")))
        out["held_targets"] = {k: held(rows, k) for k in ("model", "ask", "tables", "full")}
        verdicts = [r["boundary"]["verdict"] for r in rows if r.get("boundary")]
        if verdicts:
            out["boundary"] = {v: verdicts.count(v) for v in ("right", "wrong", "run_or_contract_error", "review")}
        labels: dict[str, int] = {}
        for r in rows:
            for lab in r["labels"]:
                labels[lab] = labels.get(lab, 0) + 1
        out["labels"] = labels
        return out

    parts = {p: [r for r in results if r["part"] == p] for p in ("indep60", "boundary")}
    out = {p: block(rows) for p, rows in parts.items() if rows}
    out["by_type"] = {t: block([r for r in results if r["type"] == t])
                      for t in TYPES if any(r["type"] == t for r in results)}
    return out
