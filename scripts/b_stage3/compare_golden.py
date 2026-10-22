import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from claview.b.period_resolver import resolve_period_for_table  # noqa: E402
from claview.metadata import MetadataStore  # noqa: E402

store = MetadataStore(
    root / "data/catalog/search_catalog.json", root / "data/raw/boK_meta_raw.jsonl"
)
golden = json.loads((root / "eval/cases/golden_set_v1.json").read_text(encoding="utf-8"))["cases"]
conds = json.loads(
    (root / "tests/b_v2/fixtures/golden_conditions/golden_conditions.json").read_text(
        encoding="utf-8"
    )
)
same = diff = 0
for case in golden:
    rows = case["expected"]["B"]["selections"]
    expected = {}
    for r in rows:
        expected.setdefault((r["org_id"], r["table_id"]), set()).add(
            (r["period"]["frequency"], r["period"]["start"], r["period"]["end"])
        )
    for (org, tid), periods in expected.items():
        c = conds[case["case_id"]]
        res = resolve_period_for_table(
            c["conditions"], store.get(org, tid), "2026-10-01", c["calc_hints"]
        )
        got = (
            (res["applied"]["frequency"], res["applied"]["start"], res["applied"]["end"])
            if res["status"] == "ok"
            else (res["status"], res["issue"]["code"], res["issue"]["detail"])
        )
        ok = got in periods and len(periods) == 1
        same += ok
        diff += not ok
        if not ok:
            print(case["case_id"], tid, "| expected", sorted(periods), "| got", got)
            print("   query:", case["query"])
            print(
                "   conds:",
                json.dumps(
                    [
                        (c["kind"], c["quote"], c["value"])
                        for c in conds[case["case_id"]]["conditions"]
                    ],
                    ensure_ascii=False,
                ),
            )
print("same", same, "diff", diff)
