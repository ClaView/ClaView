"""Plan 11 R3 observation measurements. Model off; network blocked before pipeline imports.

Compare R1b caps with provisional R3 caps from package data/decision_policy.json. Both profiles
use R3 code and bundle D: this isolates caps, and is not a claim to replay historical R1b code.
Usage: .venv/Scripts/python -B -X utf8 scripts/plan11_r3_counts.py --gen <jsonl> [out_dir]
Each question prints elapsed milliseconds and cuts. JSON/Markdown include median and maximum
question runtime, source and bundle alternative counts, and separate loss stages. No policy is
updated automatically. These datasets were used for development, not independent evaluation.
"""

from __future__ import annotations

import argparse
import collections
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from claview_v3 import _guard, paths  # noqa: E402

_guard.install_network_block()

from claview_v3.agent.graph import run_outer  # noqa: E402
from claview_v3.catalog.layer import load_layer  # noqa: E402
from claview_v3.select import cover, decision, facts  # noqa: E402
from claview_v3.select.refute import CODES  # noqa: E402
from score_selection import load_groups, load_questions  # noqa: E402

OUT = ROOT / "runs" / "plan11_r3"


@contextmanager
def observation_caps(values):
    """Override only data-backed observation settings, restoring them even after failure."""
    bindings = [(facts, "RESTORE_CAP"), (cover, "OBS_COVERS_CAP"),
                (cover, "OBS_COMBOS"), (decision, "DECISION_CAP")]
    old = [(module, name, getattr(module, name)) for module, name in bindings]
    for _, name in bindings:
        v = values[name]
        if name == "RESTORE_CAP" and v is None:
            continue
        if type(v) is not int or v < (0 if name == "RESTORE_CAP" else 1):
            raise ValueError(f"invalid observation cap: {name}")
    try:
        for module, name in bindings:
            setattr(module, name, values[name])
        yield
    finally:
        for module, name, value in old:
            setattr(module, name, value)


def assignment_tables(dec):
    return {t for a in dec["alternatives"] for ts in a["assignment"].values() for t in ts} | {
        t for a in dec["alternatives"] for e in a["edition_links"] for t in e["members"]}


def removed_tables(prev):
    """Only certified removals; a partly refuted table absent after a cap is NOT a refutation loss."""
    refs = prev["refutations"]
    out = {t: sorted({c["code"] for c in certs}) for t, certs in refs["tables"].items()}
    for t, cells in refs["cells"].items():
        if t not in out and prev["cells"] > 0 and len(cells) == prev["cells"]:
            out[t] = sorted({c["code"] for certs in cells.values() for c in certs})
    return out


def question_row(q, layer):
    started = time.perf_counter()
    state = run_outer(q["question"], "straight", layer=layer, as_of=q.get("as_of"))
    elapsed_ms = (time.perf_counter() - started) * 1000
    sels = state.get("selections") or []
    units = decision.decisions_from(sels)
    candidates, removed_in = collections.defaultdict(set), collections.defaultdict(dict)
    req_rows, source_tables, errors = [], set(), []
    for s in sels:
        rid = s["requirement_id"]
        signals = s.get("signals") or {}
        prev = signals.get("decision_preview") or {}
        if "error" in prev or "decision" not in prev:
            # The observation path failed for this requirement (selection keeps going): count it as a failure.
            errors.append({"requirement_id": rid, "error": prev.get("error") or "missing_decision_preview"})
            continue
        d, pc = prev["decision"], signals.get("pool_cut") or {}
        cands = set((signals.get("facts") or {}).get("tables") or {}) | set(prev["candidates"]["restored"])
        for t in cands:
            candidates[t].add(rid)
        for t, codes in removed_tables(prev).items():
            removed_in[t][rid] = codes
        source_tables.update(assignment_tables(d))
        req_rows.append({"requirement_id": rid, "pool_cut": bool(d["cut"]["pool_cut"]),
                         "cover_cut": bool(d["cut"]["cover_cut"]), "restorable": pc.get("restorable", 0),
                         "restored": len(prev["candidates"]["restored"]), "unchecked": pc.get("unchecked", 0),
                         "editions": prev["candidates"]["editions"], "tried": prev["candidates"]["tried"],
                         "alternatives": len(d["alternatives"])})
    by_rid = {s["requirement_id"]: s for s in sels}
    unit_rows, bundle_tables = [], set()
    for d in units.values():
        first = d["alternatives"][0] if d["alternatives"] else None
        bundle_tables.update(assignment_tables(d))
        unit_rows.append({"unit": d["unit"], "requirement_ids": d["requirement_ids"],
                          "bundle": len(d["requirement_ids"]) > 1,
                          "live": any(by_rid[r]["status"] != "no_table" for r in d["requirement_ids"]),
                          "today_in": d["today"]["in_alternatives"], "today_first": d["today"]["first"],
                          "explained_by": d["today"]["explained_by"], "alternatives": len(d["alternatives"]),
                          "one_no_gap": len(d["alternatives"]) == 1 and not first["gaps"] and not d["gaps"]
                          if first else False, "first_gaps": first["gaps"] if first else [],
                          "unit_gaps": d["gaps"], "cut": d["cut"],
                          "combinations": d.get("combination_count", len(d["alternatives"])),
                          "omitted": d.get("omitted", 0),
                          "empty_members": len(d.get("member_no_table") or {})})
    groups = q["groups"]
    lost = []
    for k, group in enumerate(groups):
        tabs = [t for t in group if t in candidates]
        if tabs and all(all(r in removed_in[t] for r in candidates[t]) for t in tabs):
            lost.append({"group": k, "codes": sorted({c for t in tabs for r in candidates[t]
                                                      for c in removed_in[t][r]})})
    route = state.get("selection_route") or {}
    stable = {"assignment": {s["requirement_id"]: [t["table_id"] for t in s["tables"]] for s in sels},
              "status": {s["requirement_id"]: s["status"] for s in sels},
              "reasons": {s["requirement_id"]: s["reasons"] for s in sels},
              "route": {"kind": route.get("kind"), "reasons": route.get("reasons")}}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"id": q["id"], "runtime_ms": elapsed_ms, "selection_sha256": digest,
            "preview_errors": errors, "requirements": req_rows, "units": unit_rows, "answer_groups": len(groups),
            "groups_in_candidates": sum(bool(set(g) & candidates.keys()) for g in groups),
            "lost_by_refutation": lost,
            "lost_between_requirement_and_bundle": [k for k, g in enumerate(groups)
                                                     if set(g) & source_tables and not set(g) & bundle_tables]}


def unit_summary(units):
    live = [u for u in units if u["live"]]
    return {"total": len(units), "live": len(live),
            "today_in": sum(u["today_in"] for u in live), "today_first": sum(u["today_first"] for u in live),
            "one_no_gap": sum(u["one_no_gap"] for u in units),
            "with_empty_member": sum(bool(u.get("empty_members")) for u in units),
            "alternatives_mean": statistics.mean(u["alternatives"] for u in units) if units else None,
            "alternatives_max": max((u["alternatives"] for u in units), default=0),
            "not_in_explained_by": dict(collections.Counter(c for u in live if not u["today_in"]
                                                            for c in u["explained_by"]))}


def summarise(rows):
    reqs = [r for q in rows for r in q["requirements"]]
    units = [u for q in rows for u in q["units"]]
    live_q = [q for q in rows if any(u["live"] for u in q["units"])]
    elapsed = [q["runtime_ms"] for q in rows]
    failed = [{"id": q["id"], **e} for q in rows for e in q.get("preview_errors") or []]
    return {"questions": len(rows), "questions_with_live_units": len(live_q),
            "preview_failures": {"requirements": len(failed), "questions": len({f["id"] for f in failed}),
                                 "by_error": dict(collections.Counter(f["error"] for f in failed)),
                                 "list": failed},
            "today_in_questions": sum(all(u["today_in"] for u in q["units"] if u["live"]) for q in live_q),
            "today_first_questions": sum(all(u["today_first"] for u in q["units"] if u["live"]) for q in live_q),
            "units": unit_summary(units), "hinted_bundles": unit_summary([u for u in units if u["bundle"]]),
            "answer_groups": sum(q["answer_groups"] for q in rows),
            "groups_in_candidates": sum(q["groups_in_candidates"] for q in rows),
            "lost_by_refutation": sum(len(q["lost_by_refutation"]) for q in rows),
            "refutation_loss_codes": {code: sum(code in g["codes"] for q in rows for g in q["lost_by_refutation"])
                                      for code in CODES},
            "lost_between_requirement_and_bundle": sum(len(q["lost_between_requirement_and_bundle"]) for q in rows),
            "cuts": {"pool_cut_requirements": sum(r["pool_cut"] for r in reqs),
                     "cover_cut_requirements": sum(r["cover_cut"] for r in reqs),
                     "decision_cut_units": sum(u["cut"].get("decision_cut", False) for u in units),
                     "decision_combinations_omitted": sum(u["omitted"] for u in units)},
            "sizes": {"restorable_max": max((r["restorable"] for r in reqs), default=0),
                      "unchecked_total": sum(r["unchecked"] for r in reqs),
                      "covers_tried_max": max((r["tried"] for r in reqs), default=0),
                      "requirement_alternatives_max": max((r["alternatives"] for r in reqs), default=0),
                      "bundle_combinations_max": max((u["combinations"] for u in units), default=0)},
            "runtime_ms": {"median": statistics.median(elapsed) if elapsed else None,
                           "max": max(elapsed, default=None),
                           "max_question": max(rows, key=lambda q: q["runtime_ms"])["id"] if rows else None}}


def datasets(gen):
    questions = load_questions(None)
    ids = sorted(c for c, q in questions.items() if q["dataset"] in ("golden", "development"))
    groups = load_groups(set(ids))
    out = {"dev217": [{"id": c, **questions[c], "groups": groups.get(c, [])} for c in ids]}
    if gen is not None:
        entries = [json.loads(line) for line in gen.read_text(encoding="utf-8").splitlines() if line.strip()]
        out["generated"] = [{"id": q["id"], "question": q["question"], "as_of": None,
                             "groups": [q["answer_tables"]]} for q in entries]
    return out


def markdown(result):
    lines = ["# 계획 11 R3 관찰 측정", "",
             "두 열 모두 R3 구현이다. before는 R1b 상한, after는 데이터 파일의 잠정 상한이다. "
             "모델 끔·외부 호출 차단 상태에서 측정하며 독립 평가가 아니다.", "",
             "시간은 카탈로그 초기화를 제외한 문항별 run_outer 전체 시간(ms)이다. "
             "각 집합·상한마다 새 카탈로그를 사용하며 첫 문항의 지연 초기화 비용은 포함한다.", ""]
    for name, profiles in result["datasets"].items():
        lines += [f"## {name}", "", "| 지표 | before | after |", "|---|---:|---:|"]
        def values(s):
            u, b, c, t = s["units"], s["hinted_bundles"], s["cuts"], s["runtime_ms"]
            return [("문항", s["questions"]),
                    ("관찰 실패(요구 수)", s["preview_failures"]["requirements"]),
                    ("현재 전체 배정이 대안에 포함(문항)", f"{s['today_in_questions']}/{s['questions_with_live_units']}"),
                    ("현재 전체 배정이 첫 대안(문항)", f"{s['today_first_questions']}/{s['questions_with_live_units']}"),
                    ("현재 묶음 선택이 포함(힌트 묶음)", f"{b['today_in']}/{b['live']}"),
                    ("현재 묶음 선택이 첫 대안(힌트 묶음)", f"{b['today_first']}/{b['live']}"),
                    ("반증으로 잃은 정답 묶음", s["lost_by_refutation"]),
                    ("요구 D→묶음 D에서 잃은 정답 묶음", s["lost_between_requirement_and_bundle"]),
                    ("대안 하나·빈틈 없음(전체 단위)", u["one_no_gap"]),
                    ("빈 구성원 배정이 있는 단위", u["with_empty_member"]),
                    ("단위당 대안 평균", u["alternatives_mean"]), ("단위당 대안 최대", u["alternatives_max"]),
                    ("pool_cut(복원 후 요구 수)", c["pool_cut_requirements"]),
                    ("cover_cut(요구 수)", c["cover_cut_requirements"]),
                    ("decision_cut(단위 수)", c["decision_cut_units"]),
                    ("문항 시간 중앙값 ms", t["median"]), ("문항 시간 최대 ms", t["max"])]
        for (label, before), (_, after) in zip(values(profiles["before"]), values(profiles["after"])):
            lines.append(f"| {label} | {before} | {after} |")
        lines += ["", f"상한별 선택 결과 차이: {result['selection_differences'][name]}", "",
                  f"대안 미포함 원인(after): {profiles['after']['units']['not_in_explained_by']}", ""]
    lines += ["## 상한 판단 자료", "", "설정은 잠정이며 이 스크립트는 값을 확정하거나 파일을 수정하지 않는다.", "",
              "- RESTORE_CAP=null: 복원 가능한 후보를 모두 복원한다. unchecked가 있으면 pool_cut은 남는다.",
              "- OBS_COVERS_CAP/OBS_COMBOS: after의 cover_cut과 시간 중앙값·최댓값을 함께 확인한다. "
              "잘림이 남으면 sizes와 문항별 기록으로 원인을 확인하고 다시 측정한다.",
              "- DECISION_CAP: 전체 배정 수의 상한이다. 생략 수와 decision_cut을 별도로 기록한다.", "",
              f"검증 문제: {result['problems'] or '없음'}", "",
              "반증 손실은 후보 풀+복원 안의 정답 허용 표가 모든 후보 요구에서 증명서로 제거된 경우다. "
              "후보에 처음부터 없는 정답은 이 손실로 세지 않는다. 요구 D→묶음 D 손실은 요구 대안에 "
              "남았던 정답 허용 표가 묶음 대안에서 모두 없어지는 경우로 따로 센다.", ""]
    return "\n".join(lines)


def main(out, gen):
    out = out.resolve()
    if not out.is_relative_to(ROOT):
        raise ValueError("output directory must be inside the repository")
    out.mkdir(parents=True, exist_ok=True)
    policy = json.loads(paths.DECISION_POLICY.read_text(encoding="utf-8"))["observation_caps"]
    after = {k: policy[k] for k in ("RESTORE_CAP", "OBS_COVERS_CAP", "OBS_COMBOS", "DECISION_CAP")}
    profiles = {"before": {**after, **policy["r1b_caps"]}, "after": after}
    result = {"caps_status": policy["status"], "caps": profiles, "datasets": {}, "selection_differences": {},
              "generated_included": gen is not None, "problems": []}
    detail = {}
    for name, questions in datasets(gen).items():
        result["datasets"][name], detail[name] = {}, {}
        for profile, values in profiles.items():
            with observation_caps(values):
                layer = load_layer(include_external_synonyms=False)
                rows = []
                # Flush every completed question, so a slow/failing later question does not hide prior timings.
                with (out / f"{name}_{profile}_questions.jsonl").open("w", encoding="utf-8") as log:
                    for q in questions:
                        row = question_row(q, layer)
                        rows.append(row)
                        log.write(json.dumps(row, ensure_ascii=False) + "\n")
                        log.flush()
                        print(json.dumps({"dataset": name, "profile": profile, "id": row["id"],
                                          "runtime_ms": row["runtime_ms"],
                                          "cuts": summarise([row])["cuts"]}, ensure_ascii=False), flush=True)
            summary = summarise(rows)
            result["datasets"][name][profile], detail[name][profile] = summary, rows
            print(json.dumps({"dataset": name, "profile": profile, "runtime_ms": summary["runtime_ms"],
                              "remaining_cuts": summary["cuts"]}, ensure_ascii=False), flush=True)
            for field in ("lost_by_refutation", "lost_between_requirement_and_bundle"):
                if summary[field]:
                    result["problems"].append(f"{name}/{profile}: {field}={summary[field]}")
            if summary["preview_failures"]["requirements"]:
                result["problems"].append(f"{name}/{profile}: preview_failures="
                                          f"{summary['preview_failures']['by_error']}")
        before = {r["id"]: r["selection_sha256"] for r in detail[name]["before"]}
        different = [r["id"] for r in detail[name]["after"] if before.get(r["id"]) != r["selection_sha256"]]
        result["selection_differences"][name] = different
        if different:
            result["problems"].append(f"{name}: selection changed between cap profiles")
    result["network_attempts"] = len(_guard.NETWORK_ATTEMPTS)
    if result["network_attempts"]:
        result["problems"].append("network attempts were blocked")
    (out / "r3_counts.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "r3_counts.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", nargs="?", type=Path, default=OUT)
    parser.add_argument("--gen", type=Path)
    args = parser.parse_args()
    raise SystemExit(main(args.out_dir, args.gen))
