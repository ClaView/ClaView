"""Reproducible search documents derived from official metadata, never golden answers."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .contracts import Json, StageFailure, fingerprint
from .metadata import MetadataStore

DOCUMENT_VERSION = "claview-search/2"
HTML_MARKUP = re.compile(
    r"<!--.*?-->|<![A-Za-z][^<>]*>|</?[A-Za-z][A-Za-z0-9:_-]*"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|[^'\"<>])*)?\s*/?>",
    re.DOTALL,
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def clean_text(value) -> str:
    # Numeric/Korean references in angle brackets are text, not HTML tag names.
    return " ".join(html.unescape(HTML_MARKUP.sub(" ", str(value or ""))).split())


def approved_aliases(store: MetadataStore, path: Path | None) -> tuple[dict, Json]:
    by_table: dict[str, list[Json]] = {}
    counts = Counter()
    payload = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
    rows = payload.get("mappings", [])
    if payload.get("schema_version") == "claview-mappings/2":
        return {}, {"total": len(rows), "query_runtime_only": len(rows)}
    for row in rows:
        if row.get("status") != "approved":
            counts["pending_review"] += 1
            continue
        if row.get("relation") != "alias":
            counts["non_alias_not_applied"] += 1
            continue
        if row.get("condition"):
            counts["conditional_alias_deferred_to_runtime"] += 1
            continue
        expression = clean_text(row.get("input_expression"))
        targets = [
            f"{row.get('scope', {}).get('org_id')}:{tid}"
            for tid in row.get("scope", {}).get("table_ids", [])
        ]
        if not expression or not targets or any(k not in store.catalog for k in targets):
            raise StageFailure("invalid_approved_mapping")
        for key in targets:
            by_table.setdefault(key, []).append(
                {
                    "mapping_id": row["mapping_id"],
                    "expression": expression,
                    "canonical_expression": clean_text(row.get("canonical_expression")),
                    "condition": clean_text(row.get("condition")),
                    "normalization_only": bool(row.get("normalization_only")),
                }
            )
        counts["applied_aliases"] += 1
    return by_table, {"total": len(rows), **dict(counts)}


def chunks(header: str, lines: list[str], max_chars: int) -> list[str]:
    """Repeat table context; split long fields without silently discarding their tails."""
    room = max_chars - len(header) - 1
    if room < 200:
        raise StageFailure("document_header_too_long")
    result, current = [], []
    length = 0
    for line in lines:
        for start in range(0, len(line), room):
            part = line[start : start + room]
            if current and length + len(part) + 1 > room:
                result.append(header + "\n" + "\n".join(current))
                current, length = [], 0
            current.append(part)
            length += len(part) + 1
    if current:
        result.append(header + "\n" + "\n".join(current))
    return result or [header]


def filter_note(text: str) -> tuple[str, str | None]:
    """Remove contact-only entries, not statistical definitions containing similar words."""
    phone = re.search(r"\d{2,3}-\d{3,4}-\d{3,4}", text)
    contact = "문의사항" in text or "문의하시" in text or phone
    semantic = any(
        word in text for word in ("잠정", "확정", "계정", "작성", "기준", "산출", "통계는")
    )
    if contact and not semantic:
        return "", "contact_only"
    return text, None


def value_entry(axis: str, code: str, value: Json, axis_name: str = "") -> Json:
    path = value.get("path", [{"code": code, "name": value["name"]}])
    label = "ITEM" if axis == "ITEM" else f"분류축 {axis_name}"
    text = f"{label}: " + " > ".join(p["name"] for p in path)
    if value["unit"]:
        text += "; 단위: " + value["unit"]
    return {
        "text": text,
        "refs": [{"axis": axis, "code": code, "path": path, "unit": value["unit"]}],
    }


def pack_entries(header: str, entries: list[Json], max_chars: int):
    """Keep exact per-chunk evidence references even when a long entry must be split."""
    room = max_chars - len(header) - 1
    if room < 200:
        raise StageFailure("document_header_too_long")
    lines, refs, length = [], [], 0
    for entry in entries:
        for start in range(0, len(entry["text"]), room):
            part = entry["text"][start : start + room]
            if lines and length + len(part) + 1 > room:
                yield header + "\n" + "\n".join(lines), refs
                lines, refs, length = [], [], 0
            lines.append(part)
            refs.extend(entry.get("refs", []))
            length += len(part) + 1
    if lines:
        yield header + "\n" + "\n".join(lines), refs


def dimension_groups(dim: Json):
    """Separate hierarchical root branches; pack flat siblings in one axis group."""
    branches, flat = defaultdict(list), []
    for code, value in sorted(
        dim["values"].items(), key=lambda pair: tuple(p["code"] for p in pair[1]["path"])
    ):
        root = value["path"][0]["code"]
        root_value = dim["values"][root]
        if len(value["path"]) == 1 and root_value["is_leaf"]:
            flat.append((code, value))
        else:
            branches[root].append((code, value))
    for root, values in branches.items():
        if sum(len(" > ".join(p["name"] for p in v["path"])) + 35 for _, v in values) <= 500:
            flat.extend(values)
        else:
            yield [{"code": root, "name": dim["values"][root]["name"]}], values
    if flat:
        yield [], flat


def build_search_documents(
    store: MetadataStore,
    mappings_path: Path | None = None,
    max_chars: int = 2400,
    definitions_path: Path | None = None,
) -> Json:
    aliases, mapping_report = approved_aliases(store, mappings_path)
    definitions = (
        json.loads(definitions_path.read_text(encoding="utf-8"))["definitions"]
        if definitions_path
        else []
    )
    docs, incomplete, exclusions = [], [], []
    for key, cat in sorted(store.catalog.items()):
        header = f"통계표: {cat['title']}\n소속 경로: {cat['path']}"
        summary, groups, unit_evidence = [], [], []

        def line(text, refs=None):
            return {"text": text, "refs": refs or []}

        try:
            meta = store.get(cat["org_id"], cat["table_id"])
            names = [
                v["name"] for dim in meta["dimensions"].values() for v in dim["values"].values()
            ]
            for definition in definitions:
                if definition["table_name"] != cat["title"]:
                    continue
                if definition["status"] != "verified_official_definition" or not definition.get(
                    "source", {}
                ).get("url"):
                    raise StageFailure("definition_not_verified")
                if not all(
                    any(term in name for name in names)
                    for term in definition["required_dimension_terms"]
                ):
                    raise StageFailure("definition_metadata_mismatch")
                summary.append(
                    line(
                        "지표 설명: " + definition["text"],
                        [
                            {
                                "definition_id": definition["definition_id"],
                                "source": definition["source"],
                            }
                        ],
                    )
                )
            summary.append(
                line(
                    "지원 주기·수록기간: "
                    + "; ".join(
                        f"{freq} {period['start']}~{period['end']}"
                        for freq, period in sorted(meta["periods"].items())
                    ),
                    [{"field": "PRD", "periods": meta["periods"]}],
                )
            )
            if meta["units"]:
                summary.append(
                    line(
                        "표 수준 단위: " + ", ".join(meta["units"]),
                        [{"field": "UNIT", "units": meta["units"]}],
                    )
                )
            summary.append(
                line("조회 분류축: " + "; ".join(v["name"] for v in meta["dimensions"].values()))
            )
            items = [value_entry("ITEM", c, v) for c, v in sorted(meta["items"].items())]
            if sum(len(e["text"]) + 1 for e in items) <= 500:
                summary.extend(items)
            else:
                groups.append(("items", [], items))
                summary.append(
                    line(
                        "ITEM 예시: "
                        + "; ".join(v["name"] for v in list(meta["items"].values())[:12])
                    )
                )
            for axis, dim in sorted(meta["dimensions"].items()):
                entries = [value_entry(axis, c, v, dim["name"]) for c, v in dim["values"].items()]
                if sum(len(e["text"]) + 1 for e in entries) <= 500:
                    summary.extend(entries)
                else:
                    for path, values in dimension_groups(dim):
                        groups.append(
                            (axis, path, [value_entry(axis, c, v, dim["name"]) for c, v in values])
                        )
                    # Names come from shallow official paths, not from evaluation questions.
                    roots = [(c, v) for c, v in dim["values"].items() if len(v["path"]) == 1]
                    for code, value in roots[:12]:
                        example = [(code, value)] + [
                            (c, v) for c, v in dim["values"].items() if v["parent_code"] == code
                        ][:3]
                        summary.append(
                            line(
                                f"{dim['name']} 포함 분류 예시: "
                                + "; ".join(
                                    " > ".join(p["name"] for p in v["path"]) for _, v in example
                                ),
                                [value_entry(axis, c, v)["refs"][0] for c, v in example],
                            )
                        )
            notes, seen_notes = [], set()
            for index, note in enumerate(meta["notes"]):
                original = clean_text(note.get("CMMT_DC"))
                text, reason = filter_note(original)
                if reason:
                    exclusions.append(
                        {"doc_id": key, "note_index": index, "text": original, "reason": reason}
                    )
                elif text and text not in seen_notes:
                    seen_notes.add(text)
                    notes.append(
                        line(
                            "공식 주석: " + text,
                            [{"field": "CMMT", "index": index, "text_hash": digest(original)}],
                        )
                    )
            if notes:
                groups.append(("notes", [], notes))
            unit_evidence = [
                {"axis": axis, "code": c, "unit": v["unit"]}
                for axis, values in [
                    ("ITEM", meta["items"]),
                    *[(a, d["values"]) for a, d in meta["dimensions"].items()],
                ]
                for c, v in values.items()
            ]
            metadata_status = "available"
        except StageFailure as exc:
            if exc.code.startswith("definition_"):
                raise
            metadata_status = exc.code
            incomplete.append({"doc_id": key, "reason": exc.code})
            summary.append(line("상세 메타 미확인: " + exc.code))
        for alias in aliases.get(key, []):
            summary.append(
                line(
                    f"승인 별칭: {alias['expression']} → {alias['canonical_expression']}; "
                    f"적용 문맥: {alias['condition']}"
                )
            )
        counters = Counter()
        for kind, group_path, entries in [("summary", [], summary), *groups]:
            group_header = header + (
                "\n분류 묶음: " + " > ".join(p["name"] for p in group_path) if group_path else ""
            )
            for text, refs in pack_entries(group_header, entries, max_chars):
                index = counters[kind]
                counters[kind] += 1
                docs.append(
                    {
                        "chunk_id": f"{key}:{kind}:{index}",
                        "doc_id": key,
                        "kind": kind,
                        "text": text,
                        "metadata_status": metadata_status,
                        "embedding_eligible": metadata_status == "available",
                        "catalog_path_ids": cat["path_ids"],
                        "catalog_path": cat["path_names"],
                        "catalog_depth": cat["depth"],
                        "dimension_group_path": group_path,
                        "source_refs": refs,
                        "source_snapshot": store.snapshot,
                        "unit_evidence": unit_evidence if kind == "summary" and index == 0 else [],
                        "shared_note_id": digest([e["text"] for e in entries])
                        if kind == "notes"
                        else None,
                        "mapping_ids": [a["mapping_id"] for a in aliases.get(key, [])]
                        if kind == "summary"
                        else [],
                        "text_hash": digest(text),
                    }
                )
    manifest = {
        "version": DOCUMENT_VERSION,
        "catalog_snapshot": store.catalog_snapshot,
        "metadata_snapshot": store.snapshot,
        "mapping_snapshot": fingerprint(mappings_path) if mappings_path else None,
        "definition_snapshot": fingerprint(definitions_path) if definitions_path else None,
        "max_chars": max_chars,
        "table_count": len(store.catalog),
        "chunk_count": len(docs),
        "mapping_report": mapping_report,
        "incomplete_metadata": incomplete,
        "excluded_notes": exclusions,
        "embedding_chunk_count": sum(d["embedding_eligible"] for d in docs),
    }
    return {"manifest": manifest, "documents": docs, "corpus_id": digest([manifest, docs])}
