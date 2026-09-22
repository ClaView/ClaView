# ClaView

KOSIS Open API 내 한국은행 소관 통계표를 대상으로 하는 자연어 통계 탐색·시각화 에이전트입니다.

## 개발 상태

- Python 3.12·uv 공통 환경 구성
- 패키지 설치·import·Python 버전 기본 테스트
- 에이전트·MCP 도구·시연 화면 설계 검토 중

## 시작하기

저장소 루트에서 실행합니다.

```sh
uv python install 3.12
uv sync --locked
uv run --locked python -c "import claview; print('claview import OK')"
uv run --locked python -m pytest -q
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
```

API 키가 필요하지 않습니다. 최초 설치 시 Python·패키지 다운로드가 발생할 수 있습니다.

- [공통 개발환경](docs/setup/공통_개발환경_준비.md)
- [문서 목차](docs/README.md)

## 디렉터리

| 경로 | 책임 |
| --- | --- |
| src/claview/shared | 공통 설정·입출력 모델·오류 |
| src/claview/backend/kosis | KOSIS API 연결 |
| src/claview/backend/metadata | 목록·메타정보 수집·검수 |
| src/claview/backend/retrieval | 검색 문서·색인·용어 매핑 |
| src/claview/backend/processing | 정합·병합·계산 |
| src/claview/backend/presentation | 표·그래프 생성 |
| src/claview/agent | 질문 해석·조건 확인·흐름 제어 |
| src/claview/mcp_server | 공통 기능의 MCP 도구 노출 |
| apps/demo | 시연 화면 |
| scripts | 수집·검수·색인 생성 진입점 |
| data | 목록·사전·샘플·원자료 |
| eval | 골든셋·평가 설정 |
| tests | 기본·단위·통합·계약·E2E 테스트 |
| docs | 개발 문서 |
| experiments | 기본 설치·테스트와 분리된 실험 |
| artifacts | 실행 산출물, Git 제외 |

폴더는 기능 책임을 나타냅니다. 서비스 모듈은 구현 전이며 저장·검색 제품과 화면 프레임워크는 미정입니다.

## 협업

- 코드·실행 안내·설계 문서는 GitHub에서 관리합니다.
- 일정·담당 업무·회의는 노션에서 관리합니다.
- 기능 브랜치에서 작업하고 PR 리뷰 후 main에 병합합니다.
- 공통 입출력 변경 시 관련 기능과 테스트를 함께 검토합니다.
- 키·원본 로그·대형 데이터는 커밋하지 않습니다.
