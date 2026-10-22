"""Prints the axis-3 evidence matrix: for every case in the three axis-3 sets
(plus the five human-labeled "최근 N년" cases in golden_set_v1), which of the
three candidate readings of "최근 N년" does the CURRENT code follow?

  S = sliding_window   (exactly N years of data ending at the anchor)
  C = calendar_anchor  (first period of calendar year anchor_year - N)
  F = floor_to_year    (first period of the calendar year S falls in)

Where two letters are shown (e.g. "S,F") the candidates coincide for that
case, so the case cannot tell them apart.

Run: PYTHONPATH=. python scripts/b_part/axis3_evidence_report.py
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from build_axis3_period_sets import PER_YEAR, candidates, is_final_anchor

from claview.contracts import Config
from claview.metadata import MetadataStore
from claview.stages import resolve_relative_period

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "eval" / "cases" / "b_period"
SETS = [
    "golden_set_v1_axis3_h_recent_years",
    "golden_set_v1_axis3_cross_frequency",
    "golden_set_v1_axis3_qm_controls",
]
LETTER = {"sliding_window": "S", "calendar_anchor": "C", "floor_to_year": "F"}


def code_period(query: str, table: dict, config: Config) -> dict:
    result = resolve_relative_period(query, table, config)
    return result["result"]["resolved_period"] if "result" in result else result["period"]


def rule_letters(period: dict, cands: dict) -> str:
    return ",".join(LETTER[name] for name, p in cands.items() if p == period) or "-"


def span(period: dict) -> str:
    freq, start, end = period["frequency"], period["start"], period["end"]
    n_periods = (int(end[:4]) - int(start[:4])) * PER_YEAR[freq]
    if freq != "Y":
        n_periods += int(end[4:]) - int(start[4:]) + 1
    else:
        n_periods += 1
    return f"{n_periods}p/{int(end[:4]) - int(start[:4]) + 1}y"


def main() -> None:
    # Pinned to the date the checked-in axis-3 case files were built with.
    config = Config(as_of="2026-10-01")
    store = MetadataStore(
        ROOT / "data" / "catalog" / "search_catalog.json",
        ROOT / "data" / "raw" / "boK_meta_raw.jsonl",
    )

    print("== A. Existing human-labeled goldens ('최근 N년') ==")
    print(f"{'case':10} {'freq':4} {'N':>2} {'table':11} {'anchor':7} {'gold start':10} matches")
    golden = json.loads((CASES_DIR.parent / "golden_set_v1.json").read_text(encoding="utf-8-sig"))[
        "cases"
    ]
    for c in golden:
        m = re.search(r"최근\s*(\d+)년", c["query"])
        if not m:
            continue
        n = int(m.group(1))
        sel = c["expected"]["B"]["selections"][0]
        table = store.get(sel["org_id"], sel["table_id"])
        freq = sel["period"]["frequency"]
        cands = candidates(table, freq, n)
        hit = ",".join(LETTER[k] for k, p in cands.items() if p["start"] == sel["period"]["start"])
        print(
            f"{c['case_id']:10} {freq:4} {n:>2} {sel['table_id']:11} {table['periods'][freq]['end']:7} "  # noqa: E501
            f"{sel['period']['start']:10} {hit or '-'}"
        )

    by_class: dict[tuple, list] = defaultdict(list)
    groups: dict[tuple, list] = defaultdict(list)
    for stem in SETS:
        cases = json.loads((CASES_DIR / f"{stem}.json").read_text(encoding="utf-8-sig"))["cases"]
        print(f"\n== {stem} ({len(cases)} cases) ==")
        print(
            f"{'case':9} {'freq':4} {'N':>2} {'table':11} {'anchor':7} {'code period':15} {'span':8} {'code=':6} S/C/F starts"  # noqa: E501
        )
        for case in cases:
            t = case["expected"]["A"]["tables"][0]
            table = store.get(t["org_id"], t["table_id"])
            n = int(re.search(r"최근\s*(\d+)년", case["query"]).group(1))
            period = case["expected"]["B"]["selections"][0]["period"]
            freq = period["frequency"]
            anchor = table["periods"][freq]["end"]
            cands = candidates(table, freq, n)
            live = code_period(case["query"], table, config)
            assert live == period, (case["case_id"], live, period)
            letters = rule_letters(live, cands)
            sfc = "/".join(cands[k]["start"] for k in LETTER if k in cands)
            final = is_final_anchor(freq, anchor)
            print(
                f"{case['case_id']:9} {freq:4} {n:>2} {t['table_id']:11} {anchor:7} "
                f"{live['start']}~{live['end']:6} {span(live):8} {letters:6} {sfc}"
            )
            by_class[(freq, "final(H2/Q4/M12)" if final else "non-final")].append(letters)
            groups[(t["table_id"], n)].append((freq, live["start"][:4], anchor))

    print("\n== B. What the current code follows, by (frequency, anchor class) ==")
    for (freq, cls), letters in sorted(by_class.items()):
        tally: dict[str, int] = defaultdict(int)
        for lt in letters:
            tally[lt] += 1
        print(f"  {freq} {cls:17} n={len(letters):2}  {dict(sorted(tally.items()))}")

    print("\n== C. Cross-frequency: same table, same N -> start calendar year per frequency ==")
    for (table_id, n), members in groups.items():
        if len({f for f, _, _ in members}) < 2:
            continue
        years = {f: y for f, y, _ in members}
        anchors = {f: a for f, _, a in members}
        verdict = "AGREE" if len(set(years.values())) == 1 else "DIVERGE"
        print(f"  {table_id} N={n}: starts {years} anchors {anchors}  -> {verdict}")


if __name__ == "__main__":
    main()
