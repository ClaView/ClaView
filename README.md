# ClaView 통합 레포 (A 표 선택 → B 계열·기간 선택 → 조회)

자연어 질문을 받아 KOSIS 한국은행 소관 통계표 349개 중 필요한 표를 고르고(A), 표의 항목·분류·기간·주기를 정해 KOSIS Open API로 값을 가져오는(B) 에이전트 PoC다. A와 B를 한 저장소·한 가상환경에서 하나의 흐름(`claview.pipeline`)으로 돌린다. 표·그래프 표현(C)은 아직 없다.

```text
질문 → A 세션(claview.a) ── 대기(ask)·no_result면 B를 부르지 않음
            └─ proceed/assume/partial → A→B 포트(claview.pipeline): 원질문 + A가 고른 표 목록
                 → B 그래프(claview.b) → b_result → 조회(fetch_data) → data_result → (C)
```

- 구조·의존 방향·경계: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 단계 사이 계약(A→B 표 목록, b_result, data_result): [docs/contracts](docs/contracts/README.md)
- 문서 목록: [docs/README.md](docs/README.md)

## 1. 폴더

| 경로 | 역할 |
|---|---|
| `src/claview/a/` | A(표 선택, split 완료2 판). 패키지 `claview.a`, 자료 위치는 `paths.py`. 기간 해석 패키지는 `period_core/`(`claview.a.period_core` 0.2.0, A만 씀, 바이트 그대로) |
| `src/claview/b/` | B(계열·기간 선택, 조회, B 검색 도구). 패키지 `claview.b`. 10/16 compact 판 + `fetch.py` 단위 표기 수정(NFKC) |
| `src/claview/pipeline/` | A→B 연결, 골든 통합 평가, 명령, 합성 B(연결 확인용) |
| `tests/a/`, `tests/b/`, `tests/pipeline/` | A, B, 연결 시험(묶음별 별도 프로세스) |
| `data/` | 349표 카탈로그·원메타(`catalog/`, `raw/`, A·B 공용), B 검색 매핑, 표본, A 입력(`a/axis-roles-20261016/`) |
| `eval/cases/` | `golden/`(골든 22), `a/`(A 사례), `b/`(B 기간·검색 사례), `legacy/`(옛 A 사례, 보관). 정답·평가셋 내용은 바꾸지 않는다 |
| `scripts/` | `a/`(A 연구·평가 도구), `b/`(B 도구), `pipeline/run_flow.py` |
| `docs/` | `ARCHITECTURE.md`, `contracts/`, `plans/`(계획 24~27), `records/`(지난 기록), `b/`(B 문서), `design`·`guides`·`research`(팀 공용) |
| `runs/` | 실행 산출물. Git에 넣지 않는다 |

## 2. 설치와 시험

Python 3.12와 uv를 쓴다. 저장소 루트에서 실행한다. API 키가 필요 없다.

```powershell
uv sync --offline --locked        # 캐시에 없으면 --offline을 빼고 uv sync --locked
$env:PYTHONHASHSEED = "0"
uv run --locked python -B -X utf8 -m pytest tests/a -q -p no:cacheprovider                        # A(기간 패키지 시험 포함)
uv run --locked python -B -X utf8 -m pytest tests/b -q -p no:cacheprovider                        # B
uv run --locked python -B -X utf8 -m pytest tests/pipeline -q -p no:cacheprovider                 # 연결
uv run --locked ruff check src/claview/pipeline tests/pipeline scripts/pipeline/run_flow.py
uv run --locked ruff format --check src/claview/pipeline tests/pipeline scripts/pipeline/run_flow.py
```

- 묶음마다 **따로 된 pytest 프로세스로** 돌린다. `tests/a/conftest.py`가 A import 가드를 깔아 같은 프로세스에서 `claview.b`·`claview.pipeline`(과 `src/claview/` 중 `a/` 밖의 파일, `eval/`·`scripts/`의 Python 소스)을 불러오지 못하게 막기 때문이다. `pytest tests`처럼 한 번에 돌리지 않는다. 인자 없이 `pytest`를 돌리면 B(`tests/b`)만 돈다.
- 모든 시험은 네트워크를 막고 돈다(A·연결: `claview.a._guard`, B: `tests/b/conftest.py`의 `block_network`).
- 저장소 밖 자료를 읽는 A 시험이 있다(417문항 묶음: `src/claview/a/paths.py`의 `CORPUS_DIR`, 옛 작업 폴더의 `claview/src/claview/agent`). 없으면 기존 조건대로 skip된다. 417문항은 저장소에 넣지 않는다.

### 시험 결과 (2026-10-23, 네트워크 차단, PYTHONHASHSEED=0)

| 묶음 | 결과 | skip 사유 |
|---|---|---|
| A `tests/a` | 4,008 통과, 2 skip(기간 패키지 59 포함) | 파일 심볼릭 링크 권한, Git 밖 EDA 결과(`runs/eda_v3`) 없음 |
| B `tests/b` | 512 통과, 2 skip | B 골든 시험의 사례 목록이 비어 있음(2) |
| 연결 `tests/pipeline` | 19 통과 | |

B 시험 수는 b-1020 판(1,344)에서 b-1020 전용 기능(515, 계획 25)과 옛 A 시험(325, 계획 26)을 빼고 B 전용 검사 8개를 되살린 수다.

## 3. 실행

```powershell
uv run --locked claview run --question "GDP 2023년 보여줘"                                    # A만(B 포트 없음)
uv run --locked claview run --synthetic-b --question "GDP 2023년 보여줘"
uv run --locked claview run --synthetic-b --synthetic-answer --question "총고정자본형성 2023년" --out runs/flow-demo
uv run --locked claview evaluate --as-of 2026-10-22 --out runs/pipeline-golden                 # 골든 통합 평가
uv run --locked claview-b evaluate --mode replay --as-of 2026-10-22 --output runs/b-golden/result.json   # B 단독
```

- `claview`(=`claview-pipeline`, `python -m claview.pipeline`)는 A→B→조회 흐름이다. `scripts/pipeline/run_flow.py`는 `claview run`과 같다. `claview-b`는 B 단독 명령(inspect, evaluate, fetch-gold, diagrams, 검색 도구)이다.
- 프로세스 전체에서 네트워크를 막는다. `--synthetic-b`가 없으면 B 포트를 만들지 않아 A는 `b_port_not_configured`로 끝난다.
- `--synthetic-b`는 `claview.pipeline.synthetic`의 **합성** B 모델(메타의 첫 항목·각 분류의 첫 값·첫 주기)과 합성 조회 클라이언트(값 "1")를 쓴다. 연결·계약 확인일 뿐 모델·값의 정확도가 아니다. `--synthetic-answer`는 대기 질문에 첫 선택지로 답하는 합성 사용자다. `--out`은 저장소 밖이나 `runs/` 아래만 받는다.
- `claview evaluate`는 골든 22문항을 새 A 세션(규칙, 모델 호출 없음) → 포트 → B(저장된 골든 B 답 재생) → 합성 조회로 끝까지 돌린다. 2026-10-23 기준선(as_of 2026-10-22): 표 일치 17/22, B 성공 17/22, completed 17/22. 골든 22는 개선에 반복해 쓴 자료라 독립 평가가 아니고, B 답은 재생, 값은 합성이다.
- 실제 HCX·KOSIS 클라이언트는 `claview.pipeline.live_b_clients(allow_external=True)`로만 만든다(환경변수 이름 `CLOVASTUDIO_API_KEY`, `KOSIS_API_KEY`). `claview run`에는 연결하지 않았다. 실호출은 호출 수와 비용을 밝히고 승인받은 뒤에만 한다.

## 4. 변경 이력

| 계획 | 내용 |
|---|---|
| 1단계(`b172d75`) | A split 완료2 + B b-1020 + 기간 패키지를 한 저장소에 합침. 기록: [docs/records](docs/records/README.md) |
| [24](docs/plans/24_통합레포_재배치_S1.md) | A를 `claview.a`로, 시험을 `tests/{a,b,pipeline}`로 |
| [25](docs/plans/25_통합레포_재배치_S2.md) | B를 10/16 compact 판(+ 단위 표기 수정)으로, 연결을 표 목록+원질문 방식으로, B·연결을 `claview.b`·`claview.pipeline`으로 |
| [26](docs/plans/26_옛A_새A교체.md) | 끝까지 도는 평가·명령을 pipeline으로, 옛 A와 그 시험·스크립트 삭제 |
| [27](docs/plans/27_폴더정리.md) | data·eval·scripts·docs 정리, 기간 패키지를 A 안(`claview.a.period_core`)으로 |

## 5. 알려진 한계

- 실제 KOSIS·HCX로는 이 통합본을 확인하지 않았다. 합성·재생 응답으로 확인한 것은 연결과 계약뿐이다.
- A는 split 완료2 판이다. 공유된 `.4` 계열(구조 `structure-2026-10-22.1`)의 입력·선택 보완은 들어 있지 않다.
- B는 10/16 compact 판이라 b-1020의 공용 기간 해석기 연결·계약 검사·구조 힌트가 없다. B는 질문 문장에서 기간을 읽는다.
- `claview.pipeline`의 포트는 한 번에 한 요청만 처리한다(요청마다 B 서비스 객체를 새로 만들고 마지막 것을 보관한다).
- B 검색 문서·매핑 자료(`data/` 아래)를 다시 만드는 준비 스크립트는 옛 A에 기대어 계획 26에서 지웠다. 이미 만든 자료는 그대로 쓴다(관련 README에 안내).

## 6. 프로젝트 목표

- 대상: KOSIS Open API 내 한국은행 소관 통계표 전체. 금리 사례는 구현과 시험에 쓰는 예시다.
- 처리: 질문 해석 → 통계표·항목·분류 선택 → 자료 조회·필요한 가공 → 표·그래프·근거 제공.
- 결과물: 에이전트, 외부 클라이언트에서 쓸 MCP 서버, 검수한 평가 사례와 측정 결과.
- 원자료 수치는 API 응답에서 가져오고, 계산한 값에는 가공 방법을 남긴다.
- 주요 문서: [전체 문서 목록](docs/README.md), [개발계획](docs/design/프로젝트_개발계획.md), [처리 흐름](docs/design/처리흐름과_연결구조.md), [데이터 안내](data/README.md).
- 코드·구현 문서·재현 자료는 GitHub에서, 일정·담당·회의 기록은 팀 노션에서 관리한다. API 키와 개인 자료는 올리지 않는다.
