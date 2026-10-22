"""Period conditions as a snapshot of the A working tree writes them (rules only, network blocked).

Usage: make_a_period_shapes.py <snapshot root> <output json>. Each entry keeps the question, A's
conditions (period and frequency only) and A's own `period_diagnosis` of every period.
"""

import json
import sys
from pathlib import Path

snap = Path(sys.argv[1])
out = Path(sys.argv[2])
sys.path.insert(0, str(snap / "src"))
from claview_v3 import _guard  # noqa: E402

_guard.install_network_block()
from claview_v3.agent.graph import run_outer  # noqa: E402
from claview_v3.catalog.layer import load_layer  # noqa: E402
from claview_v3.interpret.condition_fields import period_diagnosis  # noqa: E402

QUESTIONS = [
    "최근 3개월 소비자물가지수를 보여줘",
    "지난 분기 가계신용을 보여줘",
    "지난달 원/달러 환율을 보여줘",
    "최근 6개월 수출입물가지수를 월별로 보여줘",
]
layer = load_layer(include_external_synonyms=False)
entries = []
for i, question in enumerate(QUESTIONS, 1):
    state = run_outer(
        question, "router", layer=layer, as_of="2026-10-20", request_id=f"ps{i}",
        decomposition={"mode": "rules"},
    )  # fmt: skip
    conditions = [
        c
        for c in (state.get("analysis") or {}).get("conditions") or []
        if c.get("kind") in ("period", "frequency")
    ]
    diagnosis = {
        c["id"]: period_diagnosis(c["attrs"], "2026-10-20")
        for c in conditions
        if c["kind"] == "period"
    }
    entries.append({"question": question, "conditions": conditions, "a_diagnosis": diagnosis})
    print(i, question, [(c["id"], c["attrs"].get("extract_status")) for c in conditions], diagnosis)
out.write_text(json.dumps(entries, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
