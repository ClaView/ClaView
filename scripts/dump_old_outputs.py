"""Old-output dump: the old A pipeline over the 417 questions by stored-response replay (plan 02, 4-4).

Usage (checkout root):
    PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 scripts/dump_old_outputs.py [--limit N] [--out-dir DIR]
        [--cache-dir DIR ...]

The old code is imported and called, never changed. It sits outside the claview_v3 boundary; from the new package
it only reads ``claview_v3.paths`` (data locations, no other code).

No model, KOSIS or network call:
- At start, before the old modules are imported, socket ``connect``/``connect_ex``/``sendto``,
  ``socket.create_connection``, ``socket.getaddrinfo`` and ``urllib.request.urlopen`` are replaced by blocking
  stand-ins, an audit hook refuses the matching socket/urllib events, and after import
  ``claview.aflow.model.HcxClient.complete`` is replaced too. Every refused attempt is counted, even when a caller
  swallows the exception.
- The replay client answers only from the stored cache folders and records every miss.
- The old Q6G turns a replay miss (and any client exception) into a ``replay_miss``/``client_failure`` fallback, so
  the runner checks, per question, whether the miss count or the transport-attempt count grew. Such a question is a
  failed row, and any failed row makes the run end with exit code 1.
- A stored response that is itself an error (recorded ``ok: false``) gives a fallback too; that is a normal replay
  and is counted apart (``normal_fallback``).

Outputs (outside git): ``old_outputs.jsonl`` (one row per question) and ``summary.json`` in the output folder.
The console shows counts only, never question text or per-case content.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = {"all": 324, "golden+development": 188, "official": 136}  # adopted A v1 baseline (frozen test)
RANKED_TOP = 10
SCORE_RANKED_TOP = 5  # score_sets gets the top 5 of ranked, as in the frozen test

# ---------------------------------------------------------------- transport blocking

TRANSPORT_ATTEMPTS: list[str] = []
_NETWORK_EVENTS = frozenset({
    "socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyname_ex", "socket.gethostbyaddr", "urllib.Request",
})
_BLOCK = {"on": False}


class TransportBlocked(RuntimeError):
    """A network or model transport was attempted during the dump."""


def _refuse(event: str):
    TRANSPORT_ATTEMPTS.append(event)
    raise TransportBlocked(f"dump_old_outputs: transport refused ({event})")  # event name only, no address


def _blocked(event: str):
    def blocked(*args, **kwargs):
        _refuse(event)

    blocked.__name__ = f"blocked_{event.replace('.', '_')}"
    return blocked


def _audit(event: str, args) -> None:
    if _BLOCK["on"] and event in _NETWORK_EVENTS:
        _refuse(event)


def install_transport_block() -> None:
    if _BLOCK["on"]:
        return
    socket.socket.connect = _blocked("socket.connect")
    socket.socket.connect_ex = _blocked("socket.connect_ex")
    socket.socket.sendto = _blocked("socket.sendto")
    socket.create_connection = _blocked("socket.create_connection")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")
    urllib.request.urlopen = _blocked("urllib.request.urlopen")
    sys.addaudithook(_audit)
    _BLOCK["on"] = True


def block_model_client(model_module) -> None:
    """Replace the live HCX client's complete (the only live model transport of the old code)."""
    model_module.HcxClient.complete = _blocked("HcxClient.complete")


# ---------------------------------------------------------------- replay client


class Replay:
    """Stored HCX-007 responses keyed exactly as recorded (same key as the frozen test's replay); a miss is
    recorded and raised as LookupError, never a live call."""

    name = "HCX-007"

    def __init__(self, caches, digest, policy, reply_type, error_type):
        self.caches = [Path(c) for c in caches]
        self._digest, self._policy = digest, policy
        self._reply, self._error = reply_type, error_type
        self.case = None
        self.misses: list[dict] = []
        self.hits: list[dict] = []

    def complete(self, node, system, payload, schema):
        key = self._digest([self._policy["version"], self._policy, "live", self.name, self.case, 1, node, system,
                            payload, schema])
        path = next((c / f"{key}.json" for c in self.caches if (c / f"{key}.json").exists()), None)
        if path is None:
            self.misses.append({"case_id": self.case, "node": node, "key": key})
            raise LookupError("replay miss")
        row = json.loads(path.read_text("utf8"))
        self.hits.append({"case_id": self.case, "node": node, "key": key, "cache": path.parent.name,
                          "ok": bool(row["ok"])})
        if row["ok"]:
            return self._reply(row["text"], row["tokens_in"], row["tokens_out"])
        raise self._error(row["error_class"], "recorded", http_status=row.get("http_status"),
                          retry_after=row.get("retry_after") or 0)


# ---------------------------------------------------------------- rows

COND_FIELDS = ("cond_period", "cond_frequency", "cond_basis", "cond_unit", "cond_breakdown", "cond_exclude")


def _ranked_top(rows):
    return [dict(r) for r in (rows or [])[:RANKED_TOP]]


def case_row(case, state, groups, score_sets) -> dict:
    model = (state.get("interpretation") or {}).get("model") or {}
    pool_by_scope = state.get("pool_by_scope") or {}
    ranked_by_scope = state.get("ranked_by_scope") or {}
    a_result = state.get("a_result") or {}
    tables = list(a_result.get("tables") or [])
    inputs = {
        "groups": groups,
        "output": [t["table_id"] for t in tables],
        "ranked": [r["table_id"] for r in (state.get("ranked") or [])[:SCORE_RANKED_TOP]],
        "pool": [p["table_id"] for p in (state.get("pool") or [])],
        "k": state.get("k"),
    }
    result = score_sets(inputs["groups"], inputs["output"], inputs["ranked"], inputs["pool"], inputs["k"])
    return {
        "interpretation": {
            "requirements_rule": state.get("requirements_rule") or [],
            "requirements": state.get("requirements") or [],
            "conditions": {field: state.get(field) or [] for field in COND_FIELDS},
            "model": {"status": model.get("status"), "reason": model.get("reason"),
                      "dropped": model.get("dropped") or []},
        },
        "selection": {
            "pool_source": "pool_by_scope" if pool_by_scope else "pool",
            "pool_by_scope": pool_by_scope if pool_by_scope else None,
            "pool": None if pool_by_scope else (state.get("pool") or []),
            "ranked_source": "ranked_by_scope" if ranked_by_scope else "ranked",
            "ranked_top_by_scope": {s: _ranked_top(rows) for s, rows in ranked_by_scope.items()} or None,
            "ranked_top": _ranked_top(state.get("ranked")),
            "k": state.get("k"),
            "tables": tables,
            "confidence": state.get("confidence"),
        },
        "set_exact": {"value": bool(result["set_exact"]), "inputs": inputs, "result": result},
    }


def load_groups(path: Path) -> dict[str, list[list[str]]]:
    by_case: dict[str, dict] = collections.defaultdict(dict)
    for line in path.read_text(encoding="utf8").splitlines():
        if line.strip():
            row = json.loads(line)
            by_case[row["case_id"]][row["group_index"]] = row["allowed_tables"]
    return {cid: [g[i] for i in sorted(g)] for cid, g in by_case.items()}


# ---------------------------------------------------------------- main


def _parse(argv):
    from claview.a import paths

    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out-dir", type=Path, default=paths.V3_RUNS_DIR / "old_dump")
    parser.add_argument("--cache-dir", type=Path, action="append", default=None,
                        help="replay cache folder (repeatable, searched in order); default: the three corpus caches")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions (small check mode)")
    args = parser.parse_args(argv)
    args.questions, args.groups = paths.QUESTIONS_417, paths.ANSWER_GROUPS
    args.caches = list(args.cache_dir) if args.cache_dir else list(paths.REPLAY_CACHE_DIRS)
    return args


def main(argv=None) -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        print("dump_old_outputs: run with PYTHONHASHSEED=0 (the stored replay keys were produced with it)")
        return 2
    install_transport_block()
    for p in (ROOT / "eval", ROOT / "src"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    args = _parse(argv)
    missing_inputs = [str(p) for p in (args.questions, args.groups) if not p.is_file()]
    missing_inputs += [str(c) for c in args.caches if not Path(c).is_dir()]
    if missing_inputs:
        print(f"dump_old_outputs: replay corpus unavailable ({len(missing_inputs)} path(s) missing)")
        return 3

    from claview.aflow import model as old_model
    from claview.aflow.catalog import default_catalog
    from claview.aflow.graph import build_graph
    from claview.aflow.run import run_a_v1_state
    from judgment_runtime import POLICY, digest
    from table_sets import score_sets

    block_model_client(old_model)
    started = time.perf_counter()
    cases = json.loads(args.questions.read_text(encoding="utf8"))
    if args.limit is not None:
        cases = cases[:args.limit]
    groups = load_groups(args.groups)
    catalog, graph = default_catalog(ROOT), build_graph()
    client = Replay(args.caches, digest, POLICY, old_model.ModelReply, old_model.ModelError)
    attempts_before_cases = len(TRANSPORT_ATTEMPTS)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_rows = args.out_dir / "old_outputs.jsonl"
    seen = collections.Counter()
    totals = collections.Counter()
    by_dataset: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    with out_rows.open("w", encoding="utf8", newline="\n") as stream:
        for n, case in enumerate(cases, 1):
            cid = case["case_id"]
            seen[cid] += 1
            m0, h0, t0 = len(client.misses), len(client.hits), len(TRANSPORT_ATTEMPTS)
            client.case = cid
            row = {"case_id": cid, "dataset": case.get("dataset")}
            error = None
            try:
                state = run_a_v1_state(case["question"], catalog, client=client, as_of=case.get("as_of"),
                                       graph=graph)
            except Exception as exc:  # recorded as a failed row; the run goes on
                state, error = None, type(exc).__name__
            misses = client.misses[m0:]
            hits = client.hits[h0:]
            events = TRANSPORT_ATTEMPTS[t0:]
            if state is not None:
                row.update(case_row(case, state, groups.get(cid, []), score_sets))
            model_status = row.get("interpretation", {}).get("model", {}).get("status")
            fail_reasons = (["replay_miss"] if misses else []) + (["transport_attempt"] if events else []) \
                + (["exception"] if error else [])
            row["replay"] = {
                "misses": len(misses), "missed_keys": [m["key"] for m in misses],
                "transport_attempts": len(events), "transport_events": list(events),
                "keys": [{"key": h["key"], "cache": h["cache"], "ok": h["ok"]} for h in hits],
                "stored_error_responses": sum(1 for h in hits if not h["ok"]),
                "normal_fallback": bool(not fail_reasons and model_status == "fallback"),
                "error": error,
            }
            row["failed"] = bool(fail_reasons)
            row["fail_reasons"] = fail_reasons
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")

            ds = by_dataset[str(case.get("dataset"))]
            ds["cases"] += 1
            ds["set_exact"] += bool(row.get("set_exact", {}).get("value"))
            totals["failed_rows"] += row["failed"]
            totals["replay_misses"] += len(misses)
            totals["normal_fallbacks"] += row["replay"]["normal_fallback"]
            totals["stored_error_responses"] += row["replay"]["stored_error_responses"]
            totals["no_answer_groups"] += cid not in groups
            if n % 50 == 0 or n == len(cases):
                print(f"{n}/{len(cases)} done, failed rows {totals['failed_rows']}, "
                      f"{round(time.perf_counter() - started)} s", flush=True)

    # Missing / duplicate questions are checked on the file as written, against the question list.
    expected = collections.Counter(c["case_id"] for c in cases)
    written = collections.Counter(json.loads(line)["case_id"]
                                  for line in out_rows.read_text(encoding="utf8").splitlines() if line.strip())
    duplicates = sorted(cid for cid in set(expected) | set(written) if max(expected[cid], written[cid]) > 1)
    missing_cases = sorted((set(expected) - set(written)) | (set(written) - set(expected)))
    exact_total = sum(ds["set_exact"] for ds in by_dataset.values())
    exact_217 = sum(by_dataset[d]["set_exact"] for d in ("golden", "development") if d in by_dataset)
    full_run = args.limit is None
    summary = {
        "questions": len(cases),
        "rows": sum(written.values()),
        "full_run": full_run,
        "by_dataset": {d: dict(c) for d, c in sorted(by_dataset.items())},
        "set_exact": {"all": exact_total, "golden+development": exact_217,
                      "official": by_dataset["official"]["set_exact"] if "official" in by_dataset else 0},
        "baseline": BASELINE if full_run else None,
        "baseline_match": ({"all": exact_total == BASELINE["all"],
                            "golden+development": exact_217 == BASELINE["golden+development"],
                            "official": by_dataset["official"]["set_exact"] == BASELINE["official"]}
                           if full_run else None),
        "failed_rows": totals["failed_rows"],
        "transport_attempts": len(TRANSPORT_ATTEMPTS),
        "transport_attempts_outside_cases": attempts_before_cases,
        "replay_misses": totals["replay_misses"],
        "missing_cases": len(missing_cases),
        "duplicate_cases": len(duplicates),
        "normal_fallbacks": totals["normal_fallbacks"],
        "stored_error_responses": totals["stored_error_responses"],
        "no_answer_groups": totals["no_answer_groups"],
        "cache_dirs": [Path(c).name for c in args.caches],
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    ok = (summary["failed_rows"] == 0 and summary["transport_attempts"] == 0 and summary["replay_misses"] == 0
          and summary["missing_cases"] == 0 and summary["duplicate_cases"] == 0)
    summary["ok"] = ok
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf8")
    print(json.dumps({k: summary[k] for k in ("questions", "set_exact", "baseline_match", "failed_rows",
                                              "transport_attempts", "replay_misses", "missing_cases",
                                              "duplicate_cases", "normal_fallbacks", "elapsed_seconds", "ok")},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
