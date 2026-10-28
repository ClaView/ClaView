"""Catalog-layer statistics -> runs/v3_stretch1/catalog_stats.md (and .json next to it).

    PYTHONPATH=src PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 -m claview_a_legacy.catalog.stats

The boundary guard (old-pipeline import block, network block) is installed before anything else is imported.
Only catalog data, the axis-role inputs, the package data files, the external synonym draft (when present) and the
EDA numbers file are read. No question, answer or case file is read. No network, no model.

Three configurations of the axis-role table are counted side by side:
- A ``computed``: rules only (no user CSV judgments, no overrides) -- comparable with the EDA recount
- B ``draft_csv``: the user's per-axis CSV judgments and pressed-axis list, no overrides
- C ``draft_csv+overrides``: B plus the draft overrides (the default of ``load_layer``)
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from .. import _guard, paths

CONFIGS = (
    ("A computed", {"use_draft_csv": False, "overrides": False}),
    ("B draft_csv", {"use_draft_csv": True, "overrides": False}),
    ("C draft_csv+overrides", {"use_draft_csv": True, "overrides": True}),
)
EDA_KEYS = (
    "1층 축 이름", "1층 이름으로 정해짐", "1층 축마다 판정 이름", "1층 사람 확인 이름", "2층 일반 이름 축", "2층 대상 축",
    "2층 경계 축(30~70%)", "대상 사전", "3층 지표 축 값", "3층 기준", "3층 합계 머리", "3층 결합", "3층 대상", "3층 지표",
    "4층 눌린 축",
)


def _rel(path) -> str:
    try:
        return Path(path).resolve().relative_to(paths.ROOT).as_posix()
    except ValueError:
        return Path(path).name


def role_numbers(roles) -> dict:
    """Numbers named like the EDA numbers file (runs/eda_v3/2_axis_roles/numbers.json)."""
    from .roles import BOUNDARY

    l1_roles = collections.Counter(roles.layer1_names.values())
    named = [a for a in roles.axes.values() if a.axis_name]
    generic = [a for a in named if a.layer1 == "generic"]
    l3 = collections.Counter(v.layer3_type for v in roles.values.values() if v.layer3_type)
    return {
        "1층 축 이름": len({a.axis_name for a in named}),
        "1층 이름으로 정해짐": l1_roles["target"] + l1_roles["basis"] + l1_roles["indicator"],
        "1층 축마다 판정 이름": l1_roles["generic"],
        "1층 사람 확인 이름": l1_roles["human"],
        "2층 일반 이름 축": len(generic),
        "2층 대상 축": sum(1 for a in generic if a.role == "target"),
        "2층 경계 축(30~70%)": sum(1 for a in generic if BOUNDARY[0] <= a.ratio <= BOUNDARY[1]),
        "대상 사전": len(roles.target_dict),
        "3층 지표 축 값": sum(l3.values()),
        "3층 기준": l3["basis"],
        "3층 합계 머리": l3["total_header"],
        "3층 결합": l3["compound"],
        "3층 대상": l3["target"],
        "3층 지표": l3["indicator"],
        "4층 눌린 축": sum(1 for p in roles.pressed if not p.note.startswith("axis role is")),
    }


def collect(layer_kwargs: dict | None = None) -> dict:
    from .index import build_index
    from .layer import load_layer
    from .roles import VALUE_TYPES, build_axis_roles

    layer_kwargs = dict(layer_kwargs or {})
    layer = load_layer(**layer_kwargs)
    cat, roles = layer.catalog, layer.roles
    axes = list(cat.axes())
    values = [it for ax in axes for it in ax.items]
    role_kwargs = {k: layer_kwargs[k] for k in ("layer1_path", "generic_path", "collapsed_path", "overrides_path",
                                                "basis_words_path") if k in layer_kwargs}
    configs = {}
    for label, cfg in CONFIGS:
        r = roles if cfg == {"use_draft_csv": True, "overrides": True} else build_axis_roles(cat, **cfg, **role_kwargs)
        cls = [v for v in r.values.values() if v.obj_id != "ITEM"]
        configs[label] = {
            "numbers": role_numbers(r),
            "types": {t: sum(1 for v in cls if v.type == t) for t in VALUE_TYPES},
            "unknown_ratio": (sum(1 for v in cls if v.type == "unknown") / len(cls)) if cls else 0.0,
            "axes_by_layer_role": collections.Counter(f"{a.layer}:{a.role}" for a in r.axes.values()),
            "values_by_layer_type": collections.Counter(f"{v.layer}:{v.type}" for v in cls),
            "pressed": {"total": len(r.pressed), "detected": sum(p.detected for p in r.pressed),
                        "in_draft_csv": sum(p.in_draft_csv for p in r.pressed),
                        "applied": sum(p.applied for p in r.pressed),
                        "not_indicator_axis": sum(1 for p in r.pressed if p.note.startswith("axis role is")),
                        "level_override": sum(1 for p in r.pressed if p.level_source == "override")},
            "status": collections.Counter(v.status for v in cls),
            "overridden_values": sum(1 for v in cls if v.overridden),
        }
    report = roles.override_report
    no_external = build_index(cat, roles, None, layer.out_of_scope).stats
    eda = {}
    if paths.EDA_AXIS_ROLE_NUMBERS.exists():
        eda = json.loads(paths.EDA_AXIS_ROLE_NUMBERS.read_text(encoding="utf-8"))
    generic_links = roles.links["generic_csv"]
    collapsed_links = roles.links["collapsed_csv"]
    return {
        "sources": {
            "search_catalog": {"path": _rel(cat.sources["search_catalog"]["path"]),
                               "sha256": cat.sources["search_catalog"]["sha256"]},
            "meta_raw": {"path": _rel(cat.sources["meta_raw"]["path"]), "sha256": cat.sources["meta_raw"]["sha256"]},
            "source_date": cat.sources.get("source_date"),
            "overrides": _rel(layer_kwargs.get("overrides_path") or paths.AXIS_ROLE_OVERRIDES),
            "eda_numbers": _rel(paths.EDA_AXIS_ROLE_NUMBERS) if eda else None,
        },
        "catalog": {
            "tables": len(cat.tables),
            "tables_with_axes": sum(1 for t in cat.tables.values() if t.axes),
            "tables_without_meta": sum(1 for t in cat.tables.values() if not t.has_meta),
            "meta_rows_not_in_list": len(cat.meta_without_catalog),
            "item_rows": sum(len(t.item_rows) for t in cat.tables.values()),
            "axes": len(axes), "axes_named": sum(1 for a in axes if a.named),
            "axis_names": len({a.name for a in axes if a.named}),
            "axis_names_eng": len({a.name_eng for a in axes if a.name_eng}),
            "values": len(values), "values_named": sum(1 for v in values if v.named),
            "values_with_eng": sum(1 for v in values if v.name_eng),
            "values_with_parent": sum(1 for v in values if v.parent_id),
            "values_orphan": sum(1 for v in values if v.orphan),
            "values_with_unit": sum(1 for v in values if v.unit),
            "max_depth": max((v.depth for v in values), default=0),
            "tables_without_unit": sum(1 for t in cat.tables.values() if not t.units),
            "periods": dict(collections.Counter(p.label for t in cat.tables.values() for p in t.periods)),
            "unnamed": cat.unnamed,
        },
        "configs": configs,
        "target_dict": {"size": len(roles.target_dict), "dropped": dict(roles.target_dict.dropped),
                        "age_added": sorted(roles.target_dict.age_added)},
        "links": {
            "generic_csv": {"rows": len(generic_links),
                            "linked_one": sum(1 for e in generic_links if len(e["obj_ids"]) == 1)},
            "collapsed_csv": {"rows": len(collapsed_links),
                              "linked_one": sum(1 for e in collapsed_links if len(e["obj_ids"]) == 1)},
            "problems": roles.link_problems,
            "csv_vs_computed": collections.Counter(
                f"{a.csv_role}/{a.computed_role}" for a in roles.axes.values() if a.csv_role and a.computed_role),
        },
        "overrides": {
            "rows": len(report),
            "by_section_outcome": collections.Counter(f"{r['section']}:{r['outcome']}" for r in report),
            "by_status": collections.Counter(r["status"] for r in report),
            "applied": sum(1 for r in report if r["outcome"] == "applied"),
            "unchanged": sum(1 for r in report if r["outcome"] == "unchanged"),
            "not_applied": [r for r in report if r["outcome"] == "not_applied"],
            "value_rows_values": sum(r.get("values", 0) for r in report if "values" in r),
            "value_rows_changed": sum(r.get("changed", 0) for r in report if "changed" in r),
            "exception_rows": [{k: r.get(k) for k in ("source", "table_id", "subject", "value", "outcome", "reason")}
                               for r in report if "exception_values" in r],
            "exception_values": sum(r["exception_values"] for r in report if "exception_values" in r),
            "exception_changed": sum(r["exception_changed"] for r in report if "exception_changed" in r),
            "pressed_outside": [
                {"table_id": p.table_id, "axis_name": p.axis_name, "level_roles": list(p.level_roles),
                 "level_sizes": [len(g) for g in p.level_ids],
                 "outside": [(roles.value(p.table_id, p.obj_id, i).name, roles.value(p.table_id, p.obj_id, i).type)
                             for i in p.outside_ids]}
                for p in roles.pressed if p.outside_ids],
        },
        "index": layer.index.stats,
        "index_without_synonyms": no_external,
        "synonyms": {"external_present": layer.synonyms.external_present,
                     "include_external": layer.synonyms.include_external, **layer.synonyms.counts},
        "out_of_scope": {"lines": len(layer.out_of_scope.all_entries), "usable": len(layer.out_of_scope.entries)},
        "eda": eda,
    }


def _fmt(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:.2%}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render(s: dict) -> str:
    c, cfg = s["catalog"], s["configs"]
    labels = [label for label, _ in CONFIGS]
    eda_draft, eda_recount = s["eda"].get("draft", {}), s["eda"].get("recount", {})
    out = ["# 카탈로그 층 통계 (1구간, 승인 전 draft)", "",
           "정답·질문 자료는 읽지 않았다. 호출 0회(모델·KOSIS·네트워크 없음). 수치는 승인 전 draft 판정 기준이다.", "",
           "## 원자료", "",
           f"- 표 목록 `{s['sources']['search_catalog']['path']}` SHA-256 `{s['sources']['search_catalog']['sha256']}`",
           f"- 메타 스냅숏 `{s['sources']['meta_raw']['path']}` SHA-256 `{s['sources']['meta_raw']['sha256']}`",
           f"- 기준일 {s['sources']['source_date']}, 덮어쓰기 파일 `{s['sources']['overrides']}`, "
           f"EDA 수치 `{s['sources']['eda_numbers'] or '없음'}`", "",
           "## 카탈로그", "", "| 항목 | 값 |", "|---|---|"]
    rows = [("표", c["tables"]), ("분류 축이 있는 표", c["tables_with_axes"]), ("메타 없는 표", c["tables_without_meta"]),
            ("ITEM 행(표 이름)", c["item_rows"]), ("축(이름 있음/전체)", f"{c['axes_named']:,}/{c['axes']:,}"),
            ("축 이름(한글/영문)", f"{c['axis_names']}/{c['axis_names_eng']}"),
            ("분류값(이름 있음/전체)", f"{c['values_named']:,}/{c['values']:,}"), ("영문 이름 있는 값", c["values_with_eng"]),
            ("부모 있는 값", c["values_with_parent"]), ("부모를 못 찾은 값", c["values_orphan"]),
            ("값 단위 있는 값", c["values_with_unit"]), ("최대 깊이", c["max_depth"]),
            ("표 단위 없는 표", c["tables_without_unit"]),
            ("기간 주기(표 수)", ", ".join(f"{k} {v}" for k, v in c["periods"].items()))]
    out += [f"| {a} | {_fmt(b)} |" for a, b in rows]
    out += ["", "이름 없는 축·값(따로 표시): " + (", ".join(f"{u['table_id']} {u['obj_id']} {u['what']}"
                                             + (f" {u['item_id']}" if u["item_id"] else "") for u in c["unnamed"])
                                             or "없음"), ""]
    out += ["## 축 역할 네 층 (EDA와 나란히)", "",
            "A는 규칙 계산만(EDA 재계산과 같은 조건), B는 사용자 CSV 판정(일반축·눌린축) 사용, C는 B + draft 덮어쓰기(기본값).", "",
            "| 항목 | EDA 초안 | EDA 재계산 | " + " | ".join(labels) + " |", "|---|---|---|" + "---|" * len(labels)]
    for k in EDA_KEYS:
        out.append(f"| {k} | {_fmt(eda_draft.get(k))} | {_fmt(eda_recount.get(k))} | "
                   + " | ".join(_fmt(cfg[label]["numbers"][k]) for label in labels) + " |")
    out += ["", "- 3층 수치는 4층·값 덮어쓰기 전의 3층 결과다(지표 축 값만). 3층 결합 규칙은 초안 예 \"정기예금-2%미만\"을 잡도록 "
            "EDA 스크립트 정규식에서 하이픈 뒤 숫자를 허용했다(아래 \"EDA와 다른 점\").", ""]
    a_numbers = cfg[labels[0]]["numbers"]
    diffs = [k for k in EDA_KEYS if k in eda_recount and eda_recount[k] != a_numbers[k]]
    out += ["### EDA와 다른 점", "",
            "- A와 EDA 재계산이 다른 항목: " + (", ".join(f"{k} {eda_recount[k]:,}→{a_numbers[k]:,}" for k in diffs)
                                          or "없음"),
            "  - 3층 결합·지표: EDA 스크립트(`modules/a/scripts/eda_v3/roles.py`)의 결합 정규식은 하이픈 뒤 숫자를 받지 않아 초안 3층 예 "
            "\"정기예금-2%미만\"(\"A-B\" 꼴)을 결합으로 잡지 못한다. 여기서는 한글(또는 닫는 괄호) 뒤 하이픈 다음에 숫자를 허용했다. "
            "그 밖의 규칙·정규화는 EDA 스크립트를 그대로 옮겼다.",
            "- EDA 초안과의 차이(일반 이름 축 355→358, 대상 사전 488→476 등)는 EDA 재계산과 같은 이유다"
            "(`runs/eda_v3/README.md` 2번: 초안 CSV에 없는 1표의 3축, 초안의 정확한 정규화·기준어 목록 미확인).",
            "- B는 일반축 CSV 판정이 규칙 계산보다 앞선다(CSV와 계산이 갈린 축은 아래 \"CSV 연결\"). 눌린 축은 CSV 목록과 규칙 검출의 합집합이다.",
            "- C는 B에 draft 덮어쓰기를 더한 값이다. 덮어쓰기로 대상 축이 된 축의 값은 3층 대상에 들지 않는다(축 단위로 대상).", ""]
    out += ["## 값의 형 (분류값, ITEM 행 제외)", "", "| 형 | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for t in cfg[labels[0]]["types"]:
        out.append(f"| {t} | " + " | ".join(_fmt(cfg[label]["types"][t]) for label in labels) + " |")
    out.append("| 형 미정 비율(unknown) | " + " | ".join(_fmt(cfg[label]["unknown_ratio"]) for label in labels) + " |")
    out.append("| 덮어쓰기로 정해진 값 | " + " | ".join(_fmt(cfg[label]["overridden_values"]) for label in labels) + " |")
    out.append("| approved 값 | " + " | ".join(_fmt(cfg[label]["status"].get("approved", 0)) for label in labels) + " |")
    out += ["", "### 층별 형별 값 수 (C)", "", "| 층 | 형 | 값 수 |", "|---|---|---|"]
    for key_, n in sorted(cfg[labels[-1]]["values_by_layer_type"].items()):
        layer, t = key_.split(":")
        out.append(f"| {layer} | {t} | {n:,} |")
    out += ["", "### 층별 역할별 축 수 (C)", "", "| 층 | 축 역할 | 축 수 |", "|---|---|---|"]
    for key_, n in sorted(cfg[labels[-1]]["axes_by_layer_role"].items()):
        layer, role = key_.split(":")
        out.append(f"| {layer} | {role} | {n:,} |")
    out += ["", "### 4층 눌린 축", "", "| 항목 | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for k in ("total", "detected", "in_draft_csv", "applied", "not_indicator_axis", "level_override"):
        out.append(f"| {k} | " + " | ".join(_fmt(cfg[label]["pressed"][k]) for label in labels) + " |")
    td = s["target_dict"]
    out += ["", "## 대상 값 사전", "", f"- 열쇠 {td['size']:,}개(초안 488, EDA 재계산 476)",
            "- 뺀 값: " + ", ".join(f"{k} {v:,}" for k, v in td["dropped"].items()),
            f"- 더한 연령대: {', '.join(td['age_added'])}", ""]
    lk = s["links"]
    out += ["## CSV 연결 (표 ID, 축 이름 → 축 ID)", "",
            f"- 일반축 CSV {lk['generic_csv']['rows']}행 중 1건 연결 {lk['generic_csv']['linked_one']}",
            f"- 눌린축 CSV {lk['collapsed_csv']['rows']}행 중 1건 연결 {lk['collapsed_csv']['linked_one']}",
            "- 일반축 CSV 판정/규칙 계산(축 수): " + ", ".join(f"{k} {v}" for k, v in sorted(lk["csv_vs_computed"].items())),
            f"- 연결 0건·여러 건: {len(lk['problems'])}건" + ("" if lk["problems"] else " (없음)")]
    for p in lk["problems"]:
        out.append(f"  - {p['kind']} {p['source']} {p['table_id']} {p['axis_name']}: {p['found']}건")
    ov = s["overrides"]
    out += ["", "## draft 덮어쓰기 (C)", "",
            f"- 행 {ov['rows']}, 상태 " + ", ".join(f"{k} {v}" for k, v in ov["by_status"].items()),
            f"- 적용해 바뀜 {ov['applied']}, 적용했으나 같음 {ov['unchanged']}, 적용 못 함 {len(ov['not_applied'])}",
            "- 구분별: " + ", ".join(f"{k} {v}" for k, v in sorted(ov["by_section_outcome"].items())),
            f"- 값 덮어쓰기(겸용 값, 적힌 표 안에서만): 대상 값 {ov['value_rows_values']:,}, 형이 바뀐 값 {ov['value_rows_changed']:,}",
            f"- 축 행의 값 예외(괄호 안 \"'값' = 역할\"): 행 {len(ov['exception_rows'])}, 값 {ov['exception_values']:,}, "
            f"형이 바뀐 값 {ov['exception_changed']:,}",
            "- 덮어쓰기 파일의 기계 칸(role·level_roles·value_role·value_exceptions)만 읽는다. 못 읽은 행은 parse_note를 "
            "이유에 붙인다.", "",
            "적용 못 한 행(원본 행 · 구분 · 축 이름 또는 값 · 이유):", ""]
    for r in ov["not_applied"]:
        where = f" {r['table_id']}" if r.get("table_id") else ""
        out.append(f"- {r['source']} · {r['section']} · {r['subject']}{where} · {r['reason']}")
    out += ["", "값 예외를 적용한 축 행(원본 행 · 표 · 축 · 기계 칸 · 결과 · 이유):", ""]
    for r in ov["exception_rows"]:
        out.append(f"- {r['source']} · {r['table_id']} · {r['subject']} · {r['value']} · {r['outcome']} · {r['reason']}")
    out += ["", "3단계 이상 눌린 축에서 단계 밖에 둔 뿌리 값(표 · 축 · 단계 역할 · 단계별 값 수 · 뿌리 값=형):", ""]
    for p in ov["pressed_outside"]:
        outside = ", ".join(f"{name}={kind}" for name, kind in p["outside"])
        out.append(f"- {p['table_id']} · {p['axis_name']} · {'/'.join(p['level_roles'])} · "
                   f"{'/'.join(str(n) for n in p['level_sizes'])} · {outside}")
    ix, ix0 = s["index"], s["index_without_synonyms"]
    out += ["", "## 용어 색인", "", "| 항목 | 동의어 포함 | 동의어 없이 |", "|---|---|---|"]
    for k in ("keys", "candidates", "keys_with_several_candidates", "catalog_keys", "span_keys", "span_keys_with_space",
              "synonym_candidates", "out_of_scope_candidates"):
        out.append(f"| {k} | {_fmt(ix[k])} | {_fmt(ix0[k])} |")
    out += ["", "- span_keys = 이름 보존 구간으로 쓸 수 있는 카탈로그 이름 열쇠(2자 이상, 글자 포함). span_keys_with_space = 그중 원래 "
            "이름에 띄어쓰기가 있는 것(질문을 낱말로 자르면 깨질 수 있는 이름).", ""]
    sy = s["synonyms"]
    out += ["## 동의어", "", f"- 승인 사전: 줄 {sy['approved_lines']}, 쓸 수 있는 항목 {sy['approved_entries']}",
            f"- 외부 초안: 파일 {'있음' if sy['external_present'] else '없음'}, 스위치 {'켬' if sy['include_external'] else '끔'}, "
            f"줄 {sy['external_lines']:,}, 낱말 {sy['words']:,}, 합친 항목 {sy['external_entries']:,}, 고유 낱말 {sy['distinct_words']:,}",
            f"- 모호: 파일 표시 {sy['ambiguous_words_source']}, 다시 판정 {sy['ambiguous_words_now']}, "
            f"괄호 떼기로 하나가 되어 풀림 {sy['ambiguous_resolved_by_merge']}, 새로 모호 {sy['ambiguous_new']}", "",
            "## 범위 밖", "", f"- 줄 {s['out_of_scope']['lines']}, 쓸 수 있는 항목 {s['out_of_scope']['usable']}", ""]
    return "\n".join(out) + "\n"


def _jsonable(obj):
    if isinstance(obj, collections.Counter):
        return dict(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def run(out_path: Path | None = None, layer_kwargs: dict | None = None) -> dict:
    out_path = Path(out_path or paths.CATALOG_STATS)
    stats = collect(layer_kwargs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(stats), encoding="utf-8")
    out_path.with_suffix(".json").write_text(json.dumps(_jsonable(stats), ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else None
    stats = run(out)
    print(f"wrote {_rel(out or paths.CATALOG_STATS)}: tables {stats['catalog']['tables']}, "
          f"axes {stats['catalog']['axes']}, values {stats['catalog']['values']}")
    return 0


if __name__ == "__main__":
    _guard.install()
    raise SystemExit(main())
