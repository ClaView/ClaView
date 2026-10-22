"""Document provenance, hybrid retrieval and bounded graph integration without APIs."""

import copy
import json
from pathlib import Path

import httpx
import pytest

from claview.b.contracts import Config, StageFailure, Trace
from claview.b.embeddings import NcpEmbedder, VectorIndex, build_vector_index, unit_vector
from claview.b.graphs import build_a_graph
from claview.b.metadata import MetadataStore
from claview.b.models import ScriptedModel
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents, chunks, filter_note
from claview.b.stages import Services, select_tables


def test_contact_filter_preserves_statistical_notes():
    assert filter_note("한국은행 통계팀 02-759-1234") == ("", "contact_only")
    text = "부문의 합과 총합이 불일치하며 잠정치는 수정될 수 있음"
    assert filter_note(text) == (text, None)
    mixed = "잠정치 작성 기준은 담당자 02-759-1234에게 문의"
    assert filter_note(mixed) == (mixed, None)


def test_v2_preserves_units_and_member_evidence(store):
    corpus = build_search_documents(store)
    for key, cat in store.catalog.items():
        meta = store.get(cat["org_id"], cat["table_id"])
        rows = [d for d in corpus["documents"] if d["doc_id"] == key]
        assert all(d["catalog_depth"] == cat["depth"] for d in rows)
        assert all("단위: 미확인" not in d["text"] for d in rows)
        actual = {(r["axis"], r["code"]): r["unit"] for d in rows for r in d["unit_evidence"]}
        expected = {("ITEM", c): v["unit"] for c, v in meta["items"].items()}
        expected.update(
            {
                (a, c): v["unit"]
                for a, dim in meta["dimensions"].items()
                for c, v in dim["values"].items()
            }
        )
        assert actual == expected
        refs = {(r["axis"], r["code"]) for d in rows for r in d["source_refs"] if "axis" in r}
        assert refs == set(expected)


def test_unverified_definition_cannot_enter_corpus(store, tmp_path):
    path = tmp_path / "definitions.json"
    definition = {"table_name": next(iter(store.catalog.values()))["title"], "status": "draft"}
    path.write_text(json.dumps({"definitions": [definition]}), encoding="utf-8")
    with pytest.raises(StageFailure, match="definition_not_verified"):
        build_search_documents(store, definitions_path=path)


def test_ineligible_metadata_is_not_embedded(store, tmp_path):
    corpus = build_search_documents(store)
    corpus["documents"][0]["embedding_eligible"] = False
    path = tmp_path / "vectors.json"
    provider = SyntheticEmbedder()
    build_vector_index(corpus, provider, path)
    index = json.loads(path.read_text())
    assert len(index["rows"]) == len(corpus["documents"]) - 1
    assert corpus["documents"][0]["chunk_id"] not in {r["chunk_id"] for r in index["rows"]}
    VectorIndex(corpus, index, provider)


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store():
    return MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")


class SyntheticEmbedder:
    model_id = "synthetic-test-only"
    dimensions = 2

    def __init__(self):
        self.calls = 0
        self.fail = False

    def embed(self, text):
        self.calls += 1
        if self.fail:
            raise StageFailure("synthetic_unavailable")
        # Known synthetic vectors test wiring, not semantic model quality.
        return [1.0, 0.0] if "소비" in text else [0.0, 1.0]


def test_documents_keep_metadata_paths_and_exclude_pending_related_mappings(store, tmp_path):
    key = next(iter(store.catalog))
    org, tid = key.split(":")
    rows = []
    for status, relation, expression in [
        ("draft_review_required", "alias", "PENDINGMARKER"),
        ("approved", "related", "RELATEDMARKER"),
        ("approved", "alias", "APPROVEDMARKER"),
    ]:
        rows.append(
            {
                "mapping_id": expression,
                "status": status,
                "relation": relation,
                "input_expression": expression,
                "canonical_expression": "공식명",
                "scope": {"org_id": org, "table_ids": [tid]},
            }
        )
    path = tmp_path / "mappings.json"
    path.write_text(json.dumps({"mappings": rows}), encoding="utf-8")
    corpus = build_search_documents(store, path)
    assert corpus == build_search_documents(store, path)
    texts = "\n".join(d["text"] for d in corpus["documents"])
    assert "APPROVEDMARKER" in texts
    assert "PENDINGMARKER" not in texts and "RELATEDMARKER" not in texts
    assert all(len(d["text"]) <= 2400 for d in corpus["documents"])
    for key, cat in store.catalog.items():
        selected = [d for d in corpus["documents"] if d["doc_id"] == key]
        assert any(d["kind"] == "summary" for d in selected)
        assert all(cat["path"] in d["text"] for d in selected)
        meta = store.get(cat["org_id"], cat["table_id"])
        for dim in meta["dimensions"].values():
            for v in dim["values"].values():
                assert v["name"] in "\n".join(d["text"] for d in selected)
    index = SearchIndex(store, path, corpus)
    candidates = index.retrieve("APPROVEDMARKER")
    assert candidates[0]["doc_id"] == f"{org}:{tid}"
    assert index.candidate_context("APPROVEDMARKER", candidates)[0]["search_documents"]


def test_document_splitting_preserves_long_note_tail():
    text = "x" * 6000 + "TAIL_MARKER"
    parts = chunks("HEADER", [text], 2400)
    assert "".join(part.removeprefix("HEADER\n") for part in parts) == text


def test_vector_build_resume_and_model_corpus_isolation(store, tmp_path):
    corpus = build_search_documents(store)
    provider = SyntheticEmbedder()
    path = tmp_path / "index.json"
    report = build_vector_index(corpus, provider, path)
    assert report["new"] > 0
    calls = provider.calls
    assert build_vector_index(corpus, provider, path)["new"] == 0
    assert provider.calls == calls
    data = json.loads(path.read_text())
    vectors = VectorIndex(corpus, data, provider)
    rows = vectors.retrieve("소비", 10)
    assert len(rows) == len({r["doc_id"] for r in rows})
    chosen = rows[-1]["doc_id"]
    assert vectors.retrieve("소비", 10, {chosen})[0]["doc_id"] == chosen
    assert provider.calls == calls + 1
    for field, bad in [("model_id", "different"), ("corpus_id", "stale"), ("complete", False)]:
        broken = {**data, field: bad}
        with pytest.raises(StageFailure, match="vector_index_incompatible"):
            VectorIndex(corpus, broken, provider)


def test_failed_vector_build_leaves_incomplete_checkpoint(store, tmp_path):
    corpus = build_search_documents(store)
    provider = SyntheticEmbedder()
    provider.fail = True
    path = tmp_path / "incomplete.json"
    with pytest.raises(StageFailure):
        build_vector_index(corpus, provider, path)
    assert not json.loads(path.read_text())["complete"]
    provider.fail = False
    build_vector_index(corpus, provider, path)
    assert json.loads(path.read_text())["complete"]


def test_hybrid_fallback_records_failure_without_losing_lexical_candidates(store, tmp_path):
    corpus = build_search_documents(store)
    provider = SyntheticEmbedder()
    path = tmp_path / "index.json"
    build_vector_index(corpus, provider, path)
    vectors = VectorIndex(corpus, json.loads(path.read_text()), provider)
    index = SearchIndex(store, corpus=corpus, vectors=vectors)
    provider.fail = True
    actual = index.retrieve("소비자물가", 10)
    expected = SearchIndex(store, corpus=corpus).retrieve("소비자물가", 10)
    assert actual == expected
    assert index.last_retrieval["warning"] == "synthetic_unavailable"


def test_hybrid_hierarchy_keeps_global_results_and_matches_plain_function(store, tmp_path):
    corpus = build_search_documents(store)
    provider = SyntheticEmbedder()
    path = tmp_path / "index.json"
    build_vector_index(corpus, provider, path)
    index = SearchIndex(
        store, corpus=corpus, vectors=VectorIndex(corpus, json.loads(path.read_text()), provider)
    )
    question = "소비자물가"
    global_ids = [r["doc_id"] for r in index.retrieve(question)]
    chosen = global_ids[0]
    route_id = index.route_options(index.roots)[0]["node_id"]
    responses = {
        "route": [{"selected_node_ids": [route_id]}],
        "A": [{"selected_doc_ids": [chosen]}],
    }
    config = Config(hierarchy=True, max_route_rounds=1)

    def service():
        return Services(store, index, ScriptedModel(copy.deepcopy(responses)), config, Trace())

    svc = service()
    state = build_a_graph(svc).invoke({"query": question})
    assert state["stage_outcome"]["status"] == "ok"
    assert set(global_ids) <= {r["doc_id"] for r in state["merged_candidates"]}
    assert state["route_round"] <= 1
    assert state["stage_outcome"]["result"] == select_tables(question, service())["result"]
    assert "retrieval_diagnostics" in state
    request = next(
        e for e in svc.trace.events if e.get("task") == "A" and e["event"] == "model_request"
    )
    assert any(c["search_documents"] for c in request["context"]["candidates"])
    assert "expected" not in request["context"]


def test_embedding_http_contract_and_invalid_vectors():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000"},
                "result": {"embedding": [1.0] * 1024, "inputTokens": 8},
            },
        )

    provider = NcpEmbedder("test-only", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(provider.embed("공식 표 설명")) == 1024
    assert calls == [{"text": "공식 표 설명"}]
    assert provider.usage["input_tokens"] == 8
    provider.close()
    for value in [[0, 0], [float("nan"), 1], [1], [True, 1]]:
        with pytest.raises(StageFailure):
            unit_vector(value, 2)
