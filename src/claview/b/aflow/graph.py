"""24노드 그래프 (v1.8 3절, J6는 J7에 병합). S4는 v1 경로에 없다.

노드 래퍼가 지키는 것:
- 선언 밖 필드를 반환하면 ContractViolation (항상)
- 선언한 비누적 출력을 빠뜨리면 ContractViolation (항상, v1.9)
- audit 모드: State·context 읽기를 기록하고, 노드가 State를 제자리에서 바꾸면 ContractViolation (v1.9 N1)
"""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from .contract import GAP_WRITES, NODE_ORDER, NODE_WRITES, REDUCER_FIELDS, ARunContext, AState
from .nodes import NODES, route_after_s7


class ContractViolation(RuntimeError):
    """노드가 계약 밖으로 State를 바꿨다."""


class _ReadRecorder(Mapping):
    """노드가 읽은 State 필드를 기록한다 (생산·소비 대조 시험용)."""

    def __init__(self, state: Mapping, sink: set):
        self._state, self._sink = state, sink

    def __getitem__(self, key):
        self._sink.add(key)
        return self._state[key]

    def __iter__(self):
        return iter(self._state)

    def __len__(self):
        return len(self._state)


class _AttrRecorder:
    """config·catalog 속성 접근을 "config.k"처럼 기록한다."""

    def __init__(self, target, prefix: str, sink: set):
        self._target, self._prefix, self._sink = target, prefix, sink

    def __getattr__(self, name):
        self._sink.add(f"{self._prefix}.{name}")
        return getattr(self._target, name)


# (노드, 읽은 State 필드, 쓴 필드, 읽은 context 항목)
AuditSink = Callable[[str, set, set, set], None]


def _wrap(name: str, fn, audit: AuditSink | None):
    allowed = NODE_WRITES[name] | {f for (n, f) in GAP_WRITES if n == name}
    required = NODE_WRITES[name] - REDUCER_FIELDS

    def node(state: AState, runtime: Runtime[ARunContext]) -> dict:
        reads: set = set()
        ctx_reads: set = set()
        if audit:
            before = copy.deepcopy(dict(state))
            view = _ReadRecorder(state, reads)
            ctx = runtime.context
            runtime = runtime.override(
                context=ARunContext(
                    config=_AttrRecorder(ctx.config, "config", ctx_reads),
                    catalog=_AttrRecorder(ctx.catalog, "catalog", ctx_reads),
                    client=_AttrRecorder(ctx.client, "client", ctx_reads) if ctx.client is not None else None,
                )
            )
        else:
            view = state
        start = time.perf_counter()
        out = fn(view, runtime)
        elapsed = round((time.perf_counter() - start) * 1000, 3)
        extra = set(out) - allowed
        if extra:
            raise ContractViolation(f"{name}가 선언 밖 필드를 썼다: {sorted(extra)}")
        missing = required - set(out)
        if missing:
            raise ContractViolation(f"{name}가 선언한 출력을 빠뜨렸다: {sorted(missing)}")
        if audit:
            if dict(state) != before:
                changed = sorted(k for k in before if state.get(k) != before[k])
                raise ContractViolation(f"{name}가 State를 제자리에서 바꿨다: {changed}")
            audit(name, reads, set(out), ctx_reads)
        return {**out, "timings_ms": {name: elapsed}}

    node.__name__ = name
    return node


def _wrap_router(fn, audit: AuditSink | None):
    if not audit:
        return fn

    def route(state: AState) -> str:
        reads: set = set()
        out = fn(_ReadRecorder(state, reads))
        audit("route_after_s7", reads, set(), set())
        return out

    return route


def build_graph(audit: AuditSink | None = None):
    builder = StateGraph(AState, context_schema=ARunContext)
    for name in NODE_ORDER:
        builder.add_node(name, _wrap(name, NODES[name], audit))
    builder.add_edge(START, "Q1")
    linear = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "S1", "S2", "S3", "S5", "S6", "S7")
    for a, b in zip(linear, linear[1:]):
        builder.add_edge(a, b)
    builder.add_conditional_edges("S7", _wrap_router(route_after_s7, audit), {"T5": "T5", "J1": "J1"})
    judge = ("J1", "J2", "J3", "J4", "J5", "J7", "T1", "T2", "T3", "T4", "T5")
    for a, b in zip(judge, judge[1:]):
        builder.add_edge(a, b)
    builder.add_edge("T5", END)
    return builder.compile()
