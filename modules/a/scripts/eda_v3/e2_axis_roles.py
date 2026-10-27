"""EDA v3 item 2: recount the user's four-layer axis role draft and build the confirmation sheet.

Rules: data/a/axis-roles-20261016/지표축_나누기_초안_20261016.md (implemented in roles.py).
Compares each layer with the draft numbers and the draft CSVs, then writes confirm_sheet.csv with the five items of
draft section 5 (decision column left empty for the user).

Usage (repo root): PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 modules/a/scripts/eda_v3/e2_axis_roles.py
"""

from __future__ import annotations

import collections
import re

from common import DRAFT, OUT, clean_value, load_catalog, load_meta, read_csv, write_csv, write_json
from roles import ROLE_KO, AxisRoles, base

DIR = OUT / "2_axis_roles"

DRAFT_NUMBERS = {  # from the draft md (2026-10-16), for side-by-side comparison only
    "1층 축 이름": 29, "1층 이름으로 정해짐": 23, "1층 축마다 판정 이름": 4, "1층 사람 확인 이름": 2,
    "2층 일반 이름 축": 355, "2층 대상 축": 43, "2층 경계 축(30~70%)": 44, "대상 사전": 488,
    "3층 지표 축 값": 11353, "3층 기준": 117, "3층 합계 머리": 104, "3층 결합": 171, "3층 대상": 1011, "3층 지표": 9950,
    "4층 눌린 축": 38,
}
AMBIGUOUS_AXES = ("자금구분별", "분류체계별", "분위 구분코드별")  # draft 5절 1
BOUNDARY_TITLE = re.compile(r"부문별|지역별|업권별")  # draft 5절 2
COMPONENT_TITLE = re.compile(r"품목|용도")  # draft 5절 5 (품목·용도 같은 구성 항목)


def ex(vals, n=6):
    return " / ".join(clean_value(v.get("ITM_NM")) for v in vals[:n])


def main() -> None:
    meta, catalog = load_meta(), load_catalog()
    R = AxisRoles(meta)
    title = {tid: (catalog.get(tid, {}).get("title") or t["name"]) for tid, t in meta.items()}
    mine = {}

    # ---- layer 1
    draft_l1 = {r["축 이름"]: r for r in read_csv(DRAFT / "축이름_역할_초안.csv")}
    by_name = collections.defaultdict(lambda: {"tables": set(), "values": 0})
    for ax in R.axes:
        by_name[ax["name"]]["tables"].add(ax["tid"])
        by_name[ax["name"]]["values"] += len(ax["values"])
    l1_rows = []
    for name, d in draft_l1.items():
        m = by_name.get(name, {"tables": set(), "values": 0})
        l1_rows.append({"축 이름": name, "역할 초안": d["역할 초안"], "표 수(초안)": d["표 수"], "표 수(재계산)": len(m["tables"]),
                        "값 수(초안)": d["값 수"], "값 수(재계산)": m["values"],
                        "차이": "" if (str(len(m["tables"])) == d["표 수"] and str(m["values"]) == d["값 수"]) else "다름"})
    extra = sorted(set(by_name) - set(draft_l1))
    roles_l1 = collections.Counter(R.l1.values())
    mine["1층 축 이름"] = len(by_name)
    mine["1층 이름으로 정해짐"] = roles_l1["target"] + roles_l1["basis"] + roles_l1["measure"]
    mine["1층 축마다 판정 이름"] = roles_l1["generic"]
    mine["1층 사람 확인 이름"] = roles_l1["human"]

    # ---- layer 2
    draft_l2 = {(r["표 ID"], r["축 이름"]): r for r in read_csv(DRAFT / "일반축_축마다_판정.csv")}
    generic = [ax for ax in R.axes if ax["layer1"] == "generic"]
    l2_rows = []
    for ax in generic:
        d = draft_l2.get((ax["tid"], ax["name"]))
        role = ROLE_KO[ax["role"]]
        l2_rows.append({"표 ID": ax["tid"], "표 이름": title[ax["tid"]], "축 이름": ax["name"], "판정(재계산)": role,
                        "대상 사전 비율(재계산)": f"{ax['ratio']:.0%}", "일치/비합계": f"{ax['hit']}/{ax['n_nontotal']}",
                        "판정(초안)": d["역할 초안" if "역할 초안" in d else "역할 판정"] if d else "(초안에 없음)",
                        "근거(초안)": d["근거"] if d else "", "값 수": len(ax["values"]), "값 예": ex(ax["values"])})
    mine["2층 일반 이름 축"] = len(generic)
    mine["2층 대상 축"] = sum(1 for ax in generic if ax["role"] == "target")
    mine["2층 경계 축(30~70%)"] = sum(1 for ax in generic if 0.3 <= ax["ratio"] <= 0.7)
    mine["대상 사전"] = len(R.target_dict)
    l2_diff = [r for r in l2_rows if r["판정(초안)"] not in (r["판정(재계산)"], "(초안에 없음)")]
    not_in_draft = sorted({ax["tid"] for ax in generic if (ax["tid"], ax["name"]) not in draft_l2})
    nd_txt = ", ".join(f"{t}({title[t]})" for t in not_in_draft)
    unnamed = sorted(tid for tid, t in meta.items() if any(v.get("OBJ_NM") is None for v in t["values"]))

    # ---- layer 3
    l3 = collections.Counter()
    l3_rows = []
    for ax in R.axes:
        if ax["role"] != "measure":
            continue
        names = {v["ITM_ID"]: v.get("ITM_NM") or "" for v in ax["values"]}
        for v in ax["values"]:
            r = R.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])]
            l3[r] += 1
            l3_rows.append({"표 ID": ax["tid"], "축 이름": ax["name"], "ITM_ID": v["ITM_ID"], "값": v.get("ITM_NM"),
                            "부모 값": names.get(v.get("UP_ITM_ID"), ""), "보정 역할": ROLE_KO[r]})
    mine["3층 지표 축 값"] = sum(l3.values())
    mine["3층 기준"] = l3["basis"]
    mine["3층 합계 머리"] = l3["header"]
    mine["3층 결합"] = l3["combined"]
    mine["3층 대상"] = l3["target"] + l3["target_sibling"]
    mine["3층 지표"] = l3["measure"]

    # ---- layer 4
    draft_l4 = {r["표 ID"]: r for r in read_csv(DRAFT / "눌린축_목록.csv")}
    mine_l4 = {p["tid"]: p for p in R.pressed}
    l4_rows = []
    for p in R.pressed:
        l4_rows.append({"표 ID": p["tid"], "표 이름": title[p["tid"]], "축 이름": p["name"],
                        "초안 목록에 있음": "예" if p["tid"] in draft_l4 else "아니오",
                        "반복 자식 수": p["n_repeated"], "반복 자식": " / ".join(p["repeated_disp"][:10]),
                        "부모": " / ".join(p["parents_disp"][:10]),
                        "위 단계 대상 비율": p["upper_target_ratio"], "아래 단계 대상 비율": p["lower_target_ratio"],
                        "위 단계 역할": ROLE_KO[p["upper_role"]], "아래 단계 역할": ROLE_KO[p["lower_role"]]})
    for tid, d in draft_l4.items():
        if tid not in mine_l4:
            ax = next((a for a in R.axes if a["tid"] == tid and a["name"] == d["축 이름"]), None)
            l4_rows.append({"표 ID": tid, "표 이름": title.get(tid, d["표 이름"]), "축 이름": d["축 이름"],
                            "초안 목록에 있음": "예(재계산에서 빠짐)",
                            "반복 자식 수": d["반복되는 자식 이름 수"], "반복 자식": d["반복되는 자식 이름"],
                            "부모": d["그 부모들"], "위 단계 대상 비율": "", "아래 단계 대상 비율": "",
                            "위 단계 역할": "", "아래 단계 역할": "",
                            "빠진 이유": (f"재계산에서 축 판정이 {ROLE_KO[ax['role']]}(대상 사전 비율 {ax['ratio']:.0%})"
                                      if ax and ax["role"] != "measure" else "반복 묶음 조건 불충족")})
    mine["4층 눌린 축"] = len(R.pressed)

    # ---- target dictionary
    dict_rows = sorted(({"사전 열쇠": k, "출처 축": " / ".join(sorted(v))} for k, v in R.target_dict.items()),
                       key=lambda r: r["사전 열쇠"])

    # ---- confirm sheet (draft 5절)
    sheet = []
    for ax in R.axes:  # 1. ambiguous axis names
        if ax["name"] in AMBIGUOUS_AXES:
            sheet.append({"구분": "1 애매한 축 이름", "표 ID": ax["tid"], "표 이름": title[ax["tid"]], "축 이름": ax["name"],
                          "값": "", "지금 판정": ROLE_KO[ax["role"]] if ax["layer1"] != "human" else "사람 확인(1층 미정)",
                          "근거": f"1층 초안 {R.l1.get(ax['name'])}; 값 {len(ax['values'])}개: {ex(ax['values'])}"})
    for ax in generic:  # 2. boundary generic axes
        reasons = []
        if 0.3 <= ax["ratio"] <= 0.7:
            reasons.append(f"대상 사전 비율 {ax['ratio']:.0%}")
        m = BOUNDARY_TITLE.search(title[ax["tid"]])
        if m and not (0.3 <= ax["ratio"] <= 0.7):
            reasons.append(f"표 이름에 '{m.group(0)}' (비율 {ax['ratio']:.0%})")
        if reasons:
            sheet.append({"구분": "2 경계 축", "표 ID": ax["tid"], "표 이름": title[ax["tid"]], "축 이름": ax["name"], "값": "",
                          "지금 판정": ROLE_KO[ax["role"]],
                          "근거": "; ".join(reasons) + f"; 일치 {ax['hit']}/{ax['n_nontotal']}; 값 예: {ex(ax['values'])}"})
    for r in l4_rows:  # 3. pressed axes (recount ∪ draft list)
        now = (f"위 {r['위 단계 역할']}({r['위 단계 대상 비율']}) / 아래 {r['아래 단계 역할']}({r['아래 단계 대상 비율']})"
               if r["위 단계 역할"] else "재계산에서 눌린 축 아님")
        sheet.append({"구분": "3 눌린 축", "표 ID": r["표 ID"], "표 이름": r["표 이름"], "축 이름": r["축 이름"], "값": "",
                      "지금 판정": now,
                      "근거": f"초안 목록: {r['초안 목록에 있음']}; 반복 자식: {r['반복 자식'][:120]}; 부모: {r['부모'][:120]}"
                              + (f"; {r['빠진 이유']}" if r.get("빠진 이유") else "")})
    # 4. dictionary values that also stand among indicator siblings (기관/상품 겸용 후보)
    dual = collections.defaultdict(lambda: {"tables": set(), "sibs": set()})
    for ax in R.axes:
        if ax["role"] != "measure":
            continue
        by_parent = collections.defaultdict(list)
        for v in ax["values"]:
            by_parent[v.get("UP_ITM_ID")].append(v)
        for v in ax["values"]:
            k = base(v.get("ITM_NM"))
            if k not in R.target_dict or R.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])] != "target":
                continue
            sib = [s for s in by_parent[v.get("UP_ITM_ID")] if s is not v]
            ratio, _h, n = R.target_ratio(by_parent[v.get("UP_ITM_ID")])
            if n >= 2 and ratio < 0.5:
                dual[k]["tables"].add(ax["tid"])
                dual[k]["sibs"].update(clean_value(s.get("ITM_NM")) for s in sib[:4])
                dual[k].setdefault("disp", clean_value(v.get("ITM_NM")))
    for k, d in sorted(dual.items(), key=lambda x: (-len(x[1]["tables"]), x[0])):
        tids = sorted(d["tables"])
        sheet.append({"구분": "4 기관/상품 겸용 대상 값", "표 ID": " / ".join(tids[:5]) + (" …" if len(tids) > 5 else ""),
                      "표 이름": title[tids[0]], "축 이름": "", "값": d["disp"], "지금 판정": "대상(사전 정확 일치)",
                      "근거": f"사전 출처: {' / '.join(sorted(R.target_dict[k]))}; 형제 다수가 지표인 표 {len(tids)}개; "
                              f"형제 예: {' / '.join(sorted(d['sibs'])[:6])}"})
    for ax in R.axes:  # 5. component items
        if ax["role"] == "measure" and COMPONENT_TITLE.search(title[ax["tid"]]):
            sheet.append({"구분": "5 구성 항목", "표 ID": ax["tid"], "표 이름": title[ax["tid"]], "축 이름": ax["name"], "값": "",
                          "지금 판정": "지표(권장안 그대로)",
                          "근거": f"표 이름에 '{COMPONENT_TITLE.search(title[ax['tid']]).group(0)}'; 값 {len(ax['values'])}개; "
                                  f"보정 분포 {dict((ROLE_KO[a], b) for a, b in ax['value_roles'].most_common())}; 값 예: {ex(ax['values'])}"})
    for r in sheet:
        r["decision"] = ""
    sheet_counts = collections.Counter(r["구분"] for r in sheet)

    DIR.mkdir(parents=True, exist_ok=True)
    write_csv(DIR / "layer1_axis_names.csv", l1_rows)
    write_csv(DIR / "layer2_generic_axes.csv", l2_rows)
    write_csv(DIR / "layer3_value_roles.csv", l3_rows)
    write_csv(DIR / "layer4_pressed_axes.csv", l4_rows,
              list(l4_rows[0].keys()) + ["빠진 이유"] if l4_rows else None)
    write_csv(DIR / "target_dictionary.csv", dict_rows)
    write_csv(DIR / "confirm_sheet.csv", sheet, ["구분", "표 ID", "표 이름", "축 이름", "값", "지금 판정", "근거", "decision"])
    write_json(DIR / "numbers.json", {"draft": DRAFT_NUMBERS, "recount": mine,
                                      "confirm_sheet_rows": dict(sheet_counts),
                                      "target_dict_dropped": dict(R.target_dropped),
                                      "age_added": sorted(R.age_added),
                                      "layer1_extra_names": extra})

    l1_diff = [r for r in l1_rows if r["차이"]]
    lines = ["# 2. 축 역할 네 층 재계산 (대략치, 승인 전)", "",
             "- 규칙: `data/a/axis-roles-20261016/지표축_나누기_초안_20261016.md`를 `modules/a/scripts/eda_v3/roles.py`로 구현. 1층 역할은 초안 CSV `축이름_역할_초안.csv`를 그대로 읽었다.",
             "- 값 비교 열쇠: NFKC·소문자·공백/구두점 제거, 꼬리 각주(\"1)\")·앞 번호(\"(1)\", \"A-1.\")·업종 코드(\"C10 \") 제거, 괄호 묶음 제거(\"비금융법인(사용)\"→비금융법인).",
             "- 합계(합계·총계·계·전체·총액·\"…합계/…총계\")는 사전과 비율 분모에서 뺐다. 이름 없는 축 1개(" + ", ".join(unnamed) + ")는 29개 밖이라 뺐다.",
             "- 기준어 목록(3층): 1층 기준 축의 값 + 고정 목록(`roles.BASIS_WORDS`). \"2024년기준\"처럼 연도+기준어도 기준.",
             "", "## 초안과 나란히", "", "| 항목 | 초안 | 재계산 | 차이 |", "|---|---|---|---|"]
    for k, v in DRAFT_NUMBERS.items():
        m = mine.get(k)
        lines.append(f"| {k} | {v:,} | {m:,} | {m - v:+,} |")
    lines += ["", "## 다른 이유", "",
              f"- **1층 표·값 수:** 29개 이름 중 {len(l1_diff)}개가 다르다: "
              + ", ".join(f"{r['축 이름']}(표 {r['표 수(초안)']}→{r['표 수(재계산)']}, 값 {r['값 수(초안)']}→{r['값 수(재계산)']})" for r in l1_diff)
              + f". 일반 이름 축의 +1표는 {nd_txt}이다. 초안 CSV에는 이 표의 축이 없다(초안은 \"분류 정보가 있는 348표\"라고 적었는데, 이 스냅숏에서는 이 표도 분류 축이 있다). 지역코드별 값 수 차이는 원인을 확인하지 못했다(이 스냅숏은 12표×18값 + 3표×9값 = 243).",
              f"- **2층:** 일반 이름 축 {mine['2층 일반 이름 축']}개 = 초안 355 + 초안 CSV에 없는 {len(mine_generic_extra := [ax for ax in generic if (ax['tid'], ax['name']) not in draft_l2])}축. 판정이 초안과 다른 축 {len(l2_diff)}개: "
              + "; ".join(f"{r['표 ID']} {r['축 이름']} 재계산 {r['판정(재계산)']} {r['대상 사전 비율(재계산)']} / 초안 {r['판정(초안)']} {r['근거(초안)']}" for r in l2_diff)
              + ". 결제통화 표의 값(\"엔화 (아세안)\")은 괄호를 떼면 통화 이름이고, 통화별 축(1층 대상)의 엔화·원화·유로화가 사전에 들어 있어 비율이 50%를 넘는다. 초안은 48%로 계산했다(사전 차이 추정).",
              f"- **대상 사전:** {mine['대상 사전']}개(초안 488). 뺀 값: " + ", ".join(f"{k} {v}" for k, v in R.target_dropped.items())
              + f"; 연령대 추가 {len(R.age_added)}개. 정규화(코드·괄호 제거) 방식 차이로 개수가 조금 다르다(추정).",
              f"- **3층:** 지표 축 값 {mine['3층 지표 축 값']:,}개. 2층에서 초안과 판정이 갈린 축이 빠지고 초안 CSV에 없는 표가 더해진 차이. 기준이 초안보다 많은 것은 \"단기·장기\"(대외채무·대외채권의 \"A-1.단기\"를 번호를 떼고 읽음)와 국가별 결제통화의 \"달러 (호주)\" 같은 값이 기준어로 잡혀서다. 대상은 형제 다수결 {l3['target_sibling']}개 + 사전 정확 일치 {l3['target']}개로 초안보다 적은데, 2층에서 대상 축으로 빠진 결제통화 축의 값(초안에서는 지표 축 안의 대상 값)이 큰 몫이다(추정). 합계 머리가 많은 것은 \"전체\"·\"…합계\"·\"…총계\"까지 합계로 본 정의 차이다.",
              f"- **4층:** 눌린 축 {mine['4층 눌린 축']}개. 초안 38개 중 {sum(1 for t in draft_l4 if t in mine_l4)}개가 겹친다. 초안에만: "
              + ", ".join(sorted(t for t in draft_l4 if t not in mine_l4)) + " (2층에서 대상 축으로 판정돼 4층 대상이 아님). 재계산에만: "
              + ", ".join(sorted(t for t in mine_l4 if t not in draft_l4)) + ".",
              "", "## 3층 보정 분포 (재계산)", "", "| 보정 | 값 수 |", "|---|---|"]
    for r in ("basis", "header", "combined", "target", "target_sibling", "measure"):
        lines.append(f"| {ROLE_KO[r]} | {l3[r]:,} |")
    lines += ["", "## 확인표 `confirm_sheet.csv` (decision 빈 칸)", "", "| 구분 | 행 수 |", "|---|---|"]
    for k in sorted(sheet_counts):
        lines.append(f"| {k} | {sheet_counts[k]} |")
    lines += [f"| 합계 | {sum(sheet_counts.values())} |", "",
              "- 1: 자금구분별·분류체계별·분위 구분코드별 축이 있는 표마다 한 줄.",
              "- 2: 일반 이름 축 중 대상 사전 비율 30~70%, 또는 비율 밖이지만 표 이름에 부문별·지역별·업권별이 있는 축.",
              "- 3: 재계산 눌린 축 ∪ 초안 목록. 단계마다 대상 사전 비율과 역할.",
              "- 4: 대상 사전에 정확히 있으나, 지표 축 안에서 형제 과반이 지표인 자리에 놓인 값(기관과 상품을 겸하는 이름 후보). 자동 기준이라 초안 예시(은행·MMF·신탁)와 다를 수 있다.",
              "- 5: 표 이름에 품목·용도가 있는 지표 축(구성 항목, 권장안은 지표).",
              "", "## 파일", "",
              "- `layer1_axis_names.csv`, `layer2_generic_axes.csv`, `layer3_value_roles.csv`, `layer4_pressed_axes.csv`",
              "- `target_dictionary.csv`: 대상 사전 열쇠와 출처 축", "- `confirm_sheet.csv`: 확인표", "- `numbers.json`: 초안·재계산 수치"]
    (DIR / "summary.md").write_text("\n".join(lines) + "\n", "utf8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
