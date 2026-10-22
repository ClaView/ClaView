"""Run the frozen claview_v3 share snapshot (model off, network blocked) and dump final states."""

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

QUESTIONS = [
    "2015년부터 M2 말잔 원계열을 월별로 보여줘",
    "최근 5년 소비자물가지수와 생산자물가지수를 비교해줘",
    "지역별이 아닌 전국 소비자동향조사를 찾아줘",
    "2020년부터 2024년까지 경상수지 추이를 보여줘",
    "최근 3년 가계신용을 업권별로 분기별로 보여줘",
    "기업대출 금액을 보여줘",
    "2023년 원/달러 환율을 월별로 보여줘",
    "최근 2년 생산자물가지수를 보여줘",
]
layer = load_layer(include_external_synonyms=False)
out.mkdir(parents=True, exist_ok=True)
for i, q in enumerate(QUESTIONS, 1):
    state = run_outer(q, "router", layer=layer, as_of="2026-10-20", request_id=f"fx{i}")
    keep = {
        k: state.get(k)
        for k in (
            "question",
            "as_of",
            "analysis",
            "selections",
            "selection_groups",
            "selection_route",
            "requirements",
            "unresolved",
            "selection",
        )
    }
    (out / f"v3_fx{i}.json").write_text(
        json.dumps(keep, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    route = (state.get("selection_route") or {}).get("kind")
    print(
        i,
        q,
        "| route:",
        route,
        "| sel:",
        [
            (s["requirement_id"], s["status"], [t["table_id"] for t in s["tables"]])
            for s in state.get("selections") or []
        ],
    )
