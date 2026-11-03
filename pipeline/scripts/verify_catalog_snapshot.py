"""Read-only metadata promotion checks using the actual A and B adapters.

No API clients, keys, ledgers, model calls, dictionary ingestion, or repository writes.
The report distinguishes malformed data from reviewable changes against a prior report.
Run with Python -B; --out is a new JSON evidence file, never overwritten.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
VERSION = "catalog-adapter-validation/1"
LABELS = {"년": "Y", "반기": "H", "분기": "Q", "월": "M"}


def no_network(*args, **kwargs):
    raise RuntimeError("metadata_validator_network_forbidden")


socket.create_connection = no_network
socket.socket.connect = no_network
socket.socket.connect_ex = no_network


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plain(value):
    if dataclasses.is_dataclass(value):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((plain(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def exception_info(exc):
    return {"type": type(exc).__name__, "code": getattr(exc, "code", None), "message": str(exc)}


def compare_maps(before, after):
    old, new = set(before), set(after)
    return {
        "added": {k: after[k] for k in sorted(new - old)},
        "removed": {k: before[k] for k in sorted(old - new)},
        "changed": {k: {"before": before[k], "after": after[k]} for k in sorted(old & new) if before[k] != after[k]},
    }


def compare_rows(before, after):
    left = {digest(row): row for row in before}
    right = {digest(row): row for row in after}
    return {
        "added": [right[k] for k in sorted(right.keys() - left.keys())],
        "removed": [left[k] for k in sorted(left.keys() - right.keys())],
    }


def validate(repo, catalog_path, metadata_path, baseline_path=None):
    for module in ("common", "modules/a", "modules/b"):
        sys.path.insert(0, str(repo / module))
    from claview_a.catalog.loader import load_catalog
    from claview_a.catalog.roles import build_axis_roles
    from claview_a.periods.table_rules import table_meta
    from claview_a.search.tables import selection_catalog
    from claview_b.metadata import MetadataStore, selected_unit
    from claview_common.periods import normalize_meta_period, period_index

    report = {
        "version": VERSION,
        "scope": "saved metadata adapters only; no live/model/numeric validation",
        "external_calls": 0,
        "inputs": {},
        "errors": [],
        "warnings": [],
        "catalog": {},
        "raw_tables": {},
        "a_roles": {},
        "b_units": {},
        "periods": {},
    }

    def issue(level, code, **details):
        report[level].append({"code": code, **details})

    for name, path in (("catalog", catalog_path), ("metadata", metadata_path)):
        report["inputs"][name] = {"path": str(path), "sha256": sha(path)}
    report["inputs"]["validator_sha256"] = sha(__file__)
    report["inputs"]["repo"] = str(repo)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    report["inputs"]["head"] = head.stdout.strip()
    dependencies = [
        "data/a/axis-roles-20261016/축이름_역할_초안.csv",
        "data/a/axis-roles-20261016/일반축_축마다_판정.csv",
        "data/a/axis-roles-20261016/눌린축_목록.csv",
        "modules/a/claview_a/data/axis_role_overrides.csv",
        "modules/a/claview_a/data/basis_words.txt",
        "modules/a/claview_a/data/role_words.json",
        "modules/a/claview_a/data/edition_relations.json",
        "modules/a/claview_a/data/selection_policy.json",
        "modules/a/claview_a/periods/period_policy.json",
        "data/mappings/runtime/unit_supplement.json",
    ]
    report["inputs"]["supplements"] = {
        rel: {"exists": (repo / rel).is_file(), "sha256": sha(repo / rel) if (repo / rel).is_file() else None}
        for rel in dependencies
    }
    adapter_sources = [
        "modules/a/claview_a/catalog/loader.py",
        "modules/a/claview_a/catalog/roles.py",
        "modules/a/claview_a/search/tables.py",
        "modules/a/claview_a/periods/table_rules.py",
        "modules/a/claview_a/periods/resolver.py",
        "modules/b/claview_b/metadata.py",
        "common/claview_common/periods.py",
    ]
    report["inputs"]["adapter_sources"] = {rel: sha(repo / rel) for rel in adapter_sources}

    payload = read_json(catalog_path)
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(documents, list):
        issue("errors", "catalog_documents_not_list")
        documents = []
    report["inputs"]["catalog_source_date"] = payload.get("source_date") if isinstance(payload, dict) else None
    tids = collections.defaultdict(list)
    for position, doc in enumerate(documents):
        if not isinstance(doc, dict) or not all(
            isinstance(doc.get(k), str) and doc[k].strip() for k in ("org_id", "table_id", "title", "path", "path_ids")
        ):
            issue("errors", "catalog_required_fields", position=position)
            continue
        key = f"{doc['org_id']}:{doc['table_id']}"
        if key in report["catalog"]:
            issue("errors", "duplicate_catalog_id", table=key)
        names = [part.strip() for part in doc["path"].split(">")]
        ids = [part.strip() for part in doc["path_ids"].split(">")]
        if len(names) != len(ids) or not all(ids):
            issue("errors", "catalog_path_mismatch", table=key)
        tids[doc["table_id"]].append(key)
        report["catalog"][key] = {
            "title": doc["title"],
            "path": doc["path"],
            "path_ids": doc["path_ids"],
            "category": doc.get("category"),
        }
    for tid, keys in tids.items():
        if len(keys) != 1:
            issue("errors", "a_table_id_not_unique", table_id=tid, keys=keys)

    raw = {}
    label_counts, type_counts, units = collections.Counter(), collections.Counter(), collections.Counter()
    for lineno, line in enumerate(metadata_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            issue("errors", "metadata_json", line=lineno, detail=str(exc))
            continue
        if not isinstance(row, dict) or not all(
            isinstance(row.get(k), str) and row[k].strip() for k in ("orgId", "tblId")
        ):
            issue("errors", "metadata_identity", line=lineno)
            continue
        key = f"{row['orgId']}:{row['tblId']}"
        if key in raw:
            issue("errors", "duplicate_metadata_id", table=key, line=lineno)
        raw[key] = row
        meta = row.get("meta")
        if not isinstance(meta, dict):
            issue("errors", "metadata_not_object", table=key)
            continue
        for field in ("TBL", "PRD", "ITM", "UNIT", "CMMT"):
            rows = meta.get(field)
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                issue("errors", "metadata_rows_not_list_of_objects", table=key, field=field)
        prds = meta.get("PRD") if isinstance(meta.get("PRD"), list) else []
        freqs = collections.Counter()
        for prd in prds:
            if not isinstance(prd, dict):
                continue
            label = prd.get("PRD_SE")
            label_counts[str(label)] += 1
            freqs[str(label)] += 1
            if not isinstance(label, str) or label not in LABELS:
                issue("errors", "unsupported_raw_frequency", table=key, value=label)
                continue
            values = [prd.get("STRT_PRD_DE"), prd.get("END_PRD_DE")]
            for name, value in zip(("start", "end"), values):
                type_counts[f"{label}:{name}:{type(value).__name__}"] += 1
            if not all(isinstance(value, str) and value for value in values):
                issue("errors", "raw_period_must_be_string", table=key, label=label, values=values)
                continue
            try:
                first, last = [normalize_meta_period(v, LABELS[label]) for v in values]
                if period_index(first, LABELS[label]) > period_index(last, LABELS[label]):
                    issue("errors", "period_start_after_end", table=key, label=label, start=first, end=last)
            except Exception as exc:
                issue("errors", "raw_period_invalid_for_b", table=key, label=label, error=exception_info(exc))
        for label, count in freqs.items():
            if count > 1:
                issue(
                    "errors",
                    "duplicate_raw_frequency",
                    table=key,
                    label=label,
                    count=count,
                    consequence="A keeps the first frequency while B replaces it with the last",
                )
        if not prds:
            issue("errors", "period_rows_empty", table=key)

        items = meta.get("ITM") if isinstance(meta.get("ITM"), list) else []
        per_axis, structure = collections.defaultdict(dict), {}
        axis_numbers = collections.defaultdict(set)
        for item in items:
            if not isinstance(item, dict):
                continue
            if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("OBJ_ID", "ITM_ID")):
                issue("errors", "item_identity", table=key)
                continue
            obj, iid = item["OBJ_ID"], item["ITM_ID"]
            if "ITM_NM" not in item or item.get("ITM_NM") not in (None, "") and not isinstance(item["ITM_NM"], str):
                issue("errors", "item_name_type_or_missing", table=key, obj=obj, item=iid)
            if iid in per_axis[obj]:
                issue(
                    "errors", "duplicate_item_code", table=key, obj=obj, item=iid, identical=per_axis[obj][iid] == item
                )
            per_axis[obj][iid] = item
            if obj != "ITEM":
                number = str(item.get("OBJ_ID_SN", ""))
                axis_numbers[number].add(obj)
                if number not in {str(n) for n in range(1, 9)}:
                    issue("errors", "axis_number_invalid", table=key, obj=obj, value=number)
                if "OBJ_NM" not in item:
                    issue("errors", "axis_name_missing", table=key, obj=obj)
            unit = item.get("UNIT_NM")
            units["blank" if not unit else "present"] += 1
            if unit is not None and not isinstance(unit, str):
                issue("errors", "item_unit_type", table=key, obj=obj, item=iid)
            structure[f"{obj}:{iid}"] = {
                k: item.get(k) for k in ("OBJ_NM", "OBJ_ID_SN", "ITM_NM", "ITM_NM_ENG", "UP_ITM_ID", "UNIT_NM")
            }
        if not per_axis.get("ITEM"):
            issue("errors", "item_axis_empty", table=key)
        for number, objs in axis_numbers.items():
            if len(objs) > 1:
                issue("errors", "different_axes_share_b_number", table=key, number=number, objects=sorted(objs))
        for obj, values in per_axis.items():
            for iid, item in values.items():
                parent, seen = item.get("UP_ITM_ID"), {iid}
                while parent:
                    if not isinstance(parent, str):
                        issue("errors", "parent_code_type", table=key, obj=obj, item=iid)
                        break
                    if parent in seen:
                        issue("errors", "parent_cycle", table=key, obj=obj, item=iid)
                        break
                    seen.add(parent)
                    if parent not in values:
                        issue("warnings", "missing_parent", table=key, obj=obj, item=iid, parent=parent)
                        break
                    parent = values[parent].get("UP_ITM_ID")
        report["raw_tables"][key] = {
            "name": row.get("name"),
            "sha256": digest(row),
            "periods": prds,
            "item_count": len(items),
            "structure": structure,
            "units": meta.get("UNIT"),
            "comment_count": len(meta["CMMT"]) if isinstance(meta.get("CMMT"), list) else None,
            "comment_sha256": digest(meta.get("CMMT")),
        }
    missing = sorted(report["catalog"].keys() - raw.keys())
    extra = sorted(raw.keys() - report["catalog"].keys())
    if missing:
        issue("errors", "catalog_without_metadata", tables=missing)
    if extra:
        issue("warnings", "metadata_outside_catalog", tables=extra)

    acatalog, roles, views = None, None, {}
    try:
        acatalog = load_catalog(catalog_path, metadata_path)
        roles = build_axis_roles(acatalog)
        layer = SimpleNamespace(catalog=acatalog, roles=roles, _derived={})
        views = selection_catalog(layer).views
        report["a_roles"] = {
            "settings": plain(roles.settings),
            "links": plain(roles.links),
            "link_problems": plain(roles.link_problems),
            "override_report": plain(roles.override_report),
            "axes": {"/".join(k): plain(v) for k, v in roles.axes.items()},
            "values": {"/".join(k): plain(v) for k, v in roles.values.items()},
            "pressed": plain(roles.pressed),
            "unnamed": plain(acatalog.unnamed),
            "target_dictionary_size": len(roles.target_dict),
        }
    except Exception as exc:
        issue("errors", "a_adapter_failed", error=exception_info(exc))
    supplement_path = repo / "data/mappings/runtime/unit_supplement.json"
    store = None
    try:
        store = MetadataStore(catalog_path, metadata_path, unit_supplement_path=supplement_path)
    except Exception as exc:
        issue("errors", "b_store_failed", error=exception_info(exc))
    if not supplement_path.is_file():
        issue("warnings", "unit_supplement_file_absent", path=str(supplement_path))
    if store is not None:
        for key, supplement in store.unit_supplement.items():
            if key not in report["catalog"] or key not in raw:
                issue("warnings", "unit_supplement_orphan", table=key)
            if not str(supplement.get("source") or "").strip():
                issue("warnings", "unit_supplement_source_missing", table=key)
        for key in sorted(report["catalog"]):
            org, tid = key.split(":", 1)
            try:
                btable = store.get(org, tid)
                bperiods = btable["periods"]
                aper = table_meta(views[tid])["periods"] if tid in views else None
                matched = aper == bperiods
                report["periods"][key] = {"A": aper, "B": bperiods, "equal": matched}
                if not matched:
                    issue("errors", "a_b_period_mismatch", table=key, A=aper, B=bperiods)
                for freq, span in bperiods.items():
                    if period_index(span["start"], freq) > period_index(span["end"], freq):
                        issue("errors", "normalized_start_after_end", table=key, frequency=freq)
                observed = sorted(
                    set(btable["units"])
                    | {v["unit"] for v in btable["items"].values() if v["unit"]}
                    | {v["unit"] for d in btable["dimensions"].values() for v in d["values"].values() if v["unit"]}
                )
                supplement = store.unit_supplement.get(key)
                probes = []
                for iid in btable["items"]:
                    try:
                        probes.append(
                            {
                                "item_id": iid,
                                "unit": selected_unit(btable, iid, {}),
                                "scope": "ITEM and table fallback only; not a complete series selection",
                            }
                        )
                    except Exception as exc:
                        probes.append(
                            {
                                "item_id": iid,
                                "error": exception_info(exc),
                                "scope": "incomplete selection probe; mixed-unit table may be legitimate",
                            }
                        )
                unit_row = {
                    "official_unit_values": observed,
                    "supplement": supplement,
                    "blank_units_allowed": True,
                    "item_only_probes": probes,
                }
                if supplement:
                    unit_row["supplement_status"] = (
                        "all_official_units_empty"
                        if not observed
                        else "official_unit_now_present_matches"
                        if set(observed) == {supplement["unit"]}
                        else "official_unit_now_present_review_required"
                    )
                    if observed:
                        issue(
                            "warnings",
                            "unit_supplement_with_official_units",
                            table=key,
                            official=observed,
                            supplement=supplement["unit"],
                        )
                report["b_units"][key] = unit_row
            except Exception as exc:
                issue("errors", "table_adapter_failed", table=key, error=exception_info(exc))

    report["summary"] = {
        "catalog_count": len(report["catalog"]),
        "metadata_count": len(raw),
        "raw_frequency_counts": dict(label_counts),
        "raw_period_types": dict(type_counts),
        "item_unit_counts": dict(units),
        "a_b_period_tables_compared": len(report["periods"]),
        "a_b_period_tables_equal": sum(r["equal"] for r in report["periods"].values()),
        "a_role_link_problems": len(report["a_roles"].get("link_problems", [])),
        "a_override_outcomes": dict(
            collections.Counter(r["outcome"] for r in report["a_roles"].get("override_report", []))
        ),
        "errors": len(report["errors"]),
        "warnings": len(report["warnings"]),
        "structural_valid": not report["errors"],
    }
    if baseline_path:
        baseline = read_json(baseline_path)
        if baseline.get("version") != VERSION:
            raise ValueError("baseline_validator_version_differs")
        compare = {
            "baseline": {"path": str(baseline_path), "sha256": sha(baseline_path)},
            "catalog": compare_maps(baseline["catalog"], report["catalog"]),
            "table_names": compare_maps(
                {k: v.get("name") for k, v in baseline["raw_tables"].items()},
                {k: v.get("name") for k, v in report["raw_tables"].items()},
            ),
            "periods": compare_maps(baseline["periods"], report["periods"]),
            "units": compare_maps(baseline["b_units"], report["b_units"]),
            "a_axis_roles": compare_maps(baseline["a_roles"].get("axes", {}), report["a_roles"].get("axes", {})),
            "a_value_roles": compare_maps(baseline["a_roles"].get("values", {}), report["a_roles"].get("values", {})),
            "a_link_problems": compare_rows(
                baseline["a_roles"].get("link_problems", []), report["a_roles"].get("link_problems", [])
            ),
            "a_override_report": compare_rows(
                baseline["a_roles"].get("override_report", []), report["a_roles"].get("override_report", [])
            ),
            "errors": compare_rows(baseline["errors"], report["errors"]),
            "warnings": compare_rows(baseline["warnings"], report["warnings"]),
            "supplements": compare_maps(baseline["inputs"]["supplements"], report["inputs"]["supplements"]),
            "adapter_sources": compare_maps(baseline["inputs"]["adapter_sources"], report["inputs"]["adapter_sources"]),
            "structures": {},
            "comments_changed": [],
            "period_change_details": {},
        }
        oldraw, newraw = baseline["raw_tables"], report["raw_tables"]
        for key in sorted(oldraw.keys() & newraw.keys()):
            if oldraw[key]["structure"] != newraw[key]["structure"]:
                compare["structures"][key] = compare_maps(oldraw[key]["structure"], newraw[key]["structure"])
            if oldraw[key]["comment_sha256"] != newraw[key]["comment_sha256"]:
                compare["comments_changed"].append(key)
        for key, change in compare["periods"]["changed"].items():
            old_periods, new_periods = change["before"]["B"], change["after"]["B"]
            differences = []
            for frequency in sorted(old_periods.keys() | new_periods.keys()):
                before, after = old_periods.get(frequency), new_periods.get(frequency)
                if before == after:
                    continue
                if before is None or after is None:
                    kind = "frequency_added" if before is None else "frequency_removed"
                elif before["start"] != after["start"]:
                    kind = "start_changed"
                elif period_index(after["end"], frequency) > period_index(before["end"], frequency):
                    kind = "end_advanced"
                else:
                    kind = "end_retracted"
                differences.append({"frequency": frequency, "kind": kind, "before": before, "after": after})
            compare["period_change_details"][key] = differences
        compare["requires_human_review"] = bool(
            compare["catalog"]["added"]
            or compare["catalog"]["removed"]
            or compare["catalog"]["changed"]
            or compare["structures"]
            or compare["comments_changed"]
            or compare["a_link_problems"]["added"]
            or compare["a_override_report"]["added"]
            or any(compare["a_axis_roles"].values())
            or any(compare["a_value_roles"].values())
            or compare["units"]["changed"]
            or compare["warnings"]["added"]
            or compare["errors"]["added"]
            or any(row["kind"] != "end_advanced" for rows in compare["period_change_details"].values() for row in rows)
            or any(compare["supplements"].values())
            or any(compare["adapter_sources"].values())
        )
        report["summary"]["new_errors"] = len(compare["errors"]["added"])
        report["summary"]["resolved_errors"] = len(compare["errors"]["removed"])
        report["summary"]["existing_errors"] = len(report["errors"]) - len(compare["errors"]["added"])
        report["baseline_comparison"] = compare
    report["limitations"] = [
        "Schema and adapter consistency do not prove statistical meaning or freshness.",
        "Existing role CSV recommendations are not promoted to approved decisions.",
        "Missing units are legal; incomplete ITEM-only probes are not all series combinations.",
        "Catalog removals require official status evidence; absence alone does not mean discontinued.",
        "Numeric KOSIS response consistency and model response replay are separate checks.",
        "No synonym dictionaries or model prompts were loaded or modified by this validator.",
    ]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New JSON evidence file; must not exist")
    parser.add_argument("--baseline", type=Path, help="Prior validator JSON; does not modify it")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("output_exists_use_new_evidence_path")
    report = validate(
        args.repo.resolve(),
        args.catalog.resolve(),
        args.metadata.resolve(),
        args.baseline.resolve() if args.baseline else None,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if args.baseline:
        print(json.dumps({"requires_human_review": report["baseline_comparison"]["requires_human_review"]}))
    raise SystemExit(0 if report["summary"]["structural_valid"] else 1)


if __name__ == "__main__":
    main()
