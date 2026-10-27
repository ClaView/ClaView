"""Plan 19 contract joint: compare an offline re-verification before the kept structure with one after it, call by call,
leaving the new structure fields out (no model, no network; files only read).

    python modules/a/scripts/plan19_structure_diff.py --before runs/plan19/reverify_stage3 --after runs/plan19/reverify_structure

``--before`` predates the structure fields, so its ``analysis_digest`` is the digest without them; ``--after`` records
``analysis_digest_without_structure``. Per call: the status in both runs; for a call re-verified in both, whether the
analysis without the structure fields is the same and which outcome fields differ. A difference here is not caused by
the structure fields (they are left out): it comes from other changes between the two runs (plan 19 stages 4-5 changed
the inputs; an incompatible call is counted, never dropped). With ``--out`` the result is also written to a new file.
"""

import argparse
import json
import sys
from pathlib import Path

FIELDS = ("applied_source", "fallback_reason", "verify_outcome", "alternative", "fallback_codes", "issue_codes",
          "certain", "failed_conditions", "status", "requirement_count", "certainty", "rule_checks")


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(before: list[dict], after: list[dict]) -> dict:
    old = {r["call_id"]: r for r in before}
    status_pairs: dict[str, int] = {}
    same, changed, missing = [], [], []
    for r in after:
        b = old.get(r["call_id"])
        if b is None:
            missing.append(r["call_id"])
            continue
        key = f"{b['status']}->{r['status']}"
        status_pairs[key] = status_pairs.get(key, 0) + 1
        if b["status"] != "reverified" or r["status"] != "reverified":
            continue
        if b.get("analysis_digest") == r.get("analysis_digest_without_structure"):
            same.append(r["call_id"])
            continue
        bo, ao = b.get("outcome") or {}, r.get("outcome") or {}
        changed.append({"call_id": r["call_id"], "case_id": r["case_id"], "profile_id": r["profile_id"],
                        "repeat": r["repeat"], "fields": {k: [bo.get(k), ao.get(k)] for k in FIELDS
                                                          if bo.get(k) != ao.get(k)}})
    after_reasons: dict[str, int] = {}
    for r in after:
        for x in r.get("reasons") or []:
            after_reasons[x] = after_reasons.get(x, 0) + 1
    states: dict[str, int] = {}
    for r in after:
        st = (r.get("structure") or {}).get("state")
        if st is not None:
            states[st] = states.get(st, 0) + 1
    return {"calls": {"before": len(before), "after": len(after), "missing_in_before": missing},
            "status_pairs": status_pairs, "after_incompatible_reasons": after_reasons,
            "reverified_in_both": len(same) + len(changed),
            "same_without_structure": len(same), "changed_without_structure": len(changed),
            "changed": changed, "after_structure_states": states,
            "after_structure_problems": sum((r.get("structure") or {}).get("problems", 0)
                                            + (r.get("structure") or {}).get("alternative_problems", 0)
                                            for r in after)}


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
