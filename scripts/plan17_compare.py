"""Read-only scoring of two stored rules snapshots; writes only the requested report.

Does not execute models or selection. Gold groups and dev_mini membership are read
unchanged. Automatic table-set errors are not a measure of interpretation accuracy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claview.a import _guard

_guard.install_network_block()

from claview.a.reused.table_sets import score_sets
from score_selection import load_groups


def compare(before: dict, after: dict, groups: dict, mini: set[str]) -> dict:
    if set(before) != set(after):
        raise ValueError("snapshot case domains differ")
    rows = []
    for cid in sorted(before):
        a, b = before[cid], after[cid]
        old, new = a["selection"]["tables"], b["selection"]["tables"]
        gs = groups.get(cid, [])
        scored = [score_sets(gs, tables, [], [], 5)["set_exact"] for tables in (old, new)]
        auto = [bool(x["selection"]["tables"]) and (x.get("route") or {}).get("kind") == "proceed"
                for x in (a, b)]
        rows.append({"case_id": cid, "dev_mini": cid in mini,
                     "interpretation_changed": a["interpretation"] != b["interpretation"],
                     "selection_changed": a["selection"] != b["selection"],
                     "route_changed": a.get("route") != b.get("route"),
                     "before_tables": old, "after_tables": new,
                     "before_exact": scored[0], "after_exact": scored[1],
                     "before_auto_table_error": auto[0] and not scored[0],
                     "after_auto_table_error": auto[1] and not scored[1],
                     "kind": "1_to_n" if len(b["interpretation"]["requirements"]) == 1 and len(gs) > 1
                             else "n_to_1" if len(b["interpretation"]["requirements"]) > 1 and len(gs) == 1
                             else "other"})

    def summary(rs):
        return {"cases": len(rs),
                "interpretation_changes": sum(r["interpretation_changed"] for r in rs),
                "selection_changes": sum(r["selection_changed"] for r in rs),
                "route_changes": sum(r["route_changed"] for r in rs),
                "before_exact": sum(r["before_exact"] for r in rs),
                "after_exact": sum(r["after_exact"] for r in rs),
                "fixed": sum(not r["before_exact"] and r["after_exact"] for r in rs),
                "regressed": sum(r["before_exact"] and not r["after_exact"] for r in rs),
                "changed_both_wrong": sum(not r["before_exact"] and not r["after_exact"]
                                          and r["selection_changed"] for r in rs),
                "before_auto_table_errors": sum(r["before_auto_table_error"] for r in rs),
                "after_auto_table_errors": sum(r["after_auto_table_error"] for r in rs)}

    return {"all": summary(rows), "dev_mini": summary([r for r in rows if r["dev_mini"]]),
            "by_kind": {k: summary([r for r in rows if r["kind"] == k]) for k in ("1_to_n", "n_to_1", "other")},
            "rows": rows, "network_attempts": len(_guard.NETWORK_ATTEMPTS),
            "limits": ["Stored rules-path results; not GPT accuracy or independent evaluation.",
                       "Automatic error means route=proceed with a wrong table set, not interpretation correctness.",
                       "Snapshots do not identify the stage that lost an answer candidate."]}


if __name__ == "__main__":
    before_path, after_path, output_path = map(Path, sys.argv[1:4])
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    mini = json.loads((ROOT / "eval/cases/mini_sets_v1.json").read_text(encoding="utf-8"))
    result = compare(before, after, load_groups(set(before)), {r["case_id"] for r in mini["dev_mini"]})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("all", "dev_mini", "by_kind", "network_attempts")},
                     ensure_ascii=False))
    if result["all"]["interpretation_changes"]:
        raise SystemExit(1)
