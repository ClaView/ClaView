"""Offline 417 replay dump: per-case tables, b_handoff sha, state sha (new fields excluded) and totals.

Usage: PYTHONHASHSEED=0 python -B -X utf8 scripts/stage_split_replay_dump.py <repo_root> <out.json>  (run before and after, then compare cases)
Reuses the replay client and transport block of tests/contracts/test_a_v1_frozen.py (no live call).
"""
import collections, hashlib, json, sys
from pathlib import Path

repo, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
sys.path[:0] = [str(repo / "src"), str(repo / "eval"), str(repo / "tests/contracts")]
import test_a_v1_frozen as F  # noqa: E402
from claview.b.aflow.catalog import default_catalog  # noqa: E402
from claview.b.aflow.graph import build_graph  # noqa: E402
from claview.b.aflow.run import run_a_v1_state  # noqa: E402
from aflow_support import blocked_transports  # noqa: E402
from table_sets import score_sets  # noqa: E402

NEW_FIELDS = {"interpretation_status", "selection_status"}
cases = json.loads((F.CORPUS / "data/questions_417.json").read_text("utf8"))
groups = collections.defaultdict(dict)
for line in (F.CORPUS / "data/Q_candidate_group.jsonl").read_text("utf8").splitlines():
    r = json.loads(line); groups[r["case_id"]][r["group_index"]] = r["allowed_tables"]
catalog, client, rows, tot = default_catalog(repo), F._Replay(), {}, collections.Counter()
import claview.b.aflow.contract as C  # noqa: E402
boundary = []
if hasattr(C, "HANDOFF_FIELDS"):  # split code: selection reads must stay inside handoff + its own fields
    own = frozenset(C.STATE_FIELDS) - C.HANDOFF_FIELDS
    def audit(name, reads, writes, context):
        if reads & C.STATUS_FIELDS:
            boundary.append((client.case, name, "status_read"))
        if name in C.SELECT_NODES or name == "route_after_s7":
            if reads - C.HANDOFF_FIELDS - own or "catalog.q6g_tables" in context:
                boundary.append((client.case, name, sorted(reads - C.HANDOFF_FIELDS - own)))
        if name in C.NODE_READS:
            for got, declared in ((reads, C.NODE_READS), (writes, C.NODE_WRITES), (context, C.NODE_CONTEXT_READS)):
                if got - declared[name]:
                    boundary.append((client.case, name, "undeclared", sorted(got - declared[name])))
    graph = build_graph(audit=audit)
else:
    graph = build_graph()
with blocked_transports():
    for case in cases:
        client.case = case["case_id"]
        s = run_a_v1_state(case["question"], catalog, client=client, as_of=case["as_of"], graph=graph)
        tables = [t["table_id"] for t in s["a_result"]["tables"]]
        ok = score_sets([groups[case["case_id"]][i] for i in sorted(groups[case["case_id"]])], tables,
                        [r["table_id"] for r in s["ranked"][:5]], [p["table_id"] for p in s["pool"]], s["k"])["set_exact"]
        stable = {k: v for k, v in F._stable(s).items() if k not in NEW_FIELDS}
        rows[case["case_id"]] = {"tables": tables, "confidence": s["confidence"], "b_handoff": F._sha(s["b_handoff"]),
                                 "state": F._sha(stable), "state_matches_recorded": F._sha(stable) == F.STATE_SHA256[case["case_id"]],
                                 "new": {k: s.get(k) for k in NEW_FIELDS if k in s}}
        tot["all"] += ok; tot[case["dataset"]] += ok
        tot["high"] += s["confidence"] == "high"; tot["wrong_high"] += s["confidence"] == "high" and not ok
        tot["fallback"] += s["interpretation"]["model"]["status"] == "fallback"
tot["misses"] = len(client.misses)
tot["boundary_violations"] = len(boundary)
tot["state_hash_match"] = sum(r["state_matches_recorded"] for r in rows.values())
tot["b_handoff_match"] = sum(r["b_handoff"] == F.B_HANDOFF_BASELINE[c] for c, r in rows.items())
out.write_text(json.dumps({"totals": tot, "boundary": boundary, "cases": rows}, ensure_ascii=False, indent=1), "utf8")
print(json.dumps(tot))
