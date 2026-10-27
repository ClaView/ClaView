"""Deterministic catalog retrieval and bounded hierarchical routing support."""

from __future__ import annotations

import math
import re
from collections import Counter
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

from .contracts import Json, StageFailure
from .embeddings import VectorIndex
from .mappings import MappingDictionary
from .metadata import MetadataStore
from .search_documents import approved_aliases


def normalized(text: str) -> str:
    return re.sub(r"[^\w가-힣]", "", text.casefold())


def grams(text: str) -> set[str]:
    text = normalized(text)
    return {text[i : i + 2] for i in range(max(0, len(text) - 1))}


class SearchIndex:
    def __init__(
        self,
        store: MetadataStore,
        mappings_path: Path | None = None,
        corpus: Json | None = None,
        vectors: VectorIndex | None = None,
        mapping_policy: str = "approved",
    ):
        self.store = store
        profile = corpus["manifest"].get("document_profile") if corpus else None
        if profile in {"D0", "D1", "D2"}:
            mapping_policy = "off"
        if profile and vectors:
            raise StageFailure("document_experiment_vectors_not_enabled")
        self.mapping_dictionary = MappingDictionary(store, mappings_path, mapping_policy)
        approved, self.mapping_report = approved_aliases(store, mappings_path)
        if mapping_policy == "off" and approved:
            raise StageFailure("legacy_static_aliases_require_v2_for_ablation")
        static_ids = {alias["mapping_id"] for rows in approved.values() for alias in rows}
        if corpus and (
            corpus["manifest"].get("mapping_snapshot") != self.mapping_dictionary.snapshot
            or any(
                not set(chunk.get("mapping_ids", [])) <= static_ids for chunk in corpus["documents"]
            )
        ):
            raise StageFailure("search_corpus_mapping_incompatible")
        self.approved_query_terms = {
            (alias["expression"], alias["canonical_expression"])
            for rows in approved.values()
            for alias in rows
            if alias["canonical_expression"]
        }
        aliases = {k: [a["expression"] for a in rows] for k, rows in approved.items()}
        self.corpus = corpus
        self.vectors = vectors
        self.mode = "hybrid" if vectors else "documents" if corpus else "legacy"
        self._last_retrieval = ContextVar(f"retrieval_{id(self)}", default=None)
        self.chunks = {d["chunk_id"]: d for d in corpus["documents"]} if corpus else {}
        self.chunk_grams = {key: grams(d["text"]) for key, d in self.chunks.items()}
        chunk_counts = Counter(g for gs in self.chunk_grams.values() for g in gs)
        self.chunk_idf = {g: math.log(1 + len(self.chunks) / n) for g, n in chunk_counts.items()}
        if corpus and (
            corpus["manifest"]["catalog_snapshot"] != store.catalog_snapshot
            or corpus["manifest"]["metadata_snapshot"] != store.snapshot
            or {d["doc_id"] for d in self.chunks.values()} != set(store.catalog)
        ):
            raise StageFailure("search_corpus_incompatible")
        self.fields = {}
        self.nodes = {}
        self.documents = {}
        for key, doc in store.catalog.items():
            try:
                meta = store.get(doc["org_id"], doc["table_id"])
                terms = [v["name"] for v in meta["items"].values()]
                terms += [
                    v["name"]
                    for axis in meta["dimensions"].values()
                    for v in axis["values"].values()
                ]
                notes = " ".join(str(x.get("CMMT_DC", "")) for x in meta["notes"])
            except StageFailure:
                terms = []
                notes = ""
            if profile:
                # Field selection follows the document profile, not hidden cached metadata.
                terms, note_values = set(), set()
                for chunk in self.chunks.values():
                    if chunk["doc_id"] != key:
                        continue
                    for ref in chunk["source_refs"]:
                        if ref["source"] != "metadata":
                            continue
                        if ref["pointer"].endswith("/ITM_NM"):
                            terms.add(ref["quote"])
                        elif ref["pointer"].endswith("/CMMT_DC"):
                            note_values.add(ref["quote"])
                terms = sorted(terms)
                notes = "\n".join(sorted(note_values))
            self.documents[key] = {
                **doc,
                "metadata_terms": terms,
                "notes": notes,
                "aliases": aliases.get(key, []),
            }
            texts = [
                (doc["title"], 6.0),
                (doc["path"] if profile != "D0" else "", 1.5),
                (" ".join(terms), 2.0),
                (" ".join(aliases.get(key, [])), 4.0),
                (notes, 0.3),
            ]
            self.fields[key] = [(text, grams(text), weight) for text, weight in texts]
            for i, node_id in enumerate(doc["path_ids"]):
                prefix = doc["path_ids"][: i + 1]
                node = self.nodes.setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "name": doc["path_names"][i],
                        "depth": i,
                        "path_ids": prefix,
                        "path": doc["path_names"][: i + 1],
                        "children": set(),
                        "tables": set(),
                        "direct_tables": set(),
                    },
                )
                if node["path_ids"] != prefix:
                    raise StageFailure("catalog_node_path_conflict")
                node["tables"].add(key)
                if i + 1 < len(doc["path_ids"]):
                    node["children"].add(doc["path_ids"][i + 1])
                else:
                    node["direct_tables"].add(key)
        counts = Counter(
            g for rows in self.fields.values() for g in set().union(*(x[1] for x in rows))
        )
        self.idf = {g: math.log(1 + len(self.fields) / n) for g, n in counts.items()}
        self.roots = sorted(n for n, v in self.nodes.items() if v["depth"] == 0)

    def retrieve(self, query: str, limit: int = 10, allowed: set[str] | None = None) -> list[Json]:
        rows, diagnostics = self.retrieve_with_diagnostics(query, limit, allowed)
        self._last_retrieval.set(diagnostics)
        return rows

    @property
    def last_retrieval(self) -> Json:
        """Compatibility view isolated to the calling execution context."""
        return deepcopy(self._last_retrieval.get() or {})

    def retrieve_with_diagnostics(self, query, limit=10, allowed=None):
        ranked, warning = self._ranked_channels(query, limit, allowed)
        diagnostics = {
            **self.describe(),
            "scope_size": len(allowed) if allowed is not None else len(self.store.catalog),
            "channels": ranked,
            "warning": warning,
        }
        if not self.corpus:
            return ranked["lexical"], diagnostics
        # Reciprocal ranks avoid treating unrelated score scales as probabilities.
        fused = {}
        for channel, rows in ranked.items():
            for rank, row in enumerate(rows, 1):
                candidate = fused.setdefault(
                    row["doc_id"], {"doc_id": row["doc_id"], "score": 0.0, "evidence": []}
                )
                candidate["score"] += 1 / (60 + rank)
                candidate["evidence"].append({"channel": channel, "rank": rank, **row})
        result = sorted(fused.values(), key=lambda r: (-r["score"], r["doc_id"]))[:limit]
        for row in result:
            row["score"] = round(row["score"], 8)
        return result, diagnostics

    def _ranked_channels(
        self, query: str, limit: int, allowed: set[str] | None
    ) -> tuple[dict[str, list[Json]], str | None]:
        ranked = {"lexical": self._retrieve_legacy(query, limit, allowed)}
        if self.corpus:
            ranked["documents"] = self._retrieve_documents(query, limit, allowed)
        warning = None
        if self.vectors:
            try:
                ranked["vector"] = self.vectors.retrieve(query, limit, allowed)
            except Exception as exc:
                warning = exc.code if isinstance(exc, StageFailure) else "vector_channel_failed"
        return ranked, warning

    def retrieve_pool(
        self,
        query: str,
        expressions: list[str],
        channel_k: int,
        pool_limit: int,
        expansion_limit: int,
        allowed: set[str] | None = None,
    ) -> list[Json]:
        rows, diagnostics = self.retrieve_pool_with_diagnostics(
            query, expressions, channel_k, pool_limit, expansion_limit, allowed
        )
        self._last_retrieval.set(diagnostics)
        return rows

    def retrieve_pool_with_diagnostics(
        self,
        query,
        expressions,
        channel_k,
        pool_limit,
        expansion_limit,
        allowed=None,
        *,
        mapping_resolution=None,
    ):
        """Keep the original channel union before adding bounded expression results."""
        runs = []
        seen_queries = set()
        for kind, expression in [("original", query), *(("expanded", x) for x in expressions)]:
            if expression in seen_queries:
                continue
            seen_queries.add(expression)
            channels, warning = self._ranked_channels(expression, channel_k, allowed)
            runs.append(
                {"kind": kind, "expression": expression, "channels": channels, "warning": warning}
            )
        mapping = (
            mapping_resolution
            if mapping_resolution is not None
            else self.mapping_dictionary.resolve(query, allowed)
        )
        for expansion in mapping["expansions"]:
            channels, warning = self._ranked_channels(
                expansion["expression"], channel_k, set(expansion["allowed_ids"])
            )
            runs.append(
                {
                    "kind": "mapping",
                    "expression": expansion["expression"],
                    "channels": channels,
                    "warning": warning,
                    "mapping": expansion,
                }
            )

        def combine(source_runs: list[Json]) -> list[Json]:
            by_id: dict[str, Json] = {}
            for run in source_runs:
                for channel, rows in run["channels"].items():
                    for rank, row in enumerate(rows, 1):
                        candidate = by_id.setdefault(
                            row["doc_id"], {"doc_id": row["doc_id"], "evidence": []}
                        )
                        candidate["evidence"].append(
                            {
                                "expression": run["kind"],
                                "search_query": run["expression"],
                                "scope": "restricted"
                                if allowed is not None or "mapping" in run
                                else "global",
                                "channel": channel,
                                "rank": rank,
                                **({"mapping": run["mapping"]} if "mapping" in run else {}),
                                **row,
                            }
                        )
            for candidate in by_id.values():
                # Different expressions in one channel contribute only once.
                best = {}
                for evidence in candidate["evidence"]:
                    channel = evidence["channel"]
                    best[channel] = min(best.get(channel, channel_k + 1), evidence["rank"])
                candidate["score"] = round(sum(1 / (60 + rank) for rank in best.values()), 8)
            return sorted(by_id.values(), key=lambda row: (-row["score"], row["doc_id"]))

        original = combine(runs[:1]) if runs else []
        expanded = combine(runs)
        original_ids = {row["doc_id"] for row in original}
        additions = [row for row in expanded if row["doc_id"] not in original_ids]
        # Original evidence is enriched with expression hits without changing its rank.
        enriched = {row["doc_id"]: row for row in expanded}
        protected = [{**row, "evidence": enriched[row["doc_id"]]["evidence"]} for row in original]
        if len(protected) > pool_limit:
            raise StageFailure("original_candidate_budget_exceeded", "unresolved")
        take = min(expansion_limit, pool_limit - len(protected))
        selected = protected + additions[:take]
        diagnostics = {
            **self.describe(),
            "scope_size": len(allowed) if allowed is not None else len(self.store.catalog),
            "channel_k": channel_k,
            "runs": runs,
            "mapping_resolution": mapping,
            "original_union_count": len(original),
            "expanded_new_count": len(additions),
            "candidate_count": len(selected),
            "excluded": [
                {"doc_id": row["doc_id"], "reason": "expansion_budget"} for row in additions[take:]
            ],
        }
        return selected, diagnostics

    def describe(self) -> Json:
        return {
            "search_mode": self.mode,
            "corpus_id": self.corpus["corpus_id"] if self.corpus else None,
            "embedding_model": self.vectors.embedder.model_id if self.vectors else None,
            "mapping_report": self.mapping_report,
            "mapping_dictionary": self.mapping_dictionary.describe(),
            "document_profile": self.corpus["manifest"].get("document_profile")
            if self.corpus
            else None,
        }

    def _retrieve_documents(self, query, limit, allowed):
        q = grams(query)
        tables = {}
        for key, gs in self.chunk_grams.items():
            doc = self.chunks[key]
            if allowed is not None and doc["doc_id"] not in allowed:
                continue
            score = sum(self.chunk_idf.get(g, 0) for g in sorted(q & gs)) / math.sqrt(
                max(1, len(gs))
            )
            current = tables.get(doc["doc_id"])
            if score > 0 and (current is None or score > current["score"]):
                tables[doc["doc_id"]] = {"doc_id": doc["doc_id"], "score": score, "chunk_id": key}
        result = sorted(tables.values(), key=lambda r: (-r["score"], r["doc_id"]))[:limit]
        for row in result:
            doc = self.chunks[row["chunk_id"]]
            if doc.get("layer"):
                refs = sorted(
                    doc["source_refs"],
                    key=lambda r: (-len(q & grams(r["quote"])), r["pointer"], r["start"]),
                )
                row.update(
                    document_layer=doc["layer"],
                    document_kind=doc["kind"],
                    text_hash=doc["text_hash"],
                    source_refs=refs[:3],
                    source_ref_omissions=max(0, len(refs) - 3),
                )
        return result

    def _retrieve_legacy(self, query: str, limit: int, allowed: set[str] | None) -> list[Json]:
        q = grams(query)
        scored = []
        for key, fields in self.fields.items():
            if allowed is not None and key not in allowed:
                continue
            score = sum(
                weight
                * sum(self.idf.get(g, 0) for g in sorted(q & gs))
                / math.sqrt(max(1, len(gs)))
                for _, gs, weight in fields
            )
            if score > 0:
                scored.append({"doc_id": key, "score": round(score, 6)})
        return sorted(scored, key=lambda x: (-x["score"], x["doc_id"]))[:limit]

    def route_options(self, frontier: list[str]) -> list[Json]:
        options = []
        for parent in frontier:
            for key in sorted(self.nodes[parent]["children"]):
                n = self.nodes[key]
                options.append(
                    {
                        "node_id": key,
                        "name": n["name"],
                        "depth": n["depth"],
                        "path": n["path"],
                        "table_count": len(n["tables"]),
                        "table_titles": sorted({self.documents[d]["title"] for d in n["tables"]}),
                    }
                )
        return options

    def candidate_context(self, query: str, candidates: list[Json]) -> list[Json]:
        if self.corpus and self.corpus["manifest"].get("document_profile"):
            from .document_corpus import select_model_evidence

            packet = select_model_evidence(self.corpus, query, [c["doc_id"] for c in candidates])
            return [
                {
                    "doc_id": key,
                    "title": self.documents[key]["title"],
                    "search_documents": [d for d in packet["selected"] if d["doc_id"] == key],
                    "input_omissions": [o for o in packet["omissions"] if o.get("doc_id") == key],
                    "corpus_id": packet["corpus_id"],
                }
                for key in dict.fromkeys(c["doc_id"] for c in candidates)
                if key in self.documents
            ]
        q = grams(query)
        result = []
        for candidate in candidates:
            doc = self.documents[candidate["doc_id"]]
            ranked_terms = sorted(doc["metadata_terms"], key=lambda s: (-len(q & grams(s)), s))
            terms = ranked_terms[:32]
            meta = None
            try:
                meta = self.store.get(doc["org_id"], doc["table_id"])
            except StageFailure:
                pass
            evidence_ids = list(
                dict.fromkeys(
                    e["chunk_id"]
                    for e in [*candidate.get("evidence", []), *candidate.get("scoped_evidence", [])]
                    if e.get("chunk_id")
                )
            )
            if self.corpus and not evidence_ids:
                evidence_ids = [
                    r["chunk_id"] for r in self._retrieve_documents(query, 1, {candidate["doc_id"]})
                ]
            evidence_docs = [
                {field: self.chunks[k][field] for field in ("chunk_id", "kind", "text")}
                for k in evidence_ids[:2]
            ]
            omissions = []
            if len(ranked_terms) > len(terms):
                omissions.append(
                    {
                        "part": "related_terms",
                        "reason": "max_32",
                        "count": len(ranked_terms) - len(terms),
                    }
                )
            if len(evidence_ids) > len(evidence_docs):
                omissions.append(
                    {
                        "part": "search_documents",
                        "reason": "max_2",
                        "count": len(evidence_ids) - len(evidence_docs),
                    }
                )
            if len(doc["notes"]) > 4000:
                omissions.append(
                    {"part": "notes", "reason": "max_4000_chars", "count": len(doc["notes"]) - 4000}
                )
            result.append(
                {
                    **candidate,
                    "title": doc["title"],
                    "path": doc["path"],
                    "path_ids": doc["path_ids"],
                    "depth": doc["depth"],
                    "related_terms": terms,
                    "approved_aliases": doc["aliases"],
                    "search_documents": evidence_docs,
                    "periods": meta["periods"] if meta else {},
                    "units": meta["units"] if meta else [],
                    "notes": doc["notes"][:4000],
                    "input_omissions": omissions,
                }
            )
        return result
