"""Actual A response/resume -> actual B adapter/graph -> synthetic fetch.

Separate process, explicit source paths, no credential configuration or live SDK.
The user answers below are synthetic test choices, never applied to user sessions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def code_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / "src").rglob("*.py"))}


def main(args):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    sys.path.insert(0, str(args.b_root / "src"))
    from claview.a import _guard
    _guard.install_network_block()
    from claview.b.adapter_v3 import to_b_request
    from claview.b.contract import (
        A_ASSUMPTION_VERSIONS,
        B_CONTRACT_VERSION,
        check_b_result,
        check_request,
        check_scope_result,
    )
    from claview.b.run import run_b_request
    from claview.b.contracts import Config, Trace
    from claview.b.fetch import _RESPONSE_PRD_SE, fetch_data
    from claview.b.metadata import MetadataStore, periods_between
    from claview.b.models import ScriptedModel
    from claview.b.services import Services

    from claview.a.agent.assumptions import VERSION
    from claview.a.agent.b_connection import AdapterPort, connect
    from claview.a.agent.session import resume_session
    from claview.a.catalog.layer import load_layer

    assert VERSION in A_ASSUMPTION_VERSIONS
    args.out.mkdir(parents=True, exist_ok=False)
    before = code_hashes(args.b_root)
    store = MetadataStore(args.b_root / "data/catalog/search_catalog.json", args.b_root / "data/raw/boK_meta_raw.jsonl")
    layer = load_layer(include_external_synonyms=False)
    counts = {"ports": 0, "synthetic_models": 0, "synthetic_fetches": 0}

    class Client:
        def fetch(self, params, trace):
            counts["synthetic_fetches"] += 1
            dims = {k.replace("objL", "C"): v for k, v in params.items() if k.startswith("objL")}
            return [{"ORG_ID": params["orgId"], "TBL_ID": params["tblId"], "ITM_ID": params["itmId"],
                     "PRD_SE": _RESPONSE_PRD_SE[params["prdSe"]], "PRD_DE": p, "DT": "1", **dims}
                    for p in periods_between(params["startPrdDe"], params["endPrdDe"], params["prdSe"])]

    class ActualB:
        a_assumptions_version = VERSION
        contract_version = B_CONTRACT_VERSION
        last_payload = None

        def __call__(self, payload):
            counts["ports"] += 1
            self.last_payload = copy.deepcopy(payload)
            holder = {}

            def run(request, services):
                # This runs only after the real adapter and request checks.
                responses = {}
                for scope in request["scopes"]:
                    for table in scope["tables"]:
                        meta = store.get(table["org_id"], table["table_id"])
                        row = {"item_id": next(iter(meta["items"])),
                               "dimensions": {a: next(iter(d["values"])) for a, d in meta["dimensions"].items()},
                               "period": {"frequency": next(iter(meta["periods"])), "start": "x", "end": "x"},
                               "expand": {a: "none" for a in meta["dimensions"]}}
                        responses.setdefault(f"B:{table['org_id']}:{table['table_id']}", []).append({"selections": [row]})
                services["b"] = Services(store, None, ScriptedModel(responses), Config(as_of=request["as_of"]), Trace())
                result = run_b_request(request, services["b"])
                counts["synthetic_models"] += sum(e["event"] == "model_request" for e in services["b"].trace.events)
                return result

            port = AdapterPort(adapter=to_b_request, check_request=check_request,
                check_scope_result=check_scope_result, check_result=check_b_result, run_b=run,
                fetch=lambda result, svc: fetch_data({"selections": result["selections"]}, store, Client(), svc["b"].trace),
                services=holder, contract_version=B_CONTRACT_VERSION, a_assumptions_version=VERSION)
            return port(payload)

    rows = []
    port = ActualB()
    for file in sorted(args.input.glob("*.json")):
        if file.name == "summary.json":
            continue
        state = read(file)
        original = copy.deepcopy(state)
        before_counts = dict(counts)
        turns = 0
        result = connect(state, port)
        while result["response"]["status"] == "awaiting_answer":
            turns += 1
            assert turns <= len(result["selections"]) + 1
            assert counts == before_counts  # waiting never called B
            question = result["response"]["question"]
            result = resume_session(result, {"request_id": result["request_id"],
                "question_version": result["question_version"], "question_id": question["question_id"],
                "option_id": question["options"][0]["option_id"]}, layer=layer, b_port=port)
        assert state == original
        assert result["analysis"]["conditions"] == original["analysis"]["conditions"]
        assert result["analysis"]["judgement"]["certain"] == original["analysis"]["judgement"]["certain"]
        assert result["response"]["decomposition"]["real_calls"] == 0
        errors = [r for r in result["response"]["b"]["requirements"] if r["status"] != "returned"]
        assert not errors, errors
        unchanged = dict(counts)
        connect(result, port)
        assert counts == unchanged  # completed scopes are reused, not queried twice
        rows.append({"file": file.name, "synthetic_answer_turns": turns,
                     "a_status": result["response"]["status"], "execution_status": result["response"]["execution_status"],
                     "counts": {k: counts[k] - before_counts[k] for k in counts},
                     "scope_statuses": [s["status"] for r in result["response"]["b"]["requirements"]
                                        for s in r.get("result", {}).get("scopes", [])]})
        (args.out / file.name).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    # The public AdapterPort itself must invoke B's keyword-only assumption
    # validation. A pre-adapted lambda would hide the regression being tested.
    original_payload = copy.deepcopy(port.last_payload)
    assert original_payload is not None
    probes = []
    for invalid in ("unknown_requirement", "unknown_version", "unknown_field"):
        payload = copy.deepcopy(original_payload)
        if invalid == "unknown_requirement":
            row = copy.deepcopy(payload["a_assumptions"]["items"][0])
            row["requirement_ids"] = ["missing-requirement"]
            row["requirement_keys"] = [{"reading_id": "primary", "requirement_id": "missing-requirement"}]
            payload["a_assumptions"]["items"] = [row]
        elif invalid == "unknown_version":
            payload["a_assumptions"]["version"] = "unsupported"
        else:
            payload["a_assumptions"]["extra"] = True
        before_counts = dict(counts)
        try:
            port(payload)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid assumptions reached B")
        assert counts["synthetic_models"] == before_counts["synthetic_models"]
        assert counts["synthetic_fetches"] == before_counts["synthetic_fetches"]
        probes.append({"case": invalid, "blocked_before_model_fetch": True})
    empty = copy.deepcopy(original_payload)
    empty["a_assumptions"] = {"version": VERSION, "items": []}
    empty_result = port(empty)
    assert empty_result["a_assumptions"] == empty["a_assumptions"]
    probes.append({"case": "empty_assumptions", "accepted": True})
    after = code_hashes(args.b_root)
    assert before == after, "B changed during the probe; freeze and repeat before claiming completion"
    assert not _guard.NETWORK_ATTEMPTS
    summary = {"rows": rows, "adapter_probes": probes, "counts": counts, "real_calls": 0, "network_attempts": 0,
               "b_contract": B_CONTRACT_VERSION, "b_code_stable": True, "b_file_hashes": after,
               "note": "Saved A responses, synthetic user/B model/fetch; not model or value accuracy"}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "b_file_hashes"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--b-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    main(parser.parse_args())
