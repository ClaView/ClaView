"""Checks, against the REAL KOSIS Open API, whether actual data exists for
every candidate period recorded in
eval/cases/b_period/golden_set_v1_period_augmented.json -- not just whether a period
falls inside the table's declared metadata coverage [start, end], but
whether KOSIS actually returns a real (non-missing, non-"-") value for
every period in the range. Metadata coverage bounds can overstate what is
actually populated (embargoed recent periods, revisions, provisional gaps),
which matters most exactly for the "최근/올해/작년" cases this file checks.

This reuses claview.fetch's own request-building and response-parsing
(build_api_request / KosisClient / normalize_rows) rather than a separate
ad hoc HTTP call, so this script walks the exact same code path the real
pipeline's fetch stage would -- a period this script says is clean is a
period fetch_data() would also treat as clean.

Why this is a separate script instead of something Claude ran here: this
sandbox's network egress does not reach kosis.kr, and more importantly a
KOSIS Open API key is a personal credential that should never be typed
into a chat or pasted into a shared file -- it is read here only from the
KOSIS_API_KEY environment variable, at run time, on your own machine.

Setup:
    1. Get a KOSIS Open API key: https://kosis.kr/openapi/ (회원가입 -> 활용신청)
    2. export KOSIS_API_KEY="<your key>"
    3. pip install httpx   (already a project dependency)

Usage:
    python scripts/b_part/verify_periods_kosis.py                # all cases, every candidate
    python scripts/b_part/verify_periods_kosis.py CV-AUG-01 CV-AUG-05   # just these cases
    python scripts/b_part/verify_periods_kosis.py --write           # also fill in each case's
                                                              # "kosis_verification" field in the
                                                              # json file with the result

Reads one network response per (case, candidate) pair -- 13 cases x up to
2 candidates each, well under KOSIS's rate limits, but reruns are cheap
individually: pass specific case ids while iterating.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(
    0, str(ROOT / "src")
)  # allow running as `python scripts/b_part/verify_periods_kosis.py`

from claview.contracts import StageFailure, Trace  # noqa: E402
from claview.fetch import KosisClient, build_api_request, normalize_rows  # noqa: E402

CASES_PATH = ROOT / "eval" / "cases" / "b_period" / "golden_set_v1_period_augmented.json"


def _probe_selection(case: dict, period: dict) -> dict:
    """A selection dict shaped like claview.stages.canonicalize_selections'
    output, built from the case's own recorded expected selection but with
    `period` swapped in -- so the same table/item/dimensions/unit the case
    already uses gets checked against each of its period_candidates."""
    base = case["expected"]["B"]["selections"][0]
    return {**base, "period": period}


def _diagnose(events: list[dict], code: str) -> str | None:
    """Pulls a human-readable reason for an error straight out of the trace
    events this one (selection, candidate) attempt logged -- so a verdict
    like "unexpected_response_period" comes with what KOSIS actually sent
    back, instead of leaving it to guesswork whether the real cause is a
    wrong wire-level period code (e.g. H's internal "H" vs whatever KOSIS's
    data API itself calls 반기), a year that genuinely is not published yet,
    or a one-off service error unrelated to period handling."""
    for ev in reversed(events):
        if ev["event"] == "data_response" and code == "unexpected_response_period":
            rows = ev.get("rows") or []
            pairs = sorted({(str(r.get("PRD_SE", "")), str(r.get("PRD_DE", ""))) for r in rows})
            shown = pairs[:8]
            more = f" (+{len(pairs) - 8} more)" if len(pairs) > 8 else ""
            return f"KOSIS returned prdSe/prdDe pairs: {shown}{more}"
        if ev["event"] == "data_response_error" and code == "kosis_service_error":
            rows = ev.get("rows")
            # KOSIS's error shape isn't always "a list of row dicts" -- a
            # service error can come back as a single dict ({"err": ...}) or
            # something else entirely, so this cannot assume list indexing
            # is safe the way the *success* path (rows is always a proper
            # list of row dicts, by this point already validated) can.
            if rows is None:
                return "KOSIS returned no response body"
            if not isinstance(rows, list):
                return f"KOSIS response was not a list of rows: {rows!r}"
            if not rows:
                return "KOSIS returned an empty list (no rows at all)"
            return f"KOSIS response, first row: {rows[0]!r}"
    return None


def verify_case(client: KosisClient, trace: Trace, case: dict) -> dict:
    report = {"case_id": case["case_id"], "query": case["query"], "candidates": {}}
    # Competing candidate names can resolve to the exact same period (e.g.
    # the axis-3 sets record sliding_window / calendar_anchor / floor_to_year,
    # and two of the three always coincide). Hitting KOSIS once per distinct
    # period and reusing the verdict keeps API usage down without hiding any
    # candidate name from the report.
    seen: dict[tuple, str] = {}
    for name, period in case["period_candidates"].items():
        key = (period["frequency"], period["start"], period["end"])
        if key in seen:
            report["candidates"][name] = dict(report["candidates"][seen[key]])
            continue
        seen[key] = name
        selection = _probe_selection(case, period)
        start_idx = len(trace.events)
        try:
            rows = client.fetch(build_api_request(selection), trace)
            result, issues = normalize_rows(selection, rows, snapshot="kosis-verification")
            report["candidates"][name] = {
                "period": period,
                "expected_periods": result["coverage"]["expected"],
                "returned_periods": result["coverage"]["returned"],
                "missing_periods": result["coverage"]["missing_periods"],
                "value_issues": issues,
                "verdict": "confirmed_present"
                if not issues and not result["coverage"]["missing_periods"]
                else "confirmed_gap",
            }
        except StageFailure as exc:
            entry = {"period": period, "verdict": "error", "error": exc.code}
            diagnosis = _diagnose(trace.events[start_idx:], exc.code)
            if diagnosis:
                entry["diagnosis"] = diagnosis
            report["candidates"][name] = entry
    return report


def print_report(report: dict) -> None:
    print(f"\n=== {report['case_id']} — {report['query']} ===")
    for name, c in report["candidates"].items():
        if c["verdict"] == "error":
            print(f"  [{name}] {c['period']['start']}~{c['period']['end']}  ERROR: {c['error']}")
            if c.get("diagnosis"):
                print(f"      → {c['diagnosis']}")
            continue
        flag = "OK " if c["verdict"] == "confirmed_present" else "GAP"
        print(
            f"  [{name}] {c['period']['start']}~{c['period']['end']}  {flag}  "
            f"({c['returned_periods']}/{c['expected_periods']} periods returned"
            + (f", missing={c['missing_periods']}" if c["missing_periods"] else "")
            + (f", value_issues={c['value_issues']}" if c["value_issues"] else "")
            + ")"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_ids", nargs="*", help="Specific case_id(s) to check; default: all")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write results back into each case's kosis_verification field",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Case file to verify (path relative to the project root, or absolute); default: "
        "eval/cases/b_period/golden_set_v1_period_augmented.json. Any file using the same "
        "period_augmented schema works, e.g. the axis-3 sets under eval/cases/.",
    )
    args = parser.parse_args()
    cases_path = CASES_PATH
    if args.file:
        cases_path = Path(args.file)
        if not cases_path.is_absolute():
            cases_path = ROOT / cases_path

    api_key = os.environ.get("KOSIS_API_KEY")
    if not api_key:
        print("KOSIS_API_KEY is not set. See this script's module docstring for setup steps.")
        raise SystemExit(1)

    payload = json.loads(cases_path.read_text(encoding="utf-8-sig"))
    cases = payload["cases"]
    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [c for c in cases if c["case_id"] in wanted]
        missing = wanted - {c["case_id"] for c in cases}
        if missing:
            raise SystemExit(f"Unknown case_id(s): {sorted(missing)}")

    trace = Trace()
    client = KosisClient(api_key, client=httpx.Client(timeout=45, follow_redirects=False))
    try:
        reports = []
        for case in cases:
            report = verify_case(client, trace, case)
            print_report(report)
            reports.append(report)
            if args.write:
                target = next(c for c in payload["cases"] if c["case_id"] == case["case_id"])
                gaps = [
                    name
                    for name, c in report["candidates"].items()
                    if c["verdict"] != "confirmed_present"
                ]
                target["kosis_verification"] = {
                    "status": "confirmed_gap" if gaps else "confirmed_present",
                    "notes": f"checked candidates: {list(report['candidates'])}"
                    + (f"; gaps in: {gaps}" if gaps else ""),
                }
    finally:
        client.close()

    if args.write:
        cases_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote kosis_verification results back into {cases_path}")

    print(
        "\nNote: a 'GAP' means KOSIS has no recorded value for at least one period in that "
        "candidate's range (within its declared metadata coverage) -- for an 'open_bug' case "
        "this also tells you whether the anchor_year_fallback candidate is actually safe to "
        "return once the code is fixed to offer it."
    )


if __name__ == "__main__":
    main()
