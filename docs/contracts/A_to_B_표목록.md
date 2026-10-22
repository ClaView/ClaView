# A→B 표 목록 계약

코드: `src/claview/pipeline/b_port.py`(요청·결과·검사), `src/claview/a/agent/b_connection.py`(A의 `AdapterPort`, `connect`). 판본 상수: `TABLE_LIST_CONTRACT = "flow-table-list/2026-10-23.1"`(포트 자체 모양이며 B 계약 판본이 아니다).

## 언제 부르나

A의 `connect`가 `question_route`가 `proceed`·`assume`·`partial`이고 실행할 요구가 있을 때만 포트를 부른다. 대기(`awaiting_answer`)·`no_result`·포트 없음(`b_port_not_configured`)이면 부르지 않는다. 관계로 묶인 요구는 한 번에 보낸다.

## 요청 (`to_table_request(payload, a_assumptions=...)`)

```text
{
  "contract_version": "flow-table-list/2026-10-23.1",
  "query": <A가 받은 원질문>,
  "as_of": <기준일 YYYY-MM-DD | None>,
  "a_result": {"tables": [{"org_id", "table_id", "table_name"}, ...]},   # B가 읽는 것은 이것과 query뿐
  "a_assumptions": <A 공개 가정 봉투, 그대로 되돌려 준다>,
  "scopes": [{"scope_id": <A 요구 ID>}, ...]                              # 보낸 요구마다 하나
}
```

- 표는 보낸 요구들의 선택 표를 A 순서대로, (org_id, table_id) 중복 없이 모은다. 필드는 `B_TABLE_FIELDS = ("org_id", "table_id", "table_name")`뿐이다(B 자체의 A→B 입력과 같다).
- `check_table_request`가 막는 것: 원질문 없음, 표 0개, 표 필드가 다르거나 org_id·table_id 빈 값, scope ID가 없거나 중복. 막히면 A는 그 요구를 `connection_error`로 남기고 B를 부르지 않는다.

## 결과 (`run_b`)

```text
{
  "contract_version": "flow-table-list/2026-10-23.1",
  "a_assumptions": <요청과 같은 값>,
  "a_result": <요청의 표 목록>,
  "b_outcome": <B 단계 봉투: stage "B", status, result(b_result), issues, trace_ref>,
  "scopes": [{"scope_id", "status": <b_outcome.status>, "issues": <b_outcome.issues>}, ...],
  "selections": <status가 ok일 때 b_result.selections, 아니면 []>,
  "fetch": <A의 AdapterPort가 붙인다: data_result | not_run 봉투 | None>
}
```

- B 한 번의 실행이 요청 전체에 답하므로 묶인 요구들은 같은 상태를 나눠 갖는다.
- B 그래프가 실패하면(모델 실패 포함) `b_outcome.status`가 실패 상태가 되고 `selections`는 비며 조회하지 않는다. 그래프 밖으로 `StageFailure`가 나와도 같은 봉투로 바꾼다.
- 조회: 조회 클라이언트가 있으면 `fetch_data(b_outcome.result, ...)`의 결과([조회 결과](조회_결과.md)). 없으면 `{"status": "not_run", "reason": "no_fetch_client", "requests": [<KOSIS 요청 파라미터>, ...]}`(키 없음).
- `check_table_result`: scope ID가 요청과 같은 순서로 같은지, `b_outcome.stage == "B"`, `selections`가 목록인지. `check_scope_result`: scope마다 `scope_id`·`status`가 문자열인지.

## A 쪽 판정

A의 `connect`가 요구별로 `{"requirement_keys", "status": "returned" | "connection_error", "result"}`를 쌓고, 모든 요구가 `returned`·모든 scope가 `ok`·조회가 `ok`일 때만 `execution_status = "completed"`다. 그 밖은 `partial` 또는 `not_executed`.
