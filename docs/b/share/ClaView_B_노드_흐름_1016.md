# ClaView B 노드 흐름

B파트(표 안에서 질문에 맞는 계열과 기간을 고르는 단계)의 노드별 동작을 정리한 문서임. `ClaView_B_노드_흐름_1016.html`의 내용을 그대로 문서화한 것이며, 기준은 2026-10-16 저장소 상태임.

A가 확정한 표 → 상대 기간 확정 → 문맥 만들기 → 모델 선택 → 코드 검증(계층 확장·보정 포함) → 조회 좌표(selections). 노드를 누르면 팝업으로 하는 일, 입력·출력(State 필드), 방식, 중단 코드, 확인 방법을 볼 수 있고, 팝업 안에서 이전·다음 노드로 넘길 수 있다. 내용은 현재 저장소 코드(`stages.py`의 `BSteps`, `graphs.py`의 `build_b_graph`)를 읽고 정리한 것이다.

## 1. 범례

| 태그 | 뜻 |
|---|---|
| 규칙 | 코드가 결정(모델 호출 없음) |
| 모델 | 모델 1회 호출 |
| 중단 가능 | 조건에 걸리면 B가 그 자리에서 끝남 |
| 표마다 반복 | A가 넘긴 표 수만큼 돈다 |

## 2. 전체 흐름

그래프 노드는 4개(`load → context → select → validate`)이고, 아래 세부 단계는 각 노드 안에서 도는 처리를 나눠 적은 것이다.

```
A 결과 → [L] load → [C] context → [M] select → [V] validate → B 결과 → fetch
```

validate 끝에서 남은 표가 있으면 context로 되돌아간다(표마다 반복). 확장 지정을 쓸 수 없으면 validate에서 select로 한 번 되돌아가 같은 표를 다시 묻는다. 중단 조건에 걸리면 그 단계의 outcome(status, issues)으로 B가 끝나고, 파이프라인은 `status != ok`이면 다음 단계(fetch)로 가지 않고 응답한다. 기간 안내(period_notices)는 중단이 아니라 B 결과에 붙는 안내다.

| 그룹 | 이름 | 하는 일 |
|---|---|---|
| L | 불러오기 | A가 확정한 표의 메타데이터를 꺼낸다 |
| C | 문맥 | 표 하나마다 기간·주기를 확정하고 모델에 줄 재료를 만든다 |
| M | 모델 선택 | 코드 목록 안에서 필요한 계열을 고르게 한다 |
| V | 검증 | 모델 출력을 코드로 검증하고 조회 좌표로 확정한다 |

## 3. 노드 목록

총 30개임. 그래프 노드는 `load`, `context`, `select`, `validate` 4개이고, 아래 노드는 각 그래프 노드 안의 세부 처리임.

| ID | 이름 | 그래프 노드 | 태그 |
|---|---|---|---|
| L1 | A 결과 점검 | `load` | 규칙 · 중단 |
| L2 | 표 키 점검 | `load` | 규칙 · 중단 |
| L3 | 메타 읽기 | `load` | 규칙 · 중단 |
| C1 | 기간 표현 감지 | `context` | 규칙 · 표마다 반복 |
| C2 | 주기 정하기 | `context` | 규칙 · 중단 |
| C3 | 상대 기간 계산 | `context` | 규칙 · 중단 |
| C3a | 명시 연도 기간 계산 | `context` | 규칙 · 중단 · 표마다 반복 |
| C4 | 기간 안내 기록 | `context` | 규칙 · 표마다 반복 |
| C4a | 대안 주기 준비 | `context` | 규칙 · 표마다 반복 |
| C5 | 문맥 줄이기 | `context` | 규칙 · 중단 |
| C6 | 스키마 만들기·크기 점검 | `context` | 규칙 · 중단 |
| M1 | 모델 선택 호출 | `select` | 모델 · 중단 · 표마다 반복 |
| V1 | 출력 형식 점검 | `validate` | 규칙 · 중단 |
| V2 | 기간 표기 정리·expand 기본값 | `validate` | 규칙 |
| V2a | 이름으로 말한 값으로 바로잡기 | `validate` | 규칙 |
| V2b | expand 가드 | `validate` | 규칙 |
| V2c | 최하위 값의 펼침 낮춤 | `validate` | 규칙 |
| V3 | 이름으로 말한 하위 선택 | `validate` | 규칙 |
| V4 | 깊이 승격 | `validate` | 규칙 |
| V5 | 미사용 분해 보정 | `validate` | 규칙 |
| V6 | 확장 실행·재시도 | `validate` | 규칙 · 중단 · 재시도 1회 |
| V7 | 빈 선택 점검 | `validate` | 규칙 · 중단 |
| V8 | 기간 확정·범위 점검 | `validate` | 규칙 · 중단 |
| V9 | 명시 기간 대조 | `validate` | 규칙 · 중단 |
| V10 | 비교 기준 기간 확장 허용 | `validate` | 규칙 · 중단 |
| V11 | 이름·단위·라벨 채우기 | `validate` | 규칙 · 중단 |
| V12 | 중복 점검 | `validate` | 규칙 · 중단 |
| V13 | 계산 정의 점검 | `validate` | 규칙 · 중단 |
| V14 | 계열 수 상한 | `validate` | 규칙 · 중단 |
| V15 | 관계 기록·결과 확정 | `validate` | 규칙 · 표마다 반복 |

## 4. 노드 상세

### 4.1 불러오기 (L) — A가 확정한 표의 메타데이터를 꺼낸다

#### L1. A 결과 점검

- **그래프 노드**: `load`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: A가 넘긴 결과에 표가 1개 이상 들어 있는지 확인한다.

**입력**

- a_result (A 결과, tables[]). A·B 연결본에서는 A가 고른 표를 org_id, table_id, table_name 3개 필드로 A 순서대로 넘긴다

**출력**

- 통과 시 다음 단계로 이어진다

**방식**

- a_result.tables를 읽고 비어 있으면 중단한다.
- 구조가 어긋나 있으면(키 없음 등) invalid_a_result로 중단한다.
- A·B 연결본(ab_pipeline)에서는 A가 표를 하나도 고르지 못하면 A 단계에서 멈추고 B를 호출하지 않는다. 그래서 a_result_missing은 B를 단독으로 실행할 때 나타난다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `a_result_missing` | unresolved | tables가 비어 있음 |
| `invalid_a_result` | error | a_result 구조가 맞지 않음 |

**확인 방법**: 이 단계만 겨냥한 단위 테스트는 찾지 못했다. 골든셋 재생(test_b_golden)으로 간접 확인한다.

**코드 위치**: `stages.py · BSteps.load`

#### L2. 표 키 점검

- **그래프 노드**: `load`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 표마다 “org_id:table_id” 키를 만들고, 중복이거나 카탈로그에 없는 표를 걸러낸다.

**입력**

- a_result.tables[] (org_id, table_id)

**출력**

- table_keys (표 키 목록)

**방식**

- 같은 표가 두 번 들어 있으면 중단한다.
- 카탈로그에 없는 표가 있으면 중단한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `invalid_a_result` | error | 키 중복 또는 카탈로그에 없는 표 |

**확인 방법**: test_b_golden의 test_golden_tables_are_all_in_the_catalog가 골든 표의 카탈로그 존재를 확인한다.

**코드 위치**: `stages.py · BSteps.load`

#### L3. 메타 읽기

- **그래프 노드**: `load`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 표마다 공식 메타데이터를 읽어 담고, 표 반복에 쓸 상태를 초기화한다.

**입력**

- table_keys

**출력**

- table_metadata (표 키 → 메타)
- table_index = 0
- selections_by_table = {}

**방식**

- 메타를 읽을 때 분류값마다 상위 코드(UP_ITM_ID)를 따라 상위 경로(path)와 말단 여부(is_leaf)를 만든다. 저장된 표에는 이 값이 모두 남고(확장·라벨·검증에 쓴다), 모델에는 path를 뺀 축소 사본을 보낸다(C5).
- 주기(년·반기·분기·월)와 수록 시작·끝, 항목·분류 축, 단위 정보가 여기서 정리된다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `metadata_missing` | unresolved | 표 메타가 없음 |
| `metadata_incomplete` | unresolved | 주기·항목 등 필수 메타가 비어 있음 |
| `invalid_metadata_*, metadata_code_conflict, metadata_parent_cycle` | error | 메타 구조 오류(축·코드 충돌, 상위 연결 순환 등) |

**확인 방법**: 반기 주기 지원은 test_b_unit(test_half_year_frequency_round_trips 등)이 확인한다.

**코드 위치**: `metadata.py · MetadataStore.get`

### 4.2 문맥 (C) — 표 하나마다 기간·주기를 확정하고 모델에 줄 재료를 만든다

#### C1. 기간 표현 감지

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 표마다 반복(A가 넘긴 표 수만큼 돈다)
- **하는 일**: 질의의 기간 표현을 본다. “최근, 이후, 지금, 현재, 올해, 작년, 최신, 지난” 같은 상대 표현이 있으면 상대 기간(C3), 없고 연도가 적혀 있으면 명시 기간(C3a)을 코드가 계산한다.

**입력**

- query

**출력**

- 상대 표현 여부 (있으면 C3, 없으면 C3a를 시도)

**방식**

- 정규식 RELATIVE_WORDS로 단순 감지한다.
- 상대 기간은 모델이 추측하지 않고 코드가 표 메타 기준으로 확정한다(2026-10 팀 정책).
- 2026-10-13부터 연도를 적은 질문의 기간도 코드가 계산한다(C3a). 모델이 적은 기간은 그 경우 쓰지 않는다.

**중단 코드**: 없음

**확인 방법**: test_b_unit의 test_resolve_relative_period_* 계열, test_b_frequency의 test_explicit_period_is_none_without_years_or_with_relative_wording

**코드 위치**: `stages.py · BSteps.context, RELATIVE_WORDS`

#### C2. 주기 정하기

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 기간을 계산할 주기(Y/H/Q/M)를 정하고, 표가 다른 주기도 가지면 대안 주기 목록을 만든다.

**입력**

- query
- table_metadata[표].periods

**출력**

- 대상 주기 frequency
- 대안 주기 목록 (질문이 주기를 말하지 않고 표에 주기가 여러 개일 때)

**방식**

- 질문이 말한 주기가 있으면 그것을 쓴다. 우선순위: “반기” → “분기” → “월” 또는 “어느/몇/한 달”처럼 월을 뜻하는 표현(“달러” 제외) → “연간/연도별”.
- 말한 주기가 없고 표가 주기를 하나만 가지면 그 주기를 쓴다.
- 말한 주기가 없고 표에 주기가 여러 개이면 가장 긴 주기(Y > H > Q > M)를 기본으로 쓰고 나머지는 대안 주기로 남긴다(2026-10-13 변경. 이전에는 주기를 정하지 못해 중단하는 경우가 있었다).
- 표에 주기가 하나도 없거나(상대 기간) 정한 주기가 표에 없으면 상대 기간 계산이 중단한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `conditions_incomplete` | unresolved | 주기를 정할 수 없음(표에 해당 주기가 없음) |

**확인 방법**: test_default_frequency_is_the_longest_unless_the_question_states_one, test_a_table_with_one_frequency_has_no_alternatives, test_quarter_and_year_table_defaults_to_year (test_b_frequency)

**코드 위치**: `stages.py · decide_frequency (infer_frequency는 기간 대조 V9에서 사용)`

#### C3. 상대 기간 계산

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 표 자신의 최신 수록 시점(앵커)을 기준으로 시작·끝 기간을 계산한다. config.as_of(조회일)는 기간 계산의 기준으로 쓰지 않고, 다음 단계(C4)의 안내 판단에만 쓴다.

**입력**

- query
- C2가 정한 주기
- 표 메타의 주기별 수록 시작·끝

**출력**

- resolved_period 후보 {frequency, start, end}

**방식**

- 주기는 C2가 정한 값을 쓴다.
- “최근/지난 N년·분기·반기·개월”: 앵커를 포함해 거꾸로 N×(주기당 개수)만큼 센다. 5년이면 반기 10개, 분기 20개, 월 60개로 모든 주기가 같은 슬라이딩 윈도우다(2026-10-06 팀 결정).
- “YYYY년 이후”: 그 해 첫 기간부터 앵커까지.
- “올해”: 앵커 연도의 첫 기간부터 앵커까지. “작년”: 앵커 연도−1의 첫 기간부터 그 해 끝까지(앵커를 넘지 않게 자름).
- 표의 최초 수록 시점보다 앞서면 최초 시점으로 자른다(clamp).
- 그 밖의 표현(단독 “지금/현재/최신”, 개수 없는 “지난”)은 해석하지 않고 중단한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `conditions_incomplete` | unresolved | 해석할 수 없는 상대 기간 표현 |
| `metadata_refresh_required` | unresolved | 계산한 시작이 표의 최신 시점보다 뒤 |

**확인 방법**: test_b_unit의 resolve_relative_period 계열, test_b_period_augmented(349개 표 표본 invariant), test_b_period_axis3, test_b_frequency의 test_relative_period_with_no_stated_frequency_uses_the_default_instead_of_stopping

**코드 위치**: `stages.py · resolve_relative_period(query, table, config, freq)`

#### C3a. 명시 연도 기간 계산

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남), 표마다 반복(A가 넘긴 표 수만큼 돈다)
- **하는 일**: 상대 표현 없이 연도가 적힌 질문의 기간을 질문에서 코드가 계산한다. 모델이 적은 기간은 이 경우 쓰지 않는다(2026-10-13).

**입력**

- query
- C2가 정한 주기
- 표 메타의 주기별 수록 범위

**출력**

- resolved_period {frequency, start, end} (연도를 찾지 못했거나 주기가 표에 없으면 만들지 않음 → V9가 모델의 기간을 대조)

**방식**

- 연간: 질문의 첫 연도 ~ 마지막 연도. 분기·월·반기: 연도마다 “n분기/n월/n반기” 문구를 읽고, 문구가 없으면 시작 01, 끝 분기 04 · 월 12 · 반기 02.
- 예: “2008년 1분기부터 2024년 4분기까지” → 200801~202404, “2024년 월별” → 202401~202412.
- 전월·전년 대비 같은 비교 표현은 필요한 기간만큼 시작을 앞당긴다(V10과 같은 폭).
- 계산한 기간은 표의 수록 범위 검사(validate_period)를 거치며, 범위를 벗어나면 같은 오류로 중단한다.
- 계산한 기간을 모델 문맥에 넣지 않는다(C5). 04번에서 넣었을 때 모델이 하위 항목을 과다 선택했다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `invalid_period` | error | 없는 주기이거나 시작이 끝보다 늦음 |
| `metadata_refresh_required` | unresolved | 계산한 기간이 표의 수록 범위를 벗어남 |

**확인 방법**: test_explicit_period_is_computed_from_the_question, test_explicit_period_outside_the_coverage_is_refused, test_the_period_the_model_wrote_does_not_matter_for_stated_years (test_b_frequency), test_stated_period_comes_from_the_question_not_the_model (test_pipeline)

**코드 위치**: `stages.py · resolve_explicit_period`

#### C4. 기간 안내 기록

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 표마다 반복(A가 넘긴 표 수만큼 돈다)
- **하는 일**: 계산한 기간이 조회일(as_of) 기준 기대와 다르면 멈추지 않고 표의 최신 수록 시점으로 조회하되 안내(period_notices)로 남기고, 같은 기간으로 다음 단계를 계속한다.

**입력**

- resolved_period 후보
- config.as_of (기본값은 Config를 만든 날, 곧 조회일. 2026-10-15 변경. 이전에는 고정 2026-10-01)

**출력**

- resolved_period 확정
- period_notices[표] = {table, status, code: period_requires_confirmation, requested_as_of_period, resolved_period, message} (안내가 필요한 경우만. status 값은 이전 이름 needs_confirmation이 남아 있으나 중단이 아님)

**방식**

- 안내가 필요한 경우: clamp가 일어났거나, “작년”이 아니면서 as_of의 해당 주기 기간이 표 최신 시점과 다른 경우.
- “작년”은 닫힌 과거 연도를 말하므로 수록 지연만으로는 안내하지 않는다.
- 이용자에게 진행 여부를 묻지 않고, 확인 답변을 받아 재개하는 단계도 없다(2026-10-06 flow 변경). 안내는 B 결과의 period_notices와 응답의 notices로 전달된다.
- BOK 표는 수록 지연이 흔해 상대 기간 질의 대부분에 안내가 붙는다.
- as_of 기본값은 조회일이다. 날짜를 직접 정하려면 Config(as_of="YYYY-MM-DD") 또는 CLI의 --as-of를 쓴다.
- 조회 기간은 as_of에 따라 달라지지 않고 표의 최신 수록 시점이 정한다. as_of는 안내가 필요한지와 안내 문구의 ‘필요 기간’에만 쓰인다. 예: 월별 표의 최신 수록이 202608이면 조회일이 2026-10-15여도 202509~202608을 조회하고 “202610까지 필요하지만 최신 수록 시점은 202608”이라고 안내한다.
- as_of는 Config를 만들 때 한 번 읽는다. 서버처럼 오래 떠 있는 프로세스는 질의마다 Config를 새로 만들거나 as_of를 넘겨야 날짜가 서버 시작일로 굳지 않는다.

**중단 코드**: 없음

**확인 방법**: test_b_golden의 test_b_resolves_relative_period_notifies_and_proceeds, test_b_unit의 test_resolve_relative_period_gap_needs_confirmation / test_resolve_relative_period_last_year_notice_informs_without_asking, tests/test_query_date.py(as_of 기본값이 조회일임)

**코드 위치**: `stages.py · BSteps.context, resolve_relative_period 후반부, contracts.py · Config.as_of, today_iso`

#### C4a. 대안 주기 준비

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 표마다 반복(A가 넘긴 표 수만큼 돈다)
- **하는 일**: 질문이 주기를 말하지 않고 표에 다른 주기도 있으면, 같은 질문을 다른 주기로 읽은 기간을 모델 호출 없이 미리 계산해 둔다.

**입력**

- 대안 주기 목록 (C2)
- query
- 표 메타

**출력**

- frequency_alternatives[표] = [{frequency, period}]  (그 주기로 기간을 만들 수 있을 때만)

**방식**

- 상대 기간 질문은 resolve_relative_period를, 연도를 적은 질문은 resolve_explicit_period를 대안 주기마다 호출한다.
- 그 주기가 표에 없거나 그 기간을 보여줄 수 없으면(수록 범위 밖 등) 그 주기는 대안으로 내지 않는다.
- V15에서 같은 계열에 대안 기간만 바꾼 selections를 붙여 b_result.frequency_alternatives로 낸다. 사용자가 주기를 바꿀 때 모델을 다시 부르지 않게 하기 위한 준비이며, 이를 보여주는 화면과 조회 흐름은 아직 없다.

**중단 코드**: 없음

**확인 방법**: test_cv_ab_20_answers_annually_and_offers_quarterly_and_monthly, test_alternatives_are_not_offered_when_the_question_states_the_frequency (test_b_frequency)

**코드 위치**: `stages.py · BSteps.context, graphs.py · BState.frequency_alternatives`

#### C5. 문맥 줄이기

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 표 메타를 모델 문맥으로 만들고, 너무 크면 값이 많은 축의 후보만 줄인다. 모델에는 저장된 표가 아니라 상위 경로(path)를 뺀 축소 사본을 보낸다.

**입력**

- query
- table_metadata[표]
- config.max_b_context_chars=120000 (공통 max_context_chars=160000과 작은 쪽), max_axis_candidates=80, compact_model_context=True

**출력**

- selection_context (table_metadata: 모델용 축소 사본, pruned_axes, as_of, period_policy, resolved_period: 상대 기간 질문에서만 값이 있고 연도를 적은 질문은 null)

**방식**

- 스키마를 포함한 저장 형식(축소 전) 크기+질의+2,000자가 한도 이내면 줄이지 않는다. 가지치기를 시작하는 기준은 축소한 크기가 아니라 저장 형식 크기다.
- 한도를 넘으면 값이 80개를 넘는 축만 줄인다. 남기는 순서: ① 질문의 단어가 값 이름에 들어 있는 정도(조사가 붙은 단어는 앞부분으로 대조, 숫자로 시작하는 토큰 제외) ② 이름의 2-gram 겹침 ③ 상위 경로의 2-gram 겹침 ④ 코드순. 남긴 값의 상위(조상) 값도 함께 남긴다.
- 줄여야 하는 상황에서 질의에 “모든 항목/분류” 또는 “업권·상품·지역·산업·업종·품목별”이 있으면 후보를 줄일 수 없다고 보고 중단한다.
- 모델에 보내는 표(model_table): 값마다 name(공식 이름), parent_code, is_leaf(모든 값에 true/false), unit(있을 때만)을 보낸다. path와 빈 unit은 보내지 않는다. 같은 이름이 한 축에서 2회 이상이면 그 값에만 under(상위 이름을 ' > '로 이은 문자열)를 붙이며, 줄이기 전 축 전체로 계산한다. 값 코드는 저장 표와 같아 모델의 답을 검사하는 방식은 그대로다.
- 효과(2026-10-16 측정): 골든 22건의 모델 문맥 합계가 655,077자에서 260,576자(약 40%)로, 전체 348개 표(표+스키마)는 8,093,888자에서 3,886,804자(약 48%)로 줄었다. 형식은 CV-AB-03 실험으로 정했다. is_leaf를 false인 값에만 쓰면 하위 값을 골랐고, 모든 값에 쓰면 통과했다. under를 빼면 다른 가지를 골랐다. 원인은 확인하지 못했고 변형당 1회 결과다.
- 한도 120,000자는 호출 한도의 경계가 아니라 가지치기를 시작하는 기준이다. 429의 원인은 글자 수가 아니라 분당 토큰 한도(현재 키 60,000, 입력과 최대 출력을 합산)로 보인다. 축소 전 CV-AB-04 요청(본문 147,564자)은 429, 축소한 같은 요청(46,556자)은 200이었다. 가득 찬 한도에서 직접 확인하지는 못했다.
- 가지치기는 크기뿐 아니라 과선택도 막는 것으로 보인다. CV-AB-04·05를 가지치기 없이 보내면 0/2였고(04는 서비스·본원소득·이전소득을 빼고 하위 5개를 선택, 05는 하위 13개를 더함) 가지치기를 켜면 통과했다. 이 효과만 분리한 실험은 아니며 질문당 1회다.
- 모델 문맥의 resolved_period는 상대 기간 질문에서만 채운다. 연도를 적은 질문은 기간을 코드가 정했으므로 null로 보낸다(상태에는 계산한 기간을 유지). 근거: CV-AB-04에서 값을 보여줬을 때 3회 모두 14개를 골라 틀렸고 null일 때 3회 모두 5개로 맞았다. 원인은 확인하지 못했다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `broad_scope_context_incomplete` | unresolved | 넓은 범위를 요청했는데 문맥을 줄여야 하는 경우 |

**확인 방법**: test_build_b_context_prunes_oversized_axis_but_keeps_ancestors, test_build_b_context_refuses_broad_scope_pruning, test_question_words_keep_the_named_value_when_pruning, test_large_golden_tables_are_pruned_under_the_default_limit, test_model_table_drops_what_the_model_can_derive, test_model_table_writes_is_leaf_on_every_value_and_matches_the_stored_flag, test_same_name_values_keep_the_official_name_and_say_what_tells_them_apart, test_pruned_axis_keeps_the_ancestors_of_the_whole_axis, test_pruning_is_decided_on_the_stored_table_as_before

**코드 위치**: `stages.py · build_b_context, b_limit, term_score, model_table, display_names`

#### C6. 스키마 만들기·크기 점검

- **그래프 노드**: `context`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 모델이 고를 수 있는 값만 허용하는 출력 스키마를 만든다. 질문에 분해 표현이 있을 때만 expand를 요구하는 스키마를 쓰고, 문맥과 스키마의 합계 크기를 점검한다.

**입력**

- 줄인 table_metadata
- query
- config.max_selections=100

**출력**

- active_table
- selection_context
- model_schema
- expand_enabled (분해 표현 여부)
- resolved_period
- period_notices
- frequency_alternatives

**방식**

- item_id는 표의 항목 코드, 분류 축마다 코드, period.frequency는 표가 가진 주기만 허용(enum)한다. selections는 최대 100개.
- 분해 표현(“~별”, 세부·하위·구성·내역 등)이 있으면 selection마다 expand(축별 none / children / leaves)를 필수로 요구한다. “월별·분기별”처럼 시간 단위인 “~별”은 분해 표현이 아니다.
- 분해 표현이 없으면 expand가 없는 스키마를 쓴다. 이유: expand 스키마·프롬프트가 분해와 무관한 질문의 답(종료월, 하위 확장 등)에도 영향을 주었다.
- 모델에 보내는 문맥(축소 표)이, 그리고 문맥+스키마가 B 한도 120,000자를 넘으면 중단한다. 이 검사는 축소한 크기로 한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `context_too_large` | unresolved | 문맥+스키마가 한도 초과 |

**확인 방법**: test_build_b_schema_enumerates_only_table_codes, test_model_schema_has_expand_modes_per_axis_but_plain_schema_does_not, test_plain_question_gets_plain_schema_and_prompt_without_expand, test_breakdown_question_gets_expand_schema_and_prompt, test_breakdown_wording_is_recognised

**코드 위치**: `stages.py · BSteps.context, build_b_schema, asks_for_breakdown, b_bounded`

### 4.3 모델 선택 (M) — 코드 목록 안에서 필요한 계열을 고르게 한다

#### M1. 모델 선택 호출

- **그래프 노드**: `select`
- **태그**: 모델(모델 1회 호출), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남), 표마다 반복(A가 넘긴 표 수만큼 돈다)
- **하는 일**: 현재 표 하나에 대해 모델을 한 번 불러, 필요한 계열의 selection(항목·분류 코드·주기·기간, 필요하면 확장 방식)을 고르게 한다. 확장 오류가 나면 같은 표로 한 번 더 부른다.

**입력**

- selection_context (재시도 때는 retry_feedback이 추가됨)
- model_schema, config.max_b_output_tokens=6144

**출력**

- model_output {selections:[{item_id, dimensions{축:코드}, period{frequency,start,end}, expand{축:none|children|leaves}}]}  (expand는 분해 표현이 있는 질문에만)

**방식**

- 프롬프트: 항목 하나와 필수 축마다 코드 하나를 고른다. 부모·총계도 공식 값이면 선택할 수 있다. 이름과 parent_code로 이어지는 상위 값을 함께 읽고, 같은 이름의 값이 여럿이면 under에 적힌 상위 이름으로 구분한다. 근거가 부족하면 빈 배열을 반환한다. 단위·이름·수치·계산식은 만들지 않는다. resolved_period가 주어지면(상대 기간 질문) period에 그대로 쓴다.
- 분해 표현이 있는 질문에는 expand 규칙 블록이 붙은 프롬프트(B_PROMPT)를, 없으면 그 블록이 없는 프롬프트(B_PROMPT_PLAIN)를 쓴다. expand 규칙: 기본은 none, 구성·세부 내역을 나눌 때만 사용, 나누려는 축의 상위 값을 고르고 그 축만 확장, “~별”은 기본 leaves, 값 자신은 확장에 포함되지 않음.
- 스키마 구조화 출력, 최대 출력 토큰은 config.max_b_output_tokens(기본 6144, 이전 8192). HCX의 분당 토큰 한도에는 입력과 이 값이 함께 계산된다. 한도에서 잘린 답은 output_truncated로 실패하며 쓰지 않는다. 표가 여러 개면 표마다 한 번씩 호출한다.
- 실제 모델은 expand 규칙을 항상 따르지는 않아 V3~V5가 질문 표현으로 보정한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `hcx_key_missing` | unresolved | 모델 API 키 없음 |
| `hcx_transport_error, hcx_http_<상태>, hcx_service_error, hcx_invalid_response` | error | 호출·응답 오류 |
| `output_truncated, unexpected_finish_reason, invalid_model_output` | error | 출력 잘림·비정상 종료·형식 오류 |

**확인 방법**: 이 저장소의 시험은 정답을 재생하는 ScriptedModel을 쓰므로 이 단계의 실제 모델 정확도는 시험으로 측정되지 않는다. 실제 모델 확인은 scripts/b_part/verify_expand_hcx.py로 따로 수행하며(키 필요, 외부 호출) 결과는 검증 기록 문서에 있다. 출력 상한은 test_b_asks_for_the_configured_output_cap으로 확인한다.

**코드 위치**: `stages.py · BSteps.select, B_PROMPT, B_PROMPT_PLAIN, models.py, contracts.py · Config.max_b_output_tokens`

### 4.4 검증 (V) — 모델 출력을 코드로 검증하고 조회 좌표로 확정한다

#### V1. 출력 형식 점검

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 모델 출력이 스키마(허용 코드, 필수 필드)를 지키는지 확인한다.

**입력**

- model_output
- model_schema (expand 포함 여부는 C6가 정함)

**출력**

- 통과 시 다음 점검으로 이어진다

**방식**

- JSON Schema로 검증한다. 스키마가 허용 코드만 열어 두었으므로 표에 없는 코드를 고른 출력은 여기서 막힌다.
- expand를 요구한 질문에서 expand가 빠진 행도 여기서 막힌다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `invalid_model_output` | error | 스키마 위반 |

**확인 방법**: test_row_without_expand_is_rejected, test_selected_unit_conflict_rejected_through_canonicalize 등 canonicalize 단위 테스트가 이 경로를 지난다.

**코드 위치**: `models.py · check_output`

#### V2. 기간 표기 정리·expand 기본값

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: 모델이 쓴 기간 표기를 표 형식으로 정리하고, expand가 없는 행에 전 축 none을 채운다.

**입력**

- model_output

**출력**

- 기간 표기가 정리되고 expand가 채워진 행

**방식**

- “2024-01”, “2024.1”, “2024/1”처럼 구분 기호가 있는 월·분기·반기 값을 “202401”로 바꾼다. 이미 표 형식인 값, 연간 값, 해석할 수 없는 값은 그대로 두고 기간 검증(V8)이 처리한다. 알림은 남기지 않는다.
- 분해 표현이 없는 질문은 expand 없는 스키마로 가므로 전 축 none을 채우기만 한다.

**중단 코드**: 없음

**확인 방법**: test_model_period_text_is_normalised_to_table_format, test_cv_ab_19_period_text_with_hyphen_gives_the_2_golden_series, test_with_default_expand_fills_only_missing_rows

**코드 위치**: `stages.py · normalize_model_periods, with_default_expand`

#### V2a. 이름으로 말한 값으로 바로잡기

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: 모델이 고른 값이 질문에 쓰인 이름과 어긋나면 질문이 말한 값으로 바꾼다. 같은 입력에서 같은 오답이 반복되어 모델에 다시 묻지 않고 코드 규칙으로 처리한다(2026-10-13).

**입력**

- 기간 표기·expand가 정리된 행
- query
- 표 메타

**출력**

- 고른 값이 바뀐 행 (같아진 행은 병합)
- expand_notices (reason: query_names_longer_value / query_names_parent_value / query_excludes_named_value, applied: named_value, selected)

**방식**

- expand가 none인 축의 값에만 적용한다. 값마다 아래 규칙을 차례로 보고 처음 맞는 것을 쓴다.
- 긴 이름 규칙: 고른 이름이 다른 한 값 이름의 일부이고 그 긴 이름(3자 이상, [참고] 제외)이 질문에 그대로 있으며 짧은 이름이 따로 나오지 않으면 긴 값으로 바꾼다. 예: 대외금융자산 → 순대외금융자산(CV-AB-12).
- 상위 항목 규칙: 고른 값의 조상 중 이름(3자 이상)이 질문에 나오고 고른 이름이 그 이름으로 시작하며 고른 이름 전체는 질문에 없으면, 가장 가까운 그 조상으로 바꾼다. 질문이 그 이름을 포함한 더 긴 값을 직접 말하면 바꾸지 않는다. 예: 원재료국내출하 → 원재료(CV-AB-21).
- 제외 표현 규칙: 고른 이름이 “~이외”이고 그 이름과 앞부분이 질문에 없으며, “X를 뺀·빼고·제외한” 표현이 가리키는 “X이외” 값(이름 5자 이상)이 하나뿐이면 그 값으로 바꾼다. 예: 식료품이외 → 신선식품이외(CV-AB-17).
- 근거와 한계: 골든 22건의 정답을 넣어도 바뀌는 값이 0건이다. 이름이 조금이라도 겹치면 바꾸는 넓은 규칙은 골든 5·17번을 망가뜨려 쓰지 않았다. 이 세 규칙은 실패한 질문을 보고 만들었고 다른 질문에서의 효과는 확인하지 못했다.
- 분해 표현이 없는 질문에서도 동작하므로 expand_notices가 생길 수 있다.

**중단 코드**: 없음

**확인 방법**: test_cv_ab_12_wrong_value_is_corrected_to_the_golden_series, test_cv_ab_21_sub_types_are_replaced_by_the_named_values, test_cv_ab_17_wrong_except_value_is_replaced, test_reverse_check_never_changes_a_golden_selection, test_named_parent_rule_needs_a_3_character_name_and_a_name_prefix

**코드 위치**: `stages.py · correct_to_named_value, named_longer_value, named_parent_value, excluded_named_value`

#### V2b. expand 가드

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: 분해 표현이 없는데 expand가 있으면 none으로 낮춘다.

**입력**

- expand가 채워진 행
- query

**출력**

- expand가 낮춰진 행
- expand_notices (reason: no_breakdown_wording)

**방식**

- 안전장치다. 분해 표현이 없는 질문은 expand 없는 스키마로 가므로 정상 경로에서는 동작하지 않는다.
- expand가 있는데 분해 표현이 없으면 모두 none으로 낮추고 알림을 남긴다. 낮춘 결과 같아진 행은 하나로 합친다. 모델이 직접 쓴 중복 행은 합치지 않는다.

**중단 코드**: 없음

**확인 방법**: test_guard_lowers_expand_without_breakdown_wording_and_reports_it, test_guard_merges_rows_that_collapse_into_the_same_selection, test_guard_does_not_hide_the_models_own_duplicates

**코드 위치**: `stages.py · apply_expand_guard`

#### V2c. 최하위 값의 펼침 낮춤

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: 하위가 없는(최하위) 값에 children/leaves가 지정되면 none으로 낮춰 그 값 자신을 선택한다(2026-10-13).

**입력**

- expand가 있는 행
- 표 메타(is_leaf)

**출력**

- expand가 none으로 낮춰진 행 (같아진 행은 병합)
- expand_notices (reason: expand_on_bottom_level_value)

**방식**

- 모델이 이미 정확한 값을 골랐는데 습관적으로 펼침을 붙인 경우(예: “대기업대출·중소기업대출로 나눠”)를 다룬다. 의도한 계열은 그 값 자신이다.
- 이전에는 expand_target_has_no_children 오류로 재시도되었다(CV-AB-07). 같아진 행만 합치고 모델이 직접 쓴 중복 행은 합치지 않는다.

**중단 코드**: 없음

**확인 방법**: test_expand_on_a_bottom_level_value_becomes_none_and_is_reported, test_leaf_lowering_merges_rows_it_makes_identical_but_not_the_models_duplicates, test_cv_ab_07_children_on_two_bottom_level_values_give_the_2_golden_series

**코드 위치**: `stages.py · lower_expand_on_leaves`

#### V3. 이름으로 말한 하위 선택

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: 확장 대상 값의 바로 아래 값 중 질문이 이름으로 말한 것이 2개 이상이면, 확장하지 않고 그 하위 값만 선택한다.

**입력**

- expand가 채워진 행
- query
- 표 메타

**출력**

- 확장 지정이 이름 선택으로 바뀐 행 (expand none)
- expand_notices (reason: query_names_children, selected)

**방식**

- 이름은 정규화 후 2자 이상이고 질문에 그대로 들어 있어야 한다. 바로 아래 값만 본다(더 깊은 값의 이름은 세지 않음). [참고]로 시작하는 값은 후보에서 제외한다.
- 이름이 하나만 나오면 바꾸지 않는다.
- 예: “시설자금·운전자금·특별자금 용도별로” → 총대출금 아래 6개 중 3개만 선택한다. 모델이 이미 같은 행을 썼다면 중복으로 만들지 않는다.
- 프롬프트로도 같은 내용을 요구했으나 모델이 따르지 않아 코드 규칙으로 처리한다.

**중단 코드**: 없음

**확인 방법**: test_named_children_replace_the_expand, test_one_name_or_deeper_names_do_not_count, test_named_children_keep_the_models_own_duplicates, test_cv_ab_13_named_funds_give_the_3_golden_series

**코드 위치**: `stages.py · select_named_children`

#### V4. 깊이 승격

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: “~별” 요청에서 모델이 children을 냈고 깊이를 한정하는 말이 없으면 leaves(최하위 전체)로 올린다.

**입력**

- 이름 선택 후의 행
- query
- 표 메타

**출력**

- children이 leaves로 바뀐 행
- expand_notices (reason: split_by_wording_all_levels)

**방식**

- 조건: 시간 단위가 아닌 “~별” 표현이 있고, “한 단계·1단계·첫 단계·한 층·바로 아래·직접 하위·직하위·대분류·중분류·소분류”가 없다.
- 바로 아래 값이 모두 최하위면 바꾸지 않는다. “세부·하위”만 있고 “~별”이 없으면 바꾸지 않는다.
- 예: “업권별” → 가계신용 최하위 14개(팀 결정: ‘업권별’은 최하위 전체).

**중단 코드**: 없음

**확인 방법**: test_children_become_leaves_for_a_split_by_question, test_stage_limiting_wording_keeps_children, test_children_that_are_already_the_bottom_level_are_not_reported, test_cv_ab_08_leaves_of_household_credit_give_the_14_golden_series

**코드 위치**: `stages.py · promote_children_to_leaves`

#### V5. 미사용 분해 보정

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음)
- **하는 일**: “~별” 요청인데 모델이 expand를 하나도 쓰지 않았고, 그 단어와 맞는 축의 값이 하나뿐이며 하위가 있으면 그 값을 확장한다.

**입력**

- 승격 후의 행
- query
- 표 메타(축 이름, is_leaf)

**출력**

- 확장 지정이 추가된 행
- expand_notices (reason: split_axis_not_expanded)

**방식**

- 조건: 모든 축이 none. 질문의 “~별” 단어(끝 2자 이상)가 공식 축 이름에 포함되는 축이 정확히 하나. 그 축에서 모든 행이 같은 값이고 그 값이 최하위가 아니다.
- 동작: 그 값을 leaves로 확장(깊이 한정어가 있으면 children). 예: “지역별” + 지역코드별 축의 총계 → 8개 지역.
- 같은 축에서 값을 둘 이상 나열했거나, 최하위 값을 골랐거나, 단어가 축 이름과 맞지 않으면 건드리지 않는다. 골든 22건의 정답을 넣어도 발동하지 않는다.
- 한계: “총계와 지역별을 함께” 같은 질문에서 모델이 총계만 냈다면 총계가 지역 값으로 바뀐다.

**중단 코드**: 없음

**확인 방법**: test_unused_split_expands_the_single_parent_value_of_the_named_axis, test_unused_split_leaves_other_cases_alone, test_cv_ab_10_total_without_expand_gives_the_8_golden_series, test_golden_answers_are_never_changed_by_the_repair

**코드 위치**: `stages.py · repair_unused_split, split_axes`

#### V6. 확장 실행·재시도

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남), 재시도 1회
- **하는 일**: children·leaves를 실제 계열로 펼친다. 확장 지정을 쓸 수 없으면 모델에 한 번 다시 묻는다.

**입력**

- 보정을 마친 행
- 표 메타(parent_code, is_leaf, path)

**출력**

- 확장된 행 (expand 없는 기존 형태)
- 재시도 때: retry_feedback {error, message, previous_answer, expandable_values}

**방식**

- children은 바로 아래 값, leaves는 모든 후손 중 최하위 값이며 값 자신은 포함하지 않는다. 순서는 공식 메타 순서다. [참고]로 시작하는 값(구 M2 등 비교용 줄)은 제외한다.
- 한 selection에서 확장하는 축은 하나여야 한다.
- expand_target_has_no_children(하위가 없는 값을 확장) 또는 expand_multiple_axes(두 축 확장)이면 retry_feedback을 붙여 같은 표로 모델을 1회 다시 부른다(select로 되돌아감, context는 다시 만들지 않음). 다시 실패하면 그 코드로 중단한다. 재시도 횟수는 Config.max_expand_retries(기본 1)다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `expand_target_has_no_children` | error | 하위가 없는 값에 확장 지정 (재시도 후에도 실패) |
| `expand_multiple_axes` | error | 한 selection에서 두 축 이상 확장 (재시도 후에도 실패) |

**확인 방법**: test_children_mode_returns_direct_children_only_in_metadata_order, test_leaves_mode_returns_all_descendant_leaves, test_self_plus_children_is_two_selections, test_unusable_expand_is_retried_once_with_feedback, test_second_unusable_expand_ends_with_the_error, test_reference_lines_are_not_part_of_children_or_leaves, test_cv_ab_09_children_of_m2_give_the_14_golden_series

**코드 위치**: `stages.py · expand_selections, expand_feedback, BSteps.validate`

#### V7. 빈 선택 점검

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 모델이 근거 부족으로 selections를 비웠으면 조건 부족으로 멈춘다. 확장 후의 행은 expand 없는 기존 스키마로 다시 점검한다.

**입력**

- 확장된 selections

**출력**

- 통과 시 행(selection)마다 V8~V12가 적용된다

**방식**

- selections가 비어 있으면 중단한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `conditions_incomplete` | unresolved | 고른 계열이 없음 |

**확인 방법**: test_canonicalize_selections_rejects_empty_selections

**코드 위치**: `stages.py · canonicalize_selections`

#### V8. 기간 확정·범위 점검

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 기간을 확정하고, 표의 수록 범위 안인지 확인한다.

**입력**

- row.period (모델이 쓴 기간)
- resolved_period (C 단계가 확정한 기간: 상대 기간 또는 연도를 적은 질문)
- 표의 주기별 수록 시작·끝

**출력**

- period (확정된 기간)

**방식**

- resolved_period가 있으면 모델이 쓴 기간 대신 그 값을 그대로 쓰고, 질의 문구 대조(V9)는 건너뛴다. 연도를 적은 질문도 C3a가 기간을 정하므로 모델이 쓴 기간은 쓰이지 않는다.
- 주기가 표에 있는지, 시작이 끝보다 늦지 않은지, 표의 수록 범위 안인지 확인한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `invalid_period` | error | 없는 주기이거나 시작이 끝보다 늦음 |
| `metadata_refresh_required` | unresolved | 표의 수록 범위를 벗어남 |

**확인 방법**: test_validate_period_rejects_out_of_table_range, test_validate_period_accepts_half_year_table

**코드 위치**: `stages.py · validate_period`

#### V9. 명시 기간 대조

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: C 단계가 기간을 확정하지 못한 질의(연도가 있어도 정한 주기가 표에 없는 경우 등)에서, 모델이 쓴 기간이 질의에 적힌 연도·주기·분기/월과 맞는지 대조한다.

**입력**

- query
- period
- 표 메타

**출력**

- 통과 시 period 유지

**방식**

- 연도를 적은 보통의 질문은 C3a가 기간을 정해 이 단계를 건너뛴다. 아래는 그렇지 않은 질의에만 적용된다.
- 질의에 상대 표현이 남아 있거나 연도가 하나도 없으면 중단한다.
- 끝 연도와 시작 연도가 질의의 마지막·첫 연도와 같아야 한다.
- 질의가 말한 주기와 period.frequency가 같아야 한다.
- 분기·월·반기 표는 “n분기/n월/n반기” 문구에 맞는 시작·끝이어야 한다. 문구가 없으면 시작은 01, 끝은 분기 04 · 월 12 · 반기 02로 본다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `conditions_incomplete` | unresolved | 연도 없음 또는 해석 못한 상대 표현 |
| `explicit_period_mismatch` | error | 연도·분기/월이 질의와 다름 |
| `explicit_frequency_mismatch` | error | 주기가 질의와 다름 |

**확인 방법**: test_validate_period_rejects_mismatched_explicit_years, test_validate_period_accepts_explicit_matching_years, test_validate_period_rejects_relative_wording_even_with_a_year

**코드 위치**: `stages.py · validate_period`

#### V10. 비교 기준 기간 확장 허용

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 전월·전년 대비처럼 비교 계산에 앞선 기간이 필요한 질의는, 시작이 정확히 그만큼 앞당겨진 경우만 허용한다.

**입력**

- query
- period.start

**출력**

- 허용되면 확장된 period 유지

**방식**

- 전월·전기·전분기·전반기 대비, “가장 크게 변”: 1기간.
- 전년 대비·전년보다·전년동기, 작년 대비: 연 1, 반기 2, 분기 4, 월 12기간.
- 시작이 (질의 시작 − 확장 폭)과 정확히 같을 때만 허용하고, 확장이 있으면 분기·월·반기 세부 대조는 생략한다.
- 기여도·연쇄가중은 계산 방법론 문제여서 기간 확장 대상이 아니다(V13에서 다룬다).
- 연도를 적은 질문은 C3a가 같은 폭으로 시작을 앞당겨 계산하므로, 이 점검은 C3a가 기간을 만들지 못한 질의에만 쓰인다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `explicit_period_mismatch` | error | 확장 폭이 맞지 않음 |

**확인 방법**: test_detect_baseline_extension_by_calc_type, test_canonicalize_selections_extends_baseline_for_mom_query. 골든 CV-AB-18·20이 정답과 일치한다.

**코드 위치**: `stages.py · detect_baseline_extension`

#### V11. 이름·단위·라벨 채우기

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 분류 이름, 단위, 라벨을 메타에서 코드로 채운다. 모델은 이 값을 만들지 않는다.

**입력**

- row.item_id
- row.dimensions (축:코드)
- 표 메타

**출력**

- selection {series_id(임시), label, org_id, table_id, item_id, dimensions{축:{code,name}}, period, unit}

**방식**

- dimensions의 이름은 메타의 공식 값 이름을 쓴다.
- label은 항목명에 분류값 이름들을 “ / ”로 이어 붙인다. 같은 이름이 한 축에 2회 이상 나오면 그 값들의 라벨에만 상위 이름을 한 단계씩 앞에 붙여 구분한다(예: 판매신용 > 여신전문기관). 이름이 한 번만 나오면 그대로 둔다.
- unit은 항목·분류값에 붙은 단위를 쓰고, 없으면 표 단위를 쓴다. 서로 다른 단위가 섞이면 중단한다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `unit_conflict` | unresolved | 한 계열에 서로 다른 단위가 섞임 |

**확인 방법**: test_selected_unit_conflict_rejected_through_canonicalize, test_canonicalize_selections_happy_path, test_duplicate_names_get_parent_names_one_level_at_a_time, test_label_uses_disambiguated_name_but_dimension_name_stays_official

**코드 위치**: `stages.py · canonicalize_selections, display_names, metadata.py · selected_unit`

#### V12. 중복 점검

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 같은 계열을 두 번 고르지 않았는지 확인한다.

**입력**

- selection 목록

**출력**

- 통과 시 canonicalized rows

**방식**

- (기관, 표, 항목, 분류 조합, 주기, 시작, 끝)이 같으면 중복으로 본다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `duplicate_selection` | error | 중복 계열 |

**확인 방법**: test_canonicalize_selections_rejects_duplicates

**코드 위치**: `stages.py · selection_key`

#### V13. 계산 정의 점검

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 기여도·연쇄가중처럼 계산 방법론이 걸린 질의는, 라벨에 이미 그 지표가 들어 있는 계열만 인정한다.

**입력**

- query
- canonicalized rows

**출력**

- 통과 시 다음 단계로

**방식**

- 질의에 “기여도|연쇄가중”이 있으면 모든 계열 라벨에 “증감률|성장률|기여도” 중 하나가 있어야 한다.
- B가 계산식을 만들어 주지 않고, 표가 공식으로 제공하는 값만 선택한다는 원칙이다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `calculation_definition_required` | unresolved | 공식 값으로 제공되는 계열을 찾지 못함 |

**확인 방법**: 전용 단위 테스트는 찾지 못했다. 골든셋 재생으로 간접 확인한다.

**코드 위치**: `stages.py · BSteps.validate`

#### V14. 계열 수 상한

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 중단 가능(조건에 걸리면 B가 그 자리에서 끝남)
- **하는 일**: 확장 후 누적 계열 수가 상한(기본 30)을 넘으면 조회하지 않고 이용자 확인을 요청한다.

**입력**

- selections_by_table + 이번 표의 행
- config.max_expanded_selections=30

**출력**

- 통과 시 다음 단계로
- 초과 시 result {message, selection_count, limit, candidate_labels} (selections 없음)

**방식**

- 표가 여러 개이면 앞선 표까지의 계열을 합쳐 센다.
- 초과하면 status needs_confirmation으로 B가 끝나고, 파이프라인은 조회 없이 응답한다(응답의 confirmation에 result가 담김).
- 상한은 Config.max_expanded_selections로 바꾼다.

**중단 코드**

| 코드 | status | 조건 |
|---|---|---|
| `selection_count_requires_confirmation` | needs_confirmation | 확장 후 계열 수가 상한 초과 (result에 후보 라벨 목록 포함) |

**확인 방법**: test_pipeline_over_cap_needs_confirmation_and_returns_no_selections, test_pipeline_at_cap_is_ok, test_default_cap_is_30

**코드 위치**: `stages.py · BSteps.validate, NeedsConfirmation`

#### V15. 관계 기록·결과 확정

- **그래프 노드**: `validate`
- **태그**: 규칙(코드가 결정, 모델 호출 없음), 표마다 반복(A가 넘긴 표 수만큼 돈다)
- **하는 일**: 남은 표가 있으면 다음 표의 C로 돌아가고, 마지막 표면 전체 계열을 합쳐 상하위 관계와 함께 B 결과를 낸다.

**입력**

- selections_by_table
- table_index
- table_keys
- period_notices
- expand_notices
- frequency_alternatives

**출력**

- b_result = {selections, relations, period_notices?, expand_notices?, frequency_alternatives?} (status ok)
- series_id: S001, S002, … (합친 순서대로 코드가 부여)
- trace_ref

**방식**

- 표별 결과를 selections_by_table에 쌓고 table_index를 1 올린다. 확장 오류 재시도 횟수와 안내는 표마다 초기화한다.
- 마지막 표면 표 순서대로 이어 붙이고 series_id를 새로 매긴다.
- 항목·기간이 같고 정확히 한 축에서만 달라서 그 축의 두 값이 조상-자손인 계열 쌍을 relations에 기록한다(계열은 지우지 않고 status도 ok 유지).
- period_notices, expand_notices는 있을 때만 붙인다. 응답으로 전달되는 것은 period_notices뿐이고 relations, expand_notices, frequency_alternatives는 B 결과에만 있다.
- 대안 주기가 있으면 같은 계열에 대안 기간만 바꾼 selections를 frequency_alternatives(표, 주기, 기간, selections)로 붙인다. 응답·fetch에는 연결하지 않았다.
- 결과는 fetch가 그대로 받아 KOSIS 조회 좌표로 쓴다.

**중단 코드**: 없음

**확인 방법**: test_b_golden이 골든 22건을 재생해 확인한다. test_relations_record_parent_child_pairs, test_pipeline_expands_and_reports_relations, test_pipeline_without_hierarchy_has_empty_relations

**코드 위치**: `stages.py · BSteps.validate, find_relations, graphs.py · build_b_graph`

## 5. 중단 코드 한눈에 보기

B가 `outcome`의 `issues[0].code`로 돌려주는 값이다. 코드에서 확인한 것만 적었다.

| 코드 | status | 발생 단계 | 뜻 |
|---|---|---|---|
| `a_result_missing` | unresolved | L1 | A 결과에 표가 없음 |
| `invalid_a_result` | error | L1·L2 | A 결과 구조 오류 / 표 키 중복 / 카탈로그에 없는 표 |
| `metadata_missing, metadata_incomplete` | unresolved | L3 | 표 메타가 없거나 필수 항목이 비어 있음 |
| `conditions_incomplete` | unresolved | C2·C3·V7·V9 | 주기·상대 기간을 해석할 수 없음 / 고른 계열 없음 / 질의에 연도 없음 |
| `metadata_refresh_required` | unresolved | C3·C3a·V8 | 요청 기간이 표의 수록 범위 밖 |
| `broad_scope_context_incomplete` | unresolved | C5 | 넓은 범위 요청인데 문맥을 줄여야 함 |
| `context_too_large` | unresolved | C6 | 모델에 보내는 문맥(축소 표)+스키마가 B 한도 초과 |
| `hcx_*, output_truncated, unexpected_finish_reason` | error / unresolved(키 없음) | M1 | 모델 호출 오류 |
| `invalid_model_output` | error | M1·V1 | 모델 출력이 스키마를 어김 |
| `expand_target_has_no_children` | error | V6 | 하위가 없는 값에 확장 지정 (재시도 후에도) |
| `expand_multiple_axes` | error | V6 | 한 selection에서 두 축 이상 확장 (재시도 후에도) |
| `invalid_period` | error | C3a·V8 | 없는 주기 또는 시작이 끝보다 늦음 |
| `explicit_period_mismatch` | error | V9·V10 | 질의의 연도·분기/월과 기간이 다름 |
| `explicit_frequency_mismatch` | error | V9 | 질의의 주기와 다름 |
| `unit_conflict` | unresolved | V11 | 한 계열에 서로 다른 단위 |
| `duplicate_selection` | error | V12 | 같은 계열 중복 선택 |
| `calculation_definition_required` | unresolved | V13 | 기여도·연쇄가중 질의에서 공식 값 계열이 없음 |
| `selection_count_requires_confirmation` | needs_confirmation | V14 | 확장 후 계열 수가 상한 초과 — 확인 요청, 조회하지 않음 |

## 6. 기준과 주의

기준: 저장소 현재 상태(2026-10-16, 기간 확정·기본 주기·이름 보정 반영본에 기준일 as_of를 조회일 기본값으로 바꾼 변경과, 모델에 보내는 표 축소(path 제거·under·is_leaf 전체 표기)·출력 상한 6144를 반영). 설계 초안이 아니라 구현된 동작을 적었고, 모델 정확도 수치는 포함하지 않았다. 실제 모델 확인 결과는 검증 기록 문서에 있다. period_requires_confirmation은 중단 코드가 아니라 period_notices 항목의 code 값이다.

## 7. 2026-10-15판 대비 갱신 내용 (2026-10-16)

| 노드 | 갱신 내용 |
|---|---|
| L3 메타 읽기 | 저장된 표에는 `path`가 남고, 모델에는 `path`를 뺀 축소 사본을 보낸다는 설명으로 바꿈 |
| C5 문맥 줄이기 | 모델용 축소 표(`model_table`)의 필드와 효과, 가지치기 시작 기준(저장 형식 크기), 한도 120,000자의 의미(호출 한도가 아닌 가지치기 기준), 429의 원인 해석(분당 토큰 한도), 가지치기 해제 시 CV-AB-04·05 0/2 결과, 관련 시험 이름을 추가함 |
| C6 스키마 만들기·크기 점검 | 크기 점검이 축소한 문맥 기준임을 명시함 |
| M1 모델 선택 호출 | 프롬프트의 `parent_code`·`under` 안내, 최대 출력 토큰 8192 → `config.max_b_output_tokens`(6144), 토큰 한도 계산 방식을 반영함 |
| 중단 코드 표 | `context_too_large`의 설명을 축소한 문맥 기준으로 바꿈 |
| 기준 문구 | 기준일을 2026-10-16으로 바꾸고 반영 내용을 덧붙임 |

나머지 노드(C1~C4a, V1~V15 등)는 이번 변경(모델에 보내는 표, 출력 상한)과 관련이 없어 그대로 둠. 노드별 동작을 코드 한 줄씩 다시 대조한 것은 아니고, 다음 세 가지를 확인했음: 중단 코드가 코드의 B 단계 실패 코드를 빠짐없이 포함함, 확인 방법에 적힌 시험 이름이 저장소에 존재함, 코드 위치에 적힌 함수·클래스가 존재함.
