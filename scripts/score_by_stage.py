"""Stage-by-stage scoring of the 417 stored-response replays (no live call, pipeline unchanged).

Per case:
- interpretation: requirement count vs answer-group count -> ok / under / over, and the requirement source.
- candidates: per answer group, is any allowed table in the selection pool (S6 pool after S7) and in the
  T-stage candidates (J7 cards: top-k per requirement plus the Q6G hint)?
- selection: set_exact, the existing scoring (eval/table_sets.score_sets).
- cause of a wrong case, the first that applies: interpretation (count differs) -> not_in_pool ->
  out_of_cards (in the pool but not a card) -> selection (a card, another table picked).
The 417 benchmark scores every case: its corpus already leaves out the roles v2 clarify and boundary cases
(clarify_v2 counts them, expected 0).

Usage: PYTHONHASHSEED=0 python -B -X utf8 scripts/score_by_stage.py [out_dir] [--set dev_mini|check_mini]
  default out_dir runs/score_by_stage. --set runs only that reduced set (eval/cases/mini_sets_v1.json) and
  compares each case with its recorded baseline (fixed / broken / tables changed).
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAUSES = ("interpretation", "not_in_pool", "out_of_cards", "selection")
DATASETS = ("golden", "development", "official")


def interpretation_fit(n_requirements: int, n_groups: int) -> str:
    return "ok" if n_requirements == n_groups else "under" if n_requirements < n_groups else "over"


def stage_row(state: dict, groups: list[list[str]], set_exact: bool) -> dict:
    """Stage verdicts of one final State against its answer groups."""
    pool = {p["table_id"] for p in state["pool"]}
    cards = {c["table_id"] for c in state["cards"]}
    in_pool = [any(t in pool for t in g) for g in groups]
    in_cards = [any(t in cards for t in g) for g in groups]
    fit = interpretation_fit(len(state["requirements"]), len(groups))
    cause = None
    if not set_exact:
        cause = ("interpretation" if fit != "ok" else "not_in_pool" if not all(in_pool)
                 else "out_of_cards" if not all(in_cards) else "selection")
    status = state.get("interpretation_status") or {}
    return {"n_requirements": len(state["requirements"]), "n_groups": len(groups), "interpretation": fit,
            "requirement_source": status.get("requirement_source"), "groups_in_pool": sum(in_pool),
            "groups_in_cards": sum(in_cards), "set_exact": set_exact, "cause": cause}


def main(out_dir: Path, subset: str | None = None) -> dict:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval"), str(ROOT / "tests/contracts")]
    import test_a_v1_frozen as F
    from aflow_support import blocked_transports
    from claview.aflow.catalog import default_catalog
    from claview.aflow.graph import build_graph
    from claview.aflow.run import run_a_v1_state
    from table_sets import load_case_roles, score_sets

    cases = json.loads((F.CORPUS / "data/questions_417.json").read_text("utf8"))
    baseline = None
    if subset:
        baseline = {r["case_id"]: r["baseline"] for r in
                    json.loads((ROOT / "eval/cases/mini_sets_v1.json").read_text("utf8"))[subset]}
        cases = [c for c in cases if c["case_id"] in baseline]
    groups = collections.defaultdict(dict)
    for line in (F.CORPUS / "data/Q_candidate_group.jsonl").read_text("utf8").splitlines():
        row = json.loads(line)
        groups[row["case_id"]][row["group_index"]] = row["allowed_tables"]
    roles = load_case_roles(ROOT / "eval/cases/case_roles_v2.json")
    catalog, client, graph, rows = default_catalog(ROOT), F._Replay(), build_graph(), []
    with blocked_transports():
        for case in cases:
            client.case = cid = case["case_id"]
            state = run_a_v1_state(case["question"], catalog, client=client, as_of=case["as_of"], graph=graph)
            g = [groups[cid][i] for i in sorted(groups[cid])]
            tables = [t["table_id"] for t in state["a_result"]["tables"]]
            ok = score_sets(g, tables, [r["table_id"] for r in state["ranked"][:5]],
                            [p["table_id"] for p in state["pool"]], state["k"])["set_exact"]
            rows.append({"case_id": cid, "dataset": case["dataset"], **stage_row(state, g, ok),
                         "clarify_v2": bool(roles.get(cid, {}).get("clarify")), "tables": " ".join(tables)})
    assert not client.misses, client.misses
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {}
    for ds in (*DATASETS, "all"):
        part = [r for r in rows if ds == "all" or r["dataset"] == ds]
        summary[ds] = {"cases": len(part), "correct": sum(r["set_exact"] for r in part),
                       "wrong_by_cause": {c: sum(r["cause"] == c for r in part) for c in CAUSES},
                       "interpretation_fit": dict(collections.Counter(r["interpretation"] for r in part)),
                       "requirement_source": dict(collections.Counter(r["requirement_source"] for r in part)),
                       "correct_with_count_mismatch": sum(r["set_exact"] and r["interpretation"] != "ok" for r in part),
                       "clarify_v2": sum(r["clarify_v2"] for r in part)}
    if baseline is None:
        assert summary["all"]["correct"] == 324, summary["all"]["correct"]
    else:
        by_id = {r["case_id"]: r for r in rows}
        summary["vs_baseline"] = {
            "baseline_correct": sum(b["set_exact"] for b in baseline.values()),
            "fixed": sorted(c for c, b in baseline.items() if not b["set_exact"] and by_id[c]["set_exact"]),
            "broken": sorted(c for c, b in baseline.items() if b["set_exact"] and not by_id[c]["set_exact"]),
            "tables_changed": sorted(c for c, b in baseline.items() if b["tables"] != by_id[c]["tables"].split())}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf8")
    return summary


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--set")]
    subset = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--set=")), None)
    default = ROOT / ("runs/score_by_stage" if not subset else f"runs/score_by_stage_{subset}")
    print(json.dumps(main(Path(args[0]) if args else default, subset),
                     ensure_ascii=False, indent=1))
