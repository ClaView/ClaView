"""Bounded A channel composition with protected original candidates and cited fields."""

from __future__ import annotations

import json
import math
import re
from collections import Counter

from .contracts import StageFailure
from .search_documents import clean_text, digest

VERSION = "claview-a-retrieval/1"
POLICIES = ("baseline", "dictionary", "metadata", "conditions", "rrf", "raw_sum", "rrf_protected")
RRF_CONSTANT = 60


def load_metadata_corpus(store, path=None, mappings=None, definitions=None):
    from .document_corpus import build_document_corpus, verify_information

    if path:
        corpus = json.loads(path.read_text(encoding="utf-8-sig"))
        if corpus.get("corpus_id") != digest([corpus["manifest"], corpus["documents"]]):
            raise StageFailure("metadata_corpus_digest_mismatch")
    else:
        corpus = build_document_corpus(store, mappings, definitions, "D3")
    if corpus["manifest"].get("document_profile") not in {"D2", "D3"}:
        raise StageFailure("full_metadata_corpus_required")
    if not verify_information(store, corpus, definitions)["raw_preservation_verified"]:
        raise StageFailure("metadata_corpus_source_mismatch")
    return corpus


def content_grams(text):
    tokens = re.findall(r"[a-zA-Z가-힣]+", clean_text(text).casefold())
    return {token[i : i + 2] for token in tokens for i in range(len(token) - 1)}


def finalize_pool(original, additions, catalog, *, pool_limit, addition_limit, fusion="preserve"):
    """One budget applies to mapping, metadata and optional adapters alike."""
    by_id, rejected = {}, []
    original_ids = []
    for protected, rows in ((True, original), (False, additions)):
        for row in rows:
            key = row.get("doc_id")
            if not isinstance(key, str) or key not in catalog:
                rejected.append({"doc_id": key, "reason": "non_catalog_candidate"})
                continue
            entry = by_id.setdefault(key, {"doc_id": key, "score": 0, "evidence": []})
            entry["evidence"].extend(row.get("evidence", []))
            if protected:
                if key not in original_ids:
                    original_ids.append(key)
                value = row.get("score", 0)
                entry["score"] = (
                    value if isinstance(value, (int, float)) and math.isfinite(value) else 0
                )
                entry["original_score"] = entry["score"]
    if len(original_ids) > pool_limit:
        raise StageFailure("original_candidate_budget_exceeded", "unresolved")
    channels = {
        e.get("fusion_channel", e["channel"])
        for row in by_id.values()
        for e in row["evidence"]
        if e.get("channel")
    }
    raw_scores = {}
    for key, row in by_id.items():
        votes, raw = {}, {}
        unique = {}
        for e in row["evidence"]:
            channel = e.get("fusion_channel", e.get("channel", "unlabelled"))
            rank = e.get("rank", 1)
            if type(rank) is not int or rank < 1:
                continue
            votes[channel] = min(votes.get(channel, rank), rank)
            value = e.get("score", 0)
            if isinstance(value, (int, float)) and math.isfinite(value):
                raw[channel] = max(raw.get(channel, 0), value)
            marker = (
                channel,
                e.get("expression"),
                e.get("chunk_id"),
                e.get("rank"),
                e.get("mapping", {}).get("mapping_id"),
            )
            unique.setdefault(marker, e)
        row["evidence"] = list(unique.values())
        row["fusion_score"] = (
            sum(1 / (RRF_CONSTANT + rank) for rank in votes.values()) * 2 / max(1, len(channels))
        )
        raw_scores[key] = sum(raw.values())
        row["channel_ranks"] = votes
    maximum = max(raw_scores.values(), default=0)
    if fusion == "raw_sum":
        for key, row in by_id.items():
            row["fusion_score"] = raw_scores[key] / max(1e-12, maximum) * 2 / (RRF_CONSTANT + 1)
    new = [row for key, row in by_id.items() if key not in original_ids]
    new.sort(key=lambda r: (-r["fusion_score"], r["doc_id"]))
    take = min(addition_limit, pool_limit - len(original_ids))
    selected = [by_id[key] for key in original_ids] + new[:take]
    for row in selected:
        if fusion != "preserve" or row["doc_id"] not in original_ids:
            row["score"] = round(row["fusion_score"], 8)
    return selected, {
        "version": VERSION,
        "fusion": fusion,
        "rrf_constant": RRF_CONSTANT,
        "protected_original_ids": original_ids,
        "protected_original_count": len(original_ids),
        "unique_before_budget": len(by_id),
        "candidate_count": len(selected),
        "pool_limit": pool_limit,
        "addition_limit": addition_limit,
        "added_count": len(selected) - len(original_ids),
        "rejected": rejected,
        "budget_excluded": [
            {"doc_id": r["doc_id"], "reason": "shared_candidate_budget"} for r in new[take:]
        ],
        "channel_counts_selected": dict(
            Counter(e["channel"] for r in selected for e in r["evidence"] if e.get("channel"))
        ),
        "channel_counts_unique": {
            c: sum(c in r["channel_ranks"] for r in by_id.values()) for c in sorted(channels)
        },
    }


class RetrievalOrchestrator:
    def __init__(self, search, policy, corpus=None):
        if policy not in POLICIES:
            raise StageFailure("invalid_retrieval_policy")
        if policy in {"metadata", "conditions", "rrf", "raw_sum", "rrf_protected"} and not corpus:
            raise StageFailure("metadata_corpus_required")
        self.search, self.policy, self.corpus = search, policy, corpus
        self.rows = []
        if corpus:
            if (
                corpus["manifest"]["catalog_snapshot"] != search.store.catalog_snapshot
                or corpus["manifest"]["metadata_snapshot"] != search.store.snapshot
            ):
                raise StageFailure("metadata_corpus_incompatible")
            seen = set()
            fields = {
                "ITM_NM",
                "ITM_NM_ENG",
                "OBJ_NM",
                "OBJ_NM_ENG",
                "UNIT_NM",
                "UNIT_NM_ENG",
                "UNIT_ENG_NM",
                "TBL_NM",
                "TBL_NM_ENG",
                "CMMT_DC",
            }
            for doc in corpus["documents"]:
                for ref in doc["source_refs"]:
                    if ref["source"] != "definitions" and (
                        ref["source"] != "metadata" or ref["pointer"].split("/")[-1] not in fields
                    ):
                        continue
                    key = (doc["doc_id"], ref["source"], ref["pointer"], ref["start"], ref["end"])
                    gs = content_grams(ref["quote"])
                    if key in seen or not gs:
                        continue
                    seen.add(key)
                    self.rows.append(
                        {
                            "doc_id": doc["doc_id"],
                            "chunk_id": doc["chunk_id"],
                            "layer": doc["layer"],
                            "kind": doc["kind"],
                            "ref": ref,
                            "grams": gs,
                        }
                    )
        counts = Counter(g for row in self.rows for g in row["grams"])
        self.idf = {g: math.log(1 + len(self.rows) / n) for g, n in counts.items()}
        self.postings = {}
        for index, row in enumerate(self.rows):
            for gram in row["grams"]:
                self.postings.setdefault(gram, set()).add(index)

    @property
    def semantic_checks(self):
        return self.policy in {"conditions", "rrf", "raw_sum", "rrf_protected"}

    def describe(self):
        return {
            "version": VERSION,
            "policy": self.policy,
            "metadata_corpus_id": self.corpus["corpus_id"] if self.corpus else None,
            "field_count": len(self.rows),
            "rrf_constant": RRF_CONSTANT,
            "metadata_scope": "all_official_tables;human_fields_only;no_header_or_structural_codes",
            "semantic_checks": self.semantic_checks,
            "fusion": "rrf"
            if self.policy == "rrf_protected"
            else self.policy
            if self.policy in {"rrf", "raw_sum"}
            else "preserve",
            "protected_first_page": 5 if self.policy == "rrf_protected" else 0,
        }

    def channels(self, query, limit):
        if self.policy in {"baseline", "dictionary"}:
            return {}
        q = content_grams(query)
        result = {}
        for channel, field in (("title", "title"), ("path", "path")):
            scores = []
            for key, doc in self.search.store.catalog.items():
                gs = content_grams(doc[field])
                overlap = q & gs
                score = len(overlap) / math.sqrt(max(1, len(gs)) * max(1, len(q)))
                if score:
                    scores.append(
                        {
                            "doc_id": key,
                            "score": score,
                            "source_field": "catalog." + field,
                            "source_quote": doc[field],
                            "matched_grams": sorted(overlap),
                        }
                    )
            result[channel] = sorted(scores, key=lambda r: (-r["score"], r["doc_id"]))[:limit]
        best = {}
        eligible = set().union(*(self.postings.get(g, set()) for g in q))
        for index in sorted(eligible):
            row = self.rows[index]
            overlap = q & row["grams"]
            score = sum(self.idf[g] for g in sorted(overlap)) / math.sqrt(
                max(1, len(row["grams"])) * max(1, len(q))
            )
            if score > 0 and (row["doc_id"] not in best or score > best[row["doc_id"]]["score"]):
                best[row["doc_id"]] = {
                    "doc_id": row["doc_id"],
                    "score": score,
                    "chunk_id": row["chunk_id"],
                    "document_layer": row["layer"],
                    "document_kind": row["kind"],
                    "source_refs": [row["ref"]],
                    "matched_grams": sorted(overlap),
                }
        result["metadata"] = sorted(best.values(), key=lambda r: (-r["score"], r["doc_id"]))[:limit]
        return result
