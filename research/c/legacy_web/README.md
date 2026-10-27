# C(표현) — A·B 연결본 (보관)

팀 C 공유본(`ClaView_team_20261022_v1.zip`, SHA-256 `3b24082b12a247459578ccbb64e20d0666f73f9bb575a15d0d9cc7fdb2d13f4b`)의 `c_practice/`를 옮겨 둔 폴더(계획 [29](../../../docs/plans/29_C_레포보관.md))를 최신 A·B 흐름에 맞게 고친 것이다(계획 30). 모듈 구조로 재배치하면서 서비스에 쓰는 부분은 아래 위치로 옮겼고, 이 폴더에는 옛 로컬 서버·실행 파일만 남았다.

## 옮긴 위치

| 옛 위치(`src/claview/c/...`) | 지금 위치 |
|---|---|
| `abc_integration/pipeline.py`(질문 → A → B → 조회 → 정렬 → C 실행기) | `pipeline/claview_pipeline/staged.py` |
| `abc_integration/confirmation.py`, `request_capabilities.py` | `pipeline/claview_pipeline/` |
| `abc_integration/c_analysis.py`, `chart_policy.py`, `chart_rubric.py`, `composition.py`, `bc_demo/c_renderer.py` | `modules/c/claview_c/` |
| `bc_demo/samples/` | `modules/c/samples/`, `common/fixtures/data_result_saved/` |
| `abc_integration/ui_build.py`, `chart_display.py`, `example_questions.py` | `frontend/build/` |
| `abc_integration/*.js`, `*.css`, `ui_a_highcharts/*.html`, `vendor/` | `frontend/src/`, `frontend/src/template/`, `frontend/src/vendor/` |
| `abc_integration/golden_replay.py`, `golden_inspect.py` | `pipeline/scripts/` |
| `abc_integration/server.py`, `dependencies.py`, `launcher.py`, `background.py`, `c_presentation.py`, `credential_store.py`, `saved_pages.py`, `windows_startup.py`, `legacy_old_ab/` | 이 폴더(`research/c/legacy_web/`) |
| `tests/c/test_c_flow.py` 등 / `tests/c/test_c_server.py` | `pipeline/tests/staged/` / 이 폴더의 `tests/` |
| `docs/c/` | `docs/modules/c/` |
| `scripts/c/team_bundle/` | `research/c/team_bundle/` |

## 현재 상태

- **연결했다(계획 [30](../../../docs/plans/30_C_연결.md)).** 서비스 흐름(`pipeline/claview_pipeline/staged.py`)이 A·B·C를 각 모듈 service(`claview_a_api`, `claview_b_api`, `claview_c_api`)로 부르고 결과를 화면 카드로 옮긴다. 옛 A(`claview_v3`)와 B 사본(`vendor/a`, `vendor/b`)은 쓰지 않는다.
- A의 확인 질문은 화면의 후보 카드로 보이고, 사용자는 `option_id`만 돌려보낸다(`pipeline/claview_pipeline/confirmation.py`). 옛 조건 편집 화면(`/api/conditions`)은 쓰지 않는다.
- **복수 표 결합·이중 축(계획 [31](../../../docs/plans/31_B_C_고도화_복수표_이중축.md)).** 조회 뒤 B의 정렬(`modules/b/claview_b/align.py`, 서비스 흐름은 `claview_b_api.service.align`으로 부름)로 단위 배율·기간·주기를 맞춘다. 단위가 다른 두 지표는 `dual_axis`(이중 축) 한 카드로 보이고, 주기가 다르면 "주기를 어떻게 맞출까요?" 카드가 뜬다(선택 후에는 이미 받은 값으로 이어 실행). 환율 환산과 직접 계산한 증감률은 2차 후속이다.
- **골든셋 v3 재생.** 다중 표·이중 축 골든 5건 재생기는 서비스 흐름으로 옮겼다(`pipeline/scripts/golden_replay.py`, `check`만; A는 골든 통계표로 대신). 모드는 `replay`·`kosis`·`hcx`, 옵션은 `--hcx-retries`, `--table-focus`(표별 지표 `focus` 전달), `--no-multi-table-prompt`이고, 결과는 통과 / 골든 계열 포함·추가 계열 N개 / 불일치로 나뉜다. 저장 기록은 `pipeline/scripts/golden_inspect.py`로 본다. 절차는 [시험 가이드](../../../docs/guides/다중표_이중축_시험_가이드.md), 표별 지표 제안은 [계획 32](../../../docs/plans/32_A_B_표별_지표_focus_제안.md).
- 옛 A·B에 묶여 연결하지 않은 모듈(`b_conditions`, `named_selections`, `query_contract`, `model_bridge`, `kosis_compat`, `catalog_recovery`)은 이 폴더의 `legacy_old_ab/`에 보존했다.
- 시험: 서비스 흐름 시험은 `make test-pipeline`(또는 `PYTHONPATH=common:modules/a:modules/b:modules/c:pipeline python -B -X utf8 -m pytest pipeline/tests -q -p no:cacheprovider`, 합성 B·조회, 네트워크 차단). 이 폴더의 `tests/test_c_server.py`는 어느 시험 묶음에도 들어 있지 않다.
- 화면 실행: 지금 화면은 `frontend/`(빌드 `frontend/build/ui_build.py`, `frontend/Dockerfile`)와 pipeline API(`pipeline/claview_pipeline/api.py`)다. 이 폴더의 옛 로컬 서버(`server.py`)는 모듈 구조에서 실행 확인을 하지 않았다. 키는 [키 관리 가이드](../../../docs/modules/c/키_관리_가이드.md).
- 실제 질문은 HCX·KOSIS를 호출하므로 호출 수·비용 승인이 필요하다.
- `frontend/dist/`(생성된 화면 파일)는 레포 `.gitignore`의 `dist/` 규칙에 걸려 Git에 들어가지 않는다. `frontend/build/ui_build.py`로 만든다.
- Highcharts 이용 조건은 `docs/modules/c/THIRD_PARTY_NOTICES.md`.
- `research/c/team_bundle/`의 팀 실행 파일(`.cmd`, `setup_team.py`)은 옛 구조 기준이며 고치지 않았다.

## 남은 일(계획 30의 5절 결정 필요 사항)

- 조건 편집 화면을 되살릴지, B 사후 보정 3종(공표 증감률 기간, 지정 항목 보완, 날짜 표기 정규화)을 어디에 둘지(B 담당 확인).
- 표 ID 하드코딩(`modules/c/claview_c/c_analysis.py`의 `calculate_rates`, `composition.py`, `chart_rubric.py`) 유지 여부.
- 실제 HCX·KOSIS 호출 시험(승인 후), 배포 형태에 따른 인증·동시 처리·속도 제한.
