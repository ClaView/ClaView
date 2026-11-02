# 계약 샘플(fixtures)

모듈 API 요청·응답의 샘플이다. 앞 단계 모듈 없이 내 모듈을 개발·시험·평가할 때 입력으로 쓴다.
`pipeline/scripts/build_fixtures.py`가 골든 22문항(`eval/cases/golden/golden_set_v1.json`)에서 만든다(외부 호출 없음).

| 폴더 | 내용 | 쓰는 곳 |
|---|---|---|
| `a_request/` | 기존 `POST /a/legacy/select` 입력 `{query, as_of}`. 새 `/a/select`에는 HTTP 계약 판을 추가해야 함 | 기존 A |
| `a_result/` | A 결과 요약(decision, tables, pending_question). 규칙 경로, 모델 없음. 전체 state는 크기 때문에 뺐다 | A 회귀 확인 |
| `b_request/` | `POST /b/resolve` 입력. **tables는 골든 정답 표**라서 A 결과와 무관하다 | B |
| `b_result/` | B 봉투. **합성 B 모델**(첫 항목·첫 분류값) 결과 | 모양 확인 |
| `data_result/` | 조회 봉투. **합성 조회**(값 "1") 결과 | C 모양 확인 |
| `data_result_saved/` | C가 저장해 둔 **실제 KOSIS 조회 결과** 4건(payload, 봉투 아님) | C 화면·계산 개발 |
| `view/` | C 봉투(차트·카드) | 프론트·회귀 확인 |
| `a_view/` | 새 A 화면 계약의 focus·출처 예시. 직접 만든 합성 자료 | A→화면·B 전달 계약 확인 |
| `a_http_request/` | 필수 HTTP 판을 포함한 새 `/a/select` 요청. 기존 합성 AView에서 생성 | 새 A HTTP 모양 확인 |
| `a_http_result/` | `view`·`execution_mode`·`meta`를 담은 새 A HTTP 봉투. 합성 AView를 포장한 예시 | 새 A HTTP 계약 확인 |

- 합성 결과는 배선·모양 확인용이다. 정확도 근거로 쓰지 않는다.
- 계약(`common/claview_common/contracts`)이 바뀌면 이 샘플을 다시 만들고 `common/tests`가 통과하는지 확인한다.
- `a_view/`는 `pipeline/scripts/build_a_view_fixtures.py`로 별도 생성한다. 옛 A 골든 22 생성기와 섞지 않는다.
- 새 A HTTP의 요청·응답 예시는 [A HTTP 전환 계약](../../docs/contracts/A_HTTP_전환_20261102.md)에 있다. 옛 `a_result/`의 decision/state와 새 응답의 view/session을 같은 형식으로 읽지 않는다.
- `pipeline/scripts/build_fixtures.py --a-http-only`는 기존 `a_view/`만 읽어 새 HTTP 두 폴더를 만든다. legacy A·B·C 자료를 다시 생성하지 않으며, 이 샘플을 실제 HTTP 실행이나 모델 품질 결과로 세지 않는다.
