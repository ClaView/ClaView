"""Opt-in NCP text embeddings and versioned exact-cosine search for a small catalog."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path

import httpx

from .contracts import Json, StageFailure
from .search_documents import digest


def unit_vector(values, dimensions: int) -> list[float]:
    if (
        not isinstance(values, list)
        or len(values) != dimensions
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
    ):
        raise StageFailure("invalid_embedding_vector")
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0:
        raise StageFailure("invalid_embedding_vector")
    return [v / norm for v in values]


class NcpEmbedder:
    model_id = "ncp-embedding-v2"
    dimensions = 1024
    url = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"

    def __init__(self, api_key: str, client=None, retries: int = 2):
        if not api_key:
            raise StageFailure("embedding_key_missing", "unresolved")
        self._key = api_key
        self.client = client or httpx.Client(timeout=60, follow_redirects=False)
        self.retries = retries
        self.usage = {"requests": 0, "input_tokens": 0}

    def close(self):
        self.client.close()

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise StageFailure("embedding_empty_input")
        for attempt in range(self.retries + 1):
            self.usage["requests"] += 1
            try:
                response = self.client.post(
                    self.url,
                    json={"text": text},
                    headers={
                        "Authorization": "Bearer " + self._key,
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                    },
                )
            except httpx.TransportError:
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
                raise StageFailure("embedding_transport_error") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(min(2, 0.25 * 2**attempt))
                    continue
            if response.status_code >= 400:
                raise StageFailure(f"embedding_http_{response.status_code}")
            try:
                payload = response.json()
                if str(payload["status"]["code"]) != "20000":
                    raise StageFailure("embedding_service_error")
                result = payload["result"]
                vector = unit_vector(result["embedding"], self.dimensions)
                self.usage["input_tokens"] += int(result.get("inputTokens") or 0)
                return vector
            except (KeyError, ValueError, TypeError):
                raise StageFailure("embedding_invalid_response") from None
        raise StageFailure("embedding_transport_error")


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def build_vector_index(corpus: Json, embedder, path: Path) -> Json:
    """Checkpoint successful chunks. Incomplete indexes cannot be used for retrieval."""
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    compatible = (
        old.get("model_id") == embedder.model_id and old.get("dimensions") == embedder.dimensions
    )
    cached = {r["text_hash"]: r["vector"] for r in old.get("rows", [])} if compatible else {}
    result = {
        "version": "claview-vectors/1",
        "model_id": embedder.model_id,
        "dimensions": embedder.dimensions,
        "corpus_id": corpus["corpus_id"],
        "complete": False,
        "rows": [],
    }
    new_count = reused = 0
    for doc in corpus["documents"]:
        if not doc.get("embedding_eligible", True):
            continue
        h = doc["text_hash"]
        if h in cached:
            vector = unit_vector(cached[h], embedder.dimensions)
            reused += 1
        else:
            try:
                vector = unit_vector(embedder.embed(doc["text"]), embedder.dimensions)
            except StageFailure:
                save_json(path, result)
                raise
            cached[h] = vector
            new_count += 1
        result["rows"].append({"chunk_id": doc["chunk_id"], "text_hash": h, "vector": vector})
        if len(result["rows"]) % 20 == 0:
            save_json(path, result)
    result["complete"] = True
    save_json(path, result)
    return {"corpus_id": corpus["corpus_id"], "new": new_count, "reused": reused}


class VectorIndex:
    def __init__(self, corpus: Json, index: Json, embedder):
        docs = {d["chunk_id"]: d for d in corpus["documents"] if d.get("embedding_eligible", True)}
        if (
            not index.get("complete")
            or index.get("corpus_id") != corpus["corpus_id"]
            or index.get("model_id") != embedder.model_id
            or index.get("dimensions") != embedder.dimensions
            or len(index.get("rows", [])) != len(docs)
            or {r["chunk_id"] for r in index.get("rows", [])} != set(docs)
        ):
            raise StageFailure("vector_index_incompatible", "unresolved")
        self.rows = []
        self.snapshot = digest(index)
        for row in index["rows"]:
            doc = docs[row["chunk_id"]]
            if row["text_hash"] != doc["text_hash"]:
                raise StageFailure("vector_document_mismatch")
            self.rows.append((doc, unit_vector(row["vector"], embedder.dimensions)))
        self.embedder = embedder
        self.query_cache = {}

    def retrieve(self, query: str, limit: int, allowed: set[str] | None = None) -> list[Json]:
        key = digest(query)
        if key not in self.query_cache:
            vector = unit_vector(self.embedder.embed(query), self.embedder.dimensions)
            if len(self.query_cache) >= 128:
                self.query_cache.pop(next(iter(self.query_cache)))
            self.query_cache[key] = vector
        q = self.query_cache[key]
        tables = {}
        for doc, vector in self.rows:
            if allowed is not None and doc["doc_id"] not in allowed:
                continue
            score = sum(a * b for a, b in zip(q, vector))
            current = tables.get(doc["doc_id"])
            if current is None or score > current["score"]:
                tables[doc["doc_id"]] = {
                    "doc_id": doc["doc_id"],
                    "score": round(score, 8),
                    "chunk_id": doc["chunk_id"],
                }
        return sorted(tables.values(), key=lambda r: (-r["score"], r["doc_id"]))[:limit]
