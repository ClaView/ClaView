"""A2 official evidence function fixtures; not a performance golden set."""

import json

import pytest

from claview.b.a1_query import prepare_question
from claview.b.a2_evidence import A2Evidence, diagnose_selection
from claview.b.a_discovery import TableDiscovery
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import StageFailure
from claview.b.document_corpus import dereference, quoted
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents, digest

CTX = {"session_id": "a2-fixture", "request_id": "one", "query_revision": 1}


def item(code, name, *, axis="ITEM", parent=None, unit=None):
    return {
        "ITM_ID": code,
        "ITM_NM": name,
        "OBJ_ID": axis,
        "OBJ_ID_SN": "1",
        "OBJ_NM": axis,
        "UP_ITM_ID": parent,
        "UNIT_NM": unit,
    }


def period(frequency="월", start="2020.01", end="2026.12"):
    return {"PRD_SE": frequency, "STRT_PRD_DE": start, "END_PRD_DE": end}


@pytest.fixture
def factory(tmp_path):
    counter = 0

    def make(*, rows=None, title="공식 경제통계", periods=None, notes=None, missing=False):
        nonlocal counter
        counter += 1
        catalog = {
            "documents": [
                {
                    "org_id": "301",
                    "table_id": "T1",
                    "title": title,
                    "path_ids": "301 > G > T1",
                    "path": "한국은행 > 통계 > " + title,
                }
            ]
        }
        record = {
            "orgId": "301",
            "tblId": "T1",
            "name": title,
            "meta": {
                "ITM": rows if rows is not None else [item("A", "가계신용 잔액", unit="십억원")],
                "PRD": periods if periods is not None else [period()],
                "CMMT": notes or [],
                "UNIT": [],
            },
        }
        cat, meta = tmp_path / f"catalog-{counter}.json", tmp_path / f"meta-{counter}.jsonl"
        cat.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        meta.write_text("" if missing else json.dumps(record, ensure_ascii=False), encoding="utf-8")
        store = MetadataStore(cat, meta)
        search = SearchIndex(store, corpus=build_search_documents(store))
        return search, A2Evidence(search)

    return make


def assess(engine, query):
    result = engine.compare(prepare_question(query), ["301:T1"])
    return result, result["by_doc_id"]["301:T1"]


def by_kind(row, kind):
    return [c for c in row["checks"] if c["kind"] == kind]


def assert_citations(search, row):
    entries = {e["evidence_id"]: e for e in row["evidence"]}
    assert len(entries) == len(row["evidence"])
    for entry in entries.values():
        source = (
            search.store.catalog["301:T1"]
            if entry["source"] == "catalog"
            else search.store.records["301:T1"]
        )
        value = dereference(source, entry["pointer"])
        assert entry["quote"] == quoted(value)
        assert entry["record_hash"] == digest(source) and entry["value_hash"] == digest(value)
        assert entry["source_snapshot"] in {search.store.snapshot, search.store.catalog_snapshot}
    for check in row["checks"]:
        assert set(check["evidence_ids"]) <= set(entries)
        if check["state"] != "unknown":
            assert check["evidence_ids"]


def test_no_cross_branch_target_measure_basis_unit_join(factory):
    search, engine = factory(
        rows=[
            item("A", "실질 국내총생산 성장률", unit="달러"),
            item("B", "명목 가계신용 잔액", unit="원"),
        ]
    )
    _, row = assess(engine, "실질 원화 가계신용 성장률")
    assert all(
        c["state"] == "match"
        for c in row["checks"]
        if c["kind"] in {"price_basis", "currency", "measure"}
    )
    assert row["joint_atomic_checks"][0]["state"] == "unknown"
    assert row["whole_request_state"] == "unknown" and not row["meaning_verified"]
    assert row["series_selection"] is None
    assert_citations(search, row)


def test_joint_atomic_witness_keeps_parent_path_and_decisive_unit(factory):
    search, engine = factory(
        rows=[item("P", "실질"), item("C", "국내총생산 성장률", parent="P", unit="원")]
    )
    _, row = assess(engine, "실질 원화 국내총생산 성장률")
    joint = row["joint_atomic_checks"][0]
    assert joint["state"] == "match" and not joint["series_combination_verified"]
    refs = {e["evidence_id"]: e for e in row["evidence"]}
    quotes = [refs[e]["quote"] for e in joint["witnesses"][0]["evidence_ids"]]
    assert {"실질", "국내총생산 성장률", "원"} <= set(quotes)
    assert not joint["target_measure_unit_jointly_verified"]
    assert row["whole_request_state"] == "unknown"
    assert_citations(search, row)


@pytest.mark.parametrize(
    "rows",
    [
        [item("A", "실질 성장률", parent="absent", unit="원")],
        [item("A", "실질 성장률", parent="A", unit="원")],
        [item("A", "실질 성장률", unit="원"), item("A", "명목", unit="달러")],
    ],
)
def test_incomplete_duplicate_or_cyclic_paths_never_joint_match(factory, rows):
    _, engine = factory(rows=rows)
    _, row = assess(engine, "실질 원화 성장률")
    assert row["joint_atomic_checks"][0]["state"] == "unknown"


def test_notes_and_catalog_words_do_not_prove_a_statistic(factory):
    search, engine = factory(
        title="실질 원화 참고통계",
        rows=[item("A", "다른 통계")],
        notes=[{"CMMT_DC": "국내총생산 성장률은 이 표의 비교 참고 지표이며 조회 항목이 아님"}],
    )
    _, row = assess(engine, "국내총생산 성장률 추이")
    assert by_kind(row, "request")[0]["state"] == "unknown"
    assert any(r["kind"] == "note_requires_semantic_reading" for r in row["related_evidence"])
    assert all(r["provides_requested_statistic"] == "unknown" for r in row["related_evidence"])
    assert row["document_selection"]["chunk_ids"]
    assert_citations(search, row)


def test_cached_month_gap_is_refresh_not_api_unsupported(factory):
    search, engine = factory(periods=[period(start="2024.06", end="2024.12")])
    _, row = assess(engine, "2024년 월별 가계신용 잔액")
    assert by_kind(row, "frequency")[0]["state"] == "match"
    assert by_kind(row, "period_point")[0]["state"] == "unknown"
    assert row["metadata_refresh_needed"] and row["api_support"] == "unverified"
    assert not row["observations_verified"]
    assert_citations(search, row)


def test_duplicate_period_rows_do_not_union_into_full_coverage(factory):
    _, engine = factory(
        periods=[period(start="2024.01", end="2024.06"), period(start="2024.07", end="2024.12")]
    )
    _, row = assess(engine, "2024년 월별 가계신용")
    check = by_kind(row, "period_point")[0]
    assert check["state"] == "unknown" and check["reason"] == "coverage_varies_by_record"


def test_period_conversion_is_b_condition_not_metadata_gap(factory):
    _, engine = factory(periods=[period("년", "2020", "2026")])
    _, row = assess(engine, "2024년 1월부터 2024년 12월까지 연간 가계신용")
    check = by_kind(row, "period_range")[0]
    assert check["state"] == "unknown"
    assert check["reason"] == "period_precision_requires_b_conversion"
    assert not row["metadata_refresh_needed"]


def test_period_and_explicit_frequency_are_cited_but_not_series_observations(factory):
    search, engine = factory(periods=[period("분기", "2020 1/4", "2026 4/4")])
    _, row = assess(engine, "2024년 1분기부터 2024년 4분기까지 분기별 가계신용")
    assert by_kind(row, "period_range")[0]["state"] == "match"
    assert by_kind(row, "frequency")[0]["state"] == "match"
    assert all(
        c["requirement_id"] in row["b_condition_requirement_ids"]
        for c in row["checks"]
        if c["kind"] in {"frequency", "period_range"}
    )
    assert row["a_unresolved_requirement_ids"]
    assert_citations(search, row)


def test_relative_period_missing_meta_and_explicit_opposite_basis(factory):
    _, missing = factory(missing=True)
    _, row = assess(missing, "최근 3년 월별 가계신용")
    assert row["metadata_refresh_needed"] and row["api_support"] == "unverified"
    assert not any(c["state"] == "match" for c in row["checks"])
    assert (
        by_kind(row, "relative_period")[0]["reason"]
        == "b_period_policy_or_query_clarification_required"
    )
    search, opposite = factory(title="명목 경제통계", rows=[item("A", "국내총생산")])
    _, row = assess(opposite, "실질 국내총생산")
    check = by_kind(row, "price_basis")[0]
    assert check["state"] == "conflict" and check["evidence_ids"]
    assert_citations(search, row)


def test_unit_is_local_and_scale_conversion_is_deferred(factory):
    _, engine = factory(
        rows=[item("A", "실질 국내총생산", unit="달러"), item("B", "명목 잔액", unit="억원")]
    )
    _, row = assess(engine, "실질 국내총생산을 억원 단위로")
    assert by_kind(row, "unit")[0]["state"] == "match"
    assert row["joint_atomic_checks"][0]["state"] == "unknown"
    _, row = assess(engine, "실질 국내총생산을 백만원 단위로")
    assert by_kind(row, "unit")[0]["state"] == "unknown"
    assert by_kind(row, "unit")[0]["reason"] == "unit_binding_or_conversion_requires_B"


def test_forged_question_quote_and_foreign_candidate_fail_closed(factory):
    _, engine = factory()
    question = prepare_question("2024년 월별 가계신용")
    question["requirements"][0]["source_refs"][0]["quote"] = "forged"
    with pytest.raises(StageFailure, match="a2_requirement_source_invalid"):
        engine.compare(question, ["301:T1"])
    with pytest.raises(StageFailure, match="a2_non_catalog_candidate"):
        engine.compare(prepare_question("질문"), ["999:forged"])


def test_source_changes_change_citation_version(factory):
    _, one = factory(notes=[{"CMMT_DC": "가계신용 설명 v1"}])
    _, two = factory(notes=[{"CMMT_DC": "가계신용 설명 v2"}])
    first, _ = assess(one, "가계신용")
    second, _ = assess(two, "가계신용")
    assert first["source_versions"]["metadata"] != second["source_versions"]["metadata"]


def test_selection_diagnosis_has_no_gold_or_automatic_count_claim(factory):
    _, engine = factory(title="명목 통계", rows=[item("A", "국내총생산")])
    result, _ = assess(engine, "2099년 월별 실질 국내총생산")
    diagnosis = diagnose_selection(result, ["301:T1"])
    assert diagnosis["selected_with_conflicts"] == ["301:T1"]
    assert diagnosis["selected_requiring_metadata_refresh"] == ["301:T1"]
    assert (
        not diagnosis["missing_table_determined"] and not diagnosis["unnecessary_tables_determined"]
    )


def test_discovery_wires_a1_a2_without_rank_filter_or_confirmation_changes(factory, tmp_path):
    search, _ = factory()
    off = TableDiscovery(search, CandidateStore(tmp_path / "off.db"), a2_mode="off")
    on = TableDiscovery(search, CandidateStore(tmp_path / "on.db"))
    query = "2024년 월별 가계신용 잔액"
    before = off.search_tables(query, CTX)["interaction"]["candidate_set_id"]
    after = on.search_tables(query, CTX)["interaction"]["candidate_set_id"]
    old, new = off.repository.get(before, CTX), on.repository.get(after, CTX)
    assert old["display_order"] == new["display_order"]
    assert old["candidate_pool"][0]["rank_signals"] == new["candidate_pool"][0]["rank_signals"]
    assert new["a1_state"]["original_query"] == new["a2_state"]["original_query"] == query
    assert new["status"] == "awaiting_user" and new["a_outcome"] is None
    assert not new["a2_state"]["auto_accept"]
    restarted = TableDiscovery(search, CandidateStore(tmp_path / "on.db"))
    assert restarted.repository.get(after, CTX)["a2_state"] == new["a2_state"]
    confirmed = restarted.confirm_tables(after, [1], CTX)
    assert set(confirmed["result"]["tables"][0]) == {"org_id", "table_id", "table_name"}
    assert old["original_query"] == new["original_query"]
