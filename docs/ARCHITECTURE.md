# ClaView 구조

현재 코드 기준의 서비스 구성, 폴더, 의존 규칙, 경계를 적는다. 이전 zip 통합본의 경위는 [plans](plans/)와 [records](records/)에 있다.

## 서비스

| 서비스 | 앱 | 포트(호스트) | 역할 |
|---|---|---|---|
| `frontend` | nginx + `frontend/dist` | 10300 | 화면. `/api/*`를 pipeline으로 프록시 |
| `pipeline` | `claview_pipeline.api:app` | 10301 | 프론트가 부르는 유일한 API. 질문 → A → (확인 질문 대기) → B → 조회 → C를 **같은 프로세스에서** 각 모듈 service로 실행, 단계 이벤트 |
| `module-a` | `claview_a_api.api:app` | 10311 | 모듈 시험·평가용. `POST /a/select`, `POST /a/answer` |
| `module-b` | `claview_b_api.api:app` | 10312 | 모듈 시험·평가용. `POST /b/resolve`, `/b/fetch`, `/b/run` |
| `module-c` | `claview_c_api.api:app` | 10313 | 모듈 시험·평가용. `POST /c/present` |

pipeline은 module-a/b/c 컨테이너를 부르지 않는다(compose에도 의존 관계가 없다). 모듈 API와 pipeline은 같은 service 함수를 쓴다.

모든 백엔드는 같은 이미지(루트 `Dockerfile`, `requirements.txt`)에서 실행 명령만 다르다. 모두 `/health`, `/info`(버전·git SHA·계약 판본·모드·키 설정 여부, 값은 없음)를 가진다. 응답마다 `X-Process-Time-Ms` 헤더가, 모듈 응답에는 `meta{latency_ms, llm_calls, external_calls, mode}`가 붙는다.

## 흐름 (현재)

```text
질문 → pipeline.api (/api/query) → staged.Pipeline (단계 기록: question/table/conditions/fetch/chart)
         ├ claview_a_api.service.select / answer      → decision, 요구 묶음별 표(b_requests), A 상태(checkpoint)
         ├ 묶음마다 claview_b_api.service.BService.resolve → fetch   (M2 합산 검증용 전체 조회도 BService.fetch)
         └ claview_c_api.service.present               → 차트·표 → 화면 카드(ui_cases)
```

| 모듈 함수 | pipeline이 부름 | 같은 함수를 여는 API |
|---|---|---|
| `claview_a_api.service.select(query, as_of)` / `answer(state, option_id)` | 질문 시작 / 확인 질문 재개 | `POST /a/select` / `/a/answer` |
| `claview_b_api.service.BService.resolve(query, tables, as_of)` | 요구 묶음마다 | `POST /b/resolve` |
| `claview_b_api.service.BService.fetch(b_result)` | 조회 | `POST /b/fetch` |
| `claview_c_api.service.present(query, data_result)` | 화면 자료 | `POST /c/present` |

- A는 자기 세션 안에서 어떤 요구를 B로 넘길지 정한다. A service는 기존 A의 그 단계(`claview_a_legacy.agent.b_connection.connect`)를 기록용 포트로 재생해 묶음별 표만 꺼낸다(A 코드 무변경). B 실행은 pipeline이 한다.
- `claview` 명령과 골든 통합 평가(`claview_pipeline.session`·`b_port`·`evaluate`)는 아직 A 안의 B 포트를 쓰는 예전 경로다. 골든 22문항에서 두 경로의 상태·단계·카드·단계 문구가 모두 같다(2026-10-26 확인).

- A는 두 벌이다: 새 A `claview_a`(표 단위 경로, 공개 `run`·CLI)와 기존 A `claview_a_legacy`(10/21판). **새 A 패키지 제공 완료, 화면 연결 전**이라 위 흐름과 service는 기존 A를 쓴다. [A 기준판 문서](modules/a/A_기준판_표단위경로_20261028.md)

## 의존 규칙 (`lint-imports`, pyproject.toml)

```text
claview_pipeline(staged, api) ──→ claview_a_api, claview_b_api, claview_c_api, claview_common   (모듈 본체 직접 import 금지)
claview_pipeline(session, b_port, evaluate, cli) ──→ claview_a_legacy, claview_b   (CLI·골든 평가용 예전 경로)
claview_a_api ──→ claview_a_legacy, claview_common   (화면 연결 전)
claview_b_api ──→ claview_b, claview_common
claview_c_api ──→ claview_c, claview_common
claview_a, claview_a_legacy ──→ (표준 라이브러리, langgraph만. A 패키지 규칙)
claview_b ──→ claview_common.periods   (기간 계산)
claview_c ──→ claview_common.{periods, settings}
claview_common ──→ (모듈·파이프라인을 부르지 않는다)
```

- A·B·C 본체 패키지는 서로 import하지 않는다(independence).
- 모듈 HTTP 계층은 자기 모듈만 쓴다. 파이프라인을 부르지 않는다.
- A의 import 가드(`claview_a._guard`, 기존 A는 `claview_a_legacy._guard`)는 A 시험 프로세스에서 자기 패키지·시험 폴더 밖의 레포 Python 소스와 다른 패키지(`claview_b` 등)를 막는다. 정적 검사는 import-linter가 한다.

## 계약

`claview_common.contracts`(Pydantic)가 모듈 API의 요청·응답이다. 안쪽 내용(A 응답, b_result, data_result, chart)은 이번 판에서 dict로 두고 필드는 [contracts](contracts/README.md) 문서에 적었다. 샘플은 `common/fixtures/`(골든 22, `pipeline/scripts/build_fixtures.py`).

## 무엇이 실제이고 무엇이 합성인가

| 부분 | 기본(`CLAVIEW_MODE=synthetic`) |
|---|---|
| A 해석·선택·대기·재개 | 실제 A 코드, 규칙 경로(모델 끔) |
| 카탈로그 | 실제 저장 자료(`data/`, 2026-10-06 기준) |
| B 그래프·조회 요청·응답 정규화 | 실제 B 코드 |
| B 선택 모델 | 합성(`claview_b.synthetic`) |
| KOSIS 조회 | 합성(값 "1") |
| C 렌더러·분석·차트 정책 | 실제 C 코드 |
