"""입력 어댑터: make_initial_state (v1.8 1절 입력 경계 2, 3절 기본형).

config·catalog의 값은 사본으로만 State에 들어간다 (v1.9 N2).
"""

from __future__ import annotations

import copy
import hashlib
import json

from .contract import CANDIDATE_CARDS, FUSION_METHOD, FUSION_PARAMS, RULE_VERSIONS, STATE_FIELDS, ACatalog, ARunConfig, AState


def empty_channel() -> dict:
    return {"per_query": {}, "ranked_full": [], "merged": []}


def default_fusion_log() -> dict:
    return {
        "method": FUSION_METHOD,
        "params": copy.deepcopy(FUSION_PARAMS),
        "normalize": "max",
        "ties": [],
        "cut": {"pre": [], "post": []},
    }


def make_run_meta(question: str, config: ARunConfig, catalog: ACatalog) -> dict:
    config_snapshot = config.snapshot()
    # input_hash = sha256(question + config_snapshot을 키 정렬·ensure_ascii=False JSON으로 쓴 문자열), UTF-8
    payload = question + json.dumps(config_snapshot, ensure_ascii=False, sort_keys=True)
    metadata = {
        "config_snapshot": config_snapshot,
        "catalog_snapshot": catalog.catalog_snapshot,
        "meta_snapshot": catalog.meta_snapshot,
        "input_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    if config.model == "on":
        from .q6g import system_prompt
        metadata["q6g_system_sha256"] = system_prompt()[1]
    return metadata


def make_initial_state(question: str, config: ARunConfig, catalog: ACatalog) -> AState:
    run_meta = make_run_meta(question, config, catalog)
    fusion_log = default_fusion_log()
    state: AState = {
        "question": question,
        "run_meta": run_meta,
        "query": "",
        "query_nospace": "",
        "offsets": [],
        "cond_period": [],
        "cond_frequency": [],
        "cond_basis": [],
        "cond_unit": [],
        "cond_breakdown": [],
        "cond_exclude": [],
        "requirements": [],
        "requirements_rule": [],
        "interpretation": {"model": {"status": "off", "dropped": [], "measure_texts": {}}},
        "concepts": {"status": "off", "measures": [], "breakdowns": [], "search_terms": []},
        "search_queries": [],
        "hits_title": empty_channel(),
        "hits_item": empty_channel(),
        "hits_model": empty_channel(),
        "pool_base": [],
        "pool_base_by_scope": {},
        "fusion_log": fusion_log,
        "pool": [],
        "pool_by_scope": {},
        "relations": [],
        "search_log": {
            "versions": {
                "catalog": run_meta["catalog_snapshot"],
                "meta": run_meta["meta_snapshot"],
                "rule_versions": copy.deepcopy(RULE_VERSIONS),
                "df_scope": None,  # S7이 normalize 판본으로 채운다
            },
            "per_channel": {},
            "fusion": copy.deepcopy(fusion_log),
            "pool": [],
            "sibling_added": 0,
            "pool_size": 0,
        },
        "meta": {},
        "matched_items": {},
        "matched_by_scope": {},
        "fit_period": {},
        "period_by_scope": {},
        "fit_basis": {},
        "basis_by_scope": {},
        "ranked": [],
        "ranked_by_scope": {},
        "k": CANDIDATE_CARDS,
        "cards": [],
        "cards_by_scope": {},
        "pick_rule": {"table_id": None},
        "pick_model": {
            "status": "off",
            "table_id": None,
            "self_confidence": None,
            "model_note": None,
            "used_evidence_ids": [],
        },
        "final": {"table_id": None, "source": "none", "model_review": "not_used", "reason_codes": []},
        "requirement_matrix": {},
        "confidence": "low",
        "confidence_by_scope": {},
        "a_result": None,
        "b_handoff": None,
        "issues": [],
        "evidence": {},
        "calls": [],
        "timings_ms": {},
    }
    missing = set(STATE_FIELDS) - set(state)
    if missing:  # 기본형 표와 AState가 어긋나면 바로 드러나게 한다
        raise AssertionError(f"기본형 누락: {sorted(missing)}")
    return state
