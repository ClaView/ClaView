"""Offline-only request replay. Missing keys never call another client.

final: repeat the last successful output, or raise the last recorded failure if
none succeeded; as_recorded: consume recorded attempts in order (including errors),
then raise ReplayMiss. This module deliberately has
no dependency on claview.aflow.model or any network/client implementation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from q6g_candidates import SYSTEM, schema_for_payload


def canonical_sha(value) -> str:
    """Audit criterion: SHA-256 of sorted-key, UTF-8 JSON (default spacing)."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def request_key(node: str, system: str, payload: dict, schema: dict) -> str:
    return canonical_sha([node, hashlib.sha256(system.encode("utf-8")).hexdigest(), payload, schema])


# Archive identity is independent of future edits to the current prompt/schema.
# Frozen from the 2026-10-10 SYSTEM and schema_for([]); not a new model contract.
ARCHIVE_SYSTEM_SHA256 = "fed64e73278d2446e7ef28506b297f2492abbfb80b523abc50010a5ccde5aee4"
_ARCHIVE_SCHEMA_TEMPLATE = {'$id': 'Q6G.raw',
 'type': 'object',
 'additionalProperties': False,
 'required': ['requirements', 'note'],
 'properties': {'requirements': {'type': 'array',
                                 'minItems': 1,
                                 'maxItems': 5,
                                 'items': {'type': 'object',
                                           'additionalProperties': False,
                                           'required': ['quote',
                                                        'measure_text',
                                                        'status',
                                                        'grounding',
                                                        'suggest',
                                                        'basis_quotes',
                                                        'breakdown_quote',
                                                        'calc'],
                                           'properties': {'quote': {'type': 'string',
                                                                    'minLength': 1,
                                                                    'maxLength': 60},
                                                          'measure_text': {'type': 'string',
                                                                           'minLength': 1,
                                                                           'maxLength': 40},
                                                          'status': {'enum': ['grounded',
                                                                              'not_in_candidates',
                                                                              'not_in_catalog']},
                                                          'grounding': {'type': 'array',
                                                                        'maxItems': 3,
                                                                        'items': {'type': 'object',
                                                                                  'additionalProperties': False,
                                                                                  'required': ['cand_id',
                                                                                               'value_refs'],
                                                                                  'properties': {'cand_id': {'enum': []},
                                                                                                 'value_refs': {'type': 'array',
                                                                                                                'maxItems': 5,
                                                                                                                'items': {'enum': ['-']}}}}},
                                                          'suggest': {'type': 'array',
                                                                      'maxItems': 3,
                                                                      'items': {'type': 'string',
                                                                                'minLength': 1,
                                                                                'maxLength': 30}},
                                                          'basis_quotes': {'type': 'array',
                                                                           'maxItems': 4,
                                                                           'items': {'type': 'string',
                                                                                     'minLength': 1,
                                                                                     'maxLength': 30}},
                                                          'breakdown_quote': {'type': ['string', 'null'],
                                                                              'maxLength': 30},
                                                          'calc': {'enum': [None, 'ratio', 'growth']}}}},
                'note': {'type': 'string', 'maxLength': 200}}}


def archive_schema(payload):
    schema = copy.deepcopy(_ARCHIVE_SCHEMA_TEMPLATE)
    grounding = schema["properties"]["requirements"]["items"]["properties"]["grounding"]["items"]["properties"]
    grounding["cand_id"]["enum"] = [c["cand_id"] for c in payload["candidates"]]
    refs = [v.split(" ", 1)[0] for c in payload["candidates"] for v in c["values"]]
    grounding["value_refs"]["items"]["enum"] = refs or ["-"]
    return schema


class ReplayMiss(LookupError):
    """No recorded request/remaining attempt; abort rather than fall back live."""


class ReplayRecordedFailure(Exception):
    def __init__(self, error_class, *, completed_run=False):
        super().__init__(f"Recorded failure: {error_class}")
        self.error_class = error_class
        self.completed_run = completed_run


@dataclass(frozen=True)
class ReplayReply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class ReplayClient:
    name = "replay"

    def __init__(self, records: dict[str, list[dict]], mode="final"):
        if mode not in ("final", "as_recorded"):
            raise ValueError(f"Unknown replay mode: {mode}")
        self.records = copy.deepcopy(records)
        self.mode = mode
        self.positions = defaultdict(int)
        self.lookups = self.hits = self.misses = self.failures = 0

    def complete(self, node, system, payload, schema):
        key = request_key(node, system, payload, schema)
        self.lookups += 1
        sequence = self.records.get(key, [])
        if self.mode == "final":
            record = next((r for r in reversed(sequence) if r["ok"]), None)
            # A known request with only failures is distinct from an absent key.
            if record is None and sequence:
                record = sequence[-1]
        else:
            position = self.positions[key]
            record = sequence[position] if position < len(sequence) else None
            if record is not None:
                self.positions[key] += 1
        if record is None:
            self.misses += 1
            raise ReplayMiss(key)
        self.hits += 1
        if not record["ok"]:
            self.failures += 1
            raise ReplayRecordedFailure(record["error_class"], completed_run=record.get("completed_run", False))
        return ReplayReply(record["text"], record.get("tokens_in", 0), record.get("tokens_out", 0))


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_records(folder: Path, payloads: dict[str, dict], *, golden=False) -> dict[str, list[dict]]:
    """Read responses from ledger+results (dev/hold), results only (golden).

    payloads supplies the *archived* request identities, never the newly generated
    requests under test. Frozen archive prompt/schema identities are used, not the caller's
    possibly edited instruction or schema generator. Golden has only completed-run failures in
    results: do not fabricate per-attempt timing/tokens/429 details for it.
    """
    results = read_jsonl(folder / "results.jsonl")
    by_case = defaultdict(list)
    for row in results:
        by_case[row["case_id"]].append(row)
    records = {}
    ledger = defaultdict(list)
    if not golden:
        for row in read_jsonl(folder / "ledger.jsonl"):
            ledger[row["case_id"]].append(row)
    for cid, rows in by_case.items():
        payload = payloads[cid]["payload"]
        key = canonical_sha(["Q6G", ARCHIVE_SYSTEM_SHA256, payload, archive_schema(payload)])
        sequence = []
        if golden:
            for row in rows:
                ok = row["model_status"] == "ok"
                sequence.append({"ok": ok, "text": json.dumps(row["raw"], ensure_ascii=False)} if ok else
                                {"ok": False, "error_class": row["model_status"].split(":", 1)[-1],
                                 "completed_run": True})
        else:
            for row in ledger[cid]:
                if row["ok"] and not row.get("schema_errors"):
                    # Truncated/inconsistent archives are errors, never silently repaired.
                    raw = json.loads(row["text"])
                    if not any(r.get("raw") == raw for r in rows if r["model_status"] == "ok"):
                        raise ValueError(f"Ledger/results disagreement: {cid}")
                    sequence.append({k: row[k] for k in ("ok", "text", "tokens_in", "tokens_out")})
                else:
                    sequence.append({"ok": False, "error_class": "schema" if row.get("schema_errors") else row["error_class"]})
            successful = [json.loads(r["text"]) for r in sequence if r["ok"]]
            expected = [r["raw"] for r in rows if r["model_status"] == "ok"]
            if successful != expected:
                raise ValueError(f"Missing/reordered ledger outputs: {cid}")
        if not sequence or key in records:
            raise ValueError(f"Empty or duplicate replay identity: {cid}")
        records[key] = sequence
    return records


def replay_result(client, row, payload, *, system=SYSTEM):
    """One original execution: at most two ledger attempts, no waits or requests.

    Golden results are already aggregated executions; a recorded failed run goes
    directly to rules. ReplayMiss is intentionally not caught here.
    """
    last_error = None
    result = {"case_id": row["case_id"], "question": payload["question"],
              "rule_n_req": len(row["Q"].get("reqs", []))}
    for _ in range(2):
        try:
            reply = client.complete("Q6G", system, payload, schema_for_payload(payload))
            return {**result, "model_status": "ok", "raw": json.loads(reply.text)}
        except ReplayRecordedFailure as error:
            last_error = error.error_class
            if error.completed_run or last_error not in ("transient", "schema"):
                break
    return {**result, "model_status": f"failed:{last_error}"}
