"""Plan 19 stage 3: compare two offline re-verification outputs call by call (no model, no network; files only read).

    python scripts/plan19_stage3_diff.py --before runs/plan19/reverify_current --after runs/plan19/reverify_stage3

Prints, per call whose re-verified analysis changed between the two runs, the outcome fields that differ (applied
source, verification outcome, alternative state, fallback and issue codes, certainty, failed conditions, status,
requirement count) and the issue codes added or removed. ``fatal_alternative``: the calls of ``--after`` adopted as
the model structure whose primary findings hold ``basis_merged`` -- the calls whose applied source would turn into a
rules fallback if ``data/decompose_policy.json`` ``basis_merge.effect`` were ``fatal`` (a basis merge found only in a
kept alternative would drop that alternative and keep the applied source; ``calls.json`` does not list alternative
findings, so those are not counted). With ``--out`` the result is also written to a new file.
"""

import argparse
import json
import sys
from pathlib import Path

FIELDS = ("applied_source", "fallback_reason", "verify_outcome", "alternative", "fallback_codes", "issue_codes",
          "certain", "failed_conditions", "status", "requirement_count")
BASIS_MERGED = "basis_merged"


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(before: list[dict], after: list[dict]) -> dict:
    old = {r["call_id"]: r for r in before}
    changed, missing = [], []
    for r in after:
        b = old.get(r["call_id"])
        if b is None:
            missing.append(r["call_id"])
            continue
        if b["status"] != r["status"] or b.get("analysis_digest") != r.get("analysis_digest"):
            bo, ao = b.get("outcome") or {}, r.get("outcome") or {}
            diff = {k: [bo.get(k), ao.get(k)] for k in FIELDS if bo.get(k) != ao.get(k)}
            changed.append({"call_id": r["call_id"], "case_id": r["case_id"], "profile_id": r["profile_id"],
                            "repeat": r["repeat"], "status": [b["status"], r["status"]], "fields": diff,
                            "issue_codes_added": sorted(set(ao.get("issue_codes") or []) - set(bo.get("issue_codes")
                                                                                             or [])),
                            "issue_codes_removed": sorted(set(bo.get("issue_codes") or []) - set(ao.get("issue_codes")
                                                                                               or [])),
                            "fallback_codes_added": sorted(set(ao.get("fallback_codes") or [])
                                                           - set(bo.get("fallback_codes") or []))})
    fatal = [r["call_id"] for r in after if (r.get("outcome") or {}).get("applied_source") == "model"
             and BASIS_MERGED in ((r.get("outcome") or {}).get("issue_codes") or [])]
    by_case: dict[str, int] = {}
    for r in after:
        if r["call_id"] in fatal:
            by_case[r["case_id"]] = by_case.get(r["case_id"], 0) + 1
    count = lambda rows, k: {v: sum((x.get("outcome") or {}).get(k) == v for x in rows)  # noqa: E731
                             for v in ("model", "rules_fallback")}
    return {"calls": {"before": len(before), "after": len(after), "missing_in_before": missing},
            "applied_source": {"before": count(before, "applied_source"), "after": count(after, "applied_source")},
            "changed": changed, "changed_count": len(changed),
            "fatal_alternative": {"applied_source_would_change": len(fatal), "calls": fatal, "by_case": by_case}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="a new file for the result (never overwritten)")
    args = parser.parse_args(argv)
    result = compare(read(args.before / "calls.json"), read(args.after / "calls.json"))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out is not None:
        with args.out.open("x", encoding="utf-8") as f:
            f.write(text)
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
