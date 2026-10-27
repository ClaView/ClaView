"""Finds which part of the slimmed table (model_table) changes HCX's choice, against the REAL
HCX-007 API. Experiment only: nothing here is wired into the pipeline.

Background: with the slimmed table CV-AB-03 ("실질 재화와 서비스의 수출 증감률") fails 3 of 3
runs -- HCX picks 3010501 ("(재화)", a child) instead of 30105 ("재화와 서비스의 수출").
It passed with the table as stored. Several things changed at once, so this runs the same
case with one change undone at a time:

  old_all           table as stored + the OLD prompt wording     (B before the change)
  old_table_prompt  table as stored + the NEW prompt wording     (is it the prompt?)
  slim_old_prompt   slimmed table   + the OLD prompt wording     (is it the table?)
  slim              slimmed table   + the NEW prompt             (B now, since 2026-10-16)
  slim_no_under     slimmed, without the `under` field
  slim_leaf_all     slimmed, `is_leaf` written on every value (true and false); this is what
                    model_table sends since 2026-10-16, so it now equals `slim`
  slim_path         slimmed, plus `path` as a list of names (no codes) on every value

Each variant rebuilds the table from the stored metadata, so everything else (pruning, schema,
period, as_of) is what B sends. The schema is the same in every variant.

Reading the result: `old_all` passing and `slim` failing confirms the change matters. Then
`old_table_prompt` vs `slim_old_prompt` tell whether prompt or table does it, and the
`slim_*` variants tell which table field brings the pass back. HCX at temperature 0 gave the
same answer in 3 of 3 runs, so one run per variant is enough (--repeat 1, the default).

Cost: 1 call per case and variant; the defaults (CV-AB-03, 7 variants) are 7 calls.

Same setup as verify_expand_hcx.py (the key is read only from CLOVASTUDIO_API_KEY, never
printed or written). Windows PowerShell, from the project root:

    # no key, no network: checks the script itself
    python modules/b/scripts/b_part/experiment_context_format_hcx.py --dry-run
    # the default: CV-AB-03, all variants
    python modules/b/scripts/b_part/experiment_context_format_hcx.py --allow-external
    # more cases (cases that rely on shared names), fewer variants
    python modules/b/scripts/b_part/experiment_context_format_hcx.py --allow-external \
        --variants old_all slim CV-AB-01 CV-AB-02 CV-AB-11

Output: eval/results/context_format_experiment.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # verify_expand_hcx lives next to this
from verify_expand_hcx import CATALOG, GOLDEN, METADATA, judge, run_b  # noqa: E402

from claview_b.contracts import Config, StageFailure, today_iso  # noqa: E402
from claview_b.evaluation import scripted_answers, write_json  # noqa: E402
from claview_b.metadata import MetadataStore  # noqa: E402
from claview_b.models import HcxModel, ScriptedModel  # noqa: E402
from claview_b.stages import build_b_context, model_table  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = ROOT / "eval" / "results" / "context_format_experiment.json"
DEFAULT_CASES = ["CV-AB-03"]

# The prompt lines B had before the slimmed table, and the lines that replaced them.
PROMPT_EDITS = [
    (
        "부모·총계도 공식 값이면 선택할 수 있다. 이름과 상위 경로를 함께 읽는다.\n",
        "부모·총계도 공식 값이면 선택할 수 있다.\n"
        "이름과 parent_code(또는 path)로 이어지는 상위 값을 함께 읽는다.\n"
        "같은 이름의 값이 여럿이면 under에 그 값을 구분하는 상위 이름이 적혀 있다.\n",
    ),
]
# (Until 2026-10-16 a second pair rewrote the `is_leaf` sentence of EXPAND_PROMPT for a table that
# marked only `false`. B now writes `is_leaf` on every value and the sentence is the original
# one again, so only the line above differs between the old and the current wording.)

# variant -> (table form, prompt wording)
VARIANTS = {
    "old_all": ("stored", "old"),
    "old_table_prompt": ("stored", "new"),
    "slim_old_prompt": ("slim", "old"),
    "slim": ("slim", "new"),
    "slim_no_under": ("slim_no_under", "new"),
    "slim_leaf_all": ("slim_leaf_all", "new"),
    "slim_path": ("slim_path", "new"),
}


def old_prompt(system: str) -> str:
    """The prompt as it was before the slimmed table. Stops loudly if the text moved on."""
    for old, new in PROMPT_EDITS:
        system = system.replace(new, old)
    if PROMPT_EDITS[0][0] not in system:
        raise RuntimeError(
            "B's prompt no longer has the line this experiment rewrites; update PROMPT_EDITS"
        )
    return system


def table_form(form: str, pruned: dict, stored: dict) -> dict:
    if form == "stored":
        return pruned
    shown = model_table(pruned, stored)
    for axis, dimension in shown["dimensions"].items():
        for code, value in dimension["values"].items():
            source = pruned["dimensions"][axis]["values"][code]
            if form == "slim_no_under":
                value.pop("under", None)
            elif form == "slim_leaf_all":
                value["is_leaf"] = bool(source["is_leaf"])
            elif form == "slim_path":
                value["path"] = [step["name"] for step in source["path"]]
    return shown


class FormatVariant:
    """Rebuilds the table of the question from the stored metadata, then calls the model."""

    def __init__(self, name, inner, store, config):
        self.form, self.prompt = VARIANTS[name]
        self.inner, self.store, self.config, self.log = inner, store, config, []

    def complete(self, task, system, context, schema, max_tokens, trace):
        org, table_id = task.removeprefix("B:").split(":")
        stored = self.store.get(org, table_id)
        pruned = build_b_context(context["query"], stored, self.config)
        context = {
            **copy.deepcopy(context),
            "table_metadata": table_form(self.form, pruned, stored),
        }
        if self.prompt == "old":
            system = old_prompt(system)
        self.log.append({"context_chars": len(json.dumps(context, ensure_ascii=False))})
        return self.inner.complete(task, system, context, schema, max_tokens, trace)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("cases", nargs="*", help="case ids (default: CV-AB-03)")
    p.add_argument("--variants", nargs="+", choices=list(VARIANTS), default=list(VARIANTS))
    p.add_argument("--allow-external", action="store_true", help="required for real HCX calls")
    p.add_argument(
        "--dry-run", action="store_true", help="replay golden answers; no key, no network"
    )
    p.add_argument("--repeat", type=int, default=1, help="runs per case and variant (default 1)")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument("--delay", type=float, default=10.0, help="seconds before each HCX call")
    p.add_argument("--output", type=Path, default=OUTPUT)
    args = p.parse_args()
    if not args.dry_run:
        if not args.allow_external:
            p.error("Real HCX calls require --allow-external (or use --dry-run)")
        if not os.environ.get("CLOVASTUDIO_API_KEY"):
            p.error("CLOVASTUDIO_API_KEY is not set. See verify_expand_hcx.py for setup steps.")
    ids = args.cases or DEFAULT_CASES
    cases = {c["case_id"]: c for c in json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]}
    unknown = [i for i in ids if i not in cases]
    if unknown:
        p.error("Unknown case id(s): " + ", ".join(unknown))

    store = MetadataStore(CATALOG, METADATA)
    config = Config(as_of=args.as_of or today_iso())
    live = None if args.dry_run else HcxModel(os.environ["CLOVASTUDIO_API_KEY"])
    results = []
    try:
        print(("dry-run (replay)" if args.dry_run else "LIVE HCX-007") + f": {len(ids)} case(s)")
        for variant in args.variants:
            table, wording = VARIANTS[variant]
            print(f"\n== variant: {variant}  (table={table}, prompt={wording})")
            for case_id in ids:
                for run in range(1, args.repeat + 1):
                    case = cases[case_id]
                    inner = ScriptedModel(scripted_answers(case)) if args.dry_run else live
                    wrapper = FormatVariant(variant, inner, store, config)
                    outcome, calls = run_b(
                        case, store, config, wrapper, 0.0 if args.dry_run else args.delay
                    )
                    result = {**judge(case, outcome, calls), "variant": variant, "run": run}
                    result["variant_log"] = wrapper.log
                    results.append(result)
                    chars = wrapper.log[0]["context_chars"] if wrapper.log else "-"
                    print(
                        f"[{'PASS' if result['pass'] else 'FAIL'}] {case_id} run{run}: "
                        f"status={result['status']} series {result['series_got']}/"
                        f"{result['series_expected']} context_chars={chars}"
                    )
                    if not result["pass"]:
                        for line in result["model_rows"][:4]:
                            print("        model:", line)
                        for tag in ("missing", "extra"):
                            for item in result[tag][:3]:
                                print(f"        {tag}: {item}")
    except StageFailure as exc:
        print("Stopped:", exc.code)
        sys.exit(2)
    finally:
        if live:
            live.close()

    print("\nsummary (passed runs per variant):")
    summary = {}
    for variant in args.variants:
        rows = [r for r in results if r["variant"] == variant]
        summary[variant] = {"runs": len(rows), "passed": sum(r["pass"] for r in rows)}
        print(f"  {variant:17} {summary[variant]['passed']}/{summary[variant]['runs']}")
    write_json(args.output, {"summary": summary, "results": results})
    print("Wrote", args.output)


if __name__ == "__main__":
    main()
