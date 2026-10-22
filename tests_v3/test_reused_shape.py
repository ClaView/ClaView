"""Unit tests of the reused copies on their own (no old module is imported here).

Sentences are synthetic examples written for these rules. No model or network call.
"""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import dataclass

import pytest

from claview_v3.reused import q_rules
from claview_v3.reused.judgment_runtime import POLICY, AttemptGate, BudgetStop, JudgmentClient, VirtualClock
from claview_v3.reused.model import CachingClient, HcxClient, ModelError, ModelReply
from claview_v3.reused.node_contract import ContractViolation, wrap
from claview_v3.reused.q_nodes import frequencies, normalize, q1, q3
from claview_v3.reused.table_sets import score_sets

# ---------------------------------------------------------------- Q1


def test_normalize_collapses_space_and_maps_offsets():
    question = "  가계　대출   잔액\t "
    out = normalize(question)
    assert out["query"] == "가계 대출 잔액"
    assert out["query_nospace"] == "가계대출잔액"
    assert len(out["offsets"]) == len(out["query"])
    for k, ch in enumerate(out["query"]):
        if ch != " ":
            assert question[out["offsets"][k]] == ch


def test_normalize_nfkc_expansion_keeps_source_offset():
    out = normalize("㈜ ①")
    assert out["query"] == "(주) 1"
    assert out["offsets"][:3] == [0, 0, 0]


def test_q1_node_shape_matches_pure_entry():
    assert q1({"question": "수출액 보여줘"}) == normalize("수출액 보여줘")


# ---------------------------------------------------------------- Q3


def _freq(question):
    n = normalize(question)
    return frequencies(question, n["query"], n["offsets"])


def test_frequency_row_shape_and_span():
    question = "수출액이  매달 얼마였는지"
    (row,) = _freq(question)
    assert set(row) == {"condition_id", "status", "quote", "span", "value"}
    assert (row["condition_id"], row["status"], row["value"], row["quote"]) == ("F1", "explicit", "M", "매달")
    assert question[row["span"][0]:row["span"][1]] == "매달"


def test_frequency_end_anchor():
    (row,) = _freq("잔액을 분기말마다 보여줘")
    assert (row["value"], row["anchor"]) == ("Q", "end")


def test_period_words_are_not_frequency():
    assert _freq("최근 6개월간 실적") == []
    assert _freq("2026년 1분기 실적") == []


def test_q3_node_reads_query_nospace():
    with pytest.raises(KeyError):
        q3({"question": "매달", "query": "매달", "offsets": [0, 1]})


def test_find_periods_spans_without_as_of():
    text = "2024년 1분기부터 2025년 4분기까지 분기별 잔액"
    (p,) = q_rules.find_periods(text, None)
    assert text[p["i"]:p["j"]].strip() == "2024년 1분기부터 2025년 4분기까지"
    assert (p["kind"], p["start"], p["end"], p["status"]) == ("range", "2024-Q1", "2025-Q4", "explicit")


def test_stamp_months():
    assert q_rules.stamp_months("2024-Q2") == (2024 * 12 + 3, 2024 * 12 + 5)
    assert q_rules.stamp_months("2024-13") is None


# ---------------------------------------------------------------- node contract wrapper


@dataclass(frozen=True)
class _Ctx:
    config: object
    catalog: object
    client: object = None


class _Runtime:
    def __init__(self, context):
        self.context = context

    def override(self, **kwargs):
        return _Runtime(kwargs.get("context", self.context))


class _Config:
    as_of = None


WRITES = {"N1": frozenset({"a", "issues"}), "N2": frozenset({"b"})}
REDUCERS = frozenset({"issues"})


def _run(fn, name="N1", audit=None, state=None, gap=()):
    node = wrap(name, fn, audit, node_writes=WRITES, gap_writes=gap, reducer_fields=REDUCERS)
    return node(state if state is not None else {"x": [1]}, _Runtime(_Ctx(_Config(), object())))


def test_wrap_passes_declared_output_and_adds_timing():
    out = _run(lambda s, r: {"a": 1})
    assert out["a"] == 1 and set(out["timings_ms"]) == {"N1"}


def test_wrap_rejects_undeclared_write():
    with pytest.raises(ContractViolation, match="선언 밖"):
        _run(lambda s, r: {"a": 1, "b": 2})


def test_wrap_gap_write_is_allowed():
    assert _run(lambda s, r: {"a": 1, "b": 2}, gap=[("N1", "b")])["b"] == 2


def test_wrap_rejects_missing_output_but_not_reducer():
    with pytest.raises(ContractViolation, match="빠뜨렸다"):
        _run(lambda s, r: {"issues": []})
    assert "issues" not in _run(lambda s, r: {"a": 1})


def test_wrap_audit_records_reads_and_rejects_in_place_change():
    seen = []

    def reader(state, runtime):
        state["x"]
        runtime.context.config.as_of
        return {"a": 1}

    _run(reader, audit=lambda *a: seen.append(a))
    assert seen == [("N1", {"x"}, {"a"}, {"config.as_of"})]

    def mutating(state, runtime):
        state["x"].append(2)
        return {"a": 1}

    with pytest.raises(ContractViolation, match="제자리"):
        _run(mutating, audit=lambda *a: None)


# ---------------------------------------------------------------- set scoring


def test_score_sets_exact_and_missing():
    ok = score_sets([["A"], ["B", "C"]], ["C", "A"], ["A", "C"], ["A", "C"], 5)
    assert ok["set_exact"] and ok["missing"] == [] and ok["extra"] == []
    bad = score_sets([["A"], ["A"]], ["A", "A"], ["A"], ["A"], 5)
    assert not bad["set_exact"] and bad["n_output"] == 1 and len(bad["missing"]) == 1
    miss = score_sets([["Z"]], ["A"], ["A"], ["A"], 5)
    assert miss["missing"][0]["where"] == "not_in_pool" and miss["extra"] == ["A"]


# ---------------------------------------------------------------- model clients (fake transport only)


FAKE_KEY = "fake-key-for-tests"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_body(content):
    return {"status": {"code": "20000"},
            "result": {"message": {"content": content}, "finishReason": "stop",
                       "usage": {"promptTokens": 3, "completionTokens": 2}}}


def test_hcx_takes_key_argument_only():
    with pytest.raises(ModelError) as err:
        HcxClient("")
    assert err.value.error_class == "auth"
    sent = []

    def opener(req, timeout):
        sent.append(req)
        return _Resp(json.dumps(_ok_body('{"a": 1}')).encode())

    client = HcxClient(FAKE_KEY, min_interval=0, opener=opener)
    assert FAKE_KEY not in repr(client)
    reply = client.complete("Q6", "sys", {"q": 1}, {"$id": "x", "type": "object"})
    assert (reply.text, reply.tokens_in, reply.tokens_out) == ('{"a": 1}', 3, 2)
    body = json.loads(sent[0].data)
    assert "$id" not in body["responseFormat"]["schema"] and body["maxCompletionTokens"] == 1024


def test_hcx_http_error_classes():
    def opener(req, timeout):
        raise urllib.error.HTTPError("u", 403, "x", {}, None)

    with pytest.raises(ModelError) as err:
        HcxClient(FAKE_KEY, min_interval=0, opener=opener).complete("T2", "s", {}, {})
    assert err.value.error_class == "auth" and FAKE_KEY not in str(err.value)


class _Inner:
    name = "fake"

    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def complete(self, node, system, payload, schema):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, ModelError):
            raise out
        return out


def test_caching_client_replays_history_in_order():
    inner = _Inner([ModelError("transient", "t"), ModelReply("ok")])
    client = CachingClient(inner)
    with pytest.raises(ModelError):
        client.complete("Q6", "s", {}, {})
    assert client.complete("Q6", "s", {}, {}).text == "ok"
    client.start_run()
    with pytest.raises(ModelError):
        client.complete("Q6", "s", {}, {})
    assert client.complete("Q6", "s", {}, {}).text == "ok"
    assert client.complete("Q6", "s", {}, {}).text == "ok"
    assert (inner.calls, client.requests_sent) == (2, 2)


def test_gate_and_disk_cache_retry_transient_only(tmp_path):
    clock = VirtualClock()
    with AttemptGate(tmp_path / "ledger.jsonl", 3, mode="mock", clock=clock) as gate:
        inner = _Inner([ModelError("transient", "t", retry_after=1.0), ModelReply("ok", 1, 1),
                        ModelError("schema", "s")])
        client = JudgmentClient(inner, gate, tmp_path / "cache")
        assert client.complete("Q6G", "s", {"n": 1}, {}).text == "ok"
        assert gate.used == 2 and clock.now == POLICY["backoff_base_seconds"]  # backoff 5s > 3s pacing
        assert client.complete("Q6G", "s", {"n": 1}, {}).text == "ok"  # disk cache hit, no attempt
        assert gate.used == 2 and client.rows[-1]["cache_hit"] is True
        with pytest.raises(ModelError) as err:
            client.complete("Q6G", "s", {"n": 2}, {})  # schema error: not retried
        assert err.value.error_class == "schema" and gate.used == 3
        with pytest.raises(ModelError):
            client.complete("Q6G", "s", {"n": 2}, {})  # terminal failure is cached
        with pytest.raises(BudgetStop):
            client.complete("Q6G", "s", {"n": 3}, {})
    rows = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf8").splitlines()]
    assert [r["event"] for r in rows] == ["reserved", "finished"] * 3
    assert [r["ok"] for r in rows if r["event"] == "finished"] == [False, True, False]
