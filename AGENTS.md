# 팀 공유 저장소 작업 원칙

- 전체 구조와 기능 책임은 루트 README.md를 먼저 확인합니다. 구조 변경 시 README도 함께 갱신합니다.
- 공유 문서는 사실·절차·결정 중심으로 작성합니다. 확인된 결과, 제안, 미확정 사항을 구분합니다.
- 개인 작업 경로·사용자명·개인 전용 링크·다른 팀의 이름과 내부 자료를 공유 파일에 넣지 않습니다.
- 문서 링크는 상대경로, 실행 명령은 저장소 루트 기준으로 작성합니다. 일반 문서명은 한국어를 우선합니다.
- 코드 식별자와 코드 주석은 영어로 작성합니다.
- Python은 3.12, 의존성은 루트 `requirements.txt` 하나(모든 서비스 이미지 공통)입니다. 환경 변경 시 requirements·Dockerfile·실행 안내·테스트의 정합을 확인합니다.
- API 키·토큰·비밀정보를 읽거나 기록하지 않습니다. 요청 URL·예외 메시지·로그에도 키를 남기지 않습니다.
- 별도 요청 없이 실제 API를 호출하지 않습니다. 기본 테스트는 외부 연결 없이 수행합니다.
- 원자료·색인·대형 생성물은 기본 Git 제외입니다. 공유 데이터는 출처·기준 시점·검수 범위·재현 방법을 남깁니다.
- 원자료 수치는 API 응답을 보존하고 파생값은 코드로 계산합니다. 수집·조회·의미 검수·수치 대조를 구분합니다.
- 공통 모델의 기준을 중복 관리하지 않습니다. 공통 규격 변경은 사용하는 기능과 테스트를 함께 확인합니다.
- 노션은 일정·업무·회의, 저장소는 코드·구현 문서·재현 자료의 기준입니다.
- 기존 자료를 임의로 이동·삭제하지 않습니다. 이력 변경(force push 등)은 하지 않습니다. 작업은 브랜치와 PR로 합니다(CONTRIBUTING.md).

## 모듈 구조 원칙

- 각 담당은 자기 경로만 고칩니다: A `modules/a/`, B `modules/b/`, C `modules/c/`·`frontend/`, 연결 `pipeline/`. 공동 소유는 `common/`, `requirements*.txt`, `data/`, `eval/cases/`, Docker 파일입니다(.github/CODEOWNERS).
- 모듈 본체(`claview_a`, `claview_b`, `claview_c`)는 서로 import하지 않습니다. 모듈을 잇는 일은 `pipeline/`에만 둡니다. 규칙은 `lint-imports`로 검사합니다(pyproject.toml).
- 모듈 사이의 요청·응답 모양은 `common/claview_common/contracts`가 기준입니다. 바꿀 때는 `common/fixtures`를 다시 만들고(`pipeline/scripts/build_fixtures.py`) 쓰는 모듈의 시험을 함께 고칩니다.
- A 본체는 A 패키지 규칙(표준 라이브러리·langgraph만, 상대 import)을 지킵니다. 웹 계층은 `claview_a_api`에 둡니다.
- 시험은 묶음별 별도 pytest 프로세스로 돌립니다(README 3절). 합성 B 모델·조회(`claview_b.synthetic`)는 연결 확인용이며 정확도 근거로 쓰지 않습니다.
- 키는 `.env`(Git 제외)에만 두고 compose 파일·코드·로그에 쓰지 않습니다. 실제 호출(`CLAVIEW_MODE=live`)은 호출 수·비용 승인 뒤에만 합니다.
