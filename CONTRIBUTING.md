# 협업 규칙

zip 파일로 코드를 주고받지 않는다. 모든 변경은 이 레포의 브랜치와 PR로 한다.

처음 설치와 담당별 작업 방법은 [팀 작업 가이드](docs/guides/팀_작업_가이드.md)에 있다.

## 1. 내 경로

| 담당 | 고치는 경로 | 다른 경로가 필요하면 |
|---|---|---|
| A | `modules/a/` | 이슈(라벨 `module:a`)로 요청하거나 그 경로 담당에게 리뷰 요청 |
| B | `modules/b/` | 〃 |
| C | `modules/c/`, `frontend/` | 〃 |
| 취합 | `pipeline/` | 〃 |
| 공동 | `common/`, `requirements*.txt`, `data/`, `eval/cases/`, Docker 파일 | 관련 담당 리뷰(계정·팀 확정 전 CODEOWNERS 자동 지정 없음) |

남의 모듈 결과를 내 모듈에서 고치지 않는다(예: C가 B 결과를 사후 보정). 필요하면 그 모듈 담당에게 이슈로 요청하거나 계약을 바꾸자고 제안한다.

## 2. 브랜치·PR

- 협업 절차: main 직접 push를 하지 않고 PR + CI 통과 + 관련 담당자 리뷰를 거친다. 이것은 팀 절차이며 GitHub가 강제한다고 가정하지 않는다. 11/04 점검 당시 보호 규칙은 비활성이었다. 원격 설정 변경은 별도 팀 결정이다.
- 작업 브랜치: `a/<주제>`, `b/<주제>`, `c/<주제>`, `fe/<주제>`, `pipe/<주제>`, `common/<주제>`. `feat/`, `chore/`, `docs/`도 목적에 맞게 쓴다. 짧게 유지하고 자주 머지한다.
- 한 PR에는 한 목적. PR 템플릿의 확인 항목(시험, 린트, 평가 기준선 대비, 외부 호출 여부)을 채운다.
- 시연용으로 시점을 고정할 때는 태그를 쓴다(`demo-2026-10-30`).

## 3. 내 모듈 개발 루프

```bash
git switch -c b/period-fix
docker compose -f docker-compose.yaml -f docker-compose.local.yaml up --build   # 저장하면 서비스가 다시 뜬다
curl -X POST http://localhost:10312/b/run -H "Content-Type: application/json" -d @common/fixtures/b_request/CV-AB-04.json
# 시험 (README 3절): 내 모듈 tests, tests_api
git commit -m "B: ..." && git push -u origin b/period-fix                       # PR 생성
```

- 앞 단계가 필요하면 `common/fixtures/`의 샘플을 입력으로 쓴다. 예: B는 `b_request/`(골든 정답 표)를 쓰므로 A가 없어도 된다. C는 `data_result/`, `data_result_saved/`(실제 KOSIS 결과)를 쓴다.
- 각 모듈의 `/docs`(Swagger)에서 요청·응답 모양을 확인할 수 있다.

## 4. 계약을 바꿀 때

1. `common/claview_common/contracts`의 모델을 고친다. 호환이 깨지면 `versions.py`의 판본을 올린다.
2. `pipeline/scripts/build_fixtures.py`로 `common/fixtures`를 다시 만든다.
3. 영향 받는 모듈·파이프라인 시험을 같은 PR에서 고친다. A·B·C 담당 모두 리뷰한다.

## 5. 의존성

- 루트 `requirements.txt`에만 버전을 고정(`==`)해 추가한다. 모듈별 requirements는 두지 않는다.
- PR 설명에 이유를 쓴다. 모든 서비스 이미지에 들어가므로 무거운 패키지는 먼저 상의한다.

## 6. 하지 않는 것

- 키·토큰을 코드·compose·로그·이슈에 쓰지 않는다. `.env`(Git 제외)에만 둔다.
- 승인 없이 실제 OpenAI·KOSIS를 부르지 않는다(`CLAVIEW_MODE=live`).
- `.venv`, `runs/`, `dist/`, 스크린샷 같은 생성물을 커밋하지 않는다. 실험 코드는 `research/<담당>/`나 자기 모듈의 `scripts/`에 둔다.
- 평가셋 정답(`eval/cases/`)을 리뷰 없이 바꾸지 않는다.
