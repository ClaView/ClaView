"""Plan 11 R1b counts from the observation path (``signals.decision_preview``), model off, no external call.

The selection itself is the unchanged one; only the new fields are read. Per data set:
- the share of questions (and of live requirements) whose current selection is among the alternatives of the
  requirement decision D, and is its first alternative; when it is not among them, the codes that explain it;
- per refutation code, the answer groups lost: an answer group with a table among some requirement's candidates
  (pool and restored ones) is lost when every such table is removed (a table certificate, or every cell refuted)
  in every requirement it is a candidate of; the codes of those certificates are counted;
- gap counts recomputed from the new fields (the first alternative of every live requirement), and how many
  requirements end with exactly one alternative and no gap;
- pool cuts before restoration (R0 rule: a cut table with a strong occurrence) and after (C4: the restoration
  itself cut), cover cuts (after edition linking), and the sizes the caps are set from (K14).
Dev 217 (golden + development) and, when given, a generated set (jsonl with ``answer_tables``, one answer group).
Neither is an independent evaluation (both were used to improve the code).

Usage (repository root):
  $env:PYTHONHASHSEED="0"; .venv/Scripts/python -B -X utf8 scripts/plan11_r1b_counts.py [--gen <jsonl>] [out_dir]
Writes r1b_counts.json and r1b_counts.md to runs/plan11_r1b/ (or out_dir).
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from claview_v3 import _guard  # noqa: E402

_guard.install_network_block()

from claview_v3.agent.graph import run_outer  # noqa: E402
from claview_v3.catalog.layer import load_layer  # noqa: E402
from claview_v3.select.decision import GAP_CODES  # noqa: E402
from claview_v3.select.refute import CODES  # noqa: E402
from score_selection import load_groups, load_questions  # noqa: E402

OUT = ROOT / "runs" / "plan11_r1b"


def _removed(prev: dict) -> dict[str, list[str]]:
    """Table -> refutation codes for the candidates the preview removed (table scope, or every cell)."""
    tabs, cells = prev["refutations"]["tables"], prev["refutations"]["cells"]
    kept = {t for a in prev["decision"]["alternatives"] for t in a["tables"]}
    kept |= {m for a in prev["decision"]["alternatives"] for e in a["edition_links"] for m in e["members"]}
    out = {t: sorted({c["code"] for c in cs}) for t, cs in tabs.items()}
    for t, by_cell in cells.items():
        if t not in kept and t not in out:
            out[t] = sorted({c["code"] for cs in by_cell.values() for c in cs})
    return out


def question_row(qid: str, question: str, as_of, groups: list[list[str]], layer) -> dict:
    state = run_outer(question, "straight", layer=layer, as_of=as_of)
    sels = state.get("selections") or []
    analysis = state.get("analysis") or {}
    route = (state.get("selection_route") or {}).get("kind")
    reqs, cand_of, removed_in = [], collections.defaultdict(set), collections.defaultdict(dict)
    for s in sels:
        prev = (s.get("signals") or {}).get("decision_preview")
        if prev is None:
            continue
        dec = prev["decision"]
        facts = ((s.get("signals") or {}).get("facts") or {}).get("tables") or {}
        cands = set(facts) | set(prev["candidates"]["restored"])
        for t in cands:
            cand_of[t].add(s["requirement_id"])
        for t, codes in _removed(prev).items():
            removed_in[t][s["requirement_id"]] = codes
        first = dec["alternatives"][0] if dec["alternatives"] else None
        pc = (s.get("signals") or {}).get("pool_cut") or {}
        reqs.append({
            "requirement_id": s["requirement_id"], "status": s["status"], "live": s["status"] != "no_table",
            "today_in": dec["today"]["in_alternatives"], "today_first": dec["today"]["first"],
            "explained_by": dec["today"]["explained_by"], "alternatives": len(dec["alternatives"]),
            "complete_alternatives": sum(1 for a in dec["alternatives"] if a["complete"]),
            "first_gaps": list(first["gaps"]) if first else [], "requirement_gaps": list(dec["gaps"]),
            "one_and_no_gap": len(dec["alternatives"]) == 1 and not first["gaps"] and not dec["gaps"]
            if first else False,
            "pool_cut_before": bool(pc.get("matched_count")), "pool_cut_after": bool(pc.get("pool_cut")),
            "restorable": pc.get("restorable", 0), "restored": len(prev["candidates"]["restored"]),
            "unchecked": pc.get("unchecked", 0), "cover_cut": dec["cut"]["cover_cut"],
            "editions": prev["candidates"]["editions"], "live_tables": prev["candidates"]["live"],
            "tried": prev["candidates"]["tried"],
            "refuted_tables": len(prev["refutations"]["tables"]),
            "codes": sorted({c["code"] for cs in prev["refutations"]["tables"].values() for c in cs}
                            | {c["code"] for t in prev["refutations"]["cells"].values() for cs in t.values()
                               for c in cs})})
    lost = []
    for k, g in enumerate(groups):
        tabs = [t for t in g if t in cand_of]
        if not tabs:
            continue
        if all(all(r in removed_in.get(t, {}) for r in cand_of[t]) for t in tabs):
            codes = sorted({c for t in tabs for r in cand_of[t] for c in removed_in[t][r]})
            lost.append({"group": k, "codes": codes})
    rows = (analysis.get("judgement") or {}).get("failed_sources") or []
    return {"id": qid, "route": route, "requirements": reqs, "lost_groups": lost,
            "groups": len(groups), "groups_in_candidates": sum(1 for g in groups if any(t in cand_of for t in g)),
            "failed_sources": len(rows),
            "failed_sources_with_ids": sum(1 for r in rows if r.get("condition_ids") or r.get("fragment_ids"))}


def summarise(rows: list[dict]) -> dict:
    live_q = [r for r in rows if any(x["live"] for x in r["requirements"])]
    live = [x for r in rows for x in r["requirements"] if x["live"]]
    allreq = [x for r in rows for x in r["requirements"]]
    in_all = [r for r in live_q if all(x["today_in"] for x in r["requirements"] if x["live"])]
    first_all = [r for r in live_q if all(x["today_first"] for x in r["requirements"] if x["live"])]
    explained = collections.Counter(c for x in live if not x["today_in"] for c in x["explained_by"])
    per_code = {}
    for code in CODES:
        hit = [g for r in rows for g in r["lost_groups"] if code in g["codes"]]
        only = [g for r in rows for g in r["lost_groups"] if g["codes"] == [code]]
        per_code[code] = {"groups_lost": len(hit), "groups_lost_by_this_code_only": len(only),
                          "questions": sum(1 for r in rows if any(code in g["codes"] for g in r["lost_groups"])),
                          "requirements_with_code": sum(1 for x in allreq if code in x["codes"])}
    gaps = {}
    proceed = [r for r in rows if r["route"] == "proceed"]
    for code in [*GAP_CODES, "pool_cut", "cover_cut"]:
        def has(x):
            return code in x["first_gaps"] or code in x["requirement_gaps"]
        gaps[code] = {"live_requirements": sum(1 for x in live if has(x)),
                      "questions": sum(1 for r in live_q if any(has(x) for x in r["requirements"] if x["live"])),
                      "proceed_questions": sum(1 for r in proceed
                                               if any(has(x) for x in r["requirements"] if x["live"]))}
    sizes = [x["restorable"] for x in allreq]
    return {
        "questions": len(rows), "questions_with_live_requirement": len(live_q),
        "routes": dict(collections.Counter(r["route"] for r in rows)),
        "today_in_alternatives": {"questions": len(in_all), "of": len(live_q),
                                  "requirements": sum(1 for x in live if x["today_in"]), "of_requirements": len(live)},
        "today_first_alternative": {"questions": len(first_all), "of": len(live_q),
                                    "requirements": sum(1 for x in live if x["today_first"]),
                                    "of_requirements": len(live)},
        "not_in_explained_by": dict(explained),
        "answer_groups": {"total": sum(r["groups"] for r in rows),
                          "in_candidates": sum(r["groups_in_candidates"] for r in rows),
                          "lost_by_refutation": sum(len(r["lost_groups"]) for r in rows),
                          "questions_with_a_loss": sum(1 for r in rows if r["lost_groups"])},
        "refutation_codes": per_code,
        "gaps_first_alternative": gaps,
        "one_alternative_no_gap": {"live_requirements": sum(1 for x in live if x["one_and_no_gap"]),
                                   "of": len(live),
                                   "questions_all": sum(1 for r in live_q if all(
                                       x["one_and_no_gap"] for x in r["requirements"] if x["live"]))},
        "alternatives_per_live_requirement": {
            "mean": round(sum(x["alternatives"] for x in live) / len(live), 2) if live else None,
            "max": max((x["alternatives"] for x in live), default=0),
            "complete_mean": round(sum(x["complete_alternatives"] for x in live) / len(live), 2) if live else None},
        "cuts": {"requirements": len(allreq),
                 "pool_cut_before_restoration": sum(1 for x in allreq if x["pool_cut_before"]),
                 "pool_cut_after_restoration": sum(1 for x in allreq if x["pool_cut_after"]),
                 "pool_cut_after_by_unchecked": sum(1 for x in allreq if x["pool_cut_after"] and x["unchecked"]),
                 "cover_cut": sum(1 for x in allreq if x["cover_cut"]),
                 "restorable_max": max(sizes, default=0),
                 "restorable_over_12": sum(1 for s in sizes if s > 12),
                 "restorable_over_30": sum(1 for s in sizes if s > 30),
                 "restored_total": sum(x["restored"] for x in allreq),
                 "editions_max": max((x["editions"] for x in allreq), default=0),
                 "live_tables_max": max((x["live_tables"] for x in allreq), default=0),
                 "combinations_tried_max": max((x["tried"] for x in allreq), default=0)},
        "failed_sources": {"rows": sum(r["failed_sources"] for r in rows),
                           "rows_with_ids": sum(r["failed_sources_with_ids"] for r in rows)},
    }


def run_dev(layer) -> tuple[list[dict], dict]:
    allq = load_questions(None)
    ids = sorted(c for c, q in allq.items() if q["dataset"] in ("golden", "development"))
    groups = load_groups(set(ids))
    rows = [question_row(c, allq[c]["question"], allq[c].get("as_of"), groups.get(c, []), layer) for c in ids]
    return rows, summarise(rows)


def run_gen(layer, path: Path) -> tuple[list[dict], dict]:
    gen = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [question_row(q["id"], q["question"], None, [q["answer_tables"]], layer) for q in gen]
    return rows, summarise(rows)


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.1%})" if b else f"{a}/0"


def markdown(name: str, s: dict) -> list[str]:
    t, f = s["today_in_alternatives"], s["today_first_alternative"]
    a, c, o = s["answer_groups"], s["cuts"], s["one_alternative_no_gap"]
    lines = [f"## {name}", "",
             f"- 문항 {s['questions']}(살아 있는 요구가 있는 문항 {s['questions_with_live_requirement']}), 길 {s['routes']}",
             f"- 지금 선택 ∈ D 대안: 문항 {_pct(t['questions'], t['of'])}, 요구 {_pct(t['requirements'], t['of_requirements'])}",
             f"- 지금 선택 = D 첫 대안: 문항 {_pct(f['questions'], f['of'])}, 요구 {_pct(f['requirements'], f['of_requirements'])}",
             f"- 대안에 없을 때의 설명: {s['not_in_explained_by'] or '없음'}",
             f"- 정답 묶음 {a['total']}개, 후보(풀+되살림) 안 {a['in_candidates']}, 반증으로 잃음 {a['lost_by_refutation']}"
             f"(문항 {a['questions_with_a_loss']})",
             f"- 대안 하나·빈틈 없음: 요구 {_pct(o['live_requirements'], o['of'])}, 모든 요구가 그런 문항 {o['questions_all']}",
             f"- 살아 있는 요구당 대안 수: 평균 {s['alternatives_per_live_requirement']['mean']}, 최대 "
             f"{s['alternatives_per_live_requirement']['max']}, 완전 덮기 평균 {s['alternatives_per_live_requirement']['complete_mean']}",
             f"- 잘림(요구 {c['requirements']}): pool_cut 되살리기 전 {c['pool_cut_before_restoration']} → 뒤 "
             f"{c['pool_cut_after_restoration']}(그중 검사 불가 후보 때문 {c['pool_cut_after_by_unchecked']}), cover_cut {c['cover_cut']}",
             f"- 상한 근거: 되살릴 수 있는 후보 최대 {c['restorable_max']}, 12 초과 {c['restorable_over_12']}, 30 초과 "
             f"{c['restorable_over_30']}, 되살린 후보 합 {c['restored_total']}, 판 연결 뒤 단위 최대 {c['editions_max']}, "
             f"조합 시도 최대 {c['combinations_tried_max']}",
             f"- failed_sources 줄 {s['failed_sources']['rows']}, 그중 조건·조각 ID가 있는 줄 {s['failed_sources']['rows_with_ids']}",
             "", "| 반증 코드 | 잃은 정답 묶음 | 이 코드만으로 잃음 | 문항 | 코드가 나온 요구 |", "|---|---|---|---|---|"]
    for code, v in s["refutation_codes"].items():
        lines.append(f"| {code} | {v['groups_lost']} | {v['groups_lost_by_this_code_only']} | {v['questions']} | "
                     f"{v['requirements_with_code']} |")
    lines += ["", "| 빈틈 코드(첫 대안 기준) | 살아 있는 요구 | 문항 | 지금 확정(proceed) 문항 |", "|---|---|---|---|"]
    for code, v in s["gaps_first_alternative"].items():
        lines.append(f"| {code} | {v['live_requirements']} | {v['questions']} | {v['proceed_questions']} |")
    return lines + [""]


def main(out: Path, gen: Path | None) -> dict:
    layer = load_layer(include_external_synonyms=False)
    dev_rows, dev = run_dev(layer)
    result = {"model_calls": 0, "dev217": dev}
    detail = {"dev217": dev_rows}
    if gen is not None:
        gen_rows, g = run_gen(layer, gen)
        result["gen"] = {"file": gen.name, **g}
        detail["gen"] = gen_rows
    result["network_attempts"] = len(_guard.NETWORK_ATTEMPTS)
    out.mkdir(parents=True, exist_ok=True)
    (out / "r1b_counts.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf8")
    (out / "r1b_counts_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf8")
    lines = ["# 계획 11 R1b 관찰 경로 집계 (모델 끔, 외부 호출 0회)", "",
             "선택 결과는 R1b 전과 같다(무변경 스냅샷). 새 필드 signals.decision_preview만 읽었다. 개발 217과 생성 집합은 "
             "개선에 쓴 자료라 독립 평가가 아니다.", ""]
    lines += markdown("개발 217", dev)
    if gen is not None:
        lines += markdown(f"생성 {gen.name}", result["gen"])
    (out / "r1b_counts.md").write_text("\n".join(lines), encoding="utf8")
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    gen_path = None
    if "--gen" in args:
        gen_path = Path(args[args.index("--gen") + 1])
        del args[args.index("--gen"):args.index("--gen") + 2]
    got = main(Path(args[0]) if args else OUT, gen_path)
    print(json.dumps(got, ensure_ascii=False, indent=1))
