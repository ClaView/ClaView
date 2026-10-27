"""Saved-response -> actual interactive graph probe, offline and regression only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modules" / "a"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main(args):
    from claview_a import _guard
    _guard.install_network_block()
    from claview_a.agent.session import run_session
    from claview_a.catalog.layer import load_layer
    from claview_a.contract.check import check_analysis
    from claview_a.development_reverify import (
        ARCHIVE,
        FrozenSource,
        call_file,
        store_hashes,
    )
    from claview_a.interpret.decompose_run import DecompositionConfig

    args.out.mkdir(parents=True, exist_ok=False)
    layer = load_layer(include_external_synonyms=False)
    before = store_hashes(args.store)
    entries = read(args.splice / "exports.json")
    selected, seen, kinds = [], set(), set()
    # Data-driven sampling of compatible saved primary readings. Cover both ready
    # and unready selections, then other questions, without case/table branches.
    for ready in (False, True):
        for entry in entries:
            if entry["reading"] != "primary" or entry["source"] != "model":
                continue
            if bool(entry["bundles"]) == ready and entry["case_id"] not in seen:
                selected.append(entry)
                seen.add(entry["case_id"])
                break
    for entry in entries:
        if len(selected) >= args.limit:
            break
        if entry["reading"] == "primary" and entry["case_id"] not in seen:
            selected.append(entry)
            seen.add(entry["case_id"])
    rows = []
    for entry in selected:
        stored = read(args.splice / "states" / entry["file"])
        archive = read(call_file(args.store / ARCHIVE, entry["call_id"]))
        source = FrozenSource(archive["raw_response"], archive["request"]["input"],
                              archive["request"]["text"]["format"]["schema"])
        state = run_session(stored["query"], layer=layer, as_of=stored["as_of"],
            request_id="offline:" + entry["call_id"], decomposition=DecompositionConfig(
                mode="gpt", execution_mode="replay", proposal_source=source, as_of=stored["as_of"]))
        assert source.calls == 1 and not source.mismatch
        assert check_analysis(state["analysis"]) == []
        assert state["response"]["decomposition"]["real_calls"] == 0
        kinds.add(state["response"]["status"])
        (args.out / entry["file"]).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append({"call_id": entry["call_id"], "case_id": entry["case_id"],
                     "status": state["response"]["status"], "path": state["control"]["path"],
                     "eligible": state["selection_route"]["eligible"],
                     "skipped": state["selection_route"]["skipped"],
                     "assumptions": len(state["response"]["assumptions"]["items"]),
                     "b_status": state["response"]["b"]["status"],
                     "b_reason": state["response"]["b"].get("reason")})
    assert before == store_hashes(args.store)
    assert not _guard.NETWORK_ATTEMPTS
    report = {"cases": rows, "real_calls": 0, "network_attempts": 0, "archive_unchanged": True,
              "statuses": sorted(kinds), "B": "not executed by this probe; validate via the separate B offline probe"}
    (args.out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--splice", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=4)
    main(parser.parse_args())
