"""Conditional terminology mapping, provenance, proposal and original-query invariants."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from claview.b.a_discovery import TableDiscovery
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import StageFailure
from claview.b.mappings import VERSION, MappingDictionary, ingest_proposals, mentions
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store():
    return MetadataStore(
        ROOT / "tests/b/fixtures/catalog.json", ROOT / "tests/b/fixtures/metadata.jsonl"
    )


def payload(store, expression="TOKEN", conditions=None, exclusions=None):
    key = next(iter(store.catalog))
    name = store.catalog[key]["title"]
    source = {"catalog": store.catalog_snapshot, "metadata": store.snapshot, "legacy": "fixture"}
    return {
        "schema_version": VERSION,
        "source_version": source,
        "concepts": [
            {
                "concept_id": "concept",
                "canonical_name": name,
                "search_terms": [name],
                "evidence": [{"doc_id": key, "field": "catalog.title", "quote": name}],
            }
        ],
        "mappings": [
            {
                "mapping_id": "mapping",
                "expression": expression,
                "concept_id": "concept",
                "alternative_concept_ids": [],
                "relation": "alias",
                "scope": {"org_ids": ["301"], "table_ids": [key]},
                "conditions": [{"any_positive": x} for x in (conditions or [])],
                "exclusions": [{"any_positive": x} for x in (exclusions or [])],
                "condition_status": "structured",
                "legacy_condition": "fixture",
                "source_version": source,
                "review_status": "approved",
                "reviewer": "synthetic-test-reviewer",
                "provenance": {"kind": "synthetic_test", "source_mapping_id": "fixture"},
            }
        ],
    }


def save(tmp_path, data):
    path = tmp_path / "mappings.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "query,action,reason",
    [
        ("TOKEN 원화", "applied", "scoped_retrieval_expansion"),
        ("TOKEN 달러", "deferred", "condition_unknown"),
        ("TOKEN 원화 말고 달러", "rejected", "condition_conflict"),
        ("TOKEN 원화 달러", "rejected", "exclusion_matched"),
        ("TOKEN 말고 원화", "rejected", "expression_negated_or_mixed"),
        ("TOKEN 제외하지 말고 원화", "deferred", "negation_or_alternative_scope_unknown"),
        ("TOKEN 원화 또는 달러", "deferred", "negation_or_alternative_scope_unknown"),
    ],
)
def test_conditions_exclusions_negation_and_unknowns(store, tmp_path, query, action, reason):
    path = save(tmp_path, payload(store, conditions=[["원화"]], exclusions=[["달러"]]))
    result = MappingDictionary(store, path).resolve(query)
    row = result["trace"][0]
    assert row["action"] == action and row["reason"] == reason
    assert result["original_query"] == query
    assert bool(result["expansions"]) == (action == "applied")
    for span in row["source_spans"]:
        assert query[span["start"] : span["end"]] == span["quote"]


def test_pending_and_rejected_cannot_be_enabled_by_production_policy(store, tmp_path):
    data = payload(store)
    row = data["mappings"][0]
    row.update(review_status="draft", reviewer=None)
    path = save(tmp_path, data)
    assert MappingDictionary(store, path).resolve("TOKEN")["trace"][0]["reason"] == "pending_review"
    expanded = MappingDictionary(store, path, "draft_experiment").resolve("TOKEN")["expansions"]
    assert expanded[0]["experimental"]
    row["review_status"] = "rejected"
    path = save(tmp_path, data)
    assert not MappingDictionary(store, path, "draft_experiment").resolve("TOKEN")["expansions"]


@pytest.mark.parametrize("relation", ["related_concept", "ambiguous", "definition"])
def test_related_definition_and_ambiguous_are_not_synonyms(store, tmp_path, relation):
    data = payload(store)
    data["mappings"][0]["relation"] = relation
    result = MappingDictionary(store, save(tmp_path, data)).resolve("TOKEN")
    assert result["trace"][0]["reason"] == "not_an_equivalent_expression"
    assert not result["expansions"]


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("source", "stale_source_version"),
        ("quote", "official_evidence_missing_or_changed"),
        ("scope", "non_official_scope"),
        ("conditions", "condition_not_structured"),
    ],
)
def test_untrusted_source_and_unstructured_conditions_defer(store, tmp_path, problem, reason):
    data = payload(store)
    if problem == "source":
        data["source_version"]["catalog"] = "stale"
    elif problem == "quote":
        data["concepts"][0]["evidence"][0]["quote"] = "invented quote"
    elif problem == "scope":
        data["mappings"][0]["scope"]["table_ids"] = ["301:FAKE"]
    else:
        data["mappings"][0]["condition_status"] = "unresolved"
    result = MappingDictionary(store, save(tmp_path, data)).resolve("TOKEN")
    assert result["trace"][0]["reason"] == reason and not result["expansions"]


def test_scope_intersection_and_budget(store, tmp_path):
    path = save(tmp_path, payload(store))
    assert (
        MappingDictionary(store, path).resolve("TOKEN", allowed=set())["trace"][0]["reason"]
        == "scope_empty"
    )
    assert (
        MappingDictionary(store, path, max_expansions=0).resolve("TOKEN")["trace"][0]["reason"]
        == "expansion_budget"
    )
    assert (
        MappingDictionary(store, path, "off").resolve("TOKEN")["trace"][0]["reason"]
        == "dictionary_disabled"
    )


def test_homonym_requires_disambiguation_even_if_one_canonical_is_already_in_question(
    store, tmp_path
):
    data = payload(store)
    second = copy.deepcopy(data["concepts"][0])
    key = list(store.catalog)[1]
    name = store.catalog[key]["title"]
    second.update(
        concept_id="other",
        canonical_name=name,
        search_terms=[name],
        evidence=[{"doc_id": key, "field": "catalog.title", "quote": name}],
    )
    data["concepts"].append(second)
    row = copy.deepcopy(data["mappings"][0])
    row.update(
        mapping_id="other", concept_id="other", scope={"org_ids": ["301"], "table_ids": [key]}
    )
    data["mappings"].append(row)
    dictionary = MappingDictionary(store, save(tmp_path, data))
    for query in ("TOKEN", "TOKEN " + name):
        result = dictionary.resolve(query)
        assert not result["expansions"]
        assert all(r["reason"] == "multiple_applicable_concepts" for r in result["trace"])


def test_phrase_boundaries_case_and_original_offsets():
    assert not mentions("MYGDP2", "GDP")
    assert not mentions("가계빚쟁이", "가계빚")
    query = "연간 g d p 성장률"
    match = mentions(query, "GDP")[0]
    assert query[match["start"] : match["end"]] == "g d p"
    assert mentions("가계빚은", "가계빚")


def test_mapping_not_indexed_and_scoped_expansion_preserves_original_candidates(store, tmp_path):
    path = save(tmp_path, payload(store))
    corpus = build_search_documents(store, path)
    assert "TOKEN" not in "\n".join(row["text"] for row in corpus["documents"])
    index = SearchIndex(store, path, corpus)
    original = SearchIndex(store, path, corpus, mapping_policy="off")
    query = "TOKEN 원화 월별 수입물가"
    before, _ = original.retrieve_pool_with_diagnostics(query, [], 10, 30, 10)
    after, diagnostics = index.retrieve_pool_with_diagnostics(query, [], 10, 30, 10)
    assert {r["doc_id"] for r in before} <= {r["doc_id"] for r in after}
    assert diagnostics["runs"][0]["expression"] == query
    mapping = diagnostics["mapping_resolution"]["expansions"][0]
    assert mapping["expression"].startswith(query + "\n")
    run = next(r for r in diagnostics["runs"] if r["kind"] == "mapping")
    assert all(
        row["doc_id"] in mapping["allowed_ids"] for rows in run["channels"].values() for row in rows
    )
    assert len(after) <= 30


def test_legacy_conditional_alias_cannot_escape_through_document_index(store, tmp_path):
    key = next(iter(store.catalog))
    path = save(
        tmp_path,
        {
            "mappings": [
                {
                    "mapping_id": "unsafe",
                    "status": "approved",
                    "relation": "alias",
                    "input_expression": "UNSAFEALIAS",
                    "canonical_expression": "term",
                    "condition": "원화 명시 때만",
                    "scope": {"org_id": "301", "table_ids": [key.split(":")[1]]},
                }
            ]
        },
    )
    corpus = build_search_documents(store, path)
    index = SearchIndex(store, path, corpus)
    assert "UNSAFEALIAS" not in "\n".join(row["text"] for row in corpus["documents"])
    assert not index.approved_query_terms
    assert index.mapping_dictionary.resolve("UNSAFEALIAS 달러")["trace"][0]["action"] == "deferred"


def test_mock_model_proposal_never_trusts_approval_or_table_ids(store, tmp_path):
    data = payload(store)
    proposal = {
        "expression": "NEWALIAS",
        "concept_id": "concept",
        "relation": "alias",
        "review_status": "approved",
        "reviewer": "spoof",
        "table_ids": ["301:FAKE"],
    }
    result = ingest_proposals(data, [proposal, proposal])
    assert len(result["mappings"]) == 2 and len(data["mappings"]) == 1
    row = result["mappings"][-1]
    assert row["review_status"] == "draft" and row["reviewer"] is None
    assert row["scope"]["table_ids"] == [] and row["condition_status"] == "unresolved"
    assert not MappingDictionary(store, save(tmp_path, result), "draft_experiment").resolve(
        "NEWALIAS"
    )["expansions"]
    with pytest.raises(StageFailure, match="invalid_mapping_proposal"):
        ingest_proposals(data, [{**proposal, "concept_id": "not_official"}])


def test_approval_without_reviewer_fails_closed(store, tmp_path):
    data = payload(store)
    data["mappings"][0]["reviewer"] = None
    with pytest.raises(StageFailure, match="approval_requires_reviewer"):
        MappingDictionary(store, save(tmp_path, data))


def test_stale_or_static_alias_corpus_cannot_bypass_runtime_conditions(store, tmp_path):
    path = save(tmp_path, payload(store))
    corpus = build_search_documents(store, path)
    stale = copy.deepcopy(corpus)
    stale["manifest"]["mapping_snapshot"] = "old"
    with pytest.raises(StageFailure, match="search_corpus_mapping_incompatible"):
        SearchIndex(store, path, stale)
    injected = copy.deepcopy(corpus)
    injected["documents"][0]["mapping_ids"] = ["mapping"]
    with pytest.raises(StageFailure, match="search_corpus_mapping_incompatible"):
        SearchIndex(store, path, injected)


@pytest.mark.parametrize(
    "query,title,allowed",
    [
        ("TOKEN 실질", "명목", False),
        ("TOKEN 명목", "실질", False),
        ("TOKEN 명목 말고 실질", "명목", False),
        ("TOKEN 신규취급액", "잔액 기준", False),
        ("TOKEN 원화", "달러", False),
        ("TOKEN 실질", "명목 및 실질", True),
        ("TOKEN 원화", "통화 구분 없음", True),
    ],
)
def test_expansion_scope_rejects_only_explicit_title_conflicts(
    store, tmp_path, query, title, allowed
):
    key = next(iter(store.catalog))
    store.catalog[key]["title"] = "공식 지표 " + title
    result = MappingDictionary(store, save(tmp_path, payload(store))).resolve(query)
    assert bool(result["expansions"]) == allowed
    assert bool(result["trace"][0]["scope_conflicts"]) != allowed


def test_dictionary_policy_is_part_of_candidate_snapshot_and_no_confirmation(store, tmp_path):
    path = save(tmp_path, payload(store))
    corpus = build_search_documents(store, path)
    repository = CandidateStore(tmp_path / "candidates.sqlite3")
    on = TableDiscovery(SearchIndex(store, path, corpus), repository)
    off = TableDiscovery(SearchIndex(store, path, corpus, mapping_policy="off"), repository)
    assert on.snapshots["index"] != off.snapshots["index"]
    context = {"session_id": "s", "request_id": "r", "query_revision": 1}
    waiting = on.search_tables("TOKEN 원화 월별", context)
    bundle = repository.get(waiting["interaction"]["candidate_set_id"], context)
    assert bundle["status"] == "awaiting_user" and bundle["a_outcome"] is None
    assert not bundle["b_attempts"]
    assert bundle["diagnostics"]["mapping_resolution"]["trace"][0]["action"] == "applied"
