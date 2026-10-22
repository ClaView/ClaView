"""A 실행 입구 (v1.8 1절 입력 경계). EvalCase는 받지 않는다.

run_a_v1 / run_a_v1_state: A v1 실행 진입점. 운영 설정(모델 사용, 기준 시점)만 받는다.
run_a / run_state: 같은 그래프에 ARunConfig를 직접 넘기는 입구(시험·모델 없는 운영 모드용).
"""

from __future__ import annotations

import copy
from functools import lru_cache

from .contract import ACatalog, ARunConfig, ARunContext, ConfigError, validate_config
from .graph import build_graph
from .state import make_initial_state

# 24노드 경로는 recursion_limit 24 이하에서 멈춘다(langgraph 1.2.14 실측, 시험으로 고정. J6 병합 전 25).
# 기본값은 판본마다 달라 명시한다. 60은 최소값이 아니라 여유를 둔 값이다 (v1.9 E6).
RECURSION_LIMIT = 60


@lru_cache(maxsize=1)
def _graph():
    return build_graph()


def run_state(question: str, config: ARunConfig, catalog: ACatalog, graph=None, client=None) -> dict:
    """검증·초기화·실행까지 하고 최종 State를 돌려준다 (계약 시험용).

    graph 인자는 시험용 주입이다. 입력 경계의 보장은 기본 그래프 기준이다.
    client는 모델 호출 수단(model.ModelClient)이다. model on인데 없으면 Q6G가 규칙 요구로 대체한다(missing_client).
    """
    if not isinstance(question, str) or not question.strip():
        raise ConfigError("question은 빈 문자열일 수 없다")
    validate_config(config, catalog)
    if client is not None and not (isinstance(getattr(client, "name", None), str) and callable(getattr(client, "complete", None))):
        raise ConfigError("client는 name(str)과 complete()가 있어야 한다")
    state = make_initial_state(question, config, catalog)
    final = (graph or _graph()).invoke(
        state,
        context=ARunContext(config=config, catalog=catalog, client=client),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    return final


def make_report(final: dict) -> dict:
    """값 사본으로 만든다. 보고서를 바꿔도 다른 실행·설정에 닿지 않는다 (v1.9 N2)."""
    return copy.deepcopy({
        "issues": final["issues"],
        "trace": {
            "calls": final["calls"],
            "timings_ms": final["timings_ms"],
            "search": final["search_log"],
            "run_meta": final["run_meta"],
        },
    })


def run_a(question: str, config: ARunConfig, catalog: ACatalog, graph=None, client=None):
    """반환: (a_result, b_handoff, report). report는 그래프가 끝난 뒤 최종 State로 조립한다."""
    final = run_state(question, config, catalog, graph, client)
    return final["a_result"], final["b_handoff"], make_report(final)


def run_a_v1_state(question: str, catalog: ACatalog, *, client=None, as_of: str | None = None, graph=None) -> dict:
    """A v1 진입점(최종 State). 모델 해석·조건 중재를 쓰는 A v1 동작이 기본이다."""
    return run_state(question, ARunConfig(model="on", as_of=as_of), catalog, graph=graph, client=client)


def run_a_v1(question: str, catalog: ACatalog, *, client=None, as_of: str | None = None):
    """A v1 진입점. 반환: (a_result, b_handoff, report)."""
    final = run_a_v1_state(question, catalog, client=client, as_of=as_of)
    return final["a_result"], final["b_handoff"], make_report(final)
