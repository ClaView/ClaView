"""Checks, against the REAL HCX-007 API, how B behaves with the hierarchy
`expand` schema (none / children / leaves) -- the one thing the offline tests
cannot show: whether the model fills `expand` correctly under structured
output, and whether it picks the same series as the golden answer.

What it does (B stage only; no KOSIS call, no data fetched):

  * runs B for each chosen golden case with a live HCX model, using the golden
    A result as input (A is not exercised here);
  * relative-period cases ("최근 N년" ...) follow the pipeline's notice-and-proceed
    flow: B resolves the period, records a period notice and keeps going;
  * compares the final series set with the golden selections and reports, per
    case: status, schema compliance, which expand modes the model used, and
    missing / extra series.

A case passes when B ends `ok` and the series set equals the golden set.
The expand schema and prompt block are sent only when the question has breakdown
wording ("~별", "세부", ...); other questions get the old schema/prompt and
`expand_offered=False` in the report. B raises `children` to `leaves` for a "~별"
question that names no depth (promoted_to_leaves=...), lowers any stray expand when
there is no breakdown wording (lowered=...), expands the named axis when the model
used no expand at all for a "~별" question (repaired_unused_split=...), selects only the
children the question names instead of all of them (named_children_only=...), and
re-asks the model once after an unusable expand (RETRIED). "[참고]" memo lines are
never part of an expansion. Whether the model listed codes explicitly or
used `expand` is reported, not judged -- both are valid, but if `expand` is never
used on the "~별" cases (CV-AB-08, 10) the prompt is not steering it. CV-AB-08 expects
`leaves` of 가계신용 (all 14 bottom-level values).

Why a script instead of something Claude ran: the HCX key is a personal
credential. It is read only from the CLOVASTUDIO_API_KEY environment variable
at run time, never printed, never written to the output file (the output holds
queries, model answers and results only).

Setup (Windows PowerShell, from the project root):

    $sec = Read-Host "HCX API key" -AsSecureString
    $env:CLOVASTUDIO_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

Usage:

    # no key, no network: checks the script itself
    python scripts/b/b_part/verify_expand_hcx.py --dry-run
    # default 8 cases, 1 run each
    python scripts/b/b_part/verify_expand_hcx.py --allow-external
    python scripts/b/b_part/verify_expand_hcx.py --allow-external CV-AB-08 CV-AB-17 --repeat 3
    # same cases with expand switched off for every question (also CV-AB-08, 10)
    python scripts/b/b_part/verify_expand_hcx.py --allow-external --baseline

Default cases: CV-AB-04, 05 (parent + children), 08 (leaves), 10, 17, 20
(children), and CV-AB-01, 16 as plain controls. One HCX call per table per
run, so about 8 calls with the defaults. Output: eval/results/expand_hcx_verify.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))  # allow running without installing the package

from claview.b.contracts import Config, StageFailure, Trace, today_iso  # noqa: E402
from claview.b.evaluation import scripted_answers, write_json  # noqa: E402
from claview.b.metadata import MetadataStore  # noqa: E402
from claview.b.models import HcxModel, ScriptedModel, check_output  # noqa: E402
from claview.b.stages import (  # noqa: E402
    EXPAND_PROMPT,
    PICK_CORRECTION_REASONS,
    BSteps,
    Services,
    selection_key,
)

DEFAULT_CASES = [
    "CV-AB-04",
    "CV-AB-05",
    "CV-AB-08",
    "CV-AB-10",
    "CV-AB-17",
    "CV-AB-20",
    "CV-AB-01",
    "CV-AB-16",
]
CATALOG = ROOT / "data" / "catalog" / "search_catalog.json"
METADATA = ROOT / "data" / "raw" / "boK_meta_raw.jsonl"
GOLDEN = ROOT / "eval" / "cases" / "golden" / "golden_set_v1.json"
OUTPUT = ROOT / "eval" / "results" / "expand_hcx_verify.json"


class BaselineModel:
    """The pre-expand setup: no `expand` in the schema, no expand block in the prompt.

    Answers get `expand: none` added so the unchanged downstream code accepts them.
    Comparing a --baseline run with a normal run separates what the expand schema
    changed from mistakes the model already made without it.
    """

    def __init__(self, inner):
        self.inner = inner

    def complete(self, task, system, context, schema, max_tokens, trace):
        plain = copy.deepcopy(schema)
        row = plain["properties"]["selections"]["items"]
        row["properties"].pop("expand", None)
        row["required"] = [k for k in row["required"] if k != "expand"]
        system = system.replace(EXPAND_PROMPT, "")
        raw = self.inner.complete(task, system, context, plain, max_tokens, trace)
        for sel in raw.get("selections", []) if isinstance(raw, dict) else []:
            sel.setdefault("expand", {axis: "none" for axis in sel.get("dimensions", {})})
        return raw


class RecordingModel:
    """Wraps a model; keeps every raw answer and its schema verdict, and waits out 429s.

    HCX answers HTTP 429 when calls come too fast. The library only retries briefly,
    so this wrapper backs off for longer (10s, 20s, 40s) before giving up.
    """

    BACKOFF = (10, 20, 40)

    def __init__(self, inner, delay: float = 0.0):
        self.inner, self.delay, self.calls = inner, delay, []

    def complete(self, task, system, context, schema, max_tokens, trace):
        offered = "expand" in schema["properties"]["selections"]["items"]["properties"]
        record = {
            "task": task,
            "raw": None,
            "schema_ok": None,
            "error": None,
            "expand_offered": offered,
        }
        self.calls.append(record)
        if self.delay:
            time.sleep(self.delay)
        waits = list(self.BACKOFF)
        while True:
            try:
                raw = self.inner.complete(task, system, context, schema, max_tokens, trace)
                break
            except StageFailure as exc:
                if exc.code == "hcx_http_429" and waits:
                    wait = waits.pop(0)
                    print(f"        (HTTP 429 - waiting {wait}s and retrying)")
                    time.sleep(wait)
                    continue
                record["error"] = exc.code
                raise
        record["raw"] = raw
        try:
            check_output(raw, schema)
            record["schema_ok"] = True
        except StageFailure:
            record["schema_ok"] = False
        return raw


def run_b(case, store, config, model, delay=0.0):
    recorder = RecordingModel(model, delay)
    services = Services(store, search=None, model=recorder, config=config, trace=Trace())
    steps = BSteps(services)
    state = {"query": case["query"], "a_result": case["expected"]["A"]}
    state.update(steps.load(state))
    while "stage_outcome" not in state:
        for step in (steps.context, steps.select, steps.validate):
            state.update(step(state))
            if "stage_outcome" in state:
                break
    return state["stage_outcome"], recorder.calls


def golden_keys(case):
    return {
        selection_key(
            {**s, "dimensions": {a: {"code": v["code"]} for a, v in s["dimensions"].items()}}
        )
        for s in case["expected"]["B"]["selections"]
    }


def describe(key):
    _org, table, item, dims, _freq, start, end = key
    return f"{table}/{item}/" + ",".join(f"{a}={c}" for a, c in dims) + f"/{start}~{end}"


def extra_notes(result):
    notes = f" expand_offered={result['expand_offered']}"
    if result["expand_lowered"]:
        notes += f" lowered={result['expand_lowered']}"
    if result["expand_on_leaf"]:
        notes += f" expand_on_bottom_level={result['expand_on_leaf']}"
    if result["expand_promoted"]:
        notes += f" promoted_to_leaves={result['expand_promoted']}"
    if result["expand_named"]:
        notes += f" named_children_only={result['expand_named']}"
    if result["expand_repaired"]:
        notes += f" repaired_unused_split={result['expand_repaired']}"
    if result["named_value"]:
        notes += f" corrected_to_named_value={result['named_value']}"
    return notes


def notice_count(outcome, reason):
    """Notices with this reason (a tuple of reasons counts any of them)."""
    if outcome["status"] != "ok":
        return None
    reasons = reason if isinstance(reason, tuple) else (reason,)
    notices = outcome["result"].get("expand_notices", [])
    return sum(n["reason"] in reasons for n in notices)


def model_rows(calls):
    """One short line per selection the model returned: axis codes plus any expand mode."""
    lines = []
    for call in calls:
        for sel in (call["raw"] or {}).get("selections", []):
            dims = ",".join(f"{a}={c.split('.')[-1]}" for a, c in sel.get("dimensions", {}).items())
            modes = {a: m for a, m in (sel.get("expand") or {}).items() if m != "none"}
            period = sel.get("period", {})
            lines.append(
                f"{dims} {period.get('frequency')}:{period.get('start')}~{period.get('end')}"
                + (f" expand={modes}" if modes else "")
            )
    return lines


def judge(case, outcome, calls):
    rows = outcome["result"]["selections"] if outcome["status"] == "ok" else []
    got = {selection_key(r) for r in rows}
    want = golden_keys(case)
    used = {}
    for call in calls:
        for sel in (call["raw"] or {}).get("selections", []):
            for axis, mode in (sel.get("expand") or {}).items():
                if mode != "none":
                    used[mode] = used.get(mode, 0) + 1
    return {
        "case_id": case["case_id"],
        "query": case["query"],
        "status": outcome["status"],
        "issue": outcome["issues"][0]["code"] if outcome["issues"] else None,
        # None when no model answer arrived (HTTP error); only a real answer can violate the schema
        "schema_ok": (
            all(c["schema_ok"] for c in answered)
            if (answered := [c for c in calls if c["raw"]])
            else None
        ),
        "errors": [c["error"] for c in calls if c["error"]],
        "model_calls": len(calls),
        "expand_used": used,
        # whether the model was shown the expand schema/prompt (only for breakdown questions)
        "expand_offered": any(c["expand_offered"] for c in calls),
        # expand modes the code lowered to none because the question had no breakdown wording
        "expand_lowered": notice_count(outcome, "no_breakdown_wording"),
        # the model asked to expand a bottom-level value: code kept the value itself (none)
        "expand_on_leaf": notice_count(outcome, "expand_on_bottom_level_value"),
        # children the code raised to leaves because the question said "~별" without a depth
        "expand_promoted": notice_count(outcome, "split_by_wording_all_levels"),
        # the question named 2+ children of the expanded value: only those were selected
        "expand_named": notice_count(outcome, "query_names_children"),
        # the model never expanded although the question named an axis ("지역별"): code expanded it
        "expand_repaired": notice_count(outcome, "split_axis_not_expanded"),
        # the code replaced the model's value after checking it against the names in the question
        "named_value": notice_count(outcome, PICK_CORRECTION_REASONS),
        # more than one answer for a single table = B re-asked after an unusable expand
        "retried": len(calls) > len(case["expected"]["A"]["tables"]),
        "model_row_count": sum(len((c["raw"] or {}).get("selections", [])) for c in calls),
        "series_got": len(got),
        "series_expected": len(want),
        "missing": sorted(describe(k) for k in want - got),
        "extra": sorted(describe(k) for k in got - want),
        "pass": outcome["status"] == "ok" and got == want,
        "relations": len(outcome["result"].get("relations", []))
        if outcome["status"] == "ok"
        else None,
        "model_rows": model_rows(calls),
        "raw_model_answers": [c["raw"] for c in calls],
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("cases", nargs="*", help="case ids (default: the 8 listed above)")
    p.add_argument("--allow-external", action="store_true", help="required for real HCX calls")
    p.add_argument(
        "--dry-run", action="store_true", help="replay golden answers; no key, no network"
    )
    p.add_argument("--repeat", type=int, default=1, help="runs per case (default 1)")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    p.add_argument(
        "--baseline",
        action="store_true",
        help="run WITHOUT the expand schema/prompt (old setup) to see pre-existing model mistakes",
    )
    p.add_argument(
        "--delay", type=float, default=3.0, help="seconds to wait before each HCX call (default 3)"
    )
    p.add_argument(
        "--max-context-chars",
        type=int,
        default=None,
        help="B's request-size limit in characters (default: Config.max_b_context_chars, 120000). "
        "Tables above it are pruned to the candidates the question names. Lower it to probe "
        "HTTP 429 on large requests",
    )
    p.add_argument(
        "--full-context",
        action="store_true",
        help="send the table as stored (with `path`) instead of the slimmed copy, to compare "
        "the old request format against the new one (Config.compact_model_context=False)",
    )
    p.add_argument("--output", type=Path, default=OUTPUT)
    args = p.parse_args()

    if not args.dry_run:
        if not args.allow_external:
            p.error("Real HCX calls require --allow-external (or use --dry-run)")
        if not os.environ.get("CLOVASTUDIO_API_KEY"):
            p.error("CLOVASTUDIO_API_KEY is not set. See this script's docstring for setup steps.")
    ids = args.cases or DEFAULT_CASES
    cases = {c["case_id"]: c for c in json.loads(GOLDEN.read_text(encoding="utf-8-sig"))["cases"]}
    unknown = [i for i in ids if i not in cases]
    if unknown:
        p.error("Unknown case id(s): " + ", ".join(unknown))

    store = MetadataStore(CATALOG, METADATA)
    as_of = args.as_of or today_iso()
    options = {"compact_model_context": not args.full_context}
    if args.max_context_chars:
        options["max_b_context_chars"] = args.max_context_chars
    config = Config(as_of=as_of, **options)
    model = None if args.dry_run else HcxModel(os.environ["CLOVASTUDIO_API_KEY"])
    results = []
    try:
        mode_label = "dry-run (replay)" if args.dry_run else "LIVE HCX-007"
        mode_label += " [BASELINE: no expand]" if args.baseline else ""
        mode_label += " [FULL CONTEXT: table as stored]" if args.full_context else ""
        print(f"{mode_label}: {len(ids)} case(s) x {args.repeat} run(s)")
        for case_id in ids:
            for run in range(1, args.repeat + 1):
                case = cases[case_id]
                inner = ScriptedModel(scripted_answers(case)) if args.dry_run else model
                if args.baseline:
                    inner = BaselineModel(inner)
                outcome, calls = run_b(
                    case, store, config, inner, 0.0 if args.dry_run else args.delay
                )
                result = {**judge(case, outcome, calls), "run": run}
                results.append(result)
                flag = "PASS" if result["pass"] else "FAIL"
                print(
                    f"[{flag}] {case_id} run{run}: status={result['status']}"
                    f"{'(' + result['issue'] + ')' if result['issue'] else ''} "
                    f"series {result['series_got']}/{result['series_expected']} "
                    f"schema_ok={result['schema_ok']} expand_used={result['expand_used'] or '-'}"
                    f"{extra_notes(result)}"
                    f"{' RETRIED' if result['retried'] else ''}"
                )
                if not result["pass"]:
                    for line in model_rows(calls)[:6]:
                        print(f"        model: {line}")
                for tag in ("missing", "extra"):
                    for item in result[tag][:5]:
                        print(f"        {tag}: {item}")
                    if len(result[tag]) > 5:
                        print(f"        {tag}: ... +{len(result[tag]) - 5} more")
    except StageFailure as exc:
        print("Stopped:", exc.code)
        sys.exit(2)
    finally:
        if model:
            model.close()

    passed = sum(r["pass"] for r in results)
    summary = {
        "mode": ("dry_run" if args.dry_run else "live_hcx_007")
        + ("_baseline" if args.baseline else "")
        + ("_full_context" if args.full_context else ""),
        "runs": len(results),
        "passed": passed,
        "schema_violations": sum(r["schema_ok"] is False for r in results),
        "no_answer_runs": sum(r["schema_ok"] is None for r in results),
        "expand_used_runs": sum(bool(r["expand_used"]) for r in results),
        "expand_offered_runs": sum(r["expand_offered"] for r in results),
        "expand_lowered_runs": sum(bool(r["expand_lowered"]) for r in results),
        "expand_on_leaf_runs": sum(bool(r["expand_on_leaf"]) for r in results),
        "expand_promoted_runs": sum(bool(r["expand_promoted"]) for r in results),
        "expand_named_runs": sum(bool(r["expand_named"]) for r in results),
        "expand_repaired_runs": sum(bool(r["expand_repaired"]) for r in results),
        "named_value_runs": sum(bool(r["named_value"]) for r in results),
        "retried_runs": sum(r["retried"] for r in results),
    }
    print(
        f"\n{passed}/{len(results)} passed; schema violations: {summary['schema_violations']}; "
        f"runs without an answer: {summary['no_answer_runs']}; "
        f"runs offered expand: {summary['expand_offered_runs']}; "
        f"runs that used expand: {summary['expand_used_runs']}; "
        f"expand lowered by the guard: {summary['expand_lowered_runs']}; "
        f"expand on bottom-level value kept as none: {summary['expand_on_leaf_runs']}; "
        f"children promoted to leaves: {summary['expand_promoted_runs']}; "
        f"named children only: {summary['expand_named_runs']}; "
        f"unused split repaired: {summary['expand_repaired_runs']}; "
        f"corrected to a name in the question: {summary['named_value_runs']}; "
        f"retried: {summary['retried_runs']}"
    )
    write_json(args.output, {"summary": summary, "results": results})
    print("Wrote", args.output)


if __name__ == "__main__":
    main()
