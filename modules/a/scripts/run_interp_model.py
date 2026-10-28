"""Model step of question interpretation (2.6) on a sample of undecided questions (plan 06, 5-7).

Usage (repository root, prefix ``PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8``):
    modules/a/scripts/run_interp_model.py --set <questions.jsonl> [--per-template 3] [--seed 6060]
        [--mode dry|replay|live] [--allow-external] [--out runs/hcx_interp_20261019] [--max-billed 300]
        [--est-in 4000] [--resume-after-stop]

The set is a jsonl file of {id, template, question} rows (other fields are not read). Every question runs through
2.1-2.7 with the model off; questions whose rule path is undecided (``certain`` false) are sampled, at most
``--per-template`` per template with a fixed seed (``sample.json``).
- dry: no network (transports blocked). For every sampled question: points (kinds as ``kind:form``), payload,
  schema, character counts and a rough token estimate (``payloads.jsonl``), one full example request per point kind
  (``example_payloads.json``), summary.
- replay: no network. Replies come from the disk cache only (a missing one is recorded as missing).
- live: refused without ``--allow-external``. The key is read from the environment variable CLOVASTUDIO_API_KEY only
  and handed to the HCX client; it is never printed or written (a value with whitespace or control characters is
  refused without echoing it; an exception around the calls is shown by its class name only). Requests go through
  ``RunClient`` (disk cache, 429 deferred, transient retries, ledger, stop rules; billed requests and stop rules
  carry over between runs of one --out, a stopped folder needs --resume-after-stop; see
  modules/a/claview_a_legacy/interpret/model_calls). A question deferred by 429 keeps ``model_step: pending`` (note
  ``deferred_429``) and is sent again by a later run.
replay and live write ``analyses_off.jsonl`` and ``analyses_on.jsonl`` (id, question, full analysis), ``cases.jsonl``
and ``summary.json``. The questions of the set were used to tune the rules: results are not an independent test.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
KEY_ENV = "CLOVASTUDIO_API_KEY"


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Model step of question interpretation on a sample (plan 06).")
    p.add_argument("--set", required=True, type=Path, dest="set_path")
    p.add_argument("--per-template", type=int, default=3)
    p.add_argument("--seed", type=int, default=6060)
    p.add_argument("--mode", choices=("dry", "replay", "live"), default="dry")
    p.add_argument("--allow-external", action="store_true")
    p.add_argument("--out", type=Path, default=ROOT / "runs" / "hcx_interp_20261019")
    p.add_argument("--max-billed", type=int, default=300)  # at most 300, counted over every run of --out
    p.add_argument("--est-in", type=int, default=4000)
    p.add_argument("--resume-after-stop", action="store_true",
                   help="send again after a stop rule of an earlier run over --out (the billed cap still holds)")
    return p.parse_args(argv)


def read_set(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append({"id": str(row["id"]), "template": str(row.get("template") or ""), "question": row["question"]})
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "max": None, "total": 0}
    return {"n": len(values), "mean": round(statistics.mean(values), 1), "max": max(values),
            "total": round(sum(values), 1)}


def sample(rows: list[dict], layer, per: int, seed: int, interpret) -> tuple[list[dict], dict, dict]:
    """(sampled rows, model-off analyses by ID, counts): undecided questions, at most ``per`` per template."""
    off: dict[str, dict] = {}
    undecided: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        a = interpret(r["question"], layer)
        off[r["id"]] = a
        if not a["judgement"]["certain"]:
            undecided[r["template"]].append(r)
    rng = random.Random(seed)
    picked = []
    for template in sorted(undecided):
        pool = sorted(undecided[template], key=lambda r: r["id"])
        picked += sorted(rng.sample(pool, min(per, len(pool))), key=lambda r: r["id"])
    counts = {"questions": len(rows), "undecided": sum(len(v) for v in undecided.values()),
              "templates": len({r["template"] for r in rows}), "templates_with_undecided": len(undecided),
              "sample": len(picked)}
    return picked, off, counts


def dry(args, picked: list[dict], layer, prompt: str, prompt_sha: str, counts: dict, model_step, estimate) -> dict:
    rows, examples = [], []
    by_kind: dict[str, dict] = {}  # point kind (kind:form) -> the first request that holds one
    for r in picked:
        prep = model_step.prepare_question(r["question"], layer)
        kinds = [p.label for p in prep.points]
        row = {"id": r["id"], "template": r["template"], "points": len(prep.points), "kinds": kinds,
               "skip": prep.skip}
        if prep.payload is not None:
            pj = json.dumps(prep.payload, ensure_ascii=False)
            sj = json.dumps(prep.schema, ensure_ascii=False)
            row.update({"chars_payload": len(pj), "chars_schema": len(sj),
                        "chars_total": len(prompt) + len(pj) + len(sj),
                        "est_tokens_in": estimate(prompt) + estimate(pj) + estimate(sj),
                        "payload": prep.payload, "schema": prep.schema})
            for kind in kinds:
                if kind not in by_kind:
                    by_kind[kind] = {"kind": kind, "id": r["id"], "question": r["question"],
                                     "payload": prep.payload, "schema": prep.schema}
        rows.append(row)
    examples = [by_kind[k] for k in sorted(by_kind)]  # one example request per point kind
    write_jsonl(args.out / "payloads.jsonl", rows)
    (args.out / "example_payloads.json").write_text(json.dumps(
        {"prompt_sha256": prompt_sha, "system_prompt": prompt, "examples": examples}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    sent = [r for r in rows if "payload" in r]
    return {
        "mode": "dry", **counts, "would_call": len(sent),
        "skipped": dict(collections.Counter(r["skip"] for r in rows if r["skip"])),
        "points": stats([r["points"] for r in sent]),
        "point_kinds": dict(collections.Counter(k for r in sent for k in r["kinds"])),
        "chars_payload": stats([r["chars_payload"] for r in sent]),
        "chars_total": stats([r["chars_total"] for r in sent]),
        "est_tokens_in": stats([r["est_tokens_in"] for r in sent]),
        "prompt": {"sha256": prompt_sha, "chars": len(prompt), "est_tokens": estimate(prompt)},
        "example_ids": {e["kind"]: e["id"] for e in examples},
        "notes": ["Token estimates are rough: Hangul characters / 1.5 + other characters / 3.5 (prompt, payload and "
                  "schema). The first live requests report the real counts."],
    }


def calls(args, picked: list[dict], off: dict, layer, prompt_sha: str, counts: dict, key: str | None,
          interpret) -> dict:
    from claview_a_legacy.interpret.model_calls import RunClient
    from claview_a_legacy.reused.model import HcxClient

    inner = HcxClient(api_key=key) if args.mode == "live" else None
    key = None  # the client keeps it; nothing else does
    client = RunClient(mode=args.mode, cache_path=args.out / "cache.jsonl", ledger_path=args.out / "ledger.jsonl",
                       prompt_sha=prompt_sha, inner=inner, max_billed=args.max_billed, est_in=args.est_in,
                       resume_after_stop=args.resume_after_stop)
    on_rows, off_rows, cases = [], [], []
    for r in picked:
        client.case_id, client.last = r["id"], None
        try:
            on = interpret(r["question"], layer, model=client)
        except Exception as err:  # noqa: BLE001 - only the class name is shown: a message may hold request headers
            print(f"{r['id']}: model step raised {type(err).__name__}; the rule path is kept", file=sys.stderr)
            cases.append({"id": r["id"], "template": r["template"], "model_step": None,
                          "call": f"exception:{type(err).__name__}", "points": 0, "notes": []})
            off_rows.append({"id": r["id"], "question": r["question"], "analysis": off[r["id"]]})
            on_rows.append({"id": r["id"], "question": r["question"], "analysis": off[r["id"]]})
            continue
        j = on["judgement"]
        record = j.get("model") or {}
        cases.append({"id": r["id"], "template": r["template"], "model_step": j.get("model_step"),
                      "call": client.last or "no_call", "points": len(record.get("points") or []),
                      "notes": record.get("notes") or []})
        off_rows.append({"id": r["id"], "question": r["question"], "analysis": off[r["id"]]})
        on_rows.append({"id": r["id"], "question": r["question"], "analysis": on})
    write_jsonl(args.out / "analyses_off.jsonl", off_rows)
    write_jsonl(args.out / "analyses_on.jsonl", on_rows)
    write_jsonl(args.out / "cases.jsonl", cases)
    c = client.counts()
    answered = c["answered"]
    return {"mode": args.mode, **counts, "max_billed": args.max_billed, "est_in": args.est_in,
            "model_step": dict(collections.Counter(x["model_step"] for x in cases)),
            "deferred_429": sum(1 for x in cases if "deferred_429" in x["notes"]),  # pending, not failed
            "failed": sum(1 for x in cases if x["model_step"] == "failed"),
            "call_status": dict(collections.Counter(x["call"] for x in cases)),
            "calls": {k: c[k] for k in ("billed", "billed_before", "billed_total", "answered", "deferred",
                                        "cache_hits", "missing")},
            "tokens": {"in_total": c["tokens_in"], "out_total": c["tokens_out"],
                       "in_mean_this_run": round(c["tokens_in"] / answered, 1) if answered else None,
                       "first_tokens_in": c["first_tokens_in"]},
            "stop_reason": c["stop_reason"], "prompt_sha256": prompt_sha,
            "notes": ["429 replies are deferred: not failures, not billed; run again to fill them.",
                      "The set was used to tune the rules: not an independent test."]}


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    sys.path.insert(0, str(ROOT / "modules" / "a"))
    from claview_a_legacy.interpret.model_calls import MAX_BILLED_CAP
    from claview_a_legacy.reused.model import usable_api_key

    if not 1 <= args.max_billed <= MAX_BILLED_CAP:
        print(f"--max-billed must be 1..{MAX_BILLED_CAP} (billed requests of every run over --out add up; nothing "
              "was sent)", file=sys.stderr)
        return 2
    if args.mode == "live" and not args.allow_external:
        print("live mode sends requests to HCX-007; add --allow-external to allow it (nothing was sent)",
              file=sys.stderr)
        return 2
    key = None
    if args.mode == "live":
        raw = os.environ.get(KEY_ENV)
        if not raw:
            print(f"{KEY_ENV} is not set; stopping (nothing was sent)", file=sys.stderr)
            return 2
        key = usable_api_key(raw)
        raw = None
        if key is None:  # the value is never echoed
            print(f"{KEY_ENV} is not a usable key (empty, or whitespace or control characters inside); stopping "
                  "(nothing was sent)", file=sys.stderr)
            return 2
    try:
        return _run(args, key)
    except Exception as err:  # noqa: BLE001 - the class name only: a message may hold request headers
        print(f"run stopped: {type(err).__name__}", file=sys.stderr)
        return 1


def _run(args, key: str | None) -> int:
    from claview_a_legacy import _guard

    if args.mode != "live":
        _guard.install_network_block()  # dry and replay make no request
    from claview_a_legacy.catalog.layer import load_layer
    from claview_a_legacy.interpret import model_step
    from claview_a_legacy.interpret.model_calls import estimate_tokens
    from claview_a_legacy.interpret.pipeline import interpret_question

    t0 = time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = read_set(args.set_path)
    layer = load_layer(include_external_synonyms=False)
    picked, off, counts = sample(rows, layer, args.per_template, args.seed, interpret_question)
    counts.update({"set": args.set_path.name, "seed": args.seed, "per_template": args.per_template})
    (args.out / "sample.json").write_text(json.dumps(
        {**counts, "sample_ids": [r["id"] for r in picked],
         "rows": [{"id": r["id"], "template": r["template"], "question": r["question"]} for r in picked]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    prompt = model_step.prompt_text()
    prompt_sha = model_step.prompt_sha256()
    if args.mode == "dry":
        summary = dry(args, picked, layer, prompt, prompt_sha, counts, model_step, estimate_tokens)
    else:
        summary = calls(args, picked, off, layer, prompt_sha, counts, key, interpret_question)
    key = None
    summary["seconds"] = round(time.time() - t0, 1)
    summary["network_attempts_blocked"] = len(_guard.NETWORK_ATTEMPTS)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
