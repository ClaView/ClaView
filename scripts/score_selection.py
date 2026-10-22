"""Table-selection scores of the new interpretation + new selection on a reduced set (plan 04, 7-3; no model, no call).

Runs claview_v3 only (2.1-2.7 then select_tables; the network is blocked and no old module is loaded) on one
reduced set (eval/cases/mini_sets_v1.json) and scores:
- set_exact: the old evaluation's set scoring (reused/table_sets.score_sets: every answer group matched one to one
  by a different output table, no extra table, as many tables as groups), output = the tables of every requirement
  that is not no_table;
- candidates: answer groups with a table in some requirement's candidate pool;
- set_exact by decision state (a case takes its worst requirement state: no_table > confirm > auto);
- 1->n (one requirement, several answer groups) and n->1 (several requirements, one group) cases apart;
- the old pipeline's values on the same cases next to them (runs/m0_score_by_stage/cases.csv, stored-response replay
  with model replies; the new run uses no model, so the difference is not a wiring effect).
dev_mini (golden + development) gets a per-case CSV; check_mini holds new200 questions and gets counts only (no
question, case ID or table is written). The 417 questions are not an independent test set.

Usage: PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/score_selection.py --set dev_mini|check_mini
       [--policy representative|keep_all] [--vs keep_all|representative] [out_dir]
Writes summary.json, summary.md (and cases.csv for dev_mini) to runs/m3_selection_<set>[_<policy>]/ (the default
policy has no suffix). --vs runs the other policy on the same cases too and counts fixed / broken cases (IDs only
for golden and development).
"""

from __future__ import annotations

import collections
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claview_v3 import _guard  # noqa: E402

_guard.install_network_block()  # transports blocked; old modules are checked after the run (no import of them)

from claview_v3 import paths  # noqa: E402
from claview_v3.catalog.layer import load_layer  # noqa: E402
from claview_v3.contract.check import check_evidence, check_selections  # noqa: E402
from claview_v3.interpret.pipeline import interpret_question  # noqa: E402
from claview_v3.reused.table_sets import score_sets  # noqa: E402
from claview_v3.select.cover import DEFAULT_POLICY, POLICIES  # noqa: E402
from claview_v3.select.graph import select_tables  # noqa: E402

SETS = ("dev_mini", "check_mini")
OPEN_DATASETS = ("golden", "development")  # datasets whose questions may be written out
STATE_ORDER = ("auto", "confirm", "no_table")
OLD_CASES = ROOT / "runs" / "m0_score_by_stage" / "cases.csv"


def load_questions(ids: set[str] | None = None) -> dict[str, dict]:
    """Questions by case ID (all 417 when ``ids`` is None). Nothing is printed or written here."""
    rows = json.loads(paths.QUESTIONS_417.read_text(encoding="utf8"))
    return {r["case_id"]: r for r in rows if ids is None or r["case_id"] in ids}


def load_groups(ids: set[str] | None = None) -> dict[str, list[list[str]]]:
    """Answer groups (same source file as scripts/score_by_stage.py, read only)."""
    groups: dict[str, dict[int, list[str]]] = collections.defaultdict(dict)
    for line in paths.ANSWER_GROUPS.read_text(encoding="utf8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if ids is None or row["case_id"] in ids:
            groups[row["case_id"]][row["group_index"]] = row["allowed_tables"]
    return {cid: [g[i] for i in sorted(g)] for cid, g in groups.items()}


def old_rows(ids: set[str]) -> dict[str, dict]:
    if not OLD_CASES.exists():
        return {}
    with OLD_CASES.open(encoding="utf-8-sig", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f) if r["case_id"] in ids}


def run_case(question: dict, layer, policy: str | None = None) -> dict:
    """Interpretation and selection of one question: {analysis, selections, selection_groups, evidence, problems}."""
    analysis = interpret_question(question["question"], layer)
    out = select_tables(analysis, layer, question.get("as_of"), policy)
    problems = check_selections(out["selections"], out["selection_groups"]) + check_evidence(out["evidence"])
    return {"analysis": analysis, **out, "problems": problems}


def output_tables(selections: list[dict]) -> list[str]:
    return list(dict.fromkeys(t["table_id"] for s in selections if s["status"] != "no_table" for t in s["tables"]))


def pool_tables(evidence: dict) -> list[str]:
    return list(dict.fromkeys(e["table_id"] for e in evidence["evidence"]))


def case_state(selections: list[dict]) -> str:
    states = [s["status"] for s in selections] or ["no_table"]
    return max(states, key=STATE_ORDER.index)


def kind_of(n_requirements: int, n_groups: int) -> str:
    if n_requirements == 1 and n_groups > 1:
        return "1->n"
    if n_requirements > 1 and n_groups == 1:
        return "n->1"
    return f"{min(n_requirements, 2)}{'+' if n_requirements > 2 else ''}->{min(n_groups, 2)}"


def score_case(cid: str, question: dict, groups: list[list[str]], layer, policy: str | None = None) -> dict:
    got = run_case(question, layer, policy)
    sels = got["selections"]
    tables = output_tables(sels)
    pool = pool_tables(got["evidence"])
    scored = score_sets(groups, tables, [], pool, 5)
    in_pool = [any(t in pool for t in g) for g in groups]
    return {
        "case_id": cid, "dataset": question.get("dataset"), "n_requirements": len(sels), "n_groups": len(groups),
        "kind": kind_of(len(sels), len(groups)), "set_exact": scored["set_exact"],
        "groups_in_pool": sum(in_pool), "all_in_pool": all(in_pool) and bool(groups), "state": case_state(sels),
        "states": " ".join(s["status"] for s in sels), "reasons": " | ".join(",".join(s["reasons"]) for s in sels),
        "model_pending": sum(1 for s in sels if s["model_step"] == "pending"), "tables": " ".join(tables),
        "missing": len(scored["missing"]), "extra": len(scored["extra"]), "shape_problems": len(got["problems"]),
    }


def _rate(part: list[dict], key: str = "set_exact") -> dict:
    hit = sum(1 for r in part if r[key])
    return {"cases": len(part), "hit": hit, "rate": round(hit / len(part), 4) if part else None}


def summarise(rows: list[dict], olds: dict[str, dict]) -> dict:
    out = {"cases": len(rows), "set_exact": _rate(rows),
           "candidates": {"groups": sum(r["n_groups"] for r in rows),
                          "groups_in_pool": sum(r["groups_in_pool"] for r in rows),
                          "cases_all_in_pool": sum(1 for r in rows if r["all_in_pool"])},
           "by_state": {s: _rate([r for r in rows if r["state"] == s]) for s in STATE_ORDER},
           "by_kind": {k: _rate([r for r in rows if r["kind"] == k]) for k in sorted({r["kind"] for r in rows})},
           "model_pending_requirements": sum(r["model_pending"] for r in rows),
           "reason_counts": dict(collections.Counter(x for r in rows for part in r["reasons"].split(" | ")
                                                     for x in part.split(",") if x).most_common()),
           "shape_problems": sum(r["shape_problems"] for r in rows)}
    if olds:
        old = [{"set_exact": olds[r["case_id"]]["set_exact"] == "True", "kind": r["kind"]} for r in rows
               if r["case_id"] in olds]
        out["old_pipeline"] = {"set_exact": _rate(old),
                               "by_kind": {k: _rate([o for o in old if o["kind"] == k])
                                           for k in sorted({o["kind"] for o in old})},
                               "both_right": sum(1 for r in rows if r["set_exact"] and olds.get(r["case_id"], {})
                                                 .get("set_exact") == "True"),
                               "new_only": sum(1 for r in rows if r["set_exact"] and olds.get(r["case_id"], {})
                                               .get("set_exact") == "False"),
                               "old_only": sum(1 for r in rows if not r["set_exact"] and olds.get(r["case_id"], {})
                                               .get("set_exact") == "True")}
    return out


def _fmt(r: dict) -> str:
    return "—" if not r or r["rate"] is None else f"{r['hit']}/{r['cases']} ({r['rate']:.0%})"


def markdown(subset: str, s: dict, rows: list[dict] | None) -> str:
    old = s.get("old_pipeline") or {}
    lines = [f"# 표 선택 측정 ({subset}, 정책 {s.get('policy')}, 모델 호출 0회)", "",
             "새 해석(2.1~2.7)+새 선택(3.1~3.8, 모델 자리 pending)과 옛 파이프라인(저장 응답 재생, 모델 응답 포함)을 나란히 둔다. "
             "차이를 배선 효과로 읽지 않는다. 417은 독립 평가셋이 아니다.", "",
             "| 지표 | 새 | 옛 |", "|---|---|---|",
             f"| set_exact | {_fmt(s['set_exact'])} | {_fmt(old.get('set_exact'))} |"]
    for k, v in s["by_kind"].items():
        lines.append(f"| set_exact {k} | {_fmt(v)} | {_fmt((old.get('by_kind') or {}).get(k))} |")
    c = s["candidates"]
    lines += [f"| 정답 묶음의 후보 포함 | {c['groups_in_pool']}/{c['groups']} | — |",
              f"| 모든 정답 묶음이 후보에 있는 문항 | {c['cases_all_in_pool']}/{s['cases']} | — |", ""]
    lines += ["| 결정 상태(문항의 가장 낮은 요구) | 문항 | set_exact |", "|---|---|---|"]
    for st, v in s["by_state"].items():
        lines.append(f"| {st} | {v['cases']} | {_fmt(v)} |")
    if old:
        lines += ["", f"- 둘 다 맞음 {old['both_right']}, 새만 맞음 {old['new_only']}, 옛만 맞음 {old['old_only']}"]
    if s.get("vs_policy"):
        v = s["vs_policy"]
        lines += [f"- 정책 {v['policy']} 대비: set_exact {_fmt(v['set_exact'])} -> {_fmt(s['set_exact'])}, "
                  f"고쳐짐 {v['fixed']}, 깨짐 {v['broken']}"
                  + (f" (고쳐짐 {', '.join(v['fixed_ids']) or '없음'}; 깨짐 {', '.join(v['broken_ids']) or '없음'})"
                     if "fixed_ids" in v else "")]
    lines += [f"- 모델 자리 pending 요구 {s['model_pending_requirements']}, 모양 문제 {s['shape_problems']}",
              "- 사유 빈도(요구 단위): " + ", ".join(f"{k} {v}" for k, v in s["reason_counts"].items())]
    if rows is not None:
        lines += ["", "| 문항 | 유형 | 상태 | set_exact | 옛 | 후보 포함 | 이유 |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['case_id']} | {r['kind']} | {r['states']} | {'O' if r['set_exact'] else ''} | "
                         f"{'O' if r.get('old_set_exact') else ''} | {r['groups_in_pool']}/{r['n_groups']} | "
                         f"{r['reasons']} |")
    return "\n".join(lines) + "\n"


def compare_policies(rows: list[dict], base: list[dict], base_policy: str, open_rows: bool) -> dict:
    """Fixed / broken against another policy run on the same cases (case IDs only for open datasets)."""
    was = {r["case_id"]: r["set_exact"] for r in base}
    fixed = [r["case_id"] for r in rows if r["set_exact"] and not was[r["case_id"]]]
    broken = [r["case_id"] for r in rows if not r["set_exact"] and was[r["case_id"]]]
    out = {"policy": base_policy, "set_exact": _rate(base), "fixed": len(fixed), "broken": len(broken),
           "by_state": {s: _rate([r for r in base if r["state"] == s]) for s in STATE_ORDER}}
    if open_rows:
        out.update(fixed_ids=fixed, broken_ids=broken)
    return out


def main(subset: str, out_dir: Path, policy: str = DEFAULT_POLICY, vs: str | None = None) -> dict:
    mini = json.loads(paths.MINI_SETS.read_text(encoding="utf8"))[subset]
    ids = [r["case_id"] for r in mini]
    questions = load_questions(set(ids))
    groups = load_groups(set(ids))
    olds = old_rows(set(ids))
    t0 = time.time()
    layer = load_layer(include_external_synonyms=False)
    rows = [score_case(cid, questions[cid], groups.get(cid, []), layer, policy) for cid in ids]
    base = [score_case(cid, questions[cid], groups.get(cid, []), layer, vs) for cid in ids] if vs else None
    for r in rows:
        r["old_set_exact"] = olds.get(r["case_id"], {}).get("set_exact") == "True" if r["case_id"] in olds else None
    open_rows = all(r["dataset"] in OPEN_DATASETS for r in rows)
    summary = {"set": subset, "policy": policy, "seconds": round(time.time() - t0, 1), "model_calls": 0,
               "layer": {"overrides": True, "external_synonyms": False}, **summarise(rows, olds),
               "per_case_written": open_rows,
               "old_modules_loaded": sorted(m for m in sys.modules if m == "claview" or m.startswith("claview.")),
               "network_attempts": len(_guard.NETWORK_ATTEMPTS),
               "notes": ["set_exact is the old evaluation's set scoring (one table per answer group).",
                         "Old values are the stored-response replay with model replies; the new run has no model."]}
    if base is not None:
        summary["vs_policy"] = compare_policies(rows, base, vs, open_rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    if open_rows:
        with (out_dir / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
    (out_dir / "summary.md").write_text(markdown(subset, summary, rows if open_rows else None), encoding="utf8")
    return summary


if __name__ == "__main__":
    args = sys.argv[1:]
    subset = "dev_mini"
    if "--set" in args:
        subset = args[args.index("--set") + 1]
        del args[args.index("--set"):args.index("--set") + 2]
    if subset not in SETS:
        raise SystemExit(f"--set must be one of {SETS}")
    policy, vs = DEFAULT_POLICY, None
    if "--policy" in args:
        policy = args[args.index("--policy") + 1]
        del args[args.index("--policy"):args.index("--policy") + 2]
    if "--vs" in args:
        vs = args[args.index("--vs") + 1]
        del args[args.index("--vs"):args.index("--vs") + 2]
    if policy not in POLICIES or (vs is not None and vs not in POLICIES):
        raise SystemExit(f"--policy / --vs must be one of {POLICIES}")
    suffix = "" if policy == DEFAULT_POLICY else f"_{policy}"
    out = Path(args[0]) if args else ROOT / "runs" / f"m3_selection_{subset}{suffix}"
    result = main(subset, out, policy, vs)
    print(json.dumps({k: v for k, v in result.items() if k != "notes"}, ensure_ascii=False, indent=1))
