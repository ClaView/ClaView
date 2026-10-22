"""The four outer-graph variants on a reduced set, with the real nodes (plan 04, 1-2 and 7-3; no model, no call).

Runs claview_v3 only (the network is blocked and no old module is loaded; checked after the run): the outer graph
``agent.graph.build_outer_graph(variant)`` with the real interpretation (2.1-2.7) and selection (3.1-3.8) nodes, the
model slot off and jev off. Per case it records
- interpretation: requirement count, certain, interpretation status, V1 status;
- selection: set_exact with the judgement of scripts/score_selection.py (reused/table_sets.score_sets; output = the
  shared ``selection.tables``, i.e. the tables of every requirement whose final decision state is not no_table),
  answer groups in the candidate pool, the decision state of the case (worst final requirement state:
  no_table > confirm > auto), states lowered by V2;
- asks (plan 04, 1-4): real asks and ask_skipped, the kinds, and for table asks whether one of the options would
  give the right table set;
- path: the order of the actions (router and part-end decisions).
Asks are answered by their default in this automatic run, so set_exact is the "default-proceeding" rate; asks are
never counted as right answers.

dev_mini (golden + development) gets a per-case CSV; check_mini holds new200 questions and gets counts only (no
question, case ID or table is written). The old pipeline's values on the same cases (runs/m0_score_by_stage/cases.csv,
stored-response replay with model replies) are put next to them; the new run uses no model, so the difference is not
a wiring effect. The 417 questions are not an independent test set.

Usage:
  PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/compare_variants.py --variant VARIANT --set SET
  VARIANT: straight | router | verify_central | verify_parts | all;  SET: dev_mini | check_mini
Writes summary.json, summary.md (and cases.csv for dev_mini) to runs/m4_variants_<variant>_<set>/; with
``--variant all`` also runs/m4_variants_<set>_summary.md (the four variants side by side).
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
from claview_v3.agent.graph import build_outer_graph  # noqa: E402
from claview_v3.catalog.layer import load_layer  # noqa: E402
from claview_v3.reused.table_sets import score_sets  # noqa: E402

SETS = ("dev_mini", "check_mini")
VARIANTS = {"straight": "straight", "router": "router", "verify_central": "central", "verify_parts": "parts"}
OPEN_DATASETS = ("golden", "development")  # datasets whose questions may be written out
STATE_ORDER = ("auto", "confirm", "no_table")
OLD_CASES = ROOT / "runs" / "m0_score_by_stage" / "cases.csv"


def load_questions(ids: set[str]) -> dict[str, dict]:
    rows = json.loads(paths.QUESTIONS_417.read_text(encoding="utf8"))
    return {r["case_id"]: r for r in rows if r["case_id"] in ids}


def load_groups(ids: set[str]) -> dict[str, list[list[str]]]:
    """Answer groups (same source file as scripts/score_selection.py, read only)."""
    groups: dict[str, dict[int, list[str]]] = collections.defaultdict(dict)
    for line in paths.ANSWER_GROUPS.read_text(encoding="utf8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row["case_id"] in ids:
                groups[row["case_id"]][row["group_index"]] = row["allowed_tables"]
    return {cid: [g[i] for i in sorted(g)] for cid, g in groups.items()}


def old_rows(ids: set[str]) -> dict[str, dict]:
    if not OLD_CASES.exists():
        return {}
    with OLD_CASES.open(encoding="utf-8-sig", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f) if r["case_id"] in ids}


def kind_of(n_requirements: int, n_groups: int) -> str:
    if n_requirements == 1 and n_groups > 1:
        return "1->n"
    if n_requirements > 1 and n_groups == 1:
        return "n->1"
    return f"{min(n_requirements, 2)}{'+' if n_requirements > 2 else ''}->{min(n_groups, 2)}"


def final_states(state: dict) -> dict[str, str]:
    checked = (state.get("verification") or {}).get("selection") or {}
    if checked.get("states"):
        return dict(checked["states"])
    return {s["requirement_id"]: s["status"] for s in state.get("selections") or []}


def step_label(decision: dict, with_target: bool = False) -> str:
    final = decision["final"]
    args = final["args"]
    head = ("part:" if decision["type"] == "part" else "") + final["kind"]
    if final["kind"] in ("next", "verify"):
        return f"{head}:{args['stage']}"
    if final["kind"] == "ask":
        tail = f":{args.get('requirement_id')}" if with_target else ""
        return f"{head}:{args['kind']}{tail}" + (":skipped" if args.get("skipped") else "")
    if final["kind"] in ("finish", "stop") and args.get("reason"):
        return f"{head}:{args['reason']}"
    return head


def output_tables(state: dict) -> list[str]:
    return [t["table_id"] for t in state["selection"]["tables"]]


def pool_tables(state: dict) -> list[str]:
    pool = (state.get("evidence") or {}).get("pool") or {}
    return list(dict.fromkeys(t for tables in pool.values() for t in tables))


def option_hits(state: dict, groups: list[list[str]], pool: list[str]) -> tuple[int, int]:
    """(table asks, table asks with an option that would give the right table set)."""
    states = final_states(state)
    by_req = {s["requirement_id"]: [t["table_id"] for t in s["tables"]] for s in state.get("selections") or []}
    asks = [d for d in state["control"]["decisions"] if d.get("type") == "ask" and d["kind"] == "choose_table"]
    hits = 0
    for ask in asks:
        rid = ask["requirement_id"]
        for option in ask["options"]:
            tables = list(dict.fromkeys(t for r, ts in by_req.items() if states.get(r) != "no_table"
                                        for t in (option["table_ids"] if r == rid else ts)))
            if groups and score_sets(groups, tables, [], pool, 5)["set_exact"]:
                hits += 1
                break
    return len(asks), hits


def score_case(cid: str, question: dict, groups: list[list[str]], graph, layer) -> dict:
    state = graph.invoke({"question": question["question"], "as_of": question.get("as_of")},
                         {"configurable": {"layer": layer, "jev": None}})
    analysis = state.get("analysis") or {}
    interp = analysis.get("interpretation") or {}
    states = final_states(state)
    tables, pool = output_tables(state), pool_tables(state)
    scored = score_sets(groups, tables, [], pool, 5)
    in_pool = [any(t in pool for t in g) for g in groups]
    decisions = [d for d in state["control"]["decisions"] if d.get("type") in ("route", "part")]
    asks = [d for d in state["control"]["decisions"] if d.get("type") == "ask"]
    table_asks, table_hits = option_hits(state, groups, pool)
    n_req = len(interp.get("requirements") or [])
    evidence = state.get("evidence") or {}
    return {
        "case_id": cid, "dataset": question.get("dataset"), "n_requirements": n_req, "n_groups": len(groups),
        "kind": kind_of(n_req, len(groups)), "certain": bool((analysis.get("judgement") or {}).get("certain")),
        "interp_status": interp.get("status"), "v1": ((state["verification"] or {}).get("interpretation")
                                                      or {}).get("status"),
        "set_exact": scored["set_exact"], "groups_in_pool": sum(in_pool), "all_in_pool": all(in_pool) and bool(groups),
        "state": max(states.values(), key=STATE_ORDER.index) if states else "no_table",
        "states": " ".join(states.values()),
        "v2_lowered": len(((state["verification"] or {}).get("selection") or {}).get("lowered") or []),
        "asked": sum(1 for a in asks if a["mode"] == "asked"),
        "ask_skipped": sum(1 for a in asks if a["mode"] == "ask_skipped"),
        "ask_kinds": " ".join(a["kind"] for a in asks), "table_asks": table_asks, "table_asks_with_answer": table_hits,
        "path": " ".join(step_label(d) for d in decisions), "steps": state["control"]["budget"]["steps"],
        "response": state["response"]["status"], "finished_by": state["response"]["finished_by"],
        "reason": state["response"]["reason"] or "", "tables": " ".join(tables),
        "missing": len(scored["missing"]), "extra": len(scored["extra"]),
        "evidence_pairs": evidence.get("pairs", 0), "evidence_checks": evidence.get("checks", 0),
        "decomposition": (state.get("response") or {}).get("decomposition"),
    }


def _rate(part: list[dict], key: str = "set_exact") -> dict:
    hit = sum(1 for r in part if r[key])
    return {"cases": len(part), "hit": hit, "rate": round(hit / len(part), 4) if part else None}


def summarise(rows: list[dict], olds: dict[str, dict]) -> dict:
    asked = [r for r in rows if r["asked"]]
    table_asks = sum(r["table_asks"] for r in rows)
    out = {
        "cases": len(rows),
        "set_exact": _rate(rows),  # = default-proceeding rate (asks answered by their default)
        "candidates": {"groups": sum(r["n_groups"] for r in rows),
                       "groups_in_pool": sum(r["groups_in_pool"] for r in rows),
                       "cases_all_in_pool": sum(1 for r in rows if r["all_in_pool"])},
        "by_state": {s: _rate([r for r in rows if r["state"] == s]) for s in STATE_ORDER},
        "by_kind": {k: _rate([r for r in rows if r["kind"] == k]) for k in sorted({r["kind"] for r in rows})},
        "interpretation": {"requirements": sum(r["n_requirements"] for r in rows),
                           "certain_cases": sum(1 for r in rows if r["certain"]),
                           "status": dict(collections.Counter(r["interp_status"] for r in rows)),
                           "v1": dict(collections.Counter(r["v1"] for r in rows))},
        "asks": {"cases_asked": len(asked),
                 "asked_and_wrong": sum(1 for r in asked if not r["set_exact"]),
                 "asked_and_right": sum(1 for r in asked if r["set_exact"]),
                 "asks_total": sum(r["asked"] for r in rows),
                 "cases_with_ask_skipped": sum(1 for r in rows if r["ask_skipped"]),
                 "ask_skipped_total": sum(r["ask_skipped"] for r in rows),
                 "cases_only_skipped": sum(1 for r in rows if r["ask_skipped"] and not r["asked"]),
                 "kinds": dict(collections.Counter(k for r in rows for k in r["ask_kinds"].split())),
                 "table_asks": table_asks,
                 "table_asks_with_answer_in_options": sum(r["table_asks_with_answer"] for r in rows),
                 "note": "split_requirements options are not scored: the confirmed requirement expectations (plan 04 "
                         "7-2 (c)) are not approved"},
        "v2_lowered_requirements": sum(r["v2_lowered"] for r in rows),
        "responses": dict(collections.Counter(f"{r['response']}:{r['finished_by']}:{r['reason']}" for r in rows)),
        "paths": dict(collections.Counter(r["path"] for r in rows).most_common()),
        "actions": dict(collections.Counter(step for r in rows for step in r["path"].split()).most_common()),
        "steps_mean": round(sum(r["steps"] for r in rows) / len(rows), 2) if rows else None,
        "evidence_size_mean": {"pairs": round(sum(r["evidence_pairs"] for r in rows) / len(rows), 1) if rows else None,
                               "checks": round(sum(r["evidence_checks"] for r in rows) / len(rows), 1) if rows
                               else None},
    }
    if olds:
        old = [{"set_exact": olds[r["case_id"]]["set_exact"] == "True", "kind": r["kind"]} for r in rows
               if r["case_id"] in olds]
        out["old_pipeline"] = {
            "set_exact": _rate(old),
            "both_right": sum(1 for r in rows if r["set_exact"] and olds.get(r["case_id"], {}).get("set_exact")
                              == "True"),
            "new_only": sum(1 for r in rows if r["set_exact"] and olds.get(r["case_id"], {}).get("set_exact")
                            == "False"),
            "old_only": sum(1 for r in rows if not r["set_exact"] and olds.get(r["case_id"], {}).get("set_exact")
                            == "True")}
    return out


def _fmt(r: dict | None) -> str:
    return "—" if not r or r["rate"] is None else f"{r['hit']}/{r['cases']} ({r['rate']:.0%})"


def markdown(variant: str, subset: str, s: dict, rows: list[dict] | None) -> str:
    old = s.get("old_pipeline") or {}
    a = s["asks"]
    lines = [f"# 판 비교 {variant} ({subset}, 모델·jev 끔, 호출 0회)", "",
             "새 해석(2.1~2.7)+새 선택(3.1~3.8)을 바깥 그래프로 이은 결과와 옛 파이프라인(저장 응답 재생, 모델 응답 포함)을 "
             "나란히 둔다. 차이를 배선 효과로 읽지 않는다. 되묻기는 기본값으로 진행했고 정답으로 세지 않는다. "
             "417은 독립 평가셋이 아니다.", "",
             "| 지표 | 값 |", "|---|---|",
             f"| set_exact(기본값 진행) | {_fmt(s['set_exact'])} |",
             f"| 옛 파이프라인 set_exact | {_fmt(old.get('set_exact'))} |",
             f"| 정답 묶음의 후보 포함 | {s['candidates']['groups_in_pool']}/{s['candidates']['groups']} |",
             f"| 되묻기로 간 문항 | {a['cases_asked']} (기본값 결과 오답 {a['asked_and_wrong']}, 정답인데 물음 "
             f"{a['asked_and_right']}) |",
             f"| ask_skipped | 문항 {a['cases_with_ask_skipped']}, 건 {a['ask_skipped_total']} |",
             f"| 표 고르기 되묻기 중 선택지에 정답 | {a['table_asks_with_answer_in_options']}/{a['table_asks']} |",
             f"| V2가 낮춘 요구 | {s['v2_lowered_requirements']} |", "",
             "| 결정 상태 | 문항 | set_exact |", "|---|---|---|"]
    for st, v in s["by_state"].items():
        lines.append(f"| {st} | {v['cases']} | {_fmt(v)} |")
    lines += ["", "| 경로(행동 순서) | 문항 |", "|---|---|"]
    lines += [f"| {p} | {n} |" for p, n in s["paths"].items()]
    if old:
        lines += ["", f"- 둘 다 맞음 {old['both_right']}, 새만 맞음 {old['new_only']}, 옛만 맞음 {old['old_only']}"]
    if rows is not None:
        lines += ["", "| 문항 | 유형 | 상태 | set_exact | 되묻기 | 경로 |", "|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['case_id']} | {r['kind']} | {r['states']} | {'O' if r['set_exact'] else ''} | "
                         f"{r['asked']}+{r['ask_skipped']}s | {r['path']} |")
    return "\n".join(lines) + "\n"


def side_by_side(subset: str, results: dict[str, dict]) -> str:
    names = list(results)
    first = next(iter(results.values()))
    lines = [f"# 판 4개 비교 ({subset}, 모델·jev 끔, 호출 0회)", "",
             "되묻기는 기본값으로 진행(정답으로 세지 않음). 옛 값은 저장 응답 재생(모델 응답 포함)이라 배선 효과가 아니다.", "",
             "| 지표 | " + " | ".join(names) + " | 옛 파이프라인 |", "|---|" + "---|" * (len(names) + 1)]
    old = _fmt((first.get("old_pipeline") or {}).get("set_exact"))
    rows = [("set_exact(기본값 진행)", lambda s: _fmt(s["set_exact"]), old)]
    for st in STATE_ORDER:
        rows.append((f"set_exact {st}", lambda s, st=st: _fmt(s["by_state"][st]), "—"))
    rows += [
        ("후보 포함 묶음", lambda s: f"{s['candidates']['groups_in_pool']}/{s['candidates']['groups']}", "—"),
        ("되묻기로 간 문항", lambda s: str(s["asks"]["cases_asked"]), "—"),
        ("그중 기본값 결과 오답", lambda s: str(s["asks"]["asked_and_wrong"]), "—"),
        ("그중 정답인데 물음", lambda s: str(s["asks"]["asked_and_right"]), "—"),
        ("되묻기 건수", lambda s: str(s["asks"]["asks_total"]), "—"),
        ("ask_skipped 문항/건", lambda s: f"{s['asks']['cases_with_ask_skipped']}/{s['asks']['ask_skipped_total']}",
         "—"),
        ("표 고르기 선택지에 정답", lambda s: f"{s['asks']['table_asks_with_answer_in_options']}/{s['asks']['table_asks']}",
         "—"),
        ("V2가 낮춘 요구", lambda s: str(s["v2_lowered_requirements"]), "—"),
        ("재해석/다음 대안(건)", lambda s: "/".join(str(sum(n for a, n in s["actions"].items()
                                                         if a.removeprefix("part:") == k))
                                               for k in ("reinterpret", "next_alternative")), "—"),
        ("평균 단계 수", lambda s: str(s["steps_mean"]), "—"),
    ]
    for label, fn, last in rows:
        lines.append(f"| {label} | " + " | ".join(fn(results[n]) for n in names) + f" | {last} |")
    return "\n".join(lines) + "\n"


def main(variant: str, subset: str, layer) -> dict:
    mini = json.loads(paths.MINI_SETS.read_text(encoding="utf8"))[subset]
    ids = [r["case_id"] for r in mini]
    questions, groups, olds = load_questions(set(ids)), load_groups(set(ids)), old_rows(set(ids))
    t0 = time.time()
    graph = build_outer_graph(VARIANTS[variant])
    rows = [score_case(cid, questions[cid], groups.get(cid, []), graph, layer) for cid in ids]
    for r in rows:
        r["old_set_exact"] = olds.get(r["case_id"], {}).get("set_exact") == "True" if r["case_id"] in olds else None
    open_rows = all(r["dataset"] in OPEN_DATASETS for r in rows)
    summary = {"variant": variant, "graph_variant": VARIANTS[variant], "set": subset,
               "seconds": round(time.time() - t0, 1), "model_calls": 0, "jev": "off",
               "layer": {"overrides": True, "external_synonyms": False}, **summarise(rows, olds),
               "per_case_written": open_rows,
               "old_modules_loaded": sorted(m for m in sys.modules if m == "claview" or m.startswith("claview.")),
               "network_attempts": len(_guard.NETWORK_ATTEMPTS)}
    if not open_rows:
        summary.pop("paths")  # counts only for new200: path strings carry no case data, but keep the file minimal
        summary["paths_distinct"] = len({r["path"] for r in rows})
    out_dir = ROOT / "runs" / f"m4_variants_{variant}_{subset}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if open_rows:
        with (out_dir / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
    md_summary = summary if open_rows else {**summary, "paths": {}}
    (out_dir / "summary.md").write_text(markdown(variant, subset, md_summary, rows if open_rows else None),
                                        encoding="utf8")
    return summary


def _arg(args: list[str], name: str, default: str) -> str:
    return args[args.index(name) + 1] if name in args else default


if __name__ == "__main__":
    argv = sys.argv[1:]
    subset, chosen = _arg(argv, "--set", "dev_mini"), _arg(argv, "--variant", "all")
    if subset not in SETS:
        raise SystemExit(f"--set must be one of {SETS}")
    if chosen != "all" and chosen not in VARIANTS:
        raise SystemExit(f"--variant must be one of {tuple(VARIANTS)} or all")
    shared_layer = load_layer(include_external_synonyms=False)
    names = list(VARIANTS) if chosen == "all" else [chosen]
    results = {name: main(name, subset, shared_layer) for name in names}
    if chosen == "all":
        (ROOT / "runs" / f"m4_variants_{subset}_summary.md").write_text(side_by_side(subset, results),
                                                                         encoding="utf8")
    keep = ("variant", "set", "seconds", "cases", "set_exact", "by_state", "asks", "v2_lowered_requirements",
            "old_pipeline", "old_modules_loaded", "network_attempts", "evidence_size_mean")
    print(json.dumps({n: {k: r.get(k) for k in keep} for n, r in results.items()}, ensure_ascii=False, indent=1))
