"""Step-5 runner of the table-level path (plan 37; user 2026-10-28 04:44 UTC: preparation only, live calls not
approved).

- ``make_manifest``: a run's fixed conditions (cases, model settings, path versions and prompt hash, the trees of
  this package (with the reused current-path code and the period package inside it), of the evaluator, catalog files, standard and
  decisions hashes, as_of, the call limit) with ``approval.live_calls`` false. Only the user sets it true.
- ``gates``: the manifest against the code and files as they are now; any difference refuses the run. A changed
  code, prompt, setting or standard needs a new manifest and a new run, and ``combine`` refuses to merge runs whose
  conditions differ.
- ``LiveJudge``: one call = one transport attempt (``claview_a.transport.provider.OpenAITransport``: one
  ``http.client`` request, no SDK, no retry layer, 60 s socket timeout). A call budget refuses any call beyond the
  manifest's limit before it is sent. Every failure (transport, http status, timeout, incomplete or truncated output,
  refusal, empty or unreadable output) is archived at once with its cause and the usage the body gives, then raised
  as ``JudgeError``; nothing calls again. The key is read by the transport from the environment, never opened,
  printed or written here.
- ``FakeJudge``: a fixed, valid answer built from the cards, for checking input, records and scoring end to end. Its
  scores say nothing about a model.
- ``ReplayJudge``: the archived answer of the same request (by request hash), no network. A replay re-scores a run;
  it proves nothing about a model on new inputs.
- ``run``: the cases through the straight graph, writing the manifest, the call archive (each row as it happens),
  the records, the run record and the report into a new folder. A question's own failure (a judge error, an output
  the contract refuses, a failed check) is recorded and the run goes on. A reliability failure stops the run with what
  ran kept and no re-run (user 2026-10-28 05:30 UTC): the fixed conditions changing during the run (checked before
  every question), more than one call for a question, the call limit, or an archive or record that cannot be written.
  ``subsets`` of the manifest (named case lists) are scored apart next to golden16/new24/all.
- The standard (answer file) is chosen when the manifest is made (default standard_v2; user 2026-10-28 08:13 UTC):
  its path, version and sha256, and the sha256 of every decisions file it names, are fixed conditions; the run reads
  the cases and scores them with that very file, and the run record and the report carry the same sha256.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules" / "a"))
sys.path.insert(0, str(ROOT / "modules" / "a" / "scripts"))

from claview_a import contract as K  # noqa: E402
from claview_a.graph import JudgeError, run as graph_run, run_info  # noqa: E402
from claview_a.judges import (  # noqa: E402,F401  (the judges live in the package; re-exported for the runner)
    Archive, BudgetExceeded, FakeJudge, LiveJudge, ReplayJudge, _cause, now, text_sha)
from tl_eval import files as F  # noqa: E402
from tl_eval import report as R  # noqa: E402
from tl_eval import score as S  # noqa: E402
from tl_eval import standard as ST  # noqa: E402

LIVE_VERSION = "tl-live-2026-10-28.3"
MODEL = {"model": "gpt-6-luna", "effort": "low", "max_output_tokens": 4096}
STAGE1 = ["SB:DA3-01", "SB:CQ1-01", "SB:CQ1-03", "SB:DA3-09", "CV-AB-05", "CV-AB-07"]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha(folder: Path, pattern: str = "*") -> dict:
    files = sorted(p for p in folder.rglob(pattern) if p.is_file() and "__pycache__" not in p.parts)
    rows = [[str(p.relative_to(folder)).replace("\\", "/"), file_sha(p)] for p in files]
    return {"sha256": text_sha(rows), "files": len(rows)}


def standard_path(manifest: dict | None = None) -> Path:
    """The manifest's standard file (a manifest made before the option names none: standard_v2)."""
    rel = ((manifest or {}).get("conditions") or {}).get("standard", {}).get("path")
    return F.external(rel) if rel else ST.OUT_V2


def standard_conditions(path: Path) -> dict:
    """The standard's path (relative to the repo), version and sha256, and the sha256 of each decisions file it
    names (``decisions``, ``decisions_v3`` …), as the files are now."""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    decisions = {k: file_sha(F.external(v["path"])) for k, v in sorted(data.items())
                 if k.startswith("decisions") and isinstance(v, dict) and v.get("path")}
    return {"standard": {"path": "eval/tl_eval/" + path.name, "version": data["version"],
                         "sha256": file_sha(path)},
            "decisions": decisions}


def conditions(as_of: str, standard: Path | None = None) -> dict:
    """The fixed conditions of a run, as the code and files are now."""
    from claview_a.periods import core as claview_period

    return {"run_info": run_info(as_of, K.Settings(**MODEL)),
            "package_tree": tree_sha(F.PACKAGE_DIR),
            "evaluator_tree": tree_sha(F.EVALUATOR_DIR, "*.py"),
            "period_tree": tree_sha(Path(claview_period.__file__).parent),
            **standard_conditions(standard or ST.OUT_V2),
            "evaluator": {"score": S.SCORE_VERSION, "report": R.REPORT_VERSION, "live": LIVE_VERSION},
            "runtime": {"python": platform.python_version(), "langgraph": metadata.version("langgraph")}}


def make_manifest(run_id: str, stage: str, case_ids: list[str], as_of: str = "2026-10-22",
                  subsets: dict[str, list[str]] | None = None, standard_file: Path | None = None) -> dict:
    standard_file = Path(standard_file or ST.OUT_V2)
    standard = json.loads(standard_file.read_text(encoding="utf-8"))
    known = {c["case_id"] for c in standard["cases"]}
    unknown = [c for c in case_ids if c not in known]
    if unknown or len(set(case_ids)) != len(case_ids) or as_of != standard["as_of"]:
        raise ValueError(f"cases not in the standard, repeated, or as_of other than the standard's: {unknown}")
    if subsets and any(set(ids) - set(case_ids) for ids in subsets.values()):
        raise ValueError("a subset names a case outside the run")
    return {"run_id": run_id, "stage": stage, "created": now(), "as_of": as_of, "cases": list(case_ids),
            "subsets": dict(subsets or {}),
            "max_calls": len(case_ids), "retries": 0, "model": dict(MODEL),
            "transport": "OpenAI Responses via claview_a.transport.provider OpenAITransport: one http.client request per call",
            "conditions": conditions(as_of, standard_file),
            "approval": {"live_calls": False, "approved_by": None, "approved_at": None,
                         "note": "only the user sets live_calls true, for this manifest's cases and call limit"}}


def gates(manifest: dict) -> list[dict]:
    """Every fixed condition against now; a row with ok False refuses the run."""
    current = conditions(manifest["as_of"], standard_path(manifest))
    rows = [{"check": k, "ok": current[k] == manifest["conditions"].get(k)} for k in current]
    rows.append({"check": "call_limit", "ok": manifest["max_calls"] == len(manifest["cases"])
                 and manifest["retries"] == 0})
    rows.append({"check": "model", "ok": manifest["model"] == MODEL})
    return rows


class RunStopped(RuntimeError):
    """A reliability failure: the run stops, nothing is re-run."""


def run(manifest: dict, mode: str, out_dir: Path, layer=None, transport=None, replay_from: Path | None = None) -> dict:
    """mode 'fake' (no network), 'replay' (archived responses, no network) or 'live' (only with the manifest
    approved and every gate passing)."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists: a run is never resumed or overwritten")
    checks = gates(manifest)
    if not all(r["ok"] for r in checks):
        raise RuntimeError(f"gates failed: {[r['check'] for r in checks if not r['ok']]}")
    if mode == "live":
        if manifest["approval"]["live_calls"] is not True:
            raise PermissionError("live calls are not approved in this manifest")
        if transport is None:
            from claview_a.transport.provider import OpenAITransport

            transport = OpenAITransport()
        transport.preflight()  # key present (not read out): fails before any call is sent
    elif mode == "replay":
        rows = [json.loads(x) for x in (Path(replay_from) / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    elif mode != "fake":
        raise ValueError(mode)
    if layer is None:
        from claview_a.catalog.layer import load_layer

        layer = load_layer()
    from claview_a.search.tables import selection_catalog

    std_file = standard_path(manifest)
    std_bytes = std_file.read_bytes()  # the same bytes for the cases and the scoring
    standard = json.loads(std_bytes.decode("utf-8"))
    std_sha = hashlib.sha256(std_bytes).hexdigest()
    if std_sha != manifest["conditions"]["standard"]["sha256"]:
        raise RuntimeError("the standard file differs from the manifest's")
    by = {c["case_id"]: c for c in standard["cases"]}
    out_dir.mkdir(parents=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    archive, ref = Archive(out_dir / "calls.jsonl"), {}
    judge = {"live": lambda: LiveJudge(transport, manifest["max_calls"], archive, ref),
             "fake": lambda: FakeJudge(archive, ref),
             "replay": lambda: ReplayJudge(rows, archive, ref)}[mode]()
    settings = K.Settings(**manifest["model"])
    records, started, stopped = {}, now(), None
    try:
        for cid in manifest["cases"]:
            ref["case_id"] = cid
            changed = [r["check"] for r in gates(manifest) if not r["ok"]]
            if changed:
                raise RunStopped(f"fixed conditions changed before {cid}: {changed}")
            before = judge.calls
            state = graph_run(by[cid]["question"], layer, judge, settings, manifest["as_of"])
            if judge.calls - before > 1:
                raise RunStopped(f"{cid}: {judge.calls - before} calls for one question")
            rec = S.adapt(state)
            rec["run_id"], rec["mode"] = manifest["run_id"], mode
            records[cid] = rec
    except Exception as e:  # a reliability failure or a program fault: stop, keep what ran, re-raise
        stopped = {"case_id": ref.get("case_id"), "error": type(e).__name__, "detail": str(e)[:300],
                   "records_kept": len(records)}
        raise
    finally:
        (out_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        usage = [r["usage"] for r in archive.rows if r.get("usage")]
        summary = {"run_id": manifest["run_id"], "stage": manifest["stage"], "mode": mode, "started": started,
                   "finished": now(), "stopped": stopped, "gates": checks, "calls": judge.calls,
                   "standard": {"path": "eval/tl_eval/" + std_file.name,
                                "version": standard["version"], "sha256": std_sha},
                   "max_calls": manifest["max_calls"],
                   "failed_calls": [{k: v for k, v in r.items() if k not in ("raw_response", "request")}
                                    for r in archive.rows if r.get("outcome") == "failed"],
                   "tokens": {"input": sum(u.get("input_tokens", 0) for u in usage),
                              "cached_input": sum((u.get("input_tokens_details") or {}).get("cached_tokens", 0)
                                                  for u in usage),
                              "output": sum(u.get("output_tokens", 0) for u in usage),
                              "reasoning": sum((u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
                                               for u in usage)}}
        (out_dir / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    views = selection_catalog(layer).views
    report = R.evaluate(records, standard, {t: v.title for t, v in views.items()}, R.coverage_of(views), std_sha)
    results = {r["case_id"]: r for r in report["results"]}
    report["subsets"] = {name: {"cases": ids, "aggregate": S.aggregate([results[c] for c in ids if c in results])}
                         for name, ids in (manifest.get("subsets") or {}).items()}
    if mode == "fake":
        report["note"] = "fake judge: wiring check only, not a model result"
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**summary, "report": report}


def combine(run_dirs: list[Path]) -> dict:
    """Records of several runs as one set, only when every run has the same fixed conditions and model (a run made
    after a change of code, prompt, setting or standard is never merged with one made before) and no case twice."""
    manifests = [json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8")) for d in run_dirs]
    keys = {text_sha([m["conditions"], m["model"], m["as_of"]]) for m in manifests}
    if len(keys) != 1:
        raise ValueError("runs made under different conditions are not merged")
    records: dict = {}
    for d in run_dirs:
        for cid, rec in json.loads((Path(d) / "records.json").read_text(encoding="utf-8")).items():
            if cid in records:
                raise ValueError(f"{cid} is in more than one run")
            records[cid] = rec
    return records


def main(argv: list[str]) -> None:
    """``manifest <run_id> <stage> <out.json> [case ...]`` (default: the stage-1 six), ``manifest40 <run_id> <stage>
    <out.json>`` (every case of the standard, subsets: the stage-1 six and the other 34), ``manifest_all <run_id>
    <stage> <out.json>`` (every case of the standard named with ``--standard``, subsets by part and by relation),
    ``gates <manifest.json>``, ``run <manifest.json> fake|replay|live <out_dir> [replay_from]``. ``--standard <file>``
    (manifest commands only) picks the answer file; default standard_v2."""
    std_file = None
    if "--standard" in argv:
        i = argv.index("--standard")
        std_file = Path(argv[i + 1]).resolve()
        argv = argv[:i] + argv[i + 2:]
    cmd = argv[0]
    if cmd == "manifest40":
        every = [c["case_id"] for c in json.loads(Path(std_file or ST.OUT_V2).read_text(encoding="utf-8"))["cases"]]
        m = make_manifest(argv[1], argv[2], every, subsets={
            "repeated6": list(STAGE1), "rest34": [c for c in every if c not in STAGE1]}, standard_file=std_file)
        Path(argv[3]).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"cases": len(m["cases"]), "max_calls": m["max_calls"], "subsets": {
            k: len(v) for k, v in m["subsets"].items()}, "approval": m["approval"]["live_calls"]}))
    elif cmd == "manifest_all":
        cases = json.loads(Path(std_file or ST.OUT_V2).read_text(encoding="utf-8"))["cases"]
        every = [c["case_id"] for c in cases]
        subsets = {f"part:{p}": [c["case_id"] for c in cases if c["part"] == p]
                   for p in dict.fromkeys(c["part"] for c in cases)}
        paired = {x for c in cases if (c.get("relations") or {}).get("extends")
                  for x in (c["case_id"], c["relations"]["extends"])}
        if paired:
            subsets["extension_pairs"] = [c for c in every if c in paired]
            subsets["not_paired"] = [c for c in every if c not in paired]
        m = make_manifest(argv[1], argv[2], every, subsets=subsets, standard_file=std_file)
        Path(argv[3]).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"cases": len(m["cases"]), "max_calls": m["max_calls"], "subsets": {
            k: len(v) for k, v in m["subsets"].items()}, "approval": m["approval"]["live_calls"]}))
    elif cmd == "manifest":
        m = make_manifest(argv[1], argv[2], argv[4:] or STAGE1, standard_file=std_file)
        Path(argv[3]).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"cases": m["cases"], "max_calls": m["max_calls"], "approval": m["approval"]["live_calls"]}))
    elif cmd == "gates":
        print(json.dumps(gates(json.loads(Path(argv[1]).read_text(encoding="utf-8")))))
    elif cmd == "run":
        m = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        got = run(m, argv[2], Path(argv[3]), replay_from=Path(argv[4]) if len(argv) > 4 else None)
        print(json.dumps({k: got[k] for k in ("mode", "calls", "max_calls", "stopped", "tokens")}))
    else:
        raise SystemExit(main.__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
