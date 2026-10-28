"""Network-blocked plan 17 snapshot with an explicit candidate policy and progress.

The plan 15 snapshot projections are reused unchanged. This does not change a gold,
case role, comparison field or existing baseline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modules" / "a"))

from claview_a_legacy import _guard

_guard.install_network_block()

from claview_a_legacy.agent.graph import run_outer
from claview_a_legacy.catalog.layer import load_layer
from claview_a_legacy.interpret.pipeline import interpret_question
from claview_a_legacy.select.graph import select_tables
from plan15_baseline import interpretation_view, selection_view
from score_selection import load_questions


def main(output: Path, candidate_policy: str) -> None:
    if candidate_policy not in ("legacy", "union"):
        raise ValueError("unknown candidate policy")
    questions = load_questions(None)
    ids = sorted(c for c, q in questions.items() if q["dataset"] in ("golden", "development"))
    layer = load_layer(include_external_synonyms=False)
    snapshot = {}
    for i, cid in enumerate(ids, 1):
        q = questions[cid]
        analysis = interpret_question(q["question"], layer)
        selected = select_tables(analysis, layer, q.get("as_of"), candidate_policy=candidate_policy)
        state = run_outer(q["question"], "straight", layer=layer, as_of=q.get("as_of"),
                          candidate_policy=candidate_policy)
        route = state.get("selection_route") or {}
        snapshot[cid] = {"interpretation": interpretation_view(analysis), "selection": selection_view(selected),
                         "route": {"kind": route.get("kind"), "reasons": route.get("reasons")}}
        if i % 10 == 0:
            print(json.dumps({"done": i, "total": len(ids), "candidate_policy": candidate_policy}), flush=True)
    output.mkdir(parents=True, exist_ok=True)
    (output / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True,
                                                    default=str), encoding="utf-8")
    print(json.dumps({"cases": len(snapshot), "candidate_policy": candidate_policy,
                      "network_attempts": len(_guard.NETWORK_ATTEMPTS)}), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
