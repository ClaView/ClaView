"""EDA v3 item 5: condition vocabulary in golden + development questions.

Runs the old A rules (src/claview/aflow/rules.py: find_periods, find_frequencies, find_basis, find_handoff, find_units,
find_excludes) on the Q1-normalized question (NFKC, one space) with as_of=None, and counts what they catch. Then counts
condition-like words that remain outside the caught spans (list from 새구조_해석선택_상세_20261016.md 2.2, plus a few
added words marked "추가"). Only this script imports the old rules. Official (new200) and blind rows are never opened.

Usage (repo root): PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/eda_v3/e5_conditions.py
"""

from __future__ import annotations

import collections
import re
import sys
import unicodedata

from common import OUT, ROOT, load_questions, write_csv, write_json

sys.path.insert(0, str(ROOT / "src"))
from claview.aflow import rules  # noqa: E402  (old rules, read only)

DIR = OUT / "5_conditions"

# (label, regex, source). Source "2.2" = listed in the handoff 2.2; "추가" = added here.
WORDS = [
    ("최근(단독)", r"최근", "과제 예시"), ("추이", r"추이", "2.2"), ("비교", r"비교", "2.2"), ("구성", r"구성(?!비)", "2.2"),
    ("대비", r"대비", "2.2"), ("전년 대비", r"전년\s*(?:동기\s*)?대비", "2.2(계산 예)"), ("차이", r"차이", "2.2"),
    ("의 몇 %", r"몇\s*(?:%|퍼센트|프로)", "2.2"), ("로 나눈", r"(?:으로|로)\s*나눈", "2.2"),
    ("로 나눠서", r"(?:으로|로)\s*나눠", "2.2"), ("를 뺀/빼고", r"(?:을|를)?\s*(?:뺀|빼고)", "2.2"), ("제외", r"제외", "2.2"),
    ("중(단독)", r"(?<![가-힣])중(?:에서|에)?(?![가-힣])", "2.2"), ("가운데", r"가운데", "2.2"),
    ("~별", r"[가-힣A-Za-z0-9]별", "2.2"), ("흐름", r"흐름", "2.2(뜻 둘)"), ("전망", r"전망", "2.2(뜻 둘)"),
    ("기준(단독)", r"기준", "2.2(뜻 둘)"), ("명목", r"명목", "2.2"), ("실질", r"실질", "2.2"), ("원계열", r"원계열", "2.2"),
    ("계절조정", r"계절\s*조정", "2.2"), ("신규", r"신규", "2.2"), ("잔액", r"잔액", "2.2"), ("말잔", r"말잔", "2.2"),
    ("평잔", r"평잔", "2.2"), ("단위", r"단위", "2.2"),
    ("증가율·증감률·성장률·상승률·등락률", r"(?:증가|증감|성장|상승|등락|감소|변동)률", "추가"),
    ("변화·변동", r"변화|변동(?!률)", "추가"), ("평균", r"평균", "추가"), ("누적", r"누적", "추가"), ("합계·합친", r"합계|합친|합쳐", "추가"),
    ("비중·비율", r"비중|비율", "추가"), ("연말·월말·분기말", r"(?:연|년|월|분기|반기)\s*말(?!잔)", "추가"),
    ("동안·간", r"동안|(?<=\d)\s*년간", "추가"), ("이후·이전", r"이후|이전|이래", "추가"), ("각각", r"각각", "추가"),
    ("나란히·함께·같이", r"나란히|함께|같이", "추가"), ("가장·최대·최고·최저", r"가장|최대|최고|최저|최소", "추가"),
    ("현재·지금·요즘", r"현재|지금|요즘", "추가"), ("전월·전기·전년·동기", r"전월|전기(?!업)|전년|동기", "추가"),
    ("기여도", r"기여도", "추가"), ("얼마나", r"얼마나|얼마", "추가"),
]


def q1(text: str) -> str:
    out, prev_space = [], True
    for ch in text:
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
    return "".join(out).rstrip()


def main() -> None:
    questions = load_questions()
    dist = {k: collections.Counter() for k in ("period_kind", "period_status", "period_unit", "freq", "basis", "handoff",
                                               "unit", "exclude")}
    quotes = {k: collections.Counter() for k in ("period", "freq", "basis", "handoff", "unit", "exclude")}
    has = collections.Counter()
    left = {w[0]: {"occ": 0, "q": set(), "inside": 0, "examples": []} for w in WORDS}
    rows = []
    for q in questions:
        text = q1(q["question"])
        spans = []
        per = rules.find_periods(text, None)
        for p in per:
            dist["period_kind"][p["kind"]] += 1
            dist["period_status"][p["status"]] += 1
            if p.get("unit"):
                dist["period_unit"][p["unit"]] += 1
            quotes["period"][text[p["i"]:p["j"]]] += 1
            spans.append((p["i"], p["j"]))
        fr = rules.find_frequencies(text)
        for v, i, j in fr:
            dist["freq"][v] += 1
            quotes["freq"][text[i:j]] += 1
            spans.append((i, j))
        ba = rules.find_basis(text)
        for ax, v, i, j in ba:
            dist["basis"][f"{ax}:{v}" if ax != "classification" else "classification:X별"] += 1
            quotes["basis"][text[i:j]] += 1
            spans.append((i, j))
        ho = rules.find_handoff(text, [(i, j) for _a, _v, i, j in ba])
        for ax, v, i, j in ho:
            dist["handoff"][f"{ax}:{v}"] += 1
            quotes["handoff"][text[i:j]] += 1
            spans.append((i, j))
        un = rules.find_units(text)
        for v, i, j in un:
            dist["unit"][v] += 1
            quotes["unit"][text[i:j]] += 1
            spans.append((i, j))
        ex = rules.find_excludes(text)
        for name, i, j, _h in ex:
            dist["exclude"]["제외"] += 1
            quotes["exclude"][text[i:j]] += 1
            spans.append((i, j))
        for k, lst in (("기간", per), ("주기", fr), ("기준", ba), ("전달 조건", ho), ("단위", un), ("제외", ex)):
            if lst:
                has[k] += 1
        if not (per or fr or ba or ho or un or ex):
            has["아무것도 없음"] += 1
        found_left = []
        for label, pat, _src in WORDS:
            for m in re.finditer(pat, text):
                inside = any(a <= m.start() and m.end() <= b for a, b in spans)
                if inside:
                    left[label]["inside"] += 1
                    continue
                left[label]["occ"] += 1
                left[label]["q"].add(q["case_id"])
                if len(left[label]["examples"]) < 3:
                    left[label]["examples"].append(text)
                found_left.append(label)
        rows.append({"case_id": q["case_id"], "dataset": q["dataset"], "question": text,
                     "기간": " | ".join(f"{text[p['i']:p['j']]}({p['kind']},{p['status']})" for p in per),
                     "주기": " | ".join(f"{text[i:j]}({v})" for v, i, j in fr),
                     "기준": " | ".join(f"{text[i:j]}({a}:{v})" for a, v, i, j in ba),
                     "전달 조건": " | ".join(f"{text[i:j]}({a}:{v})" for a, v, i, j in ho),
                     "단위": " | ".join(f"{text[i:j]}({v})" for v, i, j in un),
                     "제외": " | ".join(text[i:j] for _n, i, j, _h in ex),
                     "남은 조건스러운 말": " / ".join(found_left)})

    left_rows = sorted(({"말": label, "출처": src, "규칙 밖 출현": left[label]["occ"], "규칙 밖 질문 수": len(left[label]["q"]),
                         "규칙 구간 안 출현": left[label]["inside"], "예": " || ".join(left[label]["examples"])}
                        for label, _p, src in WORDS), key=lambda r: -r["규칙 밖 질문 수"])
    n = len(questions)
    out = {"질문 수": n, "규칙별 걸린 질문 수": dict(has), "분포": {k: dict(v.most_common()) for k, v in dist.items()},
           "자주 걸린 원문 상위": {k: v.most_common(15) for k, v in quotes.items()},
           "남은 조건스러운 말": left_rows}

    DIR.mkdir(parents=True, exist_ok=True)
    write_csv(DIR / "conditions_by_question.csv", rows)
    write_csv(DIR / "leftover_condition_words.csv", left_rows)
    write_json(DIR / "numbers.json", out)

    def fmt(c):
        return ", ".join(f"{k} {v}" for k, v in c.most_common())

    lines = ["# 5. 조건 어휘 (골든+개발만, 대략치, 승인 전)", "",
             f"- 자료: 골든+개발 {n}문항. 새200·블라인드는 열지 않았다. 호출 0회.",
             "- 규칙: 옛 A 규칙 `src/claview/aflow/rules.py`의 find_periods(as_of 없음)·find_frequencies·find_basis·find_handoff·find_units·find_excludes를 Q1 정리본(NFKC, 공백 하나)에 그대로 돌렸다. 이 스크립트에서만 import.",
             "- '남은 조건스러운 말'은 위 규칙이 잡은 구간 밖에서 글자 일치로 센 것이다. 낱말 목록은 상세 2.2절 + '추가' 표시한 말. 부분 문자열이라 다른 낱말 안의 일치(예: 구성내역의 구성)도 섞인다.", "",
             "## 규칙이 잡은 것", "", "| 규칙 | 걸린 질문 | 분포 |", "|---|---|---|",
             f"| 기간 | {has['기간']} | 종류 {fmt(dist['period_kind'])}; 상태 {fmt(dist['period_status'])}; 최근 단위 {fmt(dist['period_unit'])} |",
             f"| 주기 | {has['주기']} | {fmt(dist['freq'])} |",
             f"| 기준 | {has['기준']} | {fmt(dist['basis'])} |",
             f"| 전달 조건(비교·계산) | {has['전달 조건']} | {fmt(dist['handoff'])} |",
             f"| 단위 | {has['단위']} | {fmt(dist['unit'])} |",
             f"| 제외 | {has['제외']} | {fmt(dist['exclude'])} |",
             f"| 아무것도 없음 | {has['아무것도 없음']} | |",
             "", "자주 걸린 원문(상위 8):", ""]
    for k, v in quotes.items():
        lines.append(f"- {k}: " + ", ".join(f"{a}({b})" for a, b in v.most_common(8)))
    lines += ["", "## 규칙 밖에 남은 조건스러운 말 (질문 수 순)", "", "| 말 | 출처 | 규칙 밖 질문 수 | 규칙 밖 출현 | 규칙 구간 안 출현 |",
              "|---|---|---|---|---|"]
    for r in left_rows:
        if r["규칙 밖 출현"] or r["규칙 구간 안 출현"]:
            lines.append(f"| {r['말']} | {r['출처']} | {r['규칙 밖 질문 수']} | {r['규칙 밖 출현']} | {r['규칙 구간 안 출현']} |")
    lines += ["", "- '추이'·'비교'처럼 옛 규칙 REQUEST_PATTERNS가 핵심어에서 지우는 말도 조건 구간으로는 잡히지 않아 여기서는 '규칙 밖'으로 센다.",
              "", "## 파일", "", "- `conditions_by_question.csv`: 질문마다 규칙이 잡은 조건과 남은 말(골든·개발만)",
              "- `leftover_condition_words.csv`: 남은 말 빈도와 예", "- `numbers.json`: 전체 수치"]
    (DIR / "summary.md").write_text("\n".join(lines) + "\n", "utf8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
