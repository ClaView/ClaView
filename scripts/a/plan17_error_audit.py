"""Offline selector audit on immutable saved interpretations; never runs interpretation/model.

Expected table groups are read only after selection, for diagnostics, not ranking.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from claview.a import _guard
_guard.install_network_block()
from claview.a.catalog.layer import load_layer
from claview.a.select.graph import select_tables
from claview.a.select.tables import selection_catalog
from claview.a.reused.table_sets import score_sets
from score_selection import load_groups, output_tables
from plan15_baseline import interpretation_view, selection_view


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def code_hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "src" / "claview" / "a").rglob("*"))
            if p.is_file() and p.suffix in (".py", ".json", ".jsonl", ".md")}


def main(source, output, manifest=None):
    source, output = source.resolve(), output.resolve()
    if output.exists() or output == source or source in output.parents:
        raise ValueError("output must be new and outside source")
    paths = sorted(p for p in source.glob("*.json") if len(p.stem) == 64)
    versions = code_hashes()
    dates = {i["case_id"]: i["as_of"] for i in json.loads(manifest.read_text(encoding="utf-8"))["inputs"]} if manifest else {}
    layer = load_layer(include_external_synonyms=False)
    views = selection_catalog(layer).views
    expected = load_groups()
    output.mkdir(parents=True)
    rows = []
    for n, path in enumerate(paths, 1):
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        old = json.loads(path.read_text(encoding="utf-8"))
        a = old["analysis"]
        ahash = digest(a)
        as_of = old.get("as_of") or dates[old["case_id"]]
        got = select_tables(a, layer, as_of, selection_mode="refutation")
        assert digest(a) == ahash
        assert hashlib.sha256(path.read_bytes()).hexdigest() == file_hash
        tables = output_tables(got["selections"])
        groups = expected.get(old["case_id"], [])
        pool = sorted({t for s in got["selections"] for t in s["signals"]["plan17"]["candidate_ids"]})
        score = score_sets(groups, tables, [], pool, 5) if groups else None
        detail = {"case_id": old["case_id"], "call_id": old["call_id"], "profile_id": old["profile_id"],
                  "repeat": old["repeat"], "as_of": as_of, "question": a["text"]["raw"],
                  "source_hash": file_hash, "analysis_hash": ahash, "analysis": a,
                  "interpretation": interpretation_view(a), "conditions": a["conditions"],
                  "selections": got["selections"], "evidence": got["evidence"],
                  "selection": selection_view(got), "expected_groups": groups,
                  "expected_titles": {t: views[t].title for g in groups for t in g if t in views},
                  "score": score, "expected_groups_in_pool": [any(t in pool for t in g) for g in groups]}
        (output / path.name).write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append({k: detail[k] for k in ("case_id", "call_id", "profile_id", "repeat", "question", "source_hash",
                     "analysis_hash", "interpretation", "conditions", "expected_groups", "expected_titles",
                     "score", "expected_groups_in_pool", "selection")})
        if n % 20 == 0:
            print(json.dumps({"done": n, "total": len(paths)}), flush=True)
    assert not _guard.NETWORK_ATTEMPTS
    assert code_hashes() == versions, "source code changed during the audit"
    summary = {"rows": rows, "source": str(source.relative_to(ROOT)), "model_calls": 0, "network_attempts": 0,
               "total": len(rows), "correct": sum(bool(r["score"] and r["score"]["set_exact"]) for r in rows)}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "code_hashes.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    main(args.source, args.output, args.manifest)
