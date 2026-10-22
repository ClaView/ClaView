"""Plan 11 R0 gap counts on the development questions (golden + development, 217), model off, no external call.

Reads only the R0 record fields (``signals.facts``, ``signals.covers_all``, ``signals.pool_cut``,
``signals.pruned``, ``signals.relations``, ``judgement.failed_sources``, ``interpretation.unsupported_sources``);
the selection itself is the unchanged one. Reports:
- answer groups lost before the evidence filters (no table of the group in any requirement's candidate pool) and at
  the evidence filter (in the pool, but no table of the group with evidence);
- per-requirement assignment match, apart from the question-level set score: a live requirement matches when every
  table it chose belongs to some answer group (no extra table) and it hits at least one group;
- for each gap code, how many questions now routed ``proceed`` (확정) would turn into asks if the code were switched
  on, and how many of those have the right tables now (the existing set scoring, ``score_sets``);
- the union: how many ``proceed`` questions remain with every code on.
The 217 questions were used to improve the code: no independent evaluation. Estimates of what R2 would do; the
switches themselves are R2 decisions.

Usage (repository root): $env:PYTHONHASHSEED="0"; .venv/Scripts/python -B -X utf8 scripts/a/plan11_gap_counts.py
Writes runs/plan11_r0/gap_counts.json and gap_counts.md.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "a"))

from claview.a import _guard  # noqa: E402

_guard.install_network_block()

from claview.a.agent.graph import run_outer  # noqa: E402
from claview.a.catalog.layer import load_layer  # noqa: E402
from claview.a.reused.table_sets import score_sets  # noqa: E402
from claview.a.select.cover import TITLE_BONUS  # noqa: E402,F401  (the bonus the negated-name code looks at)
from claview.a.select.state import AUTO_CONDITIONS  # noqa: E402
from claview.a.select.tables import nospace, selection_catalog  # noqa: E402
from score_selection import load_groups, load_questions, output_tables  # noqa: E402

OUT = ROOT / "runs" / "plan11_r0"
DATASETS = ("golden", "development")
AUTO_CODES = [code for code, _ in AUTO_CONDITIONS]
# 7-6 first values of the uncertainty policy, as classes of the failure sources (record: no file decides yet)
CLASSES = {"leftover_word": "leftover", "mixed_reading": "reading_split", "scope_ambiguous": "scope",
           "ambiguous_attribution": "attribution", "unused_name": "unused_name", "inexact_term": "inexact"}
NEW_CODES = ["indicator_weak", "cover_uncertain", "cover_impossible", "pool_cut", "options_cut",
             "period_partial_by_anchor", "uncertain_interp", "pruned_by_score", "pruned_by_representative_source",
             "pruned_by_representative_frequency", "title_unproven_dropped", "relation_mismatch",
             "unsupported_mixed", "negated_name_bonus", "target_trusted_unconditionally"]


def _live(sels: list[dict]) -> list[dict]:
    return [s for s in sels if s["status"] != "no_table"]


def _chosen_facts(s: dict) -> list[dict]:
    tables = ((s.get("signals") or {}).get("facts") or {}).get("tables") or {}
    return [tables[t["table_id"]] for t in s.get("tables") or [] if t["table_id"] in tables]


def interp_class(analysis: dict) -> tuple[bool, bool]:
    """(undecided, unclassifiable): a failure with no requirement source or of a kind outside the first policy
    values, or failures of two or more classes at once, is unclassifiable."""
    j = analysis.get("judgement") or {}
    failed = list(j.get("failed_conditions") or [])
    if not failed:
        return False, False
    srcs = j.get("failed_sources") or []
    classes = set()
    for code in failed:
        rows = [x for x in srcs if x["condition"] == code]
        if code == "row_2_or_7" and rows and all(x["detail"] == "appositive" for x in rows):
            classes.add("appositive")
            continue
        if code not in CLASSES or not rows:
            return True, True
        classes.add(CLASSES[code])
    return True, len(classes) > 1


def negated_bonus(analysis: dict, sels: list[dict], views) -> bool:
    """A word of ``judgement.negated`` sits in a chosen table's name stem that earns the title bonus (the stem is in
    the question words) or in one of its title-word score items."""
    neg = [nospace(w) for w in (analysis.get("judgement") or {}).get("negated") or [] if len(nospace(w)) >= 2]
    if not neg:
        return False
    qns = nospace((analysis.get("text") or {}).get("raw") or "")
    for s in _live(sels):
        items = (s.get("signals") or {}).get("score_items") or {}
        for t in s.get("tables") or []:
            v = views.get(t["table_id"])
            stem = nospace(v.title.split("(")[0]) if v else ""
            if len(stem) >= 2 and stem in qns and any(w in stem or stem in w for w in neg):
                return True
            if any(i["item"] == "title_word" and any(w in i["reason"] for w in neg)
                   for i in items.get(t["table_id"], [])):
                return True
    return False


def codes_of(analysis: dict, sels: list[dict], views) -> set[str]:
    out: set[str] = set()
    live = _live(sels)
    for s in live:
        sig = s.get("signals") or {}
        facts = _chosen_facts(s)
        req_has_indicator = any(f["indicator"]["name"] for f in facts)
        if req_has_indicator and any(f["indicator"]["weak"] for f in facts):
            out.add("indicator_weak")
        cells = {f["cells"]["combinable"] for f in facts}
        if "undetermined" in cells:
            out.add("cover_uncertain")
        if "impossible" in cells:
            out.add("cover_impossible")
        if (sig.get("pool_cut") or {}).get("pool_cut"):
            out.add("pool_cut")
        if (sig.get("pruned") or {}).get("options_cut"):
            out.add("options_cut")
        anchors = [f["period"].get("by_anchor") for f in facts]
        if "partial" in anchors and "covered" not in anchors:
            out.add("period_partial_by_anchor")
        for r in (sig.get("pruned") or {}).get("records") or []:
            if r.get("reason") == "score":
                out.add("pruned_by_score")
            if r.get("step") == "representative" and not r.get("marked") and r.get("reason") in ("source",
                                                                                                "frequency"):
                out.add(f"pruned_by_representative_{r['reason']}")
            if r.get("step") == "set_dimension" and r.get("reason") == "title_unproven":
                out.add("title_unproven_dropped")
        if any(r.get("mismatch") for r in sig.get("relations") or []):
            out.add("relation_mismatch")
        if any(f.get("entered_by") == "target" for f in facts):
            out.add("target_trusted_unconditionally")
        for reason in s.get("reasons") or []:
            out.add(f"confirm:{reason}")
    if interp_class(analysis)[1]:
        out.add("uncertain_interp")
    uns = (analysis.get("interpretation") or {}).get("unsupported_sources") or {}
    if any(not x.get("all_out_of_scope") for x in uns.values()):
        out.add("unsupported_mixed")
    if negated_bonus(analysis, sels, views):
        out.add("negated_name_bonus")
    return out


def requirement_match(sels: list[dict], groups: list[list[str]]) -> tuple[int, int, bool]:
    """(live requirements, matching ones, every live requirement matches)."""
    live = _live(sels)
    allowed = {t for g in groups for t in g}
    ok = 0
    for s in live:
        tables = [t["table_id"] for t in s.get("tables") or []]
        if tables and all(t in allowed for t in tables) and any(set(tables) & set(g) for g in groups):
            ok += 1
    return len(live), ok, bool(live) and ok == len(live)


def main() -> dict:
    allq = load_questions(None)
    ids = sorted(c for c, q in allq.items() if q["dataset"] in DATASETS)
    groups = load_groups(set(ids))
    layer = load_layer(include_external_synonyms=False)
    views = selection_catalog(layer).views
    rows = []
    for cid in ids:
        q = allq[cid]
        state = run_outer(q["question"], "straight", layer=layer, as_of=q.get("as_of"))
        sels, analysis = state.get("selections") or [], state.get("analysis") or {}
        route = (state.get("selection_route") or {}).get("kind")
        g = groups.get(cid, [])
        pool, with_ev = set(), set()
        for s in sels:
            for t, f in (((s.get("signals") or {}).get("facts") or {}).get("tables") or {}).items():
                pool.add(t)
                if f.get("with_evidence"):
                    with_ev.add(t)
        tables = output_tables(sels)
        n_live, n_ok, all_ok = requirement_match(sels, g)
        rows.append({"case_id": cid, "route": route, "set_exact": bool(score_sets(g, tables, [], sorted(pool),
                                                                                  5)["set_exact"]),
                     "groups": len(g), "lost_pool": sum(1 for x in g if not set(x) & pool),
                     "lost_evidence": sum(1 for x in g if set(x) & pool and not set(x) & with_ev),
                     "live": n_live, "live_ok": n_ok, "all_requirements_ok": all_ok,
                     "codes": sorted(codes_of(analysis, sels, views)),
                     "undecided": interp_class(analysis)[0],
                     "unsupported_any": bool((analysis.get("interpretation") or {}).get("unsupported_sources")),
                     "negated_any": bool((analysis.get("judgement") or {}).get("negated"))})
    proceed = [r for r in rows if r["route"] == "proceed"]
    found = collections.Counter(c for r in proceed for c in r["codes"])
    order = NEW_CODES + [f"confirm:{c}" for c in AUTO_CODES]
    order += sorted(c for c in found if c not in order)
    per_code = {}
    for code in order:
        hit = [r for r in proceed if code in r["codes"]]
        per_code[code] = {"questions": len(hit), "right_now": sum(1 for r in hit if r["set_exact"])}
    clean = [r for r in proceed if not r["codes"]]
    summary = {
        "questions": len(rows), "model_calls": 0, "network_attempts": len(_guard.NETWORK_ATTEMPTS),
        "routes": dict(collections.Counter(r["route"] for r in rows)),
        "set_exact": sum(1 for r in rows if r["set_exact"]),
        "proceed": len(proceed), "proceed_right": sum(1 for r in proceed if r["set_exact"]),
        "answer_groups": {"total": sum(r["groups"] for r in rows),
                          "lost_before_evidence_filters": sum(r["lost_pool"] for r in rows),
                          "lost_at_evidence_filter": sum(r["lost_evidence"] for r in rows),
                          "questions_with_a_group_lost_before_filters": sum(1 for r in rows if r["lost_pool"])},
        "requirements": {"live": sum(r["live"] for r in rows), "matching": sum(r["live_ok"] for r in rows),
                         "questions_all_matching": sum(1 for r in rows if r["all_requirements_ok"]),
                         "questions_set_exact": sum(1 for r in rows if r["set_exact"]),
                         "all_matching_not_set_exact": sum(1 for r in rows if r["all_requirements_ok"]
                                                           and not r["set_exact"]),
                         "set_exact_not_all_matching": sum(1 for r in rows if r["set_exact"]
                                                           and not r["all_requirements_ok"])},
        "gap_codes": per_code,
        "union": {"proceed_left": len(clean), "proceed_left_right": sum(1 for r in clean if r["set_exact"]),
                  "turned_to_ask": len(proceed) - len(clean)},
        "all_questions": {"unsupported_mixed": sum(1 for r in rows if "unsupported_mixed" in r["codes"]),
                          "negated_name_bonus": sum(1 for r in rows if "negated_name_bonus" in r["codes"]),
                          "relation_mismatch": sum(1 for r in rows if "relation_mismatch" in r["codes"]),
                          "interpretation_undecided": sum(1 for r in rows if r["undecided"]),
                          "unsupported_any": sum(1 for r in rows if r["unsupported_any"]),
                          "negated_any": sum(1 for r in rows if r["negated_any"])},
        "per_question": [{k: r[k] for k in ("case_id", "route", "set_exact", "codes")} for r in rows],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gap_counts.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
    (OUT / "gap_counts.md").write_text(markdown(summary), encoding="utf8")
    return summary


def markdown(s: dict) -> str:
    a, q = s["answer_groups"], s["requirements"]
    lines = ["# 계획 11 R0 빈틈 코드 집계 (개발 217, 모델 끔, 외부 호출 0회)", "",
             "기록 필드만 읽었다. 선택 결과는 R0 전과 같다. 217문항은 개선에 쓴 자료라 독립 평가가 아니다. "
             "'켜면 되묻기'는 R2에서 코드를 켰을 때의 추정이다.", "",
             f"- 길: {s['routes']}, set_exact {s['set_exact']}/{s['questions']}",
             f"- 확정(proceed) {s['proceed']}문항, 그중 지금 맞음 {s['proceed_right']}",
             f"- 정답 묶음 {a['total']}개 중 거르기 전 후보에서 빠짐 {a['lost_before_evidence_filters']}"
             f"(문항 {a['questions_with_a_group_lost_before_filters']}), 근거 거르기에서 빠짐 "
             f"{a['lost_at_evidence_filter']}",
             f"- 요구별 배정 일치: 살아 있는 요구 {q['live']} 중 {q['matching']}, 모든 요구 일치 문항 "
             f"{q['questions_all_matching']} (set_exact {q['questions_set_exact']}; 요구 일치인데 set_exact 아님 "
             f"{q['all_matching_not_set_exact']}, set_exact인데 요구 불일치 {q['set_exact_not_all_matching']})", "",
             "| 코드 | 켜면 되묻기로 갈 확정 문항 | 그중 지금 맞음 |", "|---|---|---|"]
    for code, v in s["gap_codes"].items():
        lines.append(f"| {code} | {v['questions']} | {v['right_now']} |")
    u = s["union"]
    lines += ["", f"- 모두 켜면 확정으로 남는 문항 {u['proceed_left']} (그중 맞음 {u['proceed_left_right']}), "
                  f"되묻기로 가는 문항 {u['turned_to_ask']}",
              f"- 전체 문항 기준: {s['all_questions']}", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    got = main()
    print(json.dumps({k: v for k, v in got.items() if k != "per_question"}, ensure_ascii=False, indent=1))
