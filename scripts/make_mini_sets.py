"""Build the reduced improvement sets from the 417 replay (case IDs + baseline, no questions or answers copied).

dev_mini (open: look at cases, fix rules): golden 19 + every wrong development case + fragile correct cases
  (decision gate fires: score gap < 2 or rule/model disagreement) + stable correct cases, one per twin group.
check_mini (numbers only, from new200): about half of each wrong cause + fragile and stable correct samples.
Selection is deterministic (sha256 of the case ID), not hand-picked.

Inputs: runs/score_by_stage/cases.csv and runs/decision_gates/signals.json (run those scripts first).
Output: eval/cases/mini_sets_v1.json
Usage: python -B -X utf8 scripts/make_mini_sets.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_FRAGILE, DEV_STABLE, CHECK_FRAGILE, CHECK_STABLE = 8, 6, 10, 7


def _order(cid: str) -> str:
    return hashlib.sha256(cid.encode("utf8")).hexdigest()


def _fragile(signal: dict) -> bool:
    return any((r["gap"] is not None and r["gap"] < 2) or r["contender"] for r in signal["requirements"])


def _pick(rows, n, twins=None, used=None):
    out, seen = [], set(used or ())
    for r in sorted(rows, key=lambda r: _order(r["case_id"])):
        group = (twins or {}).get(r["case_id"], r["case_id"])
        if group in seen:
            continue
        seen.add(group)
        out.append(r)
        if len(out) == n:
            break
    return out, seen


def main() -> dict:
    rows = list(csv.DictReader((ROOT / "runs/score_by_stage/cases.csv").open(encoding="utf-8-sig")))
    signals = {s["case_id"]: s for s in json.loads((ROOT / "runs/decision_gates/signals.json").read_text("utf8"))}
    roles = json.loads((ROOT / "eval/cases/case_roles_v2.json").read_text("utf8"))["cases"]
    twins = {cid: r["twin_group"] for cid, r in roles.items()}
    for r in rows:
        r["fragile"] = _fragile(signals[r["case_id"]])
        r["correct"] = r["set_exact"] == "True"

    golden = [r for r in rows if r["dataset"] == "golden"]
    dev = [r for r in rows if r["dataset"] == "development"]
    new = [r for r in rows if r["dataset"] == "official"]
    dev_wrong = [r for r in dev if not r["correct"]]
    used = {twins.get(r["case_id"]) for r in golden + dev_wrong}
    fragile, used = _pick([r for r in dev if r["correct"] and r["fragile"]], DEV_FRAGILE, twins, used)
    stable, _ = _pick([r for r in dev if r["correct"] and not r["fragile"]], DEV_STABLE, twins, used)
    dev_mini = golden + dev_wrong + fragile + stable

    check = []
    for cause in ("interpretation", "not_in_pool", "out_of_cards", "selection"):
        part = [r for r in new if r["cause"] == cause]
        check += _pick(part, math.ceil(len(part) / 2))[0]
    check += _pick([r for r in new if r["correct"] and r["fragile"]], CHECK_FRAGILE)[0]
    check += _pick([r for r in new if r["correct"] and not r["fragile"]], CHECK_STABLE)[0]

    def entry(r, role):
        return {"case_id": r["case_id"], "dataset": r["dataset"], "role": role, "baseline": {
            "set_exact": r["correct"], "cause": r["cause"] or None, "tables": r["tables"].split(),
            "interpretation": r["interpretation"], "fragile": r["fragile"]}}

    def role(r):
        return "wrong" if not r["correct"] else "fragile" if r["fragile"] else "stable"

    out = {"version": 1, "source": "417 stored-response replay, baseline 324/417 (split code, commit 13bebbc)",
           "selection": "deterministic sha256(case_id) order; one correct case per twin group in dev_mini",
           "dev_mini": [entry(r, role(r)) for r in dev_mini],
           "check_mini": [entry(r, role(r)) for r in check]}
    (ROOT / "eval/cases/mini_sets_v1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf8")
    return out


if __name__ == "__main__":
    import collections
    result = main()
    for name in ("dev_mini", "check_mini"):
        rows = result[name]
        print(name, len(rows), dict(collections.Counter(r["role"] for r in rows)),
              dict(collections.Counter(r["baseline"]["cause"] for r in rows if r["baseline"]["cause"])))
