"""Plan 07 examples: A's public assumption envelope through the adapter and B (offline, synthetic
model answers built from the metadata). Prints JSON.

Usage: a_assumption_examples.py <repo root>
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from claview.b.adapter_v3 import to_b_request  # noqa: E402
from claview.b.contract import check_b_result  # noqa: E402
from claview.b.run import run_b_request  # noqa: E402
from claview.contracts import Config, Trace  # noqa: E402
from claview.metadata import MetadataStore  # noqa: E402
from claview.models import ScriptedModel  # noqa: E402
from claview.services import Services  # noqa: E402

store = MetadataStore(
    root / "data/catalog/search_catalog.json", root / "data/raw/boK_meta_raw.jsonl"
)
state = json.loads((root / "tests/b_v2/fixtures/v3cur/v3cur_fx6.json").read_text(encoding="utf-8"))
envelope = {
    "version": "a-assumptions/2026-10-21.1",
    "items": [
        {
            "assumption_id": "a:0f3c",
            "stage": "A",
            "requirement_ids": ["r1"],
            "requirement_keys": [{"reading_id": "primary", "requirement_id": "r1"}],
            "refs": {"segment_ids": [], "mention_ids": [], "condition_ids": []},
            "reason_code": "first_assignment",
            "adopted": {"value": None, "interpretation": "실제로 채택한 내용"},
            "source": "default",
            "confirmed": False,
        }
    ],
}


def show(title, value):
    print(f"\n## {title}\n" + json.dumps(value, ensure_ascii=False, indent=1))


def answers(request):
    out = {}
    for sc in request["scopes"]:
        for t in sc["tables"]:
            meta = store.get(t["org_id"], t["table_id"])
            row = {
                "item_id": next(iter(meta["items"])),
                "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
                "expand": {a: "none" for a in meta["dimensions"]},
            }
            out[f"B:{t['org_id']}:{t['table_id']}"] = [{"selections": [row]}]
    return out


for title, given in (
    ("1 normal envelope", envelope),
    ("2 unsupported version", {**envelope, "version": "a-assumptions/2099-01-01.1"}),
):
    request = to_b_request(state, a_assumptions=given)
    services = Services(
        store, None, ScriptedModel(answers(request)), Config(as_of="2026-10-20"), Trace()
    )
    result = run_b_request(request, services)
    [scope] = result["scopes"]
    show(
        title,
        {
            "request.contract_version": request["contract_version"],
            "request.a_assumptions": request["a_assumptions"],
            "request.input_errors": request.get("input_errors", []),
            "result.a_assumptions": result["a_assumptions"],
            "result.scope.status": scope["status"],
            "result.scope.issues": scope["issues"],
            "result.scope.shared_assumptions": scope.get("shared_assumptions", []),
            "check_b_result": check_b_result(result, request),
        },
    )
