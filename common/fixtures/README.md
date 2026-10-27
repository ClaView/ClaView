# 계약 샘플(fixtures)

모듈 API 요청·응답의 샘플이다. 앞 단계 모듈 없이 내 모듈을 개발·시험·평가할 때 입력으로 쓴다.
`pipeline/scripts/build_fixtures.py`가 골든 22문항(`eval/cases/golden/golden_set_v1.json`)에서 만든다(외부 호출 없음).

| 폴더 | 내용 | 쓰는 곳 |
|---|---|---|
| `a_request/` | `POST /a/select` 입력 `{query, as_of}` | A |
| `a_result/` | A 결과 요약(decision, tables, pending_question). 규칙 경로, 모델 없음. 전체 state는 크기 때문에 뺐다 | A 회귀 확인 |
| `b_request/` | `POST /b/resolve` 입력. **tables는 골든 정답 표**라서 A 결과와 무관하다 | B |
| `b_result/` | B 봉투. **합성 B 모델**(첫 항목·첫 분류값) 결과 | 모양 확인 |
| `data_result/` | 조회 봉투. **합성 조회**(값 "1") 결과 | C 모양 확인 |
| `data_result_saved/` | C가 저장해 둔 **실제 KOSIS 조회 결과** 4건(payload, 봉투 아님) | C 화면·계산 개발 |
| `view/` | C 봉투(차트·카드) | 프론트·회귀 확인 |

- 합성 결과는 배선·모양 확인용이다. 정확도 근거로 쓰지 않는다.
- 계약(`common/claview_common/contracts`)이 바뀌면 이 샘플을 다시 만들고 `common/tests`가 통과하는지 확인한다.
