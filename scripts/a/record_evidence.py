"""Record mode of the evidence checks (plan 04, 4-2 and decision 3; no model, no call).

Runs claview_v3 only (2.1-2.7, then select_tables with every check in record mode, data/evidence_filters.json) on
the 417 questions and counts, per check, the answer groups the check would drop if it filtered, per requirement
(claview_v3.select.record.drop_record):
- a group is in the pool when one of its allowed tables is a candidate with indicator evidence of some requirement;
- a check drops a group when, for some requirement holding such candidates of the group, every one of them fails
  the check (a candidate of another requirement, or one without indicator evidence, is no survival); ``touched``
  when at least one of them fails;
- ``no_evidence`` counts the groups with candidates but none with indicator evidence (the indicator check itself
  can then drop nothing: a candidate whose indicator fails has no evidence).
Answer groups come from the same source file as scripts/score_by_stage.py (read only).

new200 (dataset "official") is counted only: no question, case ID or table of it is printed or written. The per-case
CSV holds golden and development cases. The checks with no dropped answer group are listed; adopting any of them
as a filter is the user's decision (evidence_filters.json stays "record").

Usage: PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/a/record_evidence.py [out_dir]
Writes cases.csv, summary.json, summary.md to runs/m3_evidence_record/ (or out_dir).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_selection as S  # noqa: E402  (installs the network block first)

from claview.a import _guard, paths  # noqa: E402
from claview.a.catalog.layer import load_layer  # noqa: E402
from claview.a.select.record import drop_record  # noqa: E402

DATASETS = ("golden", "development", "official")
CHECKS = ("basis", "unit", "exclude", "frequency", "period", "region", "indicator", "cross_axis", "matched_items",
          "breakdown")  # breakdown: plan 07 S1 (record mode)
OPEN = set(S.OPEN_DATASETS)


def case_record(got: dict, groups: list[list[str]]) -> dict:
    """Per check: dropped / touched answer groups of one case (per requirement, see the module docstring)."""
    return drop_record(got, groups, CHECKS)


def main(out_dir: Path) -> dict:
    t0 = time.time()
    questions = S.load_questions()
    groups = S.load_groups()
    filters = json.loads(paths.EVIDENCE_FILTERS.read_text(encoding="utf8"))
    layer = load_layer(include_external_synonyms=False)
    totals = {ds: {"cases": 0, "groups": 0, "in_pool": 0, "dropped": {c: 0 for c in CHECKS},
                   "touched": {c: 0 for c in CHECKS}, "cases_dropped": {c: 0 for c in CHECKS}, "no_evidence": 0,
                   "shape_problems": 0} for ds in (*DATASETS, "all")}
    rows = []
    for cid, q in questions.items():
        ds = q.get("dataset")
        got = S.run_case(q, layer)
        rec = case_record(got, groups.get(cid, []))
        for key in (ds, "all"):
            t = totals[key]
            t["cases"] += 1
            t["groups"] += rec["groups"]
            t["in_pool"] += rec["in_pool"]
            t["no_evidence"] += rec["no_evidence"]
            t["shape_problems"] += len(got["problems"])
            for c in CHECKS:
                t["dropped"][c] += rec["dropped"][c]
                t["touched"][c] += rec["touched"][c]
                t["cases_dropped"][c] += rec["dropped"][c] > 0
        if ds in OPEN:  # new200 is counted only
            rows.append({"case_id": cid, "dataset": ds, "groups": rec["groups"], "in_pool": rec["in_pool"],
                         "no_evidence": rec["no_evidence"], **{f"drop_{c}": rec["dropped"][c] for c in CHECKS},
                         **{f"touch_{c}": rec["touched"][c] for c in CHECKS}})
    zero = [c for c in CHECKS if totals["all"]["dropped"][c] == 0]
    zero_open = [c for c in CHECKS if all(totals[ds]["dropped"][c] == 0 for ds in ("golden", "development"))]
    summary = {"seconds": round(time.time() - t0, 1), "model_calls": 0, "filters_file": filters.get("status"),
               "filters": filters.get("checks"), "totals": totals, "zero_drop_checks_all": zero,
               "zero_drop_checks_golden_development": zero_open,
               "old_modules_loaded": sorted(m for m in sys.modules if m == "claview" or m.startswith("claview.")),
               "network_attempts": len(_guard.NETWORK_ATTEMPTS),
               "notes": ["dropped: for some requirement, every candidate of the answer group with indicator evidence "
                         "fails the check; touched: at least one such candidate fails; in_pool counts groups with an "
                         "evidence candidate.",
                         "new200 (official) is counted only. Adopting a filter is the user's decision.",
                         "The 417 questions are not an independent test set."]}
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
    (out_dir / "summary.md").write_text(markdown(summary), encoding="utf8")
    return summary


def markdown(s: dict) -> str:
    t = s["totals"]
    lines = ["# 근거 검사 기록 모드 (417문항, 모델 호출 0회)", "",
             "요구별 집계: 어느 요구에서 그 정답 묶음의 '지표 근거가 있는 후보'가 모두 그 검사에서 fail이면 탈락. 다른 "
             "요구의 후보나 지표 근거 없는 후보는 생존으로 치지 않는다. 새200은 건수만. 거르기 채택은 사용자 확인 후다"
             "(설정 파일은 모두 record).", "",
             "| 검사 | 골든 | 개발 | 새200(건수) | 전체 | 전체 문항 수 | 한 후보 이상 fail(전체) |",
             "|---|---|---|---|---|---|---|"]
    for c in CHECKS:
        lines.append(f"| {c} | {t['golden']['dropped'][c]} | {t['development']['dropped'][c]} | "
                     f"{t['official']['dropped'][c]} | {t['all']['dropped'][c]} | {t['all']['cases_dropped'][c]} | "
                     f"{t['all']['touched'][c]} |")
    lines.append(f"| (근거 0: 지표 근거 없음) | {t['golden']['no_evidence']} | {t['development']['no_evidence']} | "
                 f"{t['official']['no_evidence']} | {t['all']['no_evidence']} | — | — |")
    lines += ["", "| 자료 | 문항 | 정답 묶음 | 지표 근거 후보에 든 묶음 |", "|---|---|---|---|"]
    for ds in (*DATASETS, "all"):
        lines.append(f"| {ds} | {t[ds]['cases']} | {t[ds]['groups']} | {t[ds]['in_pool']} |")
    lines += ["", f"- 정답 탈락 0인 검사(전체): {', '.join(s['zero_drop_checks_all']) or '없음'}",
              f"- 정답 탈락 0인 검사(골든·개발): {', '.join(s['zero_drop_checks_golden_development']) or '없음'}",
              "- matched_items·cross_axis는 판정(verdict)이 늘 n/a인 기록 검사라 탈락이 0이다.",
              "- indicator는 fail이면 그 후보가 지표 근거 없는 후보라 이 집계에서 0이 된다. 지표로 잃는 묶음은 '근거 0' 줄이다."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "m3_evidence_record"
    result = main(out)
    print(json.dumps({k: v for k, v in result.items() if k not in ("notes", "totals")}, ensure_ascii=False, indent=1))
