# ClaView

KOSIS Open API 내 한국은행 소관 통계표를 대상으로 하는 자연어 통계 탐색·시각화 에이전트 프로젝트입니다.

## 현재 상태

디렉터리 골격만 준비했습니다. 실행 가능한 서비스, 의존성, Python 버전 및 도구 계약은 아직 설정하지 않았습니다. Python 환경은 uv로 관리할 예정입니다.

## 구조

| 경로 | 책임 |
|---|---|
| src/claview/shared | 공통 설정·입출력 모델·오류 |
| src/claview/backend/kosis | KOSIS API 클라이언트 |
| src/claview/backend/metadata | 목록·메타정보 수집·검수 |
| src/claview/backend/retrieval | 검색 문서·색인·용어 매핑 활용 |
| src/claview/backend/processing | 정합·병합·코드 기반 계산 |
| src/claview/backend/presentation | 표·그래프 생성 |
| src/claview/agent | 질의 해석·조건 확인·오케스트레이션 |
| src/claview/mcp_server | 공통 기능의 MCP 도구 노출 |
| apps/demo | 시연용 화면, 구현 방식 미정 |
| scripts | 수집·검수·색인 생성 실행 진입점 |
| data/catalog, lexicon, samples | 검토 후 버전 관리할 소규모 데이터 |
| eval/golden, configs | 골든셋 및 평가 설정 |
| tests | 단위·통합·계약·E2E 테스트와 픽스처 |
| docs | 개발에 필요한 최신 문서 |
| experiments | 팀이 공유·재현할 기술 실험 |
| artifacts | 실행 산출물, Git 제외 |

폴더는 논리적 책임 구분이며 별도 서버나 패키지로 배포한다는 의미는 아닙니다. agent와 MCP는 공통 처리 기능을 재사용합니다.

## 작업 원칙

- 이 저장소는 팀 공유용입니다. 개인 조사·메모는 저장소 밖에서 관리하고, 공유할 내용만 검토하여 반영합니다.
- 필요한 자료는 docs에 독립적으로 이해할 수 있게 정리하고 공식 출처·관련 팀 문서·검증 상태를 기록합니다.
- 내부 링크는 문서 기준 상대경로를 사용합니다. 실행 명령은 저장소 루트 기준으로 안내하며 개인 폴더나 로컬 절대경로에 의존하지 않습니다.
- API 키·토큰은 코드, 데이터, 문서, 로그에 넣지 않습니다.
- 대형 원자료와 색인은 data/raw, data/processed, data/indexes에 보관하고 Git에서 제외합니다.
- 노션은 업무·회의·조사 관리, GitHub는 코드와 코드에 밀접한 개발 문서의 기준으로 사용합니다.

문서 작성·반영 기준은 [docs/README.md](docs/README.md)를 참고하세요.
