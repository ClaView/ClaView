"""Run the split A v1 on an external question file (blind evaluation set kept outside the repository).

Modes
- probe (default): no transport at all. Each Q6G request is looked up in the live cache; a miss is counted
  (node, case) and Q6G falls back to rule requirements for that case. Use it to count the calls a live run needs,
  and, once the cache is full, to replay the live run with zero calls.
- live: HCX-007 through eval/judgment_runtime (3 s pacing, transient-only retry, cache written per request,
  attempt ledger with a hard cap). Only the HCX host may be resolved; any other network lookup is refused.
  The key is read by HcxClient from the CLOVASTUDIO_API_KEY environment variable and never printed.

Output (out_dir): cases.jsonl (one line per case), run.json (counts, time, settings).
Usage: PYTHONHASHSEED=0 python -B -X utf8 scripts/run_blind30.py <questions.jsonl> <out_dir> <cache_dir> [--live --cap N]
No answers are read: scoring happens elsewhere.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval")]

from claview.aflow.catalog import default_catalog  # noqa: E402
from claview.aflow.contract import COND_ALL  # noqa: E402
from claview.aflow.graph import build_graph  # noqa: E402
from claview.aflow.model import ModelError, ModelReply  # noqa: E402
from claview.aflow.run import run_a_v1_state  # noqa: E402
from judgment_runtime import POLICY, AttemptGate, JudgmentClient, digest  # noqa: E402

HCX_HOST = "clovastudio.stream.ntruss.com"
MODEL = "HCX-007"


class CacheProbe:
    """Live-cache lookup with the JudgmentClient key; a miss never reaches any transport."""
    name = MODEL

    def __init__(self, cache: Path):
        self.cache, self.context, self.misses = cache, {}, []

    def complete(self, node, system, payload, schema):
        key = digest([POLICY["version"], POLICY, "live", self.name, self.context.get("case_id"), 1,
                      node, system, payload, schema])
        path = self.cache / f"{key}.json"
        if not path.exists():
            self.misses.append({"case_id": self.context.get("case_id"), "node": node})
            raise LookupError("cache miss (probe mode, no transport)")
        return JudgmentClient._outcome(json.loads(path.read_text("utf8")))


def _network_guard(allowed: tuple[str, ...]):
    """Allow DNS only for the given hosts (live: the HCX host; probe: none) and no other socket use."""
    def hook(event, args):
        if event == "socket.getaddrinfo" and args[0] not in allowed:
            raise RuntimeError(f"network lookup refused: {args[0]!r}")
        if event == "socket.connect" and not allowed:
            raise RuntimeError("network connect refused (probe mode)")
    sys.addaudithook(hook)


def case_row(case: dict, state: dict) -> dict:
    scoped = state["interpretation"]["model"]["status"] == "ok"
    conds = {c["condition_id"]: {"kind": f.removeprefix("cond_"), "quote": c.get("quote"), "value": c.get("value")}
             for f in COND_ALL for c in state[f]}
    status = {r["scope_id"]: r for r in (state["selection_status"] or {}).get("requirements", [])}
    reqs = []
    for req in state["requirements"]:
        sid = req["scope_id"]
        ranked = state["ranked_by_scope"].get(sid, []) if scoped else state["ranked"]
        reqs.append({"scope_id": sid, "metric": req.get("measure") or req["text"], "quote": req["text"],
                     "conditions": [conds[c] for c in req["condition_ids"] + req["shared_condition_ids"] if c in conds],
                     "n_candidates": status.get(sid, {}).get("candidates"),
                     "status": status.get(sid, {}).get("status"), "confidence": status.get(sid, {}).get("confidence"),
                     "top10": [r["table_id"] for r in ranked[:10]]})
    return {"case_id": case["case_id"], "requirements": reqs, "interpretation_status": state["interpretation_status"],
            "selected_tables": [t["table_id"] for t in state["a_result"]["tables"]],
            "confidence": state["confidence"], "route": state["selection_status"]["route"],
            "fallback": state["interpretation"]["model"]["status"] == "fallback",
            "calls": len(state["calls"]), "error": None}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("cache", type=Path)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--cap", type=int, default=45)
    args = ap.parse_args(argv)
    cases = [json.loads(line) for line in args.questions.read_text("utf8").splitlines() if line.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    gate = None
    if args.live:
        from claview.aflow.model import HcxClient
        _network_guard((HCX_HOST,))
        gate = AttemptGate(args.out_dir / "ledger_live.jsonl", args.cap, mode="live")
        client = JudgmentClient(HcxClient(min_interval=0.0, token_version=POLICY["token_version"]), gate, args.cache)
    else:
        _network_guard(())
        client = CacheProbe(args.cache)
    catalog, graph, rows = default_catalog(ROOT), build_graph(), []
    start = time.time()
    try:
        for case in cases:
            client.context = {"case_id": case["case_id"]}
            try:
                state = run_a_v1_state(case["question"], catalog, client=client, as_of=case["as_of"], graph=graph)
                rows.append(case_row(case, state))
            except Exception as error:  # keep going; the row records it
                rows.append({"case_id": case["case_id"], "error": f"{type(error).__name__}: {error}"})
    finally:
        if gate:
            gate.close()
    elapsed = round(time.time() - start, 1)
    with (args.out_dir / "cases.jsonl").open("w", encoding="utf8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {"mode": "live" if args.live else "probe", "cases": len(cases), "model": MODEL,
               "config": {"model": "on", "as_of": sorted({c["as_of"] for c in cases})},
               "policy": POLICY, "elapsed_seconds": elapsed,
               "errors": sum(bool(r.get("error")) for r in rows),
               "fallback": sum(bool(r.get("fallback")) for r in rows)}
    if args.live:
        attempts = [r for r in client.rows if not r["cache_hit"]]
        summary.update(transport_attempts=len(attempts), failed_attempts=sum(not r["ok"] for r in attempts),
                       retries=sum(r["attempt"] > 1 for r in attempts), cache_hits=sum(r["cache_hit"] for r in client.rows),
                       error_classes=dict(collections.Counter(r.get("error_class") for r in attempts if not r["ok"])))
    else:
        summary.update(cache_misses=len(client.misses),
                       misses_by_node=dict(collections.Counter(m["node"] for m in client.misses)))
    (args.out_dir / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf8")
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
