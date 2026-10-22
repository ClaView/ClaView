# 계약

코드 기준으로 적은 단계 사이의 모양이다. 코드가 바뀌면 이 문서도 같이 고친다.

| 문서 | 사이 | 코드 |
|---|---|---|
| [A→B 표 목록](A_to_B_표목록.md) | A 세션 → 포트 → B | `src/claview/pipeline/b_port.py`, `src/claview/a/agent/b_connection.py` |
| [B 결과(b_result)](B_결과.md) | B 그래프 → 조회 | `src/claview/b/stages.py`(validate), `src/claview/b/contracts.py`(outcome) |
| [조회 결과(data_result)](조회_결과.md) | 조회 → (C) | `src/claview/b/fetch.py`(fetch_data, normalize_rows) |

모든 단계 결과는 같은 봉투(`claview.b.contracts.outcome`)를 쓴다.

```text
{"stage": "B" | "fetch" | ..., "status": "ok" | <실패 상태>, "result": {...} | None,
 "issues": [{"code", "message", "field", "series_id", "retryable"}], "trace_ref": <실행 ID> | None}
```

실패 상태는 `StageFailure.status`(기본 `error`, 그 밖에 `unresolved`·`unsupported`·`needs_confirmation` 등)이고, 사용자 응답을 기다리면 `awaiting_user`와 `interaction`이 붙는다.
