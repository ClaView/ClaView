"""Probes which `prdSe` REQUEST codes the real KOSIS data API accepts for a
given case's table and cycle, by sending the same request once per candidate
code and reporting what came back.

Why this exists (2026-10-06): scripts/b/b_part/verify_periods_kosis.py showed that
every 반기(H) and 연간(Y) request to a table that declares MORE THAN ONE cycle
(DT_303Y001, DT_304Y101, DT_141Y008) failed with err 30 "데이터가 존재하지
않습니다", while 반기/연간 requests to single-cycle tables (DT_105Y001 = H only,
DT_301Y015 = Y only) succeeded. Running this probe confirmed why: KOSIS wants
prdSe = "H"/"Y" in the REQUEST (a single-cycle table simply ignores a wrong
one), while its responses tag rows PRD_SE = "S"/"A". claview.b.fetch had been
sending the response-side codes; see its _RESPONSE_PRD_SE comment. Kept as a
diagnostic for any future "data does not exist" that smells like a code mix-up.

Setup is the same as scripts/b/b_part/verify_periods_kosis.py (KOSIS_API_KEY env var,
run from the project root). Usage:

    python scripts/b/b_part/probe_prdse.py \\
        --file eval/cases/b/period/golden_set_v1_axis3_cross_frequency.json \\
        --file eval/cases/b/period/golden_set_v1_axis3_h_recent_years.json \\
        AX3-B-01 AX3-B-07 AX3-B-09 AX3-B-12 AX3-B-02 AX3-A-01

~2 requests per case (one per candidate code); nothing is written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx
import verify_periods_kosis as v  # noqa: E402 -- also puts the project root on sys.path

from claview.b.contracts import StageFailure, Trace  # noqa: E402
from claview.b.fetch import KosisClient, build_api_request  # noqa: E402

# Request codes worth trying per internal frequency: the internal letter first
# (what the code sent before 2026-10-02), then the letter KOSIS's responses
# carry in PRD_SE. 분기/월 have only one candidate -- they are the controls.
CANDIDATE_CODES = {"Y": ["Y", "A"], "H": ["H", "S"], "Q": ["Q"], "M": ["M"]}


def probe_case(client, trace: Trace, case: dict) -> dict:
    selection = case["expected"]["B"]["selections"][0]
    freq = selection["period"]["frequency"]
    base = build_api_request(selection)
    outcomes = {}
    for code in CANDIDATE_CODES[freq]:
        start_idx = len(trace.events)
        try:
            rows = client.fetch({**base, "prdSe": code}, trace)
        except StageFailure as exc:
            outcomes[code] = {
                "ok": False,
                "error": exc.code,
                "diagnosis": v._diagnose(trace.events[start_idx:], exc.code),
            }
            continue
        periods = sorted({str(r.get("PRD_DE", "")) for r in rows})
        outcomes[code] = {
            "ok": True,
            "rows": len(rows),
            "response_prd_se": sorted({str(r.get("PRD_SE", "")) for r in rows}),
            "periods": f"{periods[0]}~{periods[-1]}" if periods else "-",
        }
    return {"case_id": case["case_id"], "query": case["query"], "freq": freq, "outcomes": outcomes}


def print_probe(result: dict) -> None:
    print(f"\n=== {result['case_id']} [{result['freq']}] — {result['query']} ===")
    for code, o in result["outcomes"].items():
        if o["ok"]:
            print(
                f"  request prdSe={code!r:4} -> OK   rows={o['rows']}  "
                f"response PRD_SE={o['response_prd_se']}  periods {o['periods']}"
            )
        else:
            print(f"  request prdSe={code!r:4} -> ERROR {o['error']}")
            if o.get("diagnosis"):
                print(f"      → {o['diagnosis']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("case_ids", nargs="+", help="case_id(s) to probe")
    parser.add_argument(
        "--file",
        action="append",
        required=True,
        help="Case file (project-root-relative or absolute); repeat to search several files",
    )
    args = parser.parse_args()

    api_key = os.environ.get("KOSIS_API_KEY")
    if not api_key:
        print(
            "KOSIS_API_KEY is not set. See scripts/b/b_part/verify_periods_kosis.py's docstring for setup steps."  # noqa: E501
        )
        raise SystemExit(1)

    cases = {}
    for name in args.file:
        path = Path(name) if Path(name).is_absolute() else v.ROOT / name
        for case in json.loads(path.read_text(encoding="utf-8-sig"))["cases"]:
            cases[case["case_id"]] = case
    missing = [c for c in args.case_ids if c not in cases]
    if missing:
        raise SystemExit(f"Unknown case_id(s): {missing}")

    trace = Trace()
    client = KosisClient(api_key, client=httpx.Client(timeout=45, follow_redirects=False))
    try:
        for case_id in args.case_ids:
            print_probe(probe_case(client, trace, cases[case_id]))
    finally:
        client.close()


if __name__ == "__main__":
    main()
