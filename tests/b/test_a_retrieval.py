"""Shared budgets, rank fusion, provenance and conservative joint basis checks."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from claview.b.a_discovery import TableDiscovery
from claview.b.a_eval_predict import predict_case
from claview.b.a_retrieval import RetrievalOrchestrator, finalize_pool, load_metadata_corpus
from claview.b.a_semantics import BasisEvidence, explicit_conditions
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import Config, StageFailure
from claview.b.document_corpus import build_document_corpus
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents, digest

FIXTURES = Path(__file__).parent / "fixtures"
CTX = {"session_id": "session", "request_id": "request", "query_revision": 1}


@pytest.fixture
def store():
    return MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")


def hit(key, channel="lexical", rank=1, score=1, expression="original"):
    return {
        "doc_id": key,
        "score": score,
        "evidence": [{"channel": channel, "rank": rank, "score": score, "expression": expression}],
    }


def test_one_budget_protects_original_deduplicates_and_rejects_unknown():
    catalog = {str(i): {} for i in range(40)}
    original = [hit(str(i)) for i in range(20)]
    additions = [hit(str(i), "metadata", i + 1) for i in range(40)] + [hit("fake"), hit([])]
    pool, diag = finalize_pool(original, additions, catalog, pool_limit=30, addition_limit=5)
    assert [r["doc_id"] for r in pool[:20]] == list(catalog)[:20]
    assert len(pool) == len({r["doc_id"] for r in pool}) == diag["candidate_count"] == 25
    assert diag["protected_original_count"] == 20 and diag["added_count"] == 5
    assert len(diag["budget_excluded"]) == 15 and len(diag["rejected"]) == 2
    assert all(r["score"] == 1 for r in pool[:20])
    empty, _ = finalize_pool(original, additions, catalog, pool_limit=30, addition_limit=0)
    assert [r["doc_id"] for r in empty] == list(catalog)[:20]
    with pytest.raises(StageFailure, match="original_candidate_budget_exceeded"):
        finalize_pool(original, [], catalog, pool_limit=19, addition_limit=1)


def test_rrf_ignores_raw_scale_and_expression_vote_multiplication():
    catalog = {"a": {}, "b": {}}
    rows = [
        hit("a", "title", 1, 1000),
        hit("b", "title", 2, 1),
        hit("a", "metadata", 2, 1),
        hit("b", "metadata", 1, 2),
    ]
    pool, _ = finalize_pool([], rows, catalog, pool_limit=2, addition_limit=2, fusion="rrf")
    altered = copy.deepcopy(rows)
    altered[0]["evidence"][0]["score"] = 0.00001
    altered += [hit("b", "metadata", 1, 2, "mapping")] * 3
    other, _ = finalize_pool([], altered, catalog, pool_limit=2, addition_limit=2, fusion="rrf")
    assert [(r["doc_id"], r["score"]) for r in pool] == [(r["doc_id"], r["score"]) for r in other]
    raw, _ = finalize_pool([], rows, catalog, pool_limit=2, addition_limit=2, fusion="raw_sum")
    raw_changed, _ = finalize_pool(
        [], altered, catalog, pool_limit=2, addition_limit=2, fusion="raw_sum"
    )
    assert raw[0]["doc_id"] == "a" and raw_changed[0]["doc_id"] == "b"


def basis_store(rows, title="공식 경제통계"):
    return SimpleNamespace(
        catalog={"301:table": {"title": title, "org_id": "301"}},
        records={"301:table": {"meta": {"ITM": rows}}},
    )


def row(code, name, parent=None, unit=None):
    return {"OBJ_ID": "axis", "ITM_ID": code, "ITM_NM": name, "UP_ITM_ID": parent, "UNIT_NM": unit}


def test_sibling_values_cannot_be_combined_into_one_series():
    store = basis_store([row("A", "실질", unit="달러"), row("B", "명목", unit="원")])
    result = BasisEvidence(store).assess("실질 원화 통계", "301:table")
    assert {c["state"] for c in result["checks"]} == {"match"}
    assert result["joint_state"] == "unknown" and result["series_selection"] is None
    assert not result["meaning_verified"]


def test_single_branch_witness_cites_parent_and_unit():
    store = basis_store([row("A", "실질"), row("B", "국내총생산", "A", "십억원")])
    result = BasisEvidence(store).assess("실질 원화 통계", "301:table")
    assert result["joint_state"] == "match"
    witness = result["joint_evidence"][0]
    assert witness["branch_evidence"][0]["source_quote"] == "실질"
    assert witness["unit_evidence"] == {
        "source_field": "/meta/ITM/1/UNIT_NM",
        "source_quote": "십억원",
    }


@pytest.mark.parametrize(
    "rows",
    [
        [row("A", "실질 원화", "missing")],
        [row("A", "실질 원화", "A")],
        [row("A", "실질 원화"), row("A", "명목 달러")],
    ],
)
def test_broken_or_ambiguous_branch_is_not_joint_support(rows):
    result = BasisEvidence(basis_store(rows)).assess("실질 원화 통계", "301:table")
    assert result["joint_state"] == "unknown"


@pytest.mark.parametrize(
    "query,title,expected",
    [
        ("실질 통계", "명목 경제통계", "conflict"),
        ("달러 통계", "원화 경제통계", "conflict"),
        ("실질 통계", "경제통계", "unknown"),
        ("명목과 실질 비교", "명목 및 실질 경제통계", "unknown"),
        ("명목과 실질 표 둘 다", "명목 경제통계", "unknown"),
        ("원화와 달러 통계 비교", "원화 경제통계", "unknown"),
        ("명목인지 실질인지 모르겠어", "명목 경제통계", "unknown"),
    ],
)
def test_only_explicit_official_opposite_title_is_hard_conflict(query, title, expected):
    result = BasisEvidence(basis_store([], title)).assess(query, "301:table")
    assert result["joint_state"] == expected


def test_negation_preserves_original_source_spans():
    conditions = explicit_conditions("달러는 빼고 원화 기준")
    assert any(c["choice"] == "usd" and c["polarity"] == "negative" for c in conditions)
    assert any(c["choice"] == "krw" and c["polarity"] == "positive" for c in conditions)
    assert all(s["quote"] for c in conditions for s in c["source_spans"])


def test_protected_first_page_keeps_original_membership(store, tmp_path):
    index = SearchIndex(store, corpus=build_search_documents(store))
    original = TableDiscovery(index, CandidateStore(tmp_path / "before.sqlite3"))
    original_set = original.search_tables("2024년 월별 수입물가", CTX)["interaction"]
    protected = TableDiscovery(
        index,
        CandidateStore(tmp_path / "after.sqlite3"),
        retrieval_policy="rrf_protected",
        metadata_corpus=build_document_corpus(store),
    )
    result = protected.search_tables("2024년 월별 수입물가", CTX)["interaction"]
    bundle = protected.repository.get(result["candidate_set_id"], CTX)
    ids = bundle["diagnostics"]["protected_first_page_ids"]
    assert set(ids) == {r["doc_id"] for r in original_set["options"]}
    assert set(bundle["display_order"][: len(ids)]) == set(ids)
    assert all("rank_signals" not in c for c in result["options"])


def test_metadata_search_is_global_cited_and_ignores_codes_dates_headers(store):
    key = next(iter(store.catalog))
    meta = store.records[key]["meta"]
    meta["FUTURE_CODE"] = "ZZZQQQVVV9999"
    meta["CMMT"].append({"CMMT_DC": "초전도코끼리지표"})
    corpus = build_document_corpus(store)
    index = SearchIndex(store, corpus=build_search_documents(store))
    retrieval = RetrievalOrchestrator(index, "metadata", corpus)
    result = retrieval.channels("초전도코끼리지표", 10)["metadata"]
    assert result[0]["doc_id"] == key
    assert result[0]["source_refs"][0]["quote"] == "초전도코끼리지표"
    assert not retrieval.channels("ZZZQQQVVV9999", 10)["metadata"]
    assert not retrieval.channels("2025.01", 10)["metadata"]


@pytest.mark.parametrize("forgery", ["digest", "source"])
def test_prebuilt_metadata_rejects_changed_digest_or_forged_source(store, tmp_path, forgery):
    corpus = build_document_corpus(store)
    path = tmp_path / "corpus.json"
    if forgery == "digest":
        corpus["corpus_id"] = "changed"
    else:
        doc = next(
            d
            for d in corpus["documents"]
            if any(r["source"] == "metadata" for r in d["source_refs"])
        )
        next(r for r in doc["source_refs"] if r["source"] == "metadata")["quote"] = "forged"
        corpus["corpus_id"] = digest([corpus["manifest"], corpus["documents"]])
    path.write_text(json.dumps(corpus), encoding="utf-8")
    with pytest.raises(StageFailure, match="metadata_corpus_.*_mismatch"):
        load_metadata_corpus(store, path)


def test_all_channels_share_condition_checks_budget_and_user_confirmation(store, tmp_path):
    target = next(iter(store.catalog))
    store.catalog[target]["title"] = "원화 수입물가"
    store.records[target]["meta"]["ITM"] = [row("item", "수입물가", unit="원")]
    index = SearchIndex(store, corpus=build_search_documents(store))

    class Channel:
        version = "synthetic/1"

        def retrieve(self, query):
            return [{"doc_id": key, "table_name": "spoof"} for key in store.catalog] * 20

    discovery = TableDiscovery(
        index,
        CandidateStore(tmp_path / "sets.sqlite3"),
        Config(channel_k=1, candidate_pool_limit=3, expansion_limit=2),
        channels={"metadata": Channel()},
        retrieval_policy="conditions",
        metadata_corpus=build_document_corpus(store),
    )
    response = discovery.search_tables("달러 수입물가", CTX)
    assert response["status"] == "awaiting_user" and response["result"] is None
    bundle = discovery.repository.get(response["interaction"]["candidate_set_id"], CTX)
    assert not bundle["b_attempts"] and bundle["a_outcome"] is None
    assert len(bundle["candidate_pool"]) == bundle["diagnostics"]["candidate_count"] <= 3
    candidate = next(c for c in bundle["candidate_pool"] if c["doc_id"] == target)
    assert candidate["table_name"] == "원화 수입물가"
    assert candidate["conflicts"] and target not in bundle["display_order"]
    assert candidate["selection_explanation"]["meaning"]
    assert "optional:metadata" in bundle["diagnostics"]["final_pool"]["channel_counts_unique"]


def test_cached_period_gap_aggregation_and_unknown_scope_do_not_hard_filter(store, tmp_path):
    index = SearchIndex(store, corpus=build_search_documents(store))
    discovery = TableDiscovery(
        index,
        CandidateStore(tmp_path / "sets.sqlite3"),
        retrieval_policy="conditions",
        metadata_corpus=build_document_corpus(store),
    )
    response = discovery.search_tables("2099년 월별 수입물가를 연간 평균으로 집계", CTX)
    assert response["interaction"]["options"]
    card = next(c for c in response["interaction"]["options"] if c["table_id"] == "DT_401Y018")
    assert not card["conflicts"] and card["unknowns"]
    assert card["selection_explanation"]["scope"] == "unknown"
    assert card["selection_explanation"]["differences"]
    case = {
        "case_id": "synthetic",
        "query": "기관 ID 999 수입물가",
        "query_revision": 1,
        "prior_turns": [],
    }
    prediction = predict_case(case, discovery)
    assert prediction["decision"] == "out_of_scope" and not prediction["display_order"]
    assert not any(prediction["usage"].values())
