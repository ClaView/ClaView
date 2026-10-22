# B 결과 (b_result)

코드: `src/claview/b/stages.py`(`BSteps.validate`가 표마다 고른 계열을 모아 만든다), `src/claview/b/contracts.py`(`outcome`). 입력은 원질문과 `{"tables": [{org_id, table_id, table_name}]}`([A→B 표 목록](A_to_B_표목록.md)).

## 봉투

`{"stage": "B", "status", "result", "issues", "trace_ref"}`. `status`는 `ok`, 또는 실패 상태(`error`, `unresolved` 등). 계열 수가 상한을 넘거나 상대 기간을 확인해야 하면 `needs_confirmation`이고 이때도 `result`가 붙는다.

## result

```text
{
  "selections": [
    {
      "series_id": "S001", ...                  # 표 순서대로 모은 뒤 S001부터 매긴다
      "label": "<항목명> / <분류값 이름> ...",
      "org_id", "table_id", "item_id",
      "dimensions": {"objL1": {"code", "name"}, ...},
      "period": {"frequency": "Y"|"H"|"Q"|"M", "start", "end"},
      "unit": <단위 | None>
    }, ...
  ],
  "relations": [...],                            # 같은 표 안 상·하위 계열 관계
  "period_notices": [...],                       # 있을 때만: 조회 가능 기간 안내(진행은 막지 않음)
  "frequency_alternatives": [{"table", "frequency", "period", "selections"}],  # 있을 때만
  "expand_notices": [...]                        # 있을 때만: 하위 펼침 변경 안내
}
```

- 기간 주기 글자는 B 내부와 KOSIS 요청이 같은 `Y/H/Q/M`이다(응답의 `PRD_SE`는 `A/S/Q/M`, `fetch.py`가 대조).
- `fetch.validate_b_result`가 조회 전에 다시 검사한다: 필드 집합, series_id 중복, 메타데이터와 이름·단위 일치, 같은 계열 중복.
