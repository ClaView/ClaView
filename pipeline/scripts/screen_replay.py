"""Replay stored new-A answers through the screen path (plan 54): no model, KOSIS or other external call.

For each case of the given run folders (``calls.jsonl`` + ``records.json``, read only), the stored answers are handed
back in order (first judgment, then the re-judgment when there was one) as the judge's text, whatever the rebuilt
request is; whether the rebuilt input equals the stored one is recorded, not required ("the answer and its card
references are kept"). The question runs through ``ScreenPipeline`` with the synthetic B model and fetch client.

Per case it writes: A's own outcome and final tables (compared with the stored record), the AView handoff (ready /
blocked and why), and the screen result (status, error code, unsupported links, A warnings, stage statuses).
A replay re-checks stored answers with this code; it is not a new model output and proves no model gain.

    python pipeline/scripts/screen_replay.py OUT_DIR RUN_DIR [RUN_DIR ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claview_a.transport.provider import extract
from claview_a_api import screen as a_screen
from claview_b_api.service import synthetic_clients
from claview_pipeline.screen import ScreenPipeline


def _stored_final(record: dict) -> dict:
    return {
        sid: {
            "outcome": f["outcome"],
            "tables": [[t["table_id"], t.get("period"), t.get("frequency")] for t in f.get("tables") or []],
        }
        for sid, f in (record.get("final") or {}).items()
    }


def _final(state: dict) -> dict:
    record = (state.get("a_checkpoint") or {}).get("a_state", {}).get("record") or {}
    return {
        d["statistic"]: {
            "outcome": d["outcome"],
            "tables": [[t["table_id"], t.get("period"), t.get("frequency")] for t in d.get("final_tables") or []],
        }
        for d in record.get("decisions") or []
    }


def replay(run_dir: Path, runner: ScreenPipeline) -> dict:
    records = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    calls: dict[str, list[dict]] = {}
    for line in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        calls.setdefault(row["case_id"], []).append(row)
    out = {}
    for key, rows in calls.items():
        if key not in records:
            continue
        answered = [r for r in rows if r.get("outcome") == "answered"]
        seen = {"input_equal": [], "models": []}

        def judge_for(n):
            def judge(payload, request):
                stored = answered[n]["request"]
                seen["input_equal"].append(request["input"] == stored["input"])
                seen["models"].append([request["model"], stored["model"]])
                return extract(answered[n]["raw_response"])[0]

            return judge

        runner.judges_factory = lambda: a_screen.Judges(judge_for(0), judge_for(1), "replay")
        rec = records[key]
        state = runner.run(rec["question"], as_of=rec["as_of"])
        view = state.get("a_view") or {}
        out[key] = {
            "question": rec["question"],
            "inputs": seen,
            "a_final": _final(state),
            "a_final_same_as_stored": _final(state) == _stored_final(rec),
            "a_route": view.get("a_route"),
            "view_status": view.get("status"),
            "ready": [r["statistics"] for r in (view.get("handoff") or {}).get("ready", [])],
            "blocked": [[b["statistics"], b["code"]] for b in (view.get("handoff") or {}).get("blocked", [])],
            "status": state.get("status"),
            "error_code": state.get("error_code"),
            "unsupported_links": [[u["statistics"], u["code"]] for u in state.get("unsupported_links") or []],
            "a_warnings": [[w.get("statistic"), w.get("code")] for w in state.get("a_warnings") or []],
            "handoff_notes": [[n.get("statistics"), n.get("code")] for n in state.get("handoff_notes") or []],
            "view_warnings": [[s["statistic"], w["code"]] for s in view.get("statistics") or [] for w in s["warnings"]],
            "b_periods": [
                [
                    s.get("table_id"),
                    (s.get("period") or {}).get("frequency"),
                    (s.get("period") or {}).get("start"),
                    (s.get("period") or {}).get("end"),
                ]
                for o in (state.get("stage_outcomes") or {}).get("B") or []
                for s in ((o or {}).get("result") or {}).get("selections") or []
            ],
            "b_issues": [
                i.get("code")
                for o in (state.get("stage_outcomes") or {}).get("B") or []
                for i in (o or {}).get("issues") or []
            ],
            "stages": {s["id"]: s["status"] for s in state["stages"]},
            "message": state.get("message"),
            "calls": state.get("calls"),
        }
    return out


def main() -> None:
    out_dir, runs = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    out_dir.mkdir(parents=True, exist_ok=False)
    model, client = synthetic_clients()
    runner = ScreenPipeline(model=model, client=client, runs_dir=None)
    result = {}
    for run_dir in runs:
        result[str(run_dir)] = replay(run_dir, runner)
    (out_dir / "replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = [r for got in result.values() for r in got.values()]
    print(
        json.dumps(
            {
                "cases": len(rows),
                "a_final_same": sum(r["a_final_same_as_stored"] for r in rows),
                "status": {s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
