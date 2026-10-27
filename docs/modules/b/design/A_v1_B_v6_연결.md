# A v1–B v6 연결 (2026-10-15)

상태: **연결 확인(오프라인)**. A 표 선택 → B 조건 선택 → 조회까지 그래프가 이어지는 것을 시험으로 확인했다. 실제 B 모델 정확도·KOSIS 수치·그래프 표현은 확인하지 않았다.

## 무엇을 연결했나

| 구성 | 출처 | 상태 |
|---|---|---|
| A | A 채택 기준 N5의 `src/claview/aflow` | 바이트 동일(aflow 트리 SHA-256 앞 16자리 `f29690ca011ba374`). 24노드 LangGraph 그래프 |
| B | B파트 v6 공유본 전체 | 코드·시험·자료·잠금 파일 그대로 |
| 연결 | `src/claview/ab_pipeline.py` | 새로 작성 |

B 패키지에는 1006 공유본의 옛 A(`a1_*`, `a2_*`, `a_discovery` 등)가 그대로 남아 있고 `graphs.py`가 이를 import한다. B 코드를 우선하기 위해 이 파일들은 바꾸지 않았다. 대신 연결 모듈이 B의 `build_b_graph`만 가져와 A v1 뒤에 붙인다. 옛 A 경로(`graphs.invoke_pipeline`, CLI)는 이전처럼 동작한다.

## A에서 B로 넘기는 값

B가 이미 받는 형식만 쓴다.

```json
{"query": "원질문", "a_result": {"tables": [{"org_id": "301", "table_id": "DT_401Y018", "table_name": "수입물가지수(용도별)"}]}}
```

- `tables`는 A v1의 `b_handoff["tables"]`와 같은 세 필드이며 A가 정한 순서를 유지한다(`to_b_a_result`).
- B는 이 행 전체를 모델 문맥 `selected_tables`에 넣는다. 세 필드만 넘기므로 B 모델에 들어가는 문맥은 기존 B 단독 실행과 같은 형태다. 시험이 이를 확인한다. (2026-10-26: 표 항목에 선택 필드 `focus`가 있으면 모델 문맥에는 `selected_tables`와 별도로 해당 표의 `table_focus`만 더해지며 `selected_tables`는 세 필드를 유지한다. [A→B 표 목록 계약](../../contracts/A_to_B_표목록.md))
- A의 `requirements`, `conditions`, `matched_items`, `scopes`, `confidence`, `evidence`, `issue_codes`는 B에 넘기지 않는다. 결과 state의 `a_v1`에 그대로 남겨 진단에 쓴다.
- A와 B는 같은 저장 카탈로그·메타(`data/catalog/search_catalog.json`, `data/raw/boK_meta_raw.jsonl`)를 읽는다. N5의 `reference/baseline-1006/data` 두 파일과 바이트가 같다.

## 단계와 멈춤 규칙

`build_ab_pipeline(services, a_catalog, a_config=None, a_client=None, data_client=None, renderer=None, through="B")`

| 단계 | 하는 일 | ok가 아닐 때 |
|---|---|---|
| A | `run_a(query, a_config, a_catalog, client=a_client)` | 표가 0개면 `unresolved`(이슈 코드는 A의 마지막 issue, 예 `no_candidates`·`no_requirements`)로 멈추고 B를 부르지 않음 |
| B | B v6 `load → context → select → validate` | B의 stage_outcome 그대로(`needs_confirmation`, `conditions_incomplete` 등) |
| fetch | B v6 `fetch_data` | `data_client`가 없으면 `data_client_missing` |
| C | 주어진 renderer | 없으면 `renderer_not_implemented` |

응답(`response`) 형식과 `period_notices` 노출은 B v6 기존 파이프라인과 같다. A 기본 설정은 client가 없으면 `model="off"`, 있으면 `"on"`이며 `as_of`는 B `Config.as_of`를 쓴다. 이 값의 기본은 조회일이다(2026-10-15 변경, 이전에는 고정 2026-10-01). 이 기준일 공유는 연결본의 기본값일 뿐 팀 계약은 아니다.

## 확인한 것

`tests/test_ab_pipeline.py` 5개(2026-10-15, 새 환경, 네트워크 차단):

1. B로 가는 표가 세 필드만, A 순서대로 남는다.
2. A와 B가 같은 349표 카탈로그를 읽는다.
3. A가 표를 못 고르면 A에서 멈추고 B 모델을 부르지 않는다.
4. CV-AB-19: A(모델 끔)가 `DT_401Y018`을 고르고, B(골든 B 정답 재생)가 계열 2개를 만들고, 합성 데이터로 fetch까지 `ok`. B 모델 문맥의 `selected_tables`가 A 표 세 필드와 같다.
5. A를 모델 켬으로 두고 client를 주지 않으면 실제 호출 없이 `missing_client`로 규칙 대체된다.

참고로 골든 22건에서 A(모델 끔, 당시 as_of 2026-10-01)의 표가 정답 표와 같은 것은 15건이다(CV-AB-04·05·07·10·16·20·22 불일치). 이는 연결본에서 한 번 돌려 본 관측이며 A 평가 기준(417건 집합 일치)과 다르다.

## 이후 과제

- A→B 추가 전달 필드: 요구(scopes)·조건 귀속·`matched_items`·확신도·기준일 중 B가 직접 쓸 것과 형식.
- 여러 표·일부 미해결·fallback일 때 B가 무엇을 할지, A의 low 확신도를 B나 사용자에게 어떻게 보일지.
- 기준일: 연결본은 B `Config.as_of`(조회일 기본)를 A에도 쓴다. 평가용 A는 None 또는 문항별 값을 쓰므로 하나로 정할지는 남아 있다. 서버처럼 오래 떠 있는 프로세스에서 질의마다 날짜를 새로 읽는 방법도 정해야 한다.
- B 패키지 안 옛 A 모듈과 `graphs.py`의 옛 A import를 정리할지(지금은 B 우선으로 유지).
- 실제 B 모델(HCX)·KOSIS 조회로 대표 사례 확인. 호출 범위·횟수는 별도 합의 후.
- C(그래프 렌더러). B v6에는 없다.
- LangGraph 판본: A는 1.2.14에서 검증됐고 이 폴더는 B 잠금 파일의 1.2.12다. 이 폴더의 연결 시험은 1.2.12에서 통과했다.
