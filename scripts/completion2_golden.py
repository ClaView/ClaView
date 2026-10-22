"""Read-only golden labels, rules interpretation, interactive A, no B/network."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def main(args):
    from score_selection import load_groups, load_questions, output_tables

    from claview_v3 import _guard, paths
    from claview_v3.agent.session import run_session
    from claview_v3.catalog.layer import load_layer
    from claview_v3.reused.table_sets import score_sets
    from claview_v3.select.graph import select_tables

    _guard.install_network_block()
    args.out.mkdir(parents=True, exist_ok=False)
    protected = [paths.QUESTIONS_417, paths.ANSWER_GROUPS]
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    questions = {k: v for k, v in load_questions().items() if v["dataset"] == "golden"}
    groups = load_groups(set(questions))
    layer = load_layer(include_external_synonyms=False)
    rows, diagnostics = [], []
    for cid, q in sorted(questions.items()):
        state = run_session(q["question"], layer=layer, as_of=q.get("as_of"), request_id="golden:" + cid)
        own = groups.get(cid, [])
        selected = output_tables(state["selections"])
        eligible = set(state["selection_route"]["eligible"])
        runnable = output_tables([s for s in state["selections"] if s["requirement_id"] in eligible])
        legacy = output_tables(select_tables(state["analysis"], layer, state["as_of"])["selections"])
        pool = sorted({t for s in state["selections"] for t in s["signals"]["plan17"]["candidate_ids"]})
        score = score_sets(own, selected, [], pool, 5)
        old = score_sets(own, legacy, [], [], 5)
        ready = score_sets(own, runnable, [], pool, 5)
        row = {"case_id": cid, "question": q["question"], "as_of": state["as_of"],
               "expected_groups": own, "tables": selected, "legacy_tables": legacy,
               "set_exact": score["set_exact"], "legacy_set_exact": old["set_exact"],
               "eligible_set_exact": ready["set_exact"], "eligible_tables": runnable,
               "missing": score["missing"], "extra": score["extra"], "candidate_ids": pool,
               "expected_groups_in_pool": [any(t in pool for t in group) for group in own],
               "route": state["response"]["status"],
               "question_to_user": state["response"]["question"], "skipped": state["selection_route"]["skipped"],
               "requirements": state["analysis"].get("interpretation", {}).get("requirements"),
               "certain": state["analysis"]["judgement"].get("certain"),
               "automatic_progress_unsettled": state["response"]["automatic_progress_unsettled"],
               "assumptions": state["response"]["assumptions"]}
        rows.append(row)
        # Keep requirement-local assignment ranks; flattening several requirements
        # into one top-k list would manufacture a global search rank.
        diagnostics.append({"case_id": cid, "analysis": state["analysis"],
                            "selections": state["selections"]})
        print(json.dumps({k: row[k] for k in ("case_id", "set_exact", "legacy_set_exact", "route")}), flush=True)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    assert hashes == after and not _guard.NETWORK_ATTEMPTS
    summary = {"cases": len(rows), "rules_only": True, "model_calls": 0, "B_calls": 0, "network_attempts": 0,
               "set_exact": sum(r["set_exact"] for r in rows),
               "legacy_set_exact_same_interpretation": sum(r["legacy_set_exact"] for r in rows),
               "eligible_set_exact": sum(r["eligible_set_exact"] for r in rows),
               "routes": dict(collections.Counter(r["route"] for r in rows)),
               "automatic_wrong_table_candidates": [r["case_id"] for r in rows if r["eligible_tables"] and not r["eligible_set_exact"]],
               "fixed": [r["case_id"] for r in rows if r["set_exact"] and not r["legacy_set_exact"]],
               "broken": [r["case_id"] for r in rows if not r["set_exact"] and r["legacy_set_exact"]],
               "label_hashes": after,
               "note": "Existing golden 19 reused for development; not independent/model/value accuracy. Waiting answers not auto-applied."}
    for name, value in (("cases.json", rows), ("summary.json", summary), ("diagnostics.json", diagnostics)):
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    main(parser.parse_args())
