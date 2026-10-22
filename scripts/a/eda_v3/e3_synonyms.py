"""EDA v3 item 3: synonym sources — counts and shapes.

Sources (read only): data/mappings/review/통계어사전_우선순위.csv, 통계어사전_후보.csv, 검색용_의미매핑_초안.json,
data/mappings/runtime/search_mappings.json (+ review_queue.json). Also counts candidate pairs that the catalog itself
offers: English name pairs, abbreviation pairs, and name variants (part before the parenthesis; 말잔/평잔 and
원계열/계절조정 tails removed).

Usage (repo root): PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/a/eda_v3/e3_synonyms.py
"""

from __future__ import annotations

import collections
import json
import re

from common import MAPPINGS, OUT, clean_value, key, load_catalog, load_meta, nfkc, paren_front, read_csv, write_csv, write_json

DIR = OUT / "3_synonyms"

_ABBR = re.compile(r"([가-힣][가-힣0-9·]{1,20})\s*\(([A-Za-z][A-Za-z0-9&.\-]{1,11})\)")
_ABBR_REV = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z0-9]{1,9})\s*\(([가-힣][가-힣0-9·]{1,20})\)")
# tails that only state a basis; removing them gives a name variant
_TAIL = re.compile(r"[\s,(]*(?:말잔|평잔|원계열|계절조정계열|계절조정|명목|실질)(?:[\s,]*(?:말잔|평잔|원계열|계절조정계열|계절조정|명목|실질))*\)?\s*$")


def tail_variant(name: str) -> str:
    s = nfkc(name).strip()
    inner = re.search(r"\(([^()]*)\)\s*$", s)
    if inner:
        parts = [p.strip() for p in re.split(r"[,\s]+", inner.group(1)) if p.strip()]
        kept = [p for p in parts if not re.fullmatch(r"말잔|평잔|원계열|계절조정계열|계절조정|명목|실질", p)]
        if len(kept) != len(parts):
            head = s[:inner.start()].rstrip()
            return f"{head}({', '.join(kept)})" if kept else head
        return s
    return _TAIL.sub("", s).strip() or s


def main() -> None:
    meta, catalog = load_meta(), load_catalog()
    out = {}

    # ---- 통계어사전_우선순위.csv
    pri = read_csv(MAPPINGS / "review/통계어사전_우선순위.csv")
    out["우선순위"] = {
        "행": len(pri),
        "유형": dict(collections.Counter(r["유형"] for r in pri).most_common()),
        "우선순위": dict(collections.Counter(r["우선순위"] for r in pri).most_common()),
        "분류사유": dict(collections.Counter(r["분류사유"] for r in pri).most_common()),
        "유사어_일상어 채워짐": sum(1 for r in pri if r["유사어_일상어"].strip()),
        "검토자 채워짐": sum(1 for r in pri if r["검토자"].strip()),
        "상태 채워짐": sum(1 for r in pri if r["상태"].strip()),
        "공식어≠핵심어": sum(1 for r in pri if r["공식어"] != r["핵심어"]),
        "고유 공식어": len({r["공식어"] for r in pri}),
    }
    # ---- 통계어사전_후보.csv
    cand = read_csv(MAPPINGS / "review/통계어사전_후보.csv")
    sims = sorted(float(r["직접유사도"]) for r in cand if r["직접유사도"])
    out["후보"] = {
        "행": len(cand),
        "고유 공식어": len({r["공식어"] for r in cand}),
        "고유 일상어후보": len({r["일상어후보"] for r in cand}),
        "유형": dict(collections.Counter(r["유형"] for r in cand)),
        "판정": dict(collections.Counter(r["판정"] for r in cand)),
        "자기매칭": dict(collections.Counter(r["자기매칭"] for r in cand)),
        "판정×자기매칭": {f"{a}/{b}": n for (a, b), n in collections.Counter((r["판정"], r["자기매칭"]) for r in cand).items()},
        "직접유사도 최소·중앙·최대": [sims[0], sims[len(sims) // 2], sims[-1]] if sims else [],
        "공식어당 후보 수": dict(collections.Counter(collections.Counter(r["공식어"] for r in cand).values())),
        "자동채택 예": [f"{r['공식어']}←{r['일상어후보']}" for r in cand if r["판정"] == "자동채택"][:18],
    }
    # ---- 검색용_의미매핑_초안.json
    sem = json.loads((MAPPINGS / "review/검색용_의미매핑_초안.json").read_text("utf8"))
    ms = sem["mappings"]
    out["의미매핑 초안"] = {
        "건": len(ms),
        "policy": sem.get("policy"),
        "relation": dict(collections.Counter(m["relation"] for m in ms)),
        "action": dict(collections.Counter(m["action"] for m in ms)),
        "relation×action": {f"{a}/{b}": n for (a, b), n in collections.Counter((m["relation"], m["action"]) for m in ms).items()},
        "status": dict(collections.Counter(m["status"] for m in ms)),
        "evidence_level": dict(collections.Counter(m["evidence_level"] for m in ms)),
        "normalization_only": sum(1 for m in ms if m.get("normalization_only")),
        "selection_only": sum(1 for m in ms if m.get("selection_only")),
        "auto_select_table=true": sum(1 for m in ms if m.get("auto_select_table")),
        "scope 표 수 분포": dict(collections.Counter(len((m.get("scope") or {}).get("table_ids") or []) for m in ms)),
        "고유 canonical": len({m["canonical_expression"] for m in ms}),
        "예": [f"{m['input_expression']}→{m['canonical_expression']} ({m['relation']})" for m in ms[:12]],
    }
    # ---- runtime/search_mappings.json
    rt = json.loads((MAPPINGS / "runtime/search_mappings.json").read_text("utf8"))
    rm = rt["mappings"]
    out["실행 사전"] = {
        "개념": len(rt["concepts"]), "매핑": len(rm),
        "search_terms 합": sum(len(c["search_terms"]) for c in rt["concepts"]),
        "relation": dict(collections.Counter(m["relation"] for m in rm)),
        "review_status": dict(collections.Counter(str(m["review_status"]) for m in rm)),
        "reviewer 채워짐": sum(1 for m in rm if m.get("reviewer")),
        "condition_status": dict(collections.Counter(str(m.get("condition_status")) for m in rm)),
        "근거 필드": dict(collections.Counter(re.sub(r"\[\d+\]", "[]", e["field"]) for c in rt["concepts"] for e in c["evidence"])),
    }
    rq = json.loads((MAPPINGS / "runtime/review_queue.json").read_text("utf8"))
    out["검수 큐"] = {"human_approved": rq.get("human_approved"), "legacy_rows": rq.get("legacy_rows"),
                    "current_rows": rq.get("current_rows"), "rows": len(rq.get("rows") or [])}
    approved = {
        "통계어사전_우선순위(상태·검토자 채워짐)": sum(1 for r in pri if r["상태"].strip() or r["검토자"].strip()),
        "통계어사전_후보(판정=자동채택, 사람 승인 아님)": sum(1 for r in cand if r["판정"] == "자동채택"),
        "의미매핑 초안(status≠draft)": sum(1 for m in ms if not str(m["status"]).startswith("draft")),
        "실행 사전(review_status=approved)": sum(1 for m in rm if str(m["review_status"]) == "approved"),
        "검수 큐 human_approved": rq.get("human_approved"),
    }
    out["승인된 항목"] = approved

    # ---- catalog-derived candidate pairs
    eng_pairs, abbr_pairs, front_pairs, tail_pairs = set(), set(), set(), set()
    for tid, t in meta.items():
        title = catalog.get(tid, {}).get("title") or t["name"]
        if t["name_eng"]:
            eng_pairs.add((key(title), key(t["name_eng"])))
        f = paren_front(title)
        if key(f) != key(title):
            front_pairs.add((key(title), key(f)))
        tv = tail_variant(title)
        if key(tv) != key(title):
            tail_pairs.add((key(title), key(tv)))
        for v in t["values"]:
            ko, en = v.get("ITM_NM") or "", v.get("ITM_NM_ENG") or ""
            if ko and en and key(clean_value(ko)) and key(en):
                eng_pairs.add((key(clean_value(ko)), key(en)))
            vf = paren_front(clean_value(ko))
            if ko and key(vf) and key(vf) != key(clean_value(ko)):
                front_pairs.add((key(clean_value(ko)), key(vf)))
            vt = tail_variant(clean_value(ko))
            if ko and key(vt) and key(vt) != key(clean_value(ko)):
                tail_pairs.add((key(clean_value(ko)), key(vt)))
        for text in [title] + [v.get("ITM_NM") or "" for v in t["values"]]:
            text = nfkc(text)
            for m in _ABBR.finditer(text):
                abbr_pairs.add((key(m.group(1)), m.group(2).upper()))
            for m in _ABBR_REV.finditer(text):
                abbr_pairs.add((key(m.group(2)), m.group(1).upper()))
    eng_same = sum(1 for a, b in eng_pairs if a == b)
    out["카탈로그 파생 후보"] = {
        "영문 이름 쌍(고유, 한글키-영문키)": len(eng_pairs),
        "영문 쌍 고유 한글": len({a for a, _ in eng_pairs}),
        "한글 하나에 영문 여러 개": sum(1 for _a, n in collections.Counter(a for a, _ in eng_pairs).items() if n > 1),
        "영문=한글(같은 글자)": eng_same,
        "약어 쌍(고유)": len(abbr_pairs),
        "괄호 앞 변형 쌍(고유)": len(front_pairs),
        "말잔/평잔·원계열/계절조정·명목/실질 꼬리 떼기 변형 쌍(고유)": len(tail_pairs),
        "꼬리 떼기 예": sorted({f"{a}→{b}" for a, b in tail_pairs})[:12],
    }

    DIR.mkdir(parents=True, exist_ok=True)
    write_json(DIR / "synonym_sources.json", out)
    write_csv(DIR / "abbrev_pairs_keys.csv", [{"한글키": a, "약어": b} for a, b in sorted(abbr_pairs)])
    write_csv(DIR / "tail_variant_pairs.csv", [{"원래 키": a, "꼬리 뗀 키": b} for a, b in sorted(tail_pairs)])
    write_csv(DIR / "semantic_mapping_shape.csv",
              [{"mapping_id": m["mapping_id"], "input": m["input_expression"], "canonical": m["canonical_expression"],
                "relation": m["relation"], "action": m["action"], "evidence_level": m["evidence_level"],
                "scope_tables": len((m.get("scope") or {}).get("table_ids") or []), "status": m["status"]} for m in ms])

    p, c, s, r, d = out["우선순위"], out["후보"], out["의미매핑 초안"], out["실행 사전"], out["카탈로그 파생 후보"]
    lines = ["# 3. 동의어 원천 (대략치, 승인 전)", "",
             "- 자료: `reference/baseline-1006/data/mappings/review/`, `.../runtime/`, 카탈로그 메타. 질문·정답은 읽지 않았다. 호출 0회.", "",
             "## 원천별 개수", "", "| 원천 | 개수 | 모양 |", "|---|---|---|",
             f"| 통계어사전_우선순위.csv | {p['행']:,}행(고유 공식어 {p['고유 공식어']:,}) | 유형 " + ", ".join(f"{k} {v:,}" for k, v in p["유형"].items())
             + f"; 우선순위 " + ", ".join(f"{k} {v:,}" for k, v in p["우선순위"].items()) + " |",
             f"| 통계어사전_후보.csv | {c['행']}행(공식어 {c['고유 공식어']}개 × 일상어 후보) | 판정 " + ", ".join(f"{k} {v}" for k, v in c["판정"].items())
             + f"; 자기매칭 Y {c['자기매칭'].get('Y', 0)} / N {c['자기매칭'].get('N', 0)}; 직접유사도 {c['직접유사도 최소·중앙·최대']} |",
             f"| 검색용_의미매핑_초안.json | {s['건']}건 | relation " + ", ".join(f"{k} {v}" for k, v in s["relation"].items())
             + "; action " + ", ".join(f"{k} {v}" for k, v in s["action"].items()) + f"; 모두 {', '.join(s['status'])} |",
             f"| runtime/search_mappings.json | 개념 {r['개념']}·매핑 {r['매핑']} | review_status " + ", ".join(f"{k} {v}" for k, v in r["review_status"].items()) + " |",
             "", "## 통계어사전_우선순위.csv", "",
             f"- `유사어_일상어`가 채워진 행: **{p['유사어_일상어 채워짐']}**. `검토자`·`상태`도 모두 비어 있다({p['검토자 채워짐']}, {p['상태 채워짐']}). 즉 동의어 쌍은 없고 공식어 목록이다.",
             f"- 공식어와 핵심어가 다른 행 {p['공식어≠핵심어']:,}(괄호·코드 꼬리를 뗀 핵심어). 분류사유: " + ", ".join(f"{k} {v:,}" for k, v in p["분류사유"].items()),
             "", "## 통계어사전_후보.csv와 의미매핑 초안", "",
             f"- 후보: 공식어 하나에 일상어 후보 여러 개(공식어당 후보 수 분포 {c['공식어당 후보 수']}). 판정×자기매칭 {c['판정×자기매칭']}. '자동채택'은 생성 규칙의 표시이고 사람 승인이 아니다.",
             f"- 자동채택 예: " + ", ".join(c["자동채택 예"]),
             f"- 의미매핑 초안: relation×action {s['relation×action']}; evidence {s['evidence_level']}; normalization_only {s['normalization_only']}건; scope 표 수 분포 {s['scope 표 수 분포']}; auto_select_table=true {s['auto_select_table=true']}건.",
             "", "## 카탈로그에서 나오는 후보 (질문 없이)", "",
             f"- 영문 이름 쌍(고유 한글키-영문키): {d['영문 이름 쌍(고유, 한글키-영문키)']:,}(한글 {d['영문 쌍 고유 한글']:,}, 한글 하나에 영문 여러 개 {d['한글 하나에 영문 여러 개']:,}).",
             f"- 약어 쌍(고유): {d['약어 쌍(고유)']}. 잡음 판단은 1번 `abbrev_pairs.csv`.",
             f"- 괄호 앞 변형 쌍: {d['괄호 앞 변형 쌍(고유)']:,}. 말잔/평잔·원계열/계절조정·명목/실질 꼬리 떼기 변형 쌍: {d['말잔/평잔·원계열/계절조정·명목/실질 꼬리 떼기 변형 쌍(고유)']:,}.",
             "- 꼬리 떼기 예: " + ", ".join(d["꼬리 떼기 예"][:8]),
             "", "## 승인된 항목", ""]
    for k, v in approved.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "결론(사실): 사람이 승인한 동의어 항목은 0건이다. 쓸 수 있는 원천은 공식어 목록(5,887행), 일상어 후보(136행, 검토 전. `직접유사도` 열로 보아 임베딩 유사도로 만든 것으로 추정), 의미매핑 초안(76건, draft)과 카탈로그 자체(영문·약어·이름 변형)다.",
              "", "## 파일", "", "- `synonym_sources.json`: 전체 수치", "- `semantic_mapping_shape.csv`: 의미매핑 초안 76건 모양",
              "- `abbrev_pairs_keys.csv`, `tail_variant_pairs.csv`: 카탈로그 파생 후보"]
    (DIR / "summary.md").write_text("\n".join(lines) + "\n", "utf8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
