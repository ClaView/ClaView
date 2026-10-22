"""EDA v3 item 4: question side (golden + development only).

For each question, finds spans that exactly match (after normalization) catalog terms — table names, the part before
the parenthesis, value names (Korean, English), abbreviations — taking the longest match first. Reports how much of
the question the terms cover, how often the answer tables' names and values appear verbatim, and, for answer groups
whose indicator is not found verbatim, the question-side expressions left over (synonym candidates for review).

Official (new200) rows and the blind set are never opened or counted.

Usage (repo root): PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/a/eda_v3/e4_questions.py
"""

from __future__ import annotations

import collections
import re
import statistics

from common import (OUT, bkey, clean_value, key, load_catalog, load_groups, load_meta, load_questions, nfkc, paren_front,
                    write_csv, write_json)
from roles import ROLE_KO, AxisRoles, basis_only

DIR = OUT / "4_questions"
MIN_LEN = 2
MAX_LEN = 40
_ABBR = re.compile(r"([가-힣][가-힣0-9·]{1,20})\s*\(([A-Za-z][A-Za-z0-9&.\-]{1,11})\)")
_ABBR_REV = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z0-9]{1,9})\s*\(([가-힣][가-힣0-9·]{1,20})\)")
_DROP = re.compile(r"[\s·ㆍ・,()\[\]{}<>/\-_.:;'\"~?!]")
# question-side words that are requests, periods or frequencies (not indicator expressions); fixed language list
_STOP_TOKEN = re.compile(
    r"^(?:보여\S*|알려\S*|비교해\S*|찾아\S*|궁금\S*|그려\S*|만들어\S*|정리해\S*|나타내\S*|확인해\S*|볼\S*|싶\S*|주세요|줘"
    r"|최근|지난|올해|작년|지난해|재작년|매월|매달|매년|월별|연도별|연간|분기별|분기|월|년|연|반기별|반기|각각|함께|같이|그리고|및"
    r"|추이|흐름|변화|비교|기준|기준으로|어떻게|얼마나|어떤|몇|중|가운데|간|동안|이후|부터|까지|별로|대비|전체|모두|각"
    r"|(?:월|연도|년도|분기|반기|년|연)별로?|그래프|차트|표로|원해|원함"
    r"|보고|알고|보면|싶어|아니라|있는|있|받은|받|이미|남아|남은|해서|하고|따로|나눠|나눠서|나란히|함께|정도|얼마|무엇|뭐"
    r"|\d+\S*)$")
_NUMERIC = re.compile(r"^[\d.%~]+(?:년|월|분기)?$")  # years and numbers are periods, not catalog terms
_PARTICLE = re.compile(r"(?:으로|에서|에게|처럼|부터|까지|이랑|하고|의|을|를|은|는|이|가|로|에|도|와|과|만|별로|별)$")


def norm_with_map(text: str) -> tuple[str, list[int]]:
    out, pos = [], []
    s = nfkc(text).lower()
    for i, ch in enumerate(s):
        if _DROP.match(ch):
            continue
        out.append(ch)
        pos.append(i)
    return "".join(out), pos


def build_index(meta: dict, catalog: dict) -> dict[str, set[tuple[str, str]]]:
    idx = collections.defaultdict(set)

    def add(k: str, kind: str, tid: str):
        if MIN_LEN <= len(k) <= MAX_LEN and not _NUMERIC.match(k):
            idx[k].add((kind, tid))

    for tid, t in meta.items():
        title = catalog.get(tid, {}).get("title") or t["name"]
        add(key(title), "표 이름", tid)
        add(key(t["name"]), "표 이름", tid)
        add(key(paren_front(title)), "괄호 앞 이름", tid)
        add(key(t["name_eng"]), "표 영문", tid)
        texts = [title]
        for v in t["values"]:
            nm = v.get("ITM_NM") or ""
            add(key(clean_value(nm)), "분류값", tid)
            add(bkey(nm), "분류값", tid)
            add(key(v.get("ITM_NM_ENG")), "분류값 영문", tid)
            texts.append(nm)
        for text in texts:
            text = nfkc(text)
            for m in _ABBR.finditer(text):
                add(key(m.group(2)), "약어", tid)
            for m in _ABBR_REV.finditer(text):
                add(key(m.group(1)), "약어", tid)
    return idx


def longest_matches(q: str, idx: dict) -> list[tuple[int, int, str]]:
    found = []
    for i in range(len(q)):
        for j in range(min(len(q), i + MAX_LEN), i + MIN_LEN - 1, -1):
            if q[i:j] in idx:
                found.append((i, j, q[i:j]))
                break  # longest from this start
    found.sort(key=lambda x: (x[0] - x[1], x[0]))
    taken = []
    for i, j, k in found:
        if all(j <= a or i >= b for a, b, _ in taken):
            taken.append((i, j, k))
    return sorted(taken)


def main() -> None:
    meta, catalog = load_meta(), load_catalog()
    R = AxisRoles(meta)
    questions = load_questions()
    groups = load_groups()
    qmap = {q["case_id"]: q for q in questions}
    idx = build_index(meta, catalog)
    title = {tid: (catalog.get(tid, {}).get("title") or t["name"]) for tid, t in meta.items()}

    # per-table literal keys: value key -> roles (4-layer, name-only)
    tvals = {}
    for ax in R.axes:
        d = tvals.setdefault(ax["tid"], collections.defaultdict(list))
        for v in ax["values"]:
            r4 = R.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])]
            r1 = R.name_only_role(ax["name"])
            for k in {key(clean_value(v.get("ITM_NM"))), bkey(v.get("ITM_NM"))}:
                if len(k) >= MIN_LEN and not _NUMERIC.match(k):
                    d[k].append((r4, r1, clean_value(v.get("ITM_NM"))))

    # ---- coverage per question
    cov_rows, kind_counter, term_counter = [], collections.Counter(), collections.Counter()
    matches_by_case = {}
    for q in questions:
        nq, _pos = norm_with_map(q["question"])
        ms = longest_matches(nq, idx)
        matches_by_case[q["case_id"]] = (nq, ms)
        covered = sum(j - i for i, j, _ in ms)
        kinds = set()
        for _i, _j, k in ms:
            kinds |= {kind for kind, _ in idx[k]}
            term_counter[k] += 1
        kind_counter.update(kinds)
        cov_rows.append({"case_id": q["case_id"], "dataset": q["dataset"], "question": q["question"], "정규화 글자 수": len(nq),
                         "덮인 글자 수": covered, "덮인 비율": round(covered / len(nq), 3) if nq else 0,
                         "일치 용어": " | ".join(k for _i, _j, k in ms), "일치 종류": " / ".join(sorted(kinds))})
    ratios = [r["덮인 비율"] for r in cov_rows]

    # ---- answer groups
    n_tables_by_case = collections.Counter(g["case_id"] for g in groups)
    grp_rows, nf_rows, lit_rows = [], [], []
    lit_role4, lit_role1 = collections.Counter(), collections.Counter()
    residual_counter = collections.Counter()
    basis_left = collections.Counter()
    for g in groups:
        cid = g["case_id"]
        q = qmap.get(cid)
        if not q:
            continue
        nq, ms = matches_by_case[cid]
        multi = n_tables_by_case[cid] > 1
        tids = g.get("allowed_tables") or []
        t_lit = any(key(title[t]) in nq or key(meta[t]["name"]) in nq for t in tids if t in meta)
        f_lit = any(key(paren_front(title[t])) in nq for t in tids if t in meta)
        vals = []
        for t in tids:
            if t not in tvals:
                continue
            hits = [k for k in tvals[t] if k in nq]
            hits = [k for k in hits if not any(k != k2 and k in k2 for k2 in hits)]
            for k in hits:
                r4 = collections.Counter(r for r, _, _ in tvals[t][k]).most_common(1)[0][0]
                r1 = collections.Counter(r for _, r, _ in tvals[t][k]).most_common(1)[0][0]
                vals.append((t, k, r4, r1, tvals[t][k][0][2]))
        found4 = f_lit or t_lit or any(r4 in ("measure", "header", "combined") for _t, _k, r4, _r1, _d in vals)
        found1 = f_lit or t_lit or any(r1 == "measure" for _t, _k, _r4, r1, _d in vals)
        grp_rows.append({"case_id": cid, "dataset": q["dataset"], "group_index": g.get("group_index"), "표 둘 이상": "예" if multi else "",
                         "정답 표": " / ".join(tids), "표 이름 그대로": int(t_lit), "괄호 앞 이름 그대로": int(f_lit),
                         "값 그대로 수": len(vals), "지표 찾음(네 층)": int(found4), "지표 찾음(축 이름만)": int(found1),
                         "그대로 나온 값(역할)": " | ".join(f"{d}({ROLE_KO.get(r4, r4)}/{ROLE_KO.get(r1, r1)})" for _t, _k, r4, r1, d in vals)})
        for t, k, r4, r1, d in vals:
            lit_rows.append({"case_id": cid, "dataset": q["dataset"], "표 ID": t, "값": d, "열쇠": k,
                             "네 층": ROLE_KO.get(r4, r4), "축 이름만": ROLE_KO.get(r1, r1), "표 둘 이상": "예" if multi else ""})
        if not found4:
            covered = set()
            for i, j, _k in ms:
                covered.update(range(i, j))
            res = []
            for tok in nfkc(q["question"]).split():
                tok = tok.strip(".,?!·'\"")
                stem = _PARTICLE.sub("", tok)
                if len(stem) < 2:
                    stem = tok  # "새로" keeps its 로
                stem_n, _ = norm_with_map(stem)
                if not stem_n or _STOP_TOKEN.match(stem) or _STOP_TOKEN.match(tok):
                    continue
                if basis_only(key(re.sub(r"(?:한|된|인)$", "", stem)), R.basis_vocab):
                    basis_left[stem] += 1  # basis words are conditions (item 5), not indicator expressions
                    continue
                start = nq.find(stem_n)
                if start >= 0 and all(p in covered for p in range(start, start + len(stem_n))):
                    continue
                res.append(stem)
            residual_counter.update(res)
            nf_rows.append({"case_id": cid, "dataset": q["dataset"], "group_index": g.get("group_index"),
                            "표 둘 이상": "예" if multi else "", "question": q["question"], "정답 표": " / ".join(tids),
                            "정답 표 괄호 앞 이름": " / ".join(paren_front(title[t]) for t in tids if t in meta),
                            "질문에 그대로 나온 정답 표 값": " | ".join(d for _t, _k, _r4, _r1, d in vals),
                            "질문 쪽 남은 표현(동의어 후보)": " / ".join(res),
                            "질문의 카탈로그 일치 용어": " | ".join(k for _i, _j, k in ms)})
    # unique literal values (case, table, key)
    uniq = {}
    for r in lit_rows:
        uniq[(r["case_id"], r["표 ID"], r["열쇠"])] = r
    for r in uniq.values():
        lit_role4[r["네 층"]] += 1
        lit_role1[r["축 이름만"]] += 1

    n_g = len(grp_rows)
    single = [r for r in grp_rows if not r["표 둘 이상"]]
    multi_g = [r for r in grp_rows if r["표 둘 이상"]]
    lost = [r for r in grp_rows if r["지표 찾음(축 이름만)"] and not r["지표 찾음(네 층)"]]
    both_nf_multi = [r for r in multi_g if not r["지표 찾음(축 이름만)"] and not r["지표 찾음(네 층)"]]

    def pct(n, d):
        return f"{n}/{d} ({n / d:.0%})" if d else "0/0"

    stats = {
        "질문 수(골든+개발)": len(questions), "골든": sum(1 for q in questions if q["dataset"] == "golden"),
        "개발": sum(1 for q in questions if q["dataset"] == "development"),
        "정답 묶음": n_g, "표 하나 질문": sum(1 for c, n in n_tables_by_case.items() if n == 1 and c in qmap),
        "표 둘 이상 질문": sum(1 for c, n in n_tables_by_case.items() if n > 1 and c in qmap), "표 둘 이상 묶음": len(multi_g),
        "덮인 비율 평균": round(statistics.mean(ratios), 3), "덮인 비율 중앙": round(statistics.median(ratios), 3),
        "덮인 비율 0.5 미만 질문": sum(1 for x in ratios if x < 0.5),
        "일치 종류별 질문 수": dict(kind_counter.most_common()),
        "묶음: 표 이름 그대로": sum(r["표 이름 그대로"] for r in grp_rows),
        "묶음: 괄호 앞 이름 그대로": sum(r["괄호 앞 이름 그대로"] for r in grp_rows),
        "묶음: 값 하나 이상 그대로": sum(1 for r in grp_rows if r["값 그대로 수"]),
        "묶음: 이름·값 중 하나라도 그대로": sum(1 for r in grp_rows if r["표 이름 그대로"] or r["괄호 앞 이름 그대로"] or r["값 그대로 수"]),
        "묶음: 지표 찾음(네 층)": sum(r["지표 찾음(네 층)"] for r in grp_rows),
        "묶음: 지표 찾음(축 이름만)": sum(r["지표 찾음(축 이름만)"] for r in grp_rows),
        "네 층에서 지표를 잃은 묶음(축 이름만은 찾음)": len(lost),
        "그중 표 둘 이상 묶음": sum(1 for r in lost if r["표 둘 이상"]),
        "잃은 묶음 목록": [f"{r['case_id']} {r['정답 표']} {r['그대로 나온 값(역할)']}" for r in lost],
        "표 둘 이상 묶음 중 두 방식 모두 지표 못 찾음": len(both_nf_multi),
        "지표 못 찾은 묶음(네 층)": len(nf_rows),
        "그대로 나온 정답 표 값(고유 사례·표·열쇠)": len(uniq),
        "그 값의 역할(네 층)": dict(lit_role4.most_common()), "그 값의 역할(축 이름만)": dict(lit_role1.most_common()),
        "자주 일치한 용어 상위 30": term_counter.most_common(30),
        "남은 표현 상위 40": residual_counter.most_common(40),
        "남은 기준어(조건, 동의어 후보에서 뺌)": basis_left.most_common(),
    }

    DIR.mkdir(parents=True, exist_ok=True)
    write_csv(DIR / "question_coverage.csv", cov_rows)
    write_csv(DIR / "answer_groups_literal.csv", grp_rows)
    write_csv(DIR / "literal_values.csv", list(uniq.values()))
    write_csv(DIR / "indicator_not_found.csv", nf_rows)
    write_csv(DIR / "residual_expressions.csv", [{"표현": k, "빈도": n} for k, n in residual_counter.most_common()])
    write_json(DIR / "numbers.json", stats)

    s = stats
    lines = ["# 4. 질문 쪽 (골든+개발만, 대략치, 승인 전)", "",
             f"- 자료: 골든 {s['골든']} + 개발 {s['개발']} = {s['질문 수(골든+개발)']}문항과 정답 묶음 {s['정답 묶음']}개(`Q_candidate_group.jsonl`의 golden·development 행). 새200·블라인드는 열지 않았다.",
             "- 정규화: NFKC·소문자·공백/구두점 제거. 용어: 표 이름(카탈로그·메타), 괄호 앞 이름, 분류값(각주·번호·코드 제거, 괄호 뗀 형도), 분류값·표 영문, 약어. 2~40자.",
             "- 일치: 각 위치에서 가장 긴 용어 → 전체에서 긴 일치 우선, 겹치지 않게.",
             "- 지표 찾음: 정답 표의 이름(전체·괄호 앞)이 그대로 있거나, 그대로 나온 값의 역할이 지표·합계 머리·결합(네 층) / 지표(축 이름만).",
             "- 값 역할은 2번 네 층 규칙(`roles.py`). 대상 축의 합계 행(업종 축 맨 위의 \"총고정자본형성\" 등)은 초안 4절 예처럼 합계 머리로 셌다(2번 3층 수치에는 영향 없음).",
             "- 저장 응답이나 모델 실행 결과가 아니라 글자 일치만 센 값이다. 417문항은 개선에 반복해 쓴 자료라 독립 평가가 아니다.", "",
             "## 핵심 숫자", "", "| 항목 | 값 |", "|---|---|",
             f"| 표 하나 질문 / 표 둘 이상 질문(묶음) | {s['표 하나 질문']} / {s['표 둘 이상 질문']}({s['표 둘 이상 묶음']}) |",
             f"| 질문 글자 중 용어로 덮인 비율 평균 / 중앙 | {s['덮인 비율 평균']:.0%} / {s['덮인 비율 중앙']:.0%} |",
             f"| 덮인 비율 50% 미만 질문 | {s['덮인 비율 0.5 미만 질문']} |",
             f"| 묶음: 표 이름 전체가 그대로 | {pct(s['묶음: 표 이름 그대로'], n_g)} |",
             f"| 묶음: 괄호 앞 이름이 그대로 | {pct(s['묶음: 괄호 앞 이름 그대로'], n_g)} |",
             f"| 묶음: 정답 표 값 하나 이상 그대로 | {pct(s['묶음: 값 하나 이상 그대로'], n_g)} |",
             f"| 묶음: 이름·값 중 하나라도 그대로 | {pct(s['묶음: 이름·값 중 하나라도 그대로'], n_g)} |",
             f"| 묶음: 지표 찾음 네 층 / 축 이름만 | {s['묶음: 지표 찾음(네 층)']} / {s['묶음: 지표 찾음(축 이름만)']} |",
             f"| 네 층에서 지표를 잃은 묶음(축 이름만은 찾음) | {s['네 층에서 지표를 잃은 묶음(축 이름만은 찾음)']} |",
             f"| 그중 표 둘 이상 묶음 | {s['그중 표 둘 이상 묶음']} (초안 0) |",
             f"| 표 둘 이상 묶음 중 두 방식 모두 지표 못 찾음 | {s['표 둘 이상 묶음 중 두 방식 모두 지표 못 찾음']} (초안 16) |",
             f"| 그대로 나온 정답 표 값(고유) | {s['그대로 나온 정답 표 값(고유 사례·표·열쇠)']} (초안 164) |",
             "", "## 그대로 나온 값의 역할", "", "| 방식 | " + " | ".join(sorted(set(lit_role4) | set(lit_role1))) + " |",
             "|---|" + "---|" * len(set(lit_role4) | set(lit_role1))]
    cols = sorted(set(lit_role4) | set(lit_role1))
    lines.append("| 축 이름만 | " + " | ".join(str(lit_role1.get(c, 0)) for c in cols) + " |")
    lines.append("| 네 층 | " + " | ".join(str(lit_role4.get(c, 0)) for c in cols) + " |")
    lines += ["", "초안(4절): 축 이름만 지표 136·대상 20·기준 8, 네 층 지표 101·대상 35·기준 19·합계 머리 9(합 164).",
              "", "## 일치한 용어 종류(질문 수)", "", ", ".join(f"{k} {v}" for k, v in s["일치 종류별 질문 수"].items()),
              "", "## 자주 일치한 용어 상위 30 (잡음 확인용)", "", ", ".join(f"{k}({n})" for k, n in s["자주 일치한 용어 상위 30"]),
              "", f"## 지표를 못 찾은 묶음 {len(nf_rows)}개의 질문 쪽 남은 표현 상위 40 (동의어 후보, 사람 검토 전)", "",
              ", ".join(f"{k}({n})" for k, n in s["남은 표현 상위 40"]),
              "", "- 남은 기준어(조건이라 위 목록에서 뺌): " + ", ".join(f"{k}({n})" for k, n in s["남은 기준어(조건, 동의어 후보에서 뺌)"]),
              "- '네 층에서 지표를 잃은 묶음'은 축 이름만 방식이 대상·기준 값(서울, 제조업, 신규취급액 기준 등)을 지표로 센 경우가 대부분이다. 목록은 `numbers.json`의 `잃은 묶음 목록`.",
              "- 표 둘 이상 묶음에서 잃은 것: " + ("; ".join(f"{r['case_id']} {r['정답 표']} — {r['그대로 나온 값(역할)']}" for r in lost if r["표 둘 이상"]) or "없음")
              + (". 2번에서 이 표의 축 판정이 초안과 갈렸는지 `2_axis_roles/layer2_generic_axes.csv`로 확인한다." if any(r["표 둘 이상"] for r in lost) else "."),
              "", "- 사례별 목록은 `indicator_not_found.csv`(질문, 정답 표 괄호 앞 이름, 남은 표현을 나란히). 짝짓기(예: 새로 취급한 ↔ 신규취급액)는 사람이 한다.",
              "", "## 파일", "", "- `question_coverage.csv`: 질문마다 덮인 비율과 일치 용어(골든·개발만)",
              "- `answer_groups_literal.csv`: 정답 묶음마다 이름·값이 그대로 나오는지, 지표 찾음 여부",
              "- `literal_values.csv`: 그대로 나온 정답 표 값과 두 방식의 역할", "- `indicator_not_found.csv`, `residual_expressions.csv`: 지표 못 찾은 묶음과 남은 표현",
              "- `numbers.json`: 전체 수치"]
    (DIR / "summary.md").write_text("\n".join(lines) + "\n", "utf8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
