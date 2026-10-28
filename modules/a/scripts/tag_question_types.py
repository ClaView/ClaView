"""Tag the 417 old-pipeline results with rule-based question types (plan 04, section 7-1, M0).

Purpose: attach question-type columns to the per-case stage scores written by scripts/score_by_stage.py (removed in plan 26), and
count correct cases and wrong cases by cause for every type value. Types are computed from the question text
only (catalog names, period/frequency rules, basis words, fixed word lists); the answer files are not read.
Types are rule-made suspicion marks, not judgements.

Input:
- cases_csv (default runs/m0_score_by_stage/cases.csv): output of scripts/score_by_stage.py (removed in plan 26). If a
  summary.json sits next to it, the totals are checked against it.
- claview_a_legacy.paths.QUESTIONS_417 (question text, joined on case_id).
- the catalog through claview_a_legacy.catalog.layer.load_layer (no external synonyms, no overrides).

Output (out_dir, default runs/m0_question_types):
- cases_typed.csv: input columns plus the type columns (utf-8-sig).
- by_type.json: per dataset (golden, development, official, all), per type column, per value:
  {cases, correct, wrong_by_cause{interpretation, not_in_pool, out_of_cards, selection}}.
- by_type.md: the same as tables.

No model call and no KOSIS call (0 external calls); network transport is blocked at start.
Usage: PYTHONHASHSEED=0 python -B -X utf8 modules/a/scripts/tag_question_types.py [cases_csv] [out_dir]
"""

from __future__ import annotations

import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modules" / "a"))

from claview_a_legacy import _guard  # noqa: E402

_guard.install_network_block()

from claview_a_legacy import paths  # noqa: E402
from claview_a_legacy.catalog.layer import load_layer  # noqa: E402
from claview_a_legacy.reused import q_rules  # noqa: E402

CAUSES = ("interpretation", "not_in_pool", "out_of_cards", "selection")
DATASETS = ("golden", "development", "official")

# Fixed word lists (language rules, not tuned to cases).
LIST_WORDS = re.compile(r"와|과|및|그리고|랑|이랑|하고|,|·")
RELATION_WORDS = re.compile(r"비율|차이|격차|비중|대비|합계|합친|더한")
BYEOL = re.compile(r"[가-힣]+별")
NEGATION_WORDS = re.compile(r"아니라|아닌|말고|대신|뺀|제외|빼고")
ID_FAMILY_SUFFIX = re.compile(r"[-_]?\d+$")
PARENS = re.compile(r"[(（\[][^)）\]]*[)）\]]")
SPACES = re.compile(r"\s+")
MIN_NAME_LEN = 3
BASIS_EXCLUDED = {"기준"}

TYPE_COLUMNS = ("dataset", "id_family", "n_catalog_names", "has_list", "has_relation", "has_period",
                "has_frequency", "has_basis", "has_byeol", "has_negation_or_exclusion", "no_catalog_name")


def clean_name(name: str | None) -> str:
    """Name with parenthesised parts and all whitespace removed."""
    return SPACES.sub("", PARENS.sub("", name or ""))


def catalog_names() -> list[str]:
    """Cleaned table and axis-item names of at least MIN_NAME_LEN characters, longest first."""
    layer = load_layer(include_external_synonyms=False, overrides=False)
    raw = [t.name for t in layer.catalog.tables.values()]
    raw += [it.name for ax in layer.catalog.axes() for it in ax.items]
    names = {n for n in (clean_name(r) for r in raw) if len(n) >= MIN_NAME_LEN}
    return sorted(names, key=lambda n: (-len(n), n))


def find_names(question: str, names: list[str]) -> list[str]:
    """Distinct catalog names found in the space-free question, longest first, without overlapping spans."""
    text = SPACES.sub("", question)
    taken = [False] * len(text)
    found = []
    for name in names:
        if name not in text:
            continue
        start, hit = text.find(name), False
        while start != -1:
            end = start + len(name)
            if not any(taken[start:end]):
                taken[start:end] = [True] * len(name)
                hit = True
            start = text.find(name, start + 1)
        if hit:
            found.append(name)
    return found


def basis_words() -> list[str]:
    lines = paths.BASIS_WORDS.read_text(encoding="utf-8").splitlines()
    words = [w.strip() for w in lines if w.strip() and not w.lstrip().startswith("#")]
    return [w for w in words if w not in BASIS_EXCLUDED]


def type_row(case_id: str, dataset: str, question: str, names: list[str], basis: list[str]) -> dict:
    found = find_names(question, names)
    n = len(found)
    flat = SPACES.sub("", question)
    return {
        "dataset": dataset,
        "id_family": ID_FAMILY_SUFFIX.sub("", case_id),
        "n_catalog_names": "0" if n == 0 else "1" if n == 1 else "2+",
        "has_list": bool(LIST_WORDS.search(question)) and n >= 2,
        "has_relation": bool(RELATION_WORDS.search(question)),
        "has_period": bool(q_rules.find_periods(question, None)),
        "has_frequency": bool(q_rules.find_frequencies(question)),
        "has_basis": any(w in flat for w in basis),
        "has_byeol": bool(BYEOL.search(question)),
        "has_negation_or_exclusion": bool(NEGATION_WORDS.search(question)),
        "no_catalog_name": n == 0,
        "catalog_names_found": " | ".join(found),
    }


def tally(rows: list[dict]) -> dict:
    return {"cases": len(rows), "correct": sum(r["_ok"] for r in rows),
            "wrong_by_cause": {c: sum(r["cause"] == c for r in rows) for c in CAUSES}}


def by_type(rows: list[dict]) -> dict:
    out = {}
    for ds in (*DATASETS, "all"):
        part = [r for r in rows if ds == "all" or r["dataset"] == ds]
        block = {"_total": tally(part)}
        for col in TYPE_COLUMNS:
            groups = collections.defaultdict(list)
            for r in part:
                groups[str(r[col])].append(r)
            block[col] = {v: tally(groups[v]) for v in sorted(groups)}
        out[ds] = block
    return out


def to_markdown(result: dict) -> str:
    lines = ["# 질문 유형별 옛 파이프라인 결과 (M0)", "",
             "- official(새200)은 건수만, 문항을 열어 고치지 않음",
             "- 유형은 규칙으로 만든 의심 표시", ""]
    head = "| 값 | cases | correct | interpretation | not_in_pool | out_of_cards | selection |"
    sep = "|---|---:|---:|---:|---:|---:|---:|"

    def row(label, t):
        w = t["wrong_by_cause"]
        return f"| {label} | {t['cases']} | {t['correct']} | " + " | ".join(str(w[c]) for c in CAUSES) + " |"

    for ds, block in result.items():
        lines += [f"## {ds}", "", head, sep, row("(전체)", block["_total"]), ""]
        for col in TYPE_COLUMNS:
            if col == "dataset" and ds != "all":
                continue
            lines += [f"### {col}", "", head, sep]
            lines += [row(v, t) for v, t in block[col].items()]
            lines.append("")
    return "\n".join(lines)


def main(cases_csv: Path, out_dir: Path) -> dict:
    with cases_csv.open(encoding="utf-8-sig", newline="") as f:
        cases = list(csv.DictReader(f))
    questions = {q["case_id"]: q for q in json.loads(paths.QUESTIONS_417.read_text(encoding="utf-8"))}
    names, basis = catalog_names(), basis_words()
    rows = []
    for case in cases:
        q = questions[case["case_id"]]
        typed = type_row(case["case_id"], case["dataset"], q["question"], names, basis)
        typed.pop("dataset")
        rows.append({**case, **typed})
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "cases_typed.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        r["_ok"] = r["set_exact"] == "True"
        r["cause"] = r["cause"] or None
    result = by_type(rows)
    (out_dir / "by_type.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf8")
    (out_dir / "by_type.md").write_text(to_markdown(result), "utf8")

    # Self-check against the stage summary written next to the input, when present.
    check = {"rows": len(rows), "correct": result["all"]["_total"]["correct"],
             "wrong_by_cause": result["all"]["_total"]["wrong_by_cause"], "n_catalog_names": len(names)}
    summary_path = cases_csv.parent / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf8"))
        for ds in (*DATASETS, "all"):
            mine = result[ds]["_total"]
            assert mine["cases"] == summary[ds]["cases"], (ds, mine["cases"])
            assert mine["correct"] == summary[ds]["correct"], (ds, mine["correct"])
            assert mine["wrong_by_cause"] == summary[ds]["wrong_by_cause"], (ds, mine["wrong_by_cause"])
        check["matches_summary"] = True
    assert _guard.NETWORK_ATTEMPTS == [], "network attempted"
    return check


if __name__ == "__main__":
    args = sys.argv[1:]
    src = Path(args[0]) if args else ROOT / "runs/m0_score_by_stage/cases.csv"
    dst = Path(args[1]) if len(args) > 1 else ROOT / "runs/m0_question_types"
    print(json.dumps(main(src, dst), ensure_ascii=False, indent=1))
