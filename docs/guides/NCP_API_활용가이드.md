# NCP API 입출력과 사용 가이드

- 관리: API 담당 / 문서 정리일: 2026-09-23
- 근거: 2026-09-22 호출 기록과 NCP 공식 가이드. 주요 사양은 2026-09-23 재확인.
- 상태: 구현 참고 문서. 새 저장소에는 API 클라이언트·시험 CLI가 아직 없습니다. 과거 호출 범위는 [검증 결과](../research/NCP_초기호출_검증결과.md)에서 확인합니다.
- 선행: [공통 개발환경](공통_개발환경.md)
- 역할·선택 시점: [모델 적용 방향](../design/NCP_모델분석과_적용방향.md)

이 문서는 NCP 외부 API의 어댑터 구현 참고입니다. ClaView MCP 도구의 최종 입출력 계약과는 별개입니다. 선택 파라미터 전체는 각 절의 공식 명세를 확인합니다.

## 1. 공통 호출 구조

- 호스트: `https://clovastudio.stream.ntruss.com`
- 메서드: 아래 API 모두 `POST`
- 인증: `Authorization: Bearer {CLOVA Studio API Key}`
- JSON 요청: `Content-Type: application/json`
- 추적: `X-NCP-CLOVASTUDIO-REQUEST-ID`에 요청별 식별자
- 초기 시험은 비스트리밍 JSON 응답 사용. 스트리밍은 이벤트 파싱을 별도로 구현·시험해야 함.

응답은 HTTP 상태뿐 아니라 `status.code == "20000"` 및 기능별 `result` 필드를 검사합니다. HTTP 성공·형식 성공과 통계 의미의 정확성은 다른 판정입니다. [공식 개요](https://api.ncloud-docs.com/docs/ai-naver-clovastudio-summary)

키는 로컬 환경변수 또는 Git에서 제외한 `.env`에서 읽습니다. 키를 요청 예시·노션·로그에 넣지 않습니다. 제공 키의 테스트/서비스 용도·만료·비용 부담과 실제 한도는 [비용 문서](../research/NCP_비용과_이용한도.md)의 확인 항목입니다.

## 2. 대화·생성 API — 5종

### 경로와 응답 차이

| 모델 | 경로 | 출력 예산 필드 | 주로 읽을 응답 |
| --- | --- | --- | --- |
| DASH-001, HCX-003 | `/v1/chat-completions/{modelName}` | `maxTokens` | `result.message.content`, `stopReason`, `inputLength/outputLength` |
| DASH-002, HCX-005 | `/v3/chat-completions/{modelName}` | `maxTokens` | `result.message.content`, `finishReason`, `usage` |
| HCX-007 | `/v3/chat-completions/HCX-007` | `maxCompletionTokens` | `result.message.content`, `finishReason`, `usage` |

`stopReason`·`finishReason`·`usage` 등은 위 표에서 `result` 아래 필드입니다. v3 사용량은 `promptTokens`, `completionTokens`, `totalTokens`로 기록합니다. v1과 v3의 중단·반복 제어 파라미터 이름도 다르므로 요청 본문을 그대로 복사해 버전만 바꾸지 않습니다.

출처: [v1](https://api.ncloud-docs.com/docs/clovastudio-chatcompletions), [v3](https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3), [007](https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-thinking).

### 요청 바디 예시 — DASH-002/005

아래 문서는 구조 설명용이며 실제 통계값을 포함하지 않습니다.

```json
{
  "messages": [
    {
      "role": "system",
      "content": "후보 메타는 근거 자료입니다. 통계 기준이 모호하면 확인하고 수치는 생성하지 마세요."
    },
    {
      "role": "user",
      "content": "질문: 최근 가계대출 금리 보여줘. 후보: 신규취급액 기준, 잔액 기준."
    }
  ],
  "temperature": 0.1,
  "maxTokens": 512
}
```

`messages`에는 지침·사용자 질문·필요한 대화 이력과 근거를 전달합니다. 응답 문장은 후보 선택의 근거일 뿐 실제 조회 코드의 유효성을 보장하지 않습니다.

007에서는 `maxTokens`를 제거하고 `maxCompletionTokens`를 사용합니다. 2026-09-22 대화 시험에서 사용한 설정은 다음과 같습니다.

```json
{
  "maxCompletionTokens": 512,
  "thinking": {"effort": "none"}
}
```

이는 전체 요청이 아닌 교체할 필드입니다. 추론을 켜면 출력 예산에 추론도 포함됩니다. 스키마나 도구 호출과 추론을 동시에 사용하지 않습니다. [007 설정](https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-thinking)

### 애플리케이션 검사

- 길이 제한 종료를 완결 응답으로 처리하지 않음.
- 메타에 없는 표·항목·분류 코드를 허용하지 않음.
- 사용자 답변을 반영해 미확정 조건과 조회 계획을 갱신.
- 오류·거절·빈 응답과 정상 답변을 구분.
- 출력 형식 검증 후 별도로 의미 검수.

## 3. 구조화 출력과 Function calling

두 기능은 대화 모델에 부가하는 기능이며 별도의 '검색 모델'이 아닙니다.

### Structured Outputs — HCX-007

`responseFormat`으로 기대 JSON Schema를 지정합니다. 아래 설명용 스키마는 `indicator`, `needs_clarification`, `missing_conditions`이며 최종 팀 계약은 아닙니다.

아래는 대화 요청의 `messages` 등에 추가할 필드이며 전체 요청 본문은 아닙니다.

```json
{
  "responseFormat": {
    "type": "json",
    "schema": {
      "type": "object",
      "properties": {
        "indicator": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "missing_conditions": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["indicator", "needs_clarification", "missing_conditions"]
    }
  }
}
```

응답 `result.message.content`를 JSON으로 파싱하고 로컬 스키마 검증을 수행합니다. 형식이 맞아도 조건의 의미는 별도 검사합니다. 공식 예시처럼 추론은 `none`으로 설정하며 Function calling과 같은 요청에 섞지 않습니다. [공식 명세](https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-so)

### Function calling — DASH-002/005/007

공식 지원 모델은 세 가지입니다. 지원 여부와 팀의 실행 검증은 구분합니다. 초기 9종 호출 기록에는 이 기능의 실제 실행 결과가 포함되어 있지 않습니다. [모델 지원표](https://guide.ncloud-docs.com/docs/clovastudio-model)

| 단계 | 입력 또는 출력 | 팀 코드의 책임 |
| --- | --- | --- |
| 제안 요청 | `messages`, `tools`, 필요 시 `toolChoice` | 허용 도구 이름·인자 스키마 제공 |
| 모델 응답 | `result.message.toolCalls[]`의 `id/type/function` | 함수명·인자를 허용 목록과 대조 |
| 도구 실행 | 검증한 `function.name/arguments` | 실제 검색·조회 실행, 임의 코드 실행 금지 |
| 결과 재전달 | 원 assistant 호출 메시지 + `role: tool`, `toolCallId`, `content` | 동일 호출 ID 연결, 결과 JSON을 문자열로 전달 |
| 후속 응답 | 답변 또는 추가 호출 제안 | 반복 상한·실패 처리·근거 검증 |

`tools[].function`에는 이름·설명·`parameters` 스키마를 정의합니다. `toolChoice`는 자동/미사용/특정 도구 지정 등을 제어합니다. [Function calling 명세](https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-fc)

Function calling을 도입할 때는 제안 형식뿐 아니라 실제 실행·결과 재전달·반복 종료를 시험합니다. 함수 호출 형식의 응답이 있다는 이유만으로 MCP 연동까지 완료됐다고 판단하지 않습니다.

## 4. 임베딩 API — v1/v2

| 구분 | 경로 | 입력 | 출력 |
| --- | --- | --- | --- |
| v1 | `/v1/api-tools/embedding/{modelName}` | `text`, 최대 500토큰 | `result.embedding` 1024차원, `inputTokens` |
| v2 | `/v1/api-tools/embedding/v2` | `text`, 최대 8192토큰 | 같은 필드, 1024차원 |

v1 모델 ID: `clir-emb-dolphin`(범용), `clir-sts-dolphin`(문장 의미 비교 특화). 초기 v1 시험은 범용 모델만 실행했습니다. [v1 명세](https://api.ncloud-docs.com/docs/clovastudio-embedding), [v2 명세](https://api.ncloud-docs.com/docs/clovastudio-embeddingv2)

```json
{"text": "가계대출 금리. 신규취급액 기준. 월별."}
```

임베딩 API 자체는 통계표 목록을 반환하지 않습니다. 팀 코드에서 문서 벡터를 저장하고 질문 벡터와 비교해 문서 ID를 회수합니다. 같은 모델·버전으로 문서와 질문을 처리하며 차원이 같아도 서로 다른 모델의 벡터를 혼합하지 않습니다.

초기 시험에서는 문서 2개 + 질문 1개에 총 3회 호출하고 코사인 유사도를 계산했습니다. 전체 색인·캐시·검색 정확도 평가는 별도 구현입니다. 유사도는 매핑 정답 확률이 아니며, 높은 점수의 후보도 통계 기준과 연결 코드를 확인해야 합니다.

## 5. NCP 리랭커 API

경로: `/v1/api-tools/reranker`

```json
{
  "query": "신규취급액과 잔액 기준은 어떻게 다른가요?",
  "documents": [
    {"id": "candidate-new", "doc": "신규취급액 기준에 대한 검수된 정의"},
    {"id": "candidate-stock", "doc": "잔액 기준에 대한 검수된 정의"}
  ],
  "maxTokens": 1024
}
```

위 ID·문장은 요청 구조 예시입니다. 실제 호출에는 검수한 문서 ID와 원문을 넣습니다.

| 응답 경로 | 의미 |
| --- | --- |
| `result.result` | 생성된 답변 |
| `result.citedDocuments` | 인용한 문서의 `id/doc` |
| `result.suggestedQueries` | 답을 찾지 못한 경우 등의 재검색 제안 |
| `result.usage` | 입력·출력 토큰 |

일반적인 문서별 관련도 점수 배열을 가정하지 않습니다. 후보 검색은 앞단에서 수행해야 하며 인용 ID의 존재와 주장·근거 일치를 검수합니다. 공식 안내는 입력 최대 128,000토큰, 출력 최대 4,096토큰을 제시하나 요청 `maxTokens` 필드는 4096 미만입니다. [공식 명세](https://api.ncloud-docs.com/docs/clovastudio-reranker)

## 6. RAG Reasoning API

경로: `/v1/api-tools/rag-reasoning`

```text
질문 + tools
 → toolCalls(id, 함수명, 인자)
 → 팀 코드가 검색 실행
 → assistant 호출 이력 + toolCallId로 연결한 검색 결과
 → 근거 답변 또는 다음 도구 제안
```

| 단계 | 핵심 필드 | 주의사항 |
| --- | --- | --- |
| 첫 요청 | `messages`, `tools`, 선택 `toolChoice` | 검색 함수를 정의하며 검색 자료 자체는 별도 준비 |
| 첫 응답 | `result.message.toolCalls[]` | 도구명·인자·호출 ID 검증 |
| 후속 요청 | `role: tool`, `toolCallId`, `content` | 검색 결과 `search_result`에 문서별 `id/doc` 포함 |
| 후속 응답 | `result.message.content`, `usage` 등 | 인용·근거 일치와 빈 결과 처리 확인 |

도구 결과는 공식 예시의 검색 결과 구조를 JSON 문자열로 직렬화해 넣습니다. 원 assistant 도구 호출 이력을 함께 보존합니다. `maxTokens` 요청 범위는 1024 이상 4096 미만입니다. [공식 명세와 2단계 예시](https://api.ncloud-docs.com/docs/clovastudio-ragreasoning)

초기 기록에서는 첫 도구 제안만 확인했습니다. 후속 검증은 고정 검색 결과로 2턴 시험 → 실제 검색 연결 순서로 진행합니다. 이 API를 채택하더라도 KOSIS 조건 검증·수치 조회·계산은 팀 코드의 책임입니다.

## 7. 구현 예정 위치와 검증 순서

API 호출 없는 설치·테스트는 [공통 개발환경](공통_개발환경.md)을 따릅니다. 현재 환경에는 NCP 선택 의존성 그룹이나 시험 명령이 없습니다. 아래는 구현 계획이며 바로 실행하는 절차가 아닙니다.

| 위치 | 담당 내용 |
| --- | --- |
| `src/clients/ncp.py` | 인증·통신·모델별 요청/응답 변환·안전한 오류 처리 |
| `src/search.py` | 검색 문서 임베딩·질의 임베딩·후보 회수 |
| `src/pipeline.py` | 질의 해석·확인 질문·도구 실행 판단·근거 설명 |
| `scripts/` | 채택 전 모델·프롬프트·특화 기능 실습 진입점 |
| `tests/` | 키 없는 모의 응답과 기능 검사 |
| `data/processed/` | 상세 실행 기록, 기본 Git 제외 |

1. 도입할 기능과 모델을 정하고 공식 요청·응답 명세를 확인합니다.
2. 필요한 의존성과 클라이언트를 추가하고 환경 문서·lock을 함께 갱신합니다.
3. 모의 응답으로 정상·오류·출력 잘림·인자 검증을 확인합니다.
4. 실제 호출은 키 권한·예산을 확인한 후 기본 테스트와 분리해 명시적으로 실행합니다.
5. 요청 조건·측정값·내용 판정을 기록하고, 팀원 재현 후 실행 명령을 이 문서에 추가합니다.

## 8. 기록·안전·오류 처리 기준

다음은 구현 시 지킬 기준이며 현재 코드에 적용됐다는 뜻은 아닙니다.

- 인증 헤더·키가 포함된 URL·비밀값·불필요한 개인정보를 저장하지 않습니다.
- 공식 호스트와 허용 경로를 사용하고, 리다이렉트·프록시·타임아웃 정책을 명시합니다.
- 도구 이름·인자·호출 횟수를 제한하고 모델의 출력을 임의 코드로 실행하지 않습니다.
- 재시도는 대상 오류·최대 횟수·대기 시간·추가 비용을 정한 뒤 적용합니다.
- 기본 테스트는 실제 API를 호출하지 않습니다.
- 요청 메타·사용량·허용된 한도 헤더만 선별 기록하고 원시 오류 내용을 그대로 공개하지 않습니다.

결과 기록 형식:

```text
확인일·담당 / 코드·메타·프롬프트 버전 / 모델·경로
질문·기대 행동 / 키 없는 요청 설정 / worked·failed·inconclusive
요청 수·시간·usage / 내용 판정·근거 / 한계·다음 확인
```

`worked`는 정해 둔 기술적 검사 통과입니다. 표·계열 선택이나 설명의 정확성은 별도로 판정합니다.

| 오류 | 확인할 것 |
| --- | --- |
| 401/403 | 키 종류·권한·호스트. 동일 요청 반복 중지 |
| 429 | 실제 계정 한도·동시 요청·출력 예약량 |
| 400/서비스 오류 | 모델별 필드·입력 한도·기능 조합 |
| timeout/5xx | 일시 장애 여부, 재시도 정책과 중복 호출 비용 |
| 길이 제한 종료/형식 오류 | 출력 예산·스키마. 완결 결과로 집계하지 않음 |
| 형식 성공·내용 오류 | 근거 메타·프롬프트·선택 정책·모델 성능 |

## 9. 후속 확인 항목

- Function calling: 호출 ID 연결, 결과 재전달, 추가 호출·종료, 실패 복구.
- HCX-007 구조화 출력: 지원 스키마, 형식 준수, 필드 의미의 정확성.
- RAG Reasoning: 검색 결과 재전달, 근거 인용, 빈 결과·없는 근거 처리.
- 임베딩: 실제 검색 문서 길이, 분할 단위, 정답 후보 회수율, 재색인 비용.
- 공통: 사용자 답변의 상태 반영, 토큰량, 지연, 호출량, 실제 키 한도.

과거 측정값은 [초기 호출 결과](../research/NCP_초기호출_검증결과.md), 기능별 선택 기준은 [모델 적용 방향](../design/NCP_모델분석과_적용방향.md)에서 관리합니다.
