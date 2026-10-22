"""A1 functional fixtures, excluded from golden retrieval performance metrics."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from claview.b.a1_mappings import load_experiment, source_review, write_review
from claview.b.a1_query import auxiliary_queries, prepare_question
from claview.b.a_discovery import TableDiscovery
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import StageFailure
from claview.b.mappings import VERSION, MappingDictionary
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/b/fixtures"
CTX = {"session_id": "a1-fixture", "request_id": "request", "query_revision": 1}


@pytest.fixture
def store():
    return MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")


def assert_spans(question):
    sources = {s["source_id"]: s["text"] for s in question["sources"]}
    for row in question["requirements"]:
        assert row["status"] in {"explicit", "inferred", "unresolved"}
        assert row["scope_ids"] and row["target"]["kind"] == "request_scope"
        for span in row["source_refs"]:
            assert sources[span["source_id"]][span["start"] : span["end"]] == span["quote"]


def test_quarter_range_month_relative_period_and_offsets():
    query = "  2008년 1분기부터 2024년 4분기까지 분기별 가계신용 잔액; 최근 3년 월별 금리  "
    question = prepare_question(query)
    assert question["original_query"] == query and question["table_count"] is None
    assert len(question["scopes"]) == 2
    period = next(r for r in question["requirements"] if r["kind"] == "period_range")
    assert period["value"] == {
        "start": {"year": 2008, "quarter": 1, "month": None},
        "end": {"year": 2024, "quarter": 4, "month": None},
    }
    relative = next(r for r in question["requirements"] if r["kind"] == "relative_period")
    assert relative["status"] == "unresolved"
    month = prepare_question("2024년 1월~2024년 12월 월별 물가")
    assert (
        next(r for r in month["requirements"] if r["kind"] == "period_range")["value"]["end"][
            "month"
        ]
        == 12
    )
    assert_spans(question)
    assert_spans(month)


@pytest.mark.parametrize("query", ["최근 3년 금리", "가계빚 흐름", "주담대 금리"])
def test_does_not_invent_price_or_loan_basis(query):
    result = prepare_question(query)
    assert not any(r["kind"] in {"price_basis", "loan_basis"} for r in result["requirements"])
    assert result["table_count"] is None


def test_negation_alternative_aggregation_and_series_basis():
    question = prepare_question(
        "명목 말고 실질 GDP를 월별에서 연간 평균으로 변환해줘. 신선식품 제외 원계열"
    )
    basis = {
        r["value"]: r["polarity"] for r in question["requirements"] if r["kind"] == "price_basis"
    }
    assert basis == {"nominal": "negative", "real": "positive"}
    assert all(
        r["status"] == "unresolved" for r in question["requirements"] if r["kind"] == "frequency"
    )
    assert any(
        r["kind"] == "exclusion" and r["value"] == "신선식품" for r in question["requirements"]
    )
    assert any(
        r["kind"] == "series_basis" and r["value"] == "original" for r in question["requirements"]
    )
    alternate = prepare_question("명목 또는 실질 GDP 월별 아니면 분기별")
    assert all(
        r["status"] == "unresolved"
        for r in alternate["requirements"]
        if r["kind"] in {"price_basis", "frequency"}
    )
    double = prepare_question("실질은 제외하지 말고 월별로")
    assert all(
        r["status"] == "unresolved"
        for r in double["requirements"]
        if r["kind"] in {"price_basis", "exclusion", "frequency"}
    )
    assert_spans(question)


def test_compound_does_not_assign_table_count_or_cross_bind_conditions():
    question = prepare_question("명목 GDP 연간; 실질 GDP 분기별 그리고 경상수지와 상품수지 비교")
    assert len(question["scopes"]) == 3 and question["table_count"] is None
    rows = [r for r in question["requirements"] if r["kind"] == "price_basis"]
    assert rows[0]["scope_ids"] != rows[1]["scope_ids"]
    assert all(r["target"]["binding"] == "unresolved" for r in rows)
    assert len(auxiliary_queries(question)) == 2
    assert all(q["expression"] in question["original_query"] for q in auxiliary_queries(question))
    assert_spans(question)
    comparison = prepare_question("경상수지와 상품·서비스·본원소득·이전소득수지를 비교")
    group = next(r for r in comparison["requirements"] if r["kind"] == "surface_list")
    assert len(group["value"]["components"]) == 4 and group["status"] == "unresolved"
    assert comparison["table_count"] is None


def test_answers_use_separate_sources_and_no_original_replacement():
    result = prepare_question(
        "GDP 비교",
        effective_query="GDP 비교\n사용자 기준 답변: 실질",
        answers=[{"user_input": "실질"}],
    )
    assert result["original_query"] == "GDP 비교"
    basis = next(r for r in result["requirements"] if r["kind"] == "price_basis")
    assert basis["source_refs"][0]["source_id"] == "answer:1"
    assert auxiliary_queries(result)[0]["kind"] == "user_answer"
    assert_spans(result)
    with pytest.raises(StageFailure, match="unverified_a1_search_expression"):
        prepare_question("GDP 비교", effective_query="실질 GDP 비교")


def test_reversed_period_unresolved_and_numeric_id_not_year():
    result = prepare_question("2025년~2020년 물가")
    assert (
        next(r for r in result["requirements"] if r["kind"] == "period_range")["status"]
        == "unresolved"
    )
    assert not any(
        r["kind"] == "period_point" for r in prepare_question("자료번호 202412")["requirements"]
    )
    invalid = prepare_question("2024년 13월~2025년 1월 물가")
    assert (
        next(r for r in invalid["requirements"] if r["kind"] == "period_point")["status"]
        == "unresolved"
    )
    mixed = prepare_question("2024년 4분기~2025년 1월")
    assert (
        next(r for r in mixed["requirements"] if r["kind"] == "period_range")["status"]
        == "unresolved"
    )


def test_discovery_a1_handoff_original_protection_and_restart(store, tmp_path):
    index = SearchIndex(store, corpus=build_search_documents(store))
    database = tmp_path / "sets.sqlite3"
    discovery = TableDiscovery(index, CandidateStore(database))
    query = "2024년 월별 수입물가; 국내공급물가 비교"
    result = discovery.search_tables(query, CTX)
    set_id = result["interaction"]["candidate_set_id"]
    bundle = discovery.repository.get(set_id, CTX)
    a1 = bundle["a1_state"]
    assert a1["original_query"] == bundle["original_query"] == query
    assert a1["decision"] == "pending_a2" and a1["model_calls"] == 0
    original, _ = index.retrieve_pool_with_diagnostics(query, [], 5, 30, 10)
    assert {r["doc_id"] for r in original} <= {r["doc_id"] for r in a1["retrieval_rows"]}
    assert a1["search_queries"][0]["kind"] == "original"
    assert any(q["kind"] == "literal_clause" for q in a1["search_queries"])
    restarted = TableDiscovery(index, CandidateStore(database))
    assert restarted.repository.get(set_id, CTX)["a1_state"] == a1
    selected = restarted.confirm_tables(set_id, [1], CTX)
    assert set(selected["result"]["tables"][0]) == {"org_id", "table_id", "table_name"}
    assert_spans(a1)


def test_off_and_rules_match_for_simple_query(store, tmp_path):
    index = SearchIndex(store, corpus=build_search_documents(store))
    off = TableDiscovery(index, CandidateStore(tmp_path / "off.db"), a1_mode="off")
    rules = TableDiscovery(index, CandidateStore(tmp_path / "rules.db"))
    query = "2024년 월별 수입물가 원화"
    before, _ = off.retrieve(query)
    after, diagnostics = rules.retrieve(query)
    assert before == after and diagnostics["a1"]["original_query"] == query
    assert off.snapshots["configuration"] != rules.snapshots["configuration"]


def mapping_payload(store):
    key = next(iter(store.catalog))
    title = store.catalog[key]["title"]
    source = {
        "catalog": store.catalog_snapshot,
        "metadata": store.snapshot,
        "legacy": "functional_fixture",
    }
    concept = {
        "concept_id": "c",
        "canonical_name": title,
        "search_terms": [title],
        "evidence": [{"doc_id": key, "field": "catalog.title", "quote": title}],
    }
    row = {
        "mapping_id": "spacing",
        "expression": " ".join(title),
        "concept_id": "c",
        "alternative_concept_ids": [],
        "relation": "spelling_variant",
        "scope": {"org_ids": ["301"], "table_ids": [key]},
        "conditions": [],
        "exclusions": [],
        "condition_status": "structured",
        "legacy_condition": "fixture",
        "source_version": source,
        "review_status": "draft",
        "reviewer": None,
        "provenance": {"kind": "synthetic_test", "source_mapping_id": "fixture"},
    }
    unproven = {
        **copy.deepcopy(row),
        "mapping_id": "abbreviation",
        "expression": "UNPROVEN",
        "relation": "abbreviation",
    }
    return {
        "schema_version": VERSION,
        "source_version": source,
        "concepts": [concept],
        "mappings": [row, unproven],
    }


def test_source_review_preserves_draft_and_requires_joint_acronym_witness(store, tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(mapping_payload(store), ensure_ascii=False), encoding="utf-8")
    subset, review = source_review(store, source)
    assert review["selected_mapping_ids"] == ["spacing"]
    assert subset["mappings"][0]["review_status"] == "draft"
    assert subset["mappings"][0]["reviewer"] is None
    assert not review["human_review"] and not review["operational_approval"]
    write_review(store, source, tmp_path / "experiment")
    experiment = load_experiment(store, source, tmp_path / "experiment/review.json")
    assert experiment.policy == "draft_experiment"
    assert MappingDictionary(store, source).policy == "approved"
    changed = json.loads((tmp_path / "experiment/dictionary.json").read_text(encoding="utf-8"))
    changed["mappings"][0]["review_status"] = "approved"
    (tmp_path / "experiment/dictionary.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(StageFailure, match="a1_mapping_review_mismatch"):
        load_experiment(store, source, tmp_path / "experiment/review.json")


def test_dictionary_auxiliary_is_scoped_and_compound_mapping_is_deferred(store, tmp_path):
    data = mapping_payload(store)
    data["mappings"] = [
        {**data["mappings"][1], "review_status": "approved", "reviewer": "synthetic_fixture"}
    ]
    path = tmp_path / "mappings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    index = SearchIndex(store, path, build_search_documents(store, path))
    discovery = TableDiscovery(index, CandidateStore(tmp_path / "sets.db"))
    rows, diagnostics = discovery.retrieve("UNPROVEN 월별")
    a1 = diagnostics["a1"]
    mapping = next(q for q in a1["search_queries"] if q["kind"] == "mapping")
    assert set(mapping["allowed_ids"]) == set(data["mappings"][0]["scope"]["table_ids"])
    assert all(r["doc_id"] in store.catalog for r in rows)
    _, compound = discovery.retrieve("UNPROVEN 월별; 다른 지표 연간")
    assert not compound["mapping_resolution"]["expansions"]
    assert (
        compound["mapping_resolution"]["trace"][0]["reason"] == "compound_mapping_scope_unresolved"
    )
