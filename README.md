# ClaView (A 표 선택 → B 계열·기간 선택·조회 → C 표현)

자연어 질문을 받아 KOSIS 한국은행 소관 통계표 349개 중 필요한 표를 고르고(A), 항목·분류·기간·주기를 정해 KOSIS Open API로 값을 가져와(B), 표·그래프로 보여 주는(C) 에이전트 PoC다.
A·B·C 담당자가 **한 레포에서 각자의 모듈 경로만** 고친다. 메인 파이프라인은 각 모듈의 Python service를 **코드로 직접** 불러 전체를 돌린다. 모듈별 HTTP API는 독립 시험·평가용이다. A의 기본 HTTP는 새 A의 `screen.py`·AView 경로이며, 예전 A HTTP는 `/a/legacy/*`로 구분한다. 실행 환경은 Docker로 통일한다.

```text
서비스:  브라우저 → frontend(nginx :10300) ─/api/*→ pipeline(:10301)
                                                    └ 같은 프로세스: 새 A 화면 service(AView) → B service(요청마다) → 조회 → C service

모듈 시험·평가용(같은 service 코드를 HTTP로):
         module-a(:10311) /a/select·/a/answer·/a/records/{session_id}
         module-b(:10312) /b/resolve·/b/fetch·/b/run   module-c(:10313) /c/present
```

pipeline은 module-a/b/c 컨테이너를 부르지 않는다. A HTTP 전환은 이 호출 방식을 바꾸지 않으며, 모듈 A만의 결과를 전체 화면·B·C 연결 완료로 해석하지 않는다.

- **처음이면: [팀 작업 가이드](docs/guides/팀_작업_가이드.md)** (설치, 매일 작업 흐름, 담당별 시험, 첫 과제)
- 결정 사항: [결정 기록](docs/design/결정_기록.md)
- 구조·의존 규칙: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · 협업 규칙: [CONTRIBUTING.md](CONTRIBUTING.md) · 단계 사이 계약: [docs/contracts](docs/contracts/README.md)

## 1. 폴더와 담당

| 경로 | 내용 | 담당 |
|---|---|---|
| `modules/a/claview_a/` | **새 A(표 단위 경로). 기본 화면과 모듈 HTTP에 연결하며 실행 모드는 서버 설정으로 정한다.** 공개 `run`(재검색 인자 전달), 기준 축 답변 적용 `answers`, CLI `python -m claview_a`. 표준 라이브러리·langgraph만 쓰는 패키지 규칙을 지킨다. [기준판 문서](docs/modules/a/A_기준판_표단위경로_20261028.md) · [55문항 측정판](docs/modules/a/A_측정판_55문항_20261029.md) · [계획 54](docs/plans/54_새A_화면연결.md) | A |
| `modules/a/claview_a_legacy/` | 기존 A(10/21판). `/a/legacy/select`·`/a/legacy/answer`, 옛 staged 실행기·`claview` 명령·골든 22 평가가 쓴다. 새 경로의 자동 대체로 쓰지 않는다 | A |
| `modules/a/claview_a_api/` | 새 A 화면 service(`screen.py`), AView 어댑터(`view.py`), 세션을 저장하는 HTTP 경계(`http.py`, `api.py`). 기본 `/a/select`·`/a/answer`는 새 A이며 계약 판을 반드시 보낸다. 예전 `service.py`는 legacy 경로에 남는다 | A |
| `modules/a/{tests,tests_tl,tests_legacy,tests_api,scripts}/` | 새 A 시험(옮긴 부품 / 표 단위 경로), 기존 A 시험, API 시험, 연구·평가 도구(새 A 평가기 `scripts/tl_eval`) | A |
| `modules/b/claview_b/`, `claview_b_api/` | B 본체(그래프·조회·합성 모델), service와 HTTP 계층(`/b/resolve`, `/b/fetch`, `/b/run`) | B |
| `modules/c/claview_c/`, `claview_c_api/` | C 표현 로직(렌더러·분석·차트 정책), service와 HTTP 계층(`/c/present`) | C |
| `frontend/` | 화면(정적 빌드 → nginx). 파이프라인 API만 부른다 | C |
| `pipeline/claview_pipeline/` | 메인 파이프라인(단계 진행·확인 질문·재개), 프론트가 부르는 API, 골든 통합 평가 | 취합 |
| `common/claview_common/` | 공동 소유: 계약(Pydantic), FastAPI 공통(`/health`, `/info`), 설정, 시험 도구 | 전원 |
| `common/fixtures/` | 단계별 계약 샘플(골든 22에서 생성). 앞 모듈 없이 내 모듈을 시험할 때 쓴다 | 전원 |
| `data/`, `eval/cases/` | 349표 카탈로그·원메타·매핑, 평가셋(골든 22, 다중 표·이중 축 초안 5 `golden_set_v3_multi_table.json` 등) | A·B |
| `research/` | 서비스 코드가 아닌 보관·실험물(옛 C 로컬 서버·실행 파일 등) | 각자 |
| `docs/` | 구조, 계약, 파트별 문서(`modules/b`, `modules/c`), 지난 계획·기록 | 각자 |

## 2. Docker로 실행

```bash
test -f .env || cp .env.example .env        # 기존 .env는 유지. 기본 live, 키는 .env에만 입력
docker compose up -d --build --wait        # frontend, pipeline, module-a/b/c (모두 healthy가 될 때까지 대기)
docker compose ps                          # 다섯 서비스 (healthy) 확인
docker compose down                        # 중지
```

- `/info`에 git SHA를 표시하려면 실행 전에 PowerShell `$env:GIT_SHA = git rev-parse --short HEAD`, bash `export GIT_SHA=$(git rev-parse --short HEAD)`(`make up`은 자동).

- 화면: http://localhost:10300/ · 파이프라인 API 문서: http://localhost:10301/docs · 모듈 API 문서: http://localhost:10311/docs (A), `10312`(B), `10313`(C)
- 새 A HTTP 요청에는 `contract_version: "a-http/2026-11-02.1"`이 필요하다. 응답은 `view`(AView)와 실행 모드를 담으며, 확인 답은 서버에 저장한 세션에 적용한다. [요청·답변·기록 조회와 이전 방법](docs/contracts/A_HTTP_전환_20261102.md)
- Docker·일반 배포의 기본은 `CLAVIEW_MODE=live`다. 모듈 A HTTP는 A만 실제 호출하고, 전체 화면은 같은 A service 다음에 B·KOSIS·C를 실행한다. 키가 없으면 준비되지 않은 상태로 알리며 합성 출력으로 대체하지 않는다. 일반 실사용에는 평가 승인 JSON이 필요하지 않다. `CLAVIEW_A_LIVE_APPROVAL`을 명시한 평가 실행은 기존 승인·해시·원장 검사를 통과해야 하며, 거절될 때 일반 실사용으로 전환하지 않는다. HTTP에서 실행 모드·기록 경로·checkpoint를 주입하지 않는다.
- Docker는 A HTTP와 화면의 A 기록을 `CLAVIEW_A_RUN_ROOT=/claview/runs/a_service`에 함께 저장한다. 일반 실행의 `live`·`synthetic` 기록은 하위 폴더로 분리하며, `claview_runs` named volume이 컨테이너 재생성 후에도 기록을 유지한다. 평가 원장은 별도 `claview_a_live` volume에 보관한다. `docker compose down`은 기록을 유지하고 **`down -v`는 이 volume과 기록을 삭제한다.**
- 개발(소스 마운트 + 자동 재시작): `docker compose -f docker-compose.yaml -f docker-compose.local.yaml up --build`
- 골든 통합 평가: `docker compose -f docker-compose.yaml -f docker-compose.eval.yaml run --rm eval-pipeline`
- 개발·시험에서만 `CLAVIEW_MODE=synthetic`을 명시하면 A 판단·B 선택·조회가 합성이다. B는 첫 항목·첫 분류값을 고르고 조회 값은 "1"이다. **배선 확인용이며 정확도가 아니다.** 기본 `live`는 A 첫 판단과 필요한 경우의 복구, B 선택 모델, KOSIS를 실제로 부른다. A는 기존 첫 판단 1회·복구 최대 1회를 유지하고, 되묻기 답 적용은 A 모델을 다시 부르지 않는다. 일반 실사용에 평가용 전체 비용 상한이 자동 적용되는 것은 아니다. 저장된 실제 결과 불러오기는 합성 모드와 별개이며 새 외부 호출을 만들지 않는다.
- live 준비 확인: `.env`에 `CLAVIEW_MODE=live`, `OPENAI_API_KEY`, `KOSIS_API_KEY`를 채우고(키는 채팅·로그에 쓰지 않는다) `docker compose up -d --build --force-recreate --wait`로 컨테이너를 다시 만든다. `.env`는 컨테이너를 만들 때 한 번만 읽는다. `/api/info`에서 `claview_mode`, `a_ready`, `model_ready`, `kosis_ready`, `ready`를 확인한다. 이는 설정 준비 상태이며 API 제공자의 인증·잔여 한도 확인은 아니다. [실행·기록 보존·되묻기 확인 안내](docs/guides/Docker_실호출_실행.md)
- 의존성은 루트 `requirements.txt` 하나다. 모든 백엔드 서비스가 같은 이미지(`claview-backend`)를 쓰고 실행 명령만 다르다.

## 3. 로컬 시험 (Docker 없이, Python 3.12)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
$env:PYTHONHASHSEED = "0"
$env:PYTHONPATH = "common;modules/a;modules/b;modules/c;pipeline"
.venv\Scripts\python -B -X utf8 -m pytest modules/a/tests -q -p no:cacheprovider     # 새 A 옮긴 부품 (가드: 별도 프로세스)
.venv\Scripts\python -B -X utf8 -m pytest modules/a/tests_tl -q -p no:cacheprovider  # 새 A 표 단위 경로 (평가 시험은 CLAVIEW_A_EVAL_DIR 지정 시 실행)
.venv\Scripts\python -B -X utf8 -m pytest modules/a/tests_legacy -q -p no:cacheprovider # 기존 A
.venv\Scripts\python -B -X utf8 -m pytest modules/b/tests -q -p no:cacheprovider     # B
.venv\Scripts\python -B -X utf8 -m pytest pipeline/tests -q -p no:cacheprovider      # 연결·단계 진행
.venv\Scripts\python -B -X utf8 -m pytest common/tests -q -p no:cacheprovider        # 계약·설정·기간
.venv\Scripts\python -B -X utf8 -m pytest modules/a/tests_api -q -p no:cacheprovider # 모듈 API (b, c도 같은 방식)
.venv\Scripts\python -B -X utf8 -m pytest modules/b/tests_api -q -p no:cacheprovider
.venv\Scripts\python -B -X utf8 -m pytest modules/c/tests -q -p no:cacheprovider
.venv\Scripts\python -B -X utf8 -m pytest pipeline/tests_api -q -p no:cacheprovider
.venv\Scripts\ruff check . ; .venv\Scripts\lint-imports                               # 린트, 모듈 경계
```

- 묶음마다 **별도 pytest 프로세스**로 돌린다. A 시험은 A의 import 가드를 깔아 같은 프로세스에서 다른 모듈을 불러오지 못하게 하고, B 시험은 모든 소켓을 막으며, API 시험은 루프백만 허용한다. Linux/macOS/CI는 `make test`(PYTHONPATH 구분자 `:`).
- 모든 시험은 외부 네트워크 없이 돈다.

### 시험 결과 (2026-10-25, 이 레포 구조, 네트워크 차단)

| 묶음 | 결과 | 이전 통합본 기준선 |
|---|---|---|
| A `modules/a/tests`(현재 `tests_legacy`) | 3,999 통과, 10 skip, 1 실패(레포 밖 417문항 경로, 기준선과 같음) | 3,999 통과, 10 skip, 1 실패 |
| B `modules/b/tests` | 548 통과, 2 skip | 547 통과, 1 실패(패키지 설치 확인 → requirements 확인으로 교체) |
| 연결 `pipeline/tests` | 36 통과(연결 19 + 단계 진행 17) | 19 + C 19(서버 시험 2건은 `pipeline/tests_api`로 이동) |
| 공통 `common/tests` | 1,264 통과 (계약 샘플 73, 설정 12, 기간 함수 B·C 동일성 1,179) | 신규 |
| 모듈 API A·B·C, 파이프라인 API | 6 · 9 · 27 · 3 통과 | 신규 |

### Docker 확인 (2026-10-26, Docker Desktop 26.1.4, synthetic)

| 항목 | 결과 |
|---|---|
| 빌드 | `claview-backend` 265MB, `claview-frontend` 49MB |
| 헬스체크 | frontend·pipeline·module-a·b·c 다섯 개 모두 `(healthy)` |
| 모듈 API | `/a/select`(CV-AB-12 → proceed, DT_311Y001), `/b/run`(CV-AB-04 → ok), `/c/present`(저장된 실제 KOSIS 결과 → ok) |
| nginx 경유 전체 흐름 | 골든 6문항 중 5건 5단계 완료, CV-AB-10은 A 표 미확정으로 표 단계 중단(기존과 같음). 확인 질문 → 선택 → 재개 완료. 토큰 없는 요청 403 |
| 화면 | 브라우저에서 질문 입력 → 5단계 진행 → 결과 카드·Highcharts 차트 표시, 콘솔 오류 없음. 보안 헤더(CSP 등) 화면·`/api` 모두 적용 |
| 모듈 service 직접 호출 (2026-10-26) | module-a/b/c 컨테이너를 멈춘 상태에서도 화면 경로 흐름·확인 질문 재개가 그대로 동작. 골든 22문항의 상태·단계·카드·단계 문구가 변경 전 경로와 22/22 같음 |
| 골든 통합 평가(`eval-pipeline`) | 표 일치 17/22, B 성공 17/22, completed 17/22 (기존 기준선과 같음) |

- 위 표의 `/a/select`와 `proceed` 결과는 10월 26일 당시의 기존 A HTTP 측정이다. 지금은 그 계약이 `/a/legacy/select`에 있으며 새 기본 HTTP의 결과와 합산하지 않는다.
- 모듈 A의 첫 요청은 카탈로그 적재 때문에 약 8초 걸린다(이후 요청은 빠름).
- 새 A(2026-10-28) 시험 결과는 [A 기준판 문서](docs/modules/a/A_기준판_표단위경로_20261028.md) 3·5절에 있다.
- 새 A 55문항 측정판(2026-10-29: 기간·주기 검사, 제한 재검색) 결과와 남은 한계는 [A 측정판 문서](docs/modules/a/A_측정판_55문항_20261029.md)에 있다.
- A 기간·주기 의미 구조화, 되묻기·부분 복구·focus 보존의 공유 범위는 [A 1차 기본 기능 공유](docs/modules/a/A_1차_공유_범위_20261102.md)에 있다. 새 A Python service와 A 단독 HTTP를 포함하며 전체 화면·B 연결 완료를 뜻하지 않는다.

## 4. 바뀐 점 (이전 zip 통합본 대비)

- 파일 이동: `src/claview/a|b|pipeline` → `modules/a/claview_a`, `modules/b/claview_b`, `pipeline/claview_pipeline`. import 이름 `claview.a` → `claview_a` 등. 옛 C 공유본(`src/claview/c`)은 표현 로직은 `modules/c/claview_c`, 단계 진행 실행기는 `pipeline/claview_pipeline/staged.py`, 화면은 `frontend/`, 로컬 서버·실행 파일은 `research/c/legacy_web/`로 나눴다.
- C의 추가 KOSIS 조회(M2 공식 전체, `attach_live_total`)는 C에서 파이프라인으로 옮겼다. C는 조회하지 않는다.
- 합성 B 모델·조회(`synthetic.py`)는 B 소유로 옮겼다(`claview_b.synthetic`, 파이프라인에는 별칭만).
- 공통 계약을 `claview_common.contracts`(Pydantic)로 만들고, 골든 22에서 단계별 샘플을 `common/fixtures/`에 만들었다.
- `uv`·`pyproject` 의존성 → 루트 `requirements.txt`(uv.lock의 판본 그대로). `pyproject.toml`은 도구 설정(ruff, pytest, import-linter)만 둔다.

## 5. 아직 하지 않은 것

- `claview` 명령과 골든 통합 평가(`claview evaluate`, `eval-pipeline`)는 아직 예전 경로(`claview_pipeline.session`·`b_port`: A 안의 B 포트)를 쓴다. 서비스 경로(`staged`)와 결과는 같지만 코드가 둘이다.
- 기존 A의 `agent/b_connection`은 legacy API와 옛 평가 경로에 남아 있다. 새 A HTTP는 AView의 송신 예정 값을 반환하며 B를 실행하지 않는다.
- 모듈별 평가 러너(`make eval-a/b/c`, 기준선 비교 리포트)와 GitHub Actions의 Docker 스모크는 설계안 6단계다.
- LLM 호출·카탈로그 읽기·기간 계산·경로/키 읽기가 모듈마다 중복되어 있다. 공통 모듈로 옮기는 설계와 순서: [공통 모듈 이전 설계](docs/design/공통모듈_이전_설계.md)(A 패키지 규칙 완화 결정 필요).
- 실제 KOSIS·OpenAI로 화면 전체 흐름(A→B→조회→C)을 확인하지 않았다. B 선택 단계만 live로 확인했다(2026-11-02, 골든 CV-BC-02 두 표 일치, [기록](docs/modules/b/research/B_모델_교체_GPT_고정_및_후속개선_기록_20261102.md)).

## 6. 프로젝트 목표

- 대상: KOSIS Open API 내 한국은행 소관 통계표 전체. 처리: 질문 해석 → 통계표·항목·분류 선택 → 자료 조회·가공 → 표·그래프·근거 제공.
- 원자료 수치는 API 응답에서 가져오고, 계산한 값에는 가공 방법을 남긴다.
- 코드·구현 문서·재현 자료는 GitHub에서, 일정·담당·회의 기록은 팀 노션에서 관리한다. API 키와 개인 자료는 올리지 않는다.
