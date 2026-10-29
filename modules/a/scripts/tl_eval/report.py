"""Scores a set of adapted run records against the standard and writes the results (plan 37, step 4).

Input: a JSON file ``{case_id: adapt(state)}`` (the step-5 runner writes it; no model is called here) and the
standard. Output: per-question results, the aggregate by part (right, wrong, review, not applicable, denominator), the
run-time information each record carries (``run_info``: path versions, as_of, catalog, period policy, model) kept
apart from the evaluator's own versions and the standard's version and hash. A record without ``run_info`` or
``as_of``, or with an ``as_of`` other than the standard's, is not scored: it is listed under ``unverified`` (user
2026-10-28 04:20 UTC). Questions without a record are listed as not run.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules" / "a"))
sys.path.insert(0, str(ROOT / "modules" / "a" / "scripts"))

from tl_eval import files as F  # noqa: E402
from tl_eval import indep as I  # noqa: E402
from tl_eval import score as S  # noqa: E402

REPORT_VERSION = "tl-report-2026-10-29.4"  # .3: metric definitions; .4: half-year coverage, indep standards
STANDARD_PATH = F.external("standard_v2.json")


def coverage_of(views: dict) -> dict:
    """table -> {frequency: (first month, last month)} from the catalog metadata (the scorer's own reading)."""
    import re

    def month(f, stamp, end):
        if not stamp:
            return None
        y = int(stamp[:4])
        if f == "M":
            m = re.search(r"\.(\d{1,2})", stamp)
            return y * 12 + int(m.group(1)) - 1 if m else None
        if f == "Q":
            m = re.search(r"(\d)/4", stamp)
            return y * 12 + (int(m.group(1)) * 3 - 1 if end else (int(m.group(1)) - 1) * 3) if m else None
        if f == "H":  # '2010 1/2': first or second half (.4; before, a half-year table had no coverage)
            m = re.search(r"(\d)/2", stamp)
            return y * 12 + (int(m.group(1)) * 6 - 1 if end else (int(m.group(1)) - 1) * 6) if m else None
        return y * 12 + (11 if end else 0)

    out = {}
    for t, v in views.items():
        out[t] = {}
        for f, s, e in v.periods:
            a, b = month(f, s, False), month(f, e, True)
            if a is not None and b is not None:
                out[t][f] = (a, b)
    return out


def evaluate(records: dict, standard: dict, titles: dict, coverage: dict | None = None,
             standard_sha256: str | None = None) -> dict:
    results, not_run, unverified = [], [], []
    for case in standard["cases"]:
        rec = records.get(case["case_id"])
        if rec is None:
            not_run.append(case["case_id"])
            continue
        info = rec.get("run_info") or {}
        problems = []
        if not info.get("versions"):
            problems.append("no run versions")
        if not rec.get("as_of") or info.get("as_of") != rec.get("as_of"):
            problems.append("no or inconsistent run as_of")
        elif rec["as_of"] != standard["as_of"]:
            problems.append(f"run as_of {rec['as_of']} differs from the standard's {standard['as_of']}")
        if problems:
            unverified.append({"case_id": case["case_id"], "problems": problems})
            continue
        results.append((I.score_case if I.is_indep(standard) else S.score_case)(rec, case, titles, coverage))
    runs = sorted({json.dumps((records[r["case_id"]].get("run_info") or {}).get("versions"), sort_keys=True)
                   for r in results})
    indep = I.is_indep(standard)
    return {"evaluator": {"score": S.SCORE_VERSION, "report": REPORT_VERSION,
                          **({"indep": I.INDEP_SCORE_VERSION} if indep else {})},
            "metrics": dict(I.METRICS if indep else S.METRICS),
            "standard": {"version": standard["version"], "sha256": standard_sha256, "as_of": standard["as_of"],
                         "sources": standard["sources"], "ask_axes": standard["ask_axes"],
                         "period_policy": standard["period_policy"]},
            "run_versions": [json.loads(x) for x in runs],
            "not_run": not_run, "unverified": unverified, "results": results,
            "aggregate": aggregate(standard, results)}


def aggregate(standard: dict, results: list[dict]) -> dict:
    """The aggregate of the standard's kind (an independent standard is counted by part and by type)."""
    return I.aggregate(results) if I.is_indep(standard) else S.aggregate(results)


def main(records_path: str, out_path: str, standard_path: str | None = None) -> dict:
    from claview_a.catalog.layer import load_layer
    from claview_a.search.tables import selection_catalog

    path = Path(standard_path or STANDARD_PATH)
    standard = json.loads(path.read_text(encoding="utf-8"))
    records = json.loads(Path(records_path).read_text(encoding="utf-8"))
    views = selection_catalog(load_layer()).views
    got = evaluate(records, standard, {t: v.title for t, v in views.items()}, coverage_of(views),
                   hashlib.sha256(path.read_bytes()).hexdigest())
    Path(out_path).write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    return got


if __name__ == "__main__":
    main(*sys.argv[1:])
