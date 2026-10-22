"""Plan 19 stages 4-5: the model inputs of the 198 development questions, before and after (offline; no model or
network call; questions, catalog and the plan 18 manifest are only read).

    python -B -X utf8 scripts/plan19_inputs.py run --before-src <src of the code before> \
        --manifest <plan18 store>/review_period_v02/manifest.json --out runs/plan19/inputs_stage45 [--watch CASE ...]

``run`` makes three dumps in separate processes (``dump``: the code of one ``src`` folder reads every development
question of the corpus and writes its segments, mentions, senses, payload and schema): the code before (``--before-src``,
read only), this checkout's code, and this checkout's code again (determinism). ``compare`` then writes, to a new
folder only: the restoration of the raw question, the determinism check, sizes, per question the missing structure
information, the added meanings a lexical sense carries, the reference possibilities and the omitted candidates, the
changes before -> after (segment kinds, markers, request phrases, name candidates, senses sent), the 20 questions of
the plan 18 manifest against their stored payload and rule results (fields ``segments``, ``mentions`` and ``ref_map``
excluded: they are record fields the rules path does not read), and the watched questions in full.

Golden and the new questions are never read (``dataset == development`` only). Not a model result: the effect of an
input on a model is seen only by new calls.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD_FIELDS = ("segments", "mentions", "ref_map")
INPUT_FILES = ("interpret/segments.py", "interpret/mentions.py", "interpret/tokens.py", "interpret/lexicon.py",
               "interpret/conditions.py", "interpret/condition_rules.py", "interpret/decompose_payload.py",
               "interpret/decompose_schema.py", "interpret/pipeline.py", "catalog/occurrences.py", "catalog/spans.py",
               "catalog/index.py", "catalog/names.py", "data/lexicon.json", "data/decompose_policy.json",
               "data/condition_words.json", "data/prompts/decompose.md")
FREE_AXES = ("classification", "family")  # interpret/table.py DICT_FREE_AXES (axes that name no slot)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def write_new(path: Path, value) -> None:
    with Path(path).open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=1, default=str))


def read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # the branch manifest was written with a BOM


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- dump (runs inside the package of one src folder)


def _a_package(src: Path) -> str:
    """Import name of the A package in one src folder: claview.a (plan 24 layout) or claview_v3 (older snapshots)."""
    return "claview.a" if (src / "claview" / "a" / "__init__.py").is_file() else "claview_v3"


def _dump(src: Path, out: Path) -> dict:
    sys.path.insert(0, str(src))
    name = _a_package(src)

    def mod(sub: str = ""):
        return importlib.import_module(f"{name}.{sub}" if sub else name)

    package = Path(mod().__file__).resolve().parent
    if not package.is_relative_to(src.resolve()):
        raise RuntimeError("the package was not loaded from the given src folder")
    _guard, paths = mod("_guard"), mod("paths")

    _guard.install_network_block()
    catalog_version = mod("catalog.facets").catalog_version
    load_layer = mod("catalog.layer").load_layer
    index_key = mod("catalog.text").index_key
    q4_rows = mod("interpret.condition_rules").q4_rows
    payload_mod = mod("interpret.decompose_payload")
    build_payload, is_input_limit, prompt_text = payload_mod.build_payload, payload_mod.is_input_limit, payload_mod.prompt_text
    build_schema = mod("interpret.decompose_schema").build_schema
    mention_spans = mod("interpret.mentions").mention_spans
    interpret_question = mod("interpret.pipeline").interpret_question
    normalize = mod("reused.q_nodes").normalize

    def meanings(text: str, positive_only: bool = True) -> list[str]:
        return sorted({f"{r['axis']}:{index_key(str(r['value']))}" for r in q4_rows(normalize(text or "")["query"])
                       if r["polarity"] == "+" or not positive_only})

    def asked(conditions: list[dict]) -> list[str]:
        excluded = [c["span"] for c in conditions if c["kind"] == "exclude"]
        out = set()
        for c in conditions:
            attrs = c.get("attrs") or {}
            axis, value = attrs.get("axis"), attrs.get("value") or c.get("value")
            if c["kind"] in ("basis", "calc") and axis and value and attrs.get("polarity") == "+" \
                    and not any(a <= c["span"][0] and c["span"][1] <= b for a, b in excluded):
                out.add(f"{axis}:{index_key(str(value))}")
        return sorted(out)

    def one(row: dict, layer) -> dict:
        q = row["question"]
        a = interpret_question(q, layer)
        text, conds, segs, ments = a["text"], a["conditions"], a["segments"], a["mentions"]
        payload, local = build_payload(text, conds, segs, ments, layer)
        limit = is_input_limit(payload)
        schema = None if limit else build_schema(payload)
        spans = mention_spans(text, conds, segs, layer)
        raw = text["raw"]
        markers = local.get("segment_markers") or {}
        senses = {}
        for z, s in local["senses"].items():
            names = sorted({s["name"] or ""} | {r.get("value_name") or "" for r in s["rows"]})
            senses[z] = {"name": s["name"], "role": s["role"], "path": s["path"], "rows": len(s["rows"]),
                         "rows_digest": digest(s["rows"]), "names": names,
                         "meanings": sorted(set().union(*(meanings(n) for n in names))) if names else [],
                         "tables": sorted({r["table_id"] for r in s["rows"]})}
        restored = "".join(s["quote"] for s in segs) == q
        if not limit:
            restored = restored and payload["raw"] == q and "".join(x["text"] for x in payload["segments"]) == q
        rules_view = json.loads(canonical({k: v for k, v in a.items() if k not in RECORD_FIELDS}))
        return {
            "case_id": row["case_id"], "question": q, "restored": restored,
            "input_limit": payload.get("input_limit"),
            "payload": payload, "payload_digest": digest(payload), "schema_digest": digest(schema),
            "payload_chars": len(json.dumps(payload, ensure_ascii=False)),
            "schema_chars": len(json.dumps(schema, ensure_ascii=False)) if schema is not None else 0,
            "rules_digest": digest(rules_view),
            "segments": [{"id": s["segment_id"], "span": list(s["span"]), "kind": s["kind"], "quote": s["quote"],
                          "marker": markers.get(s["segment_id"])} for s in segs],
            "mentions": [{"id": m["mention_id"], "kind": m["kind"], "span": spans.get(m["mention_id"]),
                          "quote": raw[slice(*spans[m["mention_id"]])] if m["mention_id"] in spans else None,
                          "segment_ids": list(m["segment_ids"]), "marker": m["marker"],
                          "candidates": len(m["candidates"])} for m in ments],
            "conditions": [{"id": c["id"], "kind": c["kind"], "quote": c["quote"], "span": list(c["span"]),
                            "axis": (c.get("attrs") or {}).get("axis"),
                            "polarity": (c.get("attrs") or {}).get("polarity")} for c in conds],
            "senses": senses, "mention_senses": local["mention_senses"], "mention_kept": local["mention_kept"],
            "asked": asked(conds), "written": meanings(raw, positive_only=False),
            "spans_available": bool(local.get("mention_spans")),
            "determinism": digest({"payload": payload, "schema": schema, "kept": local["mention_kept"],
                                   "senses": {z: [s["name"], s["role"], s["path"], s["rows_digest"]]
                                              for z, s in senses.items()}}),
        }

    corpus = read(paths.QUESTIONS_417)
    questions = [r for r in corpus if r.get("dataset") == "development"]
    layer = load_layer(include_external_synonyms=False)
    rows = {r["case_id"]: one(r, layer) for r in questions}
    result = {"src": str(src), "package": str(package), "catalog_version": catalog_version(layer),
              "prompt_digest": digest(prompt_text()),
              "input_files": {f: sha256_file(package / f) for f in INPUT_FILES if (package / f).is_file()},
              "questions": len(rows), "datasets_read": sorted({r.get("dataset") for r in questions}),
              "network_attempts_blocked": len(_guard.NETWORK_ATTEMPTS), "rows": rows}
    write_new(out, result)
    return {k: v for k, v in result.items() if k != "rows"}


# ---------------------------------------------------------------- compare (plain JSON; no package import)


def _name_mentions(row: dict) -> list[dict]:
    return [m for m in row["mentions"] if m["kind"] == "name"]


def _kept_names(row: dict, m: dict) -> list[list[str]]:
    return [[row["senses"][z]["name"], row["senses"][z]["role"]] for z in row["mention_kept"].get(m["id"], [])]


def _omitted(row: dict, m: dict) -> list[list[str]]:
    kept = set(row["mention_kept"].get(m["id"], []))
    return [[row["senses"][z]["name"], row["senses"][z]["role"]] for z in row["mention_senses"].get(m["id"], [])
            if z not in kept]


def _carrying_omitted(row: dict) -> list[dict]:
    """Asked meanings (positive explicit basis or calc conditions) that a sense of a name mention carries while no
    sense sent for that mention does."""
    out = []
    for m in _name_mentions(row):
        every = row["mention_senses"].get(m["id"], [])
        kept = row["mention_kept"].get(m["id"], [])
        for meaning in row["asked"]:
            if any(meaning in row["senses"][z]["meanings"] for z in every) and \
                    not any(meaning in row["senses"][z]["meanings"] for z in kept):
                out.append({"mention": m["quote"], "meaning": meaning})
    return out


def _inner(row: dict, m: dict) -> bool:
    segs = {s["id"]: s for s in row["segments"]}
    if len(m["segment_ids"]) != 1 or not m["span"]:
        return False
    return tuple(m["span"]) != tuple(segs[m["segment_ids"][0]]["span"])


def per_question(row: dict) -> dict:
    p = row["payload"]
    trunc = p.get("truncation") or {}
    names = _name_mentions(row)
    sent = {z for ids in row["mention_kept"].values() for z in ids}
    added = []
    for z in sorted(sent, key=lambda x: int(x[1:])):
        s = row["senses"][z]
        extra = [x for x in s["meanings"] if x.split(":")[0] not in FREE_AXES and x not in row["written"]]
        if s["path"] == "lexical" and extra:
            added.append({"sense": s["name"], "role": s["role"], "adds": extra})
    return {
        "restored": row["restored"], "input_limit": row["input_limit"],
        "payload_chars": row["payload_chars"], "schema_chars": row["schema_chars"],
        "missing_structure": {
            "names_without_candidates": [m["quote"] for m in names if m["candidates"] == 0],
            "shrunk": trunc.get("shrunk") or [], "condition_attrs_dropped": "condition_attrs" in (trunc.get("shrunk") or []),
            "senses_omitted": bool(trunc.get("senses_omitted")), "relations_omitted": bool(trunc.get("relations_omitted")),
            "unclassified_roles": bool(trunc.get("unclassified_roles"))},
        "added_meanings": added,
        "references": {"segments": len(row["segments"]), "name_mentions": len(names),
                       "name_mentions_with_senses": sum(bool(row["mention_kept"].get(m["id"])) for m in names),
                       "inner_name_mentions": sum(_inner(row, m) for m in names),
                       "spans_available": row["spans_available"],
                       "request_segments": sum(s["kind"] == "request" for s in row["segments"]),
                       "marker_segments": sorted(s["marker"] for s in row["segments"] if s["marker"])},
        "omitted_candidates": [{"mention": m["quote"], "omitted": _omitted(row, m)} for m in names if _omitted(row, m)],
        "carrying_omitted": _carrying_omitted(row),
    }


def changes(before: dict, after: dict) -> dict:
    bspan = {tuple(s["span"]): s for s in before["segments"]}
    seg_kind = [{"quote": s["quote"], "before": bspan[tuple(s["span"])]["kind"], "after": s["kind"]}
                for s in after["segments"] if tuple(s["span"]) in bspan and bspan[tuple(s["span"])]["kind"] != s["kind"]]
    marks = [{"quote": s["quote"], "before": bspan[tuple(s["span"])]["marker"], "after": s["marker"]}
             for s in after["segments"] if tuple(s["span"]) in bspan and bspan[tuple(s["span"])]["marker"] != s["marker"]]
    req_b = sorted(m["quote"] for m in before["mentions"] if m["kind"] == "request")
    req_a = sorted(m["quote"] for m in after["mentions"] if m["kind"] == "request")
    mark_b = sorted((m["quote"], m["marker"]) for m in before["mentions"] if m["kind"] == "marker")
    mark_a = sorted((m["quote"], m["marker"]) for m in after["mentions"] if m["kind"] == "marker")
    bn = {tuple(m["span"]): m for m in _name_mentions(before) if m["span"]}
    cand = []
    kept = []
    for m in _name_mentions(after):
        old = bn.get(tuple(m["span"])) if m["span"] else None
        if old is None:
            cand.append({"quote": m["quote"], "before": None, "after": m["candidates"]})
            continue
        if old["candidates"] != m["candidates"]:
            cand.append({"quote": m["quote"], "before": old["candidates"], "after": m["candidates"]})
        kb, ka = _kept_names(before, old), _kept_names(after, m)
        if kb != ka:
            kept.append({"quote": m["quote"], "before": kb, "after": ka,
                         "now_sent": [x for x in ka if x not in kb], "no_longer_sent": [x for x in kb if x not in ka]})
    return {"payload_changed": before["payload_digest"] != after["payload_digest"],
            "rules_equal": before["rules_digest"] == after["rules_digest"],
            "segment_kinds": seg_kind, "segment_markers": marks,
            "request_phrases": {"before": req_b, "after": req_a} if req_b != req_a else None,
            "marker_mentions": {"removed": [x for x in mark_b if x not in mark_a],
                                "added": [x for x in mark_a if x not in mark_b]},
            "name_candidates": cand, "senses_sent": kept}


def _summary_rows(payload: dict) -> dict:
    out: dict[str, list] = {}
    for r in payload.get("candidate_summary") or []:
        out.setdefault(",".join(r["segment_ids"]), []).append([r["name"], r["role"], r["path"]])
    return out


def diff20(manifest: dict, after: dict) -> dict:
    rows = []
    for x in manifest["inputs"]:
        a = after["rows"].get(x["case_id"])
        if a is None:
            rows.append({"case_id": x["case_id"], "missing_after": True})
            continue
        stored, now = x["payload"], a["payload"]
        stored_rules = {k: v for k, v in x["rules"].items() if k not in RECORD_FIELDS}
        segs_b = {s["id"]: s for s in stored.get("segments") or []}
        seg_diff = [{"id": s["id"], "text": s["text"], "before": [segs_b[s["id"]]["kind"], segs_b[s["id"]]["marker"]],
                     "after": [s["kind"], s["marker"]]} for s in now.get("segments") or []
                    if s["id"] in segs_b and (segs_b[s["id"]]["text"], segs_b[s["id"]]["kind"], segs_b[s["id"]]["marker"])
                    != (s["text"], s["kind"], s["marker"])]
        sb, sa = _summary_rows(stored), _summary_rows(now)
        cand_diff = {k: {"before": sb.get(k), "after": sa.get(k)} for k in sorted(set(sb) | set(sa)) if sb.get(k) != sa.get(k)}
        cond_diff = [c["id"] for c, d in zip(stored.get("conditions") or [], now.get("conditions") or []) if c != d]
        rows.append({"case_id": x["case_id"], "question": x["question"],
                     "payload_equal": digest(stored) == digest(now),
                     "rules_equal_excluding_record_fields": digest(json.loads(canonical(stored_rules))) == a["rules_digest"],
                     "segments_equal_count": len(stored.get("segments") or []) == len(now.get("segments") or []),
                     "segment_diffs": seg_diff, "candidate_diffs": cand_diff, "condition_row_diffs": cond_diff,
                     "truncation": {"before": stored.get("truncation"), "after": now.get("truncation")}
                     if stored.get("truncation") != now.get("truncation") else None,
                     "payload_chars": {"before": len(json.dumps(stored, ensure_ascii=False)), "after": a["payload_chars"]}})
    return {"excluded_fields": list(RECORD_FIELDS), "questions": len(rows),
            "payload_equal": sum(bool(r.get("payload_equal")) for r in rows),
            "rules_equal_excluding_record_fields": sum(bool(r.get("rules_equal_excluding_record_fields")) for r in rows),
            "rows": rows}


def watch_rows(before: dict, after: dict, cases: list[str]) -> dict:
    out = {}
    for cid in cases:
        b, a = before["rows"].get(cid), after["rows"].get(cid)
        if a is None:
            out[cid] = None
            continue
        bn = {tuple(m["span"]): m for m in _name_mentions(b) if m["span"]} if b else {}
        out[cid] = {"question": a["question"], "asked": a["asked"], "carrying_omitted_before": _carrying_omitted(b) if b else None,
                    "carrying_omitted_after": _carrying_omitted(a), "mentions": [
                        {"quote": m["quote"], "candidates": m["candidates"],
                         "sent_before": _kept_names(b, bn[tuple(m["span"])]) if m["span"] and tuple(m["span"]) in bn else None,
                         "sent_after": _kept_names(a, m), "omitted_after": _omitted(a, m),
                         "sent_meanings_after": {a["senses"][z]["name"]: a["senses"][z]["meanings"]
                                                 for z in a["mention_kept"].get(m["id"], [])}}
                        for m in _name_mentions(a)]}
    return out


def _stats(values: list[int]) -> dict:
    v = sorted(values)
    return {"min": v[0], "median": v[len(v) // 2], "max": v[-1]} if v else {}


def compare(before_path: Path, after_path: Path, again_path: Path, manifest_path: Path | None, watch: list[str],
            out: Path, branch_manifest: Path | None, new_folder: bool = True) -> dict:
    before, after, again = read(before_path), read(after_path), read(again_path)
    cases = sorted(after["rows"])
    perq = {cid: per_question(after["rows"][cid]) for cid in cases}
    diffs = {cid: changes(before["rows"][cid], after["rows"][cid]) for cid in cases if cid in before["rows"]}
    det = [cid for cid in cases if again["rows"].get(cid, {}).get("determinism") != after["rows"][cid]["determinism"]]
    count = lambda key: sum(bool(d[key]) for d in diffs.values())  # noqa: E731
    kind_changes: dict[str, int] = {}
    for d in diffs.values():
        for x in d["segment_kinds"]:
            k = f'{x["before"]}->{x["after"]}'
            kind_changes[k] = kind_changes.get(k, 0) + 1
    marker_removed: dict[str, int] = {}
    marker_added: dict[str, int] = {}
    for d in diffs.values():
        for _, m in d["marker_mentions"]["removed"]:
            marker_removed[m] = marker_removed.get(m, 0) + 1
        for _, m in d["marker_mentions"]["added"]:
            marker_added[m] = marker_added.get(m, 0) + 1
    branch = None
    if branch_manifest is not None and branch_manifest.is_file():
        hashes = {x["p"].replace("\\", "/").lstrip("/"): x["h"].lower() for x in read(branch_manifest)}
        branch = {side: {f: (hashes.get(f"src/claview/a/{f}") or hashes.get(f"src/claview_v3/{f}")) == h for f, h in dump["input_files"].items()}
                  for side, dump in (("before", before), ("after", after))}
    summary = {
        "stage": "plan19_inputs_stage45", "real_calls": 0,
        "network_attempts_blocked": {"before": before["network_attempts_blocked"], "after": after["network_attempts_blocked"],
                                     "again": again["network_attempts_blocked"]},
        "datasets_read": sorted(set(before["datasets_read"]) | set(after["datasets_read"])),
        "questions": len(cases), "catalog_version": {"before": before["catalog_version"], "after": after["catalog_version"]},
        "prompt_changed": before["prompt_digest"] != after["prompt_digest"],
        "input_files_changed": sorted(f for f in after["input_files"] if before["input_files"].get(f) != after["input_files"][f]),
        "input_files_equal_to_branch": branch,
        "restored": sum(perq[c]["restored"] for c in cases),
        "deterministic": len(cases) - len(det), "not_deterministic": det,
        "input_limit": sum(bool(perq[c]["input_limit"]) for c in cases),
        "payload_chars": {"before": _stats([before["rows"][c]["payload_chars"] for c in cases if c in before["rows"]]),
                          "after": _stats([perq[c]["payload_chars"] for c in cases])},
        "shrunk": sum(bool(perq[c]["missing_structure"]["shrunk"]) for c in cases),
        "condition_attrs_dropped": sum(perq[c]["missing_structure"]["condition_attrs_dropped"] for c in cases),
        "names_without_candidates": {"before": sum(sum(m["candidates"] == 0 for m in _name_mentions(before["rows"][c]))
                                                   for c in cases if c in before["rows"]),
                                     "after": sum(len(perq[c]["missing_structure"]["names_without_candidates"]) for c in cases)},
        "questions_with_omitted_senses": sum(bool(perq[c]["omitted_candidates"]) for c in cases),
        "carrying_omitted": {"before": sum(len(_carrying_omitted(before["rows"][c])) for c in cases if c in before["rows"]),
                             "after": sum(len(perq[c]["carrying_omitted"]) for c in cases)},
        "added_meaning_senses_sent": sum(len(perq[c]["added_meanings"]) for c in cases),
        "changes": {"payload_changed": count("payload_changed"),
                    "rules_equal_excluding_record_fields": sum(d["rules_equal"] for d in diffs.values()),
                    "questions_compared": len(diffs),
                    "questions_with_segment_kind_changes": count("segment_kinds"), "segment_kind_changes": kind_changes,
                    "questions_with_segment_marker_changes": count("segment_markers"),
                    "questions_with_request_phrase_changes": count("request_phrases"),
                    "marker_mentions_removed": marker_removed, "marker_mentions_added": marker_added,
                    "questions_with_name_candidate_changes": count("name_candidates"),
                    "questions_with_sent_sense_changes": count("senses_sent")},
        "notes": ["development questions only (golden and the new questions are not read)",
                  "input comparison only; not a model result",
                  "rules_equal_excluding_record_fields: the rule analysis without segments, mentions and ref_map"]}
    if new_folder:
        out.mkdir(parents=True, exist_ok=False)
    write_new(out / "per_question.json", {c: {**perq[c], "changes": diffs.get(c)} for c in cases})
    if manifest_path is not None:
        d20 = diff20(read(manifest_path), after)
        summary["plan18_20"] = {k: v for k, v in d20.items() if k != "rows"}
        write_new(out / "diff20.json", d20)
    if watch:
        write_new(out / "watch.json", watch_rows(before, after, watch))
    write_new(out / "summary.json", summary)
    return summary


# ---------------------------------------------------------------- run: three dumps in their own processes, then compare


def _dump_process(src: Path, out: Path) -> None:
    env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(src)}
    subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "dump", "--src", str(src),
                    "--out", str(out)], check=True, env=env)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--src", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    for name in ("compare", "run"):
        c = sub.add_parser(name)
        c.add_argument("--out", type=Path, required=True, help="a new output folder")
        c.add_argument("--manifest", type=Path, help="the plan 18 manifest (read only)")
        c.add_argument("--watch", nargs="*", default=[], help="case IDs written out in full")
        c.add_argument("--branch-manifest", type=Path, default=ROOT / "runs" / "plan19" / "branch_manifest_split.json")
        if name == "compare":
            c.add_argument("--before", type=Path, required=True)
            c.add_argument("--after", type=Path, required=True)
            c.add_argument("--again", type=Path, required=True)
        else:
            c.add_argument("--before-src", type=Path, required=True, help="src folder of the code before (read only)")
    args = parser.parse_args(argv)
    if args.command == "dump":
        print(json.dumps(_dump(args.src, args.out), ensure_ascii=False, indent=1))
        return
    if args.command == "run":
        args.out.mkdir(parents=True, exist_ok=False)  # a new folder only
        dumps = args.out / "dumps"
        dumps.mkdir()
        before, after, again = dumps / "before.json", dumps / "after.json", dumps / "after_again.json"
        _dump_process(args.before_src.resolve(), before)
        _dump_process((ROOT / "src").resolve(), after)
        _dump_process((ROOT / "src").resolve(), again)
        summary = compare(before, after, again, args.manifest, args.watch, args.out, args.branch_manifest,
                          new_folder=False)
    else:
        summary = compare(args.before, args.after, args.again, args.manifest, args.watch, args.out,
                          args.branch_manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
