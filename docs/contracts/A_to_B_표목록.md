# A→B 표 목록과 전달 경계

제품은 `claview_a_api.view`의 AView를 `claview_pipeline.screen.ScreenPipeline`에서 읽어 `claview_b_api.service.BService.resolve`로 전달한다. 공용 모델은 `common/claview_common/contracts/b.py`의 `BRequest`와 `BTableRef`다.

```text
query, as_of, tables: [{org_id, table_id, table_name, focus?, period_condition?}]
```

A의 `handoff.ready`만 실행하고 blocked·미확정 통계는 B에 보내지 않는다. 통계별 묶음의 원질문·표·focus를 보존하며 표별 기간 문맥을 분리한다. 표 코드·메타·정답은 질문을 대체하지 않는다.

B가 구조화된 기간 계약을 지원하면 `handoff.ready[].b_request`의 표와 기간 조건을 보낸다. 사용자가 답한 기간·주기는 출처·대상·원문을 포함해 전달하며, 수신 계약이 지원하지 않는 경우 답을 빼고 조회하지 않는다. B 결과의 `period_resolutions`로 적용 여부를 확인한다.

- [focus와 출처](A_focus_출처.md)
- [기간 공용 모델과 A 송신부](A_B_기간조건_공용모델과_A송신부.md)
- [B 결과](B_결과.md), [조회 결과](조회_결과.md)

회귀 시험: `pipeline/tests/staged/test_screen_54.py`, `test_screen_period_condition_1103.py`, `test_screen_table_period_calls_1103.py`, `test_screen_yoy_answered_1104.py`, `common/tests/test_b_period_contract.py`.
