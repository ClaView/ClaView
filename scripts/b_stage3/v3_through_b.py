import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from claview.b.adapter_v3 import to_b_request  # noqa: E402
from claview.b.run import run_b_request  # noqa: E402
from claview.b.contracts import Config, Trace  # noqa: E402
from claview.b.metadata import MetadataStore  # noqa: E402
from claview.b.models import ScriptedModel  # noqa: E402
from claview.b.services import Services  # noqa: E402

store = MetadataStore(
    root / "data/catalog/search_catalog.json", root / "data/raw/boK_meta_raw.jsonl"
)
folder = sys.argv[2] if len(sys.argv) > 2 else "v3"
for path in sorted((root / "tests/b_v2/fixtures" / folder).glob("*_fx*.json")):
    state = json.loads(path.read_text(encoding="utf-8"))
    request = to_b_request(state)
    responses = {}
    for scope in request["scopes"]:
        for table in scope["tables"]:
            meta = store.get(table["org_id"], table["table_id"])
            row = {
                "item_id": next(iter(meta["items"])),
                "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
                "expand": {a: "none" for a in meta["dimensions"]},
            }
            responses.setdefault(f"B:{table['org_id']}:{table['table_id']}", []).append(
                {"selections": [row]}
            )
    services = Services(store, None, ScriptedModel(responses), Config(as_of="2026-10-20"), Trace())
    result = run_b_request(request, services)
    print(path.stem, state["question"])
    for s in result["scopes"]:
        p, f = s["period"] or {}, s["frequency"] or {}
        print(
            "  ",
            s["scope_id"],
            s["status"],
            [i["code"] for i in s["issues"]],
            "| freq",
            f.get("chosen"),
            f.get("source"),
            f.get("rule"),
            "| period",
            (p.get("applied") or {}),
            p.get("source"),
            p.get("rule"),
            "| assumed",
            [a["dimension"] for a in s["assumptions"]],
            "| slots",
            s.get("condition_fields"),
            "| shared",
            [(a["reason_code"], a["adopted"]["value"]) for a in s.get("shared_assumptions") or []],
        )
    for k in result["skipped"]:
        print("   skipped", k)
