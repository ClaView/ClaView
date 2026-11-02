# A→B 기간 조건: 공용 모델과 A 송신부

## 구현 범위

B 회신 v2의 입력·출력 형식을 공용 모델로 정의하고, A가 새 요청을 조립해 AView에 담는다. B HTTP에는 수신 미지원 시 새 조건을 무시하지 않고 호출 전에 거절하는 보호 처리를 둔다. B의 기간 계산·수신 적용, 파이프라인의 실제 전송, 기존 보류 해제는 후속 단계다. 새 출력 필드가 있다고 B가 사용자 답을 적용했다고 해석하지 않는다.

| 대상 | 판 / 상태 |
|---|---|
| 기간 조건 모델 | `period-condition/2026-11-02.1` |
| B HTTP 응답 모델 | `b-result/2`; 기존 결과도 읽으며 새 범위 필드가 있을 때 검증 |
| AView | `a-view/2026-11-02.12` |
| A HTTP 요청 | `a-http/2026-11-02.1` 유지 |
| 기존 파이프라인 표 목록 | `flow-table-list/2026-10-23.1` 유지; 새 필드 전송 미연결 |

송신부는 A의 확정 결과를 다시 해석하거나 계산하지 않는다. 기간 경계·직접 답·명시 종료일이 있는 최근 기간의 A 보완은 [기간 처리 보완 문서](../modules/a/A_기간경계_종료일있는최근기간_20261103.md)를 따른다. B 회신에서 별도로 정할 달력 기간 대체와 PC-18의 명시 종료일 처리도 이 송신 계약에서 결정하지 않는다.

## 공용 입력

코드: [period_condition.py](../../common/claview_common/contracts/period_condition.py), [b.py](../../common/claview_common/contracts/b.py).

`BRequest.tables`는 A 출력의 `TableRef`와 구분한 `BTableRef`를 사용한다. 기존 세 필드와 선택 `focus`, `period_condition`을 받는다. 선택 필드가 없으면 기존 직렬화 모양을 유지한다.

`period_condition`은 다음을 담는다.

- `frequency`: Y/H/Q/M. `frequency_source`: question/catalog/user_answer.
- `period`: 유효한 YYYY-MM 시작·끝 한 구간. `period_source`: quoted/user_answer.
- `statistics`: 적용 통계 목록. 사용자 답인 필드가 있으면 빈 목록을 허용하지 않는다.
- `answers[]`: period/frequency 답의 질문 ID·버전·적용 통계·선택 ID·value·text·source.
- 선택 참고값 `period_phrase`, `latest_unpublished`.

사용자 답의 원문을 기간으로 다시 읽지 않는다. 필드별 사용자 답의 적용 통계 합집합이 조건의 대상을 모두 포함하는지 검사한다. 같은 질문 ID·버전·kind의 동일 답은 중복을 제거하고 적용 통계를 합친다. 값·원문·선택 ID가 다르면 충돌로 거절한다. 다른 통계의 답으로 누락을 채우지 않는다.

검사 단계는 구분한다.

| 공용 모델에서 확인 | B 수신부에서 확인할 것 |
|---|---|
| 형식·날짜·출처·한 구간·답 연결·충돌 | 표 존재·주기 지원·정확한 주기 환산·수록 범위·적용 우선순위 |

예를 들어 사용자 기간이 2024-02~05이고 참고 주기가 Q여도 공용 형식 검사는 허용한다. B가 M으로 정확히 표현할 수 있는지, 질문이 Q를 강제하는지 판단하는 단계는 B 수신부다.

공용 검증의 답 누락·충돌은 `period_condition_answer_missing`, `period_condition_answer_conflict`로 구분한다. 같은 표의 상충 조건은 `period_condition_conflicting_tables`다. 기본 자료형·필드 검증은 Pydantic 오류이며, 이를 B의 StageFailure 응답 코드로 변환하는 것은 B 수신부 후속 작업이다.

## A의 조립 위치와 출력

코드: [view.py](../../modules/a/claview_a_api/view.py), [AView](../../common/claview_common/contracts/a_view.py).

```text
A 확정 결과 + 사용자 답
  → handoff_conditions(AView, ready 요청)
  → prepare_b_request(AView, ready 요청)
  → handoff.ready[].b_request
```

`b_request`는 공용 `BRequest`로 검증한 `{query, as_of, tables}`다. 각 표에 `period_condition`을 넣는다. 원질문·기준일·표·focus는 기존 값을 복사하고, 원문을 다시 해석하지 않는다. 답은 기존 `period_answers`에서 연결하며 기준 축 답은 포함하지 않는다. 직접 입력은 원답 문자열을 value와 text에 그대로 남긴다.

새 조건을 만들지 못하면 `b_request=null`, `b_request_issue=<사유>`로 남긴다. A가 고른 표나 기존 ready 상태를 새 조립 오류 때문에 바꾸지 않는다. 후속 파이프라인은 준비 실패를 확인하고 사용자 답이 빠진 요청을 정상 전달로 처리하면 안 된다.

기존 `ready[].tables`, `period_answers`, `assignments`는 그대로다. 현재 파이프라인은 이 기존 필드만 사용하므로 `b_request`를 자동으로 보내지 않는다. `period_answer_contract_pending`, 같은 표의 상충 조건, 판본·불연속 기간 등 기존 보류도 유지한다.

## 출력 범위 모델

`PeriodResolution`은 표 ID·적용 통계·주기, `requested`, `fetched`, `missing`, `calculation_extra`, `applied_sources`, `substituted`, `reference`, `notices`를 검증한다. 기존 B 결과의 selections 등은 보존한다.

- Y는 YYYY, H/Q/M은 YYYY+기간 순번 두 자리다. 모든 범위에 frequency가 있으므로 H의 202402와 Q의 202402를 구분한다.
- 새 모델은 범위 계산을 실행하지 않는다. 계산과 메타 검증은 B 수신부에서 맡는다.
- `reference.differs.period`는 A 월 범위와 B requested를 월로 환산해 비교할 값이다. fetched의 잘림 여부와 구분한다.
- `substituted` 필드가 있다는 사실은 달력 자동 대체 정책을 승인하거나 구현했다는 뜻이 아니다.
- B HTTP의 형식 직렬화는 새 필드를 보존할 수 있지만, 기존 BService가 그 의미를 적용한다는 뜻은 아니다. B 응답 판 번호만으로 수신 지원 여부를 추정하지 않는다.

## 수신 미지원 B의 HTTP 보호

`/b/resolve`와 `/b/run`은 표에 `period_condition`이 있으면 먼저 서비스의 `accepts_period_condition`이 명시적으로 `True`인지 검사한다. 미지원 서비스에는 `resolve`를 호출하지 않고 `status=unsupported`, `issues[].code=period_condition_not_supported`를 반환한다. 모델 호출·조회 호출 수는 모두 0이다. `/b/run`도 이 거절 뒤 조회를 이어 가지 않는다.

조건이 없는 기존 요청은 기존 경로를 유지한다. 지원 서비스에는 원질문·기준일·구조화 조건을 그대로 넘긴다. 이 보호 처리는 B 수신부 구현이 아니며, 현재 BService에 지원 표시를 켜지 않는다. 파이프라인의 자동 전송 역시 활성화하지 않는다.

## 예시와 재생성

- [B 요청 예시](../../common/fixtures/b_period_request): 기간만 답·주기만 답·둘 다 답·quoted 참고·같은 표 복수 통계.
- [B 범위 반환 예시](../../common/fixtures/b_period_resolution): 요청/조회/미수록과 계산용 추가 구간.
- 예시는 가상 표와 합성 값이며 실제 모델·조회 결과가 아니다.

저장소 루트에서 공용 모델만 사용해 재생성한다. 이 옵션은 B 서비스나 실제 API를 호출하지 않는다.

```powershell
$env:PYTHONPATH = "common;modules/a;modules/b;modules/c;pipeline"
python -B -X utf8 pipeline/scripts/build_fixtures.py --period-contract-only
```

## 다음 연결 단계

1. B가 공용 모델·오류 대응·지원 표시를 사용해 수신부를 구현한다.
2. 파이프라인이 지원하는 B에만 `b_request`를 전달하고, 기존 보류를 조건별로 조정한다.
3. 실제 BService에 합성 모델·조회 응답을 붙여 서비스와 HTTP 양쪽에서 공통 예시를 확인한다.
4. 사용자 답의 범위·미수록·대체 상태를 화면에서 확인한다. 실호출은 별도 승인 이후다.

이번 A 소스 변경으로 기존 실호출 승인에 고정한 해시는 달라진다. 이전 승인을 그대로 재사용하지 않는다.

## 송신부 최초 구현의 확인 결과

- 공용 계약·예시 시험 1,377건, A API 178건, 파이프라인 176건, 파이프라인 API 4건, 기존 B API 11건 통과.
- 입력 직접 작성·선택지 답·주기만 답·두 종류 답을 실제 A HTTP 처리 경로와 직접 서비스 경로로 비교했다. 임시 기록 저장소를 다시 열어도 준비 요청과 답 출처가 같았다.
- 기존 화면 파이프라인에 준비 요청이 생겨도 B에 전달되는 기존 표 필드는 그대로임을 확인했다. 기간 답 계약 보류는 해제하지 않았다.
- Ruff와 모듈 의존 경계 9개 검사 통과. 시험은 외부 연결을 차단한 합성 판단·임시 기록으로 수행했다.
- 기존 시험의 동작 기대값은 바꾸지 않았다. AView 판 문자열과 추가 필드에 따른 계약 예시만 갱신했다.
- 위 최초 송신부 검증에는 실제 브라우저 UI, 새 B 수신 적용, 골든 모델 정확도, 실호출이 포함되지 않았다.

2026-11-03의 HTTP 미지원 보호와 A 기간 보완 결과는 [기간 처리 보완 문서](../modules/a/A_기간경계_종료일있는최근기간_20261103.md)에 구분한다. 최종 통합 시험 집계는 진행 중이며, 최초 송신부 시험 수를 새 후보 전체의 시험 수로 사용하지 않는다.
