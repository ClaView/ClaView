"""Run the A subgraph on the golden cases and report node-level metrics.

Usage (from the repository root):
    python eval/run_a.py
    python eval/run_a.py --data-root reference/baseline-1006/data \
        --cases reference/baseline-1006/eval/cases/golden_set_v1.json --out ../clabi/claview-runs

No external API or model calls. Results are written outside the repository by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claview.a.catalog import Catalog  # noqa: E402
from claview.a.context import Context  # noqa: E402
from claview.a.pipeline import NODES, run  # noqa: E402


def evaluate(cases: list[dict], ctx: Context) -> tuple[list[dict], dict]:
    rows = []
    for case in cases:
        gold = [t["table_id"] for t in case["expected"]["A"]["tables"]]
        state = run(case["query"], ctx, mode="eval", gold_table_ids=gold)
        g = gold[0]
        ranked = [r["table_id"] for r in state.get("ranked", [])]
        final = state["a_result"]["tables"][0]["table_id"] if state["a_result"]["tables"] else None
        log = state["trace"].get("search", {})
        rows.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "gold": g,
                "channel_rank": {ch: ranks.get(g) for ch, ranks in log.get("channels", {}).items()},
                "pool_rank": log.get("pool_rank", {}).get(g),
                "ranked_rank": ranked.index(g) + 1 if g in ranked else None,
                "in_top_k": g in ranked[: state.get("k", ctx.k)],
                "final": final,
                "correct": final == g,
                "confidence": state.get("confidence"),
                "issues": state.get("issues", []),
                "timings_ms": state["trace"].get("timings_ms", {}),
            }
        )
    n = len(rows)
    summary = {
        "cases": n,
        "in_pool": sum(r["pool_rank"] is not None for r in rows),
        "in_top_k": sum(r["in_top_k"] for r in rows),
        "top1_correct": sum(r["correct"] for r in rows),
        "mrr": round(sum(1 / r["ranked_rank"] for r in rows if r["ranked_rank"]) / n, 3) if n else 0,
        "confidence": dict(Counter(r["confidence"] for r in rows)),
        "k": ctx.k,
        "model_calls": 0,
        "node_status": {node_id: status for node_id, _, status in NODES},
    }
    return rows, summary


def write_report(out_dir: Path, rows: list[dict], summary: dict, args: argparse.Namespace) -> None:
    out_dir.mkdir(parents=True, exist_ok=False)
    meta = {"created": datetime.now().isoformat(timespec="seconds"), "data_root": str(args.data_root), "cases": str(args.cases)}
    (out_dir / "results.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n = summary["cases"]
    lines = [
        "# A 평가 결과",
        "",
        f"- 생성: {meta['created']} · 사례 {n}건 · 모델 호출 {summary['model_calls']}회",
        f"- 후보 풀 포함 {summary['in_pool']}/{n} · Top-{summary['k']} 포함 {summary['in_top_k']}/{n} · 최종 정답 {summary['top1_correct']}/{n} · MRR {summary['mrr']}",
        f"- 단순 대체 노드: {', '.join(k for k, v in summary['node_status'].items() if v == 'stub')}",
        "",
        "| 사례 | 정답 | 풀 순위 | 최종 | 정답 여부 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['case_id']} | {r['gold']} | {r['pool_rank'] or '-'} | {r['final'] or '-'} | {'O' if r['correct'] else 'X'} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-root", type=Path, default=ROOT / "reference/baseline-1006/data")
    p.add_argument("--cases", type=Path, default=ROOT / "reference/baseline-1006/eval/cases/golden_set_v1.json")
    p.add_argument("--out", type=Path, default=ROOT.parent / "clabi/claview-runs")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--no-write", action="store_true", help="print the summary only")
    args = p.parse_args(argv)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    ctx = Context(catalog=Catalog.load(args.data_root), k=args.k)
    rows, summary = evaluate(cases, ctx)
    print(json.dumps({k: v for k, v in summary.items() if k != "node_status"}, ensure_ascii=False))
    if not args.no_write:
        out_dir = args.out / f"a-eval-{datetime.now():%Y%m%d-%H%M%S}"
        write_report(out_dir, rows, summary, args)
        print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
