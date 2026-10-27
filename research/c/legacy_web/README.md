# C(표현) — A·B 연결본

팀 C 공유본(`ClaView_team_20261022_v1.zip`, SHA-256 `3b24082b12a247459578ccbb64e20d0666f73f9bb575a15d0d9cc7fdb2d13f4b`)의 `c_practice/`를 옮겨 둔 폴더(계획 [29](../../../docs/plans/29_C_레포보관.md))를 최신 A·B 흐름에 맞게 고친 것이다(계획 30). 고친 파일은 `abc_integration/`의 `pipeline.py`, `confirmation.py`, `dependencies.py`, `server.py`, `request_capabilities.py`, `composition.py`, `example_questions.py`와 이 README다.

## 현재 상태

- **연결했다(계획 [30](../../../docs/plans/30_C_연결.md)).** C의 실행기(`abc_integration/pipeline.py`)가 `claview.pipeline.FlowSession`으로 A → B → 조회를 돌리고 결과를 C 렌더러·화면 카드로 옮긴다. 옛 A(`claview_v3`)와 B 사본(`vendor/a`, `vendor/b`)은 쓰지 않는다.
- A의 확인 질문은 화면의 후보 카드로 보이고, 사용자는 `option_id`만 돌려보낸다(`confirmation.py`). 옛 조건 편집 화면(`/api/conditions`)은 쓰지 않는다.
- 옛 A·B에 묶여 연결하지 않은 모듈(`b_conditions`, `named_selections`, `query_contract`, `model_bridge`, `kosis_compat`, `catalog_recovery`)은 `abc_integration/legacy_old_ab/`에 보존했다.
- 시험: `uv run python -B -X utf8 -m pytest tests/c -q -p no:cacheprovider`(합성 B·조회, 네트워크 차단, 별도 프로세스).
- 실행(Windows PowerShell, 저장소 루트): `uv run python -X utf8 src/claview/c/abc_integration/ui_build.py` 후 `uv run python -X utf8 src/claview/c/abc_integration/server.py --prompt-keys`. 키는 [키 관리 가이드](../../../docs/c/키_관리_가이드.md).
- 실제 질문은 HCX·KOSIS를 호출하므로 호출 수·비용 승인이 필요하다.
- `abc_integration/dist/`(생성된 화면 파일)는 레포 `.gitignore`의 `dist/` 규칙에 걸려 Git에 들어가지 않는다. `ui_build.py`로 만든다.
- Highcharts 이용 조건은 `docs/c/THIRD_PARTY_NOTICES.md`.
- `scripts/c/team_bundle/`의 팀 실행 파일(`.cmd`, `setup_team.py`)은 옛 구조 기준이며 이번에 고치지 않았다.

## 구성

| 경로 | 내용 |
|---|---|
| `abc_integration/` | 질문 → A → B → 조회 → C 실행기(`pipeline.py`), 확인 카드(`confirmation.py`), 로컬 서버·실행기, C 분석·차트 정책, 화면 JS·CSS |
| `bc_demo/` | `c_renderer.py`(data_result → 차트·표 모델), 저장 샘플 4개 |
| `ui_a_highcharts/` | Highcharts 화면 시안 HTML |
| `vendor/` | Highcharts accessibility 모듈 |

## 남은 일(계획 30의 5절 결정 필요 사항)

- 조건 편집 화면을 되살릴지, B 사후 보정 3종(공표 증감률 기간, 지정 항목 보완, 날짜 표기 정규화)을 어디에 둘지(B 담당 확인).
- 표 ID 하드코딩(`c_analysis.calculate_rates`, `composition.py`, `chart_rubric.py`) 유지 여부.
- 실제 HCX·KOSIS 호출 시험(승인 후), 배포 형태에 따른 인증·동시 처리·속도 제한.
