"""Read-only capability probe of real B with final A outputs; no B model/fetch."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


def main(args):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    sys.path.insert(0, str(args.b_root / "src"))
    from claview_v3 import _guard
    _guard.install_network_block()
    from claview.b.adapter_v3 import to_b_request
    from claview.b.contract import check_request

    rows = []
    for file in sorted(args.input.glob("*.json")):
        if file.name == "summary.json":
            continue
        state = json.loads(file.read_text(encoding="utf-8"))
        eligible = set((state.get("selection_route") or {}).get("eligible") or [])
        if not eligible:
            continue
        payload = {**state, "query": state["question"], "selection_route": {"kind": "proceed"},
                   "selections": [s for s in state["selections"] if s["requirement_id"] in eligible]}
        request = to_b_request(payload)
        base_errors = check_request(request)
        extended = copy.deepcopy(request)
        extended["a_assumptions"] = state["response"]["assumptions"]
        errors = check_request(extended)
        rows.append({"file": file.name, "base_errors": base_errors, "a_assumptions_errors": errors,
                     "adapter_input_errors": request.get("input_errors") or [], "scopes": len(request["scopes"])})
    assert rows and not _guard.NETWORK_ATTEMPTS
    report = {"rows": rows, "real_calls": 0, "synthetic_model_calls": 0, "fetch_calls": 0,
              "network_attempts": 0, "note": "Read-only request validation; not B execution completion"}
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--b-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
