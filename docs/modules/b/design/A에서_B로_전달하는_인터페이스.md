# A에서 B로 전달하는 인터페이스

> 이 레포(모듈 구조)에서 A→B 전달 위치: 화면 경로는 A 서비스(`modules/a/claview_a_api/service.py`)가 만든 표 목록을 pipeline(`pipeline/claview_pipeline/staged.py`)이 B 서비스(`modules/b/claview_b_api/service.py`의 `BService.resolve`)에 넘긴다. CLI·골든 22 평가 경로는 A 포트(`pipeline/claview_pipeline/b_port.py`)를 쓴다. 공용 계약은 `common/claview_common/contracts/a.py`(`TableRef`)와 [A_to_B 표 목록](../../../contracts/A_to_B_표목록.md). 아래 본문의 옛 파일 이름은 2026-10-06 B 공유본 기준이다.

공동 계약은 **원 질문 보존과 확정 A.tables**입니다. 내부 조건·근거·후보·모델 흔적을 공통 스키마의 필수 필드로 추가하지 않습니다.

```json
{"tables": [{"org_id": "301", "table_id": "DT_401Y018", "table_name": "수입물가지수(용도별)"}]}
```

`TableDiscovery.confirm_tables`(옛 B 공유본의 `a_discovery.py`, 이 레포에는 없음)는 공식 목록의 ID·이름으로 `stage=A, status=ok, result.tables`를 반환합니다. 자동 확정도 같은 저장 경로를 사용합니다. 후보·추천 집합은 확정 표로 전달하지 않습니다.

A는 표, B는 표 안의 항목·분류·기간·주기·단위를 정합니다. 한 표의 여러 지표는 B에서 여러 계열로 구성합니다. 메타 누락을 API 미지원으로 단정하지 않습니다.

## 실제 연결

`a_discovery.run_b`는 `runner(query, a_result, services)` 또는 B 그래프의 `{"query": query, "a_result": a_result}`로 연결합니다.

`bundle.original_query`는 최초 원문입니다. 추가 답변이 없는 기본 경로는 원문을 전달합니다. 기존 에이전트 답변 경로에서는 `bundle.search_query`에 원문과 기준 답변이 결합될 수 있고 반환 state의 `a_context`에 원문·답변·판본을 별도 유지합니다. B runner가 이 구조체 전체를 별도 인자로 받는 것은 아닙니다. 팀원 구현을 연결할 때 원문 인자와 추가 맥락의 처리 방식을 확인해야 합니다.

## 저장·검증 수준

`CandidateStore`(옛 B 공유본의 `candidate_store.py`, 이 레포에는 없음)는 문맥·판본·TTL·A 확정을 검사합니다. 미확정이면 B를 막고, 성공은 재사용하며, 실패는 명시 retry로 다시 실행합니다. B 실패 때문에 A를 재검색하지 않습니다. B의 기간 안내(`period_notices`, status `needs_confirmation`)는 성공한 B 결과와 함께 저장·재사용합니다(2026-10-06 flow 변경).

옛 B 공유본의 사용자 선택·A2·그래프·에이전트 시험(`tests/test_a_discovery.py`, `test_a2_selection.py`, `test_pipeline.py`, `test_a_agent.py`. 이 레포에는 그래프 시험만 [modules/b/tests/test_pipeline.py](../../../../modules/b/tests/test_pipeline.py)로 남음)은 원문·목록과 모의 복구 경계를 확인합니다. 지정 B ZIP은 통합했고 2026-10-06 기준 전체516건의 오프라인 시험을 통과했습니다. 2026-10-12 B 계층 처리와 2026-10-13 기간·주기 확정·이름 보정 반영 후의 시험과 실제 모델 확인은 [검증 기록](../research/B_계층_처리_검증_2026-10-13.md)에 따로 적었습니다. 실제 B 의미 정확도·수치 조회는 미확인입니다.

상대기간(‘최근·최신·올해·작년’ 등) 질의에서 표의 최신 수록 시점이 현재 시점보다 앞서면, B는 조회 가능 기간을 `b_result.period_notices`와 `response.notices`에 안내로 남기고 status `ok`로 조회 단계까지 진행합니다. 이용자에게 진행 여부를 묻지 않으며 확인 답변을 받는 재개 단계는 없습니다. 안내 항목은 `table`, `status`(`needs_confirmation`), `code`(`period_requires_confirmation`), `requested_as_of_period`, `resolved_period`, `message`입니다. [파일 분리·B 기능 검토와 실행 명령](../research/A_B_분리와_B_통합_검수_2026-10-06.md)을 참고합니다.

## B 결과의 계층 관련 필드 (2026-10-12)

표 안의 분류 값이 계층을 가질 때의 B 동작은 [B 계층 처리 설계](B_계층_처리_설계.md)에 있습니다. 연동에서 확인할 부분은 다음과 같습니다.

- `b_result.selections`의 행 구조와 fetch가 다시 검증하는 스키마는 바뀌지 않았습니다. 확장된 계열도 같은 행 형태로 들어옵니다.
- `b_result.relations`: 상위와 하위 값이 함께 선택된 쌍의 목록입니다. 항목은 `axis`, `ancestor_series_id`, `descendant_series_id`, `levels`이며 없으면 빈 배열입니다. 계열은 지우거나 가중하지 않습니다.
- `b_result.expand_notices`: 코드가 확장 지정이나 고른 값을 바꾼 내역이며 있을 때만 포함됩니다(분해 표현이 없는 질문에서도 생길 수 있습니다). 형식은 [설계 문서의 알림 형식](B_계층_처리_설계.md#6-알림-형식-expand_notices)에 있습니다.
- 확장 후 계열 수가 상한(기본 30)을 넘으면 B는 `selections` 없이 status `needs_confirmation`, 이슈 코드 `selection_count_requires_confirmation`으로 끝납니다. `result`는 `message`, `selection_count`, `limit`, `candidate_labels`를 가지며 응답의 `confirmation`으로 전달됩니다. 이 경우 조회 단계로 가지 않습니다.
- 응답으로 전달되는 안내는 `period_notices`뿐입니다. `relations`, `expand_notices`, `frequency_alternatives`는 `b_result`에만 있고 응답·fetch 결과에는 연결되지 않았습니다. 응답 노출은 팀 결정이 필요합니다.

## B 결과의 기간·주기 관련 변경 (2026-10-13)

- 질문에 연도가 있으면(상대 기간 표현이 없을 때) B가 기간을 질문에서 계산하며, 모델이 적은 `period`는 결과에 쓰이지 않습니다. 같은 질문의 결과 기간이 이전과 달라질 수 있습니다.
- 질문이 주기를 말하지 않고 표에 주기가 여러 개이면 가장 긴 주기(`Y` > `H` > `Q` > `M`)로 처리합니다. 이전에는 상대 기간 질문에서 주기를 정하지 못해 `conditions_incomplete`로 멈추는 경우가 있었습니다.
- `b_result.frequency_alternatives`: 위 경우에 다른 주기로 본 결과 목록이며 있을 때만 포함됩니다. 항목은 `table`, `frequency`, `period`, `selections`입니다. 이를 보여주는 화면과 조회 흐름은 아직 없습니다.
- 모델에 보내는 문맥의 `resolved_period`는 상대 기간 질문에서만 값이 있고, 연도를 적은 질문에서는 `null`입니다. B 상태의 `resolved_period`는 계산된 값을 유지합니다.
- `expand_notices`의 `reason`이 늘었습니다: `expand_on_bottom_level_value`, `query_names_longer_value`, `query_names_parent_value`, `query_excludes_named_value`. 마지막 셋은 `applied`가 `named_value`이고 `selected`에 바꾼 값 코드가 들어갑니다.

## 통계표가 여럿일 때의 B 입력 (2026-10-26)

- 통계표가 둘 이상이면 표마다 B 호출이 한 번씩 일어나고 각 호출이 원질문 전체를 봅니다. 지표가 여럿인 질문에서 모델이 다른 표 몫의 지표를 이름이 비슷한 항목으로 채우는 경향이 실제 HCX 시험에서 확인되었습니다([계획 31 9절](../../../plans/31_B_C_고도화_복수표_이중축.md), [계획 32](../../../plans/32_A_B_표별_지표_focus_제안.md)).
- 통계표가 둘 이상일 때만 B 프롬프트에 규칙 블록(`MULTI_TABLE_PROMPT`)이 붙습니다. 통계표가 하나인 호출의 프롬프트는 바뀌지 않았습니다.
- 표 항목의 선택 필드 `focus`(질문 중 그 표가 맡은 지표 문구)가 있으면 그 표의 호출에만 문맥 `table_focus`와 프롬프트 한 문장(`TABLE_FOCUS_PROMPT`)이 더해집니다. 모델이 보는 `selected_tables`는 이전처럼 세 필드만 가집니다. `focus`가 없으면 모델 입력은 이전과 같습니다. 유효하지 않은 `focus`(문자열이 아니거나 비었거나 100자 초과)는 `invalid_a_result`입니다. A가 `focus`를 넘기는 계약은 협의 중입니다.
- 모델이 같은 행을 반복하면 B가 하나로 줄이고 `expand_notices`에 `repeated_row_dropped`를 남깁니다. 이전에는 `duplicate_selection`으로 멈췄습니다(기간 등이 달라 같은 계열이 되는 중복은 여전히 멈춤).

[현재 구조](../../../records/_이전_A1A2_20261006/A_현재_아키텍처.md) · [실행과 복구](../../../records/A_사용자선택_실행과_복구_옛A.md)
