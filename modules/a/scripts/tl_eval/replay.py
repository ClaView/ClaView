"""Replay of a stored exploration run (Terra first, Sol re-judgment) with the code as it is now (plan p11, E1).

A run directory written by ``explore.run`` holds ``manifest.json`` (the models and the re-search conditions),
``calls.jsonl`` (every request and its raw response) and ``records.json`` (the run records). ``replay_run`` runs each
recorded question again through ``claview_a.graph.run`` with the archived responses as the judges and compares the
new record with the stored one, field by field. Nothing is sent anywhere: there is no transport, and a request
that is not in the archive is recorded as a gap, never answered.

- ``mode="exact"`` (the default): an answer is returned only for a request whose hash (model, instructions, input,
  format, output cap) equals an archived one. A changed prompt or changed cards miss; the case is a ``miss`` gap.
- ``mode="order"``: the archived answers of the case are returned in their order whatever the request is. Each call
  records whether its input equals the archived input (``input_equal``) and whether the whole request (model,
  instructions, input, format, output cap) does (``request_equal``); a case is ``comparable`` only when every request
  is equal (an old answer under a new input or new instructions is not a model result).
- A re-judgment the new code asks for while the archive has none for the case is a ``new_call_not_in_archive`` gap
  (no call is made). An archived failure is raised as the judge error it was.
- Every replayed call goes through the case's call ledger (``claview_a.budget``, plan 56 order 3), in memory: the
  slot (``a_first``, ``a_self_rejudge``) is reserved and settled as not sent (``sent: no``, kind ``replay``); the
  case row keeps the ledger's slot use.

The stored files are read only. ``out_dir`` must not exist. A path naming a protected evaluation set (new200,
independent, blind, holdout) is refused: those sets are never used in an improvement loop.

"No change" means no change in ``COMPARED`` only (plan 55): the route and error, each statistic's outcome, tables
(frequency, period), asks and frequency quotes, every check reason, warning and citation problem with its targets,
every correction with its from and to, the card references, the re-search summary and the unresolved statistics.
``FIELDS_V1`` (codes only, the comparison before plan 55) is reported beside it.
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
from claview_a.budget import Ledger, key_of  # noqa: E402
from claview_a.graph import JudgeError, research_summary  # noqa: E402
from claview_a.graph import run as graph_run  # noqa: E402
from claview_a.judges import text_sha  # noqa: E402

REPLAY_VERSION = "tl-replay-2026-10-31.3"  # .3: the call ledger
PROTECTED = ("new200", "indep", "independent", "blind", "holdout")
MODES = ("exact", "order")
FIELDS_V1 = ("route", "error", "final", "checks", "corrections", "card_refs", "research")
FIELDS = FIELDS_V1 + ("unresolved",)
# what each compared field holds (written to the replay result, so a report names what "no change" covers)
COMPARED = {
    "route": "route", "error": "error code",
    "final": "per statistic: outcome, representative, tables (table_id, frequency, period), asks (axis, options "
             "(value, table_id)), frequency_quotes (quote, for, frequency, point_in_period)",
    "checks": "per statistic: reasons, warnings and citation problems, each whole (code and every target field)",
    "corrections": "per statistic: each correction whole (code, from, to, ...)",
    "card_refs": "card references", "research": "status, end, second_round_outcomes",
    "unresolved": "unresolved statistics, each whole (id, quote, periods, frequencies, calculations, ...)",
}


class ReplayGap(RuntimeError):
    """A call the archive cannot answer: the case ends here and is recorded as a gap."""

    def __init__(self, gap: str):
        super().__init__(gap)
        self.gap = gap


def refuse_protected(path) -> None:
    low = str(path).replace("\\", "/").lower()
    hit = [w for w in PROTECTED if w in low]
    if hit:
        raise PermissionError(f"protected evaluation set in path ({hit}): never replayed in an improvement loop")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recorded_contract(record: dict) -> str:
    """The contract a stored record's answers were written to: ``run_info.versions.contract``. A record without one
    is refused (its version is decided from the commit that made it and registered, never from the answer)."""
    got = ((record.get("run_info") or {}).get("versions") or {}).get("contract")
    if not got:
        raise ValueError("record without a contract version")
    return got


class _Judge:
    """One judge of a replayed case (``first`` or ``rejudge``): archived answers only."""

    def __init__(self, rows: list[dict], purpose: str, mode: str, log: list, ledger: Ledger | None = None):
        self.rows, self.purpose, self.mode, self.log, self.ledger = rows, purpose, mode, log, ledger
        self.by = {r["request_sha256"]: r for r in rows if r.get("outcome") in ("answered", "failed")}
        self.next = 0

    def __call__(self, payload: dict, request: dict) -> str:
        from claview_a.transport.provider import ProviderError, extract

        sha = text_sha(request)
        if self.mode == "exact":
            got = self.by.get(sha)
        else:
            got = self.rows[self.next] if self.next < len(self.rows) else None
            self.next += 1
        row = {"purpose": self.purpose, "request_sha256": sha, "hit": got is not None and got["request_sha256"] == sha}
        if got is not None and self.mode == "order":
            row["input_equal"] = (got.get("request") or {}).get("input") == request.get("input")
            row["request_equal"] = got["request_sha256"] == sha
            row["archived_sha256"] = got["request_sha256"]
        if self.ledger is not None:
            slot = "a_first" if self.purpose == "first" else "a_self_rejudge"
            attempt = self.ledger.reserve(slot, request.get("model"), sha)
            self.ledger.settle(attempt, "no", "replay" if got is not None else "gap", billing="none_reported")
            row.update(attempt_id=attempt, slot=slot, sent="no", billing="none_reported", kind="replay")
        self.log.append(row)
        if got is None:
            raise ReplayGap("new_call_not_in_archive" if not self.rows else "miss")
        if got["outcome"] == "failed":
            raise JudgeError(got["error"])
        try:
            return extract(got["raw_response"])[0]
        except ProviderError as e:
            raise JudgeError(str(e)) from None


def _whole(rows) -> list[str]:
    """Each row whole, in a form that depends on neither dict nor list order."""
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows or [])


def _short(rec: dict) -> dict:
    """The compared fields of a record (``COMPARED``), in a form that does not depend on dict order."""
    old = _short_v1(rec)
    for sid, f in (rec.get("final") or {}).items():
        old["final"][sid]["frequency_quotes"] = _whole(f.get("frequency_quotes"))
    checks = {sid: {k: _whole(c.get(k)) for k in ("reasons", "warnings", "citation")}
              for sid, c in (rec.get("checks") or {}).items()}
    corrections = {sid: _whole(xs) for sid, xs in (rec.get("corrections") or {}).items()}
    return {**old, "checks": checks, "corrections": corrections, "unresolved": _whole(rec.get("unresolved"))}


def _short_v1(rec: dict) -> dict:
    """The fields compared before plan 55 (codes only), in a form that does not depend on dict order."""
    final = {sid: {"outcome": f["outcome"], "representative": f.get("representative"),
                   "tables": [(t["table_id"], t.get("frequency"), json.dumps(t.get("period"), sort_keys=True,
                                                                             ensure_ascii=False))
                              for t in f.get("tables") or []],
                   "asks": [(a.get("axis"), [(o.get("value"), o.get("table_id")) for o in a.get("options") or []])
                            for a in f.get("asks") or []]}
             for sid, f in (rec.get("final") or {}).items()}
    checks = {sid: {"reasons": [r["code"] for r in c.get("reasons") or []],
                    "warnings": [w["code"] for w in c.get("warnings") or []],
                    "citation": [p["code"] for p in c.get("citation") or []]}
              for sid, c in (rec.get("checks") or {}).items()}
    corrections = {sid: [x["code"] for x in xs] for sid, xs in (rec.get("corrections") or {}).items()}
    research = rec.get("research") or None
    if research:
        research = {k: research.get(k) for k in ("status", "end", "second_round_outcomes")}
    return {"route": rec.get("route"), "error": (rec.get("error") or {}).get("code") if rec.get("error") else None,
            "final": final, "checks": checks, "corrections": corrections, "card_refs": rec.get("card_refs"),
            "research": research}


def equality(log: list[dict]) -> dict:
    """{inputs_equal, requests_equal, comparable} of a replayed case's calls: comparable only when every whole
    request equals its archived one (in exact mode every answered call is)."""
    inputs = all(c.get("input_equal", True) for c in log)
    requests = all(c.get("request_equal", c["hit"]) for c in log)
    return {"inputs_equal": inputs, "requests_equal": requests, "comparable": requests}


def diff(before: dict, after: dict, fields: tuple = FIELDS) -> dict:
    """``{field: {"before", "after"}}`` for every compared field that differs (empty: the same). ``fields``
    ``FIELDS_V1``: the comparison before plan 55."""
    short = _short_v1 if fields == FIELDS_V1 else _short
    a, b = short(before), short(after)
    return {f: {"before": a[f], "after": b[f]} for f in fields if a[f] != b[f]}


def replay_run(run_dir, out_dir, layer=None, mode: str = "exact", keys: list[str] | None = None) -> dict:
    """Replay every record of ``run_dir`` (or only ``keys``) and write ``replay.json`` and ``records.json`` to
    ``out_dir``. Returns the summary (also written)."""
    from tl_eval import score as S

    run_dir, out_dir = Path(run_dir), Path(out_dir)
    refuse_protected(run_dir)
    refuse_protected(out_dir)
    if mode not in MODES:
        raise ValueError(mode)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists: a replay is never overwritten")
    files = {n: run_dir / n for n in ("manifest.json", "calls.jsonl", "records.json")}
    before_sha = {n: _sha(p) for n, p in files.items()}
    manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    stored = json.loads(files["records.json"].read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in files["calls.jsonl"].read_text(encoding="utf-8").splitlines() if line.strip()]
    if layer is None:
        from claview_a.catalog.layer import load_layer

        layer = load_layer()
    m = manifest["model"]
    research = manifest.get("research") or {"enabled": False, "cases": []}
    settings = K.Settings(model=m["model"], effort=m["effort"], max_output_tokens=m["max_output_tokens"])
    re_settings = None
    if research.get("enabled"):
        r = research["model"]
        re_settings = K.Settings(model=r["model"], effort=r["effort"], max_output_tokens=r["max_output_tokens"])
    cases, records = {}, {}
    for key in keys or list(stored):
        old = stored[key]
        refuse_protected(key)
        case_id = key.split("|")[2]
        rows = [c for c in calls if c["case_id"] == key]
        first_rows = [c for c in rows if (c.get("request") or {}).get("model") == settings.model]
        re_rows = [c for c in rows if re_settings and (c.get("request") or {}).get("model") == re_settings.model]
        log: list[dict] = []
        ledger = Ledger.memory(key_of(old["question"]))
        first = _Judge(first_rows, "first", mode, log, ledger)
        rejudge = _Judge(re_rows, "rejudge", mode, log, ledger) if re_settings else None
        # the answers are read with the contract the record names (never guessed from their content)
        for j in (first, rejudge):
            if j is not None:
                j.answer_contract = recorded_contract(old)
        listed = case_id in (research.get("cases") or [])
        used = {"rejudge": 0}

        def budget(used=used):
            used["rejudge"] += 1
            return used["rejudge"] <= 1

        row = {"case_id": case_id, "calls": log, "gap": None}
        try:
            state = graph_run(old["question"], layer, first, settings, old["as_of"], research=listed,
                              research_budget=budget if listed else None, research_judge=rejudge,
                              research_settings=re_settings, ledger=ledger)
        except ReplayGap as g:
            row.update(gap=g.gap, diff=None, ledger=ledger.summary()["by_slot"])
            cases[key] = row
            continue
        rec = S.adapt(state)
        rec.update(research_summary(state))
        rec.update(run_id=f"replay:{old.get('run_id')}", mode=f"replay_{mode}", model_name=old.get("model_name"),
                   round=old.get("round"))
        records[key] = rec
        row.update(equality(log))
        row["diff"] = diff(old, rec)
        row["diff_v1"] = diff(old, rec, FIELDS_V1)
        row["ledger"] = ledger.summary()["by_slot"]
        row["archived_calls"] = len(rows)
        row["unused_archived_calls"] = len(rows) - sum(1 for c in log if c["hit"])
        cases[key] = row
    after_sha = {n: _sha(p) for n, p in files.items()}
    if after_sha != before_sha:
        raise RuntimeError("a stored file changed during the replay")
    totals = {"cases": len(cases), "calls": sum(len(c["calls"]) for c in cases.values()),
              "hits": sum(1 for c in cases.values() for x in c["calls"] if x["hit"]),
              "gaps": {g: sum(1 for c in cases.values() if c["gap"] == g) for g in ("miss", "new_call_not_in_archive")},
              "changed": sorted(k for k, c in cases.items() if c.get("diff")),
              "same": sorted(k for k, c in cases.items() if c.get("diff") == {}),
              "changed_v1": sorted(k for k, c in cases.items() if c.get("diff_v1")),
              "not_comparable": sorted(k for k, c in cases.items() if c.get("comparable") is False),
              "inputs_differ": sorted(k for k, c in cases.items() if c.get("inputs_equal") is False),
              "requests_differ": sorted(k for k, c in cases.items() if c.get("requests_equal") is False)}
    out = {"version": REPLAY_VERSION, "mode": mode, "run_id": manifest.get("run_id"),
           "stored_sha256": before_sha, "code": _code_info(), "compared_fields": COMPARED,
           "compared_fields_v1": list(FIELDS_V1), "totals": totals, "cases": cases,
           "note": "archived answers only; no model, B or KOSIS call was made"}
    out_dir.mkdir(parents=True)
    (out_dir / "replay.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _code_info() -> dict:
    """The code the replay ran: the package tree hash and the run info versions."""
    from claview_a.graph import run_info

    pkg = ROOT / "modules" / "a" / "claview_a"
    h = hashlib.sha256()
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(pkg)).replace("\\", "/").encode("utf-8"))
            h.update(p.read_bytes())
    settings = K.Settings(model="replay", effort="low", max_output_tokens=1)
    return {"package_sha256": h.hexdigest(), "versions": run_info("2000-01-01", settings)["versions"]}


def main(argv: list[str]) -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--mode", choices=MODES, default="exact")
    a = ap.parse_args(argv)
    got = replay_run(a.run_dir, a.out_dir, mode=a.mode)
    print(json.dumps(got["totals"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
