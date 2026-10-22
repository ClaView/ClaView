"""Run only selection on immutable development interpretations, with networking blocked.

This is a selector regression, not a model replay or a new model accuracy measurement.
Input files are never modified; output must be a new directory outside the archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claview_v3 import _guard

_guard.install_network_block()

from claview_v3.catalog.layer import load_layer
from claview_v3.select.graph import select_tables
from plan15_baseline import selection_view


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def run(source: Path, output: Path, policy: str, selection_mode: str = "legacy"):
    source, output = source.resolve(), output.resolve()
    if output == source or source in output.parents or output.exists():
        raise ValueError("output must be new and outside the source archive")
    paths = sorted(p for p in source.glob("*.json") if len(p.stem) == 64
                   and all(c in "0123456789abcdef" for c in p.stem))
    if not paths:
        raise ValueError("no frozen results")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    layer = load_layer(include_external_synonyms=False)
    rows = []
    for n, path in enumerate(paths, 1):
        old = json.loads(path.read_text(encoding="utf-8"))
        analysis = old["analysis"]
        before = digest(analysis)
        got = select_tables(analysis, layer, old.get("as_of"), candidate_policy=policy, selection_mode=selection_mode)
        assert digest(analysis) == before, "selection mutated its interpretation"
        rows.append({"call_id": old["call_id"], "case_id": old["case_id"],
                     "profile_id": old["profile_id"], "repeat": old["repeat"],
                     "source": analysis.get("decomposition", {}).get("applied_source", "rules"),
                     "analysis_hash": before, "selection": selection_view(got),
                     "queries": {s["requirement_id"]: s["signals"].get("queries", []) for s in got["selections"]},
                     "pool": {s["requirement_id"]: s["signals"]["pool"] for s in got["selections"]},
                     "plan17": {s["requirement_id"]: {k: (s["signals"].get("plan17") or {}).get(k)
                                for k in ("gaps", "execution_status", "b_handoff_ready", "bundle")}
                                for s in got["selections"]} if selection_mode == "refutation" else {}})
        if n % 10 == 0:
            print(json.dumps({"done": n, "total": len(paths)}), flush=True)
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert not _guard.NETWORK_ATTEMPTS
    output.mkdir(parents=True)
    report = {"purpose": "frozen interpretation / selector regression only", "candidate_policy": policy,
              "selection_mode": selection_mode,
              "source_hashes": hashes, "network_attempts": 0, "model_calls": 0, "rows": rows}
    (output / "snapshot.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"results": len(rows), "questions": len({r['case_id'] for r in rows}),
                      "network_attempts": 0, "model_calls": 0}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--candidate-policy", choices=("legacy", "union"), default="legacy")
    parser.add_argument("--selection-mode", choices=("legacy", "refutation"), default="legacy")
    args = parser.parse_args()
    run(args.source, args.output, args.candidate_policy, args.selection_mode)
