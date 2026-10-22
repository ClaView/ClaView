# ClaView 구조

자연어 질문 → 표 선택(A) → 계열·기간 선택(B) → KOSIS 조회 → 표·그래프(C, 아직 없음). 이 문서는 현재 코드 기준의 흐름, 폴더, 의존 방향, 경계를 적는다. 바뀐 경위는 [계획 24~27](plans/)에 있다.

## 흐름

```text
질문
 └─ A 세션 (claview.a.agent.session: intake → router → 해석 → 검증 → 선택 → 검증 → question_route)
      ├─ ask        → 대기(awaiting_answer). B를 부르지 않는다. 답을 받으면 선택·검증만 다시 한다.
      ├─ no_result  → B를 부르지 않는다.
      └─ proceed / assume / partial
           └─ A→B 포트 (claview.pipeline.b_port, A의 AdapterPort 모양)
                원질문 + A가 고른 표 목록 {"tables":[{org_id, table_id, table_name}]}
                └─ B 그래프 (claview.b.graphs.build_b_graph: load → context → select → validate)
                     └─ b_result(선택 계열·기간) → 조회 (claview.b.fetch.fetch_data) → data_result
                          └─ (C: data_result를 표·그래프로. 아직 없음)
```

- A의 답(`response`)에는 `b`(요구별 B 결과·조회 결과)와 `execution_status`가 붙는다. `completed`는 B가 보낸 모든 요구가 `ok`이고 조회가 `ok`일 때만이다.
- 계약은 [contracts](contracts/README.md)에 코드 기준으로 적었다.

## 폴더

| 경로 | 내용 |
|---|---|
| `src/claview/a/` | A(표 선택). 패키지 `claview.a`. 자료 위치는 `paths.py` 한 곳 |
| `src/claview/b/` | B(계열·기간 선택, 조회, B 검색 도구). 패키지 `claview.b`. 10/16 compact 판 + `fetch.py` 단위 표기 수정(NFKC) |
| `src/claview/pipeline/` | A→B 연결, 골든 통합 평가, 명령(`claview`, `claview-pipeline`), 합성 B(시험·연결 확인용) |
| `src/claview/__init__.py` | 상위 패키지. 설명문만 둔다(A가 import할 때 먼저 불린다) |
| `packages/claview-period/` | 공용 기간 해석 패키지(`claview_period` 0.2.0). 현재 A만 쓴다 |
| `tests/a`, `tests/b`, `tests/pipeline` | A, B, 연결 시험. 묶음마다 별도 pytest 프로세스로 돌린다 |
| `data/` | 349표 카탈로그·원메타(`catalog/`, `raw/`, A·B 공용), B 검색 매핑(`mappings/`), 표본(`samples/`), A 입력(`a/axis-roles-20261016/`) |
| `eval/cases/` | `golden/`(골든 22, A·B·연결 공용), `a/`(A 사례), `b/`(B 기간·검색 사례), `legacy/`(옛 A 사례, 보관) |
| `scripts/` | `a/`(A 연구·평가 도구), `b/`(B 도구), `pipeline/run_flow.py` |
| `docs/` | 이 문서, `contracts/`, `plans/`(통합 계획 24~), `records/`(지난 기록), `b/`(B 문서), `design`·`guides`·`research`(팀 공용 문서) |
| `runs/` | 실행 산출물. Git에 넣지 않는다 |

## 의존 방향과 경계

```text
claview.pipeline ──→ claview.a      (A 세션, AdapterPort)
       │          └─→ claview.b      (build_b_graph, fetch_data, 기본 타입)
claview.a ──→ claview_period        (공용 기간 패키지)
claview.b ──→ (A를 부르지 않는다)
```

- A는 B·연결 코드를 부르지 않는다. A의 import 가드(`claview.a._guard`, `tests/a/conftest.py`가 설치)는 `claview.a` 밖의 `claview.*`(B·pipeline)와 `src/claview/` 중 `a/` 밖의 파일, `eval/`·`scripts/`의 Python 소스를 거절하고 네트워크를 막는다. 상위 `claview` 패키지의 설명문뿐인 `__init__.py`만 허용한다.
- B는 A를 부르지 않는다. A와 B를 끝까지 잇는 일은 모두 `claview.pipeline`에 둔다(계획 26).
- 시험은 묶음별 별도 프로세스: A 가드가 같은 프로세스의 B·pipeline import를 막기 때문이다.
- 실제 HCX·KOSIS 호출은 `claview.pipeline.live_b_clients(allow_external=True)`와 B 명령의 `--allow-external`로만 만든다. 시험과 기본 실행은 네트워크를 막는다.

## 무엇이 실제이고 무엇이 합성인가

| 부분 | 기본 실행·시험 |
|---|---|
| A 해석·선택·검증·대기·재개 | 실제 A 코드, 규칙 경로(모델 끔) |
| 카탈로그 | 실제 저장 자료(`data/`, 2026-10-06 기준) |
| B 그래프·조회 요청·응답 정규화 | 실제 B 코드 |
| B 선택 모델 | 합성(`claview.pipeline.synthetic`) 또는 골든 B 답 재생(`ScriptedModel`) |
| KOSIS 조회 | 합성 클라이언트(값 "1") 또는 미실행 |

합성·재생 결과는 연결과 계약을 확인할 뿐 모델·값의 정확도가 아니다. 골든 22문항은 개발 중 반복해 쓴 자료라 독립 평가셋이 아니다.
