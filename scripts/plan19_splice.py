"""Offline A/P19 -> real B adapter/graph -> synthetic fetch integration.

Run export and b-check in separate processes: the new A package's import guard
must not be relaxed to import B. All outputs go to a new A-owned run directory.
No live client, credential loading, prompt retuning, or saved-response mutation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


def export(args):
    from claview_v3 import _guard
    _guard.install_network_block()
    from claview_v3.catalog.layer import load_layer
    from claview_v3.catalog.facets import catalog_version
    from claview_v3.contract.check import check_analysis, check_selections
    from claview_v3.development_reverify import (MANIFEST, ARCHIVE, LIVE, FrozenSource, call_file,
        current_input, reverify_call, store_hashes)
    from claview_v3.interpret.decompose_run import DecompositionConfig
    from claview_v3.interpret.pipeline import interpret_question
    from claview_v3.select.graph import select_tables
    from claview_v3.select.readings import reading_candidates

    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "states").mkdir()
    manifest, frozen = read(args.store / MANIFEST), read(args.baseline / "inputs.json")
    before = store_hashes(args.store)
    layer = load_layer(include_external_synonyms=False)
    version = catalog_version(layer)
    questions = {q["case_id"]: q for q in manifest["inputs"]}
    current = {cid: current_input(q["question"], layer) for cid, q in questions.items()}
    records, exports = [], []
    for task in manifest["tasks"]:
        cid = task["case_id"]
        q = questions[cid]
        archive = read(call_file(args.store / ARCHIVE, task["call_id"]))
        stored = read(call_file(args.store / LIVE, task["call_id"]))["primary_analysis"]
        record = reverify_call(task, q["question"], q["as_of"], archive, frozen[cid], current[cid],
                               manifest["catalog_version"], version, layer, stored)
        records.append(record)
        if record["status"] != "reverified":
            continue
        source = FrozenSource(archive["raw_response"], archive["request"]["input"],
                              archive["request"]["text"]["format"]["schema"])
        a = interpret_question(q["question"], layer, decomposition=DecompositionConfig(
            mode="gpt", execution_mode="replay", proposal_source=source, as_of=q["as_of"]))
        assert source.calls == 1 and not source.mismatch
        variants = [("primary", a)] + [(r["option"], r["analysis"]) for r in reading_candidates(a, layer)
                                      if r["point"] == "decomposition"]
        for label, analysis in variants:
            assert check_analysis(analysis) == [], check_analysis(analysis)
            before_analysis = copy.deepcopy(analysis)
            selected = select_tables(analysis, layer, q["as_of"], selection_mode="refutation")
            assert analysis == before_analysis
            assert check_selections(selected["selections"]) == [], check_selections(selected["selections"])
            name = hashlib.sha256((task["call_id"] + ":" + label).encode()).hexdigest() + ".json"
            state = {"query": q["question"], "as_of": q["as_of"], "analysis": analysis,
                     "selections": selected["selections"], "selection_groups": selected["selection_groups"]}
            write(args.out / "states" / name, state)
            exports.append({"file": name, "call_id": task["call_id"], "case_id": cid, "reading": label,
                            "source": analysis["decomposition"]["applied_source"],
                            "selections": len(selected["selections"]),
                            "bundles": sum("bundle" in s for s in selected["selections"])})
    if args.rules_probe:
        probe = read(args.rules_probe)
        a = interpret_question(probe["question"], layer, decomposition=DecompositionConfig(
            mode="rules", as_of=probe["as_of"]))
        assert check_analysis(a) == []
        selected = select_tables(a, layer, probe["as_of"], selection_mode="refutation")
        assert check_selections(selected["selections"]) == []
        assert any(len(s.get("bundle", {}).get("assignments", {})) > 1 for s in selected["selections"])
        name = "rules_multitable_probe.json"
        write(args.out / "states" / name, {"query": probe["question"], "as_of": probe["as_of"],
              "analysis": a, "selections": selected["selections"], "selection_groups": selected["selection_groups"]})
        exports.append({"file": name, "reading": "rules_multitable_probe", "source": "rules",
                        "selections": len(selected["selections"]),
                        "bundles": sum("bundle" in s for s in selected["selections"])})
    assert before == store_hashes(args.store)
    assert not _guard.NETWORK_ATTEMPTS
    assert not any(n == "claview" or n.startswith("claview.") for n in sys.modules)
    write(args.out / "calls.json", records)
    write(args.out / "exports.json", exports)
    summary = {"planned": len(records), "reverified": sum(r["status"] == "reverified" for r in records),
               "incompatible": sum(r["status"] == "incompatible" for r in records),
               "exported_readings": len(exports) - bool(args.rules_probe),
               "additional_rules_probes": int(bool(args.rules_probe)),
               "bundles": sum(x["bundles"] for x in exports),
               "model_calls": 0, "network_attempts": 0, "archive_unchanged": True}
    write(args.out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


def b_check(args):
    # Only the offline B integration process can import the B package. Its source
    # directory is selected explicitly, never the A checkout's legacy claview.
    sys.dont_write_bytecode = True  # the other owner's source folder stays read-only
    sys.path.insert(0, str(args.b_root / "src"))
    from claview_v3 import _guard
    _guard.install_network_block()
    from claview.b.adapter_v3 import to_b_request
    from claview.b.contract import check_request, check_scope_result
    from claview.b.run import run_b_request, period_plans
    from claview.contracts import Config, Trace
    from claview.fetch import fetch_data, _RESPONSE_PRD_SE
    from claview.metadata import MetadataStore, periods_between
    from claview.models import ScriptedModel
    from claview.services import Services
    from claview import stages
    from claview_v3.select.periods import month, metadata_months

    args.out.mkdir(parents=True, exist_ok=False)
    store = MetadataStore(args.b_root / "data/catalog/search_catalog.json", args.b_root / "data/raw/boK_meta_raw.jsonl")
    actual_b = Path(sys.modules["claview"].__file__).resolve()
    assert actual_b.is_relative_to((args.b_root / "src").resolve())
    seen = []
    original = stages.correct_to_named_value

    def corrected(*a, **kw):
        seen.append(bool(kw.get("structured")))
        return original(*a, **kw)
    stages.correct_to_named_value = corrected

    class Client:
        def __init__(self):
            self.requests = []

        def fetch(self, params, trace):
            self.requests.append(params)
            dims = {k.replace("objL", "C"): v for k, v in params.items() if k.startswith("objL")}
            return [{"ORG_ID": params["orgId"], "TBL_ID": params["tblId"], "ITM_ID": params["itmId"],
                     "PRD_SE": _RESPONSE_PRD_SE[params["prdSe"]], "PRD_DE": p, "DT": "1", **dims}
                    for p in periods_between(params["startPrdDe"], params["endPrdDe"], params["prdSe"])]

    def services(request):
        responses = {}
        for scope in request["scopes"]:
            for table in scope["tables"]:
                meta = store.get(table["org_id"], table["table_id"])
                row = {"item_id": next(iter(meta["items"])),
                       "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                       "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
                       "expand": {a: "none" for a in meta["dimensions"]}}
                responses.setdefault(f"B:{table['org_id']}:{table['table_id']}", []).append({"selections": [row]})
        return Services(store, None, ScriptedModel(responses), Config(as_of=request["as_of"]), Trace())

    rows, invalids = [], []
    negative_source = None
    for entry in read(args.input / "exports.json"):
        state = read(args.input / "states" / entry["file"])
        request = to_b_request(state)
        problems = check_request(request)
        assert problems == [] and not request.get("input_errors"), (entry, problems, request.get("input_errors"))
        for scope in request["scopes"]:
            source = next(s for s in state["selections"] if s["requirement_id"] == scope["scope_id"])
            if "bundle" in source:
                assert scope["bundle"] == source["bundle"]
            elif not source["signals"]["plan17"]["b_handoff_ready"]:
                assert "bundle" not in scope
            rid = scope["scope_id"]
            d = state["analysis"]["decomposition"]
            req = next(r for r in state["analysis"]["interpretation"]["requirements"] if r["id"] == rid)
            own = set(req["condition_ids"]) | set(req["shared_condition_ids"])
            conditions = {c["id"]: c for c in state["analysis"]["conditions"]}
            assert {c["id"] for c in scope["hints"]["conditions"]} == own
            for c in scope["hints"]["conditions"]:
                assert all(c[k] == conditions[c["id"]].get(k) for k in c)
            assert scope["hints"]["condition_fields"] == {
                cid: {k: v for k, v in d["condition_fields"][cid].items() if k != "applied"}
                for cid in own if cid in d["condition_fields"]}
            table_ids = {t["table_id"] for t in scope["tables"]}
            assert scope["hints"]["reference_candidates"] == [
                {**r, "fetch": False} for r in source["reference_candidates"] if r["table_id"] in table_ids]
            if state["analysis"]["decomposition"]["structure"] is not None:
                structure, sent = d["structure"], scope["hints"]["structure"]
                assert sent["version"] == structure["version"]
                assert sent["requirement"] == structure["requirements"][rid]
                assert sent["polarity"] == [p for p in structure["polarity"]
                                             if not p["requirement_ids"] or rid in p["requirement_ids"]]
                proposal_id = d["requirement_ids"].get(rid, rid)
                assert sent["condition_bindings"] == [b for b in d["bindings"] if b["scope"] == "question"
                    or (b["scope"] == "unresolved" and not b["requirement_ids"])
                    or proposal_id in b["requirement_ids"]]
                assert sent["confirmed_effects"] == []
        svc, client = services(request), Client()
        plans = {s["scope_id"]: period_plans(s, svc, request["as_of"]) for s in request["scopes"]}
        for scope in request["scopes"]:
            if "bundle" not in scope:
                continue
            for table in scope["tables"]:
                plan = plans[scope["scope_id"]][f"{table['org_id']}:{table['table_id']}"]
                assert plan["anchor"]["bundle_id"] == scope["bundle"]["bundle_id"]
                assigned = scope["bundle"]["assignments"][table["table_id"]]
                assert len(assigned) == 1
                assert all(assigned[0]["start"] <= p["start"] <= p["end"] <= assigned[0]["end"]
                           for p in plan["periods"]["covered_months"])
        start = len(seen)
        result = run_b_request(request, svc)
        for scope in result["scopes"]:
            assert check_scope_result(scope) == [], check_scope_result(scope)
            for series in scope["series"]:
                plan = plans[scope["scope_id"]][f"{series['org_id']}:{series['table_id']}"]
                assert series["period"] == plan["applied"]
        if entry["reading"] == "rules_multitable_probe":
            scope = next(s for s in request["scopes"] if len(s.get("bundle", {}).get("assignments", {})) > 1)
            returned = next(s for s in result["scopes"] if s["scope_id"] == scope["scope_id"])
            assigned, actual = scope["bundle"]["assignments"], {}
            for series in returned["series"]:
                p = series["period"]
                lo = metadata_months(p["frequency"], p["start"])[0]
                hi = metadata_months(p["frequency"], p["end"])[1]
                actual.setdefault(series["table_id"], set()).update(range(lo, hi + 1))
            wanted = {t: set(range(month(v[0]["start"]), month(v[0]["end"]) + 1)) for t, v in assigned.items()}
            assert actual == wanted  # no missing month or expansion into another table's assignment
            assert sum(map(len, actual.values())) == len(set().union(*actual.values()))
            union = set().union(*actual.values())
            assert len(union) == max(union) - min(union) + 1
        structured = state["analysis"]["decomposition"]["structure"] is not None
        assert all(v == structured for v in seen[start:])
        fetched = fetch_data({"selections": result["selections"]}, store, client, svc.trace)
        assert len(client.requests) == len(result["selections"])
        assert fetched["status"] in ("ok", "no_data"), fetched.get("issues")
        if structured and request["scopes"] and negative_source is None:
            negative_source = state
        rows.append({**entry, "scopes": [{"id": s["scope_id"], "status": s["status"], "issues": s["issues"]}
                                        for s in result["scopes"]],
                     "synthetic_model_calls": sum(e["event"] == "model_request" for e in svc.trace.events),
                     "synthetic_fetches": len(client.requests), "structured_checks": seen[start:],
                     "fetch_status": fetched["status"]})
        write(args.out / entry["file"], {"request": request, "result": result, "fetch": fetched})
    assert negative_source is not None, "no actual adopted model output reached B"
    for mutation in ("version", "model_null", "nonempty_effects", "null_without_reason"):
        bad = copy.deepcopy(negative_source)
        d = bad["analysis"]["decomposition"]
        if mutation == "version":
            d["structure"]["version"] = "unsupported"
        elif mutation == "nonempty_effects":
            d["structure"]["confirmed_effects"] = [{}]
        else:
            d["structure"] = None
            if mutation == "null_without_reason":
                d["applied_source"] = "rules_fallback"
                d.pop("structure_reason", None)
        request = to_b_request(bad)
        assert request.get("input_errors")
        if mutation == "nonempty_effects":
            assert "held empty" in json.dumps(request["input_errors"])
        svc = services(request)
        result = run_b_request(request, svc)
        assert not result["selections"]
        assert not any(e["event"] == "model_request" for e in svc.trace.events)
        invalids.append({"mutation": mutation, "blocked": True})
    assert not _guard.NETWORK_ATTEMPTS
    summary = {"readings": len(rows), "synthetic_model_calls": sum(r["synthetic_model_calls"] for r in rows),
               "synthetic_fetches": sum(r["synthetic_fetches"] for r in rows),
               "rejected_inputs": invalids, "real_calls": 0, "network_attempts": 0,
               "scope_statuses": {s: sum(x["status"] == s for r in rows for x in r["scopes"])
                                   for s in sorted({x["status"] for r in rows for x in r["scopes"]})},
               "rules_multitable_probes": sum(r["reading"] == "rules_multitable_probe" for r in rows),
               "limit": "Synthetic first-value B responses test wiring, not semantic value selection accuracy."}
    write(args.out / "rows.json", rows)
    write(args.out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="command", required=True)
    a = subs.add_parser("export")
    a.add_argument("--store", type=Path, required=True)
    a.add_argument("--baseline", type=Path, required=True)
    a.add_argument("--rules-probe", type=Path, help="Existing development record; rerun its question with rules only")
    b = subs.add_parser("b-check")
    b.add_argument("--input", type=Path, required=True)
    b.add_argument("--b-root", type=Path, required=True)
    for command in (a, b):
        command.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    (export if args.command == "export" else b_check)(args)


if __name__ == "__main__":
    main()
