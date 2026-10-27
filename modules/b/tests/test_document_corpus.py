"""Official document conservation, global metadata recovery and model input budgets."""

import copy
import json
from pathlib import Path

import pytest

from claview_b.contracts import StageFailure
from claview_b.document_corpus import (
    audit_catalog,
    build_document_corpus,
    item_paths,
    select_model_evidence,
    verify_information,
)
from claview_b.metadata import MetadataStore
from claview_b.search import SearchIndex

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store():
    return MetadataStore(FIXTURES / "catalog.json", FIXTURES / "metadata.jsonl")


def catalog_payload(store):
    return {
        "documents": [{**d, "path_ids": " > ".join(d["path_ids"])} for d in store.catalog.values()]
    }


@pytest.mark.parametrize(
    "failure", ["duplicate", "empty", "length", "root", "cycle", "depth", "prefix", "doc_id"]
)
def test_catalog_audit_detects_broken_paths(store, failure):
    payload = catalog_payload(store)
    assert not audit_catalog(payload)["errors"]
    row = payload["documents"][0]
    if failure == "duplicate":
        payload["documents"].append(copy.deepcopy(row))
    elif failure == "empty":
        row["path"] = ">" + row["path"].split(">", 1)[-1]
    elif failure == "length":
        row["path_ids"] += ">NEW"
    elif failure == "root":
        row["org_id"] = "999"
    elif failure == "cycle":
        row["path_ids"] += ">301"
        row["path"] += ">한국은행"
    elif failure == "depth":
        row["depth"] = 999
    elif failure == "prefix":
        row["path"] = "충돌 기관>" + row["path"].split(">", 1)[-1]
    else:
        row["doc_id"] = "999:WRONG"
    assert audit_catalog(payload)["errors"]


def test_three_layers_keep_all_raw_source_offsets_and_catalog_depth(store):
    corpus = build_document_corpus(store)
    assert {d["layer"] for d in corpus["documents"]} == {"basic", "semantic", "detail"}
    assert corpus == build_document_corpus(store)
    assert verify_information(store, corpus)["raw_preservation_verified"]
    for doc in corpus["documents"]:
        cat = store.catalog[doc["doc_id"]]
        assert doc["catalog_depth"] == len(cat["path_ids"]) - 1
        assert doc["catalog_path_ids"] == cat["path_ids"]
        assert len(doc["text"]) <= 2400
        assert all(0 <= r["start"] <= r["end"] for r in doc["source_refs"])


def test_long_note_tail_and_unknown_fields_are_searchable_and_cited(store):
    key = next(iter(store.catalog))
    meta = store.records[key]["meta"]
    meta["CMMT"].append({"CMMT_DC": "길이" * 4000 + "초전도코끼리지표"})
    meta["FUTURE_FIELD"] = {"nested/key": [None, [], "새공식정보"]}
    corpus = build_document_corpus(store)
    assert verify_information(store, corpus)["raw_preservation_verified"]
    assert "초전도코끼리지표" in "\n".join(d["text"] for d in corpus["documents"])
    assert any("nested~1key" in r["pointer"] for d in corpus["documents"] for r in d["source_refs"])
    index = SearchIndex(store, corpus=corpus)
    result = index._retrieve_documents("초전도코끼리지표", 1, None)[0]
    assert result["doc_id"] == key
    assert any("초전도코끼리지표" in r["quote"] for r in result["source_refs"])


def test_raw_table_is_preserved_when_normalized_metadata_is_incomplete(store):
    key = next(iter(store.catalog))
    store.records[key]["meta"]["ITM"].append({"ITM_ID": "DATA", "OBJ_ID": "axis", "TBL_ID": key})
    corpus = build_document_corpus(store)
    assert corpus["manifest"]["tables"][key]["normalized_metadata_status"] == "metadata_incomplete"
    assert verify_information(store, corpus)["raw_preservation_verified"]
    assert any(r["quote"] == "DATA" for d in corpus["documents"] for r in d["source_refs"])


@pytest.mark.parametrize("failure", ["source", "quote", "hash", "tail"])
def test_information_check_rejects_forgery_and_lost_tail(store, failure):
    corpus = build_document_corpus(store)
    docs = [
        d for d in corpus["documents"] if any(r["source"] == "metadata" for r in d["source_refs"])
    ]
    doc = docs[0]
    ref = next(r for r in doc["source_refs"] if r["source"] == "metadata")
    if failure == "source":
        ref["pointer"] = "/meta/MISSING/0"
    elif failure == "quote":
        ref["quote"] = "조작 인용"
    elif failure == "hash":
        ref["record_hash"] = "0" * 64
    else:
        for d in corpus["documents"]:
            d["source_refs"] = [
                r
                for r in d["source_refs"]
                if (r["source"], r["pointer"]) != (ref["source"], ref["pointer"])
                or d["doc_id"] != doc["doc_id"]
            ]
    assert not verify_information(store, corpus)["raw_preservation_verified"]


def test_raw_parent_paths_keep_axis_depth_separate_and_expose_missing_cycle():
    raw = {
        "meta": {
            "ITM": [
                {"OBJ_ID": "axis", "ITM_ID": "A", "ITM_NM": "상위"},
                {"OBJ_ID": "axis", "ITM_ID": "B", "ITM_NM": "하위", "UP_ITM_ID": "A"},
                {"OBJ_ID": "axis", "ITM_ID": "C", "ITM_NM": "끊김", "UP_ITM_ID": "Z"},
                {"OBJ_ID": "axis", "ITM_ID": "D", "ITM_NM": "순환", "UP_ITM_ID": "D"},
            ]
        }
    }
    paths, errors = item_paths(raw)
    assert paths[1]["dimension_depth"] == 1
    assert [r["name"] for r in paths[1]["path"]] == ["상위", "하위"]
    assert paths[2]["dimension_depth"] is None
    assert {e["reason"] for e in errors} == {"parent_missing", "parent_cycle"}


def test_metadata_search_recovers_a_table_outside_title_candidates(store):
    key = next(iter(store.catalog))
    store.records[key]["meta"]["CMMT"].append({"CMMT_DC": "초전도코끼리지표"})
    d0 = SearchIndex(store, corpus=build_document_corpus(store, profile="D0"))
    d2 = SearchIndex(store, corpus=build_document_corpus(store, profile="D2"))
    assert key not in {r["doc_id"] for r in d0.retrieve("초전도코끼리지표")}
    assert key in {r["doc_id"] for r in d2.retrieve("초전도코끼리지표")}
    assert all(not d["metadata_terms"] and not d["notes"] for d in d0.documents.values())
    result = d2._retrieve_documents("초전도코끼리지표", 1, None)[0]
    assert result["document_layer"] == "detail"
    assert result["source_refs"][0]["pointer"].startswith("/meta/CMMT/")


def test_d3_never_injects_conditional_aliases_and_pending_dictionary(store, tmp_path):
    from claview_b.mappings import VERSION

    path = tmp_path / "dictionary.json"
    payload = {
        "schema_version": VERSION,
        "source_version": {
            "catalog": store.catalog_snapshot,
            "metadata": store.snapshot,
            "legacy": "0" * 64,
        },
        "concepts": [],
        "mappings": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    d2 = build_document_corpus(store, path, profile="D2")
    d3 = build_document_corpus(store, path, profile="D3")
    assert d2["documents"] == d3["documents"]
    assert SearchIndex(store, path, d2).mapping_dictionary.policy == "off"
    assert SearchIndex(store, path, d3).mapping_dictionary.policy == "approved"


def test_model_evidence_budget_includes_citations_and_reports_every_omission(store):
    corpus = build_document_corpus(store)
    ids = list(store.catalog)
    packet = select_model_evidence(
        corpus,
        "수입",
        [*ids, ids[0], "999:BAD"],
        max_candidates=2,
        max_chunks=3,
        max_chars=9000,
        per_table=2,
    )
    assert packet["serialized_chars"] <= 9000
    assert len(packet["selected"]) <= 3
    assert len({d["doc_id"] for d in packet["selected"]}) <= 2
    assert all(d["source_refs"] for d in packet["selected"])
    assert {o["reason"] for o in packet["omissions"]} >= {
        "candidate_budget",
        "non_corpus_candidate",
    }
    tiny = select_model_evidence(corpus, "수입", ids, max_chars=2)
    assert tiny["selected"] == [] and tiny["serialized_chars"] == 2
    with pytest.raises(StageFailure):
        select_model_evidence(corpus, "수입", ids, max_chars=1)




