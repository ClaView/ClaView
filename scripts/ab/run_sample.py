"""Run one golden case through A v1 -> B -> fetch offline and save the result.

A runs with model off, B uses the golden B answer as a scripted model reply, and fetch
uses synthetic rows (value 1). This checks the wiring only: no model or KOSIS call and
no accuracy claim.

    uv run --locked python scripts/ab/run_sample.py CV-AB-19
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from test_ab_pipeline import GOLD, SyntheticDataClient  # noqa: E402

from claview.ab_pipeline import load_a_catalog, run_ab  # noqa: E402
from claview.contracts import Config, Trace, today_iso  # noqa: E402
from claview.evaluation import scripted_answers  # noqa: E402
from claview.metadata import MetadataStore  # noqa: E402
from claview.models import ScriptedModel  # noqa: E402
from claview.search import SearchIndex  # noqa: E402
from claview.services import Services  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_id", nargs="?", default="CV-AB-19")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "ab-sample")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today, the query date)")
    args = parser.parse_args()

    case = GOLD[args.case_id]
    store = MetadataStore(
        ROOT / "data/catalog/search_catalog.json", ROOT / "data/raw/boK_meta_raw.jsonl"
    )
    answers = {k: v for k, v in scripted_answers(case).items() if k != "A"}
    as_of = args.as_of or today_iso()
    services = Services(
        store, SearchIndex(store), ScriptedModel(answers), Config(as_of=as_of), Trace()
    )
    final = run_ab(
        case["query"],
        services,
        load_a_catalog(ROOT),
        data_client=SyntheticDataClient(),
        through="fetch",
    )
    summary = {
        "case_id": args.case_id,
        "query": case["query"],
        "as_of": as_of,
        "mode": {"A": "model off", "B": "scripted golden reply", "data": "synthetic rows"},
        "a_tables": final.get("a_result", {}).get("tables"),
        "expected_tables": case["expected"]["A"]["tables"],
        "b_selections": len((final.get("b_result") or {}).get("selections", [])),
        "data_series": len((final.get("data_result") or {}).get("series", [])),
        "response": final["response"],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.case_id}.json"
    path.write_text(json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {path}")
    return 0 if final["response"]["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
