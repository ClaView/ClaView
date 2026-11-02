"""Model comparison on the golden questions (plan 40; user 2026-10-28 18:35 UTC). Not an independent evaluation.

- ``make_manifest``: the fixed conditions of one comparison run.
  - What is run: the models (exact ids and settings), the case lists per standard, and the repeated cases.
  - The schedule is fixed before the run. Every case runs once on every model. Then the repeated cases run twice more
    on every model. For each case and round the model order comes from a seeded shuffle, so no model always runs
    first.
  - Call limits: the whole run, each model, and zero retries.
  - The conditions of each standard part, as ``live.conditions`` gives them.
  - ``approval.live_calls`` is false; only the user sets it true.
- ``gates``: the manifest against the code and files as they are now; any difference refuses the run.
- ``run``: the schedule in order, one new independent request per entry. No earlier answer goes into a later
  request.
  - Each call is archived as it happens, keyed ``model|round|case``.
  - Stops on: a reliability failure, a changed condition before an entry, more than one call for an entry, or a
    limit reached. What ran is kept and nothing is re-run.
  - A question's own failure (judge error, refused output, failed check) is recorded and the run goes on.
- ``summarize``: kept apart for each model.
  - The first round of all cases, scored with the same standards and evaluator as single runs.
  - The repeated cases, per case: right in 3/3, 2/3, 1/3 or 0/3 rounds, or held/error, and whether the three
    decisions were the same. Repeats are never added into the first-round counts.
  - Tokens, cost (when prices are given in the manifest) and response time.
  - No weighted overall score.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules" / "a"))
sys.path.insert(0, str(ROOT / "modules" / "a" / "scripts"))

from claview_a import contract as K  # noqa: E402
from claview_a.graph import run as graph_run  # noqa: E402
from claview_a.judges import Archive, FakeJudge, LiveJudge, now, text_sha  # noqa: E402
from tl_eval import live as L  # noqa: E402
from tl_eval import report as R  # noqa: E402

COMPARE_VERSION = "tl-compare-2026-10-29.2"  # .2: the key from .env


def _standard(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def schedule(models: list[str], cases: list[str], repeat: list[str], extra_rounds: int, seed: int) -> list[dict]:
    """Round 1: every case on every model; rounds 2..: the repeated cases. Model order shuffled per case and round
    with ``seed`` (fixed in the manifest)."""
    rng = random.Random(seed)
    out = []
    for rnd, ids in [(1, cases)] + [(r, repeat) for r in range(2, 2 + extra_rounds)]:
        for cid in ids:
            order = list(models)
            rng.shuffle(order)
            out += [{"case_id": cid, "model": m, "round": rnd} for m in order]
    return out


def make_manifest(run_id: str, models: list[dict], parts: list[dict], repeat: list[str], extra_rounds: int = 2,
                  seed: int = 20261029, prices: dict | None = None) -> dict:
    """``models``: [{name, model, effort, max_output_tokens}]; ``parts``: [{standard: path, cases: [...]}] (each
    standard's own as_of is used); ``repeat``: case ids repeated ``extra_rounds`` more times on every model;
    ``prices``: optional {name: {input, cached_input, output}} per million tokens, recorded with their source."""
    names = [m["name"] for m in models]
    if len(set(names)) != len(names) or len({m["model"] for m in models}) != len(models):
        raise ValueError("model names or ids repeated")
    all_cases, part_rows = [], []
    for p in parts:
        std = _standard(p["standard"])
        known = {c["case_id"] for c in std["cases"]}
        if set(p["cases"]) - known or len(set(p["cases"])) != len(p["cases"]):
            raise ValueError(f"cases not in {Path(p['standard']).name} or repeated")
        part_rows.append({"standard": str(p["standard"]), "as_of": std["as_of"], "cases": list(p["cases"]),
                          "conditions": L.conditions(std["as_of"], Path(p["standard"]))})
        all_cases += p["cases"]
    if len(set(all_cases)) != len(all_cases) or set(repeat) - set(all_cases) or len(set(repeat)) != len(repeat):
        raise ValueError("a case in two parts, or a repeated case outside the run")
    plan = schedule(names, all_cases, list(repeat), extra_rounds, seed)
    per_model = len(all_cases) + extra_rounds * len(repeat)
    return {"run_id": run_id, "kind": "model_comparison", "version": COMPARE_VERSION, "created": now(),
            "models": [dict(m) for m in models], "parts": part_rows, "repeat": list(repeat),
            "extra_rounds": extra_rounds, "seed": seed, "schedule": plan, "schedule_sha256": text_sha(plan),
            "max_calls": len(plan), "max_calls_per_model": per_model, "retries": 0,
            "prices": prices, "concurrency": 1,
            "approval": {"live_calls": False, "approved_by": None, "approved_at": None,
                         "note": "only the user sets live_calls true, for this manifest's schedule and limits"}}


def gates(manifest: dict) -> list[dict]:
    rows = []
    for p in manifest["parts"]:
        now_c = L.conditions(p["as_of"], Path(p["standard"]))
        rows += [{"check": f"{Path(p['standard']).name}:{k}", "ok": now_c[k] == p["conditions"].get(k)} for k in now_c]
    plan = manifest["schedule"]
    names = [m["name"] for m in manifest["models"]]
    rows.append({"check": "schedule", "ok": text_sha(plan) == manifest["schedule_sha256"]})
    rows.append({"check": "call_limits", "ok": manifest["max_calls"] == len(plan) and manifest["retries"] == 0
                 and all(sum(1 for e in plan if e["model"] == n) == manifest["max_calls_per_model"] for n in names)})
    return rows


class RunStopped(RuntimeError):
    """A reliability failure: the run stops, nothing is re-run."""


def run(manifest: dict, mode: str, out_dir: Path, layer=None, transport=None) -> dict:
    """mode 'fake' (no network) or 'live' (only with the manifest approved and every gate passing)."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists: a run is never resumed or overwritten")
    key_source = None  # where a live run's key came from (never the key)
    checks = gates(manifest)
    if not all(r["ok"] for r in checks):
        raise RuntimeError(f"gates failed: {[r['check'] for r in checks if not r['ok']]}")
    if mode == "live":
        if manifest["approval"]["live_calls"] is not True:
            raise PermissionError("live calls are not approved in this manifest")
        if transport is None:
            from claview_a.transport.provider import OpenAITransport

            key_source = L.key_from_env_file()  # the team's .env, the one key source (user 2026-10-29 09:51 UTC)
            transport = OpenAITransport()
        transport.preflight()
        transport = L.guarded(transport, manifest["run_id"])[0]  # a 401/403 halts the key (plan 56 order 3)
    elif mode != "fake":
        raise ValueError(mode)
    if layer is None:
        from claview_a.catalog.layer import load_layer

        layer = load_layer()
    by_case, as_of_of = {}, {}
    for p in manifest["parts"]:
        std = _standard(p["standard"])
        for c in std["cases"]:
            if c["case_id"] in p["cases"]:
                by_case[c["case_id"]], as_of_of[c["case_id"]] = c, p["as_of"]
    out_dir.mkdir(parents=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    archive, ref = Archive(out_dir / "calls.jsonl"), {}
    models = {m["name"]: m for m in manifest["models"]}
    judges = {n: (LiveJudge(transport, manifest["max_calls_per_model"], archive, ref, credential_ref=L.CREDENTIAL)
                  if mode == "live" else FakeJudge(archive, ref)) for n in models}
    records, started, stopped, total, ledgers = {}, now(), None, 0, {}
    from tl_eval import score as S

    try:
        for e in manifest["schedule"]:
            key = f"{e['model']}|{e['round']}|{e['case_id']}"
            ref["case_id"] = key
            changed = [r["check"] for r in gates(manifest) if not r["ok"]]
            if changed:
                raise RunStopped(f"fixed conditions changed before {key}: {changed}")
            if total >= manifest["max_calls"]:
                raise RunStopped(f"call limit {manifest['max_calls']} reached before {key}")
            judge = judges[e["model"]]
            before = judge.calls
            m = models[e["model"]]
            settings = K.Settings(model=m["model"], effort=m["effort"], max_output_tokens=m["max_output_tokens"])
            ledgers[key] = ledger = L.entry_ledger(by_case[e["case_id"]]["question"])
            L.bind([judge], ledger)
            state = graph_run(by_case[e["case_id"]]["question"], layer, judge, settings, as_of_of[e["case_id"]],
                              ledger=ledger)
            used = judge.calls - before
            total += used
            if used > 1:
                raise RunStopped(f"{key}: {used} calls for one question")
            rec = S.adapt(state)
            rec["run_id"], rec["mode"] = manifest["run_id"], mode
            rec["model_name"], rec["round"] = e["model"], e["round"]
            records[key] = rec
    except Exception as ex:  # a reliability failure or a program fault: stop, keep what ran, re-raise
        stopped = {"entry": ref.get("case_id"), "error": type(ex).__name__, "detail": str(ex)[:300],
                   "records_kept": len(records)}
        raise
    finally:
        (out_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        run_row = {"run_id": manifest["run_id"], "mode": mode, "started": started, "finished": now(),
                   "stopped": stopped, "gates": checks, "calls": total, "max_calls": manifest["max_calls"],
                   "calls_per_model": {n: j.calls for n, j in judges.items()}, "key_source": key_source,
                   "ledgers": {k: v.summary()["by_slot"] for k, v in ledgers.items()}}
        (out_dir / "run.json").write_text(json.dumps(run_row, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = summarize(manifest, records, archive.rows, layer)
    if mode == "fake":
        summary["note"] = "fake judge: wiring check only, not a model result"
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**run_row, "summary": summary}


def _decision(rec: dict) -> list:
    """The decision of one run, kept as a sorted multiset with statistic ids left out. One element per statistic:
    its outcome, its final tables with frequency and months, and its asks with the axis and option tables."""
    out = []
    for f in (rec.get("final") or {}).values():
        tables = sorted((t["table_id"], t["frequency"], tuple((p["start"], p["end"]) for p in t.get("period") or []))
                        for t in f.get("tables") or [])
        asks = sorted((a["axis"], tuple(sorted(o.get("table_id") or "" for o in a.get("options") or [])))
                      for a in f.get("asks") or [])
        out.append((f["outcome"].removesuffix("_unverified"), tuple(tables), tuple(asks)))
    return sorted(out)


def _stability(marks: list) -> str:
    if any(m not in (True, False) for m in marks):
        return "held_or_error"
    return f"{sum(1 for m in marks if m)}/{len(marks)}"


def summarize(manifest: dict, records: dict, calls: list[dict], layer) -> dict:
    from claview_a.search.tables import selection_catalog

    views = selection_catalog(layer).views
    titles, cov = {t: v.title for t, v in views.items()}, R.coverage_of(views)
    out = {"version": COMPARE_VERSION, "models": {}, "repeat": {}, "differences": []}
    first: dict[str, dict[str, dict]] = {}
    for m in manifest["models"]:
        n = m["name"]
        results = []
        for p in manifest["parts"]:
            std = _standard(p["standard"])
            recs = {cid: records[f"{n}|1|{cid}"] for cid in p["cases"] if f"{n}|1|{cid}" in records}
            got = R.evaluate(recs, {**std, "cases": [c for c in std["cases"] if c["case_id"] in p["cases"]]},
                             titles, cov)
            results += got["results"]
        first[n] = {r["case_id"]: r for r in results}
        rows = [c for c in calls if str(c.get("case_id", "")).startswith(f"{n}|")]
        usage = [c["usage"] for c in rows if c.get("usage")]
        times = [c["elapsed_s"] for c in rows if c.get("elapsed_s") is not None]
        tokens = {"input": sum(u.get("input_tokens", 0) for u in usage),
                  "cached_input": sum((u.get("input_tokens_details") or {}).get("cached_tokens", 0) for u in usage),
                  "output": sum(u.get("output_tokens", 0) for u in usage),
                  "reasoning": sum((u.get("output_tokens_details") or {}).get("reasoning_tokens", 0) for u in usage)}
        price = (manifest.get("prices") or {}).get(n)
        cost = None
        if price:
            cost = round(((tokens["input"] - tokens["cached_input"]) * price["input"]
                          + tokens["cached_input"] * price.get("cached_input", price["input"])
                          + tokens["output"] * price["output"]) / 1_000_000, 4)
        out["models"][n] = {"model": m, "first_round": _first_round(results),
                            "calls": len(rows), "failed_calls": sum(1 for c in rows if c.get("outcome") == "failed"),
                            "tokens": tokens, "cost_estimate": cost,
                            "elapsed_s": {"sum": round(sum(times), 2),
                                          "median": round(statistics.median(times), 2) if times else None}}
    for cid in manifest["repeat"]:
        out["repeat"][cid] = {}
        for m in manifest["models"]:
            n = m["name"]
            rnds = range(1, 2 + manifest["extra_rounds"])
            keys = [f"{n}|{r}|{cid}" for r in rnds]
            marks = []
            for r, k in zip(rnds, keys):
                if k not in records:
                    marks.append("not_run")
                    continue
                if r == 1:
                    res = first[n].get(cid)
                else:
                    p = next(p for p in manifest["parts"] if cid in p["cases"])
                    std = _standard(p["standard"])
                    res = R.evaluate({cid: records[k]}, {**std, "cases": [c for c in std["cases"]
                                                                          if c["case_id"] == cid]},
                                     titles, cov)["results"]
                    res = res[0] if res else None
                marks.append((res or {}).get("completed", {}).get("right") if res else "unverified")
            decisions = [json.dumps(_decision(records[k]), ensure_ascii=False) for k in keys if k in records]
            out["repeat"][cid][n] = {"completed": marks, "stability": _stability(marks),
                                     "same_decision": len(set(decisions)) == 1 if decisions else None}
    names = [m["name"] for m in manifest["models"]]
    for cid in sorted({c for f in first.values() for c in f}):
        vals = {n: (first[n].get(cid) or {}).get("completed", {}).get("right") for n in names}
        if len(set(map(str, vals.values()))) > 1:
            out["differences"].append({"case_id": cid, "completed": vals,
                                       "labels": {n: (first[n].get(cid) or {}).get("labels") for n in names}})
    return out


def _first_round(results: list[dict]) -> dict:
    from tl_eval import score as S

    agg = S.aggregate(results)["all"]
    return {k: agg[k] for k in ("questions", "run_verified", "pool_hit", "contract_ok", "model_meaning_right",
                                 "evidence_contract_ok", "code_checks_right", "final_full", "completed",
                                 "requirement_targets", "ask_measures", "labels", "review")}
