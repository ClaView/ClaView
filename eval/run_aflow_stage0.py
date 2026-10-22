"""단계 0 평가 실행기: 골든 22건을 새 A 그래프(src/claview/aflow)로 끝까지 돌린다.

- 정답은 EvalCase에만 있고 run_a에는 질문과 설정만 넘긴다.
- 정답 위치는 ARunReport.trace.search(최종 풀 순서)와 case.gold로 여기서 계산한다.
- 통합검색 기준선(S8)은 저장된 응답 요약(result_search.json)에서 다시 센다. KOSIS는 부르지 않는다.
- 결과는 claview가 아니라 clabi/claview-runs 아래 --out에 쓴다.

실행 예 (저장소 루트에서):
  uv run --no-project --python 3.12 --with "langgraph>=1.0" \
    python eval/run_aflow_stage0.py --out ../clabi/claview-runs/aflow-stage0-<날짜>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claview.b.aflow import ARunConfig, EvalCase, run_a  # noqa: E402
from claview.b.aflow.catalog import default_catalog  # noqa: E402
from claview.b.aflow.contract import GAP_READS, GAP_WRITES  # noqa: E402

GOLDEN = ROOT / "reference" / "baseline-1006" / "eval" / "cases" / "golden_set_v1.json"
BASELINE = ROOT.parent / "clabi" / "claview-runs" / "kosis-api-eda-20261008" / "result_search.json"


def load_golden(path: Path = GOLDEN) -> list[EvalCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in data["cases"]:
        tables = [t["table_id"] for t in c["expected"]["A"]["tables"]]
        cases.append(
            EvalCase(
                case_id=c["case_id"],
                question=c["query"],
                as_of=c.get("as_of"),  # 지금 골든셋 22건에는 없어 None (E9). 날짜를 지어내지 않는다
                gold={"table_ids": tables},
                layer="golden",
                review_status="baseline-1006",
            )
        )
    return cases


def score_case(case: EvalCase, a_result: dict, report: dict) -> dict:
    gold = set(case.gold["table_ids"])
    pool = report["trace"]["search"]["pool"]
    pool_rank = next((p["rank"] for p in pool if p["table_id"] in gold), None)
    picked = [t["table_id"] for t in a_result["tables"]]
    return {
        "case_id": case.case_id,
        "picked": picked,
        "pool_size": len(pool),
        "gold_pool_rank": pool_rank,
        "pool_reach": pool_rank is not None,
        "top1": bool(picked) and picked[0] in gold,  # 최종 선택(T3) 기준
        "pool_top5": pool_rank is not None and pool_rank <= 5,  # S7 최종 풀 순위 기준 (v1.9 N7)
        "confidence": a_result["confidence"],
        "issue_codes": a_result["issue_codes"],
    }


def evaluate(cases: list[EvalCase], config: ARunConfig, catalog) -> tuple[list[dict], list[tuple]]:
    rows, outputs = [], []
    for case in cases:
        a_result, b_handoff, report = run_a(case.question, replace(config, as_of=case.as_of), catalog)
        outputs.append((a_result, b_handoff, report))
        rows.append(score_case(case, a_result, report))
    return rows, outputs


def baseline_from_saved(path: Path = BASELINE, variant: str = "man") -> dict | None:
    """통합검색 저장 응답에서 기준선(최초 man variant)을 다시 센다."""
    if not path.exists():
        return None
    rows = [r for r in json.loads(path.read_text(encoding="utf-8")) if r["variant"] == variant]
    ranks = [r["rank"] for r in rows]
    return {
        "variant": variant,
        "cases": len(rows),
        "found_in_50": sum(r is not None for r in ranks),
        "top1": sum(r == 1 for r in ranks),
        "top5": sum(r is not None and r <= 5 for r in ranks),
        "source": "kosis-api-eda-20261008/result_search.json",
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "cases": n,
        "pool_reach": sum(r["pool_reach"] for r in rows),
        "top1": sum(r["top1"] for r in rows),
        "pool_top5": sum(r["pool_top5"] for r in rows),
        "empty_pool": sum(r["pool_size"] == 0 for r in rows),
        "confidence": {c: sum(r["confidence"] == c for r in rows) for c in ("high", "medium", "low")},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    catalog = default_catalog(ROOT)
    config = ARunConfig()
    rows, _ = evaluate(load_golden(), config, catalog)
    args.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "note": "단계 0 stub 그래프(계약 v1.9). 수치는 stub 검색 기준이며 단계 판정용이 아니다. top1은 최종 선택, pool_top5는 S7 풀 순위",
        "config": config.snapshot(),
        "catalog_snapshot": catalog.catalog_snapshot,
        "meta_snapshot": catalog.meta_snapshot,
        "stub_graph": summarize(rows),
        "kosis_search_baseline": baseline_from_saved(),
        "contract_gaps": [
            {"node": n, "field": f, "kind": kind, "reason": why}
            for kind, table in (("read", GAP_READS), ("write", GAP_WRITES))
            for (n, f), why in table.items()
        ],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.out / "cases.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
