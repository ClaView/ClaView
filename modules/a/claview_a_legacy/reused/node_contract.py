"""Node contract frame copied from the old A graph (commit 55fd41b), with the contract tables as arguments.

The old ``_wrap`` read ``NODE_WRITES``/``GAP_WRITES``/``REDUCER_FIELDS`` from the old contract module and rebuilt
the old ``ARunContext`` for audit mode. Here the tables are parameters and the audited context is rebuilt with
``dataclasses.replace`` (every non-None field is wrapped in ``_AttrRecorder`` under its field name). For the old
context (config, catalog always set; client optional) this is the same recording as before.

Checks kept as they were:
- a write outside the declared fields -> ContractViolation (always)
- a declared non-reducer output missing -> ContractViolation (always)
- audit mode: State and context reads are recorded, and an in-place State change -> ContractViolation
"""

from __future__ import annotations

import copy
import dataclasses
import time
from collections.abc import Callable, Iterable, Mapping

# (node, State fields read, fields written, context items read)
AuditSink = Callable[[str, set, set, set], None]


# Source: modules/a/claview_aflow/graph.py:29-30 (commit 55fd41b), copied verbatim.
class ContractViolation(RuntimeError):
    """노드가 계약 밖으로 State를 바꿨다."""


# Source: modules/a/claview_aflow/graph.py:33-47 (commit 55fd41b), copied verbatim.
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


# Source: modules/a/claview_aflow/graph.py:50-58 (commit 55fd41b), copied verbatim.
class _AttrRecorder:
    """config·catalog 속성 접근을 "config.k"처럼 기록한다."""

    def __init__(self, target, prefix: str, sink: set):
        self._target, self._prefix, self._sink = target, prefix, sink

    def __getattr__(self, name):
        self._sink.add(f"{self._prefix}.{name}")
        return getattr(self._target, name)


def record_context(context, sink: set):
    """Audit view of a dataclass run context: each non-None field records attribute reads as "field.attr"."""
    wrapped = {
        f.name: _AttrRecorder(value, f.name, sink)
        for f in dataclasses.fields(context)
        if (value := getattr(context, f.name)) is not None
    }
    return dataclasses.replace(context, **wrapped)


# Source: modules/a/claview_aflow/graph.py:65-102 (commit 55fd41b). Body unchanged except: contract tables are
# arguments (node_writes, gap_writes, reducer_fields) and the audited context comes from audit_context.
def wrap(
    name: str,
    fn,
    audit: AuditSink | None,
    *,
    node_writes: Mapping[str, Iterable[str]],
    gap_writes: Iterable[tuple[str, str]] = (),
    reducer_fields: Iterable[str] = frozenset(),
    audit_context: Callable = record_context,
):
    """Wrap node ``fn(state, runtime)`` with the write/missing-output/in-place checks for node ``name``."""
    declared = frozenset(node_writes[name])
    allowed = declared | {f for (n, f) in gap_writes if n == name}
    required = declared - frozenset(reducer_fields)

    def node(state, runtime) -> dict:
        reads: set = set()
        ctx_reads: set = set()
        if audit:
            before = copy.deepcopy(dict(state))
            view = _ReadRecorder(state, reads)
            runtime = runtime.override(context=audit_context(runtime.context, ctx_reads))
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
