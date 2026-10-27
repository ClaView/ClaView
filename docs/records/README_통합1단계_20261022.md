# ClaView 통합 흐름 (A 표 선택 + B 계열·기간 선택, 1단계)

자연어 질문을 받아 KOSIS 한국은행 소관 통계표 349개 중 필요한 표를 고르고(A), 표의 항목·분류·기간·주기를 정해 KOSIS Open API로 값을 가져오는(B) 에이전트 PoC다. 이 브랜치는 A와 B를 한 저장소·한 가상환경·한 진입점에서 하나의 흐름으로 돌리는 1단계 통합본이다. 표·그래프 표현(C)은 아직 없다.

```text
질문
 └─ A 완료2 세션 (claview_v3.agent.session.run_session)
      intake → router → 해석 → 검증(V1) → 선택 → 검증(V2) → question_route
      ├─ ask        → 대기(awaiting_answer). B를 부르지 않는다. answer()로 재개하면 선택·V2만 다시 한다.
      ├─ no_result  → B를 부르지 않는다.
      └─ proceed / assume / partial
           └─ AdapterPort (claview_flow.make_b_port, 실제 B 함수)
                to_b_request → check_request → run_b_request(B 모델) → check_b_result·check_scope_result
                └─ fetch_data(조회 클라이언트) → 응답(response.b, execution_status)
```

노드별 설명과 판단이 어디에 있는지는 [통합 흐름](docs/통합흐름.md)에 있다.

## 1. 무엇을 합쳤나

| 경로 | 내용 | 출처 |
|---|---|---|
| `src/claview_v3/`, `tests_v3/`, `scripts/`의 A 스크립트 39개 | A 완료2(바깥 그래프, router, question_route, 대기·재개, 원장, 공개 가정, B 포트) | A split 완료2 고정판. 고정 목록 253개 파일(2026-10-21 19:16 KST 기록) 해시 대조 후 그대로 복사. 다른 팀 외부 자료를 언급·사용하는 연구용 스크립트 3개(`scripts/decision_gate_study.py`, `scripts/eda_v3/e6_external_synonyms.py`, 이를 import하는 `scripts/eda_v3/e4b_questions_with_synonyms.py`)는 넣지 않았다. 흐름·시험·A 실행 코드는 이 셋을 쓰지 않는다 |
| `reference/baseline-1006/data/`, `reference/baseline-manifest.json` | A가 읽는 349표 카탈로그·메타 | A 고정판과 같은 위치(A `paths.py` 수정 없음) |
| `docs/inputs/axis-roles-20261016/` | A 로더 입력(축 역할 초안) | A 고정판 |
| `eval/*.py`, `eval/cases/`의 A 파일 | A 시험이 읽는 축소 세트·사례 역할·옛 평가 모듈 | A 고정판(B 파일과 경로 충돌 없음) |
| `src/claview/` | B 패키지(B 노드, 계약 `b-contract/2026-10-21.3`, A 가정 `a-assumptions/2026-10-21.1` 수신)와 B에 딸린 옛 A 모듈 | B b-1020 사본, 동작 변경 없음 |
| `packages/claview-period/` | 공용 기간 패키지 0.2.0 | B b-1020 사본 |
| `data/`, `eval/`(B 파일), `scripts/ab`·`b_part`·`b_stage3`와 B 스크립트, `tests/` | B 데이터·평가 자료·도구·시험 | B b-1020 사본. `eval/results/`(실행 산출물)는 넣지 않았다 |
| `docs/B/` | B 문서 | B b-1020의 `docs/`. B 코드·시험이 `docs/` 경로를 읽지 않아 하위 폴더로 옮겼다 |
| **`src/claview_flow/`** | 유일한 새 연결 코드. A 세션 호출과 실제 B 함수로 만든 `AdapterPort` | 신규 |
| `scripts/run_flow.py` | 질문을 끝까지 돌리는 오프라인 명령 | 신규 |
| `tests_flow/` | 통합 시험과 **합성** B 모델·조회 클라이언트(`synthetic_b.py`) | 신규 |

- 판본: A 고정 목록 253개 파일(위 스크립트 3개 제외 250개 포함), A 공개 가정 `a-assumptions/2026-10-21.1`, 구조 판본 `structure-2026-10-21.1`(A·B 모두 이 판본만 지원), B 계약 `b-contract/2026-10-21.3`, `claview-period` 0.2.0.
- 복사한 파일 549개의 출처와 sha256은 [통합_출처_해시.json](docs/통합_출처_해시.json)에 있다. `pyproject.toml`만 B 파일에서 설명·pytest 주석·ruff 제외 경로를 바꿨다(의존성은 그대로).
- 복사본은 줄바꿈(CRLF/LF 혼재)까지 원본 바이트 그대로다. `.gitattributes`에서 복사한 경로를 `-text`로 두어 Git이 줄바꿈을 바꾸지 않게 했다.
- `data/`와 `reference/baseline-1006/data/`의 카탈로그 원자료 두 파일(`search_catalog.json`, `boK_meta_raw.jsonl`)은 같은 바이트다. A는 뒤의 경로를, B와 `claview_flow`는 `data/` 경로를 읽는다. Git은 같은 내용을 한 번만 저장한다.
- 의존성 판: B의 `uv.lock` 그대로라 A 고정판이 시험된 환경과 일부 판이 다르다(langgraph 1.2.12, langchain-core 1.6.6, pytest 9.1.1. A 고정판 환경은 1.2.14, 1.6.11, 8.4.2).

### main 브랜치에서 바뀐 것

- main의 `src/claview.py`(기본 모듈)는 B의 `src/claview/` 패키지와 이름이 겹쳐 지웠다. main의 `tests/test_environment.py`는 B에도 같은 내용으로 있어 B 시험으로 남았다.
- main의 `pyproject.toml`·`uv.lock`·`tests/conftest.py`·`data/README.md`는 B 판으로 바뀌었다. main의 나머지 `data/`·`eval/`·`scripts/` 파일 중 B와 경로가 같은 것은 내용도 같았다.
- main의 `docs/`(설계·안내·조사), `.env.example`, `.python-version`은 그대로 두었다. `AGENTS.md`에는 통합 브랜치 원칙 3줄을 더했다. `.gitattributes`에는 위의 `-text` 줄만 더했다. `.gitignore`에는 `/runs/`, `/eval/results/`와 B·통합이 읽는 `data/raw/boK_meta_raw.jsonl` 예외를 더했다.
- main README의 목표·범위는 아래 7절에 옮겼다.

## 2. 설치와 시험

Python 3.12와 uv를 쓴다. 저장소 루트에서 실행한다. API 키가 필요 없다.

```powershell
uv sync --offline --locked        # 캐시에 없으면 --offline을 빼고 uv sync --locked
$env:PYTHONHASHSEED = "0"
uv run --locked python -B -X utf8 -m pytest tests_v3 -q -p no:cacheprovider                       # A
uv run --locked python -B -X utf8 -m pytest tests -q -p no:cacheprovider                          # B
uv run --locked python -B -X utf8 -m pytest packages/claview-period/tests -q -p no:cacheprovider  # 공용 기간 패키지
uv run --locked python -B -X utf8 -m pytest tests_flow -q -p no:cacheprovider                     # 통합
uv run --locked ruff check src/claview_flow tests_flow scripts/run_flow.py
uv run --locked ruff format --check src/claview_flow tests_flow scripts/run_flow.py
```

- 시험은 **따로 된 pytest 프로세스로** 돌린다. A의 `tests_v3/conftest.py`가 옛 패키지 `claview`(B 패키지와 이름이 같다)를 같은 프로세스에서 불러오지 못하게 막는 import 가드를 깔기 때문이다. `pytest tests tests_v3`처럼 한 번에 돌리지 않는다.
- 모든 시험은 네트워크를 막고 돈다. A·통합 시험은 A의 `_guard.install_network_block()`, B 시험은 `tests/conftest.py`의 `block_network`를 쓴다.
- 이 브랜치에서의 결과는 아래 "시험 결과"에 적었다.
- 저장소 밖 자료를 읽는 A 시험이 있다. 417문항 묶음(`claview_v3/paths.py`의 `CORPUS_DIR`, 저장소 상위 폴더의 `clabi/...`)과 옛 작업 폴더의 `claview/src/claview/agent`(`tests_v3/agent/test_agent_common.py`)다. 없으면 기존 skip 조건대로 skip되거나 실패할 수 있다. 417문항은 저장소에 넣지 않는다.

### 시험 결과 (2026-10-22, 네트워크 차단)

| 묶음 | 결과 | 기준 |
|---|---|---|
| A `tests_v3` | 3,949 통과, 2 skip | A 고정판 보고 3,947 통과, 4 skip |
| B `tests` | 1,344 통과, 3 skip | B b-1020 보고 1,344 통과, 3 skip |
| `packages/claview-period/tests` | 59 통과 | 59 |
| 통합 `tests_flow` | 13 통과 | 신규 |
| `scripts/run_flow.py --synthetic-b --synthetic-answer`, 고정 10문항(규칙 해석) | 10문항 모두 오류 없이 B까지 실행(`execution_status=completed`) | 연결 확인용. 표·값 정확도 아님 |

A의 차이는 환경에 따른 skip이다. 고정판에서 skip이던 loopback 공유·junction 시험 3개가 이 PC에서 실행돼 통과했고, Git 밖 EDA 결과(`runs/eda_v3`)가 없어 `test_catalog_real.py`의 숫자 대조 1개가 skip됐다. 남은 skip 하나는 고정판과 같은 파일 심볼릭 링크 권한 skip이다.

## 3. 질문 돌리기

```powershell
uv run --locked python -B -X utf8 scripts/run_flow.py --question "GDP 2023년 보여줘"
uv run --locked python -B -X utf8 scripts/run_flow.py --synthetic-b --question "GDP 2023년 보여줘"
uv run --locked python -B -X utf8 scripts/run_flow.py --synthetic-b --synthetic-answer --question "총고정자본형성 2023년" --out runs/flow-demo
```

- 프로세스 전체에서 네트워크를 막는다. 출력: `status`(A 경로), `execution_status`, `b_status`/`b_reason`, 요구별 표, 건너뛴 이유. `--question`은 여러 번 줄 수 있다.
- `--synthetic-b`가 없으면 B 포트를 만들지 않는다. A는 `b_port_not_configured`로 끝나고 B 선택을 지어내지 않는다.
- `--synthetic-b`는 `tests_flow/synthetic_b.py`의 **합성** B 모델(메타의 첫 항목·각 분류의 첫 값·첫 주기)과 합성 조회 클라이언트(값 "1")를 쓴다. 연결·계약 확인일 뿐 모델·값의 정확도가 아니다.
- `--synthetic-answer`는 대기 질문에 첫 선택지로 답하는 합성 사용자다.
- `--out`은 저장소 밖이나 `runs/` 아래만 받는다.

코드에서 쓸 때:

```python
from claview_flow import FlowSession, make_answer

session = FlowSession.load(b_model=..., fetch_client=...)  # b_model이 없으면 B 미실행
state = session.run_question("질문", as_of="2026-10-22")
if state["response"]["status"] == "awaiting_answer":
    state = session.answer(state, make_answer(state, option_id))
```

## 4. 무엇이 실제이고 무엇이 합성인가

| 부분 | 기본 실행·시험 |
|---|---|
| A 해석·선택·검증·질문 경로·대기·재개 | 실제 A 코드, 규칙 경로(모델 끔). 모델 경로는 시험에서 가짜 `proposal_source`로만 |
| 카탈로그(349표 메타) | 실제 저장 자료(2026-10-06 기준 사본) |
| B 어댑터·계약 검사·실행 그래프·결과 검사·조회 요청 생성·응답 정규화 | 실제 B 코드 |
| B 선택 모델(HCX) | **합성**(`--synthetic-b`, 시험) 또는 없음 |
| KOSIS 조회 | **합성**(값 "1") 또는 미실행(`fetch.status = "not_run"`, `reason = "no_fetch_client"`. 이때 `execution_status`는 `completed`가 되지 않는다) |

실제 HCX·KOSIS 클라이언트는 `claview_flow.live_b_clients(allow_external=True)`로만 만든다. 읽는 환경변수 이름은 `CLOVASTUDIO_API_KEY`, `KOSIS_API_KEY`다. 이 단계에서는 `run_flow.py`에 연결하지 않았고 실행하지 않았다. 실호출은 호출 수와 비용을 밝히고 승인받은 뒤에만 한다.

## 5. 알려진 한계

- 실제 KOSIS·HCX로는 이 통합본을 확인하지 않았다. 합성 응답으로 확인한 것은 연결과 계약뿐이다.
- 공유된 최신 30건 결과(`.4` 입력 범위 사본, 구조 `structure-2026-10-22.1`)는 다른 A 판본의 결과다. 이 브랜치의 A는 split 완료2 고정판이고 `.4`의 입력·선택 보완이 없다.
- 2단계(별도 승인): A를 `.4` 계열(구조 `structure-2026-10-22.1`)로 올리고, B가 새 구조 판본을 지원하는 변경을 함께 맞춘다. B 쪽 변경은 B 담당과 합의한 뒤에 한다.
- `claview_flow`의 포트는 한 번에 한 요청만 처리한다(요청마다 B 서비스 객체를 새로 만들고 마지막 것을 보관한다). 동시 처리는 고려하지 않았다.
- `src/claview/`의 `a1_*`·`a2_*`·`a_*`·`aflow/`는 B에 딸린 옛 A 모듈이다. 새 흐름은 쓰지 않으며, 정리는 B 담당과 정한다.

## 6. 폴더

| 경로 | 역할 |
|---|---|
| `src/claview_v3/` | A(완료2) |
| `src/claview/` | B와 옛 A 모듈 |
| `src/claview_flow/` | A→B 연결(새 코드) |
| `packages/claview-period/` | 공용 기간 패키지 |
| `tests_v3/`, `tests/`, `tests_flow/` | A, B, 통합 시험 |
| `data/`, `reference/baseline-1006/data/` | 349표 카탈로그·메타(같은 바이트, 두 경로)와 B 매핑 |
| `eval/` | A·B 평가 자료(정답·평가셋은 바꾸지 않음) |
| `scripts/` | A·B 도구와 `run_flow.py` |
| `docs/` | main 문서, `docs/B/` B 문서, `docs/inputs/` A 입력, [통합 흐름](docs/통합흐름.md) |
| `runs/` | 실행 산출물. Git에 넣지 않는다 |

## 7. 프로젝트 목표 (main README에서 옮김)

- 대상: KOSIS Open API 내 한국은행 소관 통계표 전체. 금리 사례는 구현과 시험에 쓰는 예시다.
- 처리: 질문 해석 → 통계표·항목·분류 선택 → 자료 조회·필요한 가공 → 표·그래프·근거 제공.
- 결과물: 에이전트, 외부 클라이언트에서 쓸 MCP 서버, 검수한 평가 사례와 측정 결과.
- 원자료 수치는 API 응답에서 가져오고, 계산한 값에는 가공 방법을 남긴다.
- 주요 문서: [전체 문서 목록](docs/README.md), [개발계획](docs/design/프로젝트_개발계획.md), [처리 흐름](docs/design/처리흐름과_연결구조.md), [데이터 안내](data/README.md).
- 코드·구현 문서·재현 자료는 GitHub에서, 일정·담당·회의 기록은 팀 노션에서 관리한다. API 키와 개인 자료는 올리지 않는다.
