"""Store the question interpretation's period, frequency and calc conditions of the golden queries.

Run with the frozen claview_v3 share snapshot (model off, network blocked):

    python -I make_golden_conditions.py <share snapshot dir> <golden_set_v1.json> <out.json>

Only the conditions (period / frequency / calc) and the calc hints are kept per case, so the
B stage-2 check can compare the shared resolver with the text-reading B path on the same questions.
"""

import json
import sys
from pathlib import Path

snapshot, golden, out = (Path(a) for a in sys.argv[1:4])
sys.path.insert(0, str(snapshot / "src"))
from claview_v3 import _guard  # noqa: E402

_guard.install_network_block()
from claview_v3.catalog.layer import load_layer  # noqa: E402
from claview_v3.interpret.pipeline import interpret_question  # noqa: E402

KINDS = ("period", "frequency", "calc")
layer = load_layer(include_external_synonyms=False)
cases = json.loads(golden.read_text(encoding="utf-8"))["cases"]
rows = {}
for case in cases:
    analysis = interpret_question(case["query"], layer)
    rows[case["case_id"]] = {
        "query": case["query"],
        "conditions": [
            {k: c[k] for k in ("id", "kind", "quote", "value", "attrs")}
            for c in analysis["conditions"]
            if c["kind"] in KINDS
        ],
        "calc_hints": [
            {"quote": h.get("quote"), "kind": h.get("kind")}
            for h in (analysis.get("judgement") or {}).get("calc_hints") or []
        ],
    }
out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(rows), "cases")
