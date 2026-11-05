# ClaView

자연어 질문에서 KOSIS의 한국은행 소관 통계 중 등록된 349표를 탐색하고, 조회 조건을 정해 원자료와 계산·차트를 보여 주는 PoC다. 349표는 선정 범위이며 전체 한국은행 통계를 뜻하지 않는다.

## 실행 구조

```text
브라우저 → frontend(nginx) → pipeline HTTP → ScreenPipeline
                                           ├ A service: 질문·표·기간 판단 → AView
                                           ├ B service: 항목·분류·기간 결정 → KOSIS 조회
                                           └ C service: 코드 계산 → 카드·차트
```

제품 실행기는 `pipeline/claview_pipeline/screen.py`의 `ScreenPipeline` 하나다. `staged.StagePipeline`은 B·조회·C 단계와 실행 기록을 담당하는 추상 기반 클래스다. 모듈 본체는 서로 import하지 않으며 연결은 파이프라인이 각 service를 직접 호출한다. module-a/b/c HTTP는 같은 기능을 개별 시험·평가하는 경계이며 파이프라인이 그 컨테이너를 호출하지 않는다.

| 경로 | 책임 |
|---|---|
| `modules/a/claview_a`, `claview_a_api` | 표·조건 판단, 답 적용, AView와 세션 기록, A HTTP |
| `modules/b/claview_b`, `claview_b_api` | 계열·기간 결정, 공식 자료 조회와 정렬 |
| `modules/c/claview_c`, `claview_c_api` | 원자료·계산 구분, 차트 자료 생성 |
| `pipeline/claview_pipeline` | 전체 실행, 중단·재개, HTTP, 저장 복원 |
| `common/claview_common` | 계약·설정·기간 계산·공통 API |
| `frontend` | 정적 화면 빌드와 nginx 프록시 |
| `data`, `eval` | 메타·매핑·평가 질문과 고정 기대값 |
| `pipeline/scripts`, 모듈 `scripts` | 개발·평가 도구. 제품 이미지에서 제외 |
| `research`, `docs/records`, 날짜가 붙은 과거 계획 | 연구와 과거 실행 근거. 현행 실행 지침과 구분 |

## Docker 실행

Python 3.12 기반이며 모든 백엔드가 루트 `requirements.txt`로 만든 동일 이미지를 사용한다. pipeline만 백엔드 이미지를 빌드하고 모듈 서비스가 재사용한다.

PowerShell:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
$env:GIT_SHA = git rev-parse --short HEAD
# 연결 확인용: 실제 모델과 KOSIS 호출 없음
$env:CLAVIEW_MODE = "synthetic"
docker compose up -d --build --wait
docker compose ps
python pipeline/scripts/smoke_stack.py
docker compose down
```

화면은 `http://localhost:10300/`, 파이프라인은 `10301`, 모듈 A·B·C는 `10311`·`10312`·`10313`이다. 포트 매핑은 기준 main과 동일하게 호스트 IP를 지정하지 않는다. 다른 PC의 접속은 기존처럼 서버 IP와 해당 포트를 사용하며 네트워크·방화벽 설정을 따른다. 화면은 팀 내부 환경을 전제로 하며 공용 인터넷 공개 운영 인증을 제공하지 않는다.

Docker Compose와 `.env.example`의 기본 모드는 `live`다. 환경변수 없이 Python을 직접 실행하면 공통 설정은 `synthetic`을 사용한다. 실제 호출은 [Docker 실호출 안내](docs/guides/Docker_실호출_실행.md)에 따라 별도로 준비한다. `.env`를 수정한 뒤 컨테이너를 재생성해야 반영된다. `/api/info`의 준비 상태는 설정 유무이며 제공자 인증·잔여 한도 검증이 아니다. 준비되지 않은 live 실행을 합성 결과로 대체하지 않는다.

`synthetic`은 A·B 판단과 조회 값이 합성인 배선 시험이며 정확도 근거가 아니다. 새 답 적용은 A 모델을 다시 호출하지 않는다. 저장 결과 복원은 신규 조회와 구분한다. C는 모델 없이 계산한다.

기록은 프로젝트별 `claview_runs` volume, 평가 원장은 `claview_a_live` volume에 보존한다. `docker compose down`은 보존하고 `down -v`는 삭제한다. 서로 다른 체크아웃을 함께 시험할 때는 `-p`, `*_PORT`, `CLAVIEW_BACKEND_IMAGE`, `CLAVIEW_FRONTEND_IMAGE`를 모두 구분한다.

## 로컬 검증

Python 3.12 환경에 `requirements-dev.txt`를 설치한다. 모든 패키지는 다음 PYTHONPATH로 읽는다.

```powershell
$env:PYTHONPATH = "common;modules/a;modules/b;modules/c;pipeline"
$env:PYTHONHASHSEED = "0"
python pipeline/scripts/check.py
python -m ruff check .
lint-imports
$tests = Get-ChildItem frontend/tests -Filter *.cjs | ForEach-Object FullName
node --test $tests
```

`check.py`는 공통·A·A 표단위·A HTTP·B·B HTTP·C·pipeline·pipeline HTTP를 별도 pytest 프로세스로 실행하고 새 `runs/checks/` 폴더에 로그를 남긴다. 부분 실행은 `--suite pipeline`처럼 지정한다. API 시험은 루프백만 허용하며 나머지 외부 연결은 차단한다. Windows 샌드박스가 루프백을 막으면 로컬 개발 셸에서 실행한다.

## 통합 실행과 평가

```powershell
python -m claview_pipeline run --as-of 2026-10-29 --question "가계신용" --out runs/check-one
python -m claview_pipeline evaluate --as-of 2026-10-29 --out runs/check-22
python pipeline/scripts/golden_replay.py check
```

출력 경로는 매번 새로 지정한다. `run`·`evaluate`는 제품과 같은 AView 실행기를 합성 판단·합성 조회로 실행한다. `evaluate`는 22개 질문의 입력·기록·중단 상태를 점검하며 자동으로 사용자 답을 골라 성공률을 만들지 않는다. `golden_replay`는 정답 표·B 답과 합성 수치를 사용한 B→조회→정렬→C 회귀 시험이다. 둘 다 모델 정확도나 실호출 평가가 아니다.

Docker 안의 동일 합성 확인은 `docker compose -f docker-compose.yaml -f docker-compose.eval.yaml run --rm eval-pipeline`로 실행한다. `EVAL_OUT`은 새 `runs/...` 경로, `EVAL_AS_OF`는 명시한 기준일이다. 이 컨테이너는 네트워크 없이 실행한다.

## 문서와 측정 기준

- [구조·모듈 경계](docs/ARCHITECTURE.md), [현재 문서 목록](docs/README.md), [계약](docs/contracts/README.md)
- [팀 작업 절차](docs/guides/팀_작업_가이드.md), [협업 규칙](CONTRIBUTING.md)
- [11/03 카탈로그 갱신](docs/records/카탈로그_갱신_20261103.md): 등록 349표, 추가 발견 34표는 등록 대기
- [배포 정리 계획](docs/plans/58_새A_단일경로_배포정리.md), [검증 기록](docs/records/배포정리_20261105.md)
- [최종 전체 검증·Claude 인계](docs/records/배포정리_최종검증과_인계_20261105.md)

11/04 보고서의 62문항 결과는 `1fdc123`·당시 메타·사전 미반영 조건의 개발 평가다. 이번 코드 정리의 성능 수치로 옮겨 쓰지 않는다. 과거 17/22 측정도 당시 실행기의 역사적 결과다. 신규 실호출·사용성·독립·반복 안정성 평가는 별도 검증이다.
