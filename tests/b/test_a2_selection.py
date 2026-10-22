"""Stage 4 function fixtures only; never included in reported golden performance."""

import json

import pytest

from claview.b.a1_query import prepare_question
from claview.b.a2_selection import verify_set
from claview.b.a_discovery import TableDiscovery
from claview.b.candidate_store import CandidateStore
from claview.b.contracts import StageFailure
from claview.b.graphs import build_discovery_graph
from claview.b.metadata import MetadataStore
from claview.b.search import SearchIndex
from claview.b.search_documents import build_search_documents

CTX = {"session_id": "stage4", "request_id": "one", "query_revision": 1}


@pytest.fixture
def setup(tmp_path):
    counter = 0

    def make(tables, mode="auto"):
        nonlocal counter
        counter += 1
        docs, records = [], []
        for key, title, labels in tables:
            docs.append(
                {
                    "org_id": "301",
                    "table_id": key,
                    "title": title,
                    "path_ids": "301 > " + key,
                    "path": "한국은행 > " + title,
                }
            )
            records.append(
                {
                    "orgId": "301",
                    "tblId": key,
                    "name": title,
                    "meta": {
                        "ITM": [
                            {
                                "OBJ_ID": "ITEM",
                                "OBJ_ID_SN": "1",
                                "OBJ_NM": "항목",
                                "ITM_ID": str(i),
                                "ITM_NM": label[0] if isinstance(label, tuple) else label,
                                "UNIT_NM": label[1] if isinstance(label, tuple) else "원",
                                "UP_ITM_ID": None,
                            }
                            for i, label in enumerate(labels)
                        ],
                        "PRD": [
                            {"PRD_SE": "월", "STRT_PRD_DE": "2020.01", "END_PRD_DE": "2026.12"}
                        ],
                        "CMMT": [],
                        "UNIT": [],
                    },
                }
            )
        catalog = tmp_path / f"catalog{counter}.json"
        metadata = tmp_path / f"metadata{counter}.jsonl"
        catalog.write_text(json.dumps({"documents": docs}, ensure_ascii=False), encoding="utf-8")
        metadata.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8"
        )
        store = MetadataStore(catalog, metadata)
        search = SearchIndex(store, corpus=build_search_documents(store))
        return TableDiscovery(
            search, CandidateStore(tmp_path / f"c{counter}.sqlite3"), selection_mode=mode
        )

    return make


def decision(discovery, query, ids=None):
    question = prepare_question(query)
    assessment = discovery.a2.compare(question, ids or list(discovery.search.store.catalog))
    return discovery.table_policy.decide(question, assessment)


def bundle(discovery, result):
    key = result.get("candidate_set_id") or result["interaction"]["candidate_set_id"]
    context = result.get("context") or result["interaction"]["context"]
    return discovery.repository.get(key, context)


def test_multiple_series_in_one_table_and_shadow_comparison(setup):
    d = setup([("T1", "가계신용", ["가계대출 잔액", "판매신용 잔액"])], mode="shadow")
    query = '2024년 월별 공식 항목 "가계대출 잔액"과 "판매신용 잔액" 비교'
    result = d.search_tables(query, CTX)
    saved = bundle(d, result)
    policy = saved["selection_decision"]
    assert result["status"] == "awaiting_user" and saved["a_outcome"] is None
    assert policy["auto_eligible"] and policy["proposed_ids"] == ["301:T1"]
    assert len(policy["validation"]["additional_table_contribution"]["301:T1"]) == 2
    confirmed = d.confirm_tables(saved["candidate_set_id"], [1], CTX)
    saved = d.repository.get(saved["candidate_set_id"], CTX)
    assert saved["selection_origin"] == "user" and saved["selection_comparison"]["same_set"]
    assert set(confirmed["result"]["tables"][0]) == {"org_id", "table_id", "table_name"}
    assert saved["original_query"] == query


def test_automatic_multitable_graph_restart_and_duplicate_confirmation(setup):
    d = setup(
        [("T1", "가계대출 통계", ["가계대출 잔액"]), ("T2", "판매신용 통계", ["판매신용 잔액"])]
    )
    query = '2024년 월별 공식 항목 "가계대출 잔액"과 "판매신용 잔액" 비교'
    result = build_discovery_graph(d).invoke({"query": query, "context": CTX})
    assert result["stage_outcome"]["status"] == "ok"
    saved = d.repository.get(result["candidate_set_id"], CTX)
    assert saved["selection"] == ["301:T1", "301:T2"] and saved["selection_origin"] == "automatic"
    restarted = TableDiscovery(d.search, CandidateStore(d.repository.path), selection_mode="auto")
    duplicate = restarted.confirm_tables(saved["candidate_set_id"], saved["selection"], CTX)
    assert duplicate == saved["a_outcome"]
    assert (
        restarted.repository.get(saved["candidate_set_id"], CTX)["selection_origin"] == "automatic"
    )
    with pytest.raises(StageFailure, match="selection_conflict"):
        restarted.confirm_tables(saved["candidate_set_id"], ["301:T1"], CTX)


def test_one_table_cover_wins_without_redundant_reference_tables(setup):
    d = setup(
        [
            ("T1", "통합통계", ["대출 잔액", "판매 잔액"]),
            ("T2", "대출", ["대출 잔액"]),
            ("T3", "판매", ["판매 잔액"]),
        ]
    )
    result = decision(d, '공식 항목 "대출 잔액"과 "판매 잔액"')
    assert result["proposed_ids"] == ["301:T1"]
    assert not verify_set(
        ["301:T1", "301:T2"],
        result["coverage"],
        [r["requirement_id"] for r in result["requirements"]],
    )["valid"]


def test_minimum_count_does_not_resolve_unspecified_basis(setup):
    d = setup(
        [
            ("T1", "명목 통합", ["생산액", "소비액"]),
            ("T2", "실질 생산", ["생산액"]),
            ("T3", "실질 소비", ["소비액"]),
        ]
    )
    result = decision(d, '공식 항목 "생산액"과 "소비액"')
    assert not result["auto_eligible"]
    assert result["clarification"]["axis"] == "price_basis"


def test_unstated_currency_uses_local_item_unit_not_only_title(setup):
    d = setup(
        [
            ("T1", "통합", [("생산액", "원"), ("소비액", "원")]),
            ("T2", "생산", [("생산액", "달러")]),
            ("T3", "소비", [("소비액", "달러")]),
        ]
    )
    result = decision(d, '공식 항목 "생산액"과 "소비액"')
    assert not result["auto_eligible"]
    assert result["clarification"]["axis"] == "currency"


def test_single_table_alternative_currencies_do_not_require_a_question(setup):
    d = setup([("T1", "생산", [("생산액", "원"), ("생산액", "달러")])])
    result = decision(d, '공식 항목 "생산액"')
    assert result["auto_eligible"] and result["clarification"] is None


def test_alternative_sets_do_not_union_or_pick_top_one(setup):
    d = setup([("T1", "첫 통계", ["가계신용"]), ("T2", "둘째 통계", ["가계신용"])])
    result = decision(d, '공식 항목 "가계신용"')
    assert result["route"] == "user_confirmation" and not result["auto_eligible"]
    assert result["proposed_ids"] == [] and len(result["solutions"]) == 2
    assert result["clarification"] is None


def test_key_criterion_reassesses_a2_without_a1_and_preserves_answers(setup, monkeypatch):
    d = setup([("T1", "명목 생산", ["생산액"]), ("T2", "실질 생산", ["생산액"])])
    query = '공식 항목 "생산액"'
    result = d.search_tables(query, CTX)
    parent = bundle(d, result)
    assert result["interaction"]["clarification"]["axis"] == "price_basis"
    monkeypatch.setattr(d, "retrieve", lambda *a, **k: pytest.fail("A1 must not rerun"))
    answer = d.clarify_tables(parent["candidate_set_id"], "real", CTX)
    child = bundle(d, answer)
    assert answer["status"] == "ok" and child["selection"] == ["301:T2"]
    assert child["original_query"] == query and child["a_answers"][0]["user_input"] == "실질"
    assert not child["diagnostics"]["a2_resume"]["a1_rerun"]
    assert child["display_order"] == parent["display_order"]
    assert child["context"]["query_revision"] == 2
    restarted = TableDiscovery(d.search, CandidateStore(d.repository.path), selection_mode="auto")
    assert restarted.clarify_tables(parent["candidate_set_id"], "real", CTX) == answer
    with pytest.raises(StageFailure, match="clarification_answer_conflict"):
        restarted.clarify_tables(parent["candidate_set_id"], "nominal", CTX)


def test_same_table_basis_variants_are_b_conditions_no_a_question(setup):
    d = setup([("T1", "생산 통계", ["생산액", "생산액"])])
    result = decision(d, '공식 항목 "생산액"')
    assert result["auto_eligible"] and result["clarification"] is None


def test_partial_fulfillment_and_missing_candidates_never_auto(setup):
    d = setup([("T1", "가계대출", ["대출 잔액"]), ("T2", "판매신용", ["판매 잔액"])])
    result = decision(d, '공식 항목 "대출 잔액"과 "없는 항목"')
    assert not result["auto_eligible"] and result["missing_requirements"]
    assert result["reason"] == "search_insufficient"
    result = decision(d, '공식 항목 "대출 잔액"과 "판매 잔액"', ["301:T1"])
    assert result["outside_candidate_ids"] == ["301:T2"]
    assert not result["auto_eligible"]


@pytest.mark.parametrize(
    "suffix", [" 그리고 실업도 알려줘", " 또는 수출", " 제외", " 최근 3년", " 원인도 설명해줘"]
)
def test_unparsed_and_unresolved_requirements_block_auto(setup, suffix):
    d = setup([("T1", "가계대출", ["대출 잔액"])])
    assert not decision(d, '공식 항목 "대출 잔액"' + suffix)["auto_eligible"]


def test_unquoted_natural_question_stays_unverified(setup):
    d = setup([("T1", "가계대출", ["대출 잔액"])])
    result = decision(d, "가계대출 잔액 추이를 알려줘")
    assert result["reason"] == "semantic_ambiguity" and not result["auto_eligible"]


def test_table_name_is_not_item_evidence_and_mixed_target_binding_is_unknown(setup):
    d = setup([("T1", "가계대출", ["대출 잔액"]), ("T2", "다른 통계", ["판매 잔액"])])
    assert not decision(d, '공식 항목 "가계대출"')["auto_eligible"]
    assert not decision(d, '통계표 "대출 잔액"')["auto_eligible"]
    result = decision(d, '통계표 "가계대출"의 공식 항목 "판매 잔액"')
    assert result["reason_detail"] == "official_designation_type_or_target_binding_unresolved"


def test_official_title_request_citations_and_unit_cross_branch_guard(setup):
    d = setup([("T1", "가계신용", ["가계대출 잔액", "판매신용 잔액"])])
    result = decision(d, '통계표 "가계신용"')
    assert result["auto_eligible"]
    assert result["witnesses"]["301:T1"][0]["evidence"][0]["pointer"] == "/title"
    result = decision(d, '통계표 "가계신용" 달러')
    assert not result["auto_eligible"]
    result = decision(d, '공식 항목 "가계대출 잔액" 달러')
    assert not result["auto_eligible"]


def test_metadata_gap_is_recoverable_and_not_api_unsupported(setup):
    d = setup([("T1", "가계신용", ["가계신용"])])
    result = d.search_tables('2099년 월별 공식 항목 "가계신용"', CTX)
    saved = bundle(d, result)
    assert result["status"] == "unresolved"
    assert set(result["issues"][0]) == {"code", "message", "field", "series_id", "retryable"}
    assert result["issues"][0]["retryable"]
    assert saved["selection_decision"]["reason"] == "metadata_insufficient"
    assert saved["a_outcome"] is None
    assert saved["a2_state"]["by_doc_id"]["301:T1"]["api_support"] == "unverified"
    assert d.confirm_tables(saved["candidate_set_id"], [1], CTX)["status"] == "ok"


def test_technical_failure_safe_restart_and_reassessment(setup, monkeypatch):
    d = setup([("T1", "가계신용", ["가계신용"])])

    def fail(*args):
        raise RuntimeError("do-not-log-temporary-credential")

    monkeypatch.setattr(d.table_policy, "decide", fail)
    result = build_discovery_graph(d).invoke({"query": '통계표 "가계신용"', "context": CTX})
    assert result["stage_outcome"]["status"] == "unresolved"
    saved = d.repository.get(result["candidate_set_id"], CTX)
    assert saved["selection_decision"]["reason"] == "technical_failure"
    assert "do-not-log" not in json.dumps(saved)
    restarted = TableDiscovery(d.search, CandidateStore(d.repository.path), selection_mode="auto")
    restored = restarted.reassess_tables(saved["candidate_set_id"], CTX)
    assert restored["status"] == "ok"
    child = bundle(restarted, restored)
    assert child["a_answers"] == [] and not child["diagnostics"]["a2_resume"]["a1_rerun"]
    assert restarted.reassess_tables(saved["candidate_set_id"], CTX) == restored


def test_new_search_requirements_rerun_a1_and_preserve_original(setup, monkeypatch):
    d = setup([("T1", "가계신용", ["가계신용"]), ("T2", "물가", ["물가"])])
    original = '통계표 "가계신용"'
    # Shadow keeps the initial result available for an additive user requirement.
    d = TableDiscovery(d.search, d.repository, selection_mode="shadow")
    parent = bundle(d, d.search_tables(original, CTX))
    called, retrieve = [], d.retrieve

    def observe(*args, **kwargs):
        called.append(kwargs)
        return retrieve(*args, **kwargs)

    monkeypatch.setattr(d, "retrieve", observe)
    result = d.clarify_tables(parent["candidate_set_id"], '통계표 "물가"', CTX)
    child = bundle(d, result)
    assert len(called) == 1 and called[0]["original_query"] == original
    assert child["original_query"] == original
    assert child["diagnostics"]["a2_resume"]["a1_rerun"]
    assert len(child["selection_decision"]["proposed_ids"]) == 2


def test_direct_selection_during_criterion_question_and_proposal_tamper(setup):
    d = setup([("T1", "명목 생산", ["생산액"]), ("T2", "실질 생산", ["생산액"])])
    parent = bundle(d, d.search_tables('공식 항목 "생산액"', CTX))
    result = d.confirm_tables(parent["candidate_set_id"], ["301:T2"], CTX)
    assert result["status"] == "ok"
    assert d.repository.get(parent["candidate_set_id"], CTX)["selection_origin"] == "user"
    with pytest.raises(StageFailure, match="a2_proposal_not_verified"):
        d.table_policy.validate_proposal(parent["a1_state"], parent["a2_state"], ["301:T1"])
