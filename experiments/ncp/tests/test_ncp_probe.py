"""API contracts are mocked; no service access or real credentials are needed."""

import json

import httpx
import pytest

from experiments.ncp import ncp_probe as probe

DOCUMENTS = [{"id": "new", "doc": "신규취급액 기준"}, {"id": "balance", "doc": "잔액 기준"}]


def response(result):
    return httpx.Response(200, json={"status": {"code": "20000"}, "result": result})


@pytest.mark.parametrize(
    "model,version,field",
    [
        ("HCX-DASH-001", "v1", "maxTokens"),
        ("HCX-003", "v1", "maxTokens"),
        ("HCX-DASH-002", "v3", "maxTokens"),
        ("HCX-005", "v3", "maxTokens"),
        ("HCX-007", "v3", "maxCompletionTokens"),
    ],
)
def test_chat_profiles(model, version, field):
    plan = probe.make_plan("chat", model, "가계대출 금리", DOCUMENTS)[0]
    assert plan["path"] == f"/{version}/chat-completions/{model}"
    assert plan["body"][field] == 512
    assert not {"responseFormat", "tools"} & plan["body"].keys()


def test_structured_profile_and_validation():
    plan = probe.make_plan("structured", "HCX-007", "질문", DOCUMENTS)[0]
    assert plan["body"]["thinking"] == {"effort": "none"}
    assert plan["body"]["responseFormat"]["schema"] == probe.SCHEMA
    value = {"indicator": "금리", "needs_clarification": True, "missing_conditions": ["기준"]}
    summary = probe.summarize("structured", {"message": {"content": json.dumps(value)}})
    assert summary["parsed"] == value


@pytest.mark.parametrize("kind,model", [("structured", "HCX-005"), ("function", "HCX-DASH-002")])
def test_unsupported_combinations_fail_locally(kind, model):
    with pytest.raises(probe.ProbeError):
        probe.make_plan(kind, model, "질문", DOCUMENTS)


@pytest.mark.parametrize("content", ["not JSON", "{}", '{"needs_clarification":"yes"}'])
def test_structured_invalid_result(content):
    with pytest.raises(probe.ProbeError, match="schema"):
        probe.summarize("structured", {"message": {"content": content}})


def test_function_call_is_only_proposed():
    plan = probe.make_plan("function", "HCX-005", "질문", DOCUMENTS)[0]
    assert plan["body"]["maxTokens"] == 1024
    summary = probe.summarize(
        "function",
        {
            "message": {
                "toolCalls": [
                    {"function": {"name": "search_tables", "arguments": {"query": "가계대출"}}}
                ]
            }
        },
    )
    assert summary["tool_executed"] is False


def test_reject_wrong_tool():
    with pytest.raises(probe.ProbeError, match="tool name"):
        probe.summarize(
            "function",
            {"message": {"toolCalls": [{"function": {"name": "delete_data", "arguments": {}}}]}},
        )


def test_http_call_and_report():
    plans = probe.make_plan("chat", "HCX-DASH-002", "질문", DOCUMENTS)

    def handler(request):
        assert request.url.host == "clovastudio.stream.ntruss.com"
        assert request.headers["Authorization"] == "Bearer dummy-for-offline-test"
        return response({"message": {"content": "기준을 선택해 주세요."}, "finishReason": "stop"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_live("chat", plans, "dummy-for-offline-test", client)
    assert report["status"] == "worked"
    assert report["meaning_verified"] is False
    assert report["calls_attempted"] == 1


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_errors_no_retry_no_body_leak(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="do-not-print-response-secret")

    plans = probe.make_plan("chat", "HCX-005", "질문", DOCUMENTS)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_live("chat", plans, "dummy", client)
    assert report["status"] == "failed"
    assert len(calls) == 1
    assert "do-not-print" not in json.dumps(report)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"status": {"code": "40000", "message": "secret"}},
        {"status": {"code": "20000"}, "result": []},
    ],
)
def test_bad_service_response(body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as c:
        with pytest.raises(probe.ProbeError):
            probe.request_once(c, {"path": "/v1/test", "body": {}}, "dummy")


def test_invalid_json_response():
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html>"))
    ) as c:
        with pytest.raises(probe.ProbeError, match="JSON"):
            probe.request_once(c, {"path": "/v1/test", "body": {}}, "dummy")


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("secret-detail", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(probe.ProbeError, match="timed out") as exc:
            probe.request_once(client, {"path": "/v1/test", "body": {}}, "dummy")
    assert "secret-detail" not in str(exc.value)


def test_embedding_three_calls_and_rank():
    vectors = [[1.0, 0.0] + [0.0] * 1022, [0.0, 1.0] + [0.0] * 1022, [1.0, 0.0] + [0.0] * 1022]
    iterator = iter(vectors)
    plans = probe.make_plan("embedding", "HCX-DASH-002", "새 대출", DOCUMENTS)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: response({"embedding": next(iterator), "inputTokens": 10})
        )
    ) as client:
        report = probe.run_live("embedding", plans, "dummy", client)
    assert report["calls_attempted"] == 3
    assert report["ranking"][0] == {"document_id": "new", "similarity": 1.0}
    assert "embedding" not in report["calls"][0]


def test_embedding_partial_failure_stops():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 2:
            return httpx.Response(429)
        return response({"embedding": [1.0] * 1024})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = probe.run_live(
            "embedding",
            probe.make_plan("embedding", "HCX-DASH-002", "질문", DOCUMENTS),
            "dummy",
            client,
        )
    assert count == 2
    assert len(report["calls"]) == 1
    assert report["ranking"] == []
    assert report["status"] == "failed"


@pytest.mark.parametrize("vector", [[], [0.0] * 1024, [float("nan")] * 1024, [True] * 1024])
def test_invalid_embedding(vector):
    with pytest.raises(probe.ProbeError):
        probe.get_vector({"embedding": vector})


def test_sample_loading(tmp_path):
    path = tmp_path / "sample.json"
    path.write_text(json.dumps({"documents": DOCUMENTS}), encoding="utf-8")
    assert probe.load_documents(path) == DOCUMENTS
    path.write_text(json.dumps({"documents": DOCUMENTS * 2}), encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="unique"):
        probe.load_documents(path)


def test_reranker_preserves_api_shape_not_scores():
    plan = probe.make_plan("reranker", "HCX-005", "질문", DOCUMENTS)[0]
    assert set(plan["body"]) == {"query", "documents"}
    summary = probe.summarize("reranker", {"result": "답변", "citedDocuments": ["new"]})
    assert summary["answer"] == "답변"
    assert "scores" not in summary


def test_dry_run_never_reads_credentials(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry run must not load credentials or create a client.")

    monkeypatch.setattr(probe, "load_dotenv", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    assert probe.main(["chat"]) == 0
    assert json.loads(capsys.readouterr().out)["network_calls"] == 0


def test_missing_key_and_check(monkeypatch, capsys):
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    assert probe.main(["check"]) == 0
    assert json.loads(capsys.readouterr().out)["ncp_key_present"] is False
    assert probe.main(["chat", "--live"]) == 1
    assert "Set CLOVASTUDIO_API_KEY" in capsys.readouterr().err


def test_redacts_exact_secret_with_escaping():
    secret = 'dummy-"\\-secret'
    assert "[REDACTED]" in probe.redact({"answer": secret}, secret)
    assert secret not in json.loads(probe.redact({"answer": secret}, secret))["answer"]


def test_truncated_response_rejected():
    with pytest.raises(probe.ProbeError, match="token limit"):
        probe.summarize("chat", {"message": {"content": "partial"}, "finishReason": "length"})


@pytest.mark.parametrize("reason", ["length", "LENGTH"])
def test_v1_truncation_rejected(reason):
    with pytest.raises(probe.ProbeError, match="token limit"):
        probe.summarize("chat", {"message": {"content": "partial"}, "stopReason": reason})


def test_v1_usage_and_reason():
    result = probe.summarize(
        "chat",
        {
            "message": {"content": "기준을 확인해주세요."},
            "stopReason": "STOP_BEFORE",
            "inputLength": 12,
            "outputLength": 8,
        },
    )
    assert result["finish_reason"] == "stop_before"
    assert result["raw_finish_reason"] == "STOP_BEFORE"
    assert result["usage"] == {"promptTokens": 12, "completionTokens": 8, "totalTokens": 20}


@pytest.mark.parametrize("model", ["v2", "clir-emb-dolphin", "clir-sts-dolphin"])
def test_embedding_model_routes(model):
    plans = probe.make_plan("embedding", "HCX-DASH-002", "질문", DOCUMENTS, model)
    assert len(plans) == 3
    assert all(p["path"] == f"/v1/api-tools/embedding/{model}" for p in plans)


def test_rag_first_proposal_only():
    plan = probe.make_plan("rag", "HCX-007", "질문", DOCUMENTS)[0]
    assert plan["path"] == "/v1/api-tools/rag-reasoning"
    assert plan["body"]["maxTokens"] == 1024
    assert "thinking" not in plan["body"]
    assert "maxCompletionTokens" not in plan["body"]
    summary = probe.summarize(
        "rag",
        {
            "message": {
                "toolCalls": [
                    {
                        "id": "call_test",
                        "function": {"name": "search_tables", "arguments": {"query": "금리"}},
                    }
                ]
            }
        },
    )
    assert summary["tool_executed"] is False
    assert summary["validation_scope"] == "first_tool_proposal_only"
    assert summary["tool_call_id"] == "call_test"


def test_rag_missing_id_rejected():
    with pytest.raises(probe.ProbeError, match="id is missing"):
        probe.summarize(
            "rag",
            {
                "message": {
                    "toolCalls": [
                        {
                            "function": {"name": "search_tables", "arguments": {"query": "금리"}},
                        }
                    ]
                }
            },
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ["embedding", "--embedding-model", "clir-emb-dolphin"],
        ["rag"],
    ],
)
def test_new_dry_runs_are_offline(arguments, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry run must not load credentials or create a client.")

    monkeypatch.setattr(probe, "load_dotenv", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    assert probe.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["network_calls"] == 0


def test_saved_live_summary_is_sanitized(tmp_path, monkeypatch, capsys):
    sample = probe.ROOT / "experiments/ncp/data/metadata-demo.json"
    secret = "DUMMY-CREDENTIAL-FOR-MOCK-ONLY"
    client_class = httpx.Client
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", secret)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: client_class(
            transport=httpx.MockTransport(
                lambda _: response({"message": {"content": secret}, "finishReason": "stop"})
            )
        ),
    )
    assert probe.main(["chat", "--documents", str(sample), "--live", "--save"]) == 0
    assert secret not in capsys.readouterr().out
    files = list((tmp_path / "artifacts/ncp-probes").glob("*.json"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert secret not in content
    assert json.loads(content)["calls"][0]["answer"] == "[REDACTED]"
