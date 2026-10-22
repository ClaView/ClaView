"""Offline study: how to split the values of the indicator axes (계정항목별 etc.) into roles.

Inputs (no network, no model): the stored BOK/KOSIS meta snapshot (reference/baseline-1006/data/raw/boK_meta_raw.jsonl)
and the development questions only (golden + development of the 417 corpus; new200 and the blind set are not opened).

Steps
1. Axis inventory: every classification axis name, how many tables, values, units, hierarchy depth.
2. Lexicons from the catalog itself: target values = values of target-like axes (industry, firm size, region,
   institution); basis words = a fixed short list (명목, 실질, 원계열, 계절조정, 신규취급액, 잔액, 말잔, 평잔 ...).
3. Value classes on indicator axes:
   basis   - the value is only a basis word ("신규취급액 기준", "원계열")
   target  - the value is only a target word ("제조업", "대기업")
   compound- target word + measure remainder ("기업대출", "종금사-할인어음", English "X - Y")
   header  - has children and its name joins several parts ("경제규모 및 국민소득(명목)")
   measure - the rest
4. Question check: for each development question and its answer tables, which indicator-axis values of those
   tables the question names, their classes, and whether the answer needed one table or several.

Usage: python -B -X utf8 scripts/axis_role_study.py [out_dir]   (default runs/axis_roles)
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "reference/baseline-1006/data/raw/boK_meta_raw.jsonl"
CORPUS = ROOT.parent / "clabi/claview-runs/q-improve-20261013-01/jt/work/sh/data"
INDICATOR_AXES = {"계정항목별", "계정코드별", "BSI코드별", "CSI코드별", "CSI분류코드별"}
TARGET_AXES = {"업종코드별", "기업규모별", "지역코드별", "보고기관별", "기관코드별", "제도부문코드별", "제도부문별"}
BASIS_WORDS = ("명목", "실질", "원계열", "계절조정", "신규취급액", "잔액", "말잔", "평잔", "기준", "원화", "달러",
               "전년동기", "전기", "전년", "대비", "증감률", "구성비", "기여도", "지수", "금액", "비중")
ENTITY_WORDS = ("가계", "기업", "대기업", "중소기업", "중견기업", "개인사업자", "정부", "일반정부", "공기업", "비금융법인",
                "예금은행", "비은행", "상호저축은행", "신용협동조합", "새마을금고", "종금사", "보험회사", "증권사", "외국인", "내국인")


def norm(text: str) -> str:
    return re.sub(r"[\s·,()\[\]\-_/]", "", text or "")


def load_meta():
    tables = {}
    for line in META.read_text("utf8").splitlines():
        row = json.loads(line)
        meta = row.get("meta") or {}
        itm = [x for x in meta.get("ITM") or [] if isinstance(x, dict)]
        tables[row["tblId"]] = {"name": row.get("name"), "itm": itm,
                                "unit": [u.get("UNIT_NM") for u in meta.get("UNIT") or [] if isinstance(u, dict)]}
    return tables


def axes_of(table):
    axes = collections.OrderedDict()
    for x in table["itm"]:
        if x.get("OBJ_ID") == "ITEM":
            continue
        axes.setdefault((x["OBJ_ID"], x.get("OBJ_NM")), []).append(x)
    return axes


def classify(value, children, targets):
    name = value.get("ITM_NM") or ""
    n = norm(name)
    basis_left = n
    for w in BASIS_WORDS:
        basis_left = basis_left.replace(w, "")
    if len(basis_left) <= 1:
        return "basis"
    if n in targets:
        return "target"
    eng = value.get("ITM_NM_ENG") or ""
    hits = [t for t in sorted(targets | set(ENTITY_WORDS), key=len, reverse=True) if len(t) >= 2 and t in n and t != n]
    if hits or re.search(r"\S - \S", eng) or "-" in name:
        return "compound"
    if children and re.search(r"및|와|과|,|·", name):
        return "header"
    return "measure"


def main(out_dir: Path) -> dict:
    tables = load_meta()
    axis_tables, axis_values = collections.Counter(), collections.Counter()
    targets = set()
    for t in tables.values():
        for (oid, nm), vals in axes_of(t).items():
            axis_tables[nm] += 1
            axis_values[nm] += len(vals)
            if nm in TARGET_AXES:
                targets |= {norm(v.get("ITM_NM")) for v in vals if len(norm(v.get("ITM_NM"))) >= 2}
    targets -= {"전체", "합계", "총계", "계", "전산업", "전체산업"}

    classes, examples, value_class = collections.Counter(), collections.defaultdict(list), {}
    depth_by_class = collections.defaultdict(collections.Counter)
    for tid, t in tables.items():
        for (oid, nm), vals in axes_of(t).items():
            if nm not in INDICATOR_AXES:
                continue
            parent = {v["ITM_ID"]: v.get("UP_ITM_ID") for v in vals}
            kids = collections.Counter(p for p in parent.values() if p)
            for v in vals:
                depth, cur = 0, v.get("UP_ITM_ID")
                while cur and depth < 10:
                    depth, cur = depth + 1, parent.get(cur)
                c = classify(v, kids.get(v["ITM_ID"], 0), targets)
                classes[c] += 1
                depth_by_class[c][min(depth, 3)] += 1
                value_class[(tid, norm(v.get("ITM_NM")))] = c
                if len(examples[c]) < 12 and (tid, v.get("ITM_NM")) not in examples[c]:
                    examples[c].append((tid, v.get("ITM_NM"), v.get("ITM_NM_ENG")))

    # Question check on development questions only.
    questions = [q for q in json.loads((CORPUS / "questions_417.json").read_text("utf8"))
                 if q["dataset"] in ("golden", "development")]
    groups = collections.defaultdict(dict)
    for line in (CORPUS / "Q_candidate_group.jsonl").read_text("utf8").splitlines():
        row = json.loads(line)
        groups[row["case_id"]][row["group_index"]] = row["allowed_tables"]
    check = collections.Counter()
    named_class = collections.Counter()
    samples = collections.defaultdict(list)
    for q in questions:
        qn = norm(q["question"])
        g = [groups[q["case_id"]][i] for i in sorted(groups[q["case_id"]])]
        first = [grp[0] for grp in g]  # one representative table per answer group
        named = {}
        for tid in first:
            for (t2, name), c in value_class.items():
                if t2 == tid and len(name) >= 2 and name in qn:
                    named.setdefault(tid, set()).add((name, c))
        # keep the longest names only (drop a name contained in another named one of the same table)
        for tid, names in named.items():
            longest = {(n, c) for n, c in names if not any(n != m and n in m for m, _ in names)}
            named[tid] = longest
            for _, c in longest:
                named_class[c] += 1
        multi_same = [tid for tid, names in named.items() if len(names) >= 2]
        key = ("one_table" if len(g) == 1 else "several_tables")
        check[key] += 1
        if multi_same:
            check[key + "|names>=2_in_one_table"] += 1
            if len(samples[key]) < 6:
                tid = multi_same[0]
                samples[key].append((q["case_id"], q["question"], tables[tid]["name"], sorted(named[tid])))
        if any(c == "compound" for names in named.values() for _, c in names) and len(samples["compound"]) < 6:
            samples["compound"].append((q["case_id"], q["question"],
                                        sorted((n, c) for names in named.values() for n, c in names)))

    report = {
        "tables": len(tables),
        "axis_names": {nm: {"tables": axis_tables[nm], "values": axis_values[nm]} for nm, _ in axis_tables.most_common()},
        "target_lexicon_size": len(targets),
        "indicator_value_classes": dict(classes),
        "depth_by_class": {c: dict(d) for c, d in depth_by_class.items()},
        "examples": examples,
        "questions_checked": len(questions),
        "question_check": dict(check),
        "named_value_classes": dict(named_class),
        "samples": samples,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf8")
    return report


if __name__ == "__main__":
    r = main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs/axis_roles")
    print(json.dumps({k: r[k] for k in ("tables", "target_lexicon_size", "indicator_value_classes", "depth_by_class",
                                        "questions_checked", "question_check", "named_value_classes")},
                     ensure_ascii=False, indent=1))
    for c, rows in r["examples"].items():
        print(c, rows[:8])
    for k, rows in r["samples"].items():
        print("SAMPLE", k)
        for row in rows:
            print("  ", row)
