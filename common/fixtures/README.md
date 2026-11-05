# 계약 시험 샘플

합성 fixture와 저장된 실제 자료를 구분한다. 골든 정답은 `eval/cases/`의 검수 대상이며 자동으로 고치지 않는다.

- `a_request`: 22문항의 질문·기준일 코퍼스. HTTP 요청 자체가 아니다.
- `a_http_request`, `a_http_result`: 현재 A HTTP 요청·응답 예시.
- `a_view`: AView의 focus·출처 예시.
- `b_request`, `b_result`: B의 독립 실행용 입력·결과.
- `b_period_request`, `b_period_resolution`: 명시 답과 기간 출처·수록 구간 예시.
- `data_result`, `view`: 합성 조회와 표현 경계.
- `data_result_saved`: 저장된 실제 자료. 신규 호출 증거가 아니다.

생성기는 `pipeline/scripts/build_fixtures.py`다. 생성 결과 diff를 확인하고 사용하는 모듈 시험을 함께 실행한다. `--a-http-only`, `--period-contract-only`로 범위를 제한할 수 있다.
