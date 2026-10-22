"""A 통합 계약 v1.9 (docs/01 "통합 계약 v1.8" + 10절 v1.9 차분)의 실행 가능한 형태.

단계 0 범위: 타입, 기본형, 노드별 읽기·쓰기 선언, 설정 검증.
정답(EvalCase.gold)은 이 모듈의 어떤 타입에도 들어가지 않는다.
"""

from __future__ import annotations

import copy
import operator
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict


class ConfigError(ValueError):
    """config 검증 실패. 그래프를 돌리지 않는다 (v1.8 1절 입력 경계 1)."""


# ---------------------------------------------------------------- reducer


def merge_dict(left: dict, right: dict) -> dict:
    """evidence(evidence_id 키)·timings_ms(노드 키) 병합. 노드는 새 값만 돌려준다."""
    out = dict(left or {})
    out.update(right or {})
    return out


# ---------------------------------------------------------------- 실행 문맥



# A v1 search fusion (S5). Recorded verbatim in fusion_log; "rrf_k" is kept only because the
# recorded params carry it (rank fusion is not part of A v1).
# A v1 rule versions (S1–S3·S6·J2 lexicon and the search log); alias "none" is the only supported alias set.
RULE_VERSIONS = {"normalize": "v1", "family": "none", "alias": "none"}
# J7 candidate cards per scope (docs/05 initial 5); J7 records it as state k.
CANDIDATE_CARDS = 5

FUSION_METHOD = "sum"
FUSION_PARAMS = {
    "weights": {"title": 1.0, "item": 1.0, "model": 1.0},
    "normalize": "max",
    "rrf_k": 60,
    "channel_input": "merged",
}


ALGORITHM = "a_v1"  # the one algorithm version this code implements


@dataclass(frozen=True)
class ARunConfig:
    """Operational settings of an A v1 run. The algorithm itself is fixed (ALGORITHM).

    model: "on" interprets the question with Q6G and arbitrates rule/model picks by conditions (A v1);
           "off" runs without a model (rule requirements and rule picks only).
    as_of: reference date (YYYY-MM-DD) for open periods and coverage ends; None uses metadata snapshots.
    """
    model: Literal["on", "off"] = "on"
    as_of: str | None = None

    def snapshot(self) -> dict:
        """값 사본. 반환값을 바꿔도 config와 이미 기록된 스냅숏이 바뀌지 않는다 (N2)."""
        return copy.deepcopy({"algorithm": ALGORITHM, "model": self.model, "as_of": self.as_of,
                              "rule_versions": RULE_VERSIONS})


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_config(config: ARunConfig, catalog: "ACatalog | None" = None) -> None:
    """닫힌 스키마로 검사한다. 정의 밖 값은 거부한다 (N3, T-05 정답 통로 차단)."""
    if config.model not in ("on", "off"):
        raise ConfigError(f"model은 on 또는 off: {config.model!r}")
    if config.as_of is not None and not (isinstance(config.as_of, str) and _DATE.match(config.as_of)):
        raise ConfigError(f"as_of는 None 또는 YYYY-MM-DD: {config.as_of!r}")
    if catalog is not None:
        version = RULE_VERSIONS["normalize"]
        if version not in catalog.item_index["df"] or version not in catalog.lexicon:
            raise ConfigError(f"catalog에 정규화 판본 {version!r}이 없다")


@dataclass(frozen=True)
class ACatalog:
    """실행 전 1회 생성, 읽기 전용 (v1.8 1절)."""

    documents: dict  # table_id -> {org_id, title, catalog_paths[]}
    title_index: dict  # table_id -> {title, path, text_norm}
    item_index: dict  # {"entries": {version: {name: [(table_id, obj_id, item_id)]}}, "df": {version: {name: 표 수}}}
    families: dict  # rule_version -> {table_id: [{table_id, relation_type}]}
    lexicon: dict  # normalize 판본 -> {title, path, items, idf{title, item}} 문자 2-gram 색인 (단계 1, S1·S2·S3)
    meta_store: dict  # table_id -> J1이 읽는 meta 원형
    titles: frozenset
    aliases: tuple  # 승인분만 (지금 0건)
    abbr_pairs: tuple  # ({long, abbr, table_id, source_field},)
    catalog_snapshot: str
    meta_snapshot: str
    q6g_tables: dict = field(default_factory=dict)
    arbitration_properties: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ARunContext:
    config: ARunConfig
    catalog: ACatalog
    # 모델 호출 수단(model.ModelClient). 설정값이 아니라 config 스냅숏에 넣지 않는다. 모델 스위치가 켜졌는데
    # 없으면 run_state 입구에서 거부한다(E7 사용자 선택). 이름은 CallRow.model로 남는다 (단계 5)
    client: Any = None


@dataclass(frozen=True)
class EvalCase:
    """평가 실행기만 쓴다. 그래프·run_a에 넘기지 않는다."""

    case_id: str
    question: str
    as_of: str | None
    gold: dict
    layer: Literal["golden", "official", "colloquial", "boundary"]
    review_status: str


# ---------------------------------------------------------------- State


class AState(TypedDict):
    question: str
    run_meta: dict
    # Q
    query: str
    query_nospace: str
    offsets: list
    cond_period: list
    cond_frequency: list
    cond_basis: list
    cond_unit: list
    cond_breakdown: list
    cond_exclude: list
    requirements: list
    requirements_rule: list
    interpretation: dict
    concepts: dict
    search_queries: list
    # S
    hits_title: dict
    hits_item: dict
    hits_model: dict
    pool_base: list
    pool_base_by_scope: dict
    fusion_log: dict
    pool: list
    pool_by_scope: dict
    relations: list
    search_log: dict
    # J
    meta: dict
    matched_items: dict
    matched_by_scope: dict
    fit_period: dict
    period_by_scope: dict
    fit_basis: dict
    basis_by_scope: dict
    ranked: list
    ranked_by_scope: dict
    k: int
    cards: list
    cards_by_scope: dict
    # T
    pick_rule: dict
    pick_model: dict
    final: dict
    requirement_matrix: dict
    confidence: str
    confidence_by_scope: dict
    a_result: dict | None
    b_handoff: dict | None
    # reducer 필드 (v1.8 2절: 이 넷뿐)
    issues: Annotated[list, operator.add]
    evidence: Annotated[dict, merge_dict]
    calls: Annotated[list, operator.add]
    timings_ms: Annotated[dict, merge_dict]


STATE_FIELDS: tuple[str, ...] = tuple(AState.__annotations__)
REDUCER_FIELDS = frozenset({"issues", "evidence", "calls", "timings_ms"})


# ---------------------------------------------------------------- issue


ISSUE_SEVERITY: dict[str, str] = {
    "q6g_fallback": "warn", "q6g_dropped": "info", "replay_miss": "warn",
    "model_table_overridden": "info", "no_evidence_cited": "warn",
    "no_candidates": "error",
    "no_requirements": "error",  # nothing left to judge; A returns no table
    "frequency_conflict_on_final": "error",
    "period_outside_on_final": "error",
    "auth_failed": "error",
    "q6_failed": "warn",
    "t2_failed": "warn",
    "model_rejected": "warn",
    "final_replaced_by_rule": "warn",
    "requirement_unresolved": "warn",
    "t2_skipped_no_evidence": "info",
    "sibling_cap_reached": "info",
    "ties_broken": "info",
}


def make_issue(code: str, node: str, detail: str = "", **extra: Any) -> dict:
    if code not in ISSUE_SEVERITY:
        raise KeyError(f"issue code는 표에 추가한 뒤 쓴다: {code}")
    issue = {
        "code": code,
        "node": node,
        "severity": ISSUE_SEVERITY[code],
        "evidence_ids": list(extra.pop("evidence_ids", [])),
        "detail": detail,
    }
    for key in ("scope_id", "condition_id"):
        if key in extra:
            issue[key] = extra.pop(key)
    if extra:
        raise KeyError(f"Issue에 없는 키: {sorted(extra)}")
    return issue


# ---------------------------------------------------------------- 노드 계약


COND_ALL = (
    "cond_period",
    "cond_frequency",
    "cond_basis",
    "cond_unit",
    "cond_breakdown",
    "cond_exclude",
)

# v1.8 4절 "입력 (State / context)"의 State 쪽을 그대로 옮겼다.
# 단계 0 실행 대조로 빠져 있던 T5의 run_meta·pool을 v1.9에서 더했다(E2).
NODE_READS: dict[str, frozenset] = {
    "Q1": frozenset({"question"}),
    "Q2": frozenset({"question", "query", "query_nospace", "offsets"}),  # question: 조건 quote (단계 1)
    "Q3": frozenset({"question", "query", "query_nospace", "offsets"}),
    "Q4": frozenset({"question", "query", "query_nospace", "offsets"}),
    "Q5": frozenset({"question", "query", "offsets", "cond_period", "cond_frequency", "cond_basis", "cond_unit"}),
    "Q6": frozenset({"question", *COND_ALL, "requirements"}),
    "Q7": frozenset({"query", "offsets", *COND_ALL, "requirements"}),  # offsets: 조건 구간을 정리본으로 되돌림 (단계 1)
    "S1": frozenset({"search_queries"}),
    "S2": frozenset({"search_queries"}),
    "S3": frozenset({"concepts"}),
    "S5": frozenset({"hits_title", "hits_item", "hits_model"}),
    "S6": frozenset({"pool_base"}),
    "S7": frozenset({"hits_title", "hits_item", "hits_model", "fusion_log", "pool", "relations", "run_meta"}),
    "J1": frozenset({"pool"}),
    "J2": frozenset({
        "question", "query", "query_nospace", "offsets", "cond_breakdown", "cond_exclude",
        "cond_basis", "requirements", "concepts", "meta",
        "cond_period", "cond_frequency", "cond_unit",  # 단계 3: 조건 구간은 일반 질문어에서 뺀다
    }),
    "J3": frozenset({"cond_period", "cond_frequency", "requirements", "meta", "issues"}),
    "J4": frozenset({"cond_basis", "cond_unit", "cond_exclude", "meta", "matched_items"}),
    "J5": frozenset({"pool", "fit_period", "fit_basis", "matched_items",
                     "requirements", "meta",  # 단계 4: 요구별 판정과 형제 동률(표명 괄호 앞 이름)
                     "query_nospace", "cond_basis", "cond_breakdown",
                     "question", "query", "offsets", "search_queries", *COND_ALL,  # measure spans
                     "issues"}),  # 7번 1단계: Q5가 남긴 귀속 애매 조건은 가점·탈락에 쓰지 않는다
    "J7": frozenset({"ranked", "meta", "relations", "matched_items", "fit_period", "fit_basis", "evidence"}),
    "T1": frozenset({"ranked"}),
    "T2": frozenset({"requirements"}),
    "T3": frozenset({
        "pick_rule", "pick_model", "cards", "cond_period", "cond_frequency", "requirements",
        "fit_period", "fit_basis", "relations",
        "matched_items",  # 단계 4: 분해 조건의 분류값 매치를 요구 판정에 쓴다
        "issues",  # 7번 1단계: Q5가 남긴 귀속 애매 조건
    }),
    "T4": frozenset({
        "final", "ranked", "fit_period", "fit_basis", "requirement_matrix", "pick_rule",
        "pick_model", "issues",
    }),
    "T5": frozenset({
        "question", "final", "confidence", "requirement_matrix", "requirements", *COND_ALL,
        "meta", "matched_items", "fit_basis", "fit_period", "cards", "evidence", "issues",
        "run_meta", "pool",  # v1.9 E2
    }),
}

# v1.8 4절 "/ context" 열. "config.필드"·"catalog.필드" 단위로 실제 접근을 기록해 대조한다 (T-02).
NODE_CONTEXT_READS: dict[str, frozenset] = {
    "Q1": frozenset(),
    "Q2": frozenset({"config.as_of"}),
    "Q3": frozenset(),
    "Q4": frozenset(),
    "Q5": frozenset({"catalog.meta_store"}),  # 나열 다듬기의 분류값 이름 근거
    "Q6": frozenset({"config.model", "catalog.q6g_tables", "client.complete", "client.name"}),
    "Q7": frozenset({"catalog.aliases", "catalog.meta_store"}),  # 요구 검색식: 카탈로그 이름은 조사를 떼지 않는다
    "S1": frozenset({"catalog.lexicon"}),
    "S2": frozenset({"catalog.item_index", "catalog.lexicon"}),
    "S3": frozenset(),  # 모델 개념 검색어 채널: A v1에는 개념 호출이 없어 비어 있다
    "S5": frozenset(),
    "S6": frozenset(),
    "S7": frozenset(),
    "J1": frozenset({"catalog.meta_store", "catalog.meta_snapshot"}),
    "J2": frozenset({"catalog.item_index"}),
    "J3": frozenset(),
    "J4": frozenset(),
    "J5": frozenset({"catalog.meta_store", "catalog.abbr_pairs", "config.as_of"}),
    "J7": frozenset(),
    "T1": frozenset(),
    "T2": frozenset(),
    "T3": frozenset({"catalog.arbitration_properties"}),
    "T4": frozenset(),
    "T5": frozenset({"config.as_of"}),
}

# v1.8 2절 AState의 "쓰는 노드" 열. timings_ms는 모든 노드가 쓴다(래퍼가 채움).
NODE_WRITES: dict[str, frozenset] = {
    "Q1": frozenset({"query", "query_nospace", "offsets"}),
    "Q2": frozenset({"cond_period"}),
    "Q3": frozenset({"cond_frequency"}),
    "Q4": frozenset({"cond_basis", "cond_unit"}),
    "Q5": frozenset({"cond_breakdown", "cond_exclude", "requirements", "issues"}),  # v1.9 E1
    "Q6": frozenset({"concepts", "issues", "calls"}),
    "Q7": frozenset({"search_queries"}),
    "S1": frozenset({"hits_title"}),
    "S2": frozenset({"hits_item"}),
    "S3": frozenset({"hits_model"}),
    "S5": frozenset({"pool_base", "fusion_log", "issues"}),
    "S6": frozenset({"pool", "relations"}),
    "S7": frozenset({"search_log"}),
    "J1": frozenset({"meta"}),
    "J2": frozenset({"matched_items", "evidence"}),
    "J3": frozenset({"fit_period", "evidence"}),
    "J4": frozenset({"fit_basis", "evidence"}),
    "J5": frozenset({"ranked", "evidence"}),
    "J7": frozenset({"cards", "k"}),
    "T1": frozenset({"pick_rule"}),
    "T2": frozenset({"pick_model"}),
    "T3": frozenset({"final", "requirement_matrix", "issues"}),
    "T4": frozenset({"confidence"}),
    "T5": frozenset({"a_result", "b_handoff", "issues"}),
}

# 실행 대조로 드러났지만 아직 계약에 넣지 않은 읽기·쓰기. v1.9에서 E1·E2를 본표로 옮겨 지금은 비어 있다.
GAP_WRITES: dict[tuple[str, str], str] = {}
GAP_READS: dict[tuple[str, str], str] = {}


NODE_ORDER: tuple[str, ...] = (
    "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7",
    "S1", "S2", "S3", "S5", "S6", "S7",
    "J1", "J2", "J3", "J4", "J5", "J7",
    "T1", "T2", "T3", "T4", "T5",
)
INITIAL_FIELDS = frozenset({"question", "run_meta"})

# Reads of the base (whole-question) nodes; the requirement-local wrappers (scoped_nodes) add the
# Q6G and per-scope fields below. Each non-reducer field still has exactly one writer.
BASE_NODE_READS = dict(NODE_READS)
NODE_READS["Q6"] = (NODE_READS["Q6"] - {"requirements"}) | {"requirements_rule"}
NODE_WRITES["Q5"] = (NODE_WRITES["Q5"] - {"requirements"}) | {"requirements_rule"}
NODE_WRITES["Q6"] |= {"requirements", "interpretation"}
_scope_inputs = {"question", "query", "query_nospace", "offsets", "requirements", "search_queries", *COND_ALL, "pool", "meta"}
_scoped_fields = {"matched_items": "matched_by_scope", "fit_period": "period_by_scope", "fit_basis": "basis_by_scope",
                  "ranked": "ranked_by_scope", "cards": "cards_by_scope"}
for _node in ("Q7", "S5", "S6", "S7", "J2", "J3", "J4", "J5", "J7", "T1", "T2", "T3", "T4", "T5"):
    NODE_READS[_node] |= {"interpretation"}
for _node in ("J2", "J3", "J4", "J5", "J7", "T3", "T4", "T5"):
    NODE_READS[_node] |= _scope_inputs | {"pool_by_scope"}
    NODE_READS[_node] |= {_scoped_fields[f] for f in BASE_NODE_READS[_node] if f in _scoped_fields}
for _node, _field in {"S5": "pool_base_by_scope", "S6": "pool_by_scope", "J2": "matched_by_scope",
                     "J3": "period_by_scope", "J4": "basis_by_scope", "J5": "ranked_by_scope",
                     "J7": "cards_by_scope", "T4": "confidence_by_scope"}.items():
    NODE_WRITES[_node] |= {_field}
NODE_READS["Q7"] |= {"question"}
# Q7은 애매 조건 표시를 읽어 요구별 검색식에서 뺀다
NODE_READS["Q7"] |= {"issues"}
for _node in ("J2", "J3", "J4", "J5", "J7", "T3", "T4", "T5"):
    NODE_READS[_node] |= {"issues"}  # 요구별 화면(view_for)이 애매 조건 구간을 인용에서 뺀다
NODE_READS["S5"] |= {"requirements", "search_queries"}
NODE_READS["S6"] |= {"requirements", "pool_base_by_scope"}
NODE_READS["S7"] |= {"pool_by_scope"}
NODE_READS["J5"] |= {"pool_base_by_scope"}
NODE_READS["T1"] |= {"requirements", "ranked_by_scope"}
NODE_READS["T3"] |= {"ranked_by_scope"}
NODE_READS["T5"] |= {"confidence_by_scope"}
