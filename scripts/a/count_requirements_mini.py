"""Requirement draft counts of the new question interpretation on a reduced set (plan 04, 7-2; no model, no call).

Runs 2.1-2.7 of claview_v3 (new modules only: the import guard refuses the old pipeline, the network is blocked) on
the questions of one reduced set and compares the requirement draft count with:
- (다) the confirmed requirement expectation (eval/cases/a/requirement_expect_dev_mini.json, approved by the user):
  the main measure;
- (가) proxy 1: the number of answer groups (the definition of scripts/score_by_stage.py, same source file);
- (나) proxy 2: answer groups that differ only by family, year range or frequency counted as one (family = table
  names without their bracket parts);
- the old pipeline's requirement count from its stored-response replay (runs/m0_score_by_stage/cases.csv).
The proxies are not requirement answers (plan 04 decision 11). The 417 questions are not an independent test set.

Usage: PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/a/count_requirements_mini.py --set dev_mini
       [--policy on|off] [out_dir]
Writes cases.csv, summary.json and summary.md to runs/m1_<set>/ (or out_dir). ``--policy`` sets certainty check 8
"verified structure" for the run (decision 10/18; without it the policy file decides). Sets other than dev_mini
hold new200 questions: their cases.csv keeps counts and flags only (no question, draft or word text), and their
summary lists no case.
"""

from __future__ import annotations

import collections
import itertools
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from claview.a import _guard  # noqa: E402

_guard.install_network_block()  # transports blocked; old modules are checked after the run (no import of them)

from claview.a import paths  # noqa: E402
from claview.a.catalog.layer import load_layer  # noqa: E402
from claview.a.catalog.relations import family_core  # noqa: E402
from claview.a.contract.check import check_analysis  # noqa: E402
from claview.a.interpret import structure  # noqa: E402
from claview.a.interpret.pipeline import interpret_question  # noqa: E402

SETS = ("dev_mini", "check_mini")
EXPECT = {"dev_mini": paths.CASES_DIR / "requirement_expect_dev_mini.json"}
TEXT_SETS = frozenset({"dev_mini"})  # sets whose question and draft texts may be written
TEXT_FIELDS = ("question", "drafts", "leftover", "unused_names", "structure")
OLD_CASES = ROOT / "runs" / "m0_score_by_stage" / "cases.csv"


_CASE_ID = re.compile(r'"case_id"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _array_elements(text: str):
    """Raw text slices of the objects of a JSON array, without decoding them."""
    depth, start, in_str, esc = 0, None, False, False
    for n, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            if depth == 1 and ch == "{":
                start = n
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 1 and ch == "}" and start is not None:
                yield text[start:n + 1]
                start = None


def set_questions(ids: set[str]) -> dict[str, dict]:
    """Questions of the set only (A-8): other questions (new200 included) are never decoded into objects."""
    out = {}
    for raw in _array_elements(paths.QUESTIONS_417.read_text(encoding="utf8")):
        m = _CASE_ID.search(raw)
        if m and m.group(1) in ids:
            row = json.loads(raw)
            out[row["case_id"]] = row
    return out


def answer_groups(ids: set[str]) -> dict[str, list[list[str]]]:
    """Answer groups of the set only (same source file as scripts/score_by_stage.py, read only)."""
    groups: dict[str, dict[int, list[str]]] = collections.defaultdict(dict)
    for line in paths.ANSWER_GROUPS.read_text(encoding="utf8").splitlines():
        m = _CASE_ID.search(line)
        if not m or m.group(1) not in ids:
            continue
        row = json.loads(line)
        groups[row["case_id"]][row["group_index"]] = row["allowed_tables"]
    return {cid: [g[i] for i in sorted(g)] for cid, g in groups.items()}


def merged_group_count(groups: list[list[str]], names: dict[str, str]) -> int:
    """(나): groups counted as one when they differ only by family, year range or frequency (family = table name
    without bracket parts). The count is the least number of families that every group can be given one of (each
    group takes one family from its tables), found by trying every set of families from small to large, so it does
    not depend on the order of the groups (R-14): [[A1, B1], [A2, C1], [B2], [C2]] counts 2 (B and C)."""
    fams = [{family_core(names.get(t, t)) for t in g} for g in groups]
    pool = sorted(set().union(*fams)) if fams else []
    for k in range(1, len(pool) + 1):
        for pick in itertools.combinations(pool, k):
            chosen = set(pick)
            if all(f & chosen for f in fams):
                return k
    return len(groups)


def self_check() -> None:
    """Order-independence of (나) on made-up groups (A, B, C families; run by --self-check and before main)."""
    names = {"A1": "A(1)", "A2": "A(2)", "B1": "B(1)", "B2": "B(2)", "C1": "C(1)", "C2": "C(2)"}
    cases = [([["A1", "B1"], ["A2", "C1"], ["B2"], ["C2"]], 2), ([["A1", "B1"], ["A2"], ["B2"]], 2),
             ([["A1"], ["A2"]], 1), ([["A1"], ["B1"]], 2), ([["A1", "B1"]], 1)]
    for groups, want in cases:
        for order in itertools.permutations(groups):
            got = merged_group_count([list(g) for g in order], names)
            assert got == want, (order, got, want)


def old_counts(ids: set[str]) -> dict[str, int]:
    if not OLD_CASES.exists():
        return {}
    with OLD_CASES.open(encoding="utf-8-sig", newline="") as f:
        return {r["case_id"]: int(r["n_requirements"]) for r in csv.DictReader(f) if r["case_id"] in ids}


def rate(rows: list[dict], a: str, b: str) -> dict:
    part = [r for r in rows if r[a] is not None and r[b] is not None]
    hit = sum(1 for r in part if r[a] == r[b])
    return {"cases": len(part), "match": hit, "rate": round(hit / len(part), 4) if part else None}


def main(subset: str, out_dir: Path) -> dict:
    mini = json.loads(paths.MINI_SETS.read_text(encoding="utf8"))[subset]
    ids = [r["case_id"] for r in mini]
    questions = set_questions(set(ids))
    groups = answer_groups(set(ids))
    olds = old_counts(set(ids))
    expect_path = EXPECT.get(subset)
    expect_doc = json.loads(expect_path.read_text(encoding="utf8")) if expect_path and expect_path.exists() else None
    expect = {c["case_id"]: c["count"] for c in (expect_doc or {}).get("cases", [])}
    expect_ok = bool(expect_doc) and expect_doc.get("status") == "approved"
    t0 = time.time()
    layer = load_layer(include_external_synonyms=False)
    names = {t.table_id: t.name for t in layer.catalog.tables.values()}
    rows = []
    for cid in ids:
        q = questions[cid]["question"]
        analysis = interpret_question(q, layer)
        problems = check_analysis(analysis, q)
        j = analysis["judgement"]
        drafts = j["requirement_draft"]
        g = groups.get(cid, [])
        rows_used = sorted({p["row"] for p in j["pairs"]} | {d["row"] for d in drafts if "row" in d})
        rows.append({
            "case_id": cid, "dataset": questions[cid].get("dataset"), "question": q,
            "n_drafts": len(drafts), "certain": j["certain"], "failed_conditions": " ".join(j["failed_conditions"]),
            "rows": " ".join(map(str, rows_used)),
            "drafts": " | ".join(f"{d.get('indicator') or '-'}"
                                 + (f" [{d['status']}]" if d.get("status") != "ok" else "")
                                 + (f" +{','.join(d['breakdown'])}" if d.get("breakdown") else "")
                                 + (f" @{','.join(d['targets'])}" if d.get("targets") else "") for d in drafts),
            "leftover": " ".join(j.get("leftover", [])), "unused_names": " ".join(j.get("unused_names", [])),
            "structure": j.get("structure", ""),
            "certain_without_check8": not [f for f in j["failed_conditions"] if f != "unverified_structure"],
            "expect_count": expect.get(cid) if expect_ok else None,
            "answer_groups": len(g) if g else None, "merged_groups": merged_group_count(g, names) if g else None,
            "old_n_requirements": olds.get(cid), "shape_problems": len(problems)})
    for r in rows:
        r["match_expect"] = None if r["expect_count"] is None else r["n_drafts"] == r["expect_count"]
    out_dir.mkdir(parents=True, exist_ok=True)
    texts = subset in TEXT_SETS
    with (out_dir / "cases.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = [k for k in rows[0] if texts or k not in TEXT_FIELDS]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    certain = [r for r in rows if r["certain"]]
    reasons = collections.Counter(x for r in rows for x in r["failed_conditions"].split())
    first_reason = collections.Counter(r["failed_conditions"].split()[0] for r in rows if r["failed_conditions"])
    seven = [r for r in rows if r["certain_without_check8"]]
    summary = {
        "set": subset, "cases": len(rows), "seconds": round(time.time() - t0, 1), "model_calls": 0,
        "layer": {"overrides": True, "external_synonyms": False},
        "check8_verified_structures_only": structure.verified_only(),
        "verified_structures": len(structure.verified_keys()),
        "certain_by_seven_checks": len(seven),
        "certain_by_seven_checks_count_wrong": sum(1 for r in seven if r["expect_count"] is not None
                                                   and r["n_drafts"] != r["expect_count"]),
        "structures_distinct": len({r["structure"] for r in rows}),
        "structures_distinct_among_seven_check_certain": len({r["structure"] for r in seven}),
        "undecided_first_reason": dict(first_reason.most_common()),
        "expectation": {"file": str(expect_path.relative_to(ROOT)) if expect_path else None,
                        "status": (expect_doc or {}).get("status"), "used": expect_ok},
        "certain": len(certain), "undecided": len(rows) - len(certain),
        "undecided_reasons": dict(reasons.most_common()),
        "new_vs_expect": rate(rows, "n_drafts", "expect_count"),
        "new_vs_answer_groups_proxy": rate(rows, "n_drafts", "answer_groups"),
        "new_vs_merged_groups_proxy": rate(rows, "n_drafts", "merged_groups"),
        "old_vs_expect": rate(rows, "old_n_requirements", "expect_count"),
        "old_vs_answer_groups_proxy": rate(rows, "old_n_requirements", "answer_groups"),
        "old_vs_merged_groups_proxy": rate(rows, "old_n_requirements", "merged_groups"),
        "certain_vs_expect": rate(certain, "n_drafts", "expect_count"),
        "certain_count_wrong": sorted(r["case_id"] for r in certain
                                      if r["expect_count"] is not None and r["n_drafts"] != r["expect_count"])
        if texts else [],
        "certain_count_wrong_n": sum(1 for r in certain
                                     if r["expect_count"] is not None and r["n_drafts"] != r["expect_count"]),
        "undecided_count_right": sum(1 for r in rows if not r["certain"] and r["match_expect"]),
        "shape_problems": sum(r["shape_problems"] for r in rows),
        "old_modules_loaded": sorted(m for m in sys.modules if m == "claview" or m.startswith("claview.")),
        "network_attempts": len(_guard.NETWORK_ATTEMPTS),
        "notes": ["(가)(나) are proxies, not requirement answers (plan 04 decision 11).",
                  "Old counts come from the stored-response replay of the old pipeline (model replies included); "
                  "the new counts use no model. The difference is not a wiring effect.",
                  "The count match says nothing about whether the requirement contents are right."],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
    (out_dir / "summary.md").write_text(_markdown(summary, rows if texts else []), encoding="utf8")
    return summary


def _fmt(r: dict) -> str:
    return "—" if r["rate"] is None else f"{r['match']}/{r['cases']} ({r['rate']:.0%})"


def _markdown(s: dict, rows: list[dict]) -> str:
    lines = [f"# 요구 초안 수 측정 ({s['set']}, 모델 호출 0회)", "",
             f"- 확실 검사 8(검증된 구조): {'켬' if s['check8_verified_structures_only'] else '끔'}, "
             f"검증된 구조 {s['verified_structures']}개",
             f"- 문항 {s['cases']}, 확실 {s['certain']}, 미결정 {s['undecided']}; 검사 1~7만으로 확실 "
             f"{s['certain_by_seven_checks']} (그중 (다)와 요구 수 다름 {s['certain_by_seven_checks_count_wrong']})",
             f"- 확실인데 (다)와 요구 수가 다른 문항 수: {s['certain_count_wrong_n']}",
             f"- 서로 다른 구조 서명: 전체 {s['structures_distinct']}, 검사 1~7 확실 중 "
             f"{s['structures_distinct_among_seven_check_certain']}",
             "- 미결정 첫 이유: " + ", ".join(f"{k} {v}" for k, v in s["undecided_first_reason"].items()),
             f"- 기대값 파일: {s['expectation']['file']} ({s['expectation']['status']})", "",
             "| 비교 | 새 해석(2.1~2.7) | 옛 파이프라인(저장 재생) |", "|---|---|---|",
             f"| (다) 확인된 요구 기대값 | {_fmt(s['new_vs_expect'])} | {_fmt(s['old_vs_expect'])} |",
             f"| (가) 정답 묶음 수(대리) | {_fmt(s['new_vs_answer_groups_proxy'])} | "
             f"{_fmt(s['old_vs_answer_groups_proxy'])} |",
             f"| (나) 가족·연도 범위·주기 합친 묶음 수(대리) | {_fmt(s['new_vs_merged_groups_proxy'])} | "
             f"{_fmt(s['old_vs_merged_groups_proxy'])} |", "",
             f"- 확실 중 (다)와 요구 수 일치: {_fmt(s['certain_vs_expect'])}; 확실인데 요구 수가 다른 문항: "
             f"{', '.join(s['certain_count_wrong']) or '없음'}",
             f"- 미결정인데 요구 수는 (다)와 같은 문항: {s['undecided_count_right']}",
             "- 미결정 이유(문항 수, 겹침 있음): " + ", ".join(f"{k} {v}" for k, v in s["undecided_reasons"].items()),
             "", "(가)(나)는 대리 지표다. 옛 값은 모델 응답을 포함한 저장 재생이고 새 값은 모델 없이 돈 결과라, 차이를 배선 효과로 "
             "읽지 않는다. 요구 수 일치는 요구 내용이 맞는지를 말하지 않는다.", "",
             "| 문항 | 새 | (다) | (가) | (나) | 옛 | 확실 | 실패 조건 | 판정 줄 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['case_id']} | {r['n_drafts']} | {r['expect_count']} | {r['answer_groups']} | "
                     f"{r['merged_groups']} | {r['old_n_requirements']} | {'예' if r['certain'] else ''} | "
                     f"{r['failed_conditions']} | {r['rows']} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    args = sys.argv[1:]
    self_check()
    if "--self-check" in args:
        print("self-check ok")
        raise SystemExit(0)
    subset = "dev_mini"
    if "--set" in args:
        subset = args[args.index("--set") + 1]
        del args[args.index("--set"):args.index("--set") + 2]
    if subset not in SETS:
        raise SystemExit(f"--set must be one of {SETS}")
    if "--policy" in args:
        value = args[args.index("--policy") + 1]
        del args[args.index("--policy"):args.index("--policy") + 2]
        if value not in ("on", "off"):
            raise SystemExit("--policy must be on or off")
        os.environ[structure.POLICY_ENV] = "1" if value == "on" else "0"
    out = Path(args[0]) if args else ROOT / "runs" / f"m1_{subset}"
    print(json.dumps(main(subset, out), ensure_ascii=False, indent=1))
