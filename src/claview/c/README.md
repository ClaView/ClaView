# C(표현) 공유본 보관

팀 C 공유본(`ClaView_team_20261022_v1.zip`, SHA-256 `3b24082b12a247459578ccbb64e20d0666f73f9bb575a15d0d9cc7fdb2d13f4b`)의 `c_practice/`를 바이트 그대로 옮겨 둔 폴더다. 이 README만 새로 썼다. 계획: [29](../../../docs/plans/29_C_레포보관.md).

## 현재 상태

- **보관만 했다.** `claview.pipeline`과 레포의 A(`claview.a`)·B(`claview.b`)에 연결하지 않았다. 레포의 실행 코드와 시험은 이 폴더를 import하지 않는다.
- 이 코드는 공유본의 `vendor/a`(옛 A `claview_v3`)와 `vendor/b`(B 10/16 사본)를 `sys.path`에 넣고 평면 import(`from dependencies import …`, `from claview.contracts import …`)로 도는 구조다. 두 vendor 폴더는 레포에 넣지 않았으므로 **이 폴더에서는 그대로 실행되지 않는다.**
- 실행해 보려면 공유본 zip을 레포 밖에 풀어 그 안내(`docs/c/00_먼저읽기.md`)대로 한다. 실제 질문은 HCX·KOSIS를 호출하므로 호출 수·비용 승인이 필요하다. 팀 실행 파일 사본은 `scripts/c/team_bundle/`에 있으나 레포 안에서는 vendor가 없어 돌지 않는다.
- `abc_integration/dist/`(생성된 화면 파일)는 디스크에는 있으나 레포 `.gitignore`의 `dist/` 규칙에 걸려 Git에 들어가지 않는다.
- Highcharts 이용 조건은 `docs/c/THIRD_PARTY_NOTICES.md`.

## 구성

| 경로 | 내용 |
|---|---|
| `abc_integration/` | 질문 → 옛 A → B → 조회 → C 실행기(`pipeline.py`), 로컬 서버·실행기, C 분석·차트 정책, 화면 JS·CSS |
| `bc_demo/` | `c_renderer.py`(data_result → 차트·표 모델), 저장 샘플 4개 |
| `ui_a_highcharts/` | Highcharts 화면 시안 HTML |
| `vendor/` | Highcharts accessibility 모듈 |

## 연결 때 고칠 것(계획 30, 팀원 확인 뒤)

- 옛 A 직접 호출: `pipeline.py`, `confirmation.py`, `catalog_recovery.py`가 `claview_v3`를 부른다. 레포 연결에서는 `claview.pipeline` 결과(요구별 `fetch`·`b_outcome`)를 받아 표시하고, 사용자 확인은 A의 되묻기(질문 + 선택지)로 바꾼다. 조건 편집 화면(`b_conditions*`)은 1차에서 숨긴다.
- vendor 경로: `dependencies.py`의 `vendor/a`·`vendor/b`·`bc_demo` 경로 검사.
- 표 ID·M2 하드코딩: `c_analysis.calculate_rates`(표 2개·항목 이름), `composition.py`(M2 표·상품 코드, 추가 KOSIS 조회), `chart_rubric.py`(평가 기준).
- `catalog_recovery.py`의 특정 질문용 규칙(총산출물가지수).
- `request_capabilities.py`의 정규식 거절(예측·환율·주어 없는 후속 질문). 레포 A는 예측·환율 질문을 막지 못하므로 A 쪽 작업으로 따로 다룬다.
- B 보정 3개(`query_contract.py`, `named_selections.py`, `model_bridge.py`)와 `b_conditions.py`: B 결과를 바꾸므로 B 담당 확인 대상.
- `kosis_compat.py`: 레포 B `fetch.py`의 NFKC 단위 수정과 겹친다.
