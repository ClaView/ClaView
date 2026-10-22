"""Plan 15 r3 step 1: rules-path baseline of interpretation and selection (model off, no external call).

Per question (golden + development, 217): interpretation (status, requirements with indicator, targets, breakdown,
own / shared / ambiguous condition IDs, status, reasons; common condition IDs), judgement (certain,
failed_conditions, attribution, unused_names, negated) and selection (per requirement: status, reasons, tables with
covers, alternatives' table IDs, unspecified dimensions; selection groups), plus the outer-graph route of the
straight variant. Used to check "rules path unchanged" after each step of plan 15 completion 1.

Usage (repository root):
  $env:PYTHONHASHSEED=0; .venv/Scripts/python -B -X utf8 scripts/plan15_baseline.py runs/plan15_baseline/before
Compare two snapshot.json files with --compare <a> <b> (prints the case IDs and fields that differ).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from claview.a import _guard  # noqa: E402

_guard.install_network_block()

from claview.a.agent.graph import run_outer  # noqa: E402
from claview.a.catalog.layer import load_layer  # noqa: E402
from score_selection import load_questions, output_tables, run_case  # noqa: E402


def _name(ref) -> str | None:
    return (ref or {}).get("name") if isinstance(ref, dict) else None


def interpretation_view(analysis: dict) -> dict:
    it = analysis.get("interpretation") or {}
    jd = analysis.get("judgement") or {}
    return {
        "status": it.get("status"),
        "common_condition_ids": it.get("common_condition_ids"),
        "requirements": [{
            "id": r["id"], "quote": r["quote"], "indicator": _name(r.get("indicator")),
            "targets": [_name(t) for t in r.get("targets") or []], "breakdown": r.get("breakdown"),
            "context": r.get("context"), "condition_ids": r.get("condition_ids"),
            "shared_condition_ids": r.get("shared_condition_ids"),
            "ambiguous_condition_ids": r.get("ambiguous_condition_ids"), "status": r.get("status"),
            "reasons": r.get("reasons"),
        } for r in it.get("requirements") or []],
        "certain": jd.get("certain"),
        "failed_conditions": jd.get("failed_conditions"),
        "attribution": jd.get("attribution"),
        "unused_names": jd.get("unused_names"),
        "negated": jd.get("negated"),
    }


def selection_view(got: dict) -> dict:
    return {
        "tables": output_tables(got["selections"]),
        "requirements": {s["requirement_id"]: {
            "status": s["status"], "reasons": list(s["reasons"]),
            "tables": [{"table_id": t["table_id"], "covers": t.get("covers")} for t in s["tables"]],
            "alternatives": [[t.get("table_id") for t in (a.get("tables") or [])] if isinstance(a, dict) else a
                             for a in s.get("alternatives") or []],
            "unspecified": s.get("unspecified"),
        } for s in got["selections"]},
        "groups": got.get("selection_groups"),
    }


def snapshot_case(question: dict, layer) -> dict:
    got = run_case(question, layer)
    state = run_outer(question["question"], "straight", layer=layer, as_of=question.get("as_of"))
    route = state.get("selection_route") or {}
    return {"interpretation": interpretation_view(got["analysis"]), "selection": selection_view(got),
            "route": {"kind": route.get("kind"), "reasons": route.get("reasons")}}


def main(out_dir: Path) -> None:
    allq = load_questions(None)
    ids = sorted(c for c, q in allq.items() if q["dataset"] in ("golden", "development"))
    layer = load_layer(include_external_synonyms=False)
    snap = {cid: snapshot_case(allq[cid], layer) for cid in ids}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True,
                                                      default=str), encoding="utf8")
    print(json.dumps({"cases": len(snap), "network_attempts": len(_guard.NETWORK_ATTEMPTS)}))


def compare(a: Path, b: Path) -> None:
    x = json.loads(a.read_text(encoding="utf8"))
    y = json.loads(b.read_text(encoding="utf8"))
    diff = {}
    for cid in sorted(set(x) | set(y)):
        xa, ya = x.get(cid), y.get(cid)
        if xa == ya:
            continue
        fields = sorted(k for k in set(xa or {}) | set(ya or {}) if (xa or {}).get(k) != (ya or {}).get(k))
        diff[cid] = fields
    print(json.dumps({"cases": len(set(x) | set(y)), "changed": len(diff), "detail": diff}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "--compare":
        compare(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        main(Path(sys.argv[1]))
