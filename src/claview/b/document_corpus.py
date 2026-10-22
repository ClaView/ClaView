"""Layered official documents, lossless source spans and bounded model evidence."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .contracts import StageFailure, fingerprint
from .mappings import MappingDictionary
from .search_documents import digest

VERSION = "claview-search/3"
PROFILES = ("D0", "D1", "D2", "D3")


def quoted(value):
    return (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def leaves(value, pointer=""):
    """Include empty containers and nulls in the conservation ledger."""
    if isinstance(value, dict) and value:
        for key in sorted(value):
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from leaves(value[key], pointer + "/" + escaped)
    elif isinstance(value, list) and value:
        for index, member in enumerate(value):
            yield from leaves(member, pointer + f"/{index}")
    else:
        yield pointer, value


def dereference(value, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def audit_catalog(payload):
    """Validate retained classification paths; no synonym or statistical-axis inference."""
    errors, seen, nodes, depths = [], set(), {}, Counter()
    for row in payload["documents"]:
        key = f"{row['org_id']}:{row['table_id']}"
        ids = [s.strip() for s in row["path_ids"].split(">")]
        names = [s.strip() for s in row["path"].split(">")]
        reasons = []
        if key in seen:
            reasons.append("duplicate_table_id")
        seen.add(key)
        if row.get("doc_id", key) != key:
            reasons.append("doc_id_mismatch")
        if len(ids) != len(names) or not all(ids) or not all(names):
            reasons.append("path_length_or_empty_node")
        if len(ids) != len(set(ids)):
            reasons.append("repeated_node_in_path")
        if ids[0] != row["org_id"]:
            reasons.append("root_org_mismatch")
        depth = len(ids) - 1
        depths[depth] += 1
        if "depth" in row and row["depth"] != depth:
            reasons.append("declared_depth_mismatch")
        for index, (node, name) in enumerate(zip(ids, names, strict=False)):
            token = (row["org_id"], node)
            context = (tuple(ids[: index + 1]), tuple(names[: index + 1]))
            if token in nodes and nodes[token] != context:
                reasons.append("node_prefix_or_name_conflict")
            nodes[token] = context
        errors.extend({"doc_id": key, "reason": reason} for reason in sorted(set(reasons)))
    return {
        "tables": len(payload["documents"]),
        "unique_ids": len(seen),
        "node_count": len(nodes),
        "depth_formula": "len(path_ids) - 1; organization root is depth 0",
        "depth_counts": dict(sorted(depths.items())),
        "errors": errors,
        "scope": "retained_catalog_prefix_consistency_not_live_parent_validation",
        "source_date": payload.get("source_date"),
    }


def catalog_audit_from_store(store):
    return audit_catalog(
        {
            "documents": [
                {**d, "path_ids": " > ".join(d["path_ids"])} for d in store.catalog.values()
            ]
        }
    )


def item_paths(record):
    """Derive paths from raw parent codes even when the B adapter cannot normalize a table."""
    rows = record.get("meta", {}).get("ITM", [])
    groups, errors, result = defaultdict(dict), [], {}
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict) or not row.get("ITM_ID") or not row.get("OBJ_ID"):
            errors.append({"index": index, "reason": "item_identity_missing"})
            continue
        axis = str(row["OBJ_ID"])
        code = str(row["ITM_ID"])
        if code in groups[axis] and groups[axis][code][1] != row:
            errors.append({"index": index, "reason": "duplicate_axis_code_conflict"})
        groups[axis][code] = (index, row)
    for axis, members in groups.items():
        for code, (index, row) in members.items():
            path, visited, current, status = [], set(), code, "complete"
            while current:
                if current in visited:
                    status = "parent_cycle"
                    break
                if current not in members:
                    status = "parent_missing"
                    break
                visited.add(current)
                source_index, member = members[current]
                path.insert(
                    0,
                    {
                        "code": current,
                        "name": member.get("ITM_NM"),
                        "pointer": f"/meta/ITM/{source_index}/ITM_NM",
                    },
                )
                current = str(member.get("UP_ITM_ID") or "")
            if status != "complete":
                errors.append({"index": index, "reason": status})
            result[index] = {
                "axis": axis,
                "axis_name": row.get("OBJ_NM"),
                "code": code,
                "path": path,
                "dimension_depth": len(path) - 1 if status == "complete" else None,
                "path_status": status,
            }
    return result, errors


def source_entry(source, pointer, value, label, record_hash, context=None):
    return {
        "label": label,
        "quote": quoted(value),
        "ref": {
            "source": source,
            "pointer": pointer,
            "value_hash": digest(value),
            "record_hash": record_hash,
            "context": context,
        },
    }


def pack(header, entries, max_chars):
    """Split every value at exact source offsets; never drop a long tail."""
    texts, refs, count = [], [], len(header)
    for entry in entries:
        prefix = entry["label"] + ": "
        room = max_chars - len(header) - len(prefix) - 1
        if room < 32:
            raise StageFailure("document_header_too_long")
        quote = entry["quote"]
        offsets = range(0, len(quote), room) if quote else (0,)
        for start in offsets:
            end = min(len(quote), start + room)
            part = quote[start:end]
            line = prefix + part
            if texts and count + len(line) + 1 > max_chars:
                yield header + "\n" + "\n".join(texts), refs
                texts, refs, count = [], [], len(header)
            texts.append(line)
            refs.append({**entry["ref"], "start": start, "end": end, "quote": part})
            count += len(line) + 1
    if texts:
        yield header + "\n" + "\n".join(texts), refs


def build_document_corpus(
    store, mappings_path=None, definitions_path=None, profile="D2", max_chars=2400
):
    if profile not in PROFILES:
        raise StageFailure("invalid_document_profile")
    audit = catalog_audit_from_store(store)
    if audit["errors"]:
        raise StageFailure("document_catalog_invalid")
    dictionary = MappingDictionary(store, mappings_path)
    definitions_payload = (
        json.loads(definitions_path.read_text(encoding="utf-8")) if definitions_path else {}
    )
    definitions = definitions_payload.get("definitions", [])
    include_meta = profile in {"D2", "D3"}
    docs, tables = [], {}
    for key, cat in sorted(store.catalog.items()):
        record = store.records.get(key)
        record_hash = digest(record) if record else None
        try:
            store.get(cat["org_id"], cat["table_id"])
            normalized_status = "available"
        except StageFailure as exc:
            normalized_status = exc.code
        header = f"통계표: {cat['title']}"
        if profile != "D0":
            header += "\n목록 경로: " + cat["path"]
        path_contexts, path_errors = item_paths(record or {})
        groups = defaultdict(list)
        raw_count = sum(1 for _ in leaves(record.get("meta", {}), "/meta")) if record else 0
        cat_hash = digest(cat)
        fields = ("title",) if profile == "D0" else ("title", "org_id", "table_id", "path")
        for field in fields:
            groups[("basic", "catalog")].append(
                source_entry("catalog", "/" + field, cat[field], field, cat_hash)
            )
        if include_meta and record:
            for pointer, value in leaves(record.get("meta", {}), "/meta"):
                parts = pointer.split("/")
                section = parts[2] if len(parts) > 2 else "meta"
                kind = "semantic" if section in {"TBL", "PRD", "UNIT"} else "detail"
                context = None
                label = parts[-1]
                if section == "ITM" and len(parts) > 3 and parts[3].isdigit():
                    index = int(parts[3])
                    path_info = path_contexts.get(index)
                    context = (
                        {"item_index": index, "axis": path_info["axis"], "code": path_info["code"]}
                        if path_info
                        else None
                    )
                    section = "items" if context and context["axis"] == "ITEM" else "dimensions"
                    if path_info and parts[-1] == "ITM_NM":
                        names = [p["name"] for p in path_info["path"] if p["name"]]
                        label = "ITEM 경로" if section == "items" else "분류 값 경로"
                        label += " " + " > ".join(names) + "; ITM_NM"
                elif section == "CMMT":
                    section = "notes"
                groups[(kind, section)].append(
                    source_entry("metadata", pointer, value, label, record_hash, context)
                )
                if section == "items" and parts[-1] == "ITM_NM":
                    groups[("semantic", "measurement")].append(
                        source_entry(
                            "metadata", pointer, value, "공식 ITEM명", record_hash, context
                        )
                    )
                if (
                    section == "notes"
                    and parts[-1] == "CMMT_DC"
                    and isinstance(value, str)
                    and any(term in value for term in ("작성", "산출", "기준", "정의"))
                ):
                    groups[("semantic", "basis")].append(
                        source_entry(
                            "metadata",
                            pointer,
                            value,
                            "작성 기준 관련 원문; 의미 검수 전",
                            record_hash,
                        )
                    )
            raw_names = [quoted(v) for p, v in leaves(record["meta"]) if p.endswith("/ITM_NM")]
            for index, definition in enumerate(definitions):
                if definition.get("table_name") != cat["title"]:
                    continue
                if (
                    definition.get("status") != "verified_official_definition"
                    or not definition.get("source", {}).get("url")
                    or not all(
                        any(term in name for name in raw_names)
                        for term in definition.get("required_dimension_terms", [])
                    )
                ):
                    raise StageFailure("definition_not_verified_or_metadata_mismatch")
                groups[("semantic", "definition")].append(
                    source_entry(
                        "definitions",
                        f"/definitions/{index}/text",
                        definition["text"],
                        "검증 보관 정의",
                        digest(definitions_payload),
                        definition["source"],
                    )
                )
        approved = (
            [
                row["mapping_id"]
                for row in dictionary.rows
                if row["review_status"] == "approved"
                and not dictionary.invalid[row["mapping_id"]]
                and any(
                    e["doc_id"] == key for e in dictionary.concepts[row["concept_id"]]["evidence"]
                )
            ]
            if profile == "D3"
            else []
        )
        for (layer, kind), entries in sorted(groups.items()):
            for index, (text, refs) in enumerate(pack(header, entries, max_chars)):
                contexts = {
                    r["context"]["item_index"]: path_contexts[r["context"]["item_index"]]
                    for r in refs
                    if r["source"] == "metadata" and r["context"]
                }
                docs.append(
                    {
                        "chunk_id": f"{key}:{layer}:{kind}:{index}",
                        "doc_id": key,
                        "org_id": cat["org_id"],
                        "table_id": cat["table_id"],
                        "layer": layer,
                        "kind": kind,
                        "text": text,
                        "text_hash": digest(text),
                        "source_refs": refs,
                        "source_snapshot": store.snapshot,
                        "catalog_snapshot": store.catalog_snapshot,
                        "record_hash": record_hash,
                        "catalog_path_ids": cat["path_ids"],
                        "catalog_path": cat["path_names"],
                        "catalog_depth": cat["depth"],
                        "dimension_contexts": list(contexts.values()),
                        "metadata_status": normalized_status,
                        "embedding_eligible": False,
                        "mapping_ids": [],
                        "approved_mapping_ids": approved,
                    }
                )
        item_rows = (record or {}).get("meta", {}).get("ITM", [])
        notes = (record or {}).get("meta", {}).get("CMMT", [])
        tables[key] = {
            "raw_metadata_present": record is not None,
            "normalized_metadata_status": normalized_status,
            "raw_leaf_count": raw_count,
            "indexed_raw_leaf_count": raw_count if include_meta else 0,
            "unindexed_fields": [] if include_meta else ["all_metadata_by_profile"],
            "parent_path_errors": path_errors,
            "unknowns": {
                "measurement_target_interpretation": "unknown; ITEM names indexed separately",
                "statistical_basis_interpretation": "unknown; retain notes without inference",
                "notes_present": bool(notes),
                "item_units_missing": sum(
                    not row.get("UNIT_NM") for row in item_rows if isinstance(row, dict)
                ),
            },
        }
    counts = Counter((d["layer"], d["kind"]) for d in docs)
    manifest = {
        "version": VERSION,
        "document_profile": profile,
        "catalog_snapshot": store.catalog_snapshot,
        "metadata_snapshot": store.snapshot,
        "mapping_snapshot": fingerprint(mappings_path) if mappings_path else None,
        "definition_snapshot": fingerprint(definitions_path) if definitions_path else None,
        "generator_hash": fingerprint(Path(__file__)),
        "max_chars": max_chars,
        "table_count": len(tables),
        "chunk_count": len(docs),
        "chunks_by_type": {f"{layer}.{kind}": n for (layer, kind), n in sorted(counts.items())},
        "catalog_audit": audit,
        "tables": tables,
        "mapping_report": {**dictionary.describe(), "runtime_enabled": profile == "D3"},
        "mapping_usage": "query_runtime_only; aliases_never_statically_injected",
        "semantic_scope": "explicit_raw_fields_and_retained_verified_definitions_only",
    }
    return {"manifest": manifest, "documents": docs, "corpus_id": digest([manifest, docs])}


def verify_information(store, corpus, definitions_path=None):
    """Compare all metadata source offsets with the saved record, including long tails."""
    definitions = (
        json.loads(definitions_path.read_text(encoding="utf-8")) if definitions_path else {}
    )
    spans, errors, ids = defaultdict(list), [], set()
    record_hashes = {key: digest(row) for key, row in store.records.items()}
    catalog_hashes = {key: digest(row) for key, row in store.catalog.items()}
    definition_hash = digest(definitions)
    for doc in corpus["documents"]:
        key = doc["doc_id"]
        if doc["chunk_id"] in ids:
            errors.append({"chunk_id": doc["chunk_id"], "reason": "duplicate_chunk_id"})
        ids.add(doc["chunk_id"])
        if key not in store.catalog or doc["text_hash"] != digest(doc["text"]):
            errors.append({"chunk_id": doc["chunk_id"], "reason": "identity_or_text_hash"})
            continue
        for ref in doc["source_refs"]:
            original = {
                "catalog": store.catalog[key],
                "metadata": store.records.get(key),
                "definitions": definitions,
            }.get(ref["source"])
            try:
                value = dereference(original, ref["pointer"])
                quote = quoted(value)
                if (
                    ref["value_hash"] != digest(value)
                    or ref["record_hash"]
                    != {
                        "catalog": catalog_hashes[key],
                        "metadata": record_hashes.get(key),
                        "definitions": definition_hash,
                    }[ref["source"]]
                    or quote[ref["start"] : ref["end"]] != ref["quote"]
                    or ref["quote"] not in doc["text"]
                    or not 0 <= ref["start"] <= ref["end"] <= len(quote)
                ):
                    raise ValueError("changed_span")
                if ref["source"] == "metadata":
                    spans[(key, ref["pointer"])].append((ref["start"], ref["end"]))
            except (KeyError, IndexError, TypeError, ValueError):
                errors.append({"chunk_id": doc["chunk_id"], "reason": "source_span_mismatch"})
    omissions = []
    if corpus["manifest"]["document_profile"] in {"D2", "D3"}:
        for key in store.catalog:
            if key not in store.records:
                continue
            for pointer, value in leaves(store.records[key].get("meta", {}), "/meta"):
                ranges = sorted(spans[(key, pointer)])
                end = 0
                for a, b in ranges:
                    if a > end:
                        break
                    end = max(end, b)
                if not ranges or end != len(quoted(value)):
                    omissions.append({"doc_id": key, "pointer": pointer})
    return {
        "official_tables": len(store.catalog),
        "document_tables": len({d["doc_id"] for d in corpus["documents"]}),
        "metadata_values_with_spans": len(spans),
        "source_errors": errors,
        "missing_metadata_fields": omissions,
        "raw_preservation_verified": not errors and not omissions,
    }


def select_model_evidence(
    corpus, query, candidate_ids, *, max_candidates=5, max_chunks=8, max_chars=12000, per_table=3
):
    """Return whole cited chunks within an exact serialized character budget, without a model."""
    from .search import grams

    if min(max_candidates, max_chunks, per_table) < 1 or max_chars < 2:
        raise StageFailure("invalid_evidence_budget")
    by_table = defaultdict(list)
    for doc in corpus["documents"]:
        by_table[doc["doc_id"]].append(doc)
    chosen, omissions = [], []
    seen = set()
    q = grams(query)
    eligible = []
    for key in candidate_ids:
        if key in seen:
            continue
        seen.add(key)
        if key not in by_table:
            omissions.append({"doc_id": key, "reason": "non_corpus_candidate"})
        elif len(eligible) >= max_candidates:
            omissions.append({"doc_id": key, "reason": "candidate_budget"})
        else:
            eligible.append(key)
    for key in eligible:
        rows = sorted(by_table[key], key=lambda d: (-len(q & grams(d["text"])), d["chunk_id"]))
        base = next(d for d in rows if d["layer"] == "basic")
        rows = [base, *[d for d in rows if d is not base]]
        taken = 0
        for doc in rows:
            item = {
                k: doc[k]
                for k in (
                    "chunk_id",
                    "doc_id",
                    "layer",
                    "kind",
                    "text",
                    "text_hash",
                    "source_refs",
                    "catalog_snapshot",
                    "source_snapshot",
                )
            }
            reason = None
            if len(chosen) >= max_chunks:
                reason = "chunk_budget"
            elif taken >= per_table:
                reason = "table_chunk_budget"
            elif len(json.dumps(chosen + [item], ensure_ascii=False, sort_keys=True)) > max_chars:
                reason = "serialized_character_budget"
            if reason:
                omissions.append({"chunk_id": doc["chunk_id"], "doc_id": key, "reason": reason})
            else:
                chosen.append(item)
                taken += 1
    rendered = json.dumps(chosen, ensure_ascii=False, sort_keys=True)
    return {
        "corpus_id": corpus["corpus_id"],
        "selected": chosen,
        "serialized_chars": len(rendered),
        "budget_chars": max_chars,
        "omissions": omissions,
        "scope": "serialized_selected_array_only;_not_token_or_full_prompt_budget",
    }
