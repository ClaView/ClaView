"""Plan 19 stages 1-2: freeze the stored plan 18 batch and re-verify it offline. Never calls a model or the network.

Stage 1 (``baseline``) reads the stored batch (manifest, response archive, live and replay results) and writes, to a
new folder only: the call rows (call ID, profile, case, repeat, request and response hashes, archive/live/replay file
SHA-256), the stored input of every question (payload, schema, prompt and model settings as recorded; segment, mention
and condition IDs with spans), the sense_id -> catalog rows mapping with its digest and provenance, the stored
verification/settling outcome and selector input of every call, the rule result of every question, and the metrics
(strict as stored and recomputed; core meaning and table bundles from ``development_metrics``).

Provenance of the rows behind a sense ID: the payload carries only a sense's name, role and path, and the rows were not
written down at call time. They are rebuilt with the current ``build_senses`` from the mentions the manifest stored at
preparation (before the calls; their candidates are the raw rows). The mapping is ``reconstructed_matching_payload``
only when the current code regenerates the stored payload and schema exactly, the current catalog gives the same rows
as the stored mentions, and the stored payload labels every sense and every mention's sense list as the rebuilt
mapping does; anything else is ``provenance_unverified``. Either way it is a reconstruction, not call-time evidence.

Stage 2 (``reverify``) takes every stored response with the request input and schema exactly as stored. The current
code rebuilds the question's local data; when the regenerated payload, schema, rows mapping (against the frozen stage 1
digest) and catalog version all equal the stored ones and the mapping provenance is verified, the current
verification, settling and decomposition record run on the stored proposal (a replay-mode source over the stored
text; no model). Otherwise the call is ``incompatible`` with its reasons, or ``provenance_unverified``. No call is
dropped from the denominator or turned into a rules fallback; the archive is only read; results go to a new folder.
Plan 19 contract joint: a re-verified call also records its digest without the kept-structure fields (the stored
analyses predate them, so the stored comparison leaves them out) and a summary of that structure with its contract
state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import _guard, paths
from .catalog.facets import catalog_version
from .catalog.layer import load_layer
from .development import analysis_view, input_of, runtime_versions
from .development_metrics import (bundle_signature, core_repeat_report, core_signature, core_view,
                                  proposal_signature, repeat_report, selection_signature)
from .interpret.decompose_archive import ReplayMiss, canonical, digest
from .interpret.decompose_payload import build_payload, build_senses, prompt_text
from .interpret.decompose_provider import ProviderError, extract, versions
from .interpret.decompose_run import DecompositionConfig
from .interpret.pipeline import interpret_question

MANIFEST, ARCHIVE = "review_period_v02/manifest.json", "review_period_v02/archive"
LIVE, REPLAY = "live_period_v02", "replay_period_v02"
STORE_DIRS = ("review_period_v02", "live_period_v02", "replay_period_v02")
STORE_FILES = ("final_audit.json", "actual_audit.json", "manual_review.json", "input_audit.json")
EXPECT = paths.CASES_DIR / "requirement_expect_dev_mini.json"

RECONSTRUCTED, UNVERIFIED = "reconstructed_matching_payload", "provenance_unverified"
REVERIFIED, INCOMPATIBLE = "reverified", "incompatible"
STATUSES = (REVERIFIED, INCOMPATIBLE, UNVERIFIED)
COMPAT_KEYS = ("payload", "schema", "rows", "catalog")
MAPPING_SOURCE = "manifest inputs[].rules.mentions (stored at preparation, before the calls)"


# ---------------------------------------------------------------- reading the store (never written)


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path: Path, value) -> None:
    with Path(path).open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def store_hashes(store: Path) -> dict:
    """SHA-256 of every stored file this module reads (relative path -> hash), for the before/after check."""
    out = {}
    for name in STORE_DIRS:
        folder = store / name
        if folder.is_dir():
            for p in sorted(folder.rglob("*")):
                if p.is_file():
                    out[p.relative_to(store).as_posix()] = file_sha256(p)
    for name in STORE_FILES:
        if (store / name).is_file():
            out[name] = file_sha256(store / name)
    return out


def call_file(folder: Path, call_id: str) -> Path:
    return folder / (digest(call_id) + ".json")


def expected_counts() -> dict:
    return {c["case_id"]: c["count"] for c in read(EXPECT)["cases"] if isinstance(c.get("count"), int)}


def manifest_hash_valid(manifest: dict) -> bool:
    return manifest.get("approval_hash") == digest({k: v for k, v in manifest.items() if k != "approval_hash"})


def version_diff(old: dict, new: dict) -> dict:
    return {"changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
            "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys())}


# ---------------------------------------------------------------- sense rows mapping


def sense_map(senses: dict) -> dict:
    """sense ID -> name, role, path and every raw catalog row, in table order (the mapping whose digest is frozen)."""
    return {z: {"name": s["name"], "role": s["role"], "path": s["path"], "rows": [dict(r) for r in s["rows"]]}
            for z, s in senses.items()}


def mapping_digest(senses: dict) -> str:
    return digest(sense_map(senses))


def labels_match(payload: dict, table: dict) -> bool:
    """The payload names every sense (name, role, path unless shrunk) and every mention's sense list exactly as the
    rebuilt sense table does."""
    senses, kept = table["senses"], table["mention_kept"]
    paths_shrunk = "candidate_paths" in ((payload.get("truncation") or {}).get("shrunk") or [])
    per_mention: dict[str, list] = {}
    for row in payload["candidate_summary"]:
        ids = per_mention.setdefault(row["mention_id"], [])
        z = row["sense_id"]
        if z is None:
            continue
        s = senses.get(z)
        if s is None or row["name"] != s["name"] or row["role"] != s["role"]:
            return False
        if not (row["path"] == s["path"] or (paths_shrunk and row["path"] is None)):
            return False
        ids.append(z)
    return all(kept.get(mid, []) == ids for mid, ids in per_mention.items())


def compatibility(stored: dict, current: dict) -> list[str]:
    """Reasons (``COMPAT_KEYS`` order) a stored response cannot be re-verified on the current code; empty when the
    payload, schema, rows mapping and catalog version are all the same. A missing value is never compatible."""
    return [k for k in COMPAT_KEYS if stored.get(k) is None or stored.get(k) != current.get(k)]


# ---------------------------------------------------------------- stage 1: per question


def current_input(question: str, layer) -> dict | None:
    """The current code's rule analysis, payload, schema and local table of a question (None: no model input)."""
    try:
        rules, payload, schema = input_of(question, layer)
    except ValueError:
        return None
    _, local = build_payload(rules["text"], rules["conditions"], rules["segments"], rules["mentions"], layer)
    return {"rules": rules, "payload": payload, "schema": schema, "local": local}


def _spans(rules: dict) -> dict:
    segs = {s["segment_id"]: s for s in rules["segments"]}
    return {
        "segments": [{"id": s["segment_id"], "span": list(s["span"]), "kind": s["kind"], "quote": s["quote"]}
                     for s in rules["segments"]],
        "mentions": [{"id": m["mention_id"], "kind": m["kind"], "segment_ids": list(m["segment_ids"]),
                      "span": [segs[m["segment_ids"][0]]["span"][0], segs[m["segment_ids"][-1]]["span"][1]]
                      if m["segment_ids"] else None,
                      "condition_ids": list(m["condition_ids"]), "marker": m["marker"],
                      "candidates": len(m["candidates"])} for m in rules["mentions"]],
        "conditions": [{"id": c["id"], "kind": c["kind"], "quote": c["quote"], "span": list(c["span"]),
                        "value": c["value"]} for c in rules["conditions"]]}


def _rules_summary(rules: dict) -> dict:
    j, i = rules.get("judgement") or {}, rules.get("interpretation") or {}
    return {"digest": digest(rules), "certain": bool(j.get("certain")),
            "failed_conditions": list(j.get("failed_conditions") or []),
            "failed_sources_digest": digest(j.get("failed_sources") or []),
            "status": i.get("status"), "requirement_count": len(i.get("requirements") or []),
            "interpretation_digest": digest(i)}


def frozen_input(row: dict, layer, stored_catalog: str | None, current_catalog: str) -> tuple[dict, dict]:
    """(record, rows) of one stored question: what the manifest recorded, the rebuilt mapping with its provenance
    status and the current code's comparison. ``rows``: the frozen sense map (written apart, it is large)."""
    stored_rules, stored_payload, stored_schema = row["rules"], row["payload"], row["schema"]
    # the stored mentions and conditions, read by the current sense table (plan 19 stage 5: the order reads the
    # question's conditions); a table the call-time code built otherwise fails the payload checks (unverified)
    table = build_senses(stored_rules["mentions"], stored_rules.get("conditions"))
    frozen = sense_map(table["senses"])
    now = current_input(row["question"], layer)
    checks = {
        "payload_regenerated_equal": now is not None and digest(now["payload"]) == digest(stored_payload),
        "schema_regenerated_equal": now is not None and digest(now["schema"]) == digest(stored_schema),
        "rows_current_equal_stored_mentions": now is not None and mapping_digest(now["local"]["senses"]) == digest(frozen),
        "payload_labels_match_mapping": labels_match(stored_payload, table),
        "catalog_equal": stored_catalog is not None and stored_catalog == current_catalog,
        "rules_regenerated_equal": now is not None and digest(now["rules"]) == digest(stored_rules),
    }
    verified = all(checks[k] for k in ("payload_regenerated_equal", "schema_regenerated_equal",
                                       "rows_current_equal_stored_mentions", "payload_labels_match_mapping"))
    record = {
        "case_id": row["case_id"], "question": row["question"], "as_of": row["as_of"],
        "as_of_source": row.get("as_of_source"),
        "stored": {"payload_digest": digest(stored_payload), "schema_digest": digest(stored_schema),
                   "segments": len(stored_payload.get("segments") or []),
                   "senses_sent": len({r["sense_id"] for r in stored_payload.get("candidate_summary") or []
                                       if r["sense_id"] is not None}),
                   "truncation": stored_payload.get("truncation")},
        "ids": _spans(stored_rules),
        "senses": {z: {"name": s["name"], "role": s["role"], "path": s["path"], "rows": len(s["rows"]),
                       "rows_digest": digest(s["rows"]), "mention_ids": list(table["senses"][z]["mention_ids"])}
                   for z, s in frozen.items()},
        "mapping": {"status": RECONSTRUCTED if verified else UNVERIFIED, "source": MAPPING_SOURCE,
                    "recorded_at_call_time": False, "frozen_digest": digest(frozen),
                    "current_digest": mapping_digest(now["local"]["senses"]) if now else None, "checks": checks},
        "catalog_version": {"stored": stored_catalog, "current": current_catalog},
        "rules": {"stored": _rules_summary(stored_rules),
                  "current_equal": checks["rules_regenerated_equal"]},
    }
    return record, frozen


# ---------------------------------------------------------------- stage 1: per call


def _outcome(primary: dict) -> dict:
    """The verification and settling outcome of one primary analysis (stored or re-verified, same fields)."""
    d = primary.get("decomposition") or {}
    v = d.get("verify") or {}
    j, i = primary.get("judgement") or {}, primary.get("interpretation") or {}
    return {"applied_source": d.get("applied_source"), "fallback_reason": d.get("fallback_reason"),
            "error_class": d.get("error_class"), "verify_outcome": v.get("outcome"),
            "alternative": v.get("alternative"),
            "fallback_codes": sorted({x["code"] for x in v.get("fallback_reasons") or []}),
            "issue_codes": sorted({x["code"] for x in v.get("issues") or []}),
            "certain": bool(j.get("certain")), "failed_conditions": list(j.get("failed_conditions") or []),
            "status": i.get("status"), "requirement_count": len(i.get("requirements") or []),
            "certainty": d.get("certainty"),
            "rule_checks": [[c["condition"], c["detail"], c["outcome"]] for c in d.get("rule_checks") or []]}


def selector_input(analysis: dict) -> dict:
    """What the selector read for one call (the analysis after the router): requirements, judgement certainty and
    failure sources, and the decomposition's selection refs and bindings."""
    d = analysis.get("decomposition") or {}
    j = analysis.get("judgement") or {}
    keep = ("id", "indicator", "targets", "breakdown", "context", "condition_ids", "shared_condition_ids",
            "ambiguous_condition_ids", "status", "refs", "reading_id")
    return {"requirements": [{k: r.get(k) for k in keep} for r in (analysis.get("interpretation") or {}).get(
                "requirements") or []],
            "interpretation_status": (analysis.get("interpretation") or {}).get("status"),
            "relations": (analysis.get("interpretation") or {}).get("relations") or [],
            "certain": bool(j.get("certain")), "failed_sources": j.get("failed_sources") or [],
            "applied_source": d.get("applied_source"), "reading_id": d.get("reading_id"),
            "selection_refs": d.get("selection_refs") or {}, "bindings": d.get("bindings") or [],
            "requirement_ids": d.get("requirement_ids") or {}}


def archive_problems(row: dict | None, task: dict) -> list[str]:
    if row is None:
        return ["missing"]
    out = []
    if row.get("call_id") != task["call_id"]:
        out.append("call_id")
    if row.get("status") != "received" or not isinstance(row.get("raw_response"), str):
        out.append("status")
    elif row.get("response_hash") != digest(row["raw_response"]):
        out.append("response_hash")
    if row.get("request_hash") != digest(row.get("request")) or row.get("request_hash") != task["request_hash"]:
        out.append("request_hash")
    return out


def stored_proposal(row: dict) -> dict | None:
    try:
        value = json.loads(extract(row["raw_response"])[0])
    except (ProviderError, ValueError, TypeError, KeyError):
        return None
    return value if isinstance(value, dict) else None


def _read_optional(path: Path):
    return read(path) if path.is_file() else None


def frozen_call(task: dict, store: Path) -> tuple[dict, dict | None, dict | None]:
    """(row, archive row, live result) of one planned call."""
    a_path = call_file(store / ARCHIVE, task["call_id"])
    l_path, r_path = call_file(store / LIVE, task["call_id"]), call_file(store / REPLAY, task["call_id"])
    archive = _read_optional(a_path)
    live = _read_optional(l_path)
    request = (archive or {}).get("request") or {}
    fmt = ((request.get("text") or {}).get("format") or {})
    row = {"call_id": task["call_id"], "profile": task["profile"]["id"], "case_id": task["case_id"],
           "repeat": task["repeat"], "request_hash": task["request_hash"],
           "response_hash": (archive or {}).get("response_hash"),
           "archive_problems": archive_problems(archive, task),
           "archive_file": a_path.relative_to(store).as_posix(),
           "archive_sha256": file_sha256(a_path) if a_path.is_file() else None,
           "live_file": l_path.relative_to(store).as_posix(),
           "live_sha256": file_sha256(l_path) if l_path.is_file() else None,
           "replay_file": r_path.relative_to(store).as_posix(),
           "replay_sha256": file_sha256(r_path) if r_path.is_file() else None,
           "transport_kind": (archive or {}).get("transport_kind"),
           "settings": {"model": request.get("model"), "effort": (request.get("reasoning") or {}).get("effort"),
                        "max_output_tokens": request.get("max_output_tokens"), "store": request.get("store"),
                        "service_tier": request.get("service_tier"), "schema_name": fmt.get("name"),
                        "strict": fmt.get("strict")},
           "stored_input_digest": digest(json.loads(request["input"])) if "input" in request else None,
           "stored_schema_digest": digest(fmt["schema"]) if "schema" in fmt else None,
           "instructions_digest": digest(request["instructions"]) if "instructions" in request else None,
           "call_versions_digest": digest((archive or {}).get("versions")),
           "stored_outcome": _outcome(live["primary_analysis"]) if live else None,
           "router_outcome": _outcome(live["analysis"]) if live else None,
           "selection_route": live.get("selection_route") if live else None,
           "selections": [{"requirement_id": s["requirement_id"], "status": s["status"],
                           "tables": [[t.get("org_id"), t["table_id"]] for t in s["tables"]]}
                          for s in live["selections"]] if live else None,
           "selector_input_digest": digest(selector_input(live["analysis"])) if live else None}
    return row, archive, live


def signatures(row: dict, archive: dict | None, live: dict | None) -> dict:
    """Strict signatures as stored and recomputed, and the core and bundle signatures of one call."""
    out = {"structure_stored": None, "structure_recomputed": None, "selection_stored": None,
           "selection_recomputed": None, "core": None, "core_view_relations": 0, "requirement_count": None,
           "bundle": None, "model_adopted": False}
    proposal = stored_proposal(archive) if archive and not row["archive_problems"] else None
    payload = json.loads(archive["request"]["input"]) if proposal is not None else None
    if live is not None:
        primary = live["primary_analysis"]["decomposition"]
        out.update(structure_stored=live["structure_signature"], selection_stored=live["selection_signature"],
                   selection_recomputed=selection_signature(live["analysis"], live["selections"]),
                   bundle=bundle_signature(live["selections"]), model_adopted=primary["applied_source"] == "model")
        if out["model_adopted"] and proposal is not None:
            out["structure_recomputed"] = proposal_signature(
                proposal, alternative_kept=primary["verify"]["alternative"] == "kept")
    if proposal is not None:
        view = core_view(proposal, payload)
        out["core"] = core_signature(proposal, payload)
        out["core_view_relations"] = len(view["relations"]) if view else 0
        try:
            out["requirement_count"] = len(proposal["primary"]["requirements"])
        except (KeyError, TypeError):
            out["requirement_count"] = None
    return out


def metrics(tasks: list[dict], rows: list[dict], lives: dict, summary: dict | None) -> dict:
    """Strict agreement recomputed against the stored summary, plus core meaning and table bundles."""
    strict_rows = []
    for row in rows:
        live = lives.get(row["call_id"])
        if live is None:
            continue
        sig = row["signatures"]
        strict_rows.append({"profile_id": row["profile"], "case_id": row["case_id"], "repeat": row["repeat"],
                            "primary_analysis": {"decomposition": {
                                "applied_source": live["primary_analysis"]["decomposition"]["applied_source"]}},
                            "structure_signature": sig["structure_recomputed"],
                            "selection_signature": sig["selection_recomputed"], "selections": live["selections"]})
    strict = repeat_report(tasks, strict_rows)
    core_rows = [{"profile_id": r["profile"], "case_id": r["case_id"], "repeat": r["repeat"],
                  "model_adopted": r["signatures"]["model_adopted"], "core_signature": r["signatures"]["core"],
                  "requirement_count": r["signatures"]["requirement_count"],
                  "relation_count": r["signatures"]["core_view_relations"],
                  "bundle_signature": r["signatures"]["bundle"]} for r in rows if r["call_id"] in lives]
    stored = (summary or {}).get("repeat_agreement")
    return {"strict_recomputed": strict["overall"], "strict_profiles": strict["profiles"],
            "strict_equals_stored_summary": stored is not None and strict == stored,
            "signatures_equal_stored": {
                "structure": sum(r["signatures"]["structure_recomputed"] == r["signatures"]["structure_stored"]
                                 for r in rows),
                "selection": sum(r["signatures"]["selection_recomputed"] == r["signatures"]["selection_stored"]
                                 for r in rows),
                "calls": len(rows)},
            "supplementary": core_repeat_report(tasks, core_rows, expected_counts()),
            "notes": ["agreement among repeats, not accuracy",
                      "relation direction of the old contract is not comparable (relations compared as unordered sets)",
                      "expected requirement counts read from requirement_expect_dev_mini.json (counts only, unchanged)"]}


def build_baseline(store: Path, out: Path, layer) -> dict:
    """Stage 1 (module docstring). Writes only under ``out`` (a new folder)."""
    _guard.install_network_block()
    if out.exists():
        raise FileExistsError("the output folder must be new")
    before = store_hashes(store)
    manifest = read(store / MANIFEST)
    run_info = _read_optional(store / LIVE / "run.json") or {}
    summary = _read_optional(store / LIVE / "summary.json")
    current_catalog = catalog_version(layer)
    stored_catalog = manifest.get("catalog_version")
    inputs, rows_by_case = {}, {}
    for row in manifest["inputs"]:
        inputs[row["case_id"]], rows_by_case[row["case_id"]] = frozen_input(row, layer, stored_catalog,
                                                                            current_catalog)
    rows, lives = [], {}
    selector_rows = []
    for task in manifest["tasks"]:
        row, archive, live = frozen_call(task, store)
        row["signatures"] = signatures(row, archive, live)
        row["stored_input_equals_manifest"] = row["stored_input_digest"] == inputs[task["case_id"]]["stored"][
            "payload_digest"]
        row["stored_schema_equals_manifest"] = row["stored_schema_digest"] == inputs[task["case_id"]]["stored"][
            "schema_digest"]
        rows.append(row)
        if live is not None:
            lives[task["call_id"]] = {"primary_analysis": {"decomposition": {
                "applied_source": live["primary_analysis"]["decomposition"]["applied_source"]}},
                # repeat_report reads only whether a call has selections; signatures are already computed
                "selections": [{"requirement_id": s["requirement_id"]} for s in live["selections"]]}
            selector_rows.append({"call_id": task["call_id"], "selector_input": selector_input(live["analysis"])})
    measured = metrics(manifest["tasks"], rows, lives, summary)
    current_runtime = runtime_versions()
    prompts = sorted({r["instructions_digest"] for r in rows if r["instructions_digest"]})
    after = store_hashes(store)
    totals = {
        "planned": len(manifest["tasks"]), "rows": len(rows),
        "archive_ok": sum(not r["archive_problems"] for r in rows),
        "live_results": sum(r["live_sha256"] is not None for r in rows),
        "replay_results": sum(r["replay_sha256"] is not None for r in rows),
        "stored_input_equals_manifest": sum(r["stored_input_equals_manifest"] for r in rows),
        "stored_schema_equals_manifest": sum(r["stored_schema_equals_manifest"] for r in rows),
        "mapping_status": {s: sum(x["mapping"]["status"] == s for x in inputs.values()) for s in (RECONSTRUCTED,
                                                                                                    UNVERIFIED)},
        "inputs": len(inputs)}
    result = {
        "stage": "plan19_baseline", "real_calls": 0, "network_attempts_blocked": len(_guard.NETWORK_ATTEMPTS),
        "manifest": {"approval_hash": manifest.get("approval_hash"), "hash_valid": manifest_hash_valid(manifest),
                     "prepared_at": manifest.get("prepared_at"), "file_sha256": before.get(MANIFEST)},
        "catalog_version": {"manifest": stored_catalog, "live_run": run_info.get("catalog_version"),
                            "current": current_catalog},
        "prompt": {"archived_instruction_digests": prompts, "current": digest(prompt_text()),
                   "manifest_runtime": (manifest.get("runtime_versions") or {}).get("data/prompts/decompose.md")},
        "code_versions": {"manifest": manifest.get("code_versions"), "current": versions(),
                          "diff_manifest_to_current": version_diff(manifest.get("code_versions") or {}, versions()),
                          "archived_call_versions": sorted({r["call_versions_digest"] for r in rows}),
                          "manifest_code_versions_digest": digest(manifest.get("code_versions"))},
        "runtime_versions": {"manifest_equals_live_run": manifest.get("runtime_versions") == run_info.get(
                                 "runtime_versions"),
                             "diff_manifest_to_current": version_diff(manifest.get("runtime_versions") or {},
                                                                      current_runtime)},
        "totals": totals, "metrics": measured,
        "store_unchanged": before == after, "store_files_hashed": len(before)}
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "calls.json", rows)
    write_new(out / "inputs.json", inputs)
    write_new(out / "sense_rows.json", rows_by_case)
    write_new(out / "selector_inputs.json", selector_rows)
    write_new(out / "store_hashes.json", before)
    write_new(out / "summary.json", result)
    return result


# ---------------------------------------------------------------- stage 2: offline re-verification


class FrozenSource:
    """The stored response text of one call, given only for the exact stored request input and schema (replay
    mode: no model, no network). A different payload or schema is recorded and refused (never answered)."""

    execution_mode = "replay"

    def __init__(self, raw: str, stored_input: str, stored_schema: dict):
        self.raw, self.stored_input, self.stored_schema = raw, stored_input, stored_schema
        self.measurement: dict = {}
        self.calls, self.mismatch = 0, False

    def validate(self) -> None:
        if not isinstance(self.raw, str) or not isinstance(self.stored_input, str):
            raise ValueError("offline re-verification needs the stored response and request input")

    def fingerprint(self) -> dict:
        return {"mode": "offline_reverify", "input": digest(self.stored_input), "schema": digest(self.stored_schema),
                "response": digest(self.raw)}

    def __call__(self, payload, schema):
        self.calls += 1
        self.measurement = {"real_calls": 0, "fake_calls": 0, "replay_calls": 1, "usage": None}
        if canonical(payload) != self.stored_input or digest(schema) != digest(self.stored_schema):
            self.mismatch = True
            raise ReplayMiss("replay_miss")
        return extract(self.raw)[0]


def reverify_call(task: dict, question: str, as_of: str, archive: dict | None, frozen: dict | None,
                  current: dict | None, stored_catalog: str | None, current_catalog: str, layer,
                  stored_primary: dict | None = None) -> dict:
    """One call's status (``STATUSES``) with its reasons, and for a re-verified call the current outcome and its
    comparison with the stored primary analysis (``same`` / ``changed`` / ``no_stored``)."""
    out = {"call_id": task["call_id"], "case_id": task["case_id"], "profile_id": task["profile"]["id"],
           "repeat": task["repeat"], "status": None, "reasons": [], "outcome": None, "comparison": None}
    problems = archive_problems(archive, task)
    if problems:
        return {**out, "status": INCOMPATIBLE, "reasons": ["archive:" + p for p in problems]}
    request = archive["request"]
    stored_schema = ((request.get("text") or {}).get("format") or {}).get("schema")
    stored = {"payload": digest(json.loads(request["input"])), "schema": digest(stored_schema) if stored_schema
              else None, "rows": (frozen or {}).get("mapping", {}).get("frozen_digest"), "catalog": stored_catalog}
    now = {"payload": digest(current["payload"]) if current else None,
           "schema": digest(current["schema"]) if current else None,
           "rows": mapping_digest(current["local"]["senses"]) if current else None, "catalog": current_catalog}
    reasons = compatibility(stored, now)
    if reasons:
        return {**out, "status": INCOMPATIBLE, "reasons": reasons}
    if (frozen or {}).get("mapping", {}).get("status") != RECONSTRUCTED:
        return {**out, "status": UNVERIFIED, "reasons": ["mapping_provenance"]}
    source = FrozenSource(archive["raw_response"], request["input"], stored_schema)
    analysis = interpret_question(question, layer, decomposition=DecompositionConfig(
        mode="gpt", execution_mode="replay", proposal_source=source, as_of=as_of))
    if source.mismatch or source.calls != 1:
        return {**out, "status": INCOMPATIBLE, "reasons": ["payload_at_call"]}
    analysis = json.loads(json.dumps(analysis, ensure_ascii=False))
    out.update(status=REVERIFIED, outcome=_outcome(analysis), analysis_digest=digest(analysis_view(analysis)),
               analysis_digest_without_structure=digest(analysis_view(without_structure(analysis))),
               structure=structure_summary(analysis))
    if stored_primary is None:
        out["comparison"] = "no_stored"
    else:
        # the stored analyses predate the kept structure: compared without it (the new fields only)
        same = digest(analysis_view(without_structure(stored_primary))) == out["analysis_digest_without_structure"]
        out["comparison"] = "same" if same else "changed"
        if not same:
            out["stored_outcome"] = _outcome(stored_primary)
    return out


STRUCTURE_FIELDS = ("structure", "structure_reason")  # plan 19 contract joint: the record fields added


def without_structure(analysis: dict) -> dict:
    """The analysis without the kept-structure fields of plan 19's contract joint (``decomposition.structure``,
    ``structure_reason`` and ``decomposition.alternative.structure``) and without the mention spans added with the
    exact range check (``Mention.span``, decision of 2026-10-21): what an analysis of the code before them holds."""
    if isinstance(analysis.get("mentions"), list):
        analysis = {**analysis, "mentions": [{k: v for k, v in m.items() if k != "span"} if isinstance(m, dict)
                                             else m for m in analysis["mentions"]]}
    d = analysis.get("decomposition")
    if not isinstance(d, dict):
        return analysis
    d = {k: v for k, v in d.items() if k not in STRUCTURE_FIELDS}
    if isinstance(d.get("alternative"), dict):
        d["alternative"] = {k: v for k, v in d["alternative"].items() if k != "structure"}
    return {**analysis, "decomposition": d}


def structure_summary(analysis: dict) -> dict:
    """Counts of the kept structure of a re-verified analysis and its contract state (``check.structure_status``)."""
    from .contract.check import structure_status

    primary, alternative = structure_status(analysis), structure_status(analysis, alternative=True)
    s = (analysis.get("decomposition") or {}).get("structure")
    s = s if isinstance(s, dict) else {}
    groups = [g for r in (s.get("requirements") or {}).values() for g in r.get("breakdowns") or []]
    return {"state": primary["state"], "problems": len(primary["problems"]),
            "alternative_state": alternative["state"], "alternative_problems": len(alternative["problems"]),
            "reason": (analysis.get("decomposition") or {}).get("structure_reason"),
            "breakdown_groups": {k: sum(g.get("kind") == k for g in groups) for k in ("values", "axis", "descendants")},
            "polarity_links": len(s.get("polarity") or []),
            "uncertain_links": sum(p.get("scope_status") == "uncertain" for p in s.get("polarity") or []),
            "confirmed_effects": len(s.get("confirmed_effects") or [])}


def reverify_batch(manifest: dict, archive_dir: Path, frozen_inputs: dict, layer, live_dir: Path | None = None) -> dict:
    """Stage 2 over every planned call (module docstring); the totals always sum to the planned calls."""
    _guard.install_network_block()
    current_catalog = catalog_version(layer)
    stored_catalog = manifest.get("catalog_version")
    questions = {x["case_id"]: x for x in manifest["inputs"]}
    current = {cid: current_input(x["question"], layer) for cid, x in questions.items()}
    rows = []
    for task in manifest["tasks"]:
        a_path = call_file(archive_dir, task["call_id"])
        archive = read(a_path) if a_path.is_file() else None
        stored_primary = None
        if live_dir is not None and call_file(live_dir, task["call_id"]).is_file():
            stored_primary = read(call_file(live_dir, task["call_id"]))["primary_analysis"]
        q = questions.get(task["case_id"]) or {}
        rows.append(reverify_call(task, q.get("question"), q.get("as_of"), archive,
                                  frozen_inputs.get(task["case_id"]), current.get(task["case_id"]),
                                  stored_catalog, current_catalog, layer, stored_primary))
    counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
    reasons: dict[str, int] = {}
    for r in rows:
        for x in r["reasons"]:
            reasons[x] = reasons.get(x, 0) + 1
    done = [r for r in rows if r["status"] == REVERIFIED]
    totals = {"planned": len(manifest["tasks"]), "rows": len(rows), "status": counts,
              "sum_equals_planned": sum(counts.values()) == len(manifest["tasks"]) == len(rows),
              "reasons": reasons,
              "comparison": {c: sum(r["comparison"] == c for r in done) for c in ("same", "changed", "no_stored")},
              "reverified_applied": {s: sum(r["outcome"]["applied_source"] == s for r in done)
                                     for s in ("model", "rules_fallback")},
              "reverified_fallback_reasons": sorted({r["outcome"]["fallback_reason"] for r in done
                                                     if r["outcome"]["fallback_reason"]}),
              # plan 19 contract joint: the kept structure of the re-verified calls (state, contract problems)
              "structure_states": {s: sum(r["structure"]["state"] == s for r in done)
                                   for s in sorted({r["structure"]["state"] for r in done})},
              "structure_problems": sum(r["structure"]["problems"] + r["structure"]["alternative_problems"]
                                        for r in done)}
    return {"rows": rows, "totals": totals, "catalog_version": {"stored": stored_catalog, "current": current_catalog},
            "current_versions": versions(), "real_calls": 0,
            "network_attempts_blocked": len(_guard.NETWORK_ATTEMPTS)}


def run_reverify(store: Path, baseline: Path, out: Path, layer) -> dict:
    """Stage 2 on the stored batch with the frozen stage 1 mapping read explicitly from ``baseline``."""
    if out.exists():
        raise FileExistsError("the output folder must be new")
    before = store_hashes(store)
    frozen = read(baseline / "inputs.json")
    result = reverify_batch(read(store / MANIFEST), store / ARCHIVE, frozen, layer, store / LIVE)
    after = store_hashes(store)
    frozen_hashes = read(baseline / "store_hashes.json")
    result.update(store_unchanged=before == after, store_equals_baseline=before == frozen_hashes,
                  frozen_inputs_sha256=file_sha256(baseline / "inputs.json"))
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "calls.json", result["rows"])
    summary = {k: v for k, v in result.items() if k != "rows"}
    write_new(out / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("command", choices=("baseline", "reverify"))
    parser.add_argument("--store", type=Path, required=True, help="the stored plan 18 batch folder (read only)")
    parser.add_argument("--out", type=Path, required=True, help="a new output folder")
    parser.add_argument("--baseline", type=Path, help="the stage 1 output folder (reverify)")
    args = parser.parse_args(argv)
    if args.command == "reverify" and args.baseline is None:
        parser.error("reverify needs --baseline")
    _guard.install_network_block()
    layer = load_layer(include_external_synonyms=False)
    if args.command == "baseline":
        result = build_baseline(args.store, args.out, layer)
        brief = {k: result[k] for k in ("totals", "catalog_version", "store_unchanged", "network_attempts_blocked")}
        brief["metrics"] = {"strict_equals_stored_summary": result["metrics"]["strict_equals_stored_summary"],
                            "strict": result["metrics"]["strict_recomputed"],
                            "supplementary": result["metrics"]["supplementary"]["overall"]}
    else:
        brief = run_reverify(args.store, args.baseline, args.out, layer)
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
