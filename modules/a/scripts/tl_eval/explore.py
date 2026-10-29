"""Development exploration (plan 41; user 2026-10-29 06:55 UTC): single questions run once each under the frozen
version and recorded as observations. Not an evaluation: no accuracy over the list is ever computed.

- The list file (``version``, ``cases``) names each question as written in its source, its as_of, purpose, the
  items to observe, the answer state per item (confirmed / policy hold / insufficient evidence) and, where one
  exists, the standard that confirms some items. It is a fixed condition of the run like a standard.
- ``make_manifest``: the model (exact id and settings), one call per question, 0 retries, concurrency 1, the
  conditions per (as_of, standard) as ``live.conditions`` gives them, approval false. A limit is never carried over
  from another run.
- ``gates``: every condition, the list file, the schedule and the call limit as they are now.
- ``run``: fake (wiring only) or live (approved manifest, every gate passing before the run and before each call).
  Archives each call, never re-runs; records keyed ``model|1|case``.
- ``observe``: per question the model's statistics, quotes, splitters, frequencies, choices, asks, missing and
  evidence problems, code's reasons, warnings, corrections, final tables, frequency quotes and route, the
  scorer's own fact check, the condition diagnosis, and, for a question with a standard, that standard's verdicts
  on its confirmed items only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules" / "a"))
sys.path.insert(0, str(ROOT / "modules" / "a" / "scripts"))

from claview_a import contract as K  # noqa: E402
from claview_a.graph import research_summary  # noqa: E402
from claview_a.graph import run as graph_run  # noqa: E402
from claview_a.judges import Archive, FakeJudge, LiveJudge, now, text_sha  # noqa: E402
from tl_eval import live as L  # noqa: E402
from tl_eval import report as R  # noqa: E402

EXPLORE_VERSION = "tl-explore-2026-10-29.5"  # .2 re-judge model; .3 call gate; .4 key from .env; .5 key stop
ANSWER_STATES = ("confirmed", "policy_hold", "insufficient")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _list(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [c["case_id"] for c in data["cases"]]
    if len(set(ids)) != len(ids):
        raise ValueError("a question is listed twice")
    for c in data["cases"]:
        bad = {k: v for k, v in (c.get("answer_state") or {}).items() if v.get("state") not in ANSWER_STATES}
        if bad:
            raise ValueError(f"{c['case_id']}: answer state not one of {ANSWER_STATES}: {sorted(bad)}")
    return data


def _key(case: dict) -> str:
    return f"{case['as_of']}|{Path(case['standard']['path']).name if case.get('standard') else 'list'}"


def _conditions(list_path: Path, cases: list[dict]) -> dict:
    out = {}
    for c in cases:
        std = Path(c["standard"]["path"]) if c.get("standard") else Path(list_path)
        out.setdefault(_key(c), L.conditions(c["as_of"], std))
    return out


def make_manifest(run_id: str, model: dict, list_path, prices: dict | None = None,
                  research: dict | None = None) -> dict:
    """``model``: {name, model, effort, max_output_tokens}; ``prices``: optional {input, cached_input, output} per
    million tokens; ``research``: optional {model, cases, prices}: the re-judge model and the listed cases that may
    have one re-judgment each after a wholly missing first judgment (users 2026-10-29 07:51 and 08:18 UTC). The
    limits are fixed per case, per model and for the run."""
    list_path = Path(list_path)
    data = _list(list_path)
    plan = [{"case_id": c["case_id"], "model": model["name"], "round": 1} for c in data["cases"]]
    research = research or {}
    conditions = L.research_conditions(research.get("model"), research.get("cases") or ())
    if set(conditions["cases"]) - {e["case_id"] for e in plan}:
        raise ValueError("a re-judge case is not in the list")
    if conditions["model"] and conditions["model"]["name"] == model["name"]:
        raise ValueError("the re-judge model needs a name of its own")
    return {"run_id": run_id, "kind": "dev_exploration", "version": EXPLORE_VERSION, "created": now(),
            "model": dict(model), "list": {"path": str(list_path), "version": data["version"],
                                           "sha256": _sha(list_path)},
            "conditions": _conditions(list_path, data["cases"]), "schedule": plan, "schedule_sha256": text_sha(plan),
            "max_calls": len(plan) + conditions["max_rejudge_calls"], "retries": 0, "research": conditions,
            "max_calls_per_model": _per_model(model, plan, conditions),
            "max_calls_per_case": {e["case_id"]: 1 + (e["case_id"] in conditions["cases"]) for e in plan},
            "concurrency": 1, "prices": prices, "research_prices": research.get("prices"),
            "approval": {"live_calls": False, "approved_by": None, "approved_at": None,
                         "note": "only the user sets live_calls true, for this manifest's questions and call limit"}}


def _per_model(model: dict, plan: list[dict], research: dict) -> dict:
    out = {model["name"]: len(plan)}
    if research["enabled"]:
        out[research["model"]["name"]] = research["max_rejudge_calls"]
    return out


def gates(manifest: dict) -> list[dict]:
    list_path = Path(manifest["list"]["path"])
    rows = [{"check": "list", "ok": list_path.exists() and _sha(list_path) == manifest["list"]["sha256"]}]
    if rows[0]["ok"]:
        now_c = _conditions(list_path, _list(list_path)["cases"])
        for key, cond in manifest["conditions"].items():
            rows += [{"check": f"{key}:{k}", "ok": (now_c.get(key) or {}).get(k) == v} for k, v in cond.items()]
    plan = manifest["schedule"]
    rows.append({"check": "schedule", "ok": text_sha(plan) == manifest["schedule_sha256"]})
    research = L.research_of(manifest)
    ids = [e["case_id"] for e in plan]
    limits = manifest["max_calls"] == len(plan) + research["max_rejudge_calls"] and manifest["retries"] == 0 \
        and len(set(ids)) == len(plan)
    if "max_calls_per_model" in manifest:  # older manifests: one model, one call per question
        limits = limits and manifest["max_calls_per_model"] == _per_model(manifest["model"], plan, research) \
            and manifest["max_calls_per_case"] == {c: 1 + (c in research["cases"]) for c in ids}
    rows.append({"check": "call_limits", "ok": limits})
    rows.append({"check": "research", "ok": research == L.research_conditions(research["model"], research["cases"])
                 and set(research["cases"]) <= set(ids)})
    return rows


class RunStopped(RuntimeError):
    """A reliability failure: the run stops, nothing is re-run."""


def run(manifest: dict, mode: str, out_dir: Path, layer=None, transport=None) -> dict:
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
    elif mode != "fake":
        raise ValueError(mode)
    if layer is None:
        from claview_a.catalog.layer import load_layer

        layer = load_layer()
    cases = {c["case_id"]: c for c in _list(manifest["list"]["path"])["cases"]}
    out_dir.mkdir(parents=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    archive, ref = Archive(out_dir / "calls.jsonl"), {}
    m = manifest["model"]
    research = L.research_of(manifest)
    first_max = manifest["max_calls"] - research["max_rejudge_calls"]
    gate = L.CallGate(manifest)  # every call is checked right before it is sent (user 2026-10-29 09:04 UTC)
    if mode == "live":
        transport = L.KeyGuard(transport, gate)  # a 401/403 stops later sends (user 2026-10-29 10:40 UTC)
    judge = gate.judge(m["name"], LiveJudge(transport, first_max, archive, ref) if mode == "live"
                       else FakeJudge(archive, ref))
    settings = K.Settings(model=m["model"], effort=m["effort"], max_output_tokens=m["max_output_tokens"])
    rejudge = re_settings = budget = None
    if research["enabled"]:
        r = research["model"]
        rejudge = gate.judge(r["name"], LiveJudge(transport, research["max_rejudge_calls"], archive, ref)
                             if mode == "live" else FakeJudge(archive, ref))
        re_settings = K.Settings(model=r["model"], effort=r["effort"], max_output_tokens=r["max_output_tokens"])
        budget = L.RejudgeBudget(research, gate.total, lambda: gate.calls(r["name"]), manifest["max_calls"])
    total = gate.total

    records, started, stopped = {}, now(), None
    from tl_eval import score as S

    try:
        for e in manifest["schedule"]:
            key = f"{e['model']}|{e['round']}|{e['case_id']}"
            ref["case_id"] = key
            changed = [r["check"] for r in gates(manifest) if not r["ok"]]
            if changed:
                raise RunStopped(f"fixed conditions changed before {key}: {changed}")
            if gate.calls(m["name"]) >= first_max or total() >= manifest["max_calls"]:
                raise RunStopped(f"call limit {manifest['max_calls']} reached before {key}")
            re_name = research["model"]["name"] if rejudge else None
            before, before_re = gate.calls(m["name"]), gate.calls(re_name) if re_name else 0
            c = cases[e["case_id"]]
            listed = e["case_id"] in research["cases"]
            gate.for_case(e["case_id"])
            # the re-judge goes through the gate for every case: the gate, not a missing reference, refuses it for an
            # unlisted case
            state = graph_run(c["question"], layer, judge, settings, c["as_of"], research=listed,
                              research_budget=budget.for_case(e["case_id"]) if listed else None,
                              research_judge=rejudge, research_settings=re_settings)
            used, used_re = gate.calls(m["name"]) - before, (gate.calls(re_name) if re_name else 0) - before_re
            if used > 1 or used_re > (1 if listed else 0) or total() > manifest["max_calls"]:
                raise RunStopped(f"{key}: {used} first and {used_re} re-judge calls for one question")
            rec = S.adapt(state)
            rec.update(research_summary(state))
            rec.update(run_id=manifest["run_id"], mode=mode, model_name=e["model"], round=e["round"])
            records[key] = rec
    except Exception as ex:  # stop, keep what ran, re-raise
        stopped = {"entry": ref.get("case_id"), "error": type(ex).__name__, "detail": str(ex)[:300],
                   "records_kept": len(records)}
        raise
    finally:
        (out_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        row = {"run_id": manifest["run_id"], "mode": mode, "started": started, "finished": now(), "stopped": stopped,
               "gates": checks, "calls": total(), "max_calls": manifest["max_calls"],
               "calls_per_model": {m["name"]: gate.calls(m["name"]),
                                   **({research["model"]["name"]: gate.calls(research["model"]["name"])}
                                      if rejudge else {})},
               "rejudge_calls": gate.calls(research["model"]["name"]) if rejudge else 0,
               "refused_before_sending": list(gate.refused), "key_source": key_source,
               "research_ends": {k: r["research"]["end"] for k, r in records.items() if r.get("research")}}
        (out_dir / "run.json").write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"version": EXPLORE_VERSION, "note": "observations per question; no accuracy over the list",
               "questions": {k: observe(rec, cases[k.split("|")[2]], layer) for k, rec in records.items()},
               "usage": usage(archive.rows, manifest.get("prices")) if not rejudge else
               usage_by_model(archive.rows, {m["model"]: manifest.get("prices"),
                                             research["model"]["model"]: manifest.get("research_prices")})}
    if mode == "fake":
        summary["note"] += "; fake judge: wiring check only, not a model result"
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**row, "summary": summary}


def observe(rec: dict, case: dict, layer) -> dict:
    """What happened for one question, read off the record (and, where a standard exists, its confirmed items)."""
    from claview_a.search.tables import selection_catalog
    from tl_eval import score as S

    views = selection_catalog(layer).views
    titles = {t: v.title for t, v in views.items()}
    m = rec.get("model") or {}
    out = {"question": rec["question"], "as_of": rec["as_of"], "route": rec.get("route"), "error": rec.get("error"),
           "pool_tables": len(rec.get("pool") or []), "cards": [c["name"] for c in rec.get("cards") or []],
           "statistics": [(s["id"], s["quote"], s["name"]) for s in m.get("statistics", [])],
           "splitters": [(r["quote"], r["axis"], r["for"]) for r in m.get("splitters", [])],
           "periods": [(r["quote"], r["for"]) for r in m.get("periods", [])],
           "frequencies": [(r["quote"], r["for"]) for r in m.get("frequencies", [])],
           "calculations": [(r["quote"], r["for"]) for r in m.get("calculations", [])],
           "choices": [(c["for"], S._resolve(rec, c["table"]), [(e["kind"], e["text"]) for e in c["evidence"]])
                       for c in m.get("choices", [])],
           "asks": [(a["for"], a["axis"], [(o["value"], S._resolve(rec, o["table"])) for o in a["options"]])
                    for a in m.get("asks", [])],
           "missing": m.get("missing"), "alternatives": m.get("alternatives"),
           "final": {sid: {"outcome": f["outcome"], "tables": [(t["table_id"], t["frequency"], t["period"])
                                                              for t in f.get("tables") or []],
                           "asks": f.get("asks"), "frequency_quotes": f.get("frequency_quotes"),
                           "reasons": [(r["code"], r.get("detail")) for r in rec["checks"][sid]["reasons"]],
                           "warnings": [w["code"] for w in rec["checks"][sid]["warnings"]],
                           "citation": [p["code"] for p in rec["checks"][sid].get("citation") or []],
                           "corrections": [x["code"] for x in rec["corrections"].get(sid, [])]}
                     for sid, f in (rec.get("final") or {}).items()}}
    if rec.get("research"):
        out["research"] = {k: v for k, v in rec["research"].items() if k != "families_added"}
        if rec.get("first_round"):
            out["first_round"] = {k: rec["first_round"][k] for k in ("outcomes", "route", "payload_chars")}
            out["first_round"]["missing"] = (rec["first_round"]["model"] or {}).get("missing")
    if m:
        res = {"labels": [], "review": []}
        facts, checks = S.fact_checks(rec, titles, res)
        out["scorer_facts"] = {"contract": res["contract"]["ok"], "evidence": res["evidence_contract"]["ok"],
                               "errors": {k: sorted(v) for k, v in facts.items() if v}, "code_checks": checks}
        out["conditions"] = S.condition_details(rec)
    if case.get("standard") and m:
        std = json.loads(Path(case["standard"]["path"]).read_text(encoding="utf-8"))
        c = next(x for x in std["cases"] if x["case_id"] == case["case_id"])
        got = R.evaluate({c["case_id"]: rec}, {**std, "cases": [c]}, titles, R.coverage_of(views))["results"][0]
        out["standard_verdicts"] = {"standard": std["version"],
                                    **{k: (got.get(k) or {}).get("right") for k in
                                       ("model_meaning", "final_table_set", "final_full", "completed")}}
    return out


def usage_by_model(calls: list[dict], prices: dict) -> dict:
    """``usage`` per model id (read off each archived request), and the calls and cost summed."""
    by = {mid: usage([c for c in calls if (c.get("request") or {}).get("model") == mid], p)
          for mid, p in prices.items()}
    costs = [u["cost_from_usage_and_prices"] for u in by.values()]
    return {"by_model": by, "calls": len(calls),
            "cost_from_usage_and_prices": round(sum(costs), 4) if all(x is not None for x in costs) else None,
            "note": "cache writes priced as input (their price is not given)"}


def usage(calls: list[dict], prices: dict | None) -> dict:
    rows = [c["usage"] for c in calls if c.get("usage")]
    tokens = {"input": sum(u.get("input_tokens", 0) for u in rows),
              "cached_input": sum((u.get("input_tokens_details") or {}).get("cached_tokens", 0) for u in rows),
              "cache_write": sum((u.get("input_tokens_details") or {}).get("cache_write_tokens", 0) for u in rows),
              "output": sum(u.get("output_tokens", 0) for u in rows)}
    cost = None
    if prices and rows:
        cost = round(((tokens["input"] - tokens["cached_input"]) * prices["input"]
                      + tokens["cached_input"] * prices.get("cached_input", prices["input"])
                      + tokens["output"] * prices["output"]) / 1_000_000, 4)
    return {"calls": len(calls), "tokens": tokens, "cost_from_usage_and_prices": cost,
            "note": "cache writes priced as input (their price is not given)"}
