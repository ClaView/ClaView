"""EDA v3 item 1: catalog term inventory.

Term kinds: table names (catalog title, meta name, part before the parenthesis, English), value names (Korean,
English), axis names (Korean, English), unit names (Korean, English; table-level UNIT and value-level UNIT_NM).
Outputs counts, names repeated over several tables/axes, names that collide after normalization (NFKC, spaces removed,
parentheses removed), and "한글(약어)" pairs with noise flags.

Usage (repo root): PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/a/eda_v3/e1_terms.py
"""

from __future__ import annotations

import collections
import re

from common import OUT, load_catalog, load_meta, nfkc, nospace, paren_front, strip_parens, write_csv, write_json

DIR = OUT / "1_terms"

_ABBR = re.compile(r"([가-힣][가-힣0-9·]*(?:\s[가-힣][가-힣0-9·]*){0,3})\s*\(([A-Za-z][A-Za-z0-9&.\-]{1,11})\)")
QUALIFIER = r"말잔|평잔|원계열|계절조정|명목|실질|기준|제외|포함"
_ABBR_REV = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z0-9]{1,9})\s*\(([가-힣][가-힣0-9·\s]{1,30})\)")


def stage_keys(name: str) -> tuple[str, str, str]:
    s1 = nfkc(name).strip()
    s2 = nospace(s1)
    s3 = nospace(strip_parens(s1))
    return s1, s2, s3


def collect(meta: dict, catalog: dict) -> dict[str, list[tuple[str, str, str]]]:
    """kind -> list of (name, table id, axis key) occurrences."""
    occ = collections.defaultdict(list)
    for tid, t in meta.items():
        doc = catalog.get(tid, {})
        title = doc.get("title") or t["name"]
        occ["표 이름(카탈로그)"].append((title, tid, ""))
        occ["표 이름(메타)"].append((t["name"], tid, ""))
        occ["표 이름 괄호 앞"].append((paren_front(title), tid, ""))
        if t["name_eng"]:
            occ["표 이름(영문)"].append((t["name_eng"], tid, ""))
        seen_axes = set()
        for v in t["values"]:
            ax = (tid, v["OBJ_ID"])
            if v.get("ITM_NM"):
                occ["분류값 이름(한글)"].append((v["ITM_NM"], tid, v["OBJ_ID"]))
            if v.get("ITM_NM_ENG"):
                occ["분류값 이름(영문)"].append((v["ITM_NM_ENG"], tid, v["OBJ_ID"]))
            if v.get("UNIT_NM"):
                occ["단위 이름(한글)"].append((v["UNIT_NM"], tid, v["OBJ_ID"]))
            if v.get("UNIT_ENG_NM"):
                occ["단위 이름(영문)"].append((v["UNIT_ENG_NM"], tid, v["OBJ_ID"]))
            if ax not in seen_axes:
                seen_axes.add(ax)
                if v.get("OBJ_NM"):
                    occ["축 이름(한글)"].append((v["OBJ_NM"], tid, v["OBJ_ID"]))
                if v.get("OBJ_NM_ENG"):
                    occ["축 이름(영문)"].append((v["OBJ_NM_ENG"], tid, v["OBJ_ID"]))
        for u in t["units"]:
            if u.get("UNIT_NM"):
                occ["단위 이름(한글)"].append((u["UNIT_NM"], tid, "UNIT"))
            if u.get("UNIT_NM_ENG"):
                occ["단위 이름(영문)"].append((u["UNIT_NM_ENG"], tid, "UNIT"))
    return occ


def main() -> None:
    meta, catalog = load_meta(), load_catalog()
    occ = collect(meta, catalog)

    counts, repeated_rows, collision_rows = {}, [], []
    collision_stats = {}
    for kind, rows in occ.items():
        raw = collections.defaultdict(lambda: {"tables": set(), "axes": set(), "n": 0})
        for name, tid, ax in rows:
            r = raw[name]
            r["n"] += 1
            r["tables"].add(tid)
            r["axes"].add((tid, ax))
        st = [set(), set(), set()]
        for name in raw:
            for i, k in enumerate(stage_keys(name)):
                st[i].add(k)
        counts[kind] = {"출현": len(rows), "고유(원문)": len(raw), "고유(NFKC)": len(st[0]),
                        "고유(NFKC+공백 제거)": len(st[1]), "고유(+괄호 떼기)": len(st[2])}
        # names on several tables / axes
        multi = sorted(((n, len(r["tables"]), len(r["axes"]), r["n"]) for n, r in raw.items() if len(r["tables"]) > 1),
                       key=lambda x: (-x[1], -x[2], x[0]))
        counts[kind]["두 표 이상에 나오는 이름"] = len(multi)
        for n, nt, na, no in multi[:60]:
            ex = sorted(raw[n]["tables"])[:5]
            repeated_rows.append({"종류": kind, "이름": n, "표 수": nt, "축 수": na, "출현": no, "표 예": " / ".join(ex)})
        # normalization collisions: distinct NFKC names that share a key at a later stage
        first_stage = {}
        groups = collections.defaultdict(set)
        for name in raw:
            s1, s2, s3 = stage_keys(name)
            groups[("공백 제거", s2)].add(s1)
            groups[("괄호 떼기", s3)].add(s1)
        stat = {}
        for (stage, k), names in groups.items():
            if len(names) < 2 or not k:
                continue
            if stage == "괄호 떼기":
                # report only groups that are new at this stage (not already one space-free key)
                if len({nospace(n) for n in names}) < 2:
                    continue
            stat.setdefault(stage, [0, 0])
            stat[stage][0] += 1
            stat[stage][1] += len(names)
            first_stage[(stage, k)] = names
        collision_stats[kind] = {s: {"묶음": v[0], "이름": v[1]} for s, v in stat.items()}
        tables_by_s1 = collections.defaultdict(set)
        for orig, r in raw.items():
            tables_by_s1[nfkc(orig).strip()] |= r["tables"]
        for (stage, k), names in sorted(first_stage.items(), key=lambda x: (x[0][0], -len(x[1]), x[0][1])):
            tables = set().union(*(tables_by_s1[n] for n in names))
            collision_rows.append({"종류": kind, "단계": stage, "정규화 열쇠": k, "변형 수": len(names),
                                   "변형": " | ".join(sorted(names)[:8]), "표 수": len(tables)})

    # abbreviation pairs "한글(약어)" and "약어(한글)"
    abbr = collections.defaultdict(lambda: {"n": 0, "tables": set(), "kinds": set(), "examples": set(), "eng_hit": 0})
    eng_of = {}
    for tid, t in meta.items():
        for v in t["values"]:
            eng_of[(tid, v["ITM_ID"])] = v.get("ITM_NM_ENG") or ""
    sources = [("분류값", v.get("ITM_NM") or "", tid, eng_of[(tid, v["ITM_ID"])]) for tid, t in meta.items() for v in t["values"]]
    sources += [("표 이름", catalog.get(tid, {}).get("title") or t["name"], tid, t["name_eng"]) for tid, t in meta.items()]
    for src, text, tid, eng in sources:
        text = nfkc(text)
        for pat, rev in ((_ABBR, False), (_ABBR_REV, True)):
            for m in pat.finditer(text):
                ko, ab = (m.group(2), m.group(1)) if rev else (m.group(1), m.group(2))
                ko = ko.strip()
                k = (ko, ab, "약어(한글)" if rev else "한글(약어)")
                a = abbr[k]
                a["n"] += 1
                a["tables"].add(tid)
                a["kinds"].add(src)
                a["examples"].add(text)
                if ab.lower().replace(".", "") in eng.lower().replace(".", ""):
                    a["eng_hit"] += 1
    by_abbr = collections.defaultdict(set)
    by_ko = collections.defaultdict(set)
    for ko, ab, _form in abbr:
        by_abbr[ab.upper()].add(nospace(ko))
        by_ko[nospace(ko)].add(ab.upper())
    standalone = {nfkc(v.get("ITM_NM") or "").strip().lower() for t in meta.values() for v in t["values"]}
    abbr_rows = []
    for (ko, ab, form), a in sorted(abbr.items(), key=lambda x: (-len(x[1]["tables"]), x[0])):
        flags = []
        if " " in ko:
            flags.append("한글 앞 경계 불확실(띄어쓴 여러 낱말)")
        if len(nospace(ko)) > 12:
            flags.append("한글 12자 초과")
        if a["eng_hit"] == 0:
            flags.append("영문명에 약어 없음")
        if len(by_abbr[ab.upper()]) > 1:
            flags.append(f"같은 약어에 한글 {len(by_abbr[ab.upper()])}종")
        if len(by_ko[nospace(ko)]) > 1:
            flags.append(f"같은 한글에 약어 {len(by_ko[nospace(ko)])}종")
        if re.fullmatch(r"[A-Z][a-z]{3,}", ab) or ab.lower() in {"fob", "cif", "sdrs", "sdr"}:
            flags.append("약어가 아닌 영문 낱말·거래조건일 수 있음")
        if ab.lower() in standalone:
            flags.append("약어가 단독 분류값으로도 쓰임(괄호가 한정어일 수 있음)")
        if form == "약어(한글)" and re.search(QUALIFIER, ko):
            flags.append("괄호 안이 풀이가 아니라 기준어")
        abbr_rows.append({"형태": form, "한글": ko, "약어": ab, "출현": a["n"], "표 수": len(a["tables"]),
                          "출처": "/".join(sorted(a["kinds"])), "잡음 표시": "; ".join(flags),
                          "예": " | ".join(sorted(a["examples"])[:3])})

    DIR.mkdir(parents=True, exist_ok=True)
    write_json(DIR / "term_counts.json", {"counts": counts, "normalization_collisions": collision_stats})
    write_csv(DIR / "repeated_names.csv", repeated_rows)
    write_csv(DIR / "norm_collisions.csv", collision_rows)
    write_csv(DIR / "abbrev_pairs.csv", abbr_rows)

    clean_abbr = [r for r in abbr_rows if not r["잡음 표시"]]
    lines = ["# 1. 용어 목록 (대략치, 승인 전)", "",
             "- 자료: `reference/baseline-1006/data/raw/boK_meta_raw.jsonl`(349표), `.../catalog/search_catalog.json`(349 documents). 호출 0회.",
             "- 표 이름은 카탈로그 title(쉼표 포함)과 메타 name(쉼표 없음)을 따로 셌다. 분류값은 ITEM 행을 뺀 값 행이다.",
             "- 정규화 단계: NFKC → 공백 제거 → 괄호 떼기(괄호 묶음 전부 제거). 단계별 고유 이름 수가 줄어든 만큼이 겹침이다.", "",
             "## 종류별 개수", "",
             "| 종류 | 출현 | 고유(원문) | 고유(NFKC) | NFKC+공백 제거 | +괄호 떼기 | 두 표 이상 |",
             "|---|---|---|---|---|---|---|"]
    for kind, c in counts.items():
        lines.append(f"| {kind} | {c['출현']:,} | {c['고유(원문)']:,} | {c['고유(NFKC)']:,} | {c['고유(NFKC+공백 제거)']:,} | "
                     f"{c['고유(+괄호 떼기)']:,} | {c['두 표 이상에 나오는 이름']:,} |")
    lines += ["", "## 정규화하면 겹치는 이름 (묶음 수 / 든 이름 수)", "",
              "| 종류 | 공백 제거로 겹침 | 괄호 떼기로 새로 겹침 |", "|---|---|---|"]
    for kind, s in collision_stats.items():
        a = s.get("공백 제거", {"묶음": 0, "이름": 0})
        b = s.get("괄호 떼기", {"묶음": 0, "이름": 0})
        lines.append(f"| {kind} | {a['묶음']} / {a['이름']} | {b['묶음']} / {b['이름']} |")
    lines += ["", "## 여러 표에 나오는 이름 (상위 10, 분류값 한글)", "", "| 이름 | 표 수 | 축 수 |", "|---|---|---|"]
    for r in [r for r in repeated_rows if r["종류"] == "분류값 이름(한글)"][:10]:
        lines.append(f"| {r['이름']} | {r['표 수']} | {r['축 수']} |")
    lines += ["", "## 약어 쌍", "",
              f"- 고유 쌍 {len(abbr_rows)}개(한글(약어) {sum(1 for r in abbr_rows if r['형태'] == '한글(약어)')}, "
              f"약어(한글) {sum(1 for r in abbr_rows if r['형태'] == '약어(한글)')}). 잡음 표시 없는 쌍 {len(clean_abbr)}개.",
              "- 잡음 표시: 한글 앞 경계 불확실, 한글 12자 초과, 영문명에 약어 없음, 같은 약어에 한글 여러 종, 같은 한글에 약어 여러 종, 약어가 아닌 영문 낱말.",
              "- 예(잡음 표시 없음, 표 수 순): " + ", ".join(f"{r['한글']}({r['약어']})" for r in clean_abbr[:15]), "",
              "## 파일", "", "- `term_counts.json`: 종류별 개수, 정규화 겹침 통계",
              "- `repeated_names.csv`: 종류별로 두 표 이상에 나오는 이름 상위 60", "- `norm_collisions.csv`: 정규화 단계별 겹치는 이름 묶음",
              "- `abbrev_pairs.csv`: 약어 쌍과 잡음 표시"]
    (DIR / "summary.md").write_text("\n".join(lines) + "\n", "utf8")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
